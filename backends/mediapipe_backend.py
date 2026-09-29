"""Backend MediaPipe Pose (BlazePose) — Apache-2.0.

Suporta as DUAS APIs do MediaPipe:
  * **Tasks API** (moderna, `PoseLandmarker`) — usada quando `mp.solutions.pose`
    nao existe (mediapipe >= 1.0). Requer um arquivo de modelo `.task`
    (baixado automaticamente na primeira execucao, ~10 MB, licenca Apache-2.0).
  * **Solutions API** (legada, `mp.solutions.pose`) — usada quando disponivel
    (mediapipe 0.10.x).

33 landmarks -> COCO-17. Roda em CPU. Quando disponivel, tambem expoe
`pose_world_landmarks` como 3D canonico (metros, y-up).
"""
from __future__ import annotations

import os
import urllib.request
from pathlib import Path
from typing import Sequence

import numpy as np

from core.adapter import AdapterConfig, ConfigurableAdapter
from core.canonical import COCO17, FramePose

BLAZE_33 = [
    "nose", "left_eye_inner", "left_eye", "left_eye_outer", "right_eye_inner",
    "right_eye", "right_eye_outer", "left_ear", "right_ear", "mouth_left",
    "mouth_right", "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
    "left_wrist", "right_wrist", "left_pinky", "right_pinky", "left_index",
    "right_index", "left_thumb", "right_thumb", "left_hip", "right_hip",
    "left_knee", "right_knee", "left_ankle", "right_ankle", "left_heel",
    "right_heel", "left_foot_index", "right_foot_index",
]

_BLAZE_TO_COCO = {
    "nose": "nose",
    "left_eye": "left_eye",
    "right_eye": "right_eye",
    "left_ear": "left_ear",
    "right_ear": "right_ear",
    "left_shoulder": "left_shoulder",
    "right_shoulder": "right_shoulder",
    "left_elbow": "left_elbow",
    "right_elbow": "right_elbow",
    "left_wrist": "left_wrist",
    "right_wrist": "right_wrist",
    "left_hip": "left_hip",
    "right_hip": "right_hip",
    "left_knee": "left_knee",
    "right_knee": "right_knee",
    "left_ankle": "left_ankle",
    "right_ankle": "right_ankle",
}

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
    "pose_landmarker_full/float16/latest/pose_landmarker_full.task"
)
DEFAULT_MODEL = Path("models") / "pose_landmarker_full.task"


def _cfg() -> AdapterConfig:
    return AdapterConfig(
        name="mediapipe",
        display_name="MediaPipe Pose (BlazePose)",
        native_layout=BLAZE_33,
        mapping={k: COCO17.index(v) for k, v in _BLAZE_TO_COCO.items()},
        coord="normalized",
        y_flip=False,
        score_map={k: BLAZE_33.index(k) for k in _BLAZE_TO_COCO},
        temporal="frame",
        smoothing="none",
        multi_person=False,
        license="Apache-2.0 (MediaPipe / Google)",
        notes="Single-person. x/y normalizados, y para baixo. visibility como score. "
              "Tasks API (PoseLandmarker) quando mp.solutions.pose nao existe. "
              "Fornece world landmarks (3D) quando disponivel.",
    )


class MediaPipeBackend(ConfigurableAdapter):
    def __init__(self) -> None:
        super().__init__(_cfg())
        self.name = "mediapipe"
        self.display_name = "MediaPipe Pose (BlazePose)"
        self._legacy = None
        self._task = None
        self._model_path: Path | None = None
        self._ts_ms = 0

    # ---- disponibilidade ------------------------------------------------
    @staticmethod
    def _has_legacy() -> bool:
        try:
            import mediapipe as mp

            return hasattr(mp, "solutions") and hasattr(mp.solutions, "pose")
        except Exception:
            return False

    def is_available(self) -> bool:
        try:
            import mediapipe  # noqa: F401
        except Exception:
            return False
        return True

    def availability_reason(self) -> str:
        try:
            import mediapipe  # noqa: F401
        except Exception:
            return "requer 'pip install mediapipe' (Apache-2.0)"
        return ""

    # ---- ciclo de vida --------------------------------------------------
    def _resolve_model(self, config: dict) -> Path:
        raw = config.get("model_path") or os.environ.get("V2M_MEDIAPIPE_MODEL", str(DEFAULT_MODEL))
        path = Path(raw)
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(MODEL_URL, path)  # ~10 MB, Apache-2.0
        return path

    def load(self, config: dict | None = None) -> None:
        config = config or {}
        if self._has_legacy():
            import mediapipe as mp

            self._legacy = mp.solutions.pose.Pose(
                static_image_mode=bool(config.get("static_image_mode", False)),
                model_complexity=int(config.get("model_complexity", 1)),
                min_detection_confidence=float(config.get("min_detection_confidence", 0.5)),
                min_tracking_confidence=float(config.get("min_tracking_confidence", 0.5)),
            )
        else:
            import mediapipe as mp
            from mediapipe.tasks import python as mp_python
            from mediapipe.tasks.python import vision

            self._model_path = self._resolve_model(config)
            base = mp_python.BaseOptions(model_asset_path=str(self._model_path))
            opts = vision.PoseLandmarkerOptions(
                base_options=base,
                running_mode=vision.RunningMode.VIDEO,
                num_poses=1,
                min_pose_detection_confidence=float(config.get("min_detection_confidence", 0.5)),
                min_pose_presence_confidence=float(config.get("min_pose_presence_confidence", 0.5)),
                min_tracking_confidence=float(config.get("min_tracking_confidence", 0.5)),
            )
            self._task = vision.PoseLandmarker.create_from_options(opts)
        self._ts_ms = 0
        self._loaded = True

    def unload(self) -> None:
        self._legacy = None
        self._task = None
        super().unload()

    # ---- inferencia -----------------------------------------------------
    @staticmethod
    def _nonempty(x) -> bool:
        """True se x tem conteudo (evita o erro de 'truth value ambiguous' em arrays)."""
        if x is None:
            return False
        try:
            return len(x) > 0
        except TypeError:
            return bool(x)

    @staticmethod
    def _pack(landmarks):
        kp = np.zeros((len(BLAZE_33), 2), np.float32)
        sc = np.zeros((len(BLAZE_33),), np.float32)
        for i, l in enumerate(landmarks):
            kp[i] = (l.x, l.y)
            vis = getattr(l, "visibility", 1.0)
            sc[i] = float(vis) if vis is not None else 0.0
        return kp, sc

    def infer_raw(self, frames: Sequence[np.ndarray], ctx: dict) -> list[dict]:
        import cv2

        if self._legacy is None and self._task is None:
            self.load({})
        out: list[dict] = []
        fps = float(ctx.get("fps", 30.0)) or 30.0
        dt_ms = max(1, int(1000.0 / fps))

        for frame in frames:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            kp = sc = None
            extra: dict = {}
            if self._legacy is not None:
                res = self._legacy.process(rgb)
                if self._nonempty(getattr(res, "pose_landmarks", None)):
                    kp, sc = self._pack(res.pose_landmarks.landmark)
                    if self._nonempty(getattr(res, "pose_world_landmarks", None)):
                        extra["blaze3d"] = self._world(res.pose_world_landmarks.landmark)
            else:
                import mediapipe as mp

                self._ts_ms += dt_ms
                img = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
                res = self._task.detect_for_video(img, self._ts_ms)
                if self._nonempty(getattr(res, "pose_landmarks", None)):
                    kp, sc = self._pack(res.pose_landmarks[0])
                    if self._nonempty(getattr(res, "pose_world_landmarks", None)):
                        extra["blaze3d"] = self._world(res.pose_world_landmarks[0])
            out.append(None if kp is None else {"kp": kp, "score": sc, "extra": extra})
        return out

    @staticmethod
    def _world(landmarks) -> np.ndarray:
        """World landmarks -> canonical 3D (metros, y-up, +Z = frente).

        O MediaPipe define ``z`` com origem no centro dos quadris e **quanto
        menor, mais perto da camera**. Como o nosso rig olha para +Z quando
        esta de frente (pes na direcao +Z na T-pose), invertemos o sinal de z: 
        assim uma pessoa que encara a camera gera um personagem olhando para
        +Z, e o preset de camera "frente" (camera em +Z) mostra o rosto.
        """
        kp3 = np.zeros((len(BLAZE_33), 3), np.float32)
        for i, l in enumerate(landmarks):
            kp3[i] = (l.x, -l.y, -l.z)  # y para baixo->y-up; camera->+Z
        return kp3

    def postprocess(self, pose: FramePose, frame_index: int, ctx: dict) -> FramePose:
        blaze3d = pose.meta.get("blaze3d")
        if blaze3d is None:
            return pose
        ci = {n: i for i, n in enumerate(COCO17)}
        kp3 = np.zeros((17, 3), np.float32)
        for blaze_name, coco_name in _BLAZE_TO_COCO.items():
            kp3[ci[coco_name]] = blaze3d[BLAZE_33.index(blaze_name)]
        kp3[:, 1] += 0.98  # convencao do projeto: pelvis na altura tipica
        pose.kp3d = kp3
        return pose
