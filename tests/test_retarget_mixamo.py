"""Retarget: contrato do esqueleto Mixamo, rigidez e FK reverso."""
from __future__ import annotations

import numpy as np

from core import mixamo as mx
from core.retarget import Retargeter, fk_validation_error


def test_contrato_de_65_ossos():
    mx.assert_contract()
    assert len(mx.BONE_NAMES) == 65
    assert len(mx.ANIMATED_BONES) + len(mx.END_CAPS) == 65


def test_nomes_essenciais_presentes():
    for name in [
        "Hips", "Spine", "Spine1", "Spine2", "Neck", "Head",
        "LeftShoulder", "LeftArm", "LeftForeArm", "LeftHand",
        "RightArm", "RightForeArm", "RightHand",
        "LeftUpLeg", "LeftLeg", "LeftFoot", "LeftToeBase",
        "RightUpLeg", "RightLeg", "RightFoot", "RightToeBase",
        "LeftHandThumb1", "RightHandPinky4",
    ]:
        assert name in mx.BONE_INDEX, f"osso ausente: {name}"


def test_hierarquia_correta():
    assert mx.BONE_PARENT["Hips"] is None
    assert mx.BONE_PARENT["Spine"] == "Hips"
    assert mx.BONE_PARENT["Spine1"] == "Spine"
    assert mx.BONE_PARENT["Spine2"] == "Spine1"
    assert mx.BONE_PARENT["Neck"] == "Spine2"
    assert mx.BONE_PARENT["Head"] == "Neck"
    assert mx.BONE_PARENT["LeftArm"] == "LeftShoulder"
    assert mx.BONE_PARENT["LeftForeArm"] == "LeftArm"
    assert mx.BONE_PARENT["LeftHand"] == "LeftForeArm"
    assert mx.BONE_PARENT["LeftLeg"] == "LeftUpLeg"
    assert mx.BONE_PARENT["LeftFoot"] == "LeftLeg"
    assert mx.BONE_PARENT["LeftToeBase"] == "LeftFoot"


def test_tpose_bracos_ao_longo_de_x():
    pos = mx.rest_world_positions()
    assert pos["LeftForeArm"][0] > pos["LeftArm"][0] > pos["LeftShoulder"][0]
    assert pos["RightForeArm"][0] < pos["RightArm"][0] < pos["RightShoulder"][0]
    # altura humana plausivel
    assert 1.5 < pos["HeadTop_End"][1] < 2.0
    assert abs(pos["LeftToeBase"][1]) < 0.2


def test_retarget_gera_animacao(synthetic_poses):
    anim = Retargeter(fps=30.0).retarget(synthetic_poses)
    assert anim.num_frames == len(synthetic_poses)
    for bone in mx.ANIMATED_BONES:
        q = anim.rotations[bone]
        assert q.shape == (len(synthetic_poses), 4)
        assert np.isfinite(q).all()
        assert np.allclose(np.linalg.norm(q, axis=1), 1.0, atol=1e-4)


def test_fk_reverso_erro_pequeno(synthetic_poses):
    anim = Retargeter(fps=30.0).retarget(synthetic_poses)
    fk = fk_validation_error(anim, synthetic_poses)
    # O retarget e RIGIDO: as direcoes dos ossos sao reproduzidas com erro
    # angular minimo. O erro posicional residual vem da diferenca de
    # proporcoes entre a fonte e o rig Mixamo (comprimentos de osso fixos).
    assert fk["max_angle_deg"] < 5.0, f"erro angular alto: {fk['max_angle_deg']}"
    assert fk["max_position_m"] < 0.12, f"erro posicional alto: {fk['max_position_m']}"


def test_comprimentos_de_osso_constantes(synthetic_poses):
    """Rigidez: |filho - pai| deve ser constante (= offset de rest) em todos os frames."""
    anim = Retargeter(fps=30.0).retarget(synthetic_poses)
    for t in range(anim.num_frames):
        local = {b: anim.rotations[b][t] for b in mx.ANIMATED_BONES}
        world = mx.fk_world(local, anim.root_translation[t])
        for name in mx.BONE_NAMES:
            parent = mx.BONE_PARENT[name]
            if parent is None:
                continue
            d = np.linalg.norm(world[name] - world[parent])
            assert abs(d - np.linalg.norm(mx.BONE_OFFSET[name])) < 1e-6
