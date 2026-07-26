# AWS GPU Watermark Worker: Hybrid Production Setup Plan

Status: Active implementation plan; A0, A1, A3, A4, A5, A6, A9, and the revised A8 scope are
complete. A2 is complete in this repository, with the external FastAPI repository mirror still
pending. A7 one-image acceptance and A8 five-image/completed-replay acceptance passed. A9 provider
status and idempotent terminal cancellation passed without a GPU run. Controlled interruption and
explicit provider attempt 2 are deferred while production retries remain disabled. The Batch
foundation is deployed, valid, disabled, and at zero compute capacity.

Last updated: 2026-07-26

Implementation order: After the provider-neutral worker contract in
[01-gpu-worker-setup.md](01-gpu-worker-setup.md), before the provider-specific portions of
[02-fastapi-integration.md](02-fastapi-integration.md)

Decision record:
[ADR-004](../decisions/ADR-004-hybrid-gcp-control-aws-gpu-execution.md)

## 1. Objective

Run the existing fail-closed watermark worker on an ephemeral AWS GPU while retaining the
application control plane and customer data authority in Google Cloud.

The first production milestone is:

> Given one immutable, generation-pinned GCS manifest containing at most five images, one
> authenticated GCP orchestration request creates exactly one bounded AWS Batch job. AWS Batch
> provisions one On-Demand `g4dn.xlarge` with one NVIDIA T4, runs the immutable worker image, writes
> compatible per-item results and job completion markers to GCS, records runtime evidence, and
> allows the managed compute environment to scale the idle EC2 capacity back to zero without
> manual instance lifecycle management.

This plan changes the worker's compute provider, not its product behavior. The pipeline,
fail-closed safety rules, input schema, output schema, and GCS completion-marker semantics remain
defined by [01-gpu-worker-setup.md](01-gpu-worker-setup.md) and
[ADR-001](../decisions/ADR-001-worker-safety-and-idempotency.md).

## 2. Assumptions

- FastAPI, PostgreSQL, authentication, asset ownership, public APIs, and result authorization remain
  in GCP.
- GCS remains authoritative for input manifests, source images, output artifacts, and completion
  markers.
- Pipeline version `watermark-v1` still accepts at most five images per worker manifest.
- AWS is initially a single-purpose GPU execution provider, not a second application backend.
- GCP submits work through one API Gateway HTTP API and one submission Lambda. There is no separate
  custom Lambda authorizer in the initial production design.
- API Gateway performs standard Google JWT validation; the submission Lambda enforces the exact
  approved GCP service-account subject and all job-specific authorization.
- The initial AWS implementation and GPU acceptance region is `eu-north-1` (Europe/Stockholm).
  Changing the region requires a reviewed update because quotas, ECR images, Batch resources,
  API endpoints, DynamoDB tables, IAM conditions, logs, costs, and latency are regional concerns.
- The initial environment is `staging`. The project owner is the infrastructure, security, and
  billing owner for this single-owner implementation.
- The first accepted instance is On-Demand `g4dn.xlarge`: 4 vCPUs, 16 GiB system memory, and one
  16 GiB NVIDIA T4.
- Heavy model replicas may move to S3 only after the direct-GCS path is accepted and measured.
- Account IDs, regions, VPCs, subnets, quotas, budgets, and retention values are supplied by the
  environment owners and are never guessed.

If any assumption changes, update this specification and ADR-004 before implementation.

### 2.1 Verified current-state snapshot

This is a point-in-time planning snapshot from read-only checks on 2026-07-24. Refresh it during A1;
it is evidence of the starting point, not a substitute for deployment-state discovery.

Current GCP project `project-5909a738-2fdc-451e-ad6`:

| Area | Verified state |
| --- | --- |
| Backend | Cloud Run service `infringementdetection` in `us-central1`. |
| Existing background jobs | Cloud Run jobs `shopify-bulk-scan-job` and `shopify-sync-job`; these are not the AWS GPU worker. |
| Database | Cloud SQL instance `infringement`, PostgreSQL 18, `RUNNABLE`, in `us-central1`. |
| Watermark data | GCS bucket `project-5909a738-2fdc-451e-ad6-watermark-data`. |
| Watermark models | GCS bucket `project-5909a738-2fdc-451e-ad6-watermark-models`. |
| Worker images | Artifact Registry repository `watermark-worker` contains immutable image digests; the reviewed worker has CPU evidence but no completed live GPU acceptance. |
| Identities | Existing `infringement` and `watermark-gpu-worker` service accounts are enabled. Dedicated orchestrator `watermark-aws-orchestrator` exists and its numeric subject is pinned by the AWS API. |
| GCP GPU execution | No Compute Engine instances, persistent disks, or Google Batch jobs were present. |
| GCP GPU quota | Regional NVIDIA T4 quota and project-wide `GPUS_ALL_REGIONS` quota were both `0`. |
| Watermark FastAPI integration | The core backend exists; the watermark-specific tables, endpoints, durable provider orchestration, and reconciliation described by the productionization plans are not yet complete. |

Current AWS status:

- A3-A6 submission, identity, image, and disabled Batch infrastructure is deployed from
  `infra/aws/`, and redacted verification artifacts are stored under
  `aws-acceptance/evidence/`.
- The authenticated AWS account identity was verified; the initial target account ends in `7939`.
- `eu-north-1` (Europe/Stockholm) is formally selected for the initial implementation and GPU
  acceptance.
- The regional `Running On-Demand G and VT instances` quota is `4` vCPUs, sufficient for one
  `g4dn.xlarge`, and that instance type is offered in `eu-north-1a`, `eu-north-1b`, and
  `eu-north-1c`.
- The private ECR repository contains the accepted A5 image. The A6 Batch compute environment,
  disabled queue, job definition, network, roles, and empty worker log group exist, but there are
  no Batch jobs, EC2 instances, EBS volumes, Spot requests, NAT Gateways, Elastic IPs, load
  balancers, ECS container instances, or ECS tasks.
- The account remains on the AWS Free Plan, which blocks `g4dn.xlarge` even though quota is
  approved. Paid Plan activation is an A7 prerequisite for live acceptance.
- Global monthly AWS cost budget `watermark-aws-monthly` is healthy at `$60`, with actual-spend
  email alerts at `$5`, `$15`, and `$25` and a forecasted-spend alert at `$25`. It has no automated
  actions, SNS topic, or paid report. Credits and refunds are excluded so promotional credits do not
  hide underlying resource usage.
- Terraform `1.15.8` is installed and initialized with a versioned GCS backend using prefix
  `watermark/aws/staging`; a post-deployment plan reports no changes.
- The initial Batch network uses dedicated public subnets, ephemeral public IPv4 addresses, an
  internet gateway, and no inbound security-group rules. This avoids a permanently billed NAT
  gateway while retaining the outbound HTTPS path required for GCS and Google STS.
- The dedicated GCP orchestration identity and exact numeric Google subject exist. The A4
  `watermark-aws` WIF pool, exact-role `staging` provider, and empty-permission AWS Batch job role
  are deployed.
- The A3 API Gateway HTTP API, native Google JWT authorizer, submission Lambda, DynamoDB ledger,
  redacted logs, throttling, and alarms are deployed in disabled-submit mode. Lambda is wired to
  the exact A6 queue and job-definition revision, while both submission and the queue remain
  disabled.
- The A6 managed compute environment is `VALID` with `BEST_FIT`, `g4dn.xlarge` only,
  `min=0`, `desired=0`, `max=4`, and `ECS_AL2023_NVIDIA`. Job definition revision `1` uses the
  exact A5 digest, one GPU, 4 vCPUs, 14336 MiB memory, one attempt, and a 5400-second timeout.
- The existing `watermark-gpu-worker` service account has no user-managed key. It may be
  impersonated only by the exact staging GPU job role and has conditional GCS read/create access
  only under `gpu_acceptance/` plus model read access under `watermark-v1/`.
- Staging DynamoDB idempotency retention is 90 days and CloudWatch log retention is 30 days.

## 3. Scope

### Included

- Publish the existing Linux AMD64 CUDA worker image to ECR by digest.
- Create a managed AWS Batch EC2 GPU compute environment, queue, and job definition.
- Create an authenticated and idempotent AWS submission boundary using API Gateway HTTP API, its
  native JWT authorizer, one submission Lambda, and DynamoDB.
- Allow the AWS worker to access approved GCS objects through Workload Identity Federation.
- Preserve manifest generation/checksum validation and GCS conditional writes.
- Map AWS Batch lifecycle, retries, termination, events, and logs into the application workflow.
- Run one-image, five-image, interruption/resume, and cleanup acceptance checks.
- Measure startup, model transfer, runtime, VRAM, RAM, disk, cross-cloud bytes, and cost.
- Define the provider-neutral changes required by the FastAPI plan.

### Not included

- Moving FastAPI, PostgreSQL, user authentication, or public result delivery to AWS.
- Supporting manifests larger than five items.
- AWS Batch array jobs or one task per image.
- Multiple GPUs or distributed inference.
- Spot instances before On-Demand resumability is proven.
- Treating AWS Batch success as sufficient application success.
- Public or frontend use of the AWS submission API. Its hostname may be internet-routable, but only
  the approved GCP orchestration identity is authorized.
- Long-lived AWS or Google access keys.
- Formal visual-accuracy approval; setup acceptance proves system behavior, not product quality.

## 4. Architecture

```text
Client
  |
  | POST /v1/watermark-jobs
  v
FastAPI in GCP
  |- authenticates and authorizes assets
  |- writes PostgreSQL job and item rows
  |- writes immutable GCS manifest and stores its generation
  `- creates durable orchestration intent
          |
          v
Cloud Run orchestrator in GCP
  |- obtains Google-signed service-account ID token
  |- calls fixed AWS internal submission endpoint
  `- stores returned provider job ID/ARN
          |
          v
AWS submission boundary
  |- API Gateway HTTP API validates the Google JWT signature and standard claims
  |- one submission Lambda verifies the exact GCP subject and request authorization
  |- the same Lambda reserves applicationJobId in DynamoDB
  |- the same Lambda submits a fixed AWS Batch queue/job definition
  `- repeated requests return or reconcile the same submission
          |
          v
AWS Batch managed EC2 environment
  |- scales from zero
  |- requests/provisions On-Demand g4dn.xlarge capacity when queued work requires it
  |- uses an ECS GPU-optimized AMI
  |- pulls immutable ECR worker image
  |- assigns one GPU and the dedicated task role
  `- scales idle EC2 capacity back toward zero after work finishes
          |
          v
GPU worker on AWS
  |- exchanges temporary AWS role credentials through GCP WIF
  |- reads exact GCS manifest/input generations
  |- optionally reads immutable model replicas from S3
  |- runs the unchanged fail-closed pipeline
  |- writes artifacts/results/markers to GCS
  `- exits with the existing worker exit-code contract
          |
          v
Event and reconciliation paths
  |- EventBridge emits AWS Batch state changes
  |- optional Lambda callback improves freshness
  |- GCP periodically reconciles provider state
  `- FastAPI accepts success only after GCS output validation
```

### 4.1 API request versus background job

The production flow contains two APIs and one asynchronous background job. They are different
operations:

| Operation | Runs in | Duration | Purpose |
| --- | --- | --- | --- |
| Public FastAPI request | GCP Cloud Run | Short synchronous request | Authorize the user, create the PostgreSQL job, write the immutable GCS manifest, and return an application job ID. |
| Internal submission API request | AWS API Gateway and Lambda | Short synchronous request | Authenticate GCP, reserve idempotency, submit or retrieve the AWS Batch job, and return `202 Accepted`. |
| GPU background job | AWS Batch on EC2 | Asynchronous, potentially long-running | Pull the container, read GCS inputs, execute GPU inference, and write GCS results and markers. |

The internal submission request never holds an HTTP connection open for inference. It only
enqueues work and returns the provider job identity. FastAPI clients poll or receive application
status from GCP; they never call AWS.

AWS Batch does not run a Google Cloud Run Job. Cloud Run Jobs and AWS Batch are alternative
container execution systems. The same reviewed worker container and manifest contract are deployed
as an AWS Batch job instead of as a Google-managed job.

The Batch job itself also does not call `RunInstances` or terminate an EC2 instance. AWS Batch
schedules the container, and the managed compute environment controls EC2 capacity. With
`minvCpus=0`, unused GPU capacity can return to zero after it becomes idle. Scale-down is managed
asynchronously and is not guaranteed to happen at the exact instant the worker exits.

### 4.2 Runtime sequence

1. A client calls FastAPI in GCP.
2. FastAPI authenticates the user, authorizes the assets, creates PostgreSQL job/item rows, writes
   the immutable GCS manifest, stores its generation, and commits durable orchestration intent.
3. The GCP Cloud Run orchestrator obtains a Google-signed ID token for the configured AWS API
   audience.
4. The orchestrator sends an idempotent `PUT` to the AWS internal submission endpoint.
5. API Gateway validates the token signature and standard claims before invoking Lambda.
6. The submission Lambda checks the exact GCP service-account subject, validates the request,
   conditionally reserves the application job ID in DynamoDB, and submits the fixed Batch job.
7. Lambda returns `202 Accepted` and the existing or newly created AWS Batch job identity.
8. AWS Batch queues the job and asks its managed compute environment for GPU capacity.
9. When capacity is ready, the worker container starts on a `g4dn.xlarge`, obtains short-lived GCP
   credentials through Workload Identity Federation, and reads the exact GCS manifest and objects.
10. The worker runs the unchanged pipeline, writes deterministic artifacts and completion markers
    to GCS, and exits.
11. AWS Batch records terminal infrastructure state. EventBridge may send a freshness callback.
12. GCP reconciliation validates provider state and the compatible GCS completion marker before
    updating the authoritative PostgreSQL job.
13. After the instance is idle, the managed compute environment can scale EC2 capacity back to
    zero.

### 4.3 Persistent and temporary data placement

Authoritative application/customer data stays in GCP. AWS necessarily retains limited operational
data and temporarily processes copies:

| Data or responsibility | Authority/location | Notes |
| --- | --- | --- |
| Users, tenants, authorization, application jobs | GCP FastAPI and PostgreSQL | AWS never reads PostgreSQL. |
| Source images and immutable manifests | GCS | Worker reads exact object generations. |
| Output artifacts, result records, and completion markers | GCS | GCP validates them before application success. |
| Model artifacts, Phase A | GCS | Optional immutable S3 replicas are a measured later optimization. |
| GPU worker image | Private ECR | Referenced by immutable digest in the Batch job definition. |
| Submission/idempotency metadata | DynamoDB | Operational AWS ledger, not an application database. |
| AWS job state | AWS Batch | Provider state only; PostgreSQL remains authoritative. |
| GPU runtime files | Ephemeral EC2/EBS storage | Downloaded during execution and removed with worker capacity/volumes according to the compute configuration. |
| AWS execution and API logs | CloudWatch | Redacted, encrypted, and retained for an explicit period. |

### 4.4 Why use a submission API at all

The submission API is a narrow control-plane adapter, not a model-serving API. It prevents the GCP
orchestrator from needing general AWS credentials or understanding mutable AWS infrastructure
details. The boundary:

- exposes only submit-or-retrieve, status, and cancellation for an application job;
- fixes the AWS account, region, queue, job definition, image digest, role, GPU resources, timeout,
  and retry policy in trusted deployment configuration;
- applies authentication, request authorization, throttling, logging, and stable error mapping;
- keeps the DynamoDB idempotency ledger next to the AWS mutation it protects; and
- allows the GCP application contract to remain provider-neutral.

GCP could obtain temporary AWS credentials and call DynamoDB/Batch directly, but that would move
AWS request signing, IAM permissions, infrastructure allowlisting, idempotency, and uncertain-submit
recovery into the GCP orchestrator. That remains a viable future simplification only if the team
accepts the larger AWS-specific permission and code surface in GCP.

For the stated long-term scope—one GCP caller and one GPU pipeline—the production boundary is kept
small: API Gateway native JWT validation plus one submission Lambda. A separate custom Lambda
authorizer is deliberately omitted.

## 5. Authority and responsibilities

### 5.1 FastAPI and PostgreSQL in GCP

Own:

- Users, tenants, authentication, authorization, and product limits.
- Application job and item state.
- Manifest construction and returned GCS manifest generation.
- Durable orchestration intent.
- Public status, cancellation, and result endpoints.
- Validation of worker output before application success.

Do not own:

- AWS instance selection at request time.
- GPU model loading or inference.
- AWS compute-instance lifecycle.

### 5.2 Cloud Run orchestrator in GCP

Own:

- A small, typed request to the AWS submission boundary.
- Google service identity and audience-bound ID token used for cross-cloud authentication.
- Persisting the AWS provider job ID and ARN returned by the boundary.
- Polling provider status when reconciliation requests it.
- Retrying an uncertain HTTP result with the same immutable request and application job ID.

Do not accept or forward arbitrary AWS job definitions, images, roles, queues, commands, or instance
types.

### 5.3 AWS submission boundary

Own:

- API Gateway HTTP API standard JWT validation.
- Submission Lambda exact-caller authorization.
- Request schema validation.
- Cross-cloud submission idempotency.
- The approved AWS Batch queue, job-definition revision, retry policy, timeout, and tags.
- Submit, describe, and terminate operations for the assigned application job.

Do not own:

- Public user authentication.
- PostgreSQL access.
- Asset authorization.
- Watermark inference.

There is intentionally no separate Lambda authorizer for the single-caller, single-purpose
production design. API Gateway performs standard JWT verification and passes verified claims to the
submission Lambda. The Lambda checks the exact immutable service-account `sub` claim while
performing job-specific authorization and submission. If AWS or Google token compatibility testing
shows that API Gateway cannot validate the exact production token shape, token verification may be
moved into the same submission Lambda using the official Google authentication library; do not add
hand-written JWT cryptography.

### 5.4 AWS Batch and EC2

Own:

- Queueing and capacity provisioning.
- Starting the ECS container with one assigned GPU.
- Applying retry and timeout rules.
- Publishing infrastructure state and logs.
- Managing EC2 capacity and eventually scaling idle worker compute toward zero.

### 5.5 GPU worker

Its responsibilities remain those in the GCP worker plan:

- Validate manifest/configuration/model identity.
- Process items using stage lifecycle management.
- Fail closed at the safety gate.
- Write deterministic artifacts and markers.
- Resume compatible completed items.
- Emit progress and acceptance metrics.

It never accesses PostgreSQL and never creates another AWS Batch job.

### 5.6 Required FastAPI integration changes

[02-fastapi-integration.md](02-fastapi-integration.md) remains useful for the public API,
authorization, PostgreSQL, durable intent, reconciliation, cancellation, and output-validation
design. This document supersedes its Google-provider-specific execution details for the AWS path:

| FastAPI plan concept | AWS production interpretation |
| --- | --- |
| Cloud Run orchestration | Keep a short-lived GCP orchestrator, but it calls the internal AWS submission API instead of Google Batch. |
| Google Batch resource name | Store provider-neutral fields such as `compute_provider`, `provider_job_id`, optional `provider_job_arn`, provider state, and attempt. |
| Deterministic Google Batch creation | Use idempotent `PUT` plus the DynamoDB reservation/reconciliation state machine. |
| Google Batch status | Normalize AWS Batch state through the internal status/event contract. |
| Google Batch cancellation | Call the internal AWS cancellation route; FastAPI remains the cancellation authority. |
| Successful Batch task | Still insufficient: validate the compatible GCS job completion marker before application success. |

The immutable GCS manifest generation must be returned by storage creation, stored in PostgreSQL,
sent through the orchestrator and submission API, and supplied to the worker. It must not be dropped
between FastAPI and AWS.

The FastAPI document's example allowing `5000` items is incompatible with the initial
`watermark-v1` worker contract. For this milestone FastAPI must enforce at most five images per
application job/manifest and create one AWS Batch job. Supporting larger user requests requires an
explicit future sharding design with parent/child jobs, multiple manifests, concurrency and cost
limits, cancellation semantics, and aggregate result rules; do not silently send a larger manifest
or create unbounded AWS jobs.

## 6. Preserved worker contracts

The following are unchanged:

- `schemaVersion: 1` manifests.
- Required `manifestGeneration` for GCS execution.
- Required source object generation and SHA-256 checksum.
- `watermark-v1` configuration and maximum of five items.
- Deterministic GCS output prefixes.
- Per-item `result.json` and `_COMPLETE.json`.
- Job `output-manifest.json` and `_COMPLETE.json`.
- Original source image as the only permitted LaMa image input.
- No `finalUri` for failed, blocked, unverified, or no-watermark outcomes.
- Worker exit `0` meaning that all requested items have compatible terminal records, not that every
  item was inpainted.

Provider metadata is additive. Output contracts may add optional fields such as:

```json
{
  "computeProvider": "AWS_BATCH",
  "providerRegion": "OWNER_SUPPLIED_REGION",
  "providerJobId": "AWS_BATCH_JOB_ID",
  "providerAttempt": 1,
  "instanceType": "g4dn.xlarge"
}
```

Consumers must ignore unknown additive fields. Existing required fields cannot be removed or change
meaning within schema version 1.

## 7. Internal cross-cloud API contract

The AWS endpoint is private in intent even though API Gateway exposes an internet-routable HTTPS
hostname. It is not a public product API: browsers, mobile applications, users, and the FastAPI
request handler do not call it. Only the approved GCP Cloud Run orchestration identity may invoke
it.

Use an API Gateway HTTP API integrated with one submission Lambda. Configure the route with a native
JWT authorizer for the Google token issuer and an exact, dedicated audience. API Gateway validates
the token signature and standard time/issuer/audience claims. The integration Lambda receives the
validated claims, requires the exact configured immutable service-account subject, and then applies
request-level authorization.

All submit, status, and cancellation routes may use the same Lambda because AWS is a
single-purpose execution plane. Split functions only when measured scaling, isolation, or ownership
requirements justify it.

### 7.1 Submit or retrieve one provider job

```http
PUT /internal/v1/gpu-jobs/{applicationJobId}
Authorization: Bearer <google-service-account-id-token>
Content-Type: application/json
```

```json
{
  "schemaVersion": 1,
  "applicationJobId": "wm_123",
  "manifestUri": "gs://approved-job-bucket/tenant_17/wm_123/input-manifest.json",
  "manifestGeneration": "1721210400123456",
  "outputManifestUri": "gs://approved-output-bucket/tenant_17/wm_123/output-manifest.json",
  "pipelineVersion": "watermark-v1",
  "requestSha256": "lowercase-sha256-of-canonical-request"
}
```

The path and body job IDs must match. The service validates identifier syntax, approved GCS
buckets/prefixes, manifest generation syntax, and the pipeline allowlist. It independently
canonicalizes the immutable request fields and recomputes `requestSha256`; it never trusts a
caller-supplied hash as proof of request identity.

The caller cannot provide:

- ECR image or digest.
- AWS Batch queue or job definition.
- AWS account, region, VPC, role, command, vCPU, memory, GPU, retry, or timeout settings.

Initial response:

```http
HTTP/1.1 202 Accepted
```

```json
{
  "schemaVersion": 1,
  "applicationJobId": "wm_123",
  "provider": "AWS_BATCH",
  "providerJobId": "00000000-0000-0000-0000-000000000000",
  "providerJobArn": "arn:aws:batch:REGION:ACCOUNT:job/JOB_ID",
  "status": "SUBMITTED"
}
```

A replay with the same `applicationJobId` and canonical immutable request content returns the
existing reservation and job. A replay with different immutable request content returns `409`.

### 7.2 Get provider status

```http
GET /internal/v1/gpu-jobs/{applicationJobId}
Authorization: Bearer <google-service-account-id-token>
```

The response returns only the normalized status, provider job identity, attempt count, timestamps,
and sanitized reason code needed for reconciliation. Raw IAM details, environment variables, and
container secrets are never returned.

### 7.3 Request cancellation

```http
PUT /internal/v1/gpu-jobs/{applicationJobId}/cancellation
Authorization: Bearer <google-service-account-id-token>
Content-Type: application/json
```

```json
{
  "schemaVersion": 1,
  "reasonCode": "USER_REQUESTED"
}
```

Cancellation is idempotent. The AWS service terminates the recorded Batch job, then reports
provider state. GCP retains the authoritative `CANCEL_REQUESTED` to `CANCELLED` transition.

### 7.4 Stable error envelope

```json
{
  "error": {
    "code": "AWS_GPU_SUBMISSION_CONFLICT",
    "message": "The application job already has different immutable submission parameters.",
    "retryable": false,
    "correlationId": "opaque-id"
  }
}
```

Third-party AWS responses are untrusted. The boundary maps them to enumerated internal errors and
does not return raw exception strings.

## 8. Submission idempotency

AWS Batch `SubmitJob` returns a unique job ID but the ordinary container-job API has no client token
that makes repeated requests idempotent. The AWS boundary therefore uses a DynamoDB table keyed by
`applicationJobId`.

DynamoDB is not a second customer database and is not used by the GPU pipeline. It stores no source
images, results, user records, or authorization state. It is a small AWS-side submission ledger
whose purpose is to answer:

> Has this exact GCP application job already been submitted to AWS, and if so, which AWS Batch job
> represents it?

It specifically protects the timeout case where AWS accepts `SubmitJob` but GCP does not receive
the HTTP response. Retrying the same `PUT` must return/reconcile the existing provider job instead
of starting and billing a duplicate GPU job.

Minimum record:

```text
applicationJobId
requestSha256
state: RESERVED | SUBMITTING | SUBMISSION_UNCERTAIN | SUBMITTED | FAILED_BEFORE_SUBMISSION
providerJobName
providerJobId
providerJobArn
createdAt
updatedAt
expiresAt
```

Rules:

1. Reserve with a conditional put using `attribute_not_exists(applicationJobId)`.
2. Store a SHA-256 hash that the Lambda computes from canonical immutable request fields. Do not
   rely on the caller-provided `requestSha256`.
3. A matching existing request returns or repairs the existing record.
4. A `SUBMITTED` replay returns the stored provider job without calling `SubmitJob`.
5. A `RESERVED`, `SUBMITTING`, or `SUBMISSION_UNCERTAIN` replay enters bounded reconciliation. It
   does not blindly submit again.
6. A different request hash for the same ID is a permanent `409` conflict.
7. After AWS accepts `SubmitJob`, persist the returned job ID/ARN before reporting success.
8. A timeout or crash between AWS acceptance and persistence records or infers
   `SUBMISSION_UNCERTAIN`; reconciliation inspects the approved queue using the deterministic,
   non-authoritative job name and non-sensitive correlation tags.
9. Tag the AWS job with a non-sensitive application job ID and request hash to support audit and
   reconciliation.
10. A deterministic Batch job name helps discovery but is not an idempotency guarantee.
11. DynamoDB expiration must exceed the maximum application retry, cancellation, reconciliation,
    and provider-observability window. Expiry never authorizes reusing an application job ID.
12. PostgreSQL remains the durable application source of truth; DynamoDB state is provider
    metadata and may be reconstructed/reconciled while the retention window is active.

## 9. Cross-cloud identity

### 9.1 GCP to AWS

The Cloud Run orchestrator obtains a Google-signed service-account ID token whose audience is the
configured AWS submission service audience.

API Gateway's native JWT authorizer verifies:

- Signature against Google's published keys.
- The explicitly configured Google issuer.
- Audience exactly the dedicated submission-service audience.
- Token expiry, not-before, and issued-at constraints supported by the service.

The submission Lambda then verifies:

- The authorizer context exists and contains the expected verified claims.
- Subject exactly equals the immutable numeric ID of the approved GCP service account.
- Authorized party and email when present are consistent, without using mutable display names or
  email alone as authority.
- The path, body, bucket, prefix, generation, pipeline, and canonical request hash are authorized.

Authentication success does not skip request authorization or schema validation.

Do not deploy a separate custom Lambda authorizer for this single-caller design. Do not manually
parse an unverified JWT payload in the submission Lambda. Cloud integration acceptance must prove
the exact Google service-account ID token works with the configured API Gateway issuer/audience
before production promotion.

### 9.2 AWS to GCP

The Batch container receives temporary AWS credentials from its dedicated task role. A Google
Workload Identity Pool AWS provider exchanges that identity for short-lived Google credentials,
using either direct resource access or a dedicated impersonated service account.

The provider condition must restrict the exact AWS account and dedicated worker role. Do not grant
an entire AWS account or workload identity pool access to customer data.

The resulting Google identity may:

- Read approved manifest, input, and initially model-object prefixes.
- Create objects only under approved output prefixes.
- Read objects it created when required for collision/marker validation.
- Invoke the approved internal progress endpoint if callbacks are enabled.

It may not:

- Access PostgreSQL or application secrets.
- Delete or overwrite arbitrary GCS objects.
- Read unrelated tenants.
- Administer IAM.

No service-account key file is created or mounted.

The standard Google AWS credential configuration targets EC2 instance-profile metadata. AWS Batch
instead exposes the job role through the ECS task credential provider. Do not fall back to the
broader Batch EC2 instance role merely to make WIF work.

Implement the official Google-auth custom AWS credential-supplier pattern:

1. Use Boto3's default credential provider chain inside the Batch container. In ECS this resolves
   the temporary task-role credentials exposed to that job.
2. Implement `google.auth.aws.AwsSecurityCredentialsSupplier`, cache only the resolved region, and
   return fresh Boto3 session credentials whenever Google auth requests them.
3. Construct `google.auth.aws.Credentials` with the exact workload identity provider audience,
   service-account impersonation URL, AWS SigV4 subject-token type, and required Google scopes.
4. Inject those credentials into the existing GCS client construction. Never print, persist, or
   place the temporary AWS credentials in the job definition or environment configuration.

This path requires a reviewed Boto3 runtime dependency in the AWS worker image and a storage-client
factory that can accept explicit Google credentials. Local and GCP execution continue to use
Application Default Credentials. A generated external-account credential file remains useful for
an EC2-only diagnostic, but it is not the production Batch task-role mechanism.

## 10. Storage and model strategy

### Phase A: GCS remains authoritative for everything

Use the current worker with the smallest necessary authentication adaptation:

- Read the exact manifest generation from GCS.
- Read exact input and model generations.
- Write output artifacts with `ifGenerationMatch=0`.
- Preserve current checksum and marker validation.

This is the acceptance baseline because it changes compute placement without changing storage
semantics.

### Phase B: Optional immutable S3 model replicas

After Phase A measurements, mirror enabled model artifacts to a private S3 bucket when model
transfer dominates startup time or cost.

Each S3 artifact binding must include:

- Artifact ID.
- Bucket and key.
- Immutable S3 version ID.
- SHA-256 checksum matching the approved model manifest.
- Expected safe filename and archive limits.

GCS remains the model identity authority until a new provider-neutral model-manifest schema is
approved. No mutable S3 key or `latest` alias may select executable model content.

Do not move customer results to S3 in this milestone. That would create a second result source of
truth and require a separate replication contract.

## 11. Container and ECR

- Reuse the reviewed Linux AMD64 Python 3.11 CUDA 12.1/PyTorch worker build unless AWS acceptance
  proves an incompatibility.
- Publish to a private ECR repository.
- Use the ECR image digest in the AWS Batch job-definition revision.
- Record the ECR digest independently; do not assume another registry assigns the same manifest
  digest.
- Keep the non-root UID/GID `10001` runtime and deterministic entrypoint.
- Include no AWS keys, Google keys, model credentials, or database secrets.
- Continue lazy GPU imports and stage-by-stage model release.
- Write only to configured temporary/model-cache paths and stdout/stderr.

The first AWS image must pass the same CPU-only import/ABI and read-only replay checks as the
existing GPU image before any GPU submission.

## 12. AWS Batch runtime

Initial bounded configuration:

```text
Compute environment:   Managed EC2, GPU-only
Provisioning model:    On-Demand
Instance type:         g4dn.xlarge only
Minimum vCPUs:         0
Desired vCPUs:         0
Maximum vCPUs:         4 initially
Job queue:             Dedicated watermark GPU queue
Job definition:        Immutable active revision
Job resources:         4 vCPU, 14336 MiB memory, 1 GPU
Attempts:              1 for acceptance; at most 2 after resume proof
Timeout:               90 minutes for one-image acceptance; measured production value later
Storage:               200 GiB gp3 root/data capacity initially
Container image:       Private ECR digest
Task role:             Dedicated watermark GPU worker role
Logs:                  CloudWatch Logs with explicit retention
Tags:                  Component, environment, pipeline, application job ID, request hash
```

AWS Batch managed compute environments use an ECS GPU-optimized AMI for supported G-family
instances. Record the effective AMI ID and driver/runtime versions during acceptance. AWS Batch does
not automatically update an existing compute environment to every newer AMI, so AMI promotion needs
an explicit review and regression smoke test.

The task timeout starts once the job is running, not while the image is still pulling in
`STARTING`. Record image-pull/provisioning time separately.

The worker container exits when the pipeline reaches its terminal contract. AWS Batch records the
job result and releases the container resources. The Batch job does not itself shut down EC2;
capacity scale-down belongs to the managed compute environment. With the initial maximum of four
vCPUs and `g4dn.xlarge`-only configuration, at most one such GPU instance is available to this
environment at a time. Confirm in acceptance that idle capacity and attached worker volumes are
eventually released and alert if they are not.

## 13. Networking

Initial staging and single-purpose production posture:

- One dedicated VPC with two public subnets in supported Stockholm Availability Zones.
- Each subnet routes `0.0.0.0/0` through an internet gateway and maps a public IPv4 address only to
  the temporary Batch EC2 instance.
- No NAT gateway is created. This avoids the approximately `$33.58/month` idle NAT baseline
  measured in A1.
- The Batch security group has no inbound rules. No SSH key pair, bastion, load balancer, or public
  application listener exists.
- Outbound HTTPS supports GCS, Google STS/IAM, ECR, S3-backed ECR layers, CloudWatch, and AWS APIs.
  DNS to the VPC resolver is allowed. Broader egress requires a documented reason.
- IMDSv2 is required in the launch template. The worker uses its ECS task-role credential endpoint
  through Boto3 and must not use the EC2 compute-instance role as its application identity.
- EBS is encrypted, `gp3`, delete-on-termination, and initially limited to 200 GiB total root/data
  capacity.
- Public IPv4 and internet-transfer charges exist only while the Batch instance runs. Record them
  per acceptance job.

This is a deliberate cost/security trade-off for one ephemeral GPU worker with no inbound surface.
If a later policy requires private subnets, replace this design with NAT or reviewed VPC endpoints
and accept the resulting fixed and per-GB cost before applying the change.

Cross-cloud private connectivity is out of scope until measured traffic or policy justifies Direct
Connect/Interconnect complexity.

## 14. Retry and exit behavior

Preserve worker exit meanings from the original plan.

Initial AWS mapping:

| Worker exit | AWS action | Reason |
| ---: | --- | --- |
| `0` | `EXIT` success | Compatible job marker exists; item failures may still be recorded. |
| `2`, `3`, `4`, `5`, `10` | `EXIT` failure | Deterministic/configuration, GPU, permission, or unexpected failure requires review. |
| `6`, `7` | `RETRY` once after acceptance | Explicitly classified transient dependency failures. |

The `evaluateOnExit` list must end with a catch-all `EXIT`; otherwise unmatched failures are retried
when attempts remain.

Acceptance uses one attempt to bound cost. Enable a second attempt only after interruption/resume
proves that completed items are reused correctly.

AWS timeout sends `SIGTERM` and then may force termination after a short grace period. The worker
must checkpoint at safe item boundaries and cannot assume enough time to finish a long inference
stage.

## 15. Provider state mapping

| AWS Batch state | Application interpretation |
| --- | --- |
| `SUBMITTED`, `PENDING`, `RUNNABLE` | `PROVISIONING` |
| `STARTING`, `RUNNING` | `RUNNING` |
| `SUCCEEDED` | Validate compatible GCS output before application success |
| `FAILED` | Reconcile worker output, attempts, and stable error before application failure |

An AWS job can remain `RUNNABLE` while capacity is unavailable. Alert on a configured provisioning
age rather than treating every queue wait as a worker failure.

AWS retains terminal job visibility for a limited period. PostgreSQL, GCS evidence, and configured
CloudWatch retention are the durable application/operator record.

## 16. Progress, events, and reconciliation

### Freshness path

AWS Batch state changes flow through EventBridge. An optional Lambda callback sends normalized,
idempotent events to the protected GCP endpoint.

Every event includes:

```text
eventId
applicationJobId
providerJobId
providerAttempt
providerStatus
occurredAt
```

EventBridge delivery is at least once, so GCP deduplicates `eventId` and validates monotonic state
transitions.

Worker item progress may use the same AWS-to-GCP federated identity or be omitted initially.

### Correctness path

A scheduled GCP reconciler checks:

- DynamoDB/submission-service state.
- AWS Batch job state and attempts.
- Expected GCS item markers.
- Expected GCS job output manifest and completion marker.
- Stale progress and cancellation state.

Callbacks are never required for correctness.

## 17. Cancellation

1. FastAPI authorizes the application job and records `CANCEL_REQUESTED`.
2. The GCP orchestrator calls the idempotent AWS cancellation endpoint.
3. The AWS boundary terminates the recorded job. AWS cancels work that has not started and
   terminates work in `STARTING` or `RUNNING`.
4. The worker handles `SIGTERM` when time permits.
5. Reconciliation confirms provider termination and records `CANCELLED`.
6. Compatible per-item markers remain immutable evidence; product policy decides whether a new
   application job may reuse them.

## 18. Security boundaries

### GCP orchestrator identity

May:

- Mint an ID token for and invoke the specific AWS submission API audience.

May not:

- Submit arbitrary AWS Batch definitions.
- Assume general AWS administrator roles.
- Read worker customer output directly unless required by its existing GCP role.

### API Gateway HTTP API

Must:

- Require JWT authorization on every submit, status, and cancellation route.
- Validate the configured Google issuer, dedicated audience, signature, and supported time claims.
- Pass validated claims to the Lambda integration.
- Apply explicit request throttling and access logging.
- Reject unmatched routes and unsupported methods.

It does not replace Lambda's exact-subject and request-level authorization.

### AWS submission Lambda role

May:

- Read/write the one DynamoDB idempotency table.
- Submit/describe/terminate jobs only in the approved queue and definition boundary.
- Pass only the dedicated Batch job role.
- Write logs and metrics.

May not:

- Modify compute environments, job definitions, VPCs, or IAM.
- Access PostgreSQL.
- Accept caller-selected queues, job definitions, images, roles, commands, instance types, GPU
  counts, retry counts, or timeouts.

The Lambda code must require the exact configured GCP service-account subject from API Gateway's
verified authorizer context before touching DynamoDB or AWS Batch. Missing, malformed, or unexpected
claims fail closed.

### AWS Batch service and compute roles

Use the AWS Batch service-linked role for Batch control-plane operations. Use a dedicated ECS
compute-instance role/profile for the managed EC2 environment and restrict it to the infrastructure
permissions required for ECS registration, ECR pulls, and log delivery. Do not combine that
instance role with the application-level Batch job role.

### AWS Batch job role

May:

- Obtain its own temporary AWS credentials.
- Read optional approved S3 model versions.
- Exchange identity through the specific GCP workload identity provider.
- Write logs.

May not:

- Submit AWS Batch jobs.
- Read the DynamoDB idempotency table.
- Assume unrelated roles.

### General requirements

- Encrypt ECR, DynamoDB, CloudWatch, S3, and EBS with approved AWS-managed or customer-managed keys.
- Restrict all cross-cloud subjects and audiences exactly.
- Redact ID tokens, AWS credentials, GCS authorization headers, signed URLs, and customer metadata
  from logs.
- Treat AWS API and EventBridge payloads as untrusted external input.
- Enable CloudTrail coverage needed to audit submission, termination, IAM, and infrastructure
  changes.
- Apply explicit log and DynamoDB retention; do not rely on indefinite defaults.

## 19. Observability and cost

Correlation fields across Cloud Logging, AWS logs, PostgreSQL, and GCS:

```text
applicationJobId
tenantId or non-sensitive tenantHash
pipelineVersion
workerImageDigest
manifestGeneration
provider=AWS_BATCH
providerRegion
providerJobId
providerAttempt
eventId
```

Required measurements:

- GCP-to-AWS submission latency.
- API authentication failures, throttles, Lambda errors, and duplicate/conflicting submissions.
- AWS queue and provisioning latency.
- ECR image-pull/startup time.
- GCS and optional S3 model-download time and bytes.
- Per-stage initialization and execution time.
- Peak VRAM, process RAM, and disk.
- Input/output bytes in each cross-cloud direction.
- EC2, EBS, ECR, NAT, CloudWatch, Lambda/API Gateway, DynamoDB, and transfer cost per job.
- Cleanup confirmation for EC2 and attached volumes.

Set AWS Budgets/alerts and GCP billing alerts before broad rollout. Scale-to-zero Batch compute does
not make persistent NAT, storage, logging, or registry costs zero.

## 20. Implementation conventions, commands, and planned artifacts

Implementation conventions:

- Python remains `>=3.11,<3.13` for worker-side changes. Use the AWS Lambda `python3.12` managed
  runtime so local development and Lambda share Python 3.12.
- Use Terraform for AWS resources and the small set of GCP identity/state resources. Store state in
  a pre-created, versioned, access-restricted GCS bucket; the GCS backend supports state locking.
- Declare compatible Terraform/provider version constraints and commit `.terraform.lock.hcl`.
  Terraform is not installed on the current workstation and must be installed before A3.
- JSON contracts use camelCase fields and `UPPER_SNAKE_CASE` enum values.
- Boundary inputs and AWS/GCP responses are parsed into typed models before use.
- Errors use stable enumerated codes and never expose raw provider exceptions.
- Provider-specific modules depend on the preserved worker contracts, not the reverse.
- Use AWS and Google SDKs for signed API calls; do not construct shell commands from request data.
- Use API Gateway's JWT authorizer instead of custom JWT signature code. The Lambda reads only
  claims supplied through the verified authorizer context and independently enforces the exact
  caller subject.
- Unit and hermetic integration tests remain under `pytest`; live AWS/GCP tests are opt-in markers
  and require isolated prefixes/accounts.

Commands below are templates. Replace only explicit `OWNER_SUPPLIED_*` values after read-only
preflight and review.

Read-only preflight:

```bash
aws sts get-caller-identity
aws service-quotas list-service-quotas --service-code ec2
aws ec2 describe-instance-type-offerings \
  --location-type availability-zone \
  --filters Name=instance-type,Values=g4dn.xlarge
aws batch describe-compute-environments
aws batch describe-job-queues
```

Build and publish:

```bash
export AWS_REGION=eu-north-1
export CODEBUILD_PROJECT_NAME=watermark-staging-worker-image-build
export CODEBUILD_SOURCE_BUCKET=watermark-staging-codebuild-source-154959837939
infra/aws/scripts/start-worker-image-build.sh BUILD_ID
```

This is an explicitly started remote build, not a local Docker build and not part of the Batch GPU
runtime. The helper uploads a small reviewed source ZIP to private S3. The on-demand CodeBuild
project builds the Linux AMD64 image, reuses a BuildKit registry cache in
`watermark-staging-worker-build-cache`, runs a CPU-only import check, and pushes the new immutable
release tag to `watermark-staging-worker`. No GPU or EC2 Batch capacity is created by the build.
Use a new release tag for every attempt and record the resulting digest. Exact monitoring and
failure-handling commands are in the
[A7 manual operator runbook](04-a7-manual-operator-runbook.md).

Manual acceptance submission:

```bash
aws batch submit-job \
  --cli-input-json file://aws-acceptance/job-one-image.json
```

Target repository layout:

```text
infra/
  aws/
    README.md
    versions.tf
    backend.tf
    providers.tf
    variables.tf
    locals.tf
    networking.tf
    iam.tf
    ecr.tf
    dynamodb.tf
    submission-api.tf
    batch.tf
    events.tf
    observability.tf
    outputs.tf
    environments/
      staging.tfvars.example
services/
  aws_submission/
    pyproject.toml
    uv.lock
    src/aws_submission/
      handler.py
      models.py
      auth.py
      canonical.py
      ledger.py
      batch_adapter.py
      errors.py
    tests/
contracts/
  aws-gpu-v1/
    submit-request.json
    submit-response.json
    status-response.json
    cancellation-request.json
    error-response.json
aws-acceptance/
  README.md
  job-one-image.json
  job-five-image.json
  evidence/
tests/
  unit/
    test_aws_submission_contract.py
    test_aws_batch_contract.py
    test_aws_event_mapping.py
    test_aws_wif_credentials.py
  integration/
    test_aws_submission_idempotency.py
    test_aws_gcs_federation.py
```

The FastAPI backend and Cloud Run orchestrator are not present in this workspace. Paths prefixed
with `BACKEND_REPO/` in the tasks below refer to that existing application repository. Do not add
PostgreSQL models or public FastAPI endpoints to this GPU worker repository.

Use this deployment rhythm for every Terraform change:

```bash
terraform -chdir=infra/aws fmt -check
terraform -chdir=infra/aws validate
terraform -chdir=infra/aws plan \
  -var-file=environments/staging.tfvars \
  -out=staging.tfplan
terraform -chdir=infra/aws show staging.tfplan
# Apply only after reviewing resource count, IAM, cost-bearing resources, and destroy/replace actions.
terraform -chdir=infra/aws apply staging.tfplan
```

Never use `-auto-approve` for staging or production. Never commit `*.tfvars`, Terraform state,
tokens, or temporary cloud credentials. The example tfvars file contains names and placeholders
only.

## 21. Testing strategy

### Unit and contract tests

- Submission request validation and canonical request hash.
- API Gateway authorizer configuration pins the expected Google issuer and dedicated audience.
- Submission Lambda rejects missing authorizer context and the wrong exact caller subject.
- Immutable infrastructure fields cannot be overridden.
- DynamoDB reservation, replay, conflict, and uncertain-submission reconciliation.
- AWS-to-application state mapping.
- Exit-code retry mapping with catch-all exit.
- Event replay and unknown-field tolerance.
- Provider metadata remains additive to worker schema version 1.

### Hermetic integration tests

- Submission Lambda with representative API Gateway verified-claims events and local/fake AWS
  clients.
- Repeated request creates one logical provider record.
- Different request hash returns conflict.
- Unauthenticated, wrong-audience, and wrong-subject requests cannot reach submission behavior.
- AWS error payloads map to stable internal errors.
- GCS storage behavior continues passing existing generation/collision tests.

### Opt-in cloud integration tests

- The exact GCP service-account ID token is accepted for the configured issuer/audience.
- An expired token, wrong audience, and different Google subject are rejected.
- There is no separate custom Lambda authorizer deployment.
- AWS Batch task role exchanges credentials through GCP Workload Identity Federation.
- Federated identity can access only approved GCS test prefixes.
- One CPU-only validation/replay job runs without GPU inference.

### GPU acceptance tests

1. One-image On-Demand `g4dn.xlarge` run.
2. Five-image run in one process.
3. Model-stage initialization/release evidence.
4. Safety-blocked item never initializes/runs LaMa for that item and never emits `finalUri`.
5. Interruption after at least one item marker, followed by a bounded retry/resubmission proof.
6. Completed replay makes zero model-stage calls.
7. AWS termination and EC2/EBS cleanup.
8. GCS output-manifest and completion-marker validation.

### End-to-end staging test

```text
FastAPI job
  -> PostgreSQL records
  -> generation-pinned GCS manifest
  -> authenticated AWS submission
  -> one AWS Batch T4 job
  -> GCS artifacts and completion marker
  -> callback loss/replay test
  -> GCP reconciliation
  -> authorized FastAPI result
```

## 22. Implementation tasks

Dependency order:

```text
A0 ──> A2 contracts ──> A3 submission boundary
                             |
A1 ──────────────────────────┴──> A4 keyless WIF ──> A5 ECR image
                                      |                   |
                                      └─────────┬─────────┘
                                                v
                                      A6 Batch foundation
                                                |
                                      explicit cost checkpoint
                                                |
                                                v
                                      A7 one-image acceptance
                                                |
                                                v
                                      A8 five-image/replay

A3 + A7/A8 ──> A9 AWS provider status/cancel
A8 + A9 ──> A10 FastAPI/reconciliation staging ──> A11 optional optimization
```

Complete each numbered subtask and its evidence before moving to the next dependency. Planning or
unit work may overlap, but Terraform applies, database migrations, identity grants, image
promotion, and live GPU tests remain sequential.

### A0. Accept the hybrid boundary

Acceptance:

- [x] ADR-004 is reviewed and accepted.
- [x] GCP remains authoritative for application state and customer artifacts.
- [x] The initial AWS region is `eu-north-1` (Europe/Stockholm).
- [x] The target AWS account identity is verified.
- [x] The initial environment is `staging`; the project owner owns infrastructure, security, and
  billing decisions.
- [x] The five-item limit and one-job-per-application-job rule are accepted.

Status: Complete.

Verify:

- Review this contract with worker, backend, GCP, AWS, security, and billing owners.

### A1. Run read-only AWS feasibility preflight

Acceptance:

- [x] `g4dn.xlarge` offerings and On-Demand G-family vCPU quota are recorded.
- [x] AWS Batch, ECR, VPC, CloudWatch, DynamoDB, API Gateway, Lambda, and EventBridge availability is
  confirmed in the selected region.
- [x] Expected hourly and persistent baseline costs are estimated.

Status: Complete. Budget and alert creation was not required for this read-only preflight and was
configured separately after A1.

Verify:

- Redacted evidence: [AWS GPU A1 feasibility preflight](03a-aws-a1-preflight-2026-07-24.md).

Dependencies: A0.

### A2. Define and test provider-neutral contracts

Purpose: freeze the data exchanged between the external FastAPI repository, GCP orchestrator,
submission Lambda, AWS Batch job, and existing worker before deploying infrastructure.

Status: Complete in this repository. The external FastAPI repository path was not available in this
workspace, so its matching models, canonical hash, persistence fields, and five-item tests remain a
required cross-repository follow-up. Evidence:
[A2 contract verification](../../aws-acceptance/evidence/a2-contracts/contract-verification.txt).

Fixed staging values used by A2-A11:

| Setting | Value |
| --- | --- |
| Environment | `staging` |
| AWS region | `eu-north-1` |
| Resource prefix | `watermark-staging` |
| Provider enum | `AWS_BATCH` |
| Pipeline | `watermark-v1` |
| Maximum items | `5` |
| Instance | One On-Demand `g4dn.xlarge` |
| Batch vCPU range | `min=0`, `desired=0`, `max=4` |
| Job resources | `4` vCPU, `14336` MiB, `1` GPU |
| Acceptance timeout/attempts | `5400` seconds, `1` attempt |
| JWT issuer | `https://accounts.google.com` |
| JWT audience | `urn:watermark:aws-gpu-submission:staging` |
| GCP orchestrator service account | `watermark-aws-orchestrator` |
| GCP worker service account | Existing `watermark-gpu-worker` |
| WIF pool/provider | `watermark-aws` / `staging` |
| DynamoDB TTL | `90` days |
| CloudWatch log retention | `30` days |
| Network | Public Batch subnets, no inbound, no NAT gateway |
| Monthly AWS budget | `$60`, already configured globally |

Values that must be discovered and recorded rather than guessed:

- full AWS account ID and caller ARN;
- GCP project number;
- Terraform state bucket;
- exact numeric `uniqueId` of the orchestrator service account;
- exact GCS input, manifest, model, and output bucket/prefix allowlists;
- WIF provider audience and worker service-account impersonation URL;
- ECR digest produced by A5;
- Terraform/provider versions selected by `terraform init`; and
- path to the separate FastAPI backend repository.

#### A2.1 Establish contract ownership and fixtures

Files in this repository:

```text
contracts/aws-gpu-v1/
services/aws_submission/src/aws_submission/models.py
services/aws_submission/src/aws_submission/errors.py
tests/unit/test_aws_submission_contract.py
```

Files in the external backend repository:

```text
BACKEND_REPO/app/.../watermark/provider_contracts.py
BACKEND_REPO/tests/.../test_watermark_provider_contracts.py
```

Steps:

1. Copy the accepted request/response examples from Section 7 into versioned JSON fixtures.
2. Treat the fixtures plus this specification as the cross-repository source contract. Do not make
   either service import runtime code from the other repository.
3. Create strict Pydantic 2 models for submit, status, cancellation, normalized provider state, and
   stable errors. Reject unknown request fields; allow unknown additive response fields.
4. Define the canonical immutable request fields in one ordered tuple:
   `schemaVersion`, `applicationJobId`, `manifestUri`, `manifestGeneration`,
   `outputManifestUri`, and `pipelineVersion`.
5. Canonicalize with UTF-8 JSON, sorted keys, compact separators, and no floats; compute lowercase
   SHA-256 in both repositories and prove identical fixture hashes.
6. Keep `requestSha256` as a caller diagnostic. Lambda recomputes it and rejects a mismatch; it
   never accepts the supplied value as authority.

#### A2.2 Extend the provider-neutral application contract

In the backend logical model, add or confirm:

```text
computeProvider
providerRegion
providerJobId
providerJobArn
providerAttempt
providerStatus
manifestGeneration
requestSha256
lastReconciledAt
```

Do not add AWS fields to public responses. Do not remove any existing Google-provider fields until a
separate migration proves they are unused. `manifestGeneration` is a decimal string throughout; do
not parse it through a floating-point type.

FastAPI must:

1. authorize every asset before creating a job;
2. reject zero items, more than five items, duplicate assets, arbitrary GCS URIs, or an unsupported
   pipeline;
3. store the generation returned when the immutable manifest is created;
4. store the canonical request hash in the same durable application transaction/outbox intent; and
5. return the same application job for the same client idempotency key and immutable payload.

#### A2.3 Preserve the worker schema

Provider metadata is optional and additive. Add parsing tests showing that schema-version-1
consumers:

- accept `computeProvider`, `providerRegion`, `providerJobId`, and `providerAttempt`;
- still reject missing required worker fields;
- ignore an unknown future response field; and
- produce byte-compatible required result and marker fields.

No inference code should change in A2.

#### A2.4 Test and record the contract

Run in this repository:

```bash
uv run pytest tests/unit/test_aws_submission_contract.py tests/unit/test_contracts.py -q
uv run mypy
uv run ruff check .
```

Run the matching contract tests in `BACKEND_REPO`. Store redacted fixture/hash output under:

```text
aws-acceptance/evidence/a2-contracts/
```

Negative cases must include:

- path/body application-job mismatch;
- malformed generation, URI, job ID, SHA-256, or enum;
- disallowed bucket/prefix or pipeline;
- six items;
- same ID with different immutable content;
- unknown request field; and
- unknown additive response field.

Rollback:

- Revert only the un-deployed contract branch/changes. A2 creates no cloud resources or database
  migration.

Acceptance:

- [ ] `manifestGeneration` is present from FastAPI persistence through AWS worker invocation.
- [ ] Submit/status/cancel schemas, normalized states, and stable errors are typed in both repos.
- [ ] Canonical hashing produces the same value in both repositories.
- [x] Provider metadata remains additive to worker schema version 1.
- [ ] FastAPI contract tests enforce at most five items.
- [x] Unit, typing, and lint checks pass in this repository.

Dependencies: A0. Estimated scope: medium in each repository; complete as two focused sessions.

### A3. Build the AWS submission boundary

Purpose: deploy the narrow authenticated API, Lambda, and idempotency ledger without yet creating or
starting GPU compute.

Status: Complete. Deployed to staging in `eu-north-1` with submission disabled. Evidence:
[A3 submission boundary](../../aws-acceptance/evidence/a3-submission-boundary/).

#### A3.1 Bootstrap Terraform safely

Prerequisites:

- Install Terraform from the official HashiCorp package channel.
- Create or select a dedicated GCS Terraform-state bucket in the GCP project. Enable object
  versioning and uniform bucket-level access. Grant state access only to the implementation
  identity.
- Record the bucket name outside source control and initialize the `gcs` backend with a
  `watermark/aws/staging` prefix.
- Run `aws sts get-caller-identity` and reject an unexpected account before every plan/apply.

Create the Terraform layout from Section 20. `versions.tf` declares bounded Terraform/AWS/archive
provider constraints; commit the generated `.terraform.lock.hcl`. `providers.tf` fixes
`eu-north-1`; no resource may silently use the CLI default region.

The real `staging.tfvars` remains untracked and includes only non-secret identifiers:

```text
environment
aws_region
aws_account_id
gcp_project_id
gcp_project_number
google_jwt_issuer
google_jwt_audience
expected_google_subject
approved_manifest_buckets
approved_output_buckets
pipeline_allowlist
```

Create the dedicated `watermark-aws-orchestrator` GCP service account before deploying the
authorizer. Record its numeric `uniqueId`; the email address alone is not the authorization key.
Grant it no AWS or GCS permission merely because it exists.

#### A3.2 Implement the Lambda as a separate package

Use `services/aws_submission/` with Python 3.12, Pydantic 2, Boto3, pytest, mypy, and ruff pinned in
its own lock file. The GPU worker package must not acquire Lambda-only dependencies.

Modules and responsibilities:

| Module | Responsibility |
| --- | --- |
| `handler.py` | Route dispatch, correlation ID, normalized HTTP responses. |
| `models.py` | Strict request/response models from A2. |
| `auth.py` | Read only API Gateway verified JWT claims; exact-subject check. |
| `canonical.py` | URI allowlisting and canonical request SHA-256. |
| `ledger.py` | DynamoDB conditional reservation and state transitions. |
| `batch_adapter.py` | Fixed submit/describe/terminate calls and AWS error normalization. |
| `errors.py` | Stable error enum and retryability mapping. |

Rules:

1. Dispatch only the three exact route keys from Section 7. Return `404` for every other route.
2. Require the HTTP API payload-format-2 JWT authorizer context. Never decode an unverified bearer
   token inside Lambda.
3. Compare the verified `sub` claim to `EXPECTED_GOOGLE_SUB` with an exact string comparison before
   reading or writing DynamoDB.
4. Validate path/body IDs, GCS allowlists, generation syntax, pipeline allowlist, and canonical hash.
5. Do not log authorization headers, JWT claims beyond a one-way subject fingerprint, request
   bodies containing customer paths, temporary credentials, or raw AWS exceptions.
6. Return a generated opaque correlation ID in every success/error response and structured log.
7. Keep infrastructure selection in Lambda environment variables populated by Terraform:
   queue ARN, job-definition ARN/revision, region, table name, timeout, retry count, and fixed tags.
   None are accepted from the caller.

#### A3.3 Implement the DynamoDB submission state machine

Terraform creates one on-demand table:

```text
partition key: applicationJobId (string)
TTL attribute: expiresAt (epoch seconds, createdAt + 90 days)
encryption: AWS-managed server-side encryption
point-in-time recovery: enabled
```

Required transitions:

```text
absent
  -> RESERVED
  -> SUBMITTING
  -> SUBMITTED

SUBMITTING
  -> SUBMISSION_UNCERTAIN

RESERVED
  -> FAILED_BEFORE_SUBMISSION
```

Implementation rules:

1. Reserve with `attribute_not_exists(applicationJobId)`.
2. Every update uses a condition on the expected prior state and request hash.
3. A matching `SUBMITTED` replay returns the stored job without calling Batch.
4. A mismatched hash returns `409 AWS_GPU_SUBMISSION_CONFLICT`.
5. `RESERVED`, `SUBMITTING`, or `SUBMISSION_UNCERTAIN` enters bounded reconciliation.
6. Reconciliation searches only the approved queue and deterministic job name, then validates
   non-sensitive correlation tags/hash before adopting a job ID.
7. If reconciliation remains ambiguous, return a retryable `503` and preserve
   `SUBMISSION_UNCERTAIN`; never submit a second job automatically.
8. TTL deletion is cleanup only. An expired record never authorizes reuse of an application job ID.

Use dependency-injected DynamoDB and Batch clients so all state transitions are hermetically
testable.

#### A3.4 Deploy API Gateway and Lambda in disabled-submit mode

Terraform creates:

- one Python 3.12 Lambda, outside the VPC, `512` MiB memory and `30` second timeout; the current
  account concurrency quota is `10`, and AWS requires all `10` to remain unreserved, so A3 uses
  unreserved Lambda concurrency while the API stage enforces `5` requests/second and burst `10`;
- one Lambda log group with 30-day retention;
- one DynamoDB table;
- one API Gateway HTTP API and explicit stage;
- one native JWT authorizer with issuer `https://accounts.google.com`, audience
  `urn:watermark:aws-gpu-submission:staging`, and identity source
  `$request.header.Authorization`;
- the three JWT-protected routes;
- API access logging with authorization headers and query strings excluded;
- stage throttle `5` requests/second and burst `10`; and
- Lambda error/throttle alarms without paging automation yet.

Set `SUBMISSION_ENABLED=false` until A6 supplies an approved queue and job definition. In this mode,
authenticated submit returns stable `503 AWS_GPU_SUBMISSION_DISABLED` without reserving DynamoDB;
status may read existing records and cancellation is a no-op for unknown jobs.

The Lambda role may initially read/write only the one DynamoDB table and write its logs. Add
resource-scoped Batch permissions in A6. Do not grant `batch:*`, `iam:*`, EC2, VPC, ECR write, or
Terraform permissions.

#### A3.5 Verify authentication and idempotency

Local/hermetic:

```bash
uv run --project services/aws_submission pytest services/aws_submission/tests -q
uv run --project services/aws_submission mypy --config-file services/aws_submission/pyproject.toml services/aws_submission/src
uv run --project services/aws_submission ruff check services/aws_submission
terraform -chdir=infra/aws fmt -check
terraform -chdir=infra/aws validate
```

Cloud negative tests:

1. no token -> API Gateway `401`, zero Lambda/DynamoDB calls;
2. malformed/expired token -> `401`;
3. correct Google issuer but wrong audience -> `401`;
4. correct issuer/audience but different Google service-account subject -> Lambda `403`, zero
   DynamoDB/Batch mutation;
5. wrong method/route -> `404`; and
6. valid caller while submit disabled -> stable `503`, zero DynamoDB reservation.

Hermetic idempotency tests use fake clients to prove:

- two identical requests produce one Batch submit call;
- a conflicting hash produces zero additional calls;
- a simulated timeout after Batch acceptance becomes `SUBMISSION_UNCERTAIN`;
- bounded discovery can adopt exactly one matching job; and
- ambiguous discovery never calls submit again.

Evidence:

```text
aws-acceptance/evidence/a3-submission-boundary/
  terraform-plan-redacted.txt
  unit-test-summary.txt
  auth-negative-cases.json
  idempotency-cases.json
  deployed-resource-inventory.json
```

Rollback:

1. Keep `SUBMISSION_ENABLED=false`.
2. Disable the API stage or remove route integrations through a reviewed Terraform plan.
3. Roll Lambda code back by immutable artifact hash.
4. Do not delete the DynamoDB table until it is empty or its evidence is exported and the retention
   decision is explicitly approved.

Acceptance:

- [x] One HTTP API uses native JWT validation; no Lambda authorizer exists.
- [x] One Lambda handles submit/status/cancel and requires the exact verified Google subject.
- [x] All invalid authentication cases fail before DynamoDB or Batch mutation.
- [x] Conditional idempotency and uncertain-submission reconciliation tests pass.
- [x] Caller-controlled infrastructure overrides are impossible.
- [x] Throttling, redacted access logs, Lambda logs, and basic alarms are configured.
- [x] Deployment remains `SUBMISSION_ENABLED=false` and has created no Batch/EC2 resource.

Dependencies: A2. Estimated scope: four medium sessions (package, ledger, Terraform, cloud auth).

### A4. Configure AWS-to-GCP Workload Identity Federation

Purpose: allow only the AWS Batch **job role** to impersonate the existing GCP
`watermark-gpu-worker` service account and access approved GCS data without a key file.

Status: Complete for infrastructure, worker code, hermetic tests, and static cloud verification.
The real token exchange and positive/negative GCS operations remain intentionally deferred to the
first A7 Batch run. Evidence: [A4 WIF](../../aws-acceptance/evidence/a4-wif/).

#### A4.1 Create the AWS job role first

Terraform creates `watermark-staging-gpu-job-role` with:

- trust principal `ecs-tasks.amazonaws.com`;
- no Batch submit/terminate, DynamoDB, IAM administration, EC2, or unrelated S3 access;
- only the AWS permissions genuinely required by the container (initially none beyond obtaining its
  own task credentials and writing through the configured log driver); and
- fixed environment/component tags.

Keep this job role separate from:

- the AWS Batch service-linked role;
- the ECS compute-instance profile used for ECR/ECS host operations;
- the Lambda submission role; and
- the human/Terraform deployment identity.

Record the exact IAM role name and ARN from Terraform outputs.

#### A4.2 Create the Google pool and AWS provider

Enable GCP IAM, Security Token Service, Service Account Credentials, and Resource Manager APIs.
Create:

```text
pool ID:     watermark-aws
provider ID: staging
AWS account: exact target AWS account
```

Attribute mapping:

```text
google.subject=assertion.arn
attribute.account=assertion.account
attribute.aws_role=assertion.arn.extract('assumed-role/{role_name}/')
```

Provider condition:

```text
assertion.account == 'OWNER_SUPPLIED_AWS_ACCOUNT_ID' &&
assertion.arn.startsWith(
  'arn:aws:sts::OWNER_SUPPLIED_AWS_ACCOUNT_ID:assumed-role/watermark-staging-gpu-job-role/'
)
```

Do not authorize the entire AWS account or pool. The GCP service-account binding uses:

```text
principalSet://iam.googleapis.com/projects/OWNER_SUPPLIED_GCP_PROJECT_NUMBER/locations/global/
workloadIdentityPools/watermark-aws/attribute.aws_role/watermark-staging-gpu-job-role
```

Grant that principal only `roles/iam.workloadIdentityUser` on the dedicated
`watermark-gpu-worker` service account. Do not grant Service Account Token Creator unless a tested
API specifically requires it.

#### A4.3 Apply least-privilege GCS access

Using the impersonated `watermark-gpu-worker` service account:

- grant object viewer only on the approved manifest/input/model buckets or root prefixes;
- grant object creator plus the minimum read permission required for collision/marker validation
  only on the approved output root prefix;
- do not grant bucket administration, object deletion, arbitrary overwrite, project editor, or
  access to unrelated buckets; and
- use IAM Conditions on `resource.name` where the existing bucket layout provides a stable prefix
  boundary.

Record every binding and condition in Terraform or a versioned GCP IAM file. Avoid manual console
bindings that Terraform cannot observe.

During A4 verification, three older unconditional bucket-wide bindings for
`watermark-gpu-worker` were discovered. With owner approval they were removed. Only the
Terraform-managed conditional bindings above remain.

#### A4.4 Add the worker credential adapter

Files:

```text
src/watermark_worker/aws_wif.py
src/watermark_worker/storage.py
src/watermark_worker/config.py
tests/unit/test_aws_wif_credentials.py
tests/integration/test_aws_gcs_federation.py
```

Implementation:

1. Add a reviewed Boto3 dependency to the AWS GPU lock/build.
2. Implement Google's official `AwsSecurityCredentialsSupplier` pattern using one Boto3 Session.
   Require the ECS container-credential URI environment before credential resolution; do not allow
   a missing task role to fall back to the EC2 instance profile. Boto3 resolves the ECS task-role
   credential endpoint when running in Batch.
3. Cache only the AWS region. Request refreshable Boto3 credentials when Google auth asks; do not
   copy credentials to disk or logs.
4. Construct `google.auth.aws.Credentials` from deployment-controlled WIF audience and
   service-account impersonation URL.
5. Update the GCS client factory to accept explicit Google credentials for `AWS_BATCH`; preserve
   normal ADC behavior for local/GCP execution.
6. Fail closed when the provider audience, impersonation URL, AWS region, task credentials, or
   expected project ID is missing or malformed.
7. Redact all credential material and signed STS requests from exceptions/logs.

Configuration:

```text
COMPUTE_PROVIDER=AWS_BATCH
AWS_REGION=eu-north-1
GCP_PROJECT_ID=project-5909a738-2fdc-451e-ad6
GCP_WIF_AUDIENCE=//iam.googleapis.com/projects/.../providers/staging
GCP_SERVICE_ACCOUNT_IMPERSONATION_URL=https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/...:generateAccessToken
```

These values are identifiers, not secrets. Temporary AWS and Google credentials are never supplied
as configuration.

#### A4.5 Verify in two stages

Before Batch exists:

- unit-test region/credential caching with fake Boto3 sessions;
- prove missing ECS/task credentials fail without falling back to a hard-coded key;
- inspect GCP IAM policy to prove only the exact role principalSet can impersonate; and
- run policy tests showing an unrelated role/account does not match the provider condition.

The live test is performed as the first step of A7 from the actual Batch job role:

1. read one generation-pinned manifest object;
2. create one object under the isolated acceptance output prefix;
3. read the created object if required by marker validation;
4. prove read/write denial against an unrelated prefix; and
5. delete the isolated test object later through the authorized GCP operator cleanup path, not by
   broadening the worker role.

Evidence:

```text
aws-acceptance/evidence/a4-wif/
  provider-description-redacted.json
  service-account-policy-redacted.json
  gcs-policy-bindings-redacted.json
  unit-test-summary.txt
```

Rollback:

1. Disable the WIF provider.
2. Remove the exact workload-identity-user binding from the service account.
3. Remove the worker's conditional GCS bindings.
4. Keep audit evidence; never create a service-account key as a rollback.

Acceptance:

- [x] Provider condition restricts the exact account and Batch job role.
- [x] Only that principalSet can impersonate `watermark-gpu-worker`.
- [x] GCS permissions are limited to approved buckets/prefixes.
- [x] Worker credential-adapter unit tests pass without exposing credentials.
- [x] No Google service-account key or long-lived AWS credential exists.
- [x] Live positive/negative GCS proof is explicitly deferred to the first A7 Batch run.

Dependencies: A1 and the A3 Terraform foundation. Estimated scope: three medium sessions.

### A5. Publish and verify the ECR worker image

Purpose: build the reviewed worker plus the A4 WIF adapter once, test it without a GPU, and publish
an immutable ECR digest.

Status: Complete. The Linux AMD64 image `watermark-worker:a5-20260725-d983003e6b33` was built
successfully, passed its CPU-only container gates and configuration/history review, and was
published under an immutable Stockholm ECR tag. A6 must use this exact digest reference:

```text
154959837939.dkr.ecr.eu-north-1.amazonaws.com/watermark-staging-worker@sha256:caa1f348edd423b6f64c1fbb0c7a9bdc46e6106c4da29d97bedfd363507aa218
```

Evidence: [A5 completion](../../aws-acceptance/evidence/a5-image/a5-completion.txt) and
[A5 local-build checkpoint](../../aws-acceptance/evidence/a5-image/local-build-checkpoint.txt).

Operator decisions (2026-07-25): vulnerability scanning and the embedded-secret image check are
deferred and are not A5 acceptance gates. ECR scan-on-push is disabled. Build-context exclusions
and the prohibition against including credentials, tokens, keys, customer objects, or environment
files in the image remain required, but A5 does not run an automated image scanner.

#### A5.1 Create and constrain ECR

Terraform creates one private `watermark-staging-worker` repository with:

- vulnerability scan-on-push disabled for this initial milestone;
- immutable tags;
- AWS-managed encryption;
- no cross-account or public repository policy; and
- lifecycle cleanup only for old **untagged** images. Never expire a digest referenced by an active
  job-definition revision.

#### A5.2 Build from locked inputs

Before building:

```bash
uv lock --check
uv run pytest -q
uv run mypy
uv run ruff check .
docker build \
  --platform linux/amd64 \
  --build-arg GPU_BASE_IMAGE=REVIEWED_GPU_BASE_IMAGE_BY_DIGEST \
  --file Dockerfile.gpu \
  --tag watermark-worker:BUILD_ID \
  .
```

The Dockerfile must:

- keep the reviewed CUDA 12.1/PyTorch base pinned by digest;
- install the locked GPU requirements including compatible `google-auth`, `google-cloud-storage`,
  and Boto3;
- run as UID/GID `10001`;
- contain no package manager or model download during worker startup;
- include no `.env`, AWS files, Google keys, Terraform files/state, database secrets, or customer
  objects; and
- preserve the deterministic worker entrypoint.

#### A5.3 Run CPU-only container gates

Run:

```bash
RUN_CONTAINER_TESTS=1 \
WM_CONTAINER_SKIP_BUILD=1 \
WM_CONTAINER_RUNTIME_KIND=gpu \
WM_CONTAINER_TEST_IMAGE=watermark-worker:BUILD_ID \
WM_DOCKER_CONTEXT=desktop-linux \
uv run pytest tests/container/test_container_smoke.py -m container -q \
  --basetemp="$PWD/.a5-container-test-tmp"
docker inspect watermark-worker:BUILD_ID
docker history --no-trunc watermark-worker:BUILD_ID
```

Verify:

- production modules import without initializing CUDA/models;
- the worker runs as non-root;
- a read-only filesystem run works with only approved temporary mounts;
- local completed replay makes zero model-stage calls;
- WIF adapter is not selected for local/GCP provider modes.

#### A5.4 Push and record the digest

Authenticate Docker to the Stockholm ECR registry, tag with an immutable build ID, push, and resolve
the repository digest:

```bash
aws ecr get-login-password --region eu-north-1 \
  | docker login --username AWS --password-stdin OWNER_SUPPLIED_ECR_REGISTRY
docker tag watermark-worker:BUILD_ID OWNER_SUPPLIED_ECR_REPOSITORY:BUILD_ID
docker push OWNER_SUPPLIED_ECR_REPOSITORY:BUILD_ID
aws ecr describe-images \
  --region eu-north-1 \
  --repository-name watermark-staging-worker \
  --image-ids imageTag=BUILD_ID
```

Use only:

```text
OWNER_SUPPLIED_ECR_REPOSITORY@sha256:...
```

in the job definition. Record the local image ID, ECR digest, source commit, lock hashes, base-image
digest, build timestamp, image configuration/history review, and test summary.

Evidence:

```text
aws-acceptance/evidence/a5-image/
```

Rollback:

- Deregister future job-definition revisions that reference a rejected digest. Do not delete a
  digest while any active/retryable job references it.

Acceptance:

- [x] Linux AMD64 image is in private ECR under an immutable tag and digest.
- [x] CPU import, ABI, non-root, read-only, replay, and WIF-selection gates pass.
- [x] Digest/build evidence is recorded.

Dependencies: A4 code/configuration design. Estimated scope: two medium sessions.

### A6. Create bounded AWS Batch infrastructure

Purpose: create a disabled/idle AWS Batch foundation whose hard limits make more than one
`g4dn.xlarge` impossible. Creating A6 must not launch a GPU.

Implementation status (2026-07-25): Complete. A reviewed saved Terraform plan added 23 resources,
updated 2 in place, destroyed 0, and was applied successfully. The managed compute environment and
queue are `VALID`; the queue is `DISABLED`; Lambda submission is `false`; and job definition
`watermark-staging-worker:1` uses the exact A5 digest
`sha256:caa1f348edd423b6f64c1fbb0c7a9bdc46e6106c4da29d97bedfd363507aa218`.
The final Terraform plan reports no changes. No job was submitted and live verification found zero
EC2 instances, EBS volumes, ECS container instances/tasks, Spot requests, NAT Gateways, Elastic
IPs, or load balancers. Evidence:
[A6 Batch foundation](../../aws-acceptance/evidence/a6-foundation/).

#### A6.1 Create the low-baseline network

Terraform creates:

- VPC `10.40.0.0/16`;
- two public `/24` subnets in two offered Stockholm zones;
- one internet gateway and public route table;
- public IPv4 mapping for temporary Batch instances;
- one Batch security group with no ingress; and
- reviewed outbound HTTPS/DNS rules.

Do not create NAT Gateway, Elastic IP, load balancer, VPN, Direct Connect, bastion, SSH key, or
database. Confirm the Terraform plan contains none of those resources.

Implementation: `infra/aws/batch-network.tf`.

#### A6.2 Create the distinct AWS runtime identities

1. AWS Batch service-linked role: control-plane role managed for Batch.
2. ECS compute-instance profile: ECS registration, ECR pull, and host/log operations only.
3. ECS task execution role: ECR image pull and `awslogs` delivery only.
4. Batch job role from A4: task-level WIF identity only.
5. Lambda role from A3: table plus fixed Batch submit/list/describe/terminate permissions.

Lambda does not receive `iam:PassRole`. It cannot supply or override a role in `SubmitJob`; the
reviewed job definition already contains the task and execution roles. The Terraform deployment
identity needs the relevant pass-role authority when registering that definition, not the runtime
Lambda. `SubmitJob` is restricted to the exact queue, exact job-definition revision, account job ARN
space, and Stockholm. `ListJobs` and `DescribeJobs` must use `Resource="*"` because AWS Batch does not support resource-level
authorization for those actions. `TerminateJob` is restricted to Stockholm account job ARNs. The
`aws:RequestedRegion=eu-north-1` condition remains on every Batch statement.

Implementation: `infra/aws/batch-iam.tf` and the conditional statements in `infra/aws/iam.tf`.

#### A6.3 Create the launch template and compute environment

Launch template:

- no key pair;
- IMDSv2 required with a container-restricting response hop limit;
- encrypted `gp3`, delete-on-termination, 200 GiB total initial storage;
- no user-data credentials or custom ECS settings managed by AWS Batch; and
- environment/component tags propagated to EC2/EBS where supported.

Managed EC2 compute environment:

```text
state: ENABLED
type: EC2 On-Demand
allocation strategy: BEST_FIT
instance types: g4dn.xlarge only
min vCPUs: 0
desired vCPUs: 0
max vCPUs: 4
subnets: the two dedicated public subnets
security group: no inbound
instance profile: dedicated ECS compute profile
GPU-optimized ECS image type supported by current AWS Batch
```

`BEST_FIT` is deliberate. For On-Demand compute environments, AWS documents that allocation
strategies other than `BEST_FIT` can exceed `maxvCpus` by one instance. With only one allowed
instance type there is no useful progressive choice, and `BEST_FIT` preserves the strict
one-`g4dn.xlarge` ceiling.

Let AWS Batch select the current supported GPU-optimized AMI; record the effective AMI/driver during
A7. Do not pin an unverified AMI ID.

Implementation: `infra/aws/batch-compute.tf`. The launch template has no AMI, key pair, or user
data; requires IMDSv2; uses a 200 GiB encrypted `gp3` root volume; and propagates reviewed tags.
The compute environment uses `ECS_AL2023_NVIDIA`, `min=0`, `desired=0`, and `max=4`.

#### A6.4 Create queue and immutable job definition

Queue:

- dedicated to this pipeline;
- priority `1`;
- attached only to the one compute environment; and
- initially disabled until all static checks pass.

Job definition:

```text
platform: EC2
image: ECR digest from A5
command: fixed worker entrypoint
VCPU: 4
MEMORY: 14336
GPU: 1
attempts: 1
timeout: 5400 seconds
job role: dedicated A4 task role
execution role: dedicated ECS task execution role when required
log driver: awslogs
privileged: false
readonly root: enabled when container acceptance supports it
```

Deployment-controlled environment includes only provider/region/WIF/project identifiers and
non-sensitive config. Per-job overrides are allowlisted to:

```text
JOB_ID
MANIFEST_URI
MANIFEST_GENERATION
OUTPUT_MANIFEST_URI
PIPELINE_VERSION
PROVIDER_ATTEMPT
```

Lambda constructs those values from the validated request. It cannot override image, command,
roles, queue, resources, retry, timeout, or networking.

Implementation: `infra/aws/batch-job.tf` and
`services/aws_submission/src/aws_submission/batch_adapter.py`.

The queue is controlled by `batch_queue_enabled` and defaults to `DISABLED`. The job definition is
created only when `worker_image_digest` is a valid lowercase `sha256:...` value. Its image is
`watermark-staging-worker@sha256:...`; it passes the digest itself to the worker's audit field. The
fixed command accepts only the generation-pinned GCS manifest and packaged config/model-manifest
paths. A Docker-managed scratch volume is mounted at `/tmp/watermark-worker`, allowing the
non-root worker to use its approved work directory while the container root filesystem remains
read-only.

#### A6.5 Wire and inspect without submitting

After queue/job-definition creation:

1. update Lambda environment to the exact queue/job-definition ARN;
2. add resource-scoped Batch permissions;
3. keep `SUBMISSION_ENABLED=false`;
4. keep the job queue disabled through A6; enabling it is part of the explicit A7 cost checkpoint;
5. run `terraform plan` again and require no unexpected changes; and
6. query the deployed resources with `describe-*`.

Digest gate procedure after the A5 upload:

1. use `aws ecr describe-images` to read the immutable digest for the uploaded tag;
2. put only that `sha256:...` value in the ignored staging tfvars as `worker_image_digest`;
3. keep `batch_queue_enabled=false` and `submission_enabled=false`;
4. rebuild the Lambda ZIP because its adapter parameter names changed;
5. run `terraform fmt -check`, `terraform validate`, tests, and a reviewed plan;
6. apply only after the plan has no forbidden or surprising resources; and
7. collect the deployed-resource and zero-capacity evidence below.

The Lambda environment receives the Terraform-created queue ARN and, after the digest gate, the
exact job-definition revision ARN. Its lifecycle precondition prevents submission from being
enabled unless the image exists and the queue is enabled.

Static checks:

```bash
aws batch describe-compute-environments --region eu-north-1
aws batch describe-job-queues --region eu-north-1
aws batch describe-job-definitions --region eu-north-1 --status ACTIVE
aws ec2 describe-instances --region eu-north-1
aws ec2 describe-volumes --region eu-north-1
```

Expected result: compute environment `VALID`, queue `VALID`, exact resources above, and **zero**
Batch-managed EC2 instances/volumes because `min=desired=0` and no job exists.

Do not run the old “CPU-light lifecycle” check here: this GPU-only environment would still launch a
billable GPU instance. The first lifecycle/WIF/GPU check is A7 after explicit authorization.

Evidence:

```text
aws-acceptance/evidence/a6-foundation/
  terraform-plan-redacted.txt
  policy-checks.txt
  compute-environment.json
  queue.json
  job-definition-redacted.json
  zero-capacity-proof.json
```

Rollback:

1. Ensure no job is queued/running.
2. Keep submission disabled and disable the queue.
3. Disable the compute environment and wait for it to become `VALID`.
4. Apply a reviewed Terraform rollback/destroy plan in dependency order. Never manually terminate
   Batch-managed instances while the environment can recreate them.
5. Recheck EC2 instances, EBS volumes, public IPv4 addresses, NAT gateways, and load balancers.

Acceptance:

- [x] Local Terraform permits only `g4dn.xlarge` and maximum 4 vCPUs.
- [x] Queue, job definition, Lambda wiring, and submission adapter pass static tests.
- [x] Terraform formatting and local provider-schema validation pass.
- [x] Queue and digest-pinned job-definition revision are valid.
- [x] Resources, roles, timeout, attempts, logs, storage, and network match the runbook.
- [x] Lambda cannot accept infrastructure overrides and remains submission-disabled in code.
- [x] No GPU instance, EBS worker volume, NAT gateway, or other unexpected resource is running.

Dependencies: A4 and A5. Estimated scope: three medium sessions.

### Checkpoint: AWS foundation

- [x] A0-A6 repository acceptance evidence is reviewed; the separate A2 FastAPI mirror remains
  pending.
- [x] No GPU acceptance job has been submitted without explicit cost/quota authorization.
- [x] No long-lived cross-cloud credentials exist.
- [x] AWS account is on the Paid Plan; the Free Tier API reports plan status `ACTIVE`.
- [x] Submission remains disabled and current EC2/EBS/GPU capacity is zero.
- [x] The operator approves the estimated one-image acceptance cost and a maximum of one live
  `g4dn.xlarge`.

### A7. Run one-image GPU acceptance

Purpose: spend money for the first time and prove the exact deployed identity, image, GPU, storage,
and cleanup path with one image. A7 requires a fresh explicit go-ahead immediately before
submission.

Phase 1 status (2026-07-25): Complete without GPU spend. The Paid Plan, quota, zero-capacity
state, A5 digest, current pricing, selected local PNG, create-only GCS upload, pinned one-image
manifest, pinned model generations, empty output prefix, canonical A2 request, and enable-only
Terraform plan are recorded under
[A7 one-image evidence](../../aws-acceptance/evidence/a7-one-image/). The plan was not applied;
the queue and submission remain disabled; and the final disabled-state plan reports no changes.
After explicit approval, `moaaz7151@gmail.com` received
`roles/iam.serviceAccountTokenCreator` on only `watermark-aws-orchestrator`. A temporary token with
the exact staging audience was held only in process memory; the authenticated nonexistent-job GET
returned `404 AWS_GPU_NOT_FOUND`, proving API Gateway and exact-subject Lambda authorization
without creating a ledger entry or Batch job.

Phases 2-4 status (2026-07-26): Attempted once; A7 remains incomplete. The queue and submission
gate were enabled through Terraform, and the canonical request was accepted as Batch job
`44d5d030-b405-4bf9-b655-71b464cb56da`. An identical replay returned the same provider job ID,
after which submission was disabled. The first pre-submission failures exposed a missing
`batch:TagResource` dependent permission for tagged `SubmitJob` calls; the policy was corrected
with the same fixed queue, job-definition, and account job ARN scope, and the Lambda now records
the internal AWS exception while continuing to return the stable redacted API error envelope.

The one live job created exactly one `g4dn.xlarge` and proved a Tesla T4, CUDA capability `7.5`,
PyTorch `2.5.1+cu121`, the A5 image digest, and keyless WIF reads of the pinned manifest, source,
and selected model artifacts. It then failed with exit code `10` during
`background_removal` initialization because Hugging Face defaulted its cache to the read-only
`/home/watermark-worker/.cache` path. No detection/inpainting stage or GCS result marker ran, and
no retry was submitted. The queue is `DISABLED`, submission is false, desired vCPUs are zero, the
sole EC2 instance is terminated, its delete-on-termination EBS volume is gone, ECS capacity is
empty, and no NAT gateway, Elastic IP, or load balancer exists. The deterministic AWS runtime
estimate is `$0.071122` before CloudWatch and cross-cloud data transfer. Full evidence and the
required cache-path remediation steps are under
[A7 one-image evidence](../../aws-acceptance/evidence/a7-one-image/).

Runtime cache remediation status (2026-07-26): Complete without a GPU run, local image rebuild,
or ECR upload. Terraform registered `watermark-staging-worker:2` using the unchanged A5 image
digest and configured `HF_HOME`, `TRANSFORMERS_CACHE`, `HUGGINGFACE_HUB_CACHE`, and
`XDG_CACHE_HOME` beneath the writable `/tmp/watermark-worker` scratch mount. Lambda and its
submission policy now reference revision 2. The queue remains `DISABLED`, submission remains
false, desired vCPUs remain zero, and live AWS CLI checks found no active Batch jobs, EC2
instances, EBS volumes, Spot requests, ECS tasks, or ECS container instances. The post-apply
Terraform plan reports no changes. Dockerfile defaults contain the same paths for the next natural
image release, but that release is not required for the next controlled A7 retry.

Second live attempt status (2026-07-26): Failed later in the pipeline and cleaned up. Fresh
application job `aws_a7_retry_20260725t221122z` mapped idempotently to Batch job
`e8e7fcb8-817c-419b-a525-cfc6da9a2e48`. Revision 2 fixed the original cache failure:
background removal and detection both initialized and ran on the T4. Inpainting then exited `3`
with the generic `MODEL_CHECKSUM_MISMATCH` code before producing GCS results. Offline reproduction
proved that the pinned LaMa archive checksum and layout were correct. The actual failure was an
eager import in `saicinpainting.evaluation.__init__` that loaded training-only evaluator modules
and raised `ModuleNotFoundError: No module named 'models'` when the inference runtime imported
`evaluation.utils`. Those evaluator imports are now lazy and a regression test protects the
inference-only import boundary.

Image remediation deployment status (2026-07-26): The fix was remotely built as
`a7-lama-fix-20260726-1`, passed the build-phase CPU import check, and was pushed at digest
`sha256:596493d0665a053a52861ad0b3b585700c2f329238d01ae4a5583e82c656d95d`.
The first CodeBuild run was marked failed only after the push because its final digest lookup lacked
`ecr:DescribeImages`; that scoped permission is now deployed and regression-tested. No rebuild is
required. Terraform registered `watermark-staging-worker:3` with the new digest and updated Lambda
to reference revision 3 while keeping submission false and the queue disabled. Desired vCPUs are
zero, and live checks found no EC2 instances or EBS volumes.

Final one-image acceptance status (2026-07-26): **Succeeded and cleaned up.** Fresh application job
`aws_a7_lama_fix_20260726t095002z` mapped idempotently to the single Batch job
`0f660e5f-580c-452a-8af7-7540f84b9d3d`. Revision 3 ran on one `g4dn.xlarge` with a Tesla T4,
PyTorch `2.5.1+cu121`, and CUDA capability `7.5`. Background removal, detection, and LaMa
inpainting all initialized, ran, and released successfully. The item completed with decision
`INPAINTED`; the final PNG, per-item completion marker, job completion marker, result JSON, and
output manifest were written to the isolated GCS prefix. The container exited `0` after about
231 seconds. The instance existed for about 500 seconds, giving an estimated AWS runtime cost of
`$0.081375` before delayed CloudWatch, transfer, credits, and taxes.

Cleanup is complete: submission is false, the queue is `DISABLED`, desired vCPUs are zero, and
there are no active Batch jobs, EC2 instances, EBS volumes, ECS tasks/container instances, Spot
requests, NAT gateways, Elastic IPs, or load balancers. Terraform reports no changes. Redacted
evidence is in
[the final A7 outcome](../../aws-acceptance/evidence/a7-one-image/lama-fix-run-outcome.json).

Remote image-build path status (2026-07-26): Deployed and first build completed. Terraform created the
private short-lived source bucket, mutable ECR BuildKit cache repository, scoped CodeBuild role,
14-day CloudWatch log group, and on-demand project
`watermark-staging-worker-image-build`. The project has no webhook or pipeline and Terraform cannot
start a build. The first operator-started build populated the source and cache repositories and
pushed the corrected release image. The Batch queue remains disabled with desired vCPUs zero, and
there are no active Batch jobs, EC2 instances, or EBS volumes.

Operational ownership after this attempt is intentionally split. Codex fixes and locally verifies
reported errors only. The operator explicitly starts and monitors the remote image build, performs
Terraform deployment, authentication, one-job submission, monitoring, and cleanup using the
[A7 manual operator runbook](04-a7-manual-operator-runbook.md). A new GPU attempt requires a fresh
job ID and explicit operator action.

#### A7.1 Pre-submit safety check

Record:

```bash
aws sts get-caller-identity
aws service-quotas get-service-quota \
  --service-code ec2 \
  --quota-code L-DB2E81BA \
  --region eu-north-1
aws batch describe-compute-environments --region eu-north-1
aws batch list-jobs --job-queue OWNER_SUPPLIED_QUEUE --job-status RUNNING --region eu-north-1
aws ec2 describe-instances --region eu-north-1
aws ec2 describe-volumes --region eu-north-1
```

Require:

- Paid Plan active;
- quota remains 4 vCPUs;
- one valid compute environment and queue;
- no existing queued/running watermark job;
- no running Batch EC2 worker;
- monthly budget remains `$60` and healthy;
- exact ECR digest/job-definition revision recorded;
- one-image manifest and every source/model generation pinned; and
- expected output prefix empty.

The live AWS Price List check on 2026-07-25 reports `$0.558` per On-Demand Linux
`g4dn.xlarge` hour, `$0.0836` per `gp3` GB-month, and `$0.005` per in-use public IPv4 hour.
The deterministic 90-minute ceiling is approximately `$0.8789` before logs and data transfer.
Refresh the prices and stop for explicit approval immediately before submitting.

#### A7.2 Enable exactly one controlled submission

Set `batch_queue_enabled=true` and `submission_enabled=true` together through a reviewed Terraform
plan. The queue was deliberately left disabled in A6, so both gates are required for the one
controlled submission. Do not change the definition, role, resource, timeout, network, or any
other field.

Obtain a Google-signed ID token from the exact `watermark-aws-orchestrator` service account with
audience `urn:watermark:aws-gpu-submission:staging`. Invoke the internal `PUT` once using the A2
canonical request. Do not paste or save the token into evidence, shell history, logs, or source
files.

Immediately:

1. record the sanitized API response, provider job ID/ARN, and correlation ID;
2. repeat the same `PUT` and prove the same provider job ID is returned;
3. inspect DynamoDB and prove one reservation exists; and
4. set `submission_enabled=false` again while leaving the queue enabled for the accepted job. The
   existing job continues; no second API job can be created. Disable the queue after the job
   reaches a terminal state.

#### A7.3 Make WIF the first in-container gate

Before loading any model, the worker constructs Google credentials through the A4 custom Boto3
supplier and reads the pinned manifest generation. A successful manifest/model read and
conditional result write prove the live task-role WIF path. The exact Batch job role plus the A4
conditional GCS bindings and negative-scope tests prove the denied boundary; do not rebuild the
accepted 6.5 GB image solely to add a special unrelated-prefix probe. No model initializes if the
credential exchange or manifest read fails.

Capture only pass/fail, resource names already approved for evidence, and stable error codes. Use
Batch job/task metadata to prove the exact task role; do not add credential-fingerprint logging.

#### A7.4 Run and inspect the GPU pipeline

Required evidence:

- instance type `g4dn.xlarge`;
- one NVIDIA T4, its CUDA capability, and the packaged PyTorch version;
- the GPU acceptance recorder starts successfully and real model stages run on CUDA;
- effective AWS Batch GPU-optimized AMI ID;
- container image digest exactly matches A5;
- non-root UID/GID;
- each enabled model checksum and stage initialize/release order;
- peak VRAM/RAM/disk and model/input/output bytes;
- compatible per-item result and `_COMPLETE.json`;
- compatible job `output-manifest.json` and `_COMPLETE.json`; and
- worker and Batch job exit success.

Do not treat visual quality as accepted merely because system acceptance succeeds.

#### A7.5 Prove scale-down and calculate cost

After terminal Batch state:

1. wait for the managed compute environment to return desired/running capacity toward zero;
2. confirm no running Batch EC2 instance;
3. confirm no unattached worker EBS volume, leaked Elastic IP, NAT gateway, or load balancer;
4. record actual EC2/EBS/public-IPv4/log/transfer duration and estimated cost;
5. confirm the ECR image and Terraform-managed control-plane resources are the only intended
   persistent AWS resources; and
6. keep submission disabled.

Evidence:

```text
aws-acceptance/evidence/a7-one-image/
  request-redacted.json
  response-redacted.json
  batch-job.json
  device-and-runtime.txt
  stage-lifecycle.txt
  gcs-marker-inventory.json
  wif-positive-negative.txt
  resource-measurements.json
  cost-estimate.json
  cleanup-proof.json
```

Emergency stop:

1. disable submission;
2. `aws batch terminate-job --job-id ... --reason OWNER_ABORTED_ACCEPTANCE`;
3. disable the queue if another job appears;
4. wait for Batch/EC2 cleanup and verify volumes/public IPs;
5. do not manually delete GCS evidence or DynamoDB state during incident review.

Acceptance:

- [x] One replayed API request maps to exactly one On-Demand Batch job.
- [ ] Live task-role WIF access passes without a key and the A4 negative-scope evidence remains
  valid. Keyless manifest/source/model reads passed, but conditional GCS result creation was not
  reached.
- [x] One T4 and working CUDA are proven.
- [ ] Required models run in order and compatible GCS completion markers exist.
- [x] Actual duration/resource/network/cost evidence is recorded, with delayed CloudWatch and GCP
  transfer charges called out separately.
- [x] EC2 and worker EBS capacity return to zero and submission is disabled.

Historical note: A8 was originally held behind the A7 cache/read-only-root remediation and a fresh
live-cost checkpoint. Those prerequisites were satisfied by the successful revision 3 A7 attempt.
The subsequent A8 five-image run used the same accepted revision 3 digest and was returned to zero
capacity after completion.

Dependencies: Foundation checkpoint and explicit live-cost authorization. Estimated scope: one
carefully monitored acceptance session.

### A8. Run five-image and completed-replay acceptance

Status (2026-07-26): **Complete for the revised no-retry production scope.** The maximum five-image
happy path, submission idempotency, immutable completed replay, measurements, and zero-capacity
cleanup passed. Controlled interruption and explicit provider attempt 2 are deliberately deferred;
they remain required before production retries can be enabled.

Purpose: prove the maximum initial job and completed replay while production remains limited to one
provider attempt. Interruption/resume is retained below as the acceptance gate for any future retry
policy.

#### A8.1 Five-image lifecycle run

Repeat the A7 preflight and explicit authorization with a five-image manifest. Use a new
application job ID/output prefix and the same approved pipeline/image/model identities.

Verify in one worker process:

- exactly five items are read;
- each lifecycle stage initializes once, processes applicable items, and releases before the next;
- the safety gate blocks LaMa for a blocked item and emits no `finalUri`;
- individual item failures do not corrupt successful item markers;
- output counters match the five inputs; and
- EC2/EBS cleanup returns to zero.

Result: **Passed.** Application job `aws_a8_five_20260726t110131z` mapped idempotently to exactly
one Batch job, `81abcf51-00fe-477b-9e25-a6bfe45c19f1`. An identical API replay returned the same
provider job ID. Job-definition revision 3 ran the immutable worker digest
`sha256:596493d0665a053a52861ad0b3b585700c2f329238d01ae4a5583e82c656d95d` on one
`g4dn.xlarge`/Tesla T4 and exited `0`.

All three lifecycle stages initialized and released once. Four items completed with `INPAINTED`;
the resized user-designated blocked input had no detected watermark and completed safely as
`SKIPPED_NO_WATERMARK`. It was not classified as blocked, so this run records the model's actual
decision rather than claiming safety-gate blocking evidence. The output counts were total `5`,
succeeded `4`, skipped `1`, blocked `0`, and failed `0`. All five item markers, the output
manifest, and the job marker were written to the isolated GCS prefix.

#### A8.2 Controlled interruption/resume - deferred

Use a separate five-image acceptance job:

1. wait until at least one compatible item `_COMPLETE.json` exists;
2. terminate the first Batch attempt with reason `RESUME_ACCEPTANCE`;
3. record the first attempt state, marker generations, stage calls, duration, bytes, and cost;
4. inspect outputs—do not delete or modify compatible markers;
5. explicitly authorize one follow-up provider attempt with the same application job, manifest
   generation, image, config, model identity, and output prefix;
6. submit attempt `2` through the operator-only acceptance path; normal API replay must still return
   the first provider identity and must not create this retry automatically; and
7. prove attempt 2 skips compatible items and uses `ifGenerationMatch=0` rather than overwriting
   artifacts.

This acceptance-only retry proves the future policy. It does not enable automatic retries in
production.

Decision (2026-07-26): **Deferred while retries remain disabled.** A deterministic interruption
would require additional test-control code, a new image build/deployment, and another explicitly
authorized GPU attempt. The deployed Batch definition remains at one attempt, and neither automatic
retry nor the operator-only attempt-2 path is enabled. These checks must be completed before A10 or
any later change enables production provider retries. Deferral is not evidence that mid-job
resumability passed.

#### A8.3 Completed replay without another GPU

After completion, run the same manifest against the finished marker inventory using the worker's
CPU-only replay harness. Prove zero model-stage calls and no object generation changes. Do not
launch a third GPU merely to prove completed replay.

Result: **Passed.** The local worker replayed the exact manifest generation
`1785063797486050`. All five item completions and the job completion were reported with
`reused=true`; background removal, detection, inpainting, GPU lifecycle, and provider-job counts
were all zero. The GCS generation inventory contained 68 objects before and after replay, and its
canonical listing fingerprint remained
`497a00316e4f6a7e3fdb3a7439eb0c109e1867f5f1df85988b1efed3798ebd0f`.

#### A8.4 Compare evidence and set measured limits

Record:

- queue/provisioning, image-pull, WIF, model-download, stage, and total duration;
- peak GPU/RAM/disk;
- input/model/output bytes and cross-cloud direction;
- per-attempt EC2/EBS/public-IP/log/transfer cost;
- item markers before/after interruption;
- stage call counts for first attempt, resume, and completed replay; and
- cleanup after every live attempt.

Set the proposed production timeout and retry policy from evidence. Keep the deployed policy at one
attempt until A9/A10 explicitly implement an authorized application retry.

Measured result:

| Measurement | A8 result |
| --- | ---: |
| Queue and provisioning | 325.453 seconds |
| Container runtime | 261.075 seconds |
| Total Batch lifecycle | 586.528 seconds |
| Peak allocated/reserved GPU memory | 3,011,198,976 / 3,344,957,440 bytes |
| Peak system RAM | 3,279,441,920 bytes |
| GCS input payload | 610,256 bytes across five images and one manifest |
| GCS output payload | 1,626,102 bytes across 62 result/artifact objects |
| EC2 instance lifetime used for estimate | 622 seconds |
| Deterministic AWS runtime estimate | `$0.101286` before delayed logs, transfer, credits, taxes, and provider posting |

The estimate uses the previously verified Stockholm rates of `$0.558` per On-Demand
`g4dn.xlarge` hour, `$0.0836` per `gp3` GB-month for the 200 GB delete-on-termination volume, and
`$0.005` per in-use public IPv4 hour. The measured lifecycle fits comfortably within the existing
90-minute timeout. The retry count remains one.

Evidence:

```text
aws-acceptance/evidence/a8-five-image-resume/
```

The consolidated status is recorded in
[the A8 final summary](../../aws-acceptance/evidence/a8-five-image-resume/a8-final-summary.json);
the measured live run and completed replay are recorded in
[the five-image outcome](../../aws-acceptance/evidence/a8-five-image-resume/a8_five_20260726t110131z-outcome.json)
and
[the completed-replay outcome](../../aws-acceptance/evidence/a8-five-image-resume/a8_five_20260726t110131z-completed-replay.json).

Emergency stop and cleanup are identical to A7.

Acceptance:

- [x] Five items complete in one process with one initialization per stage.
- [ ] Interruption preserves immutable compatible item markers. Deferred until retries are in scope.
- [ ] One explicitly authorized follow-up attempt skips compatible items. Deferred until retries are
  in scope.
- [x] Completed CPU replay performs zero model-stage calls and changes no outputs.
- [x] Runtime, bytes, deterministic cost, and cleanup evidence for the completed scope is complete.
- [x] Submission is false, the queue is disabled, desired vCPUs are zero, active Batch jobs/EC2/EBS
  are zero, and Terraform reports no changes.

Dependencies for the completed scope: A7 and one explicitly authorized live A8 attempt.
Dependencies before enabling retries: controlled interruption, explicit attempt 2, immutable-marker
comparison, and a fresh live-cost authorization.

### A9. Complete the AWS provider status and cancellation API

Status (2026-07-26): **Complete.** The existing Lambda was updated in place with no new AWS
service. Status now describes only the provider job reserved in DynamoDB, validates its immutable
identity, and returns normalized provider timestamps. Cancellation checks provider state first and
does not call `TerminateJob` for a terminal job.

Purpose: finish and prove the AWS-side provider control contract before connecting it to FastAPI,
PostgreSQL, or durable GCP orchestration.

Scope boundary: A9 changes only the AWS submission service and its tests/evidence in this
repository. It does not implement the GCP reconciler, public FastAPI cancellation workflow,
PostgreSQL transitions, Cloud Run orchestrator, or live application end-to-end tests; those belong
to A10.

Initial production choice: do not deploy a second callback Lambda or EventBridge callback target.
The existing submission Lambda owns authenticated submit, status, and cancellation routes. GCP
polling in A10 will be the correctness path; EventBridge may be evaluated later only for freshness.

#### A9.1 Finish normalized status

The submission Lambda `GET` route:

1. authorizes the exact Google subject;
2. loads the matching DynamoDB record;
3. calls `DescribeJobs` only for the stored provider job ID;
4. validates job name/queue/definition/tags still match the reservation;
5. maps AWS states using Section 15;
6. returns normalized timestamps, attempt, sanitized reason code, and provider identity; and
7. never returns environment variables, role ARNs not already part of the provider identity,
   container reason strings, credentials, or raw AWS exceptions.

Unknown application job -> stable `404`. Missing provider job for a submitted record ->
`AWS_GPU_PROVIDER_STATE_MISSING`, retryable only while within the reconciliation window.

#### A9.2 Finish idempotent provider cancellation

AWS cancellation route:

1. requires the exact Google subject and known ledger record;
2. treats already-terminal/cancelled requests idempotently;
3. calls `TerminateJob` with a fixed sanitized reason for `STARTING`/`RUNNING` or the provider
   cancellation behavior appropriate to queued states;
4. conditionally records cancellation request/result in DynamoDB; and
5. returns normalized provider state rather than claiming immediate application cancellation;
6. never deletes DynamoDB reservations or immutable GCS evidence.

The A9 live check may query the already-terminal A8 provider job through `GET` and may call
cancellation repeatedly for that terminal record to prove idempotency without starting a GPU.
Queued/running cancellation belongs to the A10 application acceptance because it requires a fresh
application job, durable cancellation intent, reconciliation, and explicit live-cost authorization.

#### A9.3 Prove the provider contract hermetically

Add or complete tests for:

| Case | Expected AWS provider response |
| --- | --- |
| Exact authorized Google subject | Request reaches the route handler. |
| Wrong/missing subject | Stable authorization failure with no ledger or Batch mutation. |
| Unknown application job | Stable `404`. |
| Stored provider job missing | `AWS_GPU_PROVIDER_STATE_MISSING` with bounded retryability. |
| Queue/definition/name/tag mismatch | Stable integrity failure; never adopt unrelated work. |
| Every AWS Batch state | Stable provider-neutral state and timestamps. |
| Raw AWS exception/container reason | Sanitized stable error; raw value not returned. |
| Cancel terminal job repeatedly | Same normalized terminal result and no extra mutation. |
| Cancel queued/running fixture | One sanitized `TerminateJob` call and conditional ledger update. |
| Duplicate/out-of-order state fixture | Mapping remains deterministic and cannot regress state. |

Pure EventBridge payload mapping tests are allowed if they reuse the normalized state mapper, but
do not create a deployed callback, deduplication store, dead-letter queue, or second Lambda in A9.

#### A9.4 Verify the deployed AWS boundary without a GPU

With submission false and the Batch queue disabled:

1. authenticate as the exact GCP orchestrator identity;
2. query the completed A8 application job through the real `GET` route;
3. require its stored provider identity and normalized terminal state;
4. replay terminal cancellation and require an idempotent terminal response;
5. run negative authentication, unknown-job, and request-validation probes;
6. prove no new Batch job or DynamoDB reservation was created; and
7. prove desired vCPUs, EC2, EBS, and active Batch jobs remain zero.

Evidence:

```text
aws-acceptance/evidence/a9-provider-control-api/
```

The consolidated implementation, deployed probes, and zero-resource proof are recorded in
[the A9 final summary](../../aws-acceptance/evidence/a9-provider-control-api/a9-final-summary.json).

Deployed result: authenticated `GET` for completed A8 job
`aws_a8_five_20260726t110131z` returned the exact provider job
`81abcf51-00fe-477b-9e25-a6bfe45c19f1`, status `SUCCEEDED`, and submitted/started/stopped
timestamps. Two terminal-cancellation requests returned the same terminal provider identity and
left the DynamoDB state unchanged; CloudTrail contained no A9 `TerminateJob` event. Unknown-job,
invalid-request, and unauthenticated probes returned stable `404`, `400`, and `401` responses
without creating a reservation.

Rollback:

- Keep AWS submission disabled and the Batch queue disabled.
- Roll the Lambda code/configuration back through Terraform while preserving the DynamoDB ledger.
- Never delete a reservation to repair a status or cancellation mismatch.

Acceptance:

- [x] Status/cancel routes are exact-subject authenticated and stable.
- [x] Status is loaded only for the provider identity reserved in DynamoDB and resource identity is
  revalidated.
- [x] Cancellation is idempotent and invokes only the reserved Batch job.
- [x] Responses expose no credentials, environment, raw AWS exception, or unsanitized container
  reason.
- [x] The completed A8 job passes the deployed status/terminal-cancel probes with no new GPU job.
- [x] No second callback Lambda is deployed for the initial production design.
- [x] Submission and queue remain disabled, desired vCPUs remain zero, and no EC2/EBS resource is
  created.

Dependencies: A3 and the completed A7/A8 provider records. Estimated scope: one focused AWS
implementation and verification session; no backend repository session and no GPU run expected.

### A10. Integrate FastAPI and perform staged rollout

Purpose: connect the already-proven AWS execution provider to the authoritative GCP application
without exposing AWS to users.

All A10 application files live in `BACKEND_REPO`, not this worker repository.

#### A10.1 Add the provider-neutral database migration

Adapt the logical schema in the FastAPI plan to existing ORM/migration conventions. Minimum
application-job fields:

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

Constraints:

- provider job ID unique when non-null;
- manifest generation required once orchestration begins;
- provider attempt positive;
- terminal state requires completion time;
- conditional/locked transitions prevent state regression; and
- no AWS/DynamoDB state becomes the application source of truth.

Deploy migration before code that writes the fields. Backward-compatible reads tolerate null
provider fields for old jobs. Record tested downgrade behavior; do not downgrade after production
rows require new fields unless data is safely preserved.

#### A10.2 Implement public job endpoints

Use the public API/error/authorization design from the FastAPI document, with the initial maximum
changed to five:

```text
POST /v1/watermark-jobs
GET  /v1/watermark-jobs/{jobId}
GET  /v1/watermark-jobs
GET  /v1/watermark-jobs/{jobId}/items
POST /v1/watermark-jobs/{jobId}/cancellations
```

Requirements:

- authenticate and tenant-authorize every asset/job/result;
- client supplies asset IDs, not GCS/AWS paths;
- reject >5 items and tenant concurrency above the configured limit;
- server selects `watermark-v1`;
- public responses contain no AWS IDs/ARNs, API hostname, GCS unrestricted paths, JWT data, or
  infrastructure errors;
- client idempotency returns the same application job or a `409` conflict; and
- cancellation records intent before external calls.

#### A10.3 Create immutable manifest and durable intent

Inside one application transaction/outbox workflow:

1. create the application job/items;
2. resolve authorized source objects and exact generations/checksums;
3. construct the schema-version-1 manifest;
4. upload with create-only semantics;
5. capture and persist the manifest generation;
6. compute/persist the A2 submission hash; and
7. enqueue durable `AWS_GPU_SUBMISSION_REQUESTED`.

If object upload succeeds but database commit fails, the deterministic orphan prefix is cleaned by
an operator/reaper after an age threshold. If database commit succeeds but dispatch fails, the
outbox/stale-queued scanner retries the same immutable intent.

#### A10.4 Build the short Cloud Run orchestrator

Deploy a separate CPU-only Cloud Run Job `watermark-aws-orchestrator` under its dedicated service
account. It does not run inference and has no PostgreSQL credentials.

Invocation input:

```text
APP_JOB_ID
MANIFEST_URI
MANIFEST_GENERATION
OUTPUT_MANIFEST_URI
PIPELINE_VERSION
REQUEST_SHA256
```

Deployment configuration:

```text
AWS_GPU_API_BASE_URL
AWS_GPU_API_AUDIENCE=urn:watermark:aws-gpu-submission:staging
HTTP_CONNECT_TIMEOUT_SECONDS
HTTP_READ_TIMEOUT_SECONDS
MAX_SUBMISSION_ATTEMPTS
```

Behavior:

1. validate the small invocation contract;
2. obtain a Google-signed service-account ID token from the Cloud Run metadata/auth library for the
   exact audience;
3. call idempotent AWS `PUT`;
4. parse the typed response/error;
5. report provider identity through the existing durable application event path;
6. on an uncertain HTTP result, retry the same immutable request; and
7. exit after submission/retrieval, never wait for GPU execution.

No AWS key, Google key, arbitrary image/queue/role/command, or customer token is present.

#### A10.5 Implement GCP reconciliation and application cancellation

Create or extend:

```text
BACKEND_REPO/app/.../watermark/reconciler.py
BACKEND_REPO/app/.../watermark/provider/aws_batch.py
BACKEND_REPO/tests/.../test_watermark_reconciliation.py
```

For each non-terminal application job due for reconciliation:

1. acquire the existing row/version lock;
2. call the A9 status route with a freshly minted audience-bound Google ID token;
3. reject a response whose application job/provider identity differs from PostgreSQL;
4. apply only allowed monotonic provider/application transitions;
5. update `lastReconciledAt` and a bounded next-check time;
6. on AWS `SUCCEEDED`, validate the exact GCS output manifest and job completion marker before
   setting application `SUCCEEDED`;
7. on missing/incompatible output, set stable application failure after a bounded consistency
   window; and
8. retry transport/throttling failures with jitter without changing application state.

Suggested polling cadence:

```text
QUEUED/PROVISIONING: 30-60 seconds
RUNNING:             60 seconds
CANCEL_REQUESTED:    15-30 seconds
stale/backoff:       bounded exponential, maximum 5 minutes
```

Use the existing durable worker/Cloud Run Job/Scheduler pattern in GCP; never run reconciliation in
FastAPI `BackgroundTasks`.

Application cancellation:

1. FastAPI authenticates and authorizes the user/job;
2. PostgreSQL records `CANCEL_REQUESTED` before any external call;
3. durable orchestration calls the A9 cancellation route;
4. reconciliation waits until the reserved provider job stops;
5. PostgreSQL records `CANCELLED`; and
6. immutable completed item markers remain internal evidence and are not exposed as a successful
   application job.

Hermetic backend tests must prove that duplicate/out-of-order provider states cannot regress
PostgreSQL, AWS `SUCCEEDED` without compatible GCS completion never becomes application success,
repeated cancellation is idempotent, and transport failures leave durable intent retryable.

#### A10.6 Enable staged traffic

Configuration:

```text
WATERMARK_FEATURE_ENABLED=false initially
WATERMARK_COMPUTE_PROVIDER=AWS_BATCH
WATERMARK_MAX_ITEMS_PER_JOB=5
WATERMARK_MAX_CONCURRENT_JOBS_PER_TENANT=1 initially
WATERMARK_PIPELINE_VERSION=watermark-v1
```

Rollout:

1. deploy migration;
2. deploy backend code with feature disabled;
3. deploy orchestrator/reconciler;
4. enable AWS submission;
5. enable feature for the owner/test tenant only;
6. run one-image end-to-end;
7. review GCP/AWS logs, PostgreSQL, GCS generations, WIF, cost, and cleanup;
8. run five-image end-to-end;
9. test lost-response replay, wrong tenant, missing output, and reconciliation;
10. test cancellation once while queued and once while running, preserving immutable markers and
    recording the separately authorized GPU cost; and
11. disable submission again until external rollout is explicitly approved.

The `$60` global AWS budget and email alerts remain active. Add operational dashboards/alarms for:

- API auth failures, throttles, Lambda errors;
- jobs queued/running/failed and provisioning age;
- worker/model/output failures;
- reconciliation age;
- EC2 capacity non-zero without a known job; and
- Batch terminal job without compatible GCS completion.

#### A10.7 End-to-end evidence

```text
Client
  -> FastAPI/PostgreSQL
  -> generation-pinned GCS manifest
  -> durable Cloud Run orchestrator
  -> Google JWT
  -> AWS API/Lambda/DynamoDB
  -> one Batch T4 job
  -> WIF-authenticated GCS outputs
  -> GCP reconciliation/output validation
  -> authorized public status/result
```

Store redacted evidence under:

```text
aws-acceptance/evidence/a10-end-to-end/
```

Rollback:

1. disable the public feature flag;
2. disable AWS submission;
3. stop new orchestrator dispatch while leaving reconciliation/status available;
4. cancel or allow known jobs to finish according to application state;
5. roll backend/orchestrator code back;
6. retain the additive database fields and evidence unless a reviewed downgrade is safe; and
7. verify AWS compute returns to zero.

Acceptance:

- [ ] PostgreSQL stores provider-neutral AWS identity and manifest generation.
- [ ] Public endpoints remain provider-agnostic and tenant-authorized.
- [ ] Durable orchestration, reconciliation, cancellation, and output validation work.
- [ ] Duplicate/out-of-order provider state cannot regress PostgreSQL, and AWS success without
  compatible GCS completion never becomes application success.
- [ ] Queued and running application cancellation are proven through durable intent and
  reconciliation.
- [ ] One-image and five-image owner-tenant end-to-end staging flows pass.
- [ ] Feature/concurrency/item limits, budget, dashboards, alarms, and cleanup checks are active.
- [ ] No external user is enabled without a separate rollout approval.

Dependencies: A8 and A9. Estimated scope: four to six backend sessions, two monitored happy-path
staging runs, and separately authorized queued/running cancellation checks. A queued cancellation
may avoid GPU runtime; a running cancellation intentionally incurs one bounded GPU attempt.

### A11. Evaluate S3 model replicas and Spot

Purpose: make two optional cost/performance decisions from measured production-like evidence. A11
may conclude “do nothing.”

#### A11.1 Decide whether S3 model replicas are justified

From A7/A8/A10, calculate:

- total model bytes downloaded from GCS;
- model-download duration and percentage of total startup/runtime;
- GCP egress, AWS ingress/NAT/public-path cost;
- job frequency and repeated monthly transfer; and
- operational cost of replication/versioning.

Default decision: keep direct GCS unless model transfer is a material share of startup or monthly
cost. If testing S3:

1. create a private versioned model bucket/repository path;
2. replicate only immutable model objects;
3. bind every object by bucket, key, S3 version ID, size, and SHA-256 matching the authoritative
   GCS model manifest;
4. grant only the Batch job role read access to those exact model prefixes;
5. reject mutable aliases such as `latest`;
6. run output/identity equivalence tests; and
7. keep customer manifests, source images, outputs, and markers in GCS.

Rollback by selecting direct GCS in deployment config; retain or lifecycle S3 replicas only after
confirming no active definition references them.

#### A11.2 Decide whether Spot is economically safe

Do not replace the On-Demand environment. Create a separate, bounded Spot compute environment only
for evaluation:

```text
min=0
desired=0
max=4
g4dn.xlarge only
one attempt initially
same immutable image/job role/network
On-Demand queue/environment retained as fallback
```

Use A8 interruption evidence to calculate:

- expected Spot saving;
- interruption rate;
- completed-item reuse;
- duplicated model/startup/runtime cost;
- queue delay; and
- probability/cost of exhausting the product time limit.

Enable Spot for any production traffic only when an owner-approved threshold shows a meaningful net
saving after retries and acceptable completion latency. Never let the caller select Spot.

#### A11.3 Publish the decision

Evidence:

```text
aws-acceptance/evidence/a11-optimizations/
  gcs-baseline.json
  s3-comparison.json          # only if tested
  spot-comparison.json        # only if tested
  decision.md
```

Acceptance:

- [ ] Direct-GCS measurements produce an explicit keep/replicate decision.
- [ ] Any S3 test is immutable, checksum-equivalent, least-privilege, and reversible.
- [ ] Spot is either rejected/deferred or independently proven with On-Demand retained.
- [ ] No optional resource remains running after an inconclusive/rejected experiment.

Dependencies: A10 and fresh cost authorization for every live experiment. Estimated scope:
one analysis session; additional monitored sessions only if experiments are justified.

## 23. Boundaries

### Always

- Pin manifests, inputs, models, job definitions, and images immutably.
- Validate at GCP/AWS boundaries.
- Use short-lived workload identity.
- Require native JWT authentication plus exact-subject authorization on every AWS internal route.
- Preserve GCS conditional writes and completion-marker validation.
- Start with On-Demand and one attempt for acceptance.
- Record cost and cleanup for every live GPU test.

### Ask first

- Increasing maximum vCPUs, GPU count, instance size, attempts, timeout, or batch size.
- Adding Spot, S3 customer outputs, private cross-cloud connectivity, a NAT gateway, or changing
  away from the accepted Terraform/GCS-state approach.
- Changing PostgreSQL schema or public API behavior.
- Expanding IAM principals, prefixes, or AWS regions.

### Never

- Commit or mount AWS access keys or Google service-account keys.
- Expose the AWS submission API to frontend clients or accept an unverified JWT payload.
- Accept an arbitrary image, command, queue, role, bucket, or instance type from FastAPI clients.
- Mark application success from AWS status alone.
- Reuse output with mismatched manifest/source/config/model/image identity.
- Treat the deterministic AWS job name as the sole idempotency mechanism.
- Describe setup smoke results as formal visual-quality approval.

## 24. Completion criteria

The AWS GPU foundation is complete when:

- The hybrid decision and provider contracts are accepted.
- Keyless GCP-to-AWS and AWS-to-GCP authentication works.
- API Gateway native JWT validation and the single submission Lambda accept only the exact approved
  GCP service identity; no separate Lambda authorizer is required.
- Exactly one logical AWS submission exists for a replayed application request.
- A digest-pinned ECR image runs non-root on an On-Demand `g4dn.xlarge`.
- One-image and five-image manifests complete with compatible GCS markers.
- CUDA/T4 and all enabled production models are proven on AWS hardware.
- Interruption/resume and completed replay are proven with production stages.
- Callbacks may be lost without losing correctness.
- Cancellation and cleanup are proven.
- FastAPI validates output before success and never exposes AWS infrastructure details publicly.
- Runtime, transfer, resource, and cost evidence supports the selected design.
- Quotas, retention, dashboards, alerts, and budget controls are active.

## 25. Open owner decisions

- Live `g4dn.xlarge` launch/capacity result in the selected `eu-north-1` Availability Zones.
- Whether a later measured production migration from Stockholm to a North American AWS region is
  justified by GCS latency, transfer cost, or data-location requirements.
- Name of the pre-created, versioned GCS Terraform-state bucket.
- Exact numeric subject of the new `watermark-aws-orchestrator` service account after creation.
- Exact approved GCS bucket/root-prefix bindings after inspecting the current bucket layout.
- Whether production CloudWatch retention remains 30 days or increases after staging evidence.
- Artifact/model-cache retention after measured disk/startup behavior.
- Application outcome policy for mixed succeeded/blocked/failed items.
- Cancellation promise exposed to users.
- Maximum acceptable queue delay and per-job cost. Monthly AWS budget is currently `$60`.
- Production timeout and retry count after A7/A8 measurements; acceptance remains 90 minutes and one
  attempt.
- Whether S3 model replication is required after Phase A measurement.
- Whether EventBridge callbacks add enough freshness to justify a second callback path; polling is
  the accepted initial correctness path.
- External-user rollout approval after owner-only A10 staging evidence.

## 26. Official references

- [AWS Batch GPU jobs](https://docs.aws.amazon.com/batch/latest/userguide/gpu-jobs.html)
- [AWS Batch GPU workload AMI](https://docs.aws.amazon.com/batch/latest/userguide/batch-gpu-ami.html)
- [AWS Batch compute resource AMIs](https://docs.aws.amazon.com/batch/latest/userguide/compute_resource_AMIs.html)
- [AWS Batch service-linked roles](https://docs.aws.amazon.com/batch/latest/userguide/using-service-linked-roles.html)
- [IAM roles for ECS tasks](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-iam-roles.html)
- [AWS Batch SubmitJob API](https://docs.aws.amazon.com/batch/latest/APIReference/API_SubmitJob.html)
- [AWS Batch job states](https://docs.aws.amazon.com/batch/latest/userguide/job_states.html)
- [AWS Batch automated retries](https://docs.aws.amazon.com/batch/latest/userguide/job_retries.html)
- [AWS Batch job timeouts](https://docs.aws.amazon.com/batch/latest/userguide/job_timeouts.html)
- [AWS Batch TerminateJob API](https://docs.aws.amazon.com/batch/latest/APIReference/API_TerminateJob.html)
- [AWS Batch events in EventBridge](https://docs.aws.amazon.com/batch/latest/userguide/cloudwatch_event_stream.html)
- [DynamoDB conditional expressions](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/Expressions.ConditionExpressions.html)
- [API Gateway JWT authorizers](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-jwt-authorizer.html)
- [API Gateway HTTP API throttling](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-throttling.html)
- [API Gateway HTTP API access logs](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-logging-variables.html)
- [AWS OIDC federation](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_providers_oidc.html)
- [Google service-account ID tokens](https://docs.cloud.google.com/docs/authentication/token-types)
- [Google Workload Identity Federation](https://docs.cloud.google.com/iam/docs/workload-identity-federation)
- [Configure Google WIF for AWS workloads](https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-other-clouds)
- [Google auth custom AWS credential supplier](https://docs.cloud.google.com/iam/docs/authenticate-with-auth-libraries#aws)
- [Google WIF security best practices](https://docs.cloud.google.com/iam/docs/best-practices-for-using-workload-identity-federation)
- [Terraform GCS backend and state locking](https://developer.hashicorp.com/terraform/language/backend/gcs)
- [Terraform dependency lock file](https://developer.hashicorp.com/terraform/language/files/dependency-lock)
- [AWS Lambda Python runtimes](https://docs.aws.amazon.com/lambda/latest/dg/lambda-python.html)
- [AWS Batch managed compute environments](https://docs.aws.amazon.com/batch/latest/userguide/managed_compute_environments.html)
- [Amazon VPC internet gateways](https://docs.aws.amazon.com/vpc/latest/userguide/VPC_Internet_Gateway.html)
