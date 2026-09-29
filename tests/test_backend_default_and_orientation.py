"""Backend padrao efetivo e orientacao da camera (frente = +Z)."""
from __future__ import annotations

import numpy as np

from backends.mediapipe_backend import BLAZE_33, MediaPipeBackend
from core.registry import build_registry


def test_default_e_vitpose_quando_disponivel(registry, monkeypatch):
    rec = registry.get("vitpose")
    monkeypatch.setattr(rec, "available", True)
    assert registry.default().name == "vitpose"
    assert registry.preferred_default_name() == "vitpose"


def test_default_cai_para_um_backend_disponivel(registry, monkeypatch):
    """Sem os pesos do ViTPose, o padrao NAO pode ser o vitpose (era o bug do ModuleNotFoundError)."""
    monkeypatch.setattr(registry.get("vitpose"), "available", False)
    d = registry.default()
    assert d is not None
    assert d.name != "vitpose"
    assert d.available is True
    assert d.hidden is False, "o padrao deve ser um backend visivel no dropdown"
    assert registry.preferred_default_name() == "vitpose"  # a preferencia continua registrada


class _L:
    def __init__(self, x, y, z, visibility=1.0):
        self.x, self.y, self.z, self.visibility = x, y, z, visibility


def test_pessoa_de_frente_gera_personagem_olhando_para_z_positivo():
    """MediaPipe: z menor = mais perto da camera. Pessoa encarando a camera =>
    nariz com z negativo => o canonico (que inverte z) deve ficar com nariz em +Z."""
    lm = [_L(0.5, 0.5, 0.0) for _ in BLAZE_33]
    lm[BLAZE_33.index("nose")] = _L(0.50, 0.20, -0.12)          # mais perto da camera
    lm[BLAZE_33.index("left_shoulder")] = _L(0.40, 0.35, 0.0)
    lm[BLAZE_33.index("right_shoulder")] = _L(0.60, 0.35, 0.0)
    lm[BLAZE_33.index("left_hip")] = _L(0.45, 0.60, 0.0)
    lm[BLAZE_33.index("right_hip")] = _L(0.55, 0.60, 0.0)

    kp3 = MediaPipeBackend()._world(lm)
    nose = kp3[BLAZE_33.index("nose")]
    ls = kp3[BLAZE_33.index("left_shoulder")]
    rs = kp3[BLAZE_33.index("right_shoulder")]
    chest = 0.5 * (ls + rs)
    assert nose[1] > chest[1]     # y-up: nariz acima do peito
    assert nose[2] > chest[2]     # e NA FRENTE (+Z) -> camera "frente" mostra o rosto


def test_presets_de_camera_olham_para_o_lado_certo():
    """Sanidade da convencao: o rig tem os pes em +Z na T-pose."""
    from core import mixamo as mx

    pos = mx.rest_world_positions()
    assert pos["LeftToeBase"][2] > pos["Hips"][2], "pes devem apontar para +Z"
