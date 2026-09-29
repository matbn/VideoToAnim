"""Backend de exemplo (plugin externo).

Este arquivo demonstra o mecanismo de extensao: basta soltar um ``*.py`` em
``plugins/`` que exponha ``BACKEND`` (classe ou instancia) e ele aparece
automaticamente no dropdown, sem editar o nucleo nem o frontend.

O backend aqui e trivial e deterministico: emite uma pose "em pe" estatica
centralizada no frame. Serve para validar o fluxo de registro/extensao.
"""
from __future__ import annotations

from typing import Sequence

import numpy as np

from core.adapter import AdapterConfig, ConfigurableAdapter
from core.canonical import COCO17


class CenterStandBackend(ConfigurableAdapter):
    def __init__(self) -> None:
        cfg = AdapterConfig(
            name="center_stand",
            display_name="Center Stand (plugin de exemplo)",
            native_layout=list(COCO17),
            mapping={n: i for i, n in enumerate(COCO17)},
            coord="pixel",
            license="MIT (exemplo)",
            notes="Plugin de exemplo: pose estatica centralizada.",
        )
        super().__init__(cfg)
        self.name = "center_stand"
        self.display_name = cfg.display_name

    def infer_raw(self, frames: Sequence[np.ndarray], ctx: dict) -> list[dict]:
        w = int(ctx.get("width", 640))
        h = int(ctx.get("height", 480))
        cx, cy = w / 2.0, h / 2.0
        base = np.array([
            [cx, cy - 0.40 * h],       # nose
            [cx - 0.02 * w, cy - 0.42 * h],
            [cx + 0.02 * w, cy - 0.42 * h],
            [cx - 0.05 * w, cy - 0.41 * h],
            [cx + 0.05 * w, cy - 0.41 * h],
            [cx - 0.10 * w, cy - 0.30 * h],
            [cx + 0.10 * w, cy - 0.30 * h],
            [cx - 0.18 * w, cy - 0.15 * h],
            [cx + 0.18 * w, cy - 0.15 * h],
            [cx - 0.24 * w, cy + 0.00 * h],
            [cx + 0.24 * w, cy + 0.00 * h],
            [cx - 0.06 * w, cy - 0.02 * h],
            [cx + 0.06 * w, cy - 0.02 * h],
            [cx - 0.07 * w, cy + 0.22 * h],
            [cx + 0.07 * w, cy + 0.22 * h],
            [cx - 0.07 * w, cy + 0.45 * h],
            [cx + 0.07 * w, cy + 0.45 * h],
        ], dtype=np.float32)
        return [{"kp": base, "score": np.ones(17, np.float32)} for _ in frames]


# ---------------------------------------------------------------------------
# Este plugin fica DESATIVADO por padrao: ele existe como exemplo reproduzivel
# do mecanismo de extensao, mas nao aparece no dropdown.
#
# Para reativar: descomente a linha BACKEND abaixo e apague (ou ponha False na)
# linha PLUGIN_DISABLED. Em seguida o backend "center_stand" aparece no dropdown
# na proxima carga, sem tocar no nucleo nem no frontend.
# ---------------------------------------------------------------------------
# BACKEND = CenterStandBackend()
PLUGIN_DISABLED = True
