"""Camada de refinamento de animacoes (video2mixamo).

Recebe uma `Animation` ja bakeada e devolve outra, refinada, sem tocar no
original. Ordem de aplicacao (e o motivo):

    1. constraints  — impoe limites articulares ANTES de suavizar;
    2. filters      — estabiliza o movimento (6 filtros selecionaveis);
    3. edits        — keyframes manuais por ultimo, para a intencao do usuario vencer.

Uso rapido:
    from core.refine import refine_animation
    anim2, report = refine_animation(anim, filters_config="config/filters_default.yaml")
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from core.refine import filters, plan as filter_plan
from core.refine import boneedit as boneedit_mod
from core.refine import constraints as constraints_mod
from core.refine import collision as collision_mod
from core.refine import report as report_mod

__all__ = [
    "refine_animation", "RefineReport", "filters", "filter_plan",
    "constraints_mod", "collision_mod", "boneedit_mod", "report_mod",
]


@dataclass
class RefineReport:
    constraints: dict | None = None
    collision: dict | None = None
    filters: dict | None = None
    edits: list[dict] = field(default_factory=list)
    stages: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "stages": self.stages,
            "constraints": self.constraints,
            "collision": self.collision,
            "filters": self.filters,
            "edits": self.edits,
        }


def refine_animation(
    anim,
    *,
    constraints_config: str | Path | dict | None = None,
    collision_config: str | Path | dict | None = None,
    collision_mesh_lengths: dict | None = None,
    collision_skeleton_offsets: dict | None = None,
    filters_config: str | Path | dict | None = None,
    edits: list | None = None,
    side_history: dict[str, float] | None = None,
    history=None,
    apply_constraints_stage: bool = True,
    apply_collision_stage: bool = True,
    apply_filter_stage: bool = True,
    apply_edit_stage: bool = True,
):
    """Aplica as tres etapas do refinamento. Devolve (Animation, RefineReport)."""
    rep = RefineReport()
    cur = anim

    # 1. constraints -----------------------------------------------------
    if apply_constraints_stage and constraints_config is not None:
        if isinstance(constraints_config, constraints_mod.ConstraintPreset):
            preset = constraints_config
        elif isinstance(constraints_config, dict):
            preset = constraints_mod.ConstraintPreset.from_dict(constraints_config)
        else:
            preset = constraints_mod.ConstraintPreset.load(constraints_config)
        errs = preset.validate()
        if errs:
            raise ValueError("preset de constraints invalido:\n  - " + "\n  - ".join(errs))
        cur, c_report = constraints_mod.apply_constraints(cur, preset)
        rep.constraints = c_report
        rep.stages.append("constraints")

    # 1b. anticolisao -----------------------------------------------------
    if apply_collision_stage and collision_config is not None:
        if isinstance(collision_config, collision_mod.CollisionConfig):
            ccol = collision_config
        elif isinstance(collision_config, dict):
            ccol = collision_mod.CollisionConfig.from_dict(collision_config)
        else:
            ccol = collision_mod.load_default_config(collision_config)
        errs = ccol.validate()
        if errs:
            raise ValueError("config de anticolisao invalida:\n  - " + "\n  - ".join(errs))
        cur, col_report = collision_mod.apply_collision(
            cur, config=ccol, mesh_lengths=collision_mesh_lengths,
            skeleton_offsets=collision_skeleton_offsets,
            side_history=side_history)
        rep.collision = col_report
        rep.stages.append("collision")

    # 2. filtros ---------------------------------------------------------
    if apply_filter_stage and filters_config is not None:
        if isinstance(filters_config, filter_plan.FilterPlan):
            plan = filters_config
        elif isinstance(filters_config, dict):
            plan = filter_plan.FilterPlan.from_dict(filters_config)
        else:
            plan = filter_plan.FilterPlan.load(filters_config)
        errs = plan.validate()
        if errs:
            raise ValueError("plano de filtros invalido:\n  - " + "\n  - ".join(errs))
        cur, f_report = filter_plan.apply_filter_plan(cur, plan)
        rep.filters = f_report
        rep.stages.append("filters")

    # 3. edicoes ---------------------------------------------------------
    if apply_edit_stage:
        pend = list(edits or []) + list(getattr(history, "edits", []) or [])
        if pend:
            cur, reports = boneedit_mod.rebake_from_edits(cur, [
                e if isinstance(e, boneedit_mod.BoneEdit) else boneedit_mod.BoneEdit.from_dict(e)
                for e in pend
            ])
            rep.edits = reports
            rep.stages.append("edits")

    return cur, rep
