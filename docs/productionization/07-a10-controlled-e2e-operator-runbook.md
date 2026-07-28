# A10 controlled one-image test operator runbook

Status: **Operator commands validated; repeat with a normal source image**

Date: 2026-07-28

## Purpose

This runbook lets one operator complete A10 steps 8 through 11:

1. create the one-minute GCP control-plane schedule in a paused state;
2. briefly enable the AWS submission path and FastAPI watermark feature;
3. submit and observe one real image from FastAPI through AWS Batch;
4. download the result through FastAPI; and
5. return GCP and AWS to their safe disabled state.

The data and application source of truth remain in GCP. AWS is used only for the temporary GPU
execution.

The first infrastructure run completed the complete GCP-to-AWS-to-GCP path. Its source file was a
six-panel pipeline/debug collage, however, so its mostly blank final image did not validate useful
image-processing quality. This runbook now requires a normal, unprocessed source image and treats
visual inspection as part of acceptance.

The complete path is:

```text
your curl command
  -> FastAPI in GCP
  -> PostgreSQL intent in GCP
  -> scheduled control-plane Cloud Run Job
  -> submission-orchestrator Cloud Run Job
  -> authenticated AWS submission API
  -> AWS Batch
  -> temporary g4dn.xlarge GPU instance
  -> GCS results
  -> GCP control-plane reconciliation
  -> authorized FastAPI result download
```

## Important safety rules

1. `staging.tfvars` is a configuration file. **Edit it; never execute it.**
2. Never run `chmod +x` on `staging.tfvars`.
3. Never apply a Terraform plan that failed, is incomplete, is not applyable, or contains an
   unexpected create, replace, or destroy.
4. Creating the scheduler, enabling the queue, and enabling the FastAPI feature do not themselves
   launch a GPU. The GPU starts only after a real job reaches AWS Batch.
5. A running Batch job can continue after the queue is disabled. Terminate that exact job if an
   emergency stop is required.
6. Run the shutdown phase even when the test fails.
7. Use only one small, normal source image for this test. Do not use the blocked image, an
   annotated image, an earlier pipeline result, or a multi-panel debug collage.
8. Do not submit a second job or retry a failed job during this run.

The expected starting and ending safety state is:

```text
FastAPI watermark flags: false
Cloud Scheduler: paused or absent
AWS submission gate: false
AWS Batch queue: DISABLED
AWS Batch compute environment: DISABLED
AWS Batch desired vCPUs: 0
active GPU EC2 instances: 0
active Batch jobs: 0
```

## What you edit and what you run

Edit this file:

```text
/home/maaz/Desktop/watermarkPoc/infra/aws/environments/staging.tfvars
```

Open it with:

```bash
nano /home/maaz/Desktop/watermarkPoc/infra/aws/environments/staging.tfvars
```

Do **not** type the file path by itself in the terminal. A `.tfvars` file is Terraform input, not a
shell program.

If it was previously made executable, restore normal permissions:

```bash
chmod 664 /home/maaz/Desktop/watermarkPoc/infra/aws/environments/staging.tfvars
```

Everything else shown in a `bash` block is a terminal command.

---

## Phase 0: preflight

### 0.1 Open a dedicated terminal

Use one terminal for the entire test. These exported values exist only in this terminal. If the
terminal closes or you open a new one, repeat this entire block before continuing.

Disable shell `errexit` for this interactive session so an ordinary command failure is reported
without closing the terminal:

```bash
set +e

export GCP_PROJECT_ID="project-5909a738-2fdc-451e-ad6"
export GCP_REGION="us-central1"
export API_SERVICE="infringementdetection"
export API_BASE_URL="https://infringementdetection-834057675927.us-central1.run.app"
export CONTROL_PLANE_JOB="watermark-control-plane"
export ORCHESTRATOR_JOB="watermark-aws-orchestrator"
export SCHEDULER_JOB="watermark-control-plane-every-minute"
export JOB_SERVICE_ACCOUNT="watermark-aws-orchestrator@project-5909a738-2fdc-451e-ad6.iam.gserviceaccount.com"

export AWS_REGION="eu-north-1"
export AWS_BATCH_QUEUE="watermark-staging-gpu"
export AWS_COMPUTE_ENVIRONMENT="watermark-staging-gpu"
export AWS_SUBMISSION_FUNCTION="watermark-staging-submission"

export AWS_TF_ROOT="/home/maaz/Desktop/watermarkPoc/infra/aws"
export AWS_TFVARS="${AWS_TF_ROOT}/environments/staging.tfvars"
export TEST_IMAGE="/home/maaz/Desktop/watermarkPoc/images/a10-source.png"
```

Before running the test, place one ordinary PNG photograph at `TEST_IMAGE`. It should contain a
watermark that is safe to remove and should not already contain masks, annotations, intermediate
stages, or before/after panels.

Confirm the test image exists and inspect it before enabling anything:

```bash
if [[ -f "${TEST_IMAGE}" ]]; then
  file "${TEST_IMAGE}"
  echo "Inspect this exact source before continuing: ${TEST_IMAGE}"
else
  echo "STOP: add one normal PNG source image at ${TEST_IMAGE}"
fi
```

### 0.2 Confirm both cloud identities

```bash
rtk proxy gcloud auth list --filter=status:ACTIVE
rtk proxy gcloud config get-value project
rtk proxy aws sts get-caller-identity
```

The AWS account must be:

```text
154959837939
```

If AWS authentication is expired:

```bash
rtk aws login
rtk proxy aws sts get-caller-identity
```

If the active GCP project is different, pass the explicit `--project` values from this runbook.
Do not silently operate on another project.

### 0.3 Confirm the starting safe state

Check the local Terraform switches:

```bash
rtk proxy grep -E '^(submission_enabled|batch_queue_enabled)' "${AWS_TFVARS}"
```

Both must be `false`.

Check AWS:

```bash
rtk proxy aws lambda get-function-configuration \
  --function-name "${AWS_SUBMISSION_FUNCTION}" \
  --region "${AWS_REGION}" \
  --query '{submission:Environment.Variables.SUBMISSION_ENABLED,status:LastUpdateStatus}' \
  --output json

rtk proxy aws batch describe-job-queues \
  --job-queues "${AWS_BATCH_QUEUE}" \
  --region "${AWS_REGION}" \
  --query 'jobQueues[0].{state:state,status:status}' \
  --output json

rtk proxy aws batch describe-compute-environments \
  --compute-environments "${AWS_COMPUTE_ENVIRONMENT}" \
  --region "${AWS_REGION}" \
  --query 'computeEnvironments[0].{state:state,status:status,min:computeResources.minvCpus,desired:computeResources.desiredvCpus,max:computeResources.maxvCpus}' \
  --output json

rtk proxy aws ec2 describe-instances \
  --region "${AWS_REGION}" \
  --filters Name=instance-state-name,Values=pending,running,stopping,stopped \
  --query 'Reservations[].Instances[].{id:InstanceId,state:State.Name,type:InstanceType}' \
  --output json
```

Check every active Batch status:

```bash
for status in SUBMITTED PENDING RUNNABLE STARTING RUNNING; do
  echo "=== ${status} ==="
  rtk proxy aws batch list-jobs \
    --job-queue "${AWS_BATCH_QUEUE}" \
    --job-status "${status}" \
    --region "${AWS_REGION}" \
    --query 'jobSummaryList[].{id:jobId,name:jobName,status:status}' \
    --output json
done
```

Required result:

```text
Lambda submission: false
queue: DISABLED and VALID
compute environment: DISABLED and VALID
min: 0
desired: 0
EC2 list: []
all active Batch lists: []
```

Confirm FastAPI is healthy:

```bash
rtk proxy curl --fail --silent --show-error "${API_BASE_URL}/health" | rtk proxy jq
```

### Checkpoint 0

Do not continue unless:

- [ ] both cloud identities are correct;
- [ ] the AWS account is `154959837939`;
- [ ] the test image exists;
- [ ] FastAPI health succeeds;
- [ ] both Terraform switches are false;
- [ ] AWS submission, queue, and compute environment are disabled;
- [ ] desired GPU capacity is zero; and
- [ ] no active Batch job or EC2 instance exists.

---

## Step 8: create the one-minute scheduler in a paused state

The scheduler triggers one short control-plane pass each minute. It does not run continuously and
does not contain GPU code.

### 8.1 Enable the Cloud Scheduler API

```bash
rtk proxy gcloud services enable cloudscheduler.googleapis.com \
  --project "${GCP_PROJECT_ID}"
```

### 8.2 Allow the scheduler identity to run the control-plane job

```bash
rtk proxy gcloud run jobs add-iam-policy-binding "${CONTROL_PLANE_JOB}" \
  --project "${GCP_PROJECT_ID}" \
  --region "${GCP_REGION}" \
  --member "serviceAccount:${JOB_SERVICE_ACCOUNT}" \
  --role "roles/run.invoker"
```

### 8.3 Create or inspect the scheduler

First check whether it already exists:

```bash
if rtk proxy gcloud scheduler jobs describe "${SCHEDULER_JOB}" \
  --project "${GCP_PROJECT_ID}" \
  --location "${GCP_REGION}" >/dev/null 2>&1; then
  echo "Scheduler already exists"
else
  rtk proxy gcloud scheduler jobs create http "${SCHEDULER_JOB}" \
    --project "${GCP_PROJECT_ID}" \
    --location "${GCP_REGION}" \
    --schedule "* * * * *" \
    --time-zone "Etc/UTC" \
    --uri "https://run.googleapis.com/v2/projects/${GCP_PROJECT_ID}/locations/${GCP_REGION}/jobs/${CONTROL_PLANE_JOB}:run" \
    --http-method POST \
    --headers "Content-Type=application/json" \
    --message-body '{}' \
    --oauth-service-account-email "${JOB_SERVICE_ACCOUNT}" \
    --oauth-token-scope "https://www.googleapis.com/auth/cloud-platform" \
    --attempt-deadline "60s" \
    --max-retry-attempts "0"
fi
```

Pause it immediately:

```bash
rtk proxy gcloud scheduler jobs pause "${SCHEDULER_JOB}" \
  --project "${GCP_PROJECT_ID}" \
  --location "${GCP_REGION}"
```

Inspect it:

```bash
rtk proxy gcloud scheduler jobs describe "${SCHEDULER_JOB}" \
  --project "${GCP_PROJECT_ID}" \
  --location "${GCP_REGION}" \
  --format='yaml(name,state,schedule,timeZone,httpTarget.uri,httpTarget.oauthToken.serviceAccountEmail,retryConfig)'
```

Required result:

```text
state: PAUSED
schedule: '* * * * *'
target: watermark-control-plane:run
service account: watermark-aws-orchestrator@...
max retries: 0
```

### Checkpoint 8

- [ ] The scheduler exists.
- [ ] It is `PAUSED`.
- [ ] It targets `watermark-control-plane`.
- [ ] It uses the expected service account.
- [ ] No GPU or Batch job was created.

You can safely stop here and return later.

---

## Step 9: open the controlled test window

Perform this phase only when you are ready to submit the image in Step 10.

### 9.1 Enable AWS in the Terraform configuration

Open the configuration:

```bash
nano "${AWS_TFVARS}"
```

Change only:

```hcl
submission_enabled  = true
batch_queue_enabled = true
```

Save with `Ctrl+O`, press Enter, and exit with `Ctrl+X`.

Confirm:

```bash
rtk proxy grep -E '^(submission_enabled|batch_queue_enabled)' "${AWS_TFVARS}"
```

### 9.2 Create and validate a fresh Terraform plan

Remove an old saved plan, if present:

```bash
rm -f /tmp/watermark-enable.tfplan
```

Then:

```bash
cd "${AWS_TF_ROOT}"
rtk terraform fmt -check -recursive
rtk terraform validate
rtk terraform plan \
  -var-file=environments/staging.tfvars \
  -out=/tmp/watermark-enable.tfplan
```

If `plan` prints any error, **stop**. Do not run `terraform apply`.

Confirm the saved plan is complete and applyable:

```bash
rtk terraform show -json /tmp/watermark-enable.tfplan \
  | rtk proxy jq '{errored,complete,applyable}'
```

Required:

```json
{
  "errored": false,
  "complete": true,
  "applyable": true
}
```

Review the human-readable plan:

```bash
rtk terraform show /tmp/watermark-enable.tfplan
```

Expected changes are in-place updates for:

- the Batch queue, `DISABLED -> ENABLED`;
- the Batch compute environment, `DISABLED -> ENABLED`;
- Lambda `SUBMISSION_ENABLED`, `false -> true`; and
- a derived IAM policy document if Terraform recalculates it.

The plan must not create an EC2 instance. Stop if the plan shows:

- any destroy;
- any replacement;
- an unexpected new resource; or
- a change outside the expected AWS GPU submission stack.

### 9.3 Apply the reviewed plan

```bash
rtk terraform apply /tmp/watermark-enable.tfplan
```

Verify AWS:

```bash
rtk proxy aws lambda get-function-configuration \
  --function-name "${AWS_SUBMISSION_FUNCTION}" \
  --region "${AWS_REGION}" \
  --query '{submission:Environment.Variables.SUBMISSION_ENABLED,status:LastUpdateStatus}' \
  --output json

rtk proxy aws batch describe-job-queues \
  --job-queues "${AWS_BATCH_QUEUE}" \
  --region "${AWS_REGION}" \
  --query 'jobQueues[0].{state:state,status:status}' \
  --output json

rtk proxy aws batch describe-compute-environments \
  --compute-environments "${AWS_COMPUTE_ENVIRONMENT}" \
  --region "${AWS_REGION}" \
  --query 'computeEnvironments[0].{state:state,status:status,min:computeResources.minvCpus,desired:computeResources.desiredvCpus,max:computeResources.maxvCpus}' \
  --output json
```

Required before any submission:

```text
Lambda submission: true
queue: ENABLED and VALID
compute environment: ENABLED and VALID
min: 0
desired: 0
```

An empty EC2 list is still expected because no job has been submitted.

### 9.4 Enable the two FastAPI feature flags

```bash
rtk proxy gcloud run services update "${API_SERVICE}" \
  --project "${GCP_PROJECT_ID}" \
  --region "${GCP_REGION}" \
  --update-env-vars "WATERMARK_ASSET_UPLOAD_ENABLED=true,WATERMARK_FEATURE_ENABLED=true"
```

Wait for the deployment command to finish, then verify only the two non-secret flags:

```bash
rtk proxy gcloud run services describe "${API_SERVICE}" \
  --project "${GCP_PROJECT_ID}" \
  --region "${GCP_REGION}" \
  --format=json \
  | rtk proxy jq '[
      .spec.template.spec.containers[0].env[]
      | select(.name == "WATERMARK_ASSET_UPLOAD_ENABLED"
            or .name == "WATERMARK_FEATURE_ENABLED")
      | {name, value}
    ]'
```

Both values must be `"true"`.

Check health again:

```bash
rtk proxy curl --fail --silent --show-error "${API_BASE_URL}/health" | rtk proxy jq
```

### 9.5 Resume the scheduler last

```bash
rtk proxy gcloud scheduler jobs resume "${SCHEDULER_JOB}" \
  --project "${GCP_PROJECT_ID}" \
  --location "${GCP_REGION}"
```

Verify:

```bash
rtk proxy gcloud scheduler jobs describe "${SCHEDULER_JOB}" \
  --project "${GCP_PROJECT_ID}" \
  --location "${GCP_REGION}" \
  --format='value(state)'
```

Required result:

```text
ENABLED
```

### Checkpoint 9

- [ ] Terraform plan was complete, applyable, and reviewed.
- [ ] No resource was unexpectedly created, replaced, or destroyed.
- [ ] AWS Lambda, queue, and compute environment are enabled.
- [ ] AWS desired vCPUs remain zero.
- [ ] FastAPI health succeeds and both feature flags are true.
- [ ] The scheduler is enabled.

Proceed directly to Step 10. Do not leave the controlled window open overnight.

---

## Step 10: run one real end-to-end test

### 10.1 Log in without placing the password in shell history

```bash
export LOGIN_RESPONSE_FILE="/tmp/watermark-login-response.json"

read -r -p "FastAPI email: " FASTAPI_EMAIL
read -r -s -p "FastAPI password: " FASTAPI_PASSWORD
echo

LOGIN_HTTP_STATUS="$(
  rtk proxy curl --silent --show-error \
    --output "${LOGIN_RESPONSE_FILE}" \
    --write-out "%{http_code}" \
    --request POST \
    --header "Content-Type: application/json" \
    --data "$(rtk proxy jq -n \
      --arg email "${FASTAPI_EMAIL}" \
      --arg password "${FASTAPI_PASSWORD}" \
      '{email:$email,password:$password}')" \
    "${API_BASE_URL}/api/v1/auth/login"
)"

unset FASTAPI_PASSWORD
echo "HTTP status: ${LOGIN_HTTP_STATUS}"

if [[ -f "${LOGIN_RESPONSE_FILE}" ]]; then
  rtk proxy jq . "${LOGIN_RESPONSE_FILE}" 2>/dev/null \
    || rtk proxy sed -n '1,120p' "${LOGIN_RESPONSE_FILE}"
else
  echo "No response file was created. Check API_BASE_URL and network connectivity."
fi

ACCESS_TOKEN=""
if [[ "${LOGIN_HTTP_STATUS}" == "200" && -f "${LOGIN_RESPONSE_FILE}" ]]; then
  ACCESS_TOKEN="$(
    rtk proxy jq -r '.access_token // empty' "${LOGIN_RESPONSE_FILE}"
  )"
fi

if [[ -n "${ACCESS_TOKEN}" ]]; then
  export ACCESS_TOKEN
  echo "Login succeeded; access token stored without printing it."
else
  unset ACCESS_TOKEN
  echo "STOP: login failed. Fix the response shown above before continuing."
fi
```

Confirm the token works without printing it:

```bash
rtk proxy curl --fail --silent --show-error \
  --header "Authorization: Bearer ${ACCESS_TOKEN}" \
  "${API_BASE_URL}/api/v1/auth/me" \
  | rtk proxy jq '{id,email,account:{id:.account.id,name:.account.name,role:.account.role}}'
```

### 10.2 Upload exactly one image

First confirm that the variables still exist in the current terminal:

```bash
echo "API_BASE_URL=${API_BASE_URL}"
echo "TEST_IMAGE=${TEST_IMAGE}"
test -f "${TEST_IMAGE}" && echo "Image exists" || echo "STOP: image is missing"
test -n "${ACCESS_TOKEN}" && echo "Access token exists" || echo "STOP: log in again"
```

Do not continue unless the URL is populated, the image exists, and the token exists.

```bash
if [[ -z "${API_BASE_URL}" || ! -f "${TEST_IMAGE}" || -z "${ACCESS_TOKEN}" ]]; then
  unset ASSET_ID
  echo "STOP: URL, image, or token is missing. Repeat Phase 0 and login as needed."
else
export UPLOAD_RESPONSE_FILE="/tmp/watermark-upload-response.json"

UPLOAD_HTTP_STATUS="$(
  rtk proxy curl --silent --show-error \
    --output "${UPLOAD_RESPONSE_FILE}" \
    --write-out "%{http_code}" \
    --request POST \
    --header "Authorization: Bearer ${ACCESS_TOKEN}" \
    --form "file=@${TEST_IMAGE};type=image/png" \
    "${API_BASE_URL}/api/v1/watermark-assets"
)"

echo "HTTP status: ${UPLOAD_HTTP_STATUS}"

if [[ -f "${UPLOAD_RESPONSE_FILE}" ]]; then
  rtk proxy jq . "${UPLOAD_RESPONSE_FILE}" 2>/dev/null \
    || rtk proxy sed -n '1,120p' "${UPLOAD_RESPONSE_FILE}"
else
  echo "No response file was created. Check API_BASE_URL, TEST_IMAGE, and connectivity."
fi

ASSET_ID=""
if [[ "${UPLOAD_HTTP_STATUS}" == "201" && -f "${UPLOAD_RESPONSE_FILE}" ]]; then
  ASSET_ID="$(
    rtk proxy jq -r '.asset_id // empty' "${UPLOAD_RESPONSE_FILE}"
  )"
fi

if [[ -n "${ASSET_ID}" ]]; then
  export ASSET_ID
  echo "Upload succeeded: ASSET_ID=${ASSET_ID}"
else
  unset ASSET_ID
  echo "STOP: upload failed. Fix the response shown above before continuing."
fi
fi
```

This creates a registered asset in GCS and PostgreSQL. It does not yet launch a GPU.

### 10.3 Create one watermark job

Create a unique idempotency key:

```bash
export IDEMPOTENCY_KEY="a10-one-image-$(date -u +%Y%m%dT%H%M%SZ)"
```

Submit the one asset:

```bash
if [[ -z "${ASSET_ID}" || -z "${ACCESS_TOKEN}" || -z "${API_BASE_URL}" ]]; then
  unset JOB_ID AWS_JOB_NAME
  echo "STOP: ASSET_ID, access token, or API_BASE_URL is missing."
  echo "Complete the earlier steps; no job request was sent."
else
echo "Using ASSET_ID=${ASSET_ID}"

export JOB_RESPONSE_FILE="/tmp/watermark-job-response.json"

JOB_HTTP_STATUS="$(
  rtk proxy curl --silent --show-error \
    --output "${JOB_RESPONSE_FILE}" \
    --write-out "%{http_code}" \
    --request POST \
    --header "Authorization: Bearer ${ACCESS_TOKEN}" \
    --header "Content-Type: application/json" \
    --header "Idempotency-Key: ${IDEMPOTENCY_KEY}" \
    --data "$(rtk proxy jq -n \
      --argjson asset_id "${ASSET_ID}" \
      '{asset_ids:[$asset_id]}')" \
    "${API_BASE_URL}/api/v1/watermark-jobs"
)"

echo "HTTP status: ${JOB_HTTP_STATUS}"

if [[ -f "${JOB_RESPONSE_FILE}" ]]; then
  rtk proxy jq . "${JOB_RESPONSE_FILE}" 2>/dev/null \
    || rtk proxy sed -n '1,120p' "${JOB_RESPONSE_FILE}"
else
  echo "No response file was created. Check the URL, token, and connectivity."
fi

JOB_ID=""
if [[ "${JOB_HTTP_STATUS}" == "202" && -f "${JOB_RESPONSE_FILE}" ]]; then
  JOB_ID="$(
    rtk proxy jq -r '.job_id // empty' "${JOB_RESPONSE_FILE}"
  )"
fi

if [[ -n "${JOB_ID}" ]]; then
  export JOB_ID
  export AWS_JOB_NAME="watermark-${JOB_ID}"
  echo "Keep these values:"
  echo "ASSET_ID=${ASSET_ID}"
  echo "JOB_ID=${JOB_ID}"
  echo "IDEMPOTENCY_KEY=${IDEMPOTENCY_KEY}"
else
  unset JOB_ID AWS_JOB_NAME
  echo "STOP: job creation failed. Do not create another idempotency key."
  echo "Fix the error and reuse IDEMPOTENCY_KEY=${IDEMPOTENCY_KEY}."
fi
fi
```

Expected initial response:

```text
job_id: wm_...
status: QUEUED
total_items: 1
```

Do not repeat the request with a new idempotency key.

### 10.4 Poll application status

Run this whenever you want the current status:

```bash
rtk proxy curl --fail --silent --show-error \
  --header "Authorization: Bearer ${ACCESS_TOKEN}" \
  "${API_BASE_URL}/api/v1/watermark-jobs/${JOB_ID}" \
  | rtk proxy jq
```

Or poll every 30 seconds:

```bash
while true; do
  RESPONSE="$(
    rtk proxy curl --fail --silent --show-error \
      --header "Authorization: Bearer ${ACCESS_TOKEN}" \
      "${API_BASE_URL}/api/v1/watermark-jobs/${JOB_ID}"
  )"
  STATUS="$(rtk proxy jq -r '.status' <<<"${RESPONSE}")"
  printf '%s  %s\n' "$(date -u +%FT%TZ)" "${STATUS}"
  case "${STATUS}" in
    SUCCEEDED|FAILED|CANCELLED) break ;;
  esac
  sleep 30
done
```

Normal intermediate states may include:

```text
QUEUED
ORCHESTRATING
PROVISIONING
RUNNING
AWAITING_OUTPUT
SUCCEEDED
```

Fast transitions can cause some states to be skipped when polling.

### 10.5 Observe the GCP control plane

In another terminal, repeat the complete Phase 0.1 export block. Then restore the identifiers
printed by Step 10.3. Replace the example values below with the values from your run:

```bash
export ASSET_ID="replace-with-the-numeric-asset-id"
export JOB_ID="replace-with-wm_job_id"
export IDEMPOTENCY_KEY="replace-with-the-original-idempotency-key"
export AWS_JOB_NAME="watermark-${JOB_ID}"

echo "JOB_ID=${JOB_ID}"
echo "AWS_JOB_NAME=${AWS_JOB_NAME}"
```

Do not continue in the second terminal if `JOB_ID` still contains `replace-with` or is empty.

Observe GCP:

```bash
rtk proxy gcloud run jobs executions list \
  --job "${CONTROL_PLANE_JOB}" \
  --project "${GCP_PROJECT_ID}" \
  --region "${GCP_REGION}" \
  --limit 10

rtk proxy gcloud run jobs executions list \
  --job "${ORCHESTRATOR_JOB}" \
  --project "${GCP_PROJECT_ID}" \
  --region "${GCP_REGION}" \
  --limit 10
```

The control plane should find the durable intent and launch the short orchestrator job. The
orchestrator should submit exactly one authenticated request to AWS.

### 10.6 Observe AWS Batch and the temporary GPU

The AWS Batch job name is deterministically `watermark-${JOB_ID}`. Re-derive it in every new
terminal instead of assuming an earlier export survived:

```bash
if [[ -z "${JOB_ID:-}" ]]; then
  unset AWS_JOB_NAME
  echo "STOP: JOB_ID is empty. Restore the Step 10.3 value first."
else
  export AWS_JOB_NAME="watermark-${JOB_ID}"
  echo "AWS_JOB_NAME=${AWS_JOB_NAME}"
fi
```

Only run the lookup when the printed value resembles
`watermark-wm_10f180f933f046c3b4671eebbb02782b`:

```bash
if [[ "${AWS_JOB_NAME:-}" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$ ]]; then
  rtk proxy aws batch list-jobs \
    --job-queue "${AWS_BATCH_QUEUE}" \
    --filters "name=JOB_NAME,values=${AWS_JOB_NAME}" \
    --region "${AWS_REGION}" \
    --query 'jobSummaryList[].{id:jobId,name:jobName,status:status,createdAt:createdAt}' \
    --output json
else
  echo "STOP: AWS_JOB_NAME is empty or invalid; do not call AWS Batch with it."
fi
```

An empty list is normal while FastAPI is still `QUEUED` or `ORCHESTRATING`. Wait for the
orchestrator execution rather than submitting another application job.

When the job appears, save the exact provider job ID:

```bash
PROVIDER_JOB_ID="$(
  rtk proxy aws batch list-jobs \
    --job-queue "${AWS_BATCH_QUEUE}" \
    --filters "name=JOB_NAME,values=${AWS_JOB_NAME}" \
    --region "${AWS_REGION}" \
    --query 'jobSummaryList[0].jobId' \
    --output text
)"

if [[ -n "${PROVIDER_JOB_ID}" && "${PROVIDER_JOB_ID}" != "None" ]]; then
  export PROVIDER_JOB_ID
  echo "PROVIDER_JOB_ID=${PROVIDER_JOB_ID}"
else
  unset PROVIDER_JOB_ID
  echo "Provider job is not visible yet; wait and rerun this lookup."
fi
```

Do not run `describe-jobs` until `PROVIDER_JOB_ID` is populated:

```bash
if [[ -n "${PROVIDER_JOB_ID:-}" ]]; then
rtk proxy aws batch describe-jobs \
  --jobs "${PROVIDER_JOB_ID}" \
  --region "${AWS_REGION}" \
  --query 'jobs[0].{status:status,statusReason:statusReason,createdAt:createdAt,startedAt:startedAt,stoppedAt:stoppedAt,exitCode:container.exitCode,reason:container.reason,logStream:container.logStreamName}' \
  --output json
else
  echo "Provider job is not available to describe yet."
fi
```

Expected AWS lifecycle:

```text
SUBMITTED -> PENDING -> RUNNABLE -> STARTING -> RUNNING -> SUCCEEDED
```

Watch compute and EC2:

```bash
rtk proxy aws batch describe-compute-environments \
  --compute-environments "${AWS_COMPUTE_ENVIRONMENT}" \
  --region "${AWS_REGION}" \
  --query 'computeEnvironments[0].{state:state,status:status,desired:computeResources.desiredvCpus}' \
  --output json

rtk proxy aws ec2 describe-instances \
  --region "${AWS_REGION}" \
  --filters Name=instance-state-name,Values=pending,running \
  --query 'Reservations[].Instances[].{id:InstanceId,state:State.Name,type:InstanceType,launchTime:LaunchTime}' \
  --output json
```

During the run, the expected peak is one `g4dn.xlarge` with four desired vCPUs. This is the period
that incurs GPU compute cost.

Once the Batch description contains a `logStream` value:

```bash
LOG_STREAM="$(
  rtk proxy aws batch describe-jobs \
    --jobs "${PROVIDER_JOB_ID}" \
    --region "${AWS_REGION}" \
    --query 'jobs[0].container.logStreamName' \
    --output text
)"

if [[ -n "${LOG_STREAM}" && "${LOG_STREAM}" != "None" ]]; then
  export LOG_STREAM
rtk proxy aws logs get-log-events \
  --log-group-name "/aws/batch/watermark-staging-worker" \
  --log-stream-name "${LOG_STREAM}" \
  --region "${AWS_REGION}" \
  --no-start-from-head \
  --limit 200 \
  --query 'events[].message' \
  --output text
else
  unset LOG_STREAM
  echo "The Batch log stream is not available yet."
fi
```

Useful successful worker events include:

```text
job_started
item_completed
job_completed
```

### 10.7 Confirm the application result

Return to the original terminal that still contains `ACCESS_TOKEN`. If that terminal was closed,
repeat Phase 0.1 and Step 10.1 to obtain a fresh token, then restore the existing `JOB_ID`. Do not
create another job.

After the application reaches `SUCCEEDED`, save and inspect the item response:

```bash
export ITEMS_RESPONSE_FILE="/tmp/${JOB_ID}-items.json"

rtk proxy curl --fail --silent --show-error \
  --header "Authorization: Bearer ${ACCESS_TOKEN}" \
  --output "${ITEMS_RESPONSE_FILE}" \
  "${API_BASE_URL}/api/v1/watermark-jobs/${JOB_ID}/items"

rtk proxy jq . "${ITEMS_RESPONSE_FILE}"
```

Require:

```text
item_id: asset_1
status: SUCCEEDED
result_available: true
```

Derive the item ID from the authenticated response rather than hardcoding `asset_1`:

```bash
RESULT_ITEM_ID="$(
  rtk proxy jq -r \
    '[.items[] | select(.status == "SUCCEEDED" and .result_available == true)][0].item_id // empty' \
    "${ITEMS_RESPONSE_FILE}"
)"

if [[ -n "${RESULT_ITEM_ID}" ]]; then
  export RESULT_ITEM_ID
  echo "RESULT_ITEM_ID=${RESULT_ITEM_ID}"
else
  unset RESULT_ITEM_ID
  echo "STOP: the API did not report a successful downloadable item."
fi
```

Download through the authorized FastAPI endpoint:

```bash
if [[ -z "${RESULT_ITEM_ID:-}" ]]; then
  unset RESULT_FILE
  echo "STOP: RESULT_ITEM_ID is missing; do not call the result endpoint."
else
  export RESULT_FILE="/tmp/${JOB_ID}-${RESULT_ITEM_ID}.png"

rtk proxy curl --fail --silent --show-error \
  --header "Authorization: Bearer ${ACCESS_TOKEN}" \
  --output "${RESULT_FILE}" \
  "${API_BASE_URL}/api/v1/watermark-jobs/${JOB_ID}/items/${RESULT_ITEM_ID}/result"

file "${RESULT_FILE}"
ls -lh "${RESULT_FILE}"
sha256sum "${TEST_IMAGE}" "${RESULT_FILE}"
fi
```

The API response must not expose an AWS ARN, AWS job ID, unrestricted GCS URI, or cloud token.
The source and result checksums should normally differ after inpainting.

Open the source and result locally and inspect them:

```bash
xdg-open "${TEST_IMAGE}" >/dev/null 2>&1 &
xdg-open "${RESULT_FILE}" >/dev/null 2>&1 &
```

Acceptance requires more than a valid PNG:

- the result opens without corruption;
- its dimensions are appropriate for the source;
- the intended watermark region was plausibly removed;
- unrelated image content was not blanked, replaced, or severely damaged; and
- no debug collage or intermediate artifact was accidentally used as the source.

If the API and worker say `SUCCEEDED` but the image is mostly blank or clearly damaged, record an
**image-quality failure**. The infrastructure path worked, but the functional test did not pass.

### Checkpoint 10

- [ ] Exactly one application job was created.
- [ ] Exactly one AWS Batch job was created.
- [ ] At most one temporary `g4dn.xlarge` appeared.
- [ ] The worker reached a terminal status.
- [ ] FastAPI reached `SUCCEEDED`, `FAILED`, or `CANCELLED`.
- [ ] On success, the item ID returned by FastAPI is downloadable through FastAPI.
- [ ] The downloaded file is a valid image and was visually compared with the normal source.
- [ ] The output preserves unrelated image content and is not mostly blank or corrupted.

Whether this test succeeds or fails, continue to Step 11.

---

## Step 11: close the test window and return to zero GPU capacity

### 11.1 Disable the public FastAPI feature first

```bash
rtk proxy gcloud run services update "${API_SERVICE}" \
  --project "${GCP_PROJECT_ID}" \
  --region "${GCP_REGION}" \
  --update-env-vars "WATERMARK_ASSET_UPLOAD_ENABLED=false,WATERMARK_FEATURE_ENABLED=false"
```

Verify:

```bash
rtk proxy gcloud run services describe "${API_SERVICE}" \
  --project "${GCP_PROJECT_ID}" \
  --region "${GCP_REGION}" \
  --format=json \
  | rtk proxy jq '[
      .spec.template.spec.containers[0].env[]
      | select(.name == "WATERMARK_ASSET_UPLOAD_ENABLED"
            or .name == "WATERMARK_FEATURE_ENABLED")
      | {name, value}
    ]'
```

Both values must be `"false"`.

### 11.2 Pause the scheduler

```bash
rtk proxy gcloud scheduler jobs pause "${SCHEDULER_JOB}" \
  --project "${GCP_PROJECT_ID}" \
  --location "${GCP_REGION}"

rtk proxy gcloud scheduler jobs describe "${SCHEDULER_JOB}" \
  --project "${GCP_PROJECT_ID}" \
  --location "${GCP_REGION}" \
  --format='value(state)'
```

Required result:

```text
PAUSED
```

Wait for any already-started control-plane and orchestrator executions to finish:

```bash
rtk proxy gcloud run jobs executions list \
  --job "${CONTROL_PLANE_JOB}" \
  --project "${GCP_PROJECT_ID}" \
  --region "${GCP_REGION}" \
  --limit 5

rtk proxy gcloud run jobs executions list \
  --job "${ORCHESTRATOR_JOB}" \
  --project "${GCP_PROJECT_ID}" \
  --region "${GCP_REGION}" \
  --limit 5
```

### 11.3 Return the Terraform configuration to safe mode

Open:

```bash
nano "${AWS_TFVARS}"
```

Set:

```hcl
submission_enabled  = false
batch_queue_enabled = false
```

Save and verify:

```bash
rtk proxy grep -E '^(submission_enabled|batch_queue_enabled)' "${AWS_TFVARS}"
```

### 11.4 Plan and apply the shutdown

If AWS authentication expired:

```bash
rtk aws login
rtk proxy aws sts get-caller-identity
```

Create a new shutdown plan:

```bash
rm -f /tmp/watermark-disable.tfplan
cd "${AWS_TF_ROOT}"

rtk terraform plan \
  -var-file=environments/staging.tfvars \
  -out=/tmp/watermark-disable.tfplan
```

Confirm it is complete and applyable:

```bash
rtk terraform show -json /tmp/watermark-disable.tfplan \
  | rtk proxy jq '{errored,complete,applyable}'
```

Review it:

```bash
rtk terraform show /tmp/watermark-disable.tfplan
```

Expected in-place changes:

```text
Lambda submission: true -> false
Batch queue: ENABLED -> DISABLED
Batch compute environment: ENABLED -> DISABLED
```

There must be no unexpected create, replace, or destroy.

Apply:

```bash
rtk terraform apply /tmp/watermark-disable.tfplan
```

Remove the temporary plans:

```bash
rm -f /tmp/watermark-enable.tfplan /tmp/watermark-disable.tfplan
```

### 11.5 Prove the final safe state

```bash
rtk proxy aws lambda get-function-configuration \
  --function-name "${AWS_SUBMISSION_FUNCTION}" \
  --region "${AWS_REGION}" \
  --query '{submission:Environment.Variables.SUBMISSION_ENABLED,status:LastUpdateStatus}' \
  --output json

rtk proxy aws batch describe-job-queues \
  --job-queues "${AWS_BATCH_QUEUE}" \
  --region "${AWS_REGION}" \
  --query 'jobQueues[0].{state:state,status:status}' \
  --output json

rtk proxy aws batch describe-compute-environments \
  --compute-environments "${AWS_COMPUTE_ENVIRONMENT}" \
  --region "${AWS_REGION}" \
  --query 'computeEnvironments[0].{state:state,status:status,min:computeResources.minvCpus,desired:computeResources.desiredvCpus,max:computeResources.maxvCpus}' \
  --output json
```

Check active jobs again:

```bash
for status in SUBMITTED PENDING RUNNABLE STARTING RUNNING; do
  echo "=== ${status} ==="
  rtk proxy aws batch list-jobs \
    --job-queue "${AWS_BATCH_QUEUE}" \
    --job-status "${status}" \
    --region "${AWS_REGION}" \
    --query 'jobSummaryList[].{id:jobId,name:jobName,status:status}' \
    --output json
done
```

Check EC2:

```bash
rtk proxy aws ec2 describe-instances \
  --region "${AWS_REGION}" \
  --filters Name=instance-state-name,Values=pending,running,stopping,stopped \
  --query 'Reservations[].Instances[].{id:InstanceId,state:State.Name,type:InstanceType}' \
  --output json
```

AWS Batch scale-down can take several minutes after the worker exits. Repeat the compute and EC2
checks until:

```text
submission: false
queue: DISABLED and VALID
compute environment: DISABLED and VALID
min: 0
desired: 0
all active Batch lists: []
EC2 list: []
```

Finally prove Terraform agrees with the safe configuration:

```bash
cd "${AWS_TF_ROOT}"
rtk terraform plan -var-file=environments/staging.tfvars -detailed-exitcode
```

Required result:

```text
No changes
exit code 0
```

Clear the bearer token:

```bash
unset ACCESS_TOKEN
```

### Checkpoint 11

- [ ] FastAPI watermark flags are false.
- [ ] Scheduler is paused.
- [ ] AWS submission is false.
- [ ] Batch queue and compute environment are disabled and valid.
- [ ] Batch minimum and desired vCPUs are zero.
- [ ] No active Batch job remains.
- [ ] No GPU EC2 instance remains.
- [ ] Terraform reports no changes.

API Gateway, Lambda, DynamoDB, ECR, Batch definitions, Cloud Run Job definitions, and the paused
scheduler can remain provisioned. They do not represent a running GPU.

---

## Emergency stop

Use this if a second job appears, scope is uncertain, or the current GPU job must be aborted.

### E1. Stop new GCP work

```bash
rtk proxy gcloud scheduler jobs pause "${SCHEDULER_JOB}" \
  --project "${GCP_PROJECT_ID}" \
  --location "${GCP_REGION}"

rtk proxy gcloud run services update "${API_SERVICE}" \
  --project "${GCP_PROJECT_ID}" \
  --region "${GCP_REGION}" \
  --update-env-vars "WATERMARK_ASSET_UPLOAD_ENABLED=false,WATERMARK_FEATURE_ENABLED=false"
```

### E2. Close the AWS submission gate immediately

Preserve every existing Lambda environment variable while changing only
`SUBMISSION_ENABLED=false`:

```bash
rtk proxy bash -lc 'set -euo pipefail
SAFE_ENV=$(rtk proxy aws lambda get-function-configuration \
  --function-name watermark-staging-submission \
  --region eu-north-1 \
  --query Environment \
  --output json \
  | rtk proxy jq -c '"'"'.Variables.SUBMISSION_ENABLED="false" | {Variables:.Variables}'"'"')
rtk proxy aws lambda update-function-configuration \
  --function-name watermark-staging-submission \
  --region eu-north-1 \
  --environment "${SAFE_ENV}"
unset SAFE_ENV'

rtk proxy aws lambda wait function-updated \
  --function-name "${AWS_SUBMISSION_FUNCTION}" \
  --region "${AWS_REGION}"
```

### E3. Stop AWS from scheduling new work immediately

```bash
rtk proxy aws batch update-job-queue \
  --job-queue "${AWS_BATCH_QUEUE}" \
  --state DISABLED \
  --region "${AWS_REGION}"

rtk proxy aws batch update-compute-environment \
  --compute-environment "${AWS_COMPUTE_ENVIRONMENT}" \
  --state DISABLED \
  --region "${AWS_REGION}"
```

This stops new scheduling but does not terminate a job that is already running.

### E4. Terminate the exact active job when necessary

If `PROVIDER_JOB_ID` is already known:

```bash
rtk proxy aws batch terminate-job \
  --job-id "${PROVIDER_JOB_ID}" \
  --reason "A10 emergency operator stop" \
  --region "${AWS_REGION}"
```

If it is not known, list active jobs and copy the exact ID:

```bash
for status in SUBMITTED PENDING RUNNABLE STARTING RUNNING; do
  rtk proxy aws batch list-jobs \
    --job-queue "${AWS_BATCH_QUEUE}" \
    --job-status "${status}" \
    --region "${AWS_REGION}" \
    --query 'jobSummaryList[].{id:jobId,name:jobName,status:status}' \
    --output table
done
```

Terminate only the intended exact job ID. Then complete Step 11.3 onward so Terraform and the live
state both return to disabled.

---

## Troubleshooting

### The terminal window closed after a failed command

Older copies of this runbook used `exit 1` when login, upload, or job creation did not return an ID.
In an interactive terminal, `exit 1` exits the entire shell and may close the window.

This runbook no longer uses those failure exits. Start a new terminal, repeat the complete Phase
0.1 export block, log in again, and resume from the failed operation. Do not assume exported
variables survived the closed terminal.

You can confirm the safer interactive behavior with:

```bash
set +e
```

### Curl reports `URL rejected: No host part in the URL`

`API_BASE_URL` is empty, normally because a new terminal was opened. Repeat Phase 0.1 and verify:

```bash
echo "API_BASE_URL=${API_BASE_URL}"
```

Do not retry the request until the complete HTTPS URL is printed.

### `staging.tfvars: Permission denied` or `command not found`

Cause: the `.tfvars` file was executed as a command.

Fix:

```bash
chmod 664 /home/maaz/Desktop/watermarkPoc/infra/aws/environments/staging.tfvars
nano /home/maaz/Desktop/watermarkPoc/infra/aws/environments/staging.tfvars
```

Do not execute the file.

### `Cannot apply incomplete plan`

Cause: Terraform plan generation failed, often because AWS authentication expired. The saved plan
is unusable.

Fix:

```bash
rtk aws login
rtk proxy aws sts get-caller-identity
rm -f /tmp/watermark-enable.tfplan /tmp/watermark-disable.tfplan
```

Run the relevant `terraform plan` again and require:

```json
{"errored":false,"complete":true,"applyable":true}
```

Never apply the old plan.

### Terraform says the saved plan is stale

The state changed after the plan was created. Delete the saved plan, generate a fresh one, review
it, and apply only that fresh plan.

### Upload or job creation returns `503`

Confirm both FastAPI flags are true on the live Cloud Run revision. If the configuration is
correct, inspect the Cloud Run service logs. Do not repeatedly submit.

### Login or `/auth/me` returns `401`

Log in again. Access tokens can expire. Do not print or paste the token into a document or chat.

### The application remains `QUEUED`

Check:

1. scheduler state is `ENABLED`;
2. recent `watermark-control-plane` executions;
3. recent `watermark-aws-orchestrator` executions;
4. AWS Lambda submission is true; and
5. the Batch queue and compute environment are enabled and valid.

Do not create a replacement application job.

### Batch is `RUNNABLE` but no EC2 instance appears

GPU capacity provisioning is not instant and regional capacity can be temporarily unavailable.
Inspect the Batch job `statusReason`, compute-environment status, and account quota. Do not increase
maximum capacity or submit a second job during this test.

### AWS Batch succeeds but FastAPI does not reach `SUCCEEDED`

The control plane must observe AWS success and validate the generation-pinned GCS output. Check
recent control-plane executions and GCS/result-validation errors. Do not bypass reconciliation or
mark the database row successful manually.

### The result says `SUCCEEDED` but the image is mostly blank or damaged

This is an image-quality failure, not an infrastructure failure. Confirm that `TEST_IMAGE` was a
normal source photograph and not one of the multi-panel pipeline/debug images in `images/`.

Do not declare the controlled test fully successful based only on status codes. Save the source,
result, job ID, detection count, and worker logs, then complete Step 11.

### `warningCodes` contains `INPAINTING_PENDING` on a successful result

The current worker creates this warning before the inpainting stage and carries it into the
terminal `INPAINTED` result. If `decision` is `INPAINTED`, the final image exists, and the item is
`SUCCEEDED`, this particular warning does not mean the GPU is still running. It is misleading
result metadata that should be fixed separately.

### The test fails

Record:

- `JOB_ID`;
- `PROVIDER_JOB_ID`, if created;
- final FastAPI and Batch statuses;
- Batch `statusReason`, exit code, and log stream; and
- relevant Cloud Run execution names.

Then run the full Step 11 shutdown. Fix the underlying error before starting a new controlled
attempt.

## Completion

Steps 8 through 11 are complete when:

- the paused scheduler exists;
- one small, normal source image has reached a terminal application state;
- success, if achieved, was downloaded through authenticated FastAPI and visually inspected;
- unrelated image content was preserved well enough for the controlled functional test to pass;
- the application and AWS provider created only one job each;
- the GPU instance terminated;
- every submission/feature switch is false;
- the scheduler is paused; and
- Terraform reports no drift from the safe configuration.

Do not start the five-image or broader A10 acceptance tests until this one-image path succeeds and
the final zero-capacity proof passes.
