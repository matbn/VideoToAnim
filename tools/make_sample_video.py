"""Gera um video de amostra sintetico (pessoa stylizada em movimento)."""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np


def make_video(path: str | Path, frames: int = 60, size: tuple[int, int] = (640, 480), fps: int = 30) -> str:
    import cv2

    w, h = size
    path = str(path)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(path, fourcc, fps, (w, h))
    if not writer.isOpened():
        path = str(Path(path).with_suffix(".avi"))
        writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"MJPG"), fps, (w, h))
    if not writer.isOpened():
        raise RuntimeError("nao foi possivel abrir o VideoWriter (codec indisponivel)")
    try:
        for t in range(frames):
            img = np.full((h, w, 3), 235, np.uint8)
            ph = 2 * np.pi * t / max(frames, 1)
            cx = w // 2 + int(15 * np.sin(ph))
            hip = (cx, int(h * 0.60))
            head = (cx, int(h * 0.18))
            cv2.circle(img, head, 26, (60, 60, 60), -1)
            cv2.line(img, head, (cx, int(h * 0.15)), (60, 60, 60), 6)
            cv2.line(img, (cx, int(h * 0.24)), hip, (90, 90, 90), 10)
            for sgn in (-1, 1):
                sh = (cx + sgn * 44, int(h * 0.30))
                el = (cx + sgn * (70 + int(18 * np.sin(ph))), int(h * 0.48))
                wr = (cx + sgn * (64 + int(30 * np.sin(ph))), int(h * 0.62))
                cv2.line(img, (cx, int(h * 0.26)), sh, (110, 110, 110), 8)
                cv2.line(img, sh, el, (110, 110, 110), 7)
                cv2.line(img, el, wr, (110, 110, 110), 6)
                kn = (cx + sgn * 24, int(h * 0.80))
                an = (cx + sgn * (20 + int(25 * np.sin(ph))), int(h * 0.96))
                cv2.line(img, hip, kn, (90, 90, 90), 9)
                cv2.line(img, kn, an, (90, 90, 90), 8)
            writer.write(img)
    finally:
        writer.release()
    return path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="storage/sample.mp4")
    ap.add_argument("--frames", type=int, default=60)
    args = ap.parse_args()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    print("video gerado em", make_video(args.out, frames=args.frames))
