# AWS submission, worker-image, and disabled Batch infrastructure

This Terraform root deploys the A3 submission boundary, A4 workload identity boundary, A5
private worker-image repository, and A6 bounded AWS Batch foundation:
the authenticated HTTP API, submission Lambda, DynamoDB idempotency ledger, logs, alarms,
dedicated GCP orchestrator, empty-permission AWS GPU job role, Google AWS workload identity
provider, prefix-conditioned worker GCS permissions, immutable ECR repository, dedicated public
Batch network, runtime roles, launch template, managed compute environment, disabled queue, and
digest-gated job definition. It also provisions an idle, on-demand CodeBuild project that builds
worker images inside AWS from a small operator-uploaded source ZIP. BuildKit stores reusable layers
in a separate mutable ECR cache repository; reviewed release tags remain in the immutable worker
repository.

It intentionally creates no direct EC2 instance, GPU, NAT gateway, Elastic IP, load balancer, SSH
key, or standalone EBS volume. The managed compute environment has `min_vcpus=0`,
`desired_vcpus=0`, `max_vcpus=4`, and permits only `g4dn.xlarge`. The queue and API submission
switch both default to disabled. An empty `worker_image_digest` prevents creation of the job
definition and prevents Lambda from receiving Batch permissions. Supplying a digest prepares the
immutable job revision but does not submit a job or launch an instance.

Terraform does not start a CodeBuild build. The CodeBuild project, private short-lived S3 source
bucket, ECR cache repository, IAM role, and CloudWatch log group remain idle until the operator
explicitly runs `scripts/start-worker-image-build.sh IMAGE_TAG`. That helper uploads source code,
not a multi-gigabyte local image. CodeBuild then performs the large image build and ECR push within
AWS. It does not start the Batch queue or GPU compute.

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

## Current staging checkpoint

As of 2026-07-26, the foundation is deployed in `eu-north-1`. The compute environment and queue are `VALID`,
the queue is `DISABLED`, submission is `false`, and job definition
`watermark-staging-worker:3` references the remotely built LaMa import-fix image digest
`sha256:596493d0665a053a52861ad0b3b585700c2f329238d01ae4a5583e82c656d95d`.
Revision 3 retains the writable Hugging Face and Transformers cache paths from revision 2.
The post-apply Terraform plan reports no changes. Live checks found zero jobs, EC2 instances, EBS
volumes, ECS container instances/tasks, Spot requests, NAT gateways, Elastic IPs, and load
balancers. Redacted evidence is in `aws-acceptance/evidence/a6-foundation/`.

A7 one-image acceptance completed successfully on revision 3 on 2026-07-26. The GPU worker
processed one PNG through background removal, detection, and LaMa inpainting, wrote its final image
and completion manifests to GCS, exited `0`, and automatically terminated its one `g4dn.xlarge`.
The queue and submission gate were then returned to their disabled defaults. Final evidence is in
`aws-acceptance/evidence/a7-one-image/lama-fix-run-outcome.json`.
