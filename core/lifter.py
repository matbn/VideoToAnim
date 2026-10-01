"""Lifter 2D -> 3D plugavel.

O lifter padrao e analitico/deterministico: usa priors antropometricos e
rigidez de comprimento de osso para estimar profundidade (Z). Pode ser
trocado por um modelo aprendido (ex.: MotionBERT) implementando a interface.

Convencao de saida (kp3d): metros, y-up, ancorado no centro da imagem
(preserva a translacao do root entre frames). A sequencia e deslocada
verticalmente para que o quadril do primeiro frame fique em Y=0.98 m
(altura pelvica tipica), de modo que o personagem "pise" no chao.

Profundidade: solucao 1D por frame
----------------------------------
X e Y vem **exatamente** da imagem: a deteccao 2D e a verdade, entao a
reprojecao e zero por construcao. O unico desconhecido e o Z das juntas
distais (cotovelos, pulsos, joelhos, tornozelos), resolvido por
Gauss-Newton num objetivo com quatro termos:

  1. **rigidez de comprimento de osso**: peso *suave*, nunca distorce a
     imagem, apenas prefere profundidades que fechem o comprimento real;
  2. **nao-penetracao do tronco**: o membro nao pode ficar DENTRO do
     tronco. A clearance e medida so em Z (a camera so tem essa
     ambiguidade; X/Y estao fixados pela imagem) e o tronco e uma elipse:
     meia-espessura em Z = ``half_D * sqrt(1 - (dx/half_W)^2)``;
  3. **continuidade temporal**: o Z nao salta entre frames;
  4. **prior antropometrico fraco**: bracos levemente a frente do plano do
     tronco, pernas no plano (desambigua frente/tras quando o video nao da
     evidencia).

Por que a versao anterior falhava
--------------------------------
``_solve_depth`` resolvia uma junta por vez. Quando o segmento 2D e MAIOR
que o osso nao ha solucao real (``sqrt(L^2 - d^2)`` imaginario) e o codigo
*escalava o filho em direcao ao pai*, alterando X/Y. Medido: ate 61 px de
erro de reprojecao e o braco "achatado" (o cotovelo perdia 12-20 graus).
Pior: sem solucao de profundidade, o Z saia exatamente 0, coplanar com o
tronco (medido: 30% dos cotovelos com Z=0) e o braco nascia DENTRO do
tronco, sem nada para a anticolisao corrigir.
"""
from __future__ import annotations

import math
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

# --- elipse do tronco (metades de largura/profundidade, metros) ------------
_TORSO_HALF_W = 0.17     # meio ombro-a-ombro / quadril
_TORSO_HALF_D = 0.12     # peito -> costas
_LIMB_R = 0.05           # raio do membro: a superficie do membro precisa
                         # _clearar_ (ficar fora) a superficie do tronco

# --- pesos do objetivo de profundidade ------------------------------------
_W_LEN = 1.0             # rigidez de comprimento de osso
_W_TORSO = 9.0           # nao-penetracao do tronco
_W_TIME = 2.5            # continuidade temporal
_W_PRIOR = 0.30          # prior fraco (z ~ centro)
_PEN_SOFT = 0.05         # largura do hinge suave de penetracao (m)
_GN_ITERS = 14
_GN_DAMP = 1e-4          # regularizacao de Tikhonov (estabilidade)

# Juntas com profundidade livre (tronco e cabeca ficam em z=0).
_FREE_JOINTS = (
    "left_elbow", "left_wrist", "right_elbow", "right_wrist",
    "left_knee", "left_ankle", "right_knee", "right_ankle",
)

# Segmentos com comprimento rigido: (pai, filho, comprimento).
_LENGTH_SEGS = (
    ("left_shoulder", "left_elbow", _BONE["upper_arm"]),
    ("left_elbow", "left_wrist", _BONE["forearm"]),
    ("right_shoulder", "right_elbow", _BONE["upper_arm"]),
    ("right_elbow", "right_wrist", _BONE["forearm"]),
    ("left_hip", "left_knee", _BONE["thigh"]),
    ("left_knee", "left_ankle", _BONE["shin"]),
    ("right_hip", "right_knee", _BONE["thigh"]),
    ("right_knee", "right_ankle", _BONE["shin"]),
)

# Segmentos verificados contra o tronco: (pai, filho, amostras, clearance).
# A amostra t=0 do braco e da coxa e a ARTICULACAO, que esta no tronco por
# definicao: por isso comeca em t>0, senao a correcao brigaria com a ancora.
_PENETRATION_SEGS = (
    ("left_shoulder", "left_elbow", (0.55, 1.0), 0.0),
    ("left_elbow", "left_wrist", (0.0, 0.5, 1.0), _LIMB_R),
    ("right_shoulder", "right_elbow", (0.55, 1.0), 0.0),
    ("right_elbow", "right_wrist", (0.0, 0.5, 1.0), _LIMB_R),
    ("left_hip", "left_knee", (0.6, 1.0), 0.0),
    ("left_knee", "left_ankle", (0.15, 0.55, 1.0), _LIMB_R * 0.9),
    ("right_hip", "right_knee", (0.6, 1.0), 0.0),
    ("right_knee", "right_ankle", (0.15, 0.55, 1.0), _LIMB_R * 0.9),
)


def _point_segment(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Ponto de `a`->`b` mais proximo de `p`."""
    ab = b - a
    denom = float(ab @ ab)
    if denom < 1e-12:
        return a
    t = float(np.clip(((p - a) @ ab) / denom, 0.0, 1.0))
    return a + ab * t


class Lifter(ABC):
    @abstractmethod
    def lift(self, frames: list[FramePose]) -> list[FramePose]:
        raise NotImplementedError


class AnalyticLifter(Lifter):
    """Lifter geometrico deterministico com priors antropometricos.

    `prefer_depth_sign` mantem a compatibilidade com a assinatura anterior: o
    valor por segmento ("left_elbow" etc.) e o sinal de Z preferido. O default
    (+1) e lido como "frente" e vira o prior dos bracos.
    """

    def __init__(self, prefer_depth_sign: dict[str, float] | None = None):
        self._prefer = prefer_depth_sign or {}
        self.last_report: dict = {}
        ci = COCO_INDEX
        self._var_idx = np.array([ci[n] for n in _FREE_JOINTS], dtype=np.int64)
        self._segs = [(ci[p], ci[c], float(L)) for p, c, L in _LENGTH_SEGS]
        self._pens = [(ci[p], ci[c], tuple(t), float(x))
                      for p, c, t, x in _PENETRATION_SEGS]
        # coluna do vetor z de cada junta livre (-1 = junta fixa em z=0)
        self._var_of = {int(ci[n]): k for k, n in enumerate(_FREE_JOINTS)}
        # prior por junta: bracos levemente a frente, pernas no plano
        self._prior = np.zeros(len(_FREE_JOINTS), dtype=np.float64)
        for k, name in enumerate(_FREE_JOINTS):
            side = "left" if name.startswith("left") else "right"
            limb = ("elbow" if "elbow" in name else
                    "wrist" if "wrist" in name else
                    "knee" if "knee" in name else "ankle")
            if limb in ("elbow", "wrist"):
                sign = float(self._prefer.get(f"{limb}_{side[0]}", 1.0))
                self._prior[k] = (sign if sign != 0.0 else 1.0) * 0.03

    # ------------------------------------------------------------------
    # Pipeline
    # ------------------------------------------------------------------
    def lift(self, frames: list[FramePose]) -> list[FramePose]:
        if not frames:
            return frames
        shift = self._global_shift(frames[0])
        out: list[FramePose] = []
        z_prev: np.ndarray | None = None
        pen_frames = 0
        depth_sum = 0.0
        n_ok = 0
        for f in frames:
            pts = self._frame_plane(f, shift)
            if pts is None:
                out.append(f)
                continue
            z, info = self._solve_depths(self._frame_constants(pts), z_prev)
            z_prev = z
            pen_frames += int(info["penetrating"])
            depth_sum += float(np.abs(z).mean())
            n_ok += 1
            pts[self._var_idx, 2] = z
            out.append(FramePose(f.kp2d, f.score, pts.astype(np.float32),
                                 f.width, f.height, dict(f.meta)))
        self.last_report = {
            "frames": len(out),
            "frames_with_torso_penetration": pen_frames,
            "mean_abs_limb_depth_m": round(depth_sum / max(1, n_ok), 4),
            "free_joints": list(_FREE_JOINTS),
        }
        return out

    # ------------------------------------------------------------------
    # Plano: X, Y da imagem (exatos); Z do tronco/cabeca = 0
    # ------------------------------------------------------------------
    def _frame_plane(self, f: FramePose, shift: float) -> np.ndarray | None:
        ci = COCO_INDEX
        kp = f.kp2d.astype(np.float64)
        s = self._scale(f)
        if s <= 0.0:
            return None
        cx = (f.width / 2.0) if f.width else float(kp[:, 0].mean())
        cy = (f.height / 2.0) if f.height else float(kp[:, 1].mean())

        def to_plane(idx: int) -> np.ndarray:
            return np.array([(kp[idx, 0] - cx) * s, (cy - kp[idx, 1]) * s + shift, 0.0],
                            dtype=np.float64)

        p = np.stack([to_plane(i) for i in range(kp.shape[0])], axis=0)
        mh = 0.5 * (to_plane(ci["left_hip"]) + to_plane(ci["right_hip"]))
        # ombros: largura anatomica fixa em X. O COCO 2D superestima a largura
        # quando o personagem esta de frente para a camera; fixar a largura
        # mantem o tronco sem vies lateral (o bug dos "corpos pendidos").
        half = _BONE["shoulder_w"] / 2.0
        mid_sh = 0.5 * (to_plane(ci["left_shoulder"]) + to_plane(ci["right_shoulder"]))
        # a coluna e medida no PLANO (a imagem nao tem z): normaliza-se o
        # comprimento para _BONE["spine"], sem introduzir componente em z.
        v = mid_sh - mh
        nv = float(np.linalg.norm(v))
        if nv > 1e-9:
            mid_sh = mh + v * (_BONE["spine"] / nv)
        p[ci["left_shoulder"]] = mid_sh + np.array([half, 0.0, 0.0])
        p[ci["right_shoulder"]] = mid_sh + np.array([-half, 0.0, 0.0])
        # quadris: largura anatomica fixa em X
        half_h = _BONE["hip_w"] / 2.0
        p[ci["left_hip"]] = mh + np.array([half_h, 0.0, 0.0])
        p[ci["right_hip"]] = mh + np.array([-half_h, 0.0, 0.0])
        # cabeca no PLANO do tronco (z=0 estavel): o retarget so usa a direcao
        # nariz->ombros, e z instavel fazia a cabeca oscilar sozinha.
        for idx in (ci["nose"], ci["left_eye"], ci["right_eye"],
                    ci["left_ear"], ci["right_ear"]):
            p[idx, 2] = 0.0
        return p

    # ------------------------------------------------------------------
    # Objetivo de profundidade
    # ------------------------------------------------------------------
    def _frame_constants(self, pts: np.ndarray) -> dict:
        """Constantes do frame que NAO dependem de z.

        Toda a geometria de X/Y vem da imagem e e fixa; por isso a coluna do
        eixo do tronco, a distancia horizontal de cada amostra e a meia-
        espessura exigida sao calculadas UMA vez por frame. O residuo vira
        depois uma funcao pura de z (barata), o que torna o Gauss-Newton
        rapido: 167 frames passaram de 12 s para ~0.2 s.
        """
        ci = COCO_INDEX
        a = 0.5 * (pts[ci["left_hip"]] + pts[ci["right_hip"]])
        b = 0.5 * (pts[ci["left_shoulder"]] + pts[ci["right_shoulder"]])
        # XY (e o z do eixo, que e 0) de cada amostra de penetracao
        need: list[float] = []
        zcoef: list[tuple[int, float, int, float]] = []
        for (pa, ch, samples, extra) in self._pens:
            base = pts[pa]
            tip = pts[ch]
            ca = self._var_of.get(pa, -1)
            cb = self._var_of.get(ch, -1)
            for t in samples:
                pnt = base + (tip - base) * t
                q = _point_segment(pnt, a, b)
                dx = math.hypot(pnt[0] - q[0], pnt[1] - q[1])
                if dx >= _TORSO_HALF_W:
                    need.append(0.0)          # fora do torso: residuo sempre 0
                else:
                    inside = _TORSO_HALF_D * math.sqrt(
                        max(0.0, 1.0 - (dx / _TORSO_HALF_W) ** 2))
                    need.append(inside + extra)
                # z da amostra = z_do_pai*(1-t) + z_do_filho*t
                zcoef.append((ca, 1.0 - t, cb, t))
        # XY (e o z) de cada segmento de comprimento
        seg_xy: list[tuple[float, float, float, int, int, float, float]] = []
        for (pa, ch, L) in self._segs:
            dxy = pts[ch] - pts[pa]
            seg_xy.append((float(dxy[0]), float(dxy[1]),
                           float(dxy[0] ** 2 + dxy[1] ** 2),
                           self._var_of.get(pa, -1), self._var_of.get(ch, -1),
                           1.0, L))
        return {"need": np.asarray(need, dtype=np.float64),
                "zcoef": zcoef, "seg": seg_xy}

    def _residuals(self, z: np.ndarray, const: dict,
                   z_prev: np.ndarray | None) -> np.ndarray:
        """Residuais do objetivo. O TAMANHO e fixo (a jacobiana depende disso):
        a penetracao entra como hinge quadratica suave, que vale 0 longe do
        tronco e cresce sob a elipse, sem mudar a dimensao do vetor."""
        r: list[np.ndarray] = []
        # 1) rigidez de comprimento de osso
        for (dx, dy, dxy2, ca, cb, wa, L) in const["seg"]:
            za = z[ca] if ca >= 0 else 0.0
            zb = z[cb] if cb >= 0 else 0.0
            d = math.sqrt(dxy2 + (zb - za) ** 2)
            r.append(np.array([math.sqrt(_W_LEN) * (d - L)]))
        # 2) nao-penetracao do tronco, medida SO em Z
        need = const["need"]
        if need.size:
            zsamp = np.zeros(need.size, dtype=np.float64)
            for i, (ca, wa, cb, wb) in enumerate(const["zcoef"]):
                v = 0.0
                if ca >= 0:
                    v += wa * z[ca]
                if cb >= 0:
                    v += wb * z[cb]
                zsamp[i] = v
            pen = need - np.abs(zsamp)       # o eixo do tronco esta em z=0
            act = np.where(pen > 0.0, pen, 0.0)
            r.append(-math.sqrt(_W_TORSO) * act * act / (2.0 * _PEN_SOFT))
        # 3) continuidade temporal
        if z_prev is not None:
            r.append(math.sqrt(_W_TIME) * (z - z_prev))
        # 4) prior antropometrico
        r.append(math.sqrt(_W_PRIOR) * (z - self._prior))
        return np.concatenate(r)

    def _solve_depths(self, const: dict,
                      z_prev: np.ndarray | None) -> tuple[np.ndarray, dict]:
        """Gauss-Newton com jacobiana por diferencas finitas + busca de linha."""
        n = len(self._var_idx)
        z = np.zeros(n, dtype=np.float64) if z_prev is None else z_prev.copy()
        r = self._residuals(z, const, z_prev)
        cost = float(r @ r)
        eps = 1e-4
        for _ in range(_GN_ITERS):
            if cost < 1e-10:
                break
            J = np.empty((r.size, n), dtype=np.float64)
            for k in range(n):
                zp = z.copy(); zp[k] += eps
                zm = z.copy(); zm[k] -= eps
                J[:, k] = (self._residuals(zp, const, z_prev)
                           - self._residuals(zm, const, z_prev)) / (2.0 * eps)
            A = J.T @ J + _GN_DAMP * np.eye(n)
            g = J.T @ r
            try:
                step = -np.linalg.solve(A, g)
            except np.linalg.LinAlgError:
                step = -np.linalg.lstsq(A, g, rcond=None)[0]
            if not np.all(np.isfinite(step)):
                break
            alpha = 1.0
            improved = False
            for _ls in range(6):
                zt = z + alpha * step
                rt = self._residuals(zt, const, z_prev)
                ct = float(rt @ rt)
                if ct < cost:
                    z, r, cost = zt, rt, ct
                    improved = True
                    break
                alpha *= 0.5
            if not improved:
                break
        return z, {"penetrating": bool(np.any(r < -1e-9)), "cost": cost}

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

