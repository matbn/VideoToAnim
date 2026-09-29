"""Filtros de estabilizacao para animacoes bakeadas.

Cada filtro opera sobre uma serie temporal (T,) ou (T, K) e e identificado por
um nome em `FILTERS`. O caso principal aqui sao **quaternions**: filtramos os
componentes na representacao continua (corrigindo o sinal entre frames para
evitar "saltos" de +q para -q, que sao a mesma rotacao) e re-normalizamos.

Todos implementados em **numpy puro** (sem scipy).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np


# ---------------------------------------------------------------------------
# Utilitarios
# ---------------------------------------------------------------------------
def _pad_reflect(x: np.ndarray, pad: int) -> np.ndarray:
    if pad <= 0:
        return x
    if x.shape[0] <= pad:
        pad = max(1, x.shape[0] - 1)
    head = x[1:pad + 1][::-1]
    tail = x[-pad - 1:-1][::-1]
    return np.concatenate([head, x, tail], 0)


def quat_continuity(q: np.ndarray) -> np.ndarray:
    """Corrige o sinal dos quaternions para que a serie seja continua.

    q e -q representam a mesma rotacao; filtrar sem isso produz saltos de 2 em
    cada componente e o filtro "nao faz nada" (ou faz coisa errada).
    """
    q = np.asarray(q, np.float64).copy()
    for t in range(1, q.shape[0]):
        if float(np.dot(q[t], q[t - 1])) < 0.0:
            q[t] = -q[t]
    return q


def normalize_quats(q: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(q, axis=-1, keepdims=True)
    return q / np.where(n < 1e-12, 1.0, n)


# ---------------------------------------------------------------------------
# Filtros 1D (aplicados componente a componente)
# ---------------------------------------------------------------------------
def one_euro(x: np.ndarray, fps: float, min_cutoff: float = 1.0, beta: float = 0.02,
             d_cutoff: float = 1.0) -> np.ndarray:
    """One-Euro (Casiez 2012) — ja existia no projeto, mantido como base."""
    x = np.asarray(x, np.float64)
    dt = 1.0 / max(fps, 1e-6)

    def alpha(cutoff: float) -> float:
        tau = 1.0 / (2.0 * np.pi * max(cutoff, 1e-6))
        return 1.0 / (1.0 + tau / dt)

    out = np.empty_like(x)
    x_prev = x[0].copy()
    dx_prev = np.zeros_like(x_prev)
    out[0] = x_prev
    a_d = alpha(d_cutoff)
    for t in range(1, x.shape[0]):
        dx = (x[t] - x_prev) / dt
        dx_hat = a_d * dx + (1.0 - a_d) * dx_prev
        cutoff = min_cutoff + beta * np.abs(dx_hat)
        a = alpha(float(np.mean(cutoff)))
        x_prev = a * x[t] + (1.0 - a) * x_prev
        dx_prev = dx_hat
        out[t] = x_prev
    return out


def moving_average(x: np.ndarray, fps: float, window: int = 5, weight: str = "linear") -> np.ndarray:
    """Media movel ponderada (weight: 'linear' | 'uniform')."""
    x = np.asarray(x, np.float64)
    window = max(1, int(window))
    if window == 1:
        return x.copy()
    if weight == "uniform":
        w = np.ones(window, np.float64)
    else:
        w = np.linspace(1.0, float(window), window, dtype=np.float64)
        w = np.minimum(w, w[::-1])          # simetrica
    w = w / w.sum()
    pad = window // 2
    xp = _pad_reflect(x, pad)
    if x.ndim == 1:
        return np.convolve(xp, w, mode="valid")[: x.shape[0]]
    out = np.empty_like(x)
    for k in range(x.shape[1]):
        out[:, k] = np.convolve(xp[:, k], w, mode="valid")[: x.shape[0]]
    return out


def savgol_coeffs(window: int, order: int = 2) -> np.ndarray:
    """Coeficientes Savitzky-Golay para o ponto central da janela."""
    window = int(window) | 1                        # impar
    half = window // 2
    order = min(int(order), window - 1)
    x = np.arange(-half, half + 1, dtype=np.float64)
    a = np.vander(x, order + 1, increasing=True)
    return np.linalg.pinv(a)[0] * window            # linha 0 do pinv = centro


def savgol(x: np.ndarray, fps: float, window: int = 11, order: int = 2) -> np.ndarray:
    """Savitzky-Golay (suaviza preservando melhor picos que a media movel)."""
    x = np.asarray(x, np.float64)
    window = int(window) | 1
    if window < 3 or window > x.shape[0]:
        return x.copy()
    c = savgol_coeffs(window, order)
    pad = window // 2
    xp = _pad_reflect(x, pad)
    if x.ndim == 1:
        return np.convolve(xp, c, mode="valid")[: x.shape[0]]
    out = np.empty_like(x)
    for k in range(x.shape[1]):
        out[:, k] = np.convolve(xp[:, k], c, mode="valid")[: x.shape[0]]
    return out


def kalman(x: np.ndarray, fps: float, process_noise: float = 1e-3,
           measurement_noise: float = 1e-2) -> np.ndarray:
    """Kalman com modelo de velocidade constante (estado = [posicao, velocidade])."""
    x = np.asarray(x, np.float64)
    dt = 1.0 / max(fps, 1e-6)
    f = np.array([[1.0, dt], [0.0, 1.0]])
    h = np.array([[1.0, 0.0]])
    q = np.eye(2) * float(process_noise)
    r = np.eye(1) * float(measurement_noise)
    if x.ndim == 1:
        return _kalman_1d(x, f, h, q, r)
    out = np.empty_like(x)
    for k in range(x.shape[1]):
        out[:, k] = _kalman_1d(x[:, k], f, h, q, r)
    return out


def _kalman_1d(x: np.ndarray, f, h, q, r) -> np.ndarray:
    st = np.array([x[0], 0.0])
    p = np.eye(2)
    out = np.empty_like(x)
    out[0] = x[0]
    for t in range(1, x.shape[0]):
        st = f @ st
        p = f @ p @ f.T + q
        s = h @ p @ h.T + r
        k = p @ h.T @ np.linalg.inv(s)
        st = st + (k @ (np.array([x[t]]) - h @ st))
        p = (np.eye(2) - k @ h) @ p
        out[t] = st[0]
    return out


def butterworth(x: np.ndarray, fps: float, cutoff_hz: float = 6.0,
                order: int = 2, zero_phase: bool = True) -> np.ndarray:
    """Passa-baixa Butterworth (biquad de 2a ordem via transformada bilinear).

    zero_phase=True roda para frente e para tras (sem atraso de fase), pelo
    custo de nao ser causal — ideal para limpeza offline de animacao.
    """
    x = np.asarray(x, np.float64)
    nyq = max(fps, 1e-6) / 2.0
    fc = float(np.clip(cutoff_hz, 1e-4, nyq * 0.999))
    wc = np.tan(np.pi * fc / (2.0 * nyq))
    k = wc * wc
    d = 1.0 + np.sqrt(2.0) * wc + k
    b = np.array([k / d, 2 * k / d, k / d])
    a = np.array([1.0, 2.0 * (k - 1.0) / d, (1.0 - np.sqrt(2.0) * wc + k) / d])

    def _fwd(sig: np.ndarray) -> np.ndarray:
        out = np.empty_like(sig)
        z1 = z2 = 0.0
        for t in range(sig.shape[0]):
            v = sig[t] - a[1] * z1 - a[2] * z2
            out[t] = b[0] * v + b[1] * z1 + b[2] * z2
            z2, z1 = z1, v
        return out

    def _run(sig: np.ndarray) -> np.ndarray:
        return _fwd(sig[::-1])[::-1] if zero_phase else _fwd(sig)

    if x.ndim == 1:
        return _run(x)
    out = np.empty_like(x)
    for c in range(x.shape[1]):
        out[:, c] = _run(x[:, c])
    return out


def double_exponential(x: np.ndarray, fps: float, alpha: float = 0.35,
                       beta: float = 0.1) -> np.ndarray:
    """Suavizacao exponencial dupla (Holt): nivel + tendencia."""
    x = np.asarray(x, np.float64)

    def _run1(sig: np.ndarray) -> np.ndarray:
        lvl = sig[0]
        trend = sig[1] - sig[0] if sig.shape[0] > 1 else 0.0
        out = np.empty_like(sig)
        out[0] = lvl
        for t in range(1, sig.shape[0]):
            prev = lvl
            lvl = alpha * sig[t] + (1 - alpha) * (lvl + trend)
            trend = beta * (lvl - prev) + (1 - beta) * trend
            out[t] = lvl
        return out

    if x.ndim == 1:
        return _run1(x)
    out = np.empty_like(x)
    for c in range(x.shape[1]):
        out[:, c] = _run1(x[:, c])
    return out


# ---------------------------------------------------------------------------
# Registro
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FilterSpec:
    name: str
    func: Callable
    params: dict = field(default_factory=dict)
    description: str = ""
    description_en: str = ""


FILTERS: dict[str, FilterSpec] = {
    "one_euro": FilterSpec("one_euro", one_euro,
                           {"min_cutoff": 1.0, "beta": 0.02, "d_cutoff": 1.0},
                           "Adaptativo: corta mais onde o movimento e lento. Base do projeto.",
                           "Adaptive: cuts harder where motion is slow. The project baseline."),
    "moving_average": FilterSpec("moving_average", moving_average,
                                 {"window": 5, "weight": "linear"},
                                 "Media movel ponderada. Simples e previsivel.",
                                 "Weighted moving average. Simple and predictable."),
    "savgol": FilterSpec("savgol", savgol, {"window": 11, "order": 2},
                         "Savitzky-Golay: suaviza preservando melhor picos e aceleracoes.",
                         "Savitzky-Golay: smooths while better preserving peaks and accelerations."),
    "kalman": FilterSpec("kalman", kalman, {"process_noise": 1e-3, "measurement_noise": 1e-2},
                         "Kalman com modelo de velocidade: bom com ruido gaussiano.",
                         "Kalman with a velocity model: good with Gaussian noise."),
    "butterworth": FilterSpec("butterworth", butterworth,
                              {"cutoff_hz": 6.0, "order": 2, "zero_phase": True},
                              "Passa-baixa Butterworth. zero_phase=True nao introduz atraso.",
                              "Butterworth low-pass. zero_phase=True introduces no delay."),
    "double_exponential": FilterSpec("double_exponential", double_exponential,
                                     {"alpha": 0.35, "beta": 0.1},
                                     "Exponencial dupla (Holt): segue tendencia com pouca memoria.",
                                     "Double exponential (Holt): follows trends with little memory."),
}


def filter_series(x: np.ndarray, spec_name: str, fps: float, **params) -> np.ndarray:
    if spec_name not in FILTERS:
        raise ValueError(f"filtro desconhecido: {spec_name!r} (disponiveis: {sorted(FILTERS)})")
    spec = FILTERS[spec_name]
    merged = {**spec.params, **{k: v for k, v in params.items() if v is not None}}
    return spec.func(np.asarray(x, np.float64), fps, **merged)


def smooth_quaternion_series(q: np.ndarray, spec_name: str, fps: float, **params) -> np.ndarray:
    """Filtra uma serie de quaternions (T,4) com seguranca."""
    q = np.asarray(q, np.float64)
    if q.shape[0] < 3:
        return q.copy()
    cont = quat_continuity(q)
    out = filter_series(cont, spec_name, fps, **params)
    return normalize_quats(out)
