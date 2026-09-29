"""Backend OpenPose (BODY_25 -> COCO-17).

Bottom-up, multi-pessoa nativa, sem IDs persistentes entre frames.
LICENCA: OpenPose e codigo e pesos sao ACADEMIC/NON-PROFIT
NON-COMMERCIAL. Nao distribua em produto comercial sem licenca CMU.
"""
from __future__ import annotations

import os
from typing import Sequence

import numpy as np

from core.adapter import CAT_NAO_COMERCIAL, AdapterConfig, ConfigurableAdapter
from core.canonical import COCO17

BODY_25 = [
    "nose", "neck", "right_shoulder", "right_elbow", "right_wrist",
    "left_shoulder", "left_elbow", "left_wrist", "mid_hip", "right_hip",
    "right_knee", "right_ankle", "left_hip", "left_knee", "left_ankle",
    "right_eye", "left_eye", "right_ear", "left_ear", "left_big_toe",
    "left_small_toe", "left_heel", "right_big_toe", "right_small_toe", "right_heel",
]

_BODY25_TO_COCO = {
    "nose": "nose",
    "left_eye": "left_eye", "right_eye": "right_eye",
    "left_ear": "left_ear", "right_ear": "right_ear",
    "left_shoulder": "left_shoulder", "right_shoulder": "right_shoulder",
    "left_elbow": "left_elbow", "right_elbow": "right_elbow",
    "left_wrist": "left_wrist", "right_wrist": "right_wrist",
    "left_hip": "left_hip", "right_hip": "right_hip",
    "left_knee": "left_knee", "right_knee": "right_knee",
    "left_ankle": "left_ankle", "right_ankle": "right_ankle",
}


def _cfg() -> AdapterConfig:
    return AdapterConfig(
        name="openpose",
        display_name="OpenPose (BODY_25)",
        native_layout=BODY_25,
        mapping={k: COCO17.index(v) for k, v in _BODY25_TO_COCO.items()},
        coord="pixel",
        temporal="frame",
        multi_person=True,
        license="NAO-COMERCIAL (academic/non-profit research only)",
        license_category=CAT_NAO_COMERCIAL,
        notes="BODY_25 -> COCO-17; Neck e MidHip nao existem no COCO-17 (MidHip util como root). "
              "Fixar --keypoint_scale 0 (pixel). Score c==0 => ausente.",
    )


class OpenPoseBackend(ConfigurableAdapter):
    def __init__(self) -> None:
        super().__init__(_cfg())
        self.name = "openpose"
        self.display_name = "OpenPose (BODY_25)"
        self._wrapper = None
        self._models = os.environ.get("V2M_OPENPOSE_MODELS", "")

    def is_available(self) -> bool:
        try:
            import pyopenpose  # type: ignore  # noqa: F401
        except Exception:
            return False
        return bool(self._models)

    def availability_reason(self) -> str:
        try:
            import pyopenpose  # type: ignore  # noqa: F401
        except Exception:
            return "requer build C++ do OpenPose com BUILD_PYTHON=ON (ver docs/BACKENDS.md)."
        return "defina V2M_OPENPOSE_MODELS (pasta models/)."

    def load(self, config: dict | None = None) -> None:
        config = config or {}
        self._models = config.get("models") or self._models
        self._loaded = True

    def infer_raw(self, frames: Sequence[np.ndarray], ctx: dict) -> list[dict]:
        raise RuntimeError("OpenPose: inferencia nao habilitada nesta build (requer build C++).")
