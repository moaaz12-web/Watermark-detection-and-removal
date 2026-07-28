# ADR-005: Keep all bulk GPU models resident and process one item end-to-end

## Status

Accepted

## Date

2026-07-28

## Context

The first 10,000-image design used three complete manifest passes: RMBG, detection/SAM2, and LaMa.
That design avoided holding every model on the GPU but required stage-level GCS checkpoints,
intermediate GCS reads, and more complicated restart logic.

The owner subsequently ran the complete enabled model set on a Google Colab NVIDIA T4 and confirmed
that it fits. The initial AWS Batch instance, `g4dn.xlarge`, also uses one 16-GiB NVIDIA T4. Exact
benchmark measurements still need to be retained with rollout evidence, and AWS must validate
whole-device VRAM and host RAM under representative and maximum-sized images.

The worker still must process as many as 10,000 items without retaining decoded images or
item-specific tensors in proportion to manifest size.

## Decision

- Apply this lifecycle to the new `watermark-bulk-v1` pipeline. Do not silently change the accepted
  five-image `watermark-v1` pipeline while bulk is being developed and rolled out.
- Download and checksum-verify every enabled model artifact once per AWS Batch attempt.
- Initialize RMBG, GroundingDINO, YOLO, SAM2, and LaMa once before processing manifest items.
- Keep those models resident until the worker exits.
- Process one manifest item end-to-end before reading and decoding the next item.
- Release item-specific images, masks, detections, tensors, and byte buffers after publishing that
  item's terminal result.
- Use the existing compatible per-item terminal completion marker as the retry boundary.
- On retry, adopt compatible terminal items and rerun incomplete items end-to-end.
- Keep intermediate artifacts for diagnostics where useful, but do not add stage-level completion
  markers or stage-to-stage GCS reads.
- Publish the self-contained output manifest and job completion marker after every requested item
  has one compatible terminal outcome.

## Alternatives considered

### Three manifest-wide model passes

Rejected for the initial bulk implementation after the T4 feasibility check. It lowers simultaneous
VRAM use but adds stage checkpoint contracts, additional GCS operations, and more complex retry
logic.

### Reload all models for every image

Rejected. Repeated model initialization would waste most of the benefit of a long-lived Batch
attempt and materially reduce throughput.

### One Batch job per image

Rejected. It would repeatedly provision GPU capacity, download models, and create unnecessary AWS
Batch control-plane load.

### Permanently running GPU service

Rejected for the current workload. AWS Batch already supports the several-hour execution, while
scale-to-zero avoids idle GPU cost between bulk scans.

## Consequences

- Worker control flow becomes a direct, bounded per-item loop.
- Model initialization happens once per attempt rather than once per item or once per manifest
  pass.
- One image no longer needs GCS as a handoff boundary between its model stages.
- An interruption can repeat work for only the currently incomplete item; completed items remain
  reusable.
- Steady GPU memory is higher than the three-pass design.
- AWS rollout gates must record `nvidia-smi` peak memory, PyTorch allocated/reserved peaks, host RAM,
  supported image dimensions, and memory stability across 5, 100, 500, 1,000, and 10,000 items.
- If representative AWS testing does not retain safe T4 headroom, this ADR must be superseded by a
  staged-residency design or a larger GPU instance decision.
