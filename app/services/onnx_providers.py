"""ONNX Runtime execution-provider selection.

Picks the best available execution provider so the same inference code runs on
NVIDIA (CUDA), AMD (ROCm), or CPU without per-host edits — only the providers
actually present in the installed onnxruntime build are requested, which also
avoids the spurious "provider not available" warnings ORT emits when you ask
for a provider it can't load.
"""

# Highest-throughput first; CPU is always the final fallback.
_PREFERRED = (
    "CUDAExecutionProvider",
    "ROCMExecutionProvider",
    "CPUExecutionProvider",
)


def select_providers(available: list[str]) -> list[str]:
    """Return the preferred onnxruntime providers present in ``available``.

    ``available`` is what ``onnxruntime.get_available_providers()`` reports for
    the current build. Results preserve the preference order, and
    CPUExecutionProvider is always included as a last-resort fallback.
    """
    selected = [p for p in _PREFERRED if p in available]
    if "CPUExecutionProvider" not in selected:
        selected.append("CPUExecutionProvider")
    return selected
