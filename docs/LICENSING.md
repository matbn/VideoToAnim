# Matriz de licenças

Este projeto é pensado para distribuição pública, então cada backend de pose traz uma **categoria de
licença** visível no dropdown e na API (`GET /api/backends`). A categoria não esconde o backend — ela
informa o que você pode ou não fazer.

| categoria | significado |
|---|---|
| **`livre`** | uso comercial permitido (MIT / Apache-2.0 / BSD) |
| **`nao_comercial`** | só pesquisa / uso acadêmico não comercial |
| **`licenca_a_parte`** | copyleft (ex.: AGPL), share-alike ou termos próprios — exige avaliação/negociação |

## Os 11 backends

| backend | categoria | código | pesos / modelo | observação |
|---|---|---|---|---|
| `vitpose` (padrão) | **livre** | Apache-2.0 (ViTPose/OpenMMLab) | Apache-2.0 (`onnx-community/vitpose-base-simple`) | roda em CPU via ONNX Runtime |
| `mediapipe` | **livre** | Apache-2.0 (Google) | Apache-2.0 (modelo `pose_landmarker.task`) | CPU |
| `rtmpose` | **livre** | Apache-2.0 (RTMPose/OpenMMLab) | Apache-2.0 (ONNX oficial via `rtmlib`) | CPU; `rtmlib` é MIT |
| `motionbert` | **livre** | Apache-2.0 | Apache-2.0 (`walterzhu/MotionBERT`) | requer torch |
| `yolopose` | **licença à parte** | **AGPL-3.0** (Ultralytics) | AGPL-3.0 | copyleft: distribuir exige abrir o código ou licença Enterprise |
| `sam3dbody` | **licença à parte** | SAM License (Meta) | SAM License | permissiva para uso comercial, porém **share-alike** + restrições de export/militar |
| `mhformer` | **licença à parte** | MIT | **sem licença declarada** | o código é livre; os pesos vêm sem licença (all rights reserved) |
| `wham` | **não comercial** | MIT | **SMPL (Max Planck) — non-commercial** | o código é livre; o corpo SMPL que ele usa não é |
| `openpose` | **não comercial** | non-commercial (CMU) | non-commercial | LICENSE literal: *"ACADEMIC OR NON-PROFIT ORGANIZATION NONCOMMERCIAL RESEARCH USE ONLY"* |
| `simplebaseline` | **não comercial** | MIT | **"for research purpose"** | o código é MIT; os pesos são declarados só para pesquisa |
| `synthetic` | **livre** (oculto) | MIT (deste projeto) | — | infra de teste; não aparece no dropdown |

> **Serviço auxiliar:** o `rtmlib` (usado pelo `rtmpose`) é MIT, e o `onnxruntime` é MIT.

## Como ler isso na prática

- Quer **lançar um produto fechado**? Use apenas a coluna `livre` — os quatro primeiros.
- **Pesquisa**? Tudo é utilizável; só respeite as citações.
- Quer `yolopose` ou `sam3dbody` num produto? Leia a licença antes: AGPL exige abrir o código (ou
  comprar licença da Ultralytics); a SAM License permite uso comercial mas propaga os termos.
- `openpose`, `simplebaseline` e `wham` **não** podem ir para um produto comercial como estão.

## Detalhes que costumam enganar

1. **Código livre ≠ pesos livres.** É o caso de `mhformer` (MIT + pesos sem licença) e
   `simplebaseline` (MIT + pesos "research purpose"). Sempre verifique o *checkpoint*, não só o repo.
2. **Modelos de corpo (SMPL/SMPL-X)** exigem licença da Max Planck e são **não comerciais** — é o que
   coloca o `wham` na categoria não comercial, mesmo com código MIT.
3. **AGPL é copyleft forte**: usar o `yolopose` num serviço de rede obriga a disponibilizar o código,
   salvo licença comercial.
4. **Este projeto não empacota pesos.** Cada backend baixa o que precisa na primeira execução (e
   registra o que baixou), para que a licença de cada artefato fique explícita.

## Onde isso aparece no código

- `core/adapter.py` → `LICENSE_CATEGORIES` e o campo `license_category` da `AdapterConfig`.
- `core/registry.py` → `BackendRecord.license_category` / `license` e `by_license_category()`.
- `GET /api/backends` → devolve `license_category` e `license` por backend.
- A interface mostra a categoria no dropdown (cor por categoria) e no painel do job.
- Testes: `tests/test_adapter_contract.py::test_categorias_de_licenca` garante que todo backend
  registrado tem categoria válida e descrição de licença.

## Rastreabilidade (fontes primárias)

As categorizações seguem as licenças lidas nos repositórios oficiais: `CMU-Perceptual-Computing-Lab/openpose`
(LICENSE), `microsoft/human-pose-estimation.pytorch` (README), `Vegetebird/MHFormer` (README),
`Walter0807/MotionBERT`, `ultralytics/ultralytics`, `facebookresearch/sam-3d-body`,
`yohanshin/WHAM`, ViTPose/MMPose e MediaPipe. Se alguma mudar, a categoria deve ser revista junto.
