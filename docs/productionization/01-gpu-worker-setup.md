# GPU Watermark Worker: Production Setup Plan

Status: W1, W2, and W4-W7 code implementation complete; W3 Phase A and CPU-only Phase B complete;
production acceptance remains gated by W0 and the deferred live-GCS, GPU, and Batch checks

Last updated: 2026-07-19

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
- Add unit, integration, GPU smoke, and manual setup checks.
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

The canonical version-1 configuration is `configs/watermark-v1.yaml`. A configuration containing
`null`, `TBD`, a mutable URL, or a branch name is invalid and must be rejected at startup.

W1 freezes the following setup:

- Selected notebook:
  `watermark_batch_rmbg_grounding_dino_yolo_sam2_lama_safety_gate.ipynb`.
- Enabled processing path: RMBG, combined GroundingDINO and YOLO detection, SAM 2,
  product-overlap safety gate, then LaMa.
- The optional OWLv2 classifier gate is disabled for version 1. Its immutable artifacts remain in
  `model-manifest.json` for a future pipeline version.
- YOLO uses confidence `0.35`, IoU `0.50`, image size `1024`, and augmented inference.
- The safety gate uses per-detection block `0.40`, combined warning `0.25`, combined block `0.55`,
  and minimum product area `500` pixels.
- LaMa uses two pixels of mask dilation and mask threshold `127`.
- Each job accepts at most five PNG, JPEG, or WebP images. Each compressed image is limited to
  10,000,000 bytes, dimensions are limited to 4096 by 4096, and decoded area is limited to
  16,000,000 pixels.
- Manifests, inputs, and outputs use the private
  `project-5909a738-2fdc-451e-ad6-watermark-data` bucket.

The configuration contains the exact checksum-pinned artifact IDs from `model-manifest.json`.
Changing any frozen behavior value requires a new pipeline version.

The worker must run models by lifecycle stage: RMBG, detector/SAM, then LaMa. It releases a completed stage before loading the next one and must not keep every selected model resident simultaneously. This preserves the notebook's memory behavior while retaining one load per selected stage per process.

## 6. Setup-time smoke inputs

A private, versioned evaluation set and formal human-approval workflow are not required for the
initial system-setup milestone. W1 freezes the selected pipeline, configuration, model identity,
and immutable model locations without requiring a golden-image corpus or expected-decision
manifest.

GPU and Batch setup checks may use a small operator-selected manifest of authorized images. These
inputs exist only to prove that the pinned models load, the pipeline completes, artifacts are
written, and resumability works. Formal product-quality benchmarking can be added later as a
separate product-readiness activity and does not block W1-W9 in this plan.

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
- Do not download executable model code from a model provider during worker startup.
- Setup-stage RMBG exception: `trust_remote_code=True` is allowed only with
  `local_files_only=True` against the already downloaded and extracted RMBG bundle. That bundle is
  pinned by its private GCS object generation and SHA-256 manifest identity before extraction. If
  the custom source is absent from the verified bundle, model loading must fail rather than access
  the network. See [ADR-003](../decisions/ADR-003-local-verified-rmbg-code.md).

### Model files

`model-manifest.json` records, for every model:

- Artifact ID, ending with the model file's SHA-256.
- Safe local filename.
- SHA-256 checksum.
- Private GCS object URI.
- Immutable GCS object generation.

The complete runtime contract is intentionally small:

```json
{
  "schemaVersion": 1,
  "artifacts": [
    {
      "artifactId": "sam2-large-<sha256>",
      "filename": "sam2.1_hiera_large.pt",
      "checksumSha256": "<sha256>",
      "uri": "gs://watermark-models/watermark-v1/sam2.1_hiera_large.pt",
      "generation": "123456789"
    }
  ]
}
```

Model files are initially downloaded from a private, versioned Cloud Storage prefix during
container startup. SHA-256 validation is performed on the bytes from that required download before
the file is cached; it is not a second download or a separate preflight transfer. A compatible
cached file is reused. Only artifacts referenced by the enabled production stages should be
materialized. Baking them into the container can be evaluated after measuring startup and pull
times. Mutable public URLs, mutable repository branches, and runtime repository clones are not a
production model registry.

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

Container vulnerability handling follows
[ADR-002](../decisions/ADR-002-container-vulnerability-policy.md). The automated image gate blocks
embedded secrets and fixable high/critical findings. Unfixed severe base-image findings do not
block the reference build automatically, but each remains a production-release review item. Any
reachable finding requires remediation or a named, time-bounded exception before promotion.

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

### Manual setup check

- Run a small operator-selected manifest of authorized images.
- Confirm masks, annotations, results, final-image rules, and completion markers are inspectable.
- Confirm a blocked or unverified safety decision never invokes LaMa or produces `finalUri`.

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

### [x] W1. Freeze pipeline version 1

Acceptance:

- [x] Select one notebook path and the enabled stage set, including whether the optional OWLv2 gate
  is part of version 1.
- [x] Record model artifact GCS URIs, generations, and SHA-256 checksums in
  `model-manifest.json`.
- [x] Create `configs/watermark-v1.yaml` with the selected artifact IDs and no placeholders.
- [x] Approve the detector prompt, detector thresholds, safety-gate values, inpainting settings,
  image limits, and allowed storage buckets in that configuration.
- [x] Load the configuration and model manifest together, verify every enabled artifact binding,
  and approve their hashes as pipeline version 1.

Verify:

- Approve the pinned production configuration and model manifest.

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

- [x] Dependency-light tests prove sequential model lifecycle and acceptance-metric emission.
- [ ] GPU smoke test proves the same lifecycle on L4 hardware and records real peak resource use.

Local implementation checkpoint (2026-07-18):

- The artifact cache streams only the enabled, generation-pinned GCS objects to local temporary
  files. It validates each completed file against the manifest SHA-256 before publishing it into
  the cache; model files are not accumulated in process memory.
- Verified `.tar` and `.zip` bundles are expanded once into an identity-pinned local cache. Archive
  path traversal, links, special files, excessive member counts, and excessive expanded size fail
  closed. Raw checkpoint files such as `.pt` are passed through without extraction.
- `build_production_executor` is the typed assembly boundary between verified artifacts and model
  runtimes. It excludes disabled artifacts (OWLv2 is currently disabled), passes both enabled
  detectors plus SAM 2 to the combined detection stage, and defers every heavyweight runtime load
  until execution reaches that stage.
- The executor initializes and releases stages sequentially: RMBG, combined
  GroundingDINO/YOLO/SAM 2, then LaMa. LaMa is never initialized when no item passes the safety
  gate. No model download, repository clone, or package installation occurs in per-item processing.
- Dependency-light production stage adapters and their artifact outputs are locally tested with
  in-memory runtime fakes. They preserve the original source as the sole LaMa image input and apply
  the notebook's threshold `127` and two-pixel mask dilation.
- Phase A implementation now includes a local-only GPU provider and adapters for RMBG tensor
  preprocessing/compositing, combined GroundingDINO and YOLO box detection, one-pass SAM 2 box
  segmentation, and in-process LaMa inference. Heavy modules are imported only when a runtime is
  loaded. GroundingDINO uses `local_files_only=True`; all model paths come from checksum-verified,
  prepared artifacts. CUDA availability is checked once before a provider can be used, and each
  lifecycle stage explicitly releases its model references and CUDA allocator/IPC memory.
- The binary safety mask uses an immutable one-byte-per-pixel raster. This bounds a 16-megapixel
  mask at about 16 MB of mask payload instead of representing millions of Python coordinate tuples.
- `gpu-runtime-candidates.toml` records the exact image dependency versions and pins the SAM 2 and
  LaMa source revisions to immutable commit SHAs. It is marked `cpu-image-verified`: the remote
  CPU build resolved a hash-locked environment and passed import/ABI and read-only CLI checks, but
  it is not GPU-accepted and must not be treated as a production release image.
- The RMBG runtime uses Transformers with `local_files_only=True` and `trust_remote_code=True`
  against the checksum-verified, generation-pinned local bundle. This preserves the notebook's
  custom-model loading behavior without permitting a runtime network fetch. A dependency-light
  test fixes both flags as part of the loader contract.
- LaMa's `saicinpainting` Python package is vendored from commit
  `786f5936b27fb3dacd2b1ad799e4de968ea697e7` with its Apache-2.0 license and provenance. The
  maintained checkpoint patch explicitly passes `weights_only=False` for the trusted,
  checksum-verified checkpoint and falls back for older PyTorch call signatures. Tests preserve
  the revision, license, wheel inclusion, and patch contract. Its training-only dataset, evaluator,
  and perceptual-loss imports are deferred on the `predict_only` path so inference does not require
  `webdataset` or the unbundled training assets. PyTorch Lightning 1.9 still requires
  `pkg_resources`, so the image pins Setuptools 81.0.0; Setuptools 82 and newer removed that API.
- Phase B local preparation packages the SAM 2 Python runtime and YAML configurations from commit
  `2b90b9f5ceec907a1c18123530e92e794ad901a4` under `src/sam2`, with its Apache-2.0 license and
  provenance. The optional CUDA extension is excluded, matching the notebook's
  `SAM2_BUILD_CUDA=0` setup; the checkpoint remains a generation-pinned GCS artifact.
- `requirements-gpu.in` contains the direct Linux AMD64/Python 3.11/CUDA 12.1 compatibility
  inputs. Task 4 resolved `requirements-gpu.lock` remotely with SHA-256 hashes; the lock file SHA-256
  is `805c08d08e544a15f429d0b1badd48ba3c2654414244d5bae27fe6aa4d4c692d`.
- `Dockerfile.gpu` is the fail-closed GPU image recipe. It has no default base image and uses the
  reviewed PyTorch 2.5.1/CUDA 12.1 base at digest
  `sha256:831247999fbf7e08f61b3e39f6d77ee434f38f6f07f769d00db451e853878067` plus the generated
  hash lock. It packages source but no model weights, installs no code at runtime, and runs as
  UID/GID 10001.
- When inference is requested without an injected test executor, the CLI now assembles the
  production executor from the generation-pinned model cache, safe bundle cache, and local GPU
  provider. Assembly occurs only when at least one valid, incomplete item needs inference;
  validation-only runs, corrupt-only batches, and completed replays do not import GPU dependencies
  or materialize models. `WM_MODEL_WORK_ROOT` can override the model cache root; otherwise it uses
  `$TMPDIR/models`.
- Phase A and Phase B Tasks 1-4 are complete at the CPU image-verification level. Cloud Build
  `3be291ae-be70-4d8b-9e97-5a93e0979cb7` passed the runtime import/CUDA ABI check and two read-only,
  network-disabled validation/replay runs, then published the private 6,497,389,279-byte image as
  `us-central1-docker.pkg.dev/project-5909a738-2fdc-451e-ad6/watermark-worker/watermark-worker@sha256:a5064be9496c40d7a39a873ee946fb234b7709b2608faa5d309c8bdd13a9e8b1`.
- Per operator direction, security scanning was removed entirely from W3 and is not a W3 acceptance
  requirement. The GPU image build contains no scanner step and makes no scanning claim.
- This checkpoint installed the heavy dependencies only in the remote CPU-built image. It did not
  install them locally; download model files; connect to, start, or rent a GPU; submit a Batch job;
  or run the GPU smoke test.

### Current status (W1-W7)

- W1 is complete. The selected notebook and combined GroundingDINO/YOLO detector path are recorded,
  seven production model objects are pinned in `model-manifest.json`, and
  `configs/watermark-v1.yaml` freezes the enabled artifact bindings, notebook defaults, five-image
  limits, and private data bucket. The configuration and manifest load together successfully
  without downloading model files.
- W2 is complete. It implements the typed contracts, local/GCS CLI validation, local `inputPath`
  contract, decoded-image limits, conditional result and `_COMPLETE.json` creation, and structured
  JSON logging. The tested `--validate-only` local path writes terminal `UNVERIFIED` results with no
  `finalUri`; it intentionally does not load models or run inference. Unit and integration tests
  cover valid, invalid, duplicate, unsupported, replaced-generation, and checksum-mismatched
  manifest execution.
- W3's Phase A code-level implementation and Phase B CPU image work are complete, but W3 remains
  open until its GPU acceptance checks pass. Enabled artifacts stream from
  pinned GCS generations into a checksum-validated cache; safe bundle preparation, compact safety
  masks, CUDA preflight, typed local runtime-provider assembly, lazy production stage adapters,
  sequential release, and the no-LaMa-on-blocked path are covered by dependency-light tests. The
  RMBG source-vendoring requirement is intentionally deferred under ADR-003. The dependency lock,
  reviewed base-image digest, private remote image build, CPU-only import verification, replay
  checks, and immutable image digest are recorded. GPU acceptance instrumentation now emits device
  metadata, stage initialization/release timing, current and peak CUDA allocation, peak process
  RAM, and local-disk use. Actual measurements and the GPU smoke test remain deferred. No GPU or
  Batch operation was performed for this checkpoint.
- W4's local orchestration implementation is complete against a deterministic, test-only reference
  executor. It exercises decoded input through safety-gated inpainting, writes the documented
  artifacts and structured results. The production CLI now assembles the real local provider when
  no executor is injected, while tests replace only that assembly boundary. Production model
  execution remains follow-up GPU acceptance work under W3.
- W5's local batching and resumability implementation is complete. Integration tests cover a
  five-item batch, corrupt-item isolation, per-item checkpointing, interrupted execution, partial
  replay, and a subsequent replay with zero model-stage calls.
- W6's implementation is complete. The GCS CLI path, generation-aware storage adapter, conditional
  artifact and marker writes, stable storage-error classification, replay behavior, collision
  handling, and stale-marker rejection are covered by a persistent fake GCS backend. The opt-in
  real-GCS acceptance test remains deferred until isolated bucket prefixes and credentials exist.
- W7's reference-container implementation is complete. The AMD64 image uses digest-pinned Python
  and uv inputs, installs from `uv.lock`, removes runtime packaging tools, runs as UID/GID 10001,
  and passes read-only, network-disabled CLI and replay smoke tests. Its pragmatic security gate
  reports zero fixable high/critical findings and zero secrets. The full 2026-07-18 report contains
  22 currently unfixed Debian findings; they are production-review items under ADR-002, not silent
  allowlist entries. CUDA/L4, approved models, SIGTERM checkpointing, and Batch remain deferred.

### Handoff checkpoint for the next agent (2026-07-19)

The current worktree is on branch `watermark_pipeline`. Commit `2c6708a` contains W3 Phase B Tasks
1-3. The Task 4 lock, image-build configuration, runtime fixes, evidence records, and documentation
described below are present in the worktree but are not committed yet. Preserve unrelated user
changes and review the complete diff before committing.

| Workstream | Code/build status | Still deferred or required |
| --- | --- | --- |
| W3 Phase A | Complete. Production provider, generation/checksum-verified model cache, safe bundle preparation, compact masks, lazy model adapters, safety-gated LaMa, and sequential stage release are implemented and dependency-light tested. | Actual selected-model execution has not run on CUDA hardware. |
| W3 Phase B | Complete at the CPU-image-verification level. The dependency lock, digest-pinned CUDA/PyTorch base, private image, runtime import/CUDA ABI gate, read-only validation/replay checks, and dependency-light GPU acceptance instrumentation are implemented. Security scanning is not part of W3. | Rebuild the immutable image with the acceptance instrumentation, then run CUDA/L4 one- and five-image smoke tests and collect the emitted measurements. |
| W4 | Code implementation complete. The full local pipeline path and production assembly boundary are tested. | Run an operator-selected smoke manifest and inspect real model decisions/artifacts after W3 GPU authorization. |
| W5 | Code implementation complete. Five-item batching, interruption, resumability, corrupt-item isolation, and zero-stage replay are tested with reference stages. | Repeat five-item/interruption acceptance with approved production stages on the target GPU. |
| W6 | Code implementation complete. Generation-pinned GCS reads, conditional writes, markers, collision handling, and stale-marker rejection are tested hermetically. | Opt-in live-GCS acceptance against isolated prefixes; this does not require a GPU but must use approved test paths. |
| W7 | Reference-container implementation complete and its historical reference-image security gate passed. The GPU image also passed non-root, read-only, network-disabled CPU validation/replay. | Repeat smoke, SIGTERM checkpoint, and CUDA/L4 checks on the target GPU. No GPU-image scan is required by W3. |

The latest immutable CPU-verified image, including acceptance instrumentation and packaged runtime
configuration/model manifest, is:

```text
us-central1-docker.pkg.dev/project-5909a738-2fdc-451e-ad6/watermark-worker/watermark-worker@sha256:16da56ccf7095a84134e11899f44f5be5f71511d265de09975a8cca9edaa40a2
```

Cloud Build `ee4d4eca-3e2f-4def-9a36-6d65cf31b8e6` is the latest successful publication build. Its
evidence is under
`gs://project-5909a738-2fdc-451e-ad6-watermark-data/cloud-build/gpu-images/ee4d4eca-3e2f-4def-9a36-6d65cf31b8e6/`.
The runtime evidence records PyTorch `2.5.1+cu121`, torchvision `0.20.1+cu121`, CUDA ABI `12.1`,
Transformers `4.44.2`, `ultralytics-opencv-headless` `8.4.100`, Setuptools `81.0.0`, OpenCV,
SAM 2, and LaMa imports. This is CPU-only compatibility evidence; it does not prove CUDA device
availability, GPU kernels, model correctness, performance, or memory capacity.

Operator constraints at this checkpoint:

- Do not create, rent, start, or connect to a GPU VM; submit a Batch GPU job; or run the GPU smoke
  test without new explicit operator authorization.
- Do not install PyTorch, Transformers, or the heavy GPU dependency set on the local workstation.
- Security scanning is explicitly outside W3 scope by operator decision. Do not add it back to W3,
  block GPU acceptance on it, or describe the image as scanned.
- OWLv2 remains disabled. GroundingDINO and YOLO remain enabled together, followed by SAM 2 and
  LaMa, as frozen in `configs/watermark-v1.yaml`.

### GPU acceptance preparation: Tasks 1-3 (2026-07-19)

These tasks prepare acceptance without provisioning hardware. They performed no resource creation,
Batch submission, GPU connection, model download, or local heavyweight dependency installation.

Task 1, read-only preflight, found:

- Project `project-5909a738-2fdc-451e-ad6` exposes `g2-standard-4` in `us-central1-c` with four
  vCPUs, 16,384 MiB RAM, and one NVIDIA L4.
- The regional CPU quota is 32 with zero in use. Quota preference
  `compute-us-central1-nvidia-l4` requests one on-demand NVIDIA L4 in `us-central1`, but Google
  granted `0` with the state detail that it cannot grant the requested value at this moment. This
  is a hard submission gate; no Batch GPU job may be submitted until the granted value is at least
  one.
- Batch, Compute Engine, Cloud Logging, and Cloud Quotas APIs are enabled. The Google-managed Batch
  service identity has `roles/batch.serviceAgent`.
- `default` and `infringement-vpc` exist. The latter has the private
  `cloud-run-private-subnet` (`10.10.0.0/26`) with Private Google Access enabled; the final Batch
  template must select its network/subnetwork explicitly and still provide the egress needed by
  automatic GPU-driver installation.
- Dedicated service account `watermark-gpu-worker@project-5909a738-2fdc-451e-ad6.iam.gserviceaccount.com`
  exists. It has model-object read, data-object read/create, Artifact Registry read, and log-writer
  access; it has no project editor, storage admin, Batch submission, or service-account-key role.

Task 2 is implemented in code. `PipelineExecutor` reports each production stage's initialization
and release duration. `GpuAcceptanceRecorder`, attached only when the default GPU runtime is
assembled, emits `gpu_acceptance_started` and `gpu_stage_lifecycle` structured records containing
the CUDA device/version, current and peak CUDA allocator bytes, peak process RAM, and work-disk
used/free bytes. Dependency-light fakes test this path; PyTorch and Transformers were not installed
locally. The latest immutable image above contains this instrumentation and the packaged
`configs/watermark-v1.yaml` and `model-manifest.json` required by Batch execution.

Task 3's manifest tooling is implemented. Five intentionally low-quality repository PNGs were
copied into private, deterministic job prefixes. `gpu-acceptance/input-inventory.json` records the
exact generations and SHA-256 checksums, while the checked-in one- and five-image manifests both
pass the production manifest validator. Their pinned GCS identities are:

- One image: `gs://project-5909a738-2fdc-451e-ad6-watermark-data/gpu_acceptance/gpu_smoke_one_20260719t090500z/input-manifest.json`, generation `1784452037999378`.
- Five images: `gs://project-5909a738-2fdc-451e-ad6-watermark-data/gpu_acceptance/gpu_smoke_five_20260719t090500z/input-manifest.json`, generation `1784452046042662`.

The general inventory shape remains:

```json
{
  "oneImage": {
    "itemId": "asset_1",
    "inputUri": "gs://project-5909a738-2fdc-451e-ad6-watermark-data/gpu_acceptance/gpu_smoke_one_TIMESTAMP/asset_1.png",
    "inputGeneration": "GCS_GENERATION",
    "sourceChecksumSha256": "LOWERCASE_SHA256_FROM_THE_UPLOAD"
  },
  "fiveImages": [
    {
      "itemId": "asset_1",
      "inputUri": "gs://project-5909a738-2fdc-451e-ad6-watermark-data/gpu_acceptance/gpu_smoke_five_TIMESTAMP/asset_1.png",
      "inputGeneration": "GCS_GENERATION",
      "sourceChecksumSha256": "LOWERCASE_SHA256_FROM_THE_UPLOAD"
    }
  ]
}
```

The real `fiveImages` array must contain exactly five entries. Generate both validated manifests
with:

```bash
python -m watermark_worker.smoke_manifests \
  --inventory gpu-smoke-inputs.json \
  --destination gpu-smoke-manifests \
  --output-bucket project-5909a738-2fdc-451e-ad6-watermark-data
```

This creates `one-image-manifest.json` from the first entry and `five-image-manifest.json` from all
five. It writes deterministic output prefixes and performs no GCS reads or writes.

### GPU acceptance Tasks 4-5 checkpoint (2026-07-19)

Task 4's non-GPU setup is complete. `gpu-acceptance/batch-one-image.json` pins the latest digest,
manifest generation, `g2-standard-4`, `STANDARD`, `NO_RESERVATION`, `us-central1-c`, the dedicated
service account, explicit default network/subnetwork, automatic driver installation, one task,
zero retries, a 90-minute timeout, a 200 GB balanced boot disk, and Cloud Logging. Contract tests
prevent those bounds from drifting. It has **not** been submitted because granted L4 quota is zero.

Task 5's validation boundary is recorded in `gpu-acceptance/README.md`. It checks model/runtime
loading, CUDA/L4 identity, stage lifecycle, detections, masks, annotations, results, completion
markers, GCS generations, and VM cleanup. It explicitly does not judge visual detection, mask, or
inpainting accuracy. Live validation remains blocked until Task 4 can submit the one-image job.

At this checkpoint there are no Batch jobs, Compute Engine VMs, or persistent disks in the project;
there is no active GPU or compute runtime charge.

### Google Cloud resource and cost checkpoint (2026-07-19)

Read-only inventory after Task 4 found no active Cloud Builds, Compute Engine instances, persistent
disks, or `us-central1` Batch jobs. No GPU resource exists or is accruing runtime charges. Merely
enabling APIs, retaining service accounts/IAM roles, or retaining an empty repository does not
create compute charges.

Resources that remain and can accrue storage charges:

| Resource | Observed stored size | Cost/retention notes |
| --- | ---: | --- |
| Private Artifact Registry `watermark-worker` | 6,497.428 MB reported by GCP (6,497,389,279-byte image plus small provenance records) | Immutable tags; automatic vulnerability scanning disabled; no cleanup policy observed. Approximately 6.05 GiB remains billable until deleted. |
| `watermark-models` bucket | 5,706,688,651 bytes (~5.315 GiB) | Regional Standard storage in `us-central1`; seven-day soft delete; no lifecycle rule observed. |
| `watermark-data` bucket | 415,474 bytes | Build locks/evidence are negligible; seven-day soft delete; no lifecycle rule observed. |
| Cloud Build source bucket | 3,730,341 bytes | `US` multi-region; future source archives accumulate because no lifecycle rule was observed. |

All three buckets use uniform bucket-level access and their IAM policies contain no `allUsers` or
`allAuthenticatedUsers` grant; none is public. Soft-deleted bytes are charged like live bytes during
the seven-day soft-delete window. No objects were deleted as part of this checkpoint.

At the published rates checked on 2026-07-19, approximate ongoing storage is:

- Artifact Registry: about `$0.56/month` if the billing account's 0.5 GiB free allowance is
  available, or about `$0.61/month` without it.
- Cloud Storage: about `$0.006/month` if the account is eligible for and has the 5 GiB
  `us-central1` Always Free allowance available, or about `$0.107/month` without that allowance.
- Combined ongoing storage: approximately `$0.56-$0.72/month`, depending on billing-account-wide
  free-tier usage. Operations and future data transfer can add small variable charges.

The six currently queryable final Task 4 builds used about 49.66 `E2_STANDARD_2` build-minutes,
whose list price is about `$0.30`. This is normally `$0` when the billing account still has its
2,500 monthly free build-minutes, but only Cloud Billing can confirm the actual invoice because free
allowances are shared across projects. The final successful build used the first 100 GB build disk,
which is included in the Cloud Build allowance. There is no ongoing Cloud Build charge after a
build reaches a terminal state.

Project billing is enabled. Budget/alert status could not be verified: the Cloud Billing Budget API
is disabled and the current account does not have permission to list billing-account budgets. The
API was not enabled during this checkpoint. Do not assume a budget alert or hard spending cap
exists; an authorized billing administrator should confirm or create alerts in Cloud Billing.

Pricing references: [Cloud Build](https://cloud.google.com/build/pricing),
[Artifact Registry](https://cloud.google.com/artifact-registry/pricing), and
[Cloud Storage](https://cloud.google.com/storage/pricing).

### [x] W4. Extract one complete local pipeline path

Implementation acceptance:

- [x] One image runs from decoded input through final inpainting using an injected reference
  executor.
- [x] Artifacts match the documented local output contract.
- [x] Per-item failures are structured.
- [x] Reference stages are test-only; production execution assembles the verified model cache and
  local GPU provider when an executor is not injected.

Deferred production acceptance:

- [x] Replace the default reference executor path with production stage loaders.
- [ ] Run an operator-selected smoke manifest and inspect the produced artifacts and decisions.

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
  GPU after W3, W7, and W8.

### [x] W6. Add Cloud Storage adapter

Implementation acceptance:

- [x] Manifest and images can be read through configured, deterministic GCS locations.
- [x] Manifest and source reads use pinned GCS generations.
- [x] Artifacts are uploaded with `_COMPLETE.json` last, using generation preconditions.
- [x] Storage failures are classified as retryable or permanent with stable error codes.

Verify:

- [x] Hermetic integration tests cover generation replacement, collision, zero-write replay, and
  stale-marker rejection with a generation-aware fake backend.
- [ ] Run the opt-in live test against isolated GCS prefixes once credentials and test buckets are
  configured.

### [x] W7. Build the reference container

Implementation acceptance:

- [x] The Linux AMD64 image builds from digest-pinned base/tool images and the committed lock file.
- [x] The image runs as the documented non-root CLI with a read-only root filesystem and no network.
- [x] No runtime package installation, repo clone, public mutable model dependency, or remote
  executable model code remains.
- [x] Runtime `pip`, `setuptools`, and `wheel` are absent.

Verify:

- [x] Local container smoke and completed-job replay pass.
- [x] The ADR-002 automated gate reports no secrets and no fixable high/critical vulnerabilities.
- [ ] Review or remediate the 22 unfixed Debian findings before promoting a production image.
- [ ] Run the GPU image smoke test, SIGTERM checkpoint test, and CUDA/L4 checks after W3 receives
  explicit GPU authorization.

### W8. Submit a manual Batch GPU job

Acceptance:

- Batch creates an on-demand `g2-standard-4` VM.
- CUDA and L4 detection succeed.
- A five-image manifest completes and writes a compatible output manifest and job completion marker.
- Batch removes the VM after completion.

Verify:

- Review Batch state, Cloud Logging, GCS marker generations, runtime, resource metrics, and billing labels.

### W9. Run representative performance evaluation

Acceptance:

- Representative batch runtime, throughput, peak VRAM/RAM/disk, failures, and cost are recorded.
- The selected machine and disk sizes are confirmed or adjusted from evidence.

Verify:

- Publish a performance report tied to the worker image digest and pipeline version.

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
- Runtime and resource measurements support the chosen VM shape.
- No API database or user-authentication secret is present in the worker.

## 20. Remaining inputs before release

W1 is frozen in `configs/watermark-v1.yaml` and `model-manifest.json`. The following release and
infrastructure values must still be supplied by the environment owner and must not be guessed
during implementation:

- Artifact retention, lifecycle, and deletion policy for originals, masks, annotations, and final images.
- Approved VPC, L4-capable zone(s), quota/capacity result, and service-account identities.
- Benchmark evidence confirming the VM shape and boot-disk size, or the approved larger replacement.
