# HTTP API

## Português

Este documento também está disponível em português: [API.pt-BR.md](API.pt-BR.md).

Base: `http://127.0.0.1:8000`

## `GET /api/health`

```json
{"status": "ok", "backends": 4, "registry_errors": []}
```

## `GET /api/backends`

Lists the registry backends (this is what populates the dropdown).

```json
{
  "default": "vitpose",
  "preferred": "vitpose",
  "backends": [
    {"name": "vitpose", "display_name": "ViTPose (ONNX, CPU)",
     "source": "builtin", "available": true, "reason": "",
     "is_default": true, "hidden": false, "metadata": {}}
  ],
  "hidden_count": 1,
  "disabled_plugins": ["example_backend.py: desativado (PLUGIN_DISABLED = True)"],
  "errors": []
}
```

- `preferred` — the **preferred** backend as default (`vitpose`).
- `default` — the **effective** default: the preferred one if available; otherwise, the first
  visible and available backend. This is the value the interface pre-selects.
- `hidden_count` / `disabled_plugins` — transparency about backends that exist but do not show up.
- `?include_hidden=true` — includes the hidden ones (useful for inspection/tests).

## `POST /api/mesh/inspect` (multipart)

Diagnostics for a mesh **without processing a video**: `mesh` = a `.fbx`/`.glb` file.

```json
{"ok": true, "filename": "MixamoChar.fbx", "bytes": 5669168,
 "report": {"format": "fbx-binary", "matched": 65, "missing_count": 0,
            "compatible": true, "attachable": true, "messages": []},
 "attach": {"ok": true, "vertices": 9285, "triangles": 17916,
            "stats": {"meshes": 6, "vertices": 9285, "unweighted": 0}}}
```

## `POST /api/jobs` (multipart)

| field | type | description |
|---|---|---|
| `video` | file | mp4/mov video (512 MB limit) |
| `backend` | string | backend id (`mediapipe`, `vitpose`, `synthetic`, ...) |
| `params` | JSON string | job parameters |
| `mesh` | file (optional) | Mixamo mesh `.fbx`/`.glb` to replace the capsule sticks |
| `lang` | string (optional) | log language: `pt` (default) or `en` |

> The mesh can also be checked beforehand through `POST /api/mesh/inspect`.

Rejected with `400` if the backend does not exist **or is not available**.

Parameters accepted in `params`:

| key | default | description |
|---|---|---|
| `fps` | video fps | output animation fps |
| `smoothing` | `"oneeuro"` | `"oneeuro"` or `"none"` |
| `lift_2d_to_3d` | `true` | applies the analytic lifter if the backend is 2D-only |
| `max_frames` | all | limits the number of processed frames |
| `refine` | — | refinement layer (see `params.refine` below) |

### `params.refine` (optional)

Runs the refinement layer **before** the export. Accepts `true` as a shortcut for the project's
default files, or a path/dict:

```json
{"refine": {"constraints": true,
            "filters": {"apply_to_translation": true,
                        "filters": [{"name": "kalman", "start": 0, "end": 120}]},
            "edits": [{"bone": "Head", "frame": 30, "rotation_euler_deg": [0, 30, 0],
                       "start": 20, "end": 45}]}}
```

Order: constraints -> filters -> edits. The job log records the stages and the applied filters
(e.g., `Refinement applied: constraints -> filters (filters: kalman)`).

Response `201`:

```json
{"job_id": "a1b2c3d4e5f6", "status": "queued", "mesh": "MixamoChar.fbx"}
```

## Backend installation

### `GET /api/backends/{name}/install`

The installation state of that backend, plus the plan:

```json
{"backend": "vitpose", "status": "idle|running|done|error", "log": "...",
 "report": {"ok": true, "available_after": true, "results": [...]},
 "plan": {"kind": "auto", "total_mb": 98.0, "manual_reason": "",
          "steps": [{"kind": "pip", "target": "onnxruntime", "size_mb": 15}]},
 "label": "automatically installable"}
```

`GET /api/backends` includes, per backend, `install: {kind, label, manual_reason, total_mb, installable}`.

### `POST /api/backends/{name}/install?force=false`

Starts the installation in the background (it runs in parallel; follow it with the `GET` above).
`400` for **manual** backends, with the exact reason:

> `openpose cannot be installed automatically: requires compiling C++ (CMake + CUDA/OpenCL) ...`

Plan `kind`: `auto` (pip/download), `pip-heavy` (installs, but drags in a big dependency) and
`manual` (not automatable).

### `GET /api/jobs?status=&backend=&limit=`

Lists the history (most recent first). Optional filters by `status`
(`queued|running|done|error`) and `backend`; `limit` goes up to 500.

```json
{"jobs": [{"id": "...", "video_name": "clip.mp4", "backend": "vitpose", "status": "done",
           "created_at": 1790578700.0, "finished_at": 1790578712.0,
           "artifacts": {"video": {...}, "glb": {...}, "fbx": {...}},
           "metrics": {"frames": 60, "mean_score": 0.81, "video_span_s": 2.0}}],
 "total": 42}
```

## `GET /api/jobs` · `GET /api/jobs/{id}`

```json
{
  "id": "a1b2c3d4e5f6", "video_name": "clip.mp4", "video_bytes": 845233,
  "backend": "mediapipe", "params": {"fps": 30},
  "status": "done", "created_at": 1770000000.0, "started_at": ..., "finished_at": ...,
  "error": "", "log": "[12:00:01] Job started...\n",
  "artifacts": {"video": {"path": "...", "size": 3876598},
                "glb": {"path": "...", "size": 88260},
                "fbx": {"path": "...", "size": 51234}},
  "metrics": {"frames": 166, "fps": 30.0, "video_fps": 30.0, "video_span_s": 5.533,
              "mean_score": 0.972, "fk_max_position_m": 0.18,
              "fk_max_angle_deg": 0.018, "elapsed_s": 8.7},
  "mesh_report": {
    "format": "fbx-binary", "bones_found": 66, "matched": 65,
    "missing": [], "missing_count": 0, "extra": ["Hips_skin"], "extra_count": 1,
    "compatible": true, "attachable": false,
    "messages": [], "stats": {"meshes": 6, "vertices": 9285, "unweighted": 0}
    "stats": {}
  }
}
```

States: `queued` → `running` → `done` | `error`.
`mesh_report` is `null` when no mesh was uploaded.

### Interpreting the `mesh_report`

| field | meaning |
|---|---|
| `matched` | bones in your file that match the Mixamo contract (up to 65) |
| `missing` | required bones missing from your mesh |
| `extra` | bones outside the standard (ignored) |
| `compatible` | the skeleton can receive the animation |
| `attachable` | the mesh was **actually** used in the output (binary FBX or GLB) |

## `GET /api/jobs/{id}/artifacts/{name}`

`name` = `video` | `glb` | `fbx`.

- `video` → `video/mp4` (or `video/quicktime`), served **inline**, with **Range** support
  (`206 Partial Content`) — lets you play and scrub the reference video next to the skeleton.
- `glb` → `model/gltf-binary` · `fbx` → `application/octet-stream`.

## `GET /`

Serves the SPA (`web/index.html`); the static files live at `/static/*`.
