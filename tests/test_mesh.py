"""Malha do usuário: compatibilidade de esqueleto e anexo ao output."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from core import mixamo as mx
from core.export_glb import build_glb
from core.mesh import (check_compatibility, detect_format, extract_bone_names,
                       load_fbx_mesh, load_glb_mesh)
from core.pipeline import run_pipeline
from core.registry import build_registry
from core.retarget import Retargeter

FBX_BENCH = Path(
    r"C:\Users\mathe\.openclaw-autoclaw\agents\auto-coder\workspace\.openclaw-attachments"
    r"\20260927-224442-49828ea5-86b-MixamoChar.fbx"
)


@pytest.fixture(scope="module")
def our_glb(tmp_path_factory, synthetic_poses):
    anim = Retargeter(fps=30.0).retarget(synthetic_poses)
    out = tmp_path_factory.mktemp("mesh") / "model.glb"
    build_glb(anim, out)
    return out


def test_detecta_formato_glb(our_glb):
    assert detect_format(our_glb) == "glb"


@pytest.mark.skipif(not FBX_BENCH.exists(), reason="FBX de benchmark nao encontrado")
def test_detecta_e_le_fbx_binario_do_mixamo():
    assert detect_format(FBX_BENCH) == "fbx-binary"
    bones = extract_bone_names(FBX_BENCH)
    assert len(set(bones) & set(mx.BONE_NAMES)) >= 60


@pytest.mark.skipif(not FBX_BENCH.exists(), reason="FBX de benchmark nao encontrado")
def test_fbx_real_e_compativel_e_anexavel():
    """O Mixamo exporta FBX por padrao: ele deve ser anexavel, sem exigir conversao."""
    rep = check_compatibility(FBX_BENCH)
    assert rep.format == "fbx-binary"
    assert rep.compatible is True, rep.messages
    assert rep.attachable is True
    assert len(rep.matched) == 65


@pytest.mark.skipif(not FBX_BENCH.exists(), reason="FBX de benchmark nao encontrado")
def test_load_fbx_mesh_extrai_geometria_e_pesos():
    m = load_fbx_mesh(FBX_BENCH)
    assert m["stats"]["meshes"] >= 1
    assert m["stats"]["unweighted"] == 0        # todo vertice tem peso de osso
    pos = m["positions"]
    assert pos.shape[1] == 3
    height = float(pos[:, 1].max() - pos[:, 1].min())
    assert 1.4 < height < 2.2, f"altura fora do esperado em metros: {height}"
    assert float(pos[:, 1].min()) > -0.2        # pes proximos do chao
    assert int(m["joints"].max()) < 65
    assert np.allclose(m["weights"].sum(axis=1), 1.0, atol=1e-4)
    assert m["indices"].size % 3 == 0


@pytest.mark.skipif(not FBX_BENCH.exists(), reason="FBX de benchmark nao encontrado")
def test_pipeline_anexa_o_fbx_do_mixamo(tmp_path, registry, sample_video):
    res = run_pipeline(
        job_id="fbx_mesh", video_path=sample_video, backend_name="synthetic",
        params={"fps": 12}, registry=registry, job_dir=tmp_path / "fbx_mesh", mesh_path=FBX_BENCH,
    )
    assert res["glb"]["mesh"] == "user_mesh"
    assert res["mesh_report"]["format"] == "fbx-binary"
    assert res["mesh_report"]["attachable"] is True
    assert res["mesh_report"]["stats"]["unweighted"] == 0


def test_glb_proprio_e_compativel_e_anexavel(our_glb):
    rep = check_compatibility(our_glb)
    assert rep.compatible is True
    assert rep.attachable is True
    assert len(rep.matched) == 65


def test_load_glb_mesh_remapeia_juntas(our_glb):
    mesh = load_glb_mesh(our_glb)
    assert mesh["positions"].shape[1] == 3
    assert mesh["joints"].shape[1] == 4
    assert mesh["joints"].max() < 65
    assert mesh["indices"].max() < mesh["positions"].shape[0]
    # pesos normalizados
    s = mesh["weights"].sum(axis=1)
    assert np.allclose(s, 1.0, atol=1e-4)


def test_malha_incompativel_e_detectada(tmp_path, our_glb):
    """Renomeia os ossos -> o detector deve reprovar e NAO anexar."""
    from pygltflib import GLTF2

    g = GLTF2().load(str(our_glb))
    for n in g.nodes:
        if n.name:
            n.name = "X" + n.name
    bad = tmp_path / "bad.glb"
    g.save_binary(str(bad))

    rep = check_compatibility(bad)
    assert rep.compatible is False
    assert rep.attachable is False
    assert len(rep.missing) >= 45
    with pytest.raises(ValueError):
        load_glb_mesh(bad)


def test_pipeline_usa_a_malha_do_usuario(tmp_path, registry, sample_video, our_glb):
    res = run_pipeline(
        job_id="with_mesh", video_path=sample_video, backend_name="synthetic",
        params={"fps": 12, "smoothing": "oneeuro"}, registry=registry,
        job_dir=tmp_path / "with_mesh", mesh_path=our_glb,
    )
    assert res["glb"]["mesh"] == "user_mesh"
    assert res["mesh_report"]["compatible"] is True
    assert res["mesh_report"]["attachable"] is True


def test_pipeline_sem_malha_usa_sticks(tmp_path, registry, sample_video):
    res = run_pipeline(
        job_id="no_mesh", video_path=sample_video, backend_name="synthetic",
        params={"fps": 12, "smoothing": "oneeuro"}, registry=registry,
        job_dir=tmp_path / "no_mesh",
    )
    assert res["glb"]["mesh"] == "stick_capsules"
    assert res["mesh_report"] is None


def test_pipeline_com_malha_incompativel_avisa_e_nao_quebra(tmp_path, registry, sample_video, our_glb):
    from pygltflib import GLTF2

    g = GLTF2().load(str(our_glb))
    for n in g.nodes:
        if n.name:
            n.name = "Y" + n.name
    bad = tmp_path / "bad.glb"
    g.save_binary(str(bad))

    res = run_pipeline(
        job_id="bad_mesh", video_path=sample_video, backend_name="synthetic",
        params={"fps": 12}, registry=registry, job_dir=tmp_path / "bad_mesh", mesh_path=bad,
    )
    assert res["glb"]["mesh"] == "stick_capsules"      # fallback
    assert res["mesh_report"]["compatible"] is False
    assert res["mesh_report"]["messages"]


# --- FBX ASCII -------------------------------------------------------------
ASCII_FBX = '''
; FBX 7.4.0 project file
FBXHeaderExtension:  {
	FBXHeaderVersion: 1003
	FBXVersion: 7400
}
GlobalSettings:  {
	Version: 1000
	Properties70:  {
		P: "UnitScaleFactor", "double", "Number", "", 1
	}
}
Objects:  {
	Geometry: 100, "Geometry::Body", "Mesh" {
		Vertices: *9 {
			a: 0,0,0,1,0,0,0,2,0
		}
		PolygonVertexIndex: *3 {
			a: 0,1,-3
		}
	}
	Model: 200, "Model::mixamorig:Hips", "LimbNode" {
	}
	Model: 201, "Model::mixamorig:Spine", "LimbNode" {
	}
	Deformer: 300, "Deformer::Skin", "Skin" {
	}
	Deformer: 400, "SubDeformer::Cluster", "Cluster" {
		Indexes: *3 {
			a: 0,1,2
		}
		Weights: *3 {
			a: 1,1,1
		}
	}
}
Connections:  {
	C: "OO",300,100
	C: "OO",400,300
	C: "OO",200,400
}
'''


def test_fbx_ascii_e_parseado(tmp_path):
    """FBX ASCII (outra opcao de export do Mixamo) usa o MESMO caminho de extracao."""
    from core.fbx import read_fbx, unit_scale_factor
    from core.mesh import load_fbx_mesh

    p = tmp_path / "ascii.fbx"
    p.write_text(ASCII_FBX, encoding="utf-8")
    assert detect_format(p) == "fbx-ascii"

    root, version = read_fbx(p)
    assert version == 7400
    assert root.child("Objects") is not None
    assert abs(unit_scale_factor(root) - 1.0) < 1e-9

    bones = extract_bone_names(p)
    assert "Hips" in bones and "Spine" in bones

    rep = check_compatibility(p)
    assert rep.format == "fbx-ascii"
    assert rep.compatible is False          # esqueleto incompleto: reprovado, como deve ser
    assert any("ausentes" in m for m in rep.messages)

    m = load_fbx_mesh(p)
    assert m["positions"].shape == (3, 3)
    assert m["indices"].shape[0] == 3
    assert int(m["joints"][0][0]) == mx.BONE_INDEX["Hips"]
    assert np.allclose(m["weights"].sum(axis=1), 1.0, atol=1e-6)
    # UnitScaleFactor=1 => cm -> m: a altura de 2 unidades vira 0.02 m
    assert abs(float(m["positions"][2][1]) - 0.02) < 1e-6


def test_fbx_sem_skin_da_erro_explicativo(tmp_path):
    """Caso classico: download do Mixamo com 'Without Skin'."""
    from core.mesh import load_fbx_mesh

    p = tmp_path / "noskin.fbx"
    p.write_text(
        ASCII_FBX.replace('Deformer: 300, "Deformer::Skin", "Skin" {',
                          'Deformer: 300, "Deformer::Skin", "SkinX" {'),
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as e:
        load_fbx_mesh(p)
    assert "With Skin" in str(e.value)
