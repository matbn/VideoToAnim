# Changelog

## Português

Este documento também está disponível em português: [CHANGELOG.pt-BR.md](CHANGELOG.pt-BR.md).

## [1.4.14] — Mix body parts between two jobs

Reported: *"I ran two jobs and I think in one of them the arm tracks better than the other; I want the body from B and the arm from A"*

### Added

* **`Mix body parts between two jobs`**, in the refine editor: pick the **source** job, tick the body
  parts (pelvis, torso, head, left/right arm, left/right leg) and apply. The clip in the editor then
  has only those parts coming from the other job; everything else stays as it was. There is an
  **undo** that restores the previous clip.

  Verified in the browser with two real jobs of the same video (`4ac4511714b6` mediapipe →
  `ba5bb21ae562` rtmpose): 19 bones of the left arm, **0.000° angular error against the source**, the
  rest of the body identical to the target, and undo restoring both.

* **The transplant is done in world space, and that is the whole point.** Clip rotations are **local
  (relative to the parent)**, so pasting A's local arm rotation onto B's body would not give A's arm —
  it would give a crooked arm, because the shoulder is a different one. What lands in the target is
  A's **world** rotation, re-expressed against the (already transplanted) parent in B:

  ```
  local_new[t] = conj(parent_world_new[t]) · world_A[t]
  ```

  Bones **below** a transplanted part that were not selected keep the target's local rotation and
  rotate along — that is how the forearm and hand follow the shoulder.

  Useful consequence: ticking only `torso` gives A's torso **with B's arms hanging off it**, not all
  of A.

* The parts come from the **rig hierarchy**, not a hand-written list: arm = subtree of `LeftShoulder`
  (19 bones, including all 15 finger bones), leg = subtree of `LeftUpLeg`, head = subtree of `Neck`.
  `torso` is the only explicit list (`Spine`, `Spine1`, `Spine2`) — the subtree of `Spine2` contains
  arms, legs and head, and a "part" that swallows the whole character is not a part.

* The **pelvis path** (the root translation, i.e. how the body moves through space) is only copied
  together with the `pelvis / root` part by default. There is a checkbox to force it.

* Jobs with a **different frame count or fps are rejected** with a clear error: mixing different clips
  would need time alignment, which is not done.

* Undo stores a **snapshot of the whole clip** (the edit history works at one `BoneEdit` per
  bone/frame granularity; a transplant would be thousands). `EditHistory` gained `snapshots`.

* **Two-column editor layout**: the controls live in a column that scrolls on its own, and the
  preview is **sticky beside it** — video + skeleton + mesh on `Refine`, the A/B pair on `Mix A/B`.
  Adjusting a bone no longer means scrolling to the bottom to see the result, back up to adjust,
  and down again: the preview never leaves the screen, and the page itself no longer scrolls.
  Below 1180 px the columns stack, so narrow windows keep working.

* **The source selector moved next to the A panel and the target is plain text.** The mix tab
  showed the job selector in the settings column while the A clip sat in the preview column on the
  other side, so it was not obvious which job the selector controlled. The selector now sits directly
  above the A panel, and the target hash is rendered as read-only text instead of an editable-looking
  input box.

* **Scrubbing the timeline during playback now works.** The playback loop rewrote the slider ~60x/s,
  so dragging it snapped back under the cursor and it looked like the `input` event was broken. The
  slider is now the user's while dragging (playback pauses on `pointerdown` and resumes on release),
  and the time label keeps updating. Verified in headless Chrome with a real mouse drag on the
  pipeline page: the slider follows the pointer, the video follows the slider, and the label matches.

* **The editor is now two tabs** — `Refine` (clip, bone editor, filters, constraints, preview) and
  `Mix A/B` — so the mixing controls do not crowd the refinement UI. The playbar and the log sit
  **outside** the tabs, so the same timeline drives both: scrubbing in the mix tab moves the A/B
  panels and the reference video together. Switching tabs re-measures the 3D canvases (a hidden canvas
  measures 0 and would render blank).

* **Side-by-side A/B panels**, inside the `Mix A/B` tab: both jobs on the same camera and the same timeline
  frame, labelled with the job ids. Picking the source in the selector reloads the pair; applying or
  undoing the mix reloads the target panel so you can see the result.

* **Frame-range mapping**, for when the two jobs are not exactly the same stretch of video — e.g.
  *frames 1 to 23 of the left arm from A* applied to *frames 3 to 35 of B*. The duration may change
  (the source is resampled by **slerp in time**, not by index), and **only the target range is
  rewritten**: the frames outside it stay exactly as they were.

  Measured on the real job (`1..23` → `3..35`, 23 frames becoming 33): angular error against the
  source **mean 0.107°, max 0.416°** inside the range (that difference is only the resampling
  rounding), and the frames outside it **0.0000°** against the target — untouched.

  Without the mapping, jobs with different frame counts are still rejected. The `fps` must match
  either way: with different fps the durations are not comparable.

  The error report also had to start measuring **only the mapped range** — measuring the whole clip
  reported 85° mean, which was purely the out-of-range frames (which stay the target's by design).

## [1.4.13] — Preview: the video and the 3D now share the framing and the clock

Reported: *"the mesh/skeleton output is not very well aligned with the video, the animation preview has a delay relative to the reference"*

### Fixed

* **The 3D ran 1–2 frames behind the video, always.** The playback loop drove the skeleton from
  `video.currentTime`, which is the *playback clock* — it runs ahead of the frame the browser has
  actually painted. The viewer now reads the presented frame's timestamp from
  `requestVideoFrameCallback` (`mediaTime`), so the pose on screen is the pose of the frame you are
  looking at. Measured cost of the old path: 33–66 ms at 30 fps, constant. Browsers without the API
  (Safari < 15.4, Firefox < 132) fall back to `currentTime`, as before.

* **The misalignment also grew from 0 to about one frame across the clip.** The mapping was
  `videoTime / video_span_s * clipDuration`, but the GLB's last keyframe is at `(T-1)/fps` while
  `video_span_s` is `T/fps` — so the animation was compressed by `(T-1)/T` (**0.6%** on the 167-frame
  reference clip, ~33 ms of lag by the last frame) and the loop ran one frame past the end of the
  clip. The preview now maps **frame to frame** (`animTime = mediaTime * videoFps / targetFps`) and
  loops at the last keyframe, so the two stay locked for the whole clip and for any fps combination.

* **The camera was fixed while the video was not**, which made the three panels look misaligned even
  when the pose was right. The new **`match video`** / **`enquadrar vídeo`** preset re-frames the 3D
  with the *same* geometry the lifter used to lift the pose (pelvis at 0.98 m, trunk 0.52 m), derived
  from the first frame's 2D joints and the video size:
  * distance = `0.52 / (2 · (trunk_px / video_height) · tan(fov/2))`
  * eye height = `0.98 − d · tan(fov/2) · (1 − 2·hip_py / video_height)`, plus a horizontal term for
    subjects off-centre.

  The character's frame fractions then match the video **exactly** (verified to 1e-9, in
  `tests/test_preview_sync.py`): the pelvis projects onto its own detected pixel. On the reference
  job it lands at 3.69 m / 0.92 m, close to the old fixed 3.6 m / 0.95 m — for a centred subject
  little changes, and for an off-centre or differently-scaled one the apparent offset disappears.
  Available in both the pipeline page and the refine editor, recomputed on resize.

* **Regression in the refine editor (the video "raced" and ended before any movement).** When making
  the reference video follow the frame, the target was computed as `frame * videoFps / targetFps` —
  the *inverse* direction, the formula that goes from **video to animation**. When the fps match that
  simplifies to `frame`, and a frame index was being used as a **second**: at frame 36 the video was
  told to go to 36 s in a 5.57 s clip. The seek therefore hit the end of the clip from **frame 6**,
  the video raced to the finish and stayed there while the skeleton animated normally — exactly the
  reported symptom. The correct target is `frame / videoFps`. Verified in a browser (headless Edge,
  job 4ac4511714b6): frames 0/1/6/36/90/166 → 0.000/0.033/0.200/1.200/3.000/5.533 s, error 0.0000 s.
  The re-seek guard is now `0.5 / videoFps` (video seconds) for consistency.

* **`Aplicar edição` failed on almost every frame.** The affected window defaulted to `[0, 10]` and
  never followed the target frame, so the server rejected the whole edit with *"o frame editado (36)
  precisa estar dentro de [0, 10]"* — reported only as a line in the bottom log, with nothing in the
  editor suggesting the window was the problem. The window now **recentres on the target frame**,
  preserving the width the user picked, so the target is always inside it.

* **The ramp was lopsided, so the motion whipped.** With a one-sided window the smoothstep is smooth
  only at its ends — the whole angle still has to be covered, just in fewer frames. Measured on the
  `LeftArm` of job `4ac4511714b6` (target frame 36, −120° in Z): window `[0,50]` gave a lead-in peak
  of 1.84°/frame against a lead-out of 5.48°/frame (**2.98×**); a centred `[26,46]` gives 5.28 vs
  4.91 (**0.93×**). The default width is now **40 frames** (~0.7 s of ramp per side) instead of 10,
  chosen from the measurement below.

  | window | lead-in peak | vs the bone's own rate |
  |---|---|---|
  | 10 frames | 9.54 °/frame | 7.1× |
  | 20 frames | 5.28 °/frame | 4.0× |
  | 40 frames (new default) | 3.00 °/frame | 2.2× |

  The bone's own median rate in the clip is 1.34 °/frame, so a wider window brings the blend close to
  how the animation already moves. Users who want a tighter edit can still narrow the window.

### Also

* The skeleton is only re-pinned when the instant actually changed (≥ 0.1 ms), instead of 60× per
  second, which removes mixer jitter and wasted work.

* **Regression, caught by manual testing: the preview froze / looped frantically.** The loop-back
  point was decided from `requestVideoFrameCallback`'s `mediaTime`, and that is a trap: **a seek
  discards the browser's pipeline, and a frame is only *presented* — firing the callback — if no
  other seek happens first.** Seeking over a seek therefore freezes `mediaTime` on the last frame,
  the loop-back condition stays true, and it fires again on the next animation frame: a
  self-sustaining cycle that re-seeks forever. Measured in headless Edge on the reference clip
  (5.53 s): the old path left the video **stopped at 5.566 s with the pose pinned at
  `5.53 / 5.53 s` and zero frames presented**; the new path presents **30 frames/s and loops once
  per 5.53 s cycle**. The wrap is now decided from the video's own clock (`currentTime`, which
  genuinely resets on a seek) and the stale `mediaTime` is dropped, while the pose still uses the
  presented frame. `requestVideoFrameCallback` also chains idempotently now — every `play` event used
  to start another chain that never died.

## [1.4.12] — Refine editor: edit the bone in local or global axes

Reported: *"in the refine editor the X, Y and Z are tied to global space — make an option to edit relative to local space available."*

### Added

* **Axis reference selector in the bone editor** (`local` / `global`), next to the bone picker. The
  X/Y/Z sliders and the live 3D preview both follow it, and the value travels with the edit
  (`space` on `POST /edit`, echoed in the report and stored in the edit history).
  * `local` — the bone's rotation **relative to its parent**; the axes follow the hierarchy, so the
    number you see is the joint's own contribution. This is what the editor already did, and it is
    the default, so **existing edits and saved histories are unaffected**.
  * `global` — the bone's **absolute orientation in the scene**; the axes stay fixed to the world, so
    the same number means the same thing on any bone and any frame.

  Measured on the raised arm of job `e63f073c2149` (frame 36): the left forearm reads
  `Z = -0.6°` local against `Z = -62.7°` global — the whole 62° is the shoulder, which is exactly the
  confusion the selector removes.

* Both spaces are **exact inverses of each other**: reading an angle and writing it straight back
  returns the identical quaternion (error < 1e-6°, covered by tests). A global edit converts to the
  bone's local rotation through the **parent's world rotation at the target frame** — the only
  consistent choice for a fully baked clip, where there is no curve to re-evaluate. At the root
  (`Hips`) the two coincide, since the parent is the identity.
* `GET /animation/{job_id}` now also returns `bone_parents` (the rig hierarchy) so a client can do
  the same global→local conversion in its own preview.
* An unknown `space` is rejected with a clear `400` instead of being silently ignored, on both the
  frame read and the edit.

### Verified

* pytest **160/160** (9 new tests: the two references really differ, exact round-trip per
  bone × space, a global edit lands on the requested world angle while the same number in local
  does not, invalid `space` errors, legacy edits without the field stay `local`, and
  `world_rotation` agrees with the FK).
* Live API checked over HTTP against a real job: both spaces read back, a global edit of
  `Z = 45°` reads back as `Z = 45°`, the default response is byte-identical to the old `local`
  behaviour, and `space=banana` returns `400`.

## [1.4.11] — Honest joints: geometric hinges, exact 2D reprojection, un-scaled SavGol

Reported on job `e63f073c2149` (`sword_swing_B`, t=1.19 s): *"the right arm is slightly curved in the video, and in the debug view too, but in the skeleton it is straight"* and *"the left arm is still inside the torso — it needs a better depth calculation, since in the video it is in front of the torso"*.

Three independent bugs, found by measuring the stored job offline (no guesswork: each fix is a before/after number on the real clip).

### Fixed

* **The arm was straightened by the constraints stage, not by the retarget.** The retarget reproduces the lifted pose exactly (0.00° error), but `constraints` then bent the elbow the wrong way: at t=36 the right elbow went from **45° (video) → 115°**, the left **158° → 179° (dead straight)**. Cause: the preset clamped the forearm/shin by an **Euler axis of the *local* rotation**, and that quaternion is `conj(q_parent) · q_world` — it carries the *shoulder/hip* rotation too, not the joint flexion. Measured on this clip, the forearm's Euler-Z spans **−175°…+172°**, so the "no hyperextension" clamp fired on **95/167 frames** and slerped up to **118°** of a rotation that was mostly shoulder raise.
  New constraint kind **`bend`** measures the **real joint angle from the geometry** (FK), where 0° = straight, + = flexed, − = hyperextension, and corrects by rotating the subtree about the joint's own hinge axis. The preset now uses `bend` for both elbows and knees; a `bend` rule on a non-hinge bone is rejected with a clear error. Elbow error at the reported frame: **69.3° → 8.8°**.
* **The arm started inside the torso because the lifter had no depth solution.** `_solve_depth` solved one joint at a time; when the 2D segment was *longer* than the bone (~45% of frames) there is no real `sqrt(L²−d²)`, and the code **scaled the child toward the parent in X/Y** — corrupting the image projection by up to **61 px** and flattening the arm — and emitted **z = 0**, exactly coplanar with the torso (**30% of elbows at z=0**), so anti-collision had nothing left to fix.
  The lifter now treats the image as ground truth: **X/Y are taken verbatim** (reprojection error **61 px → 0.00 px**), and only Z is unknown — solved per frame by Gauss-Newton over bone-length rigidity (soft), **torso non-penetration** (clearance measured in Z against a torso ellipse), temporal continuity, and a weak anthropometric prior. Limb depth is now non-zero in every frame.
* **The character floated 8 m above the ground.** `savgol_coeffs` returned `pinv(a)[0] * window`; the pseudo-inverse row already sums to 1, so the extra factor **scaled any signal by the window size** — a constant `root.y = 0.98 m` became **8.82 m** (`0.98 × 9`) in every job using the shipped `filters_default.yaml`. Coefficients are now normalized to sum 1 (a weighted average must return a constant unchanged).

### Verified

* On the reported job: arm reprojection onto the video **31.3 px → 25.8 px** mean (max **121.9 px → 93.9 px**); elbow at t=36 right **69.3° → 8.8°** error, left **21.1° → 9.2°**; arm-vs-torso capsule penetration mean **−0.183 m → −0.167 m**, frames overlapping > 2 cm **3.4% → 2.4%**; `root.y` preserved at 0.98 m.
* Performance: the lifter went from **12.1 s → 1.1 s** (per-frame constants hoisted out of the optimizer) and the new hinge pass from **26.9 s → 0.3 s** (FK hoisted to one evaluation per frame, restricted to the ~14 bones the hinges need).
* pytest: **151/151** (20 new regression tests in `tests/test_regressions.py`, one per defect: pixel-exact reprojection, hinge limit exactness/no-touch, torso non-penetration, SavGol constant preservation, root preservation, and an end-to-end "the pipeline must not straighten the elbow" check).

### Note

Jobs processed before this version carry all three defects and should be re-processed. The 2D-only backends (vitpose, rtmpose) are the affected ones; mediapipe was unaffected by the lifter changes because its 3D comes from the backend.

## [1.4.10] — Hand tracking actually animates + hands in the 2D debug overlay

### Fixed (reported: "the hand detector doesn't seem to be doing anything, and it doesn't show in the debug either")

* **Finger rotations were never applied (bone index bug)**: the landmark->bone mapping built the bone name with a hardcoded index `0` (`LeftHandIndex0` — a bone that does not exist), so every rotation was silently skipped: **0 finger rotations on every job** since the feature was added (the log reported it honestly). The index (1 = MCP->PIP, 2 = PIP->DIP, 3 = DIP->TIP) is now correct.
* **Hand pose mapping rewritten (skeleton math)**: the old code expressed the observed phalanges in a "hand frame" built from the hand's own landmarks — a basis unrelated to the rig — and ignored the body retarget's hand rotation; even with the index fixed the fingers came out scrambled. The new mapping resolves each joint inside the rig chain: `q1 = from_to(o2, rh^-1 . t1)`, `q2 = from_to(o3, (rh . q1)^-1 . t2)`, `q3 = from_to(o4, (rh . q1 . q2)^-1 . t3)`, where `oI` are the child bone rest offsets and `rh` is the hand's world rotation from the body retarget in that frame. A unit test reconstructs the observed phalanx directions from the output quaternions (agreement > 0.9999).
* **Hands now visible in the 2D debug overlay**: hand landmarks are persisted per job (`hands.json`, 21 points per hand in video pixels) and both debug overlays (main viewer and refine editor) draw them over the video — left hand blue, right hand terracotta — alongside the body keypoints.

### Verified

* Repro with the real clip: before = 0 rotations applied; after = **2670 rotations**, hands detected in **151/167 frames** (job `c13a77f7b4df`).
* Close-up render of the mesh: fingers curl in a natural C-shaped grip around the sword hilt, thumb on the correct side, not inside-out; a rest-pose control at the same camera shows the remaining blockiness is the source mesh (low-poly), not the animation.
* pytest: **131/131**.

## [1.4.9] — Stable rig: forearm twist eliminated + real-time refine editor

### Fixed (reported: "LeftForeArm rotates ~180° between frames 37-43 even though the LeftArm does not rotate"; "refine edit sliders stopped working in real time")

* **Parasitic forearm twist (retarget)**: bone orientations were rebuilt every frame as the minimal rotation from the rest direction (`quat_from_to(d0, d)`); when a limb swept a large arc (forearm crossing the chest), the reconstruction accumulated up to ~180° of twist about the bone's own axis while the arm stood still. Orientations now use **parallel transport** (rotate the previous frame's orientation by the minimal rotation between consecutive directions). Twisting rate on the reported clip: **−20…−50 °/frame → 0.0 °/frame** in the base; final clip through constraints→collision→filters: **+156° → +3.5°** accumulated (max 9°/frame in a single frame).
* **Euler extraction was not the inverse of euler construction**: `quat_to_euler_xyz_deg` returned negated angles (a +60° rotation read as −60°) and the round-trip missed by up to ~175°. The constraints clamp used both functions back-to-back, so near gimbal a 1.2° z-limit produced a **104°** rotation jump — the visible "impossible spin". The extraction is now the exact inverse (round-trip error 0.00°) and clamps act smoothly.
* **Refine editor: stale sliders (request races)**: every scrub fired `/frame/{t}`; late replies from older frames overwrote the euler panel (reproduced: bone+frame change showed frame-0 values at frame 42). A sequence guard now drops out-of-order responses — the last scrub wins.
* **Refine editor: live preview**: dragging rx/ry/rz rotates the selected bone immediately in the 3D views (same euler order as the server); "Apply" persists as before.
* **Bone labels**: the `mixamorig` prefix is stripped as it appears at runtime (`mixamorigHips` — three.js removes the colon from node names).
* **Body leaning sideways (lifted backends)**: the spine solve in the analytic lifter targeted the **left shoulder** as the direction reference for the chest center — biasing the chest ~0.18-0.21 m to the left and tilting the whole torso **~24°** in every frame on 2D-only backends (vitpose/rtmpose; mediapipe was unaffected since its 3D comes from the backend itself). The solve now targets the **mid-point of the shoulders** (as the code comment always intended); measured lean: **+24.3° → 0.0°** (vitpose), **+22.3° → +0.8°** (rtmpose). Long-standing bug: every previously processed job with lifting carries the lean and should be re-processed.

### Verified

* pytest **125/125**; FK validation unchanged (12.63 cm / 0.01°).
* Editor reproduced in a real headless Chromium: correct slider sync under rapid scrubbing, live preview updates both viewports, zero console errors.

## [1.4.8] — Stable head: eye-line orientation + singularity-free depth

### Fixed (reported: "the skeleton's head moves even though the head is still in the video")

* **Nose depth**: the `solve("neck")` used a rigid distance with the `sqrt(L²−d²)` term; when the 2D shoulder→nose distance dropped below 0.28 m, the nose "dived" up to ~0.19 m in ONE frame — the head picked up ~30° of yaw/pitch on its own. Now the nose stays on the chest plane (z of the shoulder center).
* **Head orientation from the eye line**: the `nose − shoulders` vector measures position, not orientation — as the body shifts across the frame it rotates (measured: 40° of roll in video A with the head still; 28.9° in B). The head direction now comes from the **eye line**, with adaptive smoothing (EMA with reduced weight when the eyes get short) and guards for degenerate frames (occlusion/profile).
* **World neutralization on frame 0**: the previous calibration only zeroed local rotations; the spine's contribution survived as a constant bias (24° sideways, measured). `calibrate_head` now also neutralizes Neck/Head in world space on the reference frame.

### Measured (A/B, 60 frames)

* A: visible tilt **40° → 0.71°** of amplitude (the video's own eye line measures 0.88°).
* B: **40° → 10.4°** (concentrated in the fast turn; mean 1.5°, std 2.2°); starts at 0.00°.

### Technical

* The user's skinning test was passing **because of the bug** (the head wobbled and the hair is ~half the vertices); it now measures the fraction of **limb** vertices that move — new `moved_fraction_limbs` metric in `tools/verify_glb_skinning.py`.
* 107 tests passing.


## [1.4.7] — Head calibration on the first frame

### Fixed

* **Head "looking down/sideways" on all default backends**: the solver orients the neck/head chain from a single estimated vector (`nose - chest`), whose depth comes from the monocular lifter and carried a **systematic bias** — measured between **55° and 75°** of rotation on `Neck` at frame 0 in real jobs (the local `Head` is always identity; all of the error lives in the neck).
* **New: calibration by the reference frame** (`core/retarget.py: calibrate_head`): the local rotation of `Neck`/`Head` at frame 0 becomes identity — the head starts at the rest orientation (facing the body's front) and **all relative motion is preserved** (test: q'(t1)⁻¹q'(t2) == q(t1)⁻¹q(t2)). Controllable via `params.head_calibration` (default `true`) and `params.head_calibration_frame` (default `0`); the result goes to the job log and to the `anim.json` `meta`.

### Verified

* E2E A/B on the same clip: `Neck |q0|` **75.46° → 0.00°** with calibration on; GLB/FBX exported normally; **107 tests** (4 new regression tests).


## [1.4.6] — RTMPose: `_onnx_cuda_ok` NameError fixed + GPU fallback

### Fixed

* **`ERRO: name '_onnx_cuda_ok' is not defined` on rtmpose**: the helper was called in `load()` but had never been defined in the module (a leftover from the GPU round). It now exists, registers the cuDNN DLLs before checking the provider, and the backend really uses CUDA — measured: **40 ms/frame on GPU** (steady state, 18 frames) against ~200 ms on CPU.
* The rtmpose `load()` gained the same care as vitpose: if the CUDA session fails to be created, it falls back to CPU with a reason; and if the GPU fails **during inference** (e.g., cuDNN), it falls back to CPU once and continues — no silent empty poses.
* The fallback reason (`_fallback_reason`) now shows up in the **job log** ("Aviso: ...") — before it was recorded on the object and never shown.

### Technical

* `core/gpu.py` gained `ensure_cuda_dlls()` (shared CUDA/cuDNN DLL helper); vitpose now delegates to it (a single source of truth).
* Static scan (pyflakes) across the whole project to make sure there are no other undefined names; new regression test `tests/test_backend_rtmpose.py`; **103 tests**.


## [1.4.5] — Reference video in the refine editor

### Added

* The refine editor gained the **reference video** panel (same artifact as the job; three panels: video · skeleton · mesh, as in the pipeline). The video **follows the scrub and playback**: each timeline frame positions the video at the corresponding instant (t / fps), with a guard against re-seeking the same instant on every step.
* When opening a job without `anim.json` (or on load failure), the video is unloaded together with the scene, instead of leaving the previous clip on screen.

### Validated

* `/refine` serves the panel (`id="refvideo"`); the video artifact responds 200 (`video/mp4`, 5.4 MB in the test job); 101 tests passing; `node --check` clean.


## [1.4.4] — Sync rotation, mesh in the refine preview and i18n (PT/EN)

### Added

* **sync rotation** in the pipeline and in the refine editor: a checkbox that keeps the camera in sync between the skeleton and mesh panels (live, both ways; the preference is remembered across sessions).
* **PT/EN internationalization of the whole interface** (pipeline + editor): `PT | EN` switcher at the top, initial language from `?lang=` / localStorage / browser, and every JS-generated string translated (hints, tables, warnings, constraints editor, editor log). Also localized: filter descriptions (`description_en`) and the notes of the 32 joints (`note_en`). Runtime logs and API errors remain in PT (known limit).

### Fixed

* **The refine editor preview now loads the job's processed MESH** (not the "capsule sticks"): the GLB from `/api/refine/animation/{id}/glb` and the refined export embed the user mesh (loaded from the job's `mesh.*`, cached per process). Verified: refine GLB byte-identical to the pipeline's (9,285 vertices / 17,916 triangles, with skin).

### Technical

* `core/gpu.py` and `core/provision.py` now expose **reason codes** (`reason_code`/`reason_vars`, `manual_code`) so the front end can localize without duplicating rules — the UI resolves PT/EN from the code, with a fallback to the text.
* Tests: 101 passing; `node --check` on the 3 JS modules; JS×HTML id consistency and repo/staging parity verified.


## [1.4.3] — Editor: opens the focused job and organized constraints

### Added

* **The refine editor automatically opens the job that is open in the main tab**: the pipeline page now syncs the focused job (including the result of a new processing run) and the editor picks, in order: `?job=` from the URL → synced job → most recent. The selection also goes into the URL (`/refine?job=…`), so reloading keeps the clip.

### Changed

* **Constraints reorganized** (`web/refine.js` + `web/style.css`): the flat list of 32 unstyled rows gave way to **groups by body region** (torso, head/neck, left/right arms, left/right legs, hands), collapsible, with **column legend** (joint · min° · max° · stiffness), **type chip** colored by axis (cone/x/y/z), **visual range bar** (−180°..180°), **search filter** by joint/type/note, **per-group count** and a **pending-changes highlight** on the Save button (with a visual warning for min > max).
* The filters checklist gained a style consistent with the rest of the page.


## [1.4.2] — Refine editor: playback and clip switching

### Fixed

* **The animation would not play in the editor**: the action was created **paused** and the scrub used `mixer.setTime()`; with the action paused, three.js uses an effective `timeScale` of 0 and the clip stays stuck at instant 0. Proven with **three.js 0.169** (the same version as the page) in an isolated script: `paused + setTime(1.5s)` leaves the property at 0; `action.time = 1.5 + mixer.update(0)` applies the interpolated value (15). The scrub now uses `action.time` + `update(0)` — the play button advances frames through the same path.
* **The old clip did not "unload" when switching jobs**: the `SkeletonHelper` stayed in the scene (the editor's `clear()` did not remove it — the main page's version did) and skeletons piled up. Now `clear()` removes helper and model (with `dispose`), runs **before** downloading the new clip, shows "carregando…" and, on failure, the reason in the overlay.
* Selecting a job without `anim.json` (an old one) now **clears the scene** and shows the reason — before, the previous clip stayed on screen as if nothing had happened.
* "Refined GLB/FBX" links are now per job: they no longer hang around when switching clips (reset; re-enabled only if the job has refined artifacts).
* The pose in the scene is applied **before** fetching the frame (playback does not depend on the network) and a failure of the euler panel no longer interrupts the animation.
* Rapid clip switching no longer lets an old load overwrite the new one (loading token).


## [1.4.1] — Refine routes restored and honest device in the interface

### Fixed

* **Empty filters dropdown (a real regression)**: the refine API include had been lost from `server/app.py` — the server started without `/api/refine/*` (silent 404) and without the `/refine` page. Restored, with a **route regression test** (`tests/test_server_routes.py`) that locks the route set via OpenAPI.
* **Latent circular import**: `install_api`/`refine_api` imported state from `app.py` at the top; importing either module *before* the app broke with `ImportError: cannot import name 'router' from partially initialized module`. The modules no longer depend on the app at boot (`store` is resolved lazily).
* **"ViTPose (ONNX, CPU)" hardcoded in the dropdown**: the name said CPU even when running on GPU. The label is now neutral and the interface shows the **real device** of each backend (GPU CUDA / GPU DirectML / GPU MPS / CPU), with the reason when it stays on CPU.
* The GPU label duplicated the vendor ("NVIDIA NVIDIA GeForce...") and MediaPipe's reason cited a vague cause — both fixed.
* Mojibake (`â€”`) in logs/docstrings/changelog.

### Documented

* **Why MediaPipe runs on CPU**: the pip desktop wheel is built without GPU support — `GPU processing is disabled in build flags` (verified on this machine, mediapipe 1.0.1). The GPU delegate only exists in the mobile builds (Android/iOS). On CPU the inference is fast (~13 ms/frame for hands).
* **Hand tracking and the model**: uses MediaPipe's HandLandmarker (`hand_landmarker.task`), isolated in `core/hands.py`; the model path can be overridden via `V2M_HAND_MODEL` or `params.hands.model_path`, and `min_conf`/`upscale` now come from the job (`params.hands`).


## [1.4.0] — Automatic GPU and hand tracking

### Added

* **GPU detection** (`core/gpu.py`): NVIDIA (nvidia-smi), AMD/Intel (WMI), Apple Silicon, ROCm — with a **per-backend compatibility matrix** and an explanation when the GPU is not usable (e.g., AMD on Windows with official PyTorch, which does not publish ROCm).
* **GPU-aware provisioning**: installs `onnxruntime-gpu` (+ `nvidia-cudnn-cu12` and `nvidia-cublas-cu12`), `onnxruntime-directml` or `onnxruntime` according to the hardware; for PyTorch, uses the CUDA build's `--index-url`; and downloads the **right model variant** (fp32 on GPU, int8 on CPU).
* **Real GPU use at runtime**: `CUDAExecutionProvider`/`DmlExecutionProvider` in the ONNX backends, with the cuDNN DLLs registered on the process `PATH`.
* **Inference fallback**: if the GPU fails at runtime, the backend falls back to CPU and records the reason — before, this silently produced empty poses.
* **Hand tracking** (`core/hands.py`): checkbox in the interface; downloads `hand_landmarker.task` (~8 MB), detects the hands (21 points) and animates the **40 finger bones**, which used to stay at identity. Parameters `min_conf` (default 0.3) and `upscale` for small hands in the video.

### Measured

* RTX 5060 Laptop: **8.0 ms/frame (GPU)** vs **77 ms/frame (CPU)** on ViTPose-ONNX — ~10×, with an equivalent score (0.80).
* Hand tracking on the test video (sword swing): hands detected in 10/40 frames with `min_conf=0.2` (0/40 at the default 0.5 threshold) — small hands require a lower threshold.

## [1.3.0] — Job history tab

### Added

* **History tab** in the interface (next to "New job"): lists the jobs stored in SQLite with id, date, video, backend, status and frames; filter by **status** and **backend**; and per-row actions — **open** in the viewer (without reprocessing), download **GLB/FBX** and go to that clip's **refine editor**.
* `GET /api/jobs` now accepts `?status=`, `?backend=` and `?limit=` (up to 500).
* The editor accepts `?job=<id>` to open directly on the clip coming from the history.
* When a new job finishes, the history list is refreshed automatically.

## [1.2.0] — Automatic backend installation

### Added

* **Per-backend provisioning** (`core/provision.py`): declarative plans with `pip`, `git` and `download` steps, executed inside the project (`models/`, `third_party/`), without touching the system.
* **API**: `GET/POST /api/backends/{name}/install` (status + log + report in the background) and the `install` field in `GET /api/backends`.
* **Interface**: the dropdown shows "installable" / "manual step" instead of "unavailable", with an **"Install automatically"** button and a live log; when done, availability is reloaded.
* Cloned backends are now detected automatically (`third_party/...`), with no environment variable.
* `tests/test_provision.py` tests ensuring every visible backend has a coherent plan and that the "manual" ones explain the reason and do not attempt to install.

### Notes

* Three backends remain **manual** for real impossibility: `openpose` (C++ build), `sam3dbody` (Meta's own terms) and `wham` (SMPL body under Max Planck's license acceptance).
* `yolopose` installs, but `ultralytics` drags in PyTorch (~2.5 GB) — the interface warns beforehand.

## [1.1.0] — Animation refinement layer

### Added

* **Stabilization filters** (`core/refine/filters.py`): six methods besides One-Euro — `moving_average`, `savgol`, `kalman`, `butterworth`, `double_exponential` — all in **pure numpy** (no scipy), configurable per bone, per axis and per frame range.
* **Filtering plan** (`core/refine/plan.py`) with a reapplicable YAML file (`config/filters_default.yaml`).
* **Joint constraints** (`core/refine/constraints.py`) with the full **humanoid preset** (32 limits) in an editable file `config/constraints_humanoid.yaml`; supports **cone** limits (total deviation) and per-**axis** limits with asymmetric min/max (elbow/knee without hyperextension), with `stiffness` and smooth correction via slerp.
* **Bone editor with per-keyframe rebake** (`core/refine/boneedit.py`): pick the bone, target frame and affected range; interpolates with ease-in-out between the last unaffected frame, the edited frame and the first unaffected one after it — **rewriting every frame** (bake), no curves in the final file.
* **Persisted history** (undo/redo) with author, note and timestamp; the session survives a restart.
* **Comparison report** (`core/refine/report.py`): smoothness (jerk energy), lag (cross-correlation) and residual deviation (RMSE + angular error) per filter, in Markdown and JSON.
* **CLI** `tools/refine.py` (`--make-samples`, `--compare`, `--inject-violation`, `--out`).
* **REST API** `server/refine_api.py` and **web editor** at `/refine`.
* **Tests** `tests/test_refine.py` (16 cases) covering the acceptance criteria.

### Fixed

* `mixamo.quat_normalize` used `np.linalg.norm(q)` without an axis: on a `(T,4)` series this computed the **Frobenius** norm and divided the whole series by it, leaving every quaternion with the wrong norm (a silent bug, exposed when filtering series). It now normalizes on the last axis and accepts 1 or N quaternions.
* `constraints.apply_constraints` rewrote every frame of the bone even without a violation; it now writes only when there is a correction, preserving bit-exactly what was not touched.

### Environment requirements

* Python 3.11+ (tested on 3.13) · numpy ≥ 1.26 · PyYAML ≥ 6.0
* No new dependencies: the layer uses only numpy/pyyaml (already present) — **scipy is not needed** (savgol and butterworth implemented by hand).
* CLI: `python tools/refine.py --make-samples` works with the project venv.

## [1.0.0] — Video → Mixamo animation pipeline

* Pose backends with a license category, retarget to the 65-bone Mixamo rig, GLB/FBX export, mesh editor (FBX/GLB) and 3D preview with a reference video. See `README.md`.
