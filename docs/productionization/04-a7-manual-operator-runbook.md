# A7 one-image GPU acceptance operator runbook

This is the operational source of truth for running A7 manually. It is written for either the
project owner or a new agent with access to this repository, AWS account `154959837939`, and GCP
project `project-5909a738-2fdc-451e-ad6`.

## Objective

A7 proves the complete hybrid path with exactly one image:

```text
authenticated GCP caller
  -> AWS submission API
  -> one AWS Batch job
  -> one temporary g4dn.xlarge / Tesla T4
  -> keyless AWS-to-GCP WIF
  -> read image and models from GCS
  -> background removal
  -> YOLO/SAM2 detection and masks
  -> LaMa inpainting
  -> results and completion markers in GCS
  -> Batch job exits
  -> EC2 and EBS terminate
  -> submission and queue return to disabled
```

A7 is a controlled acceptance run, not a production batch. Submit exactly one new application job.
Every rerun is billable and requires a fresh job ID and isolated GCS prefix.

## Proven baseline

The first successful A7 completed on 2026-07-26:

- Batch job definition: `watermark-staging-worker:3`
- Worker image digest:
  `sha256:596493d0665a053a52861ad0b3b585700c2f329238d01ae4a5583e82c656d95d`
- Result: one item `SUCCEEDED`, decision `INPAINTED`
- GPU: Tesla T4, CUDA capability `7.5`, PyTorch `2.5.1+cu121`
- Final safety state: submission false, queue disabled, desired vCPUs zero, no EC2/EBS
- Evidence:
  [lama-fix-run-outcome.json](../../aws-acceptance/evidence/a7-one-image/lama-fix-run-outcome.json)

This baseline is historical evidence only. Always run the live preflight before another attempt.
Never reuse its application job ID, GCS generations, request hash, or output prefix.

## Non-negotiable safety rules

1. Never submit more than one new A7 job.
2. Never automatically retry a failed or timed-out job.
3. Keep `submission_enabled=false` except at the short submission boundary. Keep
   `batch_queue_enabled=false` except while the one controlled job is being accepted or run.
4. Close the Lambda submission gate immediately after the accepted request and idempotency replay.
5. Do not run the final Terraform cleanup while the job is active. AWS Batch may temporarily own
   `desired_vcpus=4`; wait for a terminal job first.
6. After any outcome, disable the queue and prove zero active jobs, EC2, EBS, and ECS capacity.
7. Never print, save, or paste identity tokens. Generate the Google ID token inside one shell
   process, use it, unset it, and store only the redacted API response.
8. Do not rebuild an image merely to rerun A7. Rebuild only when worker code, dependencies,
   configuration, model manifest, or Dockerfile changed.
9. If any command produces an unexpected plan, account, region, resource, or second provider job
   ID, stop before continuing.

## Constants and per-attempt values

Run this once in each new shell:

```bash
export AWS_REGION=eu-north-1
export AWS_ACCOUNT_ID=154959837939
export GCP_PROJECT_ID=project-5909a738-2fdc-451e-ad6
export GCS_BUCKET=project-5909a738-2fdc-451e-ad6-watermark-data
export ECR_REPOSITORY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/watermark-staging-worker"
export CODEBUILD_PROJECT_NAME=watermark-staging-worker-image-build
export CODEBUILD_SOURCE_BUCKET=watermark-staging-codebuild-source-154959837939
export API_BASE_URL=https://g3qk2f8rsg.execute-api.eu-north-1.amazonaws.com/staging
export ORCHESTRATOR_SERVICE_ACCOUNT=watermark-aws-orchestrator@project-5909a738-2fdc-451e-ad6.iam.gserviceaccount.com
export GOOGLE_TOKEN_AUDIENCE=urn:watermark:aws-gpu-submission:staging
```

Choose a new lowercase identifier. Replace the timestamp manually; do not reuse an earlier value:

```bash
export ATTEMPT_ID=aws_a7_YYYYMMDDtHHMMSSz
export ATTEMPT_SLUG=a7-YYYYMMDDtHHMMSSz
export EVIDENCE_DIR=aws-acceptance/evidence/a7-one-image
export INPUT_MANIFEST_PATH="${EVIDENCE_DIR}/${ATTEMPT_SLUG}-input-manifest.json"
export REQUEST_PATH="${EVIDENCE_DIR}/${ATTEMPT_SLUG}-request-redacted.json"
export GCS_ATTEMPT_ROOT="gs://${GCS_BUCKET}/gpu_acceptance/${ATTEMPT_ID}"
```

The `ATTEMPT_ID` is used by DynamoDB for idempotency. It must be globally new for this environment.

## Run overview

| Phase | Always required? | Starts billable GPU? |
|---|---:|---:|
| 1. Decide whether to rebuild | Yes | No |
| 2. Remote image build and digest deployment | Only after image-affecting changes | No |
| 3. Create fresh GCS input and request | Yes | No |
| 4. Zero-capacity preflight | Yes | No |
| 5. Enable and submit exactly once | Yes | Yes, after Batch scales out |
| 6. Close submission and monitor | Yes | GPU may be running |
| 7. Verify GCS results | Yes | No additional GPU |
| 8. Disable and prove zero capacity | Yes | Stops further GPU cost |

## Phase 1: decide whether an image rebuild is needed

Skip Phase 2 when rerunning the exact deployed worker image without code or image changes.

Run Phase 2 when any of these changed:

- `Dockerfile.gpu`
- `requirements-gpu.lock`
- worker or vendored runtime code under `src/`
- `configs/`
- `model-manifest.json`
- anything else copied into the GPU image

Before a rebuild, run:

```bash
rtk uv lock --check
rtk uv run pytest -q
rtk uv run mypy
rtk uv run ruff check .
rtk git diff --check
```

Do not proceed if a required check fails.

## Phase 2: remotely build and deploy a changed image

### 2.1 Start one CodeBuild build

Use a new immutable ECR tag:

```bash
export BUILD_ID=a7-fix-YYYYMMDD-N
rtk infra/aws/scripts/start-worker-image-build.sh "${BUILD_ID}"
```

The helper uploads a small source ZIP. CodeBuild performs the large Linux AMD64 Docker build inside
AWS, reuses the mutable ECR BuildKit cache, runs a CPU-only import smoke test, and pushes the
release image to the immutable worker repository. It does not start AWS Batch or a GPU.

Save the returned build ID:

```bash
export CODEBUILD_BUILD_ID=watermark-staging-worker-image-build:UUID_FROM_START_BUILD
```

Monitor it:

```bash
rtk aws codebuild batch-get-builds \
  --ids "${CODEBUILD_BUILD_ID}" \
  --region "${AWS_REGION}" \
  --query 'builds[0].{status:buildStatus,phase:currentPhase,logs:logs.deepLink}' \
  --output json
```

Wait for `SUCCEEDED`. If it is `FAILED` or `TIMED_OUT`, inspect the CloudWatch link and stop. Do
not start another build until the specific error is fixed. A historical build was marked failed
after successfully pushing because `ecr:DescribeImages` was missing; that permission is now fixed.

Resolve the release digest:

```bash
export WORKER_IMAGE_DIGEST=$(
  rtk proxy aws ecr describe-images \
    --region "${AWS_REGION}" \
    --repository-name watermark-staging-worker \
    --image-ids "imageTag=${BUILD_ID}" \
    --query 'imageDetails[0].imageDigest' \
    --output text
)
printf '%s\n' "${WORKER_IMAGE_DIGEST}"
```

Require `sha256:` followed by 64 lowercase hexadecimal characters.

### 2.2 Deploy the digest with both gates closed

Edit `infra/aws/environments/staging.tfvars`:

```hcl
worker_image_digest = "sha256:NEW_DIGEST"
submission_enabled  = false
batch_queue_enabled = false
```

Then:

```bash
rtk terraform -chdir=infra/aws fmt -check -recursive
rtk terraform -chdir=infra/aws validate
rtk terraform -chdir=infra/aws plan \
  -var-file=environments/staging.tfvars \
  -out=a7-image-deploy.tfplan
rtk terraform -chdir=infra/aws show a7-image-deploy.tfplan
rtk terraform -chdir=infra/aws apply a7-image-deploy.tfplan
```

The plan may register the next Batch job-definition revision and update Lambda's exact revision
reference/policy. It must keep both gates closed and must not create an EC2 instance.

Confirm the active revision and digest:

```bash
rtk proxy aws batch describe-job-definitions \
  --job-definition-name watermark-staging-worker \
  --status ACTIVE \
  --region "${AWS_REGION}" \
  --query 'sort_by(jobDefinitions,&revision)[-1].{revision:revision,arn:jobDefinitionArn,image:containerProperties.image}' \
  --output json
```

Record the returned revision and use it throughout the attempt.

## Phase 3: create a fresh GCS input and canonical request

### 3.1 Upload the selected image with create-only semantics

The established A7 image is `images/image copy 4.png`. A different image is allowed only when the
acceptance objective explicitly changes.

```bash
rtk sha256sum "images/image copy 4.png"

rtk gcloud storage cp --if-generation-match=0 \
  "images/image copy 4.png" \
  "${GCS_ATTEMPT_ROOT}/asset_1.png"

rtk proxy gcloud storage objects describe \
  "${GCS_ATTEMPT_ROOT}/asset_1.png" \
  --format='json(name,generation,size,updateTime)'
```

Record the exact `generation`. If create-only upload reports that the object exists, the
`ATTEMPT_ID` is not fresh; choose a new ID instead of overwriting anything.

### 3.2 Create the input manifest

Copy the last known-good manifest as a structural template:

```bash
rtk cp \
  "${EVIDENCE_DIR}/lama-fix-input-manifest.json" \
  "${INPUT_MANIFEST_PATH}"
```

Edit the new file and replace every attempt-specific value:

- `jobId` -> `${ATTEMPT_ID}`
- `createdAt` -> current UTC timestamp
- `inputUri` -> `${GCS_ATTEMPT_ROOT}/asset_1.png`
- `inputGeneration` -> generation returned by GCS
- `outputPrefix` -> `${GCS_ATTEMPT_ROOT}/asset_1/`
- `sourceChecksumSha256` -> current local SHA-256

Do not change `tenantId`, `pipelineVersion`, item ID, or schema version for the standard A7 run.

Validate and upload:

```bash
rtk uv run python -m json.tool "${INPUT_MANIFEST_PATH}"
rtk gcloud storage cp --if-generation-match=0 \
  "${INPUT_MANIFEST_PATH}" \
  "${GCS_ATTEMPT_ROOT}/input-manifest.json"
rtk proxy gcloud storage objects describe \
  "${GCS_ATTEMPT_ROOT}/input-manifest.json" \
  --format='json(name,generation,size,updateTime)'
```

Record the manifest generation.

### 3.3 Create and hash the submission request

Copy the last known-good request:

```bash
rtk cp \
  "${EVIDENCE_DIR}/lama-fix-request-redacted.json" \
  "${REQUEST_PATH}"
```

Edit the new request:

- `applicationJobId` -> `${ATTEMPT_ID}`
- `manifestUri` -> `${GCS_ATTEMPT_ROOT}/input-manifest.json`
- `manifestGeneration` -> generation returned after manifest upload
- `outputManifestUri` -> `${GCS_ATTEMPT_ROOT}/output-manifest.json`
- `requestSha256` -> 64 zeroes temporarily

Calculate the canonical hash:

```bash
rtk env PYTHONPATH=services/aws_submission/src uv run python -c \
  'import sys; from pathlib import Path; from aws_submission.models import SubmitRequest; from aws_submission.canonical import canonical_request_hash; p=Path(sys.argv[1]); r=SubmitRequest.model_validate_json(p.read_bytes()); print(canonical_request_hash(r))' \
  "${REQUEST_PATH}"
```

Replace the temporary zeroes with the printed hash. Then validate that the stored value is exact:

```bash
rtk env PYTHONPATH=services/aws_submission/src uv run python -c \
  'import sys; from pathlib import Path; from aws_submission.models import SubmitRequest; from aws_submission.canonical import canonical_request_hash; p=Path(sys.argv[1]); r=SubmitRequest.model_validate_json(p.read_bytes()); actual=canonical_request_hash(r); assert actual == r.request_sha256, (actual, r.request_sha256); print(actual)' \
  "${REQUEST_PATH}"
```

Confirm the isolated prefix contains only the source image and input manifest:

```bash
rtk gcloud storage ls "${GCS_ATTEMPT_ROOT}/**"
```

There must be no pre-existing output manifest, completion marker, or `asset_1/` output directory.

## Phase 4: zero-capacity and identity preflight

Authenticate if necessary:

```bash
rtk aws login
rtk proxy aws sts get-caller-identity \
  --query '{account:Account,arn:Arn}' \
  --output json
```

Require account `154959837939` and region `eu-north-1`.

Check quota, queue, compute, image, and submission:

```bash
rtk proxy aws service-quotas get-service-quota \
  --service-code ec2 \
  --quota-code L-DB2E81BA \
  --region "${AWS_REGION}" \
  --query 'Quota.{name:QuotaName,value:Value}' \
  --output json

rtk proxy aws batch describe-job-queues \
  --job-queues watermark-staging-gpu \
  --region "${AWS_REGION}" \
  --query 'jobQueues[0].{state:state,status:status,statusReason:statusReason}' \
  --output json

rtk proxy aws batch describe-compute-environments \
  --compute-environments watermark-staging-gpu \
  --region "${AWS_REGION}" \
  --query 'computeEnvironments[0].{state:state,status:status,min:computeResources.minvCpus,desired:computeResources.desiredvCpus,max:computeResources.maxvCpus,types:computeResources.instanceTypes}' \
  --output json

rtk proxy aws batch describe-job-definitions \
  --job-definition-name watermark-staging-worker \
  --status ACTIVE \
  --region "${AWS_REGION}" \
  --query 'sort_by(jobDefinitions,&revision)[-1].{revision:revision,image:containerProperties.image}' \
  --output json

rtk proxy aws lambda get-function-configuration \
  --function-name watermark-staging-submission \
  --region "${AWS_REGION}" \
  --query '{submission:Environment.Variables.SUBMISSION_ENABLED,jobDefinition:Environment.Variables.BATCH_JOB_DEFINITION_ARN,status:LastUpdateStatus}' \
  --output json
```

Check every active Batch status:

```bash
for STATUS in SUBMITTED PENDING RUNNABLE STARTING RUNNING; do
  rtk proxy aws batch list-jobs \
    --job-queue watermark-staging-gpu \
    --job-status "${STATUS}" \
    --region "${AWS_REGION}" \
    --query 'jobSummaryList[].{id:jobId,name:jobName,status:status}' \
    --output json
done
```

Check live compute:

```bash
rtk proxy aws ec2 describe-instances \
  --region "${AWS_REGION}" \
  --filters Name=instance-state-name,Values=pending,running,stopping,stopped \
  --query 'Reservations[].Instances[].{id:InstanceId,state:State.Name,type:InstanceType}' \
  --output json

rtk proxy aws ec2 describe-volumes \
  --region "${AWS_REGION}" \
  --query 'Volumes[].{id:VolumeId,state:State,size:Size}' \
  --output json
```

Required state before enabling:

```text
quota: at least 4 G/VT vCPUs
queue: DISABLED and VALID
submission: false
compute environment: VALID
min vCPUs: 0
desired vCPUs: 0
max vCPUs: 4
instance type: only g4dn.xlarge
active Batch jobs: 0
active/stopped EC2 instances: 0
EBS volumes: 0
job definition: expected revision and immutable image digest
GCS attempt output prefix: empty
```

Stop if any invariant is false.

## Phase 5: enable and submit exactly one job

This phase crosses the billable boundary. Confirm that the owner explicitly authorizes this A7
attempt immediately before continuing.

### 5.1 Enable the two gates

Edit `infra/aws/environments/staging.tfvars`:

```hcl
submission_enabled  = true
batch_queue_enabled = true
```

Plan and review:

```bash
rtk terraform -chdir=infra/aws plan \
  -var-file=environments/staging.tfvars \
  -out=a7-enable.tfplan
rtk terraform -chdir=infra/aws show a7-enable.tfplan
```

The plan should contain only the queue transition `DISABLED -> ENABLED`, Lambda submission
`false -> true`, and derived policy/state updates. It must show no EC2 resource and no destroy.

Apply:

```bash
rtk terraform -chdir=infra/aws apply a7-enable.tfplan
```

Verify queue `ENABLED/VALID` and Lambda submission `true` before submitting.

### 5.2 Submit through the authenticated API

The token stays only in process memory:

```bash
rtk proxy bash -lc 'set -euo pipefail
A7_ID_TOKEN=$(rtk proxy gcloud auth print-identity-token \
  --impersonate-service-account='"${ORCHESTRATOR_SERVICE_ACCOUNT}"' \
  --audiences='"${GOOGLE_TOKEN_AUDIENCE}"' \
  --include-email)
rtk proxy curl --silent --show-error --request PUT \
  --header "Authorization: Bearer ${A7_ID_TOKEN}" \
  --header "Content-Type: application/json" \
  --data-binary @'"${REQUEST_PATH}"' \
  --write-out "\nHTTP_STATUS:%{http_code}\n" \
  "'"${API_BASE_URL}"'/internal/v1/gpu-jobs/'"${ATTEMPT_ID}"'"
unset A7_ID_TOKEN'
```

Require HTTP `202`. Record:

- `applicationJobId`
- `providerJobId`
- `providerJobArn`
- `correlationId`
- `status`

Run the identical request once more before closing submission. It must return the same
`providerJobId`; a different correlation ID is acceptable. If it returns a different provider job,
close submission immediately and stop.

### 5.3 Close submission immediately

First edit the desired state back to:

```hcl
submission_enabled  = false
batch_queue_enabled = true
```

Do not run a full Terraform apply while the job is active. Close only the live Lambda gate:

```bash
rtk proxy bash -lc 'set -euo pipefail
A7_ENV=$(rtk proxy aws lambda get-function-configuration \
  --function-name watermark-staging-submission \
  --region eu-north-1 \
  --query Environment \
  --output json \
  | rtk proxy jq -c '"'"'.Variables.SUBMISSION_ENABLED="false" | {Variables:.Variables}'"'"')
rtk proxy aws lambda update-function-configuration \
  --function-name watermark-staging-submission \
  --region eu-north-1 \
  --environment "${A7_ENV}"
unset A7_ENV'

rtk proxy aws lambda wait function-updated \
  --function-name watermark-staging-submission \
  --region "${AWS_REGION}"

rtk proxy aws lambda get-function-configuration \
  --function-name watermark-staging-submission \
  --region "${AWS_REGION}" \
  --query '{submission:Environment.Variables.SUBMISSION_ENABLED,status:LastUpdateStatus}' \
  --output json
```

Require submission `false`. From this point, no second API submission is possible.

## Phase 6: monitor without retrying

Set the accepted provider ID:

```bash
export PROVIDER_JOB_ID=UUID_FROM_202_RESPONSE
```

Poll manually:

```bash
rtk proxy aws batch describe-jobs \
  --jobs "${PROVIDER_JOB_ID}" \
  --region "${AWS_REGION}" \
  --query 'jobs[0].{status:status,statusReason:statusReason,createdAt:createdAt,startedAt:startedAt,stoppedAt:stoppedAt,exitCode:container.exitCode,reason:container.reason,logStream:container.logStreamName}' \
  --output json
```

Expected lifecycle:

```text
SUBMITTED -> PENDING -> RUNNABLE -> STARTING -> RUNNING -> SUCCEEDED|FAILED
```

While provisioning/running, record the one temporary instance:

```bash
rtk proxy aws batch describe-compute-environments \
  --compute-environments watermark-staging-gpu \
  --region "${AWS_REGION}" \
  --query 'computeEnvironments[0].{desired:computeResources.desiredvCpus,status:status}' \
  --output json

rtk proxy aws ec2 describe-instances \
  --region "${AWS_REGION}" \
  --filters Name=instance-state-name,Values=pending,running \
  --query 'Reservations[].Instances[].{id:InstanceId,state:State.Name,type:InstanceType,launchTime:LaunchTime}' \
  --output json
```

Expected peak is one `g4dn.xlarge` and `desired=4`.

Once `logStreamName` exists:

```bash
export LOG_STREAM=LOG_STREAM_FROM_DESCRIBE_JOBS
rtk proxy aws logs get-log-events \
  --log-group-name /aws/batch/watermark-staging-worker \
  --log-stream-name "${LOG_STREAM}" \
  --region "${AWS_REGION}" \
  --no-start-from-head \
  --limit 200 \
  --query 'events[].message' \
  --output text
```

Require evidence of:

- `job_started`
- Tesla T4 and expected PyTorch/CUDA details
- background-removal initialized and released
- detection initialized and released
- inpainting initialized and released
- `item_completed`
- `job_completed`

Warnings about the optional multi-scale deformable-attention CUDA extension or Matplotlib cache
may appear. They were non-blocking in the successful baseline; evaluate new warnings against the
actual terminal status and output artifacts.

If the CLI session expires, run `rtk aws login` and resume. The already-closed submission gate
keeps the boundary safe.

### Failure or timeout rule

On `FAILED`, do not retry. Record the provider job ID, exit code, reason, log stream, relevant
messages, and GCS marker inventory. Continue to Phase 8 cleanup.

If the job has not reached a terminal state within the accepted 90-minute A7 limit:

```bash
rtk proxy aws batch terminate-job \
  --job-id "${PROVIDER_JOB_ID}" \
  --reason "A7 operator timeout after 90 minutes" \
  --region "${AWS_REGION}"
```

Wait for `FAILED` before the full cleanup apply. Do not submit a replacement job.

## Phase 7: verify every GCS result

After `SUCCEEDED`:

```bash
rtk proxy gcloud storage ls -l "${GCS_ATTEMPT_ROOT}/**"
rtk proxy gcloud storage cat "${GCS_ATTEMPT_ROOT}/output-manifest.json"
rtk proxy gcloud storage cat "${GCS_ATTEMPT_ROOT}/asset_1/result.json"
rtk proxy gcloud storage cat "${GCS_ATTEMPT_ROOT}/asset_1/detections/detections.json"
```

Require:

```text
output manifest counts.total: 1
output manifest counts.succeeded: 1
output manifest counts.failed: 0
item status: SUCCEEDED
decision: INPAINTED or the expected policy decision
workerImageDigest: exact deployed digest
job _COMPLETE.json: present
item _COMPLETE.json: present
```

Expected artifact layout:

```text
${GCS_ATTEMPT_ROOT}/
  input-manifest.json
  output-manifest.json
  _COMPLETE.json
  asset_1.png
  asset_1/
    source_metadata.json
    result.json
    _COMPLETE.json
    background_removed/
      foreground_mask.png
      white_background.png
    detections/
      annotated.png
      detections.json
    masks/
      yolo_sam2_000.png
      combined_mask.png
    inpainted/
      lama_raw_output.png
      final.png
```

`annotated.png`, `combined_mask.png`, and `inpainted/final.png` are the main visual inspection
artifacts.

## Phase 8: mandatory teardown and zero-capacity proof

Wait until the job is `SUCCEEDED` or `FAILED`. Then edit:

```hcl
submission_enabled  = false
batch_queue_enabled = false
```

Plan and review:

```bash
rtk terraform -chdir=infra/aws plan \
  -var-file=environments/staging.tfvars \
  -out=a7-cleanup.tfplan
rtk terraform -chdir=infra/aws show a7-cleanup.tfplan
```

The expected material change is queue `ENABLED -> DISABLED`; Lambda may already be false because it
was closed directly. Apply:

```bash
rtk terraform -chdir=infra/aws apply a7-cleanup.tfplan
```

Prove the final state:

```bash
rtk proxy aws batch describe-job-queues \
  --job-queues watermark-staging-gpu \
  --region "${AWS_REGION}" \
  --query 'jobQueues[0].{state:state,status:status}' \
  --output json

rtk proxy aws lambda get-function-configuration \
  --function-name watermark-staging-submission \
  --region "${AWS_REGION}" \
  --query '{submission:Environment.Variables.SUBMISSION_ENABLED,status:LastUpdateStatus}' \
  --output json

rtk proxy aws batch describe-compute-environments \
  --compute-environments watermark-staging-gpu \
  --region "${AWS_REGION}" \
  --query 'computeEnvironments[0].{min:computeResources.minvCpus,desired:computeResources.desiredvCpus,max:computeResources.maxvCpus,status:status}' \
  --output json

for STATUS in SUBMITTED PENDING RUNNABLE STARTING RUNNING; do
  rtk proxy aws batch list-jobs \
    --job-queue watermark-staging-gpu \
    --job-status "${STATUS}" \
    --region "${AWS_REGION}" \
    --query 'jobSummaryList' \
    --output json
done

rtk proxy aws ec2 describe-instances \
  --region "${AWS_REGION}" \
  --filters Name=instance-state-name,Values=pending,running,stopping,stopped \
  --query 'Reservations[].Instances[].{id:InstanceId,state:State.Name,type:InstanceType}' \
  --output json

rtk proxy aws ec2 describe-volumes \
  --region "${AWS_REGION}" \
  --query 'Volumes[].{id:VolumeId,state:State,size:Size}' \
  --output json

rtk proxy aws ec2 describe-spot-instance-requests \
  --region "${AWS_REGION}" \
  --filters Name=state,Values=open,active \
  --query 'SpotInstanceRequests' \
  --output json
```

Resolve and inspect the Batch ECS cluster:

```bash
rtk proxy aws ecs list-clusters \
  --region "${AWS_REGION}" \
  --query 'clusterArns[?contains(@, `AWSBatch-watermark-staging-gpu`)]' \
  --output json

rtk proxy aws ecs list-container-instances \
  --cluster AWSBatch-watermark-staging-gpu-e0c1c763-5a76-3b41-824d-e27b738393d1 \
  --region "${AWS_REGION}" \
  --output json

rtk proxy aws ecs list-tasks \
  --cluster AWSBatch-watermark-staging-gpu-e0c1c763-5a76-3b41-824d-e27b738393d1 \
  --desired-status RUNNING \
  --region "${AWS_REGION}" \
  --output json
```

If the cluster suffix changes, use the ARN returned by `list-clusters` instead of the historical
value above.

Finally:

```bash
rtk terraform -chdir=infra/aws plan \
  -var-file=environments/staging.tfvars \
  -detailed-exitcode
```

Exit code `0` and `No changes` are required.

Final invariant:

```text
queue: DISABLED and VALID
submission: false
min vCPUs: 0
desired vCPUs: 0
active Batch jobs: 0
active/stopped EC2 instances: 0
EBS volumes: 0
ECS container instances: 0
ECS running tasks: 0
active Spot requests: 0
Terraform changes: 0
```

## Emergency stop

Use this whenever submission scope is uncertain, a second job appears, or the operator must abort.

1. Immediately set the live Lambda environment `SUBMISSION_ENABLED=false` using the command from
   Phase 5.3.
2. Edit `staging.tfvars` so both flags are false.
3. Terminate the known A7 job:

   ```bash
   rtk proxy aws batch terminate-job \
     --job-id "${PROVIDER_JOB_ID}" \
     --reason "A7 emergency operator stop" \
     --region "${AWS_REGION}"
   ```

4. If an unexpected second active job exists, terminate it explicitly by its exact job ID.
5. Wait for every active job to become terminal.
6. Run the Phase 8 cleanup plan/apply and zero-capacity proof.

Disabling the queue prevents new jobs from being scheduled but does not terminate an already
running job. `terminate-job` is required for an active runaway job.

## Evidence and handoff

Create attempt-specific, redacted evidence under:

```text
aws-acceptance/evidence/a7-one-image/
```

Record:

- attempt ID and UTC timestamps
- input manifest and exact GCS generations
- redacted submission request and canonical hash
- HTTP status and redacted response
- provider job ID/ARN and job-definition revision
- worker image digest
- instance ID/type, launch time, and termination time
- GPU device, CUDA capability, and PyTorch version
- stage lifecycle and terminal exit code
- GCS artifact inventory and output-manifest counts
- cleanup proof
- Terraform final no-change result
- approximate runtime cost

Never store:

- Google ID tokens
- AWS session credentials
- browser-login URLs or codes
- passwords, API keys, or authorization headers

Cost Explorer is delayed. For a deterministic estimate, record the instance launch and termination
timestamps, calculate the `g4dn.xlarge`, public IPv4, and temporary gp3 costs using current Stockholm
rates, and label the result as an estimate rather than a posted invoice.

## Completion criteria

An A7 attempt is operationally closed only after cleanup and evidence capture. A7 itself passes
only after a successful functional result.

1. Functional pass: the single expected item is `SUCCEEDED`, has the expected decision, and writes
   valid GCS manifests, completion markers, and visual artifacts. A failed attempt must still be
   documented and cleaned up, but A7 remains incomplete and must not be retried automatically.
2. Safety: submission is false, queue is disabled, and all temporary compute/storage is gone.
3. Reproducibility: the request, immutable generations, image digest, provider job ID, logs, result
   inventory, cleanup proof, and final Terraform state are recorded without secrets.
