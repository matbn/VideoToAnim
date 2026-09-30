# Known limitations

## Português

Este documento também está disponível em português: [LIMITATIONS.pt-BR.md](LIMITATIONS.pt-BR.md).

## Retarget / animation

1. **No absolute root motion.** The canonical skeleton is anchored to the image plane; the character's displacement across the scene is not recovered (the hips stay at pelvic height with the residual motion). For locomotion you need a global camera/3D estimator (GLAMR/GVHMR/TRAM — out of scope).
2. **Fingers at identity.** COCO-17 has no hand joints; the 40 finger bones stay in T-pose. Hand pose would require a hand-pose backend.
3. **Twist is not determined by a single vector.** The retarget uses minimum arc (swing). Regions where roll/twist matters (forearm pronation) can look slightly ambiguous. The FK validation measures **directional** error (< 5°); the residual **positional** error (~7 cm) comes from the proportion mismatch between the source and the Mixamo rig's fixed bone lengths.
4. **One person per video.** Multi-person videos use the highest-confidence person (no manual-selection UI).

## Backends

5. **ViTPose** requires its own install (torch + MMPose/mmcv). In this build it is **registered and reports availability honestly**, but inference depends on the setup documented in `docs/BACKENDS.md`.
6. **MediaPipe** runs on CPU — and not by our choice: the pip desktop wheel is built without GPU support (`GPU processing is disabled in build flags`, verified on mediapipe 1.0.1 on this machine); the GPU delegate only exists in the mobile builds (Android/iOS). On CPU the inference is fast (~13 ms/frame for hands) and it remains the cheapest free backend to enable (`pip install mediapipe`).
7. **Licenses:** nothing is hidden — every backend carries its **category** in the dropdown (`livre` / `nao_comercial` / `licenca_a_parte`). For a commercial product, use only the `livre` ones (`vitpose`, `mediapipe`, `rtmpose`, `motionbert`). `openpose` and `simplebaseline` are non-commercial; `wham` depends on SMPL (non-commercial); `yolopose` is AGPL-3.0; `sam3dbody` has its own license. Full matrix: `docs/LICENSING.md`.
8. **Third-party weights** always deserve a license check, even when the code is permissive.

## Export

9. **ASCII FBX without mesh.** The generated FBX contains skeleton + animation (a rig importable in Blender/Unity/Unreal). It does not include skinning/mesh or materials. For an FBX with mesh, the path is headless Blender (`blender -b -P`), which is not a project dependency.
10. **ASCII FBX** was validated structurally (65 `LimbNode`s, connections, curves). Import validation in Blender/Unity is manual.
11. **Preview mesh**: by default it is procedural "capsule sticks"; if you upload an **FBX** character (Mixamo's standard) or a `.glb` with a Mixamo skeleton, the animation is applied to its mesh, without conversion (see `docs/MESH.md`). ASCII FBX is not read.

## Infra

12. **Serialized queue.** One worker (to avoid GPU contention). Long jobs queue up.
13. **No authentication.** This is a local tool; do not expose it to the internet.
14. **3D preview** loads three.js via CDN (needs internet on first load).
15. **Reference video**: the viewer plays the uploaded video next to the skeleton, synced by the same timeline. The video is served by the server itself (`/api/jobs/{id}/artifacts/video`, with Range) and stays on disk as long as the job exists.
16. **Analytic lifter** is heuristic (anthropometric priors + depth continuity). For truer 3D, swap it for MotionBERT (the `Lifter` interface is ready) or use a backend that already delivers 3D.
