"""Backend ViTPose (padrao).

Dois motores, escolhidos automaticamente:

1. **ONNX Runtime (recomendado, padrao)** — nao depende de mmcv/mmpose.
   Usa o export ONNX do ViTPose-Base (`onnx-community/vitpose-base-simple`,
   derivado de `usyd-community/vitpose-base-simple`, Apache-2.0). O modelo e
   baixado automaticamente na primeira execucao (~83 MB, quantizado int8) e
   roda em CPU. Foi este o caminho adotado porque **`mmcv` nao publica wheel
   para Python 3.13**, o que inviabiliza o caminho MMPose nessa versao.

2. **MMPose (legado)** — se `mmpose` + `mmcv` + `torch` estiverem instalados
   (funciona em Python <= 3.12), usa o pipeline top-down classico.

Saida: keypoints COCO-17 em pixel na imagem original, y para baixo, score em [0,1]
(o proprio modelo ja usa a ordem COCO-17, entao o mapeamento e identidade).
"""
from __future__ import annotations

import os
import urllib.request
from pathlib import Path
from typing import Sequence

import numpy as np

from core.adapter import AdapterConfig, ConfigurableAdapter
from core.canonical import COCO17

# --- modelo ONNX (export oficial em onnx-community, licenca Apache-2.0) ------
HF_BASE = "https://huggingface.co/onnx-community/vitpose-base-simple/resolve/main"
MODEL_FILES = {
    "model.onnx": f"{HF_BASE}/onnx/model.onnx",                    # fp32 — melhor na GPU
    "model_quantized.onnx": f"{HF_BASE}/onnx/model_quantized.onnx",  # int8 — melhor na CPU
    "config.json": f"{HF_BASE}/config.json",
    "preprocessor_config.json": f"{HF_BASE}/preprocessor_config.json",
}
DEFAULT_DIR = Path("models") / "vitpose"

# normalizacao do preprocessor_config.json do modelo
_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
_STD = np.array([0.229, 0.224, 0.225], np.float32)
_IN_H, _IN_W = 256, 192
_HM_H, _HM_W = 64, 48


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def _cfg() -> AdapterConfig:
    return AdapterConfig(
        name="vitpose",
        display_name="ViTPose (ONNX)",
        native_layout=list(COCO17),
        mapping={n: i for i, n in enumerate(COCO17)},
        # o modelo emite score por junta na MESMA ordem do layout nativo:
        # sem este mapa as confiancas reais seriam descartadas (ficariam 1.0)
        score_map={n: i for i, n in enumerate(COCO17)},
        coord="pixel",
        temporal="frame",
        smoothing="none",
        multi_person=True,
        license="Apache-2.0 (ViTPose / onnx-community)",
        notes="Top-down (usa o frame inteiro se nao houver bbox). ONNX Runtime usa GPU "
              "quando disponivel (senao CPU); sem mmcv/mmpose. COCO-17 nativo.",
    )


class ViTPoseBackend(ConfigurableAdapter):
    def __init__(self) -> None:
        super().__init__(_cfg())
        self.name = "vitpose"
        self.display_name = "ViTPose (ONNX)"
        self.is_default = True
        self._session = None
        self._engine = ""
        self._provider = ""
        self._target = None
        self._model_file = None
        self._fallback_reason = ""
        self._dir = DEFAULT_DIR
        self._mmpose_model = None
        self._cfg_path = ""
        self._ckpt = ""

    # ---- disponibilidade ------------------------------------------------
    def is_available(self) -> bool:
        try:
            import onnxruntime  # noqa: F401
            return True
        except Exception:
            pass
        try:
            import mmpose  # noqa: F401
            import mmcv  # noqa: F401
            import torch  # noqa: F401
            return bool(self._cfg_path and self._ckpt)
        except Exception:
            return False

    def availability_reason(self) -> str:
        try:
            import onnxruntime  # noqa: F401
            return ""
        except Exception:
            return ("requer 'pip install onnxruntime' (o modelo ViTPose e baixado "
                    "automaticamente na primeira execucao).")
        return ""

    # ---- ciclo de vida --------------------------------------------------
    def _model_dir(self, config: dict) -> Path:
        raw = config.get("model_dir") or os.environ.get("V2M_VITPOSE_DIR", str(DEFAULT_DIR))
        p = Path(raw)
        return p if p.is_absolute() else Path.cwd() / p

    def _ensure_model(self, config: dict) -> Path:
        d = self._model_dir(config)
        d.mkdir(parents=True, exist_ok=True)
        fname = config.get("onnx_file")
        if not fname:
            # int8 quantizado e otimo na CPU, mas costuma ser MAIS LENTO na GPU
            # (ops int8 caem para o host). Na GPU usamos o fp32.
            try:
                from core.gpu import compute_target

                dev = compute_target("vitpose").device
            except Exception:  # noqa: BLE001
                dev = "cpu"
            fname = "model.onnx" if dev in ("cuda", "directml", "mps") else "model_quantized.onnx"
        paths = {fname: MODEL_FILES.get(fname, f"{HF_BASE}/onnx/{fname}")}
        for name, url in {**paths, "config.json": MODEL_FILES["config.json"]}.items():
            f = d / name
            if not f.exists() or f.stat().st_size == 0:
                urllib.request.urlretrieve(url, f)
        return d / fname

    def load(self, config: dict | None = None) -> None:
        config = config or {}
        self._cfg_path = config.get("config") or os.environ.get("V2M_VITPOSE_CONFIG", "")
        self._ckpt = config.get("checkpoint") or os.environ.get("V2M_VITPOSE_CHECKPOINT", "")

        mmpose_ready = False
        try:
            import mmpose  # noqa: F401
            import mmcv  # noqa: F401
            import torch  # noqa: F401
            mmpose_ready = bool(self._cfg_path and self._ckpt)
        except Exception:
            mmpose_ready = False

        if config.get("engine") == "mmpose" or (mmpose_ready and config.get("engine") != "onnx"):
            from mmpose.apis import init_model  # type: ignore

            self._mmpose_model = init_model(
                self._cfg_path, self._ckpt, device=config.get("device", "cpu")
            )
            self._engine = "mmpose"
        else:
            import onnxruntime as ort

            model_path = self._ensure_model(config)
            opts = ort.SessionOptions()
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            threads = int(config.get("threads", 0) or 0)
            if threads > 0:
                opts.intra_op_num_threads = threads
            # usa a GPU quando existe uma compativel (senao, CPU)
            from core.gpu import compute_target

            self._target = compute_target("vitpose")
            self._model_file = model_path
            if self._target.device == "cuda":
                self._dll_dirs = _add_nvidia_dll_dirs()
            disponiveis = ort.get_available_providers()
            if self._target.device == "cuda" and "CUDAExecutionProvider" in disponiveis:
                providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
            elif self._target.device == "directml" and "DmlExecutionProvider" in disponiveis:
                providers = ["DmlExecutionProvider", "CPUExecutionProvider"]
            else:
                providers = ["CPUExecutionProvider"]
            # se a GPU falhar (driver/libs CUDA ausentes), cai para CPU em vez
            # de quebrar o backend inteiro
            try:
                self._session = ort.InferenceSession(
                    str(model_path), sess_options=opts, providers=providers
                )
                self._provider = providers[0]
            except Exception as exc:  # noqa: BLE001
                if providers == ["CPUExecutionProvider"]:
                    raise
                self._session = ort.InferenceSession(
                    str(model_path), sess_options=opts, providers=["CPUExecutionProvider"]
                )
                self._provider = "CPUExecutionProvider"
                self._fallback_reason = f"GPU falhou ({exc}); usando CPU"
            # o rotulo acompanha o dispositivo de fato em uso
            if self._provider == "CUDAExecutionProvider":
                self.display_name = "ViTPose (ONNX, GPU CUDA)"
            elif self._provider == "DmlExecutionProvider":
                self.display_name = "ViTPose (ONNX, GPU DirectML)"
            else:
                self.display_name = "ViTPose (ONNX, CPU)"
            self._input_name = self._session.get_inputs()[0].name
            self._engine = "onnx"
        self._loaded = True

    def unload(self) -> None:
        self._session = None
        self._mmpose_model = None
        super().unload()

    # ---- inferencia -----------------------------------------------------
    @staticmethod
    def _preprocess(frame: np.ndarray, bbox: np.ndarray, out_hw=(_IN_H, _IN_W)) -> tuple[np.ndarray, float, float]:
        """Recorta a bbox, redimensiona e normaliza. Devolve (tensor, escala_x, escala_y)."""
        import cv2

        x1, y1, x2, y2 = [float(v) for v in bbox]
        x1, y1 = max(0.0, x1), max(0.0, y1)
        x2 = min(float(frame.shape[1]), x2)
        y2 = min(float(frame.shape[0]), y2)
        crop = frame[int(y1):max(int(y1) + 1, int(y2)), int(x1):max(int(x1) + 1, int(x2))]
        oh, ow = out_hw
        resized = cv2.resize(crop, (ow, oh), interpolation=cv2.INTER_LINEAR)
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        rgb = (rgb - _MEAN) / _STD
        tensor = np.ascontiguousarray(rgb.transpose(2, 0, 1)[None], dtype=np.float32)
        sx = (x2 - x1) / ow
        sy = (y2 - y1) / oh
        return tensor, sx, sy

    def _decode(self, heatmaps: np.ndarray, offset_x: float, offset_y: float,
                scale_x: float, scale_y: float) -> tuple[np.ndarray, np.ndarray]:
        hm = heatmaps[0]  # (17, 64, 48)
        h, w = hm.shape[1], hm.shape[2]
        kp = np.zeros((17, 2), np.float32)
        sc = np.zeros((17,), np.float32)
        step_x = _IN_W / _HM_W
        step_y = _IN_H / _HM_H
        already_prob = float(hm.max()) <= 1.5   # onnx-community ja sai pos-sigmoid
        for c in range(hm.shape[0]):
            flat = int(np.argmax(hm[c]))
            hy, hx = divmod(flat, w)
            peak = float(hm[c, hy, hx])
            # refinamento sub-pixel: centroide ponderado numa janela 3x3
            y0, y1 = max(0, hy - 1), min(h, hy + 2)
            x0, x1 = max(0, hx - 1), min(w, hx + 2)
            win = np.clip(hm[c, y0:y1, x0:x1].astype(np.float64), 0.0, None)
            tot = float(win.sum())
            if tot > 1e-9:
                ys = np.arange(y0, y1)
                xs = np.arange(x0, x1)
                cy_h = float((win.sum(axis=1) * ys).sum() / tot)
                cx_h = float((win.sum(axis=0) * xs).sum() / tot)
            else:
                cy_h, cx_h = float(hy), float(hx)
            kp[c] = (offset_x + (cx_h + 0.5) * step_x * scale_x,
                     offset_y + (cy_h + 0.5) * step_y * scale_y)
            sc[c] = peak if already_prob else float(_sigmoid(np.float32(peak)))
        return kp, np.clip(sc, 0.0, 1.0)

    def _infer_onnx(self, frame: np.ndarray, ctx: dict) -> tuple[np.ndarray, np.ndarray]:
        h, w = frame.shape[:2]
        bbox = ctx.get("bbox")
        if bbox is not None:
            b = np.asarray(bbox, np.float32).reshape(-1)[:4]
        else:
            b = np.array([0, 0, w, h], np.float32)   # sem detector: frame inteiro
        tensor, sx, sy = self._preprocess(frame, b)
        out = self._run_session(tensor)
        return self._decode(out, float(b[0]), float(b[1]), sx, sy)

    def _run_session(self, tensor: np.ndarray) -> np.ndarray:
        """Roda o modelo; se a GPU falhar em runtime (ex.: cuDNN ausente),

        cai para CPU de UMA vez e segue — nunca devolve pose vazia em silencio.
        """
        try:
            return self._session.run(None, {self._input_name: tensor})[0]
        except Exception as exc:  # noqa: BLE001
            if self._provider == "CPUExecutionProvider" or self._model_file is None:
                raise
            import onnxruntime as ort

            self._fallback_reason = (
                f"GPU falhou na inferencia ({type(exc).__name__}: {str(exc)[:120]}); "
                f"usando CPU a partir de agora")
            opts = ort.SessionOptions()
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            self._session = ort.InferenceSession(
                str(self._model_file), sess_options=opts, providers=["CPUExecutionProvider"])
            self._provider = "CPUExecutionProvider"
            return self._session.run(None, {self._input_name: tensor})[0]

    def _infer_mmpose(self, frame: np.ndarray, ctx: dict) -> tuple[np.ndarray, np.ndarray]:
        from mmpose.apis import inference_topdown  # type: ignore

        h, w = frame.shape[:2]
        bbox = ctx.get("bbox")
        boxes = (np.asarray(bbox, np.float32).reshape(-1, 4) if bbox is not None
                 else np.array([[0, 0, w - 1, h - 1]], np.float32))
        res = inference_topdown(self._mmpose_model, frame, boxes)[0]
        return (np.asarray(res.pred_instances.keypoints[0], np.float32),
                np.asarray(res.pred_instances.keypoint_scores[0], np.float32))

    def infer_raw(self, frames: Sequence[np.ndarray], ctx: dict) -> list[dict]:
        if self._engine == "":
            self.load({})
        out: list[dict] = []
        for frame in frames:
            try:
                if self._engine == "mmpose":
                    kp, sc = self._infer_mmpose(frame, ctx)
                else:
                    kp, sc = self._infer_onnx(frame, ctx)
            except Exception:
                out.append(None)
                continue
            out.append({"kp": kp, "score": sc})
        return out


def _add_nvidia_dll_dirs() -> list[str]:
    """(compat) DLLs do cuDNN no processo — ver core.gpu.ensure_cuda_dlls."""
    from core.gpu import ensure_cuda_dlls

    return ensure_cuda_dlls()
