# GPU Watermark Worker: Production Setup Plan

Status: Proposed

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

Version 1 runs one worker task on one `g2-standard-4` VM with one L4 GPU. It loads the models once and processes all items sequentially.

Creating one task per image is explicitly out of scope because repeated model initialization would waste time and GPU capacity.

### 3.5 Cloud Storage for artifacts

Original images, masks, annotations, final images, manifests, and completion markers live in Cloud Storage. PostgreSQL stores references and application state only.

### 3.6 At-least-once execution

The worker must assume that a Batch task can be retried. Processing is idempotent at item level, and a retry skips items that already have a valid success marker.

## 4. Scope

### Included

- Freeze and record pipeline version 1.
- Extract notebook logic into importable Python modules.
- Define manifest and result contracts.
- Load models once per process.
- Process a batch from local files or Cloud Storage.
- Preserve per-image review artifacts.
- Resume safely using success markers.
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

Before code extraction, create a versioned configuration file such as `configs/watermark-v1.yaml` containing every behavior-changing value:

```yaml
schema_version: 1
pipeline_version: watermark-v1

background_removal:
  enabled: true
  model: briaai/RMBG-1.4

detection:
  mode: combined
  yolo:
    enabled: true
    confidence: 0.25
    iou: 0.5
    image_size: 1024
    augment: false
  grounding_dino:
    enabled: true
    prompt: "watermark. logo. text overlay. brand mark."
    box_threshold: null
    text_threshold: null

segmentation:
  model: sam2.1_hiera_large

safety_gate:
  enabled: true
  per_detection_block: null
  combined_warn: null
  combined_block: null

inpainting:
  model: big-lama
  mask_dilation_pixels: 2
  mask_threshold: 127
```

The values shown above are placeholders where the validated notebook configuration has not yet been explicitly approved. Implementation must not silently invent missing thresholds.

## 6. Private manual verification images

Keep a simple private folder of approximately 10 authorized JPG images for manual notebook-versus-worker checks:

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

The exact filenames and mix may differ. Keep the images and filenames unchanged so repeated runs use the same inputs. Add a new authorized or sanitized image when a useful failure case is discovered.

Version 1 does not require a formal dataset schema, automated image-quality metrics, or a versioned baseline registry. Verification is manual:

1. Run the selected notebook on the folder.
2. Run the extracted worker on the same folder.
3. Compare detections, masks, safety-gate decisions, and final images side by side.
4. Approve the worker when there is no material unapproved regression.

The folder is an offline development and staging aid only. It is not bundled into the worker image, referenced by production code, or included in customer production jobs. Production jobs contain arbitrary product images supplied through their own manifests.

## 7. Input contract

### 7.1 Manifest location

The worker accepts exactly one manifest location:

```bash
python -m watermark_worker \
  --manifest-uri gs://watermark-jobs/tenant-17/wm_123/input-manifest.json
```

For local development, it also accepts a local manifest path:

```bash
python -m watermark_worker \
  --manifest-path tests/fixtures/small-manifest.json \
  --local-output-root /tmp/watermark-output
```

`--manifest-uri` and `--manifest-path` are mutually exclusive.

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
      "outputPrefix": "gs://watermark-output/tenant-17/wm_123/asset_123/",
      "sourceChecksumSha256": "optional-lowercase-hex-value"
    }
  ]
}
```

### 7.3 Validation rules

- `schemaVersion` must be supported.
- `jobId`, `tenantId`, and `itemId` must match documented length and character restrictions.
- `pipelineVersion` must exactly match a configuration packaged with the worker image.
- The manifest must contain at least one item and remain below a configured maximum item count.
- Input and output URIs must use approved buckets and prefixes.
- Duplicate `itemId` or output prefixes are rejected.
- The worker validates decoded image type, dimensions, and pixel count; it does not trust the filename or MIME metadata alone.
- The manifest is immutable after Batch submission. A changed batch requires a new application job and manifest.

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
    final.png
  result.json
  _SUCCESS.json
```

Only enabled stages need to produce their optional artifacts. `result.json` and `_SUCCESS.json` have stable meanings.

### 8.2 Result schema

```json
{
  "schemaVersion": 1,
  "jobId": "wm_123",
  "itemId": "asset_123",
  "status": "SUCCEEDED",
  "pipelineVersion": "watermark-v1",
  "workerImageDigest": "sha256:...",
  "sourceUri": "gs://watermark-input/tenant-17/wm_123/asset_123.png",
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

Allowed decisions and error codes must be enumerated in code rather than created as arbitrary strings.

### 8.3 Success marker

`_SUCCESS.json` is written last, after all required item artifacts have been uploaded and validated. It contains enough information to verify that the completed output belongs to the current source checksum, pipeline version, and worker image digest.

An existing marker is reusable only when those values match. Otherwise the worker must treat the item as stale and either fail safely or write to a new versioned output prefix; it must not overwrite an incompatible completed result silently.

### 8.4 Job output manifest

After iterating over all items, the worker writes:

```text
gs://watermark-output/tenant-17/wm_123/output-manifest.json
```

It contains counts, per-item result references, timings, worker version, and the overall outcome.

## 9. Worker execution behavior

The worker performs these steps in order:

1. Validate environment and configuration.
2. Download and validate the input manifest.
3. Initialize structured logging with `jobId` and pipeline version.
4. Validate model files and checksums.
5. Load the selected models exactly once.
6. Record GPU, CUDA, system RAM, and worker image metadata.
7. For each item:
   1. Check for a compatible `_SUCCESS.json`.
   2. Download and validate the source image.
   3. Run enabled pipeline stages.
   4. Write artifacts to a temporary local item directory.
   5. Upload artifacts to Cloud Storage.
   6. Upload `result.json`.
   7. Upload `_SUCCESS.json` last.
   8. Emit a progress event.
   9. Release item tensors and temporary files.
8. Write the job output manifest.
9. Emit final metrics and exit.

One invalid image should normally produce a failed item result and allow the batch to continue. A missing model, unsupported manifest, unavailable GPU, corrupt configuration, or inaccessible output bucket is a fatal job error.

## 10. Exit behavior

Proposed process exit codes:

| Code | Meaning |
| ---: | --- |
| `0` | Worker completed and wrote a job output manifest. Individual item failures may be present and are represented in that manifest. |
| `2` | Invalid command or unsupported manifest/configuration. |
| `3` | Required model or model checksum failure. |
| `4` | GPU/CUDA initialization failure. |
| `5` | Fatal storage or permissions failure. |
| `10` | Unexpected fatal worker failure. |

The exact mapping must be tested and documented in the worker package.

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

### Model files

`model-manifest.json` records, for every model:

- Logical name and version.
- Source and license reference.
- Expected filename.
- SHA-256 checksum.
- Size.
- Required pipeline stages.

Model files can initially be downloaded from a private, versioned Cloud Storage prefix during container startup. Baking them into the container can be evaluated after measuring startup and pull times. Mutable public URLs are not a production model registry.

## 13. Container requirements

The worker image must:

- Use a supported Linux AMD64 CUDA/PyTorch base compatible with the L4 driver.
- Run as a non-root user where practical.
- Include application code and pinned runtime dependencies.
- Use a deterministic entrypoint.
- Write temporary files only under a configured working directory.
- Handle `SIGTERM` by stopping after the current safe checkpoint when time permits.
- Emit logs to stdout/stderr.
- Avoid including GCP service-account keys or application database secrets.

Example entrypoint:

```dockerfile
ENTRYPOINT ["python", "-m", "watermark_worker"]
```

The image must be pushed to Artifact Registry with an immutable digest. Batch submissions use the digest, not `latest`.

## 14. Google Cloud Batch runtime

Initial runtime configuration:

```text
Machine type:       g2-standard-4
GPU:                1 x NVIDIA L4, attached by the G2 machine type
Provisioning model: STANDARD (on-demand)
Tasks:              1
Parallelism:        1
Task timeout:       20 hours initially
Retries:            1 initially, after resumability is verified
Boot disk:          Sized after measuring container, model cache, and temp data
Region:             Same single region as input/output buckets
Logs:               Cloud Logging
```

Manual Batch submission must be proven before FastAPI integration. Batch should install the host GPU driver automatically unless a later requirement justifies a custom VM image.

Official references:

- [Create and run a Google Cloud Batch job that uses GPUs](https://docs.cloud.google.com/batch/docs/create-run-job-gpus)
- [Google Cloud Batch job lifecycle](https://docs.cloud.google.com/batch/docs/create-run-job)
- [Google Cloud Batch pricing](https://cloud.google.com/batch/pricing)

## 15. Security boundaries

The Batch worker service account should have only the permissions required to:

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
- Success-marker compatibility checks.
- Mask preparation and combination.
- State/result enumeration.
- Error-to-exit-code mapping.

Unit tests must not require a GPU.

### Integration tests

- Local manifest with local files.
- Cloud Storage read/write against a test project or isolated bucket prefix.
- One deliberately corrupt image among valid images.
- Rerun skips compatible completed items.
- Stale success marker is rejected.

### GPU smoke tests

- CUDA is available.
- Every selected model loads.
- One representative image completes end to end.
- A five-image manifest completes in one process.
- Model initialization occurs once.

### Manual regression check

- Run the private folder of approximately 10 JPG images through the selected notebook and worker.
- Compare detections, masks, safety-gate decisions, and final images manually.
- Require human approval for material or intentional quality changes.

### Batch acceptance test

- Submit the image digest manually to Batch.
- Confirm the task uses an L4.
- Confirm artifacts and output manifest are written.
- Confirm the VM is deleted after completion.
- Confirm a retry resumes instead of reprocessing completed items.

## 18. Implementation tasks

### W1. Freeze pipeline version 1

Acceptance:

- One notebook path and enabled stage set are selected.
- All model versions, prompts, thresholds, and safety-gate values are recorded.
- A private folder of approximately 10 representative JPG images is available for manual checks.

Verify:

- Run the selected notebook on the private image folder and review its outputs.

### W2. Add contracts and local CLI shell

Acceptance:

- The worker validates a version 1 manifest.
- Local input/output mode works without model inference.
- Result and success-marker schemas are represented by typed Python models.

Verify:

- Unit tests cover valid, invalid, duplicate, and unsupported manifests.

### W3. Extract model initialization

Acceptance:

- Selected models load through a single model registry/lifecycle component.
- Model files are checksum-validated.
- No model download or repo clone occurs inside per-item processing.

Verify:

- GPU smoke test loads every model once and reports device metadata.

### W4. Extract one complete local pipeline path

Acceptance:

- One image runs from decoded input through final inpainting.
- Artifacts match the documented local output contract.
- Per-item failures are structured.

Verify:

- Run the worker on the private image folder and manually compare its outputs with the notebook outputs.

### W5. Add batch iteration and resumability

Acceptance:

- A five-image manifest runs with one model initialization.
- Completed items are skipped on rerun.
- One corrupt image does not abort remaining items.

Verify:

- Interrupt and rerun a local batch; inspect result and success markers.

### W6. Add Cloud Storage adapter

Acceptance:

- Manifest and images can be read from approved GCS locations.
- Artifacts are uploaded with `_SUCCESS.json` last.
- Storage failures produce stable error codes.

Verify:

- Integration test in an isolated test prefix and inspect object metadata.

### W7. Build the production container

Acceptance:

- Image builds reproducibly.
- Image runs as the documented CLI.
- No runtime package installation, repo clone, or public mutable model dependency remains.

Verify:

- Run local container smoke tests and scan the image for critical/high vulnerabilities.

### W8. Submit a manual Batch GPU job

Acceptance:

- Batch creates an on-demand `g2-standard-4` VM.
- CUDA and L4 detection succeed.
- A five-image manifest completes and writes its output manifest.
- Batch removes the VM after completion.

Verify:

- Review Batch state, Cloud Logging, GCS artifacts, runtime, and billing labels.

### W9. Run representative performance and quality evaluation

Acceptance:

- Representative batch runtime, throughput, peak VRAM/RAM, failures, and cost are recorded.
- Outputs for the private verification images receive manual human review.
- The selected machine and disk sizes are confirmed or adjusted from evidence.

Verify:

- Publish an evaluation report tied to the worker image digest and pipeline version.

## 19. Completion criteria

Worker foundation is complete when:

- The notebook is no longer required for batch execution.
- The production pipeline and models are pinned and versioned.
- One container command processes local and GCS manifests.
- Models load once per process.
- Per-item artifacts follow the documented contract.
- Retries skip compatible completed items.
- A manual Google Cloud Batch L4 execution succeeds.
- Manual notebook-versus-worker review shows no unapproved material regression.
- Runtime and resource measurements support the chosen VM shape.
- No API database or user-authentication secret is present in the worker.

## 20. Open decisions before W1

- Which exact detector configuration is production version 1?
- Is RMBG enabled for every input or only selected product categories?
- What safety-gate thresholds are approved?
- Which artifacts must be retained, and for how long?
- What maximum image dimensions, bytes, and batch item count are allowed?
- Does an item-level failure allow the overall job to succeed with warnings?
- Are model weights baked into the image or downloaded from a versioned private bucket?
- What quality review determines that the worker matches the notebook sufficiently?
