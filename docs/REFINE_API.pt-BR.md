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
 "bone_parents": {"Spine": "Hips", "LeftForeArm": "LeftArm", "...": "..."},
 "root_translation": [[0.0, 0.93, 0.0], "..."],
 "history": {"session_id": "...", "edits": [{"bone": "Head", "frame": 30, "start": 20, "end": 45,
              "author": "editor", "note": "virar cabeca"}], "undone": []}}
```

`409` se o job não tiver `anim.json` (rode o pipeline de novo — a animação bakeada passou a ser salva).

`bone_parents` é a hierarquia do rig — um cliente que desenha a própria prévia precisa dela para
converter uma edição no espaço do mundo no quaternion local do osso.

### `GET /api/refine/animation/{job_id}/frame/{t}`

Pose do frame `t` (quaternion + Euler em graus) para todos os ossos animados. Fora do intervalo do
clipe devolve `400`.

O parâmetro de query **`space`** escolhe o referencial dos ângulos de `euler_deg`:

| `space` | significado | eixos |
|---|---|---|
| `local` (padrão) | rotação do osso **em relação ao pai** | acompanham a hierarquia |
| `global` | **orientação absoluta** do osso na cena | presos ao mundo |

Os dois coincidem no repouso e divergem assim que um ancestral gira — com o braço levantado o
antebraço marca `Z = -0,6°` em local contra `Z = -62,7°` em global. `quat` é **sempre** o quaternion
local (é o que o clipe guarda). `space` desconhecido devolve `400`.

### `GET /api/refine/animation/{job_id}/glb`

Exporta o **clipe atual** (com as edições) em GLB — é o que alimenta a prévia do editor.

### `GET /api/refine/animation/{job_id}/curves?bone={osso}&space={local|global}`

A **curva** de rotação do osso, frame a frame — é o que o editor de curvas desenha.

```json
{"job_id": "d59925e9a2a3", "bone": "Head", "space": "local", "fps": 30.0, "num_frames": 60,
 "channels": {"x": [0.0, "..."], "y": [12.5, "..."], "z": [0.0, "..."]},
 "quats": [[0.0, 0.0, 0.0, 1.0], "..."],
 "base_quats": [[0.0, 0.0, 0.0, 1.0], "..."],
 "chain": [{"bone": "Neck", "quats": [[0.0, "..."], "..."]}, "..."],
 "keyframes": [{"index": 0, "frame": 30, "start": 11, "end": 49,
                "value": [0.0, 40.0, 0.0], "interp": "smooth", "space": "local", "note": ""}]}
```

* `channels` — os ângulos Euler (graus) do osso em cada frame, **no referencial pedido** (o mesmo
  significado de `space` no endpoint de frame acima).
* `quats` — o quaternion local de cada frame (é o que o clipe guarda).
* `base_quats` — a série do osso **sem keyframes**: a base sobre a qual as keyframes são aplicadas.
  É `null` enquanto o osso não tiver keyframes. Um cliente que reaplica as keyframes sozinho (para
  prever um arraste) tem de partir desta série, e não de `quats` — senão apagar uma keyframe
  deixaria o rebake antigo pelo clipe.
* `chain` — os ancestrais do osso, da raiz para baixo, com os quaternions por frame: o que um
  cliente precisa para converter o alvo de uma keyframe para o referencial do mundo.
* `keyframes` — as keyframes do osso (as edições dele no histórico), com o valor **no referencial
  pedido**, a janela do rebake (`start`/`end`) e o modo de interpolação.

`404` se o osso não tiver rotação animada. `400` para `space` desconhecido.

### `PUT /api/refine/animation/{job_id}/keyframes`

Substitui o **conjunto inteiro** de keyframes do osso — é o que o editor de curvas grava.

```json
{"bone": "Head", "space": "local", "half": 20,
 "keyframes": [{"frame": 12, "rotation_euler_deg": [0, 35, 0],
                "interp_in": "smooth", "interp_out": "linear",
                "handle_in": null, "handle_out": null, "handle_channel": 1}]}
```

* `half` — metade da largura (em frames) da janela de rebake de uma keyframe sem vizinhas
  (padrão 20, ou seja ~0,7 s de rampa de cada lado).
* `interp_in` — como a curva **chega** na keyframe; `interp_out` — como ela **sai**. As duas são
  `smooth` (ease-in-out, o padrão), `linear`, `step` (hold), `bezier`, ou uma das famílias
  clássicas de easing: `sine`, `quad`, `cubic`, `quart`, `quint`, `expo`, `circ`, `back`,
  `elastic`, `bounce`. Cada família tem uma variante de entrada (a curva acelera até a keyframe)
  e uma de saída (ela freia saindo) — é o par que as duas opções escolhem. O segmento entre duas
  keyframes segue a **saída** de quem deixa na primeira metade e a **entrada** de quem chega na
  segunda; com as duas iguais, o segmento inteiro usa aquele modo. (`interp` sozinho é aceito como
  apelido de `interp_in`.)
* `handle_in` / `handle_out` — as **alças da bezier** da keyframe: `[dx, dy]`, com `dx` em frames e
  `dy` em graus do canal `handle_channel` (0=X, 1=Y, 2=Z). `null` = alça automática (um terço do
  segmento, sobre a reta entre as keyframes). A alça da keyframe **anterior** modela o segmento que
  entra nesta — por isso o servidor resolve o conjunto inteiro de uma vez.
* A janela de cada keyframe vem das **vizinhas**: do frame seguinte à keyframe anterior até o frame
  anterior à seguinte, de modo que as pontas da janela são as vizinhas — que viram as âncoras do
  rebake. O resultado não depende, então, da ordem em que as keyframes foram criadas.
* `start` / `end` — o **intervalo de efeito** da keyframe (os frames que ela reescreve), arrastado
  na timeline. `null` (o padrão) mantém a janela das vizinhas; quando informado, a keyframe age só
  naquele trecho e o resto do clipe fica bit-exatamente como estava. O frame da keyframe precisa
  estar dentro do intervalo.
* O osso é reconstruído a partir da **base** (a série sem keyframes) mais o conjunto novo, e por
  isso **apagar** uma keyframe devolve o osso ao ponto anterior, em vez de deixar o rebake antigo no
  clipe.

Resposta: `{"report": {"bone": "Head", "keyframes": 2, "frames_rewritten": 58, "affected": [0, 49]},
"history_len": 2, "keyframes": [ ... ]}` (as keyframes como o servidor gravou).

`400` para: frame fora do clipe, keyframe sem alvo de 3 valores, duas keyframes no mesmo frame,
`space` ou `interp` desconhecidos, alça malformada. `404` se o osso não tiver rotação animada.

### `POST /api/refine/animation/{job_id}/edit`

```json
{"bone": "Head", "frame": 30, "rotation_euler_deg": [0, 40, 0], "space": "local",
 "start": 20, "end": 45, "interp": "smooth", "author": "editor", "note": "virar cabeca"}
```

Campos aceitos para o alvo: `rotation_euler_deg` (3) ou `rotation` (4, quaternion) ou `translation`
(3 — **só no osso `Hips`**). `start`/`end` definem o intervalo afetado (default: só o frame).

`space` (`local` por padrão) é o referencial de `rotation_euler_deg`, com o mesmo significado do GET
acima; é ignorado quando o alvo vem como quaternion cru. Em `global` os ângulos são convertidos para
a rotação local do osso usando a **rotação de mundo do pai no frame alvo** — a única definição
consistente num clipe 100% bakeado, em que não existe curva para reavaliar. Edições salvas antes
desse campo existir assumem `local`. `space` desconhecido devolve `400`.

Resposta:

```json
{"report": {"bone": "Head", "frame": 30, "affected": [20, 45], "frames_rewritten": 26,
            "anchors": [19, 46], "author": "editor", "space": "local"}, "history_len": 1}
```

`400` para: frame fora do clipe, intervalo inválido, frame editado fora do intervalo, osso
inexistente, osso end-cap, edição sem alvo, `translation` fora do `Hips`, `space` ou `interp`
desconhecidos.

`interp` escolhe como o rebake mistura: `smooth` (ease-in-out, o padrão e o comportamento de
antes), `linear` (uma reta) ou `step` (segura o valor até o frame alvo).

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
