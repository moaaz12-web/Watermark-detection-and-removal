# Runtime configuration externalization implementation plan

Status: Proposed for implementation. This plan changes how the worker receives tunable
watermark-removal policy; it does not change the independent OCR/YOLO/DINO detection policy.

## 1. Objective

Move tunable watermark-pipeline parameters out of the GPU image so threshold and mask-policy
changes can be made by publishing a new, versioned configuration profile rather than rebuilding
the worker image.

After the one-time implementation release:

- changing OCR, detector, safety-gate, inpainting, or bounded image-limit values must not require
  a new Docker image;
- every job must use an immutable configuration reference consisting of URI, GCS generation, and
  SHA-256;
- the worker must fail closed if the configuration is missing, changed, malformed, untrusted, or
  incompatible with the selected pipeline;
- existing v1 and v2 jobs must remain reproducible.

## 2. Current state and constraints

The worker currently loads a local YAML path supplied by `--config`. `Dockerfile.gpu` copies the
pipeline YAML files into the image, and AWS Batch maps each pipeline version to one of those local
paths. The worker hashes the loaded YAML and records `config_sha256` in its run identity and
output markers, which gives us a useful reproducibility primitive to retain.

The active OCR design is intentionally simple:

- EasyOCR runs on the original image;
- OCR candidates at confidence `>= 0.35` are accepted directly;
- OCR, YOLO, and Grounding DINO boxes remain independent;
- there is no bbox fusion, IoU merge, or detector-agreement requirement;
- masks are unioned only at the existing safety/inpainting stage.

The public API is in the separate repository:
`/home/maaz/Desktop/infringement_project/infringement_backend`.

## 3. Architecture decision

Use an allowlisted, versioned GCS configuration registry. The backend chooses a profile
server-side, the AWS submission boundary carries the exact URI/generation/checksum, and the worker
downloads and validates the profile before execution.

### 3.1 Externalize runtime policy

The external profile may contain:

- EasyOCR confidence, minimum region size, padding, and language selection supported by the
  pinned OCR bundle;
- YOLO confidence, IoU, image size, and augmentation;
- Grounding DINO prompt, box threshold, and text threshold;
- safety-gate thresholds and minimum product-area requirement;
- LaMa mask dilation and mask threshold;
- bounded image and item limits that are safe to tune operationally.

The profile remains subject to strict Pydantic/YAML validation and hard upper/lower bounds in
worker code. A profile cannot disable the safety gate, introduce a new fusion policy, or bypass
the independent-candidate behavior.

### 3.2 Keep release integrity immutable

Do not make configuration the mechanism for arbitrary code or model loading. Keep these under the
immutable worker release and model-manifest contract:

- Python dependencies, worker source, and CUDA/PyTorch ABI;
- model bundles, checksums, and the model manifest;
- allowed model artifact IDs and SAM 2 architecture configuration files;
- storage/IAM guardrails and approved config prefixes;
- pipeline-version contracts and output schema.

An external profile may reference only model artifacts present in the pinned model manifest and
only safe, allowlisted model configuration paths. Adding or replacing a model remains a separate
image/model release.

### 3.3 Pin the exact profile per job

Never use a mutable `latest` object. A job stores and submits:

```text
profile_id       = watermark-v2-default
config_uri       = gs://<config-bucket>/watermark/v2/<immutable-object>.yaml
config_generation = <positive GCS generation>
config_sha256     = <64 lowercase hex characters>
```

Retries, reconciliation, and output validation reuse the same reference. Publishing a newer
profile affects only jobs created after the backend selects it.

## 4. Target execution flow

```text
Backend OCR toggle/profile selection
        |
        v
Persist pipeline version + config URI/generation/SHA on WatermarkJob
        |
        v
Trusted AWS submit request carries the same immutable config reference
        |
        v
AWS Batch passes config reference to the worker
        |
        v
Worker downloads from an allowlisted GCS prefix using read-only WIF
        |
        v
Worker verifies generation, SHA-256, size, schema, bounds, and pipeline version
        |
        v
Existing pipeline executes; config SHA is recorded in run/output evidence
```

## 5. Configuration registry

Create a dedicated, private GCS bucket or a dedicated prefix with separate IAM policy. Prefer
immutable object names and a deployment-owned writer. Suggested layout:

```text
gs://<watermark-config-bucket>/watermark-configs/
  watermark-v1/<release-id>.yaml
  watermark-bulk-v1/<release-id>.yaml
  watermark-v2/<release-id>.yaml
  watermark-bulk-v2/<release-id>.yaml
```

The initial registry must contain byte-identical snapshots of the current v1 and v2 YAML files.
This gives existing and historical jobs a reproducible external reference before packaged YAML
files are removed from a future image.

Registry requirements:

- private bucket/prefix with worker read access only;
- deployment/operator write access; no public access;
- YAML content type and bounded object size;
- checksum calculated before publication and generation captured after publication;
- no secrets, credentials, arbitrary URLs, or unresolved placeholders;
- a release manifest recording profile ID, pipeline version, generation, SHA, and review status.

## 6. Ordered implementation tasks

### Phase 1: Define and publish the profile contract

#### Task 1: Split tunable policy from immutable release rules

**Description:** Document and implement the boundary between externally tunable policy and
immutable model/release identity. Reuse existing validation but ensure external input cannot alter
pipeline version, artifact trust, storage guardrails, or the independent-detector policy.

**Acceptance criteria:**

- [ ] A typed profile schema covers every intended tunable value.
- [ ] Bounds reject invalid OCR confidence, negative padding, unsafe thresholds, oversized limits,
      and unresolved placeholders.
- [ ] The schema has no fusion/merge parameter and cannot disable the safety gate.
- [ ] Artifact IDs and SAM 2 configuration paths are checked against the pinned release allowlist.

**Likely files:**

- `src/watermark_worker/config.py`
- `src/watermark_worker/model_registry.py`
- `tests/unit/test_config.py`

**Dependencies:** None.

**Estimated scope:** Medium.

#### Task 2: Create and verify initial external profile snapshots

**Description:** Publish byte-identical v1/v2 small and bulk profiles to the private GCS registry.
Record generation and SHA-256 values in a release manifest. Do not change production job
selection yet.

**Acceptance criteria:**

- [ ] Four initial profile snapshots exist for v1, bulk-v1, v2, and bulk-v2.
- [ ] Their parsed values match the current packaged YAML files.
- [ ] Each object has a recorded generation and SHA-256.
- [ ] A no-public-access and worker-read-only IAM check passes.

**Likely files/tools:**

- `model-manifest.json` or a new config release manifest
- `tools/` or an operator publishing script
- GCS configuration bucket/prefix

**Dependencies:** Task 1.

**Estimated scope:** Small to medium.

### Checkpoint 1: Profile contract

- [ ] Worker unit tests pass for valid, malformed, out-of-bounds, and tampered profiles.
- [ ] The four profile snapshots are byte-verified against the current YAML.
- [ ] Security review confirms no client can choose an arbitrary profile URI.

### Phase 2: Add worker external loading

#### Task 3: Add generation- and checksum-pinned config loading

**Description:** Extend the worker CLI/config loader to support a trusted external config
reference in addition to the local development path. Download the object through the existing
GCS/WIF storage boundary into a controlled temporary file or bytes buffer, then pass it through the
same strict parser and SHA-256 calculation.

**Acceptance criteria:**

- [ ] Local `--config` continues to work for development and offline tests.
- [ ] External loading requires URI, positive generation, and expected SHA-256.
- [ ] The worker rejects wrong generation, wrong checksum, oversized content, unsafe bucket/prefix,
      invalid YAML, invalid schema, and pipeline-version mismatch.
- [ ] The worker does not download model weights or dependencies as a side effect of config load.
- [ ] The config SHA in `RunIdentity`, item markers, and output manifests is the downloaded bytes'
      SHA-256.

**Likely files:**

- `src/watermark_worker/cli.py`
- `src/watermark_worker/config.py`
- `src/watermark_worker/storage.py`
- `src/watermark_worker/contracts.py`
- `tests/unit/test_config.py`
- `tests/unit/test_pipeline.py`

**Dependencies:** Tasks 1-2.

**Estimated scope:** Large.

#### Task 4: Remove policy YAMLs from the release image after compatibility support exists

**Description:** Add a new worker image mode that uses external profiles for AWS jobs. Keep only
immutable model-architecture files that runtime libraries require, or package those with the
corresponding model artifact. Retain a local-config path for development and a temporary
compatibility image during migration.

**Acceptance criteria:**

- [ ] The production image no longer relies on `/app/configs/watermark-*.yaml`.
- [ ] SAM 2 still loads its pinned architecture configuration safely.
- [ ] Container smoke tests execute a profile-backed validation path without network model
      downloads.
- [ ] The image contains no secret or mutable config material.

**Likely files:**

- `Dockerfile.gpu`
- `cloudbuild.gpu.yaml`
- `src/watermark_worker/runtime_assembly.py`
- `tests/unit/test_gpu_cloudbuild_contract.py`

**Dependencies:** Task 3.

**Estimated scope:** Medium.

### Checkpoint 2: Worker readiness

- [ ] Local path and external profile tests pass.
- [ ] Tamper and replay tests prove the exact generation/SHA is enforced.
- [ ] Container smoke test passes with the external v2 profile.
- [ ] No model/dependency downloads occur during profile loading.

### Phase 3: Propagate the immutable profile reference through AWS

#### Task 5: Extend the AWS submission contract

**Description:** Add profile identity to the trusted submit boundary. Version the request contract
if necessary; keep the current contract available for already-created jobs during migration.
Include profile URI, generation, and SHA in canonical request hashing and idempotency fields.

**Acceptance criteria:**

- [ ] Requests cannot omit or mutate the profile reference after reservation.
- [ ] URI is restricted to the approved GCS bucket/prefix; generation and SHA have strict formats.
- [ ] The canonical request hash includes all profile identity fields.
- [ ] AWS Batch receives the exact profile reference for both small and bulk v2 jobs.
- [ ] v1 compatibility remains available for existing jobs.

**Likely files:**

- `services/aws_submission/src/aws_submission/models.py`
- `services/aws_submission/src/aws_submission/batch_adapter.py`
- `services/aws_submission/tests/`
- `infra/aws/batch-job.tf`
- `infra/aws/variables.tf`
- `infra/aws/locals.tf`

**Dependencies:** Task 3.

**Estimated scope:** Large.

#### Task 6: Add backend persistence and server-side profile selection

**Description:** Persist the selected profile identity on `WatermarkJob`. New small and bulk jobs
use the backend OCR toggle and a server-owned profile mapping. The client cannot submit a config
URI, profile ID, threshold, or pipeline version.

**Acceptance criteria:**

- [ ] A migration adds nullable profile URI, generation, SHA, and optional profile ID fields for
      compatibility, with new jobs populating them atomically.
- [ ] Existing active jobs are backfilled to byte-identical snapshots or safely retain a local
      compatibility path before packaged configs are removed.
- [ ] Retries, reconciliation, cancellation, and output validation use the persisted reference.
- [ ] `WATERMARK_OCR_ENABLED=true` selects the OCR-enabled v2 profile by default for both job
      classes.
- [ ] Setting it to `false` selects the v1 compatibility profile for new work.

**Likely files:**

- `/home/maaz/Desktop/infringement_project/infringement_backend/alembic/versions/`
- `app/models/watermark_job.py`
- `app/core/config.py`
- `app/api/routes/watermark_jobs.py`
- `app/api/routes/watermark_bulk_jobs.py`
- `app/watermark/provider.py`
- `app/watermark/orchestrator.py`
- `app/watermark/reconciler.py`
- `tests/test_watermark_jobs_api.py`
- `tests/test_watermark_bulk_jobs_api.py`
- `tests/test_watermark_reconciliation.py`

**Dependencies:** Task 5.

**Estimated scope:** Large.

### Checkpoint 3: End-to-end control-plane integrity

- [ ] Backend and AWS contract tests prove the same profile URI/generation/SHA survives submission.
- [ ] Idempotent retries do not switch profiles after a new profile is published.
- [ ] Client payloads cannot choose arbitrary profiles or thresholds.
- [ ] Existing v1 jobs remain valid and output-compatible.

### Phase 4: Staging rollout and operational tooling

#### Task 7: Add profile publication and selection tooling

**Description:** Provide a deployment/operator command that validates a YAML profile, calculates its
SHA, uploads it, records its generation, and emits the backend/AWS configuration values needed to
select it. The tool must never mutate an existing profile object.

**Acceptance criteria:**

- [ ] Publishing requires an explicit pipeline version and profile ID.
- [ ] The tool validates locally before upload and prints only non-sensitive metadata.
- [ ] Existing profile objects are immutable; a new release creates a new object.
- [ ] A profile can be promoted, rolled back, and audited without rebuilding the image.

**Likely files/tools:**

- `tools/` or `scripts/`
- `docs/productionization/`
- deployment configuration for the private GCS registry

**Dependencies:** Tasks 1-2 and 6.

**Estimated scope:** Medium.

#### Task 8: Run one controlled staging acceptance job

**Description:** Use the new external v2 profile for one small and one bulk staging job. Verify
OCR-only removal, visual-detector removal, independent overlapping boxes, safety blocking, output
validation, and zero-GPU cleanup.

**Acceptance criteria:**

- [ ] Output records the expected config SHA and generation.
- [ ] A profile change affects only jobs created after promotion.
- [ ] Rollback to the previous profile works without rebuilding the image.
- [ ] No unauthorized config or model object is fetched.
- [ ] GPU fleet returns to zero after terminal jobs.

**Dependencies:** Tasks 4-7 and explicit deployment approval.

**Estimated scope:** Medium.

## 7. Migration and compatibility strategy

1. Publish byte-identical snapshots of the four existing pipeline YAML files.
2. Release a worker that supports both local and external config sources.
3. Add backend/AWS profile identity fields and begin writing them for new jobs.
4. Backfill active jobs that may be retried with the matching immutable snapshot reference.
5. Run staging acceptance using external v2 profiles.
6. Release the image that no longer depends on packaged pipeline YAMLs.
7. Keep the local path only for development and controlled compatibility tests.

Do not rewrite completed job history. Do not change a persisted profile reference because a newer
default has been published.

## 8. Verification matrix

| Layer | Required checks |
| --- | --- |
| Worker unit | Schema bounds, SHA mismatch, generation mismatch, URI allowlist, version mismatch, local fallback |
| Worker integration | External v2 OCR profile, OCR confidence `0.35`, independent overlapping detections, safety gate, LaMa output |
| Container | No runtime installs/downloads, external config startup, immutable model binding |
| AWS contract | Strict profile fields, canonical hash, Batch parameter propagation, v1 compatibility |
| Backend | Default OCR profile, rollback profile, persistence, retries, reconciliation, client rejection |
| Migration | Existing active v1 jobs, terminal historical jobs, backfill idempotency |
| Operations | IAM, profile publication, promotion, rollback, narrow logs, zero-GPU verification |

## 9. Security and failure behavior

- Config download failure, checksum mismatch, generation mismatch, malformed YAML, or policy
  violation fails the job closed; it must not silently use a default profile.
- The worker uses read-only identity access to the configuration prefix.
- The backend and AWS boundary reject arbitrary client-provided config references.
- Profile content is never treated as executable code and cannot select unpinned model artifacts.
- Logs expose profile ID/generation/SHA and counts, not secrets or unnecessary OCR text.
- A profile is immutable once published; rollback selects an earlier reference.

## 10. Estimated scope

Rough estimate: **2-4 engineering days** after the design is approved, assuming the existing GCS
WIF and AWS submission plumbing are reused:

- worker loader and validation: 1 day;
- backend/AWS persistence and contract propagation: 1-2 days;
- publication tooling, migration, tests, and staging acceptance: 1 day.

This requires one rebuilt worker image to add external-config support. After that release,
threshold and policy tuning should be config publication/promotion only.

## 11. Open decisions before coding

- [ ] Use a dedicated GCS config bucket or a dedicated prefix in the existing watermark bucket;
      dedicated bucket is preferred for least-privilege IAM.
- [ ] Choose whether the AWS contract uses `schemaVersion=2` or additive profile fields with a
      compatibility parser; preserve schema v1 for existing jobs either way.
- [ ] Choose the profile promotion mechanism: backend environment setting, versioned database
      registry row, or deployment-managed mapping. The profile URI/generation/SHA must remain
      server-controlled.
- [ ] Approve the one-time worker image release and staging acceptance window.
