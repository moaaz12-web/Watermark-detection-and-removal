# Watermark job metrics and timing-analysis runbook

Use this read-only runbook whenever a user supplies a `wm_...` job ID and asks about duration, per-stage metrics, bottlenecks, image counts, or a scale forecast. Do not change feature gates, scheduler state, Terraform, AWS Batch jobs, or application data while gathering metrics.

Read the [hybrid system agent guide](00-hybrid-watermark-system-agent-guide.md) first. Refresh cloud identity and never print passwords, bearer tokens, broad service environment dumps, or customer image contents.

## 1. Establish the application record

Set the identifiers and refresh identity:

```bash
export JOB_ID="wm_REPLACE_WITH_APPLICATION_JOB_ID"
export STORE_ID="2"
export GCP_PROJECT_ID="project-5909a738-2fdc-451e-ad6"
export GCP_REGION="us-central1"
export AWS_REGION="eu-north-1"
export AWS_BATCH_QUEUE="watermark-staging-gpu"

rtk proxy gcloud config get-value project
rtk proxy aws sts get-caller-identity --query '{Account:Account,Arn:Arn}' --output table
```

Stop if AWS is not account `154959837939` or the GCP project is unexpected. Through the authenticated FastAPI endpoint below, capture only the listed fields. Authenticate through the normal application login path without printing the token.

```text
GET /api/v1/stores/{STORE_ID}/watermark-scan-jobs/{JOB_ID}

job_id, status, artifact_profile, created_at, completed_at,
preparation_total_targets, preparation_processed_targets,
preparation_succeeded_targets, preparation_failed_targets,
total_items, terminal_items, stable_error_code
```

The application record is the user-visible source of truth. `total_items: 0` immediately after creation only means target freezing/preparation has not completed yet.

## 2. Resolve the AWS Batch job

The normal Batch name is `watermark-${JOB_ID}`. Search both terminal states and filter their JSON locally for that exact name:

```bash
for batch_status in SUCCEEDED FAILED; do
  rtk proxy aws batch list-jobs \
    --job-queue "${AWS_BATCH_QUEUE}" \
    --job-status "${batch_status}" \
    --region "${AWS_REGION}" \
    --max-results 100 \
    --output json
done
```

Then describe the returned provider ID:

```bash
export BATCH_JOB_ID="REPLACE_WITH_PROVIDER_JOB_ID"
rtk proxy aws batch describe-jobs \
  --jobs "${BATCH_JOB_ID}" \
  --region "${AWS_REGION}" \
  --query 'jobs[0].{status:status,reason:statusReason,createdAt:createdAt,startedAt:startedAt,stoppedAt:stoppedAt,jobDefinition:jobDefinition,exitCode:container.exitCode,logStream:container.logStreamName,image:container.image}' \
  --output json
```

Record the Batch timestamps, job definition, image digest, exit code, and log stream. `startedAt - createdAt` is AWS queue/GPU provisioning/container-start delay.

## 3. Capture worker timings

```bash
export WORKER_LOG_STREAM="REPLACE_WITH_CONTAINER_LOG_STREAM"
rtk proxy aws logs get-log-events \
  --log-group-name /aws/batch/watermark-staging-worker \
  --log-stream-name "${WORKER_LOG_STREAM}" \
  --region "${AWS_REGION}" \
  --limit 10000 \
  --output json
```

Parse JSON messages only. Use these events:

| Event | Meaning |
| --- | --- |
| `job_started` / `job_completed` | Worker wall-clock boundaries and durable output-marker evidence. |
| `gpu_acceptance_started` | GPU/CUDA availability proof. |
| `watermark_worker_item_timing` | One terminal timing record per item. |
| `watermark_worker_job_timing` | Aggregate worker timing, outcome counts, and N+1 queue depth. |

Report `workerWallMilliseconds`, `executorInitializationMilliseconds`, `itemPipelineMilliseconds`, `jobFinalizationMilliseconds`, outcome counts, and peak pending I/O depth. Aggregate item records by decision (`INPAINTED`, `NO_WATERMARK`, `BLOCKED`) and calculate average/p95/max for source read, decode, background removal, detection, inpainting, artifact writes, and marker writes.

Do not add per-item stage sums and call the result elapsed time. N+1 input/output overlap means stage work overlaps. `workerWallMilliseconds` is the worker's elapsed time.

## 4. Capture preparation timings

```bash
rtk proxy gcloud logging read \
  "resource.type=cloud_run_job AND resource.labels.job_name=watermark-bulk-preparer AND \"${JOB_ID}\"" \
  --project "${GCP_PROJECT_ID}" \
  --limit 1000 \
  --format=json
```

Aggregate `watermark_preparation_item_timing`: Shopify download, GCS stage, database, Pillow verification, validation, retry count, and total. Record the first and last such event. Their interval is approximate image-staging wall time.

Do not label the entire `created_at -> Batch createdAt` interval as image preparation. It also contains scheduler cadence, Cloud Run Job startup, target freezing, manifest publication, and AWS-orchestration handoff.

## 5. Produce the timeline

Convert AWS epoch milliseconds to UTC. Report:

```text
application created_at
-> first/last preparation timing event
-> Batch createdAt
-> Batch startedAt
-> worker job_started
-> worker job_completed
-> Batch stoppedAt
-> application completed_at
```

| Calculation | Label |
| --- | --- |
| `Batch createdAt - application created_at` | Preparation plus control-plane/orchestration handoff. |
| `last prep event - first prep event` | Approximate image-staging wall time. |
| `Batch startedAt - Batch createdAt` | AWS queue/GPU provisioning delay. |
| `job_completed - job_started` | Worker wall time. |
| `Batch stoppedAt - job_completed` | Batch shutdown tail. |
| `completed_at - Batch stoppedAt` | Reconciliation/output-validation delay. |
| `completed_at - created_at` | End-to-end user-visible duration. |

Use a compact UTC timeline table, then explain whether a long `PREPARING` interval was actual image work or fixed scheduler/Cloud Run overhead.

## 6. Forecast 10,000 images

Use only a comparable completed job and state its artifact profile and outcome mix. Label the result an estimate, not an SLA:

```text
steady worker cadence =
  (workerWall - executorInitialization - jobFinalization - modelPreparation) / item count

10,000 worker estimate = initialization + cadence * 10,000 + finalization
10,000 staging estimate = observed staging wall / observed item count * 10,000
end-to-end estimate = fixed control-plane overhead + staging + GPU provisioning + worker + reconciliation
```

Give a range and note the variables: inpainted/blocked mix, CDN latency/retries, `full` versus `essential` artifact profile, GPU provisioning, and model/image warmness.

## Example

The 99-image full-profile job `wm_39feeaf6a7f54607bc836a05975ec283` staged images in roughly 16 seconds but took 4m28s from application creation to Batch submission. The difference was scheduler/control-plane/Cloud Run handoff, not serial image download. Its worker wall time was about 10m22s, including about 3m21s of executor initialization. Make this distinction in all future status explanations and forecasts.
