# Prompt for the FastAPI agent: implement A10 locally without deployment

Copy everything below this introductory sentence and give it to the agent working inside the
FastAPI backend repository.

---

You are working inside the existing FastAPI backend repository. Your objective is to implement
**the local code portion of A10: FastAPI integration for hybrid GCP-to-AWS GPU orchestration**.
This task is strictly local-only:

```text
Public client
  -> FastAPI and PostgreSQL in GCP
  -> immutable generation-pinned manifest in GCS
  -> durable CPU-only GCP orchestration
  -> authenticated internal AWS API
  -> AWS Batch GPU worker
  -> immutable results and completion markers in GCS
  -> GCP reconciliation and output validation
  -> authorized public FastAPI result
```

The GPU worker, AWS submission API, AWS Batch foundation, keyless AWS-to-GCP access, one-image
acceptance, five-image acceptance, completed replay, provider status, and provider cancellation
have already been implemented and proven. Do not rebuild those components in the FastAPI
repository.

The FastAPI backend and PostgreSQL remain the application source of truth. AWS is only the GPU
execution provider.

## 0. Strict local-only execution boundary

You may:

- inspect both repositories;
- edit files only in the local FastAPI repository;
- create the database migration file without applying it to any existing database;
- write application, orchestrator, reconciler, and test code;
- run hermetic unit/static tests;
- run database tests only when the existing test harness automatically provisions and destroys an
  isolated disposable database; and
- write local implementation/handoff documentation.

You must not:

- commit, push, merge, or open a pull request;
- deploy the FastAPI backend, Cloud Run service, Cloud Run Job, scheduler, worker, or reconciler;
- run `gcloud run deploy`, `gcloud run jobs deploy`, `gcloud builds submit`, Terraform apply,
  Cloud Build deployment, or an equivalent deployment command;
- apply, upgrade, or downgrade a migration against the user's local development database,
  staging database, production database, or any configured persistent database;
- change GCP or AWS IAM;
- create, update, enable, disable, or delete any cloud resource;
- call the deployed AWS API, even for a status-only probe;
- mint a real Google ID token;
- enable AWS submission or the AWS Batch queue;
- launch a Cloud Run execution, AWS Batch job, EC2 instance, or GPU;
- modify the AWS worker repository; or
- modify Git history or staging state.

The repository owner will handle Git operations, migration execution, infrastructure changes,
deployment, cloud configuration, and all live testing later. Do not create a deployment/runbook
section for those owner-managed actions.

## 1. First establish repository context

Before editing any file:

1. Print and record the absolute path of the FastAPI repository:

   ```bash
   pwd
   ```

2. Read every applicable repository instruction file, including `AGENTS.md`, `CLAUDE.md`,
   contributing documentation, and nested instruction files.
3. Inspect the existing backend rather than assuming its architecture. Locate:

   - FastAPI application entry points and router registration;
   - authentication, current-user, tenant, organization, and authorization dependencies;
   - asset/image models, repositories, services, and GCS object references;
   - SQLAlchemy or other ORM conventions;
   - Alembic or other database migration conventions;
   - transaction, row-locking, optimistic-version, and idempotency patterns;
   - existing public error envelope and exception mapping;
   - existing pagination conventions;
   - existing durable queues, outbox tables, task workers, schedulers, Cloud Tasks, Pub/Sub,
     Cloud Run Jobs, or stale-record scanners;
   - existing GCS upload/download wrappers and generation-precondition support;
   - existing Google service-account and ID-token helpers;
   - application configuration/settings conventions;
   - local packaging and deployment-file conventions, for compatibility only;
   - unit, API, database, and integration-test conventions.

4. Run only local/hermetic baseline tests, formatter/linter, and type checker before changing
   behavior. Do not run tests that access persistent databases or live cloud services. Record the
   exact commands and results.
5. Check the working tree and preserve unrelated user changes. Do not reset, discard, overwrite,
   or stage unrelated work.

Do not start by creating a second architecture beside existing backend patterns. Adapt A10 to the
repository's established conventions.

## 2. Authoritative source files

The worker/AWS repository is located at:

```text
/home/maaz/Desktop/watermarkPoc
```

Read the following files from that repository using their absolute paths.

### 2.1 Primary A10 specification

Primary implementation authority:

```text
/home/maaz/Desktop/watermarkPoc/docs/productionization/03-aws-gpu-worker-setup.md
```

Read these sections completely:

- Section 4: Architecture
- Section 5: Authority and responsibilities
- Section 6: Preserved worker contracts
- Section 7: Internal cross-cloud API contract
- Section 8: Submission idempotency
- Section 9.1: GCP to AWS identity
- Section 10: Storage and model strategy
- Section 14: Retry and exit behavior
- Section 15: Provider state mapping
- Section 16: Progress, events, and reconciliation
- Section 17: Cancellation
- Section 18: Security boundaries
- Section 19: Observability and cost
- Section 21: Testing strategy
- Section A9: Complete the AWS provider status and cancellation API
- **Sections A10.1 through A10.5 only:** local FastAPI, manifest, orchestrator, reconciliation, and
  cancellation implementation
- Section 23: Boundaries

The A10 section and the accepted AWS architecture override conflicting provider-specific text in
older documents.

Do not execute or reproduce A10.6 staged deployment/traffic instructions or A10.7 live
end-to-end-evidence work in this task. Those are owner-managed later activities.

### 2.2 Detailed FastAPI product and application design

Read:

```text
/home/maaz/Desktop/watermarkPoc/docs/productionization/02-fastapi-integration.md
```

Use its provider-neutral material for:

- application terminology and ownership;
- PostgreSQL application-job and item modeling;
- application state-machine intent;
- public API behavior;
- tenant and asset authorization;
- idempotency;
- immutable manifest creation;
- durable intent;
- result authorization;
- output validation;
- cancellation semantics;
- testing;
- observability; and
- safe local feature flags and limits.

Important precedence rule:

- `03-aws-gpu-worker-setup.md`, especially A9 and A10, is authoritative for the execution
  provider.
- Do **not** implement the older Google Cloud Batch submission path from
  `02-fastapi-integration.md`.
- Do **not** make the Cloud Run orchestrator submit Google Cloud Batch jobs.
- Do **not** use the old Google Batch machine, accelerator, service-account, retry, or job-name
  configuration.
- Do **not** run a representative medium batch. The accepted initial maximum is five images.
- Call the authenticated AWS API described below.

The following older provider-specific portions are superseded:

- the Google Cloud Batch part of the Section 3 diagram;
- the Google Batch responsibilities in Section 5;
- Section 12's Google Batch submission behavior;
- Google Batch-specific environment variables in Section 18;
- Google Batch-specific IAM in Section 19;
- task B6 where it describes creating a Google Batch job; and
- rollout steps that exceed the accepted five-image limit.

### 2.3 Accepted architecture decision

Read:

```text
/home/maaz/Desktop/watermarkPoc/docs/decisions/ADR-004-hybrid-gcp-control-aws-gpu-execution.md
```

Preserve its decisions:

- FastAPI, PostgreSQL, authorization, GCS manifests, source assets, outputs, and result delivery
  remain in GCP.
- AWS runs only the GPU execution plane.
- GCP-to-AWS uses a Google-signed service-account ID token.
- AWS-to-GCP uses Workload Identity Federation.
- No long-lived AWS access key is stored in GCP.
- No Google service-account key is stored in AWS.
- DynamoDB is only an AWS submission/idempotency ledger.
- PostgreSQL is authoritative for user-visible application state.
- AWS success is insufficient without compatible GCS output evidence.

### 2.4 Exact AWS API contract fixtures

Read all five fixtures:

```text
/home/maaz/Desktop/watermarkPoc/contracts/aws-gpu-v1/submit-request.json
/home/maaz/Desktop/watermarkPoc/contracts/aws-gpu-v1/submit-response.json
/home/maaz/Desktop/watermarkPoc/contracts/aws-gpu-v1/status-response.json
/home/maaz/Desktop/watermarkPoc/contracts/aws-gpu-v1/cancellation-request.json
/home/maaz/Desktop/watermarkPoc/contracts/aws-gpu-v1/error-response.json
```

Treat AWS responses as untrusted external input. Create strict typed models in the backend for the
request, success responses, status responses, cancellation responses, and stable error envelope.
Allow only explicitly intended additive compatibility fields. Never base application decisions on
unvalidated dictionaries.

### 2.5 Canonical submission-hash implementation

Read:

```text
/home/maaz/Desktop/watermarkPoc/services/aws_submission/src/aws_submission/canonical.py
/home/maaz/Desktop/watermarkPoc/services/aws_submission/src/aws_submission/models.py
```

The backend/orchestrator must reproduce this exact canonical request algorithm:

1. Include only these fields:

   ```text
   schemaVersion
   applicationJobId
   manifestUri
   manifestGeneration
   outputManifestUri
   pipelineVersion
   ```

2. Serialize JSON using:

   - keys sorted lexicographically;
   - compact separators `,` and `:`;
   - UTF-8;
   - no NaN values; and
   - no `requestSha256` field inside the hashed content.

3. Compute lowercase SHA-256 hex.
4. Send that value as `requestSha256`.
5. Persist it in PostgreSQL as immutable submission identity.

Add a golden-vector test using:

```text
/home/maaz/Desktop/watermarkPoc/contracts/aws-gpu-v1/submit-request.json
```

The computed value must equal the fixture's `requestSha256`.

### 2.6 Deployed AWS behavior and evidence

Read:

```text
/home/maaz/Desktop/watermarkPoc/aws-acceptance/evidence/a9-provider-control-api/a9-final-summary.json
```

This proves:

- authenticated provider status works;
- provider identity is revalidated by AWS;
- terminal cancellation is idempotent;
- stable negative responses work;
- no second callback Lambda exists;
- the existing API is deployed; and
- A9 finished with zero active GPU capacity.

Do not reimplement the AWS Lambda, DynamoDB ledger, API Gateway authorizer, or AWS Batch adapter in
the FastAPI repository.

### 2.7 Worker manifest and output authority

Read:

```text
/home/maaz/Desktop/watermarkPoc/docs/productionization/01-gpu-worker-setup.md
/home/maaz/Desktop/watermarkPoc/src/watermark_worker/contracts.py
/home/maaz/Desktop/watermarkPoc/src/watermark_worker/manifest.py
/home/maaz/Desktop/watermarkPoc/src/watermark_worker/result_writer.py
/home/maaz/Desktop/watermarkPoc/tests/unit/test_manifest.py
/home/maaz/Desktop/watermarkPoc/tests/unit/test_result_writer.py
```

In `01-gpu-worker-setup.md`, focus on:

- Section 7: Input contract
- Section 8: Output contract
- Section 9: Worker execution behavior
- Section 10: Exit behavior

The old Google provider setup in that document is not authoritative, but the worker's
schema-version-1 manifest/result/marker meanings remain relevant.

Also inspect proven examples:

```text
/home/maaz/Desktop/watermarkPoc/aws-acceptance/evidence/a7-one-image/input-manifest.json
/home/maaz/Desktop/watermarkPoc/aws-acceptance/evidence/a8-five-image-resume/a8_five_20260726t110131z-input-manifest.json
/home/maaz/Desktop/watermarkPoc/aws-acceptance/evidence/a8-five-image-resume/a8_five_20260726t110131z-outcome.json
```

Do not blindly copy acceptance bucket names into production application paths. Adapt the manifest
to the backend's real tenant/asset model while preserving the worker contract and deterministic
layout.

## 3. Existing deployed integration facts

Use these as configuration inputs, not hardcoded business logic:

```text
AWS_GPU_API_BASE_URL=https://g3qk2f8rsg.execute-api.eu-north-1.amazonaws.com/staging
AWS_GPU_API_AUDIENCE=urn:watermark:aws-gpu-submission:staging
AWS_GPU_REGION=eu-north-1
AWS_GPU_PROVIDER=AWS_BATCH
GCP_PROJECT_ID=project-5909a738-2fdc-451e-ad6
GCP_PROJECT_NUMBER=834057675927
GCP_ORCHESTRATOR_SERVICE_ACCOUNT=watermark-aws-orchestrator@project-5909a738-2fdc-451e-ad6.iam.gserviceaccount.com
WATERMARK_PIPELINE_VERSION=watermark-v1
WATERMARK_MAX_ITEMS_PER_JOB=5
WATERMARK_MAX_CONCURRENT_JOBS_PER_TENANT=1
```

Current safety state:

```text
AWS submission enabled: false
AWS Batch queue: disabled
AWS Batch desired vCPUs: 0
Active AWS GPU/EC2 instances: 0
```

Do not change these gates during ordinary implementation or hermetic testing.

The AWS API base URL and audience are not secrets. ID tokens and credentials are secrets and must
never be printed, logged, committed, placed in test snapshots, or stored in PostgreSQL.

### 3.1 Internal AWS routes

Submit or idempotently retrieve the provider reservation:

```http
PUT /internal/v1/gpu-jobs/{applicationJobId}
Authorization: Bearer <Google service-account ID token>
Content-Type: application/json
```

Get provider status:

```http
GET /internal/v1/gpu-jobs/{applicationJobId}
Authorization: Bearer <Google service-account ID token>
```

Request provider cancellation:

```http
PUT /internal/v1/gpu-jobs/{applicationJobId}/cancellation
Authorization: Bearer <Google service-account ID token>
Content-Type: application/json
```

Only the dedicated GCP orchestrator identity is accepted. This API is internal even though its
hostname is internet-routable. Browsers, frontend applications, mobile clients, and public
FastAPI request handlers must not call it directly.

### 3.2 Authentication behavior

At runtime, obtain a fresh Google-signed service-account ID token through the supported
Google-auth/metadata mechanism for the exact audience:

```text
urn:watermark:aws-gpu-submission:staging
```

Requirements:

- use the Cloud Run runtime identity;
- use short-lived tokens;
- cache only according to token expiry with safe refresh;
- never use a downloaded service-account JSON key;
- never manually decode an unverified JWT as authorization;
- never put the token in a URL, database row, exception message, or normal log;
- set bounded connection and read timeouts; and
- redact authorization headers in HTTP diagnostics.

When implementation details are uncertain, consult current official Google Cloud/Google Auth
documentation. Do not guess token-minting APIs.

## 4. Required architecture in the FastAPI repository

Implement the following logical boundaries using names and folders that match the existing
backend.

### 4.1 Public FastAPI application layer

Owns:

- user authentication;
- tenant and asset authorization;
- job and item creation;
- public idempotency;
- application state;
- GCS manifest creation;
- durable submission and cancellation intent;
- public status and result access; and
- sanitized public errors.

It must not:

- hold AWS credentials;
- expose AWS identifiers publicly;
- submit AWS Batch directly;
- wait for the GPU job;
- trust client-supplied GCS paths;
- run critical work with FastAPI `BackgroundTasks`; or
- mark success from AWS state alone.

### 4.2 Provider-neutral domain/service layer

Create a provider abstraction that makes the application state machine independent of AWS.
Provider-specific IDs may be stored internally, but public domain responses must remain
provider-neutral.

At minimum, define typed operations equivalent to:

```text
submit immutable provider request
get provider status
request provider cancellation
```

The AWS implementation calls the A9 API. Do not call AWS Batch, DynamoDB, EC2, ECR, or AWS SDKs
from the FastAPI control plane.

### 4.3 Durable submission dispatcher

Use the backend's existing durable queue/outbox/worker convention if one exists. Otherwise choose
the smallest repository-consistent durable pattern and document why.

Required behavior:

1. PostgreSQL contains the application job, items, immutable manifest identity, request hash, and
   durable submission intent before external execution can be lost.
2. Delivery can occur more than once.
3. Every delivery uses the same application job ID and immutable request content.
4. An uncertain HTTP outcome retries the same request.
5. A matching AWS replay returns/adopts the same provider job.
6. A conflict is terminal/operator-visible and never launches a second provider job.

Do not use an in-process fire-and-forget task.

### 4.4 CPU-only orchestrator code

Implement the local code/package intended for a future short-lived CPU-only Cloud Run Job:

```text
watermark-aws-orchestrator
```

It receives only:

```text
APP_JOB_ID
MANIFEST_URI
MANIFEST_GENERATION
OUTPUT_MANIFEST_URI
PIPELINE_VERSION
REQUEST_SHA256
```

Implement these as typed local runtime settings for the owner to configure later:

```text
AWS_GPU_API_BASE_URL
AWS_GPU_API_AUDIENCE
HTTP_CONNECT_TIMEOUT_SECONDS
HTTP_READ_TIMEOUT_SECONDS
MAX_SUBMISSION_ATTEMPTS
```

Behavior:

1. Validate its invocation contract.
2. Obtain an audience-bound Google ID token.
3. Call the AWS submission `PUT` route.
4. Strictly parse the success/error response.
5. Deliver the provider identity through the backend's existing durable application-event path.
6. Retry only retryable/uncertain outcomes using the same immutable request.
7. Exit after submission/retrieval.

It must not:

- run inference;
- wait for AWS Batch completion;
- contain PostgreSQL credentials;
- accept arbitrary AWS infrastructure settings;
- accept a caller-supplied AWS queue, role, image, command, region, or instance type;
- possess AWS access keys; or
- expose the ID token.

Do not deploy or execute this job. Test its token provider and HTTP boundary with injected fakes.
Do not mint a real token during this task.

If the backend has no existing durable event path for returning the provider identity, implement
the smallest authenticated durable mechanism consistent with the repository and document it.
Do not give the orchestrator direct database credentials merely for convenience.

### 4.5 Durable reconciler

Use the backend's existing scheduler/worker/Cloud Run Job pattern. Do not run reconciliation in a
request process or `BackgroundTasks`.

For each non-terminal application job due for reconciliation:

1. Acquire the repository's row/version lock.
2. Load the stored immutable provider identity.
3. Mint a fresh audience-bound token.
4. Call the A9 status route.
5. Validate that response `applicationJobId`, provider, provider job ID, provider job ARN, and
   attempt match PostgreSQL.
6. Reject mismatched identity as an integrity error.
7. Apply only allowed monotonic transitions.
8. Update `last_reconciled_at` and a bounded next-check time.
9. Retry transport, timeout, `429`, and retryable stable AWS errors with jitter without regressing
   application state.
10. Treat stable non-retryable integrity/conflict errors as operator-visible failures.

Initial polling guidance:

```text
QUEUED/PROVISIONING: 30-60 seconds
RUNNING:             60 seconds
CANCEL_REQUESTED:    15-30 seconds
maximum backoff:     5 minutes
```

Use existing scheduling capabilities. Do not introduce several queues, callbacks, or event systems
if one durable polling path is sufficient.

### 4.6 Output validator

AWS `SUCCEEDED` means only that the provider process exited successfully. Before application
`SUCCEEDED`, validate the exact expected GCS evidence:

- generation-pinned input manifest identity;
- output-manifest existence;
- job `_COMPLETE.json` existence;
- supported schema version;
- matching application job ID;
- matching pipeline version;
- matching manifest URI, generation, and checksum identity;
- expected worker/config/model/image identity where required by the worker contract;
- item IDs exactly match the authorized application items;
- item result and marker object references remain under deterministic approved prefixes;
- referenced object generations, sizes, and checksums are compatible;
- counters and item outcomes are internally consistent; and
- blocked, failed, unverified, and no-watermark outcomes do not expose an invalid `finalUri`.

Treat GCS manifests and markers as untrusted external input. Validate before database transitions
or public rendering.

Use a bounded consistency window after AWS `SUCCEEDED` because GCS evidence and provider state may
be observed at slightly different times. If compatible output remains absent or invalid after the
window, set a stable application failure.

### 4.7 Application cancellation

Cancellation is asynchronous:

1. FastAPI authenticates and tenant-authorizes the job.
2. In PostgreSQL, atomically transition an eligible state to `CANCEL_REQUESTED`.
3. Persist durable cancellation intent before the external call.
4. Durable orchestration calls the AWS cancellation route with:

   ```json
   {
     "schemaVersion": 1,
     "reasonCode": "USER_REQUESTED"
   }
   ```

5. Repeated cancellation requests are idempotent.
6. Reconciliation continues until provider work has stopped.
7. PostgreSQL transitions to `CANCELLED` and records completion time.
8. Immutable GCS markers remain evidence.
9. A provider `SUCCEEDED` race must still pass full output validation and the documented
   application cancellation policy; it must not silently overwrite durable cancellation intent.

Do not report immediate `CANCELLED` merely because AWS accepted the cancellation request.

## 5. Database requirements

Adapt names and types to existing conventions. Do not mechanically create a parallel job system if
the backend already has an extensible job table.

At minimum, persist provider-neutral fields equivalent to:

```text
compute_provider
provider_region
provider_job_id
provider_job_arn
provider_attempt
provider_status
manifest_generation
submission_request_sha256
last_reconciled_at
```

Also persist or reuse existing fields for:

```text
application job ID
tenant/user ownership
application status
pipeline version
input manifest bucket/object
output manifest bucket/object
total and terminal item counts
idempotency key and immutable public request hash
durable submission intent
durable cancellation intent
next reconciliation time/backoff
stable error code
created/started/completed timestamps
```

Required invariants:

- provider job ID is unique when non-null;
- provider job ARN cannot point at a different provider job than the stored ID;
- provider attempt is positive;
- manifest generation is required once orchestration begins;
- submission request hash is immutable;
- terminal states require `completed_at`;
- client idempotency is scoped to the correct tenant/user boundary;
- duplicate idempotency key with the same immutable public request returns the existing job;
- duplicate idempotency key with different content returns `409`;
- state transitions use row locks, compare-and-set, or existing optimistic concurrency;
- duplicate/out-of-order provider events cannot regress application state;
- old rows with null provider fields remain readable; and
- DynamoDB state never replaces PostgreSQL application authority.

Create an additive migration file, but do not run it against the user's configured local,
staging, or production database. If the existing automated test harness creates a fully isolated
temporary database, migration behavior may be exercised there. Otherwise use static/model tests
and give the owner the unexecuted upgrade, downgrade, and verification commands. Document that a
production downgrade must not run after live rows depend on the new fields unless data is safely
preserved.

## 6. Application and provider state mapping

Use the backend's existing state names if semantically equivalent. Preserve these meanings:

```text
QUEUED
  -> ORCHESTRATING
  -> PROVISIONING
  -> RUNNING
  -> SUCCEEDED

Any eligible non-terminal state
  -> FAILED

QUEUED | ORCHESTRATING | PROVISIONING | RUNNING
  -> CANCEL_REQUESTED
  -> CANCELLED
```

Initial AWS mapping:

| AWS provider state | Application interpretation |
| --- | --- |
| `RESERVED`, `SUBMITTING`, `SUBMISSION_UNCERTAIN` | Keep durable submission/reconciliation active |
| `SUBMITTED`, `PENDING`, `RUNNABLE` | `PROVISIONING` |
| `STARTING`, `RUNNING` | `RUNNING` |
| `SUCCEEDED` | Validate GCS output; never directly mark application success |
| `FAILED` | Reconcile output/attempt/error, then apply a stable application failure |
| `CANCELLATION_REQUESTED` | Keep application `CANCEL_REQUESTED` |
| `CANCELLED` | Application may become `CANCELLED` after reconciliation |

Transition logic must be explicit and tested. Do not compare enum strings lexicographically.
Terminal states must not regress. Replayed older states must be ignored safely.

## 7. Public API requirements

Implement or adapt these endpoints:

```text
POST /v1/watermark-jobs
GET  /v1/watermark-jobs/{jobId}
GET  /v1/watermark-jobs
GET  /v1/watermark-jobs/{jobId}/items
POST /v1/watermark-jobs/{jobId}/cancellations
```

Follow existing backend routing, casing, response, error, and pagination conventions when they
already exist.

Required behavior:

- authenticate every request;
- tenant/user-authorize every asset, job, item, and result;
- accept asset IDs, never arbitrary GCS URIs or AWS identifiers;
- reject duplicate asset IDs unless product behavior explicitly supports them;
- reject zero items and more than five items;
- enforce initial per-tenant concurrency of one active job;
- select `watermark-v1` server-side;
- create deterministic trusted GCS prefixes;
- return HTTP `202` for accepted asynchronous creation;
- make create idempotent;
- make cancellation idempotent;
- paginate job and item lists;
- preserve the backend's stable public error envelope;
- use short-lived authorized result delivery;
- never expose internal infrastructure.

Public responses must not contain:

- AWS provider job ID or ARN;
- AWS account or region details unless region is an intentional product field;
- API Gateway hostname;
- DynamoDB state;
- Cloud Run execution names;
- JWT claims or tokens;
- service-account identities;
- unrestricted GCS paths;
- raw AWS/GCP errors;
- stack traces;
- internal role/image/queue/definition identifiers; or
- unvalidated worker output references.

Provider identifiers may appear only in internal storage, internal typed events, restricted
operator logs, and internal reconciliation.

## 8. Manifest creation and durable intent

For each create request:

1. Authenticate the caller.
2. Load and authorize every asset through existing repository/service methods.
3. Verify every source object is in an approved bucket/prefix.
4. Resolve and pin the exact source object generation.
5. Resolve or verify source SHA-256 and required metadata.
6. Create the application job and item identities.
7. Generate deterministic output prefixes from trusted tenant/job/item IDs.
8. Build a schema-version-1 manifest compatible with the worker models.
9. Upload the manifest with GCS create-only semantics, such as generation-match zero through the
   existing storage abstraction.
10. Capture the returned manifest generation.
11. Build the exact AWS submission request.
12. Compute and persist the canonical request hash.
13. Persist durable `AWS_GPU_SUBMISSION_REQUESTED` intent using the existing outbox/queue pattern.
14. Return the application job without waiting for GPU processing.

Handle partial failures explicitly:

- Database commit succeeds but dispatch fails: durable intent/stale-job scanning retries it.
- Manifest upload succeeds but database commit fails: deterministic orphan cleanup handles it only
  after an age threshold.
- HTTP response is lost after AWS acceptance: retry the exact same `PUT`; DynamoDB returns or
  reconciles the one provider job.
- Different immutable content appears for the same application job ID: stop with conflict; never
  mutate the reservation or create another provider job.

The currently deployed AWS prefix allowlist was built for acceptance paths. A later staging
rollout may require a coordinated AWS Terraform allowlist update for the real backend manifest and
output prefixes. Detect and document the required exact prefixes, but do not edit/apply AWS
Terraform or change an allowlist during this task.

## 9. Stable internal error handling

Parse the AWS error fixture and map errors by stable code and `retryable`, not by message text.

At minimum, handle:

```text
AWS_GPU_INVALID_REQUEST
AWS_GPU_FORBIDDEN
AWS_GPU_NOT_FOUND
AWS_GPU_SUBMISSION_CONFLICT
AWS_GPU_SUBMISSION_DISABLED
AWS_GPU_SUBMISSION_UNCERTAIN
AWS_GPU_PROVIDER_STATE_MISSING
AWS_GPU_PROVIDER_IDENTITY_MISMATCH
AWS_GPU_PROVIDER_RETRYABLE
AWS_GPU_INTERNAL_ERROR
```

Guidance:

- `401`/`403`: authentication or deployment identity problem; do not expose token details.
- `404`: reconcile carefully; unknown application/provider identity is not automatic permission to
  submit a replacement.
- `409`: immutable submission conflict; operator-visible and non-retryable.
- retryable `429`, `5xx`, timeouts, disconnects, and uncertain outcomes: bounded retry with jitter
  using the exact same request.
- non-retryable integrity mismatch: fail closed and alert.

Never turn an AWS error message directly into a public FastAPI response. Map it to stable
application errors and retain only sanitized correlation IDs for operators.

## 10. Configuration and feature gates

Add configuration through the backend's existing settings system:

```text
WATERMARK_FEATURE_ENABLED=false
WATERMARK_COMPUTE_PROVIDER=AWS_BATCH
WATERMARK_MAX_ITEMS_PER_JOB=5
WATERMARK_MAX_CONCURRENT_JOBS_PER_TENANT=1
WATERMARK_PIPELINE_VERSION=watermark-v1
AWS_GPU_API_BASE_URL=https://g3qk2f8rsg.execute-api.eu-north-1.amazonaws.com/staging
AWS_GPU_API_AUDIENCE=urn:watermark:aws-gpu-submission:staging
AWS_GPU_REGION=eu-north-1
HTTP_CONNECT_TIMEOUT_SECONDS=<bounded repository-appropriate value>
HTTP_READ_TIMEOUT_SECONDS=<bounded repository-appropriate value>
MAX_SUBMISSION_ATTEMPTS=<small bounded value>
```

Also use existing configuration for approved GCS input/manifest/output buckets and prefixes.

Requirements:

- validate settings at process startup;
- feature defaults to disabled;
- unsupported provider or pipeline fails closed;
- production does not silently inherit staging endpoints;
- secrets are not placed in ordinary environment variables when workload identity supplies them;
- logs redact authorization data; and
- tests always replace the live AWS base URL and token provider with local fakes. There is no
  live-test opt-in within this task.

## 11. Implementation sequence

This is a multi-session change. Implement it incrementally and keep the repository green.

### Phase 0: repository assessment and concrete plan

Create a backend-local implementation plan that maps the logical requirements above to actual
files and existing infrastructure.

The plan must record:

- existing reusable patterns;
- the proposed database migration;
- domain/application/provider boundaries;
- durable dispatch mechanism;
- local orchestrator package/entry-point location;
- reconciler scheduling mechanism;
- public API shapes;
- authorization points;
- state-transition table;
- tests and commands;
- exact future GCS prefixes the owner will need when deploying;
- unresolved future migration, IAM, and deployment prerequisites; and
- explicit confirmation that all work remains local.

Do not invent placeholder module paths before inspecting the backend.

### Phase 1: provider-neutral persistence and transitions

Implement:

- additive migration;
- ORM/domain fields;
- application/provider enums;
- explicit transition guard;
- provider identity invariants;
- idempotency storage;
- due-for-reconciliation query support; and
- migration/model/state tests.

Checkpoint:

- the migration file is reviewed without touching a configured persistent database;
- migration tests run only if the existing harness automatically provides an isolated disposable
  database;
- existing rows remain readable;
- terminal states cannot regress;
- concurrent transitions are safe; and
- all pre-existing tests still pass.

### Phase 2: public job API and immutable manifest

Implement:

- create/get/list/items/cancel routes;
- strict tenant and asset authorization;
- item and concurrency limits;
- public idempotency;
- manifest construction;
- GCS create-only upload and generation capture;
- canonical AWS request hash;
- durable submission/cancellation intent; and
- sanitized public responses.

Checkpoint:

- unauthorized or cross-tenant assets create no partial job;
- same idempotency key/content returns the same job;
- same idempotency key/different content returns `409`;
- generated manifest passes the worker contract;
- golden request hash passes;
- public schemas leak no infrastructure fields; and
- no external AWS call is required for tests.

### Phase 3: typed AWS client and CPU-only orchestrator

Implement:

- strict contract models;
- ID-token provider abstraction;
- bounded HTTP client;
- stable error mapping;
- same-request retry behavior;
- Cloud Run Job entry point;
- provider-identity delivery through the durable backend path; and
- hermetic fake-server tests.

Checkpoint:

- no AWS SDK or static AWS credential is used;
- token/header is never logged;
- timeout/lost-response replay uses identical bytes and application ID;
- `409` is not retried;
- retryable errors use bounded backoff;
- provider identity mismatch is rejected; and
- orchestrator exits after submission/retrieval.

### Phase 4: reconciliation, output validation, and cancellation

Implement:

- due-job polling;
- provider status validation;
- monotonic state mapping;
- bounded retry/backoff scheduling;
- GCS completion-marker/output-manifest validator;
- transactional item/counter updates;
- application cancellation intent/dispatch/reconciliation; and
- stale/integrity failure behavior.

Checkpoint:

- duplicate and out-of-order provider states cannot regress PostgreSQL;
- AWS `SUCCEEDED` without compatible GCS evidence cannot become application `SUCCEEDED`;
- valid GCS evidence updates job/items transactionally;
- cross-job, cross-tenant, generation, checksum, or prefix mismatch fails closed;
- repeated cancellation is idempotent;
- transport failure leaves durable intent retryable; and
- cancellation is not declared complete before provider reconciliation.

End the task after Phase 4. Give the owner a local handoff summary, but do not include or execute a
deployment phase, migration-application phase, cloud rollout, live API probe, or GPU acceptance
phase.

## 12. Required tests

Use the backend's existing frameworks and naming conventions.

### 12.1 Domain and database

Test:

- every allowed application transition;
- every rejected/regressive transition;
- terminal-state immutability;
- provider identity uniqueness;
- positive provider attempt;
- terminal completion timestamp;
- old nullable rows;
- concurrent row/version updates;
- idempotency same content;
- idempotency conflicting content; and
- due-reconciliation queries.

### 12.2 Public API and authorization

Test:

- unauthenticated create/get/list/items/cancel;
- authorized owner access;
- wrong tenant/user access;
- one unauthorized asset among several causes no partial job;
- zero, one, five, and six items;
- duplicate asset IDs;
- tenant concurrency limit;
- create replay and conflict;
- pagination;
- cancellation replay; and
- response schemas contain no internal provider fields.

### 12.3 Manifest and GCS

Test:

- deterministic paths;
- exact source generation/checksum;
- create-only upload;
- collision with identical content;
- collision with incompatible content;
- manifest generation persistence;
- schema-version-1 compatibility;
- wrong bucket/prefix;
- unsafe object path;
- orphan-upload handling; and
- canonical submission hash golden vector.

### 12.4 AWS client and orchestrator

Test every provider state:

```text
RESERVED
SUBMITTING
SUBMISSION_UNCERTAIN
SUBMITTED
PENDING
RUNNABLE
STARTING
RUNNING
SUCCEEDED
FAILED
CANCELLATION_REQUESTED
CANCELLED
```

Also test:

- valid submit/status/cancel parsing;
- unknown additive response field policy;
- malformed JSON;
- wrong application job ID;
- wrong provider;
- wrong provider job ID/ARN/attempt;
- stable AWS error codes;
- retryable and non-retryable behavior;
- timeout before response;
- disconnect after possible acceptance;
- repeated exact submission;
- authorization-header redaction;
- no static credential requirement; and
- orchestrator exits without polling GPU completion.

### 12.5 Reconciliation and output acceptance

Test:

- duplicate status;
- older status after newer status;
- provider failure;
- provider success with missing output;
- delayed compatible output inside consistency window;
- permanently missing output;
- corrupt output JSON;
- wrong schema/job/pipeline/manifest identity;
- changed object generation;
- wrong checksum;
- object outside expected prefix;
- mismatched item set;
- invalid counters;
- blocked item with `finalUri`;
- cancellation/provider-success race;
- cancellation/provider-failure result;
- repeated cancellation; and
- scheduler retry/backoff bounds.

### 12.6 Hermeticity

Normal tests must:

- use fake HTTP/GCS/provider boundaries;
- never require a real Google ID token;
- never call the deployed AWS API;
- never enable the AWS queue;
- never submit AWS Batch;
- never create EC2;
- never require CUDA, PyTorch, or the worker container; and
- remain safe to run repeatedly in CI.

Live tests must require an unmistakable explicit opt-in configuration and remain excluded from
normal test discovery/execution.

## 13. Observability requirements

Use structured, sanitized logs containing appropriate correlation fields:

```text
applicationJobId
tenantId
provider
providerJobId
providerAttempt
providerStatus
applicationStatus
pipelineVersion
requestSha256
correlationId
reconciliationAttempt
```

Do not log:

```text
Authorization header
Google ID token
JWT payload
temporary credentials
signed result URL
customer image bytes
database credentials
raw environment
raw external exception response
```

Add or plan metrics for:

- jobs by application state;
- provider submission latency/failure;
- provider provisioning age;
- reconciliation age;
- cancellation age;
- missing/incompatible output;
- duplicate submission prevented;
- tenant limit rejection;
- GPU job/runtime/cost correlation; and
- AWS compute non-zero without a known application job.

Callbacks/EventBridge are not required for initial correctness. Do not add them merely to reduce
polling latency.

## 14. Safety and authorization boundaries

Always:

- preserve PostgreSQL as application authority;
- use generation/checksum-pinned GCS data;
- use short-lived workload identity;
- validate all cross-cloud requests and responses;
- use durable intent before external work;
- enforce explicit monotonic transitions;
- validate GCS completion evidence before success;
- keep public API provider-neutral;
- keep feature disabled until rollout approval; and
- keep every live/cloud action outside this task.

Never:

- commit AWS access keys;
- commit Google service-account keys;
- print or save Google ID tokens;
- call AWS Batch directly from FastAPI;
- expose the AWS API to frontend clients;
- accept a client-supplied AWS image, queue, job definition, role, region, command, instance, or
  arbitrary GCS URI;
- treat deterministic naming alone as idempotency;
- delete DynamoDB records to repair application state;
- mark application success from AWS status alone;
- trust output JSON without validation;
- use FastAPI `BackgroundTasks` for durable submission/reconciliation/cancellation;
- broaden GCS/IAM/AWS allowlists without approval;
- enable retries that could create an additional billable GPU attempt without approval;
- enable Spot, S3 customer outputs, NAT gateways, or private cross-cloud networking as part of A10;
- launch a GPU during implementation/testing;
- apply any migration;
- commit or push Git changes;
- create or modify IAM, IaC, or cloud configuration;
- deploy any component; or
- run any live acceptance command.

If local implementation reveals that a public API incompatibility, limit change, region change,
IAM expansion, infrastructure change, or deployment change will eventually be necessary, report
it as an unresolved owner decision. Do not perform it in this task.

## 15. Expected deliverables

Produce:

1. A backend repository assessment and concrete A10 plan.
2. An additive migration file with static/model or isolated-test coverage, without applying it to
   a configured database.
3. Provider-neutral application/domain models and transition guards.
4. Public authenticated/authorized job endpoints.
5. Immutable manifest creation with generation and checksum pinning.
6. Public idempotency and durable submission/cancellation intent.
7. Strict typed AWS API models and client.
8. Local CPU-only orchestrator code and entry point.
9. Durable reconciliation.
10. GCS output/marker validation.
11. Idempotent application cancellation.
12. Feature flags and safe defaults.
13. Hermetic unit/API/database/integration tests.
14. Structured logging and local metric instrumentation where consistent with existing code.
15. A short local handoff listing changed files, unexecuted migration file, tests, and unresolved
    external prerequisites.

At each checkpoint report:

- files changed;
- migrations added;
- tests added and their purpose;
- exact verification commands and results;
- architectural decisions made based on existing backend patterns;
- unresolved blockers;
- whether any external resource was changed; and
- explicit confirmation that no GPU was launched.

Do not claim deployed A10 or end-to-end A10 complete. For this task, report only whether the
**local A10 implementation scope** is complete: persistence code and migration file, durable
intent, public API, orchestrator code, reconciliation, output validation, cancellation, safe
configuration, and hermetic tests. Explicitly state that Git push, migration application,
deployment, cloud configuration, and live acceptance were not performed and remain owned by the
repository owner.

---
