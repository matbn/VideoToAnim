"""Constraints articulares: limites por junta com preset humanoide editavel.

Duas formas de limite, ambas por osso:

* **cone** — limita o angulo TOTAL da rotacao local (desvio em relacao a
  orientacao de referencia). Serve para "o ombro nao gira 180", "a cabeca nao
  vira o rosto para tras".
* **eixo** — limita um eixo especifico (x/y/z) em graus, com min/max
  **assimetricos**: e assim que se expressa "cotovelo nao hiperextende"
  (pequena folga em um sentido, grande no outro).

Cada limite tem `stiffness` (0..1): 1 corrige integralmente, 0 nao corrige. A
correcao e suave (slerp), nunca um corte seco.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from core.mixamo import ANIMATED_BONES, BONE_NAMES, quat_mul, quat_normalize

AXES = {"x": 0, "y": 1, "z": 2}
VALID_KINDS = ("cone", "bend", "x", "y", "z")

# Articulacoes de dobradica (cotovelo/joelho): o limite e medido pelo ANGULO
# REAL da junta, extraido da geometria (FK), e nao por um eixo Euler da
# rotacao local.
#
# Por que: o retarget emite, para cada osso, um arco-minimo que leva a
# direcao de rest ate a direcao alvo, e a rotacao LOCAL resultante e
# `conj(q_pai) * q_mundo`. Esse quaternion NAO e a flexao da junta: ele
# carrega tambem a rotacao do ombro/quadril. Medido no job e63f073c2149, o
# Euler-Z do antebraco varia de -175 a +172 graus e o clamp de
# "hiperextensao" disparava em 95/167 frames, aplicando slerp de ate 118
# graus: o braco-video (cotovelo a 57 graus) saia RETO (171 graus).
#
# `bend` mede o angulo entre os dois ossos da cadeia (a junta de verdade) e
# so corrige quando esse angulo viola o limite. O eixo de dobradica e o
# proprio eixo da junta, entao a correcao mexe no membro e nao no tronco.
BEND_JOINTS: dict[str, tuple[str, str]] = {
    # osso -> (pai, filho): a junta e medida no ponto de articulacao do osso
    "LeftForeArm": ("LeftArm", "LeftHand"),
    "RightForeArm": ("RightArm", "RightHand"),
    "LeftLeg": ("LeftUpLeg", "LeftFoot"),
    "RightLeg": ("RightUpLeg", "RightFoot"),
}


def _unit(v: np.ndarray) -> np.ndarray | None:
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else None


def joint_flexion_deg(pos: dict, bone: str) -> tuple[float, np.ndarray] | None:
    """Flexao real da junta (graus) e o eixo de dobradica, em mundo.

    0 graus = membro reto; positivo = dobrado; negativo = hiperextensao.
    O angulo e medido na geometria (as posicoes vindas da FK), que e a
    unica medida que independe da parametrizacao da rotacao local.
    """
    spec = BEND_JOINTS.get(bone)
    if spec is None:
        return None
    parent, child = spec
    if bone not in pos or parent not in pos or child not in pos:
        return None
    d1 = _unit(np.asarray(pos[bone], np.float64) - np.asarray(pos[parent], np.float64))
    d2 = _unit(np.asarray(pos[child], np.float64) - np.asarray(pos[bone], np.float64))
    if d1 is None or d2 is None:
        return None
    cos = float(np.clip(d1 @ d2, -1.0, 1.0))
    interior = float(np.degrees(np.arccos(cos)))     # 180 = reto
    axis = np.cross(d1, d2)
    if float(np.linalg.norm(axis)) < 1e-6:
        return None                                   # reto: eixo degenerado
    return 180.0 - interior, axis / float(np.linalg.norm(axis))


def _axis_angle_quat(axis: np.ndarray, angle_rad: float) -> np.ndarray:
    a = np.asarray(axis, np.float64)
    n = float(np.linalg.norm(a))
    if n < 1e-9 or abs(angle_rad) < 1e-12:
        return np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float64)
    a = a / n
    h = angle_rad / 2.0
    return np.array([*(a * np.sin(h)), np.cos(h)], dtype=np.float64)



# ---------------------------------------------------------------------------
# Quaternion <-> Euler (XYZ, graus)  e  slerp
# ---------------------------------------------------------------------------
def quat_to_euler_xyz_deg(q: np.ndarray) -> np.ndarray:
    """Extrai angulos XYZ (q = qx*qy*qz) — INVERSA EXATA de euler_xyz_deg_to_quat.

    A versao anterior negava os tres angulos (rotacao x+60 extraia -60) e NAO
    era inversa: o roundtrip errava ate ~175. O clamp de constraints usava as
    duas funcoes em sequencia e, perto de gimbal, uma correcao de 1 grau virava
    ~100 graus de rotacao real (twist espurio de ~180 no antebraco).
    """
    x, y, z, w = quat_normalize(np.asarray(q, np.float64))
    m00 = 1 - 2 * (y * y + z * z)
    m01 = 2 * (x * y - w * z)
    m02 = 2 * (x * z + w * y)
    m10 = 2 * (x * y + w * z)
    m11 = 1 - 2 * (x * x + z * z)
    m12 = 2 * (y * z - w * x)
    m22 = 1 - 2 * (x * x + y * y)
    sy = float(np.clip(m02, -1.0, 1.0))
    ry = float(np.arcsin(sy))
    if abs(sy) < 0.999999:
        rx = float(np.arctan2(-m12, m22))
        rz = float(np.arctan2(-m01, m00))
    else:  # gimbal lock: rx = 0 e a rotacao vai toda para rz
        rx = 0.0
        rz = float(np.arctan2(m10, m11))
    return np.degrees([rx, ry, rz])


def euler_xyz_deg_to_quat(e: np.ndarray) -> np.ndarray:
    rx, ry, rz = np.radians(np.asarray(e, np.float64)) / 2.0
    qx = np.array([np.sin(rx), 0, 0, np.cos(rx)])
    qy = np.array([0, np.sin(ry), 0, np.cos(ry)])
    qz = np.array([0, 0, np.sin(rz), np.cos(rz)])
    return quat_normalize(quat_mul(quat_mul(qx, qy), qz))


def quat_angle_deg(q: np.ndarray) -> float:
    w = abs(float(quat_normalize(np.asarray(q, np.float64))[3]))
    return float(np.degrees(2.0 * np.arccos(np.clip(w, -1.0, 1.0))))


def slerp(q0: np.ndarray, q1: np.ndarray, t: float) -> np.ndarray:
    q0 = quat_normalize(np.asarray(q0, np.float64))
    q1 = quat_normalize(np.asarray(q1, np.float64))
    d = float(np.dot(q0, q1))
    if d < 0.0:
        q1, d = -q1, -d
    if d > 0.9995:
        return quat_normalize(q0 + t * (q1 - q0))
    th = np.arccos(np.clip(d, -1.0, 1.0))
    return quat_normalize(
        np.sin((1 - t) * th) / np.sin(th) * q0 + np.sin(t * th) / np.sin(th) * q1
    )


# ---------------------------------------------------------------------------
# Configuracao
# ---------------------------------------------------------------------------
@dataclass
class JointLimit:
    bone: str
    kind: str = "cone"          # cone | bend | x | y | z
    min_deg: float = -180.0
    max_deg: float = 180.0
    stiffness: float = 1.0
    note: str = ""
    note_en: str = ""

    def validate(self) -> list[str]:
        errs: list[str] = []
        short = self.bone.replace("mixamorig:", "")
        if short not in BONE_NAMES:
            errs.append(f"osso inexistente no rig: {self.bone!r}")
        if self.kind not in VALID_KINDS:
            errs.append(f"kind invalido: {self.kind!r} (use cone/bend/x/y/z)")
        if self.min_deg > self.max_deg:
            errs.append(f"{self.bone}: min_deg ({self.min_deg}) > max_deg ({self.max_deg})")
        if not (0.0 <= self.stiffness <= 1.0):
            errs.append(f"{self.bone}: stiffness fora de [0,1]: {self.stiffness}")
        if self.kind == "cone" and (self.max_deg > 180.0 or self.min_deg < -180.0):
            errs.append(f"{self.bone}: cone fora de [-180,180]")
        if self.kind == "bend":
            if short not in BEND_JOINTS:
                errs.append(
                    f"{self.bone}: kind 'bend' so vale para uma dobradica "
                    f"conhecida (cotovelo/joelho); use cone/x/y/z")
            if not (-20.0 <= self.min_deg and self.max_deg <= 180.0):
                errs.append(f"{self.bone}: bend fora de [-20,180] (flexao real)")
        return errs


@dataclass
class ConstraintPreset:
    name: str = "humanoid"
    limits: list[JointLimit] = field(default_factory=list)
    default_stiffness: float = 1.0

    def limits_for(self, bone: str) -> list[JointLimit]:
        short = bone.replace("mixamorig:", "")
        return [l for l in self.limits
                if l.bone.replace("mixamorig:", "") in (bone, short)]

    def validate(self) -> list[str]:
        errs: list[str] = []
        for l in self.limits:
            errs += l.validate()
        conflitos: dict[tuple[str, str], list[int]] = {}
        for i, l in enumerate(self.limits):
            key = (l.bone.replace("mixamorig:", ""), l.kind)
            conflitos.setdefault(key, []).append(i)
        for (bone, kind), idxs in conflitos.items():
            if len(idxs) > 1:
                ranges = {(self.limits[i].min_deg, self.limits[i].max_deg) for i in idxs}
                if len(ranges) > 1:
                    errs.append(f"{bone}/{kind}: limites conflitantes definidos {len(idxs)}x")
        return errs

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "default_stiffness": self.default_stiffness,
            "limits": [
                {k: v for k, v in {
                    "bone": l.bone, "kind": l.kind, "min_deg": l.min_deg,
                    "max_deg": l.max_deg, "stiffness": l.stiffness, "note": l.note,
                    "note_en": l.note_en,
                }.items() if v not in ("", None)}
                for l in self.limits
            ],
        }

    @staticmethod
    def from_dict(d: dict) -> "ConstraintPreset":
        limits = []
        for item in (d.get("limits") or []):
            limits.append(JointLimit(
                bone=str(item["bone"]),
                kind=str(item.get("kind", "cone")),
                min_deg=float(item.get("min_deg", -180.0)),
                max_deg=float(item.get("max_deg", 180.0)),
                stiffness=float(item.get("stiffness", d.get("default_stiffness", 1.0))),
                note=str(item.get("note", "")),
                note_en=str(item.get("note_en", "")),
            ))
        return ConstraintPreset(name=str(d.get("name", "humanoid")), limits=limits,
                                default_stiffness=float(d.get("default_stiffness", 1.0)))

    @staticmethod
    def load(path: str | Path) -> "ConstraintPreset":
        p = Path(path)
        text = p.read_text(encoding="utf-8")
        if str(p).lower().endswith((".yaml", ".yml")):
            import yaml
            data = yaml.safe_load(text) or {}
        else:
            import json
            data = json.loads(text or "{}")
        preset = ConstraintPreset.from_dict(data)
        errs = preset.validate()
        if errs:
            raise ValueError("preset de constraints invalido:\n  - " + "\n  - ".join(errs))
        return preset

    def save(self, path: str | Path, header: str = "") -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        d = self.to_dict()
        try:
            import yaml
            body = yaml.safe_dump(d, allow_unicode=True, sort_keys=False)
        except Exception:  # pragma: no cover
            import json
            body = json.dumps(d, indent=2, ensure_ascii=False)
        p.write_text(header + body, encoding="utf-8")


def default_constraints_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "constraints_humanoid.yaml"


def load_default_preset(path: str | Path | None = None) -> ConstraintPreset:
    p = Path(path) if path else default_constraints_path()
    if not p.exists():
        preset = humanoid_preset()
        preset.save(p, header=_HUMANOID_HEADER)
        return preset
    return ConstraintPreset.load(p)


# ---------------------------------------------------------------------------
# Preset humanoide
# ---------------------------------------------------------------------------
def humanoid_preset(stiffness: float = 1.0) -> ConstraintPreset:
    """Limites articulares para o rig humanoide de 65 ossos.

    Os limites de EIXO sao assimetricos onde a articulacao e assimetrica de
    fato (cotovelo/joelho sem hiperextensao); onde a junta e ampla (ombro,
    quadril) usa-se **cone**, que limita o desvio total em vez de um eixo.
    """
    lim: list[JointLimit] = []

    def add(bone: str, kind: str, lo: float, hi: float, note: str = ""):
        lim.append(JointLimit(bone, kind, lo, hi, stiffness, note))

    # --- tronco -------------------------------------------------------
    for sp in ("Spine", "Spine1", "Spine2"):
        add(sp, "x", -30, 30, "flexao/extensao do tronco")
        add(sp, "y", -35, 35, "rotacao/rolagem limitada")
        add(sp, "z", -30, 30, "inclinacao lateral")
    # --- pescoco / cabeca ---------------------------------------------
    add("Neck", "cone", -180, 60, "pescoco: no maximo 60 graus de desvio")
    add("Neck", "y", -60, 60, "rotacao do pescoco")
    add("Head", "cone", -180, 50, "cabeca: nunca 180 — aqui 50 graus")
    add("Head", "y", -70, 70, "rotacao lateral da cabeca")
    add("Head", "x", -35, 35, "aceno")
    # --- ombros / bracos ----------------------------------------------
    for side in ("Left", "Right"):
        add(f"{side}Shoulder", "cone", -180, 40, "clavicula: movimento pequeno")
        add(f"{side}Arm", "cone", -180, 160, "ombro: cone amplo, mas nao 180")
        add(f"{side}ForeArm", "bend", -5, 160, "cotovelo: flexao real, sem hiperextensao")
        add(f"{side}Hand", "cone", -180, 80, "punho")
        # --- pernas ----------------------------------------------------
        add(f"{side}UpLeg", "cone", -180, 130, "quadril: cone amplo")
        add(f"{side}Leg", "bend", -3, 160, "joelho: flexao real, sem hiperextensao")
        add(f"{side}Foot", "cone", -180, 60, "tornozelo")
    return ConstraintPreset("humanoid", lim, stiffness)


_HUMANOID_HEADER = """# Constraints articulares — preset humanoide (VideoToAnim)
#
# kind:  cone  -> limita o angulo TOTAL da rotacao local (graus)
#        bend  -> limita a FLEXAO REAL da junta (cotovelo/joelho), medida pela
#                 geometria (FK): 0 = reto, positivo = dobrado, negativo =
#                 hiperextensao. Use bend (e nao x/y/z) nestas juntas: a
#                 rotacao local do antebraco/canela carrega tambem a rotacao
#                 do ombro/quadril, entao um clamp de eixo Euler mede a coisa
#                 errada e endsirea o braco.
#        x/y/z -> limita um eixo especifico, com min/max ASSIMETRICOS
# stiffness: 0..1 — 1 corrige integralmente, 0 ignora; valores intermediarios
#            aplicam correcao suave (slerp), sem corte seco.
#
# Edite livremente: na proxima execucao o preset e recarregado do arquivo.
# min_deg > max_deg, stiffness fora de [0,1] ou osso inexistente geram ERRO claro.
#
"""


# ---------------------------------------------------------------------------
# Aplicacao
# ---------------------------------------------------------------------------
def apply_constraints(anim, preset: ConstraintPreset, *, report_violations: bool = True):
    """Aplica o preset a uma `Animation` e devolve (Animation, relatorio)."""
    from core.retarget import Animation

    rotations = {k: np.asarray(v, np.float64).copy() for k, v in anim.rotations.items()}
    stats: dict[str, dict] = {}
    total_frames_fixed = 0

    # ---- 0) dobradicas (cotovelo/joelho): limite geometrico --------------
    # Roda ANTES e SEPARADO dos clamps de rotacao local porque o angulo da
    # junta so existe depois da FK. A correcao e uma rotacao no eixo da
    # propria junta, convertida para o espaco local do osso, de modo que
    # apenas o membro abaixo da articulacao gira (o tronco nao e arrastado).
    bend_rules = {b: [r for r in preset.limits_for(b) if r.kind == "bend"]
                  for b in ANIMATED_BONES}
    bend_rules = {b: rs for b, rs in bend_rules.items() if rs}
    if bend_rules:
        from core.mixamo import (BONE_OFFSET, BONE_PARENT, quat_conj, quat_mul,
                                 quat_normalize as _qn, quat_rotate)

        T = int(anim.num_frames)
        root = np.asarray(anim.root_translation, np.float64)
        ident = np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float64)
        # cadeia de ancestrais de cada osso articulado (ate a raiz), calculada
        # uma vez: dentro do laco de frames ela nao muda.
        chains: dict[str, list[str]] = {}
        for b in bend_rules:
            ch: list[str] = []
            cur = BONE_PARENT.get(b)
            while cur is not None:
                ch.append(cur)
                cur = BONE_PARENT.get(cur)
            chains[b] = ch
        # Fk parcial: as juntas so precisam dos ancestrais + do proprio osso e
        # do filho. A FK completa (65 ossos, incluindo 40 falanges) custava
        # 32 ms/frame; aqui sao ~14 ossos, o que corta a etapa de ~6.7 s para
        # ~0.2 s neste job sem mudar nenhum resultado.
        needed: set[str] = set()
        for b in bend_rules:
            needed.add(b)
            needed.update(chains[b])
            needed.update(BEND_JOINTS[b])
        fk_order = [n for n in BONE_NAMES if n in needed]
        acc: dict[str, dict] = {
            b: {"fixed": 0, "max_viol": 0.0, "sum_corr": 0.0, "n_corr": 0}
            for b in bend_rules if b in rotations
        }

        def fk_partial(loc: dict, root_t: np.ndarray) -> dict:
            pos: dict[str, np.ndarray] = {}
            rot: dict[str, np.ndarray] = {}
            for name in fk_order:
                parent = BONE_PARENT[name]
                if parent is None:
                    rot[name] = _qn(loc.get(name, ident))
                    pos[name] = np.asarray(root_t, dtype=np.float64)
                    continue
                if parent not in pos:            # pai fora do subconjunto
                    rot[name] = _qn(loc.get(name, ident))
                    pos[name] = np.asarray(root_t, dtype=np.float64)
                    continue
                pos[name] = pos[parent] + quat_rotate(rot[parent], BONE_OFFSET[name])
                rot[name] = _qn(quat_mul(rot[parent], loc.get(name, ident)))
            return pos

        # O laco de FRAMES e o externo: a FK e cara e so depende do frame, entao
        # e feita uma vez por frame e reutilizada por todas as juntas. (Fazendo
        # o oposto — FK por osso — custava 27 s neste job.)
        for t in range(T):
            loc = {k: rotations[k][t] for k in ANIMATED_BONES if k in rotations}
            pos = fk_partial(loc, root[t])
            for b, rs in bend_rules.items():
                if b not in rotations:
                    continue
                m = joint_flexion_deg(pos, b)
                if m is None:
                    continue
                flex, axis = m
                rule = rs[0]
                target = float(np.clip(flex, rule.min_deg, rule.max_deg))
                if abs(target - flex) <= 1e-9:
                    continue                      # dentro do limite: nao toca
                # `joint_flexion_deg` devolve o eixo d1 x d2. Rodar o membro
                # DESTE eixo por +delta AUMENTA o angulo interno (o membro
                # estende), logo para levar a flexao de `flex` ate `target` o
                # passo e (flex - target), com o sinal invertido.
                delta = np.radians(flex - target) * float(rule.stiffness)
                # A correcao gira a SUBARVORE do osso em torno do EIXO DA
                # JUNTA (vetor de mundo). Como a rotacao local do osso e
                # relativa ao pai, a rotacao de mundo e transportada para o
                # espaco do pai:  local' = conj(P) * R_eixo * P * local.
                # A cadeia comeca no PAI de proposito: incluir o proprio osso
                # aqui introduziria a rotacao local dele no eixo e a correcao
                # sairia curta (medido: 20 graus de falta).
                p_world = np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float64)
                for name in reversed(chains[b]):
                    p_world = quat_mul(p_world, loc.get(name, ident))
                axis_local = quat_rotate(quat_conj(p_world), axis)
                q_fix = _axis_angle_quat(axis_local, delta)
                rotations[b][t] = quat_normalize(quat_mul(q_fix, rotations[b][t]))
                st = acc[b]
                viol = abs(target - flex)
                st["max_viol"] = max(st["max_viol"], viol)
                st["sum_corr"] += viol * float(rule.stiffness)
                st["n_corr"] += 1
                st["fixed"] += 1
        for b, st in acc.items():
            if st["fixed"]:
                total_frames_fixed += st["fixed"]
                stats[b] = {
                    "frames_corrected": st["fixed"],
                    "max_violation_deg": round(st["max_viol"], 3),
                    "mean_correction_deg": round(
                        st["sum_corr"] / max(1, st["n_corr"]), 3),
                    "kind": "bend",
                }

    for bone in ANIMATED_BONES:
        # as regras `bend` ja foram aplicadas acima (precisam da FK)
        rules = [r for r in preset.limits_for(bone) if r.kind != "bend"]
        if not rules or bone not in rotations:
            continue
        series = rotations[bone]
        fixed_frames = 0
        max_viol = 0.0
        sum_corr = 0.0
        n_corr = 0
        for t in range(series.shape[0]):
            q = series[t]
            q_out = q
            for rule in rules:
                if rule.kind == "cone":
                    ang = quat_angle_deg(q_out)
                    hi = rule.max_deg
                    if ang > hi + 1e-9:
                        viol = ang - hi
                        max_viol = max(max_viol, viol)
                        # rotacao alvo: mesma direcao de eixo, angulo limitado
                        q_norm = quat_normalize(q_out)
                        w = np.clip(quat_normalize(q_out)[3], -1.0, 1.0)
                        axis = q_norm[:3]
                        n = np.linalg.norm(axis)
                        if n > 1e-9:
                            axis = axis / n
                            q_lim = np.array([*(axis * np.sin(np.radians(hi) / 2)),
                                              np.cos(np.radians(hi) / 2)])
                        else:
                            q_lim = np.array([0, 0, 0, 1.0])
                        q_out = slerp(q_out, q_lim, rule.stiffness)
                        sum_corr += viol * rule.stiffness
                        n_corr += 1
                else:
                    e = quat_to_euler_xyz_deg(q_out)
                    a = AXES[rule.kind]
                    clamped = float(np.clip(e[a], rule.min_deg, rule.max_deg))
                    if abs(clamped - e[a]) > 1e-9:
                        viol = abs(clamped - e[a])
                        max_viol = max(max_viol, viol)
                        e2 = e.copy()
                        e2[a] = clamped
                        q_clamped = euler_xyz_deg_to_quat(e2)
                        q_out = slerp(q_out, q_clamped, rule.stiffness)
                        sum_corr += viol * rule.stiffness
                        n_corr += 1
            if not np.allclose(q_out, q, atol=1e-9):
                fixed_frames += 1
                # so escreve quando houve correcao: frames sem violacao ficam
                # bit-exatamente iguais ao original (criterio de aceite)
                series[t] = quat_normalize(q_out)
        if fixed_frames:
            total_frames_fixed += fixed_frames
            stats[bone] = {
                "frames_corrected": fixed_frames,
                "max_violation_deg": round(max_viol, 3),
                "mean_correction_deg": round(sum_corr / max(1, n_corr), 3),
            }
        rotations[bone] = series

    out = Animation(
        fps=anim.fps, num_frames=anim.num_frames, bone_names=list(anim.bone_names),
        rotations=rotations, root_translation=np.asarray(anim.root_translation).copy(),
        meta={**getattr(anim, "meta", {}), "constraints": preset.name},
    )
    report = {
        "preset": preset.name,
        "bones_corrected": len(stats),
        "frames_corrected_total": total_frames_fixed,
        "per_bone": dict(sorted(stats.items(), key=lambda kv: -kv[1]["frames_corrected"])),
    }
    return out, report
