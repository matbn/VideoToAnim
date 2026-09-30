# Backends

## Português

Este documento também está disponível em português: [BACKENDS.pt-BR.md](BACKENDS.pt-BR.md).

Canonical skeleton for all of them: **COCO-17**, absolute pixels, y down, score [0,1].
Each one's **license category** appears in the dropdown (see `docs/LICENSING.md`).

The dropdown shows 10 backends; `synthetic` stays hidden (test infra).

## `livre` — commercial use allowed

### `vitpose` — ViTPose (**default**)

| | |
|---|---|
| Type | top-down, heatmap, single-frame |
| Install | **`pip install onnxruntime`** — the model (~83 MB) is downloaded on first run |
| Engine | ONNX Runtime on CPU (**~60 ms/frame** measured) |
| Detector | none — uses the whole frame (accepts `params.bbox`) |
| License | **Apache-2.0** |

> **Why ONNX and not MMPose:** `mmcv` does not publish a wheel for Python 3.13 (measured with
> `pip download mmcv --only-binary=:all:` → *No matching distribution found*). The MMPose path
> remains available as legacy (`params.engine = "mmpose"`) on Python ≤ 3.12.

### `mediapipe` — MediaPipe Pose (BlazePose)

Single-person detector+tracker. 33 points → COCO-17. `pose_world_landmarks` gives 3D in meters
(skips the lifter). **Apache-2.0.**

### `rtmpose` — RTMPose (rtmlib, ONNX)

| | |
|---|---|
| Type | top-down "light" SoTA, COCO-17 native |
| Install | **`pip install rtmlib --no-deps`** (uses the already-installed onnxruntime) |
| Model | official ONNX (~48 MB) downloaded on first run |
| Performance | **~200 ms/frame** on CPU (measured) |
| License | **Apache-2.0** (RTMPose/OpenMMLab) · `rtmlib` MIT |

### `motionbert` — MotionBERT (2D→3D lifting)

Unified backbone (ICCV 2023), a reference in lifting. H36M-17, temporal window.
**Apache-2.0** (code and weights from the official repo). Requires torch + weights.

## `licenca_a_parte` — assess before using

| backend | why |
|---|---|
| `yolopose` (Ultralytics) | **AGPL-3.0**: strong copyleft; a product/service requires open-sourcing or an Enterprise license |
| `sam3dbody` (Meta, MHR) | **SAM License**: commercial use allowed, but share-alike + export/military restrictions |
| `mhformer` | MIT code, **weights with no declared license** (all rights reserved by default) |

## `nao_comercial` — research only

| backend | why |
|---|---|
| `openpose` | CMU LICENSE: *"ACADEMIC OR NON-PROFIT ORGANIZATION NONCOMMERCIAL RESEARCH USE ONLY"* (code **and** weights) |
| `simplebaseline` | MIT code, but *"All models are provided for research purpose"* |
| `wham` | MIT code, but depends on **SMPL/SMPL-X** (Max Planck non-commercial license) |

## Hidden

`synthetic` — generated from the Mixamo rig itself, deterministic, no dependencies. **MIT** (this
project). It stays out of the dropdown (`hidden_from_ui`) but remains in the registry for the tests;
the backend-matrix test uses it as the Adapter contract reference.

## Runtime state on this machine

| backend | available | note |
|---|---|---|
| `vitpose` | ✅ | runs (ONNX, CPU) |
| `mediapipe` | ✅ | runs (Tasks API, CPU) |
| `rtmpose` | ✅ | runs (rtmlib, CPU) |
| `motionbert` | ❌ | requires torch + weights |
| `yolopose` | ❌ | requires `pip install ultralytics` |
| `sam3dbody` | ❌ | requires the official package/checkpoint |
| `wham` | ❌ | requires repo + SMPL body |
| `openpose` | ❌ | requires a C++ build |
| `simplebaseline` | ❌ | requires repo + checkpoint |
| `mhformer` | ❌ | requires repo + weights |

The unavailable ones appear in the dropdown with the exact reason — and all of them cross the pipeline
in the matrix test (`tests/test_backend_matrix.py`), which exercises each adapter's **real mapping**.
