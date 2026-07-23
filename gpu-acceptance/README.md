# GPU acceptance checkpoint

This directory records the bounded W3 one-image acceptance run. It is a system-integration check,
not an accuracy or product-quality evaluation.

Last verified: 2026-07-24

## Current gate

The acceptance target is one on-demand NVIDIA T4 attached to `n1-standard-4` in `us-central1-c`.
The earlier L4/G2 target was dropped because the same pipeline already ran successfully on a T4 in
Colab. The client confirmed that billing account `01CFC5-E41BC1-082150` was upgraded to a paid
account on 2026-07-19. Google nevertheless denied repeated quota requests based on account/project
eligibility. A read-only check on 2026-07-24 still reports both required limits at zero:

- `NVIDIA-T4-GPUS-per-project-region` in `us-central1`: `0`.
- `GPUS-ALL-REGIONS-per-project`: `0`.

Every queried region also has standard T4 quota `0` for this project, so moving regions does not
bypass the gate. No Batch GPU job has been submitted. Do not submit `batch-one-image.json` until
both granted values are at least one.

The corrected CPU build `ee4d4eca-3e2f-4def-9a36-6d65cf31b8e6` passed and published:

```text
us-central1-docker.pkg.dev/project-5909a738-2fdc-451e-ad6/watermark-worker/watermark-worker@sha256:16da56ccf7095a84134e11899f44f5be5f71511d265de09975a8cca9edaa40a2
```

## Pinned inputs

- One-image manifest URI:
  `gs://project-5909a738-2fdc-451e-ad6-watermark-data/gpu_acceptance/gpu_smoke_one_20260719t090500z/input-manifest.json`
- One-image manifest generation: `1784452037999378`
- Five-image manifest URI:
  `gs://project-5909a738-2fdc-451e-ad6-watermark-data/gpu_acceptance/gpu_smoke_five_20260719t090500z/input-manifest.json`
- Five-image manifest generation: `1784452046042662`

`input-inventory.json` records the exact image generations and SHA-256 checksums. The manifests and
all inputs are private. The source images are intentionally low-quality setup fixtures from the
repository's ignored `images/` directory.

## Task 5 acceptance boundary

Pass the one-image run when all of the following are true:

- Batch reports `SUCCEEDED`, the container exits `0`, and the allocated device is an NVIDIA T4.
- CUDA is available and RMBG, combined YOLO/GroundingDINO/SAM 2 detection, and LaMa initialize
  without an import, checkpoint, CUDA, or memory failure.
- Structured lifecycle evidence shows initialized and released stages in the expected order.
- At least one detection record, detection mask, combined mask, annotation, item result, item
  `_COMPLETE.json`, output manifest, and job `_COMPLETE.json` are stored with generations.
- A final image is required only if the safety decision permits inpainting. A blocked or unverified
  decision must not produce a final image.
- Batch removes its VM after the job reaches a terminal state.

Do **not** assess whether boxes, masks, detections, or inpainting are visually accurate. Accuracy is
explicitly outside this setup milestone.

## Remaining Tasks 4-8

| Task | Current state | Completion |
| --- | --- | --- |
| 4. One-image Batch job | Configuration and inputs are ready; blocked by both zero GPU quotas. | Submit only after both quota values are at least one. |
| 5. One-image validation | Acceptance rules above are ready; no live run exists. | Validate model loading, pipeline/artifacts, generations, terminal state, and cleanup without judging accuracy. |
| 6. Five-image run | Five-image manifest is pinned; Batch submission is not yet prepared or run. | Run one bounded worker process and collect lifecycle/resource measurements. |
| 7. Interruption/resume | Hermetic resumability tests pass; no live GPU interruption test exists. | Interrupt after a completed item, resubmit, and prove safe skip/resume behavior. |
| 8. Evidence and closure | No live GPU evidence exists. | Record jobs/logs/digests/generations/cost, verify VM/disk cleanup, update docs, and close W3 if acceptance passes. |

## Emergency stop

```bash
gcloud batch jobs cancel wm-gpu-smoke-one-20260719t090500z \
  --location=us-central1 \
  --project=project-5909a738-2fdc-451e-ad6
```

The Batch configuration has zero retries and a 90-minute task timeout. After cancellation, verify
that no Batch VM remains before assuming GPU billing has stopped.
