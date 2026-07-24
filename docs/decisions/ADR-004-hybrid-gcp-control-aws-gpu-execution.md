# ADR-004: Keep the application control plane in GCP and run GPU workers on AWS

## Status

Accepted

## Date

2026-07-24

## Context

The watermark worker, its immutable manifest/result contracts, and its fail-closed processing
behavior were designed for Google Cloud Batch. The application backend, PostgreSQL database,
authorization layer, source assets, and customer result delivery remain in Google Cloud.

The project currently has zero regional T4 quota and zero project-wide GPU quota in Google Cloud,
which prevents live GPU acceptance. Moving the entire application to AWS would expand the migration
far beyond the blocked component. The GPU worker is already isolated from the application database
and accepts an immutable GCS manifest, so it is a natural provider boundary.

The hybrid design must preserve:

- FastAPI and PostgreSQL as the application source of truth.
- GCS generation and SHA-256 pinning for manifests and source images.
- The existing item result, artifact, and completion-marker contracts.
- At-least-once execution with item-level resumability.
- No long-lived AWS access keys in GCP and no Google service-account keys in AWS.
- One application job producing at most one billable AWS Batch job unless an explicit retry policy
  permits another attempt.

## Decision

- Keep FastAPI, PostgreSQL, application authorization, input manifests, source assets, output
  artifacts, and result access in GCP.
- Use AWS only as the initial GPU execution plane.
- Use `eu-north-1` (Europe/Stockholm) as the initial AWS implementation and GPU acceptance region
  because the account has an approved four-vCPU On-Demand G/VT quota there and `g4dn.xlarge` is
  offered in three Availability Zones. Treat this as an explicit initial placement decision, not a
  claim that Stockholm is permanently optimal for GCS data in `us-central1`; measure cross-cloud
  latency and transfer cost before broader production rollout.
- Run the worker through an AWS Batch managed EC2 compute environment, initially restricted to one
  On-Demand `g4dn.xlarge` instance with one NVIDIA T4.
- Publish the worker image to Amazon ECR and submit it by immutable digest.
- Place a small authenticated AWS submission service in front of AWS Batch. The service owns the
  approved job queue, job-definition revision, image digest, task role, instance family, timeout,
  and retry policy; callers cannot override them.
- Implement that boundary as one API Gateway HTTP API with native JWT validation and one submission
  Lambda. API Gateway validates the Google token signature, configured issuer, dedicated audience,
  and supported time claims. The Lambda validates the exact immutable GCP service-account subject,
  validates the job request, owns DynamoDB idempotency, and calls AWS Batch. Do not deploy a
  separate custom Lambda authorizer for the initial single-caller, single-purpose production
  boundary.
- Use a DynamoDB conditional record keyed by `applicationJobId` to reserve and reconcile one AWS
  Batch submission. A deterministic AWS job name alone is not the idempotency boundary because the
  `SubmitJob` API returns a unique job ID and provides no ordinary container-job client token.
- Authenticate the GCP orchestrator to the AWS submission service with a Google-signed service
  account ID token. Validate signature, issuer, audience, and expiry at API Gateway and the exact
  immutable service-account subject in the submission Lambda before accepting a request.
- Give the AWS Batch job an IAM task role and use Google Workload Identity Federation to exchange
  its temporary AWS credentials for short-lived Google credentials. Do not create a Google
  service-account key.
- Keep GCS as the authoritative input and output store for initial acceptance. Evaluate immutable
  S3 model replicas after measuring model-download latency and cross-cloud transfer cost.
- Keep callbacks optional for correctness. GCP reconciliation validates AWS state and the compatible
  GCS job completion marker before updating the application job to `SUCCEEDED`.

## Alternatives considered

### Continue waiting for Google Cloud GPU quota

Retained as a future provider option, but rejected as the only path to acceptance. The current
quota gate has no committed resolution date.

### Move the complete backend and database to AWS

Rejected. The GPU is the blocked and independently deployable component. Moving authentication,
PostgreSQL, APIs, and customer storage would add migration risk without improving the worker
contract.

### Let the GCP orchestrator call AWS Batch directly

Deferred. OIDC federation can provide short-lived AWS credentials, but ordinary AWS Batch
`SubmitJob` has no client token for container jobs. An AWS-side submission ledger gives a stronger
cross-cloud idempotency boundary and prevents callers from overriding infrastructure settings.

### Use a separate custom Lambda authorizer

Rejected for the initial production design. There is one trusted GCP caller and a very small
internal route surface. API Gateway's native JWT authorizer can validate the standard Google token
properties, while the single submission Lambda can enforce the exact subject alongside
job-specific authorization. A separate authorizer adds another function, deployment, permission,
log stream, failure mode, and cache policy without adding a required trust boundary. Reconsider
only if multiple callers, multiple APIs, or independently owned authorization policy appear.

### Verify every Google token only inside the submission Lambda

Retained as a compatibility fallback, not the preferred deployment. It can remain secure when the
official Google authentication library is used and every request fails closed, but it invokes
Lambda for unauthenticated traffic and combines standard cryptographic verification with business
logic. Prefer native API Gateway JWT validation and keep exact-subject/job authorization in Lambda.

### Move all manifests, inputs, and outputs to S3

Rejected for the initial milestone. It would require bidirectional replication and would make GCP
result authorization depend on another storage source of truth. Model artifacts may be mirrored to
S3 separately because they are large, immutable, and shared across jobs.

### Use EC2 directly instead of AWS Batch

Rejected. Direct instance lifecycle code would recreate queueing, provisioning, retries,
termination, and cleanup behavior already supplied by AWS Batch.

### Use Spot capacity immediately

Deferred until On-Demand interruption/resume acceptance is complete and cost/runtime evidence
exists. A long GPU attempt can be expensive to repeat even when item checkpoints are reusable.

## Consequences

- The public FastAPI contract and PostgreSQL authority remain in GCP.
- The provider-specific orchestration contract changes from Google Cloud Batch to an internal AWS
  submission API and AWS Batch job ID.
- The AWS submission service has one API Gateway HTTP API and one submission Lambda; there is no
  separate custom Lambda authorizer in the initial production architecture.
- The worker needs keyless GCP credentials that function from an AWS Batch/ECS task role.
- ECR, AWS Batch, DynamoDB, API Gateway/Lambda, CloudWatch, and EventBridge become operational
  dependencies.
- Cross-cloud networking and transfer costs must be measured and budgeted.
- Initial AWS resources and provider identities are region-bound to `eu-north-1`; changing region
  requires redeployment and a reviewed quota, identity, cost, and data-path decision.
- Logs and traces span Cloud Logging and CloudWatch and therefore require consistent correlation
  fields.
- AWS Batch success is necessary but insufficient; GCP accepts success only after validating the
  expected output manifest and job completion marker.
- The provider boundary remains replaceable. A later Google Batch provider can coexist behind the
  same application job and worker manifest contracts.
