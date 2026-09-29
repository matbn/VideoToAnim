"""Regressao: RTMPose precisa do helper _onnx_cuda_ok definido.

O load() chamava `_onnx_cuda_ok()` que nunca existiu no modulo -> NameError em
todo job com rtmpose. Este teste trava o helper e a escolha de dispositivo.
"""
import pytest


def test_helper_de_cuda_definido() -> None:
    from backends.rtmpose import _onnx_cuda_ok

    assert isinstance(_onnx_cuda_ok(), bool)


def test_load_escolhe_dispositivo() -> None:
    pytest.importorskip("rtmlib")
    from backends.rtmpose import RTMPoseBackend

    b = RTMPoseBackend()
    b.load({})
    assert b._device_used in {"cpu", "cuda"}
    assert b._body is not None
