"""Backend RTMPose (OpenMMLab) — roda via ONNX (rtmlib; GPU quando disponivel).

Por que este backend existe: e o SoTA "leve" do MMPose e, via `rtmlib`
(que baixa o ONNX oficial e roda em onnxruntime), **nao depende de mmcv** —
funciona no mesmo ambiente do ViTPose-ONNX.

Licenca: Apache-2.0 (RTMPose / OpenMMLab). O `rtmlib` e MIT.
"""
from __future__ import annotations

from typing import Sequence

import numpy as np

from core.adapter import CAT_LIVRE, AdapterConfig, ConfigurableAdapter
from core.canonical import COCO17
from core.canonical import FramePose


def _cfg() -> AdapterConfig:
    return AdapterConfig(
        name="rtmpose",
        display_name="RTMPose (rtmlib, ONNX)",
        native_layout=list(COCO17),
        mapping={n: i for i, n in enumerate(COCO17)},
        score_map={n: i for i, n in enumerate(COCO17)},
        coord="pixel",
        temporal="frame",
        smoothing="none",
        multi_person=True,
        license="Apache-2.0 (RTMPose/OpenMMLab) · rtmlib MIT",
        license_category=CAT_LIVRE,
        notes="COCO-17 nativo. rtmlib baixa o ONNX (~48 MB) na 1a execucao e roda em "
              "onnxruntime (GPU CUDA quando disponivel; senao CPU). Sem mmcv.",
    )


def _onnx_cuda_ok() -> bool:
    """True se o onnxruntime instalado expoe o provider CUDA (DLLs do cuDNN prontas)."""
    try:
        from core.gpu import ensure_cuda_dlls

        ensure_cuda_dlls()
        import onnxruntime as ort

        return "CUDAExecutionProvider" in ort.get_available_providers()
    except Exception:  # noqa: BLE001
        return False


class RTMPoseBackend(ConfigurableAdapter):
    def __init__(self) -> None:
        super().__init__(_cfg())
        self.name = "rtmpose"
        self.display_name = "RTMPose (rtmlib, ONNX)"
        self._body = None
        self._target = None
        self._device_used = "cpu"
        self._fallback_reason = ""
        self._fallback_done = False

    def is_available(self) -> bool:
        try:
            import rtmlib  # noqa: F401
            import onnxruntime  # noqa: F401
        except Exception:
            return False
        return True

    def availability_reason(self) -> str:
        try:
            import rtmlib  # noqa: F401
        except Exception:
            return "requer 'pip install rtmlib --no-deps' (o ONNX ~48 MB e baixado na 1a execucao)"
        try:
            import onnxruntime  # noqa: F401
        except Exception:
            return "requer 'pip install onnxruntime'"
        return ""

    def load(self, config: dict | None = None) -> None:
        from rtmlib import Body

        config = config or {}
        from core.gpu import compute_target

        self._target = compute_target("rtmpose")
        # rtmlib aceita "cuda" quando o onnxruntime-gpu esta instalado
        device = config.get("device") or (
            "cuda" if self._target.device == "cuda" and _onnx_cuda_ok() else "cpu")
        self._fallback_done = False
        try:
            self._body = Body(
                mode=config.get("mode", "balanced"),    # lightweight | balanced | performance
                backend="onnxruntime",
                device=device,
                to_openpose=False,
            )
            self._device_used = device
        except Exception as exc:  # noqa: BLE001
            if device == "cpu":
                raise
            # GPU indisponivel na criacao (drivers/libs): cai para CPU com motivo
            self._fallback_reason = f"GPU falhou ao criar a sessao ({exc}); usando CPU"
            self._body = Body(
                mode=config.get("mode", "balanced"),
                backend="onnxruntime",
                device="cpu",
                to_openpose=False,
            )
            self._device_used = "cpu"
        self._loaded = True

    def unload(self) -> None:
        self._body = None
        super().unload()

    def infer_raw(self, frames: Sequence[np.ndarray], ctx: dict) -> list[dict]:
        if self._body is None:
            self.load({})
        out: list[dict] = []
        for frame in frames:
            try:
                kps, scores = self._body(frame)
                kps = np.asarray(kps, np.float32)
                scores = np.asarray(scores, np.float32)
            except Exception as exc:  # noqa: BLE001
                # a GPU pode falhar SO na inferencia (ex.: cuDNN ausente): cai para CPU 1x
                if not self._fallback_done and self._device_used == "cuda":
                    self._fallback_done = True
                    self._fallback_reason = (
                        f"GPU falhou na inferencia ({exc}); usando CPU a partir de agora")
                    self.load({"device": "cpu"})
                    try:
                        kps, scores = self._body(frame)
                        kps = np.asarray(kps, np.float32)
                        scores = np.asarray(scores, np.float32)
                    except Exception:
                        out.append(None)
                        continue
                else:
                    out.append(None)
                    continue
            if kps.ndim != 3 or kps.shape[0] == 0:
                out.append(None)
                continue
            # multi-pessoa: escolhe a de maior confianca media
            best = int(np.argmax(scores.mean(axis=1))) if scores.ndim == 2 else 0
            out.append({"kp": kps[best], "score": scores[best] if scores.ndim == 2 else scores})
        return out

    def postprocess(self, pose: FramePose, frame_index: int, ctx: dict) -> FramePose:
        return pose
