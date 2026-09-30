# Backends

## English

This document is also available in English: [BACKENDS.md](BACKENDS.md).

Esqueleto canônico de todos: **COCO-17**, pixel absoluto, y para baixo, score [0,1].
A **categoria de licença** de cada um aparece no dropdown (ver `docs/LICENSING.pt-BR.md`).

O dropdown mostra 10 backends; `synthetic` fica oculto (infra de teste).

## `livre` — pode usar comercialmente

### `vitpose` — ViTPose (**padrão**)

| | |
|---|---|
| Tipo | top-down, heatmap, single-frame |
| Instalação | **`pip install onnxruntime`** — o modelo (~83 MB) é baixado na 1ª execução |
| Motor | ONNX Runtime em CPU (**~60 ms/frame** medido) |
| Detector | sem detector usa o frame inteiro (aceita `params.bbox`) |
| Licença | **Apache-2.0** |

> **Por que ONNX e não MMPose:** `mmcv` não publica wheel para Python 3.13 (medido com
> `pip download mmcv --only-binary=:all:` → *No matching distribution found*). O caminho MMPose
> segue disponível como legado (`params.engine = "mmpose"`) em Python ≤ 3.12.

### `mediapipe` — MediaPipe Pose (BlazePose)

Detector+tracker single-person. 33 pontos → COCO-17. `pose_world_landmarks` dá 3D em metros
(dispensa o lifter). **Apache-2.0.**

### `rtmpose` — RTMPose (rtmlib, ONNX)

| | |
|---|---|
| Tipo | top-down SoTA "leve", COCO-17 nativo |
| Instalação | **`pip install rtmlib --no-deps`** (usa o onnxruntime já instalado) |
| Modelo | ONNX oficial (~48 MB) baixado na 1ª execução |
| Desempenho | **~200 ms/frame** em CPU (medido) |
| Licença | **Apache-2.0** (RTMPose/OpenMMLab) · `rtmlib` MIT |

### `motionbert` — MotionBERT (lifting 2D→3D)

Backbone unificado (ICCV 2023), referência em lifting. H36M-17, janela temporal.
**Apache-2.0** (código e pesos do repo oficial). Requer torch + pesos.

## `licenca_a_parte` — avalie antes de usar

| backend | por quê |
|---|---|
| `yolopose` (Ultralytics) | **AGPL-3.0**: copyleft forte; produto/serviço exige abrir código ou licença Enterprise |
| `sam3dbody` (Meta, MHR) | **SAM License**: uso comercial permitido, mas share-alike + restrições de export/militar |
| `mhformer` | código MIT, **pesos sem licença declarada** (all rights reserved por padrão) |

## `nao_comercial` — só pesquisa

| backend | por quê |
|---|---|
| `openpose` | LICENSE da CMU: *"ACADEMIC OR NON-PROFIT ORGANIZATION NONCOMMERCIAL RESEARCH USE ONLY"* (código **e** pesos) |
| `simplebaseline` | código MIT, mas *"All models are provided for research purpose"* |
| `wham` | código MIT, mas depende do **SMPL/SMPL-X** (licença non-commercial da Max Planck) |

## Oculto

`synthetic` — gerado do próprio rig Mixamo, determinístico, sem dependências. **MIT** (deste
projeto). Fica fora do dropdown (`hidden_from_ui`) mas segue no registry para os testes; a matriz de
testes o usa como referência do contrato do Adapter.

## Estado de execução nesta máquina

| backend | disponível | observação |
|---|---|---|
| `vitpose` | ✅ | roda (ONNX, CPU) |
| `mediapipe` | ✅ | roda (Tasks API, CPU) |
| `rtmpose` | ✅ | roda (rtmlib, CPU) |
| `motionbert` | ❌ | requer torch + pesos |
| `yolopose` | ❌ | requer `pip install ultralytics` |
| `sam3dbody` | ❌ | requer pacote/checkpoint oficiais |
| `wham` | ❌ | requer repo + corpo SMPL |
| `openpose` | ❌ | requer build C++ |
| `simplebaseline` | ❌ | requer repo + checkpoint |
| `mhformer` | ❌ | requer repo + pesos |

Os indisponíveis aparecem no dropdown com o motivo exato — e todos atravessam o pipeline no teste de
matriz (`tests/test_backend_matrix.py`), que exercita o **mapeamento real** de cada adapter.
