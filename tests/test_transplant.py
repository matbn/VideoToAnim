# -*- coding: utf-8 -*-
"""Mistura de partes do corpo entre dois clipes (core/refine/transplant.py)."""
from __future__ import annotations

import numpy as np
import pytest

from core.mixamo import ANIMATED_BONES, BONE_PARENT
from core.refine import io as rio, transplant as tr
from core.retarget import Animation


def _anim(T=12, seed=1, fps=30.0, rot_bone="LeftArm", deg=80.0):
    """Clipe sintetico: dois ossos com rotacoes bem diferentes por semente."""
    rng = np.random.default_rng(seed)
    rots = {}
    for b in ANIMATED_BONES:
        q = rng.normal(size=(T, 4))
        rots[b] = q / np.linalg.norm(q, axis=1, keepdims=True)
    ang = np.radians(deg)
    t = np.arange(T) / max(T - 1, 1)
    s, c = np.sin(ang / 2 * t), np.cos(ang / 2 * t)
    rots[rot_bone] = np.stack([np.zeros(T), np.zeros(T), s, c], axis=1)
    return Animation(fps=fps, num_frames=T, bone_names=list(ANIMATED_BONES),
                     rotations=rots,
                     root_translation=np.stack([np.zeros(T), 0.98 + 0.1 * t, np.zeros(T)], axis=1),
                     meta={})


TOL_G = 1e-4   # graus; o erro real e ~2e-6 (float na cadeia de 7 niveis)


def _ang(w1, w2):
    dot = np.clip(np.abs(np.sum(w1 * w2, axis=1)), -1.0, 1.0)
    return np.degrees(2.0 * np.arccos(dot))


# --------------------------------------------------------------- catalogo
def test_partes_cobrem_o_rig_inteiro():
    total = set()
    for p in tr.PART_ORDER:
        total |= set(tr.part_bones(p))
    assert total == set(ANIMATED_BONES), sorted(set(ANIMATED_BONES) - total)


def test_torso_e_so_a_coluna():
    """Regressao: subarvore de Spine2 engolia bracos, pernas e cabeca."""
    b = tr.part_bones("torso")
    assert b == ["Spine", "Spine1", "Spine2"], b
    for nao in ("LeftArm", "RightArm", "LeftUpLeg", "Head", "Neck"):
        assert nao not in b


def test_quadril_e_a_so_raiz():
    assert tr.part_bones("hips") == ["Hips"]


def test_braco_inclui_deditos():
    b = tr.part_bones("left_arm")
    for esperado in ("LeftShoulder", "LeftArm", "LeftForeArm", "LeftHand", "LeftHandIndex1"):
        assert esperado in b, esperado
    assert "RightArm" not in b


def test_ordem_topologica_pai_antes_do_filho():
    for p in tr.PART_ORDER:
        bones = tr.part_bones(p)
        visto = set()
        for b in bones:
            parent = BONE_PARENT.get(b)
            if parent in set(bones):
                assert parent in visto, f"{p}: pai {parent} depois de {b}"
            visto.add(b)


def test_parte_desconhecida_da_erro():
    with pytest.raises(ValueError, match="parte desconhecida"):
        tr.part_bones("asa")


# ------------------------------------------------------------- a maths
def test_parte_transplantada_bate_exatamente_com_a_origem():
    """O motivo de existir: em espaco de MUNDO, nao copiando a local crua."""
    A, B = _anim(seed=1), _anim(seed=2)
    R, rep = tr.transplant(B, A, ["left_arm"])
    rw, aw = tr.world_rotation_series(R), tr.world_rotation_series(A)
    for b in rep["grafted_bones"]:
        assert _ang(rw[b], aw[b]).max() == pytest.approx(0.0, abs=TOL_G), b


def test_o_resto_continua_sendo_do_destino():
    A, B = _anim(seed=3), _anim(seed=4)
    R, rep = tr.transplant(B, A, ["left_arm"])
    rw, bw = tr.world_rotation_series(R), tr.world_rotation_series(B)
    for b in ANIMATED_BONES:
        if b in rep["grafted_bones"]:
            continue
        assert _ang(rw[b], bw[b]).max() == pytest.approx(0.0, abs=1e-4), b


def test_filhos_nao_escolhidos_giram_com_o_pai_transplantado():
    """Torso de A + braco de B: o braco e o de B, pendurado no torso de A."""
    A, B = _anim(seed=5), _anim(seed=6)
    R, _ = tr.transplant(B, A, ["torso"])
    # o local do braco continua sendo o do destino
    assert np.allclose(R.rotations["LeftArm"], B.rotations["LeftArm"], atol=1e-9)
    # ... e o mundo dele virou junto com o torso
    rw, bw, aw = (tr.world_rotation_series(x) for x in (R, B, A))
    assert _ang(rw["LeftArm"], aw["LeftArm"]).max() > 1.0
    assert _ang(rw["Spine2"], aw["Spine2"]).max() == pytest.approx(0.0, abs=TOL_G)


def test_multiplas_partes_de_jobs_diferentes():
    A, B = _anim(seed=7), _anim(seed=8)
    R, rep = tr.transplant(B, A, ["left_arm", "right_leg", "head"])
    rw, aw = tr.world_rotation_series(R), tr.world_rotation_series(A)
    for b in rep["grafted_bones"]:
        assert _ang(rw[b], aw[b]).max() == pytest.approx(0.0, abs=TOL_G), b
    assert len(rep["grafted_bones"]) == (19 + 4 + 2)


def test_trajetoria_da_raiz_so_vem_com_o_quadril():
    A, B = _anim(seed=9), _anim(seed=10)
    R, rep = tr.transplant(B, A, ["left_arm"])
    assert rep["copied_root_translation"] is False
    assert np.allclose(R.root_translation, B.root_translation)

    R2, rep2 = tr.transplant(B, A, ["hips"])
    assert rep2["copied_root_translation"] is True
    assert np.allclose(R2.root_translation, A.root_translation)


def test_raiz_pode_ser_forcada():
    A, B = _anim(seed=11), _anim(seed=12)
    R, _ = tr.transplant(B, A, ["left_arm"], copy_root_translation=True)
    assert np.allclose(R.root_translation, A.root_translation)


def test_relatorio_de_diferenca_diz_zero():
    A, B = _anim(seed=13), _anim(seed=14)
    R, _ = tr.transplant(B, A, ["left_arm"])
    d = tr.difference_report(B, R, A)
    assert d["mean_deg"] == pytest.approx(0.0, abs=TOL_G)
    assert d["bones"] == 19


# ------------------------------------------------------------- validacao
def test_clipes_de_tamanhos_diferentes_sao_recusados():
    A, B = _anim(T=12, seed=15), _anim(T=20, seed=16)
    with pytest.raises(ValueError, match="frames diferentes"):
        tr.transplant(B, A, ["left_arm"])


def test_fps_diferente_e_recusado():
    A, B = _anim(fps=30.0, seed=17), _anim(fps=24.0, seed=18)
    with pytest.raises(ValueError, match="fps diferentes"):
        tr.transplant(B, A, ["left_arm"])


def test_nenhuma_parte_recusada():
    A, B = _anim(seed=19), _anim(seed=20)
    with pytest.raises(ValueError, match="nenhuma parte"):
        tr.transplant(B, A, [])


def test_transplante_e_idempotente():
    A, B = _anim(seed=21), _anim(seed=22)
    R1, _ = tr.transplant(B, A, ["left_arm"])
    R2, _ = tr.transplant(R1, A, ["left_arm"])
    for b in ANIMATED_BONES:
        assert np.allclose(R1.rotations[b], R2.rotations[b], atol=1e-9), b


def test_volta_ao_original_quando_a_parte_vem_do_proprio_destino():
    A = _anim(seed=23)
    R, _ = tr.transplant(A, A, ["left_arm"])
    for b in ANIMATED_BONES:
        assert np.allclose(R.rotations[b], A.rotations[b], atol=1e-9), b


def test_sobrevive_ao_ida_e_volta_em_json(tmp_path):
    A, B = _anim(seed=24), _anim(seed=25)
    R, _ = tr.transplant(B, A, ["left_arm", "torso"])
    p = tmp_path / "mix.json"
    rio.save_animation(R, p)
    de_volta = rio.load_animation(p)
    assert de_volta.num_frames == R.num_frames
    rw, aw = tr.world_rotation_series(de_volta), tr.world_rotation_series(A)
    for b in de_volta.meta["transplant_bones"]:
        assert _ang(rw[b], aw[b]).max() == pytest.approx(0.0, abs=1e-3), b


def test_historico_guarda_o_snapshot(tmp_path):
    from core.refine.boneedit import EditHistory
    A, B = _anim(seed=26), _anim(seed=27)
    R, _ = tr.transplant(B, A, ["left_arm"])
    h = EditHistory()
    h.snapshots.append({"clip": rio.animation_to_dict(B), "parts": ["left_arm"],
                        "source_job": "x"})
    rec = EditHistory.load(h.save(tmp_path / "hist.json"))
    assert len(rec.snapshots) == 1
    assert rec.snapshots[0]["parts"] == ["left_arm"]
    # o snapshot reconstroi o clipe ANTERIOR, nao o resultado
    antes = rio.animation_from_dict(rec.snapshots[0]["clip"])
    for b in ANIMATED_BONES:
        assert np.allclose(antes.rotations[b], B.rotations[b], atol=1e-9), b


# ------------------------------------------------- mapeamento de intervalo
def test_frames_de_fora_do_intervalo_nao_mudam():
    A, B = _anim(seed=40), _anim(seed=41)
    R, rep = tr.transplant(B, A, ["left_arm"],
                           frame_map={"source": [0, 4], "target": [3, 7]})
    rw, bw = tr.world_rotation_series(R), tr.world_rotation_series(B)
    for t in (0, 1, 2, 8, 9, 10, 11):
        e = _ang(rw["LeftArm"][t:t+1], bw["LeftArm"][t:t+1]).max()
        assert e == pytest.approx(0.0, abs=1e-4), (t, e)


def test_intervalo_mapeado_copia_a_origem_reamostrada():
    """frames 3..7 do destino recebem os frames 0..4 da origem, 1:1."""
    A, B = _anim(seed=42), _anim(seed=43)
    R, _ = tr.transplant(B, A, ["left_arm"],
                         frame_map={"source": [0, 4], "target": [3, 7]})
    rw, aw = tr.world_rotation_series(R), tr.world_rotation_series(A)
    for k in range(5):
        e = _ang(rw["LeftArm"][3 + k:4 + k], aw["LeftArm"][k:k + 1]).max()
        assert e == pytest.approx(0.0, abs=TOL_G), (k, e)


def test_duracoes_diferentes_sao_reamostradas():
    """O exemplo do usuario: 1..23 da origem -> 3..35 do destino (23 -> 33)."""
    A = _anim(T=60, seed=44)
    B = _anim(T=60, seed=45)
    R, rep = tr.transplant(B, A, ["left_arm"],
                           frame_map={"source": [1, 23], "target": [3, 35]})
    assert rep["frame_map"] == {"source": [1, 23], "target": [3, 35]}
    rw, aw = tr.world_rotation_series(R), tr.world_rotation_series(A)
    # o inicio e o fim batem exatamente
    assert _ang(rw["LeftArm"][3:4], aw["LeftArm"][1:2]).max() == pytest.approx(0.0, abs=TOL_G)
    assert _ang(rw["LeftArm"][35:36], aw["LeftArm"][23:24]).max() == pytest.approx(0.0, abs=TOL_G)
    # o meio e exatamente o slerp da origem na posicao interpolada
    for t in range(3, 36):
        pos = 1.0 + (t - 3) * (23.0 - 1.0) / (35.0 - 3.0)
        i0 = int(np.floor(pos)); i1 = min(i0 + 1, 23); frac = pos - i0
        esp = tr._slerp_series(aw["LeftArm"][i0:i0 + 1], aw["LeftArm"][i1:i1 + 1],
                               np.array([frac]))
        assert _ang(rw["LeftArm"][t:t + 1], esp).max() == pytest.approx(0.0, abs=TOL_G), t


def test_mapeamento_permite_clipes_de_tamanhos_diferentes():
    A, B = _anim(T=30, seed=46), _anim(T=50, seed=47)
    R, rep = tr.transplant(B, A, ["left_arm"],
                           frame_map={"source": [0, 29], "target": [0, 29]})
    assert rep["frames"] == 50
    for b in ANIMATED_BONES:
        assert np.isfinite(R.rotations[b]).all(), b


def test_intervalo_fora_do_clipe_da_erro():
    A, B = _anim(T=12, seed=48), _anim(T=12, seed=49)
    with pytest.raises(ValueError, match="origem"):
        tr.transplant(B, A, ["left_arm"], frame_map={"source": [0, 99], "target": [0, 3]})
    with pytest.raises(ValueError, match="destino"):
        tr.transplant(B, A, ["left_arm"], frame_map={"source": [0, 3], "target": [5, 99]})


def test_fps_continua_sendo_exigido_com_mapeamento():
    A, B = _anim(fps=30.0, seed=50), _anim(fps=24.0, seed=51)
    with pytest.raises(ValueError, match="fps diferentes"):
        tr.transplant(B, A, ["left_arm"], frame_map={"source": [0, 3], "target": [0, 3]})


def test_trajetoria_da_raiz_tambem_e_reamostrada():
    A, B = _anim(T=20, seed=52), _anim(T=20, seed=53)
    R, _ = tr.transplant(B, A, ["hips"],
                         frame_map={"source": [2, 10], "target": [4, 12]})
    # dentro do intervalo veio de A, fora ficou de B
    assert np.allclose(R.root_translation[4:13], A.root_translation[2:11], atol=1e-9)
    assert np.allclose(R.root_translation[0:4], B.root_translation[0:4], atol=1e-9)
    assert np.allclose(R.root_translation[13:], B.root_translation[13:], atol=1e-9)
