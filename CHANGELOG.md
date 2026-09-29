# Changelog

## [1.4.7] — Calibração da cabeça pelo primeiro frame

### Corrigido

* **Cabeça "olhando para baixo/para o lado" em todos os backends default**: o solver
  orienta a cadeia pescoço/cabeça por um único vetor estimado (`nose - chest`), cuja
  profundidade vem do lifter monocular e carregava um **viés sistemático** — medido entre
  **55° e 75°** de rotação no `Neck` no frame 0 em jobs reais (o `Head` local é sempre
  identidade; todo o erro mora no pescoço).
* **Novo: calibração pelo frame de referência** (`core/retarget.py: calibrate_head`):
  a rotação local de `Neck`/`Head` no frame 0 vira a identidade — a cabeça começa na
  orientação de repouso (frente do corpo) e **todo o movimento relativo é preservado**
  (teste: q'(t1)⁻¹q'(t2) == q(t1)⁻¹q(t2)). Controlável por `params.head_calibration`
  (default `true`) e `params.head_calibration_frame` (default `0`); o resultado vai para o
  log do job e para o `meta` do `anim.json`.

### Verificado

* E2E A/B no mesmo clipe: `Neck |q0|` **75,46° → 0,00°** com a calibração ligada;
  GLB/FBX exportados normalmente; **107 testes** (4 novos de regressão).


## [1.4.6] — RTMPose: NameError `_onnx_cuda_ok` corrigido + fallback de GPU

### Corrigido

* **`ERRO: name '_onnx_cuda_ok' is not defined` no rtmpose**: o helper era chamado no
  `load()` mas nunca tinha sido definido no módulo (pendência da rodada de GPU). Agora ele
  existe, registra as DLLs do cuDNN antes de checar o provider, e o backend usa CUDA de
  verdade — medido: **40 ms/frame na GPU** (steady state, 18 frames) contra ~200 ms na CPU.
* O `load()` do rtmpose ganhou o mesmo cuidado do vitpose: se a sessão CUDA falhar na
  criação, cai para CPU com motivo; e se a GPU falhar **na inferência** (ex.: cuDNN), cai
  para CPU uma única vez e segue — sem poses vazias silenciosas.
* O motivo do fallback (`_fallback_reason`) agora aparece no **log do job** ("Aviso: ...")
  — antes era registrado no objeto e nunca mostrado.

### Técnico

* `core/gpu.py` ganhou `ensure_cuda_dlls()` (helper compartilhado de DLLs CUDA/cuDNN);
  o vitpose passou a delegar para ele (uma única fonte de verdade).
* Varredura estática (pyflakes) no projeto inteiro para garantir que não há outros nomes
  indefinidos; novo teste de regressão `tests/test_backend_rtmpose.py`; **103 testes**.


## [1.4.5] — Vídeo de referência no editor de refino

### Adicionado

* O editor de refino ganhou o painel de **vídeo de referência** (mesmo artefato do job;
  três painéis: vídeo · esqueleto · malha, como no pipeline). O vídeo **acompanha o scrub e o
  playback**: cada frame da timeline posiciona o vídeo no instante correspondente (t / fps),
  com guarda para não re-buscar o mesmo instante a cada passo.
* Ao abrir um job sem `anim.json` (ou em falha de carregamento), o vídeo é descarregado junto
  com a cena, em vez de ficar o clipe anterior na tela.

### Validado

* `/refine` serve o painel (`id="refvideo"`); o artefato de vídeo responde 200
  (`video/mp4`, 5,4 MB no job de teste); 101 testes passando; `node --check` limpo.


## [1.4.4] — Sync rotation, malha na prévia do refino e i18n (PT/EN)

### Adicionado

* **sync rotation** no pipeline e no editor de refino: checkbox que sincroniza a câmera
  entre os painéis de esqueleto e malha (ao vivo, nos dois sentidos; a preferência é
  lembrada entre sessões).
* **Internacionalização PT/EN de toda a interface** (pipeline + editor): seletor `PT | EN`
  no topo, idioma inicial por `?lang=` / localStorage / navegador, e todos os textos
  gerados por JS traduzidos (hints, tabelas, avisos, editor de constraints, log do editor).
  Conteúdos também localizados: descrições dos filtros (`description_en`) e notas das 32
  juntas (`note_en`). Logs de execução e erros da API seguem em PT (limite conhecido).

### Corrigido

* **A prévia do editor de refino agora carrega a MALHA processada do job** (não os
  "capsule sticks"): o GLB de `/api/refine/animation/{id}/glb` e o export refinado embutem
  a malha do usuário (carregada do `mesh.*` do job, cacheada por processo). Verificado:
  GLB do refino byte-idêntico ao do pipeline (9.285 vértices / 17.916 triângulos, com skin).

### Técnico

* `core/gpu.py` e `core/provision.py` passaram a expor **códigos de motivo**
  (`reason_code`/`reason_vars`, `manual_code`) para o front localizar sem duplicar regra —
  a UI resolve PT/EN pelo código, com fallback para o texto.
* Testes: 101 passando; `node --check` nos 3 módulos JS; consistência de ids JS×HTML e
  paridade repo/staging verificadas.


## [1.4.3] — Editor: abre o job em foco e constraints organizadas

### Adicionado

* **O editor de refino abre automaticamente o job que está aberto na aba principal**: a
  página do pipeline passa a sincronizar o job em foco (inclusive o resultado de um novo
  processamento) e o editor escolhe, em ordem: `?job=` da URL → job sincronizado → mais
  recente. A seleção também fica na URL (`/refine?job=…`), então recarregar mantém o clipe.

### Alterado

* **Constraints reorganizadas** (`web/refine.js` + `web/style.css`): a lista plana de 32
  linhas sem estilo deu lugar a **grupos por região do corpo** (tronco, cabeça/pescoço,
  braços esquerdo/direito, pernas esquerda/direita, mãos), recolhíveis, com **legenda de
  colunas** (junta · mín° · máx° · rigidez), **chip de tipo** com cor por eixo (cone/x/y/z),
  **barra visual de faixa** (−180°..180°), **filtro de busca** por junta/tipo/nota,
  **contagem por grupo** e **destaque de alterações pendentes** no botão Salvar (com aviso
  visual de mín > máx).
* Checklist de filtros ganhou estilo consistente com o resto da página.


## [1.4.2] — Editor de refino: playback e troca de clipe

### Corrigido

* **A animação não tocava no editor**: a ação de animação era criada **pausada** e o scrub
  usava `mixer.setTime()`; com a ação pausada o three.js usa `timeScale` efetivo 0 e o clipe
  fica travado no instante 0. Comprovado com o **three.js 0.169** (a mesma versão da página)
  num script isolado: `paused + setTime(1.5s)` deixa a propriedade em 0;
  `action.time = 1.5 + mixer.update(0)` aplica o valor interpolado (15). O scrub agora usa
  `action.time` + `update(0)` — o botão play avança os frames pelo mesmo caminho.
* **O clipe antigo não "descarregava" ao trocar de job**: o `SkeletonHelper` ficava na cena
  (o `clear()` do editor não o removia — a versão da página principal já removia) e acumulava
  esqueletos. Agora `clear()` remove helper e modelo (com `dispose`), roda **antes** do
  download do novo clipe, mostra "carregando…" e, em falha, o motivo no overlay.
* Escolher um job sem `anim.json` (antigo) agora **limpa a cena** e mostra o motivo — antes o
  clipe anterior ficava na tela como se nada tivesse acontecido.
* Links "GLB/FBX refinado" agora são por job: não ficam pendurados ao trocar de clipe
  (resetados; reativados apenas se o job tiver artefatos refinados).
* A pose na cena é aplicada **antes** do fetch do frame (o playback não depende da rede) e
  uma falha do painel de euler não interrompe mais a animação.
* Troca rápida de clipe não deixa mais um carregamento antigo sobrescrever o novo
  (token de carregamento).


## [1.4.1] — Rotas de refino restauradas e dispositivo honesto na interface

### Corrigido

* **Dropdown de filtros vazio (regressão real)**: o include da API de refino tinha se
  perdido do `server/app.py` — o servidor subia sem `/api/refine/*` (404 silencioso) e
  sem a página `/refine`. Restaurado, com **teste de regressão de rotas**
  (`tests/test_server_routes.py`) que trava o conjunto de rotas via OpenAPI.
* **Import circular latente**: `install_api`/`refine_api` importavam estado do `app.py`
  no topo; importar qualquer um dos módulos *antes* do app quebrava com
  `ImportError: cannot import name 'router' from partially initialized module`.
  Agora os módulos não dependem do app no boot (o `store` é resolvido tardiamente).
* **"ViTPose (ONNX, CPU)" fixo no dropdown**: o nome dizia CPU mesmo rodando em GPU.
  O rótulo agora é neutro e a interface mostra o **dispositivo real** de cada backend
  (GPU CUDA / GPU DirectML / GPU MPS / CPU), com o motivo quando fica em CPU.
* Rótulo de GPU duplicava o vendor ("NVIDIA NVIDIA GeForce...") e o motivo do
  MediaPipe citava uma razão vaga — ambos corrigidos.
* Mojibake (`â€”`) em logs/docstrings/changelog.

### Documentado

* **Por que o MediaPipe roda em CPU**: o wheel de desktop do pip é compilado sem
  suporte a GPU — `GPU processing is disabled in build flags` (verificado nesta
  máquina, mediapipe 1.0.1). O delegate de GPU só existe nas builds móveis
  (Android/iOS). Em CPU a inferência é rápida (~13 ms/frame para mãos).
* **Hand tracking e o modelo**: usa o HandLandmarker do MediaPipe
  (`hand_landmarker.task`), isolado em `core/hands.py`; o caminho do modelo pode ser
  trocado via `V2M_HAND_MODEL` ou `params.hands.model_path`, e `min_conf`/`upscale`
  agora vêm do job (`params.hands`).


## [1.4.0] — GPU automática e hand tracking

### Adicionado

* **Detecção de GPU** (`core/gpu.py`): NVIDIA (nvidia-smi), AMD/Intel (WMI), Apple Silicon, ROCm —
  com **matriz de compatibilidade por backend** e explicação quando a GPU não serve
  (ex.: AMD no Windows com PyTorch oficial, que não publica ROCm).
* **Provisionamento ciente de GPU**: instala `onnxruntime-gpu` (+ `nvidia-cudnn-cu12` e
  `nvidia-cublas-cu12`), `onnxruntime-directml` ou `onnxruntime` conforme o hardware; para
  PyTorch, usa o `--index-url` da build CUDA; e baixa a **variante certa do modelo**
  (fp32 na GPU, int8 na CPU).
* **Uso real da GPU no runtime**: `CUDAExecutionProvider`/`DmlExecutionProvider` nos backends ONNX,
  com registro das DLLs do cuDNN no `PATH` do processo.
* **Fallback de inferência**: se a GPU falhar em runtime, o backend volta para CPU e registra o
  motivo — antes isso produzia poses vazias silenciosamente.
* **Hand tracking** (`core/hands.py`): checkbox na interface; baixa o `hand_landmarker.task` (~8 MB),
  detecta as mãos (21 pontos) e anima os **40 ossos de dedo**, que antes ficavam em identidade.
  Parâmetros `min_conf` (default 0,3) e `upscale` para mãos pequenas no vídeo.

### Medido

* RTX 5060 Laptop: **8,0 ms/frame (GPU)** vs **77 ms/frame (CPU)** no ViTPose-ONNX — ~10×,
  com score equivalente (0,80).
* Hand tracking no vídeo de teste (sword swing): mãos detectadas em 10/40 frames com `min_conf=0,2`
  (0/40 no limiar padrão de 0,5) — mãos pequenas exigem limiar mais baixo.
## [1.3.0] — Aba de histórico de jobs

### Adicionado

* **Aba Histórico** na interface (ao lado de "Novo job"): lista os jobs guardados no SQLite
  com id, data, vídeo, backend, status e frames; filtro por **status** e **backend**; e ações
  por linha — **abrir** no visualizador (sem reprocessar), baixar **GLB/FBX** e ir para o
  **editor de refino** daquele clipe.
* `GET /api/jobs` agora aceita `?status=`, `?backend=` e `?limit=` (até 500).
* O editor aceita `?job=<id>` para abrir já no clipe vindo do histórico.
* Ao terminar um job novo, a lista do histórico é atualizada automaticamente.

## [1.2.0] — Instalação automática dos backends

### Adicionado

* **Provisionamento por backend** (`core/provision.py`): planos declarativos com passos `pip`, `git`
  e `download`, executados dentro do projeto (`models/`, `third_party/`), sem tocar no sistema.
* **API**: `GET/POST /api/backends/{nome}/install` (status + log + relatório em background) e o campo
  `install` em `GET /api/backends`.
* **Interface**: o dropdown mostra "instalável" / "passo manual" em vez de "indisponível", com botão
  **"Instalar automaticamente"** e log ao vivo; ao terminar, a disponibilidade é recarregada.
* Backends clonados passam a ser detectados sozinhos (`third_party/...`), sem variável de ambiente.
* Testes `tests/test_provision.py` garantindo que todo backend visível tem plano coerente e que os
  "manuais" explicam o motivo e não tentam instalar.

### Notas

* Três backends continuam **manuais** por impossibilidade real: `openpose` (build C++),
  `sam3dbody` (termos próprios da Meta) e `wham` (corpo SMPL com aceite de licença da Max Planck).
* `yolopose` instala, mas o `ultralytics` arrasta o PyTorch (~2,5 GB) — a interface avisa antes.

## [1.1.0] — Camada de refinamento de animações

### Adicionado

* **Filtros de estabilização** (`core/refine/filters.py`): seis métodos além do One-Euro —
  `moving_average`, `savgol`, `kalman`, `butterworth`, `double_exponential` — todos em **numpy puro**
  (sem scipy), configuráveis por osso, por eixo e por intervalo de frames.
* **Plano de filtragem** (`core/refine/plan.py`) com arquivo YAML reaplicável
  (`config/filters_default.yaml`).
* **Constraints articulares** (`core/refine/constraints.py`) com **preset humanoide** completo
  (32 limites) em arquivo editável `config/constraints_humanoid.yaml`; suporta limite por **cone**
  (desvio total) e por **eixo** com min/max assimétricos (cotovelo/joelho sem hiperextensão), com
  `stiffness` e correção suave por slerp.
* **Editor de bone com rebake por keyframe** (`core/refine/boneedit.py`): escolhe osso, frame alvo e
  intervalo afetado; interpola com ease-in-out entre o último frame não afetado, o frame editado e o
  primeiro não afetado depois — **reescrevendo cada frame** (bake), sem curvas no arquivo final.
* **Histórico persistido** (undo/redo) com autor, nota e timestamp; a sessão sobrevive a reinício.
* **Relatório comparativo** (`core/refine/report.py`): suavidade (energia do jerk), atraso
  (correlação cruzada) e desvio residual (RMSE + erro angular) por filtro, em Markdown e JSON.
* **CLI** `tools/refine.py` (`--make-samples`, `--compare`, `--inject-violation`, `--out`).
* **API REST** `server/refine_api.py` e **editor web** em `/refine`.
* **Testes** `tests/test_refine.py` (16 casos) cobrindo os critérios de aceite.

### Corrigido

* `mixamo.quat_normalize` usava `np.linalg.norm(q)` sem eixo: numa série `(T,4)` isso calculava a
  norma de **Frobenius** e dividia a série inteira por ela, deixando cada quaternion com norma errada
  (bug silencioso, exposto ao filtrar séries). Agora normaliza no último eixo e aceita 1 ou N
  quaternions.
* `constraints.apply_constraints` reescrevia todos os frames do osso mesmo sem violação; agora só
  escreve quando há correção, preservando bit-exatamente o que não foi tocado.

### Requisitos de ambiente

* Python 3.11+ (testado em 3.13) · numpy ≥ 1.26 · PyYAML ≥ 6.0
* Sem dependências novas: a camada usa apenas numpy/pyyaml (já presentes) — **scipy não é
  necessário** (savgol e butterworth implementados à mão).
* CLI: `python tools/refine.py --make-samples` funciona com o venv do projeto.

## [1.0.0] — Pipeline vídeo → animação Mixamo

* Backends de pose com categoria de licença, retarget para o rig Mixamo de 65 ossos, export GLB/FBX,
  editor de malha (FBX/GLB) e preview 3D com vídeo de referência. Ver `README.md`.
