"""Metricas objetivas para comparar filtros de estabilizacao.

Para cada filtro comparamos, contra o sinal original:

* **suavidade** — energia do jerk (2a derivada); quanto menor, mais suave;
* **atraso** — defasagem em frames via correlacao cruzada (0 = nenhum atraso);
* **desvio residual** — RMSE entre original e filtrado (quanto de sinal mudou).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from core.mixamo import ANIMATED_BONES
from core.refine.filters import filter_series, smooth_quaternion_series, quat_continuity
from core.refine.constraints import quat_angle_deg


# ---------------------------------------------------------------------------
# Metricas
# ---------------------------------------------------------------------------
def jerk_energy(x: np.ndarray, fps: float) -> float:
    x = np.asarray(x, np.float64)
    if x.shape[0] < 3:
        return 0.0
    d2 = np.diff(x, n=2, axis=0)
    return float(np.mean(d2 ** 2) * (fps ** 4))


def cross_corr_lag(orig: np.ndarray, filt: np.ndarray, max_lag: int = 30) -> int:
    """Defasagem (em frames) que melhor alinha filtrado a original."""
    a = np.asarray(orig, np.float64)
    b = np.asarray(filt, np.float64)
    if a.shape[0] < 4 or a.shape != b.shape:
        return 0
    a = a - a.mean(axis=0)
    b = b - b.mean(axis=0)
    n = a.shape[0]
    max_lag = int(min(max_lag, n - 2))
    best_lag, best = 0, -np.inf
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            x, y = a[: n - lag], b[lag:]
        else:
            x, y = a[-lag:], b[: n + lag]
        if x.shape[0] < 3:
            continue
        num = float(np.sum(x * y))
        den = float(np.sqrt(np.sum(x * x) * np.sum(y * y))) + 1e-12
        c = num / den
        if c > best:
            best, best_lag = c, lag
    return int(best_lag)


def rmse(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, np.float64)
    b = np.asarray(b, np.float64)
    if a.shape != b.shape or a.size == 0:
        return 0.0
    return float(np.sqrt(np.mean((a - b) ** 2)))


def angular_error_deg(orig_q: np.ndarray, filt_q: np.ndarray) -> np.ndarray:
    """Erro angular (graus) por frame entre duas series de quaternions."""
    a = quat_continuity(np.asarray(orig_q, np.float64))
    b = quat_continuity(np.asarray(filt_q, np.float64))
    d = np.abs(np.sum(a * b, axis=1))
    return np.degrees(2.0 * np.arccos(np.clip(d, -1.0, 1.0)))


# ---------------------------------------------------------------------------
# Relatorio
# ---------------------------------------------------------------------------
@dataclass
class FilterMetrics:
    filter: str
    params: dict = field(default_factory=dict)
    jerk_before: float = 0.0
    jerk_after: float = 0.0
    smoothness_gain_pct: float = 0.0
    lag_frames: int = 0
    rmse: float = 0.0
    angular_rmse_deg: float = 0.0
    max_angular_deg: float = 0.0

    def as_row(self) -> dict:
        return {
            "filtro": self.filter, "jerk_antes": round(self.jerk_before, 6),
            "jerk_depois": round(self.jerk_after, 6),
            "ganho_suavidade_%": round(self.smoothness_gain_pct, 2),
            "atraso_frames": self.lag_frames, "rmse": round(self.rmse, 6),
            "erro_angular_medio_deg": round(self.angular_rmse_deg, 3),
            "erro_angular_max_deg": round(self.max_angular_deg, 3),
        }


def compare_filters(anim, filter_names: list[str], fps: float | None = None,
                    params: dict | None = None) -> list[FilterMetrics]:
    """Aplica cada filtro ao MESMO clipe e mede suavidade, atraso e desvio."""
    fps = float(fps or getattr(anim, "fps", 30.0) or 30.0)
    params = params or {}
    out: list[FilterMetrics] = []
    for name in filter_names:
        jerks_b, jerks_a, rmses, angs, angmax, lags = [], [], [], [], [], []
        for bone in ANIMATED_BONES:
            q = anim.rotations.get(bone)
            if q is None or q.shape[0] < 4:
                continue
            q = np.asarray(q, np.float64)
            p = dict(params.get(name, {}))
            qf = smooth_quaternion_series(q, name, fps, **p)
            jerks_b.append(jerk_energy(quat_continuity(q), fps))
            jerks_a.append(jerk_energy(quat_continuity(qf), fps))
            rmses.append(rmse(quat_continuity(q), quat_continuity(qf)))
            err = angular_error_deg(q, qf)
            angs.append(float(err.mean()))
            angmax.append(float(err.max()))
            lags.append(cross_corr_lag(quat_continuity(q), quat_continuity(qf)))
        jb = float(np.mean(jerks_b)) if jerks_b else 0.0
        ja = float(np.mean(jerks_a)) if jerks_a else 0.0
        gain = (1.0 - ja / jb) * 100.0 if jb > 0 else 0.0
        out.append(FilterMetrics(
            filter=name, params=params.get(name, {}),
            jerk_before=jb, jerk_after=ja, smoothness_gain_pct=gain,
            lag_frames=int(np.median(lags)) if lags else 0,
            rmse=float(np.mean(rmses)) if rmses else 0.0,
            angular_rmse_deg=float(np.mean(angs)) if angs else 0.0,
            max_angular_deg=float(np.max(angmax)) if angmax else 0.0,
        ))
    return out


def to_markdown(rows: list[dict] | list[FilterMetrics], title: str = "Comparativo de filtros") -> str:
    rows = [r.as_row() if isinstance(r, FilterMetrics) else r for r in rows]
    if not rows:
        return f"# {title}\n\n(sem dados)\n"
    cols = list(rows[0].keys())
    lines = [f"# {title}", "", "| " + " | ".join(cols) + " |",
             "|" + "|".join(["---"] * len(cols)) + "|"]
    for r in rows:
        lines.append("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
    return "\n".join(lines) + "\n"


def save_report(out_dir: str | Path, *, comparison: list[FilterMetrics] | None = None,
                constraints: dict | None = None, edits: list[dict] | None = None,
                extra: dict | None = None) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    payload: dict = {}
    if comparison is not None:
        payload["filter_comparison"] = [m.as_row() for m in comparison]
        (out / "filtros_comparativo.md").write_text(
            to_markdown(comparison, "Comparativo de estabilizacao"), encoding="utf-8")
    if constraints is not None:
        payload["constraints"] = constraints
    if edits is not None:
        payload["edits"] = edits
    if extra:
        payload.update(extra)
    (out / "refine_report.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload
