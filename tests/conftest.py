"""Fixtures compartilhadas dos testes."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.registry import build_registry  # noqa: E402


@pytest.fixture(scope="session")
def project_root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def registry():
    return build_registry(ROOT / "plugins")


@pytest.fixture(scope="session")
def dummy_frames():
    return [np.zeros((480, 640, 3), np.uint8) for _ in range(24)]


@pytest.fixture(scope="session")
def synthetic_poses(dummy_frames):
    from backends.synthetic import SyntheticBackend
    from core.smoothing import smooth_frames

    b = SyntheticBackend()
    ctx = {"width": 640, "height": 480, "fps": 30.0}
    frames = b.infer_video(dummy_frames, ctx)
    return smooth_frames(frames, method="oneeuro")


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory):
    from tools.make_sample_video import make_video

    d = tmp_path_factory.mktemp("video")
    return make_video(d / "sample.mp4", frames=24, size=(320, 240), fps=12)
