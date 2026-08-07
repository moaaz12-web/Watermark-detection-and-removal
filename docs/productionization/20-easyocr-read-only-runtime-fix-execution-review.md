# EasyOCR Read-Only Runtime Fix: Execution Review

Date: 2026-08-07

Repository: `/home/maaz/Desktop/watermarkPoc`

Branch: `watermark_pipeline`

Status: **Implemented, committed, built, and deployed. The controlled acceptance was safely blocked by AWS GPU capacity before the worker started. The system was returned to development-safe mode.**

This document is an execution record for a reviewing orchestrator. The authoritative incident
specification remains [the EasyOCR handoff](19-easyocr-read-only-runtime-fix-handoff.md), and the
operator procedures remain the [controlled E2E runbook](07-a10-controlled-e2e-operator-runbook.md)
and [worker image deployment runbook](aws-worker-image-deployment-and-gpu-acceptance-runbook.md).

## 1. Incident and required outcome

The previous controlled acceptance failed before the worker pipeline could process an image:

1. `easyocr.Reader` attempted to create its default user-network directory under the read-only
   container home, `/home/watermark-worker/.EasyOCR/user_network`.
2. Executor initialization raised before `outcome` existed. The cleanup `finally` block then raised
   `UnboundLocalError`, masking the original EasyOCR `OSError`.

The requested fix was deliberately limited to those two root causes. The read-only root
filesystem, IAM permissions, persistent storage, download behavior, and cleanup behavior were
left unchanged.

## 2. Source changes

### EasyOCR scratch directory

In [`detection.py`](../../src/watermark_worker/gpu_runtime/detection.py), the EasyOCR constructor
now receives:

```python
user_network_directory=str(
    artifacts.easyocr.runtime_path.parent / "easyocr-user-network"
)
```

The existing settings remain intact:

- `download_enabled=False` remains enforced.
- `model_storage_directory` remains the prepared EasyOCR artifact directory.
- The new directory is adjacent to the artifact and therefore scratch-backed, not inside the
  artifact and not under `/home/watermark-worker`.

### Safe cleanup when initialization fails

In [`cli.py`](../../src/watermark_worker/cli.py), immediately before the relevant `try`, the code
now initializes:

```python
outcome: PipelineOutcome | None = None
```

This allows the existing cleanup path to run without masking an executor initialization failure.
No cleanup was removed and no initialization error is ignored.

### Focused tests added

- [`tests/unit/test_gpu_runtime_provider.py`](../../tests/unit/test_gpu_runtime_provider.py)
  asserts the EasyOCR constructor receives the explicit scratch-backed user-network directory,
  keeps downloads disabled, and does not write under the container home.
- [`tests/integration/test_cli_local.py`](../../tests/integration/test_cli_local.py) uses an
  executor whose `initialize()` raises before an outcome exists. It asserts the original
  `OSError` is reported and no `UnboundLocalError` masks it.

## 3. TDD and local verification evidence

The focused tests were run before implementation to establish the required red state:

- EasyOCR test failed with `KeyError: 'user_network_directory'`.
- Initialization-failure test observed `UnboundLocalError` instead of the original `OSError`.

After the two source changes:

- Focused tests: `2 passed in 2.88s`.
- Full pytest: `277 passed, 3 skipped`.
- `uv lock --check`: passed.
- Ruff on all four changed source/test files: passed.
- `git diff --check` for the implementation work: passed.
- Terraform formatting and validation: passed.

The three skipped tests are the repository's existing opt-in container/GCS integration tests.

The following repository-wide quality failures were pre-existing and were not mixed into this
fix:

- Ruff: four unrelated `E501` failures in `tests/unit/test_aws_batch_terraform.py`.
- Mypy: one unrelated error at `src/watermark_worker/cli.py:331` involving `float + None`.

## 4. Git review

Implementation commit:

```text
434180db77063abdb92550021ef9f7268cd74756
fix: support EasyOCR on read-only workers
```

The commit contains exactly these four files:

- `src/watermark_worker/cli.py`
- `src/watermark_worker/gpu_runtime/detection.py`
- `tests/integration/test_cli_local.py`
- `tests/unit/test_gpu_runtime_provider.py`

The commit check passed and no secrets or generated files were included. Two productionization
documents were already staged before this work began and were intentionally excluded from the
implementation commit:

- `docs/productionization/19-easyocr-read-only-runtime-fix-agent-prompt.md`
- `docs/productionization/19-easyocr-read-only-runtime-fix-handoff.md`

## 5. Build and immutable release

Exactly one worker image build was started using the repository helper.

- CodeBuild project: `watermark-staging-worker-image-build`
- Build ID:
  `watermark-staging-worker-image-build:534a0ed5-bdd6-4973-8577-20884a9d071b`
- Build status: `SUCCEEDED`
- Build time: `2026-08-07T13:16:09Z` to `2026-08-07T13:25:47Z`
- ECR release tag: `worker-20260807-easyocr1`
- Immutable digest:
  `sha256:6045dfff562c8a91e6f21cc991d596b8bc2f3ed3ca6fc054965349ecf053cae4`

The tag was checked before use and the deployed Batch definitions reference the digest, not the
mutable tag.

## 6. Cloud preflight and deployment

The refreshed deployment identities and locations were:

- AWS account: `154959837939`
- AWS region: `eu-north-1`
- GCP project: `project-5909a738-2fdc-451e-ad6`
- GCP region: `us-central1`

Before image deployment, the intended submission and Batch gates were closed, the scheduler was
paused, no active Batch jobs existed, and no GPU instances existed. The existing least-privilege
`watermark-v2/` GCS viewer binding was present and preserved.

### Image deployment plan

The reviewed image plan contained:

- `0 to add`
- `8 to change`
- `0 to destroy`

The changes were the six expected worker job-definition revisions plus the derived submission
Lambda policy/function references. There were no replacements, region changes, capacity changes,
or broad IAM changes. The exact saved plan applied successfully:

```text
Apply complete! Resources: 0 added, 8 changed, 0 destroyed.
```

The active definitions included:

- `watermark-staging-worker:8`
- `watermark-staging-worker-bulk:5`
- `watermark-staging-worker-v2:3`
- `watermark-staging-worker-bulk-v2:3`
- `watermark-staging-worker-v2-external:3`
- `watermark-staging-worker-bulk-v2-external:3`

All six were verified against the new immutable digest.

## 7. One controlled public acceptance

The repository's prescribed `images/a10-source.png` was absent. No debug collage was used. One
existing ordinary watermarked product PNG outside the repository was visually inspected and used
directly:

```text
/home/maaz/Documents/Watermarked image/微信图片_20260508211436_87_1.png
PNG, 1000 x 1000, RGB
```

The temporary test window was opened in the required order:

1. Terraform enabled AWS submission and the Batch queue/compute gate.
2. FastAPI enabled only `WATERMARK_FEATURE_ENABLED` and
   `WATERMARK_ASSET_UPLOAD_ENABLED`.
3. `WATERMARK_BULK_FEATURE_ENABLED` remained `false`.
4. The scheduler was resumed last.

The public application flow was used. The internal AWS submission API was not called directly.

Exactly one asset and one job were created:

- Asset ID: `757`
- Application job ID: `wm_670e6ae074004d59b0875de5f8724f80`
- Idempotency key:
  `easyocr-readonly-20260807T134645Z`
- Initial application status: `QUEUED`

The application progressed through `ORCHESTRATING` and `PROVISIONING`. The authenticated control
plane submitted exactly one AWS provider job:

- Provider job ID: `db2032f3-85eb-42fe-b526-afb32dea6d09`
- Provider job name: `watermark-wm_670e6ae074004d59b0875de5f8724f80`
- Batch definition:
  `arn:aws:batch:eu-north-1:154959837939:job-definition/watermark-staging-worker-v2-external:3`
- Image: the new immutable digest above
- `readonlyRootFilesystem`: `true`

### Acceptance blocker

The provider job stayed `RUNNABLE`. The managed fallback environment raised desired capacity to
four vCPUs, but no EC2 instance could launch. Auto Scaling activity reported repeated
`InsufficientInstanceCapacity` for `g4dn.xlarge` in eu-north-1a and eu-north-1b.

The runbook forbids changing capacity, changing instance types/regions, or submitting a second
job for this attempt. Therefore the acceptance was not retried. After the public feature was
closed and the scheduler paused, the exact provider job was terminated with the capacity-block
reason and reached terminal `FAILED`.

The application was then reconciled by one safe internal control-plane execution,
`watermark-control-plane-w4rc7`, with all public and AWS gates already closed. The final
application status was:

```text
FAILED
stable_error_code=AWS_BATCH_FAILED
total_items=1
terminal_items=0
```

The worker never reached `STARTING` or `RUNNING`, so it produced no CloudWatch log stream and no
result image. Consequently, the forbidden EasyOCR/`UnboundLocalError` strings were not observable
in a worker log for this attempt, and output-image validation was not applicable. The deployment
and code fix remain ready for a future acceptance when regional GPU capacity is available.

## 8. Cleanup and final safety proof

Cleanup followed the required order:

1. FastAPI public watermark and upload flags were set to `false`.
2. The scheduler was paused.
3. AWS submission and the Batch queue were closed.
4. The exact provider job was terminated after the queue was closed.
5. Managed capacity was allowed to settle.
6. A final internal reconciliation marked the application job failed.

The first shutdown Terraform plan included a runtime fallback desired-capacity change. AWS rejected
that change because managed Batch environments cannot be manually scaled down while attached to a
queue. The runbook queue-detach action was used to close the queue, the exact provider job was
terminated, and a fresh plan was generated. The fresh safe plan contained only the remaining
submission Lambda gate update:

- `0 to add`
- `1 to change`
- `0 to destroy`

It applied successfully. The final Terraform plan returned exit code `0` with `No changes`.

Final verified state:

- FastAPI ordinary feature: `false`
- FastAPI asset upload: `false`
- FastAPI bulk feature: `false`
- Control-plane watermark flags: no explicit enable flags, effective safe defaults
- Scheduler: `PAUSED`
- AWS submission: `false`
- Batch queue `watermark-staging-gpu`: `DISABLED / VALID`
- Main compute environment `watermark-staging-gpu`: `DISABLED / VALID`
- Main compute min/desired vCPUs: `0 / 0`
- Fallback compute environment: `ENABLED / VALID` by the Terraform design, with min/desired
  vCPUs `0 / 0`; the disabled queue is the safety gate
- Active Batch jobs in `SUBMITTED`, `PENDING`, `RUNNABLE`, `STARTING`, and `RUNNING`: `0`
- Auto Scaling desired capacity for both Batch groups: `0`
- GPU-family EC2 instances: `0`
- Final Terraform drift: none

Temporary login responses, bearer-token files, API responses, Terraform plans, and temporary
acceptance files were removed. No password, token, or cloud response payload was committed.

## 9. Review conclusion

The source fix is minimal and addresses both observed root causes. Local tests prove the new
EasyOCR constructor contract and the unmasked initialization failure path. The immutable image
was built and deployed to the expected Batch definitions.

The cloud acceptance is **not accepted** because AWS had no usable GPU capacity in the live region.
This is an infrastructure-capacity blocker, not evidence that the EasyOCR fix failed. A future
attempt should refresh capacity and run a new controlled acceptance under the existing runbook;
it must use a new acceptance attempt and must not reuse this attempt's idempotency key.
