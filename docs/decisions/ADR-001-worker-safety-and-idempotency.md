# ADR-001: Fail closed and pin worker inputs and outputs

## Status

Accepted

## Date

2026-07-17

## Context

The notebook combines RMBG preprocessing, detection, a product-overlap safety gate, and LaMa inpainting. Its white-background preprocessing image can be mistaken for an untouched final result, and a missing or unreliable product mask can leave the safety gate unable to assess a proposed removal.

Google Cloud Batch can retry a task after interruption. Cloud Storage object names are mutable references: a new generation can replace the live object at the same name. A completion marker that identifies only a path can therefore incorrectly reuse output from a different source image or manifest.

## Decision

- Treat the source image as the sole final-inpainting input. RMBG output is preprocessing/diagnostic data only.
- Fail closed: when the product-overlap gate is unavailable, unreliable, or blocks a mask, do not run LaMa and do not publish a `finalUri`.
- Require a GCS generation and SHA-256 checksum for the input manifest and every source image. The worker reads exactly those generations.
- Use per-item `_COMPLETE.json` markers for all terminal item outcomes and a job-level `_COMPLETE.json` marker for a complete output manifest.
- Create artifacts and completion markers with Cloud Storage generation preconditions. A retry may reuse an object only after all immutable identities and checksums match.
- Exit `0` when every requested item has a terminal recorded outcome and the job completion marker exists. Retry only explicitly classified transient worker failures.

## Alternatives considered

### Fail open when RMBG or the safety gate is unavailable

Rejected. The system would inpaint a product despite lacking the evidence required to distinguish a watermark from product content.

### Publish the white-background preprocessing image for clean or blocked items

Rejected. It silently changes an image in a path intended to represent an unedited or non-watermark result.

### Identify inputs and completion markers by GCS object name only

Rejected. A later object generation can replace the live object at the same name, so a retry could reuse output for a different input.

### Retry every non-zero worker exit code

Rejected. Invalid manifests, missing models, and permission failures are deterministic and waste GPU capacity when retried.

## Consequences

- Some images are blocked rather than automatically edited; this is deliberate and must be visible to users/operators through item status and review artifacts.
- FastAPI and the orchestrator must pass pinned GCS generations, validate job completion markers, and treat output manifests as untrusted until validation succeeds.
- The worker requires typed contracts for item outcomes, input identity, marker compatibility, and retry classification.
- The private evaluation set must include high-risk negative examples and reject unapproved product alteration.
