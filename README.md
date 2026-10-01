# video2mixamo

## Português

Este documento também está disponível em português: [README.pt-BR.md](README.pt-BR.md).

**Local** tool that takes a video, extracts the pose with a **backend selectable from a
dropdown**, retargets it to the **Mixamo skeleton** and exports a 3D animation as
**GLB** or **FBX**.

- Web UI (FastAPI + three.js) with backend dropdown, upload, job log, 3D preview
  (play/pause, timeline, cameras) and downloads.
- Backends are **plugins**: drop one into `plugins/` and it appears in the dropdown
  on its own, with no core or frontend changes.
- Ships with **commercially free** backends: ViTPose (default), MediaPipe, RTMPose
  and MotionBERT.

## Requirements

- Python 3.11+
- Windows, Linux or macOS. A GPU is optional: everything runs on CPU.
- Internet on first load (three.js comes from a CDN, plus the backend weights).

## Setup and run

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py          # open http://127.0.0.1:8000
```

On Linux/macOS use `.venv/bin/python run.py`.

## Usage

1. Pick a backend in the dropdown. Each one is labelled with its **license category**
   (`free` / `non-commercial` / `separate license`). On first use the UI offers to install
   it automatically — everything stays inside the project folder, the system is untouched.
2. Upload a video (mp4/mov).
3. *(Optional)* Upload a **Mixamo mesh** (`.fbx` or `.glb`) to replace the stick figure.
   The skeleton is checked against the 65-bone contract and the UI warns if it does not match.
4. Adjust the parameters if you want (output fps, max frames, smoothing) and click
   **Process video**.
5. The result shows three panels side by side — **reference video × skeleton × mesh** — on a
   shared timeline, with **GLB** and **FBX** download buttons.

> COCO-17 has no finger joints, so the 40 finger bones stay still. Tick **hand tracking** to
> detect the hands and fill them in.

## Backends

| id | name | license |
|----|------|---------|
| `vitpose` | ViTPose — **default** | **free** (Apache-2.0) |
| `mediapipe` | MediaPipe Pose | **free** (Apache-2.0) |
| `rtmpose` | RTMPose | **free** (Apache-2.0) |
| `motionbert` | MotionBERT (2D→3D) | **free** (Apache-2.0) |
| `yolopose` | YOLO-pose | **separate license** (AGPL-3.0) |
| `sam3dbody` | SAM 3D Body | **separate license** (SAM) |
| `mhformer` | MHFormer | **separate license** (unlicensed weights) |
| `wham` | WHAM (SMPL) | **non-commercial** |
| `openpose` | OpenPose | **non-commercial** |
| `simplebaseline` | SimpleBaseline | **non-commercial** |

A few need a manual install (native build or license-gated download); the dropdown shows the
exact reason instead of failing silently. Details: `docs/BACKENDS.md` · `docs/LICENSING.md`.

## Refining a result

Open `http://127.0.0.1:8000/refine` for the refinement editor: stabilization filters, humanoid
joint constraints, and a per-bone/per-frame editor with undo/redo. Guide: `docs/REFINE.md`.

The editor has **two tabs** — `Refine` and `Mix A/B` — and the playbar sits **outside** the tabs,
so the same playhead drives both: scrubbing in the mix tab moves the A/B panels and the reference
video together.

### Mix A/B — taking body parts from another job

Two backends on the same video tend to go wrong in different places. The `Mix A/B` tab lets you
**take body parts from one job into the clip you have open**, leaving the rest as it is — "I want
B's body and A's arm".

1. Pick the **source** job in the selector above panel A. The target is the job in the editor.
2. Tick the parts: hip/root, torso, head, left/right arm, left/right leg.
3. *(Optional)* **map a frame range**, for when the two jobs are not exactly the same stretch of
   video (e.g. `frames 1–23 of A` applied onto `frames 3–35 of B`).
4. **Apply mix**. There is an **undo** that goes back to the previous clip.

Both panels sit side by side on the **same camera and the same timeline frame**, labelled with the
job ids — so the result shows up immediately.

The parts come from the **rig hierarchy**, not a hand-written list: arm = subtree of `LeftShoulder`
(19 bones, fingers included), leg = subtree of `LeftUpLeg`, head = subtree of `Neck`. `torso` is
the only explicit list (`Spine`, `Spine1`, `Spine2`) — the `Spine2` subtree would also swallow arms,
legs and head, and a "part" that eats the whole character is not a part.

The transplant happens in **world space**: what lands in the target is the source's world rotation,
reexpressed relative to the already-transplanted parent. Bones below a transplanted part that you
did not pick keep the target's local rotation and swing along — that is how the forearm and hand
follow the shoulder.

> Jobs with different **fps** are rejected: the durations are not comparable. With different frame
> counts, use the range mapping.

Every run is stored, and the **History** tab lets you reopen, re-export or refine a past job
without processing the video again.

## Extending

Add a backend in `plugins/` — see `docs/ADDING_A_BACKEND.md`.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## Limitations and further reading

Single-person video, no absolute root motion, FBX written without the mesh.
See `docs/LIMITATIONS.md` · `docs/ARCHITECTURE.md` · `docs/API.md` ·
[CHANGELOG.md](CHANGELOG.md).

