"""Testes da anticolisao (core/refine/collision.py).

Cobrem:
  1. deteccao de tamanhos por execucao (malha -> escala; fallback referencia);
  2. FK interna identica a do mixamo;
  3. T-pose limpa -> zero correcoes e saida bit-exata;
  4. penetracao sintetica (braco dentro do tronco) -> corrigida;
  5. a mesma pose com malha menor (escala detectada 0.55) -> sem correcao;
  6. `mesh_bone_lengths` (GLB roundtrip pelo build_glb; FBX real quando existe);
  7. integracao com `refine_animation` (etapa "collision" no relatorio).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from core import mixamo as mx
from core.refine import collision as col
from core.refine.constraints import euler_xyz_deg_to_quat
from core.retarget import Animation

FBX_BENCH = Path(
    r"C:\Users\mathe\.openclaw-autoclaw\agents\auto-coder\workspace\.openclaw-attachments"
    r"\20260927-224442-49828ea5-86b-MixamoChar.fbx"
)


def _anim(rots: dict, T: int = 3, fps: float = 30.0) -> Animation:
    base = {b: np.tile(mx.quat_identity(), (T, 1)) for b in mx.ANIMATED_BONES}
    for b, q in rots.items():
        base[b] = np.tile(np.asarray(q, np.float64), (T, 1))
    root = np.tile(np.asarray(mx.BONE_OFFSET["Hips"], np.float64), (T, 1))
    return Animation(fps=fps, num_frames=T, bone_names=list(mx.ANIMATED_BONES),
                     rotations=base, root_translation=root)


def _fk(anim: Animation, t: int = 0):
    local = {b: anim.rotations[b][t] for b in anim.rotations}
    return col._fk_frame(local, anim.root_translation[t])


def _dist_pair(anim: Animation, a: str, b: str, t: int = 0) -> float:
    pos, _rot = _fk(anim, t)
    sa = col._seg_of(a, pos)
    sb = col._seg_of(b, pos)
    d, _c1, _c2 = col._seg_seg_distance(sa[0], sa[1], sb[0], sb[1])
    return d


def test_detected_sizes_referencia_sem_malha():
    det = col.detect_sizes(None)
    assert det["source"] == "reference"
    assert det["global_scale"] == 1.0
    assert det["radii"]["LeftForeArm"] == pytest.approx(col.REF_RADII["LeftForeArm"])


def test_detected_sizes_escalam_com_a_malha():
    mesh = {b: v * 1.5 for b, v in col.REF_LENGTHS.items()}
    det = col.detect_sizes(mesh)
    assert det["source"] == "mesh"
    assert det["global_scale"] == pytest.approx(1.5, abs=0.01)
    assert det["radii"]["LeftForeArm"] == pytest.approx(
        col.REF_RADII["LeftForeArm"] * 1.5, rel=1e-6)
    small = {b: v * 0.6 for b, v in col.REF_LENGTHS.items()}
    det2 = col.detect_sizes(small)
    assert det2["global_scale"] == pytest.approx(0.6, abs=0.01)


def test_fk_frame_bate_com_o_fk_do_mixamo():
    rng = np.random.default_rng(3)
    local = {b: mx.quat_normalize(rng.normal(size=4)) for b in mx.BONE_NAMES}
    root_t = np.array([0.1, 0.9, 0.2])
    pos, _rot = col._fk_frame(local, root_t)
    ref = mx.fk_world(local, root_t)
    for b in mx.BONE_NAMES:
        assert np.allclose(pos[b], ref[b], atol=1e-9), b


def test_tpose_limpa_nao_muda_nada():
    anim = _anim({}, T=6)
    out, rep = col.apply_collision(anim)
    assert rep["frames_corrected_total"] == 0
    assert rep["corrections_total"] == 0
    assert rep["detected"]["source"] == "reference"
    for b, series in anim.rotations.items():
        assert np.array_equal(out.rotations[b], series)


def test_braco_dentro_do_tronco_e_corrigido():
    q = euler_xyz_deg_to_quat(np.array([0.0, 0.0, 120.0]))
    anim = _anim({"LeftArm": q}, T=3)
    d0 = _dist_pair(anim, "LeftArm", "Spine2")
    det = col.detect_sizes(None)
    pen0 = det["radii"]["LeftArm"] + det["radii"]["Spine2"] - d0
    assert pen0 > 0.03  # a pose sintetica realmente penetra
    cfg = col.CollisionConfig(passes=3, damping=0.7)
    out, rep = col.apply_collision(anim, config=cfg)
    assert rep["frames_corrected_total"] >= 1
    assert rep["corrections_total"] >= 1
    d1 = _dist_pair(out, "LeftArm", "Spine2")
    assert d1 > d0 + 0.01, (d0, d1)
    assert any("Spine2" in k for k in rep["pairs"]), rep["pairs"]


def test_malha_menor_muda_a_execucao():
    q = euler_xyz_deg_to_quat(np.array([0.0, 0.0, 105.0]))
    anim = _anim({"LeftArm": q}, T=3)
    _out1, rep1 = col.apply_collision(anim)
    assert rep1["frames_corrected_total"] >= 1
    small = {b: v * 0.55 for b, v in col.REF_LENGTHS.items()}
    _out2, rep2 = col.apply_collision(anim, mesh_lengths=small)
    assert rep2["detected"]["source"] == "mesh"
    assert rep2["detected"]["global_scale"] == pytest.approx(0.55, abs=0.01)
    assert rep2["frames_corrected_total"] == 0


def test_mesh_bone_lengths_roundtrip_glb(tmp_path):
    from core.export_glb import build_glb
    from core.mesh import mesh_bone_lengths

    anim = _anim({}, T=2)
    glb = tmp_path / "rig.glb"
    build_glb(anim, glb)
    L = mesh_bone_lengths(glb)
    assert L, "sem comprimentos detectados"
    assert "LeftForeArm" in L and "Spine" in L
    for bone, ref in col.REF_LENGTHS.items():
        if bone in L:
            assert abs(L[bone] - ref) < 1e-4, bone


@pytest.mark.skipif(not FBX_BENCH.exists(), reason="FBX de benchmark nao encontrado")
def test_mesh_bone_lengths_fbx_real():
    from core.mesh import mesh_bone_lengths

    L = mesh_bone_lengths(FBX_BENCH)
    n_end = sum(1 for b in mx.BONE_NAMES if mx.BONE_IS_END[b])
    assert len(L) == len(mx.BONE_NAMES) - n_end  # todos os ossos animados
    assert L["LeftArm"] == pytest.approx(0.27687, abs=5e-3)
    assert L["LeftForeArm"] == pytest.approx(0.27874, abs=5e-3)
    assert L["Spine"] == pytest.approx(0.16255, abs=5e-3)
    det = col.detect_sizes(L)
    assert det["source"] == "mesh"
    assert 0.8 < det["global_scale"] < 1.2


def test_refine_animation_inclui_etapa_de_colisao():
    from core.refine import refine_animation

    q = euler_xyz_deg_to_quat(np.array([0.0, 0.0, 120.0]))
    anim = _anim({"LeftArm": q}, T=2)
    out, rep = refine_animation(anim, collision_config={"enabled": True},
                                filters_config=None)
    assert "collision" in rep.stages
    assert rep.collision is not None
    assert rep.collision["frames_corrected_total"] >= 1
    assert "collision" in rep.as_dict()
    assert out is not anim



def test_anticolisao_pode_rodar_no_esqueleto_da_malha():
    anim = _anim({}, T=2)
    big = {b: np.asarray(off, np.float64) * 1.3 for b, off in mx.BONE_OFFSET.items()}
    _out, rep = col.apply_collision(anim, skeleton_offsets=big)
    assert rep["skeleton_source"] == "mesh"
    assert rep["frames_corrected_total"] == 0  # T-pose continua limpa
    pos, _rot = col._fk_frame({b: mx.quat_identity() for b in mx.BONE_NAMES},
                              mx.BONE_OFFSET["Hips"], offsets=big)
    ref = mx.rest_world_positions()
    esperado = mx.BONE_OFFSET["Hips"] + 1.3 * (ref["LeftLeg"] - mx.BONE_OFFSET["Hips"])
    assert np.allclose(pos["LeftLeg"], esperado, atol=1e-9)
