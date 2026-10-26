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

## 3b. Curve editor (the timeline, like a DCC)

The bone editor is a *numeric* view of the same thing; the curve editor is the *graphical* one.
`http://127.0.0.1:8000/refine` → the **timeline spans the bottom of the page**, across both
columns, and it is one single widget:

```
┌ toolbar ── bone │ axes │ |◀ ◀ ▶ ▶ ▶| │ frame 87 · 2.90 / 8.00 s │ + key − key │ fit │ fullscreen ┐
├ ruler ── frames  30 fps   0    20    40    60 …                                       │
├ keyframes ── Head      ◆              ◆                                             │
└ curves ──  X  ────────╱╲──────╱╲────────  (X −3.3°  Y 35.0°  Z −29.2°)
```

* **click a curve** to insert a keyframe at that frame (the curve does not jump — the keyframe
  starts controlling it from there);
* **drag the diamond** to change the value (vertically) or to move the keyframe in time
  (horizontally);
* **drag the ruler** to scrub; the **keyframe row** shows the bone's keyframes (click one to select
  it, click the row to insert one);
* the **X/Y/Z rows on the left** show the **angle of each channel of the selected keyframe**:
  the letter turns the curve on and off, the angle opens an **input** to type the exact value, and
  the small **↺** puts the channel back to its **original** value;
* **clicking the diamond** of a keyframe opens that same input right there — the drag still moves
  the diamond, a click without a drag types the value;
* the dashed curve is the **original** (before the keyframes), drawn in a **lighter shade of the
  channel's own colour** — close enough to tell which axis it belongs to, different enough not to
  be mistaken for the editable curve;
* the **band** behind the selected keyframe is its rebake window, and the small marks at its ends
  are the anchors;
* the curve shown is the **real rebake**: the browser reproduces the server's maths (slerp +
  the same easing), so what you drag is what lands in the file.

### The preview follows the drag, in real time

While a keyframe is being dragged, the pose of the bone **at the current frame** changes on every
pixel — and the 3D preview shows it immediately, without waiting for the server: the browser
applies the same rebake maths and writes the quaternion straight into the scene. The server is
only called when the mouse is released.

### Interpolation curves (bezier handles)

Each keyframe has its own interpolation, and one of them is a real **curve**: `bezier`. In that mode
the keyframe gets two handles (the in and out handles of the DCCs), drawn as a dashed line from the
keyframe, and **dragging a handle shapes the segment** — the way the value rises and falls between
two keyframes.

* the handles are **automatic** by default (a third of the segment, on the line between the two
  keyframes — the "auto" of Blender): they appear as soon as the keyframe switches to `bezier`;
* the horizontal distance of the handle is in **frames**, the vertical one in **degrees of the
  channel** the handle belongs to (the channel is the one whose curve you drag);
* the browser reproduces the server's bezier exactly (same cubic, same bisection), so the preview
  of the drag is what lands in the file.

### Entry and exit, separately

Every keyframe has **two** interpolation options:

| option | what it models |
|---|---|
| **entrada** (`interp_in`) | how the curve **arrives** at the keyframe |
| **saída** (`interp_out`) | how the curve **leaves** it |

The segment between two keyframes follows the **exit** of the keyframe it leaves on its first half
and the **entry** of the keyframe it reaches on its second half. With both options equal, the
segment uses that mode all the way (the classic behaviour); with different options, the segment is
split in the middle — that is how you get "arrives slowly, leaves quickly" on a single keyframe.

### The easing families

Besides `suave` (ease-in-out), `linear`, `degrau` (hold) and `curva` (the bezier handles), each
option can be one of the classic easing families:

| family | shape |
|---|---|
| `sine` | a gentle acceleration |
| `quad` | quadratic |
| `cubic` | cubic |
| `quart` | quartic |
| `quint` | quintic |
| `expo` | exponential (very sharp) |
| `circ` | circular |
| `back` | overshoots and comes back |
| `elastic` | springs |
| `bounce` | bounces |

Each family has an **entry** variant (the curve accelerates towards the keyframe) and an **exit**
variant (it eases off leaving it) — that is the pair the two options pick. `back` and `elastic`
overshoot the value on purpose (the motion goes past the keyframe and returns); `bounce` breaks the
monotonicity (the motion bounces).

### The effect range of a keyframe (start and end)

Every keyframe acts on a **range of frames** — the ones it rewrites. By default the range is
derived from the neighbours (it runs from just after the previous keyframe to just before the next
one), but you can set it yourself:

* the **bar** under the keyframe in the timeline shows the range, and **its two ends drag**;
* the **início** / **fim** fields of the selected keyframe show and change the same numbers;
* the shaded band behind the selected keyframe in the graph is the same range.

A keyframe with a narrow range only bends the motion inside it — the rest of the clip is left
bit-exactly as it was.

### The window comes from the neighbours

A keyframe does not carry its own range: its window runs from the frame **after the previous
keyframe** to the frame **before the next one**, so the ends of the window are the neighbours —
which become the anchors of the rebake. The result therefore does not depend on the order the
keyframes were created in, and a keyframe with no neighbours gets a ±20 frame ramp (configurable
per commit).

### Interpolation per keyframe

Each keyframe has its own interpolation: `smooth` (ease-in-out, zero derivative at the ends),
`linear` or `step` (hold the value until the target frame).

### Deleting a keyframe really deletes it

The bone is rebuilt from its **base** (the series it had before the first keyframe) plus the
current keyframe set. Without the base, deleting a keyframe would leave the old rebake baked into
the clip; with it, the bone goes back exactly where it was.

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
