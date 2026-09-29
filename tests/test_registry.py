"""Registry: descoberta embutida, default e extensao via plugins."""
from __future__ import annotations

from pathlib import Path

from core.registry import build_registry

PLUGIN_SRC = '''
from typing import Sequence
import numpy as np
from core.adapter import AdapterConfig, ConfigurableAdapter
from core.canonical import COCO17


class TempBackend(ConfigurableAdapter):
    def __init__(self):
        cfg = AdapterConfig(
            name="temp_plugin",
            display_name="Temp Plugin",
            native_layout=list(COCO17),
            mapping={n: i for i, n in enumerate(COCO17)},
        )
        super().__init__(cfg)
        self.name = "temp_plugin"
        self.display_name = cfg.display_name

    def infer_raw(self, frames: Sequence[np.ndarray], ctx: dict) -> list[dict]:
        c = np.zeros((17, 2), np.float32)
        return [{"kp": c, "score": np.ones(17, np.float32)} for _ in frames]


BACKEND = TempBackend()
'''


def test_vitpose_e_a_preferencia_de_padrao(registry):
    # vitpose continua sendo o padrao PREFERIDO...
    assert registry.preferred_default_name() == "vitpose"
    # ...mas o padrao EFETIVO precisa estar disponivel (evita o ModuleNotFoundError)
    d = registry.default()
    assert d is not None
    assert d.available is True


def test_erros_de_descoberta_nao_derrubam_registry(registry):
    assert isinstance(registry.errors, list)


def test_plugin_novo_aparece_sem_editar_o_nucleo(tmp_path: Path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "temp_backend.py").write_text(PLUGIN_SRC, encoding="utf-8")

    reg = build_registry(plugins)
    assert "temp_plugin" in reg.names(), "plugin novo nao foi descoberto"
    rec = reg.get("temp_plugin")
    assert rec is not None
    assert rec.source.startswith("plugin")
    assert rec.available

    # executa de ponta a ponta (aplica o contrato canonico)
    import numpy as np

    out = rec.backend.infer_video([np.zeros((16, 16, 3), np.uint8)], {"width": 16, "height": 16})
    assert len(out) == 1
    assert out[0].kp2d.shape == (17, 2)

    # ao remover o arquivo, o backend deixa de existir no novo registry
    (plugins / "temp_backend.py").unlink()
    reg2 = build_registry(plugins)
    assert "temp_plugin" not in reg2.names()
