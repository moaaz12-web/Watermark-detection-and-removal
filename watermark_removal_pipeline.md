# Watermark Detection, Masking, and Inpainting Pipeline

## Objective

The objective of this pipeline is to automatically detect and remove watermarks from ecommerce product images while minimizing damage to the actual product. The pipeline uses a multi-stage approach because watermarks can appear in many forms: text overlays, transparent logos, corner marks, repeated background patterns, brand stamps, or marks placed directly over the product.

The core idea is not to rely on a single method. Instead, the system combines classification, OCR, object detection, zero-shot detection, segmentation, mask refinement, inpainting, and post-processing checks.

The pipeline should only be used for images we own, control, or have permission to edit.

---

## High-Level Flow

```text
Input product image
↓
0. Watermark image classifier
↓
If confidently clean → skip
If watermark likely or uncertain → continue
↓
1. OCR text detection
↓
If OCR finds text → create OCR mask candidate
Do not stop here
↓
2. YOLO watermark detector
↓
If YOLO finds bbox → pass bbox to SAM → create mask candidate
↓
3. GroundingDINO fallback
↓
If YOLO fails or confidence is low → GroundingDINO bbox → SAM mask candidate
↓
4. Combine all candidate masks
↓
5. Clean and refine final mask
↓
6. Inpaint using LaMa
↓
7. Run post-inpaint residual check
↓
8. If watermark remains → second pass or manual review
↓
Output cleaned image
```

---

# Step 0: Watermark Image Classifier

## Purpose

The classifier is the first gate in the pipeline. It only answers one question:

```text
Does this image likely contain a watermark?
```

It does not return a bounding box, mask, location, or segmentation. It only returns a probability score.

Example output:

```json
{
  "watermark_score": 0.87,
  "prediction": "watermarked"
}
```

## What This Step Does

The classifier checks the whole image and predicts whether a watermark is present.

This helps avoid running expensive downstream models on images that are clearly clean.

For example, if we have 100,000 product images and only 20,000 contain watermarks, the classifier can prevent many clean images from going through OCR, YOLO, GroundingDINO, SAM, and LaMa.

## Important Logic

The classifier should be conservative. It should only skip the image if it is very confident that there is no watermark.

Recommended threshold logic:

```text
watermark_score < 0.15
→ confidently clean
→ skip image

0.15 <= watermark_score < 0.70
→ uncertain
→ continue pipeline

watermark_score >= 0.70
→ watermark likely
→ continue pipeline
```

The reason is that a false negative is dangerous. If the classifier says “no watermark” when the image actually has one, the image never reaches the detector, and the watermark remains.

So the classifier is not the final authority. It is just a cheap early filter.

## Output of This Step

Possible outcomes:

```text
Confidently clean
→ skip image

Watermark likely
→ continue

Uncertain
→ continue
```

---

# Step 1: OCR Text Detection

## Purpose

OCR is used to detect readable text-based watermarks.

Examples:

```text
example.com
AliExpress
Sample
Demo
Shop name
Brand watermark text
Phone number
Website URL
```

## What This Step Does

The OCR engine scans the image for text regions. If text is detected, the system creates a mask around the OCR polygons or bounding boxes.

Example:

```text
OCR detects text box:
x1=120, y1=50, x2=300, y2=90

Create mask over that region.
Dilate slightly.
Save as candidate mask.
```

## Why We Do Not Stop After OCR

OCR may only detect part of the watermark.

For example:

```text
Watermark contains:
- transparent logo
- website text
- diagonal pattern

OCR may detect:
- website text only
```

If we immediately inpaint after OCR and stop, the logo or transparent pattern may remain.

So OCR creates one candidate mask, but the pipeline continues to YOLO and possibly GroundingDINO.

## OCR Limitations

OCR is useful, but brittle.

It can fail on:

```text
transparent text
low contrast text
diagonal text
curved text
blurred text
stylized brand logos
repeated watermark patterns
non-text logos
very small text
watermarks blended into the product
```

So OCR should be treated as a helper, not the main solution.

## Output of This Step

If OCR finds text:

```text
OCR polygons / boxes
↓
OCR mask candidate
```

If OCR finds nothing:

```text
No OCR mask candidate
Continue pipeline
```

---

# Step 2: YOLO Watermark Detector

## Purpose

YOLO is the main trained detector for finding watermark locations.

Unlike the classifier, YOLO gives location information.

Example output:

```json
[
  {
    "label": "watermark",
    "confidence": 0.91,
    "bbox": [100, 40, 420, 120]
  }
]
```

## What This Step Does

YOLO scans the image and returns bounding boxes around likely watermark regions.

It is useful for:

```text
common watermark layouts
corner watermarks
center watermarks
repeated patterns
logos
text overlays
watermarks not detected by OCR
```

## Why YOLO Is Useful

The classifier only says whether a watermark exists.

YOLO says where it is.

That location is needed before we can generate a mask and inpaint.

## YOLO + SAM Flow

YOLO gives a bounding box, but a bounding box is not enough for high-quality inpainting because it may include too much background or product area.

So we pass the YOLO bounding box into SAM.

```text
YOLO bbox
↓
SAM segmentation
↓
Pixel-level mask
```

SAM uses the box as a prompt and tries to segment the object or region inside it.

## Output of This Step

If YOLO finds a watermark:

```text
YOLO bbox
↓
SAM mask candidate
```

If YOLO fails or confidence is low:

```text
Continue to GroundingDINO fallback
```

---

# Step 3: GroundingDINO Fallback

## Purpose

GroundingDINO is used as a zero-shot fallback detector.

It is helpful when YOLO misses the watermark or when we do not yet have enough training data for YOLO.

GroundingDINO can detect regions based on text prompts.

Example prompt:

```text
"watermark . logo . text overlay . transparent logo . brand mark"
```

## What This Step Does

GroundingDINO receives the image and text prompt. It tries to return bounding boxes for regions matching the prompt.

Example:

```text
Prompt:
"watermark . logo . text overlay . transparent logo . brand mark"

Output:
bbox around suspected transparent logo
```

## GroundingDINO + SAM Flow

GroundingDINO gives bounding boxes. SAM turns those boxes into masks.

```text
GroundingDINO prompt
↓
GroundingDINO bbox
↓
SAM segmentation
↓
GroundingDINO/SAM mask candidate
```

## When to Run GroundingDINO

GroundingDINO should run when:

```text
YOLO finds no boxes
YOLO confidence is low
YOLO detects only part of the watermark
OCR finds text but the classifier still thinks watermark remains
we want an additional fallback mask candidate
```

## Why It Is a Fallback

GroundingDINO is useful, but it may be slower and less predictable than a trained YOLO detector.

YOLO is better when trained on our actual watermark dataset.

GroundingDINO is better as a flexible backup when the watermark style is unknown.

## Output of This Step

If GroundingDINO finds regions:

```text
GroundingDINO bbox
↓
SAM mask candidate
```

If it finds nothing:

```text
No GroundingDINO mask candidate
Continue with available masks or send to review
```

---

# Step 4: Combine Candidate Masks

## Purpose

At this stage, we may have multiple mask candidates from different sources.

Possible masks:

```text
OCR mask
YOLO → SAM mask
GroundingDINO → SAM mask
```

The goal is to merge these into one final watermark mask.

## Why Mask Combination Matters

Different methods detect different parts of the watermark.

Example:

```text
OCR detects text
YOLO detects logo
GroundingDINO detects transparent overlay
```

If we only use one mask, part of the watermark may remain.

So we combine all valid masks.

## Mask Combination Logic

The basic logic is:

```text
final_mask = OCR mask OR YOLO/SAM mask OR GroundingDINO/SAM mask
```

This means any pixel detected by any method can be included in the final mask.

## Safety Consideration

Mask combination can become too aggressive. If the combined mask covers too much of the product, inpainting may damage the product.

So after combining masks, we should check:

```text
mask size relative to image
mask overlap with product region
number of disconnected components
confidence of each mask source
```

## Output of This Step

```text
Combined raw mask
```

This mask is not yet ready for inpainting. It needs cleanup.

---

# Step 5: Clean and Refine Final Mask

## Purpose

Raw masks are often messy. They may be too small, too large, noisy, jagged, or incomplete.

Mask cleanup prepares the mask for inpainting.

Good inpainting depends heavily on mask quality.

## What This Step Does

The cleanup stage can include:

```text
Remove tiny noise
Merge nearby mask regions
Fill small holes
Dilate the mask slightly
Smooth edges
Feather mask edges if needed
Restrict mask size if it becomes too large
```

## Common Cleanup Operations

### Remove Small Components

Small random detections should be removed.

Example:

```text
Tiny 5-pixel dot detected as watermark
→ remove it
```

### Merge Nearby Regions

If text letters are detected as separate components, we merge them.

Example:

```text
W A T E R M A R K
Each letter has separate mask
→ merge into one continuous text mask
```

### Dilate Slightly

Watermark masks should usually be expanded slightly so the edges are fully removed.

Example:

```text
Detected text mask
↓
Dilate by 3–10 pixels
↓
Covers text edges better
```

The dilation amount should depend on image resolution.

Small images need smaller dilation. Large images can tolerate more.

### Fill Holes

If the mask has gaps inside letters or logos, those holes should be filled.

Example:

```text
Logo mask has holes
→ fill holes
→ cleaner inpainting
```

### Feather Edges

Feathering can help the inpainted area blend naturally into the surrounding pixels.

This is useful for soft transparent watermarks.

## Product Safety Check

If possible, we should use a product foreground mask to check whether the watermark mask overlaps the actual product.

Possible logic:

```text
If mask is mostly background:
    safe to inpaint

If mask slightly overlaps product:
    inpaint carefully

If mask heavily overlaps product:
    flag as hard case
```

This matters because watermark removal over the product is more dangerous than watermark removal in the background.

## Output of This Step

```text
Clean final mask ready for inpainting
```

---

# Step 6: Inpaint Using LaMa

## Purpose

LaMa removes the masked watermark region and fills it with plausible background/product pixels.

Input:

```text
Original image
Clean final mask
```

Output:

```text
Inpainted image
```

## What This Step Does

LaMa looks at the surrounding pixels and reconstructs the masked area.

It works especially well when the watermark is on:

```text
plain background
white background
simple texture
corner area
non-product region
```

It is harder when the watermark is on:

```text
product edges
product labels
complex fabric texture
skin-like texture
transparent objects
reflective surfaces
detailed packaging
```

## Why Mask Quality Is Critical

Bad mask:

```text
too small → watermark edges remain
too large → useful product detail gets destroyed
too noisy → image gets artifacts
```

Good mask:

```text
covers the full watermark
does not cover unnecessary product regions
has smooth enough edges
```

## Output of This Step

```text
First-pass cleaned image
```

This image still needs to be checked.

---

# Step 7: Post-Inpaint Residual Check

## Purpose

After inpainting, we need to verify whether the watermark was actually removed.

This is important because the first pass may not remove everything.

Examples of leftover issues:

```text
faint watermark shadow remains
logo removed but text remains
text removed but transparent pattern remains
inpainting artifact looks like watermark
watermark edges remain
```

## What This Step Does

Run the detection pipeline again on the inpainted image.

At minimum:

```text
Run classifier again
```

Optional deeper checks:

```text
Run OCR again
Run YOLO again
Run GroundingDINO again
Compare before/after watermark score
Check if suspicious regions remain
```

## Recommended Logic

```text
If post_inpaint_watermark_score < clean_threshold:
    accept image

If score is still high:
    run second-pass detection and inpainting

If no mask can be found but classifier still says watermark likely:
    flag for manual review
```

## Output of This Step

Possible outcomes:

```text
Clean
Needs second pass
Needs manual review
Failed
```

---

# Step 8: Second-Pass Inpainting or Manual Review

## Purpose

If watermark remains after the first pass, run the pipeline again on the inpainted image.

This is called a second pass.

## Why Second Pass Helps

The first pass may remove the obvious watermark part. After that, the remaining watermark may become easier to detect.

Example:

```text
First pass removes big text
Second pass detects faint logo behind it
```

## Second-Pass Logic

```text
Inpainted image
↓
Classifier
↓
If clean → accept
↓
If watermark likely → OCR / YOLO / GroundingDINO again
↓
Generate residual mask
↓
Clean residual mask
↓
LaMa again
```

## Pass Limit

Do not run infinite loops.

Recommended:

```text
max_passes = 2
```

Maybe:

```text
max_passes = 3
```

for offline batch processing, but this increases the risk of damaging the product image.

## When to Send to Manual Review

Send image to hard-case/manual review if:

```text
classifier still detects watermark after max passes
no mask can be found
mask overlaps too much of the product
inpainting damages the product
watermark is directly over important product detail
detectors disagree strongly
final quality score is low
```

## Output of This Step

```text
Accepted cleaned image
or
Manual review image
or
Failed image
```

---

# Final Pipeline With Detailed Decision Flow

```text
Input image
↓
0. Watermark classifier
   - Output: watermark_score
   - If confidently clean → skip
   - Else → continue
↓
1. OCR
   - Detect readable text
   - Convert OCR polygons to mask
   - Save OCR mask candidate
   - Do not stop here
↓
2. YOLO watermark detector
   - Detect watermark bounding boxes
   - If boxes found → send boxes to SAM
   - Save YOLO/SAM mask candidate
↓
3. GroundingDINO fallback
   - Run if YOLO fails, YOLO confidence is low, or extra detection is needed
   - Use prompt:
     "watermark . logo . text overlay . transparent logo . brand mark"
   - Send GroundingDINO boxes to SAM
   - Save GroundingDINO/SAM mask candidate
↓
4. Combine masks
   - Merge OCR mask + YOLO/SAM mask + GroundingDINO/SAM mask
   - Remove invalid masks
   - Avoid overly large masks
↓
5. Clean mask
   - Remove noise
   - Merge nearby components
   - Fill holes
   - Dilate slightly
   - Smooth/feather edges
   - Optionally check product overlap
↓
6. Inpaint
   - Use LaMa / IOPaint
   - Generate first-pass cleaned image
↓
7. Residual check
   - Run classifier again
   - Optionally run OCR/YOLO/GroundingDINO again
   - Decide whether image is clean
↓
8. Second pass or review
   - If watermark remains → run second-pass masking/inpainting
   - If still unresolved → manual review / hard-case bucket
↓
Output final image
```

---

# Component Responsibilities

## Classifier

The classifier answers:

```text
Should this image enter the expensive pipeline?
```

It does not answer where the watermark is.

## OCR

OCR answers:

```text
Is there readable watermark text?
```

It is useful for text watermarks, but brittle.

## YOLO

YOLO answers:

```text
Where is the likely watermark?
```

It is the main trained detector once we have enough labeled data.

## GroundingDINO

GroundingDINO answers:

```text
Can I find watermark-like regions using a text prompt?
```

It is a zero-shot fallback when YOLO misses something.

## SAM

SAM answers:

```text
Given a box or point, what are the exact pixels of this region?
```

It converts bounding boxes into pixel masks.

## Mask Cleanup

Mask cleanup answers:

```text
Is this mask safe and clean enough for inpainting?
```

It improves the raw mask before LaMa.

## LaMa

LaMa answers:

```text
How do I fill the masked region naturally?
```

It performs the actual inpainting.

## Residual Check

Residual check answers:

```text
Did we actually remove the watermark?
```

It prevents accepting images where the watermark is still visible.

---

# Recommended MVP Version

For the first working version, keep it simpler:

```text
0. Classifier gate
1. OCR mask candidate
2. YOLO bbox → SAM mask candidate
3. GroundingDINO fallback → SAM mask candidate
4. Combine masks
5. Clean mask
6. LaMa inpaint
7. Classifier re-check
8. Second pass or review
```

This gives us a practical balance between automation, accuracy, and complexity.

---

# Important Design Principle

The pipeline should not be a strict one-path system.

Bad design:

```text
OCR found something
↓
inpaint
↓
done
```

Better design:

```text
OCR finds one signal
YOLO finds another signal
GroundingDINO finds another signal
SAM improves masks
All masks are merged
Then inpainting happens
Then we check if anything remains
```

This is more robust because watermark removal is not just a detection problem. It is a detection, segmentation, masking, inpainting, and quality-control problem.

---

# Final Summary

The final pipeline should work like this:

```text
Classifier decides whether the image should be processed.

OCR finds obvious readable text.

YOLO finds trained/common watermark regions.

GroundingDINO finds watermark-like regions when YOLO misses them.

SAM converts boxes into accurate masks.

Mask cleanup makes the mask safe for inpainting.

LaMa removes the masked watermark.

Residual check verifies whether the watermark is gone.

Second pass handles leftovers.

Manual review catches hard cases.
```

This gives us a practical and extensible architecture for ecommerce product watermark removal.
