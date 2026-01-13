#!/usr/bin/env python3
"""
Test WD-Tagger v3 model inference.

Uses the same WDTaggerModel class as the arq worker jobs.

Usage:
    uv run python scripts/test_wd_tagger.py /path/to/image.jpg
    uv run python scripts/test_wd_tagger.py /path/to/image.jpg 0.5  # custom threshold
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings
from app.services.onnx_model import (
    CHARACTER_CATEGORY,
    GENERAL_CATEGORY,
    RATING_CATEGORY,
    WDTaggerModel,
)


async def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: uv run python scripts/test_wd_tagger.py /path/to/image.jpg [threshold]")
        sys.exit(1)

    image_path = sys.argv[1]
    threshold = float(sys.argv[2]) if len(sys.argv) > 2 else 0.35

    # Use same paths as the arq worker
    model_dir = Path(settings.ML_MODELS_PATH)
    model_path = model_dir / "wd-swinv2-tagger-v3" / "model.onnx"
    tags_path = model_dir / "wd-swinv2-tagger-v3" / "selected_tags.csv"

    print(f"Loading model from {model_path}...")
    model = WDTaggerModel(str(model_path), str(tags_path))
    await model.load()

    print(f"\nProcessing image: {image_path}")
    print(f"Threshold: {threshold}")

    # Get predictions for all categories
    all_categories = {GENERAL_CATEGORY, CHARACTER_CATEGORY, RATING_CATEGORY}
    predictions = await model.predict(
        image_path,
        min_confidence=threshold,
        include_categories=all_categories,
    )

    # Group results by category
    category_names = {
        GENERAL_CATEGORY: "General",
        CHARACTER_CATEGORY: "Character",
        RATING_CATEGORY: "Rating",
    }

    results_by_category: dict[str, list[dict]] = {
        "Rating": [],
        "Character": [],
        "General": [],
    }

    for pred in predictions:
        cat_name = category_names.get(pred["category"], "Other")
        if cat_name in results_by_category:
            results_by_category[cat_name].append(pred)

    # Print results
    print(f"\n{'='*60}")
    print(f"Results (threshold={threshold}):")
    print(f"{'='*60}")

    for category in ["Rating", "Character", "General"]:
        tags = results_by_category[category]
        if tags:
            print(f"\n{category} ({len(tags)} tags):")
            for pred in tags[:30]:  # Limit to 30 per category
                print(f"  {pred['confidence']:.3f}  {pred['tag']}")
            if len(tags) > 30:
                print(f"  ... and {len(tags) - 30} more")

    # Summary
    total_tags = sum(len(tags) for tags in results_by_category.values())
    print(f"\n{'='*60}")
    print(f"Total: {total_tags} tags above threshold {threshold}")

    await model.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
