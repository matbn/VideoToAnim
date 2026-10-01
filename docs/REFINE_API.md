# Refinement API

## Português

Este documento também está disponível em português: [REFINE_API.pt-BR.md](REFINE_API.pt-BR.md).

Base: `http://127.0.0.1:8000`. Complements `docs/API.md` (the video pipeline).

## Filters and constraints

### `GET /api/refine/filters`

```json
{"filters": [{"name": "butterworth", "params": {"cutoff_hz": 6.0, "order": 2, "zero_phase": true},
              "description": "Passa-baixa Butterworth. zero_phase=True nao introduz atraso."}]}
```

### `GET /api/refine/plan`

The default filtering plan (from `config/filters_default.yaml`) as a JSON object.

### `GET /api/refine/constraints`

```json
{"path": ".../config/constraints_humanoid.yaml",
 "preset": {"name": "humanoid", "default_stiffness": 1.0,
            "limits": [{"bone": "Head", "kind": "cone", "min_deg": -180, "max_deg": 50, "stiffness": 1.0}]}}
```

### `PUT /api/refine/constraints`

Body: `{"preset": {...}}`. Validates and **saves to the project's YAML**.
`400` with a clear message for: `min_deg > max_deg`, `stiffness` outside [0,1], nonexistent bone,
invalid `kind`, conflicting limits.

```json
{"saved": ".../config/constraints_humanoid.yaml", "limits": 32}
```

## Clip and bone editor

### `GET /api/refine/animation/{job_id}`

Metadata for the current clip (already with the edits) and the history.

```json
{"job_id": "d59925e9a2a3", "fps": 30.0, "num_frames": 60,
 "animated_bones": ["Hips", "Spine", "..."],
 "bone_parents": {"Spine": "Hips", "LeftForeArm": "LeftArm", "...": "..."},
 "root_translation": [[0.0, 0.93, 0.0], "..."],
 "history": {"session_id": "...", "edits": [{"bone": "Head", "frame": 30, "start": 20, "end": 45,
              "author": "editor", "note": "turn head"}], "undone": []}}
```

`409` if the job has no `anim.json` (run the pipeline again — the baked animation is now saved along).

`bone_parents` is the rig hierarchy — a client rendering its own preview needs it to turn a
world-space edit into the bone's local quaternion.

### `GET /api/refine/animation/{job_id}/frame/{t}`

Pose of frame `t` (quaternion + Euler in degrees) for every animated bone. Outside the clip range
it returns `400`.

The query parameter **`space`** picks the frame the `euler_deg` angles are measured in:

| `space` | meaning | axes |
|---|---|---|
| `local` (default) | the bone's rotation **relative to its parent** | follow the hierarchy |
| `global` | the bone's **absolute orientation in the scene** | fixed to the world |

The two coincide at rest and diverge as soon as an ancestor is rotated — on a raised arm the
forearm reads `Z = -0.6°` local against `Z = -62.7°` global. `quat` is **always** the local
quaternion (that is what the clip stores). An unknown `space` returns `400`.

### `GET /api/refine/animation/{job_id}/glb`

Exports the **current clip** (with the edits) as GLB — this is what feeds the editor's preview.

### `POST /api/refine/animation/{job_id}/edit`

```json
{"bone": "Head", "frame": 30, "rotation_euler_deg": [0, 40, 0], "space": "local",
 "start": 20, "end": 45, "author": "editor", "note": "turn head"}
```

Accepted fields for the target: `rotation_euler_deg` (3) or `rotation` (4, quaternion) or `translation`
(3 — **only on the `Hips` bone**). `start`/`end` define the affected range (default: just the frame).

`space` (`local` by default) is the reference frame of `rotation_euler_deg`, with the same meaning
as on the GET above; it is ignored when the target is given as a raw quaternion. In `global` the
angles are converted to the bone's local rotation using the **parent's world rotation at the target
frame** — the only consistent choice for a fully baked clip, where there is no curve to re-evaluate.
Edits saved before this field existed default to `local`. An unknown `space` returns `400`.

Response:

```json
{"report": {"bone": "Head", "frame": 30, "affected": [20, 45], "frames_rewritten": 26,
            "anchors": [19, 46], "author": "editor", "space": "local"}, "history_len": 1}
```

`400` for: frame outside the clip, invalid range, edited frame outside the range, nonexistent bone,
end-cap bone, edit with no target, `translation` outside `Hips`.

### `POST /api/refine/animation/{job_id}/undo` · `/redo` · `/reset`

Undo/redo one edit (rebuilding the clip from the original + history) or clear them all.

### Persisted session

The current clip and the history live in `storage/refine/<job_id>/current.json` and `session.json` —
they survive a server restart. Closing and reopening keeps the edits applied.

## Comparison and final refinement

### `POST /api/refine/compare/{job_id}`

```json
{"filters": ["one_euro", "savgol", "kalman"], "params": {}}
```

Response: `{"rows": [{"filtro": "one_euro", "ganho_suavidade_%": 98.79, "atraso_frames": 0,
"rmse": 0.018755, "erro_angular_medio_deg": 3.938, ...}], "markdown": "..."}`

### `POST /api/refine/animation/{job_id}/apply`

```json
{"use_filters": true, "use_constraints": true, "filters": ["one_euro", "savgol"]}
```

Applies constraints → filters to the current clip, generates `model_refined.glb` / `model_refined.fbx`
(downloadable via `/api/jobs/{id}/artifacts/glb_refined` and `fbx_refined`) and writes the report to
`storage/refine/<job_id>/refine_report.json`.

## Valid values

* filters: `one_euro`, `moving_average`, `savgol`, `kalman`, `butterworth`, `double_exponential`
* constraint `kind`: `cone`, `x`, `y`, `z`
* filter `axis`: `x`, `y`, `z`, `w`
