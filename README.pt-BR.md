# VideoToAnim

## English

This document is also available in English: [README.md](README.md).

Ferramenta **local** que recebe um vídeo, extrai a pose por um **backend selecionável em
dropdown**, faz **retarget para o esqueleto do Mixamo** e exporta a animação 3D em
**GLB** ou **FBX**.

- Interface web (FastAPI + three.js) com dropdown de backend, upload, log do job,
  preview 3D (play/pause, timeline, câmeras) e download.
- Backends são **plugins**: solte um em `plugins/` e ele aparece sozinho no dropdown,
  sem editar o núcleo ou o frontend.
- Vem com backends **livres para uso comercial**: ViTPose (padrão), MediaPipe, RTMPose
  e MotionBERT.

## Requisitos

- Python 3.11+
- Windows, Linux ou macOS. GPU é opcional: tudo roda em CPU.
- Internet na primeira carga (o three.js vem de CDN, além dos pesos do backend).

## Instalação e execução

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py          # abra http://127.0.0.1:8000
```

No Linux/macOS use `.venv/bin/python run.py`.

## Uso

1. Escolha o backend no dropdown. Cada um vem rotulado com a **categoria de licença**
   (`livre` / `não comercial` / `licença à parte`). No primeiro uso a interface oferece
   instalar automaticamente — tudo fica dentro da pasta do projeto, sem mexer no sistema.
2. Envie um vídeo (mp4/mov).
3. *(Opcional)* Envie uma **malha Mixamo** (`.fbx` ou `.glb`) para substituir o boneco de
   pau. O esqueleto dela é conferido contra o contrato de 65 ossos e a interface avisa se
   não bater.
4. Ajuste os parâmetros se quiser (fps de saída, máx. frames, suavização) e clique em
   **Processar vídeo**.
5. O resultado mostra três painéis lado a lado — **vídeo de referência × esqueleto × malha** —
   numa linha do tempo compartilhada, com botões de download de **GLB** e **FBX**.

> O COCO-17 não tem juntas de dedo, então os 40 ossos de dedo ficam parados. Marque
> **hand tracking** para detectar as mãos e preenchê-los.

## Backends

| id | nome | licença |
|----|------|---------|
| `vitpose` | ViTPose — **padrão** | **livre** (Apache-2.0) |
| `mediapipe` | MediaPipe Pose | **livre** (Apache-2.0) |
| `rtmpose` | RTMPose | **livre** (Apache-2.0) |
| `motionbert` | MotionBERT (2D→3D) | **livre** (Apache-2.0) |
| `yolopose` | YOLO-pose | **licença à parte** (AGPL-3.0) |
| `sam3dbody` | SAM 3D Body | **licença à parte** (SAM) |
| `mhformer` | MHFormer | **licença à parte** (pesos sem licença) |
| `wham` | WHAM (SMPL) | **não comercial** |
| `openpose` | OpenPose | **não comercial** |
| `simplebaseline` | SimpleBaseline | **não comercial** |

Alguns exigem instalação manual (build nativo ou download com aceite de licença); o dropdown
mostra o motivo exato em vez de falhar em silêncio. Detalhes: `docs/BACKENDS.pt-BR.md` ·
`docs/LICENSING.pt-BR.md`.

## Refinar o resultado

Abra `http://127.0.0.1:8000/refine` para o editor de refinamento: filtros de estabilização,
constraints articulares humanoides e um editor por osso/frame com desfazer/refazer.
Guia: `docs/REFINE.pt-BR.md`.

O editor tem **duas abas** — `Refino` e `Mistura A/B` — e a barra de tempo fica **fora** das
abas, então o mesmo playhead serve as duas: arrastar na aba da mistura move os painéis A/B e o
vídeo de referência juntos.

### Mistura A/B — pegar partes do corpo de outro job

Dois backends no mesmo vídeo costumam errar em lugares diferentes. A aba `Mistura A/B` deixa
você **pegar partes do corpo de um job e usar no clipe que está aberto**, deixando o resto como
está — "quero o corpo do B e o braço do A".

1. Escolha o job de **origem** no seletor acima do painel A. O destino é o job do editor.
2. Marque as partes: quadril/raiz, torso, cabeça, braço esquerdo/direito, perna esquerda/direita.
3. *(Opcional)* **mapear intervalo de frames**, para quando os dois jobs não são exatamente o
   mesmo trecho de vídeo (ex.: `frames 1–23 de A` aplicados nos `frames 3–35 de B`).
4. **aplicar mistura**. Há um **desfazer** que volta ao clipe anterior.

Os dois painéis ficam lado a lado, na **mesma câmera e no mesmo frame** da timeline, com os nomes
dos jobs — o que mudou aparece na hora.

As partes saem da **hierarquia do rig**, não de uma lista na mão: braço = subárvore de
`LeftShoulder` (19 ossos, incluindo os dedos), perna = subárvore de `LeftUpLeg`, cabeça =
subárvore de `Neck`. `torso` é a única lista explícita (`Spine`, `Spine1`, `Spine2`) — a subárvore
de `Spine2` conteria braços, pernas e cabeça, e uma "parte" que engole o personagem inteiro não
é uma parte.

O transplante acontece em **espaço de mundo**: o que entra no destino é a rotação de mundo da
origem, reexpressa em relação ao pai já transplantado. Ossos abaixo de uma parte transplantada que
não foram escolhidos mantêm a rotação local do destino e giram junto — é assim que o antebraço e
a mão acompanham o ombro.

> Jobs com **fps** diferentes são recusados: as durações não são comparáveis. Com números de
> frames diferentes, use o mapeamento de intervalo.

Cada execução fica guardada, e a aba **Histórico** permite reabrir, reexportar ou refinar um
job anterior sem processar o vídeo de novo.

## Estender

Adicione um backend em `plugins/` — ver `docs/ADDING_A_BACKEND.pt-BR.md`.

## Testes

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## Limitações e leitura complementar

Vídeo de uma pessoa, sem root motion absoluto, FBX gravado sem a malha.
Ver `docs/LIMITATIONS.pt-BR.md` · `docs/ARCHITECTURE.pt-BR.md` · `docs/API.pt-BR.md` ·
[CHANGELOG.pt-BR.md](CHANGELOG.pt-BR.md).

