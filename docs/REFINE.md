# Animation refinement layer

## Português

Este documento também está disponível em português: [REFINE.pt-BR.md](REFINE.pt-BR.md).

The animation coming out of the pipeline is **baked**: one pose per frame, no curves. This layer
refines that clip with three tools, without touching the original file:

```
baked animation ──► 1. constraints ──► 2. filters ──► 3. bone edits ──► refined animation
                     (joint limits)     (stabilization)   (rebaked keyframes)
```

The order matters: the limits are imposed **before** smoothing (otherwise the filter can push back
outside the limit), and the manual edits come **last**, so your intention wins.

---

## 1. Stabilization filters

Six methods, all in pure numpy (no scipy), selectable by name and configurable per **bone**,
per **axis** and per **frame range**:

| filter | parameters | when to use |
|---|---|---|
| `one_euro` | `min_cutoff` (1.0), `beta` (0.02), `d_cutoff` (1.0) | project default; cuts more where the motion is slow |
| `moving_average` | `window` (5), `weight` (`linear`\|`uniform`) | simple and predictable; good for short tremors |
| `savgol` | `window` (11, odd), `order` (2) | smooths **preserving peaks** and accelerations |
| `kalman` | `process_noise` (1e-3), `measurement_noise` (1e-2) | Gaussian noise; constant-velocity model |
| `butterworth` | `cutoff_hz` (6.0), `order` (2), `zero_phase` (true) | frequency cut; `zero_phase` **introduces no lag** |
| `double_exponential` | `alpha` (0.35), `beta` (0.1) | follows trend with little memory (Holt) |

Quaternions are filtered with **sign-continuity correction** (`q` and `-q` are the same rotation;
filtering without that produces jumps of 2 in each component) and re-normalized.

### Configuration file

`config/filters_default.yaml`:

```yaml
apply_to_translation: true      # also filters the root (Hips) translation
filters:
  - name: one_euro              # hands tremble more: reactive window
    params: {min_cutoff: 1.2, beta: 0.03}
    bones: [LeftHand, RightHand, LeftForeArm, RightForeArm]
  - name: butterworth           # torso: smooth and with no phase lag
    params: {cutoff_hz: 6.0, zero_phase: true}
    bones: [Spine, Spine1, Spine2]
    start: 0                    # optional range (inclusive)
    end: 120
  - name: savgol                # default for everything else
    params: {window: 9, order: 2}
  - name: kalman                # only the head's Y axis, in a stretch
    axis: y
    bones: [Head]
    start: 200
    end: 260
```

Rules are evaluated **in order**; the last one matching the bone wins. Fields: `name`, `params`,
`bones` (omit = all), `start`/`end` (inclusive range), `axis` (`x`/`y`/`z`/`w` = filters only that
component).

**Frames outside the range stay exactly the same** — the filter is applied to the stretch, not the clip.

---

## 2. Joint constraints

Full humanoid preset in `config/constraints_humanoid.yaml` — **edit the file**, no need to touch code.

```yaml
name: humanoid
limits:
  - {bone: Head, kind: cone, min_deg: -180, max_deg: 50, stiffness: 1.0}
  - {bone: Head, kind: y, min_deg: -70, max_deg: 70}
  - {bone: LeftForeArm, kind: z, min_deg: -5, max_deg: 160}   # no hyperextension
  - {bone: RightLeg, kind: z, min_deg: -155, max_deg: 5}
```

* `kind: cone` — limits the **total deviation** of the local rotation (in degrees). It is what stops
  "head turning 180°" and gives the shoulder its **motion cone**.
* `kind: x | y | z` — limits **one axis**, with **asymmetric** `min_deg`/`max_deg`: this is how you
  express *elbow/knee without hyperextension* (little slack one way, a lot the other).
* `stiffness` (0..1) — 1 corrects fully, 0 ignores, intermediate values apply the correction
  **smoothly (slerp)**, never a hard cut.

Each run's report carries, per bone: frames corrected, largest violation (degrees) and average
correction. Configuration errors (`min_deg > max_deg`, `stiffness` outside [0,1], nonexistent bone,
conflicting limits) produce a **clear message** citing the bone and the field.

---

## 3. Bone editor (rebaked keyframe)

Every edit is: **bone + target frame + new value + affected frame range**.

```
anchor_before ......... [ start ...... frame ...... end ] ......... anchor_after
   (intact)               ^-------- rebaked ---------^              (intact)
```

* at the target `frame` the pose becomes the edited one;
* from `anchor_before` (= `start-1`) to the target frame, interpolation with **ease-in-out**
  (smoothstep: zero derivative at the ends → no jump);
* from the target frame to `anchor_after` (= `end+1`), the same on the way back;
* **frames outside `[start, end]` stay bit-exactly the same** (verified by a diff test).

This is the requested "keyframe system", but **rebaked**: the final file still has one pose per frame,
without curves.

### History

Every edit goes into a persisted history (JSON) with bone, frame, range, author, note and
timestamp. Undo/redo work, and **closing and reopening the session keeps the edits applied** —
the clip is rebuilt from the original + the saved history.

---

## 4. Comparison report

For each filter applied to the **same clip**, we measure:

| metric | what it says |
|---|---|
| `ganho_suavidade_%` | jerk energy (2nd derivative) reduction — the higher, the smoother |
| `atraso_frames` | lag via cross-correlation (0 = no lag) |
| `rmse` | residual deviation from the original signal |
| `erro_angular_*_deg` | how much the rotation actually changed (mean and max) |

Output as a Markdown table + JSON (`filtros_comparativo.md`, `refine_report.json`).

Real example (synthetic clip, 80 frames):

```
| filtro             | ganho_suavidade_% | atraso_frames | rmse     | erro_angular_medio_deg |
|--------------------|-------------------|---------------|----------|------------------------|
| one_euro           | 98.79             | 0             | 0.018755 | 3.938                  |
| moving_average     | 98.73             | 0             | 0.012899 | 2.729                  |
| savgol             | 98.47             | 0             | 0.015252 | 3.229                  |
| kalman             | 94.78             | 0             | 0.014819 | 3.090                  |
| butterworth        | 94.65             | -1            | 0.017353 | 3.639                  |
| double_exponential | 94.46             | 0             | 0.017145 | 3.438                  |
```

---

## 5. CLI

```powershell
# before/after examples + comparison report (written to storage/refine_samples/)
.\.venv\Scripts\python.exe tools\refine.py --make-samples

# applies constraints + filters and saves the refined clip (+ GLB/FBX)
.\.venv\Scripts\python.exe tools\refine.py --input storage\refine_samples\sample_before.json `
    --constraints config\constraints_humanoid.yaml --filters config\filters_default.yaml `
    --out storage\refine_samples\refined

# compare filters only
.\.venv\Scripts\python.exe tools\refine.py --input clipe.json --compare one_euro,savgol,kalman

# inject an absurd violation (head at 180°) to watch the preset fix it
.\.venv\Scripts\python.exe tools\refine.py --input clipe.json --inject-violation head180 `
    --no-filters --out out/
```

---

## 6. Use as a library

```python
from core.refine import refine_animation, boneedit as be

refined, report = refine_animation(
    anim,
    constraints_config="config/constraints_humanoid.yaml",
    filters_config="config/filters_default.yaml",
    edits=[be.BoneEdit(bone="Head", frame=30, rotation_euler_deg=[0, 30, 0], start=20, end=45)],
)

print(report.stages)          # ['constraints', 'filters', 'edits']
print(report.constraints)     # frames corrected per bone
```

### Pipeline integration

Just pass `params.refine` in the job — the layer runs **before** the export:

```json
{"fps": 30, "refine": {"constraints": "config/constraints_humanoid.yaml",
                       "filters": "config/filters_default.yaml"}}
```

Without `params.refine`, the behavior is exactly as before.

---

## 7. Editor in the interface

`http://127.0.0.1:8000/refine` — same visual language as the pipeline:

* pick the clip (jobs with `anim.json`), navigate by frame, choose the bone and adjust X/Y/Z;
* set the **affected range** and apply; the 3D preview (skeleton + mesh) updates instantly;
* undo/redo/clear, compare filters (table) and apply the refinement (generates refined GLB/FBX);
* edit the constraint limits and save them to the YAML.
