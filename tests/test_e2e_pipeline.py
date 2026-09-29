"""Teste ponta a ponta: video -> backend -> retarget -> GLB + FBX."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.pipeline import run_pipeline
from core.registry import build_registry

# Backend 2D temporario (so keypoints 2D): prova que o lifter analitico fecha o
# pipeline. O plugin de exemplo do projeto fica desativado por padrao, entao o
# teste cria o proprio.
PLUGIN_2D = '''
from typing import Sequence
import numpy as np
from core.adapter import AdapterConfig, ConfigurableAdapter
from core.canonical import COCO17


class TwoDProbe(ConfigurableAdapter):
    def __init__(self):
        cfg = AdapterConfig(name="two_d_probe", display_name="TwoD Probe",
                            native_layout=list(COCO17),
                            mapping={n: i for i, n in enumerate(COCO17)})
        super().__init__(cfg)
        self.name = "two_d_probe"
        self.display_name = cfg.display_name

    def infer_raw(self, frames: Sequence[np.ndarray], ctx: dict) -> list[dict]:
        w = float(ctx.get("width", 640)); h = float(ctx.get("height", 480))
        cx = w / 2.0
        kp = np.array([
            [cx, h * 0.20],
            [cx - 0.02 * w, h * 0.19], [cx + 0.02 * w, h * 0.19],
            [cx - 0.05 * w, h * 0.20], [cx + 0.05 * w, h * 0.20],
            [cx - 0.10 * w, h * 0.32], [cx + 0.10 * w, h * 0.32],
            [cx - 0.18 * w, h * 0.46], [cx + 0.18 * w, h * 0.46],
            [cx - 0.24 * w, h * 0.60], [cx + 0.24 * w, h * 0.60],
            [cx - 0.06 * w, h * 0.58], [cx + 0.06 * w, h * 0.58],
            [cx - 0.07 * w, h * 0.78], [cx + 0.07 * w, h * 0.78],
            [cx - 0.07 * w, h * 0.97], [cx + 0.07 * w, h * 0.97],
        ], np.float32)
        return [{"kp": kp, "score": np.ones(17, np.float32)} for _ in frames]


BACKEND = TwoDProbe()
'''


@pytest.fixture()
def registry_2d(tmp_path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "two_d_probe.py").write_text(PLUGIN_2D, encoding="utf-8")
    return build_registry(plugins)


def _run(tmp_path: Path, registry, video: str, backend: str, **params):
    job_dir = tmp_path / f"job_{backend}"
    return run_pipeline(
        job_id=f"job_{backend}",
        video_path=video,
        backend_name=backend,
        params={"fps": 12, "smoothing": "oneeuro", **params},
        registry=registry,
        job_dir=job_dir,
    )


def test_e2e_backend_synthetic(tmp_path, registry, sample_video):
    result = _run(tmp_path, registry, sample_video, "synthetic")
    assert (tmp_path / "job_synthetic" / "model.glb").exists()
    assert (tmp_path / "job_synthetic" / "model.fbx").exists()
    assert result["metrics"]["frames"] == 24
    assert result["glb"]["bones"] == 65
    assert result["fbx"]["bones"] == 65
    assert result["metrics"]["video_span_s"] > 0


def test_e2e_backend_2d_com_lifter(tmp_path, registry_2d, sample_video):
    """Backend so-2D + lifter analitico deve completar o pipeline."""
    result = _run(tmp_path, registry_2d, sample_video, "two_d_probe")
    assert (tmp_path / "job_two_d_probe" / "model.glb").exists()
    assert result["metrics"]["frames"] == 24


def test_e2e_sem_lifter_falha_para_backend_2d(tmp_path, registry_2d, sample_video):
    with pytest.raises(RuntimeError):
        _run(tmp_path, registry_2d, sample_video, "two_d_probe", lift_2d_to_3d=False)


def test_e2e_backend_inexistente(tmp_path, registry, sample_video):
    with pytest.raises(ValueError):
        _run(tmp_path, registry, sample_video, "nao_existe")
