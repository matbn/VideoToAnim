# video2mixamo

## Português

Este documento também está disponível em português: [README.pt-BR.md](README.pt-BR.md).

**Local** tool that takes a video, extracts the pose with a **backend selectable
from a dropdown**, performs **retargeting to the Mixamo skeleton** and delivers a
3D animation you can preview and export as **GLB** and **FBX**.

- Local web UI (FastAPI + three.js SPA) with backend dropdown, upload, job
  status panel, 3D preview (play/pause, timeline, cameras) and downloads.
- Extensible **Adapter** architecture: a new backend plugs in through
  configuration/plugin and shows up in the dropdown by itself, with no core edits.
- Backends **free for commercial use**: **ViTPose (default)** and MediaPipe
  (Apache-2.0), plus a deterministic `synthetic` backend for tests.
  Backends with commercial conflicts were removed (see `docs/LICENSING.md`).

## Requirements

- Python 3.11+ (tested with 3.13)
- Windows, Linux or macOS. NVIDIA GPU is optional: the core runs on CPU.
- For the 3D preview, the browser loads three.js from a CDN (needs internet on
  first load; the `web/` folder can be served locally if you prefer).

## Setup (once)

```powershell
cd video2mixamo
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Run (one command)

```powershell
.\.venv\Scripts\python.exe run.py
# open http://127.0.0.1:8000
```

On Linux/macOS use `.venv/bin/python run.py`.

## Usage

1. Pick a backend in the dropdown — each one is labelled with its **license
   category** (`free` / `non-commercial` / `separate license`, with a color). The
   **effective** default is ViTPose **if it is available**; without the weights, the UI
   pre-selects the first available backend and shows the reason at the top.
2. Upload a video (mp4/mov).
3. (Optional) Upload a **Mixamo mesh** (`.fbx` or `.glb`) to replace the “capsule sticks”.
   Its skeleton is checked against the 65-bone contract and the UI **warns** if it is
   incompatible. **FBX is read directly** (Mixamo standard, no conversion) — see `docs/MESH.md`.
4. Adjust parameters (output fps, max frames, smoothing, 2D→3D lifter).
5. Click **Process video** and follow the job log.
6. When it finishes, the screen shows **three panels side by side** — **reference video ×
   skeleton (Mixamo rig) × mesh** — with a shared timeline (play/pause and scrub control all
   three). If the job has no uploaded mesh, panel 3 shows the **skeleton in a second view**
   instead. There are camera presets (`perspective / front / back / side / top`, applied to
   both 3D panels) and download buttons for **GLB** and **FBX**.

## Backends

| id | name in the dropdown | category | type | installation |
|----|----------------------|----------|------|--------------|
| `vitpose` | ViTPose (ONNX, CPU) — **default** | **free** (Apache-2.0) | top-down heatmap, COCO-17 | `pip install onnxruntime` (~83 MB model on 1st run) |
| `mediapipe` | MediaPipe Pose (BlazePose) | **free** (Apache-2.0) | one-shot, 33→COCO-17 | `pip install mediapipe` |
| `rtmpose` | RTMPose (rtmlib, ONNX) | **free** (Apache-2.0) | top-down, COCO-17 | `pip install rtmlib --no-deps` |
| `motionbert` | MotionBERT | **free** (Apache-2.0) | 2D→3D lifting (H36M-17) | torch + weights from the repo |
| `yolopose` | YOLO-pose (Ultralytics) | **separate license** (AGPL-3.0) | one-shot, COCO-17 | `pip install ultralytics` |
| `sam3dbody` | SAM 3D Body (Meta, MHR) | **separate license** (SAM License) | single-image 3D mesh | official package/checkpoint |
| `mhformer` | MHFormer | **separate license** (unlicensed weights) | temporal lifting | repo + weights |
| `wham` | WHAM (SMPL) | **non-commercial** (SMPL) | world-grounded mesh | repo + SMPL body |
| `openpose` | OpenPose (BODY_25) | **non-commercial** | bottom-up multi-person | C++ build |
| `simplebaseline` | SimpleBaseline | **non-commercial** (weights) | top-down heatmap | repo + checkpoint |

> The **license category** appears in the dropdown (with a color) and in the job panel. Full
> matrix and the meaning of each category: **`docs/LICENSING.md`**. The `synthetic` backend
> (test infrastructure) stays hidden.

Installation details, licenses and quirks: `docs/BACKENDS.md`.

## Automatic backend installation

No bare "unavailable": each backend declares an **installation plan** that runs on first use
or via the **"Install automatically"** button in the dropdown. Everything happens **inside the
project** (current venv, `models/` and `third_party/`) — the system is left untouched.

| backend | installation | what it downloads |
|---|---|---|
| `vitpose` | **automatic** | onnxruntime + quantized model (~98 MB) |
| `mediapipe` | **automatic** | package + `.task` model (~99 MB) |
| `rtmpose` | **automatic** | `rtmlib` (~1 MB; 48 MB ONNX on 1st inference) |
| `simplebaseline` | **automatic** (code) | `git clone` of the repo (checkpoint is a manual step) |
| `motionbert` | automatic w/ big deps | torch CPU (~124 MB) + repo clone |
| `mhformer` | automatic w/ big deps | torch CPU (~124 MB) + repo clone |
| `yolopose` | automatic w/ big deps | `ultralytics` (~2.5 GB, drags PyTorch along) |
| `openpose` | **manual** | requires a C++ build (CMake/CUDA) — no Python package |
| `sam3dbody` | **manual** | Meta package/checkpoint with its own terms |
| `wham` | **manual** | depends on the SMPL body (Max Planck license, separate acceptance) |

The three "manual" ones are not laziness: there is no automatable installation path (native
build, license-gated download or unpublished package). The dropdown shows the exact reason,
and the API returns `400` explaining why instead of failing silently.

Equivalent CLI/API:

```powershell
# in the UI: pick the backend and click "Install automatically"
curl -X POST http://127.0.0.1:8000/api/backends/vitpose/install
curl http://127.0.0.1:8000/api/backends/vitpose/install    # status + log
```

## Automatic GPU and hand tracking

### The right GPU, for the right backend

The system detects the hardware and picks the accelerator **and the requirement
variant** automatically — it never installs CPU when a compatible GPU exists,
and never fakes GPU when the GPU does not fit:

| situation | what happens |
|---|---|
| **NVIDIA** + ONNX backend (vitpose, rtmpose) | installs `onnxruntime-gpu` **+ `nvidia-cudnn-cu12` + `nvidia-cublas-cu12`** and uses `CUDAExecutionProvider` |
| **AMD/Intel on Windows** + ONNX backend | installs `onnxruntime-directml` and uses `DmlExecutionProvider` |
| **AMD** + PyTorch backend on Windows | warns that official PyTorch does not ship ROCm for Windows — proceeds on CPU **explaining why** |
| **Apple Silicon** | `onnxruntime-silicon` / MPS |
| no compatible GPU | CPU |
| **MediaPipe** (any GPU) | stays on CPU: the pip desktop wheel is built without GPU support (`GPU processing is disabled in build flags`, confirmed on mediapipe 1.0.1); even so, inference is fast (~13 ms/frame) |

Two details we found in practice and that the code handles:

* `onnxruntime-gpu` **does not bundle cuDNN** — without `cudnn64_9.dll` the CUDA provider fails
  at runtime (`NOT_IMPLEMENTED`). Provisioning installs cuDNN via pip and the backend
  registers the DLLs on the process `PATH` (`add_dll_directory` is not enough: cuDNN resolves
  its sub-DLLs through the standard Windows search);
* if the GPU fails **during inference** (driver/libs), the backend falls back to CPU automatically
  and records the reason — previously this returned empty poses silently, which is worse than the error.

Measured on this machine (RTX 5060 Laptop): **8.0 ms/frame on GPU** vs **77 ms/frame on CPU**
with the quantized model — ~10× faster, with the same score (0.80).

> Useful curiosity: the **int8 quantized** model is great on CPU and *worse* on GPU. Provisioning
> downloads the right variant for each case (fp32 on GPU, int8 on CPU).

### Hand tracking

COCO-17 has no hand joints, so during body retargeting the **40 finger bones** used to stay
still. With the **“hand tracking”** checkbox the pipeline downloads `hand_landmarker.task`
(~8 MB, Apache-2.0), detects the hands (21 points each) and fills in the fingers:

```
index 5-6-7-8 · middle 9-10-11-12 · ring 13-14-15-16 · pinky 17-18-19-20 · thumb 1-2-3-4
                        -> HandIndex1..3 / HandMiddle1..3 / ... (the 4th is the tip)
```

The phalanx directions are converted into the hand's frame (built from the wrist, the index MCP
and the pinky MCP) and become local rotations for the finger bones.

**Practical tip:** small hands in the video (camera far away) are not found at the default
threshold — the `min_conf` parameter (default 0.3) controls that; on the test video, lowering
it to 0.2 made the hand appear. There is also `upscale` in the detector, for extreme cases.

## Head: stable orientation (eyes + frame 0)

Three sources of instability were eliminated in head retargeting:

1. **Nose depth**: the rigid-distance solver had a singularity near the threshold
   (`sqrt(L²−d²)`): when the 2D shoulder→nose distance fell slightly below 0.28 m, the nose
   "dove" by up to ~0.19 m **in a single frame** — the head picked up spurious yaw/pitch of
   ~30°. The nose now stays on the chest plane.
2. **Orientation from the eyes**: the `nose − shoulders` vector measures position, not
   orientation — when the body moves within the frame (crouching, turning), the head turned
   along (up to 40° of roll with the video still). The head direction now comes from the
   **eye line** (translation-invariant), with adaptive smoothing and guards for
   occlusion/profile.
3. **Frame-0 neutralization**: besides zeroing the local Neck/Head rotations on the reference
   frame, calibration now also neutralizes in WORLD space — the head starts exactly at the
   rest orientation (facing forward).

Measured (sword_swing A/B, 60 frames): visible head tilt of **40° → 0.7°** (A; the video
itself measures 0.9°) and **40° → 10°** (B, concentrated in the fast turn; mean 1.5°).
Controllable via `params.head_calibration` (default `true`); the result appears in the job log
and in the `anim.json` `meta`.

## Language (PT/EN) and camera sync

**Two languages, one interface.** The pipeline and the refine editor speak Portuguese and
English: the `PT | EN` selector sits at the top of both pages. The initial language comes from
`?lang=`, the last choice (localStorage) or the browser; switching reloads the page (live state
stays in the URL).

**Content** shown in the UI is localized too: filter descriptions (`description_en`) and joint
notes (`note_en`, all 32 in the humanoid preset). Note: the job log now follows the UI
language; API error messages still stay in Portuguese for now.

**sync rotation** (on both pages): a checkbox that syncs the camera between the **skeleton and
mesh** panels — any orbit/zoom/camera change on one reflects on the other, live and in both
directions; the preference is remembered across sessions.

**The refine editor preview uses the job's MESH** (the one processed by the pipeline, not the
"capsule sticks") and also shows the **reference video** (the same job artifact) in the left
panel, following the timeline: the GLB served by the editor and the refined export embed the
user mesh.

## Job history

Every processing run is recorded in SQLite (`storage/jobs.db`) — id, video, backend,
parameters, status, timestamps, log, metrics and artifacts. The UI has a
**History** tab (next to "New job") with filters by **status** and **backend**, plus:

* **open** a job in the viewer without reprocessing (video + GLB loaded on the spot);
* download **GLB/FBX** straight from the row;
* **refine** → opens the editor with that clip already selected (`/refine?job=<id>`).

Via API: `GET /api/jobs?status=done&backend=vitpose&limit=50`.

## Animation refinement

The main page already exposes, right in the form, the **stabilization dropdown** (the six
filters, coming from the API) and the **joint constraints toggle** — the choice enters the
job as `params.refine`. The **“refine editor →”** link at the top opens the full editor.

After generating a clip, http://127.0.0.1:8000/refine opens the **refinement editor** with
three tools:

1. **Stabilization** — six filters (one_euro, moving_average, savgol, kalman, butterworth,
   double_exponential), configurable per bone, per axis and per frame range
   (config/filters_default.yaml).
2. **Constraints** — humanoid preset of joint limits (head, shoulders, elbows without
   hyperextension, knees, hips) in config/constraints_humanoid.yaml, editable from the UI.
3. **Bone editor** — pick bone + frame + affected range; the system **rebakes every frame**
   interpolating from the last unaffected frame up to the edited frame (no jump, and without
   touching frames outside the range), with undo/redo history persisted across sessions.

Full guide: [docs/REFINE.md](docs/REFINE.md) · API: [docs/REFINE_API.md](docs/REFINE_API.md) ·
version history: [CHANGELOG.md](CHANGELOG.md).

It can also be run from the CLI or inside the pipeline:

```powershell
.\.venv\Scripts\python.exe tools\refine.py --make-samples
```

```json
{fps: 30, refine: {constraints: config/constraints_humanoid.yaml,
                       filters: config/filters_default.yaml}}
```

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

They cover: the Adapter contract for every registered backend, plugin discovery,
the Mixamo skeleton contract (65 bones + hierarchy), rigidity and reverse FK
of the retarget, GLB structure (validated with `pygltflib`) and ASCII FBX,
job persistence and an end-to-end pipeline with a sample video.

## Architecture

```
upload → pre-processing → pose backend (Adapter) → post/stabilization
      → 2D→3D lifter (if needed) → retarget solver → Mixamo skeleton
      → export (GLB + FBX) → three.js preview
```

Details: `docs/ARCHITECTURE.md`.

## Adding a backend

See `docs/ADDING_A_BACKEND.md`. Summary: create `plugins/my_backend.py` exposing
`BACKEND` (an instance of `ConfigurableAdapter` with your `AdapterConfig`).
It shows up in the dropdown on the next load — with no core or frontend edits.

## Limitations

See `docs/LIMITATIONS.md` (single-person video, no absolute root motion,
heavy backends require their own setup, ASCII FBX without mesh). Licenses and
removed backends: `docs/LICENSING.md`.
