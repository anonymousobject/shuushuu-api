# WD-Tagger v3 (SwinV2) Model

Download the model from HuggingFace:

```bash
cd ml_models/wd-swinv2-tagger-v3
wget https://huggingface.co/SmilingWolf/wd-swinv2-tagger-v3/resolve/main/model.onnx
```

Or use the HuggingFace CLI:

```bash
huggingface-cli download SmilingWolf/wd-swinv2-tagger-v3 model.onnx --local-dir ml_models/wd-swinv2-tagger-v3
```

## Model Info

- **Source**: https://huggingface.co/SmilingWolf/wd-swinv2-tagger-v3
- **Size**: ~467MB
- **Input**: 448x448 RGB image (BGR channel order, 0-255 range, no normalization)
- **Output**: 10,861 tag probabilities (apply sigmoid)
- **Tags**: 8,106 general + 2,751 character + 4 rating tags

## Preprocessing

- Convert to RGB, composite alpha onto white background
- Pad to square (white padding)
- Resize to 448x448 (bicubic)
- Convert RGB to BGR
- Keep pixel values in 0-255 range (NO normalization)
- Shape: (1, 448, 448, 3) float32
