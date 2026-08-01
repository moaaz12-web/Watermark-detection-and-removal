# AWS worker image deployment and GPU acceptance runbook

Status: Current operational procedure.

Use this runbook when a change to the watermark worker must be built into the AWS GPU image,
published to ECR, deployed to the AWS Batch job definitions, and accepted with one controlled
application job.

This runbook deliberately excludes the historical A7 walkthrough. Historical job IDs, image
digests, Batch revisions, timestamps, and acceptance evidence are not safe deployment inputs.
Git history and `aws-acceptance/evidence/` retain that record.

## Scope

Run this procedure when any image input changes:

- `Dockerfile.gpu`
- `requirements-gpu.lock`
- `src/`
- `configs/`
- `model-manifest.json`
- another file explicitly added to the CodeBuild source archive

Do not rebuild the worker image for a FastAPI-only, frontend-only, submission-Lambda-only, or
Terraform-only change.

The deployment path is:

```text
reviewed repository state
  -> AWS CodeBuild
  -> immutable ECR image digest
  -> reviewed Terraform plan
  -> new small and bulk AWS Batch job-definition revisions
  -> one controlled FastAPI watermark job
  -> output validation
  -> zero-GPU proof
  -> normal operational mode or rollback
```

## Authority and freshness

Read these before acting:

- [Hybrid Watermark System Agent Guide](00-hybrid-watermark-system-agent-guide.md)
- [Operational Mode Switch Runbook](09-operational-mode-switch-runbook.md)
- [AWS Terraform README](../../infra/aws/README.md)
- [Controlled End-to-End Operator Runbook](07-a10-controlled-e2e-operator-runbook.md)

The live AWS deployment is currently in `eu-north-1`. The Ohio document is a proposal, not the
active deployment.

Always refresh identities, resource state, feature gates, active jobs, image digests, and Batch
revisions. Values recorded in documentation are orientation only.

## Safety rules

1. Verify AWS account `154959837939` and the active GCP project before any cloud action.
2. Preserve unrelated working-tree changes. Build only reviewed source.
3. Never execute or source `infra/aws/environments/staging.tfvars`; pass it to Terraform with
   `-var-file`.
4. Do not deploy an unproven digest while new jobs can enter the old execution path.
5. Do not close or change gates while a job is active without following the operational-mode
   runbook.
6. Never apply an incomplete, stale, or unreviewed Terraform plan.
7. Keep AWS Batch `min_vcpus=0` and verify idle capacity returns to zero.
8. Run exactly one acceptance job. Do not automatically retry a failure.
9. Never print or store Google identity tokens, AWS session credentials, passwords, API keys, or
   authorization headers.
10. Stop if a plan includes an unexplained destroy, replacement, region change, IAM expansion, or
    second provider job.

## 1. Prepare and validate the release

### 1.1 Inspect repository scope

```bash
rtk git status --short --branch
rtk git diff --check
rtk git diff -- Dockerfile.gpu requirements-gpu.lock src configs model-manifest.json
```

The CodeBuild helper packages only:

```text
.dockerignore
Dockerfile.gpu
requirements-gpu.lock
src/
configs/
model-manifest.json
buildspec.yml
```

Documentation and tests do not enter the image. Commit or otherwise identify the exact reviewed
source state before starting a release build.

### 1.2 Run local quality gates

```bash
rtk uv lock --check
rtk uv run pytest -q
rtk uv run mypy
rtk uv run ruff check .
rtk git diff --check
```

Do not build if a required check fails. Record any intentionally skipped live or container test
and why it was not required for this change.

### 1.3 Verify cloud identities and active region

```bash
export AWS_REGION="eu-north-1"
export AWS_ACCOUNT_ID="154959837939"
export GCP_PROJECT_ID="project-5909a738-2fdc-451e-ad6"

rtk proxy aws sts get-caller-identity \
  --query '{account:Account,arn:Arn}' \
  --output json

rtk proxy gcloud config get-value project
rtk proxy gcloud auth list --filter=status:ACTIVE --format='value(account)'
```

If AWS authentication is expired, run `rtk aws login`, then verify the identity again.

### 1.4 Discover current image and active work

```bash
rtk proxy aws batch describe-job-definitions \
  --job-definition-name watermark-staging-worker \
  --status ACTIVE \
  --region "${AWS_REGION}" \
  --query 'sort_by(jobDefinitions,&revision)[-1].{revision:revision,arn:jobDefinitionArn,image:containerProperties.image}' \
  --output json

rtk proxy aws batch describe-job-definitions \
  --job-definition-name watermark-staging-worker-bulk \
  --status ACTIVE \
  --region "${AWS_REGION}" \
  --query 'sort_by(jobDefinitions,&revision)[-1].{revision:revision,arn:jobDefinitionArn,image:containerProperties.image}' \
  --output json

for status in SUBMITTED PENDING RUNNABLE STARTING RUNNING; do
  rtk proxy aws batch list-jobs \
    --job-queue watermark-staging-gpu \
    --job-status "${status}" \
    --region "${AWS_REGION}" \
    --query 'jobSummaryList[].{id:jobId,name:jobName,status:status}' \
    --output json
done
```

Record the currently deployed digest as the rollback digest. If work is active, allow it to finish
or follow the operational-mode runbook. Do not improvise a drain.

## 2. Build and publish the worker image

### 2.1 Start one remote CodeBuild build

Choose a unique lowercase tag that identifies the release:

```bash
export BUILD_ID="worker-YYYYMMDD-N"

rtk infra/aws/scripts/start-worker-image-build.sh "${BUILD_ID}"
```

The helper uploads a small reviewed source ZIP to the private CodeBuild source bucket. CodeBuild
performs the Linux AMD64 GPU-image build, reuses the ECR BuildKit cache, runs its configured smoke
checks, and pushes the release image. It does not start AWS Batch or a GPU instance.

Save the returned build ID:

```bash
export CODEBUILD_BUILD_ID="watermark-staging-worker-image-build:UUID"
```

### 2.2 Monitor the build

```bash
rtk proxy aws codebuild batch-get-builds \
  --ids "${CODEBUILD_BUILD_ID}" \
  --region "${AWS_REGION}" \
  --query 'builds[0].{status:buildStatus,phase:currentPhase,logs:logs.deepLink}' \
  --output json
```

Continue only after `SUCCEEDED`. On `FAILED`, `FAULT`, `STOPPED`, or `TIMED_OUT`, inspect the
linked CloudWatch logs and fix the specific cause before starting another build.

### 2.3 Resolve and validate the immutable digest

```bash
export WORKER_IMAGE_DIGEST="$(
  rtk proxy aws ecr describe-images \
    --region "${AWS_REGION}" \
    --repository-name watermark-staging-worker \
    --image-ids "imageTag=${BUILD_ID}" \
    --query 'imageDetails[0].imageDigest' \
    --output text
)"

printf '%s\n' "${WORKER_IMAGE_DIGEST}"
```

Require exactly `sha256:` followed by 64 lowercase hexadecimal characters:

```bash
if [[ ! "${WORKER_IMAGE_DIGEST}" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "STOP: invalid ECR digest" >&2
  exit 1
fi
```

Also verify the tag resolves to the expected repository and recently completed build. A mutable
tag is only a lookup aid; Terraform and Batch must use the immutable digest.

## 3. Deploy the digest through Terraform

### 3.1 Enter a safe deployment window

For an unproven image, follow the
[Operational Mode Switch Runbook](09-operational-mode-switch-runbook.md) to:

1. stop new public watermark work;
2. pause the GCP control-plane scheduler;
3. drain or explicitly terminate active work;
4. close AWS submission; and
5. disable the Batch queue without changing the zero-capacity floor.

Do not assume the environment already starts disabled. Normal production operation may have the
FastAPI flags, scheduler, submission gate, and queue enabled.

Before the digest apply, require:

```text
new public watermark work: stopped
control-plane scheduler: paused
AWS submission: false
Batch queue: DISABLED
active Batch jobs: 0
GPU EC2 instances: 0
```

### 3.2 Update the reviewed Terraform input

Edit:

```text
infra/aws/environments/staging.tfvars
```

Set:

```hcl
worker_image_digest = "sha256:NEW_DIGEST"
submission_enabled  = false
batch_queue_enabled = false
```

Do not change the region, instance type, GPU capacity, API allowlists, WIF trust, or unrelated
infrastructure as part of an image-only deployment.

### 3.3 Create and review the plan

```bash
rtk terraform -chdir=infra/aws fmt -check -recursive
rtk terraform -chdir=infra/aws validate
rtk terraform -chdir=infra/aws plan \
  -var-file=environments/staging.tfvars \
  -out=worker-image-deploy.tfplan
rtk terraform -chdir=infra/aws show worker-image-deploy.tfplan
```

For an image-only deployment, expect new small and bulk Batch job-definition revisions and the
derived Lambda references/policy updates. The plan must keep submission closed, the queue
disabled, and `min_vcpus=0`. It must not create an EC2 instance.

Apply only the reviewed saved plan:

```bash
rtk terraform -chdir=infra/aws apply worker-image-deploy.tfplan
```

### 3.4 Verify both Batch definitions

```bash
for definition in watermark-staging-worker watermark-staging-worker-bulk; do
  rtk proxy aws batch describe-job-definitions \
    --job-definition-name "${definition}" \
    --status ACTIVE \
    --region "${AWS_REGION}" \
    --query 'sort_by(jobDefinitions,&revision)[-1].{name:jobDefinitionName,revision:revision,arn:jobDefinitionArn,image:containerProperties.image,status:status}' \
    --output json
done
```

Both definitions must reference:

```text
154959837939.dkr.ecr.eu-north-1.amazonaws.com/watermark-staging-worker@${WORKER_IMAGE_DIGEST}
```

Verify the submission Lambda references the new exact revisions while submission remains closed:

```bash
rtk proxy aws lambda get-function-configuration \
  --function-name watermark-staging-submission \
  --region "${AWS_REGION}" \
  --query '{
    submission:Environment.Variables.SUBMISSION_ENABLED,
    definitions:Environment.Variables.BATCH_JOB_DEFINITION_ARNS,
    status:LastUpdateStatus
  }' \
  --output json
```

## 4. Run one controlled GPU acceptance

Use the application-level path in the
[Controlled End-to-End Operator Runbook](07-a10-controlled-e2e-operator-runbook.md), after
refreshing its point-in-time resource values against the agent guide and live state.

Acceptance must:

1. use exactly one ordinary source image;
2. open only the required FastAPI, scheduler, submission, and queue gates;
3. create one new FastAPI watermark job;
4. observe one AWS Batch provider job;
5. verify the Batch job uses the new job-definition revision and image digest;
6. download the authorized final result through FastAPI;
7. inspect the final image and retained diagnostic artifacts;
8. close the controlled window after any outcome; and
9. prove GPU capacity returns to zero.

Do not call the internal AWS submission API directly for routine acceptance. FastAPI,
PostgreSQL, the GCP control plane, AWS submission, Batch execution, GCS output validation, and
authorized result delivery are all part of the production contract.

For the duplicate-inpainting-output removal, additionally verify:

```text
asset_1/inpainted/final.png: present
asset_1/inpainted/lama_raw_output.png: absent
item result and completion marker: present
job output manifest and completion marker: present
```

If the job fails or times out, do not automatically retry. Record the application job ID,
provider job ID, Batch definition, digest, exit code, log stream, and GCS marker inventory, then
close the controlled window.

## 5. Restore operation and prove zero GPU capacity

Follow the [Operational Mode Switch Runbook](09-operational-mode-switch-runbook.md) to restore the
owner-selected final mode:

- normal operational mode, if the new image passed acceptance; or
- development-safe mode, if acceptance failed or remains inconclusive.

An enabled queue does not mean a GPU is running. Regardless of final mode, prove idle GPU capacity:

```bash
rtk proxy aws batch describe-compute-environments \
  --compute-environments watermark-staging-gpu \
  --region "${AWS_REGION}" \
  --query 'computeEnvironments[0].{state:state,status:status,min:computeResources.minvCpus,desired:computeResources.desiredvCpus,max:computeResources.maxvCpus}' \
  --output json

for status in SUBMITTED PENDING RUNNABLE STARTING RUNNING; do
  rtk proxy aws batch list-jobs \
    --job-queue watermark-staging-gpu \
    --job-status "${status}" \
    --region "${AWS_REGION}" \
    --query 'jobSummaryList[].{id:jobId,name:jobName,status:status}' \
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
```

Required idle state:

```text
compute environment: VALID
min vCPUs: 0
desired vCPUs: 0
active Batch jobs: 0
active or stopped GPU EC2 instances: 0
orphan EBS volumes: 0
```

Finally verify Terraform matches the selected final mode:

```bash
rtk terraform -chdir=infra/aws plan \
  -var-file=environments/staging.tfvars \
  -detailed-exitcode
```

Exit code `0` and `No changes` are required.

## Rollback

Rollback uses the previously recorded immutable digest; never use a mutable tag.

1. Stop new public work and pause the control-plane scheduler.
2. Drain active jobs.
3. Close submission and disable the queue using the operational-mode runbook.
4. Set `worker_image_digest` in `staging.tfvars` to the previous digest.
5. Create and review a new Terraform plan.
6. Apply the reviewed saved plan.
7. Verify both Batch definitions and Lambda references point to the rollback revisions.
8. Either run one controlled acceptance of the rollback or keep the system in development-safe
   mode.
9. Prove idle GPU capacity and a no-change Terraform plan.

Do not attempt to mutate an existing Batch job definition. AWS Batch definitions are versioned;
Terraform registers new revisions that reference the selected immutable digest.

## Emergency stop

Use the [Operational Mode Switch Runbook](09-operational-mode-switch-runbook.md) emergency
procedure when submission scope is uncertain, an unexpected second job appears, or a GPU job must
be stopped.

The safe ordering is:

1. stop new FastAPI work;
2. pause the GCP scheduler;
3. close the live AWS submission gate;
4. disable the Batch queue;
5. terminate each exact active Batch job when necessary;
6. wait for terminal state and managed compute teardown; and
7. reconcile Terraform desired state.

Disabling a queue does not terminate a running job. Do not directly terminate a Batch-managed EC2
instance as the normal stop mechanism because Batch may replace it while the provider job remains
active.

## Deployment record

Record, without secrets:

- source commit
- CodeBuild build ID and terminal status
- ECR release tag and immutable digest
- prior and new small/bulk Batch revisions
- Terraform plan summary and apply result
- application and provider job IDs for the one acceptance
- final-image inspection result
- relevant warning and error codes
- final operational mode
- zero-GPU verification
- rollback digest

Store controlled acceptance evidence under an attempt-specific path in `aws-acceptance/evidence/`.
Never store identity tokens, session credentials, passwords, API keys, or authorization headers.

## Completion criteria

Deployment is complete only when:

1. local quality gates passed;
2. CodeBuild succeeded;
3. ECR returned a valid immutable digest;
4. Terraform registered both expected Batch revisions without unrelated changes;
5. Lambda references the new exact revisions;
6. one controlled application job exercised the new worker image;
7. the final result and required contract objects were verified;
8. the selected final operational mode was restored;
9. GPU capacity returned to zero; and
10. Terraform reports no remaining changes.
