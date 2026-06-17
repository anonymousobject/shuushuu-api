"""preprocess.json-driven preprocessing for animetimm taggers.

Each animetimm model ships a ``preprocess.json`` describing its exact eval
transform (pad → resize → center-crop → to-tensor → normalize). Driving
preprocessing from that file makes any animetimm model a drop-in — different
resolutions/normalization "just work" — instead of hardcoding one model's
pipeline. The default below mirrors swinv2_base_window8_256.dbv4-full so behaviour
is unchanged when a model dir has no preprocess.json.

NOTE: scripts/ml_gpu_infer.py keeps an app-free copy of this logic for the GPU
host; tests/integration/test_ml_gpu_infer.py guards the two against drift.
"""

import json
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

_FILTERS = {
    "nearest": Image.Resampling.NEAREST,
    "bilinear": Image.Resampling.BILINEAR,
    "bicubic": Image.Resampling.BICUBIC,
    "lanczos": Image.Resampling.LANCZOS,
}

# swinv2_base_window8_256.dbv4-full "test" pipeline — fallback when a model dir
# has no preprocess.json (keeps legacy behaviour identical).
DEFAULT_TEST_PIPELINE: list[dict[str, Any]] = [
    {
        "type": "pad_to_size",
        "size": [512, 512],
        "background_color": "white",
        "interpolation": "bilinear",
    },
    {"type": "resize", "size": 448, "interpolation": "bicubic"},
    {"type": "center_crop", "size": [448, 448]},
    {"type": "maybe_to_tensor"},
    {"type": "normalize", "mean": [0.485, 0.456, 0.406], "std": [0.229, 0.224, 0.225]},
]


def load_test_pipeline(model_dir: Path) -> list[dict[str, Any]]:
    """Return the model's preprocess.json 'test' pipeline, or the default."""
    path = model_dir / "preprocess.json"
    if not path.exists():
        return DEFAULT_TEST_PIPELINE
    pipeline: list[dict[str, Any]] = json.loads(path.read_text())["test"]
    return pipeline


def _filter(name: str) -> Image.Resampling:
    try:
        return _FILTERS[name]
    except KeyError:
        raise ValueError(f"unsupported interpolation: {name}") from None


def load_rgb(image_path: str) -> Image.Image:
    """Open an image, compositing any alpha onto white (animetimm convention)."""
    img = Image.open(image_path).convert("RGBA")
    background = Image.new("RGBA", img.size, (255, 255, 255, 255))
    background.paste(img, mask=img.split()[3])
    return background.convert("RGB")


def apply_test_pipeline(
    img: Image.Image, pipeline: list[dict[str, Any]]
) -> np.ndarray[Any, np.dtype[np.float32]]:
    """Run a preprocess.json 'test' pipeline, returning a (1, C, H, W) float32 tensor."""
    arr: np.ndarray[Any, np.dtype[np.float32]] | None = None
    for op in pipeline:
        kind = op["type"]
        if kind == "pad_to_size":
            target_w, target_h = op["size"]
            bg = (255, 255, 255) if op.get("background_color") == "white" else (0, 0, 0)
            scale = min(target_w / img.width, target_h / img.height)
            new = (int(img.width * scale), int(img.height * scale))
            img = img.resize(new, _filter(op.get("interpolation", "bilinear")))
            canvas = Image.new("RGB", (target_w, target_h), bg)
            canvas.paste(img, ((target_w - new[0]) // 2, (target_h - new[1]) // 2))
            img = canvas
        elif kind == "resize":
            size = op["size"]
            if isinstance(size, int):  # resize shortest edge to `size`, keep aspect
                scale = size / min(img.width, img.height)
                new = (int(img.width * scale), int(img.height * scale))
            else:  # [h, w]
                new = (size[1], size[0])
            img = img.resize(new, _filter(op.get("interpolation", "bicubic")))
        elif kind == "center_crop":
            crop_h, crop_w = op["size"]
            left = (img.width - crop_w) // 2
            top = (img.height - crop_h) // 2
            img = img.crop((left, top, left + crop_w, top + crop_h))
        elif kind == "maybe_to_tensor":
            arr = np.asarray(img, dtype=np.float32) / 255.0
            arr = np.transpose(arr, (2, 0, 1))  # HWC -> CHW
        elif kind == "normalize":
            if arr is None:
                raise ValueError("normalize op before maybe_to_tensor in pipeline")
            mean = np.array(op["mean"], dtype=np.float32).reshape(3, 1, 1)
            std = np.array(op["std"], dtype=np.float32).reshape(3, 1, 1)
            arr = (arr - mean) / std
        else:
            raise ValueError(f"unsupported preprocess op: {kind}")

    if arr is None:
        raise ValueError("preprocess pipeline produced no tensor (missing maybe_to_tensor)")
    return np.expand_dims(arr, axis=0)
