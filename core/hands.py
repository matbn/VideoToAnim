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

Mapeamento matematico (o rig compoe rot[osso] = rot[pai] * q; a direcao de um
subtree = rot * offset_do_filho, com offsets de rest no frame do pai):

    q1 = from_to(o2, rh^-1 . t1)              # osso 1 (MCP): falange MCP->PIP
    q2 = from_to(o3, (rh . q1)^-1 . t2)       # osso 2 (PIP): falange PIP->DIP
    q3 = from_to(o4, (rh . q1 . q2)^-1 . t3)  # osso 3 (DIP): falange DIP->TIP

onde oI = BONE_OFFSET do osso FILHO (o rest da falange), tI = direcao observada
no frame canonico do video (y-up, +Z frente — mesma conversao do backend) e
rh = rotacao MUNDO da mao vinda do retarget do corpo NAQUELE frame. A versao
antiga ignorava rh e montava um "referencial da mao" com os proprios landmarks
(sem relacao com o frame do rig) — alem do indice do osso ser sempre 0
("HandXxxxx0" nao existe), o que descartava tudo em silencio.
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


def _hand_world_rot(rotations: dict, t: int, side: str) -> np.ndarray:
    """Rotacao MUNDO do osso {side}Hand no frame `t` (produto dos locais ate a raiz)."""
    chain = []
    p = f"{side}Hand"
    while p is not None:
        chain.append(p)
        p = mx.BONE_PARENT.get(p)
    q = np.array([0.0, 0.0, 0.0, 1.0])
    for b in reversed(chain):
        series = rotations.get(b)
        if series is None:
            continue
        q = mx.quat_mul(q, np.asarray(series[t], np.float64))
    return q


def _to_canonical(lm: np.ndarray) -> np.ndarray:
    """World landmarks do MediaPipe -> frame canonico (y-up, +Z frente)."""
    lm = np.asarray(lm, np.float64)
    return np.stack([lm[:, 0], -lm[:, 1], -lm[:, 2]], axis=1)


def finger_rotations_from_landmarks(lm_world: np.ndarray, side: str,
                                    hand_world_rot: np.ndarray | None = None
                                    ) -> dict[str, np.ndarray]:
    """Rotacoes LOCAIS dos dedos a partir dos world landmarks (21,3).

    `side` e "Left"/"Right" (prefixo dos ossos). `hand_world_rot` e a rotacao
    MUNDO da mao vinda do retarget do corpo no mesmo frame; e ela que amarra o
    frame canonico do video ao referencial do rig (sem ela, assume identidade —
    so para testes isolados).
    """
    lm = _to_canonical(lm_world)
    rh = np.array([0.0, 0.0, 0.0, 1.0]) if hand_world_rot is None \
        else np.asarray(hand_world_rot, np.float64)
    nr = np.linalg.norm(rh)
    if nr > 0:
        rh = rh / nr
    rot: dict[str, np.ndarray] = {}

    def add(finger: str, joints: tuple[int, int, int, int]) -> None:
        j = list(joints)
        q_prev = rh          # rotacao MUNDO acumulada do PAI do osso atual
        for idx in (1, 2, 3):
            bone = f"{side}Hand{finger}{idx}"
            if bone not in mx.BONE_INDEX:
                break
            child = f"{side}Hand{finger}{idx + 1}"
            rest = np.asarray(mx.BONE_OFFSET.get(child, mx.BONE_OFFSET[bone]), np.float64)
            n0 = np.linalg.norm(rest)
            d_alvo = lm[j[idx]] - lm[j[idx - 1]]
            nd = np.linalg.norm(d_alvo)
            if n0 < 1e-9 or nd < 1e-9:
                continue
            t_local = mx.quat_rotate(mx.quat_conj(q_prev), d_alvo / nd)
            q = mx.quat_from_to(rest / n0, t_local)
            rot[bone] = q
            q_prev = mx.quat_mul(q_prev, q)

    for finger, joints in FINGERS.items():
        add(finger, joints)
    add("Thumb", THUMB)
    return rot
def finger_rest_bones(side: str) -> list[str]:
    nomes = [f"{side}Hand{f}{i}" for f in list(FINGERS) + ["Thumb"] for i in (1, 2, 3)]
    return [n for n in nomes if n in mx.BONE_INDEX]


def hands_debug_payload(hfs, width: int, height: int, fps: float = 30.0) -> dict:
    """Payload do overlay de debug: landmarks em PIXELS do video.

    Uma linha por frame: {"left": [[x, y] x21] | null, "right": ...}.
    """
    rows = []
    for hf in hfs:
        row = {}
        for side in ("left", "right"):
            img = getattr(hf, side + "_img", None)
            if img is None:
                row[side] = None
                continue
            row[side] = [[round(float(p[0]) * width, 2), round(float(p[1]) * height, 2)]
                         for p in np.asarray(img, np.float64)]
        rows.append(row)
    return {"fps": float(fps), "width": int(width), "height": int(height),
            "frames": rows}


# ---------------------------------------------------------------------------
# Detector (MediaPipe Tasks HandLandmarker)
# ---------------------------------------------------------------------------
@dataclass
class HandFrame:
    left: np.ndarray | None = None      # (21,3) world landmarks
    right: np.ndarray | None = None
    left_img: np.ndarray | None = None  # (21,3) landmarks normalizados da imagem
    right_img: np.ndarray | None = None


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
            hl = getattr(res, "hand_landmarks", None)
            handed = getattr(res, "handedness", None) or []
            if wl is not None and len(wl) > 0:
                for i, pts in enumerate(wl):
                    label = "Right"
                    if i < len(handed) and handed[i]:
                        label = handed[i][0].category_name or "Right"
                    arr = np.array([[p.x, p.y, p.z] for p in pts], np.float64)
                    img = None
                    if hl is not None and i < len(hl):
                        img = np.array([[p.x, p.y, p.z] for p in hl[i]], np.float64)
                    if label.lower().startswith("l"):
                        hf.left = arr
                        hf.left_img = img
                    else:
                        hf.right = arr
                        hf.right_img = img
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
                rh = _hand_world_rot(rotations, t, lado)
                rots = finger_rotations_from_landmarks(lm, lado, hand_world_rot=rh)
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
