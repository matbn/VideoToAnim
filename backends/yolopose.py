"""Backend YOLO-pose (Ultralytics).

Detector+pontos em uma passada, muito rapido em GPU e popular por ser "one-shot".
Saida COCO-17 (o Ultralytics tambem expoe `keypoints.xy` em pixels e `keypoints.conf`).

Licenca: **AGPL-3.0** -> categoria "licenca_a_parte". O AGPL e copyleft forte:
usar em produto de rede exige abrir o codigo ou comprar licenca comercial da
Ultralytics (Enterprise). Por isso este backend NAO e "livre".
"""
from __future__ import annotations

from typing import Sequence

import numpy as np

from core.adapter import CAT_LICENCA_A_PARTE, AdapterConfig, ConfigurableAdapter
from core.canonical import COCO17


def _cfg() -> AdapterConfig:
    return AdapterConfig(
        name="yolopose",
        display_name="YOLO-pose (Ultralytics)",
        native_layout=list(COCO17),
        mapping={n: i for i, n in enumerate(COCO17)},
        score_map={n: i for i, n in enumerate(COCO17)},
        coord="pixel",
        temporal="frame",
        smoothing="none",
        multi_person=True,
        license="AGPL-3.0 (Ultralytics) — copyleft; licenca comercial separada disponivel",
        license_category=CAT_LICENCA_A_PARTE,
        notes="COCO-17. Usa keypoints.xy (pixels) e keypoints.conf. Requer "
              "'pip install ultralytics'. AGPL: avalie o impacto antes de distribuir.",
    )


class YOLOPoseBackend(ConfigurableAdapter):
    def __init__(self) -> None:
        super().__init__(_cfg())
        self.name = "yolopose"
        self.display_name = "YOLO-pose (Ultralytics)"
        self._model = None
        self._weights = "yolo11n-pose.pt"

    def is_available(self) -> bool:
        try:
            from ultralytics import YOLO  # noqa: F401
        except Exception:
            return False
        return True

    def availability_reason(self) -> str:
        try:
            from ultralytics import YOLO  # noqa: F401
        except Exception:
            return "requer 'pip install ultralytics' (ATENCAO: licenca AGPL-3.0)"
        return ""

    def load(self, config: dict | None = None) -> None:
        from ultralytics import YOLO

        config = config or {}
        self._weights = config.get("weights", self._weights)
        self._model = YOLO(self._weights)
        self._loaded = True

    def unload(self) -> None:
        self._model = None
        super().unload()

    def infer_raw(self, frames: Sequence[np.ndarray], ctx: dict) -> list[dict]:
        if self._model is None:
            self.load({})
        out: list[dict] = []
        for frame in frames:
            try:
                res = self._model.predict(frame, verbose=False)[0]
                kps = res.keypoints
                if kps is None or kps.xy is None or len(kps.xy) == 0:
                    out.append(None)
                    continue
                xy = kps.xy.cpu().numpy().astype(np.float32)          # (N,17,2) pixels
                conf = (kps.conf.cpu().numpy().astype(np.float32)
                        if kps.conf is not None else np.ones(xy.shape[:2], np.float32))
                best = int(np.argmax(conf.mean(axis=1)))
                out.append({"kp": xy[best], "score": conf[best]})
            except Exception:
                out.append(None)
        return out
