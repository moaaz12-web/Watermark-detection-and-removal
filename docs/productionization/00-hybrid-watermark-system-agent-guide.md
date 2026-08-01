# Hybrid Watermark System Agent Guide

Status: Authoritative orientation and task-routing guide for agents working across the watermark
system.

Last verified: 2026-07-30

This document explains what exists, where it lives, how the pieces communicate, how to inspect
them safely, and which deeper runbook to use for each operation. It is deliberately broader than
an implementation plan. Future agents should use it as their first map of the system.

## 1. Start here

The production watermark feature is a hybrid GCP and AWS system:

- GCP owns the application, users, authorization, PostgreSQL state, input preparation, customer
  data, model artifacts, results, and reconciliation.
- AWS is a narrow GPU execution provider. It authenticates one GCP service identity, creates an
  idempotent AWS Batch job, starts an ephemeral GPU EC2 instance when work exists, and returns to
  zero GPU capacity after the work becomes idle.
- Users and the frontend call only FastAPI in GCP. They never call AWS directly.
- The GPU job reads and writes GCS through short-lived Google credentials obtained with Workload
  Identity Federation. No service-account key file is used.

Before changing anything:

1. Read [Section 2](#2-current-state-and-freshness-rules).
2. Find the task in [Section 17](#17-task-routing-index).
3. Read the linked source file and runbook.
4. Discover the live state with the narrow read-only commands in this guide.
5. Explain the intended change and why it is needed.
6. Use the repository's existing Terraform or deployment workflow. Do not improvise a parallel
   infrastructure path.

This guide contains no passwords, private keys, access tokens, or database credentials. Do not add
any.

## 2. Current state and freshness rules

### 2.1 Verified current deployment

The following was verified read-only on 2026-07-30:

| Boundary | Verified state |
| --- | --- |
| GCP project | `project-5909a738-2fdc-451e-ad6` (`834057675927`) |
| GCP application region | `us-central1` |
| AWS account | `154959837939` |
| Current AWS GPU region | `eu-north-1` (Stockholm) |
| FastAPI service | `infringementdetection`, ready in `us-central1` |
| FastAPI watermark gates | Asset upload, small-job, and bulk-job flags enabled |
| Watermark Cloud Run jobs | `watermark-control-plane`, `watermark-bulk-preparer`, and `watermark-aws-orchestrator`, all present |
| Control-plane scheduler | `watermark-control-plane-every-minute`, enabled, `* * * * *`, UTC |
| AWS submission gate | Enabled in `infra/aws/environments/staging.tfvars` |
| AWS Batch queue and compute environment | Enabled and valid |
| AWS GPU floor | `min_vcpus=0`, `desired_vcpus=0` |
| AWS GPU ceiling | `max_vcpus=4`, enough for one `g4dn.xlarge` |
| Active AWS GPU work at verification time | No GPU EC2 instance and no RUNNABLE, STARTING, or RUNNING Batch job |

An enabled Batch queue does not mean a GPU is running. A GPU EC2 instance is requested only when a
Batch job needs capacity. AWS Batch manages eventual scale-down after the worker stops.

### 2.2 Current versus proposed region

The live AWS deployment is still in Stockholm. The proposed move to `us-east-2` (Ohio) is documented
in [10-aws-gpu-regional-migration-plan.md](10-aws-gpu-regional-migration-plan.md), but it has not
been executed.

Do not:

- treat Ohio as the current production region;
- edit the current Terraform state in place merely by changing `aws_region`;
- assume ECR images, DynamoDB records, API endpoints, Batch resources, logs, or quotas move between
  regions; or
- decommission Stockholm before an isolated Ohio deployment passes acceptance.

### 2.3 Freshness rule

Resource state, revisions, image digests, URLs, job executions, and feature flags change. Values in
this guide are orientation, not permission to skip discovery.

Before an operational or deployment action, verify:

```bash
rtk proxy gcloud config get-value project
rtk proxy aws sts get-caller-identity --query '{Account:Account,Arn:Arn}' --output table
```

Then use the task-specific discovery commands below. Never infer the active account or region from
the shell prompt.

## 3. Repository map and ownership

### 3.1 GPU worker and AWS infrastructure

Repository:

`/home/maaz/Desktop/watermarkPoc`

This repository owns:

- the provider-neutral worker and its CLI;
- GPU runtime assembly and model loading;
- input/output contracts and deterministic GCS behavior;
- the AWS internal submission Lambda;
- AWS Terraform for API Gateway, DynamoDB, Batch, EC2 capacity, ECR, CodeBuild, IAM, networking,
  logs, alarms, and GCP Workload Identity Federation;
- worker tests and AWS submission tests; and
- productionization plans and operator runbooks.

Important paths:

| Path | Purpose |
| --- | --- |
| `src/watermark_worker/` | Worker CLI, manifests, storage, model registry, execution, progress, results, and runtime assembly |
| `src/watermark_worker/gpu_runtime/` | GPU implementations for background removal, detection, segmentation/inpainting support, and provider assembly |
| `configs/watermark-v1.yaml` | Small-job pipeline, maximum five items |
| `configs/watermark-bulk-v1.yaml` | Bulk pipeline, maximum 10,000 items |
| `model-manifest.json` | Generation-pinned, checksum-pinned GCS model artifacts |
| `contracts/aws-gpu-v1/` | Canonical submit, status, cancellation, and error fixtures shared across the cloud boundary |
| `services/aws_submission/` | API Gateway Lambda handler, authorization, DynamoDB ledger, Batch adapter, and tests |
| `infra/aws/` | Terraform and remote CodeBuild helper |
| `infra/aws/environments/staging.tfvars` | Current staging input values and operational gates; this is Terraform data, not a shell script |
| `tests/` | Worker, contract, storage, runtime, resumability, and integration tests |
| `docs/productionization/` | Architecture, implementation history, runbooks, migration plan, and measurements |

### 3.2 FastAPI backend and GCP control plane

Repository:

`/home/maaz/Desktop/infringement_project/infringement_backend`

This repository owns:

- public user authentication and authorization;
- watermark assets and application job rows;
- store-scoped bulk job creation and selection;
- the durable database outbox;
- the one-pass control plane;
- Cloud Run job dispatch for preparation and AWS orchestration;
- the authenticated AWS provider client;
- provider reconciliation and GCS output validation;
- result download authorization;
- database migrations; and
- GCP service and Cloud Run Jobs operational documentation.

Important paths:

| Path | Purpose |
| --- | --- |
| `app/api/routes/watermark_assets.py` | Trusted image upload and asset registration |
| `app/api/routes/watermark_jobs.py` | Small manual watermark jobs |
| `app/api/routes/watermark_bulk_jobs.py` | Store-scoped bulk jobs |
| `app/models/watermark_job.py` | Jobs, items, frozen targets, assets, and durable intents |
| `app/schemas/watermark_jobs.py` | Public request and response schemas |
| `app/watermark/bulk_preparer.py` | Frozen target selection, source download, GCS staging, and manifest publication |
| `app/watermark/control_plane.py` | One bounded dispatch and reconciliation pass |
| `app/watermark/orchestrator.py` | One immutable GCP-to-AWS submission execution |
| `app/watermark/provider.py` | Typed AWS API client, request hash, token provider, and state mapping |
| `app/watermark/reconciler.py` | Provider polling and authoritative output acceptance |
| `app/watermark/output_validation.py` | GCS output-manifest and per-item validation |
| `app/jobs/dispatcher.py` | Cloud Run Jobs API launcher |
| `app/jobs/runner.py` | Cloud Run Job command entry point, including `watermark-bulk-prepare` |
| `alembic/versions/0047_add_watermark_application_jobs.py` | Initial watermark tables |
| `alembic/versions/0048_add_bulk_watermark_preparation.py` | Bulk preparation tables and fields |
| `tests/test_watermark_*.py` | Backend unit, API, preparation, provider, control-plane, and reconciliation tests |
| `docs/google_cloud_run_setup/06_cloud_run_jobs_ops_runbook.md` | Backend deploy, Cloud Run Job, log, Cloud SQL, and migration operations |
| `docs/postman_infringement_detection_collection.json` | Existing Postman collection, including watermark routes |

Read `/home/maaz/Desktop/infringement_project/infringement_backend/AGENTS.md` before editing this
repository.

### 3.3 Frontend

Repository:

`/home/maaz/Desktop/infringement_project/infringement_frontend`

The frontend owns the user-facing watermark screens, calls the FastAPI routes, and polls
application job state. It does not receive AWS credentials, call API Gateway, read DynamoDB, or
manage GPU instances.

Before frontend changes, inspect the existing bulk-scan UI, API-client, authentication, state,
loading, error, and styling patterns. Do not create a second unrelated interaction model.

## 4. Whole-system architecture

```text
Browser or API client
        |
        | Bearer-authenticated FastAPI request
        v
GCP Cloud Run service: infringementdetection
        |
        | PostgreSQL transaction:
        | job + frozen request + durable intent
        v
GCP Cloud SQL: infringement
        |
        | every minute
        v
GCP Cloud Scheduler
        |
        v
GCP Cloud Run Job: watermark-control-plane
        |
        +-- PREPARING intent --> Cloud Run Job: watermark-bulk-preparer
        |                         |- freezes selected Shopify media
        |                         |- downloads one source at a time
        |                         |- validates and uploads each to GCS
        |                         `- publishes immutable input manifest
        |
        +-- AWS submission intent --> Cloud Run Job: watermark-aws-orchestrator
        |                              |- obtains audience-bound Google ID token
        |                              `- PUTs immutable request to AWS
        |
        v
AWS API Gateway HTTP API
        |- validates Google JWT signature, issuer, audience, and standard claims
        v
AWS Lambda: watermark-staging-submission
        |- validates exact Google service-account subject
        |- validates URI prefixes, pipeline, hash, and request shape
        |- reserves applicationJobId in DynamoDB
        `- submits or adopts one AWS Batch job
        |
        v
AWS Batch queue and managed EC2 compute environment
        |- scales from zero
        |- provisions one On-Demand g4dn.xlarge when required
        |- pulls the digest-pinned ECR worker image
        `- starts one container with one NVIDIA T4
        |
        v
GPU worker
        |- obtains temporary GCP credentials through WIF
        |- loads pinned models from GCS once per Batch attempt
        |- reads manifest items sequentially
        |- writes each deterministic item result to GCS
        `- writes output manifest and completion marker
        |
        v
AWS Batch terminal state + GCS authoritative outputs
        |
        | later control-plane pass
        v
GCP reconciliation
        |- polls AWS status
        |- validates GCS generations, checksums, paths, and item results
        `- updates PostgreSQL to SUCCEEDED, FAILED, or CANCELLED
        |
        v
Frontend polls FastAPI and downloads authorized results from FastAPI
```

The control plane is a Cloud Run Job, not a continuously running server. Cloud Scheduler currently
starts one bounded pass every minute. Consequently, a newly created job normally waits for a
scheduler pass before preparation begins, and waits for another pass before AWS submission begins.

The control plane does not directly start or stop EC2. It creates Cloud Run Job executions, calls
the AWS provider through the orchestrator, and reconciles state. AWS Batch owns EC2 provisioning
and eventual scale-down.

## 5. Data and state authority

| Information | Authoritative system | Important rule |
| --- | --- | --- |
| Users, accounts, stores, authorization | FastAPI and PostgreSQL | AWS must never authorize end users |
| Application job and item status | PostgreSQL | AWS Batch status alone is not application success |
| Durable work requests | `watermark_job_intents` in PostgreSQL | Dispatch is retried from persisted intent |
| Shopify selection snapshot | `watermark_job_targets` | The preparer freezes the accepted selection |
| Input images | GCS data bucket | Worker reads immutable object generations |
| Input manifest | GCS data bucket | Path and generation are part of the signed request |
| Model artifacts | GCS model bucket | Manifest pins URI, generation, and SHA-256 |
| Item artifacts and final images | GCS data bucket | Deterministic, create-only or validate-existing writes |
| Job output manifest | GCS data bucket | GCP validates it before success |
| AWS idempotency reservation | DynamoDB | Provider ledger only, not the application database |
| AWS execution status | AWS Batch | Provider evidence consumed by GCP reconciliation |
| Container image | Private ECR | Batch pins an immutable digest |
| Runtime scratch data | Ephemeral EC2/EBS | Not durable application storage |
| Operational logs | Cloud Logging and CloudWatch | Diagnostics, not application state |

The normal object layout is:

```text
gs://project-5909a738-2fdc-451e-ad6-watermark-data/
  account_<account-id>/
    <application-job-id>/
      input-manifest.json
      output-manifest.json
      asset_<sequence>/
        ...
```

Trusted standalone asset uploads use an account-owned path below the same data bucket. Backend code
constructs object paths; clients provide file bytes or asset IDs, not arbitrary GCS destinations.

## 6. GCP service inventory

### 6.1 Project and region

| Setting | Value |
| --- | --- |
| Project ID | `project-5909a738-2fdc-451e-ad6` |
| Project number | `834057675927` |
| Runtime region | `us-central1` |

Set these explicitly in a working terminal:

```bash
export GCP_PROJECT_ID="project-5909a738-2fdc-451e-ad6"
export GCP_REGION="us-central1"
rtk proxy gcloud config set project "${GCP_PROJECT_ID}"
```

### 6.2 Cloud Run service

`infringementdetection` is the public FastAPI backend. Discover its active URL and revision instead
of copying an old hostname:

```bash
rtk proxy gcloud run services describe infringementdetection \
  --project "${GCP_PROJECT_ID}" \
  --region "${GCP_REGION}" \
  --format='table(metadata.name,status.url,status.latestReadyRevisionName)'
```

At the last verification the active URL was:

`https://infringementdetection-4ldyqude6a-uc.a.run.app`

Treat discovery as authoritative because Cloud Run may expose more than one valid URL form and old
runbooks can contain a stale hostname.

### 6.3 Cloud Run jobs

| Job | Role | Runtime identity |
| --- | --- | --- |
| `watermark-control-plane` | Executes one bounded intent-dispatch and reconciliation pass | `watermark-aws-orchestrator@project-5909a738-2fdc-451e-ad6.iam.gserviceaccount.com` |
| `watermark-bulk-preparer` | Freezes Shopify targets, stages images in GCS, and publishes the bulk manifest | `infringement@project-5909a738-2fdc-451e-ad6.iam.gserviceaccount.com` |
| `watermark-aws-orchestrator` | Obtains a Google ID token and submits one immutable request to AWS | `watermark-aws-orchestrator@project-5909a738-2fdc-451e-ad6.iam.gserviceaccount.com` |
| `shopify-sync-job` | Existing non-watermark Shopify synchronization | Discover before changing |
| `shopify-bulk-scan-job` | Existing non-watermark infringement bulk scan | Discover before changing |

List definitions without exposing environment values:

```bash
rtk proxy gcloud run jobs list \
  --project "${GCP_PROJECT_ID}" \
  --region "${GCP_REGION}" \
  --format='table(metadata.name,status.latestCreatedExecution.name)'
```

The three watermark jobs use backend code from the FastAPI repository. Deploying the API service
does not automatically update their pinned images. Follow the backend Cloud Run Jobs runbook when
repinning them.

### 6.4 Scheduler

`watermark-control-plane-every-minute` runs every minute in UTC and was enabled at the last
verification.

```bash
rtk proxy gcloud scheduler jobs list \
  --project "${GCP_PROJECT_ID}" \
  --location "${GCP_REGION}" \
  --format='table(name.basename(),state,schedule,timeZone)'
```

Pausing this scheduler stops new automatic control-plane passes. It does not cancel a Cloud Run
execution or AWS Batch job that already exists.

### 6.5 Cloud SQL

| Setting | Value |
| --- | --- |
| Instance | `infringement` |
| Engine | PostgreSQL 18 |
| Region | `us-central1` |
| Connection name | `project-5909a738-2fdc-451e-ad6:us-central1:infringement` |

Use Cloud SQL Auth Proxy and private credentials supplied outside agent context. The authoritative
connection and Alembic procedure is:

`/home/maaz/Desktop/infringement_project/infringement_backend/docs/google_cloud_run_setup/06_cloud_run_jobs_ops_runbook.md`

Do not dump connection strings, passwords, Cloud Run environment arrays, or shell history.

### 6.6 GCS

| Bucket | Purpose | Location |
| --- | --- | --- |
| `project-5909a738-2fdc-451e-ad6-watermark-data` | Inputs, manifests, item artifacts, results, and completion records | `US-CENTRAL1` |
| `project-5909a738-2fdc-451e-ad6-watermark-models` | Immutable model artifacts under `watermark-v1/` | `US-CENTRAL1` |
| `watermark-tfstate-834057675927` | Versioned Terraform remote state | `US-CENTRAL1` |

Safe discovery:

```bash
rtk proxy gcloud storage buckets list \
  --project "${GCP_PROJECT_ID}" \
  --format='table(name,location,storageClass)'
```

Inspect one known job prefix:

```bash
export ACCOUNT_ID="<numeric-account-id>"
export APP_JOB_ID="wm_<job-id>"

rtk proxy gcloud storage ls \
  "gs://project-5909a738-2fdc-451e-ad6-watermark-data/account_${ACCOUNT_ID}/${APP_JOB_ID}/**"
```

Do not recursively download an unknown tenant or bucket prefix.

### 6.7 GCP identities

| Service account | Purpose |
| --- | --- |
| `infringement@project-5909a738-2fdc-451e-ad6.iam.gserviceaccount.com` | FastAPI and application runtime identity |
| `watermark-aws-orchestrator@project-5909a738-2fdc-451e-ad6.iam.gserviceaccount.com` | Approved GCP caller of the AWS internal API |
| `watermark-gpu-worker@project-5909a738-2fdc-451e-ad6.iam.gserviceaccount.com` | GCS identity impersonated by the AWS GPU job through WIF |

The AWS API pins the orchestrator's numeric Google subject. The AWS GPU role can impersonate only
the worker identity allowed by the Workload Identity Federation binding.

## 7. AWS service inventory

### 7.1 Account, region, and capacity

| Setting | Value |
| --- | --- |
| Account | `154959837939` |
| Current region | `eu-north-1` |
| Environment | `staging` |
| GPU quota | 4 On-Demand G and VT vCPUs |
| Instance | `g4dn.xlarge`, 4 vCPU, 16 GiB RAM, one NVIDIA T4 |

Always set and verify the region:

```bash
export AWS_ACCOUNT_ID="154959837939"
export AWS_REGION="eu-north-1"

rtk proxy aws sts get-caller-identity \
  --query '{Account:Account,Arn:Arn}' \
  --output table
```

### 7.2 Submission boundary

| Resource | Name |
| --- | --- |
| API Gateway HTTP API | `watermark-staging-gpu-submission` |
| Stage | `staging` |
| Lambda | `watermark-staging-submission` |
| DynamoDB ledger | `watermark-staging-submission-ledger` |

Discover the API endpoint:

```bash
rtk proxy aws apigatewayv2 get-apis \
  --region "${AWS_REGION}" \
  --query 'Items[?Name==`watermark-staging-gpu-submission`].{id:ApiId,endpoint:ApiEndpoint}' \
  --output table
```

The deployed base URL is the returned API endpoint plus `/staging`. The endpoint is internal by
contract even though it is internet-routable. Only the approved GCP orchestrator identity should
call it.

Protected routes:

```text
PUT /internal/v1/gpu-jobs/{applicationJobId}
GET /internal/v1/gpu-jobs/{applicationJobId}
PUT /internal/v1/gpu-jobs/{applicationJobId}/cancellation
```

API Gateway validates the Google JWT. Lambda then validates the exact caller subject, request
schema, pipeline allowlist, GCS prefixes, canonical request hash, and job identity.

### 7.3 Why DynamoDB exists

DynamoDB is a small AWS-side idempotency and submission ledger keyed by `applicationJobId`.

It prevents duplicate Batch jobs when:

- GCP retries the same request;
- the HTTP response is lost after AWS accepted the request;
- Lambda crashes between reservation and submission; or
- reconciliation needs to adopt the previously created provider job.

It does not contain users, stores, source images, results, or authoritative application status.
PostgreSQL remains the application database.

### 7.4 Batch and ephemeral EC2

| Resource | Name or setting |
| --- | --- |
| Managed compute environment | `watermark-staging-gpu` |
| Job queue | `watermark-staging-gpu` |
| Small job definition | `watermark-staging-worker` |
| Bulk job definition | `watermark-staging-worker-bulk` |
| Compute type | On-Demand EC2 |
| Allocation strategy | `BEST_FIT` |
| Instance type | `g4dn.xlarge` only |
| Capacity | min 0, desired 0, max 4 vCPU |
| Root EBS | Encrypted 200 GiB gp3, deleted on termination |
| Small timeout/retries | 5,400 seconds, one attempt |
| Bulk timeout/retries | 86,400 seconds, two attempts for selected infrastructure/runtime exits |

Batch provisions EC2 only for queued work. The worker container requests 4 vCPU, 14,336 MiB memory,
and one GPU.

The Batch foundation uses dedicated public subnets, ephemeral public IPv4, an internet gateway, no
inbound security-group rules, and constrained outbound DNS/HTTPS. There is no always-billed NAT
gateway in this design.

### 7.5 ECR and CodeBuild

| Resource | Name |
| --- | --- |
| Worker image repository | `watermark-staging-worker` |
| Build cache repository | `watermark-staging-worker-build-cache` |
| CodeBuild project | `watermark-staging-worker-image-build` |
| CodeBuild source bucket | `watermark-staging-codebuild-source-154959837939` |

The normal worker-image workflow builds remotely in CodeBuild, exports reusable BuildKit cache to
ECR, pushes the final image, and returns an immutable digest. It avoids repeatedly building and
uploading a multi-gigabyte image from the laptop.

The currently pinned digest is recorded in `infra/aws/environments/staging.tfvars` and in active
Batch job-definition revisions. Discover rather than copying it from this guide.

### 7.6 IAM roles

| Role | Purpose |
| --- | --- |
| `watermark-staging-submission-lambda-role` | Lambda logs, DynamoDB ledger, fixed Batch submit/describe/terminate |
| `watermark-staging-batch-instance-role` | ECS container-instance permissions |
| `watermark-staging-batch-execution-role` | Pull ECR image and write worker logs |
| `watermark-staging-gpu-job-role` | Runtime AWS identity exchanged through GCP WIF |
| `watermark-staging-worker-image-build-role` | CodeBuild source, cache, image, and logs |

Do not move permissions between execution and job roles casually. The execution role serves the
container runtime. The GPU job role is the identity available to worker application code.

### 7.7 Logs and alarms

| Log group | Retention |
| --- | ---: |
| `/aws/apigateway/watermark-staging-gpu-submission` | 30 days |
| `/aws/lambda/watermark-staging-submission` | 30 days |
| `/aws/batch/watermark-staging-worker` | 30 days |
| `/aws/codebuild/watermark-staging-worker-image-build` | 14 days |

Configured Lambda alarms:

- `watermark-staging-submission-errors`
- `watermark-staging-submission-throttles`

Both were `OK` at the last verification.

### 7.8 Budget

`watermark-aws-monthly` is an account-wide, unfiltered monthly cost budget:

- limit: USD 60;
- actual-spend notifications: USD 5, 15, and 25; and
- forecasted-spend notification: USD 25.

It has notifications, not automated shutdown actions. A budget alert does not stop a GPU.

## 8. Authentication and connection paths

### 8.1 User to FastAPI

The browser or API client authenticates through the FastAPI auth routes and uses the returned bearer
access token for watermark endpoints.

Use the safer login and error-handling commands in:

[07-a10-controlled-e2e-operator-runbook.md](07-a10-controlled-e2e-operator-runbook.md)

Avoid snippets that execute `exit 1` directly in an interactive shell. A failed validation can
close the user's terminal. Prefer an `if` block that prints the response and leaves the shell open.

### 8.2 GCP control plane to Cloud Run jobs

The control-plane runtime uses Application Default Credentials from its Cloud Run service account
and calls the Cloud Run Jobs v2 API. Per-execution arguments or immutable environment overrides
identify the preparation or orchestration request.

The FastAPI HTTP request does not synchronously run the GPU pipeline.

### 8.3 GCP orchestrator to AWS

The `watermark-aws-orchestrator` job:

1. obtains a Google-signed ID token;
2. sets the configured audience `urn:watermark:aws-gpu-submission:staging`;
3. calls the AWS API over HTTPS; and
4. uses the same immutable request on retries.

API Gateway checks Google JWT standard claims. Lambda checks the exact approved subject and the
job-specific contract.

No long-lived AWS access key is stored in GCP for this path.

### 8.4 AWS worker to GCS

The Batch container receives temporary credentials from its AWS job role. The worker's WIF adapter:

1. gets the AWS role identity;
2. exchanges it through the GCP Workload Identity Federation provider;
3. impersonates `watermark-gpu-worker@...`; and
4. receives short-lived Google access tokens.

Those tokens are limited by both the WIF trust binding and conditional GCS IAM grants. No Google
service-account key file is baked into ECR.

## 9. Public FastAPI watermark API

All paths below are under `/api/v1` and require the normal application bearer token.

### 9.1 Trusted asset and small-job API

| Method and path | Purpose |
| --- | --- |
| `POST /watermark-assets` | Validate JPEG/PNG/WebP bytes, write a trusted account-owned GCS object, and register immutable metadata |
| `POST /watermark-jobs` | Create a small job from trusted asset IDs, maximum five |
| `GET /watermark-jobs` | List the caller's application jobs |
| `GET /watermark-jobs/{job_id}` | Get application status |
| `GET /watermark-jobs/{job_id}/items` | List per-item status |
| `GET /watermark-jobs/{job_id}/items/{item_id}/result` | Download an authorized, generation-pinned final image through FastAPI |
| `POST /watermark-jobs/{job_id}/cancellations` | Request idempotent cancellation |

The asset upload endpoint never starts GPU work.

### 9.2 Store-scoped bulk API

| Method and path | Purpose |
| --- | --- |
| `POST /stores/{store_id}/watermark-scan-jobs/estimate` | Resolve and report the requested Shopify scope without creating GPU work |
| `POST /stores/{store_id}/watermark-scan-jobs` | Create a PREPARING bulk job and durable preparation intent |
| `GET /stores/{store_id}/watermark-scan-jobs/{job_id}` | Get job and preparation counters |
| `GET /stores/{store_id}/watermark-scan-jobs/{job_id}/items` | Page through frozen targets and reconciled item status |
| `POST /stores/{store_id}/watermark-scan-jobs/{job_id}/cancellations` | Cancel locally before provider submission or request provider cancellation |

Use an `Idempotency-Key` header on create. Reusing the same key with the same request returns the
existing application job. Reusing it with different content returns a conflict.

Only one active watermark job per store is allowed by both application logic and a database
constraint.

## 10. Job lifecycle

Application statuses:

```text
PREPARING
  -> QUEUED
  -> ORCHESTRATING
  -> PROVISIONING
  -> RUNNING
  -> AWAITING_OUTPUT
  -> SUCCEEDED

Any active state may reach FAILED or CANCEL_REQUESTED/CANCELLED according to the contract.
```

Meaning:

| Status | Meaning |
| --- | --- |
| `PREPARING` | GCP is freezing, downloading, validating, and staging the selected inputs |
| `QUEUED` | Immutable GCS manifest exists and a durable AWS submission intent is pending |
| `ORCHESTRATING` | GCP launched the AWS submission job but may not yet have an AWS provider ID |
| `PROVISIONING` | AWS accepted the request and Batch is acquiring capacity |
| `RUNNING` | Worker container is running |
| `AWAITING_OUTPUT` | Provider is terminal but GCP still needs authoritative GCS output validation |
| `SUCCEEDED` | Provider succeeded and GCP accepted all required output evidence |
| `FAILED` | Stable application or provider failure |
| `CANCEL_REQUESTED` | Cancellation intent is durable but not yet terminal |
| `CANCELLED` | Cancellation is terminal |

Do not mark an application job successful merely because AWS Batch says `SUCCEEDED`. GCP must also
validate the output manifest and per-item records.

## 11. Bulk preparation and worker behavior

### 11.1 What the GCP bulk preparer does

`watermark-bulk-preparer` is a CPU-only Cloud Run Job. It does not load PyTorch, CUDA, or GPU
models. It:

1. reads the accepted bulk request and store authorization from PostgreSQL;
2. freezes no more than the selected product/image scope;
3. persists the frozen targets;
4. downloads each remote Shopify image;
5. validates size, content type, and decodability;
6. creates immutable account/job objects in GCS;
7. creates job-item rows for valid inputs;
8. writes the deterministic input manifest;
9. stores the manifest generation and checksum;
10. changes the job to `QUEUED`; and
11. writes the durable `AWS_GPU_SUBMISSION_REQUESTED` intent.

It is separate from the FastAPI request so thousands of network downloads do not hold an HTTP
request open or exceed a request timeout.

### 11.2 What the AWS worker does

The worker:

1. starts once per Batch attempt;
2. validates the pinned pipeline config and model manifest;
3. downloads and verifies the required model artifacts;
4. initializes the GPU stages once;
5. iterates manifest items sequentially;
6. reads one input item, processes it, and writes its deterministic outputs;
7. adopts already completed compatible item outputs after a retry;
8. releases per-item memory before continuing; and
9. publishes the output manifest and completion marker at the end.

It does not load all 10,000 images into memory. The manifest describes all items, but image data is
handled one item at a time.

## 12. Build, deploy, and infrastructure paths

### 12.1 AWS infrastructure

Source of truth:

`/home/maaz/Desktop/watermarkPoc/infra/aws`

Terraform uses the versioned GCS backend and staging variables. The `.tfvars` file is input data:

```text
infra/aws/environments/staging.tfvars
```

Never execute it, source it, or add the executable bit. Use it with `terraform -var-file=...`.

For operational mode switching, use:

[09-operational-mode-switch-runbook.md](09-operational-mode-switch-runbook.md)

For a controlled one-image acceptance window, use:

[07-a10-controlled-e2e-operator-runbook.md](07-a10-controlled-e2e-operator-runbook.md)

Do not apply an incomplete, stale, or unreviewed Terraform plan.

### 12.2 Submission Lambda

Source:

`/home/maaz/Desktop/watermarkPoc/services/aws_submission`

Build helper:

```text
services/aws_submission/scripts/build_lambda.sh
```

Terraform packages the built Lambda artifact. Run the submission package tests before changing the
deployed boundary.

### 12.3 Worker image

Docker and source:

```text
Dockerfile.gpu
requirements-gpu.lock
src/
configs/
model-manifest.json
```

Remote build helper:

```text
infra/aws/scripts/start-worker-image-build.sh
```

The normal deployment sequence is:

1. run relevant local unit/contract tests;
2. start CodeBuild;
3. monitor `/aws/codebuild/watermark-staging-worker-image-build`;
4. obtain the immutable ECR digest;
5. update `worker_image_digest` in the reviewed staging variables;
6. run Terraform plan with submission closed if the change is unproven;
7. review and apply the plan; and
8. run the appropriate controlled acceptance.

Do not rebuild the large image merely for a Lambda-only, Terraform-only, backend-only, or frontend
change.

### 12.4 FastAPI service and GCP jobs

The backend source and its three watermark Cloud Run jobs use code from:

`/home/maaz/Desktop/infringement_project/infringement_backend`

Use:

`/home/maaz/Desktop/infringement_project/infringement_backend/docs/google_cloud_run_setup/06_cloud_run_jobs_ops_runbook.md`

Important rule: deploying the Cloud Run service does not repin existing Cloud Run Job definitions.
When backend job code changes, verify and update all affected job images deliberately.

Database migrations are a separate owner-controlled operation. Use Cloud SQL Auth Proxy, inspect
the current Alembic revision, and apply only the repository's reviewed migrations.

## 13. Safe observability

### 13.1 GCP service and job logs

Recent control-plane logs:

```bash
rtk proxy gcloud logging read \
  'resource.type="cloud_run_job"
   AND resource.labels.job_name="watermark-control-plane"' \
  --project "${GCP_PROJECT_ID}" \
  --limit 100 \
  --format='value(timestamp,severity,textPayload,jsonPayload.message)'
```

Recent preparer logs:

```bash
rtk proxy gcloud logging read \
  'resource.type="cloud_run_job"
   AND resource.labels.job_name="watermark-bulk-preparer"' \
  --project "${GCP_PROJECT_ID}" \
  --limit 100 \
  --format='value(timestamp,severity,textPayload,jsonPayload.message)'
```

Recent orchestrator logs:

```bash
rtk proxy gcloud logging read \
  'resource.type="cloud_run_job"
   AND resource.labels.job_name="watermark-aws-orchestrator"' \
  --project "${GCP_PROJECT_ID}" \
  --limit 100 \
  --format='value(timestamp,severity,textPayload,jsonPayload.message)'
```

For one execution, add:

```text
AND labels."run.googleapis.com/execution_name"="<execution-name>"
```

### 13.2 AWS job and GPU state

```bash
for status in RUNNABLE STARTING RUNNING; do
  rtk proxy aws batch list-jobs \
    --job-queue watermark-staging-gpu \
    --job-status "${status}" \
    --region "${AWS_REGION}" \
    --query 'jobSummaryList[].{id:jobId,name:jobName,status:status}' \
    --output table
done

rtk proxy aws ec2 describe-instances \
  --region "${AWS_REGION}" \
  --filters \
    Name=instance-state-name,Values=pending,running,stopping,stopped \
    Name=instance-type,Values='g*','p*' \
  --query 'Reservations[].Instances[].{id:InstanceId,type:InstanceType,state:State.Name,launched:LaunchTime}' \
  --output table
```

An empty result is the expected zero-GPU state.

Get the worker log stream for a known Batch job:

```bash
export AWS_BATCH_JOB_ID="<provider-job-id>"

rtk proxy aws batch describe-jobs \
  --jobs "${AWS_BATCH_JOB_ID}" \
  --region "${AWS_REGION}" \
  --query 'jobs[0].{status:status,reason:statusReason,logStream:container.logStreamName,attempts:attempts}' \
  --output json
```

Then read the returned stream from `/aws/batch/watermark-staging-worker`.

### 13.3 Secret-safe inspection

Cloud Run service and job specifications can contain database passwords, signing keys, provider
settings, and third-party credentials.

Do not:

- print a full Cloud Run service or job JSON/YAML specification into chat;
- run broad environment dumps;
- paste a bearer token, login response, database URL, or password;
- print all Lambda environment variables; or
- commit live credentials into `.tfvars`, documentation, fixtures, or shell scripts.

Use narrow `--format` fields or filter an explicit allowlist of non-secret environment names. The
backend Cloud Run Jobs runbook contains safe examples.

## 14. Operational modes and cost behavior

### 14.1 Operational mode

Operational mode means:

- FastAPI watermark feature flags are enabled;
- the one-minute scheduler is enabled;
- AWS `submission_enabled=true`;
- AWS `batch_queue_enabled=true`; and
- the Batch compute environment remains scale-to-zero with min/desired 0.

In this mode a user can create a FastAPI job and wait for normal polling. No manual gate toggle is
required per job.

### 14.2 Development-safe mode

Development-safe mode means new public work is stopped, the scheduler is paused, and AWS submission
and Batch scheduling are disabled after existing work is safely drained or terminated.

Use it for unproven infrastructure, submission-boundary, contract, worker-image, identity, or
regional changes.

### 14.3 Costs at rest

Zero GPU capacity does not mean zero cloud cost.

Possible at-rest or usage-based costs include:

- GCP Cloud SQL;
- GCS storage and operations;
- Cloud Run service and job executions;
- Cloud Scheduler;
- API Gateway requests;
- Lambda requests and duration;
- DynamoDB requests and storage;
- ECR image and cache storage;
- S3 CodeBuild source storage;
- CodeBuild executions;
- CloudWatch logs; and
- brief EC2/EBS/public IPv4 usage while Batch capacity exists.

The expensive GPU compute is absent when no matching EC2 instance is pending or running.

## 15. Safety and zero-GPU verification

Before a risky deployment or after a controlled test:

1. stop accepting new work if the runbook requires it;
2. pause the scheduler if the runbook requires it;
3. inspect active PostgreSQL jobs and durable intents;
4. inspect AWS Batch RUNNABLE, STARTING, and RUNNING jobs;
5. inspect GPU-family EC2 instances;
6. verify Batch min and desired capacity remain zero;
7. use Terraform to close gates where appropriate; and
8. record the exact final state.

Read-only compute-environment check:

```bash
rtk proxy aws batch describe-compute-environments \
  --compute-environments watermark-staging-gpu \
  --region "${AWS_REGION}" \
  --query 'computeEnvironments[].{state:state,status:status,min:computeResources.minvCpus,desired:computeResources.desiredvCpus,max:computeResources.maxvCpus}' \
  --output table
```

Do not manually terminate a Batch-managed EC2 instance as the normal cleanup mechanism. Terminate
the exact Batch job for an emergency, disable new scheduling, and allow the managed environment to
scale down. Direct EC2 termination is a last-resort incident action because Batch may replace the
instance while the job remains runnable.

## 16. Performance and scale facts

The bulk contract allows 10,000 manifest items, but the current single-T4 throughput does not meet a
10-hour target.

Measured 15-product test:

- the selection produced 14 images;
- end-to-end time was 18m 50.583s;
- steady item processing was 3m 13.801s;
- average item time was 13.84s;
- throughput was 4.33 items/minute; and
- a naive 10,000-item projection was approximately 38.5 hours.

The bulk Batch attempt timeout is currently 24 hours. Therefore the 10,000-item contract is not a
guarantee that every representative 10,000-image job will complete within one attempt on the
current T4.

The worker already keeps models resident once per attempt and processes images sequentially.
Naively processing two GPU items simultaneously is not known to be safe: prior measurement showed
roughly 13.0 GiB allocated and 14.3 GiB reserved on a 16 GiB T4. Any concurrency change requires a
representative benchmark and memory evidence.

Read:

- [08-10000-image-bulk-processing-plan.md](08-10000-image-bulk-processing-plan.md) for the bulk
  architecture and implemented contract; and
- [11-15-product-timing-baseline-and-optimization.md](11-15-product-timing-baseline-and-optimization.md)
  for the timing evidence and optimization order; and
- [12-steady-state-worker-throughput-implementation-plan.md](12-steady-state-worker-throughput-implementation-plan.md)
  for the implementation-ready worker timing, CUDA-kernel, bounded-I/O, and artifact-overhead plan.

## 17. Task-routing index

| If the task is... | Read first | Main code or system |
| --- | --- | --- |
| Understand the whole system | This guide, Sections 3-11 | Both repositories, GCP, AWS |
| Change worker behavior or model loading | [01-gpu-worker-setup.md](01-gpu-worker-setup.md), then [08-10000-image-bulk-processing-plan.md](08-10000-image-bulk-processing-plan.md) | `src/watermark_worker/`, configs, model manifest |
| Change cross-cloud request/response contracts | [03-aws-gpu-worker-setup.md](03-aws-gpu-worker-setup.md), Sections 7-9 | `contracts/aws-gpu-v1/`, Lambda, backend provider |
| Change API Gateway, Lambda, DynamoDB, Batch, WIF, ECR, or CodeBuild | [03-aws-gpu-worker-setup.md](03-aws-gpu-worker-setup.md), then `infra/aws/README.md` | `infra/aws/`, `services/aws_submission/` |
| Build, deploy, and accept a worker image | [aws-worker-image-deployment-and-gpu-acceptance-runbook.md](aws-worker-image-deployment-and-gpu-acceptance-runbook.md) | CodeBuild, ECR, Terraform, Batch |
| Run one controlled cross-cloud acceptance | [07-a10-controlled-e2e-operator-runbook.md](07-a10-controlled-e2e-operator-runbook.md) | Both clouds |
| Turn normal automatic operation on or off | [09-operational-mode-switch-runbook.md](09-operational-mode-switch-runbook.md) | FastAPI flags, scheduler, Terraform gates |
| Change FastAPI watermark routes or persistence | [02-fastapi-integration.md](02-fastapi-integration.md), then backend source | Backend repo |
| Deploy backend or repin Cloud Run Job images | Backend `docs/google_cloud_run_setup/06_cloud_run_jobs_ops_runbook.md` | GCP service and jobs |
| Connect to Cloud SQL or apply Alembic | Backend `docs/google_cloud_run_setup/06_cloud_run_jobs_ops_runbook.md`, Sections “Connect” and “Run Alembic” | Cloud SQL and backend migrations |
| Diagnose PREPARING | This guide, Sections 10, 11, and 13 | Control plane, bulk preparer, PostgreSQL, GCS |
| Diagnose ORCHESTRATING | This guide, Sections 8 and 13 | Control plane, orchestrator, API Gateway, Lambda, DynamoDB |
| Diagnose PROVISIONING or RUNNING | This guide, Sections 7 and 13 | Batch, EC2, ECR, worker logs |
| Diagnose AWAITING_OUTPUT | Backend reconciler and output validation | Batch state, GCS output manifest, PostgreSQL |
| Verify or shut down GPU capacity | [09-operational-mode-switch-runbook.md](09-operational-mode-switch-runbook.md), Sections 10-11 | Scheduler, Batch, EC2 |
| Plan Stockholm to Ohio migration | [10-aws-gpu-regional-migration-plan.md](10-aws-gpu-regional-migration-plan.md) | New isolated regional Terraform state |
| Optimize throughput or latency | [11-15-product-timing-baseline-and-optimization.md](11-15-product-timing-baseline-and-optimization.md), then [12-steady-state-worker-throughput-implementation-plan.md](12-steady-state-worker-throughput-implementation-plan.md) for the first four worker tasks | Instrumentation, region, preparer, worker |
| Add or change frontend watermark UI | Existing frontend bulk-scan patterns and Postman collection | Frontend repo and FastAPI only |

## 18. Document authority and historical notes

| Document | Use it for | Important caveat |
| --- | --- | --- |
| `00-hybrid-watermark-system-agent-guide.md` | Current map, service inventory, safe discovery, and task routing | Refresh dynamic state before acting |
| `01-gpu-worker-setup.md` | Provider-neutral worker contract and safety behavior | Its Google GPU execution plan is legacy for the current AWS runtime |
| `02-fastapi-integration.md` | Application orchestration concepts and API design | Some original tasks were superseded by implemented backend and bulk design |
| `03-aws-gpu-worker-setup.md` | Main AWS architecture, A0-A11 decisions, contracts, security, and implementation history | Its status header and some point-in-time snapshots are older than the live operational state |
| `03a-aws-a1-preflight-2026-07-24.md` | Historical Stockholm quota/service/cost evidence | Point-in-time preflight only |
| `aws-worker-image-deployment-and-gpu-acceptance-runbook.md` | Current worker build, digest deployment, controlled GPU acceptance, rollback, and zero-capacity procedure | Refresh live state before every deployment |
| `05-a10-fastapi-agent-implementation-prompt.md` | Historical A10 backend implementation contract | Not a deployment or current-operations runbook |
| `07-a10-controlled-e2e-operator-runbook.md` | Safe controlled one-image end-to-end test | Temporarily toggles controls; use the operational runbook for normal mode |
| `08-10000-image-bulk-processing-plan.md` | Bulk architecture and cross-repository implementation | The 10,000 limit is not a current 10-hour performance guarantee |
| `09-operational-mode-switch-runbook.md` | Authoritative operational versus development-safe switching | Verify active work before closing gates |
| `10-aws-gpu-regional-migration-plan.md` | Proposed Stockholm-to-Ohio migration | Proposal only; current AWS remains Stockholm |
| `11-15-product-timing-baseline-and-optimization.md` | Measured latency and optimization priorities | Based on one 14-image execution |
| `12-steady-state-worker-throughput-implementation-plan.md` | Implementation order and acceptance gates for worker timing, CUDA extension, bounded I/O overlap, and artifact overhead | Plan only until checkpoint commits and benchmark evidence are recorded |

When documentation and live state differ:

1. do not silently choose one;
2. identify whether the document is a contract, historical record, proposal, or runbook;
3. verify Terraform, source code, and live read-only state;
4. explain the discrepancy; and
5. update the correct authority if the user asks for a change.

## 19. Common mistakes to avoid

- Do not execute or `chmod +x` a `.tfvars` file.
- Do not call the AWS internal API from the frontend or Postman as an end user.
- Do not give the GCP backend general long-lived AWS credentials.
- Do not put a GCP service-account key in the worker image.
- Do not treat an enabled Batch queue as an active GPU.
- Do not assume the GCP control plane directly shuts down EC2.
- Do not mark a job successful from Batch state without GCS output validation.
- Do not create a second application database in DynamoDB.
- Do not create one Batch job per image without a new measured design.
- Do not load all bulk image bytes into memory.
- Do not rebuild the GPU image for backend-only or Lambda-only changes.
- Do not use an old Cloud Run URL without discovering the current service URL.
- Do not dump full Cloud Run or Lambda environments into chat or logs.
- Do not reuse an idempotency key with a different request.
- Do not use an application job ID as an AWS Batch name filter without confirming the actual Batch
  job-name format.
- Do not assume ECR, DynamoDB, Batch, API Gateway, logs, quotas, or endpoints are global.
- Do not move the AWS region by editing the existing regional state in place.
- Do not promise 10,000 images in 10 hours from the current T4 benchmark.

## 20. Minimal new-agent checklist

At the start of a watermark task:

```text
[ ] Read repository AGENTS.md
[ ] Read this guide's current-state section
[ ] Identify the owning repository
[ ] Identify whether the referenced document is current, historical, or proposed
[ ] Check git status and preserve unrelated user changes
[ ] Verify GCP project and AWS account/region
[ ] Inspect live state with narrow, secret-safe queries
[ ] Confirm whether the task is discussion, diagnosis, implementation, deployment, or operation
[ ] Read the task-specific runbook from Section 17
[ ] Explain why before changing code or infrastructure
[ ] Test in proportion to the change
[ ] For AWS work, verify active Batch jobs and GPU EC2 state before and after
[ ] Report what changed, what was verified, and what remains
```
