"""Backend WHAM — recuperacao de malha humana com mundo/gravidade (CVPR 2024).

Estado da arte em human mesh recovery a partir de video; estima pose SMPL
"world-grounded" (com contato no chao). Codigo sob **MIT**, mas depende do
**SMPL/SMPL-X**, cujo modelo corporal exige licenca da Max Planck e e
**NON-COMMERCIAL**.

Por isso a categoria e "nao_comercial": o codigo e livre, os pesos/body model
que ele usa nao sao. Leia `docs/LICENSING.md` antes de usar em produto.
"""
from __future__ import annotations

from typing import Sequence

import numpy as np

from core.adapter import CAT_NAO_COMERCIAL, AdapterConfig, ConfigurableAdapter
from core.canonical import COCO17

# SMPL-24 -> COCO-17
SMPL_24 = [
    "pelvis", "left_hip", "right_hip", "spine1", "left_knee", "right_knee", "spine2",
    "left_ankle", "right_ankle", "spine3", "left_foot", "right_foot", "neck",
    "left_collar", "right_collar", "head", "left_shoulder", "right_shoulder",
    "left_elbow", "right_elbow", "left_wrist", "right_wrist", "left_hand", "right_hand",
]

_SMPL_TO_COCO = {
    "head": "nose",
    "left_shoulder": "left_shoulder", "right_shoulder": "right_shoulder",
    "left_elbow": "left_elbow", "right_elbow": "right_elbow",
    "left_wrist": "left_wrist", "right_wrist": "right_wrist",
    "left_hip": "left_hip", "right_hip": "right_hip",
    "left_knee": "left_knee", "right_knee": "right_knee",
    "left_ankle": "left_ankle", "right_ankle": "right_ankle",
}


def _cfg() -> AdapterConfig:
    return AdapterConfig(
        name="wham",
        display_name="WHAM (SMPL world-grounded)",
        native_layout=SMPL_24,
        mapping={k: COCO17.index(v) for k, v in _SMPL_TO_COCO.items()},
        coord="pixel",
        temporal="window",
        window=243,
        multi_person=False,
        license="Codigo MIT, mas depende do SMPL/SMPL-X (Max Planck) — NON-COMMERCIAL",
        license_category=CAT_NAO_COMERCIAL,
        notes="Malha SMPL world-grounded. Exige corpo SMPL (licenca nao-comercial) + torch. "
              "Mapeamento SMPL-24 -> COCO-17 e parcial.",
    )


class WHAMBackend(ConfigurableAdapter):
    def __init__(self) -> None:
        super().__init__(_cfg())
        self.name = "wham"
        self.display_name = "WHAM (SMPL world-grounded)"

    def is_available(self) -> bool:
        try:
            import torch  # noqa: F401
            import smplx  # type: ignore  # noqa: F401
        except Exception:
            return False
        return False      # corpo SMPL exige licenca/arquivos que nao vem no pip

    def availability_reason(self) -> str:
        return ("requer o repo WHAM + corpo SMPL/SMPL-X (licenca NON-COMMERCIAL da Max Planck). "
                "Ver docs/BACKENDS.md e docs/LICENSING.md.")

    def load(self, config: dict | None = None) -> None:
        self._loaded = True

    def infer_raw(self, frames: Sequence[np.ndarray], ctx: dict) -> list[dict]:
        raise RuntimeError(
            "WHAM: inferencia nao habilitada (depende do corpo SMPL, licenca nao-comercial). "
            "Ver docs/BACKENDS.md."
        )
