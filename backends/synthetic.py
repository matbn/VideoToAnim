"""Backend sintetico deterministico.

Gera uma sequencia de poses a partir do proprio esqueleto Mixamo (FK com
rotacoes sinusoidais), deriva o esqueleto canonico COCO-17 (2D e 3D) e projeta
em 2D. Nao depende de modelos externos: serve para testes ponta a ponta e como
referencia do contrato do Adapter.
"""
from __future__ import annotations

from typing import Sequence

import numpy as np

from core import mixamo as mx
from core.adapter import AdapterConfig, ConfigurableAdapter
from core.canonical import COCO17, FramePose


def _local_rotations(t: int, fps: float) -> dict[str, np.ndarray]:
    """Movimento roteirizado suave (deterministico a partir do indice do frame)."""
    phase = 2.0 * np.pi * (t / max(fps, 1.0))

    def qz(a: float) -> np.ndarray:
        return np.array([0.0, 0.0, np.sin(a / 2), np.cos(a / 2)])

    def qx(a: float) -> np.ndarray:
        return np.array([np.sin(a / 2), 0.0, 0.0, np.cos(a / 2)])

    rots = {name: mx.quat_identity() for name in mx.BONE_NAMES}
    rots["LeftArm"] = mx.quat_mul(qz(-1.2 + 0.35 * np.sin(phase)), qx(0.15 * np.sin(phase)))
    rots["RightArm"] = mx.quat_mul(qz(1.2 - 0.35 * np.sin(phase)), qx(-0.15 * np.sin(phase)))
    rots["LeftForeArm"] = qz(-0.5 + 0.4 * np.sin(phase + 1.0))
    rots["RightForeArm"] = qz(0.5 - 0.4 * np.sin(phase + 1.0))
    rots["LeftUpLeg"] = qx(0.35 * np.sin(phase))
    rots["RightUpLeg"] = qx(-0.35 * np.sin(phase))
    rots["LeftLeg"] = qx(max(0.0, 0.7 * np.sin(phase + 0.5)))
    rots["RightLeg"] = qx(max(0.0, -0.7 * np.sin(phase + 0.5)))
    rots["Spine"] = qx(0.05 * np.sin(phase))
    rots["Spine1"] = qz(0.05 * np.sin(phase * 0.5))
    rots["Neck"] = qz(0.08 * np.sin(phase * 0.7))
    return rots


def _derive_coco_from_skeleton(world_pos: dict[str, np.ndarray]) -> np.ndarray:
    ci = {n: i for i, n in enumerate(COCO17)}
    kp = np.zeros((17, 3), np.float64)
    for bone, coco in mx.BONE_TO_COCO.items():
        kp[ci[coco]] = world_pos[bone]
    head = world_pos["Head"]
    top = world_pos["HeadTop_End"]
    d = top - head
    n = np.linalg.norm(d)
    d = d / n if n > 1e-9 else np.array([0.0, 1.0, 0.0])
    nose = head + d * 0.08
    kp[ci["nose"]] = nose
    # eixos locais da cabeca
    right = np.array([1.0, 0.0, 0.0])
    up = d
    fwd = np.cross(right, up)
    nf = np.linalg.norm(fwd)
    fwd = fwd / nf if nf > 1e-9 else np.array([0.0, 0.0, 1.0])
    kp[ci["left_eye"]] = nose + right * 0.03 + up * 0.02
    kp[ci["right_eye"]] = nose - right * 0.03 + up * 0.02
    kp[ci["left_ear"]] = nose + right * 0.06
    kp[ci["right_ear"]] = nose - right * 0.06
    return kp


class SyntheticBackend(ConfigurableAdapter):
    # backend de teste/infra: fica no registry (os testes usam), mas FORA do dropdown
    hidden_from_ui = True

    def __init__(self) -> None:
        cfg = AdapterConfig(
            name="synthetic",
            display_name="Synthetic (deterministico, para testes)",
            native_layout=list(COCO17),
            mapping={n: i for i, n in enumerate(COCO17)},
            coord="pixel",
            license="MIT (parte deste projeto)",
            notes="Gera pose a partir do esqueleto Mixamo; sem dependencias.",
        )
        super().__init__(cfg)
        self.name = "synthetic"
        self.display_name = cfg.display_name

    def infer_raw(self, frames: Sequence[np.ndarray], ctx: dict) -> list[dict]:
        w = int(ctx.get("width", 640))
        h = int(ctx.get("height", 480))
        fps = float(ctx.get("fps", 30.0))
        k = h / 2.0
        out = []
        for t in range(len(frames)):
            rots = _local_rotations(t, fps)
            world = mx.fk_world(rots, np.array([0.0, 0.98, 0.0]))
            kp3d = _derive_coco_from_skeleton(world)
            kp2d = np.zeros((17, 2), np.float64)
            kp2d[:, 0] = w / 2.0 + kp3d[:, 0] * k
            kp2d[:, 1] = h - 0.96 * k - kp3d[:, 1] * k  # projecao ortografica frontal
            score = np.ones(17, np.float32)
            out.append({"kp": kp2d, "score": score, "extra": {"kp3d": kp3d.tolist()}})
        return out

    def postprocess(self, pose: FramePose, frame_index: int, ctx: dict) -> FramePose:
        if pose.meta.get("kp3d") is not None:
            pose.kp3d = np.asarray(pose.meta["kp3d"], dtype=np.float32)
        return pose
