"""Editor de bone com rebake por keyframe.

A animacao e **toda bakeada** (uma pose por frame, sem curvas). Entao uma edicao
"de keyframe" precisa reescrever **cada frame** do intervalo afetado:

    anchor_before ......... [start ...... frame ...... end] ......... anchor_after
      (intacto)              ^----------- afetado -----------^        (intacto)

* no `frame` alvo o valor passa a ser o valor editado;
* de `anchor_before` (= start-1) ate o frame alvo, interpola com **ease-in-out**
  (smoothstep: derivada zero nas pontas, portanto sem "salto");
* do frame alvo ate `anchor_after` (= end+1) o mesmo, na volta;
* **frames fora de [start, end] ficam bit-exatamente iguais aos originais.**

Isso e verificado por teste de diff numerico.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from core.mixamo import (ANIMATED_BONES, BONE_NAMES, BONE_PARENT, quat_conj,
                        quat_identity, quat_mul, quat_normalize, world_rotation)
from core.refine.constraints import euler_xyz_deg_to_quat, quat_to_euler_xyz_deg, slerp

# Espacos em que os angulos X/Y/Z do editor podem ser interpretados.
#
# * ``local``  — rotacao do osso **em relacao ao pai**; os eixos acompanham a
#   hierarquia (e o que o editor usava antes desta opcao, e o padrao dos DCCs
#   para "local"). Medido no job e63f073c2149: o antebraco esquerdo mostra
#   Z=-0,6 graus aqui, contra -62,7 em mundo — a diferenca e toda do ombro.
# * ``global`` — orientacao **absoluta do osso na cena**; os eixos ficam
#   presos ao mundo, entao o mesmo numero significa a mesma coisa em qualquer
#   osso e em qualquer frame.
#
# Os dois sao inversos exatos: ler e escrever no mesmo espaco devolve o
# quaternion original (ver `tests/test_refine.py`).
EDIT_SPACES = ("local", "global")

# Modos de interpolacao do rebake (o que o editor de curvas desenha).
#
# * ``smooth`` — ease-in-out (smoothstep): derivada zero nas pontas, sem
#   "salto" na transicao. E o historico do editor de bone.
# * ``linear`` — reta entre a ancora e o alvo.
# * ``step``   — o valor segura (constante) ate o frame alvo: e o "hold" dos
#   DCCs, para um membro que fica parado e muda de pose de uma vez.
# * ``bezier`` — as curvas de interpolacao propriamente ditas: cada keyframe
#   tem uma alca de entrada e uma de saida (as "handles" dos DCCs) e o
#   segmento entre duas keyframes e uma bezier cubica.
#
# As familias classicas de easing (seno, quad, cubic, quart, quint, expo,
# circular, back, elastica e bounce) tambem valem: cada uma tem uma variante
# de entrada (a curva acelera) e uma de saida (a curva freia).
EDIT_INTERPOLATIONS = (
    "smooth", "linear", "step", "sine", "quad", "cubic", "quart", "quint",
    "expo", "circ", "back", "elastic", "bounce", "bezier",
)

# Meia-largura (em frames) da janela de rebake de uma keyframe que nao tem
# vizinha: 20 frames de rampa para cada lado (~0,7 s a 30 fps).
DEFAULT_KEY_HALF = 20


def smoothstep(u: float) -> float:
    u = float(np.clip(u, 0.0, 1.0))
    return u * u * (3.0 - 2.0 * u)


# ---------------------------------------------------------------------------
# As familias classicas de easing
# ---------------------------------------------------------------------------
# Cada familia tem duas funcoes: a de ENTRADA (a curva acelera ate o alvo) e
# a de SAIDA (a curva freia saindo do alvo). E o par que modela a "entrada" e
# a "saida" de uma keyframe.
def _sine_in(t): return 1.0 - math.cos(t * math.pi / 2.0)
def _sine_out(t): return math.sin(t * math.pi / 2.0)

def _quad_in(t): return t * t
def _quad_out(t): return 1.0 - (1.0 - t) ** 2

def _cubic_in(t): return t ** 3
def _cubic_out(t): return 1.0 - (1.0 - t) ** 3

def _quart_in(t): return t ** 4
def _quart_out(t): return 1.0 - (1.0 - t) ** 4

def _quint_in(t): return t ** 5
def _quint_out(t): return 1.0 - (1.0 - t) ** 5

def _expo_in(t): return 0.0 if t <= 0.0 else 2.0 ** (10.0 * (t - 1.0))
def _expo_out(t): return 1.0 if t >= 1.0 else 1.0 - 2.0 ** (-10.0 * t)

def _circ_in(t): return 1.0 - math.sqrt(max(0.0, 1.0 - t * t))
def _circ_out(t): return math.sqrt(max(0.0, 1.0 - (t - 1.0) ** 2))

def _back_in(t):
    c1, c3 = 1.70158, 2.70158
    return c3 * t ** 3 - c1 * t ** 2
def _back_out(t):
    c1, c3 = 1.70158, 2.70158
    return 1.0 + c3 * (t - 1.0) ** 3 + c1 * (t - 1.0) ** 2

def _elastic_in(t):
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    c4 = (2.0 * math.pi) / 3.0
    return -(2.0 ** (10.0 * t - 10.0)) * math.sin((t * 10.0 - 10.75) * c4)
def _elastic_out(t):
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    c4 = (2.0 * math.pi) / 3.0
    return 2.0 ** (-10.0 * t) * math.sin((t * 10.0 - 0.75) * c4) + 1.0

def _bounce_out(t):
    n1, d1 = 7.5625, 2.75
    if t < 1.0 / d1:
        return n1 * t * t
    if t < 2.0 / d1:
        t -= 1.5 / d1
        return n1 * t * t + 0.75
    if t < 2.5 / d1:
        t -= 2.25 / d1
        return n1 * t * t + 0.9375
    t -= 2.625 / d1
    return n1 * t * t + 0.984375
def _bounce_in(t): return 1.0 - _bounce_out(1.0 - t)

# o catalogo: nome -> (entrada, saida)
EASINGS: dict[str, tuple] = {
    "smooth": (smoothstep, smoothstep),
    "linear": (lambda t: t, lambda t: t),
    # o "hold": segura a ancora ate o alvo e segura o alvo depois
    "step": (lambda t: 1.0 if t >= 1.0 - 1e-9 else 0.0, lambda t: 0.0),
    "sine": (_sine_in, _sine_out),
    "quad": (_quad_in, _quad_out),
    "cubic": (_cubic_in, _cubic_out),
    "quart": (_quart_in, _quart_out),
    "quint": (_quint_in, _quint_out),
    "expo": (_expo_in, _expo_out),
    "circ": (_circ_in, _circ_out),
    "back": (_back_in, _back_out),
    "elastic": (_elastic_in, _elastic_out),
    "bounce": (_bounce_in, _bounce_out),
}


def ease_in(u: float, mode: str = "smooth") -> float:
    """Curva da ANCORA ate o alvo (lado esquerdo da keyframe)."""
    u = float(np.clip(u, 0.0, 1.0))
    fn = EASINGS.get(mode, EASINGS["smooth"])[0]
    return float(fn(u))


def ease_out(u: float, mode: str = "smooth") -> float:
    """Curva do alvo ate a ANCORA (lado direito da keyframe)."""
    u = float(np.clip(u, 0.0, 1.0))
    fn = EASINGS.get(mode, EASINGS["smooth"])[1]
    return float(fn(u))


def segment_ease(mode_out: str, mode_in: str, x: float) -> float:
    """Como o segmento entre duas keyframes avanca (0 no inicio, 1 no fim).

    Cada keyframe tem **uma** opcao de entrada e **uma** de saida: o
    segmento sai da keyframe de acordo com a saida dela e chega na keyframe
    de destino seguindo a entrada dela.

    * com os dois modos iguais, o segmento inteiro usa aquele modo (e o
      comportamento de sempre);
    * com modos diferentes, o segmento e dividido ao meio: a primeira metade
      segue a **saida** de quem deixa e a segunda a **entrada** de quem
      chega.
    """
    x = float(np.clip(x, 0.0, 1.0))
    if mode_out == mode_in:
        return ease_in(x, mode_out)
    if x <= 0.5:
        return 0.5 * ease_in(x * 2.0, mode_out)
    return 0.5 + 0.5 * ease_out((x - 0.5) * 2.0, mode_in)


# ---------------------------------------------------------------------------
# Curvas de interpolacao (bezier cubica com as "handles" dos DCCs)
# ---------------------------------------------------------------------------
def bezier_point(p0, p1, p2, p3, u: float) -> tuple[float, float]:
    """Ponto de uma bezier cubica (x e y) no parametro `u`."""
    mu = 1.0 - u
    a = mu * mu * mu
    b = 3 * mu * mu * u
    c = 3 * mu * u * u
    d = u * u * u
    return (a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
            a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1])


def bezier_param_for_x(p0, p1, p2, p3, x: float, iters: int = 48) -> float:
    """O parametro `u` em que a bezier passa por `x` (bisseccao).

    A bezier e monotonica em x enquanto as alcas ficarem dentro do segmento
    (e o editor garante isso), entao a bisseccao acha o parametro exato.
    """
    lo, hi = 0.0, 1.0
    for _ in range(iters):
        mid = (lo + hi) / 2.0
        if bezier_point(p0, p1, p2, p3, mid)[0] < x:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def _auto_handle(span: float, dv: float) -> list[float]:
    """Alca automatica: um terco do segmento, sobre a linha dos valores.

    E o que o Blender chama de "auto": a alca fica na reta entre as duas
    keyframes, o que da uma curva suave sem estourar.
    """
    return [span / 3.0, dv / 3.0]


def _handle_pair(h, span: float, dv: float, sign: float) -> list[float]:
    """Normaliza uma alca: [dx, dy] com o dx dentro do segmento."""
    if not h:
        return _auto_handle(span, dv)
    try:
        dx = float(h[0])
        dy = float(h[1])
    except (TypeError, ValueError, IndexError):
        return _auto_handle(span, dv)
    # o dx e a distancia em frames; nunca passa do segmento (a bezier
    # deixaria de ser monotonica em x)
    lim = max(span, 1e-6)
    dx = max(0.0, min(abs(dx), lim))
    return [dx, dy * sign]


def _bezier_factor(edit: "BoneEdit", side: str, fa: int, fb: int,
                   e_a: np.ndarray, e_b: np.ndarray, t: int) -> float:
    """Quanto do segmento (fa -> fb) ja foi percorrido no frame `t`.

    `side` = "in" (o segmento que ENTRA na keyframe) ou "out" (o que sai).
    O segmento e uma bezier cubica: as duas alcas sao as handles das duas
    keyframes nas pontas; o que ela devolve e o fator do slerp.
    """
    span = max(fb - fa, 1e-9)
    # a "ease" e medida no canal das alcas (o angulo de rotacao do osso):
    # e o canal que o editor de curvas mostra.
    ch = max(0, min(int(getattr(edit, "handle_channel", 1)), 2))
    dv = float(e_b[ch] - e_a[ch])
    if side == "in":
        h1 = _handle_pair(edit.handle_prev, span, dv, 1.0)   # alca da anterior
        h2 = _handle_pair(edit.handle_in, span, dv, -1.0)    # alca desta
    else:
        h1 = _handle_pair(edit.handle_out, span, dv, 1.0)   # alca desta
        h2 = _handle_pair(None, span, dv, -1.0)             # a outra ponta
    p0 = (float(fa), float(e_a[ch]))
    p1 = (fa + h1[0], float(e_a[ch]) + h1[1])
    p2 = (fb - h2[0], float(e_b[ch]) + h2[1])
    p3 = (float(fb), float(e_b[ch]))
    u = bezier_param_for_x(p0, p1, p2, p3, float(t))
    val = bezier_point(p0, p1, p2, p3, u)[1]
    # o fator do slerp: quanto do caminho (em angulo) ja foi feito
    if abs(dv) < 1e-9:
        return float(np.clip(u, 0.0, 1.0))
    return float(np.clip((val - p0[1]) / dv, 0.0, 1.0))


@dataclass
class BoneEdit:
    bone: str
    frame: int
    rotation: list[float] | None = None      # quaternion alvo (x,y,z,w)
    rotation_euler_deg: list[float] | None = None   # alternativa em graus XYZ
    space: str = "local"                      # espaco dos euler: local | global
    translation: list[float] | None = None   # so faz sentido no root (Hips)
    start: int | None = None                 # intervalo afetado (inclusivo)
    end: int | None = None
    interp: str = "smooth"                   # smooth | linear | step | bezier
    # A opcao de SAIDA da keyframe: como a curva deixa dela. A opcao de
    # ENTRADA e o proprio `interp` (como a curva chega nela).
    interp_out: str = ""                     # vazio = igual ao `interp`
    # A opcao de saida da keyframe ANTERIOR: e ela que modela a primeira
    # metade do segmento que chega nesta. Resolvido pelo servidor.
    interp_prev: str = ""
    # O intervalo de efeito ESCOLHIDO pelo usuario (arrastado na timeline).
    # None = a janela sai das vizinhas (o comportamento padrao).
    range_explicit: list[int] | None = None
    # Alcas da bezier (so no modo "bezier"): [dx em frames, dy em graus].
    # `handle_in` e a alca de ENTRADA da keyframe, `handle_out` a de SAIDA, e
    # `handle_prev` e a alca de saida da keyframe ANTERIOR (e ela que desenha
    # o lado esquerdo do segmento). None = alca automatica.
    handle_in: list[float] | None = None
    handle_out: list[float] | None = None
    handle_prev: list[float] | None = None
    # O canal (0=X, 1=Y, 2=Z) em que as alcas medem o valor. As curvas de
    # interpolacao sao desenhadas por canal, entao a alca pertence a um.
    handle_channel: int = 1
    author: str = "user"
    note: str = ""
    created_at: float = field(default_factory=lambda: time.time())

    # ---- conveniencias -------------------------------------------------
    def target_quat(self, parent_world: np.ndarray | None = None) -> np.ndarray | None:
        """Quaternion LOCAL alvo (e o que a `Animation` guarda).

        `parent_world` e a rotacao mundial do pai no frame editado: so e usada
        no espaco `global`, onde os euler descrevem a orientacao absoluta e
        precisam ser trazidos de volta para o espaco local do osso. Sem ela (ou
        na raiz, cujo pai e a identidade) global e local coincidem.
        """
        if self.rotation is not None:
            return quat_normalize(np.asarray(self.rotation, np.float64))
        if self.rotation_euler_deg is not None:
            q = euler_xyz_deg_to_quat(np.asarray(self.rotation_euler_deg, np.float64))
            if self.space == "global" and parent_world is not None:
                q = quat_mul(quat_conj(quat_normalize(
                    np.asarray(parent_world, np.float64))), q)
            return q
        return None

    def validate(self, total_frames: int | None = None) -> list[str]:
        errs: list[str] = []
        if self.bone.replace("mixamorig:", "") not in BONE_NAMES:
            errs.append(f"osso inexistente: {self.bone!r}")
        if self.frame < 0:
            errs.append("frame negativo")
        if total_frames is not None and self.frame >= total_frames:
            errs.append(f"frame {self.frame} fora do clipe (0..{total_frames - 1})")
        s = self.start if self.start is not None else self.frame
        e = self.end if self.end is not None else self.frame
        if s > e:
            errs.append(f"intervalo invalido: start={s} > end={e}")
        if not (s <= self.frame <= e):
            errs.append(f"o frame editado ({self.frame}) precisa estar dentro de [{s}, {e}]")
        if self.rotation is None and self.rotation_euler_deg is None and self.translation is None:
            errs.append("edicao sem alvo: informe rotation, rotation_euler_deg ou translation")
        if self.rotation is not None and len(self.rotation) != 4:
            errs.append("rotation deve ter 4 valores (x,y,z,w)")
        if self.rotation_euler_deg is not None and len(self.rotation_euler_deg) != 3:
            errs.append("rotation_euler_deg deve ter 3 valores (x,y,z)")
        if self.space not in EDIT_SPACES:
            errs.append(
                f"invalid space: {self.space!r} (use {' or '.join(EDIT_SPACES)})")
        if self.interp not in EDIT_INTERPOLATIONS:
            errs.append(
                f"invalid interp: {self.interp!r} "
                f"(use {' ou '.join(EDIT_INTERPOLATIONS)})")
        if self.interp_out and self.interp_out not in EDIT_INTERPOLATIONS:
            errs.append(
                f"interp_out invalido: {self.interp_out!r} "
                f"(use {' ou '.join(EDIT_INTERPOLATIONS)})")
        if self.interp_prev and self.interp_prev not in EDIT_INTERPOLATIONS:
            errs.append(
                f"interp_prev invalido: {self.interp_prev!r} "
                f"(use {' ou '.join(EDIT_INTERPOLATIONS)})")
        for nome, h in (("handle_in", self.handle_in),
                        ("handle_out", self.handle_out),
                        ("handle_prev", self.handle_prev)):
            if h is None:
                continue
            if not isinstance(h, (list, tuple)) or len(h) != 2:
                errs.append(f"{nome} invalido: esperado [dx, dy]")
            else:
                try:
                    float(h[0]), float(h[1])
                except (TypeError, ValueError):
                    errs.append(f"{nome} invalido: esperado [dx, dy] numerico")
        if self.translation is not None and len(self.translation) != 3:
            errs.append("translation deve ter 3 valores (x,y,z)")
        if self.translation is not None and self.bone.replace("mixamorig:", "") != "Hips":
            errs.append("translation so e suportada no osso Hips (root)")
        return errs

    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: v for k, v in d.items() if v not in (None, "", [])} | {"bone": self.bone,
                                                                         "frame": self.frame}

    @staticmethod
    def from_dict(d: dict) -> "BoneEdit":
        return BoneEdit(
            bone=str(d["bone"]), frame=int(d["frame"]),
            rotation=list(d["rotation"]) if d.get("rotation") else None,
            rotation_euler_deg=list(d["rotation_euler_deg"]) if d.get("rotation_euler_deg") else None,
            # edicoes salvas antes do campo `space` existirem: retrocompativeis
            space=str(d.get("space") or "local"),
            translation=list(d["translation"]) if d.get("translation") else None,
            start=int(d["start"]) if d.get("start") is not None else None,
            end=int(d["end"]) if d.get("end") is not None else None,
            # edicoes salvas antes do campo `interp` existir: retrocompativeis
            interp=str(d.get("interp") or "smooth"),
            interp_out=str(d.get("interp_out") or ""),
            interp_prev=str(d.get("interp_prev") or ""),
            range_explicit=(list(d["range_explicit"]) if d.get("range_explicit") else None),
            handle_in=(list(d["handle_in"]) if d.get("handle_in") else None),
            handle_out=(list(d["handle_out"]) if d.get("handle_out") else None),
            handle_prev=(list(d["handle_prev"]) if d.get("handle_prev") else None),
            handle_channel=int(d.get("handle_channel", 1)),
            author=str(d.get("author", "user")), note=str(d.get("note", "")),
            created_at=float(d.get("created_at", time.time())),
        )


# ---------------------------------------------------------------------------
# Rebake
# ---------------------------------------------------------------------------
def _parent_world_at(rotations: dict[str, np.ndarray], bone: str, frame: int):
    """Rotacao mundial do PAI de `bone` no frame `frame` (identidade na raiz).

    E o que converte euler global -> quaternion local. Usa a pose do frame
    ALVO, antes de qualquer rebake: e a unica definicao consistente para um
    clipe 100% bakeado, em que nao existe curva para reavaliar a cada frame.
    """
    parent = BONE_PARENT.get(bone)
    if parent is None or parent not in rotations:
        return quat_identity()
    return world_rotation({k: v[frame] for k, v in rotations.items()}, parent)


# Versao publica (o mesmo calculo): o editor de curvas precisa da rotacao
# mundial do pai para reinterpretar o alvo de uma keyframe no espaco global.
parent_world_at = _parent_world_at


def apply_edit(anim, edit: BoneEdit) -> tuple[object, dict]:
    """Rebakeia o intervalo afetado. Devolve (nova Animation, relatorio)."""
    from core.retarget import Animation

    errs = edit.validate(anim.num_frames)
    if errs:
        raise ValueError("invalid edit:\n  - " + "\n  - ".join(errs))

    T = anim.num_frames
    bone = edit.bone if edit.bone in anim.rotations else edit.bone.replace("mixamorig:", "")
    if bone not in anim.rotations:
        raise ValueError(f"o osso {edit.bone!r} nao tem rotacao animada nesta animacao")
    if bone not in ANIMATED_BONES:
        raise ValueError(f"o osso {bone!r} nao e animavel (end-cap)")

    rotations = {k: np.asarray(v, np.float64).copy() for k, v in anim.rotations.items()}
    root = np.asarray(anim.root_translation, np.float64).copy()
    series = rotations[bone]

    start = max(0, int(edit.start if edit.start is not None else edit.frame))
    end = min(T - 1, int(edit.end if edit.end is not None else edit.frame))
    frame = int(np.clip(edit.frame, start, end))
    a_before = max(0, start - 1)
    a_after = min(T - 1, end + 1)

    q_edit = edit.target_quat(_parent_world_at(rotations, bone, frame))
    changed = 0

    if q_edit is not None:
        q_before = series[a_before].copy()
        q_after = series[a_after].copy()
        # A primeira metade do segmento que CHEGA na keyframe segue a saida da
        # keyframe anterior; a segunda segue a entrada desta.
        mode_in = edit.interp
        mode_out = edit.interp_prev or mode_in
        mode_exit = edit.interp_out or mode_in
        if "bezier" in (mode_in, mode_out):
            # As curvas de interpolacao: o segmento e uma bezier cubica com as
            # alcas das duas keyframes. O que a bezier define e o COMO o
            # segmento avanca (a "ease"); o valor vai pelo slerp.
            e_before = quat_to_euler_xyz_deg(q_before)
            e_edit = quat_to_euler_xyz_deg(q_edit)
            e_after = quat_to_euler_xyz_deg(q_after)
            for t in range(start, frame):
                u = _bezier_factor(
                    edit, "in", a_before, frame, e_before, e_edit, t)
                series[t] = slerp(q_before, q_edit, u)
                changed += 1
            series[frame] = q_edit
            changed += 1
            for t in range(frame + 1, end + 1):
                u = _bezier_factor(
                    edit, "out", frame, a_after, e_edit, e_after, t)
                series[t] = slerp(q_edit, q_after, u)
                changed += 1
        else:
            for t in range(start, frame):
                x = (t - a_before) / max(1e-9, frame - a_before)
                u = segment_ease(mode_out, mode_in, x)
                series[t] = slerp(q_before, q_edit, u)
                changed += 1
            series[frame] = q_edit
            changed += 1
            for t in range(frame + 1, end + 1):
                x = (t - frame) / max(1e-9, a_after - frame)
                u = ease_out(x, mode_exit)
                series[t] = slerp(q_edit, q_after, u)
                changed += 1
        # normaliza SOMENTE o intervalo escrito: fora dele os valores precisam ficar
        # bit-exatamente iguais aos originais (criterio de aceite)
        series[start:end + 1] = np.stack(
            [quat_normalize(q) for q in series[start:end + 1]])

    t_changed = 0
    if edit.translation is not None and bone.replace("mixamorig:", "") == "Hips":
        v_before = root[a_before].copy()
        v_after = root[a_after].copy()
        v_edit = np.asarray(edit.translation, np.float64)
        for t in range(start, frame):
            u = ease_in((t - a_before) / max(1e-9, frame - a_before), edit.interp)
            root[t] = v_before * (1 - u) + v_edit * u
            t_changed += 1
        root[frame] = v_edit
        t_changed += 1
        for t in range(frame + 1, end + 1):
            u = ease_out((t - frame) / max(1e-9, a_after - frame), edit.interp)
            root[t] = v_edit * (1 - u) + v_after * u
            t_changed += 1

    out = Animation(
        fps=anim.fps, num_frames=T, bone_names=list(anim.bone_names),
        rotations=rotations, root_translation=root,
        meta={**dict(getattr(anim, "meta", {}) or {}),
              "edits_applied": int((getattr(anim, "meta", {}) or {}).get("edits_applied", 0)) + 1},
    )
    report = {
        "bone": bone, "frame": frame, "affected": [start, end],
        "frames_rewritten": changed + t_changed,
        "anchors": [a_before, a_after],
        "author": edit.author,
        "space": edit.space,
        "interp": edit.interp,
    }
    return out, report


def rebake_from_edits(anim, edits: list[BoneEdit]) -> tuple[object, list[dict]]:
    """Reaplica uma lista de edicoes em ordem (usado no undo/redo)."""
    reports = []
    cur = anim
    for e in edits:
        cur, r = apply_edit(cur, e)
        reports.append(r)
    return cur, reports


# ---------------------------------------------------------------------------
# Keyframes (o editor de curvas)
# ---------------------------------------------------------------------------
def keyframe_windows(keys, total: int,
                     half: int = DEFAULT_KEY_HALF) -> list[tuple[int, int]]:
    """Janela de rebake `[inicio, fim]` de cada keyframe, na ordem dos frames.

    `keys` pode ser uma lista de frames (a janela sai das vizinhas) ou uma
    lista de dicionarios (com `frame` e, opcionalmente, `start`/`end` — o
    intervalo que o usuario escolheu arrastando na timeline).

    Sem intervalo explicito, a janela de uma keyframe vai do frame SEGUINTE a
    vizinha anterior ate o frame ANTERIOR a vizinha seguinte. Assim as duas
    pontas da janela sao exatamente as vizinhas, que viram as **ancoras** do
    rebake: a curva sai do valor da vizinha e chega nela de novo. E o que faz
    o resultado nao depender da ordem em que as keyframes foram criadas.

    Sem vizinhas, a janela e `[frame - half, frame + half]`.

    Levanta `ValueError` se houver frames repetidos.
    """
    if keys and not isinstance(keys[0], dict):
        chaves = [{"frame": int(f)} for f in keys]
    else:
        chaves = list(keys)
    ordenadas = sorted(chaves, key=lambda k: int(k["frame"]))
    fs = [int(k["frame"]) for k in ordenadas]
    if len(set(fs)) != len(fs):
        raise ValueError("frames de keyframe repetidos")
    last = max(int(total) - 1, 0)
    out: list[tuple[int, int]] = []
    for i, k in enumerate(ordenadas):
        f = int(k["frame"])
        # o intervalo explicito (arrastado na timeline) tem prioridade
        if k.get("start") is not None and k.get("end") is not None:
            s = max(0, min(int(k["start"]), last))
            e = max(s, min(int(k["end"]), last))
            s = max(0, min(s, f))
            e = min(last, max(e, f))
            out.append((s, e))
            continue
        prev = fs[i - 1] if i > 0 else None
        nxt = fs[i + 1] if i + 1 < len(fs) else None
        start = prev + 1 if prev is not None else max(0, f - half)
        end = nxt - 1 if nxt is not None else min(last, f + half)
        start = max(0, min(start, f))
        end = min(last, max(end, f))
        out.append((start, end))
    return out


def build_keyframe_edits(bone: str, space: str, keys: list[dict], total: int,
                          half: int = DEFAULT_KEY_HALF) -> list[BoneEdit]:
    """Converte um conjunto de keyframes em `BoneEdit`s (com as janelas).

    `keys` sao dicionarios com `frame`, `rotation_euler_deg`, `interp` e
    `note`. As keyframes sao ordenadas por frame; a janela de cada uma vem
    de `keyframe_windows`.

    As alcas da bezier sao resolvidas aqui: cada edicao sabe a sua alca de
    entrada e a de saida da keyframe ANTERIOR (e ela que desenha o lado
    esquerdo do segmento).
    """
    ordered = sorted(keys, key=lambda k: int(k["frame"]))
    windows = keyframe_windows(ordered, total, half)
    edits: list[BoneEdit] = []
    prev_out: str = ""
    prev_handle: list[float] | None = None
    for k, (s, e) in zip(ordered, windows):
        h_in = _norm_handle(k.get("handle_in"))
        h_out = _norm_handle(k.get("handle_out"))
        chan = int(k.get("handle_channel", 1) or 1)
        mode_in = str(k.get("interp_in") or k.get("interp") or "smooth")
        mode_out = str(k.get("interp_out") or mode_in)
        explicito = (k.get("start") is not None and k.get("end") is not None)
        edits.append(BoneEdit(
            bone=bone, frame=int(k["frame"]),
            rotation_euler_deg=[float(x) for x in k["rotation_euler_deg"]],
            space=space, start=s, end=e,
            interp=mode_in, interp_out=mode_out, interp_prev=prev_out,
            handle_in=h_in, handle_out=h_out, handle_prev=prev_handle,
            handle_channel=max(0, min(chan, 2)),
            range_explicit=[s, e] if explicito else None,
            note=str(k.get("note") or ""),
        ))
        prev_out = mode_out
        prev_handle = h_out
    return edits


def _norm_handle(h) -> list[float] | None:
    """Normaliza uma alca vinda da API: [dx, dy] ou None."""
    if not h:
        return None
    try:
        return [float(h[0]), float(h[1])]
    except (TypeError, ValueError, IndexError):
        return None


def rebuild_bone(anim, bone: str, base: np.ndarray,
                 edits: list[BoneEdit]) -> tuple[object, list[dict]]:
    """Recria a serie do osso a partir da BASE (sem keyframes) + as edicoes.

    A base e a serie do osso como ela estava antes da primeira keyframe.
    Reaplicar as keyframes sobre a base (e nao sobre o clipe ja editado)
    e o que faz **apagar** uma keyframe voltar o osso ao estado anterior
    em vez de deixar o rebake antigo pelo clipe.

    `bone` e o nome do osso (o da serie que volta a ser a base); com uma
    lista de edicoes vazia ele continua sendo necessario — sem ele nao
    haveria de onde saber qual osso resetar.
    """
    from core.retarget import Animation

    rots = {k: np.asarray(v, np.float64).copy() for k, v in anim.rotations.items()}
    name = bone if bone in rots else bone.replace("mixamorig:", "")
    if name in rots:
        rots[name] = np.asarray(base, np.float64).copy()
    tmp = Animation(fps=anim.fps, num_frames=anim.num_frames,
                    bone_names=list(anim.bone_names), rotations=rots,
                    root_translation=np.asarray(anim.root_translation, np.float64).copy(),
                    meta=dict(getattr(anim, "meta", {}) or {}))
    return rebake_from_edits(tmp, edits)


# ---------------------------------------------------------------------------
# Historico persistente
# ---------------------------------------------------------------------------
@dataclass
class EditHistory:
    edits: list[BoneEdit] = field(default_factory=list)
    undone: list[BoneEdit] = field(default_factory=list)
    # Transplantes nao sao BoneEdits (seriam milhares, um por osso/frame): sao
    # steps de clipe inteiro. Guardamos o clipe ANTERIOR para poder desfazer.
    snapshots: list[dict] = field(default_factory=list)
    # Serie do osso ANTES da primeira keyframe dele: e a "base" sobre a qual
    # as keyframes sao reaplicadas. Sem ela, apagar uma keyframe deixaria
    # o rebake antigo pelo clipe (o osso nao voltaria ao estado anterior).
    base_rotations: dict[str, list] = field(default_factory=dict)
    source: str = ""
    session_id: str = ""
    updated_at: float = field(default_factory=lambda: time.time())

    def base_of(self, bone: str) -> np.ndarray | None:
        b = bone.replace("mixamorig:", "")
        for k, v in (self.base_rotations or {}).items():
            if k.replace("mixamorig:", "") == b:
                return np.asarray(v, np.float64)
        return None

    def set_base(self, bone: str, series) -> None:
        b = bone.replace("mixamorig:", "")
        self.base_rotations = {k: v for k, v in (self.base_rotations or {}).items()
                               if k.replace("mixamorig:", "") != b}
        self.base_rotations[b] = np.asarray(series, np.float64).tolist()

    def add(self, edit: BoneEdit) -> dict:
        self.edits.append(edit)
        self.undone.clear()
        self.updated_at = time.time()
        return {"applied": edit.to_dict(), "total": len(self.edits)}

    def undo(self) -> dict:
        if not self.edits:
            raise ValueError("nada para desfazer")
        e = self.edits.pop()
        self.undone.append(e)
        self.updated_at = time.time()
        return {"undone": e.to_dict(), "total": len(self.edits)}

    def redo(self) -> dict:
        if not self.undone:
            raise ValueError("nada para refazer")
        e = self.undone.pop()
        self.edits.append(e)
        self.updated_at = time.time()
        return {"redone": e.to_dict(), "total": len(self.edits)}

    def log(self) -> list[dict]:
        return [e.to_dict() for e in self.edits]

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id or f"session-{int(self.updated_at)}",
            "source": self.source,
            "updated_at": self.updated_at,
            "edits": [e.to_dict() for e in self.edits],
            "undone": [e.to_dict() for e in self.undone],
            "snapshots": list(self.snapshots),
            "base_rotations": {k: np.asarray(v, float).tolist()
                               for k, v in (self.base_rotations or {}).items()},
        }

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return p

    @staticmethod
    def load(path: str | Path) -> "EditHistory":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        h = EditHistory(
            edits=[BoneEdit.from_dict(x) for x in d.get("edits", [])],
            undone=[BoneEdit.from_dict(x) for x in d.get("undone", [])],
            snapshots=list(d.get("snapshots", [])),
            base_rotations={str(k): np.asarray(v, np.float64)
                            for k, v in (d.get("base_rotations") or {}).items()},
            source=str(d.get("source", "")),
            session_id=str(d.get("session_id", "")),
            updated_at=float(d.get("updated_at", time.time())),
        )
        return h

    def replay(self, anim) -> tuple[object, list[dict]]:
        """Reconstroi a animacao a partir do clipe original + edicoes salvas."""
        return rebake_from_edits(anim, self.edits)
