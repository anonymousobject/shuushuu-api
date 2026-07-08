"""ML tagger category constants (light: no onnxruntime import).

The model wrappers (onnx_model.py, animetimm_model.py) define the same numeric
category ids; this module mirrors them so code in the always-imported router
chain can reference the suggestion category set WITHOUT importing onnxruntime.
Keep in sync with the wrappers' constants (general=0, character=4, rating=9).
"""

GENERAL_CATEGORY = 0
CHARACTER_CATEGORY = 4
RATING_CATEGORY = 9

# Categories surfaced as tag suggestions: general (-> internal theme tags) + character.
SUGGESTION_CATEGORIES: set[int] = {GENERAL_CATEGORY, CHARACTER_CATEGORY}
