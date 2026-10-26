# Camada de refinamento de animações

## English

This document is also available in English: [REFINE.md](REFINE.md).

A animação que sai do pipeline é **bakeada**: uma pose por frame, sem curvas. Esta camada refina esse
clipe com três ferramentas, sem tocar no arquivo original:

```
animação bakeada ──► 1. constraints ──► 2. filtros ──► 3. edições de bone ──► animação refinada
                       (limites)          (estabilização)   (keyframes rebakeados)
```

A ordem importa: os limites são impostos **antes** de suavizar (senão o filtro pode empurrar de volta
para fora do limite), e as edições manuais vêm **por último**, para a sua intenção vencer.

---

## 1. Filtros de estabilização

Seis métodos, todos em numpy puro (sem scipy), selecionáveis por nome e configuráveis por **osso**,
por **eixo** e por **intervalo de frames**:

| filtro | parâmetros | quando usar |
|---|---|---|
| `one_euro` | `min_cutoff` (1.0), `beta` (0.02), `d_cutoff` (1.0) | padrão do projeto; corta mais onde o movimento é lento |
| `moving_average` | `window` (5), `weight` (`linear`\|`uniform`) | simples e previsível; bom para tremores curtos |
| `savgol` | `window` (11, ímpar), `order` (2) | suaviza **preservando picos** e acelerações |
| `kalman` | `process_noise` (1e-3), `measurement_noise` (1e-2) | ruído gaussiano; modelo de velocidade constante |
| `butterworth` | `cutoff_hz` (6.0), `order` (2), `zero_phase` (true) | corte de frequência; `zero_phase` **não introduz atraso** |
| `double_exponential` | `alpha` (0.35), `beta` (0.1) | segue tendência com pouca memória (Holt) |

Quaternions são filtrados com **correção de continuidade de sinal** (`q` e `-q` são a mesma rotação;
filtrar sem isso produz saltos de 2 em cada componente) e re-normalizados.

### Arquivo de configuração

`config/filters_default.yaml`:

```yaml
apply_to_translation: true      # filtra também a translação do root (Hips)
filters:
  - name: one_euro              # mãos tremem mais: janela reativa
    params: {min_cutoff: 1.2, beta: 0.03}
    bones: [LeftHand, RightHand, LeftForeArm, RightForeArm]
  - name: butterworth           # tronco: suave e sem atraso de fase
    params: {cutoff_hz: 6.0, zero_phase: true}
    bones: [Spine, Spine1, Spine2]
    start: 0                    # intervalo opcional (inclusivo)
    end: 120
  - name: savgol                # padrão para todo o resto
    params: {window: 9, order: 2}
  - name: kalman                # só o eixo Y da cabeça, num trecho
    axis: y
    bones: [Head]
    start: 200
    end: 260
```

Regras são avaliadas **em ordem**; a última que casar com o osso vence. Campos: `name`, `params`,
`bones` (omitir = todos), `start`/`end` (intervalo inclusivo), `axis` (`x`/`y`/`z`/`w` = filtra só
esse componente).

**Frames fora do intervalo ficam exatamente iguais** — o filtro é aplicado ao trecho, não ao clipe.

---

## 2. Constraints articulares

Preset humanoide completo em `config/constraints_humanoid.yaml` — **edite o arquivo**, nada de tocar
em código.

```yaml
name: humanoid
limits:
  - {bone: Head, kind: cone, min_deg: -180, max_deg: 50, stiffness: 1.0}
  - {bone: Head, kind: y, min_deg: -70, max_deg: 70}
  - {bone: LeftForeArm, kind: z, min_deg: -5, max_deg: 160}   # sem hiperextensão
  - {bone: RightLeg, kind: z, min_deg: -155, max_deg: 5}
```

* `kind: cone` — limita o **desvio total** da rotação local (em graus). É o que impede "cabeça
  virando 180°" e dá o **cone de movimento** do ombro.
* `kind: x | y | z` — limita **um eixo**, com `min_deg`/`max_deg` **assimétricos**: é assim que se
  expressa *cotovelo/joelho sem hiperextensão* (pouca folga num sentido, muita no outro).
* `stiffness` (0..1) — 1 corrige integralmente, 0 ignora, intermediários aplicam a correção de forma
  **suave (slerp)**, nunca um corte seco.

O relatório de cada execução traz, por osso: frames corrigidos, maior violação (graus) e correção
média. Erros de configuração (`min_deg > max_deg`, `stiffness` fora de [0,1], osso inexistente,
limites conflitantes) geram **mensagem clara** citando o osso e o campo.

---

## 3. Editor de bone (keyframe rebakeado)

Cada edição é: **osso + frame alvo + novo valor + intervalo de frames afetados**.

```
anchor_before ......... [ start ...... frame ...... end ] ......... anchor_after
   (intacto)              ^-------- rebakeado --------^              (intacto)
```

* no `frame` alvo a pose passa a ser a editada;
* de `anchor_before` (= `start-1`) ao frame alvo, interpolação com **ease-in-out** (smoothstep:
  derivada zero nas pontas → sem salto);
* do frame alvo a `anchor_after` (= `end+1`), o mesmo na volta;
* **frames fora de `[start, end]` ficam bit-exatamente iguais** (verificado por teste de diff).

Isso é o "sistema de keyframes" pedido, mas **rebakeado**: o arquivo final continua com uma pose por
frame, sem curvas.

### Histórico

Toda edição entra em um histórico persistido (JSON) com osso, frame, intervalo, autor, nota e
timestamp. Desfazer/refazer funcionam, e **fechar e reabrir a sessão mantém as edições aplicadas** —
o clipe é reconstruído do original + histórico salvo.

---

## 3b. Editor de curvas (a timeline, como num DCC)

O editor de bone é a visão *numérica* da mesma coisa; o editor de curvas é a *gráfica*.
`http://127.0.0.1:8000/refine` → a **timeline atravessa o rodapé da página**, nas duas colunas, e é
um widget só:

```
┌ barra ── osso │ eixos │ |◀ ◀ ▶ ▶ ▶| │ frame 87 · 2,90 / 8,00 s │ + key − key │ enquadrar │ tela cheia ┐
├ régua ── frames  30 fps   0    20    40    60 …                                     │
├ keyframes ── Head      ◆              ◆                                           │
└ curvas ──  X  ────────╱╲──────╱╲────────  (X −3,3°  Y 35,0°  Z −29,2°)
```

* **clique numa curva** para inserir uma keyframe naquele frame (a curva não salta — a keyframe
  passa a controlar a partir dali);
* **arraste o losango** para mudar o valor (na vertical) ou para mover a keyframe no tempo
  (na horizontal);
* **arraste a régua** para scrubar; a **linha das keyframes** mostra as keyframes do osso (clique
  numa para selecionar, clique na linha para inserir uma);
* as **linhas X/Y/Z da esquerda** mostram o **ângulo de cada canal da keyframe selecionada**:
  a letra liga e desliga a curva, o ângulo abre um **input** para digitar o valor exato, e o
  **↺** devolve o canal ao valor **original**;
* **clicar no losango** de uma keyframe abre esse mesmo input ali mesmo — arrastar continua
  movendo o losango, clicar (sem arrastar) digita o valor;
* a curva tracejada é a **original** (antes das keyframes), desenhada num **tom mais claro da
  própria cor do canal** — perto o bastante para saber a que eixo pertence, longe o bastante
  para não se confundir com a curva editável;
* a **faixa** atrás da keyframe selecionada é a janela de rebake, e as marcas pequenas nas pontas
  são as âncoras;
* a curva mostrada é o **rebake de verdade**: o navegador reproduz a matemática do servidor (slerp +
  a mesma suavização), então o que você arrasta é o que vai para o arquivo.

### A prévia acompanha o arraste, em tempo real

Enquanto uma keyframe é arrastada, a pose do osso **no frame atual** muda a cada pixel — e a
prévia 3D mostra isso na hora, sem esperar o servidor: o navegador aplica a mesma matemática do
rebake e escreve o quaternion direto na cena. O servidor só é chamado quando o mouse solta.

### Curvas de interpolação (alças de bezier)

Cada keyframe tem sua própria interpolação, e uma delas é uma **curva** de verdade: `bezier`. Nesse
modo a keyframe ganha duas alças (as de entrada e de saída dos DCCs), desenhadas como uma linha
tracejada a partir da keyframe, e **arrastar uma alça modela o segmento** — é assim que o valor sobe
e desce entre duas keyframes.

* as alcas são **automáticas** por padrão (um terço do segmento, sobre a reta entre as duas
  keyframes — o "auto" do Blender): elas aparecem quando a keyframe passa para `bezier`;
* a distância horizontal da alça é em **frames**, a vertical em **graus do canal** a que a alça
  pertence (o canal é o da curva que você arrastou);
* o navegador reproduz a bezier do servidor exatamente (a mesma cúbica, a mesma bisseção), então o
  preview do arraste é o que vai para o arquivo.

### Entrada e saída, separadas

Toda keyframe tem **duas** opções de interpolação:

| opção | o que ela modela |
|---|---|
| **entrada** (`interp_in`) | como a curva **chega** na keyframe |
| **saída** (`interp_out`) | como a curva **sai** dela |

O segmento entre duas keyframes segue a **saída** de quem deixa na primeira metade e a
**entrada** de quem chega na segunda. Com as duas opções iguais, o segmento inteiro usa
aquele modo (o comportamento de sempre); com opções diferentes, o segmento é dividido ao
meio — é assim que se consegue "chega devagar, sai rápido" em uma única keyframe.

### As famílias de easing

Além de `suave` (ease-in-out), `linear`, `degrau` (hold) e `curva` (as alças da bezier), cada
opção pode ser uma das famílias clássicas de easing:

| família | o formato |
|---|---|
| `sine` | aceleração suave |
| `quad` | quadrática |
| `cubic` | cúbica |
| `quart` | quártica |
| `quint` | quíntica |
| `expo` | exponencial (bem fechada) |
| `circ` | circular |
| `back` | passa do ponto e volta |
| `elastic` | elástica |
| `bounce` | quica |

Cada família tem uma variante de **entrada** (a curva acelera até a keyframe) e uma de **saída**
(ela freia saindo) — é esse o par que as duas opções escolhem. `back` e `elastic` passam do valor
de propósito (o movimento vai além da keyframe e volta); `bounce` quebra a monotonicidade (o
movimento quica).

### O intervalo de efeito da keyframe (início e fim)

Toda keyframe age num **intervalo de frames** — os que ela reescreve. Por padrão o intervalo sai
das vizinhas (vai do frame seguinte à keyframe anterior até o frame anterior à seguinte), mas dá
para escolher:

* a **barra** embaixo da keyframe na timeline mostra o intervalo, e **as duas pontas se arrastam**;
* os campos **início** / **fim** da keyframe selecionada mostram e mudam os mesmos números;
* a faixa escurecida atrás da keyframe selecionada no gráfico é o mesmo intervalo.

Uma keyframe com intervalo estreito só mexe o movimento dentro dele — o resto do clipe fica
bit-exatamente como estava.

### A janela vem das vizinhas

Quando o intervalo **não** é escolhido, a janela vem das vizinhas: vai do frame **seguinte à
keyframe anterior** até o frame **anterior à seguinte**, de modo que as pontas da janela são as
vizinhas — que viram as âncoras do rebake. O resultado não depende, então, da ordem em que as
keyframes foram criadas, e uma keyframe sem vizinhas ganha uma rampa de ±20 frames.

### Interpolação por keyframe

Cada keyframe tem **duas** opções de interpolação — **entrada** (como a curva chega nela) e
**saída** (como ela sai) — e cada uma pode ser `suave` (ease-in-out, derivada zero nas pontas),
`linear`, `degrau` (hold) ou `curva` (bezier, com as alças).

### Apagar uma keyframe apaga mesmo

O osso é reconstruído a partir da sua **base** (a série que ele tinha antes da primeira keyframe)
mais o conjunto atual de keyframes. Sem a base, apagar uma keyframe deixaria o rebake antigo
assado no clipe; com ela, o osso volta exatamente onde estava.

---

## 4. Relatório comparativo

Para cada filtro aplicado ao **mesmo clipe**, medimos:

| métrica | o que diz |
|---|---|
| `ganho_suavidade_%` | redução da energia do jerk (2ª derivada) — quanto mais alto, mais suave |
| `atraso_frames` | defasagem via correlação cruzada (0 = sem atraso) |
| `rmse` | desvio residual em relação ao sinal original |
| `erro_angular_*_deg` | quanto a rotação de fato mudou (médio e máximo) |

Saída em tabela Markdown + JSON (`filtros_comparativo.md`, `refine_report.json`).

Exemplo real (clipe sintético, 80 frames):

```
| filtro             | ganho_suavidade_% | atraso_frames | rmse     | erro_angular_medio_deg |
|--------------------|-------------------|---------------|----------|------------------------|
| one_euro           | 98.79             | 0             | 0.018755 | 3.938                  |
| moving_average     | 98.73             | 0             | 0.012899 | 2.729                  |
| savgol             | 98.47             | 0             | 0.015252 | 3.229                  |
| kalman             | 94.78             | 0             | 0.014819 | 3.090                  |
| butterworth        | 94.65             | -1            | 0.017353 | 3.639                  |
| double_exponential | 94.46             | 0             | 0.017145 | 3.438                  |
```

---

## 5. CLI

```powershell
# exemplos antes/depois + relatório comparativo (gera em storage/refine_samples/)
.\.venv\Scripts\python.exe tools\refine.py --make-samples

# aplica constraints + filtros e salva o clipe refinado (+ GLB/FBX)
.\.venv\Scripts\python.exe tools\refine.py --input storage\refine_samples\sample_before.json `
    --constraints config\constraints_humanoid.yaml --filters config\filters_default.yaml `
    --out storage\refine_samples\refined

# só compara filtros
.\.venv\Scripts\python.exe tools\refine.py --input clipe.json --compare one_euro,savgol,kalman

# injeta uma violação absurda (cabeça a 180°) para ver o preset corrigir
.\.venv\Scripts\python.exe tools\refine.py --input clipe.json --inject-violation head180 `
    --no-filters --out out/
```

---

## 6. Uso como biblioteca

```python
from core.refine import refine_animation, boneedit as be

refinado, relatorio = refine_animation(
    anim,
    constraints_config="config/constraints_humanoid.yaml",
    filters_config="config/filters_default.yaml",
    edits=[be.BoneEdit(bone="Head", frame=30, rotation_euler_deg=[0, 30, 0], start=20, end=45)],
)

print(relatorio.stages)          # ['constraints', 'filters', 'edits']
print(relatorio.constraints)     # frames corrigidos por osso
```

### Integração com o pipeline

Basta passar `params.refine` no job — a camada roda **antes** do export:

```json
{"fps": 30, "refine": {"constraints": "config/constraints_humanoid.yaml",
                       "filters": "config/filters_default.yaml"}}
```

Sem `params.refine`, o comportamento é exatamente o de antes.

---

## 7. Editor na interface

`http://127.0.0.1:8000/refine` — mesma linguagem visual do pipeline:

* seleciona o clipe (jobs com `anim.json`), navega por frame, escolhe o osso e ajusta X/Y/Z;
* define o **intervalo afetado** e aplica; a prévia 3D (esqueleto + malha) atualiza na hora;
* desfazer/refazer/limpar, comparar filtros (tabela) e aplicar o refino (gera GLB/FBX refinados);
* edita os limites de constraint e salva no YAML.
