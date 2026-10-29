# Bulk v2 finalization fix release

Date: 2026-10-29. Source commit: `c3104e8`.

## Cause and change

Job `wm_0fd22da6e7914370bc7fb6dd650a8956` finished its 20,796 image results but
remained RUNNING during sequential result validation. The deployed worker image
`sha256:55dd20ff3d6cb491b8a65b7d53ddef81fcbe6d8e20b24109aa188ee0a81f6dd0`
only enabled `entries_prevalidated` for `watermark-bulk-v1`. The v2 bulk pipeline
therefore re-read every item marker and result twice during finalization.

Extend that existing optimization to `watermark-bulk-v2`. Item write/reuse
validation, create-only output writes, job completion markers, and small-job
validation remain in place. No backend API or database changes are required.

## Verification

- Reproduced before the fix: the v2 bulk regression case performed 20 successful
  item-record reads for five images instead of zero; the other three pipeline
  cases passed.
- After the fix: 282 tests passed, 3 optional container/live-GCS tests skipped.
- Ruff, mypy, `uv lock --check`, and `git diff --check` passed.
- The regression test verifies both bulk versions avoid repeated result reads,
  both small versions retain them, and a batch containing a corrupt image still
  produces correct counts and a job completion marker.

## Release preparation

- AWS account: `154959837939`; region: `eu-north-1`.
- CodeBuild build: `watermark-staging-worker-image-build:b1e9ea41-bdc7-49bb-a185-cce842ee7a01`.
- ECR tag: `worker-20261029-c3104e8`.
- Build status: SUCCEEDED, including the container import smoke check.
- Immutable digest: `sha256:c79699d9e3bd38a2ffa88c45df7e2f3e95c911f186fd059f8a677dbeaeed65f7`.
- ECR publication verified at 2026-10-29 16:47 Pakistan time.
- Deployment and live acceptance: pending; this change is not yet live.

The user explicitly requested leaving the current worker running. Do not cancel
or restart Batch job `08791964-dc96-46c0-adea-fe978b18bf2a` to release this fix.

After active work finishes, follow the
[worker image deployment runbook](aws-worker-image-deployment-and-gpu-acceptance-runbook.md).
Refresh live state first, verify the build succeeded, resolve its immutable
digest, review the Terraform plan, and run the required controlled acceptance.
Preserve the previous digest above for rollback. Do not assume a backend push
deploys this separately built AWS worker image.
