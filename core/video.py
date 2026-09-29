"""Leitura de video (frames BGR) com OpenCV."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class VideoInfo:
    frames: list[np.ndarray]
    fps: float
    width: int
    height: int

    @property
    def count(self) -> int:
        return len(self.frames)


def read_video(path: str, max_frames: int | None = None, resize_max: int | None = 960) -> VideoInfo:
    """Le um video e devolve frames BGR (numpy HxWx3).

    resize_max: largura/altura maxima (downscale mantendo aspecto) para acelerar.
    """
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"nao foi possivel abrir o video: {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frames: list[np.ndarray] = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if resize_max:
            h, w = frame.shape[:2]
            m = max(h, w)
            if m > resize_max:
                scale = resize_max / m
                frame = cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        frames.append(frame)
        if max_frames and len(frames) >= max_frames:
            break
    cap.release()
    if not frames:
        raise RuntimeError(f"nenhum frame lido de {path}")
    h, w = frames[0].shape[:2]
    return VideoInfo(frames=frames, fps=float(fps), width=int(w), height=int(h))
