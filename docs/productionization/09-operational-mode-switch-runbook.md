# Watermark system operational-mode switch runbook

**Status:** Ready for operator use  
**Last verified:** 2026-07-29  
**GCP project:** `project-5909a738-2fdc-451e-ad6`  
**GCP region:** `us-central1`  
**AWS account:** `154959837939`  
**AWS region:** `eu-north-1`

## 1. Purpose

This runbook lets an operator or a new agent place the hybrid watermark system in either of two
states:

| Mode | Intended use | FastAPI accepts new jobs | Automatic dispatch | Idle GPU |
|---|---|---:|---:|---:|
| **Operational** | Normal application use | Yes | Every minute | None |
| **Development-safe** | Worker changes, image builds, and controlled tests | No | Paused | None |

Operational mode does **not** keep an EC2 GPU instance running. The AWS Batch compute environment
has `min_vcpus = 0` and `desired_vcpus = 0`. A `g4dn.xlarge` instance is created only after a real
job reaches AWS Batch and is removed after that job becomes terminal.

In operational mode, a request follows this path:

```text
FastAPI request
  -> PostgreSQL durable intent
  -> one-minute GCP control-plane schedule
  -> bulk preparer Cloud Run Job, when bulk input preparation is required
  -> GCS immutable input manifest
  -> orchestrator Cloud Run Job
  -> authenticated AWS submission API
  -> AWS Batch
  -> temporary g4dn.xlarge GPU worker
  -> GCS results
  -> scheduled GCP reconciliation
  -> FastAPI job and item status
```

Normal pickup takes approximately 0 to 60 seconds because the control-plane job runs once per
minute.

## 2. Repositories and source-of-truth files

```text
Worker and AWS infrastructure:
/home/maaz/Desktop/watermarkPoc

AWS staging variables:
/home/maaz/Desktop/watermarkPoc/infra/aws/environments/staging.tfvars

FastAPI and GCP control-plane application:
/home/maaz/Desktop/infringement_project/infringement_backend

Cloud Run job operations reference:
/home/maaz/Desktop/infringement_project/infringement_backend/docs/google_cloud_run_setup/06_cloud_run_jobs_ops_runbook.md

Bulk architecture and implementation record:
/home/maaz/Desktop/watermarkPoc/docs/productionization/08-10000-image-bulk-processing-plan.md
```

`staging.tfvars` is a Terraform input file. Edit it with an editor. Never execute it and never make
it executable.

## 3. Safety rules

1. Stop new FastAPI submissions before pausing the dispatcher or disabling AWS.
2. Do not disable AWS while a Batch job is active unless performing an emergency shutdown.
3. Disabling a Batch queue does not terminate an already-running job.
4. Use Terraform for AWS mode changes. Do not manually enable the queue or compute environment.
5. Always create and inspect a saved Terraform plan before applying it.
6. Do not apply a failed, incomplete, or unexpected plan.
7. Do not put passwords, access tokens, database URLs, or secret values into this document or a
   terminal transcript.
8. Finish every shutdown with the zero-GPU checks in section 10.
9. The existing AWS budget is an alert, not an automatic shutdown mechanism.

## 4. Session variables and authentication

Open a dedicated terminal and set:

```bash
set +e

export GCP_PROJECT_ID="project-5909a738-2fdc-451e-ad6"
export GCP_REGION="us-central1"
export API_SERVICE="infringementdetection"
export CONTROL_PLANE_JOB="watermark-control-plane"
export ORCHESTRATOR_JOB="watermark-aws-orchestrator"
export BULK_PREPARER_JOB="watermark-bulk-preparer"
export SCHEDULER_JOB="watermark-control-plane-every-minute"
export ORCHESTRATOR_SA="watermark-aws-orchestrator@project-5909a738-2fdc-451e-ad6.iam.gserviceaccount.com"
export DATA_BUCKET="project-5909a738-2fdc-451e-ad6-watermark-data"

export AWS_REGION="eu-north-1"
export AWS_ACCOUNT_ID="154959837939"
export AWS_BATCH_QUEUE="watermark-staging-gpu"
export AWS_COMPUTE_ENVIRONMENT="watermark-staging-gpu"
export AWS_SUBMISSION_FUNCTION="watermark-staging-submission"

export AWS_TF_ROOT="/home/maaz/Desktop/watermarkPoc/infra/aws"
export AWS_TFVARS="${AWS_TF_ROOT}/environments/staging.tfvars"
```

Confirm identities:

```bash
rtk proxy gcloud auth list --filter=status:ACTIVE
rtk proxy gcloud config get-value project
rtk proxy aws sts get-caller-identity
```

The AWS account must be `154959837939`. If the AWS session has expired:

```bash
rtk aws login
rtk proxy aws sts get-caller-identity
```

Every command below includes an explicit project and region so it does not depend on a local
default.

## 5. First-time prerequisite: deploy the bulk preparer job

Run this section only if `watermark-bulk-preparer` does not exist, or when it must be refreshed to
the same backend image and runtime configuration as the current backend jobs.

Check first:

```bash
rtk proxy gcloud run jobs describe "${BULK_PREPARER_JOB}" \
  --project "${GCP_PROJECT_ID}" \
  --region "${GCP_REGION}" \
  --format='yaml(metadata.name,spec.template.template.serviceAccount,spec.template.template.containers[0].image)'
```

If it is missing, create it by cloning the existing `shopify-sync-job`. This deliberately avoids
printing or manually copying its database configuration. The temporary file can contain sensitive
environment configuration, so it is created with mode `600` and deleted automatically:

```bash
export BACKEND_ROOT="/home/maaz/Desktop/infringement_project/infringement_backend"
export PREPARER_YAML="$(mktemp /tmp/watermark-bulk-preparer.XXXXXX.yaml)"
chmod 600 "${PREPARER_YAML}"
trap 'rm -f "${PREPARER_YAML}"' EXIT

rtk proxy gcloud run jobs describe shopify-sync-job \
  --project "${GCP_PROJECT_ID}" \
  --region "${GCP_REGION}" \
  --format=export > "${PREPARER_YAML}"

"${BACKEND_ROOT}/.venv/bin/python" - "${PREPARER_YAML}" <<'PY'
import sys
import yaml

path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    document = yaml.safe_load(handle)

document["metadata"]["name"] = "watermark-bulk-preparer"
task = document["spec"]["template"]["spec"]["template"]["spec"]
container = task["containers"][0]
container["command"] = ["python"]
container["args"] = [
    "-m",
    "app.jobs.runner",
    "watermark-bulk-prepare",
    "--job-id",
    "0",
]
task["timeoutSeconds"] = 86400
task["maxRetries"] = 0

with open(path, "w", encoding="utf-8") as handle:
    yaml.safe_dump(document, handle, sort_keys=False)
PY

rtk proxy gcloud run jobs replace "${PREPARER_YAML}" \
  --project "${GCP_PROJECT_ID}" \
  --region "${GCP_REGION}"

rm -f "${PREPARER_YAML}"
trap - EXIT
```

The placeholder job ID `0` is intentional. An accidental manual execution fails safely; the
control plane supplies the actual database job ID as an execution override.

Add only the non-secret watermark settings:

```bash
rtk proxy gcloud run jobs update "${BULK_PREPARER_JOB}" \
  --project "${GCP_PROJECT_ID}" \
  --region "${GCP_REGION}" \
  --update-env-vars \
"WATERMARK_INPUT_BUCKET=${DATA_BUCKET},WATERMARK_MANIFEST_BUCKET=${DATA_BUCKET},WATERMARK_OUTPUT_BUCKET=${DATA_BUCKET},WATERMARK_COMPUTE_PROVIDER=AWS_BATCH,WATERMARK_OCR_ENABLED=true,WATERMARK_BULK_MAX_ITEMS_PER_JOB=25000"
```

Allow the control-plane identity to execute this job with the per-execution job ID override:

```bash
rtk proxy gcloud run jobs add-iam-policy-binding "${BULK_PREPARER_JOB}" \
  --project "${GCP_PROJECT_ID}" \
  --region "${GCP_REGION}" \
  --member "serviceAccount:${ORCHESTRATOR_SA}" \
  --role "roles/run.jobsExecutorWithOverrides"
```

Verify its image, identity, command, timeout, and IAM:

```bash
rtk proxy gcloud run jobs describe "${BULK_PREPARER_JOB}" \
  --project "${GCP_PROJECT_ID}" \
  --region "${GCP_REGION}" \
  --format='yaml(metadata.name,spec.template.template.serviceAccount,spec.template.template.timeout,spec.template.template.containers[0].image,spec.template.template.containers[0].command,spec.template.template.containers[0].args)'

rtk proxy gcloud run jobs get-iam-policy "${BULK_PREPARER_JOB}" \
  --project "${GCP_PROJECT_ID}" \
  --region "${GCP_REGION}" \
  --flatten='bindings[].members' \
  --filter="bindings.role:roles/run.jobsExecutorWithOverrides AND bindings.members:serviceAccount:${ORCHESTRATOR_SA}" \
  --format='table(bindings.role,bindings.members)'
```

Do not manually execute the preparer during setup.

## 6. Pre-switch checks

### 6.1 Confirm required GCP resources

```bash
rtk proxy gcloud run services describe "${API_SERVICE}" \
  --project "${GCP_PROJECT_ID}" \
  --region "${GCP_REGION}" \
  --format='value(status.latestReadyRevisionName)'

for JOB_NAME in "${CONTROL_PLANE_JOB}" "${ORCHESTRATOR_JOB}" "${BULK_PREPARER_JOB}"; do
  rtk proxy gcloud run jobs describe "${JOB_NAME}" \
    --project "${GCP_PROJECT_ID}" \
    --region "${GCP_REGION}" \
    --format='table(metadata.name,status.conditions[0].status,spec.template.template.containers[0].image)'
done

rtk proxy gcloud scheduler jobs describe "${SCHEDULER_JOB}" \
  --project "${GCP_PROJECT_ID}" \
  --location "${GCP_REGION}" \
  --format='yaml(state,schedule,httpTarget.uri,httpTarget.oauthToken.serviceAccountEmail)'
```

The scheduler target must be the `watermark-control-plane` job, its service account must be
`watermark-aws-orchestrator@...`, and the schedule must be `* * * * *`.

### 6.2 Confirm no active AWS work

Run section 10. If any active job or GPU instance appears, do not switch modes until its purpose
and owner are known.

### 6.3 Check the database dispatch backlog

Before entering operational mode, check that enabling the scheduler will not dispatch an old,
forgotten job. Connect to the database using:

```text
/home/maaz/Desktop/infringement_project/infringement_backend/docs/google_cloud_run_setup/06_cloud_run_jobs_ops_runbook.md
```

Then run:

```sql
SELECT
    id,
    application_job_id,
    source_kind,
    status,
    provider_job_id,
    created_at
FROM watermark_jobs
WHERE status IN (
    'PREPARING',
    'QUEUED',
    'ORCHESTRATING',
    'PROVISIONING',
    'RUNNING',
    'AWAITING_OUTPUT',
    'CANCEL_REQUESTED'
)
ORDER BY created_at;

SELECT
    i.id,
    j.application_job_id,
    i.kind,
    i.attempts,
    i.created_at
FROM watermark_job_intents AS i
JOIN watermark_jobs AS j ON j.id = i.job_id
WHERE i.delivered_at IS NULL
ORDER BY i.created_at;
```

An empty result from both queries is the clean starting state. Do not delete or modify old rows
just to make the query empty. Investigate and explicitly finish or cancel them.

## 7. Switch to operational mode

Complete the steps in this order.

### 7.1 Enable the FastAPI routes

This permits authenticated asset upload, the one-to-five-image API, and the store-scoped bulk API:

```bash
rtk proxy gcloud run services update "${API_SERVICE}" \
  --project "${GCP_PROJECT_ID}" \
  --region "${GCP_REGION}" \
  --update-env-vars \
"WATERMARK_FEATURE_ENABLED=true,WATERMARK_BULK_FEATURE_ENABLED=true,WATERMARK_ASSET_UPLOAD_ENABLED=true,CLOUD_RUN_WATERMARK_BULK_PREPARER_JOB_NAME=${BULK_PREPARER_JOB}"
```

### 7.2 Configure the control-plane job

```bash
rtk proxy gcloud run jobs update "${CONTROL_PLANE_JOB}" \
  --project "${GCP_PROJECT_ID}" \
  --region "${GCP_REGION}" \
  --update-env-vars \
"WATERMARK_FEATURE_ENABLED=true,WATERMARK_BULK_FEATURE_ENABLED=true,CLOUD_RUN_WATERMARK_BULK_PREPARER_JOB_NAME=${BULK_PREPARER_JOB}"
```

Do not enable the scheduler yet.

### 7.3 Enable the AWS gates through Terraform

Edit:

```bash
nano "${AWS_TFVARS}"
```

Change only:

```hcl
submission_enabled  = true
batch_queue_enabled = true
```

Keep the existing image digest and both pipeline names unchanged.

Validate, create a saved plan, and inspect it:

```bash
cd "${AWS_TF_ROOT}"
rtk terraform fmt -check
rtk terraform validate
rtk terraform plan \
  -var-file="${AWS_TFVARS}" \
  -out=/tmp/watermark-operational.tfplan
rtk terraform show -no-color /tmp/watermark-operational.tfplan
```

The plan should only enable the submission flag and Batch queue/compute environment. It must not
replace the worker definitions, change the worker image digest, or create unrelated resources.

Apply the exact saved plan:

```bash
rtk terraform apply /tmp/watermark-operational.tfplan
rm -f /tmp/watermark-operational.tfplan
```

### 7.4 Resume automatic polling

```bash
rtk proxy gcloud scheduler jobs resume "${SCHEDULER_JOB}" \
  --project "${GCP_PROJECT_ID}" \
  --location "${GCP_REGION}"
```

### 7.5 Verify operational mode

```bash
rtk proxy gcloud scheduler jobs describe "${SCHEDULER_JOB}" \
  --project "${GCP_PROJECT_ID}" \
  --location "${GCP_REGION}" \
  --format='yaml(state,schedule)'

rtk proxy aws lambda get-function-configuration \
  --function-name "${AWS_SUBMISSION_FUNCTION}" \
  --region "${AWS_REGION}" \
  --query 'Environment.Variables.SUBMISSION_ENABLED' \
  --output text

rtk proxy aws batch describe-job-queues \
  --job-queues "${AWS_BATCH_QUEUE}" \
  --region "${AWS_REGION}" \
  --query 'jobQueues[0].{state:state,status:status}' \
  --output table

rtk proxy aws batch describe-compute-environments \
  --compute-environments "${AWS_COMPUTE_ENVIRONMENT}" \
  --region "${AWS_REGION}" \
  --query 'computeEnvironments[0].{state:state,status:status,min:computeResources.minvCpus,desired:computeResources.desiredvCpus,max:computeResources.maxvCpus,types:computeResources.instanceTypes}' \
  --output table
```

Expected:

```text
Scheduler: ENABLED
Lambda SUBMISSION_ENABLED: true
Batch queue: ENABLED / VALID
Compute environment: ENABLED / VALID
min vCPUs: 0
desired vCPUs while idle: 0
instance type: g4dn.xlarge
```

Run section 10 once more. Zero GPUs is still the expected idle state.

The system is now automatically operational. A user can submit through FastAPI without manually
enabling AWS or executing the control plane.

## 8. Switch to development-safe mode

This is a graceful shutdown. It preserves job definitions, container images, logs, DynamoDB
reservations, GCS data, and PostgreSQL records.

### 8.1 Stop accepting new work

```bash
rtk proxy gcloud run services update "${API_SERVICE}" \
  --project "${GCP_PROJECT_ID}" \
  --region "${GCP_REGION}" \
  --update-env-vars \
"WATERMARK_FEATURE_ENABLED=false,WATERMARK_BULK_FEATURE_ENABLED=false,WATERMARK_ASSET_UPLOAD_ENABLED=false"
```

### 8.2 Drain current work

Run section 10 and the database queries in section 6.3.

If work is active, leave the scheduler and AWS enabled while the job finishes and the control plane
reconciles its terminal state. Monitor:

```bash
rtk proxy gcloud logging read \
  "resource.type=cloud_run_job AND (resource.labels.job_name=${CONTROL_PLANE_JOB} OR resource.labels.job_name=${ORCHESTRATOR_JOB} OR resource.labels.job_name=${BULK_PREPARER_JOB})" \
  --project "${GCP_PROJECT_ID}" \
  --limit 100 \
  --format='table(timestamp,resource.labels.job_name,severity,textPayload)'
```

Continue only when:

- every accepted application job is terminal;
- there are no undelivered intents;
- no AWS Batch job is active; and
- no GPU EC2 instance is running or pending.

### 8.3 Pause the dispatcher

```bash
rtk proxy gcloud scheduler jobs pause "${SCHEDULER_JOB}" \
  --project "${GCP_PROJECT_ID}" \
  --location "${GCP_REGION}"
```

### 8.4 Disable AWS through Terraform

Edit:

```bash
nano "${AWS_TFVARS}"
```

Change only:

```hcl
submission_enabled  = false
batch_queue_enabled = false
```

Plan and inspect:

```bash
cd "${AWS_TF_ROOT}"
rtk terraform fmt -check
rtk terraform validate
rtk terraform plan \
  -var-file="${AWS_TFVARS}" \
  -out=/tmp/watermark-development-safe.tfplan
rtk terraform show -no-color /tmp/watermark-development-safe.tfplan
```

Apply only if the plan disables those gates and has no unrelated changes:

```bash
rtk terraform apply /tmp/watermark-development-safe.tfplan
rm -f /tmp/watermark-development-safe.tfplan
```

### 8.5 Keep the control-plane configuration internally consistent

The scheduler is paused, so this job will not run automatically. Set its feature flags false as an
explicit record of the selected mode:

```bash
rtk proxy gcloud run jobs update "${CONTROL_PLANE_JOB}" \
  --project "${GCP_PROJECT_ID}" \
  --region "${GCP_REGION}" \
  --update-env-vars \
"WATERMARK_FEATURE_ENABLED=false,WATERMARK_BULK_FEATURE_ENABLED=false"
```

These two settings do not themselves prevent a direct manual execution of the control-plane
command. The paused scheduler, disabled FastAPI routes, and disabled AWS submission gate are the
actual operational barriers.

### 8.6 Verify safe mode

```bash
rtk proxy gcloud scheduler jobs describe "${SCHEDULER_JOB}" \
  --project "${GCP_PROJECT_ID}" \
  --location "${GCP_REGION}" \
  --format='value(state)'

rtk proxy aws lambda get-function-configuration \
  --function-name "${AWS_SUBMISSION_FUNCTION}" \
  --region "${AWS_REGION}" \
  --query 'Environment.Variables.SUBMISSION_ENABLED' \
  --output text

rtk proxy aws batch describe-job-queues \
  --job-queues "${AWS_BATCH_QUEUE}" \
  --region "${AWS_REGION}" \
  --query 'jobQueues[0].{state:state,status:status}' \
  --output table

rtk proxy aws batch describe-compute-environments \
  --compute-environments "${AWS_COMPUTE_ENVIRONMENT}" \
  --region "${AWS_REGION}" \
  --query 'computeEnvironments[0].{state:state,status:status,desired:computeResources.desiredvCpus}' \
  --output table
```

Expected:

```text
Scheduler: PAUSED
Lambda SUBMISSION_ENABLED: false
Batch queue: DISABLED / VALID
Compute environment: DISABLED / VALID
desired vCPUs: 0
```

Finish with section 10.

## 9. Controlled testing while in development-safe mode

Do not partially improvise a test by directly enabling AWS resources. Use the controlled end-to-end
runbook:

```text
/home/maaz/Desktop/watermarkPoc/docs/productionization/07-a10-controlled-e2e-operator-runbook.md
```

That runbook temporarily enables the required path, submits one bounded job, observes it, and
returns the system to the safe state. After testing a new worker image, update Terraform's immutable
worker digest and complete its controlled acceptance before returning to operational mode.

## 10. Mandatory zero-GPU and active-work verification

List active Batch jobs in every non-terminal state:

```bash
for STATUS in SUBMITTED PENDING RUNNABLE STARTING RUNNING; do
  echo "=== ${STATUS} ==="
  rtk proxy aws batch list-jobs \
    --job-queue "${AWS_BATCH_QUEUE}" \
    --job-status "${STATUS}" \
    --region "${AWS_REGION}" \
    --query 'jobSummaryList[].{id:jobId,name:jobName,status:status,createdAt:createdAt}' \
    --output table
done
```

List active GPU instances:

```bash
rtk proxy aws ec2 describe-instances \
  --region "${AWS_REGION}" \
  --filters \
    "Name=instance-state-name,Values=pending,running,stopping" \
    "Name=instance-type,Values=g4dn.xlarge" \
  --query 'Reservations[].Instances[].{id:InstanceId,state:State.Name,type:InstanceType,launched:LaunchTime}' \
  --output table
```

Confirm Batch is not requesting idle compute:

```bash
rtk proxy aws batch describe-compute-environments \
  --compute-environments "${AWS_COMPUTE_ENVIRONMENT}" \
  --region "${AWS_REGION}" \
  --query 'computeEnvironments[0].{state:state,status:status,min:computeResources.minvCpus,desired:computeResources.desiredvCpus,max:computeResources.maxvCpus}' \
  --output table
```

Success means:

- every active-job table is empty;
- the EC2 table is empty; and
- `desired` is `0`.

## 11. Emergency shutdown

Use this only when a job is malfunctioning or incurring unwanted GPU cost.

1. Disable FastAPI flags using section 8.1.
2. Pause the scheduler using section 8.3.
3. List active Batch jobs using section 10.
4. For each confirmed target job ID, terminate it:

```bash
rtk proxy aws batch terminate-job \
  --job-id "PASTE_CONFIRMED_JOB_ID" \
  --reason "Operator emergency shutdown" \
  --region "${AWS_REGION}"
```

5. Wait until the Batch job is terminal and the GPU EC2 instance disappears.
6. Disable AWS with the Terraform procedure in section 8.4.
7. Run section 10.
8. Record which application job was interrupted. Do not mark it successful; reconcile or fail it
   explicitly before enabling normal operation again.

Never terminate jobs by guessed names or IDs.

## 12. What remains deployed and what can cost money

Both modes retain:

- API Gateway and the Lambda submission service;
- DynamoDB idempotency records;
- ECR worker images;
- AWS Batch definitions, queue, and compute-environment definitions;
- CloudWatch logs;
- Cloud Run service and job definitions;
- Cloud Scheduler definition;
- GCS objects; and
- PostgreSQL state.

These control-plane and storage resources can have small ongoing charges. The expensive GPU compute
charge occurs only while a `g4dn.xlarge` instance exists. Pausing the GCP scheduler prevents
automatic polling charges; disabling the AWS gates prevents new submissions and new Batch compute.

The `$60` AWS budget alerts on spend. It does not stop a job or enforce a hard cap.

## 13. Current recorded state

At the last verification on 2026-07-29:

- FastAPI watermark feature flags were false;
- the one-minute scheduler existed and was paused;
- `watermark-bulk-preparer` had not yet been deployed;
- AWS `submission_enabled` was false;
- the Batch queue and compute environment were disabled and valid;
- Batch desired vCPUs were zero;
- no active Batch job or GPU instance existed;
- the deployed worker digest was
  `sha256:f3e14394f94df90845ea840983baabeda988a40a366172058cac030cf59241b1`;
- the small Batch definition was `watermark-staging-worker:4`; and
- the bulk Batch definition was `watermark-staging-worker-bulk:1`.

Always trust fresh commands over this historical snapshot.

## 14. Success criteria

Operational mode is complete only when:

- all three FastAPI watermark flags are true;
- the bulk preparer exists and the control-plane identity can execute it;
- control-plane feature flags are true;
- the scheduler is enabled at one-minute cadence;
- AWS submission, queue, and compute environment are enabled and valid;
- idle desired vCPUs remain zero; and
- no unexpected backlog was released.

Development-safe mode is complete only when:

- all three FastAPI watermark flags are false;
- the scheduler is paused;
- control-plane feature flags are false;
- AWS submission, queue, and compute environment are disabled and valid;
- no active Batch job exists;
- no GPU EC2 instance exists; and
- desired vCPUs are zero.
