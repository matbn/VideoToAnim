"""Regressao de skinning da malha anexada.

Historico do bug: o Mixamo emite um Cluster para TODO osso do esqueleto, e os que
nao influenciam nada vem **sem** ``Indexes``/``Weights``. Eu tratava "sem Indexes"
como "influencia todos os controlos", e como o corpo tem 12 clusters vazios, esses
12 ossos ganhavam peso 1.0 em TODOS os 6658 vertices do corpo e dominavam o top-4
— o corpo ficava preso a pontas de dedo/end-caps em vez de Hips/Spine/pernas.
Agora clusters vazios sao simplesmente ignorados.

Estes testes garantem que:
  1. a malha anexada realmente DEFORMA entre dois instantes da animacao;
  2. o corpo NAO fica preso a ossos de dedo (a patologia medida).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core import mixamo as mx
from core.pipeline import run_pipeline
from tools.verify_glb_skinning import measure

FBX_BENCH = Path(
    r"C:\Users\mathe\.openclaw-autoclaw\agents\auto-coder\workspace\.openclaw-attachments"
    r"\20260927-224442-49828ea5-86b-MixamoChar.fbx"
)

# ossos de dedo (nao recebem animacao no nosso retarget: ficam em identidade)
_FINGER_BONES = {b for b in mx.BONE_NAMES if "Hand" in b and b[-1].isdigit()}


def _build(tmp_path, registry, sample_video, mesh=None):
    return run_pipeline(
        job_id="skin", video_path=sample_video, backend_name="synthetic",
        params={"fps": 12}, registry=registry, job_dir=tmp_path / "skin", mesh_path=mesh,
    )


def test_malha_de_sticks_defoma(tmp_path, registry, sample_video):
    res = _build(tmp_path, registry, sample_video)
    r = measure(res["glb"]["path"])
    assert r["ok"], r
    assert r["deforms"], r
    assert r["moved_fraction"] > 0.5, r
    assert r["bind_pose_ok"], r        # skinning bate com a geometria (bind exato)
    assert not r["rigid_like"], r      # nao pode se mover como corpo rigido


@pytest.mark.skipif(not FBX_BENCH.exists(), reason="FBX de benchmark nao encontrado")
def test_malha_fbx_do_usuario_defoma(tmp_path, registry, sample_video):
    res = _build(tmp_path, registry, sample_video, FBX_BENCH)
    assert res["glb"]["mesh"] == "user_mesh"
    r = measure(res["glb"]["path"])
    assert r["ok"], r
    assert r["deforms"], r
    # A cabeca fica PARADA de proposito neste clipe (calibracao pelo frame 0 +
    # orientacao pela linha dos olhos); e o cabelo e ~metade dos vertices, entao
    # a fracao global cai — o que prova a saude do skinning e os MEMBROS moverem.
    assert r["moved_fraction"] > 0.3, r
    assert r["moved_fraction_limbs"] > 0.8, r
    assert r["unweighted"] == 0, r
    assert r["weights_normalized"], r
    assert r["bind_pose_ok"], r
    assert not r["rigid_like"], r


@pytest.mark.skipif(not FBX_BENCH.exists(), reason="FBX de benchmark nao encontrado")
def test_corpo_nao_fica_preso_a_dedos(tmp_path, registry, sample_video):
    """Regressao da causa raiz (clusters vazios virando 'influencia todos')."""
    res = _build(tmp_path, registry, sample_video, FBX_BENCH)
    r = measure(res["glb"]["path"])
    top = [t["joint"].replace("mixamorig:", "") for t in r["top_joints"][:5]]
    dedos = [b for b in top if b in _FINGER_BONES]
    assert not dedos, f"ossos de dedo entre os mais influentes do corpo: {dedos} (top5={top})"
    # e o corpo tem que usar muitas juntas, nao um punhado
    assert r["joints_in_use"] >= 40, r["joints_in_use"]


@pytest.mark.skipif(not FBX_BENCH.exists(), reason="FBX de benchmark nao encontrado")
def test_clusters_vazios_sao_ignorados_e_nada_fica_sem_peso(tmp_path, registry, sample_video):
    res = _build(tmp_path, registry, sample_video, FBX_BENCH)
    stats = res["mesh_report"]["stats"]
    assert stats["empty_clusters"] > 0, stats
    assert stats["unweighted"] == 0, stats
