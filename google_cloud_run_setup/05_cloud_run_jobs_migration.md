# Cloud Run Jobs Migration For Background Work

## Status

Planning note as of 2026-07-02.

Updated 2026-07-02: the v1 concurrency model is finalized as rejection-based.
While a job in a capacity family is running, new requests in that family are
rejected; there is no queueing and no automatic promotion in v1.

Updated 2026-07-03: on the `cloud_run_migration` branch, repo implementation
work for Tasks 1-6 is present through commit `cb78791`. The remaining migration
work is infrastructure validation, production cutover, and cleanup of legacy
Celery/Redis runtime assumptions. Checklist items below are marked complete
only when they are implemented in this branch or locally verified; Google Cloud
smoke checks and production cutover items remain open.

Updated 2026-07-04: the user-facing Shopify paths have been production smoke
tested through Cloud Run Jobs. API-triggered Shopify sync and Shopify bulk scan
both launch Cloud Run Job executions and transition their DB rows to terminal
states. The LLM term-classification Cloud Run Job is intentionally deferred
because it is backend/admin-only right now; do not treat that path as validated
until `llm-term-classification-job` is provisioned and smoke tested. Bulk scan
runtime concurrency was reduced to fit the current small Cloud SQL connection
limit.

Updated 2026-07-06: global capacity/manual conflict checks have been manually
verified. `shopify-bulk-scan-job` was updated to `maxRetries=0`. The repo no
longer has Celery/Redis runtime wiring: health checks, local compose config,
settings, dependency declarations, and worker transport files now reflect the
Cloud Run Jobs-only path. The LLM term-classification job remains intentionally
deferred, and the old Cloud Run worker pools plus Redis VM have been deleted.

### Current Migration Snapshot

As of 2026-07-06, the migration is complete for the user-facing Shopify
background paths, but not for the full original three-job-family scope.

Done:

- Code-level migration work for Tasks 1-6 is present in the repo.
- Production database schema includes the Cloud Run execution identity and
  heartbeat/lifecycle columns needed by the job runners.
- The API service is configured with explicit Cloud Run Jobs dispatch target
  settings:
  - `CLOUD_RUN_JOBS_PROJECT=project-5909a738-2fdc-451e-ad6`
  - `CLOUD_RUN_JOBS_LOCATION=us-central1`
- `shopify-sync-job` exists in `us-central1`, is pinned to the current API image,
  runs `python -m app.jobs.runner shopify-sync`, uses `ENV=production`, and has
  `maxRetries=0`.
- `shopify-bulk-scan-job` exists in `us-central1`, is pinned to the current API
  image, runs `python -m app.jobs.runner shopify-bulk-scan`, uses
  `ENV=production`, is configured with `DB_POOL_PROFILE=bulk_scan`, and has
  `maxRetries=0`.
- API-triggered Shopify sync has been smoke tested in production and reached a
  terminal DB state.
- API-triggered Shopify bulk scan has been smoke tested in production and
  reached a terminal DB state.
- Global capacity/manual conflict behavior has been manually verified.
- Bulk scan runtime settings were reduced after observing Cloud SQL connection
  pressure:
  - `SHOPIFY_BULK_SCAN_PRODUCT_CONCURRENCY=2`
  - `SHOPIFY_BULK_SCAN_IMAGE_CONCURRENCY=4`
  - `BULK_SCAN_DB_POOL_SIZE=12`
  - `BULK_SCAN_DB_MAX_OVERFLOW=2`
- `/health/infrastructure` now checks database connectivity and Cloud Run Jobs
  dispatch configuration instead of Redis.
- Celery/Redis repo cleanup is done for the runtime path: `celery[redis]` was
  removed from dependencies, local `docker-compose.yml` no longer starts Redis
  or worker containers, and the Celery worker transport files were removed.
- Google Cloud cleanup is done: the old Cloud Run worker pools and Redis VM
  have been deleted.
- Automated migration tests passed on 2026-07-06:
  `rtk .venv/bin/python -m unittest tests.test_config tests.test_llm_term_classification tests.test_worker tests.test_shopify_catalog_sync tests.test_shopify_bulk_scan_jobs tests.test_shopify_bulk_scan_worker tests.test_llm_term_classification_routes tests.test_health tests.test_job_runner tests.test_job_dispatcher`.
- Operational notes and recovery commands are captured in
  [Cloud Run Jobs Operations Runbook](./06_cloud_run_jobs_ops_runbook.md).

Deferred:

- `llm-term-classification-job` is intentionally not provisioned yet.
- LLM term-classification create, retry, and apply smoke tests are deferred
  because that path is backend/admin-only right now.
- Decision: keep the LLM/admin path explicitly out of the current production
  Cloud Run Jobs cutover until it is needed.

Remaining:

- Automate job image repinning after API deploys, or keep it as a documented
  manual deploy step.

This document records the recommended migration path away from always-on Cloud
Run worker pools for background jobs.

Why this note exists:

- the current worker-pool design keeps background consumers running all the time
- that creates idle cost even when no sync, scan, or LLM work is being done
- some jobs in this repo can run for many hours, and in real usage may run for
  1-2 days
- the codebase is already close to a run-to-completion model because PostgreSQL
  is the source of truth for job state

This note focuses on:

- which tasks currently enter each worker
- why the current design costs money while idle
- what the Cloud Run Jobs replacement should look like
- what code would change in this repo
- what Google Cloud resources and IAM need to be set up
- the tradeoffs of this migration

## Executive Summary

The recommended direction is:

- keep the current DB-backed job tables and processor functions
- stop using Celery + Redis as the transport layer
- replace the two always-on Cloud Run worker pools with Cloud Run Jobs
- execute one Cloud Run Job per logical background job
- let the container exit when that single job finishes
- enforce the current effective concurrency caps in v1 by rejecting new work
  with `409` while another job in the same capacity family is still running

Why this is the right fit:

- Cloud Run worker pools do not autoscale by default and do not wake
  themselves up from `0`
- Cloud Run services are request-based and currently limited to under 60
  minutes per request
- Cloud Tasks targets Cloud Run services and Google explicitly recommends Cloud
  Run Jobs for work that must run longer than the Cloud Tasks timeout
- Cloud Run Jobs support task timeouts up to 168 hours (7 days), which fits the
  stated 1-2 day job duration requirement
- this repo already persists job state in PostgreSQL instead of relying on
  Celery state, which makes transport replacement much less invasive

Official references:

- Cloud Run worker pools do not automatically scale:
  <https://docs.cloud.google.com/run/docs/overview/what-is-cloud-run>
- Cloud Run services request timeout is less than 60 minutes:
  <https://docs.cloud.google.com/run/docs/configuring/request-timeout>
- Cloud Tasks page recommends Cloud Run Jobs for work longer than the maximum
  Cloud Tasks timeout:
  <https://docs.cloud.google.com/run/docs/triggering/using-tasks>
- Cloud Run Jobs task timeout can be set up to 168 hours:
  <https://docs.cloud.google.com/run/docs/configuring/task-timeout>

## Current Background-Job Shape

The current design uses:

- FastAPI API service
- Celery as the dispatch and execution transport
- Redis as the Celery broker
- one mixed worker for Shopify sync and LLM term classification
- one separate worker for Shopify bulk scan

Current worker commands are documented in
[04_background_jobs_redis_and_workers.md](./04_background_jobs_redis_and_workers.md)
and mirrored in [docker-compose.yml](/home/maaz/Desktop/infringement_project/infringement_backend/docker-compose.yml:53).

### Original Worker Split Before Cutover

| Runtime | Current command shape | What it handles | Why it exists |
|---|---|---|---|
| Mixed worker | `celery ... -Q shopify-sync,llm-term-classification` | Shopify sync and all LLM term classification tasks | Sync and LLM jobs are moderate background work and were grouped together |
| Bulk worker | `celery ... -Q shopify-bulk-scan --concurrency=1` | Shopify bulk scan only | Bulk scans are long-running and intentionally isolated from sync work |

### Original Live Worker-Pool Settings

The live Cloud Run worker-pool settings shared during planning are:

| Worker pool | Queues | CPU | Memory | Instance count | Migration meaning |
|---|---|---|---|---|---|
| Mixed worker | `shopify-sync`, `llm-term-classification` | `1 vCPU` | `512 MiB` | `1` | Preserve one global running sync-or-LLM slot in v1 |
| Bulk worker | `shopify-bulk-scan` | `1 vCPU` | `1 GiB` | `1` | Preserve one global running bulk-scan slot in v1 |

Important notes:

- these were the live worker-pool deployment values used for migration planning
- the mixed worker does not explicitly pin Celery concurrency in the Cloud Run
  UI, but with one live worker-pool instance on one vCPU it should still be
  treated as a single mixed-path slot for migration planning
- the Cloud Run Jobs v1 plan intentionally keeps these effective caps instead
  of introducing new parallelism at the same time as the transport change

### Background Job Inventory

Before the Cloud Run Jobs cutover, Celery carried five logical background
entrypoints. The same logical work now dispatches through the Cloud Run Jobs
dispatcher and runs through `app.jobs.runner`.

| Former task name | Former queue | Current Cloud Run mode | Processor |
|---|---|---|---|
| `shopify.sync.run` | `shopify-sync` | `shopify-sync` | `process_shopify_sync_job()` |
| `shopify.bulk_scan.run` | `shopify-bulk-scan` | `shopify-bulk-scan` | `process_shopify_bulk_scan_job()` |
| `llm.term_classification.classify.run` | `llm-term-classification` | `llm-classify` | `process_llm_term_classification_job()` |
| `llm.term_classification.failed_batches.retry.run` | `llm-term-classification` | `llm-retry-failed-batches` | `retry_failed_llm_term_classification_batches()` |
| `llm.term_classification.apply.run` | `llm-term-classification` | `llm-apply` | `process_llm_term_classification_apply_job()` |

Relevant code:

- Cloud Run Jobs dispatcher:
  [app/jobs/dispatcher.py](/home/maaz/Desktop/infringement_project/infringement_backend/app/jobs/dispatcher.py:1)
- Cloud Run Jobs runner:
  [app/jobs/runner.py](/home/maaz/Desktop/infringement_project/infringement_backend/app/jobs/runner.py:1)
- Shopify processors:
  [app/worker/tasks.py](/home/maaz/Desktop/infringement_project/infringement_backend/app/worker/tasks.py:61)
- LLM processors:
  [app/services/llm_protected_term_classifier.py](/home/maaz/Desktop/infringement_project/infringement_backend/app/services/llm_protected_term_classifier.py:1)
  and
  [app/services/llm_term_classification_apply.py](/home/maaz/Desktop/infringement_project/infringement_backend/app/services/llm_term_classification_apply.py:1)

### Current API Entry Points

The API already creates durable DB job rows first, then dispatches transport
work second.

That pattern is important because it means the DB is already the source of
truth.

Current enqueue entrypoints:

- Shopify sync create/dispatch:
  [app/api/routes/stores.py](/home/maaz/Desktop/infringement_project/infringement_backend/app/api/routes/stores.py:200)
- Shopify bulk scan create/dispatch:
  [app/api/routes/stores.py](/home/maaz/Desktop/infringement_project/infringement_backend/app/api/routes/stores.py:293)
- LLM term classification create/dispatch:
  [app/api/routes/llm_term_classification_jobs.py](/home/maaz/Desktop/infringement_project/infringement_backend/app/api/routes/llm_term_classification_jobs.py:57)
- LLM failed-batch retry dispatch:
  [app/api/routes/llm_term_classification_jobs.py](/home/maaz/Desktop/infringement_project/infringement_backend/app/api/routes/llm_term_classification_jobs.py:322)
- LLM apply dispatch:
  [app/api/routes/llm_term_classification_jobs.py](/home/maaz/Desktop/infringement_project/infringement_backend/app/api/routes/llm_term_classification_jobs.py:390)

### Why The Current Shape Costs Money

Celery with Redis is a pull-based worker model.

That means:

- the queue consumer must already be alive to pull work
- Redis must already be reachable
- the worker pool must stay running even when no tasks are pending

Cloud Run worker pools are designed for continuous background work, not
scale-to-zero job execution. Google explicitly documents that worker pools do
not automatically scale, and if the instance count is `0`, the worker will not
start by itself.

That is the core cost problem in this repo:

- the current transport requires idle consumers
- the platform primitive being used also assumes an always-on consumer model

### Current Cost Baseline

The current deployment is paying for both worker pools continuously, even when
no jobs are running.

Pricing assumptions used for this note:

- `us-central1` default Cloud Run pricing
- a `730` hour month for rough monthly comparison
- free tier ignored because it is shared across the billing account and may
  already be consumed by other Cloud Run usage

Approximate current idle-cost baseline:

| Runtime | Live shape | Approx. always-on monthly cost | Equivalent Cloud Run Job active cost |
|---|---|---|---|
| Mixed worker pool | `1 vCPU`, `512 MiB`, `1` instance | `$31.18/month` | `$0.0684/hour` while executing |
| Bulk worker pool | `1 vCPU`, `1 GiB`, `1` instance | `$32.80/month` | `$0.0720/hour` while executing |
| Total | Both current worker pools | `$63.98/month` | N/A |

What this means:

- Cloud Run Jobs are slightly more expensive per active hour than worker pools
  at the same CPU and memory sizes
- the savings come from eliminating idle time, not from cheaper active compute
- the rough break-even point is about `456` active hours per month per job
  family, or about `19` days of continuous use

Why this still supports the migration:

- your stated pain is idle cost from always-on workers
- if these workloads are bursty instead of busy most of the month, Cloud Run
  Jobs should materially reduce spend
- if a path eventually runs nearly continuously, that specific path can be
  revisited later with measured data

## Why Cloud Run Jobs Are A Better Fit

Cloud Run Jobs are a better primitive when work should:

- start only when requested
- run to completion
- exit when finished
- avoid idle cost between executions

That matches the current background work much better than worker pools.

### Why Not Keep Worker Pools And Autoscale Them

It is technically possible to bolt on external scaling for worker pools.

Why that is not the preferred path here:

- it keeps Celery and Redis in the architecture
- it keeps the operational burden of queue consumers
- it adds more scaling logic instead of simplifying the design
- it still does not align as naturally with the repo's existing
  job-row-plus-processor structure

### Why Not Use Cloud Run Services + Cloud Tasks For Everything

Cloud Tasks is useful for bounded HTTP background work, but it is not the main
recommendation here because some jobs in this system can run for 1-2 days.

Google's docs for Cloud Run services and Cloud Tasks create a hard ceiling:

- Cloud Run service requests must complete in under 60 minutes
- Cloud Tasks expects the target to return within its configured timeout
- Google says to consider Cloud Run Jobs when the work must run longer than the
  maximum Cloud Tasks timeout

That means:

- Cloud Tasks is not sufficient as the sole execution primitive for these long
  jobs
- Cloud Run Jobs is the correct base primitive

### Why The Existing Codebase Makes This Migration Easier

The main business logic is already outside Celery.

The Celery layer mostly does two things:

- transports a `job_id`
- calls the relevant processor function

The real state machine already lives in PostgreSQL:

- Shopify sync jobs use `shopify_sync_jobs`
- Shopify bulk scan jobs use `shopify_bulk_scan_jobs`
- LLM term classification uses `llm_term_classification_jobs`

This matters because the recommended migration is mostly transport replacement,
not a full background-job rewrite.

## Recommended Target Architecture

### High-Level Flow

The recommended flow is:

1. API receives a request to create a background job.
2. API validates auth and business rules.
3. API takes the capacity-family advisory lock and runs a global stale sweep.
4. If another job in that capacity family is still running, the API rejects
   the request with `409` and does not create a DB row.
5. Otherwise the API inserts the durable DB job row.
6. API calls the Cloud Run Jobs `run` API for the appropriate job definition.
7. Cloud Run launches one execution with runtime overrides for that job.
8. The container runs the existing processor for that one logical job.
9. The processor writes progress and terminal state to PostgreSQL.
10. The container exits when finished.

This preserves the current product-facing behavior:

- the frontend still polls DB-backed job endpoints
- the database remains the source of truth
- stale-job handling remains app-controlled

### Recommended Cloud Run Job Split

Use three Cloud Run Jobs, all built from the same application image.

| Cloud Run Job | Purpose | Why separate |
|---|---|---|
| `shopify-sync-job` | Executes one Shopify sync job | Simple runtime shape and separate observability |
| `shopify-bulk-scan-job` | Executes one Shopify bulk scan job | Needs different DB pool and bulk concurrency settings |
| `llm-term-classification-job` | Executes one LLM classification, retry, or apply run | Same general runtime shape, multiple modes |

Why three jobs instead of one:

- bulk scan already has different capacity and DB-pool requirements
- sync and LLM work are conceptually separate operational surfaces
- separate job definitions make timeouts and resources clearer and safer

### Recommended Execution Contract

Each execution should handle exactly one logical repo job.

Example command arguments:

```text
python -m app.jobs.runner shopify-sync --job-id 123
python -m app.jobs.runner shopify-bulk-scan --job-id 456 --image-llm-only false
python -m app.jobs.runner llm-term-classification classify --job-id 789
python -m app.jobs.runner llm-term-classification retry --job-id 789 --batch-indexes 1,4,8
python -m app.jobs.runner llm-term-classification apply --job-id 789
```

Why this shape:

- it matches the existing `job_id`-based processors
- it keeps job execution deterministic and auditable
- it avoids teaching Cloud Run Jobs about application internals beyond simple
  arguments

### Recommended Dispatch Mechanism

The API should call the Cloud Run Jobs execution API with overrides.

Google documents that job execution supports runtime overrides for:

- arguments
- environment variables
- task count
- task timeout

Official reference:

- <https://docs.cloud.google.com/run/docs/execute/jobs>

Why use the execution API with overrides:

- the code can pass `job_id` and small mode flags directly
- there is no need to create a separate job definition for every single job id
- the same Cloud Run Job definition can serve many executions safely

## Recommended Codebase Changes

This section describes what should change in the repo if this migration is
implemented.

### 1. Add A Dedicated Job Runner Module

Add a small CLI-style entrypoint, for example:

- `app/jobs/runner.py`

It should:

- parse job type and arguments
- call the existing processor functions
- exit non-zero on failure

Why:

- Cloud Run Jobs need a concrete container command
- the current image already contains all processor code
- a thin runner keeps the transport layer simple

### 2. Replace The Celery Dispatch Layer

Before cutover, the dispatch API lived in the Celery transport layer. The
replacement now lives in:

- [app/jobs/dispatcher.py](/home/maaz/Desktop/infringement_project/infringement_backend/app/jobs/dispatcher.py:1)

That module owns background-job dispatch and calls the Cloud Run Jobs REST API.

Why a new module is cleaner than stretching `celery_app.py`:

- the current file is Celery-specific in both naming and behavior
- transport should stop implying a broker after migration
- future code should read as "dispatch background job", not "send Celery task"

### 3. Keep The Existing Processor Functions

The following functions should remain the core workers:

- `process_shopify_sync_job()`
- `process_shopify_bulk_scan_job()`
- `process_llm_term_classification_job()`
- `retry_failed_llm_term_classification_batches()`
- `process_llm_term_classification_apply_job()`

Why keep them:

- they already encode the business logic
- they already know how to claim/finalize durable DB rows
- replacing the transport should not force a rewrite of proven application code

### 4. Update API Routes To Use The New Dispatcher

The route handlers in:

- [app/api/routes/stores.py](/home/maaz/Desktop/infringement_project/infringement_backend/app/api/routes/stores.py:200)
- [app/api/routes/llm_term_classification_jobs.py](/home/maaz/Desktop/infringement_project/infringement_backend/app/api/routes/llm_term_classification_jobs.py:57)

should keep their current product behavior:

- create/update DB row
- try dispatch
- mark dispatch failure cleanly if execution launch fails

Only the transport call should change.

Why:

- this keeps the API contract stable
- the frontend should not care whether transport is Celery or Cloud Run Jobs

### 5. Prefer REST API Dispatch Over Adding A Heavy Client Library

The repo already depends on:

- `requests`
- `google-auth`

Those are enough to call the Cloud Run Jobs REST API from the API service using
an access token from the metadata server.

Why this is the recommended first implementation:

- it avoids adding another Google client dependency just to execute jobs
- it keeps the dispatcher logic explicit
- the repo already uses `requests` and `google-auth` in other Google API code

### 6. Remove Redis And Celery As Runtime Requirements After Cutover

Once all background surfaces are migrated:

- remove Redis from production runtime requirements
- remove `REDIS_URL` and `CELERY_BROKER_URL` from production deployment
- remove the Redis VM if nothing else uses it
- remove the worker pools

Repo cleanup completed on 2026-07-06:

- removed `celery[redis]` from `requirements.txt`
- removed the Celery transport files from `app/worker/`
- simplified infra health so it no longer checks Redis
- updated local `docker-compose.yml` to stop depending on Redis and Celery

Why this cleanup matters:

- cost savings only become real once the old infrastructure is actually gone
- leaving dead transport code around invites drift and confusion

### 7. Harden LLM Term Classification Lifecycle

This migration is a good time to fix a gap in the current LLM job model.

Current Shopify jobs already support heartbeat/stale recovery:

- sync job heartbeat helpers:
  [app/services/shopify_sync_jobs.py](/home/maaz/Desktop/infringement_project/infringement_backend/app/services/shopify_sync_jobs.py:1)
- bulk scan heartbeat helpers:
  [app/services/shopify_bulk_scan_jobs.py](/home/maaz/Desktop/infringement_project/infringement_backend/app/services/shopify_bulk_scan_jobs.py:1)

Current LLM term classification jobs do not have the same lifecycle hardening:

- model:
  [app/models/llm_term_classification_job.py](/home/maaz/Desktop/infringement_project/infringement_backend/app/models/llm_term_classification_job.py:26)
- claim logic:
  [app/services/llm_protected_term_classifier.py](/home/maaz/Desktop/infringement_project/infringement_backend/app/services/llm_protected_term_classifier.py:892)
- apply logic:
  [app/services/llm_term_classification_apply.py](/home/maaz/Desktop/infringement_project/infringement_backend/app/services/llm_term_classification_apply.py:35)

Recommended hardening:

- add `heartbeat_at` to `llm_term_classification_jobs`
- add stale detection for long-running `running` and `applying` states
- optionally add a controlled redispatch path if an execution launch fails after
  the DB row is already committed

Why this should be bundled with the migration:

- Cloud Run Jobs make execution more explicit, but containers can still die
- the LLM path should have the same operational recovery guarantees as Shopify
  jobs
- fixing it later would leave one job family operationally weaker than the
  others

### 8. Enforce The Global Caps With An Atomic Guard

The v1 caps are enforced by rejection, and the check must be atomic.

Recommended implementation:

- define one PostgreSQL advisory lock id per capacity family: one for bulk
  scan, one for the mixed sync-or-LLM path
- in each creation/dispatch route, take the family lock, run the global stale
  sweep, check for running jobs, insert the row, dispatch, then release
- return `409` with a "another job is currently running" detail when the
  family is busy, without creating a DB row

Why:

- without the lock, two concurrent requests can both pass the "nothing
  running" check and both dispatch
- the mixed-path check spans two tables (`shopify_sync_jobs` and
  `llm_term_classification_jobs`), so a single-table unique constraint cannot
  express it
- the advisory lock keeps the guard in the database without new tables

Implementation note:

- the Cloud Run dispatch HTTP call happens while the lock is held, so give it
  a short timeout to keep the critical section bounded

### 9. Make Stale Detection Global

Today stale detection is per-store and request-triggered: sync and bulk scan
cleanup runs from that store's own routes, scoped by `store_id`.

Under a global cap that is not enough:

- a dead `running` job from store A blocks job creation for every store
- the rejected users are from other stores, and their requests never trigger
  store A's cleanup

Recommended implementation:

- add a shared helper that sweeps stale `running` (and LLM `retrying` /
  `applying`) jobs across all three job families, regardless of store
- run that sweep inside the cap guard before evaluating the cap
- treat "dispatched but never heartbeated" as stale using the dispatch
  timestamp, so a container that never started cannot block the system
- keep the existing per-store cleanup calls; they remain correct for
  store-scoped views

### 10. Persist Execution Identity And Handle Termination Signals

Two smaller changes make the new model operable:

- store the Cloud Run execution name returned by the `run` API on the job row
- make the job runner trap `SIGTERM` and mark the job state before exiting

Why:

- without the execution name there is no way to correlate a DB job row with
  its Cloud Run execution for debugging or cancellation
- Cloud Run sends `SIGTERM` before killing a container on timeout or
  cancellation; a runner that ignores it leaves the row `running` until the
  stale sweep fires, blocking the global cap in the meantime
- heartbeat interval, stale threshold, and task timeout must be chosen
  together: the stale threshold must be comfortably larger than the heartbeat
  interval, and the task timeout must be the largest of the three envelopes

## Recommended Google Cloud Setup

This section describes the target Google Cloud resources and configuration.

### APIs To Enable

Required:

- Cloud Run API
- Cloud SQL Admin API
- Artifact Registry API
- Secret Manager API

Optional, only if a later dispatch-cap layer is added:

- Cloud Tasks API

Why:

- Cloud Run Jobs need Cloud Run
- the runtime already depends on Cloud SQL
- the image must exist in Artifact Registry
- secrets should be injected via Secret Manager

Official references:

- Cloud Run Jobs creation:
  <https://docs.cloud.google.com/run/docs/create-jobs>
- Cloud Run Jobs secrets:
  <https://docs.cloud.google.com/run/docs/configuring/jobs/secrets>

### Cloud Run Jobs To Create

Create these job definitions in the same region as the existing service:

- `shopify-sync-job`
- `shopify-bulk-scan-job`
- `llm-term-classification-job`

Recommended shared defaults:

- one task per execution
- low retry count or no retry by default
- same base image family as the API
- same project and region as the current deployment unless there is a deliberate
  regional change

Why one task per execution:

- the application is not written as a multi-task sharded Cloud Run Job
- one execution should map to one logical DB job row

### Manual Provisioning Clarification

The chosen operational direction is:

- create the three Cloud Run Job definitions manually in Google Cloud
- keep job execution automatic from the API when a user creates a sync, bulk
  scan, or LLM job

What "manual provisioning" means here:

- you manually create and maintain the three reusable Cloud Run Job
  definitions
- the API still launches executions automatically at request time

What it does **not** mean:

- it does not mean you manually click "Run" every time a user submits a bulk
  scan
- it does not mean sync or LLM work becomes operator-triggered instead of
  user-triggered

Why this clarification matters:

- the one-time infrastructure setup and the per-request execution path are two
  different concerns
- manual infrastructure setup is compatible with fully automatic user-triggered
  execution

### Manual Job Definition Checklist

If the three Cloud Run Job definitions are created manually, capture and verify
these fields for each one:

| Field | What to set | Why it matters |
|---|---|---|
| Name | `shopify-sync-job`, `shopify-bulk-scan-job`, `llm-term-classification-job` | The API dispatcher needs stable target names |
| Region | Same region as the API service | Keeps latency and operational context simple |
| Image | Same application image family as the API | Reuses the existing backend artifact and code |
| Command | Point at the Python job runner | Cloud Run Jobs need a run-to-completion entrypoint |
| Base args | Match the job family | Keeps each job definition aligned with one business surface |
| Task count | `1` | One execution should process one logical DB job row |
| Retries | `0` or very low | Avoid accidental duplicate execution of long-running business work |
| Timeout | Sized per job family | Bulk, sync, and LLM have different runtime envelopes |
| Service account | Runtime service account with DB/secret/AI access | The execution must reach Cloud SQL, secrets, Shopify, and Google AI APIs |
| Cloud SQL attachment | Same Cloud SQL instance path as the API | The job processors persist state in PostgreSQL |
| Secrets | Same runtime secret set needed by that job family | Avoids hardcoding production credentials |
| Environment variables | App env plus job-family-specific tuning | The processors already depend on runtime settings |
| Networking / VPC | Match the current DB/network path if required | Needed if the environment depends on private connectivity |

Recommended environment split for manual setup:

- all three jobs:
  - database settings
  - app secrets
  - Shopify secrets where relevant
  - `GOOGLE_CLOUD_PROJECT`
  - `GOOGLE_CLOUD_LOCATION`
- bulk scan job only:
  - `DB_POOL_PROFILE=bulk_scan`
  - `SHOPIFY_BULK_SCAN_PRODUCT_CONCURRENCY`
  - `SHOPIFY_BULK_SCAN_IMAGE_CONCURRENCY`
  - `BULK_SCAN_DB_POOL_SIZE`
  - `BULK_SCAN_DB_MAX_OVERFLOW`
- sync and LLM jobs:
  - keep the default DB pool profile unless a later measurement proves they need
    something larger

Why document this checklist:

- it reduces the chance of creating a Cloud Run Job definition that exists but
  cannot actually run the app's processors
- it separates "what must be configured once" from "what the API will override
  for each execution"

### Created Job Definitions

Operational note from 2026-07-02: the three reusable Cloud Run Job definitions
were created manually in Google Cloud with the configuration below.

Current status:

- the job definitions exist so IAM, labels, runtime settings, Cloud SQL, and
  secrets can be prepared before code cutover
- they may initially point at the old backend image as a placeholder
- do not execute the jobs while they point at an old image that does not include
  `app.jobs.runner`
- after the Cloud Build run produces the migration image, update all three job
  definitions to the same image SHA as the API service before smoke testing

Shared job definition values:

| Field | Value |
|---|---|
| Project | `project-5909a738-2fdc-451e-ad6` |
| Region | `us-central1` |
| Image | Same backend image family as the API service; update to the new commit image after deploy |
| Container command | `python`, `-m`, `app.jobs.runner` |
| Task count | `1` |
| Parallelism | `1` |
| Retries | `0` |
| Runtime service account | `infringement@project-5909a738-2fdc-451e-ad6.iam.gserviceaccount.com` |
| Cloud SQL attachment | `project-5909a738-2fdc-451e-ad6:us-central1:infringement` |

Shared labels:

```text
app=infringement-backend
env=prod
component=background-job
job_family=<job-specific value>
```

Shared runtime environment:

- `ENV=production`
- `GOOGLE_CLOUD_PROJECT=project-5909a738-2fdc-451e-ad6`
- `GOOGLE_CLOUD_LOCATION=us-central1`
- `CLOUD_SQL_CONNECTION_NAME=project-5909a738-2fdc-451e-ad6:us-central1:infringement`
- `POSTGRES_DB=infringement`
- `POSTGRES_USER` from the same source as the API service
- `POSTGRES_PASSWORD` from Secret Manager or equivalent secret wiring
- `SECRET_KEY` from Secret Manager or equivalent secret wiring
- image-processing tuning such as `IMAGE_OCR_MAX_FILE_BYTES` and
  `IMAGE_LOGO_MIN_SCORE`, matching the API/current worker settings
- Shopify runtime settings and secrets where relevant, including
  `SHOPIFY_API_VERSION`, `SHOPIFY_API_KEY`, `SHOPIFY_API_SECRET`,
  `SHOPIFY_APP_AUTOMATION_TOKEN`, and `SHOPIFY_TOKEN_ENCRYPTION_KEY`

Do not add `REDIS_URL` or `CELERY_BROKER_URL` to the new Cloud Run Jobs unless
temporarily debugging the old transport. The Cloud Run Jobs path does not use
Redis.

Job-specific values:

| Job | Base args | CPU | Memory | Timeout | Extra label |
|---|---|---:|---:|---:|---|
| `shopify-sync-job` | `shopify-sync` | `1` | `512 MiB` | `24h` | `job_family=shopify-sync` |
| `shopify-bulk-scan-job` | `shopify-bulk-scan` | `1` | `1 GiB` | `72h` | `job_family=shopify-bulk-scan` |
| `llm-term-classification-job` | `llm-term-classification` | `1` | `512 MiB` | `24h` | `job_family=llm-term-classification` |

Bulk scan extra environment:

```text
DB_POOL_PROFILE=bulk_scan
SHOPIFY_BULK_SCAN_PRODUCT_CONCURRENCY=2
SHOPIFY_BULK_SCAN_IMAGE_CONCURRENCY=4
BULK_SCAN_DB_POOL_SIZE=12
BULK_SCAN_DB_MAX_OVERFLOW=2
```

These are the reduced production values after smoke testing against the current
Cloud SQL instance. The database has a small connection ceiling, so the earlier
bulk settings (`product_concurrency=3`, `image_concurrency=5`,
`BULK_SCAN_DB_POOL_SIZE=24`, `BULK_SCAN_DB_MAX_OVERFLOW=10`) could exhaust
Postgres connection slots during image-heavy scans.

LLM term-classification extra environment:

```text
LLM_PROTECTED_TERM_MODEL=<same value as API/current worker>
LLM_TERM_CLASSIFICATION_HEARTBEAT_INTERVAL_SECONDS=60
LLM_TERM_CLASSIFICATION_STALE_TIMEOUT_SECONDS=600
```

Why the command is configured this way:

- the API dispatch code uses Cloud Run execution overrides for runtime args
  such as `--job-id`, `--image-llm-only`, and retry batch indexes
- Cloud Run arg overrides replace the job's configured args, so the durable
  job definition must keep `python -m app.jobs.runner` in the container command
- the final effective command for a bulk scan execution should become:

```text
python -m app.jobs.runner shopify-bulk-scan --job-id <id> --image-llm-only <true|false>
```

IAM reminder:

- the API service account must be able to execute each job with runtime
  overrides
- grant the API service account Cloud Run permissions that include
  `run.jobs.runWithOverrides`; in the current setup, use Cloud Run Developer on
  each of the three job definitions unless a narrower custom role is introduced
  later
- the job runtime service account must keep runtime access to Cloud SQL, Secret
  Manager, Cloud Logging, Shopify-related secrets, and Vertex AI / Google AI
  APIs where needed

### Keeping Job Images In Sync With Deploys

The three Cloud Run Job definitions pin an image reference, just like the API
service does.

That means:

- deploying a new backend image updates the API service only
- the job definitions keep running the previously pinned image until they are
  updated too

Recommended handling:

- update all three job definitions to the new image as part of every backend
  deploy, in the same step that updates the API service
- treat "API and jobs run different code" as a deployment failure condition

Why this matters:

- the dispatcher, runner, and processors live in the same codebase
- version skew between the API and the jobs can break argument contracts or
  DB expectations silently

### Monitoring And Alerting

With `retries: 0`, a failed execution does not retry itself.

Recommended minimum:

- alert on failed Cloud Run Job executions for all three job definitions
- alert on job rows that the global stale sweep marks as failed
- keep per-job logs discoverable via the persisted execution name

Why:

- rejection-based v1 has no queue to absorb failures; a failed long job is
  simply gone until a user resubmits it
- operators should learn about infrastructure-caused failures from alerts, not
  from user reports

### Timeouts

Cloud Run Jobs support task timeouts up to 168 hours.

That means this design can support the stated 1-2 day runtime requirement as
long as the relevant job definitions are configured with sufficiently long task
timeouts.

Recommended first-deployment timeout targets:

| Job | Timeout | Why |
|---|---|---|
| `shopify-sync-job` | `24h` | Starts conservatively from the current mixed-worker shape without leaving runaway executions unbounded |
| `shopify-bulk-scan-job` | `72h` | Leaves margin for the stated 1-2 day worst-case scans without jumping straight to the 7-day maximum |
| `llm-term-classification-job` | `24h` | Uses the same mixed-path reference as sync and can be raised later if real workbook sizes prove it necessary |

Why configure per job type:

- the workloads have different runtime envelopes
- shorter timeouts where possible reduce accidental runaway executions
- the first deployment should start from current behavior, then widen only
  where real execution data shows it is needed

Official reference:

- <https://docs.cloud.google.com/run/docs/configuring/task-timeout>

### Runtime Service Accounts

Each Cloud Run Job should run under a user-managed service account.

That service account should have the runtime permissions already needed by the
current worker behavior, including as applicable:

- Cloud SQL connectivity permissions
- Secret Manager secret access
- Vertex AI / Google AI permissions for LLM work
- Cloud Logging write access if not already implicit through service behavior

Why:

- jobs should follow the same keyless runtime-identity model as the existing
  Cloud Run service
- runtime permissions should stay separate from build/deploy permissions

Official reference:

- <https://docs.cloud.google.com/run/docs/configuring/jobs/service-identity>

### API Service Account Permissions To Execute Jobs

The API service account must be allowed to execute the three Cloud Run Jobs,
including execution with overrides.

Why:

- the API will be the component that launches executions after inserting the DB
  job row
- the API needs to pass runtime arguments such as `job_id`, retry batch
  indexes, or `image_llm_only`

Official reference:

- <https://docs.cloud.google.com/run/docs/execute/jobs>

Important note:

- exact IAM bindings should be verified against the current Cloud Run Jobs IAM
  docs at implementation time
- do not assume that generic service invoker permissions are enough for job
  execution with overrides

### Secret Injection

Job configuration should use Cloud Run secret wiring for runtime secrets such
as:

- `POSTGRES_PASSWORD`
- `SECRET_KEY`
- Shopify secrets
- other production-only credentials

The runtime service identity used by the job must be able to access those
secrets.

Why:

- it keeps secrets out of deployment commands and source control
- it matches the recommended Cloud Run operational model

Official reference:

- <https://docs.cloud.google.com/run/docs/configuring/jobs/secrets>

### Database Connectivity

The current settings already support Cloud SQL via
`CLOUD_SQL_CONNECTION_NAME` in
[app/core/config.py](/home/maaz/Desktop/infringement_project/infringement_backend/app/core/config.py:50).

Recommended direction:

- keep using Cloud SQL
- keep using the same Cloud SQL connection model as the API service
- attach the relevant Cloud SQL connection to each Cloud Run Job

Why:

- all durable job state already lives in PostgreSQL
- there is no value in changing both the transport layer and the database path
  at the same time

Official reference:

- <https://docs.cloud.google.com/sql/docs/postgres/connect-run>

### Networking

After Celery/Redis removal, the jobs no longer need Redis network access.

They still need:

- database connectivity
- outbound access to Shopify
- outbound access to Google AI / Vision services

Why this matters:

- removing Redis reduces private network dependency surface
- bulk scan and sync still need strong egress connectivity

If the database path depends on VPC networking in the current environment, the
jobs must be configured consistently with that path.

## Suggested Job Configuration Shape

This is the recommended v1 configuration shape for the first deployment.

Why start here:

- it uses the same live CPU and memory reference points as the current worker
  pools
- it avoids changing transport and runtime sizing at the same time
- it keeps the pricing comparison simple and easier to validate after cutover

### Shopify Sync Job

- image: same application image as API
- command: Python runner
- base args: `shopify-sync`
- per-execution override args: `--job-id <id>`
- CPU: `1`
- memory: `512 MiB`
- task count: `1`
- retries: `0`
- timeout: `24h`
- env: standard runtime env

### Shopify Bulk Scan Job

- image: same application image as API
- command: Python runner
- base args: `shopify-bulk-scan`
- per-execution override args: `--job-id <id>` and `--image-llm-only <true|false>`
- CPU: `1`
- memory: `1 GiB`
- task count: `1`
- retries: `0`
- timeout: `72h`
- env:
  - `DB_POOL_PROFILE=bulk_scan`
  - `SHOPIFY_BULK_SCAN_PRODUCT_CONCURRENCY`
  - `SHOPIFY_BULK_SCAN_IMAGE_CONCURRENCY`
  - `BULK_SCAN_DB_POOL_SIZE`
  - `BULK_SCAN_DB_MAX_OVERFLOW`

Why bulk scan needs a separate definition:

- it already uses a different DB pool profile today
- it already has materially different runtime behavior and cost profile

### LLM Term Classification Job

- image: same application image as API
- command: Python runner
- base args: `llm-term-classification`
- per-execution override args:
  - `classify --job-id <id>`
  - `retry --job-id <id> --batch-indexes ...`
  - `apply --job-id <id>`
- CPU: `1`
- memory: `512 MiB`
- task count: `1`
- retries: `0`
- timeout: `24h`

## Tradeoffs And Risks

This migration is the recommended direction, but it is not free of tradeoffs.

### Tradeoff 1: Lower Idle Cost, But Not Lower Active Cost

Cloud Run Jobs solve idle cost, not active compute cost.

Why this matters:

- when nothing is running, jobs do not keep consumers alive
- when a sync or bulk scan is actually executing, you still pay for the compute
- very long scans can still be expensive while active
- at the current `us-central1` list price, Jobs are slightly more expensive per
  active hour than worker pools at the same shape

This is still the right trade:

- idle waste is removed
- active cost becomes directly tied to real usage

### Tradeoff 2: Simpler Runtime, But More Explicit Dispatch Logic

Celery hides execution transport behind `send_task()`.

Cloud Run Jobs shifts that responsibility into app code:

- the API must explicitly execute the right Cloud Run Job
- the API must pass overrides correctly
- execution-launch failures must be handled explicitly

Why this trade is acceptable:

- the transport code in this repo is already thin
- explicit dispatch is easier to reason about than hidden broker behavior

### Tradeoff 3: No Broker, But Also No Queue Buffer

Removing Celery/Redis means losing a traditional intermediary queue.

Why this matters:

- today the API can enqueue broker messages quickly
- with Cloud Run Jobs, the API is directly launching executions
- if execution launch fails, the API must mark that cleanly in the DB and
  surface a dispatch failure

Why this is acceptable here:

- job rows are already created first and can represent dispatch failure
- the system already treats PostgreSQL as the durable truth
- v1 deliberately replaces buffering with rejection: a busy capacity family
  returns `409` instead of accepting work it cannot start yet

### Tradeoff 4: Easier Scale-To-Zero Requires Explicit Scheduler Rules

This is the most important operational tradeoff.

Today the bulk worker is intentionally isolated and effectively capacity-limited
by always-on worker infrastructure.

If bulk were migrated naively to Cloud Run Jobs:

- every accepted bulk scan request can launch its own execution
- multiple users can create multiple long-running executions at roughly the same
  time
- total concurrent spend and external API pressure can rise faster than with a
  single standing worker

Why this matters:

- the migration removes idle cost
- but it can increase burst parallelism if many jobs are accepted at once

Decision for v1:

- keep the existing per-store exclusivity rules
- enforce the current effective bulk-worker cap by allowing only one running
  bulk scan job globally at a time
- enforce the current effective mixed-worker cap by allowing only one running
  sync-or-LLM job globally at a time across Shopify sync and all LLM
  term-classification modes
- do not queue behind the cap and do not auto-promote: while the capacity
  family is busy, new requests are rejected with `409`
- this is an accepted product decision: a user can be told "busy, try again
  later", even while a 1-2 day bulk scan is running

Why rejection instead of queueing with automatic promotion:

- queue promotion needs an owner: some component must notice a finished
  execution and dispatch the next queued job
- a runner-side promotion step disappears when the container is killed by OOM,
  timeout, or infrastructure failure, stranding everything queued behind it
- rejection removes that whole class of problems: a job row only ever exists
  because it was dispatched at creation time
- queueing can still be layered on later without reworking the DB-backed job
  model

Recommended application behavior:

- when a user creates any background job, first take the PostgreSQL advisory
  lock for that capacity family (bulk, or mixed sync-or-LLM)
- while holding the lock, run a global stale sweep across all job families so
  a dead execution cannot block the system indefinitely
- still holding the lock, check whether any job in that capacity family is
  running; if yes, return `409` with a clear "another job is currently
  running" detail and do not create a DB row
- if the family is free, insert the DB row, dispatch the Cloud Run Job
  execution, and release the lock
- keep the dispatch HTTP call timeout short, because the advisory lock is held
  across it

Why the advisory lock is required even in the rejection model:

- two concurrent requests can otherwise both observe "nothing running" and
  both dispatch, silently doubling the intended global concurrency
- the lock makes check-plus-insert-plus-dispatch a single serialized critical
  section per capacity family

Why the stale sweep must be global, not per-store:

- today stale cleanup is scoped to one store and triggered by that store's own
  routes
- under a global cap, the users being rejected are from other stores, and
  their requests never touch the dead job's store-scoped cleanup
- the sweep must also catch jobs that were dispatched but never produced a
  heartbeat (for example, a container that failed to start), using the
  dispatch timestamp as the staleness baseline

Why this is the right first shape:

- it preserves current bulk cost and concurrency behavior as closely as
  possible
- it avoids changing transport and global throughput policy at the same time
- it keeps the first migration aligned with today's real worker behavior

If later production behavior shows that more parallelism or buffering is
worthwhile, that can be added deliberately, for example:

- relaxing the global cap for a specific family
- adding DB-backed queueing with an explicit promotion owner
- Cloud Tasks as a lightweight launch gate for the bulk scan path

Why not add more complexity immediately:

- rejection already solves the immediate spend-risk concern
- adding queueing or promotion in v1 would reintroduce the orchestration
  problems this decision removes

### Tradeoff 5: Operational Simplicity Improves, But Cancellation Semantics Still Matter

Cloud Run Jobs can be cancelled, but cancellation does not undo work already
performed.

Why this matters:

- job cancellation is still an application behavior question, not only an
  infrastructure switch
- partial effects still need durable app-side handling

This does not block the migration, but it is a reason to keep the DB-backed job
state as the real system of record.

Official reference:

- <https://docs.cloud.google.com/run/docs/execute/jobs>

## Implementation Plan: Cloud Run Jobs Migration

### Overview

Migrate background execution from Celery + Redis + always-on Cloud Run worker
pools to Cloud Run Jobs while preserving the existing DB-backed job model, API
contract, and processor functions.

Why this plan is structured this way:

- transport replacement is the main change, not business-logic replacement
- risky foundation work should land before job-family cutovers
- each job family should be migrated as a vertical slice that can be tested on
  its own
- old infrastructure should not be deleted until the new path is verified

### Architecture Decisions

- PostgreSQL remains the source of truth for all background job state.
- One Cloud Run Job execution should process one logical repo job row.
- Use three Cloud Run Job definitions: sync, bulk scan, and LLM term
  classification.
- Include LLM heartbeat/stale recovery in the migration scope instead of
  leaving LLM operationally weaker than Shopify jobs.
- Enforce the current effective caps in v1 by rejection: only one running bulk
  scan job globally and only one running sync-or-LLM job globally, with new
  requests rejected with `409` while the capacity family is busy.
- No queueing behind the cap and no automatic promotion in v1; users retry
  when capacity frees up, which is an accepted product decision.
- Guard every cap check with a per-family PostgreSQL advisory lock held across
  check, insert, and dispatch.
- Run a global stale sweep across all job families inside the cap guard so a
  dead execution cannot block job creation system-wide.

### Task List

#### Phase 1: Foundation

## Task 1: Add Cloud Run Jobs dispatcher configuration and client

**Description:** Introduce the application-side transport abstraction that will
replace Celery dispatch. This task should add settings for Cloud Run Job names,
project, and region, and a dispatcher that can execute the Cloud Run Jobs `run`
API with overrides using the API service identity.

**Acceptance criteria:**
- [x] Runtime settings exist for Cloud Run Jobs dispatch configuration without
      breaking current local or production startup.
- [x] A dispatcher module can build and send authenticated job-execution
      requests with runtime overrides such as `job_id`, mode, and
      `image_llm_only`.
- [x] Launch failures are surfaced as structured application errors suitable for
      the existing route-level dispatch-failure handling.
- [x] The dispatcher returns the Cloud Run execution name, and job rows can
      persist it for debugging and cancellation.
- [x] The dispatch HTTP call uses a short timeout so cap guards holding an
      advisory lock stay bounded.

**Verification:**
- [ ] Tests pass: `rtk .venv/bin/python -m unittest tests.test_config`
      - Local 2026-07-03 run failed because
        `Settings().SHOPIFY_BULK_SCAN_PRODUCT_CONCURRENCY` resolved to `3`
        while the test expects default `1`; this appears environment-related
        rather than a Cloud Run dispatcher failure.
- [x] Tests pass: `rtk .venv/bin/python -m unittest tests.test_job_dispatcher`
- [ ] Manual check: verify the dispatcher can construct the expected request
      shape for one sync execution and one bulk execution.

**Dependencies:** None

**Files likely touched:**
- `app/core/config.py`
- `app/jobs/dispatcher.py`
- job model files plus an alembic migration for the execution-name column
- `tests/test_config.py`
- `tests/test_job_dispatcher.py`

**Estimated scope:** Large: 5+ files

## Task 2: Add a container job runner entrypoint

**Description:** Add a thin CLI-style runner that Cloud Run Jobs can invoke.
The runner should parse mode-specific arguments and call the existing processor
functions, returning a non-zero exit code when the processor fails.

**Acceptance criteria:**
- [x] The runner supports Shopify sync, Shopify bulk scan, LLM classification,
      LLM retry, and LLM apply modes.
- [x] Required arguments are validated clearly before any processor runs.
- [x] Processor exceptions produce non-zero exit status and useful logs for job
      execution debugging.
- [x] The runner traps `SIGTERM` and records an interrupted state on the job
      row before exiting, so timeout or cancellation does not leave the row
      `running` until the stale sweep fires.

**Verification:**
- [x] Tests pass: `rtk .venv/bin/python -m unittest tests.test_job_runner`
- [x] Manual check: `rtk .venv/bin/python -m app.jobs.runner --help`
- [ ] Manual check: invoke the runner locally with a known test `job_id` and
      confirm it reaches the intended processor.

**Dependencies:** Task 1

**Files likely touched:**
- `app/jobs/runner.py`
- `tests/test_job_runner.py`
- `Dockerfile`

**Estimated scope:** Medium: 3-5 files

## Task 3: Add LLM heartbeat and stale-job recovery

**Description:** Close the current lifecycle gap in LLM term classification
jobs before the new execution model is fully relied upon. This task should add
LLM heartbeat storage and stale detection so that a killed Cloud Run Job does
not leave LLM jobs stuck forever in `running`, `retrying`, or `applying`.

**Acceptance criteria:**
- [x] `llm_term_classification_jobs` stores enough lifecycle data to detect
      stale executions cleanly.
- [x] Long-running LLM classification and apply flows update heartbeat state
      during execution.
- [x] Stale LLM jobs transition to a recoverable terminal or retryable state
      instead of remaining stuck indefinitely.
- [x] A shared global stale sweep exists that covers Shopify sync, bulk scan,
      and LLM jobs across all stores, including jobs that were dispatched but
      never produced a heartbeat.

**Verification:**
- [ ] Tests pass: `rtk .venv/bin/python -m unittest tests.test_llm_term_classification`
- [ ] Tests pass: `rtk .venv/bin/python -m unittest tests.test_worker`
- [x] Tests pass: `rtk .venv/bin/python -m unittest tests.test_background_job_stale_sweep`
- [ ] Manual check: simulate a stale LLM `running` job in a dev database and
      verify that stale detection behaves as documented.

**Dependencies:** None

**Files likely touched:**
- `app/models/llm_term_classification_job.py`
- `alembic/versions/...`
- `app/services/llm_protected_term_classifier.py`
- `app/services/llm_term_classification_apply.py`
- `tests/test_llm_term_classification.py`
- `tests/test_worker.py`

**Estimated scope:** Large: 5+ files

### Checkpoint: Foundation

- [ ] Foundation tests pass cleanly.
- [x] The codebase can dispatch a Cloud Run Job request without touching any API
      route behavior yet.
- [x] The job runner can execute each supported mode locally.
- [x] LLM lifecycle hardening is designed and tested before LLM cutover begins.

#### Phase 2: Core Features

## Task 4: Migrate Shopify sync dispatch to Cloud Run Jobs

**Description:** Replace Celery dispatch for the Shopify sync path while
preserving the existing enqueue response, stale-job semantics, and dispatch
failure behavior, and enforcing the mixed-path cap of one running sync-or-LLM
job globally at a time by rejection.

**Acceptance criteria:**
- [x] `POST /stores/{store_id}/sync-products` dispatches through the new Cloud
      Run Jobs transport instead of Celery when no other sync-or-LLM job is
      currently running globally.
- [x] If another sync-or-LLM job is already running globally, the request is
      rejected with `409` and no new job row is created.
- [x] The check-insert-dispatch sequence runs under the mixed-path advisory
      lock, with the global stale sweep executed first.
- [x] The existing sync job row lifecycle and 202/503 route behavior remain
      unchanged for accepted jobs.
- [x] Stale queued sync redispatch behavior uses the new dispatcher path.

**Verification:**
- [ ] Tests pass: `rtk .venv/bin/python -m unittest tests.test_shopify_catalog_sync`
- [ ] Tests pass: `rtk .venv/bin/python -m unittest tests.test_worker`
- [ ] Manual check: create a sync job through the API and confirm a Cloud Run
      Job execution is launched with the expected `job_id`.
- [ ] Manual check: create a sync job while an LLM job is already running and
      verify the request is rejected with `409` and no sync job row is
      created.

**Dependencies:** Tasks 1-2

**Files likely touched:**
- `app/api/routes/stores.py`
- `app/jobs/dispatcher.py`
- `tests/test_shopify_catalog_sync.py`
- `tests/test_worker.py`

**Estimated scope:** Medium: 3-5 files

## Task 5: Migrate Shopify bulk scan dispatch to Cloud Run Jobs

**Description:** Replace Celery dispatch for Shopify bulk scan while preserving
the existing job creation contract, one-active-job-per-store rules, and
`image_llm_only` support, and enforcing the global cap of one running bulk
scan job at a time by rejection.

**Acceptance criteria:**
- [x] `POST /stores/{store_id}/scan-jobs` dispatches the bulk scan Cloud Run
      Job immediately only when no other bulk scan job is currently running
      globally.
- [x] If another bulk scan is already running globally, the request is
      rejected with `409` and no new job row is created.
- [x] The check-insert-dispatch sequence runs under the bulk-path advisory
      lock, with the global stale sweep executed first.
- [x] Existing per-store conflict behavior and stored job metadata remain
      unchanged.
- [x] Dispatch failure continues to mark the DB row cleanly and returns the
      expected API error shape.

**Verification:**
- [ ] Tests pass: `rtk .venv/bin/python -m unittest tests.test_shopify_bulk_scan_jobs`
- [ ] Tests pass: `rtk .venv/bin/python -m unittest tests.test_shopify_bulk_scan_worker`
- [ ] Manual check: create one normal bulk scan and one `image_llm_only` bulk
      scan and confirm the execution arguments are correct.
- [ ] Manual check: create a second bulk scan job from a different store while
      one is running and verify it is rejected with `409` until the first
      finishes.

**Dependencies:** Tasks 1-2

**Files likely touched:**
- `app/api/routes/stores.py`
- `app/jobs/dispatcher.py`
- `tests/test_shopify_bulk_scan_jobs.py`
- `tests/test_shopify_bulk_scan_worker.py`

**Estimated scope:** Medium: 3-5 files

## Task 6: Migrate LLM term-classification dispatch surfaces to Cloud Run Jobs

**Description:** Replace Celery dispatch for all three LLM term-classification
surfaces: create/classify, failed-batch retry, and apply. The route behavior
and status transitions should remain stable from the user's point of view while
enforcing the mixed-path cap of one running sync-or-LLM job globally at a time
by rejection.

**Acceptance criteria:**
- [x] Job creation dispatches the LLM Cloud Run Job in `classify` mode only
      when no other sync-or-LLM job is currently running globally.
- [x] Failed-batch retry dispatches the same job definition in `retry` mode with
      the requested batch indexes, subject to the same global mixed-path cap.
- [x] Apply dispatches the same job definition in `apply` mode while preserving
      existing status validation and failure handling, subject to the same
      global mixed-path cap.
- [x] If another sync-or-LLM job is already running globally, the create,
      retry, or apply request is rejected with `409` without creating rows or
      changing existing job status.
- [x] All three surfaces run their check-dispatch sequence under the
      mixed-path advisory lock, with the global stale sweep executed first.

**Verification:**
- [ ] Tests pass: `rtk .venv/bin/python -m unittest tests.test_llm_term_classification_routes`
- [ ] Tests pass: `rtk .venv/bin/python -m unittest tests.test_worker`
- [ ] Manual check: trigger create, retry, and apply flows in a dev or staging
      environment and verify the launched mode matches the API action.
- [ ] Manual check: trigger an LLM job while a sync job is already running and
      verify the request is rejected with `409` and the existing job status is
      unchanged.

**Dependencies:** Tasks 1-3

**Files likely touched:**
- `app/api/routes/llm_term_classification_jobs.py`
- `app/jobs/dispatcher.py`
- `tests/test_llm_term_classification_routes.py`
- `tests/test_worker.py`

**Estimated scope:** Medium: 3-5 files

### Checkpoint: Core Features

- [x] All three job families dispatch through Cloud Run Jobs in code.
- [x] Route-level tests for sync, bulk scan, and LLM still pass.
- [x] Frontend polling behavior is unchanged; the only contract addition is
      the global-busy `409` on creation and retry/apply surfaces.
- [x] Celery/Redis cutover cleanup is complete in the repo.

#### Phase 3: Infrastructure And Cutover

## Task 7: Provision Cloud Run Jobs and validate them end-to-end

**Description:** Create the three Cloud Run Job definitions, wire IAM, secrets,
Cloud SQL, timeouts, and runtime settings, then verify end-to-end execution in
a staging or production-like environment.

**Acceptance criteria:**
- [x] `shopify-sync-job` and `shopify-bulk-scan-job` exist with the intended
      service account, Cloud SQL, secret, and environment wiring for the
      user-facing Shopify cutover.
- [ ] `llm-term-classification-job` exists with the intended timeout, service
      account, Cloud SQL, secret, and environment wiring.
      - Deferred intentionally because this path is backend/admin-only right now.
- [x] The API service account can execute the Shopify jobs with runtime
      overrides.
- [x] Successful API-triggered Shopify sync and Shopify bulk scan executions are
      demonstrated in production.
- [ ] One successful end-to-end LLM term-classification execution is
      demonstrated in staging or an equivalent safe environment.
      - Deferred intentionally until that backend/admin path is needed.

**Verification:**
- [x] Manual check: `gcloud run jobs describe` shows the expected runtime
      configuration for `shopify-sync-job` and `shopify-bulk-scan-job`.
- [ ] Manual check: `gcloud run jobs describe` shows the expected runtime
      configuration for `llm-term-classification-job`.
      - Deferred intentionally.
- [x] Manual check: `gcloud run jobs executions list` shows Shopify sync and
      bulk scan executions launched from API-triggered requests.
- [x] Manual check: API-triggered Shopify sync and bulk scan flows transition
      their DB job rows to terminal states.
- [ ] Manual check: API-triggered LLM create, retry, and apply flows transition
      their DB job rows to the expected terminal states.
      - Deferred intentionally.

**Production smoke notes, 2026-07-04:**
- Shopify bulk scan initially exposed three infra issues:
  - API dispatch needed explicit `CLOUD_RUN_JOBS_PROJECT` and
    `CLOUD_RUN_JOBS_LOCATION=us-central1`.
  - The bulk job image had to be repinned to the current API service image.
  - `SHOPIFY_BULK_SCAN_USE_DUMMY_DETECTIONS` had to be set to `false` instead
    of an empty env var.
- Shopify sync initially exposed an old image pin on `shopify-sync-job`; it was
  repinned to the current API service image and set to `ENV=production` with
  `maxRetries=0`.
- Bulk image scans can still hit connection pressure if concurrency is raised
  beyond the reduced values above without increasing Cloud SQL capacity.

**Dependencies:** Tasks 1-6

**Files likely touched:**
- `docs/google_cloud_run_setup/05_cloud_run_jobs_migration.md`
- `docs/google_cloud_run_setup/README.md`
- deployment runbooks or scripts if this repo stores them

**Estimated scope:** Large: 5+ files

## Task 8: Cut over production and remove legacy Celery/Redis runtime dependencies

**Description:** After Cloud Run Jobs are validated, remove the old runtime
path from production and clean up now-obsolete Celery/Redis infrastructure,
repo dependencies, health checks, and local development wiring.

**Acceptance criteria:**
- [x] Production API dispatch uses Cloud Run Jobs only.
      - User-facing Shopify sync and bulk scan dispatch through Cloud Run Jobs
        now. LLM term-classification is still deferred because the job
        definition is not provisioned.
- [x] Cloud Run worker pools are no longer required for normal user-facing
      Shopify operation.
      - The old Cloud Run worker pools and Redis VM have been deleted in Google
        Cloud.
- [x] Redis and Celery are removed from production runtime assumptions and repo
      documentation where appropriate.

**Verification:**
- [x] Tests pass: `rtk .venv/bin/python -m unittest tests.test_health tests.test_config`
- [x] Tests pass: `rtk .venv/bin/python -m unittest tests.test_shopify_catalog_sync tests.test_shopify_bulk_scan_jobs tests.test_llm_term_classification_routes`
- [x] Manual check: production or staging environment variables no longer
      require `REDIS_URL` / `CELERY_BROKER_URL` for background execution.
- [x] Manual check: the worker pools and Redis VM were deleted after confirming
      Shopify sync and bulk scan use Cloud Run Jobs.

**Dependencies:** Task 7

**Files likely touched:**
- `requirements.txt`
- `app/api/routes/health.py`
- `docker-compose.yml`
- `app/worker/`
- `docs/google_cloud_run_setup/04_background_jobs_redis_and_workers.md`
- `docs/google_cloud_run_setup/README.md`

**Estimated scope:** Large: 5+ files

### Checkpoint: Complete

- [x] All targeted automated tests pass.
- [x] Staging or production-like end-to-end validation exists for the
      user-facing Shopify sync and bulk scan flows.
      - LLM remains intentionally deferred because that path is backend/admin-only.
- [x] Worker pools were deleted without breaking Shopify background execution.
- [x] Redis is no longer a production dependency for this repo's background
      work.
- [x] Documentation is updated to reflect the post-migration architecture.

### Parallelization Opportunities

- Safe to parallelize after Phase 1:
  - Shopify sync dispatch migration
  - bulk scan dispatch migration
  - drafting Cloud Run Job definitions and IAM changes
- Needs coordination:
  - LLM dispatch migration and LLM lifecycle hardening should share one agreed
    status/heartbeat design
- Must stay sequential:
  - database migration rollout
  - final production cutover
  - Redis/Celery removal

### Plan-Specific Risks And Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Cloud Run Job executions exceed today's global worker behavior | High | Enforce one running bulk scan globally and one running sync-or-LLM job globally in v1 by rejecting new requests with `409`, guarded by per-family advisory locks |
| A dead execution blocks all job creation under the global cap | High | Run a global stale sweep inside the cap guard, including jobs that were dispatched but never heartbeated |
| Concurrent requests race past the cap check | High | Hold a per-family PostgreSQL advisory lock across check, insert, and dispatch, with a short timeout on the dispatch HTTP call |
| API and job definitions drift to different image versions | Medium | Update all three job definitions in the same deploy step that updates the API service |
| LLM jobs remain operationally weaker than Shopify jobs | High | Land heartbeat/stale handling before or alongside LLM dispatch cutover |
| API service account cannot execute jobs with overrides | High | Validate IAM in staging early and add manual smoke checks before route cutover |
| Cleanup removes Celery/Redis too early | Medium | Keep old infrastructure until all three job families pass end-to-end validation |
| Bulk scan runtime settings are under-sized for multi-day jobs | Medium | Set timeouts/resources conservatively at first and validate with representative long scans |

### Finalized First-Deployment Decisions

The remaining planning questions are resolved as follows:

- create the three Cloud Run Job definitions manually one time in Google Cloud
- keep job execution automatic from the API whenever a user submits sync, bulk
  scan, or LLM work
- enforce the current effective global throughput caps in v1 by rejection: one
  running bulk scan globally and one running sync-or-LLM job globally, with
  new requests rejected with `409` while the family is busy
- accept the product consequence that a user can be told "busy, try again
  later" while a long job runs; no queueing and no automatic promotion in v1
- serialize each cap check with a per-family PostgreSQL advisory lock and run
  a global stale sweep before every cap evaluation
- start with the same live worker CPU and memory reference points: sync
  `1 vCPU / 512 MiB`, bulk `1 vCPU / 1 GiB`, and LLM `1 vCPU / 512 MiB`
- start with timeouts of `24h` for sync, `72h` for bulk scan, and `24h` for
  LLM term classification
- revisit timeouts or resource sizes only after staging or production metrics
  show CPU saturation, OOM pressure, or legitimate timeout pressure

## Local Development Implications

Local development will need a new shape after cutover.

Likely direction:

- keep the backend container or local app server as-is
- remove the Redis dependency from the background path
- run the job runner manually for local debugging when needed

Examples:

```bash
python -m app.jobs.runner shopify-sync --job-id 123
python -m app.jobs.runner shopify-bulk-scan --job-id 456
python -m app.jobs.runner llm-term-classification classify --job-id 789
```

Why this is better locally:

- fewer moving parts
- no need to keep Redis and Celery running just to test one background job

## Recommendation

Move this repo's background execution from always-on Cloud Run worker pools to
Cloud Run Jobs.

Keep:

- the existing DB-backed job tables
- the existing business processor functions
- the current API contract and polling model

Replace:

- Celery task dispatch
- Redis broker transport
- always-on worker pools

Why this is the recommended decision:

- it directly addresses idle cost
- it supports the stated 1-2 day job duration requirement
- it aligns with how the codebase already models job state
- it removes infrastructure that currently exists only to keep pull workers
  alive

## External References

- Cloud Run overview:
  <https://docs.cloud.google.com/run/docs/overview/what-is-cloud-run>
- Deploy worker pools:
  <https://docs.cloud.google.com/run/docs/deploy-worker-pools>
- Execute jobs:
  <https://docs.cloud.google.com/run/docs/execute/jobs>
- Create jobs:
  <https://docs.cloud.google.com/run/docs/create-jobs>
- Job task timeout:
  <https://docs.cloud.google.com/run/docs/configuring/task-timeout>
- Cloud Run service request timeout:
  <https://docs.cloud.google.com/run/docs/configuring/request-timeout>
- Cloud Tasks with Cloud Run:
  <https://docs.cloud.google.com/run/docs/triggering/using-tasks>
- Configure secrets for jobs:
  <https://docs.cloud.google.com/run/docs/configuring/jobs/secrets>
- Configure service identity for jobs:
  <https://docs.cloud.google.com/run/docs/configuring/jobs/service-identity>
- Connect Cloud Run to Cloud SQL for PostgreSQL:
  <https://docs.cloud.google.com/sql/docs/postgres/connect-run>
- Cloud Run pricing:
  <https://cloud.google.com/run/pricing>
