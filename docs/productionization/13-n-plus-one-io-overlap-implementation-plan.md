# N+1 Worker I/O-Overlap Implementation Plan

Status: Implementation-ready. No runtime changes in this document have been executed.

Last updated: 2026-08-02

Primary repository: `/home/maaz/Desktop/watermarkPoc`

Parent plan: [Steady-State Worker Throughput Implementation Plan](12-steady-state-worker-throughput-implementation-plan.md)

## 1. Goal and approved scope

Overlap bounded CPU/GCS work with the single existing GPU pipeline. While the main thread runs GPU
inference for item N, one worker prepares item N+1. While the main thread runs inference for item
N+1, one output worker serializes and durably writes item N.

The first implementation is deliberately fixed at N+1:

- one GPU `PipelineExecutor.process_one` call at a time, on the main thread only;
- at most one decoded prepared item beyond the current GPU item;
- at most one pending output write; and
- one input worker and one output worker, each with its own storage adapter and `ResultWriter`.

This plan does not add a public setting, environment variable, queue-depth two mode, concurrent
artifact uploads, artifact-retention changes, FastAPI changes, or Terraform changes.

## 2. Current path and target path

Current path:

```text
read/decode N -> GPU N -> encode/write N -> read/decode N+1 -> GPU N+1
```

Target N+1 path:

```text
input worker:  prepare N+1             prepare N+2
                     |                      |
main/GPU:      GPU N ----------------> GPU N+1
                     |                      |
output worker:       serialize/write N ------> serialize/write N+1
```

The main thread waits whenever either lane would exceed its one-item bound. Item results are still
recorded in manifest order, and the job output manifest is still written only after every item write
has completed successfully.

## 3. Non-negotiable invariants

- Source generation and SHA-256 validation happen before GPU inference, exactly as today.
- Replay remains fail-closed and compatible completed items bypass inference.
- The GPU executor, CUDA tensors, and model objects never leave the main thread.
- A CPU-owned Pillow final image may be transferred once to the output worker; it must not be used
  concurrently and the output worker owns and closes it.
- Stage artifacts are immutable bytes before they enter the output worker.
- Each worker thread receives a distinct storage adapter and `ResultWriter`. The current writer has
  mutable operation counters and must not be shared across threads.
- All required artifacts must be durable before `result.json`; `result.json` must be durable before
  item `_COMPLETE.json`; the job marker remains last.
- A failed input or output future is always observed, stops new work, suppresses the job marker, and
  closes every owned image.
- No list of futures, decoded images, final images, or artifact payloads may grow with manifest size.

## 4. Data and ownership contract

| Transfer | Producer | Consumer | Ownership rule |
| --- | --- | --- | --- |
| `PreparedReplay` | input worker | main thread | Immutable result/marker data; main records it immediately. |
| `PreparedInferenceItem` | input worker | main/GPU thread | Main owns the decoded source image and closes it after inference or cancellation. |
| `PendingOutputItem` | main/GPU thread | output worker | Contains immutable metadata/artifact bytes and, if inpainted, one CPU Pillow final image; output worker closes that image. |
| `WrittenOutputItem` | output worker | main thread | Immutable result, marker reference, write timing, and terminal timing event; main alone updates counts and manifest entries. |

Refactor `PreparedInferenceItem` so its fields required after transfer are explicit and it has an
idempotent `close()` method. Do not pass the current context object wholesale if it retains an image
that the receiving lane does not own.

## 5. Implementation tasks

### Task N1: Extract synchronous input preparation and output publication boundaries

**Description:** Move the current source read, checksum, replay probe, decode, terminal-decode
failure construction, result assembly, and item write flow into testable functions. Preserve the
existing synchronous CLI behavior before adding threads.

**Acceptance criteria:**

- An input-preparation function returns either a replayed terminal item, a terminal item that still
  needs publication, or one owned prepared inference item.
- An output-publication function produces `result.json`, required artifacts, and `_COMPLETE.json`
  in the current order.
- Existing local inference, replay, corrupt-image, and result contract tests remain unchanged in
  behavior.

**Verification:**

- New unit tests cover each returned preparation variant and idempotent image close.
- `rtk uv run pytest -q tests/unit/test_execution_pipeline.py tests/integration/test_cli_local.py`
  passes.

**Files likely touched:**

- new `src/watermark_worker/execution_pipeline.py`
- `src/watermark_worker/cli.py`
- `src/watermark_worker/result_writer.py`
- new `tests/unit/test_execution_pipeline.py`
- `tests/integration/test_cli_local.py`

**Estimated scope:** Medium.

### Task N2: Create thread-confined storage/writer factories

**Description:** Add an internal factory that creates one storage adapter for each input/output
lane. GCS execution creates separate GCS clients; local tests create separate adapters rooted at the
same local output directory. Keep the injected test API explicit so tests never silently share an
adapter across threads.

**Acceptance criteria:**

- Input and output workers never share the main thread's adapter or `ResultWriter`.
- Production GCS credentials and pinned URI checks remain unchanged.
- Existing create-or-validate replay semantics are unchanged.

**Verification:**

- A recording adapter test proves each worker receives a distinct instance.
- A failure-injection test proves a storage error propagates through the owning future.
- Result-writer replay tests pass unchanged.

**Dependencies:** N1.

**Files likely touched:**

- `src/watermark_worker/storage.py`
- `src/watermark_worker/cli.py`
- `src/watermark_worker/result_writer.py`
- storage and CLI tests

**Estimated scope:** Small.

### Checkpoint N1: Synchronous boundary preservation

- Run the complete local worker suite before adding concurrency.
- Confirm no new image or artifact is retained and output bytes remain identical for the reference
  pipeline.
- Review the storage-factory contract before allowing worker threads.

### Task N3: Add exactly one prepared-future input lane

**Description:** Use one `ThreadPoolExecutor` and one future. As soon as the main thread owns item
N for GPU work, submit preparation for N+1. Before the next GPU call, await that one future and
record `inputQueueWaitMilliseconds` only if it was not ready.

**Acceptance criteria:**

- At most one future input item is decoded and resident ahead of the GPU item.
- The GPU executor stays on the main thread and receives items in manifest order.
- Replayed items and terminal decode failures do not consume GPU work or reorder entries.
- On failure or cancellation, the pending prepared image is closed and no further input is started.

**Verification:**

- A blocking fake input proves item N+1 preparation overlaps GPU item N.
- An active-image test proves the maximum is current GPU image plus one prepared image.
- A 10,000-item synthetic test proves one pending input future and bounded memory.

**Dependencies:** Checkpoint N1.

**Files likely touched:**

- `src/watermark_worker/execution_pipeline.py`
- `src/watermark_worker/cli.py`
- execution-pipeline and CLI tests

**Estimated scope:** Medium.

### Task N4: Add exactly one pending output lane

**Description:** After GPU inference, transfer the item output to one output worker. The output
worker performs final CPU PNG encoding when required, builds result/artifact payloads, writes all
required artifacts, writes `result.json`, then writes the item marker. Before submitting a second
pending output, the main thread waits for and records the prior output future.

**Acceptance criteria:**

- GPU work for N+1 can begin while N encodes/uploads.
- Only immutable bytes/metadata and one explicitly transferred CPU Pillow final image enter the
  output worker; no CUDA object or shared source image crosses the boundary.
- The output worker owns and closes the final image even when encoding or upload fails.
- Output completion results are recorded by the main thread in input-manifest order.

**Verification:**

- A blocking fake writer proves GPU N+1 starts before output N completes.
- Failure injection proves no result/marker is published after an artifact failure and no job marker
  is published after an output-future failure.
- Marker-last, replay, and output-manifest order tests pass.

**Dependencies:** N3.

**Files likely touched:**

- `src/watermark_worker/execution_pipeline.py`
- `src/watermark_worker/cli.py`
- `src/watermark_worker/result_writer.py`
- `tests/unit/test_result_writer.py`
- execution-pipeline and CLI tests

**Estimated scope:** Medium.

### Task N5: Complete telemetry, cleanup, and end-of-job drain

**Description:** Populate the existing input/output queue wait fields and peak pending-item counters
for the N+1 lanes. Drain both futures before output-manifest construction, close all resources on
every terminal path, and keep the validate-only mode synchronous.

**Acceptance criteria:**

- `peakPendingInputItems` and `peakPendingOutputItems` never exceed 1.
- `inputQueueWaitMilliseconds` and `outputQueueWaitMilliseconds` are non-null only when their lane
  was used.
- No terminal timing event is omitted or duplicated; `itemWallMilliseconds` ends after the durable
  item marker.
- A failed future prevents `job_completed` and the job completion marker.

**Verification:**

- Tests cover success, replay, skip, block, corrupt input, GPU failure, output failure, and shutdown
  with each ownership state.
- `rtk uv run pytest -q` and `rtk uv run ruff check src tests` pass.

**Dependencies:** N4.

**Files likely touched:**

- `src/watermark_worker/cli.py`
- `src/watermark_worker/timing.py`
- `src/watermark_worker/execution_pipeline.py`
- local integration and timing tests

**Estimated scope:** Medium.

## 6. Benchmark and deployment checkpoint

Run three controlled attempts for each mode using the same immutable representative sources:

1. current synchronous worker;
2. N+1 input prefetch only; and
3. N+1 input prefetch plus one pending output write.

Compare throughput, p50/p95 `itemWallMilliseconds`, source/input wait, GPU-stage, output wait,
artifact/result/marker write time, peak memory, outcome mix, and error rate. Keep N+1 only if it
has no safety-decision or output-contract regression and shows repeatable benefit.

Deploy through the existing worker-image runbook. Use one controlled FastAPI acceptance job, then
verify output objects, timing events, and zero active GPU capacity. FastAPI code is not deployed for
this worker-only change.

## 7. Rollback and failure policy

- Stop immediately on an output ordering, replay, marker, or safety-decision failure.
- Roll back by repinning the prior accepted immutable worker-image digest through reviewed Terraform.
- Keep the synchronous implementation available until the N+1 benchmark passes; do not increase
  queue depth as a workaround for a failed or inconclusive result.
- Record the image digest, job IDs, timing comparison, and final zero-GPU evidence.

## 8. Definition of done

- N+1 input preparation and N+1 output publication overlap the GPU without concurrent GPU calls.
- No lane ever has more than one pending item.
- All image ownership and future failures are deterministically handled.
- Immutable artifact/result/marker ordering and replay behavior are unchanged.
- Timing events demonstrate the overlap and no terminal events are missing.
- Controlled benchmark and acceptance show a repeatable benefit with zero safety regressions.
