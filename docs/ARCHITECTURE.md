# Architecture — VideoToAnim

## Português

Este documento também está disponível em português: [ARCHITECTURE.pt-BR.md](ARCHITECTURE.pt-BR.md).

## Flow

```
      ┌────────────┐
      │  Browser   │    three.js SPA (dropdown, upload, status, 3D preview)
      └─────┬──────┘
            │ HTTP (REST + multipart)
      ┌─────▼──────────────────────────────────────────┐
      │ FastAPI  (server/app.py)                       │
      │  /api/backends  /api/jobs  /api/jobs/{id}      │
      │  /api/jobs/{id}/artifacts/{glb|fbx}            │
      └─────┬──────────────────────────────────────────┘
            │ submits job (ThreadPoolExecutor, 1 worker)
      ┌─────▼───────────────┐
      │ JobStore (SQLite)   │    id, video, backend, params, status,
      │ storage/jobs.db     │    timestamps, log, error, artifacts, metrics
      └─────┬───────────────┘
            │
      ┌─────▼──────────────────────────────────────────────────────────┐
      │ Pipeline (core/pipeline.py)                                    │
      │                                                                │
      │  1. video.read_video()        frames BGR                       │
      │  2. BackendRegistry.get()  →  PoseBackend (Adapter)            │
      │  3. Adapter.infer_video()  →  FramePose (canonical COCO-17)    │
      │  4. smoothing (one-euro)                                       │
      │  5. AnalyticLifter (if the backend is 2D-only) → kp3d in meters│
      │  6. Retargeter.retarget()  →  Animation (65 Mixamo bones)      │
      │  7. (optional) check_compatibility(mesh) → attach or warn      │
      │  8. build_glb() + build_fbx()                                  │
      └─────┬──────────────────────────────────────────────────────────┘
            │
            storage/jobs/<job_id>/model.glb , model.fbx
```

## Modules

| File | Responsibility |
|---|---|
| `core/canonical.py` | Canonical COCO-17 skeleton and `FramePose`. |
| `core/adapter.py` | `PoseBackend` (ABC) + `AdapterConfig` + `ConfigurableAdapter`. |
| `core/registry.py` | Backend registration and discovery (built-in + `plugins/`). |
| `core/smoothing.py` | One-Euro filter. |
| `core/lifter.py` | Pluggable 2D→3D lifter (analytic by default). |
| `core/mixamo.py` | The 65-bone contract, quaternions and FK. |
| `core/retarget.py` | Solver joints→local rotations + reverse FK validation. |
| `core/export_glb.py` | GLB container (skins + animation + user mesh or capsule sticks). |
| `core/export_fbx.py` | Pure ASCII FBX 7.4 writer. |
| `core/mesh.py` | Inspection of the uploaded mesh, compatibility (65 bones) and loading (FBX/GLB). |
| `core/fbx.py` | Pure-Python binary FBX 7.x reader (nodes, properties, deflate arrays). |
| `core/jobs.py` | SQLite `JobStore`. |
| `core/pipeline.py` | Orchestration. |
| `core/video.py` | Video reading (OpenCV). |
| `backends/*.py` | 11 pose backends, each with its **license category** (`livre` / `nao_comercial` / `licenca_a_parte`). |
| `plugins/` | External backends (automatic discovery). |
| `server/app.py` | FastAPI API + SPA hosting. |
| `web/` | SPA (HTML/CSS/JS + three.js). |

## Design decisions

1. **A single canonical skeleton (COCO-17).** Every backend converts to COCO-17
   (absolute pixels, y down, score in [0,1]). ViTPose already is COCO-17;
   MediaPipe (BlazePose 33) has a declarative remapping table in `AdapterConfig`.
   The backends with commercial conflicts (`openpose`, `simplebaseline`, `mhformer`)
   were removed from the product — see `docs/LICENSING.md`.
2. **Declarative adapter.** Each detector's "peculiarities" (joint order,
   normalized/pixel coordinates, flipped y, score, smoothing,
   multi-person) are configuration data, not duplicated code.
3. **Automatic plugin discovery.** `BackendRegistry` scans `plugins/`
   for `*.py` with `BACKEND`/`BACKENDS` and for `*.yaml` with `entrypoint`.
   The dropdown is populated from `/api/backends`.
4. **Retarget rigidity.** Bone lengths always come from the rest offset;
   per frame only the target **direction** is computed (from-to quaternion with
   anti-180° protection) plus the world→local conversion. The rig never
   shrinks/stretches and there are no gross inversions.
5. **Units and axes.** The retarget exports in **meters, Y-up, front +Z**
   (glTF/three.js convention). Mixamo's FBX uses centimeters — that is why
   importers show a 0.01 scale; our GLB already comes out in meters.
6. **UI = FastAPI + SPA.** Justified in
   `docs/UI_FRAMEWORK_COMPARISON.md` (three.js timeline/playback requirements
   are not met out of the box by Gradio/Streamlit).
7. **Export without Blender.** GLB written by hand (validated with `pygltflib`
   in the tests) and ASCII FBX 7.4 written in pure Python.
