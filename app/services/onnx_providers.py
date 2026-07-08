"""ONNX Runtime execution-provider selection.

Picks the best available execution provider so the same inference code runs on
NVIDIA (CUDA), AMD (ROCm), or CPU without per-host edits — only the providers
actually present in the installed onnxruntime build are requested, which also
avoids the spurious "provider not available" warnings ORT emits when you ask
for a provider it can't load.
"""

import onnxruntime as ort  # type: ignore[import-untyped]


def make_session_options(intra_op_threads: int) -> ort.SessionOptions:
    """Build SessionOptions, optionally capping intra-op threads.

    ``intra_op_threads <= 0`` leaves onnxruntime's default (all cores). A positive
    value caps cores per inference (with inter_op pinned to 1), so that
    ``semaphore_size x intra_op_threads`` is the process-wide CPU ceiling for
    inference and serving keeps headroom.
    """
    so = ort.SessionOptions()
    if intra_op_threads > 0:
        so.intra_op_num_threads = intra_op_threads
        so.inter_op_num_threads = 1
    return so


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
