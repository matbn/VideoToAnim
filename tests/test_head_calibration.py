"""Calibracao da cabeca pelo frame de referencia.

Regressao do vies: o solver orienta a cadeia Neck/Head por um unico vetor
estimado (nose - chest) e o pescoco "nasce" com dezenas de graus de rotacao
sistematica (personagem olhando para baixo/para o lado).
"""
import numpy as np

from core import mixamo as mx
from core.retarget import Animation, calibrate_head


def _rot_x(deg: float) -> np.ndarray:
    a = np.radians(deg) / 2.0
    return np.array([np.sin(a), 0.0, 0.0, np.cos(a)], np.float64)


def _anim(neck: np.ndarray, com_head: bool = True) -> Animation:
    rots = {"Neck": neck.copy()}
    if com_head:
        rots["Head"] = np.tile([0.0, 0.0, 0.0, 1.0], (neck.shape[0], 1))
    return Animation(fps=30.0, num_frames=neck.shape[0], bone_names=list(rots),
                     rotations=rots, root_translation=np.zeros((neck.shape[0], 3)))


def test_frame_de_referencia_vira_identidade() -> None:
    neck = np.stack([_rot_x(60.0), _rot_x(62.0), _rot_x(58.0)])
    anim = _anim(neck)
    rep = calibrate_head(anim)
    assert rep["applied"] and rep["frame"] == 0
    assert rep["bones"]["Neck"]["angle_deg"] > 50.0
    q0 = anim.rotations["Neck"][0]
    assert abs(abs(float(q0[3])) - 1.0) < 1e-6, "frame de referencia deveria virar identidade"
    assert float(rep["bones"]["Head"]["angle_deg"]) < 1e-6


def test_movimento_relativo_preservado() -> None:
    neck = np.stack([_rot_x(60.0), _rot_x(75.0), _rot_x(45.0), _rot_x(70.0)])
    anim = _anim(neck)
    antes = anim.rotations["Neck"].copy()
    calibrate_head(anim)
    depois = anim.rotations["Neck"]

    def rel(q1: np.ndarray, q2: np.ndarray) -> np.ndarray:
        return mx.quat_mul(q2, mx.quat_conj(q1))

    for i in (1, 2, 3):
        assert np.allclose(rel(antes[0], antes[i]), rel(depois[0], depois[i]), atol=1e-6)


def test_sem_ossos_nao_faz_nada() -> None:
    anim = _anim(np.stack([_rot_x(30.0)]), com_head=False)
    anim.rotations.pop("Neck")
    rep = calibrate_head(anim)
    assert rep["applied"] is False and rep["bones"] == {}


def test_frame_fora_do_intervalo_e_reprovado_no_limite() -> None:
    neck = np.stack([_rot_x(20.0), _rot_x(30.0)])
    anim = _anim(neck)
    rep = calibrate_head(anim, frame=99)
    assert rep["frame"] == 1  # clampado ao ultimo frame
