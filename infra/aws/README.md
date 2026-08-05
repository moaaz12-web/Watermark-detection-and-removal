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

When an image digest is configured, Terraform defines four trusted pipeline identities with
packaged-config compatibility profiles and external-config command profiles:

- `watermark-v1`, retaining the local-config compatibility path, capped by its five-item
  configuration with a 90-minute timeout and one attempt; and
- `watermark-bulk-v1`, retaining the local-config compatibility path, capped by its 10,000-item
  configuration with a 24-hour per-attempt
  timeout and two attempts. The bulk definition retries EC2 host failures and worker exit codes
  `6` and `7`, which represent retryable storage and external-service failures. All other worker
  exits fail immediately.

The OCR-enabled successors `watermark-v2` and `watermark-bulk-v2` use the same resource envelopes
but receive a generation- and SHA-pinned YAML reference from the private
`watermark_config_bucket`/`watermark_config_prefix`. They remain separately allowlisted so v1
jobs retain their historical detector behavior.

The submission Lambda maps each exact pipeline identity to its dedicated job-definition ARN and
validates the external config prefix. A caller cannot supply an arbitrary job definition,
configuration file, or tuning value.

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
6. A private GCS config bucket/prefix supplied as `watermark_config_bucket` and
   `watermark_config_prefix`; the same prefix must appear in `gcs_worker_read_prefixes`.

Before enabling OCR jobs, publish the four immutable YAML snapshots from the repository and
record the command's JSON output in the backend's `WATERMARK_CONFIG_REFS_JSON` mapping:

```bash
uv run python scripts/publish_pipeline_configs.py \
  --bucket OWNER_SUPPLIED_CONFIG_BUCKET \
  --release 2026-08-05-initial
```

The command is create-only; changing a threshold later means publishing a new release and
changing only the server-side mapping. It does not build or push a worker image.

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

The bulk-worker deployment completed on 2026-07-28. CodeBuild tag
`bulk-20260728t172927z` resolves to immutable ECR digest
`sha256:f3e14394f94df90845ea840983baabeda988a40a366172058cac030cf59241b1`.
The deployed definitions are `watermark-staging-worker:4` for `watermark-v1` and
`watermark-staging-worker-bulk:1` for `watermark-bulk-v1`. Lambda is active, accepts both exact
pipeline identities, and remains configured with `SUBMISSION_ENABLED=false`. The queue and compute
environment are `DISABLED`, desired vCPUs are zero, and the post-apply Terraform plan reports no
changes. Final checks found no active Batch jobs or `g4dn.xlarge` instances.

The first bulk CodeBuild attempt failed before image creation because the Dockerfile referenced an
architecture-specific uv manifest that BuildKit could not resolve from GHCR. The Dockerfiles now
pin uv `0.10.9` using its immutable multi-platform index digest. During the first Terraform apply,
the AWS provider normalized hidden empty arrays in one job definition and rejected a second
definition that copied the first resource's computed JSON. `batch-job.tf` now constructs both
definitions from one local container-property value, avoiding that provider-dependent
cross-resource normalization. The corrected saved plan applied successfully.
