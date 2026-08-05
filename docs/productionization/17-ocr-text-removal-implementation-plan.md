# OCR text-removal implementation plan

Status: Implementation in progress. The worker, v2 configuration, AWS submission boundary,
pinned EasyOCR model artifact, and backend OCR toggle are now implemented locally; production
rollout remains gated by the evaluation, image-build, and controlled-acceptance steps below.

Related follow-on plan: [runtime configuration externalization](18-runtime-config-externalization-implementation-plan.md),
which removes tunable policy values from future worker images while preserving immutable job
configuration identity.

## 1. Goal

Add EasyOCR to the core watermark-removal worker so that every OCR text region with confidence greater than or equal to **0.35** is an accepted removal candidate. The worker will inpaint the resulting text and the existing YOLO/Grounding DINO watermark candidates with LaMa.

The product-image rule supplied for this change is important: approved source images must not contain intentional readable words. Therefore OCR text is treated as removable content, not as a hint that needs corroboration by another detector. YOLO and Grounding DINO remain enabled because they find non-text overlays such as logos, icons, stickers, and unreadable/stylized watermarks.

## 2. Current system context

The worker currently executes this packaged pipeline:

```text
source original
  -> RMBG foreground mask and white-background diagnostic/detector image
  -> YOLO + Grounding DINO candidate boxes on the detector image
  -> SAM 2 masks
  -> product-overlap safety gate
  -> dilate approved union mask
  -> LaMa inpaints the source original
```

Important implementation boundaries:

- The GPU worker lives in this repository under `src/watermark_worker/` and is deployed as an immutable AWS Batch image. The public API and job lifecycle remain owned by the separate GCP FastAPI repository.
- `watermark-v1` and `watermark-bulk-v1` are accepted cross-cloud contracts. Their version, packaged configuration, model artifact IDs, worker image digest, and AWS Batch mapping must not be silently redefined.
- Each configured model must be a checksum- and GCS-generation-pinned artifact in `model-manifest.json`; the worker does not download weights at inference time.
- The current runtime is Python 3.11, PyTorch 2.5.1 with CUDA 12.1, and one NVIDIA T4 on a `g4dn.xlarge`. EasyOCR is selected because it uses PyTorch rather than adding a second GPU framework.

## 3. Accepted design decisions

### 3.1 Create new pipeline versions

Create `watermark-v2` and `watermark-bulk-v2`. They are the OCR-enabled successors to the two v1 pipelines. Keep the v1 configurations, allowed provider values, and historical job behavior unchanged until their documented retirement.

This preserves reproducibility: a `watermark-v1` job can never begin producing OCR-derived masks merely because a later image was deployed.

### 3.2 OCR consumes the original image only

OCR receives the unmodified `source_original` image. It does not require RMBG or the white-background image. This keeps watermark removal independent from a future standalone background-removal workflow and ensures all OCR coordinates already align with the image LaMa will inpaint.

The white-background image remains available to the existing visual detectors in v2 exactly as it is in v1. A second OCR pass over a derived image is explicitly out of scope. It may be evaluated later as a separately measured detector configuration, not made an implicit prerequisite.

### 3.3 OCR has direct acceptance at confidence 0.35

An EasyOCR result whose reported confidence is `>= 0.35` is accepted for removal without requiring YOLO or Grounding DINO agreement. Results below `0.35`, empty/invalid geometry, and regions below the configured minimum text-region size are discarded.

The lower bound is deliberately permissive for translucent and low-contrast watermarks. It raises false-positive risk, so every accepted candidate and final mask must remain auditable, and the existing structural safety gate remains in force. A safety-gate block is recorded as a blocked worker outcome rather than bypassed silently.

### 3.4 Keep accepted candidates independent

The first OCR release deliberately does not fuse or deduplicate boxes. The product rule has
already decided that accepted OCR text is removable, so every accepted candidate is retained and
gets its own SAM 2 prompt:

```text
OCR candidate, score >= 0.35 --------------------------> accepted
YOLO/DINO candidate ------------------------------------> existing acceptance behavior
every accepted candidate -------------------------------> one independent SAM 2 prompt
all SAM 2 masks ----------------------------------------> existing safety gate and LaMa
```

Record the detector source, score, OCR text, and OCR quadrilateral on each detection. Never discard
an OCR candidate merely because another detector disagrees or fails to detect it. If two boxes
overlap, the safety gate still unions their masks; no new bbox clustering policy is introduced in
this release.

### 3.5 Keep mask generation conservative and inspectable

EasyOCR returns a four-point text quadrilateral, while the current SAM 2 integration accepts box prompts. Preserve the quadrilateral in output evidence and send the enclosing, lightly padded box to SAM 2. SAM 2 produces the pixel mask that is combined with existing masks and then dilated by the existing LaMa configuration.

Do not directly inpaint a large rectangular OCR box unless a measured fallback is approved. A rectangle removes background around and between glyphs. If SAM 2 does not make sufficiently tight text masks on the evaluation corpus, investigate a text-stroke-mask refinement as a separate follow-up; do not silently substitute rectangle inpainting in the first release.

### 3.6 Use EasyOCR fully offline with one pinned model bundle

Package the selected EasyOCR detector and English recognizer files into one reviewed archive. Add that archive as a new immutable GCS artifact with its generation and SHA-256 in `model-manifest.json`. At runtime, configure `model_storage_directory` to the verified extracted bundle and set `download_enabled=False`.

The first version uses English only (`["en"]`). Adding language support later requires an explicit new pinned bundle and pipeline configuration/version decision; it must not trigger automatic model downloads.

## 4. Target v2 data flow

```text
                              source original
                              /             \\
                             /               \\
EasyOCR on original, >=0.35                   RMBG -> white-background image
  -> quadrilateral, text, score                         -> YOLO + Grounding DINO
  -> accepted OCR candidate                              -> visual candidates
                     \\                                  /
                      \\---- independent boxes --------/
                         |          |          |
                       SAM 2      SAM 2      SAM 2
                                      |
                  detection masks -> existing safety gate
                                      |
                         approved combined mask
                                      |
                      small existing mask dilation
                                      |
                       LaMa on source original image
```

The background-removal result is not an input dependency of OCR. A future background-removal feature may run separately from the same original image, with its own output and lifecycle.

## 5. Configuration and evidence contract

Add an explicit `detection.easyocr` section to the v2 configurations. The final field names may follow established Pydantic naming conventions, but its immutable semantics must include:

```yaml
detection:
  mode: combined
  easyocr:
    enabled: true
    model_artifact_id: easyocr-<sha256>
    languages: [en]
    confidence: 0.35
    min_region_size_pixels: <measured value>
    box_padding_pixels: <measured value>
```

Validation must reject unknown languages, invalid thresholds, zero/negative sizes, unpinned artifact IDs, and configurations with no enabled detector. `PipelineConfig.model_artifact_ids` and the model-stage definitions must include the EasyOCR artifact exactly when EasyOCR is enabled.

Extend each detection record in `detections/detections.json` without removing existing fields. EasyOCR records need, at minimum:

```json
{
  "detector": "easyocr_sam2",
  "className": "text",
  "confidence": 0.35,
  "boxXyxy": [10.0, 20.0, 150.0, 60.0],
  "polygon": [[10.0, 20.0], [150.0, 20.0], [150.0, 60.0], [10.0, 60.0]],
  "text": "example watermark",
  "samScore": 0.9,
  "sources": ["easyocr"]
}
```

`sources` contains the one detector source for each independent candidate (`easyocr`, `yolo_sam2`,
or `grounding_dino_sam2`). Text is customer image-derived data: retain it only in existing
protected job artifacts; do not emit it in broad logs, metric labels, or error messages.

## 6. Implementation tasks

### Task 1: Establish an evaluation corpus and acceptance metrics

**Description:** Assemble a representative, access-controlled corpus of approved product images: text-only watermarks, transparent/low-contrast text, rotated text, text over products, images with no watermark, and non-text logo/graphic overlays. Annotate OCR text quadrilaterals and a tight desired removal mask where practical.

**Acceptance criteria:**

- [ ] Corpus has documented ownership/access controls and does not enter the source image or test fixture repository.
- [ ] Evaluation reports text-region recall at the fixed 0.35 threshold, false-positive rate, mask IoU, and visual repair quality.
- [ ] Initial `min_region_size_pixels` and prompt padding are chosen from the corpus and written into v2 configuration.

**Verification:** A reproducible, offline benchmark command produces a versioned aggregate report; no customer image bytes or recognized text appears in routine logs.

**Dependencies:** None. This task must complete before the settings marked “measured value” are frozen.

### Task 2: Build, review, and register the offline EasyOCR model bundle

**Description:** Select exact EasyOCR and model-weight versions, obtain the detector and English recognizer through a reviewed build process, package them into one safe archive, and upload the archive to the existing private model bucket. Record the immutable URI, object generation, file name, and SHA-256 in the model manifest.

**Likely files:**

- `requirements-gpu.in`
- regenerated `requirements-gpu.lock`
- `model-manifest.json`
- GPU image smoke/lock tests and model-bundle tests

**Acceptance criteria:**

- [x] `easyocr` resolves with the existing PyTorch 2.5.1/CUDA 12.1 runtime and does not replace `torch` or `torchvision`.
- [x] Both EasyOCR weights are present in the verified archive; `download_enabled=False` is configured after artifact preparation and a missing file fails closed.
- [x] The new artifact is checksum-pinned and uploaded to the private model bucket; production image/network acceptance remains pending.
- [x] The new artifact conforms to current checksum, immutable-generation, safe extraction, and maximum-expanded-size rules.

**Verification:** Regenerate the lock through the existing remote lock process; build the GPU image; run an offline container test that imports EasyOCR but verifies it has not initialized CUDA until the worker runs.

**Dependencies:** Task 1 for the selected model/version and image-resolution settings.

### Task 3: Add v2 configuration and immutable model binding

**Description:** Add typed `EasyOcrConfig`, include its artifact in materialization and prepared model artifacts, and create `watermark-v2.yaml` and `watermark-bulk-v2.yaml`. Preserve v1 files and their validation expectations unchanged.

**Likely files:**

- `src/watermark_worker/config.py`
- `src/watermark_worker/model_registry.py`
- `src/watermark_worker/production_executor.py`
- `configs/watermark-v2.yaml`
- `configs/watermark-bulk-v2.yaml`
- `model-manifest.json`
- configuration, model-binding, and stage-definition tests

**Acceptance criteria:**

- [x] Both v2 files set EasyOCR `confidence: 0.35`, use the original image as OCR input by design, and reference the same pinned EasyOCR artifact.
- [x] The worker selects precisely the configured artifacts for each v1/v2 pipeline and rejects missing or extra configured artifacts.
- [x] Loading v1 configurations yields the existing artifact set and behavior.

**Verification:** Unit tests cover valid v2 configuration, threshold boundary `0.35`, invalid OCR configuration, both v2 manifests, and unchanged v1 contract fixtures.

**Dependencies:** Task 2.

### Task 4: Implement the offline EasyOCR detector adapter

**Description:** Add an `ObjectDetector` implementation that loads one preconfigured EasyOCR Reader on the assigned CUDA device, reads only the original image, returns valid text quadrilaterals/text/confidence, and releases references and CUDA memory with the current runtime lifecycle.

**Likely files:**

- `src/watermark_worker/gpu_runtime/detection.py`
- `src/watermark_worker/gpu_runtime/provider.py`
- `src/watermark_worker/production_executor.py`
- GPU detection/runtime-provider tests

**Acceptance criteria:**

- [x] A reader is loaded once per Batch attempt, never per image, and only from the prepared artifact directory.
- [x] Confidence is evaluated with `>= 0.35`; malformed, out-of-image, empty, and undersized regions are rejected deterministically.
- [x] The implementation creates no network client and requests no model download.
- [x] It supports CPU-free unit fakes and closes deterministically on success and error paths.

**Verification:** Unit tests cover confidence equal to 0.35, below threshold, coordinate clamping, polygon-to-box conversion, original-image selection, model-missing failure, and lifecycle release.

**Dependencies:** Tasks 2 and 3.

### Task 5: Preserve independent candidate evidence and output compatibility

**Description:** Extend raw detections and production-stage evidence to retain quadrilateral/text
data while sending each accepted OCR, YOLO, and Grounding DINO box independently to SAM 2. OCR
candidates are directly accepted; visual candidates keep their current behavior. Preserve existing
v1 `detections.json` semantics.

**Likely files:**

- `src/watermark_worker/gpu_runtime/detection.py`
- `src/watermark_worker/production_stages.py`
- `src/watermark_worker/pipeline.py`
- detection/production-stage/pipeline/CLI artifact tests

**Acceptance criteria:**

- [x] OCR-only text at 0.35 creates one SAM 2 prompt and can reach LaMa without YOLO/DINO agreement.
- [x] Overlapping OCR/visual candidates retain independent prompts and masks with source provenance.
- [x] Non-overlapping candidates retain independent masks; non-text YOLO/DINO candidates continue to work when OCR finds nothing.
- [x] `detections.json`, per-detection masks, combined mask, final image, result marker ordering, and result validation remain deterministic.

**Verification:** Synthetic tests cover independent overlap, non-overlap, original-image routing,
and stable ordering. Pipeline tests cover OCR-only inpainting, visual-only inpainting, no detection,
and safety-gate block behavior.

**Dependencies:** Task 4.

### Checkpoint: worker functional readiness

- [ ] All local unit, integration, formatting, type, and container tests pass.
- [ ] A controlled local image demonstrates an OCR-only candidate at 0.35 reaching an audited final result.
- [ ] The evaluation corpus confirms the chosen OCR refinement and padding values meet the agreed quality threshold.

### Task 6: Expose v2 through the AWS submission boundary and GCP control plane

**Description:** Add v2 versions to the strict submit contract, Lambda pipeline map, Terraform allowlist/job-definition mapping, and GCP provider/control-plane selection. Keep v1 mappings live for already-created jobs. Configure v2 to use the new packaged configuration paths and immutable worker image digest.

**Likely files:**

- `services/aws_submission/src/aws_submission/models.py`
- `services/aws_submission/src/aws_submission/batch_adapter.py`
- `services/aws_submission/tests/`
- `infra/aws/locals.tf`, `variables.tf`, `batch-job.tf`, and staging Terraform values/tests
- GCP backend watermark pipeline selection, provider contract fixtures, and control-plane tests in `/home/maaz/Desktop/infringement_project/infringement_backend`

**Acceptance criteria:**

- [x] New submissions can select the appropriate small or bulk v2 pipeline only through trusted server-side code; callers cannot provide arbitrary config paths, artifact IDs, or AWS job definitions.
- [x] v1 submissions and reconciliation retain their current behavior.
- [x] The AWS Lambda maps each allowed version to an explicit Batch job definition and packaged configuration; the configuration remains selected server-side.

**Verification:** Contract, Lambda adapter, Terraform validation, backend provider, and control-plane tests cover v1 compatibility and both v2 variants.

**Dependencies:** Tasks 3 through 5.

### Task 7: Build, deploy, and perform one controlled v2 GPU acceptance job

**Description:** Follow the current worker-image and operational-mode runbooks to publish one reviewed image digest, deploy explicit v2 Batch job-definition revisions through Terraform, and run exactly one controlled end-to-end v2 job from FastAPI. Inspect only narrow, job-scoped output and telemetry, then prove the GPU fleet returns to zero.

**Acceptance criteria:**

- [ ] The CodeBuild image build succeeds with the new lock, offline OCR model bundle, and container smoke tests.
- [ ] Exactly one acceptance job reaches completed state; its output has OCR evidence, an OCR-derived SAM 2 mask, and a visually reviewed inpainted image.
- [ ] Output validation accepts the expected result; a no-text control image does not create an OCR removal mask.
- [ ] After the job is terminal, there is no active GPU EC2 instance and no RUNNABLE, STARTING, or RUNNING Batch job.

**Verification:** Execute the release and acceptance commands in `aws-worker-image-deployment-and-gpu-acceptance-runbook.md`, the controlled end-to-end operator runbook, and the zero-GPU verification steps from the authoritative agent guide. Record only sanitized job-scoped evidence.

**Dependencies:** Task 6 and explicit approval for the production deployment window.

## 7. Rollout and rollback

1. Build and test the v2 image while the backend OCR toggle defaults to enabled for new jobs.
2. Deploy the v2 provider/Batch mapping before enabling GPU execution; the toggle remains
   server-controlled and is not client-selectable.
3. Run the one controlled v2 acceptance job and review text-removal quality at the fixed 0.35 threshold.
4. Keep v2 as the default for new jobs after acceptance. Never mutate jobs already frozen to v1.
5. Roll back by setting the backend OCR toggle to false, directing new work back to v1. Retain
   the accepted v1 image digest and job-definition revisions. Do not delete v2 artifacts or
   historical outputs during rollback.

## 8. Risks and mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Low 0.35 threshold creates false OCR positives | High | Product rule permits direct removal, but evaluate a no-text control set, retain mask/text evidence, preserve safety gate, and require visual acceptance before rollout. |
| OCR/recognition misses faint or stylized text | Medium | Measure recall on translucent/rotated examples; retain YOLO/DINO paths; assess a separate OCR pre-processing experiment only after baseline results. |
| SAM 2 box prompt selects too much surrounding content | High | Preserve quadrilaterals, use measured small padding, inspect mask IoU/visual output, and do not fall back silently to full rectangles. |
| EasyOCR dependency changes Torch/CUDA resolution | High | Regenerate the hash-locked dependency set remotely, run ABI/container smoke checks, and reject resolution that replaces the approved Torch stack. |
| Runtime model download or a mutable model file | High | Pin a private archive in GCS, set `download_enabled=False`, test offline startup, and use existing generation/checksum validation. |
| v1 behavior changes accidentally | High | New v2 pipeline names, explicit AWS maps, fixture regression tests, and a staged rollout with v1 retained. |
| Customer-recognized text leaks to logs | High | Store it only in protected job artifacts; use aggregate counts/metrics and redact error context. |

## 9. Explicit non-goals

- Running OCR on both original and white-background images.
- Treating visual-model agreement as a prerequisite for OCR removal.
- Replacing the current safety gate or LaMa with a separate inpainting system.
- Deploying PaddleOCR, a second CUDA framework, or an external OCR API.
- Changing the public browser-facing API to expose AWS credentials or direct GCS access.
- Reprocessing historical v1 jobs automatically.

## 10. Approval points before production rollout

- Confirm the evaluation corpus, success metrics, and visual quality threshold.
- Confirm English-only OCR for v2 or name the required language set before the model bundle is created.
- Confirm the backend OCR toggle is enabled for both small and bulk jobs before the production
  acceptance window; disable it only for an explicit compatibility/rollback window.
- Approve the worker-image release and controlled production acceptance window separately from implementation.
