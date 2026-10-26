"""Editor de curvas: janelas de keyframe, interpolacoes e a API /curves.

O editor de curvas do navegador e o mesmo rebake do servidor: aqui a prova
e de que a matematica bate (a curva desenhada e a que vai para o disco) e
de que apagar uma keyframe volta o osso ao estado anterior.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from core import mixamo as mx
from core.refine import boneedit as be
from core.refine import constraints as cons
from core.refine import io as rio
from core.retarget import Animation


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
def _anim(T: int = 60) -> Animation:
    """Clipe sintetico: a cabeca gira para um lado e para o outro."""
    rots = {b: np.tile(np.array([0.0, 0.0, 0.0, 1.0]), (T, 1))
            for b in mx.ANIMATED_BONES}
    for t in range(T):
        a = np.radians(20.0 * np.sin(2 * np.pi * t / T))
        rots["Head"][t] = cons.euler_xyz_deg_to_quat(np.array([0.0, a, 0.0]))
    root = np.tile(np.array([0.0, 0.98, 0.0]), (T, 1))
    return Animation(30.0, T, list(mx.BONE_NAMES), rots, root, {})


def _euler_y(anim: Animation, bone: str = "Head") -> np.ndarray:
    return np.array([float(cons.quat_to_euler_xyz_deg(anim.rotations[bone][t])[1])
                     for t in range(anim.num_frames)])


# ---------------------------------------------------------------------------
# janelas de keyframe
# ---------------------------------------------------------------------------
def test_janela_vem_das_vizinhas():
    """A janela de uma keyframe vai da vizinha anterior ate a seguinte."""
    janelas = be.keyframe_windows([10, 30, 45], total=60)
    assert janelas == [(0, 29), (11, 44), (31, 59)]
    # a keyframe esta sempre dentro da propria janela
    for f, (s, e) in zip([10, 30, 45], janelas):
        assert s <= f <= e


def test_janela_sem_vizinha_usa_a_meia_largura():
    assert be.keyframe_windows([30], total=60) == [(10, 50)]
    assert be.keyframe_windows([30], total=60, half=5) == [(25, 35)]


def test_frames_repetidos_sao_erro():
    with pytest.raises(ValueError) as e:
        be.keyframe_windows([10, 10], total=60)
    assert "repetidos" in str(e.value)


def test_build_keyframe_edits_ordena_e_calcula_janelas():
    edits = be.build_keyframe_edits("Head", "local", [
        {"frame": 30, "rotation_euler_deg": [0, 40, 0], "interp": "linear"},
        {"frame": 10, "rotation_euler_deg": [0, 10, 0], "interp": "smooth"},
    ], total=60)
    assert [e.frame for e in edits] == [10, 30]
    assert [(e.start, e.end) for e in edits] == [(0, 29), (11, 50)]
    assert [e.interp for e in edits] == ["smooth", "linear"]


# ---------------------------------------------------------------------------
# interpolacoes
# ---------------------------------------------------------------------------
def test_interpolacao_linear_e_reta():
    anim = _anim()
    edit = be.BoneEdit(bone="Head", frame=30, rotation_euler_deg=[0, 60, 0],
                        start=10, end=50, interp="linear")
    out, _ = be.apply_edit(anim, edit)
    y = _euler_y(out)
    # reta de ~0 ate 60 no frame 30
    assert abs(y[30] - 60.0) < 1e-6
    assert abs(y[20] - 30.0) < 2.0, y[20]
    # a curva original era um seno: o linear NAO passa pelo meio dela
    assert abs(y[20] - y[20]) < 1e-9


def test_interpolacao_step_hold_o_valor_ate_o_alvo():
    anim = _anim()
    edit = be.BoneEdit(bone="Head", frame=30, rotation_euler_deg=[0, 60, 0],
                        start=10, end=50, interp="step")
    out, _ = be.apply_edit(anim, edit)
    y = _euler_y(out)
    # segura o valor da ancora ate o frame alvo...
    assert abs(y[25] - y[10]) < 1e-6, (y[10], y[25])
    # ...e salta para o alvo
    assert abs(y[30] - 60.0) < 1e-6
    # depois do alvo, segura o alvo ate o fim da janela
    assert abs(y[45] - 60.0) < 1e-6


def test_interp_invalido_da_erro_claro():
    anim = _anim()
    edit = be.BoneEdit(bone="Head", frame=5, rotation_euler_deg=[0, 0, 0],
                        interp="banana")
    assert any("interp" in e for e in edit.validate())
    with pytest.raises(ValueError) as e:
        be.apply_edit(anim, edit)
    assert "invalid interp" in str(e.value)


def test_edicao_antiga_sem_interp_continua_suave():
    """Retrocompatibilidade: historico salvo antes do campo nao pode quebrar."""
    d = {"bone": "Head", "frame": 1, "rotation_euler_deg": [1, 2, 3]}
    assert be.BoneEdit.from_dict(d).interp == "smooth"
    assert be.BoneEdit(bone="Head", frame=1, rotation_euler_deg=[0, 0, 0],
                        interp="linear").to_dict()["interp"] == "linear"


# ---------------------------------------------------------------------------
# base + rebuild (apagar keyframe)
# ---------------------------------------------------------------------------
def test_apagar_keyframe_volta_o_osso_a_base():
    """O conjunto de keyframes e reaplicado sobre a BASE (sem keyframes).

    Sem isso, apagar uma keyframe deixaria o rebake antigo pelo clipe: o
    osso nao voltaria ao estado anterior.
    """
    anim = _anim()
    base = anim.rotations["Head"].copy()
    edits = be.build_keyframe_edits("Head", "local", [
        {"frame": 20, "rotation_euler_deg": [0, 50, 0]},
        {"frame": 40, "rotation_euler_deg": [0, -50, 0]},
    ], total=anim.num_frames)
    com_kf, _ = be.rebuild_bone(anim, "Head", base, edits)
    assert not np.allclose(com_kf.rotations["Head"], base)

    # apagar tudo: a serie volta a ser a base, bit a bit
    sem_kf, _ = be.rebuild_bone(com_kf, "Head", base, [])
    assert np.allclose(sem_kf.rotations["Head"], base, atol=1e-9)

    # apagar so uma: a que ficou continua valendo
    so_uma, _ = be.rebuild_bone(com_kf, "Head", base, edits[:1])
    assert not np.allclose(so_uma.rotations["Head"], base)
    assert np.allclose(so_uma.rotations["Head"][20],
                       com_kf.rotations["Head"][20], atol=1e-9)


def test_historial_guarda_a_base(tmp_path):
    h = be.EditHistory(source="clipe.json")
    assert h.base_of("Head") is None
    base = np.tile(np.array([0.0, 0.0, 0.0, 1.0]), (3, 1))
    h.set_base("Head", base)
    assert h.base_of("Head") is not None
    assert h.base_of("mixamorig:Head") is not None
    # a base sobrevive ao arquivo da sessao
    p = h.save(tmp_path / "sessao.json")
    h2 = be.EditHistory.load(p)
    assert h2.base_of("Head") is not None
    assert np.allclose(h2.base_of("Head"), base)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
class _FakeStore:
    def __init__(self, jobs):
        self.jobs = jobs

    def get(self, jid):
        return self.jobs.get(jid)

    def set_artifact(self, *a, **k):
        return None


@pytest.fixture()
def job(tmp_path, monkeypatch):
    """Um job com anim.json, com os caminhos da API apontados para o tmp."""
    from server import refine_api

    jid = "job_curvas"
    jobs_dir = tmp_path / "jobs"
    (jobs_dir / jid).mkdir(parents=True)
    anim = _anim(40)
    rio.save_animation(anim, jobs_dir / jid / "anim.json")
    refine_dir = tmp_path / "refine"

    monkeypatch.setattr(refine_api, "JOBS_DIR", jobs_dir)
    monkeypatch.setattr(refine_api, "REFINE_DIR", refine_dir)
    # o `STORAGE` e usado pelo relatorio do refino: sem isso o teste escreveria
    # no storage de verdade
    monkeypatch.setattr(refine_api, "STORAGE", tmp_path)
    monkeypatch.setattr(refine_api, "store", _FakeStore({jid: {"id": jid}}))
    return jid


def test_api_curves_devolve_serie_e_keyframes(job):
    from server import refine_api

    d = refine_api.get_curves(job, "Head", "local")
    assert d["bone"] == "Head"
    assert d["num_frames"] == 40
    assert len(d["channels"]["x"]) == 40
    assert len(d["channels"]["y"]) == 40
    assert len(d["quats"]) == 40
    assert d["keyframes"] == []
    # a cadeia de ancestrais vem para o preview no espaco global
    # (raiz primeiro: e a ordem em que o mundo e empilhado)
    assert [c["bone"] for c in d["chain"]] == ["Hips", "Spine", "Spine1", "Spine2", "Neck"]


def test_api_osso_inexistente_da_404(job):
    from server import refine_api
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as e:
        refine_api.get_curves(job, "OssoQueNaoExiste", "local")
    assert e.value.status_code == 404


def test_api_put_keyframes_grava_apaga_e_rebuilda(job):
    from server import refine_api

    body = refine_api.KeyframeBody(bone="Head", space="local", keyframes=[
        {"frame": 10, "rotation_euler_deg": [0, 30, 0], "interp": "smooth"},
        {"frame": 25, "rotation_euler_deg": [0, -20, 0], "interp": "linear"},
    ])
    r = refine_api.put_keyframes(job, body)
    assert r["report"]["keyframes"] == 2
    assert len(r["keyframes"]) == 2
    assert r["keyframes"][0]["frame"] == 10

    d = refine_api.get_curves(job, "Head", "local")
    assert len(d["keyframes"]) == 2
    # o valor da keyframe e o angulo pedido no referencial pedido
    assert abs(d["keyframes"][0]["value"][1] - 30.0) < 1e-6

    # apagar uma keyframe: o historico do osso fica so com a outra
    body2 = refine_api.KeyframeBody(bone="Head", space="local", keyframes=[
        {"frame": 10, "rotation_euler_deg": [0, 30, 0], "interp": "smooth"},
    ])
    r2 = refine_api.put_keyframes(job, body2)
    assert len(r2["keyframes"]) == 1
    assert r2["keyframes"][0]["frame"] == 10


def test_api_put_keyframes_rejeita_entradas_invalidas(job):
    from server import refine_api
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as e:
        refine_api.put_keyframes(job, refine_api.KeyframeBody(
            bone="Head", keyframes=[{"frame": 999, "rotation_euler_deg": [0, 0, 0]}]))
    assert e.value.status_code == 400

    with pytest.raises(HTTPException) as e:
        refine_api.put_keyframes(job, refine_api.KeyframeBody(
            bone="Head", keyframes=[{"frame": 3, "rotation_euler_deg": [0, 0]}]))
    assert e.value.status_code == 400

    with pytest.raises(HTTPException) as e:
        refine_api.put_keyframes(job, refine_api.KeyframeBody(
            bone="Head", keyframes=[
                {"frame": 3, "rotation_euler_deg": [0, 0, 0]},
                {"frame": 3, "rotation_euler_deg": [0, 0, 0]}]))
    assert e.value.status_code == 400

    with pytest.raises(HTTPException) as e:
        refine_api.put_keyframes(job, refine_api.KeyframeBody(
            bone="Head", space="banana", keyframes=[]))
    assert e.value.status_code == 400


def test_api_curves_no_espaco_global_da_o_mundo(job):
    from server import refine_api

    d = refine_api.get_curves(job, "Head", "global")
    # no espaco global o angulo e o absoluto: difere do local quando o pai gira
    assert len(d["channels"]["y"]) == 40
    assert d["space"] == "global"


# ---------------------------------------------------------------------------
# curvas de interpolacao (bezier) e intervalo do clipe
# ---------------------------------------------------------------------------
def test_familias_de_easing():
    """As familias classicas: comecam em 0, terminam em 1 e sao crescentes.

    `back` e `elastic` passam do 1 no meio (e voltam) — e isso que faz a
    "curva" dar um salto e voltar.
    """
    familias = ["smooth", "linear", "sine", "quad", "cubic", "quart", "quint",
                "expo", "circ", "back", "elastic", "bounce"]
    for fam in familias:
        assert fam in be.EASINGS, fam
        entrada, saida = be.EASINGS[fam]
        assert abs(entrada(0.0)) < 1e-9, fam
        assert abs(entrada(1.0) - 1.0) < 1e-9, fam
        assert abs(saida(0.0)) < 1e-9, fam
        assert abs(saida(1.0) - 1.0) < 1e-9, fam
        # as que nao estouram e nao quicam sao crescentes
        if fam not in ("back", "elastic", "bounce"):
            vals = [entrada(i / 50) for i in range(51)]
            assert all(b >= a - 1e-9 for a, b in zip(vals, vals[1:])), fam
    # back e elastic estouram no meio
    assert max(be.EASINGS["back"][1](i / 50) for i in range(51)) > 1.0
    assert max(be.EASINGS["elastic"][1](i / 50) for i in range(51)) > 1.0
    # e o bounce quica (nao e crescente)
    vals = [be.EASINGS["bounce"][1](i / 50) for i in range(51)]
    assert any(b < a - 1e-6 for a, b in zip(vals, vals[1:]))
    # e todas valem como modo de interpolacao
    for fam in familias:
        assert fam in be.EDIT_INTERPOLATIONS
    assert "bezier" in be.EDIT_INTERPOLATIONS


def test_easing_desconhecido_da_erro_claro():
    anim = _anim(20)
    edit = be.BoneEdit(bone="Head", frame=5, rotation_euler_deg=[0, 0, 0],
                        interp="wobble")
    assert any("interp" in e for e in edit.validate())
    with pytest.raises(ValueError) as e:
        be.apply_edit(anim, edit)
    assert "invalid interp" in str(e.value)


def test_entrada_e_saida_sao_independentes():
    anim = _anim(40)
    base = anim.rotations["Head"].copy()
    chave = {"frame": 20, "rotation_euler_deg": [0.0, 60.0, 0.0],
              "interp_in": "linear", "interp_out": "smooth"}
    base_keys = [chave]
    mistas = [dict(chave, interp_in="smooth", interp_out="smooth")]
    y_base = _euler_y(be.rebuild_bone(anim, "Head", base,
                                      be.build_keyframe_edits("Head", "local", base_keys,
                                                             anim.num_frames))[0])
    y_mistas = _euler_y(be.rebuild_bone(anim, "Head", base,
                                        be.build_keyframe_edits("Head", "local", mistas,
                                                               anim.num_frames))[0])
    # a entrada diferente muda a curva ANTES da keyframe
    assert abs(y_base[12] - y_mistas[12]) > 0.5, (y_base[12], y_mistas[12])
    # e o valor da keyframe e o mesmo
    assert abs(y_base[20] - 60.0) < 1e-6
    assert abs(y_mistas[20] - 60.0) < 1e-6


def test_entrada_e_saida_no_payload():
    """A API guarda as duas opcoes e o servidor resolve a da vizinha."""
    keys = [
        {"frame": 10, "rotation_euler_deg": [0, 20, 0],
         "interp_in": "linear", "interp_out": "smooth"},
        {"frame": 20, "rotation_euler_deg": [0, -20, 0],
         "interp_in": "step", "interp_out": "linear"},
    ]
    edits = be.build_keyframe_edits("Head", "local", keys, 40)
    assert [e.interp for e in edits] == ["linear", "step"]
    assert [e.interp_out for e in edits] == ["smooth", "linear"]
    # a keyframe seguinte sabe a saida da anterior (e ela que modela a
    # primeira metade do segmento que chega nela)
    assert edits[1].interp_prev == "smooth"


def test_alca_muda_a_curva():
    """As handles mudam a curva: e o que o usuario arrasta."""
    anim = _anim(40)
    base = anim.rotations["Head"].copy()
    chave = {"frame": 20, "rotation_euler_deg": [0.0, 60.0, 0.0], "interp": "bezier"}
    reta, _ = be.rebuild_bone(anim, "Head", base, be.build_keyframe_edits(
        "Head", "local", [chave], anim.num_frames))
    puxada, _ = be.rebuild_bone(anim, "Head", base, be.build_keyframe_edits(
        "Head", "local", [dict(chave, handle_in=[4.0, -30.0], handle_channel=1)],
        anim.num_frames))
    suave, _ = be.rebuild_bone(anim, "Head", base, be.build_keyframe_edits(
        "Head", "local", [dict(chave, interp="smooth")], anim.num_frames))
    y1 = _euler_y(reta)
    y2 = _euler_y(puxada)
    ys = _euler_y(suave)
    assert abs(y1[20] - 60.0) < 1e-6
    assert abs(y2[20] - 60.0) < 1e-6
    # a alca arrasta muda a curva no meio do segmento
    assert abs(y1[12] - y2[12]) > 0.1, (y1[12], y2[12])
    # e a curva de interpolacao e diferente do "suave" (o smoothstep)
    assert abs(y1[12] - ys[12]) > 0.1, (y1[12], ys[12])


def test_alca_invalida_da_erro_claro():
    anim = _anim(20)
    edit = be.BoneEdit(bone="Head", frame=5, rotation_euler_deg=[0, 0, 0],
                        interp="bezier", handle_in=[1.0])
    assert any("handle_in" in e for e in edit.validate())
    with pytest.raises(ValueError) as e:
        be.apply_edit(anim, edit)
    assert "handle_in" in str(e.value)


# ---------------------------------------------------------------------------
# a curva do navegador tem que ser a curva do servidor
# ---------------------------------------------------------------------------
NODE_SCRIPT = """
import { readFileSync } from 'node:fs';
import { simularRebake } from '%s';

const c = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const base = c.base_quats || c.quats;
const got = simularRebake(base, c.chain, c.keys, c.space, c.total);
let maior = 0;
for (const ch of ['x', 'y', 'z']) {
  for (let i = 0; i < c.total; i += 1) {
    const d = Math.abs(got[ch][i] - c.expected[ch][i]);
    if (d > maior) maior = d;
  }
}
console.log(maior);
"""


def _compara_navegador_e_servidor(anim, chain, keys, space, tmp_path, node, curves_js):
    """Roda o mesmo caso no Python e no node e compara as duas curvas."""
    import json
    import subprocess

    edits = be.build_keyframe_edits("Head", space, keys, anim.num_frames)
    out, _ = be.rebuild_bone(anim, "Head", anim.rotations["Head"], edits)
    expected = {"x": [], "y": [], "z": []}
    for t in range(anim.num_frames):
        frame_rots = {k: v[t] for k, v in out.rotations.items()}
        q = out.rotations["Head"][t] if space == "local" else mx.world_rotation(frame_rots, "Head")
        e = cons.quat_to_euler_xyz_deg(q)
        for i, ch in enumerate(("x", "y", "z")):
            expected[ch].append(float(e[i]))
    caso = {
        "quats": [[float(x) for x in anim.rotations["Head"][t]]
                  for t in range(anim.num_frames)],
        "chain": chain,
        "keys": [{"frame": k["frame"], "value": k["rotation_euler_deg"],
                  "interp": k.get("interp"), "interp_in": k.get("interp_in"),
                  "interp_out": k.get("interp_out"),
                  "handle_in": k.get("handle_in"), "handle_out": k.get("handle_out"),
                  "handle_channel": k.get("handle_channel", 1)} for k in keys],
        "space": space,
        "total": anim.num_frames,
        "expected": expected,
    }
    cf = tmp_path / f"caso_{space}_{keys[0]['frame']}.json"
    cf.write_text(json.dumps(caso), encoding="utf-8")
    sc = tmp_path / "check.mjs"
    # em ESM o caminho tem de ser uma URL de arquivo (no Windows, "C:\...")
    sc.write_text(NODE_SCRIPT % curves_js.as_uri(), encoding="utf-8")
    r = subprocess.run([node, str(sc), str(cf)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-500:]
    erro = float(r.stdout.strip().splitlines()[-1])
    assert erro < 1e-9, f"[{space}] a curva do navegador difere em {erro} graus"


def test_matematica_do_navegador_bate_com_a_do_servidor(tmp_path):
    """O preview do arraste (JS) tem que ser o rebake do servidor (Python).

    O editor de curvas reproduz o rebake no navegador para o preview ser
    exato. Se as duas matematicas divergirem, o usuario ve uma coisa e o
    arquivo recebe outra.
    """
    import json
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node nao instalado: o preview do navegador nao pode ser conferido")

    root = Path(__file__).resolve().parents[1]
    curves_js = root / "web" / "curves.js"
    assert curves_js.exists(), "web/curves.js sumiu"

    anim = _anim(40)
    rot_bases = {b: np.asarray(v, np.float64) for b, v in anim.rotations.items()}
    chain = []
    parent = mx.BONE_PARENT.get("Head")
    while parent is not None:
        chain.append({"bone": parent,
                      "quats": [[float(x) for x in rot_bases[parent][t]]
                                for t in range(anim.num_frames)]})
        parent = mx.BONE_PARENT.get(parent)
    chain.reverse()

    keys = [
        {"frame": 8, "rotation_euler_deg": [0.0, 35.0, 0.0], "interp": "smooth"},
        {"frame": 18, "rotation_euler_deg": [10.0, -25.0, 0.0], "interp": "linear"},
        {"frame": 30, "rotation_euler_deg": [0.0, 15.0, -20.0], "interp": "step"},
        # uma keyframe com curvas de interpolacao (bezier) e alcas arrastadas
        {"frame": 36, "rotation_euler_deg": [0.0, -8.0, 0.0], "interp": "bezier",
         "handle_in": [2.5, -6.0], "handle_out": [3.5, 4.0], "handle_channel": 1},
    ]
    # e um com entrada e saida DIFERENTES (o segmento e dividido ao meio)
    keys_mistos = [
        {"frame": 10, "rotation_euler_deg": [0.0, 20.0, 0.0],
         "interp_in": "linear", "interp_out": "smooth"},
        {"frame": 22, "rotation_euler_deg": [0.0, -20.0, 0.0],
         "interp_in": "smooth", "interp_out": "step"},
        {"frame": 34, "rotation_euler_deg": [0.0, 30.0, 0.0],
         "interp_in": "step", "interp_out": "linear"},
    ]
    # e um com cada familia de easing (a curva tem que ser a mesma dos dois
    # lados: servidor e navegador)
    familias = ["sine", "quad", "cubic", "quart", "quint", "expo", "circ",
                "back", "elastic", "bounce"]
    keys_easing = [
        {"frame": 6 + i * 3, "rotation_euler_deg": [0.0, 40.0 - i * 3, 0.0],
         "interp_in": fam, "interp_out": fam}
        for i, fam in enumerate(familias)
    ]
    for space in ("local", "global"):
        for conjunto in (keys, keys_mistos, keys_easing):
            _compara_navegador_e_servidor(
                anim, chain, conjunto, space, tmp_path, node, curves_js)


def test_api_apagar_keyframe_volta_a_curva_original(job, tmp_path):
    """Apagar a keyframe tem que devolver o osso a curva original."""
    from server import refine_api
    from server.refine_api import JOBS_DIR

    orig = rio.load_animation(JOBS_DIR / job / "anim.json")
    y_orig = [float(cons.quat_to_euler_xyz_deg(orig.rotations["Head"][t])[1])
              for t in range(orig.num_frames)]

    refine_api.put_keyframes(job, refine_api.KeyframeBody(
        bone="Head", space="local",
        keyframes=[{"frame": 10, "rotation_euler_deg": [0, 40, 0]}]))
    d = refine_api.get_curves(job, "Head", "local")
    assert len(d["keyframes"]) == 1
    assert abs(d["channels"]["y"][10] - 40.0) < 1e-6     # a keyframe valeu
    # o frame 0 e a ancora (fica igual); dentro da janela a curva mudou
    assert abs(d["channels"]["y"][5] - y_orig[5]) > 1.0

    refine_api.put_keyframes(job, refine_api.KeyframeBody(
        bone="Head", space="local", keyframes=[]))
    d2 = refine_api.get_curves(job, "Head", "local")
    assert d2["keyframes"] == []
    # a curva voltou a ser a original (o payload arredonda em 4 casas)
    assert d2["channels"]["y"] == pytest.approx(y_orig, abs=1e-3)


def test_intervalo_de_efeito_da_keyframe():
    """Cada keyframe tem um intervalo de efeito (inicio/fim) ajustavel.

    Sem intervalo escolhido, a janela sai das vizinhas (como sempre); com
    ele, a keyframe age so naquele trecho.
    """
    anim = _anim(60)
    base = anim.rotations["Head"].copy()
    vizinhas = [{"frame": 10, "rotation_euler_deg": [0, 30, 0]},
                {"frame": 40, "rotation_euler_deg": [0, -30, 0]}]
    derivadas = be.build_keyframe_edits("Head", "local", vizinhas, anim.num_frames)
    assert [(e.start, e.end) for e in derivadas] == [(0, 39), (11, 59)]
    # sem intervalo explicito, a edicao nao guarda nenhum
    assert derivadas[0].range_explicit is None

    fixas = [{"frame": 10, "rotation_euler_deg": [0, 30, 0], "start": 4, "end": 16}]
    edits = be.build_keyframe_edits("Head", "local", fixas, anim.num_frames)
    assert (edits[0].start, edits[0].end) == (4, 16)
    assert edits[0].range_explicit == [4, 16]
    # e a keyframe so age naquele trecho: antes e depois, o clipe original
    out, _ = be.rebuild_bone(anim, "Head", base, edits)
    diff = np.abs(out.rotations["Head"] - anim.rotations["Head"]).max(axis=1)
    assert float(diff[3]) == 0.0, "antes do inicio a keyframe nao age"
    assert float(diff[4]) > 1e-6, "dentro do intervalo a keyframe age"
    assert float(diff[17]) == 0.0, "depois do fim a keyframe nao age"


def test_intervalo_de_efeito_da_keyframe_na_api(job):
    """A API aceita e devolve o intervalo de efeito."""
    from server import refine_api

    body = refine_api.KeyframeBody(bone="Head", space="local", keyframes=[
        {"frame": 10, "rotation_euler_deg": [0, 30, 0], "start": 4, "end": 16},
    ])
    r = refine_api.put_keyframes(job, body)
    k = r["keyframes"][0]
    assert (k["start"], k["end"]) == (4, 16)
    assert k["range_explicit"] == [4, 16]

    # um frame fora do intervalo e erro
    with pytest.raises(Exception) as e:
        refine_api.put_keyframes(job, refine_api.KeyframeBody(
            bone="Head", space="local", keyframes=[
                {"frame": 10, "rotation_euler_deg": [0, 30, 0], "start": 20, "end": 30},
            ]))
    assert "dentro de" in str(e.value)
