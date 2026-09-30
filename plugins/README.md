# Backend plugins (extend without touching the core)

## Português

Este documento também está disponível em português: [README.pt-BR.md](README.pt-BR.md).

Drop a file here and it shows up in the dropdown automatically.

## Format A — Python module (recommended)

`plugins/my_backend.py`:

```python
from core.adapter import AdapterConfig, ConfigurableAdapter
from core.canonical import COCO17

class MeuBackend(ConfigurableAdapter):
    def __init__(self):
        cfg = AdapterConfig(
            name="meu_backend",                 # unique id (shown in the dropdown)
            display_name="My Backend",
            native_layout=list(COCO17),         # order of the native keypoints
            mapping={n: i for i, n in enumerate(COCO17)},  # native -> COCO-17
            coord="pixel",                      # "pixel" | "normalized"
        )
        super().__init__(cfg)

    def is_available(self) -> bool:
        return True

    def infer_raw(self, frames, ctx):
        # returns, per frame: {"kp": (N,2), "score": (N,) | None} or None
        ...

BACKEND = MeuBackend()
```

Restart the server (or reload the page) and the backend appears in the dropdown.

## Format B — YAML with entrypoint

`plugins/my_backend.yaml`:

```yaml
name: meu_backend
entrypoint: my_package.module        # importable module exposing BACKEND
metadata:
  license: MIT
```

## Rules

- `__init__.py` and files starting with `_` are ignored.
- The `mapping` is the core piece: it is where you describe your detector's
  peculiarities (joint order, normalized/pixel coordinates, inverted y,
  score). No core edits.
- If `is_available()` returns `False`, the backend appears in the dropdown
  marked as unavailable, with the reason in `availability_reason()`.
