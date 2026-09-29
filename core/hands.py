"""Hand tracking: anima os 40 ossos de dedo a partir das maos detectadas.

Motivo: o COCO-17 nao tem juntas de mao, entao no retarget de corpo os dedos
ficam parados (identidade). Com o `HandLandmarker` do MediaPipe (21 pontos por
mao) preenchemos essa lacuna — os dedos passam a ser animados.

Como funciona o mapeamento (21 landmarks -> 4 ossos por dedo do nosso rig):

    dedo        landmarks                       ossos
    indicador   5(MCP) 6(PIP) 7(DIP) 8(TIP)  -> HandIndex1..3 (+4 = ponta)
    medio       9 10 11 12
    anelar      13 14 15 16
    mindinho    17 18 19 20
    polegar     1(CMC) 2(MCP) 3(IP) 4(TIP)

Cada osso recebe a rotacao que leva a direcao de rest (offset do rig) ate a
direcao da falange, expressa no referencial da MAO (montado com punho, MCP do
indicador e MCP do mindinho) — que e o referencial do osso `Hand` no nosso rig.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from core import mixamo as mx

HAND_MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
                  "hand_landmarker/float16/latest/hand_landmarker.task")
DEFAULT_HAND_MODEL = Path("models") / "hand_landmarker.task"

# (nome do dedo, landmarks mcp/pip/dip/tip)
FINGERS = {
    "Index":  (5, 6, 7, 8),
    "Middle": (9, 10, 11, 12),
    "Ring":   (13, 14, 15, 16),
    "Pinky":  (17, 18, 19, 20),
}
THUMB = (1, 2, 3, 4)          # CMC, MCP, IP, TIP


def _hand_frame(lm: np.ndarray) -> np.ndarray:
    """Base ortonormal da mao: x = punho->MCP indicador, z = normal da palma."""
    wrist = lm[0]
    x = lm[5] - wrist
    nx = np.linalg.norm(x)
    x = x / nx if nx > 1e-9 else np.array([1.0, 0.0, 0.0])
    v = lm[17] - wrist
    z = np.cross(x, v)
    nz = np.linalg.norm(z)
    z = z / nz if nz > 1e-9 else np.array([0.0, 0.0, 1.0])
    y = np.cross(z, x)
    return np.stack([x, y, z], axis=0)      # linhas = eixos


def finger_rotations_from_landmarks(lm_world: np.ndarray, side: str) -> dict[str, np.ndarray]:
    """Rotacoes locais dos dedos a partir dos world landmarks (21,3).

    `side` e "Left" ou "Right" (prefixo dos ossos do rig).
    Devolve {nome_do_osso: quaternion local}.
    """
    lm = np.asarray(lm_world, np.float64)
    # MediaPipe: y para baixo e z para a camera -> nosso referencial (y-up, +Z frente)
    lm = np.stack([lm[:, 0], -lm[:, 1], -lm[:, 2]], axis=1)
    base = _hand_frame(lm)
    rot: dict[str, np.ndarray] = {}

    def add(finger: str, joints: tuple[int, int, int, int], thumb: bool) -> None:
        a, b, c, d = joints
        if thumb:
            pares = [(0, 1, a, b), (0, 2, b, c), (0, 3, c, d)]
        else:
            pares = [(0, 1, a, b), (0, 2, b, c), (0, 3, c, d)]
        for idx, _i, p, q in pares:
            bone = f"{side}Hand{finger}{idx}"
            if bone not in mx.BONE_INDEX:
                continue
            d0 = mx.BONE_OFFSET[bone]                     # direcao de rest (frame do pai)
            n0 = np.linalg.norm(d0)
            if n0 < 1e-9:
                continue
            d_world = lm[q] - lm[p]
            nd = np.linalg.norm(d_world)
            if nd < 1e-9:
                continue
            d_local = base @ (d_world / nd)               # para o frame da mao
            rot[bone] = mx.quat_from_to(d0 / n0, d_local)

    for finger, joints in FINGERS.items():
        add(finger, joints, thumb=False)
    add("Thumb", THUMB, thumb=True)
    return rot


def finger_rest_bones(side: str) -> list[str]:
    nomes = [f"{side}Hand{f}{i}" for f in list(FINGERS) + ["Thumb"] for i in (1, 2, 3)]
    return [n for n in nomes if n in mx.BONE_INDEX]


# ---------------------------------------------------------------------------
# Detector (MediaPipe Tasks HandLandmarker)
# ---------------------------------------------------------------------------
@dataclass
class HandFrame:
    left: np.ndarray | None = None      # (21,3) world landmarks
    right: np.ndarray | None = None


class HandTracker:
    def __init__(self, model_path: str | Path | None = None, num_hands: int = 2,
                 min_conf: float = 0.3):
        # limiar baixo por padrao: maos sao pequenas/parciais em video de corpo.
        # O modelo padrao e o HandLandmarker do MediaPipe; um .task compativel
        # pode ser apontado via V2M_HAND_MODEL (ambiente) ou model_path.
        import os

        self.model_path = Path(model_path or os.environ.get("V2M_HAND_MODEL")
                               or DEFAULT_HAND_MODEL)
        self.num_hands = num_hands
        self.min_conf = float(min_conf)
        self._landmarker = None
        self._ts_ms = 0

    @staticmethod
    def is_available() -> bool:
        try:
            import mediapipe as mp  # noqa: F401
            from mediapipe.tasks import python  # noqa: F401
            from mediapipe.tasks.python import vision  # noqa: F401
        except Exception:
            return False
        return True

    def _ensure_model(self) -> Path:
        p = self.model_path
        if not p.is_absolute():
            p = Path.cwd() / p
        if not p.exists() or p.stat().st_size == 0:
            import urllib.request

            p.parent.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(HAND_MODEL_URL, p)
        return p

    def load(self) -> None:
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision

        modelo = self._ensure_model()
        opts = vision.HandLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(modelo)),
            running_mode=vision.RunningMode.VIDEO,
            num_hands=int(self.num_hands),
            min_hand_detection_confidence=float(self.min_conf),
            min_hand_presence_confidence=float(self.min_conf),
            min_tracking_confidence=float(self.min_conf),
        )
        self._landmarker = vision.HandLandmarker.create_from_options(opts)
        self._ts_ms = 0

    def detect(self, frames, fps: float = 30.0, upscale: float = 1.0) -> list[HandFrame]:
        """Detecta maos em cada frame. Devolve uma lista de HandFrame.

        `upscale` aumenta o frame antes de detectar — maos pequenas no video
        (com a camera longe) so sao encontradas assim. Paga caro em desempenho.
        """
        import cv2
        import mediapipe as mp

        if self._landmarker is None:
            self.load()
        dt_ms = max(1, int(1000.0 / max(fps, 1e-6)))
        out: list[HandFrame] = []
        for frame in frames:
            if upscale and upscale != 1.0:
                h, w = frame.shape[:2]
                frame = cv2.resize(frame, (int(w * upscale), int(h * upscale)),
                                   interpolation=cv2.INTER_CUBIC)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            self._ts_ms += dt_ms
            img = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
            res = self._landmarker.detect_for_video(img, self._ts_ms)
            hf = HandFrame()
            wl = getattr(res, "hand_world_landmarks", None)
            handed = getattr(res, "handedness", None) or []
            if wl is not None and len(wl) > 0:
                for i, pts in enumerate(wl):
                    label = "Right"
                    if i < len(handed) and handed[i]:
                        label = handed[i][0].category_name or "Right"
                    arr = np.array([[p.x, p.y, p.z] for p in pts], np.float64)
                    if label.lower().startswith("l"):
                        hf.left = arr
                    else:
                        hf.right = arr
            out.append(hf)
        return out


def apply_hands(anim, hands: list[HandFrame], *, mirror: bool = True):
    """Escreve as rotacoes dos dedos na Animation (so nos dedos)."""
    from core.retarget import Animation

    rotations = {k: np.asarray(v, np.float64).copy() for k, v in anim.rotations.items()}
    aplicados = 0
    for t, hf in enumerate(hands[: anim.num_frames]):
        pares = (("Left", hf.left), ("Right", hf.right))
        for side, lm in pares:
            if lm is None:
                continue
            lado = side if mirror else ("Right" if side == "Left" else "Left")
            try:
                rots = finger_rotations_from_landmarks(lm, lado)
            except Exception:  # noqa: BLE001
                continue
            for bone, q in rots.items():
                if bone in rotations:
                    rotations[bone][t] = q
                    aplicados += 1

    out = Animation(
        fps=anim.fps, num_frames=anim.num_frames, bone_names=list(anim.bone_names),
        rotations=rotations, root_translation=np.asarray(anim.root_translation).copy(),
        meta={**dict(getattr(anim, "meta", {}) or {}),
              "hands": {"frames": len(hands), "rotations_applied": aplicados,
                        "mirror": mirror}},
    )
    return out, {"frames_with_hands": sum(1 for h in hands if h.left is not None or h.right is not None),
                 "rotations_applied": aplicados, "mirror": mirror}
