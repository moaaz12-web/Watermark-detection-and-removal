# AWS GPU Regional Migration Plan: Stockholm to Ohio

**Status:** Proposed. This document is a plan, not an operator runbook.

**Source AWS region:** `eu-north-1` (Stockholm)

**Target AWS region:** `us-east-2` (Ohio)

**GCP region:** `us-central1` remains unchanged

**Primary repository:** `/home/maaz/Desktop/watermarkPoc`

**FastAPI repository:** `/home/maaz/Desktop/infringement_project/infringement_backend`

## 1. Objective

Move the regional AWS GPU execution stack from Stockholm to Ohio while keeping the application,
database, control plane, manifests, input images, and results in GCP `us-central1`.

The target flow remains:

```text
FastAPI and PostgreSQL in GCP us-central1
    |
    v
GCP watermark control plane and orchestrator in us-central1
    |
    | authenticated HTTPS request
    v
AWS API Gateway and Lambda in us-east-2
    |
    v
AWS Batch in us-east-2
    |
    v
Ephemeral g4dn.xlarge GPU instance in us-east-2
    |
    | reads and writes over HTTPS
    v
Existing GCS bucket in us-central1
```

This is a regional runtime migration. It is not a redesign of the proven pipeline.

## 2. Decisions

1. Keep all existing GCP resources and customer data in `us-central1`.
2. Build a second AWS stack in `us-east-2` beside the Stockholm stack.
3. Keep `g4dn.xlarge`, On-Demand EC2, one GPU per Batch job, `min_vcpus=0`,
   `desired_vcpus=0`, and `max_vcpus=4` for the migration.
4. Do not combine the region move with a `g5` or `g6` instance-family change.
5. Keep the same JWT issuer, JWT audience, application contracts, pipeline versions, GCS bucket,
   and GCS object layout.
6. Reuse the existing AWS GPU job role and GCP Workload Identity Federation relationship.
7. Give Ohio its own regional ECR repositories, image digest, CodeBuild project, submission
   ledger, API Gateway endpoint, Lambda, Batch resources, networking, logs, and alarms.
8. Cut over through configuration only after Ohio passes controlled tests.
9. Retain Stockholm in a disabled, zero-capacity rollback state for a short observation period.
10. Decommission Stockholm only after Ohio is proven and no active jobs depend on Stockholm.

## 3. What will not move

| Component | Decision | Reason |
|---|---|---|
| FastAPI Cloud Run service | Keep in `us-central1` | It is already colocated with the core application and database. |
| Cloud SQL PostgreSQL | Keep in `us-central1` | Moving the database is a separate, high-risk data migration. |
| GCS watermark data bucket | Keep in `us-central1` | The existing bucket location cannot be changed in place, and GCP remains the data authority. |
| Watermark control-plane job | Keep in `us-central1` | It reads and updates the GCP database and launches GCP jobs. |
| Watermark bulk preparer job | Keep in `us-central1` | It reads application data and creates manifests and inputs in GCS. |
| Watermark AWS orchestrator job | Keep in `us-central1` | Only its AWS endpoint and provider-region configuration change. |
| GCP scheduler | Keep unchanged | Its target remains the GCP control plane. |
| GCP WIF pool and provider | Reuse | They trust the AWS account and the exact GPU job role, not an EC2 region. |
| Frontend | No change | The frontend continues to call the same FastAPI endpoints. |

## 4. Why this is not a one-line region change

`infra/aws/providers.tf` currently gives the entire Terraform root one AWS provider region, and
`infra/aws/variables.tf` explicitly accepts only `eu-north-1`. The same Terraform state contains:

- regional AWS resources, such as VPC, Batch, ECR, DynamoDB, Lambda, API Gateway, CodeBuild, and
  CloudWatch resources;
- global AWS IAM resources; and
- shared GCP service-account, WIF, and GCS IAM resources.

Changing only `aws_region` to `us-east-2` would cause Terraform to look for the existing
Stockholm resource IDs through Ohio service endpoints. That can create unmanaged Stockholm
resources or an unsafe replacement plan. The migration must create a separately managed Ohio
runtime and must not mutate the provider region underneath the existing state.

## 5. Terraform state and code structure

Use a two-root structure during the migration:

```text
infra/aws/
    Existing state: watermark/aws/staging
    Owns the current Stockholm stack and shared AWS/GCP identities

infra/aws/regions/us-east-2/
    New state: watermark/aws/staging/us-east-2
    Owns the Ohio runtime and Ohio-specific support roles

infra/aws/modules/regional-runtime/
    Reusable implementation for regional submission, image-build, and Batch resources
```

The target root must not recreate resources already owned by the existing state.

### 5.1 Shared resources that remain in the existing state

- `watermark-staging-gpu-job-role`
- the AWS Batch service-linked role
- `watermark-aws-orchestrator` GCP service account
- the GCP Workload Identity Pool and AWS provider
- the WIF binding on the GCP service account
- prefix-scoped GCS IAM grants for the GPU worker

Before Ohio is deployed, change the shared GPU job role trust policy from one fixed ECS region to
an explicit set of trusted regions:

```hcl
gpu_job_trusted_regions = ["eu-north-1", "us-east-2"]
```

After Stockholm is decommissioned, reduce it to:

```hcl
gpu_job_trusted_regions = ["us-east-2"]
```

The GCP WIF condition can remain tied to the same AWS role name, so no new key, WIF pool, or GCP
service account is required.

### 5.2 Ohio-specific resources

The target state owns:

- Ohio VPC, two public subnets in different Availability Zones, Internet Gateway, routes, and the
  no-ingress Batch security group;
- Ohio-specific Batch instance role/profile and task execution role;
- ECR worker and BuildKit-cache repositories;
- a region-suffixed S3 CodeBuild source bucket, because S3 bucket names are globally unique;
- CodeBuild project, role, and log group;
- Batch launch template, managed compute environment, queue, worker definitions, and worker logs;
- DynamoDB submission ledger;
- submission Lambda and its Ohio-specific IAM role;
- HTTP API Gateway, native Google JWT authorizer, stage, routes, and access logs; and
- Lambda error and throttle alarms.

Use Ohio-specific names for globally named support resources, for example:

```text
watermark-staging-batch-instance-role-us-east-2
watermark-staging-batch-execution-role-us-east-2
watermark-staging-submission-lambda-role-us-east-2
watermark-staging-worker-image-build-role-us-east-2
watermark-staging-codebuild-source-154959837939-us-east-2
```

Regional resources may retain the current logical names because AWS separates them by region.

## 6. Migration phases

### Phase M0: Freeze the migration contract

**Description:** Record exactly what is moving and capture the source state before changing
Terraform.

**Tasks:**

1. Create a new ADR that accepts `us-east-2` as the operational GPU region and supersedes only the
   region decision in ADR-004.
2. Record the current Terraform state bucket and prefix without committing private backend
   configuration.
3. Record current Stockholm outputs: API URL, role ARN, WIF audience, impersonation URL, ECR
   digest, Batch queue, job definitions, and enable/disable state.
4. Record active Batch jobs and EC2 instances.
5. Confirm no migration starts while an application job is non-terminal.

**Acceptance criteria:**

- [ ] Source configuration and live resources are inventoried.
- [ ] Existing Terraform state remains readable and locked correctly.
- [ ] No active job is using the Stockholm stack.
- [ ] The current GCP service-account and WIF identifiers are recorded.

**Likely files:**

- `docs/decisions/ADR-005-aws-gpu-region-us-east-2.md`
- `docs/productionization/10-aws-gpu-regional-migration-plan.md`

**Estimated scope:** Small.

### Phase M1: Make shared identity region-aware

**Description:** Allow the one existing GPU task identity to be assumed by ECS tasks from both
regions during the migration window.

**Tasks:**

1. Add `gpu_job_trusted_regions` with exact validation for the approved source and target regions.
2. Generate the GPU job role `aws:SourceArn` trust values from this explicit set.
3. Keep the GCP WIF provider condition tied to the unchanged GPU job role name.
4. Add tests proving that arbitrary regions are rejected and both approved regions are represented
   during migration.
5. Apply this shared-only change and confirm the plan contains no regional resource replacement.

**Acceptance criteria:**

- [ ] The role name and ARN do not change.
- [ ] The GCP WIF pool/provider and GCS bindings are not replaced.
- [ ] The trust policy permits only the AWS account and the two approved ECS regions.
- [ ] The Terraform apply changes only the intended trust policy and related outputs/tests.

**Likely files:**

- `infra/aws/variables.tf`
- `infra/aws/gpu-job-role.tf`
- `infra/aws/environments/staging.tfvars.example`
- `tests/unit/test_aws_batch_terraform.py`

**Estimated scope:** Medium.

### Checkpoint 1: Shared identity safety

- [ ] `terraform plan` is reviewed and contains no destroy or replacement of WIF, service account,
      GPU role, GCS IAM, or Stockholm runtime resources.
- [ ] Worker role trust contains `eu-north-1` and `us-east-2`, and no wildcard region.
- [ ] Stockholm remains operational and has zero running GPU instances.

### Phase M2: Add the isolated Ohio Terraform root

**Description:** Create an independently managed target stack without changing the Stockholm
provider or state.

**Tasks:**

1. Create `infra/aws/modules/regional-runtime/` from the current regional implementation for the
   Ohio target. Do not move the existing Stockholm resource addresses or state during the
   migration.
2. Create `infra/aws/regions/us-east-2/` with its own GCS backend prefix.
3. Pass the stable shared GPU role ARN, WIF audience, GCP impersonation URL, and orchestrator
   service-account unique ID into the target root as validated inputs.
4. Create Ohio-specific IAM names for Lambda, CodeBuild, Batch host, and Batch execution.
5. Add the `us-east-2` environment example with:

   ```hcl
   submission_enabled  = false
   batch_queue_enabled = false
   worker_image_digest = ""
   ```

6. Preserve the current GCS manifest and output allowlists and both approved pipeline versions.
7. Update tests so region-sensitive behavior is configuration-driven instead of embedding
   Stockholm in fixtures or worker defaults.

**Acceptance criteria:**

- [ ] Existing Stockholm state addresses and resources are unchanged.
- [ ] Ohio initializes against a different Terraform state prefix.
- [ ] Ohio cannot create a job definition before an immutable image digest is supplied.
- [ ] Ohio submission and Batch compute default to disabled.
- [ ] No direct EC2 instance, NAT Gateway, Elastic IP, SSH key, or standalone EBS volume is
      declared.

**Likely files:**

- `infra/aws/modules/regional-runtime/*.tf`
- `infra/aws/regions/us-east-2/*.tf`
- `infra/aws/regions/us-east-2/environments/staging.tfvars.example`
- `infra/aws/scripts/start-worker-image-build.sh`
- `services/aws_submission/src/aws_submission/handler.py`
- `src/watermark_worker/config.py`
- region-sensitive unit tests under `tests/` and `services/aws_submission/tests/`

**Estimated scope:** Large and should be implemented as several small commits.

### Phase M3: Provision the disabled Ohio foundation

**Description:** Apply the target infrastructure without building an image, accepting submissions,
or launching a GPU.

**Preflight:**

1. Reauthenticate the AWS CLI and verify account `154959837939`.
2. Recheck the Ohio On-Demand G and VT quota. The last verified value was 4 vCPUs, exactly enough
   for one `g4dn.xlarge`.
3. Recheck `g4dn.xlarge` regional and Availability Zone offerings.
4. Check for existing name collisions and any unexpected Ohio resources.
5. Build the submission Lambda package locally.

**Apply boundary:**

- `submission_enabled=false`
- `batch_queue_enabled=false`
- `worker_image_digest=""`
- Batch `min_vcpus=0`
- Batch `desired_vcpus=0`
- Batch `max_vcpus=4`

**Acceptance criteria:**

- [ ] Ohio API Gateway, Lambda, DynamoDB, ECR, CodeBuild, networking, and Batch foundation exist.
- [ ] The Batch compute environment and queue are `VALID` and `DISABLED`.
- [ ] No Batch job definition exists while the digest is empty.
- [ ] No EC2 instance, GPU, standalone volume, NAT Gateway, or Elastic IP was launched.
- [ ] Lambda and API Gateway log retention and alarms exist in Ohio.

**Estimated scope:** Medium.

### Checkpoint 2: Zero-cost GPU boundary

- [ ] Both regions report zero active Batch GPU jobs.
- [ ] Both regions report zero running or pending `g4dn.xlarge` instances.
- [ ] Ohio desired vCPUs are zero.
- [ ] Terraform reports no unexpected changes in either state.

### Phase M4: Build and pin the Ohio worker image

**Description:** Build the same reviewed source commit inside Ohio and pin its immutable Ohio ECR
digest.

**Tasks:**

1. Package the exact tested Git commit into the small CodeBuild source ZIP.
2. Upload it to the Ohio region-suffixed source bucket.
3. Start the Ohio CodeBuild project.
4. Build and push the release image and BuildKit cache to Ohio ECR.
5. Record the release tag, source commit, image manifest, and immutable digest.
6. Set the Ohio `worker_image_digest`.
7. Apply the digest-gated `watermark-v1` and `watermark-bulk-v1` Batch job definitions.
8. Keep the queue and submission API disabled.

Do not rely on enabling ECR replication after the fact for the existing Stockholm image. Existing
images are not copied retroactively by new ECR replication rules. A target-region CodeBuild run is
the simplest auditable path and avoids a multi-gigabyte local upload.

**Acceptance criteria:**

- [ ] The target image was built from the intended commit.
- [ ] Both Ohio Batch definitions reference the Ohio ECR repository by digest, not by mutable tag.
- [ ] The bulk definition retains the 24-hour per-attempt timeout and current retry policy.
- [ ] No GPU instance starts during the build or definition apply.

**Estimated scope:** Medium.

### Phase M5: Validate the Ohio submission boundary while disabled

**Description:** Prove authentication, routing, validation, and the kill switch before allowing a
billable job.

**Tasks:**

1. Confirm requests without a Google ID token are rejected.
2. Confirm a wrong audience is rejected.
3. Confirm a valid orchestrator token reaches Lambda.
4. Confirm a valid submission receives the stable disabled response while
   `submission_enabled=false`.
5. Confirm the new DynamoDB ledger is empty before cutover.
6. Confirm the exact GCS manifest/output allowlists and pipeline allowlist are configured.

**Acceptance criteria:**

- [ ] JWT issuer and audience behavior matches Stockholm.
- [ ] The GCP orchestrator identity is the only accepted caller identity.
- [ ] Disabled submission cannot create a Batch job.
- [ ] API and Lambda logs contain correlation identifiers without credentials or customer data.

**Estimated scope:** Small.

### Phase M6: Prepare GCP configuration

**Description:** Prepare a reversible configuration-only cutover. No GCP region or data migration
occurs.

**Target values:**

```text
AWS_GPU_API_BASE_URL=https://<ohio-api-id>.execute-api.us-east-2.amazonaws.com/staging
AWS_GPU_REGION=us-east-2
AWS_GPU_API_AUDIENCE=urn:watermark:aws-gpu-submission:staging
```

Update these GCP resources:

| GCP resource | Required change |
|---|---|
| `infringementdetection` Cloud Run service | Set Ohio API URL and `AWS_GPU_REGION=us-east-2`. |
| `watermark-control-plane` Cloud Run job | Set Ohio API URL and region. |
| `watermark-aws-orchestrator` Cloud Run job | Set Ohio API URL and region. |
| `watermark-bulk-preparer` Cloud Run job | No AWS call is made. Update shared AWS values only if its deployed settings include them, so all revisions remain consistent. |

Do not change:

- `WATERMARK_MANIFEST_BUCKET`
- `WATERMARK_INPUT_BUCKET`
- `WATERMARK_OUTPUT_BUCKET`
- GCS prefixes
- Cloud SQL connection
- `CLOUD_RUN_JOBS_LOCATION`
- JWT audience
- pipeline versions

The FastAPI code and container image should not need a region-specific code change. This phase is
configuration deployment only unless validation finds a remaining hard-coded Stockholm default.

**Acceptance criteria:**

- [ ] The exact previous Stockholm values are recorded for rollback.
- [ ] Proposed Cloud Run revisions change only the intended AWS endpoint/region settings.
- [ ] The feature can be temporarily paused while the endpoint changes.
- [ ] No database migration is required.

**Estimated scope:** Small.

### Phase M7: Controlled cutover

**Description:** Quiesce new work, switch GCP to Ohio, and run the first billable Ohio tests.

**Sequence:**

1. Temporarily disable creation/dispatch of new watermark jobs.
2. Wait until every existing watermark job is terminal.
3. Confirm Stockholm has no active Batch job or GPU instance.
4. Enable the Ohio Batch queue and compute environment.
5. Enable Ohio submission.
6. Deploy the prepared GCP Cloud Run configurations pointing to Ohio.
7. Run one controlled single-image job.
8. Verify:
   - one FastAPI job;
   - one Ohio DynamoDB reservation;
   - one Ohio Batch job;
   - the correct manifest generation;
   - expected GCS outputs and completion marker;
   - correct PostgreSQL reconciliation; and
   - automatic GPU termination.
9. Replay the same idempotency key and verify it adopts the same reservation and does not create a
   second Batch job.
10. Run the established 15-product timing test for store `2`.
11. Compare source and target timings:
    - FastAPI submission to preparer start;
    - preparation duration;
    - prepared to Ohio submission;
    - Batch submission to EC2 start;
    - image pull;
    - model load;
    - image processing;
    - GCS output and PostgreSQL reconciliation; and
    - EC2 termination.
12. Re-enable normal job creation only after both controlled tests pass.

**Acceptance criteria:**

- [ ] The one-image job reaches the correct terminal FastAPI state.
- [ ] Idempotent replay creates no duplicate AWS job.
- [ ] The product limit is not exceeded in the 15-product test.
- [ ] Every selected image is independently reconciled as succeeded, skipped, blocked, or failed.
- [ ] No output path or API contract changes.
- [ ] Ohio returns to zero running GPU instances and desired vCPUs zero after the test.
- [ ] No new job was submitted to Stockholm after cutover.

**Estimated scope:** Medium.

### Checkpoint 3: Cutover decision

Proceed only if:

- [ ] Both controlled tests passed.
- [ ] Cross-cloud authentication worked without new credentials.
- [ ] GCS reads/writes and PostgreSQL reconciliation are correct.
- [ ] Ohio logs and alarms are visible.
- [ ] Ohio has zero GPU instances after completion.
- [ ] The measured timing is accepted, even if some phases did not materially improve.

If any item fails, execute the rollback in Section 8.

### Phase M8: Observation window

**Description:** Use Ohio operationally while retaining a fast rollback path.

1. Keep Stockholm submission disabled.
2. Keep the Stockholm Batch queue disabled with desired vCPUs zero.
3. Retain Stockholm API, ledger, logs, ECR image, and Batch definitions for seven days or at least
   two successful representative bulk jobs, whichever is later.
4. Monitor Ohio Lambda errors/throttles, Batch failures, stuck jobs, FastAPI reconciliation, and
   zero-capacity behavior.
5. Do not copy the Stockholm DynamoDB ledger into Ohio. The cutover occurs with no active jobs,
   PostgreSQL remains authoritative, and old ledger records remain available in Stockholm for
   investigation.

**Acceptance criteria:**

- [ ] At least two representative jobs complete through Ohio.
- [ ] No job remains stuck in a non-terminal state.
- [ ] No unexpected Stockholm API submission occurs.
- [ ] Both regions remain free of idle GPU instances.

**Estimated scope:** Operational observation, no code work expected.

### Phase M9: Decommission Stockholm

**Description:** Remove only the obsolete Stockholm runtime after Ohio is accepted.

**Tasks:**

1. Confirm zero active Stockholm jobs and zero Stockholm GPU instances.
2. Preserve required Terraform outputs, logs, and acceptance evidence.
3. Use a reviewed Terraform plan to delete Stockholm regional resources and source-specific IAM
   support roles.
4. Keep shared GPU role, Batch service-linked role, GCP service account, WIF provider, and GCS IAM
   grants.
5. Reduce GPU role trust to `us-east-2` only.
6. Remove Stockholm endpoint/region values from active runbooks and examples.
7. Preserve historical evidence files that correctly describe earlier Stockholm tests.
8. Run cross-region cleanup checks for EC2 instances, EBS volumes, ENIs, public IPs, Batch jobs,
   and unexpected capacity.

**Acceptance criteria:**

- [ ] No Stockholm runtime resource remains unless explicitly retained for logs/evidence.
- [ ] Shared GCP identity and data resources still exist.
- [ ] Ohio remains operational after the source removal.
- [ ] Both regions have zero idle GPU instances.
- [ ] Terraform plans for the shared/source state and Ohio state are both clean.

**Estimated scope:** Medium.

## 7. Repository impact

### `/home/maaz/Desktop/watermarkPoc`

Expected changes:

- make region validation configuration-driven;
- add the Ohio Terraform root and regional module;
- update AWS IAM trust boundaries for the controlled two-region window;
- make test fixtures use configured regions instead of hard-coded Stockholm ARNs;
- build and pin a new Ohio ECR image digest;
- add Ohio-specific operational outputs;
- update active runbooks after cutover;
- add an ADR for the accepted region change; and
- retain historical acceptance evidence unchanged.

No model or pipeline algorithm change is planned.

### `/home/maaz/Desktop/infringement_project/infringement_backend`

Expected changes:

- normally no Python code change;
- update deployed Cloud Run environment variables for the API, control plane, and orchestrator;
- update the bulk preparer only if it carries the shared AWS settings;
- preserve all GCS, Cloud SQL, feature, and pipeline settings; and
- run existing configuration and watermark tests before deploying the revised Cloud Run
  definitions.

No database migration is planned.

### Frontend

No change is expected. The public FastAPI routes and responses do not change.

## 8. Rollback

Rollback remains available until Stockholm is decommissioned.

1. Pause new watermark job creation or dispatch.
2. Disable Ohio submission so it cannot reserve another Batch job.
3. Allow a known Ohio job to finish, or cancel it through the existing provider-control path.
4. Verify Ohio reaches zero GPU capacity.
5. Restore the recorded Cloud Run values:

   ```text
   AWS_GPU_API_BASE_URL=<stockholm-api-url>
   AWS_GPU_REGION=eu-north-1
   ```

6. Enable the Stockholm Batch queue and submission boundary.
7. Run one controlled smoke job through Stockholm.
8. Resume normal job creation.
9. Preserve Ohio logs and DynamoDB records for diagnosis. Do not delete evidence during the
   incident.

Rollback does not require moving GCS objects or PostgreSQL rows because those remain in GCP
throughout the migration.

## 9. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Existing Terraform provider is changed in place | Stockholm resources become orphaned or an unsafe plan is produced | Use a separate Ohio root and state prefix. |
| Global IAM or GCP WIF is created in both states | Resource ownership conflict and accidental deletion risk | Reuse the existing GPU role and WIF resources; create only region-suffixed support roles in the target state. |
| Target image is unavailable in Ohio | Batch cannot pull the worker | Build once in Ohio CodeBuild and pin the target ECR digest. |
| `g4dn.xlarge` capacity is unavailable in one AZ | Batch waits or retries placement | Use two Ohio Availability Zones and verify offerings before apply. |
| Ohio GPU quota changes or is insufficient | EC2 cannot launch | Recheck the regional On-Demand G and VT quota immediately before the live test. |
| Cutover occurs with an active Stockholm job | Reconciliation may query the wrong provider region | Quiesce submissions and require all jobs to be terminal before switching GCP. |
| New endpoint accepts traffic too early | Unvalidated GPU jobs may launch | Keep queue and submission disabled until the controlled test window. |
| Region hard-coding remains in code/tests/runbooks | Wrong ARNs, logs, or endpoint may be used | Search the repository, convert runtime defaults to explicit configuration, and update only active documentation. |
| Cross-cloud transfer remains a bottleneck | Migration yields less improvement than expected | Compare phase timings. The migration targets geographic distance, not image pull or model-load time. |
| Old region continues to cost money | Unexpected idle resources accumulate | Keep min/desired vCPUs zero, disable the old queue/API, and perform explicit EC2/EBS/ENI/public-IP checks. |

## 10. Final definition of done

The migration is complete only when:

- [ ] GCP FastAPI, Cloud SQL, GCS, control plane, preparer, and orchestrator remain in
      `us-central1`.
- [ ] The active AWS submission endpoint, Lambda, DynamoDB ledger, ECR image, CodeBuild project,
      Batch queue, compute environment, and logs are in `us-east-2`.
- [ ] The worker still uses the same GCP WIF identity and GCS prefix permissions.
- [ ] One-image, idempotency-replay, and 15-product tests pass through Ohio.
- [ ] Results appear in the existing GCS layout and reconcile into PostgreSQL.
- [ ] The GPU instance terminates after every test and no idle GPU exists.
- [ ] Stockholm receives no new submissions.
- [ ] The rollback path was retained through the observation window.
- [ ] Stockholm regional resources are removed after acceptance.
- [ ] Active documentation and Cloud Run configuration identify `us-east-2` as the provider region.
- [ ] Both Terraform states produce clean, reviewed plans.

## 11. Implementation order

The work must remain sequential because Terraform state, identity trust, image digest, provider
endpoint, and active-job state depend on one another:

```text
M0 inventory
  -> M1 shared identity
  -> M2 isolated Ohio IaC
  -> M3 disabled Ohio foundation
  -> M4 Ohio image and job definitions
  -> M5 disabled API validation
  -> M6 prepared GCP configuration
  -> M7 controlled cutover
  -> M8 observation
  -> M9 Stockholm decommission
```

Do not combine M7 and M9. The old stack is the rollback mechanism until the target has been proven
with real workloads.

## 12. References

- AWS EC2 quotas are regional:
  https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-resource-limits.html
- AWS EC2 instance-type offerings vary by region and Availability Zone:
  https://docs.aws.amazon.com/ec2/latest/instancetypes/ec2-instance-regions.html
- Existing ECR images are not replicated retroactively:
  https://docs.aws.amazon.com/AmazonECR/latest/userguide/replication.html
- Cloud Run services and jobs are regional:
  https://cloud.google.com/run/docs/locations
- An existing GCS bucket location cannot be changed in place:
  https://cloud.google.com/storage/docs/faq
