# GPU acceptance checkpoint

This directory records the bounded W3 one-image acceptance run. It is a system-integration check,
not an accuracy or product-quality evaluation.

## Current gate

The acceptance target is one on-demand NVIDIA T4 attached to `n1-standard-4` in `us-central1-c`.
The earlier L4/G2 target was dropped because the same pipeline already ran successfully on a T4 in
Colab. On 2026-07-19 Google immediately denied the retried requests for both required limits:
`NVIDIA-T4-GPUS-per-project-region` in `us-central1` and `GPUS-ALL-REGIONS-per-project` remain at
`0`. No Batch GPU job has been submitted. Do not submit `batch-one-image.json` until both granted
values are at least one.

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

## Emergency stop

```bash
gcloud batch jobs cancel wm-gpu-smoke-one-20260719t090500z \
  --location=us-central1 \
  --project=project-5909a738-2fdc-451e-ad6
```

The Batch configuration has zero retries and a 90-minute task timeout. After cancellation, verify
that no Batch VM remains before assuming GPU billing has stopped.
