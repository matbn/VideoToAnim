# API HTTP

## English

This document is also available in English: [API.md](API.md).

Base: `http://127.0.0.1:8000`

## `GET /api/health`

```json
{"status": "ok", "backends": 4, "registry_errors": []}
```

## `GET /api/backends`

Lista os backends do registry (é o que popula o dropdown).

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

- `preferred` — o backend **preferido** como padrão (`vitpose`).
- `default` — o padrão **efetivo**: o preferido se estiver disponível; caso contrário, o primeiro
  backend visível e disponível. É esse valor que a interface pré-seleciona.
- `hidden_count` / `disabled_plugins` — transparência sobre backends que existem mas não aparecem.
- `?include_hidden=true` — inclui os ocultos (útil para inspeção/testes).

## `POST /api/mesh/inspect` (multipart)

Diagnóstico de uma malha **sem processar vídeo**: `mesh` = arquivo `.fbx`/`.glb`.

```json
{"ok": true, "filename": "MixamoChar.fbx", "bytes": 5669168,
 "report": {"format": "fbx-binary", "matched": 65, "missing_count": 0,
            "compatible": true, "attachable": true, "messages": []},
 "attach": {"ok": true, "vertices": 9285, "triangles": 17916,
            "stats": {"meshes": 6, "vertices": 9285, "unweighted": 0}}}
```

## `POST /api/jobs` (multipart)

| campo | tipo | descrição |
|---|---|---|
| `video` | arquivo | vídeo mp4/mov (limite 512 MB) |
| `backend` | string | id do backend (`mediapipe`, `vitpose`, `synthetic`, ...) |
| `params` | string JSON | parâmetros do job |
| `mesh` | arquivo (opcional) | malha Mixamo `.fbx`/`.glb` para substituir os capsule sticks |

> A malha também pode ser conferida antes, pelo endpoint `POST /api/mesh/inspect`.

Rejeita com `400` se o backend não existir **ou não estiver disponível**.

Parâmetros aceitos em `params`:

| chave | default | descrição |
|---|---|---|
| `fps` | fps do vídeo | fps da animação de saída |
| `smoothing` | `"oneeuro"` | `"oneeuro"` ou `"none"` |
| `lift_2d_to_3d` | `true` | aplica o lifter analítico se o backend só der 2D |
| `max_frames` | todos | limita o número de frames processados |
| `refine` | — | camada de refino (ver `params.refine` abaixo) |

### `params.refine` (opcional)

Roda a camada de refinamento **antes** do export. Aceita `true` como atalho para os arquivos padrao
do projeto, ou um caminho/dict:

```json
{"refine": {"constraints": true,
            "filters": {"apply_to_translation": true,
                        "filters": [{"name": "kalman", "start": 0, "end": 120}]},
            "edits": [{"bone": "Head", "frame": 30, "rotation_euler_deg": [0, 30, 0],
                       "start": 20, "end": 45}]}}
```

Ordem: constraints -> filtros -> edicoes. O log do job registra os estagios e os filtros aplicados
(ex.: `Refino aplicado: constraints -> filters (filtros: kalman)`).

Resposta `201`:

```json
{"job_id": "a1b2c3d4e5f6", "status": "queued", "mesh": "MixamoChar.fbx"}
```

## Instalação de backends

### `GET /api/backends/{nome}/install`

Estado da instalação daquele backend, mais o plano:

```json
{"backend": "vitpose", "status": "idle|running|done|error", "log": "...",
 "report": {"ok": true, "available_after": true, "results": [...]},
 "plan": {"kind": "auto", "total_mb": 98.0, "manual_reason": "",
          "steps": [{"kind": "pip", "target": "onnxruntime", "size_mb": 15}]},
 "label": "instalável automaticamente"}
```

`GET /api/backends` inclui, por backend, `install: {kind, label, manual_reason, total_mb, installable}`.

### `POST /api/backends/{nome}/install?force=false`

Dispara a instalação em background (roda em paralelo; acompanhe pelo `GET` acima). `400` para
backends **manuais**, com o motivo exato:

> `openpose nao pode ser instalado automaticamente: exige compilar C++ (CMake + CUDA/OpenCL) ...`

`kind` do plano: `auto` (pip/download), `pip-heavy` (instala, mas arrasta dependência grande) e
`manual` (não automatizável).

### `GET /api/jobs?status=&backend=&limit=`

Lista o histórico (mais recentes primeiro). Filtros opcionais por `status`
(`queued|running|done|error`) e `backend`; `limit` vai até 500.

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
  "error": "", "log": "[12:00:01] Job iniciado...\n",
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

Estados: `queued` → `running` → `done` | `error`.
`mesh_report` é `null` quando nenhuma malha foi enviada.

### Interpretação do `mesh_report`

| campo | significado |
|---|---|
| `matched` | ossos do seu arquivo que casam com o contrato Mixamo (até 65) |
| `missing` | ossos exigidos que faltam na sua malha |
| `extra` | ossos fora do padrão (ignorados) |
| `compatible` | o esqueleto serve para receber a animação |
| `attachable` | a malha foi **de fato** usada no output (FBX binário ou GLB) |

## `GET /api/jobs/{id}/artifacts/{name}`

`name` = `video` | `glb` | `fbx`.

- `video` → `video/mp4` (ou `video/quicktime`), servido **inline**, com suporte a **Range**
  (`206 Partial Content`) — permite tocar e fazer scrub do vídeo de referência junto do esqueleto.
- `glb` → `model/gltf-binary` · `fbx` → `application/octet-stream`.

## `GET /`

Serve a SPA (`web/index.html`); os estáticos ficam em `/static/*`.
