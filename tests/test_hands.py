"""Hand tracking: contrato da conversao landmark -> rotacoes dos dedos.

Cobre: (a) o bug historico do indice 0 (0 rotacoes aplicadas); (b) a cadeia
matematica nova (rotacao mundo da mao + resolucao hierarquica) — a direcao
final reconstruida de cada falange deve coincidir com o landmark observado.
"""
from __future__ import annotations

import numpy as np

from core import mixamo as mx
from core.hands import (HandFrame, _hand_world_rot, finger_rest_bones,
                        finger_rotations_from_landmarks, hands_debug_payload)


def _landmarks_aleatorias(seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.random((21, 3)) * 0.05 + np.array([0.0, 0.0, 1.0])


def test_converte_os_15_ossos_da_mao():
    rots = finger_rotations_from_landmarks(_landmarks_aleatorias(), "Left")
    assert len(rots) == 15
    assert set(rots) == set(finger_rest_bones("Left"))
    assert all(not nome.endswith("0") for nome in rots)  # bug antigo


def test_quaternions_unitarios():
    rots = finger_rotations_from_landmarks(_landmarks_aleatorias(7), "Right")
    for nome, q in rots.items():
        assert np.asarray(q).shape == (4,), nome
        assert abs(np.linalg.norm(q) - 1.0) < 1e-9, nome


def test_cadeia_reconstroi_as_falanges_observadas():
    """Com a rotacao mundo da mao, q1..q3 devem reproduzir t1..t3 exatamente.

    Reconstroi usando a MESMA composicao do FK do rig
    (rot = pai * local; direcao = rot . offset_do_filho).
    """
    lm = _landmarks_aleatorias(11)
    rng = np.random.default_rng(5)
    rh = rng.normal(size=4)
    rh /= np.linalg.norm(rh)
    rots = finger_rotations_from_landmarks(lm, "Left", hand_world_rot=rh)

    lm2 = np.stack([lm[:, 0], -lm[:, 1], -lm[:, 2]], axis=1)

    def unit(v):
        return v / np.linalg.norm(v)

    t1 = unit(lm2[6] - lm2[5])
    t2 = unit(lm2[7] - lm2[6])
    t3 = unit(lm2[8] - lm2[7])
    o2 = unit(np.asarray(mx.BONE_OFFSET["LeftHandIndex2"]))
    o3 = unit(np.asarray(mx.BONE_OFFSET["LeftHandIndex3"]))
    o4 = unit(np.asarray(mx.BONE_OFFSET["LeftHandIndex4"]))
    r1 = mx.quat_mul(rh, rots["LeftHandIndex1"])
    r2 = mx.quat_mul(r1, rots["LeftHandIndex2"])
    r3 = mx.quat_mul(r2, rots["LeftHandIndex3"])
    assert float(np.dot(mx.quat_rotate(r1, o2), t1)) > 0.9999
    assert float(np.dot(mx.quat_rotate(r2, o3), t2)) > 0.9999
    assert float(np.dot(mx.quat_rotate(r3, o4), t3)) > 0.9999


def test_rotacao_mundo_da_mao_usa_a_cadeia_do_rig():
    """_hand_world_rot = produto dos locais do Hips ate o Hand."""
    ident = np.array([0.0, 0.0, 0.0, 1.0])
    rotations = {b: np.tile(ident, (3, 1)) for b in mx.BONE_NAMES}
    qz = np.array([0.0, 0.0, np.sin(np.pi / 4), np.cos(np.pi / 4)])
    rotations["LeftArm"] = np.tile(qz, (3, 1))
    q = _hand_world_rot(rotations, 1, "Left")
    esperado = mx.quat_mul(mx.quat_mul(qz, np.array([0.0, 0.0, 0.0, 1.0])), np.array([0.0, 0.0, 0.0, 1.0]))
    # LeftHips, LeftUpLeg... -> o caminho Hips->...->Arm->ForeArm->Hand;
    # com apenas LeftArm != identidade, o produto = qz (aplicado na posicao da cadeia)
    assert np.allclose(q, qz, atol=1e-9)
    # e sem a rotacao, tudo identidade
    rotations["LeftArm"] = np.tile(ident, (3, 1))
    q2 = _hand_world_rot(rotations, 1, "Left")
    assert np.allclose(q2, ident, atol=1e-9)


def test_dobra_do_indicador_muda_apenas_o_indicador():
    lm = _landmarks_aleatorias(3)
    r0 = finger_rotations_from_landmarks(lm, "Left")
    lm2 = lm.copy()
    lm2[6] += np.array([0.0, 0.02, 0.0])
    lm2[7] += np.array([0.0, 0.04, 0.0])
    lm2[8] += np.array([0.0, 0.06, 0.0])
    r1 = finger_rotations_from_landmarks(lm2, "Left")

    def angulo(q1, q2):
        d = min(1.0, abs(float(np.dot(q1, q2))))
        return 2.0 * np.arccos(d)

    assert angulo(r0["LeftHandIndex1"], r1["LeftHandIndex1"]) > 0.05
    for nome in r0:
        if "Index" not in nome:
            assert np.allclose(r0[nome], r1[nome]), nome


def test_payload_debug_em_pixels_do_video():
    img = np.tile(np.array([[0.25, 0.5, 0.0]]), (21, 1))
    hf = HandFrame(left=None, right=None, left_img=img, right_img=None)
    p = hands_debug_payload([hf], width=1280, height=720)
    assert p["width"] == 1280 and p["height"] == 720
    assert p["frames"][0]["right"] is None
    assert len(p["frames"][0]["left"]) == 21
    assert p["frames"][0]["left"][0] == [320.0, 360.0]
    p2 = hands_debug_payload([HandFrame()], width=640, height=360)
    assert p2["frames"][0] == {"left": None, "right": None}
