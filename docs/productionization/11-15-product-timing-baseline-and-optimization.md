# Watermark Pipeline Timing Baselines and Optimization Recommendations

**Test dates:** 2026-07-30 and 2026-08-01

**Status:** Measured baseline and recommendations. No optimization is implemented by this
document.

**Measured jobs:**

| Run | FastAPI job | AWS Batch job | Submitted images | Terminal result |
|---|---|---|---:|---|
| Controlled 15-product baseline | `wm_a093861c82d049f0a8bf34a142646e76` | `dad7588b-dc91-4207-a879-8a542e3c57ce` | 14 | `SUCCEEDED` |
| Observed 99-image bulk job | `wm_944043e1ac3e4e49a6e6256b09bf81aa` | `9ccb5444-faa4-4b61-86cd-e5f50053dd7b` | 99 | `SUCCEEDED` |

**GCP region:** `us-central1`

**AWS region:** `eu-north-1`

**GPU:** one `g4dn.xlarge` with one NVIDIA T4

## 1. Purpose

This document records the timings from the controlled 15-product baseline and a later observed
99-image bulk job. It identifies which changes are most likely to improve:

1. time from FastAPI submission to the first result;
2. total time for a small job; and
3. sustained throughput for a future 10,000-image job.

The recommendations are ordered by measured impact. They deliberately separate fixed startup
costs from per-image processing costs because the correct optimization depends on job size.

## 2. Test results

The FastAPI request selected exactly 15 products. Those products contained 14 eligible images, so
the GPU manifest contained 14 items rather than 15.

| Result | Count |
|---|---:|
| Products selected | 15 |
| Images prepared and submitted | 14 |
| `SUCCEEDED` | 11 |
| `SKIPPED_NO_WATERMARK` | 2 |
| `BLOCKED_BY_SAFETY_GATE` | 1 |
| Failed items | 0 |
| Terminal items | 14 |

The application job reached `SUCCEEDED`. The AWS Batch container exited with code `0`, and the
ephemeral GPU instance terminated afterward.

### 2.1 Observed 99-image bulk job

The second run selected and prepared 99 images. It completed successfully, with a substantially
different result mix from the 14-image baseline.

| Result | Count |
|---|---:|
| Images prepared and submitted | 99 |
| `SUCCEEDED` | 45 |
| `SKIPPED_NO_WATERMARK` | 28 |
| `BLOCKED_BY_SAFETY_GATE` | 26 |
| Failed items | 0 |
| Terminal items | 99 |

The result mix matters: only 45 images were inpainted, while 54 took a faster no-watermark or
safety-gate path. This run is strong evidence for preparation, orchestration, startup, and
end-to-end status timing, but it must not replace the 14-image run as the conservative
inpainting-throughput baseline.

## 3. Headline timings

| Measurement | 14-image controlled baseline | 99-image observed bulk job |
|---|---:|---:|
| FastAPI job creation to FastAPI `SUCCEEDED` | **18m 50.583s** | **37m 14.609s** |
| FastAPI job creation to preparer completion | 3m 20.623s | 11m 07.839s |
| Preparer execution duration | 2m 43.271s | **10m 33.131s** |
| FastAPI job creation to worker container start | 11m 00.544s | 19m 33.034s |
| FastAPI job creation to all GPU stages ready | **14m 28.843s** | **23m 02.836s** |
| FastAPI job creation to first item result | 14m 56.573s | 23m 30.672s |
| Processing after all GPU stages were ready | **3m 13.801s** | **12m 33.993s** |
| Worker container execution | 6m 45.675s | 16m 07.213s |
| Batch stop to FastAPI terminal reconciliation | 1m 04.364s | 1m 34.362s |
| FastAPI success to EC2 termination initiation | 1m 03.083s | Not captured |

The important result is that image processing did not consume most of the small test. Approximately
77% of the user-visible wait occurred before all models were ready for inference.

## 4. Complete timelines

All timestamps are UTC.

| Time | Event |
|---|---|
| `09:29:12.333736` | FastAPI created the watermark job in `PREPARING`. |
| `09:29:49.685993` | Cloud Run created preparer execution `watermark-bulk-preparer-mwh59`. |
| `09:31:02.224933` | The preparer container reached the started condition. |
| `09:32:27.791349` | The immutable input manifest was written to GCS. |
| `09:32:32.956569` | The preparer execution completed successfully. |
| `09:33:07.079865` | Cloud Run created orchestrator execution `watermark-aws-orchestrator-6lng2`. |
| `09:34:00.554629` | The orchestrator container reached the started condition. |
| `09:34:06.469000` | AWS Batch accepted the GPU job. |
| `09:36:31.826000` | First `g4dn.xlarge` launch attempt failed for insufficient capacity in `eu-north-1a`. |
| `09:36:42.211000` | Second launch attempt failed in `eu-north-1a`. |
| `09:36:50.685000` | AWS successfully launched `i-053112d2e146891d6` in `eu-north-1b`. |
| `09:37:35.375000` | The EC2 host registered with ECS. |
| `09:37:39.149000` | ECS started pulling the worker image from ECR. |
| `09:39:58.403000` | ECS finished pulling the worker image. |
| `09:40:12.878000` | The Batch container started. |
| `09:40:13.928830` | The worker emitted `job_started`. |
| `09:40:17.890688` | GPU acceptance confirmed the Tesla T4 and CUDA runtime. |
| `09:43:21.453492` | Background-removal stage was initialized. |
| `09:43:37.403459` | Detection stage was initialized. |
| `09:43:41.176635` | Inpainting stage was initialized; all required stages were resident. |
| `09:44:08.906974` | The first item completed. |
| `09:46:54.977969` | The fourteenth and final item completed. |
| `09:46:56.758457` | Worker wrote the output manifest and job completion marker. |
| `09:46:58.553000` | AWS Batch container stopped successfully. |
| `09:48:02.917106` | GCP reconciliation marked the FastAPI job `SUCCEEDED`. |
| `09:49:06.000000` | EC2 termination was initiated after Batch scaled down. |

### 4.1 Observed 99-image bulk-job timeline

All timestamps are UTC.

| Time | Event |
|---|---|
| `10:34:44.529514` | FastAPI created `wm_944043e1ac3e4e49a6e6256b09bf81aa` in `PREPARING`. |
| `10:35:19.238072` | Cloud Run created preparer execution `watermark-bulk-preparer-zgh2g`. |
| `10:45:52.368727` | The preparer execution completed; all 99 inputs were staged and the immutable input manifest was available. |
| `10:48:03.507000` | AWS Batch accepted job `9ccb5444-faa4-4b61-86cd-e5f50053dd7b`. |
| `10:50:34.000000` | Batch launched temporary GPU instance `i-09e666890edc51dc9` (`g4dn.xlarge`). |
| `10:54:17.563000` | The Batch container started. |
| `10:54:20.027778` | The worker emitted `job_started`. |
| `10:54:24.104384` | GPU acceptance confirmed the Tesla T4 and CUDA runtime. |
| `10:57:27.542233` | Background-removal stage initialized. |
| `10:57:43.375718` | Detection stage initialized. |
| `10:57:47.365414` | Inpainting stage initialized; all required stages were resident. |
| `10:58:15.201215` | The first item completed. |
| `11:10:21.358374` | The ninety-ninth and final item completed. |
| `11:10:24.776000` | AWS Batch container stopped successfully. |
| `11:11:59.138163` | GCP reconciliation validated the final output manifest and marked the FastAPI job `SUCCEEDED`; all 99 item statuses became terminal. |

## 5. Contiguous phase breakdown

The following phases account for the complete 18m 50.583s application duration without
overlapping one another.

| Phase | Duration | Share of total | What it represents |
|---|---:|---:|---|
| FastAPI creation to preparer dispatch | 37.352s | 3.30% | Waiting for the control plane to pick up the durable preparation intent. |
| Preparer Cloud Run startup | 1m 12.539s | 6.42% | Creating and starting the preparer container. |
| Preparer active work | 1m 25.566s | 7.57% | Selecting targets, downloading 14 Shopify images, validating them, uploading them to GCS, and building the manifest. |
| Preparer finalization | 5.165s | 0.46% | Container exit and Cloud Run completion after the manifest write. |
| Prepared job to orchestrator dispatch | 34.123s | 3.02% | Waiting for the next control-plane pass to pick up the GPU submission intent. |
| Orchestrator Cloud Run startup | 53.475s | 4.73% | Starting a separate container for a submission operation that took only a few seconds. |
| Orchestrator active submission | 5.914s | 0.52% | Obtaining the Google ID token and creating/adopting the AWS reservation and Batch job. |
| Batch scheduling and successful EC2 launch | 2m 44.216s | 14.52% | Batch scaling reaction plus two capacity failures before launching in another AZ. |
| EC2 boot and ECS registration | 48.464s | 4.29% | GPU host boot, ECS agent registration, and task assignment. |
| ECR worker-image pull | **2m 19.254s** | **12.32%** | Downloading and unpacking the large worker image on the new host. |
| ECS post-pull task startup | 14.475s | 1.28% | Preparing and starting the container after the image pull. |
| Worker entrypoint setup | 1.051s | 0.09% | Python process start before the worker emitted `job_started`. |
| Pre-inference setup and model preparation | **3m 27.248s** | **18.33%** | First source access, model artifact materialization/preparation, imports, and initialization of all GPU stages. |
| Processing 14 items | **3m 13.801s** | **17.14%** | Sequential inference, safety decisions, inpainting where approved, and item result writes. |
| Worker output finalization | 1.780s | 0.16% | Stage release, output manifest, and completion marker. |
| Container stop | 1.795s | 0.16% | Time from worker completion marker to Batch container stop. |
| GCP terminal reconciliation | 1m 04.364s | 5.69% | Waiting for the GCP control plane to observe AWS success and validate GCS outputs. |
| **Total** | **18m 50.583s** | **100%** | FastAPI creation through FastAPI terminal success. |

### 5.1 Observed 99-image bulk-job phase breakdown

The following contiguous phases account for the complete 37m 14.609s application duration. Some
sub-phases that were separately measured in the 14-image run, such as preparer container startup
versus active image I/O and ECR pull versus ECS registration, were not separately emitted for this
run. They must not be guessed from the aggregate intervals.

| Phase | Duration | What it represents |
|---|---:|---|
| FastAPI creation to preparer dispatch | 34.709s | Waiting for the scheduled control-plane pass to claim the durable preparation intent. |
| Preparer execution | **10m 33.131s** | Target snapshotting, serial Shopify downloads, byte/image validation, immutable GCS staging, database state writes, and input-manifest publication for 99 images. |
| Preparation completion to AWS Batch acceptance | 2m 11.138s | Control-plane/orchestrator dispatch, orchestrator startup, authenticated AWS submission, and Batch acceptance. |
| Batch acceptance to GPU instance launch | 2m 30.493s | Batch scheduling and scale-from-zero capacity acquisition. |
| GPU instance launch to container start | 3m 43.563s | EC2 boot, ECS registration, ECR image pull, and task startup. |
| Container start to worker `job_started` | 2.465s | Container entrypoint setup. |
| Worker `job_started` to all GPU stages ready | 3m 27.338s | First input access, model artifact preparation, imports, and GPU-stage initialization. |
| All GPU stages ready to final item completion | **12m 33.993s** | Sequential processing and deterministic output writes for all 99 items. |
| Final item to Batch container stop | 3.418s | Worker output finalization and container exit. |
| Batch stop to FastAPI terminal reconciliation | 1m 34.362s | Waiting for GCP to observe Batch success and validate the final manifest. |
| **Total** | **37m 14.609s** | FastAPI creation through FastAPI terminal success. |

### 5.2 Current UI status semantics and the observed progress gap

The 99-image job confirmed an important product behavior: the worker did make incremental progress,
but the public item-status API did not expose it during the run.

| Observation | Evidence from the 99-image run | User-visible consequence |
|---|---|---|
| Worker completion is incremental | The worker emitted the first `item_completed` event at `10:58:15`; 49 of 99 events were present by `11:04:57`; all 99 had completed by `11:10:21`. | GPU work was progressing even while the UI showed no completed image. |
| Application item updates are all-or-nothing | The reconciler updates `WatermarkJobItem` rows only after Batch success and validation of the final output manifest. | Every item remained `QUEUED` while the parent job was `RUNNING`. |
| Terminal update is delayed by reconciliation | Batch stopped at `11:10:24`; FastAPI marked the job and all 99 items terminal at `11:11:59`. | After worker completion, the UI can wait up to the next reconciliation pass plus manifest validation. |
| Batch or worker failure has no partial UI completion contract | Without an accepted final output manifest, the reconciler marks the parent job failed but does not publish completed-item rows. | A failed long job can leave completed work invisible to the user, even when some deterministic artifacts exist. |

The all-or-nothing behavior preserves authoritative validation: the frontend does not trust
unvalidated worker events, and `SUCCEEDED` is only set after GCS output evidence is accepted.
It is nevertheless a product and observability gap for long jobs. The UI should at least show
durable preparation and processing counters; a later design can expose validated partial results
without weakening final-manifest acceptance as the success criterion.

## 6. What the model-preparation measurement means

The 3m 27.248s model phase must not be interpreted as only GPU deserialization.

The worker performs several operations before all stages are resident:

- reads and validates the first source image;
- reads the selected immutable model artifacts from the GCS model bucket;
- verifies checksums and prepares archives;
- imports the model libraries;
- constructs the runtimes; and
- transfers the selected weights into the GPU stages.

The structured stage events measured the final stage initialization calls as:

| Stage | Initialization duration |
|---|---:|
| Background removal | 2.159s |
| Detection | 15.949s |
| Inpainting | 3.773s |
| **Total explicit stage initialization** | **21.881s** |

Approximately 3m 05s of the broader model phase therefore occurred outside those three explicit
stage-initialization calls. It includes model artifact transfer/preparation, imports, and the first
item's initial I/O. The current logging does not separate those operations precisely.

This distinction matters because moving AWS closer to GCS may improve model-artifact transfer, but
it will not automatically speed up Python imports or GPU runtime construction.

## 7. Processing throughput and the 10,000-image implication

Once every model was resident, the worker processed 14 items in 193.801 seconds:

```text
Observed throughput = 14 / 193.801 * 60
                    = 4.33 items per minute
```

The average was 13.84 seconds per item. This dataset contained 11 inpainted images, two
no-watermark skips, and one safety block.

If that rate remained constant:

```text
10,000 images / 4.33 images per minute = approximately 38.5 hours
```

The 99-image job showed a materially faster post-initialization aggregate rate:

```text
99 images / 753.993 seconds * 60 = 7.88 items per minute
```

That would linearly project to approximately 21.2 GPU hours for 10,000 images. It is not a
replacement capacity commitment: 54 of its 99 items were fast no-watermark or safety-gate
outcomes, whereas 11 of 14 items in the controlled baseline required inpainting. The current
evidence therefore supports an expected workload-dependent range of roughly 21 to 38.5 GPU hours,
not a promise of either figure. Both ends are incompatible with the current 24-hour bulk Batch
attempt timeout for a representative 10,000-image run.

The same 99-image run also measured **10m 33.131s** for preparation, including target snapshot,
serial image staging, and manifest publication. Projected linearly, that is approximately 17.8
hours of preparation for 10,000 images before AWS submission begins. Preparation and GPU work are
currently sequential phases, so improving only one does not make the end-to-end path viable.

A 10,000-image job completing within 10 hours requires:

```text
10,000 images / 600 minutes = 16.67 images per minute
```

The representative pipeline must therefore become approximately **3.85 times faster** than this
small-run steady-state rate to meet a 10-hour target on one job.

These are estimates, not final capacity results. The 14-image and 99-image result mixes are not
enough to establish a stable p50/p95 throughput distribution, and image dimensions, detection
counts, safety decisions, and inpainting work all affect duration. A 100-to-500-image
representative benchmark is required before selecting the production GPU or promising a 10-hour
completion target.

## 8. Optimization priorities

### Priority 0: Add measurements before changing performance-sensitive code

**Why:** The current instrumentation gives excellent infrastructure boundaries and item completion
times, but it cannot separate several important worker and preparer costs.

Add structured duration events for:

- control-plane intent age when picked up;
- preparer target selection;
- per-image Shopify download;
- per-image GCS upload;
- manifest generation;
- per-model GCS download;
- checksum validation and archive extraction;
- Python/runtime import;
- per-stage model initialization;
- per-item GCS input read;
- background removal, detection, safety evaluation, and inpainting;
- per-item artifact upload; and
- final reconciliation.

Do not add a new tracing platform initially. Structured JSON logs with monotonic durations and the
existing job/item identifiers are sufficient.

**Expected direct speed improvement:** None.

**Value:** Makes every later optimization measurable and prevents misleading conclusions.

### Priority 1: Complete the AWS region move from Stockholm to Ohio

The existing migration plan is:

`docs/productionization/10-aws-gpu-regional-migration-plan.md`

The move keeps GCP data in `us-central1` and moves the AWS runtime to `us-east-2`.

It directly targets:

- repeated GCS model-artifact reads;
- per-image GCS input reads;
- per-item GCS output writes;
- cross-cloud control requests; and
- Stockholm-specific `g4dn.xlarge` capacity behavior.

It does not directly improve:

- ECR image pull time, because ECR is already colocated with the EC2 instance in each AWS region;
- Python imports;
- model deserialization;
- T4 compute throughput; or
- Cloud Run cold starts in GCP.

The same 15-product test must be rerun after Ohio cutover. Improvement must be calculated for each
phase rather than only comparing total duration.

**Expected direct speed improvement:** Unknown until measured. The strongest likely effect is on
GCS-backed model and image I/O.

### Priority 2: Remove avoidable control-plane and orchestrator waits for small jobs

The measured run spent:

- 37.352s waiting to launch the preparer;
- 34.123s waiting to launch the orchestrator; and
- 64.364s waiting for terminal reconciliation.

Together, these scheduling/reconciliation waits contributed 2m 15.839s.

The simplest reliable improvement is:

1. after FastAPI commits the durable intent, request an immediate bounded control-plane pass;
2. retain the existing one-minute schedule as the recovery fallback;
3. after the preparer commits the immutable submission intent, request another immediate
   control-plane pass; and
4. keep periodic reconciliation for lost notifications and recovery.

An optional second improvement is to submit to AWS directly from the control-plane process instead
of launching a separate `watermark-aws-orchestrator` Cloud Run Job. The control plane already has
the AWS client and Google ID-token provider for reconciliation, while the measured orchestrator
spent approximately 53.5s starting a container and about 5.9s doing the submission.

This change must preserve:

- the durable outbox;
- the immutable request hash;
- AWS DynamoDB idempotency;
- lost-response replay; and
- scheduled recovery if the immediate attempt fails.

Do not introduce Pub/Sub or another queue solely for this optimization unless the simple
immediate-kick plus scheduled-fallback design proves insufficient.

**Potential small-job saving:** Approximately 1 to 2 minutes, subject to remeasurement.

**Large-job impact:** Negligible compared with hours of GPU processing.

### Priority 3: Parallelize only the I/O-bound bulk-preparer work

The current backend preparer handles targets sequentially:

1. download one Shopify image;
2. validate it;
3. upload it to GCS;
4. write database state; and
5. continue to the next target.

The active work took 1m 25.566s for 14 images, approximately 6.1 seconds per image.
The later 99-image execution took 10m 33.131s from Cloud Run execution creation to completion,
or approximately 6.4 seconds per image including container startup, target materialization,
serial image I/O, database state writes, and manifest publication. Current logs do not separate
those components, so neither value proves which individual operation is the bottleneck.

For large selections, use a bounded pool for only the network and image-validation portion:

- start with four concurrent downloads/uploads;
- keep a strict maximum response size;
- keep per-target success/failure and resumability;
- do not share one SQLAlchemy session across worker threads;
- serialize or carefully batch the database state updates; and
- retain the immutable create-or-validate GCS behavior.

Test at concurrency 1, 2, 4, and 8. Stop increasing concurrency when Shopify throttling, GCS
latency, memory, or Cloud SQL contention stops improving throughput.

**Potential small-job saving:** Tens of seconds.

**Large-job impact:** Potentially substantial because the current sequential preparer would scale
poorly to thousands of images. Four-way concurrency has an ideal, unproven projection of reducing
the 99-image-derived 10,000-image preparation estimate from roughly 17.8 hours to roughly 4.5
hours. Treat that only as a benchmark hypothesis, not an estimate to show users.

### Priority 3A: Add validated incremental progress for the UI

The 99-image job exposed a separate issue from throughput: AWS completed items continuously, but
the user saw all 99 as `QUEUED` until the entire job succeeded. This is intentional current
behavior, not a frontend-only polling defect. The public API does not receive a trustworthy,
incremental completion record until GCP validates the final output manifest.

Do not update the UI directly from CloudWatch logs or trust an unverified worker callback. Instead,
design and test a durable, authenticated progress-checkpoint contract:

1. The worker publishes a bounded, versioned progress checkpoint after a small number of completed
   items, containing only the data GCP needs to validate those items.
2. GCP validates the checkpoint and updates durable preparation/processing counters and, where
   supported, terminal per-item statuses in bounded database batches.
3. The final output manifest remains the only evidence that allows the parent job to become
   `SUCCEEDED`.
4. The UI polls the existing FastAPI API for counters and validated partial statuses; it never
   reads CloudWatch, GCS, AWS Batch, or AWS credentials directly.

This improves truthfulness and user confidence for long runs but does not increase GPU throughput.
The protocol must be designed so retries, out-of-order checkpoints, cancellation, partial output,
and a failed final manifest cannot expose invalid results as successful downloads.

### Priority 4: Improve steady-state GPU throughput

This is the only priority capable of moving a projected 10,000-image duration from roughly 38.5
hours toward 10 hours.

The current worker intentionally loads all required models once, keeps them resident, and processes
items one at a time. Keeping models resident is correct. Do not revert to loading models per item.

Recommended sequence:

1. Add per-item stage timings.
2. Benchmark 100-to-500 representative images on `g4dn.xlarge`.
3. Add a bounded one-or-two-item CPU/GCS prefetch buffer so the next source can download and decode
   while the GPU handles the current item.
4. Overlap result encoding/upload with later GPU work where deterministic result ordering does not
   require blocking.
5. Investigate small stage-level batches for models that support batching.
6. Benchmark the exact same dataset on `g5.xlarge` and `g6.xlarge` after the regional migration.
7. Select the instance based on total cost per successfully processed image and p95 throughput, not
   hourly price alone.

Do not run several full `process_one` calls concurrently on the T4 without new memory measurements.
The test recorded:

- peak CUDA memory allocated: approximately 13.0 GB; and
- peak CUDA memory reserved: approximately 14.3 GB.

That leaves little safe headroom on a 16 GB T4. Naive full-pipeline concurrency is likely to cause
GPU out-of-memory failures. Prefer I/O overlap and carefully measured stage batching.

If throughput still misses the target, consider FP16/runtime optimization or TensorRT only as a
later project with output-quality regression tests. Those changes are more complex and can affect
model accuracy.

### Priority 5: Reduce the worker-image pull

The ECR pull consumed 2m 19.254s. This is a meaningful small-job cost but a minor cost for a
10-hour job.

First inspect the image with `docker history` and layer-level disk usage. In particular, verify
whether the PyTorch base image and the copied virtual environment contain duplicate PyTorch/CUDA
payloads. The current build installs CUDA-enabled `torch` and `torchvision` into the virtual
environment on top of a PyTorch CUDA base image, so duplication is plausible and must be measured.

Possible improvements:

- eliminate proven duplicate frameworks or CUDA libraries;
- remove build-only packages from the runtime layer;
- keep frequently changing application source in small top layers; and
- retain the AWS-side CodeBuild cache for rebuild speed.

Do not embed all GCS model artifacts into the image merely to avoid model downloads. That shifts
bytes from the model-preparation phase into the already-large image pull, makes releases larger,
and couples model updates to full image rebuilds. It should be considered only if measured total
startup time improves.

**Potential small-job saving:** Up to part of the measured 2m 19s.

**Large-job impact:** Low.

### Priority 6: Keep scale-to-zero; do not hold a GPU open

An always-warm GPU could eliminate much of the AWS Batch/EC2/image/model startup delay. It is not
recommended for the expected workload of approximately 15 bulk scans per month at around 10 hours
each.

That workload is roughly 150 useful GPU-hours per month. Keeping one GPU available continuously
would make it billable for roughly 720 hours per month. The measured six-minute AWS provisioning
delay across 15 monthly jobs is only about 1.5 hours of total delay, and fixed startup becomes less
than 1% of a 10-hour job.

Keep:

- Batch `min_vcpus=0`;
- desired vCPUs returning to zero;
- ephemeral EC2; and
- models loaded once per long-running job.

Reconsider a warm GPU only if the product later requires interactive small-job latency measured in
seconds or a few minutes.

## 9. Recommended order of work

```text
1. Add structured timing events
2. Complete Stockholm to Ohio migration
3. Rerun the identical 15-product baseline
4. Add bounded preparer I/O concurrency
5. Design and test validated incremental progress checkpoints for the FastAPI UI
6. Run a 100-to-500-image g4dn throughput benchmark
7. Add worker I/O prefetch and safe overlap
8. Benchmark g4dn, g5, and g6 with the exact same dataset
9. Choose the lowest-cost setup that meets the required images-per-minute target
10. Optimize the container image only if small-job startup is still important
```

Do not optimize Cloud Run cold starts, image pull, GPU type, model runtime, and worker concurrency
all at once. Each change needs a before/after measurement against the same dataset.

## 10. Recommended performance targets

Before calling the 10,000-image path capacity-proven, demonstrate:

| Metric | Target |
|---|---:|
| Representative dataset | At least 100 images; 500 preferred |
| Required sustained throughput for 10k in 10h | At least 16.67 terminal images/minute |
| Safety margin target | At least 20 terminal images/minute |
| Failed infrastructure items | 0 |
| Per-item terminal reconciliation | 100% |
| GPU idle after job | 0 instances |
| Model initialization count | Once per Batch attempt |
| Peak GPU memory | Below the safe device limit with measured headroom |

Use p50 and p95 item duration, not only an average. Record the workload mix, including how many
items were inpainted, skipped, blocked, and failed.

## 11. Measurement limitations

- This was one run, not a statistical sample.
- The request selected 15 products but produced 14 GPU items.
- The 99-image run had a different workload mix: 45 inpainted successes, 28 no-watermark skips,
  and 26 safety-gate blocks. Its faster aggregate throughput is not directly comparable to an
  inpainting-heavy workload.
- The 99-image preparer measurement is an aggregate execution duration. It does not isolate Cloud
  Run startup, Shopify download, image validation, GCS upload, database writes, or manifest write.
- Two launch attempts failed in `eu-north-1a`, although the failures added only seconds after Batch
  began trying to launch capacity.
- The current logs do not separate each model download, checksum, extraction, and import.
- The 14-item result mix may not represent a real 10,000-image store.
- The 10,000-image duration is a linear projection and must be replaced by a larger benchmark.
- Region migration effects remain unmeasured until the same test runs in Ohio.

## 12. Evidence sources

The timings were reconstructed from:

- saved FastAPI test responses:
  - `/tmp/wm-timing-create.json`
  - `/tmp/wm-timing-status.json`
  - `/tmp/wm-timing-estimate.json`
  - `/tmp/wm-timing-items.json`
- Cloud Run execution metadata:
  - `watermark-bulk-preparer-mwh59`
  - `watermark-aws-orchestrator-6lng2`
- AWS Batch job:
  - `dad7588b-dc91-4207-a879-8a542e3c57ce`
- Observed 99-image FastAPI responses:
  - `GET /api/v1/stores/2/watermark-scan-jobs/wm_944043e1ac3e4e49a6e6256b09bf81aa`
  - `GET /api/v1/stores/2/watermark-scan-jobs/wm_944043e1ac3e4e49a6e6256b09bf81aa/items`
- Cloud Run preparation execution:
  - `watermark-bulk-preparer-zgh2g`
- AWS Batch job:
  - `9ccb5444-faa4-4b61-86cd-e5f50053dd7b`
- EC2 instance:
  - `i-09e666890edc51dc9`
- CloudWatch worker stream:
  - `/aws/batch/watermark-staging-worker`
  - `worker/default/1ced1414536c47f486879f97561957ce`
- ECS task:
  - `eabe08880e6f424fa5d042fcc0ba4b66`
- EC2 instance:
  - `i-053112d2e146891d6`
- CloudWatch worker stream:
  - `/aws/batch/watermark-staging-worker`
  - `worker/default/eabe08880e6f424fa5d042fcc0ba4b66`
- Auto Scaling activities for:
  - `AWSBatch-watermark-staging-gpu-asg-f2d58834-796c-33bc-81ba-55845965b69c`

The `/tmp` files are local transient evidence. The durable value of this document is the recorded
identifiers, timestamps, derived durations, limitations, and recommended remeasurement procedure.
