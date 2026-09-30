# Comparison of local web UI frameworks

## Português

Este documento também está disponível em português: [UI_FRAMEWORK_COMPARISON.pt-BR.md](UI_FRAMEWORK_COMPARISON.pt-BR.md).

Criteria derived from the product requirements: a backend dropdown fed by the plugin registry;
video upload; persistent job status panel; **3D preview with play/pause, timeline (scrub) and
camera switching**; GLB and FBX download; local startup with a single command.

> This comparison went through an **adversarial counter-check** (document
> `.cluster/video2mixamo/subagent_02.md`), which corrected a common premise:
> Gradio's `gr.Model3D` **does play** the GLB animation on autoplay (Babylon viewer,
> `animationAutoPlay=true` since PR #10993). What it does **not** offer is a control API
> (play/pause/timeline/scrub) from Python. The decision below was rewritten with that correction.

## Matrix by requirement

| Requirement | Gradio | Streamlit | **FastAPI + SPA (chosen)** |
|---|---|---|---|
| **3D preview + play/pause + timeline + camera** | **almost** — orbit and GLB animation autoplay work; play/pause/timeline need injected JS (`js=`) or a custom component | **breaks** — every 3D viewer lives in an **isolated iframe**; the app's controls cannot command it; it would need a React component (≈ writing the SPA) | **native** — three.js `AnimationMixer` + our own timeline |
| Dynamic dropdown from the plugin registry | works (`Dropdown.choices` as output) | works (`selectbox.options`) | works (`GET /api/backends`) |
| Long jobs + progress + cancellation | queue, `gr.Progress` and `cancels=` native; **persistence across sessions is DIY** (official guide tells you to bring APScheduler); recent cancellation bugs (#13323/#13895) | own executor + `st.fragment(run_every=)` for polling (supported); cooperative cancellation; open state bug on auto-rerun (#14064) | **same work** (`JobStore` + worker), without fighting the framework's execution model |
| Two download formats per job | works (`DownloadButton`) | works (`st.download_button`) | works (`FileResponse`) |
| Extensible adapters | works | works (mind import-time registration + reruns) | works (no foreign lifecycle) |
| Single-command startup | great | great | great (`python run.py`) |
| Weight / license | heavy · Apache-2.0 | heavy · Apache-2.0 | **light · MIT** |

**Point the counter-check reinforces:** none of the three ships a *multi-session persistent job
system* out of the box. On any base, the `JobStore` + worker with cooperative cancellation is its own
module — as it in fact was implemented (`core/jobs.py`, `core/pipeline.py`).

## Decision: FastAPI + own SPA (three.js)

**Correct justification** (rewritten after the counter-check): it is not "Streamlit/Gradio cannot do
jobs or downloads" — they do, and well. The deciding factor is that the **product's central
requirement** (timeline + play/pause + camera switching over a GLB animation, controlled by the app)
**pushes both frameworks into custom components / injected JS**. That is: the cost of a customized
frontend is paid either way — and the direct path also avoids (i) Streamlit's iframe bridge and
(ii) the coupling to Gradio's Babylon viewer release cycle (regression #10983/#10993 is evidence
that this coupling breaks in practice).

**Explicit, conditional fallback:** if the preview is downgraded to "orbit the GLB with autoplay"
(no timeline and no controls of our own), the hypothesis falls in favor of **Gradio mounted on
FastAPI** (`gr.mount_gradio_app`) — which delivers requirements (b)–(e) with far less code and keeps
FastAPI underneath. Gradio **does not abandon** FastAPI: it is FastAPI.

**Discarded: Streamlit** for this product. The script re-execution model and the iframe isolation
attack exactly the central requirement; for the other requirements it is adequate, but they are not
the differentiators.

**Open WebUI** was discarded in the initial research: it is a chat-first product for local LLMs,
with no 3D viewer and no job pipeline, under a license with a branding clause.

## Architectural consequence

Separating API (`server/app.py`) and SPA (`web/`) allows:
- swapping the UI without touching the core (the registry, adapters, queue and persistence logic is pure Python);
- using the API programmatically (`curl`, scripts, CI);
- keeping the dropdown in sync with the registry through a single endpoint;
- migrating to `mount_gradio_app` in the future **without rewriting the core**, if the preview scope changes.

## Declared gap

The counter-check relied on documentation, issues and changelogs — **not** on a spike running the
three stacks with a real animated GLB from the pipeline. The remaining material uncertainty is the
behavior of Gradio's Babylon viewer with retargeted GLBs. If the decision needs to be reopened,
1 day of spike (a Mixamo GLB in `gr.Model3D` v6.x vs. a minimal three.js prototype) turns it into data.
