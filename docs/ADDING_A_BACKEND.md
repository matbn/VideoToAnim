# Adding a new pose backend

## Português

Este documento também está disponível em português: [ADDING_A_BACKEND.pt-BR.md](ADDING_A_BACKEND.pt-BR.md).

There are two paths. **Neither requires editing the core or the frontend.**

## Path A — Python plugin (recommended)

1. Create `plugins/my_backend.py`:

```python
from typing import Sequence
import numpy as np
from core.adapter import AdapterConfig, ConfigurableAdapter
from core.canonical import COCO17

class MyBackend(ConfigurableAdapter):
    def __init__(self):
        cfg = AdapterConfig(
            name="my_backend",               # unique id -> shows up in the dropdown
            display_name="My Backend",
            native_layout=["j0", "j1", ...], # joint names in NATIVE order
            mapping={                        # native name -> COCO-17 index
                "j0": COCO17.index("nose"),
                "j1": COCO17.index("left_shoulder"),
                # ...
            },
            coord="pixel",                   # "pixel" | "normalized"
            y_flip=False,                    # True if the backend uses y up
            score_map={"j0": 2, ...},        # optional: score index per joint
            smoothing="none",                # "none" | "oneeuro"
            license="MIT",
            license_category="livre",       # livre | nao_comercial | licenca_a_parte
            notes="what this detector has that is peculiar",
        )
        super().__init__(cfg)

    def is_available(self) -> bool:
        try:
            import my_lib  # noqa
            return True
        except Exception:
            return False

    def availability_reason(self) -> str:
        return "requires 'pip install my-lib'"

    def load(self, config: dict | None = None) -> None:
        self._model = ...  # load weights here
        self._loaded = True

    def infer_raw(self, frames: Sequence[np.ndarray], ctx: dict) -> list[dict]:
        out = []
        for frame in frames:
            kp = ...    # (N, 2) in native_layout order
            sc = ...    # (N,) scores in [0,1]  (or None)
            out.append({"kp": kp, "score": sc} if kp is not None else None)
        return out

BACKEND = MyBackend()   # <-- the registry looks for BACKEND or BACKENDS
```

2. Restart the server (or reload the page) and confirm:

```powershell
curl http://127.0.0.1:8000/api/backends
```

`my_backend` shows up in the list and in the dropdown. If it is the first **available** backend and
the preferred one (`vitpose`) is not installed, it becomes the effective default.

3. Run the contract tests — they **iterate every registered backend**,
   so yours joins automatically:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_adapter_contract.py tests/test_backend_matrix.py -q
```

> Tip: add your backend id to the `REGISTRABLE` list in `tests/test_backend_matrix.py` so
> it also crosses the full pipeline in the matrix test.

## Path B — YAML descriptor with an entrypoint

If the backend already lives in an importable package:

```yaml
# plugins/my_backend.yaml
name: my_backend
entrypoint: my_package.module     # module that exposes BACKEND
metadata:
  license: MIT
```

## Peculiarities the Adapter solves by configuration

| Peculiarity | How to handle it |
|---|---|
| Different joint order | `native_layout` + `mapping` |
| Normalized coordinates ([0,1]) | `coord="normalized"` (the Adapter multiplies by w/h) |
| y up (2D/3D) | `y_flip=True` (the Adapter converts to y-down) |
| Camera depth/proximity | **watch the Z sign**: our canonical uses **+Z = front** (the T-pose has the feet at +Z). If your backend uses "negative z = closer to the camera" (like MediaPipe), flip the sign — see `backends/mediapipe_backend.py::_world` |
| Score in a separate channel | `score_map={native_joint: score_index}` |
| Missing points | return a low `score`; use `score_threshold` |
| Multi-person | pick the highest-confidence bbox/person and return only that one (`multi_person=True` documents it) |
| Smoothing baked into the model | keep `smoothing="none"` to avoid smoothing twice |
| Native 3D output | fill `extra={"kp3d": ...}` or override `kp3d` in `postprocess()` |

## `ConfigurableAdapter` hooks

- `infer_raw(frames, ctx)` — **required**: the model call.
- `postprocess(pose, index, ctx)` — optional: per-frame adjustments (e.g., inject 3D).
- `is_available()` / `availability_reason()` — optional: honest availability.

## Canonical skeleton conventions (COCO-17)

`0 nose, 1 left_eye, 2 right_eye, 3 left_ear, 4 right_ear, 5 left_shoulder,
6 right_shoulder, 7 left_elbow, 8 right_elbow, 9 left_wrist, 10 right_wrist,
11 left_hip, 12 right_hip, 13 left_knee, 14 right_knee, 15 left_ankle,
16 right_ankle` — in **absolute pixels**, **y down**, `score` in [0,1].
In 3D: **meters**, **y up**, **+Z = the character's front**.

## Controlling whether the backend shows up in the dropdown

| attribute | effect |
|---|---|
| `license_category` | `"livre"` / `"nao_comercial"` / `"licenca_a_parte"` (constants `CAT_LIVRE`, `CAT_NAO_COMERCIAL`, `CAT_LICENCA_A_PARTE`). Shows as a colored label in the dropdown, in the API and in the job panel. Also feeds `registry.by_license_category()`. |
| `license` | free text with the exact license (e.g., `"AGPL-3.0 (Ultralytics)"`). Shown in the hint when selecting. |
| `hidden_from_ui = True` | the backend stays **registered** and runnable, but does **not appear** in the dropdown. |
| `PLUGIN_DISABLED = True` (module) | the plugin is **not loaded**: it stays on disk as an example, out of the registry, without raising an error. State of `plugins/example_backend.py`. |

In the first case, `GET /api/backends?include_hidden=true` still lists it; in the second, it only
comes back to existence when `PLUGIN_DISABLED` is removed/False.
