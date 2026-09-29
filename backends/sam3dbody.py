"""Backend SAM 3D Body (Meta) — recuperacao de corpo 3D a partir de UMA imagem.

Paper/release de 2025 (Meta), estado da arte em human mesh recovery de imagem
unica. Saida no formato **MHR** (Meta Human Rig), nao em COCO-17.

Licenca: **SAM License** (Meta, atualizada em 19/11/2025) -> categoria
"licenca_a_parte". Nao e non-commercial (a concessao permite uso comercial),
mas e share-alike: derivados/distribuicao seguem os termos da SAM License, e ha
restricoes de uso militar/export control. Leia `docs/LICENSING.md`.
"""
from __future__ import annotations

from typing import Sequence

import numpy as np

from core.adapter import CAT_LICENCA_A_PARTE, AdapterConfig, ConfigurableAdapter
from core.canonical import COCO17

# MHR -> COCO-17 (mapeamento PARCIAL: o rig MHR tem muito mais juntas que o COCO)
_MHR_TO_COCO = {
    "nose": "nose",
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
        name="sam3dbody",
        display_name="SAM 3D Body (Meta, MHR)",
        native_layout=list(_MHR_TO_COCO.keys()),
        mapping={k: COCO17.index(v) for k, v in _MHR_TO_COCO.items()},
        coord="pixel",
        temporal="frame",
        multi_person=True,
        license="SAM License (Meta) — permissiva para uso comercial, porem share-alike",
        license_category=CAT_LICENCA_A_PARTE,
        notes="Recuperacao de MALHA (nao so keypoints): saida MHR. Mapeamento MHR->COCO-17 e "
              "PARCIAL (o rig tem muito mais juntas). Exige pesos e codigo do repo oficial.",
    )


class SAM3DBodyBackend(ConfigurableAdapter):
    def __init__(self) -> None:
        super().__init__(_cfg())
        self.name = "sam3dbody"
        self.display_name = "SAM 3D Body (Meta, MHR)"
        self._predictor = None

    def is_available(self) -> bool:
        try:
            import sam_3d_body  # type: ignore  # noqa: F401
        except Exception:
            return False
        return True

    def availability_reason(self) -> str:
        try:
            import sam_3d_body  # type: ignore  # noqa: F401
        except Exception:
            return ("requer o pacote oficial do SAM 3D Body + checkpoint (licenca SAM; "
                    "ver docs/BACKENDS.md). Saida em MHR, nao em COCO-17.")
        return ""

    def load(self, config: dict | None = None) -> None:
        self._loaded = True

    def infer_raw(self, frames: Sequence[np.ndarray], ctx: dict) -> list[dict]:
        raise RuntimeError(
            "SAM 3D Body: inferencia nao habilitada nesta build (requer pesos oficiais). "
            "A saida e MHR (malha), nao COCO-17 — ver docs/BACKENDS.md."
        )
