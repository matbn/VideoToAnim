"""Backend SimpleBaseline (microsoft/human-pose-estimation.pytorch).

Top-down, heatmap, COCO-17 nativo. Requer PyTorch e o repositorio/checkpoint.
"""
from __future__ import annotations

from pathlib import Path

import os
from typing import Sequence

import numpy as np

from core.adapter import CAT_NAO_COMERCIAL, AdapterConfig, ConfigurableAdapter
from core.canonical import COCO17


def _cfg() -> AdapterConfig:
    return AdapterConfig(
        name="simplebaseline",
        display_name="SimpleBaseline (ResNet, top-down)",
        native_layout=list(COCO17),
        mapping={n: i for i, n in enumerate(COCO17)},
        coord="pixel",
        temporal="frame",
        multi_person=True,
        license="MIT (Microsoft); pesos 'for research purpose'",
        license_category=CAT_NAO_COMERCIAL,
        notes="COCO-17 nativo, single-frame, requer bbox. Codigo de 2018.",
    )


class SimpleBaselineBackend(ConfigurableAdapter):
    def __init__(self) -> None:
        super().__init__(_cfg())
        self.name = "simplebaseline"
        self.display_name = "SimpleBaseline (ResNet, top-down)"
        self._repo = os.environ.get("V2M_SIMPLEBASELINE_REPO", "")
        self._ckpt = os.environ.get("V2M_SIMPLEBASELINE_CKPT", "")
        self._net = None

    def is_available(self) -> bool:
        try:
            import torch  # noqa: F401
        except Exception:
            return False
        if not self._repo:
            auto = (Path(__file__).resolve().parents[1] / "third_party"
                    / "human-pose-estimation.pytorch")
            if auto.exists():
                self._repo = str(auto)
        return bool(self._repo and self._ckpt)

    def availability_reason(self) -> str:
        try:
            import torch  # noqa: F401
        except Exception:
            return "requer PyTorch ('pip install torch')."
        return "defina V2M_SIMPLEBASELINE_REPO e V2M_SIMPLEBASELINE_CKPT."

    def load(self, config: dict | None = None) -> None:
        config = config or {}
        self._repo = config.get("repo") or self._repo
        self._ckpt = config.get("checkpoint") or self._ckpt
        self._loaded = True

    def infer_raw(self, frames: Sequence[np.ndarray], ctx: dict) -> list[dict]:
        raise RuntimeError(
            "SimpleBaseline: inferencia nao habilitada nesta build (requer repo+checkpoint). "
            "Ver docs/BACKENDS.md."
        )
