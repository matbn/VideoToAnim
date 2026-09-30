# Comparativo de frameworks de UI web local

## English

This document is also available in English: [UI_FRAMEWORK_COMPARISON.md](UI_FRAMEWORK_COMPARISON.md).

Critérios derivados dos requisitos do produto: dropdown de backend alimentado por registro de
plugins; upload de vídeo; painel de status de job persistente; **preview 3D com play/pause,
timeline (scrub) e troca de câmera**; download de GLB e FBX; subir local com um comando.

> Este comparativo passou por **contraprova adversarial** (documento
> `.cluster/video2mixamo/subagent_02.md`), que corrigiu uma premissa comum:
> o `gr.Model3D` do Gradio **reproduz** a animação do GLB em autoplay (viewer Babylon,
> `animationAutoPlay=true` desde o PR #10993). O que ele **não** oferece é API de controle
> (play/pause/timeline/scrub) a partir do Python. A decisão abaixo foi reescrita com essa correção.

## Matriz por requisito

| Requisito | Gradio | Streamlit | **FastAPI + SPA (escolhido)** |
|---|---|---|---|
| **Preview 3D + play/pause + timeline + câmera** | **quase** — órbita e autoplay da animação GLB funcionam; play/pause/timeline exigem JS injetado (`js=`) ou componente custom | **quebra** — todo viewer 3D vive em **iframe isolado**; controles do app não o comandam; exigiria componente React (≈ escrever a SPA) | **nativo** — three.js `AnimationMixer` + timeline própria |
| Dropdown dinâmico por registro de plugins | atende (`Dropdown.choices` como output) | atende (`selectbox.options`) | atende (`GET /api/backends`) |
| Jobs longos + progresso + cancelamento | fila, `gr.Progress` e `cancels=` nativos; **persistência entre sessões é DIY** (guia oficial manda trazer APScheduler); bugs recentes de cancelamento (#13323/#13895) | executor próprio + `st.fragment(run_every=)` para polling (suportado); cancelamento cooperativo; bug aberto de estado em auto-rerun (#14064) | **mesmo trabalho** (`JobStore` + worker), sem lutar contra o modelo de execução do framework |
| Download de 2 formatos por job | atende (`DownloadButton`) | atende (`st.download_button`) | atende (`FileResponse`) |
| Adapters extensíveis | atende | atende (cuidado com registro em import-time + reruns) | atende (sem lifecycle alheio) |
| Subir com 1 comando | ótimo | ótimo | ótimo (`python run.py`) |
| Peso / licença | pesado · Apache-2.0 | pesado · Apache-2.0 | **leve · MIT** |

**Ponto que a contraprova reforça:** nenhum dos três entrega *job system persistente multi-sessão*
pronto. Em qualquer base, o `JobStore` + worker com cancelamento cooperativo é módulo próprio — como
de fato foi implementado (`core/jobs.py`, `core/pipeline.py`).

## Decisão: FastAPI + SPA própria (three.js)

**Justificativa correta** (reescrita após a contraprova): não é "Streamlit/Gradio não fazem jobs ou
downloads" — fazem, e bem. O fator decisivo é que o **requisito central do produto** (timeline +
play/pause + troca de câmera sobre animação GLB, controlados pelo app) **empurra ambos os frameworks
para componentes custom / JS injetado**. Ou seja: o custo do frontend customizado é pago de qualquer
jeito — e o caminho direto evita ainda (i) a ponte por iframe do Streamlit e (ii) o acoplamento ao
ciclo de releases do viewer Babylon do Gradio (a regressão #10983/#10993 é evidência de que esse
acoplamento quebra na prática).

**Fallback explícito e condicional:** se o preview for rebaixado para "orbitar o GLB com autoplay"
(sem timeline nem controles próprios), a hipótese cai em favor do **Gradio montado sobre FastAPI**
(`gr.mount_gradio_app`) — que entrega os requisitos restantes (b)–(e) com bem menos código e mantém
FastAPI por baixo. O Gradio **não abandona** FastAPI: é FastAPI.

**Descartado: Streamlit** para este produto. O modelo de re-execução do script e o isolamento por
iframe atacam exatamente o requisito central; para os demais requisitos ele é adequado, mas eles não
são os diferenciadores.

**Open WebUI** foi descartado na pesquisa inicial: é um produto chat-first para LLMs locais, sem
viewer 3D nem pipeline de jobs, com licença que tem cláusula de branding.

## Consequência arquitetural

Separar API (`server/app.py`) e SPA (`web/`) permite:
- trocar a UI sem tocar no núcleo (a lógica de registry, adapters, fila e persistência é Python puro);
- usar a API programaticamente (`curl`, scripts, CI);
- manter o dropdown sincronizado com o registry por um único endpoint;
- migrar para `mount_gradio_app` no futuro **sem reescrever o núcleo**, caso o escopo do preview mude.

## Lacuna declarada

A contraprova baseou-se em documentação, issues e changelogs — **não** em um spike executando os
três stacks com um GLB animado real da pipeline. A incerteza material restante é o comportamento do
viewer Babylon do Gradio com GLBs retargetados. Se a decisão precisar ser reaberta, 1 dia de spike
(GLB Mixamo no `gr.Model3D` v6.x vs. protótipo three.js mínimo) converte isso em dado.
