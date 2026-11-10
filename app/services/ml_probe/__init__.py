"""Theme-tag linear probe on frozen animetimm embeddings.

Pure logic for the experiment described in
docs/plans/2026-Q4/2026-11-03-theme-probe-design.md: the embedding store
reader, the image-id split, the label matrix with provenance, the mapping-path
baseline, metrics, the numpy logistic head and the comparison report. The
CLI that wires these together is scripts/ml_probe.py; the embedding store
writer lives in the app-free GPU runner, scripts/ml_gpu_infer.py.
"""
