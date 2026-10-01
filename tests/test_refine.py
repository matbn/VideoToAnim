"""Testes da camada de refinamento (filtros, constraints, rebake, historico).

Cobre diretamente os criterios de aceite do pedido:
  * >=4 filtros distintos, selecionaveis, com resultados mensuravelmente diferentes;
  * configuracao por osso e por intervalo (salva em arquivo e reaplicavel);
  * preset humanoide corrige rotacao absurda e registra no relatorio;
  * limites invalidos -> erro claro;
  * edicao afeta SO o intervalo (diff numerico zero fora dele);
  * rebake sem salto na transicao;
  * undo/redo e persistencia da sessao;
  * frames/ossos nao tocados permanecem iguais.
"""
from __future__ import annotations

import numpy as np
import pytest

from core import mixamo as mx
from core.refine import boneedit_mod as be
from core.refine import constraints as cons
from core.refine import filter_plan, filters, io as rio, refine_animation, report as rep_mod
from core.retarget import Animation


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def noisy_anim() -> Animation:
    """Clipe sintetico: movimento suave + ruido de alta frequencia."""
    rng = np.random.default_rng(3)
    T, fps = 80, 30.0
    rotations: dict[str, np.ndarray] = {}
    for bone in mx.ANIMATED_BONES:
        series = np.tile(np.array([0.0, 0.0, 0.0, 1.0]), (T, 1))
        if bone in ("Head", "LeftArm", "RightUpLeg", "Spine2"):
            axis = np.array({"Head": (0, 1, 0), "LeftArm": (0, 0, -1),
                             "RightUpLeg": (-1, 0, 0), "Spine2": (0, 0, 1)}[bone], float)
            axis /= np.linalg.norm(axis)
            for t in range(T):
                a = 0.5 * np.sin(2 * np.pi * t / (T - 1))
                series[t] = np.array([*(axis * np.sin(a / 2)), np.cos(a / 2)])
        rotations[bone] = mx.quat_normalize(series + rng.normal(0, 0.02, series.shape))
    root = np.tile(np.array([0.0, 0.93, 0.0]), (T, 1))
    return Animation(fps, T, list(mx.BONE_NAMES), rotations, root, {})


# ---------------------------------------------------------------------------
# 1. Filtros
# ---------------------------------------------------------------------------
def test_pelo_menos_quatro_filtros_implementados():
    assert len(filters.FILTERS) >= 4
    for nome in ("one_euro", "moving_average", "savgol", "kalman",
                 "butterworth", "double_exponential"):
        assert nome in filters.FILTERS
        assert callable(filters.FILTERS[nome].func)


def test_filtros_produzem_resultados_diferentes(noisy_anim):
    comp = rep_mod.compare_filters(noisy_anim, list(filters.FILTERS.keys()))
    assert len(comp) == len(filters.FILTERS)
    gains = [c.smoothness_gain_pct for c in comp]
    assert all(g > 0 for g in gains), "todo filtro deve reduzir o jerk"
    assert max(gains) - min(gains) > 1.0, "os filtros devem diferir mensuravelmente"
    rmses = [round(c.rmse, 6) for c in comp]
    assert len(set(rmses)) > 3, "cada filtro deve dar um desvio residual proprio"
    # nao sao um corte seco: o desvio tem que ser pequeno
    assert all(c.angular_rmse_deg < 30.0 for c in comp)


def test_filtro_reduz_jerk(noisy_anim):
    q = noisy_anim.rotations["Head"]
    antes = rep_mod.jerk_energy(filters.quat_continuity(q), 30.0)
    depois = rep_mod.jerk_energy(filters.quat_continuity(
        filters.smooth_quaternion_series(q, "savgol", 30.0, window=11)), 30.0)
    assert depois < antes


def test_plano_por_osso_e_por_intervalo(tmp_path, noisy_anim):
    """Filtros diferentes por osso + intervalo, salvo em arquivo e reaplicado."""
    plan = filter_plan.FilterPlan(rules=[
        filter_plan.FilterRule("savgol", {"window": 11}, bones=["LeftHand", "RightHand"]),
        filter_plan.FilterRule("one_euro", {"min_cutoff": 1.0}, bones=["Spine"], start=10, end=40),
    ])
    assert plan.validate() == []
    p = tmp_path / "filtros.yaml"
    plan.save(p)
    assert p.exists()

    recarregado = filter_plan.FilterPlan.load(p)
    assert len(recarregado.rules) == 2
    assert recarregado.rules[0].bones == ["LeftHand", "RightHand"]

    out, info = filter_plan.apply_filter_plan(noisy_anim, recarregado)
    assert info["applied"], "o plano deve registrar o que aplicou"
    # osso com regra restrita foi filtrado...
    assert not np.allclose(out.rotations["Spine"][20], noisy_anim.rotations["Spine"][20])
    # ...e fora do intervalo o osso da regra nao mudou
    assert np.array_equal(out.rotations["Spine"][:10], noisy_anim.rotations["Spine"][:10])
    assert np.array_equal(out.rotations["Spine"][41:], noisy_anim.rotations["Spine"][41:])
    # osso sem regra ficou intacto
    assert np.array_equal(out.rotations["Hips"], noisy_anim.rotations["Hips"])


def test_plano_invalido_da_erro_claro(tmp_path):
    p = tmp_path / "ruim.yaml"
    p.write_text("filters:\n  - name: filtro_que_nao_existe\n", encoding="utf-8")
    with pytest.raises(ValueError) as e:
        filter_plan.FilterPlan.load(p)
    assert "desconhecido" in str(e.value)

    p2 = tmp_path / "ruim2.yaml"
    p2.write_text("filters:\n  - name: savgol\n    start: 50\n    end: 10\n", encoding="utf-8")
    with pytest.raises(ValueError) as e2:
        filter_plan.FilterPlan.load(p2)
    assert "intervalo invalido" in str(e2.value)


# ---------------------------------------------------------------------------
# 2. Constraints
# ---------------------------------------------------------------------------
def test_preset_humanoide_carregado_por_padrao():
    preset = cons.load_default_preset()
    assert preset.validate() == []
    assert cons.default_constraints_path().exists(), "o preset deve vir em arquivo editavel"
    assert len(preset.limits) >= 20
    # regras exigidas pelo pedido
    assert preset.limits_for("Head"), "cabeca precisa de limite"
    assert preset.limits_for("LeftForeArm"), "cotovelo precisa de limite"
    assert preset.limits_for("RightLeg"), "joelho precisa de limite"


def test_constraints_corrigem_rotacao_absurda(noisy_anim):
    """Cabeca a 180 graus deve ser limitada e registrada no relatorio."""
    anim = noisy_anim
    rots = {k: v.copy() for k, v in anim.rotations.items()}
    for t in range(20, 50):
        rots["Head"][t] = np.array([0.0, 1.0, 0.0, 0.0])       # 180 graus em Y
    inj = Animation(anim.fps, anim.num_frames, list(anim.bone_names), rots,
                    anim.root_translation.copy(), {})

    preset = cons.load_default_preset()
    out, rep = cons.apply_constraints(inj, preset)
    assert rep["bones_corrected"] >= 1
    assert "Head" in rep["per_bone"]
    head = rep["per_bone"]["Head"]
    assert head["frames_corrected"] >= 25
    assert head["max_violation_deg"] > 100.0                    # ~130 desde 50 de limite
    # o limite realmente foi respeitado
    limite = max(l.max_deg for l in preset.limits_for("Head") if l.kind == "cone")
    for t in range(20, 50):
        assert cons.quat_angle_deg(out.rotations["Head"][t]) <= limite + 1e-6
    # frames fora da violacao intocados
    assert np.array_equal(out.rotations["Head"][:20], anim.rotations["Head"][:20])


def test_limite_invalido_da_erro_claro(tmp_path):
    p = tmp_path / "ruim.yaml"
    p.write_text("limits:\n  - bone: Head\n    kind: cone\n    min_deg: 90\n    max_deg: 10\n",
                 encoding="utf-8")
    with pytest.raises(ValueError) as e:
        cons.ConstraintPreset.load(p)
    assert "min_deg" in str(e.value)

    p2 = tmp_path / "ruim2.yaml"
    p2.write_text("limits:\n  - bone: OssoQueNaoExiste\n    kind: cone\n    max_deg: 30\n",
                  encoding="utf-8")
    with pytest.raises(ValueError) as e2:
        cons.ConstraintPreset.load(p2)
    assert "inexistente" in str(e2.value)


def test_limites_ajustaveis_sem_tocar_codigo(tmp_path, noisy_anim):
    """Mudar o YAML muda o resultado da proxima execucao."""
    inj_rots = {k: v.copy() for k, v in noisy_anim.rotations.items()}
    for t in range(10, 40):
        inj_rots["Head"][t] = np.array([0.0, 1.0, 0.0, 0.0])
    inj = Animation(noisy_anim.fps, noisy_anim.num_frames, list(noisy_anim.bone_names),
                    inj_rots, noisy_anim.root_translation.copy(), {})

    restrito = cons.ConstraintPreset("custom", [cons.JointLimit("Head", "cone", -180, 30, 1.0)])
    amplo = cons.ConstraintPreset("custom", [cons.JointLimit("Head", "cone", -180, 90, 1.0)])
    out_r, _ = cons.apply_constraints(inj, restrito)
    out_a, _ = cons.apply_constraints(inj, amplo)
    assert cons.quat_angle_deg(out_r.rotations["Head"][20]) <= 30.001
    assert cons.quat_angle_deg(out_a.rotations["Head"][20]) > 30.0


# ---------------------------------------------------------------------------
# 3. Editor de bone / rebake
# ---------------------------------------------------------------------------
def test_edicao_afeta_so_o_intervalo(noisy_anim):
    orig = {k: v.copy() for k, v in noisy_anim.rotations.items()}
    edit = be.BoneEdit(bone="Head", frame=40, rotation_euler_deg=[0, 30, 0], start=25, end=55)
    out, rep = be.apply_edit(noisy_anim, edit)

    assert rep["affected"] == [25, 55]
    diff = np.abs(out.rotations["Head"] - orig["Head"]).max(axis=1)
    assert float(np.abs(diff[25:56]).max()) > 1e-6, "dentro do intervalo tem que mudar"
    fora = np.concatenate([diff[:25], diff[56:]])
    assert float(np.abs(fora).max()) == 0.0, "fora do intervalo tem que ser bit-exato"
    for bone in orig:
        if bone != "Head":
            assert np.array_equal(out.rotations[bone], orig[bone]), f"{bone} nao deveria mudar"


def test_rebake_sem_salto_na_transicao(noisy_anim):
    edit = be.BoneEdit(bone="Head", frame=40, rotation_euler_deg=[0, 45, 0], start=25, end=55)
    out, _ = be.apply_edit(noisy_anim, edit)

    def gap(series, t):
        return cons.quat_angle_deg(np.array([0, 0, 0, 1])) * 0 + float(
            np.degrees(np.arccos(np.clip(abs(float(np.dot(series[t], series[t - 1]))), -1, 1))) * 2)

    transicao = max(gap(out.rotations["Head"], t) for t in (25, 26, 40, 41, 55))
    pico_geral = max(gap(out.rotations["Head"], t) for t in range(1, out.num_frames))
    assert transicao < 5.0, f"transicao com salto: {transicao:.2f} graus"
    assert transicao <= pico_geral + 1e-6


def test_edicao_invalida_da_erro_claro(noisy_anim):
    with pytest.raises(ValueError) as e:
        be.apply_edit(noisy_anim, be.BoneEdit(bone="Head", frame=10, start=20, end=30,
                                              rotation_euler_deg=[0, 10, 0]))
    assert "dentro de" in str(e.value)

    with pytest.raises(ValueError) as e2:
        # translation so existe no root: em outro osso deve recusar
        be.apply_edit(noisy_anim, be.BoneEdit(bone="LeftArm", frame=0, translation=[0, 0, 0]))
    assert "Hips" in str(e2.value)

    with pytest.raises(ValueError) as e4:
        # edicao sem nenhum alvo
        be.apply_edit(noisy_anim, be.BoneEdit(bone="Head", frame=0))
    assert "sem alvo" in str(e4.value)

    with pytest.raises(ValueError) as e3:
        be.apply_edit(noisy_anim, be.BoneEdit(bone="HeadTop_End", frame=0,
                                              rotation_euler_deg=[0, 0, 0]))
    assert "animavel" in str(e3.value) or "nao tem rotacao" in str(e3.value)


# ----------------------------------------------------------------------
# 3b. Referencial dos euler: local (pai) x global (cena)
# ----------------------------------------------------------------------
def _anim_com_pai_girado(T: int = 5):
    """Serie em que a cadeia de ancestrais esta girada: local != global."""
    rots = {b: np.tile(np.array([0.0, 0.0, 0.0, 1.0]), (T, 1))
            for b in mx.ANIMATED_BONES}
    for t in range(T):
        rots["Spine2"][t] = cons.euler_xyz_deg_to_quat(np.array([0.0, 0.0, 30.0]))
        rots["LeftArm"][t] = cons.euler_xyz_deg_to_quat(np.array([0.0, 0.0, -62.0]))
    root = np.tile(np.array([0.0, 0.98, 0.0]), (T, 1))
    return Animation(30.0, T, list(mx.BONE_NAMES), rots, root, {})


def test_local_e_global_sao_referenciais_diferentes():
    """O painel tem de mostrar numeros diferentes nos dois espacos.

    Sem pai girado os dois coincidem (no repouso local == mundo), e um teste
    de "sao iguais" passaria sem provar nada: aqui a cadeia esta a -32 graus.
    """
    anim = _anim_com_pai_girado()
    f = 2
    frame_rots = {k: v[f] for k, v in anim.rotations.items()}
    local = cons.quat_to_euler_xyz_deg(frame_rots["LeftForeArm"])
    global_ = cons.quat_to_euler_xyz_deg(mx.world_rotation(frame_rots, "LeftForeArm"))
    assert abs(global_[2] - local[2]) > 20.0, (
        f"o teste precisa de um pai girado: local={local} global={global_}")


@pytest.mark.parametrize("bone", ["LeftForeArm", "LeftHand", "Hips"])
@pytest.mark.parametrize("space", ["local", "global"])
def test_euler_faz_round_trip_nos_dois_espacos(bone, space):
    """Ler o angulo e grava-lo de volta tem que devolver o MESMO quaternion.

    E o criterio de aceite do toggle: se os dois espacos fossem inversos um do
    outro, toda edicao local viraria global (e vice-versa) silenciosamente.
    """
    anim = _anim_com_pai_girado()
    f = 2
    bone_local = {k: v[f] for k, v in anim.rotations.items()}
    ref = bone_local[bone] if space == "local" else mx.world_rotation(bone_local, bone)
    euler = [float(x) for x in cons.quat_to_euler_xyz_deg(ref)]

    out, rep = be.apply_edit(anim, be.BoneEdit(bone=bone, frame=f,
                                              rotation_euler_deg=euler, space=space))
    # a comparacao tem de ser feita NO MESMO espaco do round-trip: a
    # Animation guarda o quaternion local, entao em `global` reconverte-se.
    depois_local = {k: v[f] for k, v in out.rotations.items()}
    got = depois_local[bone] if space == "local" else mx.world_rotation(depois_local, bone)
    err = cons.quat_angle_deg(mx.quat_mul(got, mx.quat_conj(ref)))
    assert err < 1e-6, f"round-trip {space} perdeu {err:.6f} graus"
    assert rep["space"] == space


def test_edicao_global_atinge_a_orientacao_absoluta():
    """Pedir 45 graus no mundo tem que dar 45 graus no mundo."""
    anim = _anim_com_pai_girado()
    f = 2
    out, _ = be.apply_edit(anim, be.BoneEdit(bone="LeftForeArm", frame=f,
                                            rotation_euler_deg=[0.0, 0.0, 45.0],
                                            space="global"))
    frame_rots = {k: v[f] for k, v in out.rotations.items()}
    world = cons.quat_to_euler_xyz_deg(mx.world_rotation(frame_rots, "LeftForeArm"))
    assert abs(world[2] - 45.0) < 1e-6, f"globais pedido 45, veio {world}"

    # ...e o MESMO numero em local tem que dar outra coisa (o pai gira junto)
    out2, _ = be.apply_edit(anim, be.BoneEdit(bone="LeftForeArm", frame=f,
                                             rotation_euler_deg=[0.0, 0.0, 45.0],
                                             space="local"))
    fr2 = {k: v[f] for k, v in out2.rotations.items()}
    world2 = cons.quat_to_euler_xyz_deg(mx.world_rotation(fr2, "LeftForeArm"))
    assert abs(world2[2] - 45.0) > 20.0, "local e global nao podem dar o mesmo resultado"


def test_space_invalido_da_erro_claro(noisy_anim):
    edit = be.BoneEdit(bone="Head", frame=0, rotation_euler_deg=[0, 0, 0], space="banana")
    assert any("space" in e for e in edit.validate())
    with pytest.raises(ValueError) as e:
        be.apply_edit(noisy_anim, edit)
    assert "space invalido" in str(e.value)


def test_edicao_antiga_sem_space_continua_local():
    """Retrocompatibilidade: historico salvo antes do campo nao pode quebrar."""
    d = {"bone": "Head", "frame": 1, "rotation_euler_deg": [1, 2, 3]}
    assert be.BoneEdit.from_dict(d).space == "local"
    # e o round-trip serializa->dict preserva o espaco escolhido
    assert be.BoneEdit(bone="Head", frame=1, rotation_euler_deg=[0, 0, 0],
                       space="global").to_dict()["space"] == "global"


def test_world_rotation_bate_com_a_fk():
    """`world_rotation` tem que concordar com a FK (mesma empilhamento)."""
    anim = _anim_com_pai_girado()
    f = 2
    frame_rots = {k: v[f] for k, v in anim.rotations.items()}
    pos = mx.fk_world(frame_rots, np.asarray(anim.root_translation)[f])
    for bone in ("LeftForeArm", "LeftHand", "Spine2"):
        # a FK tambem empilha os ancestrais; comparamos via o offset do filho
        child = mx._first_child(bone)
        if child is None:
            continue
        v_fk = pos[child] - pos[bone]
        v_q = mx.quat_rotate(mx.world_rotation(frame_rots, bone), mx.BONE_OFFSET[child])
        cos = float(np.dot(v_fk, v_q) / (np.linalg.norm(v_fk) * np.linalg.norm(v_q)))
        assert cos > 0.99999, f"{bone}: direcao da FK difere ({cos:.6f})"


def test_historico_undo_redo_e_persistencia(tmp_path, noisy_anim):
    orig = {k: v.copy() for k, v in noisy_anim.rotations.items()}
    h = be.EditHistory(source="clipe.json")
    h.add(be.BoneEdit("Head", 30, rotation_euler_deg=[0, 20, 0], start=20, end=40, author="ana"))
    h.add(be.BoneEdit("Head", 60, rotation_euler_deg=[0, -25, 0], start=55, end=70, author="ana", note="segunda passada"))
    assert h.log()[0]["author"] == "ana" and h.log()[0]["frame"] == 30

    aplicado, _ = h.replay(noisy_anim)
    assert not np.array_equal(aplicado.rotations["Head"], orig["Head"])

    h.undo()
    assert len(h.edits) == 1 and len(h.undone) == 1
    h.redo()
    assert len(h.edits) == 2

    p = h.save(tmp_path / "sessao.json")
    assert p.exists()
    h2 = be.EditHistory.load(p)
    assert len(h2.edits) == 2 and h2.edits[1].note == "segunda passada"
    reconstruido, _ = h2.replay(noisy_anim)
    assert np.allclose(reconstruido.rotations["Head"], aplicado.rotations["Head"], atol=1e-9)
    # undo depois de recarregar continua funcionando
    h2.undo()
    assert len(h2.edits) == 1


def test_refino_completo_nao_toca_no_que_nao_deve(tmp_path, noisy_anim):
    """Filtro so no Head num intervalo + constraints: o resto fica intacto."""
    orig = {k: v.copy() for k, v in noisy_anim.rotations.items()}
    plan = filter_plan.FilterPlan(rules=[
        filter_plan.FilterRule("savgol", {"window": 9}, bones=["Head"], start=30, end=50),
    ])
    preset = cons.ConstraintPreset("vazio", [])          # sem limites -> sem alteracoes
    out, _ = refine_animation(noisy_anim, constraints_config=preset, filters_config=plan)

    for bone in orig:
        if bone == "Head":
            continue
        assert np.array_equal(out.rotations[bone], orig[bone]), f"{bone} nao deveria mudar"
    assert np.array_equal(out.rotations["Head"][:30], orig["Head"][:30])
    assert np.array_equal(out.rotations["Head"][51:], orig["Head"][51:])


# ---------------------------------------------------------------------------
# 4. IO / amostras
# ---------------------------------------------------------------------------
def test_roundtrip_json_e_glb(tmp_path, noisy_anim):
    p = rio.save_animation(noisy_anim, tmp_path / "a.json")
    back = rio.load_animation(p)
    assert back.num_frames == noisy_anim.num_frames
    for k in noisy_anim.rotations:
        assert np.allclose(back.rotations[k], noisy_anim.rotations[k], atol=1e-6)
    glb = rio.export_animation_glb(noisy_anim, tmp_path / "a.glb")
    assert glb["bytes"] > 1000 and glb["bones"] == 65


def test_relatorio_markdown(tmp_path, noisy_anim):
    comp = rep_mod.compare_filters(noisy_anim, ["one_euro", "savgol"])
    md = rep_mod.to_markdown(comp)
    assert "| filtro |" in md and "atraso_frames" in md
    payload = rep_mod.save_report(tmp_path, comparison=comp)
    assert (tmp_path / "refine_report.json").exists()
    assert (tmp_path / "filtros_comparativo.md").exists()
    assert len(payload["filter_comparison"]) == 2
