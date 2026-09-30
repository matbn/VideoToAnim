# Changelog

## Português

Este documento também está disponível em português: [CHANGELOG.pt-BR.md](CHANGELOG.pt-BR.md).

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
