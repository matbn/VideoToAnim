"""Serializacao de `Animation` para JSON e exportacao para GLB/FBX.

Serve para: (a) guardar clipes antes/depois dos exemplos, (b) trocar dados com a
API do editor, (c) rodar a CLI sem depender de um GLB de entrada.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from core.retarget import Animation


def animation_to_dict(anim: Animation) -> dict:
    return {
        "fps": float(anim.fps),
        "num_frames": int(anim.num_frames),
        "bone_names": list(anim.bone_names),
        "rotations": {k: np.asarray(v, float).round(8).tolist() for k, v in anim.rotations.items()},
        "root_translation": np.asarray(anim.root_translation, float).round(8).tolist(),
        "meta": dict(getattr(anim, "meta", {}) or {}),
    }


def animation_from_dict(d: dict) -> Animation:
    return Animation(
        fps=float(d.get("fps", 30.0)),
        num_frames=int(d["num_frames"]),
        bone_names=list(d.get("bone_names") or list(d["rotations"].keys())),
        rotations={k: np.asarray(v, np.float64) for k, v in d["rotations"].items()},
        root_translation=np.asarray(d.get("root_translation", []), np.float64),
        meta=dict(d.get("meta") or {}),
    )


def save_animation(anim: Animation, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(animation_to_dict(anim), ensure_ascii=False), encoding="utf-8")
    return p


def load_animation(path: str | Path) -> Animation:
    return animation_from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def export_animation_glb(anim: Animation, path: str | Path,
                         skin_mesh: dict | None = None,
                         rig: dict | None = None) -> dict:
    from core.export_glb import build_glb
    return build_glb(anim, path, skin_mesh=skin_mesh, rig=rig)


def export_animation_fbx(anim: Animation, path: str | Path) -> dict:
    from core.export_fbx import build_fbx
    return build_fbx(anim, path)
