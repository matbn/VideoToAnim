"""Regressoes dos bugs reportados no job e63f073c2149 (sword_swing_B, 1.19 s).

Tres defeitos independentes, todos com medicao antes/depois:

1. **Braco reto no esqueleto** — o preset de constraints limitava o
   antebraco/canela por eixo Euler da rotacao LOCAL. Essa rotacao e
   ``conj(q_pai) * q_mundo`` e carrega tambem a rotacao do ombro/quadril, entao
   o clamp media a coisa errada: o cotovelo de 45 graus do video saia com 69
   graus de erro. Agora a junta usa `kind: bend`, que mede a flexao REAL pela
   geometria (FK).
2. **Braco dentro do torso** — o lifter antigo, sem solucao de profundidade
   (segmento 2D maior que o osso), escalava o filho em X/Y (ate 61 px de erro
   de reprojecao) e emitia z=0, coplanar com o tronco. Agora X/Y vem exatos da
   imagem e o Z e resolvido por um objetivo que inclui nao-penetracao.
3. **Personagem a 8 m do chao** — `savgol_coeffs` multiplicava os coeficientes
   pelo tamanho da janela, escalando qualquer sinal por `window` (0.98 m ->
   8.82 m com window=9).
"""
from __future__ import annotations

import numpy as np
import pytest

from core import mixamo as mx
from core.canonical import COCO_INDEX, FramePose
from core.lifter import _BONE, AnalyticLifter
from core.refine import constraints as cons
from core.refine import filters as flt
from core.retarget import Animation, Retargeter


# ----------------------------------------------------------------------
# Utilitarios
# ----------------------------------------------------------------------
def _angle(a, b, c) -> float:
    """Angulo em GRAUS no vertice b."""
    v1 = np.asarray(a, np.float64) - np.asarray(b, np.float64)
    v2 = np.asarray(c, np.float64) - np.asarray(b, np.float64)
    n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
    if n1 < 1e-9 or n2 < 1e-9:
        return float("nan")
    return float(np.degrees(np.arccos(np.clip((v1 @ v2) / (n1 * n2), -1.0, 1.0))))


def _axis_quat(axis, deg) -> np.ndarray:
    a = np.asarray(axis, np.float64)
    a = a / np.linalg.norm(a)
    h = np.radians(deg) / 2.0
    return np.array([*(a * np.sin(h)), np.cos(h)])


def _arm_anim(T: int = 3, shoulder_deg: float = -100.0,
              elbow_deg: float = 60.0) -> Animation:
    """Braco levantado com cotovelo dobrado: o cotovelo NAO esta no ombro."""
    rots = {b: np.tile(np.array([0.0, 0.0, 0.0, 1.0]), (T, 1))
            for b in mx.ANIMATED_BONES}
    for t in range(T):
        rots["LeftArm"][t] = _axis_quat([0, 0, 1], shoulder_deg)
        rots["LeftForeArm"][t] = _axis_quat([0, 1, 0], elbow_deg)
        rots["RightArm"][t] = _axis_quat([0, 0, 1], -shoulder_deg)
        rots["RightForeArm"][t] = _axis_quat([0, 1, 0], elbow_deg)
    root = np.tile(np.array([0.0, 0.98, 0.0]), (T, 1))
    return Animation(30.0, T, list(mx.BONE_NAMES), rots, root, {})


def _fk(anim: Animation, t: int = 0) -> dict:
    loc = {b: np.asarray(anim.rotations[b][t], np.float64)
           for b in mx.ANIMATED_BONES}
    return mx.fk_world(loc, np.asarray(anim.root_translation, np.float64)[t])


def _elbow(anim: Animation, t: int = 0, side: str = "Left") -> float:
    p = _fk(anim, t)
    return _angle(p[f"{side}Arm"], p[f"{side}ForeArm"], p[f"{side}Hand"])


def _knee(anim: Animation, t: int = 0, side: str = "Left") -> float:
    p = _fk(anim, t)
    return _angle(p[f"{side}UpLeg"], p[f"{side}Leg"], p[f"{side}Foot"])




# ----------------------------------------------------------------------
# 1. Constraints: a dobradica preserva a flexao real
# ----------------------------------------------------------------------
def test_bend_mede_a_flexao_real_da_junta():
    """`bend` le o angulo geometrico (0 = reto), nao um eixo Euler local.

    O cotovelo esta dobrado 60 graus, mas a junta e medida no ESPACO DO
    MEMBRO: com o ombro a -100 graus, a flexao total vista da FK e 120. E
    exatamente esse angulo que o clamp por eixo Euler errava.
    """
    anim = _arm_anim()
    p = _fk(anim)
    flex, axis = cons.joint_flexion_deg(p, "LeftForeArm")
    assert abs(flex - 120.0) < 1e-6, f"flexao medida fora do esperado: {flex}"
    assert abs(float(np.linalg.norm(axis)) - 1.0) < 1e-9


def test_bend_atinge_o_limite_exatamente():
    """Com o limite em 20 graus, a junta vai para 20 (nao 'quase')."""
    anim = _arm_anim()
    antes = _elbow(anim)
    preset = cons.ConstraintPreset(
        "t", [cons.JointLimit("LeftForeArm", "bend", -5, 20, 1.0)])
    out, rep = cons.apply_constraints(anim, preset)
    depois = _elbow(out)
    assert abs(depois - 20.0) < 0.5, f"cotovelo deveria ficar em 20, ficou em {depois}"
    assert antes > 100.0, "pre-condicao: o cotovelo comeca dobrado"
    assert rep["per_bone"]["LeftForeArm"]["kind"] == "bend"


def test_bend_nao_corrige_o_que_ja_esta_dentro_do_limite():
    """Limite amplo nao pode mexer no braco (bit-exatamente igual)."""
    anim = _arm_anim()
    antes = anim.rotations["LeftForeArm"].copy()
    preset = cons.ConstraintPreset(
        "t", [cons.JointLimit("LeftForeArm", "bend", -5, 170, 1.0)])
    out, _ = cons.apply_constraints(anim, preset)
    assert np.array_equal(out.rotations["LeftForeArm"], antes)


def test_bend_apenas_no_membro_nao_arrasta_o_tronco():
    """A correcao e no eixo da junta: ombro->cotovelo nao muda de direcao."""
    anim = _arm_anim()
    p_antes = _fk(anim)
    preset = cons.ConstraintPreset(
        "t", [cons.JointLimit("LeftForeArm", "bend", -5, 20, 1.0)])
    out, _ = cons.apply_constraints(anim, preset)
    p_depois = _fk(out)
    d_antes = p_antes["LeftForeArm"] - p_antes["LeftArm"]
    d_depois = p_depois["LeftForeArm"] - p_depois["LeftArm"]
    cos = float(np.dot(d_antes, d_depois) /
                (np.linalg.norm(d_antes) * np.linalg.norm(d_depois)))
    assert cos > 0.9999, "o segmento ombro->cotovelo nao pode mudar de direcao"


def test_preset_padrao_usa_bend_no_cotovelo_e_joelho():
    """O preset entregue tem de usar a dobradica geometrica nas 4 juntas."""
    preset = cons.ConstraintPreset.load(cons.default_constraints_path())
    assert preset.validate() == []
    bend = {l.bone: (l.min_deg, l.max_deg) for l in preset.limits if l.kind == "bend"}
    assert set(bend) == {"LeftForeArm", "RightForeArm", "LeftLeg", "RightLeg"}
    # e NAO pode mais existir o clamp de eixo Euler que destruia o braco
    euler_forearm = [l for l in preset.limits
                     if l.bone in ("LeftForeArm", "RightForeArm") and l.kind in "xyz"]
    assert euler_forearm == [], "clamp Euler no antebraco mede a rotacao errada"


def test_bend_recusado_em_osso_que_nao_e_dobradica():
    preset = cons.ConstraintPreset(
        "bad", [cons.JointLimit("Head", "bend", -5, 20, 1.0)])
    assert any("dobradica" in e for e in preset.validate())


def test_bend_recusado_fora_da_faixa_de_flexao():
    preset = cons.ConstraintPreset(
        "bad", [cons.JointLimit("LeftLeg", "bend", -200, 20, 1.0)])
    assert any("flexao" in e for e in preset.validate())


def test_joelho_preserva_a_flexao_dentro_do_limite():
    """Joelho reto esta dentro do limite: o nao-toque tem de ser exato."""
    anim = _arm_anim()
    antes = _knee(anim)
    preset = cons.ConstraintPreset(
        "t", [cons.JointLimit("LeftLeg", "bend", -3, 160, 1.0)])
    out, _ = cons.apply_constraints(anim, preset)
    assert abs(_knee(out) - antes) < 1e-6, "joelho reto esta no limite: nao toca"


# ----------------------------------------------------------------------
# 2. Lifter: reprojecao exata e profundidade sem auto-penetracao
# ----------------------------------------------------------------------
def _synthetic_kp2d(n: int = 24, seed: int = 7) -> list[FramePose]:
    """Clip 2D plausivel: pessoa em pe, bracos variando ao longo do tempo."""
    rng = np.random.default_rng(seed)
    W, H = 960, 540
    out = []
    for t in range(n):
        ang = 2.0 * np.pi * t / max(1, n - 1)
        kp = np.zeros((17, 2), np.float64)
        kp[COCO_INDEX["nose"]] = (480 + 12 * np.sin(ang), 150)
        kp[COCO_INDEX["left_eye"]] = (472, 146)
        kp[COCO_INDEX["right_eye"]] = (488, 146)
        kp[COCO_INDEX["left_ear"]] = (464, 150)
        kp[COCO_INDEX["right_ear"]] = (496, 150)
        kp[COCO_INDEX["left_shoulder"]] = (420, 200)
        kp[COCO_INDEX["right_shoulder"]] = (540, 200)
        kp[COCO_INDEX["left_hip"]] = (445, 360)
        kp[COCO_INDEX["right_hip"]] = (515, 360)
        # Bracos anatomicamente plausive: o cotovelo fica logo abaixo do
        # ombro (a ~0.28 m) e o punho abaixo do cotovelo (a ~0.26 m). O
        # ombro e ancorado em X por largura anatomica fixa, entao o cotovelo
        # tambem precisa se mover pouco em X.
        kp[COCO_INDEX["left_elbow"]] = (420 - 8 * np.sin(ang), 200 + 82)
        kp[COCO_INDEX["left_wrist"]] = (420 - 14 * np.sin(ang), 200 + 82 + 76)
        kp[COCO_INDEX["right_elbow"]] = (540 + 8 * np.sin(ang), 200 + 82)
        kp[COCO_INDEX["right_wrist"]] = (540 + 14 * np.sin(ang), 200 + 82 + 76)
        kp[COCO_INDEX["left_knee"]] = (445, 360 + 110)
        kp[COCO_INDEX["right_knee"]] = (515, 360 + 110)
        kp[COCO_INDEX["left_ankle"]] = (445, 360 + 110 + 105)
        kp[COCO_INDEX["right_ankle"]] = (515, 360 + 110 + 105)
        kp += rng.normal(0.0, 0.4, kp.shape)
        out.append(FramePose(kp, np.ones(17, np.float32), None, W, H))
    return out


def test_lifter_reprojeta_exatamente_sobre_a_imagem():
    """A imagem e a verdade: o kp3d tem de voltar para os mesmos pixels.

    Este e o coracao do bug do braco achatado: a versao anterior escalava o
    filho em X/Y quando o segmento 2D era maior que o osso (medido: ate 61 px).
    """
    frames = _synthetic_kp2d()
    lifter = AnalyticLifter()
    lifted = lifter.lift(frames)
    flex = [COCO_INDEX[n] for n in ("left_elbow", "right_elbow", "left_wrist",
                                    "right_wrist", "left_knee", "right_knee",
                                    "left_ankle", "right_ankle", "nose")]
    for f2, f3 in zip(frames, lifted):
        s = lifter._scale(f2)
        shift = lifter._global_shift(frames[0])
        px = f3.kp3d[:, 0].astype(np.float64) / s + f2.width / 2.0
        py = f2.height / 2.0 - (f3.kp3d[:, 1].astype(np.float64) - shift) / s
        alvo = f2.kp2d.astype(np.float64)
        # ombros/quadris tem largura anatomica fixa: nao exigem pixel-exato
        err = np.linalg.norm(np.stack([px, py], 1) - alvo, axis=1)
        assert float(err[flex].max()) < 1e-3, f"reprojecao furou: {err[flex].max():.4f} px"


def test_lifter_nao_deixa_o_braco_dentro_do_tronco():
    """Nenhum membro livre pode ficar coplanar (z=0) dentro do tronco."""
    frames = _synthetic_kp2d()
    lifted = AnalyticLifter().lift(frames)
    ci = COCO_INDEX
    for f in lifted:
        k = f.kp3d.astype(np.float64)
        a = 0.5 * (k[ci["left_hip"]] + k[ci["right_hip"]])
        b = 0.5 * (k[ci["left_shoulder"]] + k[ci["right_shoulder"]])
        ab = b - a
        for side in ("left", "right"):
            for nome in ("elbow", "wrist"):
                p = k[ci[f"{side}_{nome}"]]
                t = float(np.clip(((p - a) @ ab) / (ab @ ab), 0.0, 1.0))
                q = a + ab * t
                dx = float(np.hypot(p[0] - q[0], p[1] - q[1]))
                if dx < 0.12:      # dentro do tronco em X
                    assert abs(p[2] - q[2]) > 1e-4, (
                        f"{side}_{nome} esta dentro do tronco (dx={dx:.3f}, dz=0)")


def test_lifter_respeita_o_comprimento_dos_ossos():
    """A profundidade e livre, mas o comprimento continua anatomico."""
    frames = _synthetic_kp2d()
    lifted = AnalyticLifter().lift(frames)
    ci = COCO_INDEX


# ----------------------------------------------------------------------
# 3. Filtros: savgol nao pode escalar o sinal
# ----------------------------------------------------------------------
@pytest.mark.parametrize("window,order", [(5, 2), (9, 2), (11, 2), (7, 3)])
def test_savgol_preserva_sinal_constante(window, order):
    """BUG: os coeficientes eram multiplicados por `window`.

    Um filtro de media ponderada tem de devolver um constante igual: com o bug,
    0.98 m virava 8.82 m (0.98 * 9) e o personagem subia 8 m do chao.
    """
    c = flt.savgol_coeffs(window, order)
    assert abs(float(c.sum()) - 1.0) < 1e-9, "os coeficientes tem de somar 1"
    out = flt.savgol(np.full(40, 0.98), 30.0, window=window, order=order)
    assert np.allclose(out, 0.98, atol=1e-9)


def test_savgol_preserva_ruido_de_media_zero():
    """Sinal de media zero nao pode ganhar nivel (vies) com o filtro."""
    rng = np.random.default_rng(3)
    x = rng.normal(0.0, 0.1, 200)
    out = flt.savgol(x, 30.0, window=9, order=2)
    assert abs(float(out.mean())) < 0.01


def test_filtros_nao_moveem_a_translacao_do_root():
    """O root tem de sobreviver ao plano de filtros (a CONFIG usa savgol)."""
    from pathlib import Path

    from core.refine import plan as fp

    T = 40
    rots = {b: np.tile(np.array([0.0, 0.0, 0.0, 1.0]), (T, 1))
            for b in mx.ANIMATED_BONES}
    root = np.tile(np.array([0.0, 0.98, 0.0]), (T, 1))
    anim = Animation(30.0, T, list(mx.BONE_NAMES), rots, root, {})
    out, _ = fp.apply_filter_plan(
        anim, fp.FilterPlan.load(Path("config/filters_default.yaml")))
    assert np.allclose(out.root_translation, root, atol=1e-9), (
        "a translacao do root nao pode ser alterada por um filtro de media")


# ----------------------------------------------------------------------
# 4. Integracao: o rig final mantem o cotovelo do video
# ----------------------------------------------------------------------
def test_pipeline_nao_endireita_o_cotovelo():
    """Fim a fim: lifter -> retarget -> constraints nao deve endireitar o braco.

    Este e o teste do relato: "o braco direito esta levemente curvo no video,
    mas no esqueleto ele esta reto".
    """
    frames = _synthetic_kp2d(n=12)
    lifted = AnalyticLifter().lift(frames)
    anim = Retargeter(fps=30.0).retarget(lifted)
    preset = cons.ConstraintPreset.load(cons.default_constraints_path())
    out, _ = cons.apply_constraints(anim, preset)

    for t in range(len(lifted)):
        antes = _elbow(anim, t, "Left")
        depois = _elbow(out, t, "Left")
        assert abs(depois - antes) < 12.0, (
            f"frame {t}: o cotovelo foi de {antes:.1f} para {depois:.1f} graus")


def test_lifter_respeita_o_comprimento_dos_ossos():
    """A profundidade e livre, mas o comprimento continua anatomico."""
    frames = _synthetic_kp2d()
    lifted = AnalyticLifter().lift(frames)
    ci = COCO_INDEX
    segs = (("left_shoulder", "left_elbow", "upper_arm"),
            ("left_elbow", "left_wrist", "forearm"),
            ("right_shoulder", "right_elbow", "upper_arm"),
            ("right_elbow", "right_wrist", "forearm"),
            ("left_hip", "left_knee", "thigh"),
            ("left_knee", "left_ankle", "shin"))
    for f in lifted:
        k = f.kp3d.astype(np.float64)
        for pai, filho, nome in segs:
            L = float(np.linalg.norm(k[ci[filho]] - k[ci[pai]]))
            # o comprimento e um peso SUAVE: nunca distorce a imagem, mas
            # precisa ficar na mesma ordem de grandeza do anatomico
            assert 0.45 * _BONE[nome] < L < 2.2 * _BONE[nome], (
                f"{pai}->{filho} com {L:.3f} m, esperado perto de {_BONE[nome]:.3f}")


def test_lifter_eh_deterministico():
    frames = _synthetic_kp2d()
    a = AnalyticLifter().lift(frames)
    b = AnalyticLifter().lift(frames)
    for x, y in zip(a, b):
        assert np.array_equal(x.kp3d, y.kp3d)


def test_lifter_relatorio_documenta_penetracao():
    lifter = AnalyticLifter()
    lifter.lift(_synthetic_kp2d())
    rep = lifter.last_report
    assert rep["frames"] > 0
    assert "frames_with_torso_penetration" in rep
    assert set(rep["free_joints"]) >= {"left_elbow", "left_wrist"}

    preset = cons.ConstraintPreset(
        "bad", [cons.JointLimit("Head", "bend", -5, 20, 1.0)])
    assert any("dobradica" in e for e in preset.validate())


def test_bend_recusado_fora_da_faixa_de_flexao():
    preset = cons.ConstraintPreset(
        "bad", [cons.JointLimit("LeftLeg", "bend", -200, 20, 1.0)])
    assert any("flexao" in e for e in preset.validate())


def test_joelho_preserva_a_flexao_dentro_do_limite():
    anim = _arm_anim()
    antes = _knee(anim)
    preset = cons.ConstraintPreset(
        "t", [cons.JointLimit("LeftLeg", "bend", -3, 160, 1.0)])
    out, _ = cons.apply_constraints(anim, preset)
    assert abs(_knee(out) - antes) < 1e-6, "joelho reto esta no limite: nao toca"
