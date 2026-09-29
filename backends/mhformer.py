"""Backend MHFormer (lifting 2D->3D temporal, H36M-17).

Requer o repositorio externo Vegetebird/MHFormer + PyTorch + checkpoints.
Como e temporal (janela de ~243/351 frames) e dependente de um detector 2D,
o pipeline usa o proprio ViTPose/MediaPipe como fonte 2D (params.lifter_backend)
ou injeta keypoints 2D via params.precomputed_2d.
"""
from __future__ import annotations

from pathlib import Path

import os
from typing import Sequence

import numpy as np

from core.adapter import CAT_LICENCA_A_PARTE, AdapterConfig, ConfigurableAdapter
from core.canonical import COCO17

H36M_17 = [
    "hip", "right_hip", "right_knee", "right_foot", "left_hip", "left_knee",
    "left_foot", "spine", "thorax", "nose", "head", "left_shoulder", "left_elbow",
    "left_wrist", "right_shoulder", "right_elbow", "right_wrist",
]

# H36M-17 -> COCO-17 (mapeamento aproximado; pes/quadril derivados)
_H36M_TO_COCO = {
    "nose": "nose",
    "head": "nose",
    "left_shoulder": "left_shoulder",
    "right_shoulder": "right_shoulder",
    "left_elbow": "left_elbow",
    "right_elbow": "right_elbow",
    "left_wrist": "left_wrist",
    "right_wrist": "right_wrist",
    "left_hip": "left_hip",
    "right_hip": "right_hip",
    "hip": "left_hip",
    "left_knee": "left_knee",
    "right_knee": "right_knee",
    "left_foot": "left_ankle",
    "right_foot": "right_ankle",
}


def _cfg() -> AdapterConfig:
    return AdapterConfig(
        name="mhformer",
        display_name="MHFormer (2D->3D temporal)",
        native_layout=H36M_17,
        mapping={k: COCO17.index(v) for k, v in _H36M_TO_COCO.items()},
        coord="pixel",
        temporal="window",
        window=243,
        smoothing="none",
        license="MIT (codigo MHFormer); pesos declarados para pesquisa",
        license_category=CAT_LICENCA_A_PARTE,
        notes="Saida 3D H36M (y-up). Janela temporal: precisa de >100 frames de contexto. "
              "Exige detector 2D upstream.",
    )


class MHFormerBackend(ConfigurableAdapter):
    def __init__(self) -> None:
        super().__init__(_cfg())
        self.name = "mhformer"
        self.display_name = "MHFormer (2D->3D temporal)"
        self._repo = os.environ.get("V2M_MHFORMER_REPO", "")

    def is_available(self) -> bool:
        try:
            import torch  # noqa: F401
        except Exception:
            return False
        if not self._repo:
            auto = Path(__file__).resolve().parents[1] / "third_party" / "MHFormer"
            if auto.exists():
                self._repo = str(auto)
        return bool(self._repo and os.path.isdir(self._repo))

    def availability_reason(self) -> str:
        try:
            import torch  # noqa: F401
        except Exception:
            return "requer PyTorch ('pip install torch')."
        return "defina V2M_MHFORMER_REPO apontando para o clone de Vegetebird/MHFormer."

    def load(self, config: dict | None = None) -> None:
        config = config or {}
        self._repo = config.get("repo") or self._repo
        if not self._repo:
            raise RuntimeError("MHFormer: V2M_MHFORMER_REPO nao definido.")
        self._loaded = True

    def infer_raw(self, frames: Sequence[np.ndarray], ctx: dict) -> list[dict]:
        # A integracao completa exige importar o modelo do repo + janela temporal.
        raise RuntimeError(
            "MHFormer: integracao de inferencia nao habilitada nesta build. "
            "Use o lifter analitico ou habilite o repo (docs/BACKENDS.md)."
        )
