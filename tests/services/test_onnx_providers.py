"""Tests for ONNX execution-provider selection."""

from app.services.onnx_providers import select_providers


def test_prefers_cuda_when_available():
    avail = ["TensorrtExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"]
    assert select_providers(avail) == ["CUDAExecutionProvider", "CPUExecutionProvider"]


def test_prefers_rocm_when_no_cuda():
    avail = ["MIGraphXExecutionProvider", "ROCMExecutionProvider", "CPUExecutionProvider"]
    assert select_providers(avail) == ["ROCMExecutionProvider", "CPUExecutionProvider"]


def test_cpu_only_box_drops_unusable_providers():
    # An onnxruntime CPU build advertises Azure + CPU; we must not request CUDA.
    avail = ["AzureExecutionProvider", "CPUExecutionProvider"]
    assert select_providers(avail) == ["CPUExecutionProvider"]


def test_cpu_appended_as_fallback_even_if_absent():
    avail = ["CUDAExecutionProvider"]
    assert select_providers(avail) == ["CUDAExecutionProvider", "CPUExecutionProvider"]
