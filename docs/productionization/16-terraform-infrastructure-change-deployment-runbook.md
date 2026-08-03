# Terraform infrastructure-change deployment runbook

Status: Current operator procedure for AWS Terraform changes.

Use this runbook for a reviewed change under `infra/aws/`, including AWS Batch queue, compute
environment, Lambda, IAM, networking, or observability changes. It prevents a source change from
being confused with a deployment and records the safety checks required before an apply.

This is not the worker-image release process. If the change affects `Dockerfile.gpu`, worker source,
configuration, model manifest, or Python dependencies, also follow the
[AWS worker image deployment and GPU acceptance runbook](aws-worker-image-deployment-and-gpu-acceptance-runbook.md).

## Safety rules

1. Work from a clean, reviewed commit. Do not deploy unrelated working-tree changes.
2. Use Terraform and the saved-plan workflow. Never make equivalent Batch, Lambda, IAM, or queue
   changes in the AWS console.
3. Never execute or source `environments/staging.tfvars`. It is Terraform input, not a shell script.
4. Before a potentially disruptive change, put the system in development-safe mode using the
   [operational-mode runbook](09-operational-mode-switch-runbook.md#8-switch-to-development-safe-mode).
5. Do not apply a stale plan. Create a new plan after every source, `.tfvars`, or live-state change,
   including after a failed or partial apply.
6. Do not use `-auto-approve`.
7. Stop for an unexplained destroy, replacement, AWS region change, IAM expansion, new billable
   resource, or a second concurrent worker path.

## 1. Establish the deployment window

Use the live regions and account, rather than inferring them from a terminal prompt:

```bash
cd /home/maaz/Desktop/watermarkPoc

export AWS_REGION="eu-north-1"
export AWS_ACCOUNT_ID="154959837939"
export AWS_TF_ROOT="$PWD/infra/aws"
export AWS_TFVARS="$AWS_TF_ROOT/environments/staging.tfvars"

rtk proxy aws sts get-caller-identity \
  --query '{account:Account,arn:Arn}' \
  --output json

rtk git status --short --branch
rtk git log -1 --oneline
rtk git diff --check
```

The AWS identity must be account `154959837939`. If authentication has expired, run `rtk aws login`
and repeat the identity check.

For changes that affect Batch, its queue, submission, worker IAM, network, or compute capacity:

1. Stop public submissions and let accepted jobs finish.
2. Pause the control-plane scheduler.
3. Set `submission_enabled = false` and `batch_queue_enabled = false` in
   `infra/aws/environments/staging.tfvars` using an editor.
4. Apply the development-safe gate change through a reviewed Terraform plan if it is not already
   deployed.

Use the [development-safe procedure](09-operational-mode-switch-runbook.md#8-switch-to-development-safe-mode)
for the exact GCP and AWS gate ordering. Do not disable a queue as a substitute for terminating a
running job: it prevents new work but does not stop existing work.

Confirm the deployment window is idle:

```bash
for STATUS in SUBMITTED PENDING RUNNABLE STARTING RUNNING; do
  rtk proxy aws batch list-jobs \
    --job-queue watermark-staging-gpu \
    --job-status "$STATUS" \
    --region "$AWS_REGION" \
    --query 'jobSummaryList[].{id:jobId,name:jobName,status:status}' \
    --output table
done

rtk proxy aws ec2 describe-instances \
  --region "$AWS_REGION" \
  --filters \
    'Name=instance-state-name,Values=pending,running,stopping' \
    'Name=instance-type,Values=g4dn.xlarge,g5.xlarge,g6.xlarge' \
  --query 'Reservations[].Instances[].{id:InstanceId,state:State.Name,type:InstanceType}' \
  --output table
```

Every active-job table and the instance table must be empty before the infrastructure apply.

## 2. Validate and create a fresh saved plan

Choose an unambiguous temporary plan filename. Do not reuse a plan produced before the latest
source or gate change.

```bash
rtk terraform -chdir=infra/aws fmt -check -recursive
rtk terraform -chdir=infra/aws validate

rtk terraform -chdir=infra/aws plan \
  -var-file=environments/staging.tfvars \
  -out=/tmp/watermark-terraform-change.tfplan

rtk terraform -chdir=infra/aws show -no-color \
  /tmp/watermark-terraform-change.tfplan
```

Review the complete plan, not only the summary. In a safe deployment window it must preserve:

- `min_vcpus = 0` and `desired_vcpus = 0` for every Batch compute environment;
- `submission_enabled = false` and a disabled queue for a capacity-affecting change;
- the existing AWS account and `eu-north-1` region; and
- the expected worker image digest and Batch job-definition revisions, unless the release includes
  an intentionally reviewed worker-image deployment.

Terraform may show an in-place submission-Lambda policy update when a Lambda environment value
changes. Confirm it is a rendering/update consequence of the reviewed change, not an IAM privilege
expansion.

## 3. Apply only the reviewed plan

```bash
rtk terraform -chdir=infra/aws apply \
  /tmp/watermark-terraform-change.tfplan

rm -f /tmp/watermark-terraform-change.tfplan
```

If apply fails, preserve its error text, do not rerun the old plan, and inspect the current state.
Correct the underlying Terraform configuration, validate it, then return to section 2 for a new
plan. A partially applied plan may have changed some safe gate resources before the failing resource.

### AWS Batch compute-environment creation rule

AWS Batch requires a new managed compute environment to be **created in `ENABLED` state**. This is
not permission to run work. Keep the job queue and submission gate disabled during creation; with
`min_vcpus = 0` and `desired_vcpus = 0`, an enabled but unqueued environment has no GPU instance and
does not incur GPU runtime charges.

Do not attempt to create the compute environment disabled through the console or Terraform. If AWS
returns `Compute Environment must be created in ENABLED state`, change the new resource's initial
state to `ENABLED`, keep the queue disabled, create a new plan, and apply that plan.

### Batch allocation-strategy transitions

Do not mutate a live `BEST_FIT` environment into another allocation strategy in place. Use a
blue-green transition:

1. retain the legacy environment as an idle rollback resource;
2. create a separately named green environment with the new strategy and zero capacity floor;
3. repoint the disabled queue to the green environment in the same reviewed plan;
4. run a controlled application-level acceptance; and
5. retire the legacy environment only in a later, separately reviewed change.

The current fallback environment is `watermark-staging-gpu-fallback`. It uses
`BEST_FIT_PROGRESSIVE_ORDERED` with `g4dn.xlarge`, then `g5.xlarge`, then `g6.xlarge`; the legacy
`watermark-staging-gpu` environment remains the rollback resource.

## 4. Verify live infrastructure and zero GPU capacity

Run these commands after a successful apply:

```bash
rtk proxy aws batch describe-compute-environments \
  --compute-environments watermark-staging-gpu watermark-staging-gpu-fallback \
  --region "$AWS_REGION" \
  --query 'computeEnvironments[].{name:computeEnvironmentName,state:state,status:status,allocation:computeResources.allocationStrategy,types:computeResources.instanceTypes,min:computeResources.minvCpus,desired:computeResources.desiredvCpus,max:computeResources.maxvCpus}' \
  --output table

rtk proxy aws batch describe-job-queues \
  --job-queues watermark-staging-gpu \
  --region "$AWS_REGION" \
  --query 'jobQueues[0].{state:state,status:status,order:computeEnvironmentOrder}' \
  --output json

rtk proxy aws lambda get-function-configuration \
  --function-name watermark-staging-submission \
  --region "$AWS_REGION" \
  --query '{submission:Environment.Variables.SUBMISSION_ENABLED,status:LastUpdateStatus}' \
  --output json
```

Repeat the active-job and EC2 checks from section 1. Required safe-state result:

```text
Batch queue: DISABLED / VALID
Submission: false
All compute environments: VALID
min vCPUs: 0
desired vCPUs: 0
Active Batch jobs: none
GPU EC2 instances: none
```

Finally, verify Terraform convergence:

```bash
rtk terraform -chdir=infra/aws plan \
  -var-file=environments/staging.tfvars \
  -detailed-exitcode
```

Exit code `0` and `No changes` are required. Exit code `2` means investigate the drift and create a
new reviewed plan; do not apply it automatically.

## 5. Acceptance and return to operation

For a Batch or worker execution-path change, run exactly one ordinary application-level watermark
job through FastAPI. Do not submit directly to the internal AWS API. Use the
[controlled end-to-end operator runbook](07-a10-controlled-e2e-operator-runbook.md), then verify
the result and return to zero GPU capacity.

Only after acceptance succeeds, use the [operational-mode runbook](09-operational-mode-switch-runbook.md#7-switch-to-operational-mode)
to re-enable the FastAPI feature flags, Terraform submission and queue gates, and scheduler in the
documented order.

## 6. Rollback

Keep the system in development-safe mode. Revert the reviewed infrastructure commit or make a
small, explicit Terraform change that restores the previous queue routing or setting. Then create a
new plan, review it, apply it, and repeat the verification in section 4. Never use a stale plan and
never directly delete a Batch-managed GPU instance as a rollback shortcut.
