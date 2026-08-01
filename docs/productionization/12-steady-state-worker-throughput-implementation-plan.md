# Steady-State Worker Throughput Implementation Plan

Status: Implementation-ready plan; no runtime changes in this document have been executed.

Last updated: 2026-08-02

Primary repository: `/home/maaz/Desktop/watermarkPoc`

## 1. Purpose

This plan covers the first four measured optimizations for the AWS GPU worker:

1. add per-item stage timings;
2. compile and require the missing CUDA custom kernel;
3. overlap bounded CPU/GCS input and output work with the single-GPU pipeline; and
4. reduce optional artifact encoding and upload overhead while preserving immutable results.

The goal is to improve the steady-state interval from first completed image to final completed
image without weakening safety decisions, immutable GCS identities, retry behavior, or scale-to-zero
operation.

This is a worker-image project. It does not require a FastAPI or Cloud Run deployment unless the
accepted implementation changes an application-facing contract. The intended design below avoids
such a contract change.

## 2. Current measured baseline

The implementation agent must retain this baseline and compare every optimization against the same
representative source set.

| Field | Latest measured value |
| --- | ---: |
| Application job | `wm_251766b09ad44d60acbc161ab2a8100a` |
| AWS Batch job | `8ce85ddf-28bc-47b4-83f3-518cb6b2c18f` |
| GPU | Tesla T4 on `g4dn.xlarge` in `eu-north-1` |
| Items | 99 |
| Inpainted | 45 |
| No-watermark skips | 28 |
| Safety-gate blocks | 26 |
| First item to last item | 733.335 seconds |
| Completion cadence | approximately 7.48 seconds per completion |
| Throughput | approximately 8.1 terminal images/minute |
| Worker start to worker completion | 985.905 seconds |
| Explicit model-stage initialization | 21.752 seconds |
| Peak CUDA allocated | approximately 13.0 GB |
| Peak CUDA reserved | approximately 14.3 GB |
| Infrastructure failures | 0 |

At the latest workload mix, linear steady-state projection is about 20.6 hours for 10,000 images.
The minimum 10-hour throughput is 16.67 images/minute, and the preferred safety-margin target is
20 images/minute. These are benchmark gates, not promises.

The run also emitted repeated warnings that the multi-scale deformable-attention CUDA extension
could not load because `CUDA_HOME` and the compiled shared object were unavailable. The worker
continued through a fallback path. Whether that fallback materially affects detection latency must
be measured, not assumed.

## 3. Existing execution path

The current worker processes one item at a time in `src/watermark_worker/cli.py` and
`src/watermark_worker/pipeline.py`:

```text
read immutable source from GCS
  -> checksum and replay lookup
  -> decode source image
  -> background removal
  -> YOLO detection
  -> GroundingDINO detection
  -> SAM 2 segmentation
  -> safety-gate evaluation
  -> optional LaMa inpainting
  -> create PNG/JSON artifacts
  -> upload artifacts serially
  -> write result.json
  -> write item _COMPLETE.json last
```

Models are already loaded once and kept resident. SAM 2 already receives all boxes for an image in
one call. Inpainting is already skipped for no-watermark and safety-blocked outcomes.

## 4. Non-negotiable invariants

Every task in this plan must preserve the following:

- Only one complete `PipelineExecutor.process_one` invocation may use the T4 at a time.
- No unbounded queue, executor, future list, decoded-image cache, or artifact-byte buffer is allowed.
- GCS source generations and checksums remain pinned and verified.
- Artifact writes remain create-or-validate immutable operations.
- An item completion marker is written only after all required artifacts and `result.json` succeed.
- The job completion marker is written only after every item result is durable and the output
  manifest is durable.
- A failed asynchronous task must be observed and must prevent the relevant item or job marker.
- Replay must either adopt a compatible complete item or fail closed on incompatible identity.
- Result order in the output manifest remains the input-manifest order.
- The safety gate, detection thresholds, model identities, and final-image semantics do not change
  in Tasks 1-4.
- Logs must not contain source URLs, signed URLs, credentials, image bytes, prompts supplied by a
  user, or raw exception messages that may embed sensitive values.
- GPU capacity must return to zero after every controlled benchmark.

## 5. Target worker shape

The safe target is a three-lane bounded pipeline. It overlaps CPU/network work but retains a single
GPU owner.

```text
Input lane (depth 1, benchmark depth 2)
  GCS read -> checksum -> replay lookup -> decode
                        |
                        v
GPU lane (exactly one item)
  RMBG -> detectors -> SAM 2 -> safety -> optional LaMa
                        |
                        v
Output lane (depth 1, benchmark depth 2)
  serialize required payloads -> upload artifacts -> result -> marker
```

The input and output lanes must use bounded backpressure. The main thread remains the only owner of
the GPU executor and CUDA-backed objects. PIL images and byte payloads must have explicit ownership
and deterministic close/release behavior.

## 6. Measurement contract

### 6.1 One terminal timing event per item

Add a structured event named `watermark_worker_item_timing` with `schemaVersion: 1`. Emit it only
after an item is terminal or after a terminal write/replay failure has been classified. Use a
monotonic clock for durations and UTC only for the event timestamp supplied by the existing event
emitter.

Required identity and outcome fields:

```text
eventType
schemaVersion
jobId
itemId
status
decision
reused
errorType                 # class name only; null when successful
```

Required duration fields, in milliseconds, with `null` for stages that did not run:

```text
itemWallMilliseconds
inputQueueWaitMilliseconds
sourceReadMilliseconds
sourceChecksumMilliseconds
completionLookupMilliseconds
decodeMilliseconds
executorInitializationWaitMilliseconds
backgroundRemovalMilliseconds
detectionMilliseconds
safetyGateMilliseconds
inpaintingMilliseconds
resultAssemblyMilliseconds
outputQueueWaitMilliseconds
artifactWriteMilliseconds
resultWriteMilliseconds
markerWriteMilliseconds
```

Required counters and sizes:

```text
sourceBytes
artifactCount
artifactBytes
gcsReadCount
gcsWriteCount
detectionCount
```

`backgroundRemovalMilliseconds` and `detectionMilliseconds` initially measure the complete stage
method, including its current post-processing and diagnostic encoding. Task 4 removes or isolates
avoidable encoding. Do not silently change the meaning of an existing timing field; add a new field
and increment `schemaVersion` if later work needs a different definition.

### 6.2 One aggregate event per job

Add `watermark_worker_job_timing` after the durable job marker succeeds. It must contain:

```text
schemaVersion
jobId
totalItems
succeededItems
skippedItems
blockedItems
failedItems
reusedItems
workerWallMilliseconds
modelPreparationMilliseconds
executorInitializationMilliseconds
itemPipelineMilliseconds
jobFinalizationMilliseconds
peakPendingInputItems
peakPendingOutputItems
```

Percentiles should be calculated offline from item events rather than emitted by every worker.

### 6.3 Baseline query output

The implementation must include a small checked-in log-analysis script or documented deterministic
query that reports, for one job:

- count and outcome mix;
- p50, p95, maximum, and sum for every duration;
- item throughput;
- artifact count and bytes by outcome;
- first and last item timestamps;
- peak CUDA memory from existing lifecycle events; and
- any missing timing events.

The script must consume exported JSON logs and must not require production credentials in tests.

## 7. Dependency order

```text
T1 timing primitives
  -> T2 end-to-end timing events
      -> benchmark checkpoint A
          -> T3 custom-kernel discovery/build
              -> benchmark checkpoint B
          -> T4 bounded input prefetch
              -> T5 bounded output overlap
                  -> benchmark checkpoint C
                      -> T6 artifact policy
                          -> T7 avoid optional encoding
                              -> T8 bounded artifact uploads
                                  -> benchmark checkpoint D
```

Do not combine all four optimizations into one image or commit. Each checkpoint must produce an
immutable worker image digest that can be rolled back independently.

## 8. Phase A: stage-level measurement

### Task T1: Add internal timing primitives

**Description:** Add a small internal timing model that records monotonic named spans without
logging from model/runtime classes. The CLI owns final event emission.

**Acceptance criteria:**

- A frozen timing record represents absent stages explicitly and cannot contain negative values.
- `PipelineExecutor.process_one` records background removal, detection, safety evaluation, and
  optional inpainting around the current calls.
- Timing collection does not alter persisted `ItemResult`, marker, or manifest schemas.

**Verification:**

- Unit tests use an injected fake monotonic clock; no sleeps.
- Existing pipeline outcome and close-on-error tests remain unchanged in behavior.
- `pytest -q tests/unit/test_pipeline.py` passes.

**Dependencies:** None.

**Files likely touched:**

- `src/watermark_worker/pipeline.py`
- new `src/watermark_worker/timing.py`
- `tests/unit/test_pipeline.py`
- new `tests/unit/test_timing.py`

**Estimated scope:** Medium.

### Task T2: Instrument CLI input, output, and job phases

**Description:** Time source access, checksum/replay work, decode, result assembly, item writes, and
job finalization. Extend writer return values with internal write metrics or add an internal writer
timing callback. Emit the two versioned events defined in Section 6.

**Acceptance criteria:**

- Exactly one item timing event exists for every terminal `item_completed` event, including reused,
  skipped, blocked, and failed items.
- Artifact, result, and marker write times are separate; marker-last ordering is unchanged.
- Timing events contain only the safe allowlisted fields from Section 6.

**Verification:**

- Integration tests cover succeeded, skipped, blocked, failed, and replayed items.
- Tests assert an item marker is never written after an artifact/result write failure.
- Tests assert event count, schema version, required keys, and null-stage semantics.
- `pytest -q tests/unit/test_result_writer.py tests/integration/test_cli_local.py` passes.

**Dependencies:** T1.

**Files likely touched:**

- `src/watermark_worker/cli.py`
- `src/watermark_worker/result_writer.py`
- `src/watermark_worker/progress.py`
- `tests/unit/test_result_writer.py`
- `tests/integration/test_cli_local.py`

**Estimated scope:** Medium.

### Task T2a: Add deterministic timing analysis

**Description:** Add a script that reads exported CloudWatch JSON events and calculates the baseline
table without network access.

**Acceptance criteria:**

- The script rejects mixed job IDs unless a job ID is explicitly selected.
- Missing or duplicate item timing events cause a non-zero exit.
- Output uses deterministic ordering and fixed units.

**Verification:**

- Fixture logs cover normal, reused, missing, and duplicate events.
- The script reproduces the known 99-item count and outcome mix from a sanitized fixture summary.

**Dependencies:** T2.

**Files likely touched:**

- new `scripts/analyze_worker_timings.py`
- new `tests/fixtures/worker-timing/`
- new `tests/unit/test_analyze_worker_timings.py`

**Estimated scope:** Small.

### Checkpoint A: Measurement-only image

- Run all unit/integration/container contract tests.
- Build and deploy a measurement-only worker image using the worker-image runbook.
- Run the frozen representative benchmark at least three times.
- Confirm 99 item timing events and one job timing event per attempt.
- Confirm timing overhead is less than 5% at p50 throughput.
- Record p50/p95 for every stage before changing the CUDA build or concurrency.
- Prove zero active GPU capacity after the benchmark.

## 9. Phase B: compiled CUDA custom kernel

### Task T3: Identify and pin the extension build contract

**Description:** Determine which installed package/source owns the failing
`MultiScaleDeformableAttention` extension and the exact build command compatible with PyTorch
`2.5.1+cu121`. Do not toggle `SAM2_BUILD_CUDA` merely because it mentions CUDA; the warning must be
traced to its actual owner.

**Acceptance criteria:**

- The extension source, Python import path, produced `.so`, compiler ABI, CUDA architecture list,
  and license are documented.
- A local/CodeBuild probe distinguishes the compiled path from the fallback path.
- The selected build and runtime base images are pinned by digest; no mutable tag or runtime fetch
  is introduced.

**Verification:**

- A CPU-only container import probe loads the compiled module without initializing CUDA.
- A controlled GPU probe performs the relevant forward path and proves the compiled operation was
  selected.

**Dependencies:** Checkpoint A.

**Files likely touched:**

- `Dockerfile.gpu`
- `requirements-gpu.lock` only if a reviewed build dependency is required
- `tests/unit/test_container_contract.py`
- `tests/container/test_container_smoke.py`
- a focused note under `docs/productionization/`

**Estimated scope:** Medium.

### Task T3a: Compile in the builder and keep the runtime minimal

**Description:** Use a digest-pinned CUDA development builder with the compiler/toolkit required by
the extension, then copy only the virtual environment and compiled shared object into the existing
non-root runtime. Set `CUDA_HOME` only where required. Do not install compilers or `nvcc` in the
runtime image.

**Acceptance criteria:**

- CodeBuild fails if the compiled extension is absent or imports through the fallback.
- The runtime remains non-root, read-only compatible, network-independent, and checksum-pinned.
- `SAM2_BUILD_CUDA=0` remains unchanged unless T3 proves this exact extension is owned by SAM 2 and
  GPU parity tests justify changing it.

**Verification:**

- Container contract tests assert digest-pinned build inputs and absence of compiler tooling in the
  runtime stage.
- CodeBuild smoke imports the exact extension path.
- One controlled GPU acceptance has no missing-kernel warning.

**Dependencies:** T3.

**Files likely touched:**

- `Dockerfile.gpu`
- `buildspec.yml`
- `infra/aws/codebuild.tf` if a separate `GPU_BUILD_IMAGE` argument is required
- `infra/aws/variables.tf`
- `infra/aws/environments/staging.tfvars.example`
- container tests

**Estimated scope:** Medium.

### Checkpoint B: Kernel A/B benchmark

- Use the same source generations and outcome-quality review set as Checkpoint A.
- Run at least three attempts with the measurement image and three with the compiled-kernel image.
- Compare detection p50/p95, total item p50/p95, throughput, peak CUDA memory, and decisions.
- Require zero safety-decision regressions on the reviewed set.
- Keep the compiled path only if it removes the warning and produces a repeatable improvement or is
  otherwise required for correctness/supportability.
- Restore zero GPU capacity.

## 10. Phase C: bounded I/O overlap

### Task T4: Extract a testable prepared-input boundary

**Description:** Move source read, checksum, completed-item lookup, and decode into a function that
returns either a replayed terminal entry or an owned `PreparedInferenceItem`. This function must not
touch CUDA or the shared pipeline executor.

**Acceptance criteria:**

- Prepared input has an explicit `close()`/ownership rule for its PIL image and bytes.
- Source identity and replay validation are identical to the current synchronous path.
- Exceptions are re-raised at the corresponding manifest position and preserve current fail-closed
  behavior.

**Verification:**

- Tests cover normal decode, corrupt input, generation mismatch, completed replay, and incompatible
  replay.
- Tests prove every decoded image is closed on success, failure, and cancellation.

**Dependencies:** Checkpoint B.

**Files likely touched:**

- `src/watermark_worker/cli.py`
- new `src/watermark_worker/execution_pipeline.py`
- `tests/integration/test_cli_local.py`
- new `tests/unit/test_execution_pipeline.py`

**Estimated scope:** Medium.

### Task T4a: Add bounded input prefetch

**Description:** Add one input worker and a bounded queue. Default depth is 1; permit a strictly
validated depth of 2 for benchmarks. The main thread consumes manifest order and remains the only
GPU executor owner.

**Acceptance criteria:**

- At most the configured number of decoded future items is resident.
- No future is accumulated for all 10,000 manifest items.
- Prefetch stops, drains, and closes owned objects on the first fatal job error.
- Replayed items bypass GPU execution without disturbing manifest order.

**Verification:**

- A concurrency test blocks the first GPU item and proves only the allowed number of inputs are
  prepared.
- A thread-affinity test proves `PipelineExecutor.process_one` is called on one thread only.
- A 10,000-entry synthetic test proves bounded pending-item count.

**Dependencies:** T4.

**Files likely touched:**

- `src/watermark_worker/execution_pipeline.py`
- `src/watermark_worker/cli.py`
- execution-pipeline tests
- CLI integration tests

**Estimated scope:** Medium.

### Task T5: Add one-item output overlap

**Description:** After inference and required serialization have produced immutable byte payloads,
submit the previous item's write to one output worker while the GPU processes the next item. Begin
with one pending output item. Do not pass CUDA tensors or open GPU-backed images to the writer
thread.

**Acceptance criteria:**

- The output worker receives only immutable Python data/bytes and its own thread-confined storage
  adapter/writer.
- The main thread applies backpressure before a second pending output exceeds the configured depth.
- All output futures are joined before the job manifest is constructed.
- Any output exception prevents the item/job completion contract from being falsely published.

**Verification:**

- A blocking fake writer proves GPU work for item N+1 overlaps the write for item N.
- Failure tests prove artifact, result, and marker ordering and job-marker suppression.
- Tests prove output manifest ordering remains input ordering even if writes finish out of order.
- Tests prove the storage adapter is not shared across threads unless its thread safety is explicitly
  established and covered.

**Dependencies:** T4a.

**Files likely touched:**

- `src/watermark_worker/execution_pipeline.py`
- `src/watermark_worker/cli.py`
- `src/watermark_worker/storage.py` or a new storage factory boundary
- `src/watermark_worker/result_writer.py`
- execution and writer tests

**Estimated scope:** Medium.

### Checkpoint C: I/O-overlap benchmark

- Benchmark synchronous, input-depth-1, input-depth-2, and input-depth-2/output-depth-1 modes.
- Use three attempts per mode and the same immutable source set.
- Compare source-read, input-wait, GPU-stage, output-wait, artifact-write, total item p50/p95,
  throughput, system RAM, and GPU memory.
- Select the smallest queue depths that remove measurable GPU idle gaps.
- Reject a mode that improves the mean while materially worsening p95, memory, or reliability.
- Keep complete GPU calls serial and restore zero GPU capacity.

## 11. Phase D: artifact retention and upload efficiency

### Task T6: Define an explicit artifact-retention policy

**Description:** Add a strict pipeline-config section that distinguishes contract-critical/audit
artifacts from large review-only diagnostics. Artifact policy affects immutable output identity and
therefore belongs in the checksum-pinned pipeline config, not an untracked environment toggle.

Proposed profiles:

| Profile | Retained artifacts |
| --- | --- |
| `full` | Exact current artifact set; compatibility and debugging profile |
| `essential` | `source_metadata.json`, `detections/detections.json`, approved combined mask when present, final image when inpainted, `result.json`, and completion marker |

Review-only artifacts omitted by `essential`:

- `background_removed/white_background.png`;
- `background_removed/foreground_mask.png`;
- per-detector mask PNGs; and
- `detections/annotated.png`.

**Acceptance criteria:**

- The default remains `full` until controlled acceptance approves `essential`.
- `result.json`, final image, combined-mask links, result marker, and job marker remain valid.
- The configuration is strict, hashed into run identity, and documented.

**Verification:**

- Config tests reject unknown profiles and omitted required fields.
- Contract tests enumerate exact artifacts for both profiles and every outcome.
- Replay rejects an artifact set generated under an incompatible config identity.

**Dependencies:** Checkpoint C.

**Files likely touched:**

- `src/watermark_worker/config.py`
- `configs/watermark-bulk-v1.yaml`
- test configuration fixtures
- config and replay tests

**Estimated scope:** Small.

### Task T7: Avoid creating artifacts that will not be retained

**Description:** Pass the retention policy into production stages. Do not construct annotated
images, encode PNGs, or allocate per-detection artifact bytes and then filter them later.
In-memory masks required by the safety gate must remain unchanged.

**Acceptance criteria:**

- `essential` skips optional ImageDraw work and optional PNG serialization at the source.
- Detection masks still reach the safety gate and combined-mask construction unchanged.
- The final image and required audit JSON remain identical in meaning.

**Verification:**

- Unit tests spy on PNG serialization and prove omitted artifacts are never encoded.
- Golden safety decisions and final-image review pass for both profiles.
- Timing events show artifact count/bytes and serialization differences.

**Dependencies:** T6.

**Files likely touched:**

- `src/watermark_worker/production_executor.py`
- `src/watermark_worker/production_stages.py`
- `src/watermark_worker/pipeline.py`
- `src/watermark_worker/cli.py`
- production stage and integration tests

**Estimated scope:** Medium.

### Task T8: Upload independent artifacts with bounded concurrency

**Description:** Within one item write, upload independent artifact payloads with a small fixed
worker count, then write `result.json`, then write `_COMPLETE.json`. This is separate from the
item-level output lane and must have its own strict bound.

Start with artifact-upload concurrency 2 and benchmark 4. Do not create an executor proportional
to detection count or item count.

**Acceptance criteria:**

- All artifact futures succeed before `result.json` is written.
- `result.json` succeeds before the item marker is written.
- On any artifact failure, no result or marker is published; successful immutable partial artifacts
  can be adopted by the existing retry path.
- Returned artifact references are deterministically ordered by relative name.

**Verification:**

- Blocking fake storage proves no more than the configured uploads run simultaneously.
- Failure-injection tests cover first, middle, and last artifact failures.
- Replay tests prove compatible partial artifacts are create-or-validated and incompatible bytes
  fail closed.
- Result-writer unit tests and local GCS integration tests pass.

**Dependencies:** T7.

**Files likely touched:**

- `src/watermark_worker/result_writer.py`
- `src/watermark_worker/storage.py` or storage factory boundary
- `tests/unit/test_result_writer.py`
- integration tests

**Estimated scope:** Medium.

### Checkpoint D: Artifact-policy and upload benchmark

- Benchmark `full` serial upload, `full` concurrent upload, and `essential` concurrent upload.
- Compare object count, artifact bytes, serialization time, upload time, p50/p95 item time,
  throughput, and replay behavior.
- Inspect retained artifacts through the authorized application download path.
- Obtain product/operations approval before making `essential` the production profile.
- Require zero missing contract-critical objects and zero decision regressions.
- Restore zero GPU capacity.

## 12. Required test matrix

| Area | Required cases |
| --- | --- |
| Timing | success, skip, block, failure, replay, absent stage, exception in write |
| Input prefetch | depth bound, order, corrupt image, read failure, cancellation, image close |
| Output overlap | backpressure, out-of-order completion, writer failure, drain before job marker |
| Artifact policy | full/essential sets for success, skip, block, failure |
| Artifact upload | concurrency bound, deterministic references, partial failure, retry adoption |
| CUDA extension | build probe, CPU import, GPU compiled-path probe, fallback rejection |
| Security | non-root, read-only runtime, no runtime network fetch, no secrets in logs |
| Scale | synthetic 10,000-entry manifest with bounded memory/queue counts |

Before each image build, run at minimum:

```bash
rtk pytest -q tests/unit
rtk pytest -q tests/integration/test_cli_local.py tests/integration/test_cli_gcs.py
rtk python -m compileall -q src tests scripts
rtk git diff --check
```

Run container tests with the reviewed GPU image and their documented environment gates. Do not
pretend a CPU-only test proves the CUDA extension executes on a T4.

## 13. Benchmark protocol

### 13.1 Dataset

- Use at least 100 fixed source objects; 500 is preferred for final capacity evidence.
- Pin every source URI, generation, checksum, and size.
- Preserve a representative mix of inpainted, skipped, and safety-blocked items.
- Use a new job ID and output prefix per attempt; never reuse completed output from a prior digest.
- Run at least three attempts per configuration after one warm-up/acceptance attempt.

### 13.2 Metrics

Record:

- Batch acceptance-to-start and Batch runtime;
- worker/model preparation and explicit stage initialization;
- every item timing p50/p95/max;
- terminal images/minute;
- outcome mix and infrastructure failures;
- artifact object count and bytes;
- GCS read/write operation counts;
- peak system RAM and CUDA allocated/reserved bytes; and
- worker image digest, config checksum, and model-manifest checksum.

### 13.3 Decision gates

- Correctness and safety gates are pass/fail and take precedence over speed.
- No optimization is accepted from a single run.
- Retain a change only when the same dataset shows a repeatable stage-specific improvement.
- The combined four-task result should materially improve the current approximately 8.1
  images/minute. Reaching 16.67 images/minute remains the 10-hour single-worker target.
- If the first four tasks do not reach the target, the next decision is stage-specific inference
  optimization or multi-GPU sharding, not unbounded concurrency on the T4.

## 14. Commit and deployment sequence

Use small, reversible commits and immutable images:

1. `feat: add worker item stage timings`
2. `test: add deterministic worker timing analysis`
3. build/deploy/benchmark Checkpoint A
4. `build: compile deformable-attention CUDA extension`
5. build/deploy/benchmark Checkpoint B
6. `refactor: extract prepared worker input boundary`
7. `feat: add bounded worker input prefetch`
8. `feat: overlap one durable item write`
9. build/deploy/benchmark Checkpoint C
10. `feat: add worker artifact retention profiles`
11. `perf: skip optional diagnostic artifact encoding`
12. `perf: upload item artifacts with bounded concurrency`
13. build/deploy/benchmark Checkpoint D
14. `docs: record final worker throughput baseline`

For every worker image:

1. refresh AWS identity, region, submission gate, queue, job definitions, and zero-GPU state;
2. close submission for the controlled deployment window;
3. run the reviewed CodeBuild path;
4. record the immutable ECR digest;
5. update the reviewed staging digest through Terraform;
6. inspect the targeted Terraform plan;
7. apply only the reviewed job-definition/submission references;
8. run controlled application-level acceptance;
9. compare metrics against the previous digest;
10. restore the owner-selected operational mode; and
11. prove no Batch work or GPU EC2 instance remains active.

Follow `aws-worker-image-deployment-and-gpu-acceptance-runbook.md`; do not directly mutate a running
Batch job or manually launch an EC2 instance.

## 15. Rollback

Rollback must be possible at every checkpoint:

- Keep the prior accepted ECR digest and Batch job-definition revision recorded.
- Repoint the Terraform variable to the prior digest and apply the reviewed rollback plan.
- Set input/output queue depths to their synchronous values if overlap is operationally disabled.
- Keep artifact profile `full` until `essential` passes product acceptance.
- Never delete immutable partial outputs during rollback; a new job ID/output prefix avoids identity
  collisions.
- If a worker attempt fails, do not automatically retry before recording job ID, provider job ID,
  image digest, config checksum, exit code, log stream, marker inventory, and current GPU state.

## 16. Risks and mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Timing code changes hot paths | Measurement distorts baseline | One event per item, monotonic in-memory spans, Checkpoint A overhead gate |
| Wrong CUDA extension is compiled | Larger image with no speed benefit | T3 ownership proof, exact import probe, A/B detection timings |
| CUDA ABI mismatch | Import/runtime failure | Digest-pinned devel builder matching PyTorch CUDA 12.1 and GPU acceptance |
| Prefetch increases memory | Worker OOM | Depth 1 default, depth 2 maximum until measured, explicit ownership/close tests |
| GCS client is shared unsafely | Intermittent corruption/failures | Thread-confined storage adapter per I/O worker |
| Async failure is lost | False successful marker | Join every future, marker-last assertions, failure injection |
| Artifact reduction removes required evidence | Product/debug regression | `full` default, explicit essential allowlist, authorized artifact acceptance |
| Concurrent writes break determinism | Replay incompatibility | Sort references, immutable create-or-validate, config checksum identity |
| Mean improves but tail worsens | Poor large-job reliability | Three-run p50/p95 gates and fixed workload mix |
| Full GPU concurrency is attempted | T4 OOM | Main-thread GPU ownership test and no concurrent `process_one` calls |

## 17. Definition of done

All four tasks are complete only when:

- every item emits a complete safe timing event;
- the timing analyzer produces deterministic p50/p95 evidence;
- the custom extension loads through its compiled path or is explicitly rejected as non-beneficial;
- input and output overlap remain bounded and preserve all replay/marker invariants;
- essential artifact mode avoids generating omitted diagnostics;
- artifact writes are bounded, deterministic, and marker-last;
- the same representative dataset has before/after results for every checkpoint;
- safety decisions and authorized final downloads pass review;
- the accepted digest is deployed through Terraform and recorded;
- the timing baseline document is updated with the new job IDs and metrics; and
- AWS Batch and GPU EC2 capacity are verified idle after the final run.

## 18. Handoff instructions for the implementing agent

1. Read the authoritative agent guide and current-state rules before acting.
2. Read this plan, the timing baseline, the bulk-processing plan, and the worker-image deployment
   runbook completely.
3. Refresh live AWS state; do not copy old point-in-time values into commands.
4. Start with T1 only. Do not implement prefetch or artifact changes before Checkpoint A exists.
5. Keep each task in a separate tested commit and record the image digest for every checkpoint.
6. Stop at a checkpoint if safety decisions change, marker ordering fails, GPU memory loses safe
   headroom, or the measured stage does not improve.
7. Do not deploy backend/Cloud Run code for worker-only changes.
8. Do not enable multiple complete GPU items on the same T4 as part of this plan.
