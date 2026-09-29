"""Exportacao GLB: estrutura validada de forma independente com pygltflib."""
from __future__ import annotations

from pathlib import Path

import pytest

from core import mixamo as mx
from core.export_glb import build_glb
from core.retarget import Retargeter


@pytest.fixture(scope="module")
def glb_path(tmp_path_factory, synthetic_poses):
    anim = Retargeter(fps=30.0).retarget(synthetic_poses)
    out = tmp_path_factory.mktemp("glb") / "model.glb"
    info = build_glb(anim, out)
    return out, info


def test_glb_gerado(glb_path):
    path, info = glb_path
    assert path.exists()
    assert path.stat().st_size > 1000
    assert info["bones"] == 65
    assert info["channels"] == len(mx.ANIMATED_BONES) + 1


def test_glb_magic(glb_path):
    path, _ = glb_path
    head = path.read_bytes()[:4]
    assert head == b"glTF"


def test_glb_estrutura_com_pygltflib(glb_path):
    pygltflib = pytest.importorskip("pygltflib")
    path, _ = glb_path
    g = pygltflib.GLTF2().load(str(path))
    assert g.asset.version == "2.0"
    assert len(g.skins) == 1
    assert len(g.skins[0].joints) == 65
    assert len(g.animations) == 1
    assert len(g.animations[0].channels) == len(mx.ANIMATED_BONES) + 1
    names = [n.name for n in g.nodes]
    assert f"{mx.PREFIX}Hips" in names
    assert f"{mx.PREFIX}LeftForeArm" in names
    # IBM presente e com 65 matrizes
    ibm_acc = g.accessors[g.skins[0].inverseBindMatrices]
    assert ibm_acc.type == "MAT4"
    assert ibm_acc.count == 65


def test_glb_quaternions_normalizados(glb_path):
    pygltflib = pytest.importorskip("pygltflib")
    import numpy as np

    path, _ = glb_path
    g = pygltflib.GLTF2().load(str(path))
    blob = g.binary_blob()
    anim = g.animations[0]
    checked = 0
    for ch in anim.channels:
        if ch.target.path != "rotation":
            continue
        acc = g.accessors[anim.samplers[ch.sampler].output]
        view = g.bufferViews[acc.bufferView]
        off = (view.byteOffset or 0) + (acc.byteOffset or 0)
        arr = np.frombuffer(blob, dtype=np.float32, count=acc.count * 4, offset=off).reshape(-1, 4)
        norms = np.linalg.norm(arr, axis=1)
        assert np.allclose(norms, 1.0, atol=1e-3)
        checked += 1
    assert checked >= 40
