"""Filtros de suavizacao temporal para sequencias de keypoints."""
from __future__ import annotations

import numpy as np


class OneEuroFilter:
    """Filtro One-Euro vetorizado (Casiez et al., 2012).

    Reduz jitter sem introduzir atraso excessivo em movimentos rapidos.
    """

    def __init__(self, min_cutoff: float = 1.0, beta: float = 0.02, d_cutoff: float = 1.0):
        self.min_cutoff = float(min_cutoff)
        self.beta = float(beta)
        self.d_cutoff = float(d_cutoff)
        self._x_prev: np.ndarray | None = None
        self._dx_prev: np.ndarray | None = None

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2.0 * np.pi * max(cutoff, 1e-6))
        return 1.0 / (1.0 + tau / max(dt, 1e-6))

    def __call__(self, x: np.ndarray, dt: float = 1.0 / 30.0) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64)
        if self._x_prev is None:
            self._x_prev = x.copy()
            self._dx_prev = np.zeros_like(x)
            return x.astype(np.float32)
        dx = (x - self._x_prev) / max(dt, 1e-6)
        a_d = self._alpha(self.d_cutoff, dt)
        dx_hat = a_d * dx + (1.0 - a_d) * self._dx_prev
        cutoff = self.min_cutoff + self.beta * np.abs(dx_hat)
        a = self._alpha(float(np.mean(cutoff)), dt)
        x_hat = a * x + (1.0 - a) * self._x_prev
        self._x_prev = x_hat
        self._dx_prev = dx_hat
        return x_hat.astype(np.float32)


def smooth_sequence(seq: list[np.ndarray], method: str = "none", **kwargs) -> list[np.ndarray]:
    """Aplica suavizacao a uma lista de arrays com a mesma forma.

    method: "none" | "oneeuro"
    """
    if method in (None, "none") or len(seq) < 3:
        return seq
    if method == "oneeuro":
        flt = OneEuroFilter(
            min_cutoff=kwargs.get("min_cutoff", 1.0),
            beta=kwargs.get("beta", 0.02),
        )
        return [flt(np.asarray(a, dtype=np.float32), kwargs.get("dt", 1.0 / 30.0)) for a in seq]
    raise ValueError(f"metodo de suavizacao desconhecido: {method}")


def smooth_frames(frames, method: str = "none", **kwargs):
    """Suaviza kp2d (e kp3d, se presente) de uma lista de FramePose in-place."""
    if method in (None, "none") or len(frames) < 3:
        return frames
    kp2d = smooth_sequence([f.kp2d for f in frames], method, **kwargs)
    for f, k in zip(frames, kp2d):
        f.kp2d = np.asarray(k, dtype=np.float32)
    if any(f.kp3d is not None for f in frames):
        base = frames[0].kp3d
        if base is not None:
            kp3d = smooth_sequence([f.kp3d if f.kp3d is not None else base for f in frames], method, **kwargs)
            for f, k in zip(frames, kp3d):
                f.kp3d = np.asarray(k, dtype=np.float32)
    return frames
