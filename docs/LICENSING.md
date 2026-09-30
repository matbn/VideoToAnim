# License matrix

## Português

Este documento também está disponível em português: [LICENSING.pt-BR.md](LICENSING.pt-BR.md).

This project is designed for public distribution, so every pose backend carries a **license category** visible in the dropdown and in the API (`GET /api/backends`). The category does not hide the backend — it tells you what you may or may not do. (The category identifiers are in Portuguese: `livre` = free, `nao_comercial` = non-commercial, `licenca_a_parte` = separate license.)

| category | meaning |
|---|---|
| **`livre`** | commercial use allowed (MIT / Apache-2.0 / BSD) |
| **`nao_comercial`** | research / non-commercial academic use only |
| **`licenca_a_parte`** | copyleft (e.g., AGPL), share-alike or custom terms — requires assessment/negotiation |

## The 11 backends

| backend | category | code | weights / model | note |
|---|---|---|---|---|
| `vitpose` (default) | **`livre`** | Apache-2.0 (ViTPose/OpenMMLab) | Apache-2.0 (`onnx-community/vitpose-base-simple`) | runs on CPU via ONNX Runtime |
| `mediapipe` | **`livre`** | Apache-2.0 (Google) | Apache-2.0 (`pose_landmarker.task` model) | CPU |
| `rtmpose` | **`livre`** | Apache-2.0 (RTMPose/OpenMMLab) | Apache-2.0 (official ONNX via `rtmlib`) | CPU; `rtmlib` is MIT |
| `motionbert` | **`livre`** | Apache-2.0 | Apache-2.0 (`walterzhu/MotionBERT`) | requires torch |
| `yolopose` | **`licenca_a_parte`** | **AGPL-3.0** (Ultralytics) | AGPL-3.0 | copyleft: distributing requires open-sourcing or an Enterprise license |
| `sam3dbody` | **`licenca_a_parte`** | SAM License (Meta) | SAM License | permissive for commercial use, but **share-alike** + export/military restrictions |
| `mhformer` | **`licenca_a_parte`** | MIT | **no declared license** | the code is free; the weights come with no license (all rights reserved) |
| `wham` | **`nao_comercial`** | MIT | **SMPL (Max Planck) — non-commercial** | the code is free; the SMPL body it uses is not |
| `openpose` | **`nao_comercial`** | non-commercial (CMU) | non-commercial | LICENSE verbatim: *"ACADEMIC OR NON-PROFIT ORGANIZATION NONCOMMERCIAL RESEARCH USE ONLY"* |
| `simplebaseline` | **`nao_comercial`** | MIT | **"for research purpose"** | the code is MIT; the weights are declared for research only |
| `synthetic` | **`livre`** (hidden) | MIT (this project) | — | test infra; does not appear in the dropdown |

> **Auxiliary service:** `rtmlib` (used by `rtmpose`) is MIT, and `onnxruntime` is MIT.

## How to read this in practice

- Want to **ship a closed product**? Use only the `livre` column — the first four.
- **Research**? Everything is usable; just respect the citations.
- Want `yolopose` or `sam3dbody` in a product? Read the license first: AGPL requires open-sourcing (or buying an Ultralytics license); the SAM License allows commercial use but propagates its terms.
- `openpose`, `simplebaseline` and `wham` **cannot** go into a commercial product as they are.

## Details that often mislead

1. **Free code ≠ free weights.** This is the case for `mhformer` (MIT + weights with no license) and `simplebaseline` (MIT + "research purpose" weights). Always check the *checkpoint*, not just the repo.
2. **Body models (SMPL/SMPL-X)** require a Max Planck license and are **non-commercial** — that is what puts `wham` in the non-commercial category, even with MIT code.
3. **AGPL is strong copyleft**: using `yolopose` in a network service requires making the code available, unless under a commercial license.
4. **This project does not bundle weights.** Each backend downloads what it needs on first run (and records what it downloaded), so the license of each artifact stays explicit.

## Where this appears in the code

- `core/adapter.py` → `LICENSE_CATEGORIES` and the `license_category` field of `AdapterConfig`.
- `core/registry.py` → `BackendRecord.license_category` / `license` and `by_license_category()`.
- `GET /api/backends` → returns `license_category` and `license` per backend.
- The interface shows the category in the dropdown (color per category) and in the job panel.
- Tests: `tests/test_adapter_contract.py::test_categorias_de_licenca` guarantees every registered backend has a valid category and license description.

## Traceability (primary sources)

The categorizations follow the licenses read in the official repositories: `CMU-Perceptual-Computing-Lab/openpose` (LICENSE), `microsoft/human-pose-estimation.pytorch` (README), `Vegetebird/MHFormer` (README), `Walter0807/MotionBERT`, `ultralytics/ultralytics`, `facebookresearch/sam-3d-body`, `yohanshin/WHAM`, ViTPose/MMPose and MediaPipe. If any of them changes, the category must be reviewed along.
