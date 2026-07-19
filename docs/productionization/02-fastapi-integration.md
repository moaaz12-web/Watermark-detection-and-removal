# Phase 6: FastAPI Watermark Job Orchestration Plan

Status: Proposed

Last updated: 2026-07-17

Implementation order: After the GPU worker foundation

## 1. Objective

Phase 6 adds an authenticated, durable watermark-removal job workflow to the existing FastAPI backend after the worker described in [01-gpu-worker-setup.md](01-gpu-worker-setup.md) has passed a manual Google Cloud Batch GPU execution.

The backend remains the authority for users, authorization, application job state, and result access. It does not run model inference. The existing Cloud Run Jobs mechanism performs short-lived orchestration, and Google Cloud Batch creates the temporary T4 VM that runs the GPU worker.

The first backend milestone is:

> An authorized user creates a watermark job for owned assets, FastAPI stores a durable application job and immutable GCS manifest, Google accepts a Cloud Run orchestration execution, that execution submits one Google Cloud Batch T4 job, and the user can retrieve a final `SUCCEEDED` or `FAILED` state and authorized result references from FastAPI.

## 2. Prerequisites

Do not begin this integration until the worker foundation has demonstrated all of the following:

- A versioned manifest contract exists.
- A versioned output/result contract exists.
- The production pipeline configuration and model files are pinned.
- A container image processes local and GCS manifests.
- The worker is idempotent at item level.
- A manual on-demand `n1-standard-4` Batch execution with one attached T4 succeeds.
- Representative runtime, VRAM, RAM, and output quality have been measured.

If those conditions are not met, changes to the backend schema and API are likely to be based on guesses.

## 3. System architecture

```text
Client
  |
  | POST /v1/watermark-jobs
  v
FastAPI API service
  |- authenticate and authorize assets
  |- create PostgreSQL application job
  |- write immutable input manifest to Cloud Storage
  |- request a Cloud Run Job execution
  `- return HTTP 202 after Google accepts the execution
          |
          v
Cloud Run orchestration job
  |- receives APP_JOB_ID and MANIFEST_URI
  |- creates deterministic Google Cloud Batch job
  |- reports Batch job name
  `- exits
          |
          v
Google Cloud Batch
  |- creates on-demand n1-standard-4 VM with one T4
  |- installs/activates NVIDIA driver
  |- pulls immutable GPU worker image
  `- runs one worker task
          |
          v
GPU watermark worker
  |- reads manifest and source images from GCS
  |- loads models once
  |- writes per-item results and success markers to GCS
  |- reports progress
  `- writes final output manifest and exits
          |
          v
FastAPI status and reconciliation
  |- updates PostgreSQL state
  |- authorizes result access
  `- returns status/results to client
```

## 4. Terminology

The word "job" refers to three different resources. They must remain distinct in code and documentation.

### Application job

The PostgreSQL record owned by the FastAPI application. It represents the user-visible watermark operation and is the source of truth for authorization and product state.

Example ID:

```text
wm_01k2...
```

### Orchestration execution

One execution of the existing Cloud Run Job that submits or reconciles infrastructure work. It should complete quickly and must not perform watermark inference.

Example provider name:

```text
projects/PROJECT/locations/REGION/jobs/watermark-orchestrator/executions/EXECUTION
```

### Batch job

The Google Cloud Batch resource that creates a temporary Compute Engine GPU VM and runs the worker container.

Example deterministic ID:

```text
watermark-wm-01k2...
```

Never expose Cloud Run execution names or Batch resource names as the public application job ID.

## 5. Responsibilities and boundaries

### FastAPI API service

Owns:

- Authentication and tenant/user authorization.
- Request validation and quota enforcement.
- Application job and item records.
- Resolving internal asset IDs to approved GCS objects.
- Input manifest creation.
- Starting the Cloud Run orchestration execution.
- Public job status and result endpoints.
- Cancellation requests.
- Reconciliation of external provider state.
- Generating authorized, short-lived result download URLs.

Does not own:

- CUDA or model dependencies.
- GPU inference.
- Per-image temporary files.
- Direct manipulation of Batch VM lifecycle.

### Cloud Run orchestration job

Owns:

- Validating its small invocation contract.
- Submitting one deterministic Google Cloud Batch job.
- Using the approved GPU worker image digest and Batch configuration.
- Reporting the Batch resource name or stable error code.
- Exiting after submission/reconciliation.

Does not own:

- Public authentication.
- Store connection or user-facing business logic.
- Long-running inference.
- Direct access to the application database unless a later approved design explicitly requires it.

### Google Cloud Batch

Owns:

- Scheduling and provisioning the temporary GPU VM.
- Starting the worker task.
- Applying task timeout/retry settings.
- Recording infrastructure state and logs.
- Deleting job compute resources after execution.

### GPU worker

Owns:

- Manifest validation.
- Model lifecycle and inference.
- Per-item idempotency and artifact generation.
- Worker-level progress events.
- Final output manifest.

Does not own:

- Application database access.
- User authentication or authorization.
- Product billing decisions.

## 6. Application job state machine

Proposed public/internal states:

```text
QUEUED
  -> ORCHESTRATING
  -> PROVISIONING
  -> RUNNING
  -> SUCCEEDED

Any non-terminal state
  -> FAILED

QUEUED | ORCHESTRATING | PROVISIONING | RUNNING
  -> CANCEL_REQUESTED
  -> CANCELLED
```

### State definitions

| State | Meaning |
| --- | --- |
| `QUEUED` | Database job and manifest exist; orchestration has not yet been accepted. |
| `ORCHESTRATING` | Google accepted the Cloud Run Job execution or a durable orchestration event is being processed. |
| `PROVISIONING` | The Batch job exists and is waiting for or creating resources. |
| `RUNNING` | The Batch task/worker has started. |
| `SUCCEEDED` | The worker produced a valid final output manifest and the backend accepted it. |
| `FAILED` | The application, orchestration, Batch, or worker failed terminally. |
| `CANCEL_REQUESTED` | Cancellation is recorded and provider cancellation is pending. |
| `CANCELLED` | External execution is stopped and no further work should run. |

The API must not derive user-visible status solely from callbacks. A reconciliation process must periodically compare non-terminal application jobs with Cloud Run, Batch, and output-manifest state.

## 7. PostgreSQL model

The following is a logical schema. Adapt types, naming, tenancy fields, and migration style to the existing FastAPI codebase conventions.

### 7.1 `watermark_jobs`

| Column | Purpose |
| --- | --- |
| `id` | Public application job ID. |
| `tenant_id` | Tenant/account boundary when applicable. |
| `user_id` | User that created the job. |
| `status` | Application state enum. |
| `pipeline_version` | Approved worker pipeline version. |
| `worker_image_digest` | Immutable expected worker image digest. |
| `input_manifest_object` | Stable bucket/object reference, not a signed URL. |
| `output_manifest_object` | Expected output-manifest object reference. |
| `orchestrator_execution_name` | Cloud Run Job execution resource name. |
| `batch_job_name` | Google Cloud Batch resource name. |
| `total_items` | Number of requested items. |
| `processed_items` | Items with terminal worker results. |
| `succeeded_items` | Successfully inpainted or approved skipped items. |
| `failed_items` | Items with failures. |
| `blocked_items` | Items blocked by the safety gate. |
| `attempt` | Current orchestration/application attempt. |
| `idempotency_key` | Client request idempotency key scoped to the creator/tenant. |
| `error_code` | Stable machine-readable terminal error code. |
| `error_message` | Sanitized user-safe or operator-safe summary, according to existing conventions. |
| `created_at` | Creation time. |
| `orchestration_started_at` | Cloud Run orchestration accepted/started time. |
| `batch_started_at` | GPU task start time. |
| `completed_at` | Terminal time. |
| `updated_at` | Last application state update. |

Required constraints:

- Unique application `id`.
- Unique `(tenant_id, user_id, idempotency_key)` when an idempotency key is provided.
- Unique non-null `batch_job_name`.
- Counters cannot be negative or exceed `total_items` in invalid combinations.
- Terminal states require `completed_at`.
- State updates use row locking or conditional updates to prevent invalid concurrent transitions.

### 7.2 `watermark_job_items`

| Column | Purpose |
| --- | --- |
| `id` | Internal row ID. |
| `job_id` | Parent application job. |
| `asset_id` | Existing application asset reference. |
| `status` | Item state. |
| `source_object` | Stable GCS bucket/object reference. |
| `output_prefix` | Stable GCS output prefix. |
| `result_object` | Result JSON reference. |
| `final_object` | Final image reference. |
| `combined_mask_object` | Review-mask reference when retained. |
| `source_checksum_sha256` | Source version identity. |
| `decision` | Inpainted, safety-blocked, clean-skipped, or failed. |
| `error_code` | Stable worker item error code. |
| `processing_seconds` | Reported item duration. |
| `completed_at` | Item terminal time. |

Required constraints:

- Unique `(job_id, asset_id)` unless repeated processing of one asset is an explicit product requirement.
- Unique `(job_id, output_prefix)`.
- Output references must not be accepted from an untrusted client.

### 7.3 Optional transactional outbox

If the existing backend already uses a durable queue or outbox, follow that pattern. Otherwise add an outbox table so job creation and orchestration intent can be committed atomically.

Example event:

```json
{
  "eventId": "evt_123",
  "type": "WATERMARK_JOB_CREATED",
  "aggregateId": "wm_123",
  "schemaVersion": 1
}
```

The outbox prevents this failure:

```text
Database job commits
  -> Cloud Run execution request fails before being accepted
  -> no durable record remains to retry orchestration
```

If the API synchronously requests the Cloud Run execution before returning, a periodic scanner for stale `QUEUED` jobs is still required to close partial-failure gaps.

## 8. Public API contract

Endpoint names and error envelopes should follow the existing FastAPI application conventions. The examples below define behavior, not mandatory casing if the current API uses another established convention.

### 8.1 Create job

```http
POST /v1/watermark-jobs
Authorization: Bearer <user-token>
Idempotency-Key: <client-generated-key>
Content-Type: application/json
```

```json
{
  "assetIds": ["asset_123", "asset_456"],
  "options": {
    "pipelineVersion": "watermark-v1"
  }
}
```

Prefer server-selected pipeline versions. If `pipelineVersion` is accepted from clients, it must be restricted to an allowlist and authorized by product policy.

Success response:

```http
HTTP/1.1 202 Accepted
```

```json
{
  "id": "wm_123",
  "status": "ORCHESTRATING",
  "pipelineVersion": "watermark-v1",
  "totalItems": 2,
  "processedItems": 0,
  "failedItems": 0,
  "createdAt": "2026-07-17T10:00:00Z"
}
```

Required behavior:

- Authenticate the caller.
- Verify access to every asset.
- Reject duplicate asset IDs unless explicitly supported.
- Enforce file count, size, plan, and concurrency limits.
- Resolve asset IDs to server-controlled GCS objects.
- Create deterministic output prefixes.
- Create the application job and item records.
- Write the immutable manifest.
- Persist durable orchestration intent or wait for Google to accept the Cloud Run execution.
- Return the existing job for a repeated idempotency key with the same payload.
- Return `409` for the same idempotency key with a different payload.

### 8.2 Get job

```http
GET /v1/watermark-jobs/{jobId}
```

```json
{
  "id": "wm_123",
  "status": "RUNNING",
  "pipelineVersion": "watermark-v1",
  "totalItems": 5000,
  "processedItems": 1830,
  "succeededItems": 1800,
  "failedItems": 4,
  "blockedItems": 26,
  "createdAt": "2026-07-17T10:00:00Z",
  "startedAt": "2026-07-17T10:04:00Z",
  "completedAt": null,
  "error": null
}
```

The response must not expose provider service-account details, raw stack traces, signed callback tokens, or unrestricted GCS paths.

### 8.3 List jobs

```http
GET /v1/watermark-jobs?status=RUNNING&page=1&pageSize=20
```

The endpoint must be paginated and tenant/user scoped.

### 8.4 List job items

```http
GET /v1/watermark-jobs/{jobId}/items?page=1&pageSize=50&status=FAILED
```

The endpoint returns item metadata and authorized result links or stable result identifiers. It must not generate thousands of signed URLs unnecessarily in a single response.

### 8.5 Cancel job

Model cancellation as a subresource or follow the existing API command convention. One possible contract is:

```http
POST /v1/watermark-jobs/{jobId}/cancellations
```

Cancellation is idempotent. It records `CANCEL_REQUESTED` first, then cancels/deletes active provider execution as appropriate, and finally records `CANCELLED` after reconciliation.

## 9. Error envelope

Use the existing API error format. If none exists, use one stable shape:

```json
{
  "error": {
    "code": "WATERMARK_JOB_LIMIT_EXCEEDED",
    "message": "The job contains more images than your current limit allows.",
    "details": {
      "maximumItems": 5000
    }
  }
}
```

Suggested status mapping:

| HTTP status | Usage |
| ---: | --- |
| `400` | Malformed request or unsupported option. |
| `401` | Not authenticated. |
| `403` | Caller cannot access one or more assets/job results. |
| `404` | Job does not exist within the caller's scope. |
| `409` | Idempotency conflict, invalid state transition, or concurrency conflict. |
| `422` | Valid JSON with invalid asset list or job configuration. |
| `429` | User/tenant watermark concurrency or rate limit exceeded. |
| `503` | Orchestration provider temporarily unavailable. |

Internal errors must be logged with correlation IDs but not returned as raw exception strings.

## 10. Manifest creation

FastAPI, not the browser or GPU worker, creates the manifest.

For each requested asset:

1. Load the asset through the existing authorized repository/service layer.
2. Verify tenant/user access.
3. Confirm the object is in an approved input bucket.
4. Resolve or calculate source checksum and file metadata.
5. Generate the output prefix from trusted internal IDs.
6. Insert the corresponding `watermark_job_items` row.
7. Add the item to the immutable manifest.

Store this reference in PostgreSQL:

```text
bucket: watermark-jobs
object: tenant-17/wm_123/input-manifest.json
```

Do not store a signed URL. Signed URLs expire and should be generated only when an authorized client needs temporary object access.

The input bucket, output bucket, Batch region, and GPU resources should use the same single GCP region where practical to avoid unnecessary transfer cost and latency.

## 11. Starting the Cloud Run orchestration job

### 11.1 Do not use FastAPI `BackgroundTasks`

This is unsafe:

```python
background_tasks.add_task(run_cloud_job, job.id)
return job
```

The task exists only inside the API process after the response. A restart, scale-down, deployment, or process failure can remove it before Google accepts the execution.

Use either:

- The existing durable queue/outbox mechanism, or
- A synchronous Cloud Run Jobs API request before returning HTTP `202`.

"Synchronous" means waiting only for Google to accept the execution request, not waiting for orchestration or GPU processing to finish.

### 11.2 Invocation contract

The API passes only minimal, non-secret execution overrides:

```text
APP_JOB_ID=wm_123
MANIFEST_URI=gs://watermark-jobs/tenant-17/wm_123/input-manifest.json
OUTPUT_MANIFEST_URI=gs://watermark-output/tenant-17/wm_123/output-manifest.json
PIPELINE_VERSION=watermark-v1
```

The API stores the returned Cloud Run execution resource name and transitions the application job to `ORCHESTRATING`.

If the execution request fails before acceptance, keep a durable retry path. Do not report the job as running.

## 12. Cloud Run orchestration behavior

The orchestration job performs these steps:

1. Validate required environment variables.
2. Construct the deterministic Batch job ID from the application job ID.
3. Build a Batch request using approved server configuration.
4. Submit the Batch job with one `n1-standard-4` and one attached T4, one task, and the immutable worker image digest.
5. If the deterministic Batch job already exists, treat that as an idempotent replay and retrieve it.
6. Report the Batch resource name through the approved internal event/callback path.
7. Exit `0` after submission is confirmed.

It must not accept a caller-provided machine type, arbitrary container image, service account, shell command, or bucket. Those are deployment configuration controlled by operators.

The orchestration job is intentionally short. The 10-15 hour GPU pipeline runs through Google Cloud Batch, not Cloud Run Jobs. Cloud Run GPU job tasks currently have a one-hour maximum, which is incompatible with this workload without sharding.

Official reference:

- [Cloud Run Job task timeout](https://docs.cloud.google.com/run/docs/configuring/task-timeout)

## 13. Deterministic provider identity and idempotency

Use a deterministic, normalized Batch job ID derived from the immutable application job ID:

```text
watermark-<application-job-id>
```

This prevents repeated orchestration delivery from creating duplicate billable GPU jobs.

Required cases:

| Situation | Behavior |
| --- | --- |
| First submission | Create Batch job and store its name. |
| Orchestrator retries after timeout | Look up deterministic Batch ID before creating. |
| Batch create returns already exists | Validate it belongs to the same application job and treat as success. |
| Application job already has a different Batch name | Stop and raise an operator-visible conflict. |
| Same API idempotency key and same request | Return the existing application job. |
| Same API idempotency key and different request | Return `409`. |

## 14. Progress reporting

The GPU worker should not update PostgreSQL directly. Use one of these patterns:

### Preferred initial pattern: authenticated callback plus reconciliation

The worker sends progress after a configurable number of items or interval:

```http
POST /internal/v1/watermark-jobs/{jobId}/events
Authorization: Bearer <service-identity-token>
```

```json
{
  "schemaVersion": 1,
  "eventId": "evt_123",
  "type": "PROGRESS",
  "attempt": 1,
  "processedItems": 1830,
  "succeededItems": 1800,
  "failedItems": 4,
  "blockedItems": 26,
  "occurredAt": "2026-07-17T12:00:00Z"
}
```

The backend verifies service identity, job/attempt association, monotonically valid counters, and event idempotency before updating PostgreSQL.

### Reconciliation

A scheduled existing backend worker periodically checks non-terminal jobs:

- Cloud Run execution state while `ORCHESTRATING`.
- Batch state while `PROVISIONING` or `RUNNING`.
- Output-manifest existence for terminal worker success.
- Stale jobs with no recent progress.

Callbacks improve freshness; reconciliation provides correctness.

## 15. Completion handling

Do not mark a job `SUCCEEDED` merely because Batch reports a successful task exit.

Success requires:

1. Batch job/task reports success.
2. Expected output manifest exists.
3. Output manifest schema is supported.
4. `jobId`, pipeline version, source identities, and worker image digest match expectations.
5. Referenced item result objects exist or pass the chosen validation level.
6. Job/item counters are internally consistent.

Then update item rows and the application job in a transaction and set `completed_at`.

If Batch succeeds but the output manifest is absent or invalid, mark the application job `FAILED` with a stable output-validation error and retain operator logs.

## 16. Cancellation

Cancellation is asynchronous:

1. Authorize the caller against the application job.
2. Atomically change an eligible state to `CANCEL_REQUESTED`.
3. Cancel/delete the active Cloud Run execution or Batch job using provider APIs as appropriate.
4. Reconcile until provider work is stopped.
5. Mark `CANCELLED` and set `completed_at`.

The GPU worker should handle `SIGTERM` by completing or recording a safe item checkpoint when the shutdown window permits. Already-uploaded compatible `_COMPLETE.json` markers remain reusable for an explicitly created retry job if product policy allows it.

## 17. Retry policy

Different failures require different retry decisions.

| Failure | Initial policy |
| --- | --- |
| Cloud Run execution request rejected transiently | Retry through durable orchestration mechanism with backoff. |
| Orchestrator exits before Batch submission confirmation | Retry; deterministic Batch ID prevents duplicates. |
| GPU resource temporarily unavailable | Allow Batch/provider retry or operator retry according to provider status. |
| Worker process crashes after partial progress | Retry once after resumability is proven. |
| Invalid manifest or unsupported pipeline version | Do not retry automatically. |
| Invalid/corrupt single image | Record item failure and continue. |
| Model checksum or missing-model failure | Do not repeatedly retry; alert operators. |
| Output permission failure | Fail and alert; retry only after configuration is corrected. |

Automatic retries for a 10-15 hour GPU workload can be expensive. Keep retry counts conservative and rely on per-item checkpoints.

## 18. Environment configuration boundaries

### FastAPI API service

Needs existing application configuration plus:

```text
WATERMARK_FEATURE_ENABLED
WATERMARK_INPUT_BUCKET
WATERMARK_JOB_BUCKET
WATERMARK_OUTPUT_BUCKET
WATERMARK_PIPELINE_VERSION
WATERMARK_CLOUD_RUN_JOB_NAME
WATERMARK_CLOUD_RUN_REGION
WATERMARK_MAX_ITEMS_PER_JOB
WATERMARK_MAX_CONCURRENT_JOBS_PER_TENANT
```

It retains the PostgreSQL, store, authentication, and existing application environment variables because it owns those concerns.

### Cloud Run orchestration job

Needs:

```text
GCP_PROJECT_ID
GCP_BATCH_REGION
GCP_BATCH_ALLOWED_LOCATIONS
GCP_BATCH_MACHINE_TYPE=n1-standard-4
GCP_BATCH_ACCELERATOR_TYPE=nvidia-tesla-t4
GCP_BATCH_ACCELERATOR_COUNT=1
GCP_BATCH_PROVISIONING_MODEL=STANDARD
GCP_BATCH_SERVICE_ACCOUNT
WATERMARK_WORKER_IMAGE_DIGEST
WATERMARK_TASK_TIMEOUT_SECONDS
WATERMARK_TASK_RETRY_COUNT
WATERMARK_CALLBACK_URL
```

Invocation overrides provide `APP_JOB_ID`, manifest URI, output-manifest URI, and pipeline version.

It does not need store/PostgreSQL environment values if it communicates through the manifest and authenticated internal events. Therefore, store connection changes should be applied only to services that actually use that connection.

### GPU Batch worker

Receives:

```text
JOB_ID
MANIFEST_URI
OUTPUT_MANIFEST_URI
PIPELINE_VERSION
CALLBACK_URL
PROGRESS_INTERVAL_ITEMS
```

It authenticates to GCP using its assigned service account. Do not place a service-account JSON key in environment variables.

## 19. IAM design

Use separate service accounts for the three runtime roles.

### API service account

Minimum intended access:

- Execute the specific Cloud Run orchestration job.
- Read/write only required job manifest objects, according to current application flow.
- Existing application permissions.

It should not have permission to run arbitrary container images or administer all Batch resources.

### Cloud Run orchestrator service account

Minimum intended access:

- Create/get the relevant Google Cloud Batch jobs.
- Act as the approved GPU worker service account.
- Invoke the protected internal progress/submission callback if used.
- Read the manifest only if orchestration validation requires it.

### GPU worker service account

Minimum intended access:

- Pull the approved Artifact Registry image.
- Read approved model, manifest, and input objects.
- Write approved output objects.
- Write logs.
- Invoke the protected progress callback or publish approved events.

It must not have PostgreSQL credentials, application admin roles, or permission to create more Batch jobs.

## 20. Security requirements

- Authorize every requested asset before job creation.
- Generate GCS locations from trusted internal IDs.
- Do not accept arbitrary `gs://` paths from clients.
- Validate callback identity and intended audience.
- Make internal progress events idempotent.
- Restrict job creation rate, batch size, and concurrent GPU jobs per tenant.
- Store stable object references, not signed URLs, in PostgreSQL.
- Generate short-lived result URLs only after authorization.
- Do not expose provider errors, stack traces, or service-account identities publicly.
- Audit who created, cancelled, or retried each job.
- Treat worker output manifests as external input and validate them before database updates.

## 21. Observability

Required correlation fields across API, Cloud Run, Batch, and worker logs:

```text
applicationJobId
tenantId
orchestratorExecutionName
batchJobName
pipelineVersion
workerImageDigest
attempt
eventId
```

Required operational metrics:

- Jobs created, running, succeeded, failed, and cancelled.
- Orchestration submission latency and failures.
- Batch provisioning latency.
- GPU execution duration.
- Images processed per job and per hour.
- Item failure/block/skip rates.
- Jobs with stale progress.
- Duplicate-submission prevention events.
- Estimated compute cost per job when billing labels/exports are available.

Alerts should cover:

- Repeated orchestration failure.
- Jobs stuck in a non-terminal state beyond configured thresholds.
- Model/checksum failures.
- Missing output manifest after Batch success.
- Permission failures.
- Unexpected growth in job failure rate or runtime.

## 22. Testing strategy

### Unit tests

- Job creation validation and authorization decisions.
- State transition rules.
- Idempotency-key behavior.
- Deterministic provider job-name generation.
- Manifest construction.
- Output-manifest validation.
- Callback event validation and replay handling.
- Error mapping.

### API integration tests

- Authorized create/get/list flow.
- Unauthorized asset access is rejected without partial job creation.
- Duplicate request with same idempotency key returns the same job.
- Conflicting idempotency payload returns `409`.
- Cloud Run execution request failure leaves a retryable, consistent state.
- Result endpoints never expose another tenant's objects.

### Orchestration tests

- Cloud Run Job creates the expected Batch request.
- Repeated execution reuses the deterministic Batch job.
- Unapproved machine/image overrides are impossible.
- Batch submission error produces a stable callback/event.

### End-to-end staging test

```text
POST application job
  -> PostgreSQL records
  -> GCS input manifest
  -> Cloud Run execution
  -> Batch T4 task
  -> GPU worker output
  -> callback/reconciliation
  -> GET application job returns SUCCEEDED
```

Run with one image, then five, then a representative medium batch. Include cancellation and a deliberately corrupt item.

## 23. Implementation tasks

### B1. Record backend integration contract

Acceptance:

- Existing backend conventions for IDs, errors, schemas, migrations, workers, and authorization are identified.
- Worker manifest/result schemas are copied or referenced as versioned contracts.
- OpenAPI behavior for create/get/list/items/cancel is approved.

Verify:

- Contract review against the completed worker and existing API conventions.

### B2. Add application job persistence

Acceptance:

- Job and item migrations/models exist.
- State constraints and idempotency uniqueness are enforced.
- Repository/service tests cover conditional state transitions.

Verify:

- Migration applies and rolls back in a disposable environment; persistence tests pass.

### B3. Implement authorized manifest creation

Acceptance:

- Asset IDs are authorized and resolved to approved objects.
- Job, item records, and manifest metadata remain consistent on failure.
- Manifest matches worker schema version 1.

Verify:

- Integration tests inspect the generated test-bucket manifest and unauthorized requests.

### B4. Implement job creation and status endpoints

Acceptance:

- `POST`, `GET`, and paginated list endpoints follow the approved contract.
- Request idempotency works.
- Job status is tenant/user scoped.

Verify:

- API tests cover success, validation, authorization, idempotency, and limits.

### B5. Implement durable Cloud Run orchestration trigger

Acceptance:

- The existing durable worker/outbox pattern or synchronous acceptance pattern is used.
- FastAPI `BackgroundTasks` is not used for critical submission.
- Cloud Run execution name is stored.
- Transient submission failure can be retried.

Verify:

- Simulate API/process interruption and confirm a queued job is eventually orchestrated once.

### B6. Implement the Cloud Run Batch submitter

Acceptance:

- The orchestrator submits the approved worker digest to one on-demand `n1-standard-4` Batch task with one attached T4.
- Batch job ID is deterministic.
- Duplicate orchestration is idempotent.
- Stable submission events are reported.

Verify:

- Execute twice for one staging application job and confirm only one Batch job exists.

### B7. Implement progress events and reconciliation

Acceptance:

- Internal events are authenticated, validated, and idempotent.
- Reconciliation maps Cloud Run/Batch/output state to valid application transitions.
- Stale jobs are detected.

Verify:

- Drop or replay callbacks in tests and confirm eventual correct application state.

### B8. Implement completion and result access

Acceptance:

- Output manifest is validated before `SUCCEEDED`.
- Item records and counters are updated transactionally.
- Authorized users can retrieve result metadata and temporary download access.

Verify:

- Test valid, missing, corrupt, mismatched, and cross-tenant output manifests.

### B9. Implement cancellation and conservative retry

Acceptance:

- Eligible jobs can be cancelled idempotently.
- Provider cancellation is reconciled.
- Retry creates no duplicate GPU job and respects product limits.

Verify:

- Cancel jobs in orchestration, provisioning, and running states in staging.

### B10. Add rollout controls and observability

Acceptance:

- Feature is disabled by default outside staging.
- Per-tenant concurrency and batch-size limits exist.
- Logs, dashboards, and alerts correlate all resource IDs.
- Budget monitoring is configured.

Verify:

- Run the staging end-to-end checklist and review logs, metrics, permissions, and estimated cost.

## 24. Rollout plan

1. Deploy database/API changes behind `WATERMARK_FEATURE_ENABLED=false`.
2. Deploy the Cloud Run orchestrator with no public invocation path.
3. Grant least-privilege IAM in staging.
4. Enable the feature for internal test users only.
5. Run one-image and five-image end-to-end jobs.
6. Run a representative medium batch.
7. Test duplicate submission, API restart, callback loss, worker retry, and cancellation.
8. Review output quality and cost.
9. Enable for a small tenant/user cohort with strict concurrency limits.
10. Expand only after runtime, failures, and spend remain within approved limits.

## 25. Completion criteria

FastAPI integration is complete when:

- Public APIs are authenticated, authorized, typed, documented, and tested.
- Application jobs and items have durable, valid state transitions.
- Input manifests are generated only from authorized internal assets.
- Critical orchestration does not depend on in-process `BackgroundTasks`.
- Cloud Run orchestration submits exactly one deterministic Batch job.
- The GPU worker has no database or store credentials.
- Progress callbacks are optional for correctness because reconciliation exists.
- Batch success is validated against a compatible output manifest.
- Cancellation and retry are idempotent.
- GCS result access is authorized and temporary.
- End-to-end staging tests pass through a real T4 Batch execution.
- Logs, alerts, quotas, and budget controls are in place before broad rollout.

## 26. Open decisions before B1

- What are the existing FastAPI worker/queue and transactional patterns?
- Should the API request Cloud Run execution synchronously or through the existing durable queue/outbox?
- What user/tenant plan limits apply to image count, storage, and concurrent GPU jobs?
- Which item outcomes count as overall job success with warnings?
- What progress granularity does the product need?
- How long are originals, masks, debug artifacts, and final images retained?
- What cancellation behavior is promised to users?
- Can users retry failed items, or only create a new job?
- Which internal callback authentication method matches the current deployment?
- Which API response fields follow existing snake_case or camelCase conventions?

## 27. Official platform references

- [FastAPI Background Tasks caveat](https://fastapi.tiangolo.com/tutorial/background-tasks/#caveat)
- [Execute Cloud Run Jobs and apply per-execution overrides](https://docs.cloud.google.com/run/docs/execute/jobs)
- [Configure environment variables for Cloud Run Jobs](https://docs.cloud.google.com/run/docs/configuring/jobs/environment-variables)
- [Cloud Run service background activity guidance](https://docs.cloud.google.com/run/docs/tips/general)
- [Cloud Run Job task timeout](https://docs.cloud.google.com/run/docs/configuring/task-timeout)
- [Google Cloud Batch GPU jobs](https://docs.cloud.google.com/batch/docs/create-run-job-gpus)
- [Google Cloud Batch lifecycle](https://docs.cloud.google.com/batch/docs/create-run-job)
- [Cloud Storage pricing and same-location transfer behavior](https://cloud.google.com/storage/pricing)
