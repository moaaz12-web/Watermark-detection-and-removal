# Per-job artifact profile implementation plan

Status: Proposed implementation plan. No code changes are made by this document.

## 1. Goal and scope

Add an explicit job-submission choice that FastAPI receives from the frontend and freezes into the
immutable worker input manifest:

```json
{"artifactProfile":"full"}
```

or:

```json
{"artifactProfile":"essential"}
```

`full` is the compatibility default and retains the current artifact set. `essential` keeps only
outputs needed to accept, use, and audit a completed watermark result:

- `source_metadata.json`;
- `detections/detections.json` without links to omitted per-detection masks;
- `masks/combined_mask.png` when the safety gate approves a mask;
- `inpainted/final.png` for a successful inpaint;
- `result.json`; and
- `_COMPLETE.json`, written last.

`essential` omits review-only diagnostics:

- `background_removed/white_background.png`;
- `background_removed/foreground_mask.png`;
- per-detection mask PNGs; and
- `detections/annotated.png`.

The scope is the existing FastAPI watermark job API and the GPU worker. The frontend needs only a
single optional control on the bulk-job form initially; it must not call AWS or GCS directly.
Artifact-upload concurrency, CUDA changes, and further worker pipelining are explicitly out of
scope.

## 2. Decisions and invariants

1. **Per-job, immutable selection.** `artifactProfile` is persisted with the application job before
   preparation begins, is included in the canonical input-manifest bytes, and cannot change after
   job creation. This makes the selected output set visible in the pinned manifest generation and
   checksum used by replay and reconciliation.
2. **Safe default.** Omitted client input means `full`. Existing clients, previous manifests, and
   historical output remain compatible.
3. **Strict enum only.** FastAPI and the worker accept only `full` or `essential`; unknown, null,
   or malformed values fail validation. The worker does not use a mutable environment variable for
   artifact policy.
4. **Safety data remains in memory.** `essential` suppresses only serialization/upload of optional
   artifacts. The background foreground mask and detection masks remain available to the safety
   gate and combined-mask calculation, so decisions and final image semantics do not change.
5. **Marker-last remains unchanged.** All retained artifacts must be durable before `result.json`,
   which remains durable before `_COMPLETE.json`. A failed write never creates a job marker.
6. **No post-hoc profile switching.** Retrying an existing job uses its persisted profile. A user
   who wants different diagnostics creates a new job and output prefix.

## 3. Data flow

```text
Bulk-job UI switch
  -> FastAPI request artifactProfile
  -> WatermarkJob.artifact_profile (durable row)
  -> immutable input-manifest artifactProfile
  -> GPU worker InputManifest validation
  -> stage-level artifact creation policy
  -> marker-last GCS outputs
  -> FastAPI output acceptance and UI job response
```

The current FastAPI path is `watermark_bulk_jobs.py` -> `WatermarkJob` ->
`app/watermark/manifest.py` -> bulk preparer. The AWS request already pins the input-manifest URI
and generation, so it does not need a separate provider API field.

## 4. Implementation tasks

### Task 1: Define the shared contract and database state

**Description:** Add a two-value `ArtifactProfile` type in both repositories and add a non-null
`artifact_profile` column to FastAPI's `watermark_jobs`, defaulting to `full`. Expose the selected
profile in job responses so the UI can accurately display what was requested.

**Why:** The FastAPI row is the durable source of truth before manifest creation. Storing the value
only in an HTTP request or a worker environment variable would make retries ambiguous.

**Likely files:**

- `infringement_backend/app/models/watermark_job.py`
- new Alembic revision following the latest backend revision
- `infringement_backend/app/schemas/watermark_jobs.py`
- `infringement_backend/app/api/routes/watermark_jobs.py`
- `infringement_backend/app/api/routes/watermark_bulk_jobs.py`
- `src/watermark_worker/contracts.py`
- backend schema/model/API tests and worker contract tests

**Acceptance criteria:**

- `full` and `essential` are the only accepted values.
- Existing rows migrate to `full`; the new column is non-null after migration.
- Both small and bulk create endpoints default omitted input to `full` and return the persisted
  selection.
- The idempotency request hash includes the profile, so reusing a key with a different profile
  returns the existing idempotency conflict rather than silently accepting a different request.

**Verification:**

- Migration upgrade test and model tests pass.
- API tests cover omitted/default, each accepted value, invalid value, response shape, and
  idempotency mismatch.

**Dependencies:** None.

### Task 2: Freeze the profile in the manifest and validate it end to end

**Description:** Serialize `artifactProfile` from the persisted FastAPI row in
`app/watermark/manifest.py`. Add a required `artifact_profile` field to the worker `InputManifest`
and preserve it in worker output manifest and completion markers for auditability. Update FastAPI
output validation to require that the returned profile matches the persisted job selection.

**Why:** The manifest is the pinned cross-cloud contract. Explicitly echoing the value in output
makes a profile mismatch fail closed rather than looking like a normal job result.

**Likely files:**

- `infringement_backend/app/watermark/manifest.py`
- `infringement_backend/app/watermark/output_validation.py`
- `src/watermark_worker/contracts.py`
- `src/watermark_worker/result_writer.py`
- `src/watermark_worker/cli.py`
- smoke-manifest fixtures, output fixtures, backend manifest/reconciliation tests, and worker
  contract/replay tests

**Acceptance criteria:**

- Published bulk manifest contains exactly one canonical `artifactProfile` value.
- Worker rejects a manifest without it or with an unknown value once the backend and worker rollout
  is complete.
- Item and job output artifacts echo the selected profile; FastAPI rejects an output whose profile
  differs from the stored row.
- Replay/create-or-validate rejects pre-existing output produced for a different profile.

**Verification:**

- Deterministic manifest-byte test proves profile changes the SHA-256.
- Worker manifest and result-writer tests prove profile mismatch fails closed.
- Backend output-validation tests cover matching and mismatching profile values.

**Dependencies:** Task 1.

### Task 3: Make worker artifact production profile-aware

**Description:** Pass the validated profile into production stages and construct only artifacts
retained by that profile. Keep detector/foreground mask objects needed for safety computation in
memory, but do not create optional preview images, optional per-detection PNG byte payloads, or
their metadata rows under `essential`.

**Why:** Filtering artifact objects only in `ResultWriter` would still spend CPU encoding PNGs and
memory holding their bytes. The performance benefit requires avoiding their creation upstream.

**Likely files:**

- `src/watermark_worker/production_executor.py`
- `src/watermark_worker/production_stages.py`
- `src/watermark_worker/pipeline.py`
- `src/watermark_worker/cli.py`
- reference test stages and production-stage/pipeline/CLI integration tests

**Acceptance criteria:**

- `full` produces the current artifact contract byte-for-byte where deterministic fixtures permit.
- `essential` never serializes the four optional diagnostics listed in Section 1.
- The detector-results JSON in `essential` contains no dangling `maskArtifact` reference.
- Safety-gate decisions, combined-mask presence, final image, result status, and marker ordering
  are identical in meaning for the same input under both profiles.

**Verification:**

- Unit tests spy on PNG serialization and ImageDraw construction to prove skipped artifacts are not
  created under `essential`.
- Tests enumerate artifact paths for succeeded, skipped, blocked, failed, and replayed items under
  both profiles.
- Existing marker-last and corruption/failure tests remain green.

**Dependencies:** Task 2.

### Task 4: Add the frontend submission control

**Description:** Add a labelled “Save diagnostic artifacts” control to the existing bulk watermark
job form. It maps enabled to `full` and disabled to `essential`, defaulting enabled. Include the
selected value in the existing FastAPI create request and render it in job details/history when the
API response provides it.

**Why:** The user needs a deliberate per-job choice before work starts. A default-on UI avoids
surprising loss of diagnostics during rollout.

**Likely files:**

- existing watermark bulk-job form and API client in
  `/home/maaz/Desktop/infringement_project/infringement_frontend`
- frontend request/response types and form tests

**Acceptance criteria:**

- The default submission sends `artifactProfile: "full"`.
- Turning the control off sends `"essential"` and the submitted job detail displays that value.
- The UI preserves current validation, loading, error, and auth behavior.

**Verification:**

- Component/API-client tests cover both values.
- Browser test creates one job of each profile against a test backend or mocked API.

**Dependencies:** Task 1. It can be developed in parallel with Tasks 2 and 3 once the API enum is
settled.

### Task 5: Controlled rollout and benchmark

**Description:** Build and deploy the backend and worker in an order that never sends a manifest
the currently deployed worker cannot understand: deploy worker support first, then backend support,
then frontend. Start with `full` as the only UI default, run an authorized small `essential` job,
then compare a representative 100-image job under each profile.

**Why:** Artifact profiles change the immutable output contract and need both correctness and
measured performance evidence before large production use.

**Acceptance criteria:**

- One controlled `full` job validates compatibility and one controlled `essential` job validates
  the retained artifacts through the authorized application download path.
- For the same representative workload, recorded metrics include artifact object count, bytes,
  artifact-write time, item p50/p95, and outcome mix for both profiles.
- AWS GPU capacity is verified idle after testing.

**Verification:**

- Run backend migrations, backend tests, worker tests, and frontend checks before deployment.
- Follow the existing backend Cloud Run and AWS worker-image runbooks; record immutable deployed
  revisions/digests and job IDs in the timing baseline document.

**Dependencies:** Tasks 1 through 4.

## 5. Checkpoints

### Checkpoint A: Durable contract

- Database, API, manifest, worker, and output-validation tests prove profile identity is immutable
  and fail closed on mismatches.
- No artifact behavior changes yet; all jobs still use `full`.

### Checkpoint B: Artifact behavior

- Full and essential artifact inventories pass for every terminal outcome.
- Omitted diagnostics are proved not to be encoded, while safety decisions remain unchanged.

### Checkpoint C: Product and performance acceptance

- Frontend switch works end to end.
- A repeated benchmark demonstrates the storage/I/O reduction and no correctness regression.
- `full` remains the default until the product owner explicitly changes the policy.

## 6. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| A retry changes artifact identity | Persist profile before preparation and pin it in manifest bytes. |
| A skipped JSON reference points to an omitted mask | Make detections JSON profile-aware and assert no dangling references. |
| Safety behavior changes while removing artifacts | Keep all safety inputs in memory; test decisions across both profiles. |
| Older worker rejects a new manifest during rollout | Deploy worker support before enabling backend manifest emission. |
| Debugging loses evidence unexpectedly | Default to `full`; show the choice in job responses/UI; use `essential` deliberately. |
| Savings are assumed rather than measured | Compare matching datasets with the existing timing events and artifact object counts. |

## 7. Definition of done

- A user can select diagnostics on/off at job submission.
- FastAPI persists and returns the choice, and the immutable manifest, worker outputs, and backend
  validation all agree on it.
- `essential` omits only the defined diagnostics without changing safety decisions or required
  outputs.
- Replay and marker-last invariants hold.
- Tests and a controlled benchmark show the behavior and measured impact.
