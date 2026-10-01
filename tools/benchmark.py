"""Benchmark: roda o pipeline em videos com um ou mais backends e tabula os resultados.

Uso:
    python tools/benchmark.py --out storage/benchmark \
        --backends mediapipe synthetic \
        --videos caminho/A.mp4 caminho/B.mp4
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.pipeline import run_pipeline  # noqa: E402
from core.registry import build_registry  # noqa: E402
from core.video import read_video  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--videos", nargs="+", required=True)
    ap.add_argument("--backends", nargs="+", required=True)
    ap.add_argument("--out", default=str(ROOT / "storage" / "benchmark"))
    ap.add_argument("--max-frames", type=int, default=None)
    ap.add_argument("--fps", type=float, default=30.0)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    registry = build_registry(ROOT / "plugins")

    rows = []
    for video in args.videos:
        video = str(video)
        info = read_video(video, max_frames=args.max_frames)
        vname = Path(video).stem
        for backend in args.backends:
            rec = registry.get(backend)
            if rec is None:
                rows.append({"video": vname, "backend": backend, "status": "nao registrado"})
                continue
            if not rec.available:
                rows.append({"video": vname, "backend": backend, "status": f"indisponivel: {rec.reason}"})
                continue
            job_dir = out / f"{vname}__{backend}"
            t0 = time.time()
            try:
                res = run_pipeline(
                    job_id=f"{vname}__{backend}",
                    video_path=video,
                    backend_name=backend,
                    params={"fps": args.fps, "smoothing": "oneeuro", "max_frames": args.max_frames},
                    registry=registry,
                    job_dir=job_dir,
                )
                m = res["metrics"]
                rows.append({
                    "video": vname, "backend": backend, "status": "ok",
                    "frames": m["frames"], "wall_s": round(time.time() - t0, 2),
                    "mean_score": round(m["mean_score"], 3),
                    "fk_max_pos_cm": round(m["fk_max_position_m"] * 100, 2),
                    "fk_max_ang_deg": round(m["fk_max_angle_deg"], 3),
                    "glb_bytes": res["glb"]["bytes"], "fbx_bytes": res["fbx"]["bytes"],
                    "glb": str(Path(res["glb"]["path"]).relative_to(out)),
                })
            except Exception as exc:  # noqa: BLE001
                rows.append({"video": vname, "backend": backend, "status": f"erro: {exc}"})
            print(f"[{vname} / {backend}] {rows[-1]['status']}", flush=True)

    (out / "results.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = ["# Benchmark — VideoToAnim", "",
             f"Videos: {', '.join(Path(v).name for v in args.videos)}",
             f"Backends: {', '.join(args.backends)}", "",
             "| video | backend | status | frames | wall (s) | score medio | FK pos (cm) | FK ang (deg) | GLB (B) | FBX (B) |",
             "|---|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        if r.get("status") == "ok":
            lines.append("| {video} | {backend} | ok | {frames} | {wall_s} | {mean_score} | {fk_max_pos_cm} | "
                         "{fk_max_ang_deg} | {glb_bytes} | {fbx_bytes} |".format(**r))
        else:
            lines.append(f"| {r['video']} | {r['backend']} | {r['status']} | - | - | - | - | - | - | - |")
    (out / "results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
