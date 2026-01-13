#!/usr/bin/env python3
"""
Test WD-Tagger v3 model inference.

Usage:
    uv run python scripts/test_wd_tagger.py /path/to/image.jpg
"""

import csv
import sys
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image

# Model paths
MODEL_DIR = Path(__file__).parent.parent / "ml_models" / "wd-swinv2-tagger-v3"
MODEL_PATH = MODEL_DIR / "model.onnx"
TAGS_PATH = MODEL_DIR / "selected_tags.csv"

# WD-Tagger v3 uses 448x448 input
INPUT_SIZE = 448


def load_tags() -> tuple[list[str], list[int]]:
    """Load tag names and categories from CSV."""
    names = []
    categories = []
    with open(TAGS_PATH) as f:
        reader = csv.DictReader(f)
        for row in reader:
            names.append(row["name"])
            categories.append(int(row["category"]))
    return names, categories


def preprocess_image(image_path: str) -> np.ndarray:
    """
    Preprocess image for WD-Tagger.

    - Resize to 448x448 (pad to square first, then resize)
    - Convert to RGB
    - Normalize with mean=0.5, std=0.5 -> range [-1, 1]
    - Format: NHWC (batch, height, width, channels)
    """
    img = Image.open(image_path).convert("RGBA")

    # Create white background for transparency
    background = Image.new("RGBA", img.size, (255, 255, 255, 255))
    background.paste(img, mask=img.split()[3] if img.mode == "RGBA" else None)
    img = background.convert("RGB")

    # Pad to square
    max_dim = max(img.size)
    pad_left = (max_dim - img.size[0]) // 2
    pad_top = (max_dim - img.size[1]) // 2

    padded = Image.new("RGB", (max_dim, max_dim), (255, 255, 255))
    padded.paste(img, (pad_left, pad_top))

    # Resize to model input size
    img = padded.resize((INPUT_SIZE, INPUT_SIZE), Image.BICUBIC)

    # Convert to numpy array (NO normalization - keep 0-255 range)
    img_array = np.asarray(img, dtype=np.float32)

    # Convert RGB to BGR (required by WD-Tagger ONNX model)
    img_array = img_array[:, :, ::-1]

    # Add batch dimension: (H, W, C) -> (1, H, W, C)
    img_array = np.expand_dims(img_array, axis=0)

    return img_array


def run_inference(session: ort.InferenceSession, image_array: np.ndarray) -> np.ndarray:
    """Run model inference."""
    input_name = session.get_inputs()[0].name
    output_name = session.get_outputs()[0].name

    outputs = session.run([output_name], {input_name: image_array})
    return outputs[0][0]  # Remove batch dimension


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: uv run python scripts/test_wd_tagger.py /path/to/image.jpg")
        sys.exit(1)

    image_path = sys.argv[1]
    threshold = float(sys.argv[2]) if len(sys.argv) > 2 else 0.35

    print(f"Loading model from {MODEL_PATH}...")
    session = ort.InferenceSession(
        str(MODEL_PATH),
        providers=["CUDAExecutionProvider", "CPUExecutionProvider"]
    )
    print(f"Using providers: {session.get_providers()}")

    print(f"\nLoading tags from {TAGS_PATH}...")
    tag_names, tag_categories = load_tags()
    print(f"Loaded {len(tag_names)} tags")

    print(f"\nProcessing image: {image_path}")
    image_array = preprocess_image(image_path)
    print(f"Input shape: {image_array.shape}")

    print("\nRunning inference...")
    predictions = run_inference(session, image_array)

    # Apply sigmoid to get probabilities
    probabilities = 1 / (1 + np.exp(-predictions))

    # Group results by category
    category_names = {0: "General", 4: "Character", 9: "Rating"}
    results_by_category: dict[str, list[tuple[str, float]]] = {
        "Rating": [],
        "Character": [],
        "General": [],
    }

    for i, (name, cat, prob) in enumerate(zip(tag_names, tag_categories, probabilities)):
        if prob >= threshold:
            cat_name = category_names.get(cat, "Other")
            if cat_name in results_by_category:
                results_by_category[cat_name].append((name, float(prob)))

    # Sort by probability
    for cat in results_by_category:
        results_by_category[cat].sort(key=lambda x: x[1], reverse=True)

    # Print results
    print(f"\n{'='*60}")
    print(f"Results (threshold={threshold}):")
    print(f"{'='*60}")

    for category in ["Rating", "Character", "General"]:
        tags = results_by_category[category]
        if tags:
            print(f"\n{category} ({len(tags)} tags):")
            for name, prob in tags[:30]:  # Limit to 30 per category
                print(f"  {prob:.3f}  {name}")
            if len(tags) > 30:
                print(f"  ... and {len(tags) - 30} more")

    # Summary
    total_tags = sum(len(tags) for tags in results_by_category.values())
    print(f"\n{'='*60}")
    print(f"Total: {total_tags} tags above threshold {threshold}")


if __name__ == "__main__":
    main()
