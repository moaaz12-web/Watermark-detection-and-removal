# AWS submission, worker-image, and disabled Batch infrastructure

This Terraform root deploys the A3 submission boundary, A4 workload identity boundary, A5
private worker-image repository, and A6 bounded AWS Batch foundation:
the authenticated HTTP API, submission Lambda, DynamoDB idempotency ledger, logs, alarms,
dedicated GCP orchestrator, empty-permission AWS GPU job role, Google AWS workload identity
provider, prefix-conditioned worker GCS permissions, immutable ECR repository, dedicated public
Batch network, runtime roles, launch template, managed compute environment, disabled queue, and
digest-gated job definition.

It intentionally creates no direct EC2 instance, GPU, NAT gateway, Elastic IP, load balancer, SSH
key, or standalone EBS volume. The managed compute environment has `min_vcpus=0`,
`desired_vcpus=0`, `max_vcpus=4`, and permits only `g4dn.xlarge`. The queue and API submission
switch both default to disabled. An empty `worker_image_digest` prevents creation of the job
definition and prevents Lambda from receiving Batch permissions. Supplying a digest prepares the
immutable job revision but does not submit a job or launch an instance.

The A4 GPU job role remains an identity anchor with no managed or inline policy. The A5 ECR
lifecycle policy can expire only untagged images older than 30 days; tagged release images are not
selected. By operator decision on 2026-07-25, ECR vulnerability scan-on-push and the embedded-secret
image check are deferred. Build-context exclusions and the prohibition against placing credentials
or customer data in the image remain required.

## Prerequisites

1. Terraform `>=1.15,<1.16`.
2. AWS caller account `154959837939`.
3. Google Application Default Credentials for project
   `project-5909a738-2fdc-451e-ad6`.
4. A pre-existing versioned GCS state bucket.
5. A Lambda build produced by:

   ```bash
   services/aws_submission/scripts/build_lambda.sh
   ```

## Initialize and review

Use the real state bucket name without committing it:

```bash
terraform -chdir=infra/aws init \
  -backend-config="bucket=OWNER_SUPPLIED_STATE_BUCKET" \
  -backend-config="prefix=watermark/aws/staging"
terraform -chdir=infra/aws fmt -check
terraform -chdir=infra/aws validate
terraform -chdir=infra/aws plan \
  -var-file=environments/staging.tfvars \
  -out=staging.tfplan
terraform -chdir=infra/aws show staging.tfplan
```

Never use `-auto-approve`. Review resource counts, IAM actions, cost-bearing resources, and any
destroy/replace action before applying.

## A6 image-digest gate

Keep these values in the ignored `environments/staging.tfvars` file while the A5 upload is pending:

```hcl
worker_image_digest = ""
batch_queue_enabled = false
submission_enabled  = false
```

After ECR reports the pushed image's immutable `sha256:...` digest, set only
`worker_image_digest` and run a reviewed plan. Keep the queue and submission disabled through the
A6 apply and zero-capacity inspection. Enabling either belongs to a later, explicitly authorized
checkpoint; submitting the first billable GPU job belongs to A7.
