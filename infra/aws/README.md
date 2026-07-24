# AWS submission boundary infrastructure

This Terraform root deploys A3 only: the authenticated HTTP API, submission Lambda, DynamoDB
idempotency ledger, logs, alarms, and dedicated GCP orchestrator service account.

It intentionally contains no Batch compute environment, EC2 instance, GPU, NAT gateway, EBS
volume, ECR repository, or Batch permission. `submission_enabled` defaults to `false`.

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
