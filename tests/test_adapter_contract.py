"""Contrato do Adapter: validacao da configuracao e da saida canonica."""
from __future__ import annotations

import numpy as np
import pytest

from core.adapter import ConfigurableAdapter
from core.canonical import NUM_JOINTS

REQUIRED = {"vitpose", "mediapipe", "synthetic"}
ALL_VISIBLE = [
    "vitpose", "mediapipe", "rtmpose", "motionbert", "yolopose",
    "sam3dbody", "wham", "openpose", "simplebaseline", "mhformer",
]


def test_backends_esperados_registrados(registry):
    names = set(registry.names())
    assert REQUIRED.issubset(names), f"faltando: {REQUIRED - names}"


def test_todos_os_backends_visiveis_registrados(registry):
    names = set(registry.names())
    faltando = [n for n in ALL_VISIBLE if n not in names]
    assert not faltando, f"backends nao registrados: {faltando}"


def test_categorias_de_licenca(registry):
    """A licenca e categoria de primeira classe (nao um motivo para esconder)."""
    from core.adapter import LICENSE_CATEGORIES

    cats = registry.by_license_category()
    assert {"vitpose", "mediapipe", "rtmpose"} <= set(cats["livre"])
    assert {"openpose", "simplebaseline", "wham"} <= set(cats["nao_comercial"])
    assert {"mhformer", "yolopose", "sam3dbody"} <= set(cats["licenca_a_parte"])
    for rec in registry.list():
        assert rec.license_category in LICENSE_CATEGORIES, rec.name
        assert rec.license, f"{rec.name} sem descricao de licenca"

def test_mapping_body25_do_openpose(registry):
    """OpenPose BODY_25 -> COCO-17: MidHip/Neck nao existem no COCO-17."""
    from core.canonical import COCO_INDEX

    cfg = registry.get("openpose").backend.adapter_config
    assert "mid_hip" not in cfg.mapping
    assert "neck" not in cfg.mapping
    assert cfg.mapping["left_hip"] == COCO_INDEX["left_hip"]
    assert cfg.mapping["right_ankle"] == COCO_INDEX["right_ankle"]
    assert cfg.license_category == "nao_comercial"


def test_configuracao_de_cada_backend_e_valida(registry):
    for rec in registry.list():
        backend = rec.backend
        if not isinstance(backend, ConfigurableAdapter):
            continue
        problems = backend.adapter_config.validate()
        assert problems == [], f"{rec.name}: {problems}"


def test_layout_nativo_sem_nomes_duplicados(registry):
    for rec in registry.list():
        backend = rec.backend
        if not isinstance(backend, ConfigurableAdapter):
            continue
        layout = backend.adapter_config.native_layout
        assert len(layout) == len(set(layout)), f"{rec.name}: layout com duplicatas"


def test_conversao_canonica_gera_coco17(registry):
    """Um vetor nativo de zeros deve produzir FramePose (17,2)/(17,) validos."""
    for rec in registry.list():
        backend = rec.backend
        if not isinstance(backend, ConfigurableAdapter):
            continue
        cfg = backend.adapter_config
        n = len(cfg.native_layout)
        kp = np.zeros((n, 2), np.float32)
        sc = np.ones(n, np.float32)
        pose = cfg.to_canonical(kp, sc, 640, 480)
        assert pose.kp2d.shape == (NUM_JOINTS, 2)
        assert pose.score.shape == (NUM_JOINTS,)
        assert np.all(pose.score >= 0.0) and np.all(pose.score <= 1.0)
        assert np.isfinite(pose.kp2d).all()


def test_backend_disponivel_produz_saida_canonica(registry, dummy_frames):
    """Backends disponiveis sem dependencia pesada devem rodar de verdade."""
    ctx = {"width": 640, "height": 480, "fps": 30.0}
    checked = 0
    for rec in registry.list():
        if rec.name != "synthetic":
            continue
        backend = rec.backend
        if not rec.available:
            continue
        out = backend.infer_video(dummy_frames, ctx)
        assert len(out) == len(dummy_frames)
        for pose in out:
            assert pose.kp2d.shape == (NUM_JOINTS, 2)
            assert np.isfinite(pose.kp2d).all()
        checked += 1
    assert checked >= 1, "o backend synthetic (testes) deveria estar disponivel"


def test_plugin_de_exemplo_esta_desativado(registry):
    """O plugin de exemplo existe no disco mas NAO aparece no dropdown."""
    assert registry.get("center_stand") is None
    assert any("example_backend.py" in d for d in registry.disabled), registry.disabled


def test_synthetic_fica_oculto_do_dropdown(registry):
    rec = registry.get("synthetic")
    assert rec is not None, "synthetic deve seguir no registry (usado pelos testes)"
    assert rec.hidden is True
    assert rec.as_public_dict()["hidden"] is True


def test_y_down_e_coordenadas_em_pixel(registry):
    """Todos os backends embutidos usam pixel absoluto com y para baixo."""
    for rec in registry.list():
        backend = rec.backend
        if not isinstance(backend, ConfigurableAdapter):
            continue
        if rec.source.startswith("plugin"):
            continue
        assert backend.adapter_config.coord in ("pixel", "normalized")
        assert backend.adapter_config.y_flip is False
