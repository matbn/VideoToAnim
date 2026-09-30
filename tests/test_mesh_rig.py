"""Testes: esqueleto DA PROPRIA malha (pivos proprios) + exportacao com ele.

Malhas do Mixamo autorigger vem em T-Pose com o formato padrao de nomes, mas
com tamanhos de osso proprios. Para a deformacao ser exata, o GLB exportado
passa a usar o esqueleto de rest do arquivo da malha:
  * `core.mesh.mesh_skeleton` le o bind (FBX: TransformLink dos clusters;
    GLB: inverseBindMatrices) e deriva locais + offsets de mundo;
  * `core.export_glb.build_glb(..., rig=...)` escreve nodes com os locais do
    arquivo e converte a animacao osso a osso;
  * a anticolisao roda com os offsets da malha quando disponivel.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from core import mixamo as mx
from core.export_glb import build_glb
from core.mesh import _read_accessor, mesh_skeleton
from core.refine.constraints import euler_xyz_deg_to_quat
from core.retarget import Animation
from tools.verify_glb_skinning import _mat_from_quat_t, measure

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


def _rig_sintetico(escala_braco: float = 1.25) -> dict:
    offsets = {b: np.asarray(mx.BONE_OFFSET[b], np.float64).copy() for b in mx.BONE_NAMES}
    for b in ("LeftArm", "LeftForeArm", "LeftHand"):
        offsets[b] = offsets[b] * escala_braco
    world_pos = {}
    for b in mx.BONE_NAMES:
        p = mx.BONE_PARENT[b]
        world_pos[b] = offsets[b].copy() if p is None else world_pos[p] + offsets[b]
    bones = {
        b: {"parent": mx.BONE_PARENT[b], "translation": offsets[b],
            "rotation": mx.quat_identity(), "world_pos": world_pos[b],
            "world_rot": mx.quat_identity(), "world_offset": offsets[b]}
        for b in mx.BONE_NAMES
    }
    return {"ok": True, "source": "synthetic", "n_bones": len(mx.BONE_NAMES),
            "missing": [], "bones": bones}


def _world_rest(g, name: str) -> np.ndarray:
    """Translacao mundo (rest) do node `name` pela cadeia de nos do GLB."""
    nodes = list(g.nodes)
    parent = {}
    for i, n in enumerate(nodes):
        for c in (n.children or []):
            parent[c] = i
    idx = next(i for i, n in enumerate(nodes)
               if (n.name or "").replace("mixamorig:", "") == name)
    chain = []
    i = idx
    while i is not None:
        chain.append(i)
        i = parent.get(i)
    chain.reverse()
    m = np.eye(4)
    for i in chain:
        n = nodes[i]
        tr = np.array(n.translation if n.translation else [0, 0, 0], np.float64)
        ro = np.array(n.rotation if n.rotation else [0, 0, 0, 1], np.float64)
        m = m @ _mat_from_quat_t(ro, tr)
    return m[:3, 3]


def test_export_glb_com_rig_da_malha_sintetico(tmp_path):
    from pygltflib import GLTF2

    rig = _rig_sintetico()
    q = euler_xyz_deg_to_quat(np.array([0.0, 0.0, -90.0]))  # braco para baixo
    anim = _anim({"LeftArm": q}, T=2)

    out_ref = tmp_path / "ref.glb"
    info_ref = build_glb(anim, out_ref)
    assert info_ref["rig"] == "reference"

    out = tmp_path / "rig.glb"
    info = build_glb(anim, out, rig=rig)
    assert info["rig"] == "mesh"
    g = GLTF2().load(str(out))
    nodes = list(g.nodes)
    by_name = {(n.name or "").replace("mixamorig:", ""): i for i, n in enumerate(g.nodes)}

    # 1) nodes carregam os locais do rig da malha
    for b in ("LeftArm", "LeftForeArm", "LeftHand"):
        tr = np.asarray(nodes[by_name[b]].translation, np.float64)
        assert np.allclose(tr, rig["bones"][b]["translation"], atol=1e-5), b

    # 2) skinning end-to-end: vertice 100% LeftHand no punho do rig -> esperado
    bp = rig["bones"]
    a = np.asarray(bp["LeftArm"]["world_pos"], np.float64)       # ombro
    e = np.asarray(bp["LeftForeArm"]["world_pos"], np.float64)   # cotovelo
    w = np.asarray(bp["LeftHand"]["world_pos"], np.float64)      # punho
    r = mx.quat_to_matrix(q)
    e2 = a + r @ (e - a)
    w2 = e2 + r @ (w - e)
    v0 = w + np.array([0.0, 0.0, 0.10])
    esperado = w2 + r @ (v0 - w)

    skin = g.skins[0]
    joints = list(skin.joints)
    ibm = _read_accessor(g, skin.inverseBindMatrices).reshape(-1, 4, 4).transpose(0, 2, 1)
    rot_series = None
    for c in g.animations[0].channels:
        if c.target.node == by_name["LeftArm"] and c.target.path == "rotation":
            sam = g.animations[0].samplers[c.sampler]
            rot_series = _read_accessor(g, sam.output)
    assert rot_series is not None
    m = np.eye(4)
    for b in ("Hips", "Spine", "Spine1", "Spine2", "LeftShoulder", "LeftArm",
              "LeftForeArm", "LeftHand"):
        i = by_name[b]
        n = nodes[i]
        tr = np.array(n.translation if n.translation else [0, 0, 0], np.float64)
        ro = np.array(n.rotation if n.rotation else [0, 0, 0, 1], np.float64)
        if b == "LeftArm":
            ro = np.asarray(rot_series[0], np.float64)
        m = m @ _mat_from_quat_t(ro, tr)
    j_local = joints.index(by_name["LeftHand"])
    skinned = (m @ ibm[j_local]) @ np.append(v0, 1.0)
    assert np.allclose(skinned[:3], esperado, atol=1e-4), (skinned[:3], esperado)


@pytest.mark.skipif(not FBX_BENCH.exists(), reason="FBX de benchmark nao encontrado")
def test_mesh_skeleton_fbx_real():
    rig = mesh_skeleton(FBX_BENCH)
    assert rig is not None and rig["ok"], rig
    assert rig["n_bones"] == 65 and not rig["missing"]
    b = rig["bones"]
    # valores medidos no arquivo (juntas exatas da malha em T-pose)
    assert abs(b["LeftForeArm"]["world_pos"][0] - 0.462) < 6e-3
    assert abs(b["LeftForeArm"]["world_pos"][1] - 1.446) < 6e-3
    assert abs(float(np.linalg.norm(b["LeftForeArm"]["translation"])) - 0.27874) < 5e-3
    assert abs(float(b["LeftArm"]["world_offset"][0]) - 0.1232) < 6e-3
    # T-pose limpa: anticolisao no esqueleto da malha nao dispara
    from core.refine import collision as col

    offs = {k: np.asarray(v["world_offset"], np.float64) for k, v in b.items()}
    _out, rep = col.apply_collision(_anim({}, T=2), skeleton_offsets=offs)
    assert rep["skeleton_source"] == "mesh"
    assert rep["frames_corrected_total"] == 0


@pytest.mark.skipif(not FBX_BENCH.exists(), reason="FBX de benchmark nao encontrado")
def test_pipeline_exporta_com_pivos_da_malha(tmp_path, registry, sample_video):
    from pygltflib import GLTF2

    from core.pipeline import run_pipeline

    res = run_pipeline(job_id="rig", video_path=sample_video, backend_name="synthetic",
                       params={"fps": 12}, registry=registry, job_dir=tmp_path / "rig",
                       mesh_path=FBX_BENCH)
    assert res["glb"]["rig"] == "mesh"
    rig = mesh_skeleton(FBX_BENCH)
    g = GLTF2().load(res["glb"]["path"])
    world = _world_rest(g, "LeftArm")
    assert np.allclose(world, rig["bones"]["LeftArm"]["world_pos"], atol=2e-3), (world, rig["bones"]["LeftArm"]["world_pos"])
    m = measure(res["glb"]["path"])
    assert m["ok"] and m["deforms"] and m["bind_pose_ok"], m
