"""Esqueleto canonico COCO-17 e estruturas de pose comuns a todos os backends.

Todo backend de pose, independente do formato nativo, deve produzir FramePose
com keypoints 2D no esqueleto COCO-17 (pixel absoluto na imagem original,
y para baixo) e score em [0, 1] por junta. Opcionalmente pode produzir kp3d
em metros, y-up, com origem no mid-hip.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# Ordem canonica COCO-17 (identica ao COCO keypoints oficial)
COCO17 = [
    "nose",
    "left_eye",
    "right_eye",
    "left_ear",
    "right_ear",
    "left_shoulder",
    "right_shoulder",
    "left_elbow",
    "right_elbow",
    "left_wrist",
    "right_wrist",
    "left_hip",
    "right_hip",
    "left_knee",
    "right_knee",
    "left_ankle",
    "right_ankle",
]
COCO_INDEX = {name: i for i, name in enumerate(COCO17)}
NUM_JOINTS = len(COCO17)

# Arestas para visualizacao/esqueletizacao
COCO_EDGES = [
    (0, 1), (0, 2), (1, 3), (2, 4),
    (5, 6), (5, 7), (7, 9), (6, 8), (8, 10),
    (5, 11), (6, 12), (11, 12),
    (11, 13), (13, 15), (12, 14), (14, 16),
]

# Nomes "amigaveis" para exibicao
COCO_LABELS_PT = {
    "nose": "nariz", "left_eye": "olho_esq", "right_eye": "olho_dir",
    "left_ear": "orelha_esq", "right_ear": "orelha_dir",
    "left_shoulder": "ombro_esq", "right_shoulder": "ombro_dir",
    "left_elbow": "cotovelo_esq", "right_elbow": "cotovelo_dir",
    "left_wrist": "punho_esq", "right_wrist": "punho_dir",
    "left_hip": "quadril_esq", "right_hip": "quadril_dir",
    "left_knee": "joelho_esq", "right_knee": "joelho_dir",
    "left_ankle": "tornozelo_esq", "right_ankle": "tornozelo_dir",
}


@dataclass
class FramePose:
    """Pose de um unico frame no esqueleto canonico.

    kp2d:  (17, 2) float32, pixel absoluto na imagem original, y para baixo.
    score: (17,)  float32 em [0, 1].
    kp3d:  (17, 3) float32 em metros, y-up, origem no mid-hip (opcional).
    """

    kp2d: np.ndarray
    score: np.ndarray
    kp3d: np.ndarray | None = None
    width: int = 0
    height: int = 0
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.kp2d = np.asarray(self.kp2d, dtype=np.float32).reshape(NUM_JOINTS, 2)
        self.score = np.asarray(self.score, dtype=np.float32).reshape(NUM_JOINTS)
        if self.kp3d is not None:
            self.kp3d = np.asarray(self.kp3d, dtype=np.float32).reshape(NUM_JOINTS, 3)

    def as_dict(self) -> dict:
        d = {
            "kp2d": self.kp2d.tolist(),
            "score": self.score.tolist(),
            "width": int(self.width),
            "height": int(self.height),
        }
        if self.kp3d is not None:
            d["kp3d"] = self.kp3d.tolist()
        return d

    @property
    def mean_score(self) -> float:
        return float(np.mean(self.score)) if self.score.size else 0.0


def empty_pose(width: int = 0, height: int = 0) -> FramePose:
    return FramePose(
        kp2d=np.zeros((NUM_JOINTS, 2), np.float32),
        score=np.zeros(NUM_JOINTS, np.float32),
        kp3d=None,
        width=width,
        height=height,
    )


def mid_hip(kp: np.ndarray) -> np.ndarray:
    """Ponto medio entre os quadris."""
    return 0.5 * (kp[COCO_INDEX["left_hip"]] + kp[COCO_INDEX["right_hip"]])


def mid_shoulder(kp: np.ndarray) -> np.ndarray:
    """Ponto medio entre os ombros."""
    return 0.5 * (kp[COCO_INDEX["left_shoulder"]] + kp[COCO_INDEX["right_shoulder"]])
