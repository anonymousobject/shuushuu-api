# WD-Tagger v3 (SwinV2) Model

Both files below are provisioned here from HuggingFace — neither is committed to
the repo (`model.onnx` is large; `selected_tags.csv` is the full danbooru tag
vocabulary, including explicit tag names we never map). Fetch both onto each host
that runs this model:

```bash
cd ml_models/wd-swinv2-tagger-v3
wget https://huggingface.co/SmilingWolf/wd-swinv2-tagger-v3/resolve/main/model.onnx
wget https://huggingface.co/SmilingWolf/wd-swinv2-tagger-v3/resolve/main/selected_tags.csv
```

Or use the HuggingFace CLI:

```bash
huggingface-cli download SmilingWolf/wd-swinv2-tagger-v3 model.onnx selected_tags.csv --local-dir ml_models/wd-swinv2-tagger-v3
```

## Model Info

- **Source**: https://huggingface.co/SmilingWolf/wd-swinv2-tagger-v3
- **Size**: ~467MB
- **Input**: 448x448 RGB image (BGR channel order, 0-255 range, no normalization)
- **Output**: 10,861 tag probabilities (sigmoid already applied in the ONNX graph — threshold directly)
- **Tags**: 8,106 general + 2,751 character + 4 rating tags

## Preprocessing

- Convert to RGB, composite alpha onto white background
- Pad to square (white padding)
- Resize to 448x448 (bicubic)
- Convert RGB to BGR
- Keep pixel values in 0-255 range (NO normalization)
- Shape: (1, 448, 448, 3) float32
