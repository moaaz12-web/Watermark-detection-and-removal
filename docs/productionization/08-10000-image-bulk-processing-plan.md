# 10,000-image bulk watermark processing plan

**Status:** Backend B1-B8 and worker/AWS W1-W7 implemented; AWS worker image, Lambda, and Batch
definitions deployed disabled; measured GPU rollout remains pending  
**Date:** 2026-07-28  
**Backend implementation note (2026-07-28):** The FastAPI repository now contains the B1-B8
models, migration, store-scoped API, resumable CPU preparer command, durable control-plane path,
bounded output reconciliation, configuration, and tests. The feature defaults disabled. Migration
application, Cloud Run deployment/configuration, and all worker/AWS changes are intentionally not
part of that local implementation.

**Worker implementation note (2026-07-28):** This repository now contains the
`watermark-bulk-v1` contract and packaged configuration, bounded one-image-at-a-time worker loop,
all-resident model lifecycle, terminal-item retry adoption, scalable final publication, exact
pipeline-to-Batch-definition selection, and a separate 24-hour bulk Batch definition. The bulk
definition uses `g4dn.xlarge`, allows two attempts, and retries only EC2 host failures or the
worker's explicit retryable exit codes `6` and `7`. Dependency-light tests cover the 10,000-item
resident lifecycle, bounded decode behavior, interruption/adoption, final publication, and the
10,001-item rejection. No image was built, infrastructure applied, queue enabled, Batch job
submitted, or GPU started as part of this local implementation. Container/GPU memory measurements
and the controlled rollout in Phase 6 remain required.

**AWS deployment checkpoint (2026-07-28):** CodeBuild pushed tag
`bulk-20260728t172927z` with immutable digest
`sha256:f3e14394f94df90845ea840983baabeda988a40a366172058cac030cf59241b1`.
Terraform deployed `watermark-staging-worker:4`,
`watermark-staging-worker-bulk:1`, the exact pipeline-to-definition Lambda mapping, and the updated
scoped IAM policy. Lambda is active and its last update succeeded. The Batch queue and compute
environment are both `DISABLED`, `submission_enabled` is `false`, desired vCPUs are zero, and the
post-apply Terraform plan has no changes. Live checks found no active Batch job or `g4dn.xlarge`
instance. No GPU acceptance job was submitted.

**Worker architecture decision (2026-07-28):** The owner reports that the complete enabled model
set fits successfully on a Google Colab NVIDIA T4. Because the AWS `g4dn.xlarge` also uses a
16-GiB T4, the bulk-worker plan now keeps every enabled model resident for the attempt and processes
one image end-to-end at a time. Record the benchmark's exact peak VRAM, host RAM, image dimensions,
and software versions with the rollout evidence before enabling a large AWS run. See
`docs/decisions/ADR-005-all-resident-models-per-item-execution.md`.

**Repositories:**

- GPU worker and AWS infrastructure: `/home/maaz/Desktop/watermarkPoc`
- FastAPI application and GCP control plane:
  `/home/maaz/Desktop/infringement_project/infringement_backend`

## 1. Objective

Extend the proven five-image watermark workflow so one application job can safely process as many
as 10,000 images over several hours.

The first implementation keeps:

- GCP and PostgreSQL as the application control plane and system of record;
- GCS as the authoritative input, terminal-item, and output store;
- the existing GCP-to-AWS authenticated submission boundary;
- one long AWS Batch GPU execution for one application job;
- one model initialization at worker startup and one-image-at-a-time execution; and
- scale-to-zero GPU compute after the Batch job finishes.

This is not accomplished by only changing `maximum_items` from `5` to `10000`. Both repositories
need bounded-memory execution, asynchronous input preparation, scalable completion reconciliation,
new tests, and a longer Batch timeout.

## 2. Scope assumption

The primary 10,000-image source is a store/catalog bulk selection, such as Shopify product media.
The existing manual API that accepts already registered `WatermarkAsset` IDs should remain capped
at five images initially.

If the first bulk source is instead 10,000 already registered assets, only the FastAPI target
selection and preparation input changes. The worker, GCS terminal-item, AWS Batch, output, and
reconciliation design remains the same.

## 3. What stays unchanged

- FastAPI owns users, accounts, stores, authorization, job state, and result access.
- AWS never connects to PostgreSQL.
- AWS never downloads mutable Shopify/CDN URLs directly.
- The short `watermark-aws-orchestrator` Cloud Run job remains the component that calls the AWS
  submission API.
- API Gateway, Lambda, DynamoDB idempotency, AWS Batch, ECR, and WIF retain their existing roles.
- Inputs and outputs remain under deterministic account/job GCS prefixes.
- The GPU VM is created for the Batch job and removed after the job reaches a terminal state.
- The existing one-to-five-image workflow remains available while bulk processing is rolled out.

## 4. Original blockers and current implementation state

| Area | Original behavior | Why it failed at 10,000 |
|---|---|---|
| FastAPI request schema | `asset_ids` has `max_length=5` | The request is rejected |
| FastAPI settings | `WATERMARK_MAX_ITEMS_PER_JOB` cannot exceed `5` | Configuration cannot enable bulk |
| HTTP job creation | Copies every selected asset and writes the manifest before returning | 10,000 GCS operations do not belong in an HTTP request |
| Worker configuration | `watermark-v1` accepts at most five items | The worker rejects the manifest |
| Worker input handling | Downloads and decodes every pending image before inference | RAM grows with the whole manifest |
| Worker pipeline | Retains all RMBG and detection outputs between stages | RAM grows with the whole manifest |
| Worker final validation | Re-reads per-item markers and results while publishing the job manifest | Tens of thousands of GCS reads |
| FastAPI JSON reader | Output JSON is limited to 1 MiB | A 10,000-entry output manifest will probably exceed it |
| FastAPI reconciliation | Reads result and marker JSON separately for every item | Roughly 20,000 sequential reads in one pass |
| AWS Batch job | One attempt and a 5,400-second timeout | The accepted 90-minute envelope is too short |
| Proven throughput | Only one- and five-image runs exist | A 10,000-image duration cannot yet be promised |

The current worker loops over sources, but it is not memory-bounded: `cli.py` keeps every decoded
pending image in `prepared`/`pending`, and `pipeline.py` keeps stage-output dictionaries for the
whole manifest. The T4 residency benchmark removes the earlier reason for rejecting an end-to-end
item loop. The target is now to load the complete enabled model set once, retain it for the Batch
attempt, and process one image end-to-end before reading the next image. This bounds image and
intermediate memory without reloading models per item.

Relevant current files:

- FastAPI request:
  `/home/maaz/Desktop/infringement_project/infringement_backend/app/api/routes/watermark_jobs.py`
- FastAPI schema:
  `/home/maaz/Desktop/infringement_project/infringement_backend/app/schemas/watermark_jobs.py`
- FastAPI settings:
  `/home/maaz/Desktop/infringement_project/infringement_backend/app/core/config.py`
- FastAPI output validation:
  `/home/maaz/Desktop/infringement_project/infringement_backend/app/watermark/output_validation.py`
- Worker CLI:
  `/home/maaz/Desktop/watermarkPoc/src/watermark_worker/cli.py`
- Worker executor:
  `/home/maaz/Desktop/watermarkPoc/src/watermark_worker/pipeline.py`
- Worker configuration:
  `/home/maaz/Desktop/watermarkPoc/configs/watermark-v1.yaml`
- Batch definition:
  `/home/maaz/Desktop/watermarkPoc/infra/aws/batch-job.tf`

## 5. Target architecture

```text
Authenticated FastAPI request
        |
        | creates PREPARING parent job, freezes selection settings,
        | and writes GCS_INPUT_PREPARATION_REQUESTED
        v
PostgreSQL durable intent
        |
        | the periodically scheduled watermark-control-plane notices it
        | (with a one-minute schedule, normal pickup delay is about 0-60 seconds)
        v
Launch an execution of the already-deployed watermark-bulk-preparer Cloud Run Job
        v
GCP watermark bulk preparer
        |
        | freezes exact catalog media into target rows
        | downloads/validates one bounded set at a time
        | writes immutable job inputs to GCS
        | creates item rows
        | writes one immutable input manifest
        | changes PREPARING -> QUEUED
        | writes AWS_GPU_SUBMISSION_REQUESTED only after the manifest is pinned
        v
PostgreSQL durable AWS submission intent
        |
        | the next scheduled watermark-control-plane execution notices it
        v
Launch an execution of the already-deployed watermark-aws-orchestrator Cloud Run Job
        | authenticated GCP-to-AWS request
        v
AWS submission API -> DynamoDB reservation -> one AWS Batch job
        |
        v
One ephemeral GPU EC2 instance
        |
        | download and verify model artifacts once
        | load RMBG, GroundingDINO, YOLO, SAM2, and LaMa once
        | stream one manifest item at a time
        | run that item end-to-end and publish its terminal marker
        | release item tensors/images, but retain the models
        v
Immutable output manifest + job completion marker in GCS
        |
        | scheduled watermark-control-plane polls AWS and, after provider success,
        | reads the completion marker and exact pinned output manifest
        v
FastAPI/GCP control-plane reconciliation
        |
        | validates the self-contained output summary
        | updates item rows in bounded database batches
        v
PostgreSQL SUCCEEDED/FAILED item results
        |
        v
GPU EC2 instance terminates
```

There is one user-visible parent job and initially one AWS Batch provider execution. There are not
10,000 Batch jobs and there is no per-image GPU provisioning.

The control plane, preparer, and orchestrator are three separate responsibilities:

- `watermark-control-plane` is the periodically scheduled dispatcher and reconciler;
- `watermark-bulk-preparer` is a CPU-only Cloud Run Job execution that prepares GCS input; and
- `watermark-aws-orchestrator` is the short Cloud Run Job execution that crosses into AWS.

The control plane launches executions of pre-created Cloud Run Job definitions. It does not create
new Cloud Run Job definitions for each application job.

## 6. Model residency strategy

### 6.1 Model files

The container image continues to contain application code and libraries, but not the model weights.
The enabled checksum-pinned model artifacts currently total about 3.82 GiB.

At the start of one AWS Batch attempt:

1. Download each enabled model artifact from its exact GCS URI and generation.
2. Verify size and SHA-256.
3. Expand verified bundles into the job's ephemeral work directory.
4. Reuse those local files for the complete attempt.

There is no model download per image.

### 6.2 GPU memory

The bulk worker uses one all-resident model lifecycle:

1. Load RMBG, GroundingDINO, YOLO, SAM2, and LaMa once at attempt startup.
2. Retain those model objects for the complete manifest.
3. Read and decode one source image.
4. Run RMBG, detection, segmentation, the safety gate, and, when allowed, LaMa for that image.
5. Publish the item's artifacts, result, and terminal completion marker.
6. Close the current images and release item-specific CPU/CUDA tensors.
7. Continue with the next item without reloading any model.
8. Release all models only when the attempt exits.

The all-resident T4 feasibility check has been accepted by the owner. The AWS rollout must still
capture whole-device GPU usage with `nvidia-smi`, because framework allocator statistics can omit
memory held outside the current PyTorch allocator. The first AWS bulk gates must also confirm that
memory stabilizes across many items and that the tested image dimensions include the largest
supported production case.

This decision deliberately trades higher steady GPU memory for simpler execution, no GCS
round-trips between model stages, and one model-load cost per Batch attempt.

### 6.3 Cross-job residency

Do not add EFS, a permanent EC2 GPU, or a shared model volume initially. A new Batch VM may download
the model set again. For a several-hour execution, this startup cost is amortized across the job.

The existing ADR already defers an immutable S3 model replica until measured GCS-to-AWS download
time and egress cost justify it. That remains the correct decision.

## 7. GCS object layout

Keep the existing deterministic job prefix:

```text
gs://<watermark-data-bucket>/account_<account-id>/<job-id>/
```

Objects:

```text
input-manifest.json
asset_1.png
asset_2.webp
...

asset_1/stages/background/detector.png
asset_1/stages/background/foreground-mask.png
asset_1/stages/detection/combined-mask.png
asset_1/stages/detection/detections.json

asset_1/inpainted/final.png
asset_1/result.json
asset_1/_COMPLETE.json

output-manifest.json
_COMPLETE.json
```

Intermediate artifacts may remain for review and diagnostics, but they are not independent resume
boundaries. An item is reusable only after its create-only item `_COMPLETE.json` marker exists and
passes compatibility validation. An item marker is authoritative only when it pins:

- job ID and tenant ID;
- input manifest URI, generation, and checksum;
- source URI, generation, and checksum;
- pipeline, config, model-manifest, and worker-image identities;
- item-result schema version; and
- every published artifact's URI, generation, size, checksum, and content type.

An existing compatible terminal item marker is adopted on restart. An incomplete item is rerun
end-to-end. Existing deterministic artifacts may be adopted only when their immutable identities
and checksums match; incompatible output fails closed.

## 8. FastAPI repository changes

### B1. Preserve the small manual endpoint

Keep the current:

```text
POST /api/v1/watermark-jobs
```

capped at five registered assets until bulk has passed its rollout gates. Do not silently change
this proven endpoint into a catalog-scale operation.

### B2. Add a store-scoped bulk resource

Add:

```text
POST /api/v1/stores/{store_id}/watermark-scan-jobs/estimate
POST /api/v1/stores/{store_id}/watermark-scan-jobs
GET  /api/v1/stores/{store_id}/watermark-scan-jobs/{job_id}
GET  /api/v1/stores/{store_id}/watermark-scan-jobs/{job_id}/items
POST /api/v1/stores/{store_id}/watermark-scan-jobs/{job_id}/cancellations
```

Reuse the existing Shopify bulk filter and selection semantics where possible. Do not overload the
existing Shopify infringement `scan_mode=image`; that workflow performs different processing.

The create route must:

1. Authenticate the user and authorize access to the store/account.
2. Validate the requested limit against `WATERMARK_BULK_MAX_ITEMS_PER_JOB`.
3. Freeze the filter/selection request and idempotency hash.
4. Create a `PREPARING` application job.
5. Persist a durable preparation intent.
6. Commit and return `202` without downloading images or writing the GPU manifest.

### B3. Extend the database model

Create a new Alembic migration after `0047`.

Extend `WatermarkJob` with the minimum bulk fields:

- `source_kind`, initially `REGISTERED_ASSETS` or `SHOPIFY_STORE`;
- nullable `store_id`;
- frozen selection/filter JSON;
- `planned_items`;
- `prepared_items`;
- `preparation_failed_items`;
- optional preparation heartbeat/timestamp;
- optional current GPU stage and stage processed count; and
- the `PREPARING` job status.

Add a `WatermarkJobTarget` table for catalog preparation:

- job ID and deterministic sequence number;
- Shopify product and media database IDs;
- immutable product/media GID, URL, position, and title snapshots needed for result mapping;
- preparation status and stable error code;
- resulting `WatermarkAsset` and `WatermarkJobItem` IDs when staging succeeds; and
- unique `(job_id, sequence)` and `(job_id, shopify_media_id)` constraints.

The target table is needed because a corrupt or missing source has no valid staged asset or worker
item, but its failure must still be visible.

Add to `WatermarkJobItem`:

- final object checksum;
- final object size; and
- final object content type.

These fields let the authorized result endpoint verify the exact downloaded generation and
checksum.

Add a partial unique constraint allowing only one active bulk watermark job per store. Continue
using one status machine: do not create a second AWS-specific application job model.

Likely files:

- `app/models/watermark_job.py`
- `alembic/versions/0048_*.py`
- `app/schemas/watermark_jobs.py`
- `app/api/routes/watermark_jobs.py` or a focused new bulk route module

### B4. Add the CPU-only preparation Cloud Run command

Add a runner command:

```bash
python -m app.jobs.runner watermark-bulk-prepare --job-id <database-id>
```

The preparation worker must:

1. Atomically claim a `PREPARING` job so duplicate Cloud Run executions cannot both own it.
2. Materialize the exact product/media target set with keyset pagination.
3. Store target snapshots before long-running downloads.
4. Download source images with bounded concurrency and existing retry/timeout patterns.
5. Stream each response, enforce the size/type/image limits, and avoid retaining many images.
6. Write directly to the deterministic job input URI using a create-only generation precondition.
7. Record GCS generation, checksum, size, and content type.
8. Create the trusted asset and `WatermarkJobItem` rows.
9. Commit progress in bounded batches so an interrupted preparation can resume.
10. Record a stable per-target failure for missing, corrupt, unsupported, or oversized images.
11. Write one immutable input manifest containing only successfully prepared items.
12. Persist its generation/checksum and the canonical AWS request hash.
13. Create `AWS_GPU_SUBMISSION_REQUESTED` only after manifest publication succeeds.
14. Move the parent job from `PREPARING` to `QUEUED`.

If no image is successfully prepared, finish the application job without starting AWS.

The same immutable destination may exist after a lost response. Add a storage method that adopts an
existing destination only after validating its expected identity. Do not treat a create-only
collision as an automatic success.

Likely files:

- `app/jobs/runner.py`
- `app/jobs/dispatcher.py`
- `app/watermark/storage.py`
- new `app/watermark/bulk_preparer.py`
- existing Shopify selection helpers under `app/services/`
- `app/watermark/manifest.py`

Deploy one CPU-only Cloud Run Job, for example:

```text
watermark-bulk-preparer
```

It uses the existing backend image, Cloud SQL connection, GCS service identity, and bounded
background-job conventions. It does not install or import PyTorch and it never requests a GPU.

### B5. Extend the durable control plane

Add a preparation-intent dispatch path alongside the existing AWS submission intent.

The control plane should:

- launch `watermark-bulk-preparer` for a pending preparation intent;
- continue launching `watermark-aws-orchestrator` only for a completed immutable manifest;
- tolerate a lost Cloud Run dispatch response by relying on the preparation job's atomic claim;
- continue polling AWS once the provider job is reserved; and
- retain the current cancellation route and AWS cancellation intent.

Do not let the preparation Cloud Run Job call AWS directly. Preparation publishes immutable input;
the existing orchestrator remains the single GCP-to-AWS submission component.

Likely files:

- `app/watermark/control_plane.py`
- `app/jobs/dispatcher.py`
- `app/watermark/orchestrator.py` only if its pipeline-version validation changes
- `tests/test_watermark_control_plane.py`
- `tests/test_job_dispatcher.py`

### B6. Make the output manifest a scalable authoritative summary

The worker's output-manifest entry should include:

```json
{
  "itemId": "asset_1",
  "status": "SUCCEEDED",
  "decision": "INPAINTED",
  "source": {
    "uri": "gs://...",
    "generation": "123",
    "checksumSha256": "..."
  },
  "result": {
    "uri": "gs://...",
    "generation": "124",
    "checksumSha256": "...",
    "sizeBytes": 1234,
    "contentType": "application/json"
  },
  "marker": {
    "uri": "gs://...",
    "generation": "125",
    "checksumSha256": "...",
    "sizeBytes": 2345,
    "contentType": "application/json"
  },
  "final": {
    "uri": "gs://...",
    "generation": "126",
    "checksumSha256": "...",
    "sizeBytes": 456789,
    "contentType": "image/png"
  }
}
```

`final` is required only for `SUCCEEDED`.

FastAPI should read only:

1. the job completion marker; and
2. the exact output-manifest generation pinned by that marker.

It then validates every summary entry against the persisted item row and deterministic prefix.
It does not download `result.json` and the item marker for all 10,000 items during normal
reconciliation.

This is safe because:

- the trusted worker writes immutable objects under a deterministic account/job prefix;
- the job marker is written last and pins the output manifest by generation and checksum;
- each summary entry pins the exact source and output object identities; and
- the authorized download endpoint reads the exact final generation and verifies its checksum.

Use a dedicated bounded JSON limit such as `WATERMARK_MAX_MANIFEST_BYTES`, initially 32 MiB. Do not
remove read limits.

Update database rows in batches, for example 500 at a time. Keep the parent in `AWAITING_OUTPUT`
until every expected entry is reconciled. The operation is idempotent, so a control-plane restart
may safely repeat already applied batches.

Likely files:

- `app/watermark/storage.py`
- `app/watermark/output_validation.py`
- `app/watermark/reconciler.py`
- `app/models/watermark_job.py`
- `app/core/config.py`

### B7. Configuration

Add:

```text
WATERMARK_BULK_MAX_ITEMS_PER_JOB=10000
WATERMARK_MAX_MANIFEST_BYTES=33554432
CLOUD_RUN_WATERMARK_BULK_PREPARER_JOB_NAME=watermark-bulk-preparer
```

Keep:

```text
WATERMARK_MAX_ITEMS_PER_JOB=5
WATERMARK_MAX_CONCURRENT_JOBS_PER_TENANT=1
```

Start production rollout with the bulk maximum set below 10,000, then raise it without changing the
worker image after the worker-side hard ceiling has been proven.

### B8. Backend tests

Add tests proving:

- the manual endpoint still rejects more than five items;
- the bulk endpoint accepts a frozen selection up to its configured limit;
- 10,001 items are rejected;
- the create request performs no source downloads or GCS copies;
- a duplicate idempotency key returns the same job;
- preparation handles targets in bounded batches;
- preparation resumes after a simulated interruption;
- duplicate preparer executions cannot both claim the job;
- individual corrupt/missing images fail independently;
- the manifest contains only successfully staged immutable items;
- AWS submission intent is impossible before the manifest is pinned;
- a 10,000-entry output manifest can be read within the configured bound;
- reconciliation uses two job-level JSON reads rather than two reads per item;
- batched reconciliation is idempotent;
- final downloads enforce account authorization, generation, and checksum; and
- cancellation before and after AWS submission is safe.

## 9. `watermarkPoc` changes

### W1. Add a new bulk pipeline identity

Do not mutate the accepted five-image `watermark-v1` behavior in place.

Add a packaged configuration such as:

```text
configs/watermark-bulk-v1.yaml
```

It initially uses the same model IDs, thresholds, safety gate, image limits, and storage buckets as
`watermark-v1`, but has:

```yaml
pipeline_version: watermark-bulk-v1
limits:
  maximum_items: 10000
```

The FastAPI provider model, AWS Lambda request model, Terraform pipeline allowlist, and Batch job
definition must explicitly accept this exact pipeline identity.

Prefer a separate bulk job-definition revision/config binding so the five-image definition remains
available for rollback. Do not construct arbitrary configuration paths from untrusted input.

Likely files:

- `configs/watermark-bulk-v1.yaml`
- `services/aws_submission/src/aws_submission/models.py`
- `services/aws_submission/src/aws_submission/canonical.py`
- `infra/aws/variables.tf`
- `infra/aws/submission-api.tf`
- `infra/aws/batch-job.tf`
- matching contracts and tests

### W2. Replace all-items-in-memory execution

Refactor the worker into a bounded one-item executor.

#### Attempt initialization

- Prepare and checksum-verify every configured model artifact once.
- Initialize RMBG, GroundingDINO, YOLO, SAM2, and LaMa once.
- Retain all runtimes until final publication or process shutdown.

#### Per-item execution

- Iterate manifest descriptors without downloading every source in advance.
- Check for and adopt a compatible terminal item completion marker.
- Otherwise read and validate the exact source generation and checksum.
- Decode only the current image.
- Run RMBG and pass its detector image and foreground mask directly to the next in-process stage.
- Run GroundingDINO and YOLO, segment detections with SAM2, and evaluate the safety gate.
- If allowed, run LaMa against the original source and combined mask.
- Finalize blocked, unverified, no-watermark, succeeded, and item-failed outcomes independently.
- Write review artifacts, result JSON, and the item completion marker.
- Close every current-item image and release masks, tensors, detections, and source bytes.
- Continue with the next item without releasing or reloading model runtimes.
- Continue after item-level validation or inference failures when they can be represented by a
  terminal item result.

#### Final publication

- Build the compact self-contained output summary from compatible terminal markers.
- Write `output-manifest.json` create-only.
- Write the job `_COMPLETE.json` marker last.
- Release all model runtimes.
- Exit zero.

The implementation must not keep a `prepared` dictionary, `pending` list, background-output
dictionary, or detection-output dictionary proportional to all decoded images. A bounded list of
compact output-summary entries is acceptable because the output manifest itself contains one entry
per requested item.

Likely files:

- `src/watermark_worker/cli.py`
- `src/watermark_worker/pipeline.py`
- `src/watermark_worker/production_executor.py`
- `src/watermark_worker/production_stages.py`
- `src/watermark_worker/result_writer.py`
- `src/watermark_worker/contracts.py`

### W3. Reuse item-level completion as the retry boundary

Do not introduce stage-level completion contracts. Reuse the existing typed item result,
create-or-validate behavior, and immutable per-item completion marker.

An item may be adopted only when its terminal marker matches:

- current manifest identity;
- current source identity;
- current worker image, pipeline, config, and model-manifest hashes;
- expected item-result schema version; and
- exact published artifact identities.

Missing marker means the item is incomplete and must run end-to-end. A marker with incompatible
identity is a permanent stale-output failure, not permission to overwrite. Deterministic partial
artifacts left by an interrupted item may be adopted only when their immutable identity and content
match exactly.

### W4. Remove per-item reads from job-final publication

`ResultWriter.write_job()` currently validates entries by reading each item marker and result more
than once.

For bulk:

- validate each new or reused terminal item when it is produced/adopted;
- carry its validated summary entry forward;
- perform structural and aggregate validation before job publication;
- avoid re-reading every item object again at final publication; and
- keep strict validation when adopting an already existing job completion marker.

The final publication cost should be proportional to the output-manifest bytes, not several remote
reads for each item.

### W5. Extend the Batch execution envelope

After bounded per-item execution and terminal-item adoption are implemented:

- set the bulk Batch attempt timeout to an initial 24 hours (`86400` seconds);
- use two total attempts;
- retry only EC2/agent infrastructure failures and explicitly retryable worker exits;
- exit immediately for invalid manifest, configuration, model, authorization, and permanent
  storage failures; and
- retain `min_vcpus = 0` so the GPU scales to zero.

AWS documents that Batch timeout is per attempt, has a 60-second minimum, and has no maximum for
EC2 Batch jobs. A timeout termination itself is not automatically retried:
<https://docs.aws.amazon.com/batch/latest/userguide/job_timeouts.html>.

AWS supports conditional retry behavior with `evaluateOnExit`:
<https://docs.aws.amazon.com/batch/latest/userguide/job_retries.html>.

Do not enable the second attempt until terminal-item adoption has proven that a repeated attempt
skips completed items and repeats at most the currently incomplete item.

### W6. Preserve current model storage

Keep the current model manifest and private GCS model objects for the first bulk rollout.

Measure:

- model bytes and download duration;
- bundle preparation duration;
- each model initialization duration and the combined startup duration;
- time per item and per in-process model stage;
- whole-device peak VRAM, allocator peaks, host RAM, and steady-state memory; and
- GCP cross-cloud egress.

Only introduce an immutable S3 model replica if these measurements show material startup cost.

### W7. Worker and contract tests

Dependency-light tests must prove with a synthetic 10,000-item manifest:

- only one current item is decoded at a time;
- peak retained in-memory items remains bounded;
- every enabled runtime initializes once per attempt;
- every runtime remains usable across multiple items and closes once at attempt shutdown;
- every source is pinned by generation and checksum;
- item failures do not stop unrelated items;
- blocked items never invoke LaMa;
- completed items are adopted after interruption while the incomplete item runs end-to-end again;
- an incompatible terminal item marker fails closed;
- output counts and item IDs exactly match the terminal set;
- fresh final publication does not perform per-item remote validation reads; and
- 10,001 items are rejected.

Container/GPU tests must prove:

- all enabled models fit simultaneously on the selected T4;
- model runtimes initialize once and remain resident for the item loop;
- item-specific CUDA memory does not grow across a representative multi-item run;
- process RAM does not grow with item count;
- local scratch remains bounded;
- cancellation and `SIGTERM` leave only valid completed item markers; and
- a retry adopts completed work.

**Local implementation status:** The dependency-light portion is implemented. The real
container/GPU checks in the second list remain rollout gates because they require the newly built
worker image and a billable AWS Batch execution.

## 10. Ordered implementation phases

### Phase 1: Freeze cross-repository contracts

1. Approve `watermark-bulk-v1`.
2. Confirm the existing item completion marker contains every identity required for retry adoption.
3. Define the self-contained output-manifest entry.
4. Add or update contract fixtures in both repositories.

**Checkpoint:** Both repositories agree on exact JSON and canonical hashes before runtime code
changes.

**Status:** Implemented locally.

### Phase 2: Make the worker bounded and resumable

1. Add a single all-model runtime owner for attempt startup and shutdown.
2. Implement the end-to-end one-item loop.
3. Reuse compatible item completion markers and rerun incomplete items.
4. Remove manifest-sized image/intermediate collections.
5. Optimize final publication.
6. Run 10,000-item fake-runtime tests and a multi-item GPU memory-stability test.

**Checkpoint:** No AWS deployment yet. Unit/integration tests prove bounded memory and resume at
the terminal-item boundary.

**Status:** Implemented locally. The multi-item real-GPU memory-stability check remains in the
measured rollout.

### Phase 3: Add asynchronous FastAPI preparation

1. Add migration and bulk schemas/routes.
2. Add target materialization.
3. Add resumable GCS preparation.
4. Add the Cloud Run runner/dispatcher command.
5. Create AWS submission intent only after immutable manifest publication.

**Checkpoint:** A large job can be prepared entirely in GCP without enabling AWS.

**Status:** Implemented locally in the FastAPI repository.

### Phase 4: Add scalable output reconciliation

1. Add the bounded large-manifest reader.
2. Validate the self-contained summary.
3. Apply item results in database batches.
4. Verify authorized final downloads by generation and checksum.

**Checkpoint:** A synthetic 10,000-entry completed output reconciles without 20,000 GCS reads.

**Status:** Implemented locally across the FastAPI reconciler and worker final publisher.

### Phase 5: Build and deploy

1. Build and push a new GPU image through CodeBuild/ECR.
2. Deploy the new AWS Lambda contract and bulk Batch job definition.
3. Deploy the backend image.
4. Create/update `watermark-bulk-preparer`.
5. Apply the FastAPI database migration.
6. Keep bulk maximum low and submission disabled until tests are ready.

**Status:** AWS worker portion complete. The new image, Lambda, IAM mapping, and both Batch
definitions are deployed. Submission and the queue remain disabled. No GPU compute was launched.
Any remaining FastAPI/GCP deployment work is tracked in that repository; Phase 6 remains pending.

### Phase 6: Measured rollout

Run in order:

1. 5 representative images through `watermark-bulk-v1`.
2. 100 representative images.
3. An interruption/resume test.
4. 500 representative images.
5. A cancellation test.
6. 1,000 representative images.
7. One controlled 10,000-image job.

At every gate record:

- prepared, failed-preparation, processed, blocked, skipped, failed, and succeeded counts;
- stage and total duration;
- peak host RAM and GPU memory;
- model download/load duration;
- GCS operation count and transfer bytes;
- AWS Batch queue/start/stop timestamps;
- EC2 runtime and estimated cost; and
- confirmation of zero running GPU instances afterward.

Do not promise a 10-hour completion target until the 100- and 500-image measurements support the
required sustained throughput. The existing five-image evidence is not a valid 10,000-image
capacity benchmark.

## 11. Completion criteria

The system supports 10,000 images only when all are true:

- FastAPI returns `202` without performing catalog downloads/GCS staging in the request.
- Preparation is resumable and independently records bad inputs.
- The immutable manifest contains no more than 10,000 successfully staged items.
- One AWS Batch GPU job processes the manifest.
- Model artifacts download once per attempt.
- Every enabled GPU model loads once per attempt and remains resident until shutdown.
- Host RAM and scratch use remain bounded as item count grows.
- A worker restart reuses compatible terminal item results and reruns incomplete items.
- Every prepared item reaches exactly one terminal output status.
- FastAPI reconciles a 10,000-entry output without per-item result/marker downloads.
- Result access remains tenant-authorized and pinned by generation/checksum.
- Cancellation is safe.
- Batch timeout and retry behavior are bounded.
- The final GPU resource check shows no running GPU instances.
- The measured duration and cost are acceptable to the owner.

## 12. Explicit non-goals for the first version

Do not add:

- one Batch job per image;
- AWS Batch array jobs or parent/child sharding;
- Step Functions;
- SQS-per-image processing;
- ECS or a permanently running GPU;
- direct AWS-to-PostgreSQL connectivity;
- direct AWS downloads from mutable Shopify URLs;
- DynamoDB progress tracking;
- EFS/shared model cache;
- Spot instances;
- multiple GPUs;
- callback webhooks from AWS to FastAPI; or
- an automatic unlimited retry loop.

These may be reconsidered only after the single-job 100/500/1,000/10,000 measurements identify a
specific bottleneck.

## 13. Cost expectation

At the stated workload of approximately 15 scans per month and about 10 GPU hours per scan, the
GPU alone is approximately 150 instance-hours per month.

Using the previously observed console rate of roughly USD 0.65 to 0.74 per hour, EC2 compute alone
would be approximately USD 98 to 111 monthly, before storage, public IPv4, logs, CodeBuild, and
GCP-to-AWS transfer. This is an estimate, not a bill.

The existing USD 60 AWS Budget is an alert, not a spending cap. If the workload reaches the stated
frequency, the alert will probably trigger. Update the alert threshold only after measured
100/500-image runtime provides a better monthly estimate.
