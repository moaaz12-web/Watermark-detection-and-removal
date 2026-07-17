# GPU Watermark Worker: Production Setup Plan

Status: Implementation in progress — production acceptance remains gated by W0 and W1

Last updated: 2026-07-17

Implementation order: First

## 1. Objective

Convert the validated watermark-removal notebook pipeline into a versioned, testable, resumable Python worker that runs as a container on a Google Cloud Batch GPU VM.

The first production milestone is:

> Given a five-image manifest, one Google Cloud Batch task creates an on-demand `g2-standard-4` VM, runs the worker with its L4 GPU, writes deterministic per-image results to Cloud Storage, resumes safely after interruption, and exits successfully without manual intervention.

This document covers only the worker and its Batch runtime. FastAPI integration is specified separately in [02-fastapi-integration.md](02-fastapi-integration.md).

## 2. Why this work comes first

The worker is the highest-risk part of the system because the current implementation is notebook-oriented and contains environment setup, experimental alternatives, model loading, inference, artifact generation, and inpainting in the same execution flow.

The backend integration depends on stable answers to these questions:

- What exact pipeline is production version 1?
- What input does the worker accept?
- What files and metadata does it produce?
- How does it report partial failures and progress?
- How does it resume without repeating completed work?
- What CPU, RAM, VRAM, disk, and runtime does it actually need?

Those questions must be answered before database and API contracts are finalized.

## 3. Architectural decisions

### 3.1 Separate deployable worker

The GPU worker is a separate Python package and container from the FastAPI application.

Reasons:

- CUDA, PyTorch, SAM 2, YOLO, and LaMa dependencies must not enlarge or destabilize the API image.
- The API and worker have different scaling, security, startup, and release requirements.
- A worker image digest identifies exactly which code processed a job.
- The worker can be tested and submitted to Batch without involving the API.

This repository will become the worker implementation repository unless the team later chooses to move the package into a monorepo.

### 3.2 One frozen pipeline for version 1

Production version 1 must select one exact pipeline rather than carrying every experimental notebook branch into the worker.

Candidate production path:

```text
optional RMBG preprocessing
  -> configured detector path
  -> SAM 2 segmentation
  -> product-overlap safety gate
  -> mask preparation
  -> LaMa inpainting
```

The current candidate source is `watermark_batch_rmbg_grounding_dino_yolo_sam2_lama_safety_gate.ipynb`, but the owner must confirm the exact enabled detector path, thresholds, and safety-gate configuration before implementation begins.

The exploratory `rnd_complete.ipynb` is reference material, not the production entrypoint.

### 3.3 Manifest-driven batch execution

The worker receives an immutable manifest stored in Cloud Storage. It does not query the application database and does not accept arbitrary database credentials.

### 3.4 One Batch task and one GPU initially

Version 1 runs one worker task on one `g2-standard-4` VM with one L4 GPU. It initializes each selected model stage once and processes all items sequentially.

Creating one task per image is explicitly out of scope because repeated model initialization would waste time and GPU capacity.

### 3.5 Cloud Storage for artifacts

Original images, masks, annotations, final images, manifests, and completion markers live in Cloud Storage. PostgreSQL stores references and application state only.

### 3.6 At-least-once execution

The worker must assume that a Batch task can be retried. Processing is idempotent at item level, and a retry skips items that already have a valid terminal marker.

### 3.7 Accepted version-1 safety and output decisions

The following decisions are intentional constraints, not implementation suggestions. Their rationale is recorded in [ADR-001](../decisions/ADR-001-worker-safety-and-idempotency.md).

- Version 1 is a watermark-removal service, not a background-normalization service. The source image is the only permitted input to final inpainting. RMBG's white-background image is a detector and safety-gate artifact only; it is never a final customer output.
- A proposed removal mask may be inpainted only after a valid product-overlap safety-gate evaluation. If RMBG fails, produces no foreground mask, or produces a foreground mask smaller than the approved minimum, the item is `BLOCKED_BY_SAFETY_GATE` with decision `UNVERIFIED`; LaMa must not run.
- `BLOCKED_BY_SAFETY_GATE` and `SKIPPED_NO_WATERMARK` never produce `finalUri`. The application continues to reference the already-authorized source asset; the worker does not copy a white-background preprocessing artifact and call it a final image.
- One worker run may contain terminal item failures and still exit `0` after it writes a complete job manifest. Exit `0` means the batch completed its reporting contract, not that every image was inpainted. Item outcomes remain visible in the manifest and application counters.
- A Cloud Storage manifest and every input object are pinned to an immutable generation and SHA-256 checksum. Completed markers and the job completion marker are created with Cloud Storage generation preconditions.

## 4. Scope

### Included

- Freeze and record pipeline version 1.
- Extract notebook logic into importable Python modules.
- Define manifest and result contracts.
- Initialize each selected model stage once per process.
- Process a batch from local files or Cloud Storage.
- Preserve per-image review artifacts.
- Resume safely using terminal markers.
- Package the worker in a GPU-compatible container.
- Run the container manually through Google Cloud Batch.
- Add unit, integration, GPU smoke, and manual regression checks.
- Emit structured logs and progress events.

### Not included

- Public HTTP server in the GPU container.
- User authentication or authorization.
- Direct PostgreSQL access from the GPU worker.
- FastAPI endpoints or database migrations.
- Multiple concurrent GPUs or distributed inference.
- Spot VM support before resumability is verified.
- Training or fine-tuning models.
- Supporting every experimental detector configuration in version 1.

## 5. Production pipeline specification

Before code extraction, create a versioned configuration file such as `configs/watermark-v1.yaml` containing every behavior-changing value. A configuration containing `null`, `TBD`, a mutable URL, or a branch name is invalid and must be rejected at startup.

```yaml
schema_version: 1
pipeline_version: watermark-v1

background_removal:
  enabled: true                         # required when safety_gate.enabled is true
  detector_input: white_background
  final_image_input: source_original
  model_artifact_id: rmbg-1.4-<sha256>

detection:
  mode: combined
  yolo:
    enabled: true
    model_artifact_id: watermark-yolo-<sha256>
    confidence: 0.35
    iou: 0.5
    image_size: 1024
    augment: true
  grounding_dino:
    enabled: true
    model_artifact_id: grounding-dino-base-<sha256>
    prompt: "watermark, logo, brand logo, text, label, sticker, stamp, copyright notice, graphic overlay, promotiona text, ecommerce text, commercial messaging, shipping text, discount offer"
    box_threshold: 0.35
    text_threshold: 0.55

segmentation:
  model_artifact_id: sam2.1-hiera-large-<sha256>

safety_gate:
  enabled: true
  failure_policy: block_unverified
  per_detection_block: 0.40
  combined_warn: 0.25
  combined_block: 0.55
  min_product_area_pixels: 500

inpainting:
  model_artifact_id: big-lama-<sha256>
  input: source_original
  mask_dilation_pixels: 2
  mask_threshold: 127
```

The values above are the current notebook candidate snapshot, not approval to ship. W1 replaces every `<sha256>` identifier with an approved model artifact and records the exact source revision, code patch hash, license review, and file checksum in `model-manifest.json`. Changing any listed value creates a new pipeline version.

The worker must run models by lifecycle stage: RMBG, detector/SAM, then LaMa. It releases a completed stage before loading the next one and must not keep every selected model resident simultaneously. This preserves the notebook's memory behavior while retaining one load per selected stage per process.

## 6. Private evaluation set and quality gate

Keep a private, versioned evaluation set of authorized or sanitized images. It begins with the following approximately 10-image smoke subset and grows whenever a useful failure case is found:

```text
golden-images/
  01-small-watermark.jpg
  02-large-watermark.jpg
  03-transparent-watermark.jpg
  04-edge-watermark.jpg
  05-clean-product.jpg
  06-clean-product-with-logo.jpg
  07-product-with-printed-text.jpg
  08-false-positive-risk.jpg
  09-high-resolution.jpg
  10-unusual-aspect-ratio.jpg
```

The exact filenames and mix may differ. Keep image bytes and filenames unchanged so repeated runs use the same inputs. Store a private manifest alongside the images containing each source checksum, expected decision (`INPAINTED`, `SKIPPED_NO_WATERMARK`, or `BLOCKED_BY_SAFETY_GATE`), risk class, and human approval record.

The evaluation set must include both editable watermark cases and high-risk negative cases: clean products, product logos, printed text, small/partial products, transparent overlays, large overlays, high-resolution images, and unusual aspect ratios. The high-risk negative cases have a strict rule: no unapproved product alteration and no inpainting when the safety gate is unavailable.

Verification combines automated and manual checks:

1. Record the source notebook outputs as an exploratory reference before extraction.
2. Run the extracted worker with the pinned image, config, and model manifests.
3. Assert expected decisions and source checksums automatically.
4. Compare masks and final images side by side for every changed decision or high-risk case.
5. Require named human approval for any intentional quality change; otherwise it is a regression.

The folder is an offline development and staging aid only. It is not bundled into the worker image, referenced by production code, or included in customer production jobs. Production jobs contain arbitrary product images supplied through their own manifests.

## 7. Input contract

### 7.1 Manifest location

The worker accepts exactly one manifest location:

```bash
python -m watermark_worker \
  --manifest-uri gs://watermark-jobs/tenant-17/wm_123/input-manifest.json \
  --manifest-generation 1721210400123456
```

For local development, it also accepts a local manifest path:

```bash
python -m watermark_worker \
  --manifest-path tests/fixtures/small-manifest.json \
  --local-output-root /tmp/watermark-output
```

`--manifest-uri` and `--manifest-path` are mutually exclusive. `--manifest-generation` is required for Cloud Storage execution and forbidden for local development. FastAPI writes the manifest, records its returned GCS generation, and passes that generation to Batch; the worker reads exactly that generation with no live-object fallback.

### 7.2 Manifest schema

```json
{
  "schemaVersion": 1,
  "jobId": "wm_123",
  "tenantId": "tenant_17",
  "pipelineVersion": "watermark-v1",
  "createdAt": "2026-07-17T10:00:00Z",
  "items": [
    {
      "itemId": "asset_123",
      "inputUri": "gs://watermark-input/tenant-17/wm_123/asset_123.png",
      "inputGeneration": "1721210300123456",
      "outputPrefix": "gs://watermark-output/tenant-17/wm_123/asset_123/",
      "sourceChecksumSha256": "required-lowercase-64-character-hex-value"
    }
  ]
}
```

For local development, version 1 uses the explicit `inputPath` variant rather than `file://` in
the manifest. `inputPath` is a safe relative path resolved from the local manifest's directory;
`inputGeneration` is forbidden; and `outputPrefix` is the relative, deterministic key
`<tenantId>/<jobId>/<itemId>/`. The worker may use `file://` URIs internally after resolution,
but those are not part of the local manifest schema.

```json
{
  "schemaVersion": 1,
  "jobId": "wm_123",
  "tenantId": "tenant_17",
  "pipelineVersion": "watermark-v1",
  "createdAt": "2026-07-17T10:00:00Z",
  "items": [
    {
      "itemId": "asset_123",
      "inputPath": "inputs/asset_123.png",
      "outputPrefix": "tenant_17/wm_123/asset_123/",
      "sourceChecksumSha256": "required-lowercase-64-character-hex-value"
    }
  ]
}
```

### 7.3 Validation rules

- `schemaVersion` must be supported.
- `jobId`, `tenantId`, and `itemId` must match documented length and character restrictions.
- `pipelineVersion` must exactly match a configuration packaged with the worker image.
- The manifest must contain at least one item and remain below a configured maximum item count.
- The worker reads the manifest at the required GCS generation and rejects a missing, changed, or mismatched generation.
- Cloud Storage items require an `inputGeneration` and `sourceChecksumSha256`. The worker reads that exact input generation and verifies the decoded bytes against the checksum before inference. Local test manifests use a local path plus the same required checksum, but no GCS generation.
- Input and output URIs must use approved buckets and the exact deterministic prefixes derived from `tenantId`, `jobId`, and `itemId`; the worker rejects arbitrary paths even if they use an approved bucket.
- Duplicate `itemId` or output prefixes are rejected.
- The worker validates decoded image type, dimensions, and pixel count; it does not trust the filename or MIME metadata alone.
- A changed manifest or source object requires a new application job and manifest. Cloud Storage object names alone are insufficient identity because the live object at a name can be replaced.

## 8. Output contract

### 8.1 Per-item layout

```text
<outputPrefix>/
  source_metadata.json
  background_removed/
    foreground_mask.png
    white_background.png
  detections/
    detector_results.json
    annotated.png
  masks/
    combined_mask.png
    mask_000.png
  inpainted/
    final.png                 # present only for decision INPAINTED
  result.json
  _COMPLETE.json
```

Only enabled stages need to produce their optional artifacts. `result.json` and `_COMPLETE.json` have stable meanings. `white_background.png` is diagnostic/preprocessing output only and cannot be exposed as `finalUri`.

### 8.2 Result schema

```json
{
  "schemaVersion": 1,
  "jobId": "wm_123",
  "itemId": "asset_123",
  "status": "SUCCEEDED",
  "pipelineVersion": "watermark-v1",
  "workerImageDigest": "sha256:...",
  "configSha256": "...",
  "modelManifestSha256": "...",
  "sourceUri": "gs://watermark-input/tenant-17/wm_123/asset_123.png",
  "sourceGeneration": "1721210300123456",
  "sourceChecksumSha256": "...",
  "finalUri": "gs://watermark-output/tenant-17/wm_123/asset_123/inpainted/final.png",
  "combinedMaskUri": "gs://watermark-output/tenant-17/wm_123/asset_123/masks/combined_mask.png",
  "detectionCount": 2,
  "decision": "INPAINTED",
  "warningCodes": [],
  "processingSeconds": 4.72,
  "completedAt": "2026-07-17T10:05:00Z"
}
```

Allowed item statuses:

- `SUCCEEDED`
- `FAILED`
- `BLOCKED_BY_SAFETY_GATE`
- `SKIPPED_NO_WATERMARK`

`finalUri` is required only for `SUCCEEDED` with decision `INPAINTED`; it is absent for `FAILED`, `BLOCKED_BY_SAFETY_GATE`, and `SKIPPED_NO_WATERMARK`. `BLOCKED_BY_SAFETY_GATE` uses decision `UNVERIFIED` or `BLOCKED`; `SKIPPED_NO_WATERMARK` uses decision `NO_WATERMARK`. Allowed decisions and error codes must be enumerated in code rather than created as arbitrary strings.

### 8.3 Terminal item marker

`_COMPLETE.json` is written last, after all required item artifacts and `result.json` have been uploaded and validated. It is written for every terminal item outcome, including a deliberately failed corrupt image, so a retry can distinguish a recorded terminal result from an interrupted item.

The marker contains the manifest generation, source generation and checksum, pipeline version, config hash, model-manifest hash, worker image digest, result checksum, and object generations/checksums for required artifacts. An existing marker is reusable only when all values match. Otherwise the worker fails with a stale-output error; it never overwrites an incompatible completed result silently.

Artifact objects and markers use Cloud Storage generation preconditions. Create requests use `ifGenerationMatch=0`; a collision is reusable only after checksum and metadata validation. This prevents two retries from silently replacing one another's artifacts. Cloud Storage documents preconditions as the mechanism for safe conditional operations: <https://docs.cloud.google.com/storage/docs/request-preconditions>.

### 8.4 Job output manifest

After iterating over all items, the worker writes an `output-manifest.json`, validates that it references a terminal marker for every requested item, and then creates the job-level completion marker:

```text
gs://watermark-output/tenant-17/wm_123/output-manifest.json
gs://watermark-output/tenant-17/wm_123/_COMPLETE.json
```

The output manifest contains counts, per-item result references, timings, manifest generation, configuration/model hashes, worker version, and the overall outcome. The job marker contains the output-manifest generation and checksum. Consumers must treat a job as complete only when the compatible job marker exists; an output manifest without that marker may belong to an interrupted attempt.

## 9. Worker execution behavior

The worker performs these steps in order:

1. Validate environment and configuration.
2. Download and validate the input manifest.
3. Initialize structured logging with `jobId` and pipeline version.
4. Read the manifest and every source image at their pinned GCS generations; verify every required source checksum.
5. Validate the pinned configuration, model-manifest hash, model files, and checksums.
6. Load models once per lifecycle stage, releasing RMBG before detector/SAM and detector/SAM before LaMa.
7. Record GPU, CUDA, system RAM, local disk, and worker image metadata.
8. For each item:
   1. Check for a compatible `_COMPLETE.json`.
   2. Download and validate the source image.
   3. Use RMBG only for detector preprocessing and the product-overlap gate; use the original source image as the LaMa input.
   4. If the gate is unavailable, unreliable, or blocks the removal, write a terminal blocked result and do not run LaMa.
   5. Write artifacts to a temporary local item directory.
   6. Upload artifacts to Cloud Storage with generation preconditions.
   7. Upload `result.json`.
   8. Upload `_COMPLETE.json` last.
   9. Emit a progress event.
   10. Release item tensors and temporary files.
9. Write and validate the job output manifest, then create the job `_COMPLETE.json` marker with a generation precondition.
10. Emit final metrics and exit.

One invalid image should normally produce a failed item result and allow the batch to continue. A missing model, unsupported manifest, unavailable GPU, corrupt configuration, or inaccessible output bucket is a fatal job error.

## 10. Exit behavior

Proposed process exit codes:

| Code | Meaning |
| ---: | --- |
| `0` | Worker completed and wrote a compatible job output manifest and job `_COMPLETE.json`. Individual terminal item failures or blocks may be present in that manifest. |
| `2` | Invalid command or unsupported manifest/configuration. |
| `3` | Required model or model checksum failure. |
| `4` | GPU/CUDA initialization failure. |
| `5` | Permanent storage or permissions failure. |
| `6` | Retryable storage/network failure; no compatible job completion marker was written. |
| `7` | Retryable external platform dependency failure; no compatible job completion marker was written. |
| `10` | Unexpected fatal worker failure. |

Batch retries only `6` and `7`, once initially. It must fail tasks for all other worker exit codes. The exact mapping and Batch lifecycle policy must be tested and documented in the worker package.

## 11. Proposed project structure

```text
.
  pyproject.toml
  requirements.lock
  Dockerfile
  .dockerignore
  configs/
    watermark-v1.yaml
  model-manifest.json
  src/
    watermark_worker/
      __init__.py
      __main__.py
      cli.py
      config.py
      contracts.py
      manifest.py
      model_registry.py
      pipeline.py
      progress.py
      storage.py
      result_writer.py
      stages/
        background_removal.py
        detection.py
        segmentation.py
        safety_gate.py
        inpainting.py
  tests/
    unit/
    integration/
    fixtures/
  notebooks/
    ... retained R&D notebooks if they are moved later
```

Avoid abstractions that are not required by pipeline version 1. Each stage boundary should exist because it enables isolated testing or lifecycle management, not merely to mirror notebook sections.

## 12. Dependency and model management

### Python dependencies

- Pin Python and all direct dependencies.
- Produce a lock file with hashes where the chosen package tooling supports it.
- Do not run `pip install` during worker startup.
- Do not install another PyTorch build over an unverified base image at runtime.
- Run a dependency vulnerability and license review before release.

### Source dependencies

- Do not clone repositories during job execution.
- Pin upstream source dependencies to released packages or specific commit SHAs during image build.
- Preserve required LaMa compatibility changes as reviewed patches or maintained source, with tests that fail if an upstream layout changes.
- Do not use Hugging Face `trust_remote_code=True` or download executable model code at worker startup. Vendor or package reviewed custom model code in the image, pinned to a reviewed source revision.

### Model files

`model-manifest.json` records, for every model:

- Logical name and version.
- Source and license reference.
- Expected filename.
- SHA-256 checksum.
- Size.
- Required pipeline stages.
- Immutable private object URI and GCS generation when the model is fetched at startup.
- Source-code revision/patch hash when the model needs custom loading code.
- License and commercial-use review status.

Model files are initially downloaded from a private, versioned Cloud Storage prefix during container startup, at pinned generations and with checksum validation. Baking them into the container can be evaluated after measuring startup and pull times. Mutable public URLs, mutable repository branches, and runtime repository clones are not a production model registry.

## 13. Container requirements

The worker image must:

- Use a supported Linux AMD64 CUDA/PyTorch base compatible with the L4 driver.
- Run as a non-root user.
- Include application code and pinned runtime dependencies.
- Use a deterministic entrypoint.
- Write temporary files only under a configured working directory.
- Handle `SIGTERM` by stopping after the current safe checkpoint when time permits.
- Emit logs to stdout/stderr.
- Avoid including GCP service-account keys or application database secrets.
- Contain no executable code fetched dynamically from model providers.

Example entrypoint:

```dockerfile
ENTRYPOINT ["python", "-m", "watermark_worker"]
```

The image must be pushed to Artifact Registry with an immutable digest. Batch submissions use the digest, not `latest`.

## 14. Google Cloud Batch runtime

Initial runtime configuration:

```text
Machine type:         g2-standard-4 (4 vCPU, 16 GB RAM, 1 x 24 GB NVIDIA L4)
Provisioning model:   STANDARD (on-demand)
Reservation policy:   NO_RESERVATION
Tasks / parallelism:  1 / 1
Task timeout:         20 hours
Retries:              maxRetryCount: 1, only worker exit codes 6 and 7
Boot disk:            200 GB for the first benchmark; resize only from measured evidence
Locations:            Explicit approved L4-capable zone(s), selected after quota/capacity validation
Identity:             Explicit dedicated GPU worker service account; never the Compute default account
Networking:           Approved VPC; Private Google Access/Cloud NAT or equivalent for required services
Logs:                 Cloud Logging
```

The initial staging job uses `installGpuDrivers: true` with a supported OS, explicit network egress for the driver installation, and the immutable worker-image digest. Google Batch requires this field for automatic driver installation; the field defaults to `false`. If the production network cannot allow that dependency, the production job must instead use a tested custom VM image with GPU drivers and the NVIDIA Container Toolkit, and set `installGpuDrivers: false`. See [Google's GPU Batch guidance](https://docs.cloud.google.com/batch/docs/create-run-job-gpus).

Manual Batch submission must be proven before FastAPI integration. The first benchmark is a capacity gate, not proof that this is the final VM shape: `g2-standard-4` supplies 16 GB system RAM and 24 GB GPU memory. Record peak system RAM, VRAM, disk, startup time, and five-image runtime before retaining or increasing the shape. See [G2 machine specifications](https://docs.cloud.google.com/compute/docs/gpus).

The Batch template must explicitly set the service account, `installGpuDrivers`, `machineType`, `provisioningModel`, `reservation`, allowed locations, task timeout, retry lifecycle policy, network, and digest image URI. Do not rely on Batch defaults.

Official references:

- [Create and run a Google Cloud Batch job that uses GPUs](https://docs.cloud.google.com/batch/docs/create-run-job-gpus)
- [Google Cloud Batch job lifecycle](https://docs.cloud.google.com/batch/docs/create-run-job)
- [Automate task retries](https://docs.cloud.google.com/batch/docs/automate-task-retries)
- [Batch networking overview](https://docs.cloud.google.com/batch/docs/networking-overview)
- [Google Cloud Batch pricing](https://cloud.google.com/batch/pricing)

## 15. Security boundaries

The Batch worker service account is required, named explicitly in the Batch allocation policy, and has only the permissions required to:

- Pull the worker image from its Artifact Registry repository.
- Read approved model and input objects.
- Write job artifacts under approved output locations.
- Write logs.
- Send authenticated progress callbacks or events, if enabled.

It must not have:

- General database credentials.
- User authentication secrets.
- Broad project owner/editor permissions.
- Permission to create arbitrary Batch jobs.

IAM bindings must be restricted to the Artifact Registry repository and the approved GCS object prefixes, using IAM Conditions where available. The worker does not receive a service-account key. Default VPC egress is not permitted: configure the VPC and firewall explicitly, allow only the required Google APIs and staging driver-install route, and remove general internet egress from the production worker after the driver strategy is proven.

Input controls must include:

- Allowed image formats verified by decoded content.
- Maximum compressed size, dimensions, and pixel count.
- Protection against decompression-bomb images.
- Sanitized local filenames generated from internal IDs rather than user filenames.
- No user-controlled shell commands or subprocess arguments.

## 16. Observability

Every structured log entry should include, where applicable:

- `jobId`
- `itemId`
- `pipelineVersion`
- `workerImageDigest`
- `stage`
- `eventType`
- `durationMs`
- `attempt`
- `errorCode`

Required metrics include:

- Job and item duration.
- Model initialization duration.
- Items succeeded, failed, blocked, and skipped.
- Peak GPU memory and system memory where measurable.
- Input and output bytes.
- Detections per image.
- Time spent in each pipeline stage.

Logs must not contain signed URLs, bearer tokens, raw service-account credentials, or unrelated customer metadata.

## 17. Testing strategy

### Unit tests

- Manifest parsing and validation.
- Configuration validation.
- Output path construction.
- Terminal-marker compatibility checks.
- Mask preparation and combination.
- State/result enumeration.
- Error-to-exit-code mapping.

Unit tests must not require a GPU.

### Integration tests

- Local manifest with local files.
- Cloud Storage read/write against a test project or isolated bucket prefix.
- One deliberately corrupt image among valid images.
- Rerun skips compatible completed items.
- Stale terminal marker is rejected.
- A replaced manifest or source object generation is rejected.
- Concurrent/replayed completion-marker writes are protected by generation preconditions.
- A blocked, missing, or unreliable product mask never invokes LaMa and never yields `finalUri`.

### GPU smoke tests

- CUDA is available.
- Every selected model loads.
- One representative image completes end to end.
- A five-image manifest completes in one process.
- Each selected model stage initializes once, and completed model stages are released before the next stage.
- Peak VRAM, system RAM, local disk, and model initialization time are recorded.

### Manual regression check

- Assert expected item decisions from the private evaluation manifest.
- Review every high-risk negative case and every changed decision/mask/final image side by side.
- Require human approval for material or intentional quality changes; reject any unapproved product alteration.

### Batch acceptance test

- Submit the image digest manually to Batch.
- Confirm the task uses an L4.
- Confirm artifacts and output manifest are written.
- Confirm only the compatible job `_COMPLETE.json` allows the manifest to be consumed.
- Confirm the VM is deleted after completion.
- Confirm a retry resumes from compatible terminal markers instead of reprocessing completed items.
- Confirm only retryable exit codes are retried.

## 18. Implementation tasks

### W0. Close safety, storage, and infrastructure decisions

Acceptance:

- The decisions in section 3.7 and ADR-001 are accepted by the worker and product owners.
- The final-image policy is `source_original`; preprocessing artifacts are never customer final images.
- The source/manifest generation and checksum contract, terminal-marker preconditions, and retryable exit codes are approved.
- An explicit Batch template identifies the service account, driver strategy, VPC, allowed locations, and image digest input.

Verify:

- Review the contract with the FastAPI/orchestration owner before either service is implemented.

### [ ] W1. Freeze pipeline version 1

Acceptance:

- One notebook path and enabled stage set are selected.
- All model artifact generations/checksums, source revisions, prompts, thresholds, and safety-gate values are recorded.
- Custom-weight provenance and licenses are approved.
- A private evaluation manifest contains expected decisions and approvals for representative and high-risk negative images.

Verify:

- Record the selected notebook outputs as a reference and approve the pinned production configuration.

### [x] W2. Add contracts and local CLI shell

Acceptance:

- The worker validates a version 1 manifest.
- Local input/output mode works without model inference.
- Result, terminal-marker, and job-marker schemas are represented by typed Python models.

Verify:

- Unit tests cover valid, invalid, duplicate, unsupported, replaced-generation, and checksum-mismatched manifests.

### [ ] W3. Extract model initialization

Acceptance:

- Selected models load through a single model registry/lifecycle component.
- Model files are checksum-validated.
- No model download or repo clone occurs inside per-item processing.

Verify:

- GPU smoke test proves sequential model lifecycle, reports device metadata, and records peak resource use.

### Current status (W1-W5)

- W1 remains open. The repository does not contain the approved production model-object
  generations/checksums, source revisions and patch hashes, license/provenance approvals, or the
  private evaluation manifest with human approvals required by its acceptance criteria.
- W2 is complete. It implements the typed contracts, local/GCS CLI validation, local `inputPath`
  contract, decoded-image limits, conditional result and `_COMPLETE.json` creation, and structured
  JSON logging. The tested `--validate-only` local path writes terminal `UNVERIFIED` results with no
  `finalUri`; it intentionally does not load models or run inference. Unit and integration tests
  cover valid, invalid, duplicate, unsupported, replaced-generation, and checksum-mismatched
  manifest execution.
- W3 has a tested foundation but remains open. The model-artifact cache reads every configured
  artifact only at its pinned GCS generation, verifies size and SHA-256 before caching it, and
  records immutable cache metadata. The stage lifecycle permits each stage to initialize once
  before release. No production loader factory, model object, GPU smoke test, or Batch run exists
  until W1 supplies approved artifact locations, checksums, source revisions, and licenses.
- W4's local orchestration implementation is complete against a deterministic, test-only reference
  executor. It exercises decoded input through safety-gated inpainting, writes the documented
  artifacts and structured results, and keeps the production CLI fail-closed when no real executor
  is injected. Production model binding and private quality evaluation remain follow-up acceptance
  work after W1 and W3.
- W5's local batching and resumability implementation is complete. Integration tests cover a
  five-item batch, corrupt-item isolation, per-item checkpointing, interrupted execution, partial
  replay, and a subsequent replay with zero model-stage calls. GCS execution remains W6 scope.

### [x] W4. Extract one complete local pipeline path

Implementation acceptance:

- [x] One image runs from decoded input through final inpainting using an injected reference
  executor.
- [x] Artifacts match the documented local output contract.
- [x] Per-item failures are structured.
- [x] Reference stages are test-only; production execution remains fail-closed without an injected
  executor.

Deferred production acceptance:

- [ ] Replace the reference executor with approved production stage loaders after W1 and W3.
- [ ] Run the worker on the private evaluation set; assert expected decisions and manually review
  all high-risk outputs.

### [x] W5. Add batch iteration and resumability

Implementation acceptance:

- [x] A five-image manifest initializes each selected reference stage once.
- [x] Completed items are validated and skipped on rerun.
- [x] One corrupt image does not abort remaining items.
- [x] An unverified safety gate blocks inpainting and leaves `finalUri` absent.
- [x] Terminal item results are committed incrementally so interruption preserves completed work.

Verify:

- [x] Interrupt and rerun a local batch; inspect result, terminal, and job markers.
- [x] Replay the completed batch and assert that no reference stage runs again.
- [ ] Repeat the five-image and interruption checks with approved production stages on the target
  GPU after W1, W3, W7, and W8.

### W6. Add Cloud Storage adapter

Acceptance:

- Manifest and images can be read from approved GCS locations.
- Manifest and source reads use the pinned GCS generations.
- Artifacts are uploaded with `_COMPLETE.json` last, using generation preconditions.
- Storage failures are classified as retryable or permanent with stable error codes.

Verify:

- Integration test in an isolated test prefix covers generation replacement, collision, replay, and stale-marker rejection.

### W7. Build the production container

Acceptance:

- Image builds reproducibly.
- Image runs as the documented CLI.
- No runtime package installation, repo clone, public mutable model dependency, or remote executable model code remains.

Verify:

- Run local container smoke tests and scan the image for critical/high vulnerabilities.

### W8. Submit a manual Batch GPU job

Acceptance:

- Batch creates an on-demand `g2-standard-4` VM.
- CUDA and L4 detection succeed.
- A five-image manifest completes and writes a compatible output manifest and job completion marker.
- Batch removes the VM after completion.

Verify:

- Review Batch state, Cloud Logging, GCS marker generations, runtime, resource metrics, and billing labels.

### W9. Run representative performance and quality evaluation

Acceptance:

- Representative batch runtime, throughput, peak VRAM/RAM/disk, failures, and cost are recorded.
- Outputs for the private evaluation images meet automated decision expectations and receive required human review.
- The selected machine and disk sizes are confirmed or adjusted from evidence.

Verify:

- Publish an evaluation report tied to the worker image digest and pipeline version.

## 19. Completion criteria

Worker foundation is complete when:

- The notebook is no longer required for batch execution.
- The production pipeline and models are pinned and versioned.
- One container command processes local and GCS manifests.
- Each selected model stage initializes once per process.
- Per-item artifacts follow the documented contract.
- Retries skip compatible completed items.
- Blocked or unverified items never run LaMa and never expose a preprocessing image as final output.
- Manifest, source, item marker, and job marker identities are generation- and checksum-verified.
- A manual Google Cloud Batch L4 execution succeeds.
- Manual notebook-versus-worker review shows no unapproved material regression.
- Runtime and resource measurements support the chosen VM shape.
- No API database or user-authentication secret is present in the worker.

## 20. Remaining inputs before W1 and release

The architecture decisions are closed; the following concrete values must be supplied by W1 or the environment owner and must not be guessed during implementation:

- Approved model-object generations, checksums, source revisions, and custom-weight license/provenance records.
- Exact maximum compressed bytes, dimensions, pixel count, and item count.
- Artifact retention, lifecycle, and deletion policy for originals, masks, annotations, and final images.
- Approved VPC, L4-capable zone(s), quota/capacity result, and service-account identities.
- Benchmark evidence confirming the VM shape and boot-disk size, or the approved larger replacement.
