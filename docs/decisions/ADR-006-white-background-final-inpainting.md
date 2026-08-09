# ADR-006: Inpaint the white-background composite for watermark outputs

## Status

Proposed

## Date

2026-08-09

## Context

The notebook pipeline detects watermark regions on the RMBG white-background composite and
inpaints that same composite. The production executor previously detected on the composite but
sent the untouched source image to LaMa, so successful production outputs retained the original
background instead of matching the notebook output.

## Decision

The production worker will use the RMBG white-background composite as the LaMa input for all v1
and v2 single-image and bulk configurations.

- GroundingDINO, YOLO, and SAM2 continue to use the white-background composite.
- EasyOCR in v2 continues to use the original image.
- The safety gate continues to compare proposed masks with the RMBG foreground mask.
- A successful final image will therefore contain the inpainted white background.
- A blocked outcome will publish the unchanged white-background composite as
  `background_removed/final.png`, while remaining `BLOCKED_BY_SAFETY_GATE` and skipping LaMa.
- No-watermark outcomes remain skipped and do not publish a final image.

## Consequences

- The shared executor must retain the detector image until safety evaluation and inpainting finish.
- The four checked-in pipeline configs declare `white_background` as both the detector final-image
  input and LaMa input.
- Blocked results now expose a background-removed final artifact without being reported as
  successfully inpainted.
- This changes frozen pipeline behavior and requires a new config publication and acceptance check
  before deployment. The live deployment remains unchanged until that process is completed.

## Alternatives considered

### Keep the original image as the LaMa input

Rejected for this change because it does not match the requested notebook behavior or the desired
white-background output.

### Add a second production executor path

Rejected. The shared executor has one image-routing point, so changing it once keeps single-image
and bulk processing consistent.
