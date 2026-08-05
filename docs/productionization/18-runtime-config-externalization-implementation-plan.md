# Runtime configuration externalization implementation plan

Status: Implementation in progress (worker/control-plane code complete; publication and deployment remain gated).

## 1. Goal

Move tunable watermark-policy values out of the GPU image so OCR, detector, safety-gate, LaMa,
and bounded image-limit tuning does not require rebuilding the image.

The current detection policy remains unchanged:

- EasyOCR runs on the original image;
- OCR confidence `>= 0.35` is accepted directly;
- OCR, YOLO, and Grounding DINO boxes remain independent;
- there is no bbox fusion, IoU merge, or detector-agreement requirement;
- masks are unioned only by the existing safety/inpainting path.

After the one-time implementation release, a policy change will require publishing/promoting a
new config object, not building a new worker image. Model, dependency, and worker-code changes
will still require an image release.

## 2. Simplified architecture

Use the existing private watermark GCS area with an allowlisted `watermark-configs/` prefix.
Each job receives one immutable config reference:

```text
config_uri        = gs://.../watermark-configs/watermark-v2/<release>.yaml
config_generation = <GCS generation>
config_sha256     = <64-character lowercase SHA-256>
```

The backend chooses the reference server-side by pipeline version. Clients cannot submit a
profile, URI, threshold, or pipeline version.

The external object is the existing strict pipeline YAML, not a second configuration framework.
The worker reuses its current Pydantic validation and config hashing. The pinned model manifest,
model files, dependencies, worker code, and SAM 2 architecture file remain release assets.

## 3. Tunable versus immutable values

| Externalized policy | Kept immutable or release-controlled |
| --- | --- |
| EasyOCR confidence, minimum region size, padding | Worker code and dependencies |
| YOLO confidence, IoU, image size, augmentation | Model bundles and model-manifest checksums |
| Grounding DINO prompt and thresholds | Allowed artifact IDs and SAM 2 architecture |
| Safety-gate thresholds | Pipeline/output contract and IAM guardrails |
| LaMa mask dilation and threshold | Code-enforced schema bounds |
| Safe image/item limits | Approved config bucket/prefix |

External profiles must still pass all current validation. They cannot disable the safety gate,
introduce a fusion policy, load arbitrary code, or select an artifact absent from the pinned model
manifest.

## 4. Target flow

```text
Backend OCR toggle and pipeline-version mapping
        |
        v
Persist URI + generation + SHA on WatermarkJob
        |
        v
AWS submit request carries the same immutable reference
        |
        v
AWS Batch passes the reference to the worker
        |
        v
Worker downloads through read-only WIF, verifies, and loads the YAML
        |
        v
Existing pipeline runs and records the config SHA in output evidence
```

The worker must fail closed on download failure, wrong generation, checksum mismatch, oversized
or malformed YAML, unsafe URI, invalid bounds, or pipeline-version mismatch. It must never fall
back silently to a different profile.

## 5. Ordered implementation tasks

### Phase 1: External config and worker support

#### Task 1: Publish immutable config snapshots

Publish byte-identical snapshots of the four current files:

```text
watermark-v1.yaml
watermark-bulk-v1.yaml
watermark-v2.yaml
watermark-bulk-v2.yaml
```

Store them under the private `watermark-configs/` prefix. Record each object's generation and
SHA-256. Use a deployment/operator identity for writes and read-only worker access.

**Acceptance criteria:**

- [x] A local operator command validates all four snapshots and computes SHA-256 values.
- [ ] Objects are private, bounded in size, and immutable by publication convention.
- [ ] Generation and SHA values are available to the backend deployment configuration after the
      operator runs the command against the approved bucket.

**Likely files/tools:** GCS configuration, `tools/` or `scripts/`, release documentation.

**Estimated scope:** Small. The command is `scripts/publish_pipeline_configs.py`.

#### Task 2: Add external config loading to the worker

Extend the CLI/config loader to accept a trusted external URI, generation, and expected SHA in
addition to the existing local `--config` path. Download through the existing storage/WIF
boundary, then pass the bytes through the same YAML parser, schema validation, and SHA calculation.

Keep the local path for development and the migration period. Do not download models or
dependencies as part of config loading.

**Acceptance criteria:**

- [x] Local `--config` behavior remains unchanged.
- [x] External loading requires URI, positive generation, and SHA-256.
- [x] The URI is restricted to the approved config bucket/prefix.
- [x] Generation, SHA, size, YAML, schema, pipeline version, artifact binding, and safe-path
      checks all fail closed.
- [x] `RunIdentity`, item markers, and output manifests record the downloaded bytes' SHA.

**Likely files:**

- `src/watermark_worker/cli.py`
- `src/watermark_worker/config.py`
- `src/watermark_worker/storage.py`
- `tests/unit/test_config.py`
- `tests/unit/test_pipeline.py`

**Dependencies:** Task 1.

**Estimated scope:** Medium.

### Checkpoint 1: Worker readiness

- [ ] Local and external config tests pass.
- [ ] Tampered, stale, malformed, and unauthorized profiles are rejected.
- [ ] External v2 validation runs without runtime installs or model downloads.

### Phase 2: Propagate config identity through the control plane

#### Task 3: Extend AWS submission and Batch parameters

Add `configUri`, `configGeneration`, and `configSha256` to the new trusted AWS submission
contract. Use a new request schema version for new submissions while retaining the current parser
for existing v1 jobs. Include all three values in canonical request hashing and idempotency.

AWS Batch should pass the reference as job parameters/CLI arguments. The submission boundary must
validate the approved GCS prefix and strict generation/SHA formats.

**Acceptance criteria:**

- [x] New small and bulk v2 submissions carry the exact config reference.
- [x] The reference cannot be omitted or changed after reservation.
- [x] Batch receives the same URI, generation, and SHA sent by the backend.
- [x] Existing v1 submissions remain compatible.

**Likely files:**

- `services/aws_submission/src/aws_submission/models.py`
- `services/aws_submission/src/aws_submission/batch_adapter.py`
- `services/aws_submission/tests/`
- `infra/aws/batch-job.tf`
- `infra/aws/locals.tf`
- `infra/aws/variables.tf`

**Dependencies:** Task 2.

**Estimated scope:** Medium.

#### Task 4: Persist and select config references in FastAPI

Add nullable config URI, generation, and SHA columns to `watermark_jobs`. New jobs populate them
atomically from a server-owned mapping keyed by pipeline version. The existing
`WATERMARK_OCR_ENABLED=true` default selects the v2/bulk-v2 references; `false` selects the
v1/bulk-v1 compatibility references.

Retries, reconciliation, cancellation, manifest creation, and output validation must reuse the
persisted reference. No client request may provide configuration identity or tuning values.

**Acceptance criteria:**

- [x] Migration and model fields are added with safe compatibility defaults.
- [x] Small and bulk routes persist the selected reference before submission.
- [x] Repeated submission/reconciliation uses the same reference.
- [ ] Existing active jobs are backfilled to byte-identical snapshots before packaged configs are
      removed; completed history is not rewritten.
- [x] OCR remains enabled by default for new jobs.

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

**Dependencies:** Task 3.

**Estimated scope:** Medium to large.

### Checkpoint 2: Control-plane integrity

- [ ] Backend and AWS tests prove URI/generation/SHA survives unchanged.
- [ ] Client payloads cannot select arbitrary profiles or thresholds.
- [ ] Retries cannot switch to a newly published profile.
- [ ] Existing v1 jobs remain valid.

### Phase 3: Image transition and rollout

#### Task 5: Release the external-config worker image

Build one worker image that uses external profiles for AWS jobs. Stop copying the four pipeline
YAML files into the production image after the compatibility period. Keep only immutable runtime
assets required by model libraries, such as the pinned SAM 2 architecture file. Retain local
config loading for development and controlled compatibility tests.

**Acceptance criteria:**

- [ ] Production jobs no longer depend on `/app/configs/watermark-*.yaml`.
- [ ] Container smoke tests cover external v2 and bulk-v2 profiles.
- [ ] Model binding and offline-startup checks still pass.
- [ ] Existing active jobs have external references before the old image path is retired.

**Likely files:** `Dockerfile.gpu`, `cloudbuild.gpu.yaml`, runtime tests, AWS Batch definitions.

**Dependencies:** Tasks 1-4.

**Estimated scope:** Medium.

#### Task 6: Add simple publication, promotion, and rollback workflow

Provide an operator command or documented procedure that validates a YAML, uploads a new
immutable object, records generation/SHA, and updates the backend's server-side mapping. Promotion
and rollback change only the selected reference; they do not rebuild the worker image.

Run one small and one bulk staging acceptance job covering OCR-only removal, visual-only removal,
independent overlapping boxes, safety blocking, output validation, and zero-GPU cleanup.

**Acceptance criteria:**

- [ ] A new threshold profile can be promoted without an image build.
- [ ] A previous reference can be restored without an image build.
- [ ] Job output contains the expected config SHA and generation.
- [ ] Only jobs created after promotion use the new profile.
- [ ] Staging acceptance and zero-GPU checks pass.

**Dependencies:** Task 5 and explicit deployment approval.

**Estimated scope:** Medium.

## 6. Migration order

1. Publish byte-identical snapshots of all four current pipeline YAMLs.
2. Release a worker supporting both local and external config sources.
3. Add AWS and backend reference fields; write references for all new jobs.
4. Backfill active jobs that may retry, preserving the matching v1/v2 snapshot.
5. Run staging acceptance using external v2 profiles.
6. Release the image that no longer packages tunable pipeline YAMLs.
7. Use profile publication/promotion for future tuning.

Never mutate a persisted job reference because a newer default was published. Do not rewrite
completed job history.

## 7. Verification matrix

| Layer | Required checks |
| --- | --- |
| Worker | Schema bounds, URI allowlist, generation/SHA mismatch, version mismatch, local fallback |
| Pipeline | OCR threshold `0.35`, independent detections, safety gate, LaMa output |
| Container | External startup, no runtime installs/downloads, immutable model binding |
| AWS | Strict reference fields, canonical hash, Batch propagation, v1 compatibility |
| Backend | Default OCR selection, rollback, persistence, retry/reconciliation, client rejection |
| Migration | Snapshot equivalence, active-job backfill, idempotency |
| Operations | IAM, promotion, rollback, narrow logs, zero-GPU verification |

## 8. Security and failure behavior

- Config download, generation, checksum, schema, bounds, or URI failures fail closed.
- Worker access to the config prefix is read-only; only deployment tooling writes profiles.
- Clients never submit config URIs, profile files, thresholds, or pipeline versions.
- Profiles cannot load arbitrary code or unpinned model artifacts.
- Logs expose profile identity and counts, not secrets or unnecessary OCR text.

## 9. Implementation status

Completed in code:

- Worker CLI supports local and generation/SHA-pinned external GCS YAML, with allowlist, size,
  checksum, and schema checks.
- AWS schema v2, canonical hashing, Lambda prefix policy, v2 Batch parameters, and v1 fallback
  are wired.
- FastAPI stores the three config identity fields, selects them server-side from
  `WATERMARK_CONFIG_REFS_JSON`, and propagates them through Cloud Run orchestration.
- Alembic revision `0053` adds the nullable compatibility columns.
- `scripts/publish_pipeline_configs.py` validates and create-only uploads immutable snapshots.

Not executed in this implementation pass:

- GCS publication, backend environment promotion, image build, staging acceptance, or AWS
  deployment. Those require explicit operator/deployment approval and live credentials.

## 10. Scope estimate

Roughly **2-4 engineering days** using the existing GCS WIF and AWS plumbing:

- worker loader and tests: about 1 day;
- AWS/backend propagation and migration: 1-2 days;
- image transition, publication procedure, and staging acceptance: about 1 day.

This requires one image rebuild to add external-config support. After that, normal policy tuning
is a config upload plus server-side promotion only.
