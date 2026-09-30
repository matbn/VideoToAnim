# API do refinamento

## English

This document is also available in English: [REFINE_API.md](REFINE_API.md).

Base: `http://127.0.0.1:8000`. Complementa `docs/API.pt-BR.md` (pipeline de vídeo).

## Filtros e constraints

### `GET /api/refine/filters`

```json
{"filters": [{"name": "butterworth", "params": {"cutoff_hz": 6.0, "order": 2, "zero_phase": true},
              "description": "Passa-baixa Butterworth. zero_phase=True nao introduz atraso."}]}
```

### `GET /api/refine/plan`

Plano de filtragem padrão (de `config/filters_default.yaml`) como objeto JSON.

### `GET /api/refine/constraints`

```json
{"path": ".../config/constraints_humanoid.yaml",
 "preset": {"name": "humanoid", "default_stiffness": 1.0,
            "limits": [{"bone": "Head", "kind": "cone", "min_deg": -180, "max_deg": 50, "stiffness": 1.0}]}}
```

### `PUT /api/refine/constraints`

Corpo: `{"preset": {...}}`. Valida e **salva no YAML** do projeto.
`400` com mensagem clara para: `min_deg > max_deg`, `stiffness` fora de [0,1], osso inexistente,
`kind` inválido, limites conflitantes.

```json
{"saved": ".../config/constraints_humanoid.yaml", "limits": 32}
```

## Clipe e editor de bone

### `GET /api/refine/animation/{job_id}`

Metadados do clipe atual (já com as edições) e o histórico.

```json
{"job_id": "d59925e9a2a3", "fps": 30.0, "num_frames": 60,
 "animated_bones": ["Hips", "Spine", "..."],
 "root_translation": [[0.0, 0.93, 0.0], "..."],
 "history": {"session_id": "...", "edits": [{"bone": "Head", "frame": 30, "start": 20, "end": 45,
              "author": "editor", "note": "virar cabeca"}], "undone": []}}
```

`409` se o job não tiver `anim.json` (rode o pipeline de novo — a animação bakeada passou a ser salva).

### `GET /api/refine/animation/{job_id}/frame/{t}`

Pose do frame `t` (quaternion + Euler em graus) para todos os ossos animados. Fora do intervalo do
clipe devolve `400`.

### `GET /api/refine/animation/{job_id}/glb`

Exporta o **clipe atual** (com as edições) em GLB — é o que alimenta a prévia do editor.

### `POST /api/refine/animation/{job_id}/edit`

```json
{"bone": "Head", "frame": 30, "rotation_euler_deg": [0, 40, 0],
 "start": 20, "end": 45, "author": "editor", "note": "virar cabeca"}
```

Campos aceitos para o alvo: `rotation_euler_deg` (3) ou `rotation` (4, quaternion) ou `translation`
(3 — **só no osso `Hips`**). `start`/`end` definem o intervalo afetado (default: só o frame).

Resposta:

```json
{"report": {"bone": "Head", "frame": 30, "affected": [20, 45], "frames_rewritten": 26,
            "anchors": [19, 46], "author": "editor"}, "history_len": 1}
```

`400` para: frame fora do clipe, intervalo inválido, frame editado fora do intervalo, osso
inexistente, osso end-cap, edição sem alvo, `translation` fora do `Hips`.

### `POST /api/refine/animation/{job_id}/undo` · `/redo` · `/reset`

Desfaz/refaz uma edição (reconstruindo o clipe do original + histórico) ou limpa todas.

### Sessão persistida

O clipe atual e o histórico ficam em `storage/refine/<job_id>/current.json` e `session.json` —
sobrevivem a reinício do servidor. Fechar e reabrir mantém as edições aplicadas.

## Comparativo e refino final

### `POST /api/refine/compare/{job_id}`

```json
{"filters": ["one_euro", "savgol", "kalman"], "params": {}}
```

Resposta: `{"rows": [{"filtro": "one_euro", "ganho_suavidade_%": 98.79, "atraso_frames": 0,
"rmse": 0.018755, "erro_angular_medio_deg": 3.938, ...}], "markdown": "..."}`

### `POST /api/refine/animation/{job_id}/apply`

```json
{"use_filters": true, "use_constraints": true, "filters": ["one_euro", "savgol"]}
```

Aplica constraints → filtros sobre o clipe atual, gera `model_refined.glb` / `model_refined.fbx`
(baixáveis por `/api/jobs/{id}/artifacts/glb_refined` e `fbx_refined`) e grava o relatório em
`storage/refine/<job_id>/refine_report.json`.

## Valores válidos

* filtros: `one_euro`, `moving_average`, `savgol`, `kalman`, `butterworth`, `double_exponential`
* `kind` de constraint: `cone`, `x`, `y`, `z`
* `axis` de filtro: `x`, `y`, `z`, `w`
