# Arquitetura — video2mixamo

## Fluxo

```
      ┌────────────┐
      │  Navegador │  SPA three.js (dropdown, upload, status, preview 3D)
      └─────┬──────┘
            │ HTTP (REST + multipart)
      ┌─────▼──────────────────────────────────────────┐
      │ FastAPI  (server/app.py)                        │
      │  /api/backends  /api/jobs  /api/jobs/{id}       │
      │  /api/jobs/{id}/artifacts/{glb|fbx}             │
      └─────┬──────────────────────────────────────────┘
            │ submete job (ThreadPoolExecutor, 1 worker)
      ┌─────▼───────────────┐
      │ JobStore (SQLite)   │  id, video, backend, params, status,
      │ storage/jobs.db     │  timestamps, log, erro, artefatos, métricas
      └─────┬───────────────┘
            │
      ┌─────▼──────────────────────────────────────────────────────┐
      │ Pipeline (core/pipeline.py)                                 │
      │                                                             │
      │  1. video.read_video()        frames BGR                    │
      │  2. BackendRegistry.get()  →  PoseBackend (Adapter)         │
      │  3. Adapter.infer_video()  →  FramePose (COCO-17 canônico)  │
      │  4. smoothing (one-euro)                                    │
      │  5. AnalyticLifter (se o backend só dá 2D) → kp3d em metros │
      │  6. Retargeter.retarget()  →  Animation (65 ossos Mixamo)   │
      │  7. (opcional) check_compatibility(malha) → anexa ou avisa  │
      │  8. build_glb() + build_fbx()                               │
      └─────┬──────────────────────────────────────────────────────┘
            │
      storage/jobs/<job_id>/model.glb , model.fbx
```

## Módulos

| Arquivo | Responsabilidade |
|---|---|
| `core/canonical.py` | Esqueleto canônico COCO-17 e `FramePose`. |
| `core/adapter.py` | `PoseBackend` (ABC) + `AdapterConfig` + `ConfigurableAdapter`. |
| `core/registry.py` | Registro e descoberta de backends (embutidos + `plugins/`). |
| `core/smoothing.py` | Filtro One-Euro. |
| `core/lifter.py` | Lifter 2D→3D plugável (analítico por padrão). |
| `core/mixamo.py` | Contrato dos 65 ossos, quaternions e FK. |
| `core/retarget.py` | Solver juntas→rotações locais + validação FK reverso. |
| `core/export_glb.py` | Container GLB (skins + animação + malha do usuário ou capsule sticks). |
| `core/export_fbx.py` | Escritor FBX ASCII 7.4 puro. |
| `core/mesh.py` | Inspeção da malha enviada, compatibilidade (65 ossos) e carga (FBX/GLB). |
| `core/fbx.py` | Leitor de FBX binário 7.x em Python puro (nós, propriedades, arrays deflate). |
| `core/jobs.py` | `JobStore` SQLite. |
| `core/pipeline.py` | Orquestração. |
| `core/video.py` | Leitura de vídeo (OpenCV). |
| `backends/*.py` | 11 backends de pose, cada um com sua **categoria de licença** (`livre` / `nao_comercial` / `licenca_a_parte`). |
| `plugins/` | Backends externos (descoberta automática). |
| `server/app.py` | API FastAPI + montagem da SPA. |
| `web/` | SPA (HTML/CSS/JS + three.js). |

## Decisões de projeto

1. **Esqueleto canônico único (COCO-17).** Todo backend converte para COCO-17
   (pixel absoluto, y para baixo, score em [0,1]). ViTPose já é COCO-17;
   MediaPipe (BlazePose 33) tem tabela de remapeamento declarativa na `AdapterConfig`.
   Os backends com conflito comercial (`openpose`, `simplebaseline`, `mhformer`)
   foram removidos do produto — ver `docs/LICENSING.md`.
2. **Adapter declarativo.** As "peculiaridades" de cada detector (ordem de
   juntas, coordenada normalizada/pixel, y invertido, score, suavização,
   multi-pessoa) são dados de configuração, não código duplicado.
3. **Descoberta automática de plugins.** O `BackendRegistry` varre `plugins/`
   em busca de `*.py` com `BACKEND`/`BACKENDS` e de `*.yaml` com `entrypoint`.
   O dropdown é populado a partir de `/api/backends`.
4. **Rigidez do retarget.** Os comprimentos de osso vêm sempre do offset de
   rest; por frame só se calcula a **direção** alvo (quaternion from-to com
   proteção anti-180°) e a conversão mundo→local. Assim o rig nunca
   encolhe/estica e não há inversões grosseiras.
5. **Unidades e eixos.** O retarget exporta em **metros, Y-up, frente +Z**
   (convenção glTF/three.js). O FBX do Mixamo usa centímetros — por isso
   importadores mostram escala 0.01; nosso GLB já sai em metros.
6. **UI = FastAPI + SPA.** Escolha justificada em
   `docs/UI_FRAMEWORK_COMPARISON.md` (requisitos de timeline/playback do
   three.js não são atendidos de fábrica por Gradio/Streamlit).
7. **Exportação sem Blender.** GLB escrito à mão (validado com `pygltflib`
   nos testes) e FBX ASCII 7.4 escrito em Python puro.
