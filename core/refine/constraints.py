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
VALID_KINDS = ("cone", "x", "y", "z")


# ---------------------------------------------------------------------------
# Quaternion <-> Euler (XYZ, graus)  e  slerp
# ---------------------------------------------------------------------------
def quat_to_euler_xyz_deg(q: np.ndarray) -> np.ndarray:
    x, y, z, w = quat_normalize(np.asarray(q, np.float64))
    m00 = 1 - 2 * (y * y + z * z)
    m01 = 2 * (x * y - w * z)
    m02 = 2 * (x * z + w * y)
    m12 = 2 * (y * z - w * x)
    m22 = 1 - 2 * (x * x + y * y)
    sy = float(np.clip(-m02, -1.0, 1.0))
    ry = float(np.arcsin(sy))
    if abs(sy) < 0.999999:
        rx = float(np.arctan2(m12, m22))
        rz = float(np.arctan2(m01, m00))
    else:  # gimbal lock
        rx = 0.0
        rz = float(np.arctan2(-m01, m00)) if sy > 0 else float(np.arctan2(m01, m00))
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
    kind: str = "cone"          # cone | x | y | z
    min_deg: float = -180.0
    max_deg: float = 180.0
    stiffness: float = 1.0
    note: str = ""
    note_en: str = ""

    def validate(self) -> list[str]:
        errs: list[str] = []
        if self.bone.replace("mixamorig:", "") not in BONE_NAMES:
            errs.append(f"osso inexistente no rig: {self.bone!r}")
        if self.kind not in VALID_KINDS:
            errs.append(f"kind invalido: {self.kind!r} (use cone/x/y/z)")
        if self.min_deg > self.max_deg:
            errs.append(f"{self.bone}: min_deg ({self.min_deg}) > max_deg ({self.max_deg})")
        if not (0.0 <= self.stiffness <= 1.0):
            errs.append(f"{self.bone}: stiffness fora de [0,1]: {self.stiffness}")
        if self.kind == "cone" and (self.max_deg > 180.0 or self.min_deg < -180.0):
            errs.append(f"{self.bone}: cone fora de [-180,180]")
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
        add(f"{side}ForeArm", "cone", -180, 165, "cotovelo: cone")
        add(f"{side}ForeArm", "z", -5, 160, "cotovelo NAO hiperextende (assimetrico)")
        add(f"{side}Hand", "cone", -180, 80, "punho")
        # --- pernas ----------------------------------------------------
        add(f"{side}UpLeg", "cone", -180, 130, "quadril: cone amplo")
        add(f"{side}Leg", "cone", -180, 160, "joelho: cone")
        add(f"{side}Leg", "z", -155, 5, "joelho NAO hiperextende (assimetrico)")
        add(f"{side}Foot", "cone", -180, 60, "tornozelo")
    return ConstraintPreset("humanoid", lim, stiffness)


_HUMANOID_HEADER = """# Constraints articulares — preset humanoide (video2mixamo)
#
# kind:  cone  -> limita o angulo TOTAL da rotacao local (graus)
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

    for bone in ANIMATED_BONES:
        rules = preset.limits_for(bone)
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
