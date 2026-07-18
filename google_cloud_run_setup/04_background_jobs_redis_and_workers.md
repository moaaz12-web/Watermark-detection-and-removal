# Background Jobs, Redis, and Worker Networking

> Historical note, 2026-07-06: this document describes the pre-cutover
> Celery/Redis worker-pool architecture. The repo runtime path has moved to
> Cloud Run Jobs; use
> [Cloud Run Jobs Migration For Background Work](./05_cloud_run_jobs_migration.md)
> and
> [Cloud Run Jobs Operations Runbook](./06_cloud_run_jobs_ops_runbook.md)
> for current background-job operations.

This document records the infrastructure shape chosen for async Shopify sync
background jobs.

For terminology, read [Terms and Mental Model](./00_terms_and_mental_model.md)
first.

## Current Direction

The async sync broker is a self-hosted Redis instance running on a Google Cloud
VM, not Memorystore.

Why this direction was chosen:

- Redis is only the Celery broker in this design
- the real sync state lives in PostgreSQL
- a small VM is materially cheaper than Memorystore at this stage
- the operational tradeoff is acceptable while the async system is still being
  proven

This means the deployment shape is:

```text
Cloud Run API service -> private Redis VM -> Cloud Run Worker Pool
                         |
                         -> Cloud SQL remains the system of record through app code
```

## Redis Role

Redis is used only for:

- Celery broker transport
- small task messages, typically just a `job_id`

Redis is not the source of truth for:

- sync progress
- sync counters
- final success/failure state
- product catalog data

Those stay in PostgreSQL, especially in `shopify_sync_jobs`.

Why this split matters:

- Redis can stay small and cheap
- losing Redis transport briefly is operationally painful but not a data-loss
  event for synced catalog state
- the frontend still reads stable state from the API backed by PostgreSQL

## Network Shape

The Redis VM is expected to use a private IP address on the Google Cloud VPC.

Desired access pattern:

- local laptops should not be able to reach Redis directly
- the Cloud Run API service should be able to reach Redis
- the Cloud Run Worker Pool should be able to reach Redis

Why this is the right shape:

- Redis is infrastructure, not a public application surface
- private-only access reduces exposure materially
- only the API service and Worker Pool need broker access

## Required Cloud Run And Worker Pool Environment Variables

Set these on both the API service and the Worker Pool:

```text
REDIS_URL=redis://:PASSWORD@10.10.0.2:6379/0
CELERY_BROKER_URL=redis://:PASSWORD@10.10.0.2:6379/0
```

Current repo behavior:

- `REDIS_URL` is the shared Redis connection string
- `CELERY_BROKER_URL` can override it explicitly
- if `CELERY_BROKER_URL` is unset, the app falls back to `REDIS_URL`

Important formatting note:

- do not insert spaces in the Redis URL
- use `redis://:PASSWORD@HOST:PORT/DB`
- not `redis://: PASSWORD@HOST:PORT/DB`

The Worker Pool also needs the same runtime configuration needed by the task
code it executes:

- database connectivity, including Cloud SQL attachment and `POSTGRES_*`
  settings
- Google AI configuration such as `GOOGLE_CLOUD_PROJECT`,
  `GOOGLE_CLOUD_LOCATION`, and `LLM_PROTECTED_TERM_MODEL`
- the same runtime service account or equivalent IAM permissions, including
  Cloud SQL Client, Secret Manager Secret Accessor, Logs Writer, and Vertex AI
  User when LLM tasks are enabled

Why this matters: the API only enqueues work. Shopify sync and LLM
classification both execute inside Celery, so the Worker Pool needs access to
the database, Redis, and Google AI APIs.

## Result Backend

Do not configure a Celery result backend for this feature right now.

Why:

- `shopify_sync_jobs` already stores the product-facing lifecycle and progress
- a Redis result backend would add extra writes and retention without giving
  the frontend a better source of truth
- keeping Redis broker-only is simpler operationally

## VM Expectations

Minimum practical VM direction for this repo:

- machine class around `e2-small`
- private IP
- Redis auth enabled
- persistence enabled
- automatic restart enabled
- firewall restricted to the app/worker network path only

Why this is enough:

- broker messages are tiny
- Redis is not storing full catalog data
- PostgreSQL remains authoritative

## Verification Checklist

Task 5 should be considered complete only after all of these are true:

- the Redis VM exists and is running
- the Redis VM is reachable from the Cloud Run API service
- the Redis VM is reachable from the Cloud Run Worker Pool
- `REDIS_URL` and `CELERY_BROKER_URL` are injected safely through Cloud Run
  configuration or Secret Manager
- Redis is not publicly exposed
- the chosen broker URL works without local-only assumptions

## Local Development Note

It is acceptable for the production Redis private IP to be unreachable from a
local laptop.

Why:

- that private-only restriction is intentional
- local development can use a different Redis instance, such as
  `redis://localhost:6379/0`, when running workers locally
- production infrastructure should not be coupled to local reachability

## Local Docker Testing

The repo's `docker-compose.yml` includes a local Redis container for testing.

Current local shape:

```text
db      -> postgres:16-alpine
redis   -> redis:7-alpine
backend -> FastAPI container using db + redis service names
worker  -> Celery worker using the same image and Redis service name
```

Local behavior:

- the backend container is forced to use `redis://redis:6379/0`
- the worker container is forced to use `redis://redis:6379/0`
- this avoids accidentally pointing local Docker traffic at the production
  private Redis VM
- host-machine development can still use `redis://localhost:6379/0` if the
  local Redis container is exposed on port `6379`

Example local start command:

```bash
docker compose up --build db redis backend worker bulk_scan_worker
```

The sync/LLM worker currently runs:

```bash
celery -A app.worker.worker:celery_app worker --loglevel=info -Q shopify-sync,llm-term-classification
```

That worker now:

- consumes Celery tasks from the `shopify-sync` queue
- consumes Celery tasks from the `llm-term-classification` queue
- claims queued `shopify_sync_jobs` rows in PostgreSQL
- updates `heartbeat_at` while the sync is running
- executes the Shopify bulk export/import sync service
- executes protected-term LLM classification jobs
- marks jobs `completed` or `failed`

The bulk scan worker runs separately:

```bash
celery -A app.worker.worker:celery_app worker --loglevel=info --concurrency=1 -Q shopify-bulk-scan
```

That worker now:

- consumes Celery tasks from the `shopify-bulk-scan` queue
- claims queued `shopify_bulk_scan_jobs` rows in PostgreSQL
- executes long-running Shopify bulk scan work without sharing worker slots
  with sync jobs
- marks bulk scan jobs `completed` or `failed`

## Cloud Run Worker Pool Command

When deploying the Cloud Run Worker Pool, reuse the same application image and
override the container command to run Celery instead of the Dockerfile's web
server command.

```bash
celery -A app.worker.worker:celery_app worker --loglevel=info -Q shopify-sync,llm-term-classification
```

and a second worker pool with:

```bash
celery -A app.worker.worker:celery_app worker --loglevel=info --concurrency=1 -Q shopify-bulk-scan
```

In the Cloud Console UI, this corresponds to:

```text
Container command:
celery

Container arguments:
-A
app.worker.worker:celery_app
worker
--loglevel=info
-Q
shopify-sync,llm-term-classification
```

For the bulk scan worker pool, use:

```text
Container command:
celery

Container arguments:
-A
app.worker.worker:celery_app
worker
--loglevel=info
--concurrency=1
-Q
shopify-bulk-scan
```

Required environment and runtime access:

- `REDIS_URL`
- `CELERY_BROKER_URL` if you want an explicit override instead of falling back
  to `REDIS_URL`
- Cloud SQL/database settings
- `GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_LOCATION`
- `LLM_PROTECTED_TERM_MODEL` for protected-term classification
- `SHOPIFY_BULK_SCAN_PRODUCT_CONCURRENCY` for the bulk scan worker pool
- `SHOPIFY_BULK_SCAN_IMAGE_CONCURRENCY` for the bulk scan worker pool
- `DB_POOL_PROFILE=bulk_scan` on the bulk scan worker pool only
- `BULK_SCAN_DB_POOL_SIZE` / `BULK_SCAN_DB_MAX_OVERFLOW` sized for the bulk
  worker's nested product/image concurrency
- attached runtime service account with the app's runtime permissions

Important deployment note:

- when Python code changes, build and deploy a new image to the Worker Pool
- updating only the FastAPI Cloud Run service does not update code running in
  Celery workers
- verify Worker Pool logs show both queues if LLM classification should run

## Bulk Scan Nested Concurrency

The bulk scan worker now uses nested concurrency inside one Celery task:

```text
bulk Celery concurrency * product concurrency * image concurrency
```

Current operational guidance:

- keep bulk Celery concurrency at `1`
- treat `SHOPIFY_BULK_SCAN_PRODUCT_CONCURRENCY` as a runtime-only capacity dial,
  not a user setting
- use `SHOPIFY_BULK_SCAN_IMAGE_CONCURRENCY` as the per-product inner cap
- set `DB_POOL_PROFILE=bulk_scan` only on the bulk scan worker process

Recommended tuned target after staging verification:

```env
DB_POOL_PROFILE=bulk_scan
SHOPIFY_BULK_SCAN_PRODUCT_CONCURRENCY=3
SHOPIFY_BULK_SCAN_IMAGE_CONCURRENCY=5
BULK_SCAN_DB_POOL_SIZE=24
BULK_SCAN_DB_MAX_OVERFLOW=10
```

Recommended Cloud Run bulk worker starting shape:

- run one bulk worker process with Celery `--concurrency=1`
- start with one Worker Pool instance for the bulk queue
- start the bulk worker at `2 vCPU`
- start the bulk worker at `4 GiB` memory

Why:

- with the tuned target, total image pipelines are
  `1 * 3 * 5 = 15`
- raising Celery concurrency from `1` to `2` doubles that to
  `2 * 3 * 5 = 30`, which also doubles DB/provider/memory pressure
- `2 vCPU` is a reasonable starting point because one bulk job is doing real
  parallel product and image work inside one container
- `4 GiB` gives safer headroom than `2 GiB` for large-image bursts, OCR/Gemini
  request handling, and short-lived overlap between worker sessions

If staging metrics stay comfortable, `2 GiB` can be tested later. Do not raise
Celery concurrency before you have clear evidence that CPU is the bottleneck and
the database/provider limits still have headroom.

Why this is the current direction:

- `1 * 3 * 5 = 15` gives one bulk job enough image throughput margin to target
  a 12-hour store-wide scan
- the API and sync/LLM worker can stay on the small default DB pool
- total provider and DB pressure stays predictable because only the bulk worker
  opts into the large pool and Celery still runs one bulk job at a time

Per-process DB pool split:

- API service: keep `DB_POOL_PROFILE=default` (or omit it and inherit the
  default)
- sync/LLM worker pool: keep `DB_POOL_PROFILE=default`
- bulk scan worker pool: set `DB_POOL_PROFILE=bulk_scan`

Connection sizing model:

```text
(pool_size + max_overflow) * number_of_processes
```

For the tuned bulk worker target, active connections inside that one process can
include:

- main bulk scan session: `1`
- product worker sessions: up to `3`
- image worker sessions: up to `15`
- heartbeat/progress sessions: `1-2`
- headroom for short overlap and retries

That is why the bulk worker uses `BULK_SCAN_DB_POOL_SIZE=24` /
`BULK_SCAN_DB_MAX_OVERFLOW=10`, while the API and sync/LLM worker keep the
smaller `DB_POOL_SIZE=6` / `DB_MAX_OVERFLOW=10` baseline.

Rollback:

```env
SHOPIFY_BULK_SCAN_PRODUCT_CONCURRENCY=1
DB_POOL_PROFILE=default
```

This disables product-level parallelism and shrinks the worker back to the
small pool profile. See
[the product concurrency plan](../shopify_bulk_scan_product_concurrency_plan.md)
for the full rollout and verification steps, and
[the image concurrency plan](../shopify_bulk_scan_image_concurrency_plan.md) for
the inner image-cap rationale.

## Current Worker Guardrail

Do not treat the current worker implementation as fully production-hardened yet.

Why:

- the worker lifecycle is now real and DB-backed
- the Shopify Bulk Operations path is now the live sync implementation
- retry policy, cancellation, and broader operational hardening are still
  intentionally out of scope for this stage

## Infrastructure Ping Endpoint

The API exposes a dedicated infrastructure health endpoint:

```text
GET /health/infrastructure
```

What it checks:

- database connectivity
- Redis broker connectivity using the resolved Celery broker URL

Response shape:

```json
{
  "status": "ok",
  "db": {
    "connected": true
  },
  "redis": {
    "configured": true,
    "connected": true
  }
}
```

Why this endpoint exists:

- it gives a direct Phase 2 Task 5 verification path
- it separates infrastructure readiness from business endpoints
- it works for both Cloud Run deployments and local Docker testing
