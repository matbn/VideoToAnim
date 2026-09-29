"""CLI da camada de refinamento.

Exemplos:
  # gera clipes de exemplo antes/depois + relatorio
  python tools/refine.py --make-samples

  # compara filtros sobre um clipe salvo
  python tools/refine.py --input storage/refine_samples/sample_before.json \\
      --compare one_euro,savgol,kalman,butterworth,moving_average,double_exponential

  # aplica constraints + filtros e salva o resultado
  python tools/refine.py --input clipe.json --constraints config/constraints_humanoid.yaml \\
      --filters config/filters_default.yaml --out storage/refine_samples/out

  # injeta uma violacao absurda (cabeca a 180 graus) para ver o preset corrigir
  python tools/refine.py --input clipe.json --inject-violation head180 --report out/
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.refine import (  # noqa: E402
    boneedit_mod, constraints_mod, filter_plan, io as refine_io, refine_animation, report_mod,
)
from core.refine.filters import FILTERS  # noqa: E402

SAMPLES = ROOT / "storage" / "refine_samples"


def _make_sample_animation(frames: int = 90, fps: float = 30.0):
    """Clipe sintetico com movimento suave + ruido, gerado dos 65 ossos reais."""
    from core import mixamo as mx
    from core.retarget import Animation

    rng = np.random.default_rng(7)
    T = frames
    rotations: dict[str, np.ndarray] = {}
    for bone in mx.ANIMATED_BONES:
        base = np.array([0.0, 0.0, 0.0, 1.0])
        series = np.tile(base, (T, 1)).astype(np.float64)
        if bone in ("LeftArm", "RightArm", "LeftForeArm", "RightForeArm",
                    "LeftUpLeg", "RightUpLeg", "LeftLeg", "RightLeg", "Head", "Spine2"):
            for t in range(T):
                a = 0.35 * np.sin(2 * np.pi * t / max(T - 1, 1) * 2.0)
                axis = {"LeftArm": (0, 0, -1), "RightArm": (0, 0, 1),
                        "LeftForeArm": (0, 0, -1), "RightForeArm": (0, 0, 1),
                        "LeftUpLeg": (1, 0, 0), "RightUpLeg": (-1, 0, 0),
                        "LeftLeg": (1, 0, 0), "RightLeg": (-1, 0, 0),
                        "Head": (0, 1, 0), "Spine2": (0, 0, 1)}[bone]
                ax = np.asarray(axis, float)
                ax = ax / np.linalg.norm(ax)
                q = np.array([*(ax * np.sin(a / 2)), np.cos(a / 2)])
                series[t] = mx.quat_normalize(q)
        # ruido de alta frequencia (o que os filtros devem atacar)
        noise = rng.normal(0.0, 0.02, size=series.shape)
        noisy = mx.quat_normalize(series + noise)
        rotations[bone] = noisy
    root = np.tile(np.array([0.0, 0.93, 0.0]), (T, 1))
    root[:, 0] += 0.02 * np.sin(np.linspace(0, 6, T))
    return Animation(fps, T, list(mx.BONE_NAMES), rotations, root, {"source": "synthetic_sample"})


def _inject_head_180(anim):
    """Injeta 180 graus na cabeca — violacao que o preset deve corrigir."""
    from core.mixamo import quat_normalize

    out = {k: v.copy() for k, v in anim.rotations.items()}
    T = anim.num_frames
    for t in range(T // 3, 2 * T // 3):
        out["Head"][t] = quat_normalize(np.array([0.0, 1.0, 0.0, 0.0]))  # 180 graus em Y
    from core.retarget import Animation
    return Animation(anim.fps, T, list(anim.bone_names), out, anim.root_translation.copy(),
                     {**getattr(anim, "meta", {}), "injected": "head180"})


def main() -> int:
    ap = argparse.ArgumentParser(description="camada de refinamento de animacoes")
    ap.add_argument("--input", help="clipe JSON (ver core/refine/io.py)")
    ap.add_argument("--out", help="diretorio de saida para o clipe refinado")
    ap.add_argument("--constraints", default=str(ROOT / "config" / "constraints_humanoid.yaml"))
    ap.add_argument("--filters", default=str(ROOT / "config" / "filters_default.yaml"))
    ap.add_argument("--compare", help="lista de filtros separada por virgula (so compara)")
    ap.add_argument("--report", help="diretorio para salvar o relatorio comparativo")
    ap.add_argument("--make-samples", action="store_true", help="gera clipes de exemplo")
    ap.add_argument("--inject-violation", choices=["head180"], help="injeta violacao proposital")
    ap.add_argument("--no-constraints", action="store_true")
    ap.add_argument("--no-filters", action="store_true")
    args = ap.parse_args()

    SAMPLES.mkdir(parents=True, exist_ok=True)

    if args.make_samples:
        anim = _make_sample_animation()
        before = refine_io.save_animation(anim, SAMPLES / "sample_before.json")
        glb_b = refine_io.export_animation_glb(anim, SAMPLES / "sample_before.glb")
        refined, rep = refine_animation(anim, constraints_config=args.constraints,
                                        filters_config=args.filters)
        after = refine_io.save_animation(refined, SAMPLES / "sample_after.json")
        glb_a = refine_io.export_animation_glb(refined, SAMPLES / "sample_after.glb")
        comp = report_mod.compare_filters(anim, list(FILTERS.keys()))
        report_mod.save_report(SAMPLES, comparison=comp, constraints=rep.constraints,
                               extra={"filters": rep.filters})
        print(f"antes : {before.name} ({before.stat().st_size} B) | {Path(glb_b['path']).name} ({glb_b['bytes']} B)")
        print(f"depois: {after.name} ({after.stat().st_size} B) | {Path(glb_a['path']).name} ({glb_a['bytes']} B)")
        print(report_mod.to_markdown(comp, "Comparativo de estabilizacao"))
        return 0

    src = Path(args.input) if args.input else SAMPLES / "sample_before.json"
    if not src.exists():
        print(f"clipe nao encontrado: {src} — rode --make-samples primeiro")
        return 2
    anim = refine_io.load_animation(src)
    if args.inject_violation == "head180":
        anim = _inject_head_180(anim)
        print("violacao injetada: cabeca a 180 graus em Y (1/3 do clipe)")

    if args.compare:
        names = [n.strip() for n in args.compare.split(",") if n.strip()]
        comp = report_mod.compare_filters(anim, names)
        print(report_mod.to_markdown(comp, "Comparativo de estabilizacao"))
        if args.report:
            report_mod.save_report(args.report, comparison=comp)
        return 0

    refined, rep = refine_animation(
        anim,
        constraints_config=None if args.no_constraints else args.constraints,
        filters_config=None if args.no_filters else args.filters,
    )
    out_dir = Path(args.out) if args.out else SAMPLES / "refined"
    out_dir.mkdir(parents=True, exist_ok=True)
    p = refine_io.save_animation(refined, out_dir / (src.stem + "_refined.json"))
    glb = refine_io.export_animation_glb(refined, out_dir / (src.stem + "_refined.glb"))
    report_mod.save_report(args.report or out_dir, constraints=rep.constraints,
                           extra={"filters": rep.filters, "stages": rep.stages})
    print("estagios:", rep.stages)
    if rep.constraints:
        c = rep.constraints
        print(f"constraints: {c['bones_corrected']} ossos, {c['frames_corrected_total']} frames corrigidos")
        for bone, s in list(c["per_bone"].items())[:5]:
            print(f"   {bone:24} frames={s['frames_corrected']:3d} viol_max={s['max_violation_deg']:.1f}°")
    if rep.filters:
        print(f"filtros aplicados: {rep.filters['applied'] and len(rep.filters['applied']) or 0} regras "
              f"({', '.join(rep.filters['filters'])})")
    print(f"saida: {p} | GLB {glb['bytes']} B")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
