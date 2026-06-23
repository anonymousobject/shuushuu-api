"""Tests for ONNX execution-provider selection."""

import onnxruntime as ort

from app.services.onnx_providers import make_session_options, select_providers


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


def test_make_session_options_caps_intra_op_threads():
    so = make_session_options(3)
    assert isinstance(so, ort.SessionOptions)
    assert so.intra_op_num_threads == 3
    assert so.inter_op_num_threads == 1


def test_make_session_options_zero_uses_library_default():
    so = make_session_options(0)
    # 0 means "let onnxruntime decide" — we must not force a cap.
    assert so.intra_op_num_threads == 0
