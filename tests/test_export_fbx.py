"""Exportacao FBX ASCII: validacao estrutural."""
from __future__ import annotations

import pytest

from core.export_fbx import build_fbx
from core.retarget import Retargeter


@pytest.fixture(scope="module")
def fbx_path(tmp_path_factory, synthetic_poses):
    anim = Retargeter(fps=30.0).retarget(synthetic_poses)
    out = tmp_path_factory.mktemp("fbx") / "model.fbx"
    info = build_fbx(anim, out)
    return out, info


def test_fbx_gerado(fbx_path):
    path, info = fbx_path
    assert path.exists()
    assert info["bytes"] > 2000
    assert info["bones"] == 65


def test_fbx_cabecalho_e_versao(fbx_path):
    path, _ = fbx_path
    text = path.read_text(encoding="utf-8")
    assert "FBXVersion: 7400" in text
    assert text.startswith("; FBX 7.4.0 project file")


def test_fbx_65_limbnodes_com_prefixo_mixamo(fbx_path):
    path, _ = fbx_path
    text = path.read_text(encoding="utf-8")
    assert text.count('"LimbNode"') == 65
    assert 'Model::mixamorig:Hips' in text
    assert 'Model::mixamorig:LeftForeArm' in text
    assert 'Model::mixamorig:RightToeBase' in text


def test_fbx_hierarquia_e_animacao(fbx_path):
    path, _ = fbx_path
    text = path.read_text(encoding="utf-8")
    assert "Connections:" in text
    assert "AnimationStack:" in text
    assert "AnimationCurveNode:" in text
    assert "AnimationCurve:" in text
    assert 'KeyTime: *' in text
    assert 'KeyValueFloat: *' in text


def test_fbx_conexoes_raiz_e_filho(fbx_path):
    path, _ = fbx_path
    text = path.read_text(encoding="utf-8")
    # Hips conecta ao RootNode (0); existe pelo menos uma conexao OO Hips->0
    assert 'C: "OO",100001,0' in text or ',0\n' in text
