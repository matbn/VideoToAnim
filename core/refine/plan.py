"""Plano de filtragem: QUAIS filtros, em QUAIS ossos, em QUAIS frames.

Configuravel por arquivo (YAML/JSON), com overrides por osso e por intervalo de
frames. Regras sao avaliadas em ordem; a ultima que casar com o osso vence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from core.mixamo import ANIMATED_BONES, BONE_NAMES
from core.refine.filters import FILTERS, filter_series, smooth_quaternion_series

VALID_AXES = {"x": 0, "y": 1, "z": 2, "w": 3}


@dataclass
class FilterRule:
    name: str = "one_euro"
    params: dict = field(default_factory=dict)
    bones: list[str] | None = None      # None = todos os ossos animados
    start: int | None = None            # frame inicial (inclusivo)
    end: int | None = None              # frame final (inclusivo)
    axis: str | None = None             # "x"|"y"|"z"|"w" -> so esse componente

    def matches_bone(self, bone: str) -> bool:
        if self.bones is None:
            return True
        if bone in self.bones:
            return True
        short = bone.replace("mixamorig:", "")
        return short in self.bones

    def validate(self) -> list[str]:
        errs: list[str] = []
        if self.name not in FILTERS:
            errs.append(f"filtro desconhecido: {self.name!r} (disponiveis: {sorted(FILTERS)})")
        if self.axis is not None and self.axis not in VALID_AXES:
            errs.append(f"axis invalido: {self.axis!r} (use x/y/z/w)")
        if self.start is not None and self.start < 0:
            errs.append("start nao pode ser negativo")
        if self.start is not None and self.end is not None and self.end < self.start:
            errs.append(f"intervalo invalido: start={self.start} > end={self.end}")
        if self.bones is not None:
            desconhecidos = [b for b in self.bones
                             if b.replace("mixamorig:", "") not in BONE_NAMES]
            if desconhecidos:
                errs.append(f"ossos inexistentes no rig: {desconhecidos[:5]}")
        return errs


@dataclass
class FilterPlan:
    rules: list[FilterRule] = field(default_factory=list)
    apply_to_translation: bool = True

    def rules_for(self, bone: str) -> list[FilterRule]:
        return [r for r in self.rules if r.matches_bone(bone)]

    def validate(self) -> list[str]:
        errs: list[str] = []
        for i, r in enumerate(self.rules):
            errs += [f"regra {i}: {e}" for e in r.validate()]
        return errs

    def to_dict(self) -> dict:
        return {
            "apply_to_translation": self.apply_to_translation,
            "filters": [
                {k: v for k, v in {
                    "name": r.name, "params": r.params, "bones": r.bones,
                    "start": r.start, "end": r.end, "axis": r.axis,
                }.items() if v is not None}
                for r in self.rules
            ],
        }

    @staticmethod
    def from_dict(d: dict) -> "FilterPlan":
        rules = []
        for item in (d.get("filters") or d.get("rules") or []):
            rules.append(FilterRule(
                name=item.get("name", "one_euro"),
                params=dict(item.get("params") or {}),
                bones=list(item["bones"]) if item.get("bones") else None,
                start=item.get("start"),
                end=item.get("end"),
                axis=item.get("axis"),
            ))
        return FilterPlan(rules=rules,
                          apply_to_translation=bool(d.get("apply_to_translation", True)))

    @staticmethod
    def load(path: str | Path) -> "FilterPlan":
        text = Path(path).read_text(encoding="utf-8")
        if str(path).lower().endswith((".yaml", ".yml")):
            import yaml
            data = yaml.safe_load(text) or {}
        else:
            import json
            data = json.loads(text or "{}")
        plan = FilterPlan.from_dict(data)
        errs = plan.validate()
        if errs:
            raise ValueError("plano de filtros invalido:\n  - " + "\n  - ".join(errs))
        return plan

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        text = ("# plano de filtragem (VideoToAnim)\n"
                + _yaml_dump(self.to_dict()))
        p.write_text(text, encoding="utf-8")


def _yaml_dump(d: dict) -> str:
    try:
        import yaml
        return yaml.safe_dump(d, allow_unicode=True, sort_keys=False)
    except Exception:  # pragma: no cover
        import json
        return json.dumps(d, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Aplicacao
# ---------------------------------------------------------------------------
def _apply_rule(series: np.ndarray, rule: FilterRule, fps: float,
                is_quat: bool) -> np.ndarray:
    """Aplica a regra a uma serie (T, C) e devolve a serie modificada.

    O intervalo [start, end] e respeitado: fora dele os valores ficam intactos.
    """
    t = series.shape[0]
    start = 0 if rule.start is None else max(0, int(rule.start))
    end = (t - 1) if rule.end is None else min(t - 1, int(rule.end))
    if end <= start:
        return series
    out = series.copy()
    chunk = out[start:end + 1]

    if is_quat and rule.axis is None:
        filt = smooth_quaternion_series(chunk, rule.name, fps, **rule.params)
    elif rule.axis is not None:
        a = VALID_AXES[rule.axis]
        if chunk.shape[1] <= a:
            return out
        col = filter_series(chunk[:, a], rule.name, fps, **rule.params)
        filt = chunk.copy()
        filt[:, a] = col
        if is_quat:
            n = np.linalg.norm(filt, axis=1, keepdims=True)
            filt = filt / np.where(n < 1e-12, 1.0, n)
    else:
        filt = filter_series(chunk, rule.name, fps, **rule.params)

    out[start:end + 1] = filt
    return out


def apply_filter_plan(anim, plan: FilterPlan) -> tuple[object, dict]:
    """Aplica o plano a uma `Animation`. Devolve (nova Animation, relatorio)."""
    from core.retarget import Animation

    fps = float(getattr(anim, "fps", 30.0) or 30.0)
    rotations = {k: np.asarray(v, np.float64).copy() for k, v in anim.rotations.items()}
    root = np.asarray(anim.root_translation, np.float64).copy()
    applied: list[dict] = []

    for bone, series in rotations.items():
        rules = plan.rules_for(bone)
        if not rules:
            continue
        for rule in rules:
            if rule.start is None and rule.end is None:
                pass
            series = _apply_rule(series, rule, fps, is_quat=True)
            applied.append({
                "bone": bone, "filter": rule.name, "params": rule.params,
                "start": rule.start, "end": rule.end, "axis": rule.axis,
            })
        rotations[bone] = series

    if plan.apply_to_translation and root.shape[0] > 2:
        for rule in plan.rules:
            if not rule.bones:          # regras por osso nao se aplicam ao root
                continue
        # o root usa apenas regras globais (sem lista de ossos)
        for rule in [r for r in plan.rules if r.bones is None]:
            root = _apply_rule(root, rule, fps, is_quat=False)
            applied.append({"bone": "root_translation", "filter": rule.name,
                            "params": rule.params, "start": rule.start,
                            "end": rule.end, "axis": rule.axis})

    out = Animation(
        fps=anim.fps, num_frames=anim.num_frames, bone_names=list(anim.bone_names),
        rotations=rotations, root_translation=root,
        meta={**getattr(anim, "meta", {}), "filters_applied": len(applied)},
    )
    return out, {"applied": applied, "filters": sorted({a["filter"] for a in applied})}
