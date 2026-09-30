# video2mixamo

## English

This document is also available in English: [README.md](README.md).

Ferramenta **local** que recebe um vídeo, extrai a pose por um **backend
selecionável em dropdown**, faz **retarget para o esqueleto do Mixamo** e
entrega uma animação 3D visualizável e exportável em **GLB** e **FBX**.

- UI web local (FastAPI + SPA com three.js) com dropdown de backend, upload,
  painel de status do job, preview 3D (play/pause, timeline, câmeras) e download.
- Arquitetura de **Adapters** extensível: um backend novo entra por
  configuração/plugin e aparece sozinho no dropdown, sem editar o núcleo.
- Backends **livres para uso comercial**: **ViTPose (padrão)** e MediaPipe
  (Apache-2.0), mais um backend `synthetic` determinístico para testes.
  Backends com conflito comercial foram removidos (ver `docs/LICENSING.pt-BR.md`).

## Requisitos

- Python 3.11+ (testado com 3.13)
- Windows, Linux ou macOS. GPU NVIDIA é opcional: o núcleo roda em CPU.
- Para o preview 3D, o navegador carrega o three.js via CDN (precisa de internet
  na primeira carga; o `web/` pode ser servido localmente se preferir).

## Setup (uma vez)

````powershell
cd video2mixamo
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Rodar (um comando)

````powershell
.\.venv\Scripts\python.exe run.py
# abra http://127.0.0.1:8000
```

No Linux/macOS use `.venv/bin/python run.py`.

## Uso

1. Escolha o backend no dropdown — cada um vem rotulado com a **categoria de licença**
   (`livre` / `não comercial` / `licença à parte`, com cor). O padrão **efetivo** é o ViTPose **se ele
   estiver disponível**; sem os pesos, a interface pré-seleciona o primeiro backend disponível e
   mostra o motivo no topo.
2. Envie um vídeo (mp4/mov).
3. (Opcional) Envie uma **malha Mixamo** (`.fbx` ou `.glb`) para substituir os “capsule sticks”.
   O esqueleto dela é conferido contra o contrato de 65 ossos e a interface **avisa** se for
   incompatível. **FBX é lido direto** (padrão do Mixamo, sem conversão) — ver `docs/MESH.pt-BR.md`.
4. Ajuste parâmetros (fps de saída, máx. frames, suavização, lifter 2D→3D).
5. Clique em **Processar vídeo** e acompanhe o log do job.
6. Ao terminar, a tela mostra **três painéis lado a lado** — **vídeo de referência × esqueleto (rig Mixamo)
   × malha** — com linha do tempo compartilhada (play/pause e scrub controlam os três). Se o job não tiver
   malha enviada, o painel 3 passa a mostrar o **esqueleto em uma 2ª vista**. Há presets de câmera
   (`perspectiva / frente / costas / lado / topo`, aplicados aos dois painéis 3D) e os botões de download de
   **GLB** e **FBX**.

## Backends

| id | nome no dropdown | categoria | tipo | instalação |
|----|------------------|-----------|------|------------|
| `vitpose` | ViTPose (ONNX, CPU) — **padrão** | **livre** (Apache-2.0) | top-down heatmap, COCO-17 | `pip install onnxruntime` (modelo ~83 MB na 1ª execução) |
| `mediapipe` | MediaPipe Pose (BlazePose) | **livre** (Apache-2.0) | one-shot, 33→COCO-17 | `pip install mediapipe` |
| `rtmpose` | RTMPose (rtmlib, ONNX) | **livre** (Apache-2.0) | top-down, COCO-17 | `pip install rtmlib --no-deps` |
| `motionbert` | MotionBERT | **livre** (Apache-2.0) | lifting 2D→3D (H36M-17) | torch + pesos do repo |
| `yolopose` | YOLO-pose (Ultralytics) | **licença à parte** (AGPL-3.0) | one-shot, COCO-17 | `pip install ultralytics` |
| `sam3dbody` | SAM 3D Body (Meta, MHR) | **licença à parte** (SAM License) | malha 3D de imagem única | pacote/checkpoint oficiais |
| `mhformer` | MHFormer | **licença à parte** (pesos sem licença) | lifting temporal | repo + pesos |
| `wham` | WHAM (SMPL) | **não comercial** (SMPL) | malha world-grounded | repo + corpo SMPL |
| `openpose` | OpenPose (BODY_25) | **não comercial** | bottom-up multi-pessoa | build C++ |
| `simplebaseline` | SimpleBaseline | **não comercial** (pesos) | top-down heatmap | repo + checkpoint |

> A **categoria de licença** aparece no dropdown (com cor) e no painel do job. Matriz completa e o
> significado de cada categoria: **`docs/LICENSING.pt-BR.md`**. O `synthetic` (infra de teste) fica oculto.

Detalhes de instalação, licenças e peculiaridades: `docs/BACKENDS.pt-BR.md`.

## Instalação automática dos backends

Nada de "indisponível" seco: cada backend declara um **plano de instalação** que roda no primeiro uso
ou pelo botão **"Instalar automaticamente"** no dropdown. Tudo acontece **dentro do projeto** (venv
atual, `models/` e `third_party/`) — sem mexer no sistema.

| backend | instalação | o que baixa |
|---|---|---|
| `vitpose` | **automática** | onnxruntime + modelo quantizado (~98 MB) |
| `mediapipe` | **automática** | pacote + modelo `.task` (~99 MB) |
| `rtmpose` | **automática** | `rtmlib` (~1 MB; ONNX de 48 MB na 1ª inferência) |
| `simplebaseline` | **automática** (código) | `git clone` do repo (checkpoint é passo manual) |
| `motionbert` | automática c/ deps grandes | torch CPU (~124 MB) + clone do repo |
| `mhformer` | automática c/ deps grandes | torch CPU (~124 MB) + clone do repo |
| `yolopose` | automática c/ deps grandes | `ultralytics` (~2,5 GB, arrasta o PyTorch) |
| `openpose` | **manual** | exige compilar C++ (CMake/CUDA) — não há pacote Python |
| `sam3dbody` | **manual** | pacote/checkpoint da Meta com termos próprios |
| `wham` | **manual** | depende do corpo SMPL (licença Max Planck, aceite próprio) |

Os três "manuais" não são preguiça: não existe caminho de instalação automatizável (build nativo,
download com aceite de licença ou pacote não publicado). O dropdown mostra o motivo exato, e a API
devolve `400` explicando em vez de falhar em silêncio.

CLI/API equivalentes:

````powershell
# pela interface: escolha o backend e clique em "Instalar automaticamente"
curl -X POST http://127.0.0.1:8000/api/backends/vitpose/install
curl http://127.0.0.1:8000/api/backends/vitpose/install    # status + log
```

## GPU automática e hand tracking

### A GPU certa, para o backend certo

O sistema detecta o hardware e escolhe o acelerador **e a variante dos
requisitos** automaticamente — nunca instala CPU quando existe GPU compatível,
e nunca finge GPU quando ela não serve:

| situação | o que acontece |
|---|---|
| **NVIDIA** + backend ONNX (vitpose, rtmpose) | instala `onnxruntime-gpu` **+ `nvidia-cudnn-cu12` + `nvidia-cublas-cu12`** e usa `CUDAExecutionProvider` |
| **AMD/Intel no Windows** + backend ONNX | instala `onnxruntime-directml` e usa `DmlExecutionProvider` |
| **AMD** + backend PyTorch no Windows | avisa que o PyTorch oficial não publica ROCm para Windows — segue em CPU **explicando o motivo** |
| **Apple Silicon** | `onnxruntime-silicon` / MPS |
| sem GPU compatível | CPU |
| **MediaPipe** (qualquer GPU) | fica em CPU: o wheel de desktop do pip é compilado sem suporte a GPU (`GPU processing is disabled in build flags`, confirmado no mediapipe 1.0.1); mesmo assim a inferência é rápida (~13 ms/frame) |

Dois detalhes que descobrimos na prática e estão tratados no código:

* o `onnxruntime-gpu` **não embute o cuDNN** — sem `cudnn64_9.dll` o provider CUDA falha
  em runtime (`NOT_IMPLEMENTED`). O provisionamento instala o cuDNN via pip e o backend
  registra as DLLs no `PATH` do processo (`add_dll_directory` não basta: o cuDNN resolve
  as sub-DLLs pela busca padrão do Windows);
* se a GPU falhar **na inferência** (driver/libs), o backend cai para CPU automaticamente e
  registra o motivo — antes isso devolvia poses vazias em silêncio, que é pior que o erro.

Medido nesta máquina (RTX 5060 Laptop): **8,0 ms/frame na GPU** contra **77 ms/frame na CPU**
com o modelo quantizado — ~10× mais rápido, com o mesmo score (0,80).

> Curiosidade útil: o modelo **int8 quantizado** é ótimo na CPU e *pior* na GPU. O provisionamento
> baixa a variante certa para cada caso (fp32 na GPU, int8 na CPU).

### Hand tracking

O COCO-17 não tem juntas de mão, então no retarget de corpo os **40 ossos de dedo** ficavam
parados. Com a checkbox **“hand tracking”** o pipeline baixa o `hand_landmarker.task` (~8 MB,
Apache-2.0), detecta as mãos (21 pontos cada) e preenche os dedos:

```
indicador 5-6-7-8 · médio 9-10-11-12 · anelar 13-14-15-16 · mindinho 17-18-19-20 · polegar 1-2-3-4
                        -> HandIndex1..3 / HandMiddle1..3 / ... (o 4º é a ponta)
```

As direções das falanges são convertidas para o referencial da mão (montado com punho, MCP do
indicador e MCP do mindinho) e viram rotações locais dos ossos de dedo.

**Dica prática:** mãos pequenas no vídeo (câmera longe) não são achadas no limiar padrão —
o parâmetro `min_conf` (default 0,3) controla isso; no vídeo de teste, baixar para 0,2 fez a mão
aparecer. Há também `upscale` no detector, para casos extremos.

## Cabeça: orientação estável (olhos + frame 0)

Três fontes de instabilidade foram eliminadas no retarget da cabeça:

1. **Profundidade do nariz**: o solver de distância rígida tinha singularidade perto do
   limiar (`sqrt(L²−d²)`): quando a distância 2D ombro→nariz caía um pouco abaixo de
   0,28 m, o nariz "mergulhava" até ~0,19 m **em um único frame** — a cabeça ganhava
   yaw/pitch espúrios de ~30°. Agora o nariz fica no plano do tórax.
2. **Orientação pelos olhos**: o vetor `nariz − ombros` mede posição, não orientação —
   quando o corpo se desloca no quadro (agachar, girar), a cabeça girava junto (até 40°
   de roll com o vídeo parado). A direção da cabeça agora vem da **linha dos olhos**
   (invariante à translação), com suavização adaptativa e guardas para oclusão/perfil.
3. **Neutralização no frame 0**: além de zerar as rotações locais de Neck/Head no frame
   de referência, a calibração agora neutraliza também no MUNDO — a cabeça começa
   exatamente na orientação de repouso (olhando para a frente).

Medido (sword_swing A/B, 60 frames): tilt visível da cabeça de **40° → 0,7°** (A; o
próprio vídeo mede 0,9°) e **40° → 10°** (B, concentrados no giro rápido; média 1,5°).
Controlável por `params.head_calibration` (default `true`); o resultado aparece no log do
job e no `meta` do `anim.json`.

## Idioma (PT/EN) e sincronismo de câmera

**Duas línguas, uma interface.** O pipeline e o editor de refino falam português e inglês: o
seletor `PT | EN` fica no topo das duas páginas. O idioma inicial vem de `?lang=`, da última
escolha (localStorage) ou do navegador; a troca recarrega a página (o estado vivo fica na URL).

Também são localizados os **conteúdos** que aparecem na UI: descrições dos filtros
(`description_en`) e notas das juntas (`note_en`, as 32 do preset humanoide). Limite conhecido:
logs de execução e mensagens de erro da API continuam em português por enquanto.

**sync rotation** (nas duas páginas): checkbox que sincroniza a câmera entre os painéis de
**esqueleto e malha** — qualquer órbita/zoom/troca de câmera em um reflete no outro, ao vivo e
nos dois sentidos; a preferência é lembrada entre sessões.

**A prévia do editor de refino usa a MALHA do job** (a processada no pipeline, não os "capsule
sticks") e mostra também o **vídeo de referência** (mesmo artefato do job) no painel esquerdo, acompanhando a timeline: o GLB servido pelo editor e o export refinado embutem a malha do usuário.

## Histórico de jobs

Todo processamento fica registrado no SQLite (`storage/jobs.db`) — id, vídeo, backend,
parâmetros, status, timestamps, log, métricas e artefatos. A interface tem a aba
**Histórico** (ao lado de "Novo job") com filtro por **status** e **backend**, além de:

* **abrir** um job no visualizador sem reprocessar (vídeo + GLB carregados na hora);
* baixar **GLB/FBX** direto da linha;
* **refino** → abre o editor já com aquele clipe selecionado (`/refine?job=<id>`).

Pela API: `GET /api/jobs?status=done&backend=vitpose&limit=50`.

## Refinamento da animacao

A página principal já expõe, no próprio formulário, o **dropdown de estabilização** (os
seis filtros, vindos da API) e o **toggle de constraints articulares** — a escolha entra no
job como `params.refine`. O link **“editor de refino →”** no topo abre o editor completo.

Depois de gerar o clipe, http://127.0.0.1:8000/refine abre o **editor de refinamento** com tres
ferramentas:

1. **Estabilizacao** — seis filtros (one_euro, moving_average, savgol, kalman, butterworth,
   double_exponential), configuraveis por osso, por eixo e por intervalo de frames
   (config/filters_default.yaml).
2. **Constraints** — preset humanoide de limites articulares (cabeca, ombros, cotovelos sem
   hiperextensao, joelhos, quadril) em config/constraints_humanoid.yaml, editavel pela interface.
3. **Editor de bone** — escolha osso + frame + intervalo afetado; o sistema **rebakeia cada frame**
   interpolando do ultimo frame nao afetado ate o frame editado (sem salto, e sem tocar nos frames
   fora do intervalo), com historico de desfazer/refazer persistido entre sessoes.

Guia completo: [docs/REFINE.md](docs/REFINE.pt-BR.md) · API: [docs/REFINE_API.md](docs/REFINE_API.pt-BR.md) ·
historico de versoes: [CHANGELOG.md](CHANGELOG.pt-BR.md).

Tambem da para rodar por CLI ou dentro do pipeline:

```powershell
.\\.venv\\Scripts\\python.exe tools\\refine.py --make-samples
```

```json
{fps: 30, refine: {constraints: config/constraints_humanoid.yaml,
                       filters: config/filters_default.yaml}}
```

## Testes

````powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Cobrem: contrato do Adapter para todos os backends registrados, descoberta de
plugins, contrato do esqueleto Mixamo (65 ossos + hierarquia), rigidez e FK
reverso do retarget, estrutura do GLB (validada com `pygltflib`) e do FBX ASCII,
persistência de jobs e um pipeline ponta a ponta com vídeo de amostra.

## Arquitetura

```
upload → pré-processamento → backend de pose (Adapter) → pós/estabilização
      → lifter 2D→3D (se necessário) → solver de retarget → esqueleto Mixamo
      → exportação (GLB + FBX) → preview three.js
```

Detalhes: `docs/ARCHITECTURE.pt-BR.md`.

## Adicionar um backend

Ver `docs/ADDING_A_BACKEND.pt-BR.md`. Resumo: crie `plugins/meu_backend.py` expondo
`BACKEND` (uma instância de `ConfigurableAdapter` com sua `AdapterConfig`).
Ele aparece no dropdown na próxima carga — sem tocar em núcleo ou frontend.

## Limitações

Ver `docs/LIMITATIONS.pt-BR.md` (vídeo de uma pessoa, sem root motion absoluto,
backends pesados exigem setup próprio, FBX ASCII sem malha). Licenças e
backends removidos: `docs/LICENSING.pt-BR.md`.
