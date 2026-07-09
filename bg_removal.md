CODE TO USE FOR BACKGROUND REMOVAL

!pip install -qr https://huggingface.co/briaai/RMBG-1.4/resolve/main/requirements.txt


!pip uninstall -y transformers
!pip install -q "transformers==4.39.3" "huggingface_hub" "scikit-image"


from pathlib import Path
from PIL import Image
import matplotlib.pyplot as plt


# -----------------------------
# Paste your file path here
# -----------------------------
IMAGE_PATH = Path(
    "/content/微信图片_20260508221452_145_1.png"
)


# -----------------------------
# Helper
# -----------------------------
def show_image_on_white_background(image_path: Path):
    image_path = Path(image_path)

    if not image_path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    image = Image.open(image_path).convert("RGBA")

    white_background = Image.new(
        "RGBA",
        image.size,
        (255, 255, 255, 255)
    )

    white_background.alpha_composite(image)

    final_image = white_background.convert("RGB")

    plt.figure(figsize=(8, 8))
    plt.imshow(final_image)
    plt.title(image_path.name)
    plt.axis("off")
    plt.show()


# -----------------------------
# Display image
# -----------------------------
show_image_on_white_background(IMAGE_PATH)


_____________

import os
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

import torch
import torch.nn.functional as F
from transformers import AutoModelForImageSegmentation
from torchvision.transforms.functional import normalize
from tqdm import tqdm


# -----------------------------
# Config
# -----------------------------
INPUT_ROOT_DIR = Path(
    "/content/drive/MyDrive/infringement_project/Product Images with Watermarks"
)

OUTPUT_ROOT_DIR = Path(
    "/content/drive/MyDrive/infringement_project/Processed Product Images"
)

MODEL_NAME = "briaai/RMBG-1.4"
MODEL_INPUT_SIZE = [1024, 1024]

WHITE_JPG_QUALITY = 90

SUPPORTED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"
}


# -----------------------------
# Device
# -----------------------------
device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
print(f"Using device: {device}")


# -----------------------------
# Load model once
# -----------------------------
model = AutoModelForImageSegmentation.from_pretrained(
    MODEL_NAME,
    trust_remote_code=True
)

model.to(device)
model.eval()


# -----------------------------
# Preprocess image
# -----------------------------
def preprocess_image(image: np.ndarray, model_input_size: list) -> torch.Tensor:
    if len(image.shape) < 3:
        image = image[:, :, np.newaxis]

    # Grayscale to RGB
    if image.shape[2] == 1:
        image = np.repeat(image, 3, axis=2)

    # RGBA to RGB
    if image.shape[2] == 4:
        image = image[:, :, :3]

    image_tensor = torch.tensor(image, dtype=torch.float32).permute(2, 0, 1)
    image_tensor = image_tensor.unsqueeze(0)

    image_tensor = F.interpolate(
        image_tensor,
        size=model_input_size,
        mode="bilinear",
        align_corners=False
    )

    image_tensor = image_tensor / 255.0

    image_tensor = normalize(
        image_tensor,
        mean=[0.5, 0.5, 0.5],
        std=[1.0, 1.0, 1.0]
    )

    return image_tensor


# -----------------------------
# Extract model output
# -----------------------------
def get_model_output_tensor(model_output):
    if isinstance(model_output, torch.Tensor):
        return model_output

    if isinstance(model_output, (list, tuple)):
        output = model_output[0]

        if isinstance(output, (list, tuple)):
            output = output[0]

        return output

    if hasattr(model_output, "logits"):
        return model_output.logits

    raise TypeError(f"Unsupported model output type: {type(model_output)}")


# -----------------------------
# Postprocess output mask
# -----------------------------
def postprocess_image(result: torch.Tensor, original_size: tuple) -> np.ndarray:
    result = result.detach()

    # Remove extra dimensions safely
    while result.ndim > 4:
        result = result.squeeze(0)

    if result.ndim == 4:
        # Shape: [B, C, H, W]
        result = result[0, 0]

    elif result.ndim == 3:
        # Shape: [C, H, W]
        result = result[0]

    elif result.ndim == 2:
        # Shape: [H, W]
        pass

    else:
        raise ValueError(f"Unexpected result shape: {result.shape}")

    # Convert to [B, C, H, W] for interpolation
    result = result.unsqueeze(0).unsqueeze(0)

    result = F.interpolate(
        result,
        size=original_size,
        mode="bilinear",
        align_corners=False
    )

    result = result.squeeze()

    max_val = torch.max(result)
    min_val = torch.min(result)

    result = (result - min_val) / (max_val - min_val + 1e-8)

    mask = (result * 255).cpu().numpy().astype(np.uint8)

    return mask


# -----------------------------
# Process one image
# -----------------------------
def process_single_image(image_path: Path):
    # Preserve category/subfolder structure
    relative_parent = image_path.parent.relative_to(INPUT_ROOT_DIR)

    transparent_dir = OUTPUT_ROOT_DIR / relative_parent / "no_background"
    white_bg_dir = OUTPUT_ROOT_DIR / relative_parent / "white_background"

    transparent_dir.mkdir(parents=True, exist_ok=True)
    white_bg_dir.mkdir(parents=True, exist_ok=True)

    transparent_output_path = transparent_dir / f"{image_path.stem}_no_bg.png"
    white_bg_output_path = white_bg_dir / f"{image_path.stem}_white_bg.jpg"

    # Skip already processed files
    if transparent_output_path.exists() and white_bg_output_path.exists():
        return "skipped"

    # Load image safely
    orig_image = Image.open(image_path)
    orig_image = ImageOps.exif_transpose(orig_image)
    orig_image = orig_image.convert("RGB")

    orig_np = np.array(orig_image)
    original_size = orig_np.shape[:2]  # H, W for torch interpolate

    # Inference
    input_tensor = preprocess_image(orig_np, MODEL_INPUT_SIZE).to(device)

    with torch.no_grad():
        model_output = model(input_tensor)

    output_tensor = get_model_output_tensor(model_output)

    mask = postprocess_image(output_tensor, original_size)

    # Transparent PNG
    mask_image = Image.fromarray(mask).convert("L")

    transparent_image = orig_image.copy()
    transparent_image.putalpha(mask_image)

    transparent_image.save(transparent_output_path)

    # White background JPG
    white_background = Image.new(
        "RGBA",
        transparent_image.size,
        (255, 255, 255, 255)
    )

    white_background.alpha_composite(transparent_image)

    white_background = white_background.convert("RGB")

    white_background.save(
        white_bg_output_path,
        quality=WHITE_JPG_QUALITY,
        optimize=True
    )

    return "processed"


# -----------------------------
# Collect images
# -----------------------------
if not INPUT_ROOT_DIR.exists():
    raise FileNotFoundError(f"Input directory not found: {INPUT_ROOT_DIR}")

image_paths = [
    path for path in INPUT_ROOT_DIR.rglob("*")
    if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
]

print(f"Found {len(image_paths)} images.")


# -----------------------------
# Batch process
# -----------------------------
processed_count = 0
skipped_count = 0
failed_count = 0
failed_files = []

for image_path in tqdm(image_paths, desc="Processing images"):
    try:
        status = process_single_image(image_path)

        if status == "processed":
            processed_count += 1
        elif status == "skipped":
            skipped_count += 1

    except Exception as e:
        failed_count += 1
        failed_files.append((str(image_path), str(e)))
        print(f"\nFailed: {image_path}")
        print(f"Reason: {e}")


# -----------------------------
# Summary
# -----------------------------
print("\nBatch processing completed.")
print(f"Processed: {processed_count}")
print(f"Skipped: {skipped_count}")
print(f"Failed: {failed_count}")
print(f"Output saved to: {OUTPUT_ROOT_DIR}")

if failed_files:
    print("\nFailed files:")
    for file_path, error in failed_files:
        print(f"- {file_path}")
        print(f"  Error: {error}")