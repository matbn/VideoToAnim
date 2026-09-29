"""Lifter 2D -> 3D plugavel.

O lifter padrao e analitico/deterministico: usa priors antropometricos e
rigidez de comprimentos de osso para estimar profundidade (Z). Pode ser
trocado por um modelo aprendido (ex.: MotionBERT) implementando a interface.

Convencao de saida (kp3d): metros, y-up, ancorado no centro da imagem
(preserva a translacao do root entre frames). A sequencia e deslocada
verticalmente para que o quadril do primeiro frame fique em Y=0.98 m
(altura pelvica tipica), de modo que o personagem "pise" no chao.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

from .canonical import COCO_INDEX, FramePose, mid_hip, mid_shoulder

# comprimentos medios de osso/segmento (metros) para um adulto ~1.75 m
_BONE = {
    "spine": 0.52,       # mid-hip -> mid-shoulder
    "neck": 0.28,        # mid-shoulder -> nariz
    "shoulder_w": 0.36,
    "hip_w": 0.18,
    "upper_arm": 0.28,
    "forearm": 0.26,
    "thigh": 0.44,
    "shin": 0.42,
}
_PELVIS_HEIGHT = 0.98


class Lifter(ABC):
    @abstractmethod
    def lift(self, frames: list[FramePose]) -> list[FramePose]:
        raise NotImplementedError


def _solve_depth(parent: np.ndarray, target_xy: np.ndarray, length: float, sign: float) -> np.ndarray:
    """Ajusta o filho para que |filho - pai| == length, usando XY e profundidade."""
    v = np.array([target_xy[0] - parent[0], target_xy[1] - parent[1]], dtype=np.float64)
    d = float(np.linalg.norm(v))
    if length <= 1e-9:
        return np.array([parent[0] + v[0], parent[1] + v[1], parent[2]])
    if d < length:
        dz = float(np.sqrt(max(0.0, length * length - d * d)))
        return np.array([parent[0] + v[0], parent[1] + v[1], parent[2] + dz * sign])
    scale = length / max(d, 1e-9)
    return np.array([parent[0] + v[0] * scale, parent[1] + v[1] * scale, parent[2]])


class AnalyticLifter(Lifter):
    """Lifter geometrico deterministico com priors antropometricos."""

    def __init__(self, prefer_depth_sign: dict[str, float] | None = None):
        self._prefer = prefer_depth_sign or {}

    def lift(self, frames: list[FramePose]) -> list[FramePose]:
        if not frames:
            return frames
        shift = self._global_shift(frames[0])
        state: dict[str, float] = {}
        return [self._lift_frame(f, state, shift) for f in frames]

    @staticmethod
    def _scale(f: FramePose) -> float:
        mh = mid_hip(f.kp2d)
        ms = mid_shoulder(f.kp2d)
        torso_px = float(np.linalg.norm(ms - mh))
        return (_BONE["spine"] / torso_px) if torso_px > 5.0 else 0.0

    def _global_shift(self, first: FramePose) -> float:
        s = self._scale(first)
        if s <= 0:
            return 0.0
        cy = first.height / 2.0 if first.height else 0.0
        mh_y = float(mid_hip(first.kp2d)[1])
        return _PELVIS_HEIGHT - (cy - mh_y) * s

    def _lift_frame(self, f: FramePose, state: dict[str, float], shift: float) -> FramePose:
        ci = COCO_INDEX
        kp = f.kp2d.astype(np.float64)
        s = self._scale(f)
        if s <= 0.0:
            return f
        cx = (f.width / 2.0) if f.width else float(kp[:, 0].mean())
        cy = (f.height / 2.0) if f.height else float(kp[:, 1].mean())

        def to_plane(idx: int) -> np.ndarray:
            return np.array([(kp[idx, 0] - cx) * s, (cy - kp[idx, 1]) * s + shift, 0.0], dtype=np.float64)

        p: dict[int, np.ndarray] = {i: to_plane(i) for i in range(kp.shape[0])}
        mh3 = to_plane(ci["left_hip"]) * 0.5 + to_plane(ci["right_hip"]) * 0.5

        def solve(name: str, parent: np.ndarray, child_idx: int, length: float) -> np.ndarray:
            sign = state.get(name, self._prefer.get(name, 1.0))
            newc = _solve_depth(parent, p[child_idx], length, sign)
            prev_z = state.get(name + "_z")
            if prev_z is not None:
                alt = _solve_depth(parent, p[child_idx], length, -sign)
                if abs(alt[2] - prev_z) < abs(newc[2] - prev_z):
                    newc = alt
                    sign = -sign
            state[name] = sign
            state[name + "_z"] = float(newc[2])
            p[child_idx] = newc
            return newc

        # coluna: quadril -> centro dos ombros
        center_sh = solve("spine", mh3, ci["left_shoulder"], _BONE["spine"])
        # ombros a partir do centro do torax (largura fixa em X)
        half = _BONE["shoulder_w"] / 2.0
        p[ci["left_shoulder"]] = center_sh + np.array([half, 0.0, 0.0])
        p[ci["right_shoulder"]] = center_sh + np.array([-half, 0.0, 0.0])
        # cabeca
        nose = solve("neck", center_sh, ci["nose"], _BONE["neck"])
        # bracos
        solve("upper_arm_l", p[ci["left_shoulder"]], ci["left_elbow"], _BONE["upper_arm"])
        solve("forearm_l", p[ci["left_elbow"]], ci["left_wrist"], _BONE["forearm"])
        solve("upper_arm_r", p[ci["right_shoulder"]], ci["right_elbow"], _BONE["upper_arm"])
        solve("forearm_r", p[ci["right_elbow"]], ci["right_wrist"], _BONE["forearm"])
        # quadris
        half_h = _BONE["hip_w"] / 2.0
        p[ci["left_hip"]] = mh3 + np.array([half_h, 0.0, 0.0])
        p[ci["right_hip"]] = mh3 + np.array([-half_h, 0.0, 0.0])
        # pernas
        solve("thigh_l", p[ci["left_hip"]], ci["left_knee"], _BONE["thigh"])
        solve("shin_l", p[ci["left_knee"]], ci["left_ankle"], _BONE["shin"])
        solve("thigh_r", p[ci["right_hip"]], ci["right_knee"], _BONE["thigh"])
        solve("shin_r", p[ci["right_knee"]], ci["right_ankle"], _BONE["shin"])
        # olhos/orelhas proximos a cabeca
        for idx, off in (
            (ci["left_eye"], (0.03, 0.02, 0.0)), (ci["right_eye"], (-0.03, 0.02, 0.0)),
            (ci["left_ear"], (0.06, 0.0, 0.0)), (ci["right_ear"], (-0.06, 0.0, 0.0)),
        ):
            p[idx] = nose + np.asarray(off, dtype=np.float64)

        kp3d = np.stack([p[i] for i in range(kp.shape[0])], axis=0).astype(np.float32)
        return FramePose(f.kp2d, f.score, kp3d, f.width, f.height, dict(f.meta))
