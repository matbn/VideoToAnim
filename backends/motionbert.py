"""Backend MotionBERT — lifting 2D->3D (H36M-17), backbone unificado.

Estado da arte de 2023 (ICCV) e ainda referencia para lifting; muito usado como
"lifter" depois de um detector 2D. Requer PyTorch e os pesos publicados
(HuggingFace `walterzhu/MotionBERT`).

Licenca: **Apache-2.0** (codigo e pesos do repositorio oficial) -> categoria livre.

Como no MHFormer, e um lifter: precisa de keypoints 2D H36M-17 upstream. Use
`params.upstream_backend` (ex.: "vitpose"/"mediapipe"/"rtmpose") ou injete 2D por
`params.precomputed_2d`.
"""
from __future__ import annotations

from pathlib import Path

import os
from typing import Sequence

import numpy as np

from core.adapter import CAT_LIVRE, AdapterConfig, ConfigurableAdapter
from core.canonical import COCO17

H36M_17 = [
    "hip", "right_hip", "right_knee", "right_foot", "left_hip", "left_knee",
    "left_foot", "spine", "thorax", "nose", "head", "left_shoulder", "left_elbow",
    "left_wrist", "right_shoulder", "right_elbow", "right_wrist",
]

_H36M_TO_COCO = {
    "nose": "nose", "head": "nose",
    "left_shoulder": "left_shoulder", "right_shoulder": "right_shoulder",
    "left_elbow": "left_elbow", "right_elbow": "right_elbow",
    "left_wrist": "left_wrist", "right_wrist": "right_wrist",
    "left_hip": "left_hip", "right_hip": "right_hip", "hip": "left_hip",
    "left_knee": "left_knee", "right_knee": "right_knee",
    "left_foot": "left_ankle", "right_foot": "right_ankle",
}


def _cfg() -> AdapterConfig:
    return AdapterConfig(
        name="motionbert",
        display_name="MotionBERT (2D→3D lifting)",
        native_layout=H36M_17,
        mapping={k: COCO17.index(v) for k, v in _H36M_TO_COCO.items()},
        coord="pixel",
        temporal="window",
        window=243,
        smoothing="none",
        license="Apache-2.0 (MotionBERT) — codigo e pesos do repo oficial",
        license_category=CAT_LIVRE,
        notes="Lifting temporal (janela ~243 frames). Precisa de detector 2D upstream. "
              "Requer torch + pesos do HF (walterzhu/MotionBERT).",
    )


class MotionBERTBackend(ConfigurableAdapter):
    def __init__(self) -> None:
        super().__init__(_cfg())
        self.name = "motionbert"
        self.display_name = "MotionBERT (2D→3D lifting)"
        self._repo = os.environ.get("V2M_MOTIONBERT_REPO", "")
        self._ckpt = os.environ.get("V2M_MOTIONBERT_CKPT", "")

    def is_available(self) -> bool:
        try:
            import torch  # noqa: F401
        except Exception:
            return False
        if not self._repo:
            # repo clonado pelo provisionamento automatico (third_party/)
            auto = Path(__file__).resolve().parents[1] / "third_party" / "MotionBERT"
            if auto.exists():
                self._repo = str(auto)
        return bool(self._repo and self._ckpt)

    def availability_reason(self) -> str:
        try:
            import torch  # noqa: F401
        except Exception:
            return ("requer PyTorch e os pesos do MotionBERT (V2M_MOTIONBERT_REPO / "
                    "V2M_MOTIONBERT_CKPT). Alternativa em CPU: use o lifter analitico.")
        return "defina V2M_MOTIONBERT_REPO e V2M_MOTIONBERT_CKPT (ver docs/BACKENDS.md)."

    def load(self, config: dict | None = None) -> None:
        config = config or {}
        self._repo = config.get("repo") or self._repo
        self._ckpt = config.get("checkpoint") or self._ckpt
        self._loaded = True

    def infer_raw(self, frames: Sequence[np.ndarray], ctx: dict) -> list[dict]:
        raise RuntimeError(
            "MotionBERT: inferencia nao habilitada nesta build (requer torch + pesos). "
            "Use o lifter analitico ou configure o repo."
        )
