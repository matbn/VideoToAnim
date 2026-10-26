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

### `GET /api/refine/animation/{job_id}/curves?bone={bone}&space={local|global}`

The bone's rotation **curve**, frame by frame — what the curve editor draws.

```json
{"job_id": "d59925e9a2a3", "bone": "Head", "space": "local", "fps": 30.0, "num_frames": 60,
 "channels": {"x": [0.0, "..."], "y": [12.5, "..."], "z": [0.0, "..."]},
 "quats": [[0.0, 0.0, 0.0, 1.0], "..."],
 "base_quats": [[0.0, 0.0, 0.0, 1.0], "..."],
 "chain": [{"bone": "Neck", "quats": [[0.0, "..."], "..."]}, "..."],
 "keyframes": [{"index": 0, "frame": 30, "start": 11, "end": 49,
                "value": [0.0, 40.0, 0.0], "interp": "smooth", "space": "local", "note": ""}]}
```

* `channels` — the Euler angles (degrees) of the bone per frame, **in the requested reference frame**
  (the same meaning as `space` on the frame endpoint above).
* `quats` — the local quaternion per frame (what the clip stores).
* `base_quats` — the bone's series **without keyframes**: the base the keyframes are applied on top
  of. It is `null` while the bone has no keyframes. A client that re-applies the keyframes itself
  (to preview a drag) must start from this series, not from `quats` — otherwise deleting a
  keyframe would leave the old rebake behind.
* `chain` — the ancestors of the bone, root first, with their quaternions per frame: what a client
  needs to convert a keyframe target to the world frame.
* `keyframes` — the bone's keyframes (its edits in the history), with the value **in the requested
  reference frame**, the rebake window (`start`/`end`) and the interpolation mode.

`404` if the bone has no animated rotation. `400` for an unknown `space`.

### `PUT /api/refine/animation/{job_id}/keyframes`

Replaces the bone's **whole keyframe set** — what the curve editor commits.

```json
{"bone": "Head", "space": "local", "half": 20,
 "keyframes": [{"frame": 12, "rotation_euler_deg": [0, 35, 0],
                "interp_in": "smooth", "interp_out": "linear",
                "handle_in": null, "handle_out": null, "handle_channel": 1}]}
```

* `half` — half the width (in frames) of the rebake window of a keyframe with no neighbours
  (default 20, i.e. ~0.7 s of ramp on each side).
* `interp_in` — how the curve **arrives** at the keyframe; `interp_out` — how it **leaves** it.
  Both are `smooth` (ease-in-out, the default), `linear`, `step` (hold), `bezier`, or one of the
  classic easing families: `sine`, `quad`, `cubic`, `quart`, `quint`, `expo`, `circ`, `back`,
  `elastic`, `bounce`. Each family has an entry variant (the curve accelerates towards the
  keyframe) and an exit variant (it eases off leaving it) — that is the pair the two options pick.
  The segment between two keyframes follows the **exit** of the keyframe it leaves on its first
  half and the **entry** of the keyframe it reaches on its second half; with both equal, the
  segment uses that mode all the way. (`interp` alone is accepted as an alias of `interp_in`.)
* `handle_in` / `handle_out` — the **bezier handles** of the keyframe: `[dx, dy]`, with `dx` in
  frames and `dy` in degrees of the channel `handle_channel` (0=X, 1=Y, 2=Z). `null` = the
  automatic handle (a third of the segment, on the line between the keyframes). The handle of the
  *previous* keyframe shapes the segment that comes into this one, which is why the server
  resolves the whole set at once.
* The window of each keyframe is derived from its **neighbours**: from the frame after the previous
  keyframe to the frame before the next one, so the ends of the window are the neighbours — which
  become the anchors of the rebake. The result therefore does not depend on the order the
  keyframes were created in.
* `start` / `end` — the **effect range** of the keyframe (the frames it rewrites), dragged on the
  timeline. `null` (the default) keeps the neighbour-derived window; when given, the keyframe acts
  only on that stretch and the rest of the clip stays bit-exactly as it was. The keyframe's frame
  must be inside the range.
* The bone is rebuilt from the **base** (its series without keyframes) plus the new set, so
  **deleting** a keyframe brings the bone back to where it was, instead of leaving the old rebake
  in the clip.

Response: `{"report": {"bone": "Head", "keyframes": 2, "frames_rewritten": 58, "affected": [0, 49]},
"history_len": 2, "keyframes": [ ... ]}` (the keyframes as the server stored them).

`400` for: a frame outside the clip, a keyframe without a 3-value target, two keyframes on the same
frame, an unknown `space` or an unknown `interp`, a malformed handle. `404` if the bone has no
animated rotation.

### `POST /api/refine/animation/{job_id}/edit`

```json
{"bone": "Head", "frame": 30, "rotation_euler_deg": [0, 40, 0], "space": "local",
 "start": 20, "end": 45, "interp": "smooth", "author": "editor", "note": "turn head"}
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
end-cap bone, edit with no target, `translation` outside `Hips`, unknown `space` or `interp`.

`interp` chooses how the rebake blends: `smooth` (ease-in-out, the default and the historical
behaviour), `linear` (a straight line) or `step` (holds the value until the target frame).

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
