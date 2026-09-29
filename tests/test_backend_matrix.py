"""Matriz de backends: cada backend registrado atravessa o pipeline completo.

Os backends pesados (ViTPose/MMPose) nao tem suas dependencias instaladas neste
ambiente. Para ainda assim validar que o MAPEAMENTO de cada backend funciona no
codigo de producao (adapter -> retarget -> export), este teste substitui apenas a
chamada ao modelo (`infer_raw`) por uma saida nativa construida a partir de uma
pose canonica conhecida — o mesmo truque de "stub de inferencia" da engenharia de
testes. Tudo o mais e o codigo real.
"""
from __future__ import annotations

import numpy as np
import pytest

from core.pipeline import run_pipeline
from core.registry import build_registry
from core.video import read_video

REGISTRABLE = [
    "vitpose", "mediapipe", "rtmpose", "motionbert", "yolopose",
    "sam3dbody", "wham", "openpose", "simplebaseline", "mhformer",
]


def _native_from_canonical(cfg, pose, width: int, height: int) -> np.ndarray:
    """Inverte o mapeamento do adapter: canonico COCO-17 -> layout nativo."""
    idx = cfg.native_index()
    kp = np.zeros((len(cfg.native_layout), 2), np.float32)
    for nname, cidx in cfg.mapping.items():
        ni = idx.get(nname)
        if ni is None:
            continue
        x, y = float(pose.kp2d[cidx, 0]), float(pose.kp2d[cidx, 1])
        if cfg.y_flip:
            y = height - y
        if cfg.coord == "normalized":
            x, y = x / width, y / height
        kp[ni] = (x, y)
    return kp


@pytest.mark.parametrize("name", REGISTRABLE)
def test_backend_atravessa_pipeline(name, tmp_path, registry, sample_video, synthetic_poses, monkeypatch):
    rec = registry.get(name)
    assert rec is not None, f"backend {name} nao registrado"
    backend = rec.backend
    cfg = backend.adapter_config

    info = read_video(sample_video, max_frames=24)
    n_frames = len(info.frames)

    raws = [
        {
            "kp": _native_from_canonical(cfg, synthetic_poses[i % len(synthetic_poses)], info.width, info.height),
            "score": np.ones(len(cfg.native_layout), np.float32),
        }
        for i in range(n_frames)
    ]

    cls = type(backend)
    monkeypatch.setattr(rec, "available", True)
    monkeypatch.setattr(rec, "reason", "")
    monkeypatch.setattr(cls, "is_available", lambda self: True)
    monkeypatch.setattr(cls, "availability_reason", lambda self: "")
    monkeypatch.setattr(cls, "load", lambda self, config=None: setattr(self, "_loaded", True))
    monkeypatch.setattr(cls, "infer_raw", lambda self, frames, ctx, _r=raws: _r)

    job_dir = tmp_path / f"job_{name}"
    result = run_pipeline(
        job_id=f"job_{name}",
        video_path=sample_video,
        backend_name=name,
        params={"fps": 12, "smoothing": "oneeuro"},
        registry=registry,
        job_dir=job_dir,
    )

    assert (job_dir / "model.glb").exists()
    assert (job_dir / "model.fbx").exists()
    assert result["metrics"]["frames"] == n_frames
    assert result["glb"]["bones"] == 65
    assert result["metrics"]["fk_max_angle_deg"] < 45.0


def test_todos_os_backends_registrados_tem_config_valida(registry):
    for rec in registry.list():
        cfg = getattr(rec.backend, "adapter_config", None)
        if cfg is None:
            continue
        assert cfg.validate() == [], f"{rec.name}: {cfg.validate()}"


def test_mapping_normalizado_do_mediapipe(registry):
    """MediaPipe usa coordenadas normalizadas: o adapter deve converter p/ pixel."""
    cfg = registry.get("mediapipe").backend.adapter_config
    assert cfg.coord == "normalized"
    kp = np.zeros((len(cfg.native_layout), 2), np.float32)
    kp[cfg.native_index()["left_shoulder"]] = (0.5, 0.25)
    pose = cfg.to_canonical(kp, None, 640, 480)
    from core.canonical import COCO_INDEX

    assert pose.kp2d[COCO_INDEX["left_shoulder"]][0] == pytest.approx(320.0)
    assert pose.kp2d[COCO_INDEX["left_shoulder"]][1] == pytest.approx(120.0)


def test_backends_removidos_por_licenca_nao_estao_no_registry(registry):
    """Nada e removido por licenca: todos ficam registrados COM categoria."""
    from core.adapter import LICENSE_CATEGORIES

    for name in ("openpose", "simplebaseline", "mhformer", "yolopose", "wham", "sam3dbody"):
        rec = registry.get(name)
        assert rec is not None, f"{name} deveria estar registrado com categoria"
        assert rec.license_category in LICENSE_CATEGORIES, name
