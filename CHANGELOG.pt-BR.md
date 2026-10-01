# Changelog

## English

This document is also available in English: [CHANGELOG.md](CHANGELOG.md).

## [1.4.14] — Mistura de partes do corpo entre dois jobs

Reported: *"eu rodei dois jobs e acho que em um deles o braço tem um tracking melhor que o do outro; quero
o corpo do B e o braço do A"*

### Adicionado

* **`Misturar partes entre dois jobs`**, no editor de refino: escolha o job de **origem**, marque as
  partes do corpo (quadril, torso, cabeça, braco esquerdo/direito, perna esquerda/direita) e aplique.
  O clipe do editor passa a ter só aquelas partes vindo do outro job; o resto continua como estava.
  Há um **desfazer** que volta ao clipe anterior.

  Verificado no navegador com dois jobs reais do mesmo vídeo (`4ac4511714b6` mediapipe →
  `ba5bb21ae562` rtmpose): 19 ossos do braço esquerdo, **erro angular 0,000° contra a origem**, o
  restante do corpo idêntico ao destino, e o desfazer restaurando os dois.

* **O transplante acontece em espaço de mundo, e isso é o ponto.** As rotações do clipe são **locais
  (em relação ao pai)**, então colar a rotação local do braço de A no corpo de B não daria o braço de
  A — daria um braço torto, porque o ombro é outro. O que entra no destino é a **rotação de mundo** de
  A, reexpressa em relação ao pai (já transplantado) de B:

  ```
  local_novo[t] = conj(mundo_pai_novo[t]) · mundo_A[t]
  ```

  Ossos **abaixo** de uma parte transplantada que não foram escolhidos mantêm a rotação local do
  destino e giram junto — é assim que o antebraço e a mão acompanham o ombro.

  Consequência útil: escolher só `torso` dá o torso de A **com os braços de B pendurados nele**, e não
  o personagem inteiro de A.

* As partes saem da **hierarquia do rig**, não de uma lista na mão: braço = subárvore de
  `LeftShoulder` (19 ossos, incluindo os 15 dos dedos), perna = subárvore de `LeftUpLeg`,
  cabeça = subárvore de `Neck`. `torso` é a única lista explícita (`Spine`, `Spine1`, `Spine2`) —
  a subárvore de `Spine2` contém braços, pernas e cabeça, e uma "parte" que engole o personagem todo
  não é uma parte.

* **A trajetória do quadril** (a translação da raiz, que é o movimento do corpo no espaço) só é copiada
  junto com a parte `quadril / raiz`, por padrão. Há uma caixa para forçar.

* Jobs com **número de frames ou fps diferentes** são recusados com erro claro: misturar clipes
  diferentes exigiria alinhar no tempo, que não é feito.

* Desfazer guarda um **snapshot do clipe inteiro** (o histórico de edições é da granularity de um
  `BoneEdit` por osso/frame; um transplante seriam milhares). `EditHistory` ganhou `snapshots`.

* **Layout de duas colunas no editor**: os controles ficam numa coluna que rola sozinha e a prévia
  fica **presa ao lado** — vídeo + esqueleto + malha no `Refino`, o par A/B na `Mistura A/B`.
  Ajustar um osso não exige mais rolar até o fim para ver o resultado, subir para ajustar e descer de
  novo: a prévia nunca sai da tela, e a página em si deixa de rolar. Abaixo de 1180 px as colunas
  empilham, então janelas estreitas continuam funcionando.

* **O seletor de origem foi para o painel A e o destino virou texto puro.** A aba de mistura
  mostrava o seletor de job na coluna de ajustes enquanto o clipe A ficava na coluna da prévia, do
  outro lado — não ficava claro qual job o seletor controlava. O seletor agora fica logo acima do
  painel A, e o hash do destino é texto somente-leitura em vez de uma caixa de input com aparência
  de editável.

* **Arrastar a timeline durante a reprodução passou a funcionar.** O loop de reprodução reescrevia o
  slider ~60x/s, então o arrasto voltava para debaixo do cursor e parecia que o evento `input` estava
  quebrado. Agora o slider é do usuário durante o arraste (a reprodução pausa no `pointerdown` e volta
  no `pointerup`), e o rótulo de tempo continua atualizando. Verificado no Chrome headless com arrasto
  real de mouse na página do pipeline: o slider segue o ponteiro, o vídeo segue o slider e o rótulo confere.

* **O editor agora tem duas abas** — `Refino` (clipe, editor de bone, filtros, constraints, prévia) e
  `Mistura A/B` — para os controles de mistura não apertarem a tela de refino. A barra de tempo e o
  log ficam **fora** das abas, entao a mesma timeline serve as duas: arrastar o playhead na aba da
  mistura move os painéis A/B e o vídeo de referência juntos. Trocar de aba redimensiona os canvas
  3D (um canvas oculto mede 0 e renderizaria em branco).

* **Painéis A/B lado a lado**, dentro da aba `Mistura A/B`: os dois jobs na mesma câmera e no mesmo frame da
  timeline, com os nomes dos jobs. Escolher a origem no seletor recarrega o par; aplicar ou desfazer a
  mistura recarrega o painel de destino para mostrar o resultado.

* **Mapeamento de intervalo**, para quando os dois jobs não são exatamente o mesmo trecho de vídeo —
  por exemplo `frames 1 a 23 do braco esquerdo de A` aplicados nos `frames 3 a 35 de B`. A duração pode
  mudar (a origem é reamostrada por **slerp no tempo**, não por índice), e **só o intervalo do destino é
  reescrito**: os frames de fora continuam exatamente como estavam.

  Medido no job real (`1..23` → `3..35`, 23 frames viram 33): erro angular contra a origem
  **média 0,107°, máximo 0,416°** dentro do intervalo (a diferença é só o arredondamento da
  reamostração), e os frames fora dele **0,0000°** contra o destino — intocados.

  Sem o mapeamento, jobs com números de frames diferentes continuam sendo recusados. O `fps` precisa
  bater de qualquer forma: com fps diferente as durações não são comparáveis.

  O relatório de erro também passou a medir **só o intervalo mapeado** — medir o clipe inteiro dava
  85° médios, que eram só os frames de fora (que por desenho continuam sendo do destino).

## [1.4.13] — Prévia: vídeo e 3D compartilham enquadramento e relógio

Relatado: *"o output da malha/esqueleto não está muito bem alinhado com o vídeo, o preview da animação tem um delay em relação à referência"*

### Corrigido

* **O 3D sempre corria 1–2 frames atrás do vídeo.** O loop de reprodução movia o esqueleto a partir de
  `video.currentTime`, que é o *relógio de reprodução* — ele corre à frente do frame que o navegador já
  pintou. O viewer agora lê o instante do frame apresentado em
  `requestVideoFrameCallback` (`mediaTime`), então a pose na tela é a pose do frame que se está vendo.
  Custo medido do caminho antigo: 33–66 ms a 30 fps, constante. Navegadores sem a API
  (Safari < 15.4, Firefox < 132) caem para `currentTime`, como antes.

* **O desalinhamento ainda crescia de 0 até cerca de um frame ao longo do clipe.** O mapeamento era
  `videoTime / video_span_s * clipDuration`, mas o último keyframe do GLB está em `(T-1)/fps` enquanto
  `video_span_s` é `T/fps` — a animação era comprimida em `(T-1)/T` (**0,6%** no clipe de referência de
  167 frames, ~33 ms de atraso no último frame) e o loop passava um frame do fim do clipe. A prévia
  agora mapeia **frame a frame** (`animTime = mediaTime * videoFps / targetFps`) e faz o loop no último
  keyframe, então os dois ficam travados no clipe inteiro e em qualquer combinação de fps.

* **A câmera era fixa e o vídeo não**, o que fazia os três painéis parecerem desalinhados mesmo com a
  pose certa. A nova preset **`enquadrar vídeo`** reenquadra o 3D com a *mesma* geometria que o lifter
  usou para liftar a pose (quadril em 0,98 m, tronco 0,52 m), derivada das juntas 2D do primeiro frame e
  do tamanho do vídeo:
  * distância = `0.52 / (2 · (tronco_px / altura_video) · tan(fov/2))`
  * altura do olho = `0.98 − d · tan(fov/2) · (1 − 2·py_quadril / altura_video)`, mais um termo
    horizontal para personagens fora do centro.

  Aí as frações de quadro do personagem batem com as do vídeo **exatamente** (verificado até 1e-9 em
  `tests/test_preview_sync.py`): o quadril projeta exatamente no pixel detectado. No job de
  referência cai em 3,69 m / 0,92 m, perto dos 3,6 m / 0,95 m fixos — para um personagem centrado
  muda pouco, e para um fora do centro ou com outra escala o deslocamento aparente desaparece.
  Disponível na página do pipeline e no editor de refino, recalculada ao redimensionar.

* **Regressão no editor de refino (o vídeo "corria" e acabava antes de qualquer movimento).** Ao
  fazer o vídeo de referência seguir o frame, calculei o alvo como `frame · videoFps / targetFps`, que
  é a inversa — a fórmula que vai de **vídeo para animação**. Quando os fps batem ela dá `frame`, e o
  alvo é um índice de frame sendo usado como **segundo**: no frame 36 o vídeo era mandado para 36 s
  num clipe de 5,57 s. Resultado: a busca batia no fim do clipe a partir do **frame 6**, o vídeo
  acelerava até o fim e ficava lá, enquanto o esqueleto animava normal — exatamente o sintoma
  relatado. O correto é `frame / videoFps`. Verificado no navegador (Edge headless, job
  4ac4511714b6): quadro 0/1/6/36/90/166 → 0,000/0,033/0,200/1,200/3,000/5,533 s, erro 0,0000 s.
  A guarda de re-busca passou a ser `0.5 / videoFps` (segundos de vídeo) por consistência.

* **`Aplicar edição` falhava em quase todo frame.** A janela afetada tinha padrão `[0, 10]` e nunca
  acompanhava o frame alvo, então o servidor rejeitava a edição inteira com *"o frame editado (36)
  precisa estar dentro de [0, 10]"* — isso aparecia só como uma linha no log do rodapé, sem nada no
  editor indicando que o problema era a janela. Agora a janela **recentra no frame alvo**,
  preservando a largura escolhida, de modo que o alvo fica sempre dentro dela.

* **O rampa era torto, e o movimento virava chicote.** Com a janela de um lado só, o smoothstep é
  suave só nas pontas — o ângulo inteiro ainda precisa ser coberto, só que em menos frames. Medido
  no `LeftArm` do job 4ac4511714b6 (alvo no frame 36, −120° em Z): janela `[0,50]` deu pico de
  entrada de 1,84°/frame contra 5,48°/frame na saída (**2,98×**); uma janela centrada `[26,46]` dá
  5,28 contra 4,91 (**0,93×**). A largura padrão agora é **40 frames** (~0,7 s de rampa de cada
  lado) em vez de 10, escolhida pela medição abaixo.

  | janela | pico de entrada | vs a taxa natural do osso |
  |---|---|---|
  | 10 frames | 9,54 °/frame | 7,1× |
  | 20 frames | 5,28 °/frame | 4,0× |
  | 40 frames (padrão novo) | 3,00 °/frame | 2,2× |

  A taxa mediana do próprio osso no clipe é 1,34 °/frame, então uma janela mais larga traz a mistura
  para perto de como a animação já se move. Quem quiser uma edição mais curta ainda pode estreitar a
  janela.

### Também

* O esqueleto só é repinado quando o instante realmente mudou (≥ 0,1 ms), em vez de 60× por segundo,
  o que remove jitter do mixer e trabalho desperdiçado.

* **Regressão, pegada no teste manual: a prévia travava / entrava em loop enlouquecido.** O ponto
  de wrap era decidido pelo `mediaTime` do `requestVideoFrameCallback`, e isso é uma armadilha:
  **um seek descarta o pipeline do navegador, e um frame só é *apresentado* — disparando o callback —
  se não houver outro seek antes.** Seek em cima de seek congela o `mediaTime` no último frame, a
  condição de wrap continua verdadeira e ela dispara de novo no frame seguinte: um ciclo que se
  auto-sustenta refazendo seek para sempre. Medido no Edge headless no clipe de referência (5,53 s):
  o caminho antigo deixava o vídeo **parado em 5,566 s com a pose presa em `5.53 / 5.53 s` e zero
  frames apresentados**; o novo apresenta **30 frames/s e dá um loop por ciclo de 5,53 s**. O wrap
  passou a ser decidido pelo relógio do próprio vídeo (`currentTime`, que zera de verdade no seek) e
  o `mediaTime` velho é descartado, enquanto a pose continua usando o frame apresentado. O
  `requestVideoFrameCallback` também encadeia de forma idempotente agora — cada evento `play` criava
  outra cadeia que nunca morria.

## [1.4.12] — Editor de refino: editar o osso em eixos locais ou globais

Relatado: *"no editor de refino o X, Y e Z estão atrelados ao espaço global — disponibilize uma opção de editar em relação ao espaço local."*

### Adicionado

* **Seletor de referencial no editor de bone** (`local` / `global`), ao lado do seletor de osso. Os
  sliders X/Y/Z e a prévia 3D ao vivo seguem a escolha, e o valor viaja com a edição (`space` no
  `POST /edit`, devolvido no relatório e guardado no histórico).
  * `local` — rotação do osso **em relação ao pai**; os eixos acompanham a hierarquia, então o número
    mostrado é a contribuição da própria junta. É o que o editor já fazia e continua sendo o padrão,
    então **edições existentes e históricos salvos não mudam**.
  * `global` — **orientação absoluta** do osso na cena; os eixos ficam presos ao mundo, então o mesmo
    número significa a mesma coisa em qualquer osso e em qualquer frame.

  Medido no braço levantado do job `e63f073c2149` (frame 36): o antebraço esquerdo marca
  `Z = -0,6°` em local contra `Z = -62,7°` em global — os 62° são todos do ombro, que é exatamente a
  confusão que o seletor elimina.

* Os dois espaços são **inversos exatos um do outro**: ler um ângulo e gravá-lo de volta devolve o
  mesmo quaternion (erro < 1e-6°, coberto por testes). Uma edição em global é convertida para a
  rotação local do osso pela **rotação de mundo do pai no frame alvo** — a única definição
  consistente num clipe 100% bakeado, em que não existe curva para reavaliar. Na raiz (`Hips`) os
  dois coincidem, porque o pai é a identidade.
* `GET /animation/{job_id}` passou a devolver também `bone_parents` (a hierarquia do rig), para um
  cliente fazer a mesma conversão global→local na própria prévia.
* Um `space` desconhecido é recusado com `400` claro em vez de ser ignorado em silêncio, tanto na
  leitura do frame quanto na edição.

### Verificado

* pytest **160/160** (9 testes novos: os dois referenciais são mesmo diferentes, round-trip exato
  por osso × espaço, uma edição global chega no ângulo de mundo pedido enquanto o mesmo número em
  local não, `space` inválido dá erro, edições antigas sem o campo continuam `local`, e
  `world_rotation` concorda com a FK).
* API viva checada por HTTP contra um job real: os dois espaços leem de volta, uma edição global de
  `Z = 45°` relida como `Z = 45°`, a resposta padrão é idêntica ao comportamento `local` antigo, e
  `space=banana` devolve `400`.

## [1.4.11] — Juntas honestas: dobradiças geométricas, reprojeção 2D exata, SavGol sem escala

Relatado no job `e63f073c2149` (`sword_swing_B`, t=1,19 s): *"o rightarm está levemente curvado para a direita, inclusive no debug, mas no esqueleto ele está reto"* e *"o left arm ainda está dentro do torso, precisa de um cálculo de depth melhor, porque no vídeo ele está na frente do torso"*.

Três bugs independentes, encontrados medindo o job salvo offline (sem chute: cada correção é um número antes/depois no clip real).

### Corrigido

* **O braço era	endireitado pela etapa de constraints, não pelo retarget.** O retarget reproduz a pose levantada exatamente (erro 0,00°), mas `constraints` então dobrava o cotovelo para o lado errado: em t=36 o cotovelo direito ia de **45° (vídeo) → 115°**, o esquerdo de **158° → 179° (reto)**. Causa: o preset limitava antebraço/canela por **eixo Euler da rotação *local***, e esse quaternion é `conj(q_pai) · q_mundo` — carrega também a rotação do ombro/quadril, não a flexão da junta. Medido neste clip, o Euler-Z do antebraço varia de **−175°…+172°**, então o clamp de "sem hiperextensão" disparava em **95/167 frames** aplicando slerp de até **118°** de uma rotação que era quase toda elevação de ombro.
  Novo tipo de limite **`bend`** mede o **ângulo real da junta pela geometria** (FK), onde 0° = reto, + = dobrado, − = hiperextensão, e corrige girando a subárvore no próprio eixo de dobradiça da junta. O preset agora usa `bend` nos dois cotovelos e joelhos; uma regra `bend` em osso que não é dobradiça é recusada com erro claro. Erro do cotovelo no frame relatado: **69,3° → 8,8°**.
* **O braço nascia dentro do torso porque o lifter não tinha solução de profundidade.** `_solve_depth` resolvia uma junta por vez; quando o segmento 2D era *maior* que o osso (~45% dos frames) não existe `sqrt(L²−d²)` real, e o código **escalava o filho na direção do pai em X/Y** — corrompendo a projeção da imagem em até **61 px** e achatando o braço — e emitia **z = 0**, exatamente coplanar com o tronco (**30% dos cotovelos com z=0**), então a anticolisão não tinha o que corrigir.
  O lifter agora trata a imagem como verdade: **X/Y vêm literalmente** (erro de reprojeção **61 px → 0,00 px**) e só o Z é desconhecido, resolvido por frame com Gauss-Newton sobre rigidez de comprimento de osso (suave), **não-penetração do tronco** (folga medida em Z contra uma elipse do tronco), continuidade temporal e um prior antropométrico fraco. A profundidade dos membros agora é diferente de zero em todos os frames.
* **O personagem flutuava 8 m do chão.** `savgol_coeffs` devolvia `pinv(a)[0] * window`; a linha da pseudo-inversa já soma 1, então o fator extra **escalava qualquer sinal pelo tamanho da janela** — um `root.y` constante de `0,98 m` virava **8,82 m** (`0,98 × 9`) em todo job que usa o `filters_default.yaml` entregue. Os coeficientes agora são normalizados para somar 1 (uma média ponderada tem que devolver um constante igual).

### Verificado

* No job relatado: reprojeção do braço sobre o vídeo **31,3 px → 25,8 px** na média (máx **121,9 px → 93,9 px**); cotovelo em t=36 direito **69,3° → 8,8°** de erro, esquerdo **21,1° → 9,2°**; penetração braco×tronco (cápsulas) média **−0,183 m → −0,167 m**, frames com sobreposição > 2 cm **3,4% → 2,4%**; `root.y` preservado em 0,98 m.
* Desempenho: o lifter foi de **12,1 s → 1,1 s** (constantes do frame extraídas do otimizador) e a nova etapa de dobradiças de **26,9 s → 0,3 s** (FK feita uma vez por frame, restrita aos ~14 ossos de que as juntas precisam).
* pytest: **151/151** (20 testes de regressão novos em `tests/test_regressions.py`, um por defeito: reprojeção pixel-exata, exatidão/não-toque do limite de dobradiça, não-penetração do torso, preservação de constante no SavGol, preservação do root e uma checagem fim a fim "o pipeline não pode endireitar o cotovelo").

### Nota

Jobs processados antes desta versão carregam os três defeitos e devem ser reprocessados. Os backends só-2D (vitpose, rtmpose) são os afetados; o mediapipe não foi afetado pelas mudanças do lifter porque o 3D dele vem do próprio backend.

## [1.4.10] — Hand tracking realmente anima + mãos no debug 2D

### Corrigido (relatado: "o detector de mão não parece estar fazendo nada, e também não mostra no debug")

* **As rotações de dedo nunca eram aplicadas (bug do índice do osso)**: o mapeamento landmark->osso montava o nome do osso com índice fixo `0` (`LeftHandIndex0` — osso que não existe), e toda rotação era descartada em silêncio: **0 rotações em todos os jobs** desde que o recurso existe (o log dizia a verdade). O índice (1 = MCP->PIP, 2 = PIP->DIP, 3 = DIP->TIP) agora está correto.
* **Mapeamento da pose da mão reescrito (matemática do esqueleto)**: o código antigo expressava as falanges observadas num "referencial da mão" montado pelos próprios landmarks — uma base sem relação com o rig — e ignorava a rotação da mão vinda do retarget do corpo; mesmo com o índice corrigido, os dedos saíam embaralhados. O novo mapeamento resolve cada junta dentro da cadeia do rig: `q1 = from_to(o2, rh^-1 . t1)`, `q2 = from_to(o3, (rh . q1)^-1 . t2)`, `q3 = from_to(o4, (rh . q1 . q2)^-1 . t3)`, com `oI` = offsets de rest do osso filho e `rh` = rotação mundo da mão (retarget) no frame. Um teste unitário reconstrói as direções observadas a partir dos quaternions de saída (concordância > 0,9999).
* **Mãos agora aparecem no overlay de debug 2D**: os landmarks das mãos são persistidos por job (`hands.json`, 21 pontos por mão em pixels do vídeo) e os dois overlays de debug (visualizador principal e editor de refino) os desenham sobre o vídeo — mão esquerda em azul, direita em terracota — junto com os keypoints do corpo.

### Verificado

* Reprodução com o clipe real: antes = 0 rotações aplicadas; depois = **2670 rotações**, mãos em **151/167 frames** (job `c13a77f7b4df`).
* Render close-up do mesh: dedos em C envolvendo o punho da espada, polegar do lado certo, sem inversão; um controle em rest pose na mesma câmera mostra que o resto da "blockiness" é o próprio mesh (low-poly), não a animação.
* pytest: **131/131**.

## [1.4.9] — Rig estavel: twist do antebraco eliminado + editor de refino em tempo real

### Corrigido (relatado: "o LeftForeArm gira ~180° entre os frames 37-43 mesmo com o LeftArm parado"; "os sliders do refine edit pararam de funcionar em tempo real")

* **Twist parasita do antebraco (retarget)**: as orientacoes dos ossos eram remontadas a cada frame como a rotacao minima a partir do repouso (`quat_from_to(d0, d)`); quando um membro varria um arco grande (antebraco cruzando o peito), a remontagem acumulava ate ~180° de twist em torno do proprio eixo com o braco parado. Agora as orientacoes usam **transporte paralelo** (gira a orientacao do frame anterior pela rotacao minima entre as direcoes consecutivas). Taxa de twist no clipe relatado: **−20…−50 °/frame → 0,0 °/frame** na base; clipe final por constraints→collision→filters: **+156° → +3,5°** acumulados (max 9°/frame em um unico frame).
* **Extracao de euler nao era a inversa da construcao**: `quat_to_euler_xyz_deg` devolvia angulos negados (rotacao de +60° lia −60°) e o round-trip errava ate ~175°. O clamp de constraints usava as duas funcoes em sequencia e, perto de gimbal, um limite de 1,2° no eixo z produzia um salto de **104°** na rotacao — o "giro impossivel" visivel. A extracao agora e a inversa exata (erro de round-trip 0,00°) e os clamps agem suavemente.
* **Editor de refino: sliders defasados (corrida de requisicoes)**: cada scrub disparava `/frame/{t}`; respostas atrasadas de frames antigos sobrescreviam o painel de euler (reproduzido: troca de osso+frame mostrava valores do frame 0 no frame 42). Uma guarda de sequencia descarta respostas fora de ordem — o ultimo scrub prevalece.
* **Editor de refino: preview ao vivo**: arrastar rx/ry/rz gira o osso selecionado na hora nas vistas 3D (mesma ordem de euler do servidor); "Aplicar" persiste como antes.
* **Rotulos dos ossos**: o prefixo `mixamorig` agora e cortado como aparece em runtime (`mixamorigHips` — o three.js remove os dois-pontos dos nomes de node).
* **Corpo pendendo para o lado (backends com lifting)**: o solve da coluna no lifter analitico mirava o **ombro esquerdo** como referencia de direcao do centro do peito — deslocando o peito ~0,18-0,21 m para a esquerda e inclinando o tronco inteiro **~24°** em todos os frames nos backends so-2D (vitpose/rtmpose; o mediapipe nao era afetado pois o 3D vem do proprio backend). O solve agora mira o **ponto medio dos ombros** (como o comentario do codigo sempre indicou); lean medido: **+24,3° → 0,0°** (vitpose), **+22,3° → +0,8°** (rtmpose). Bug antigo: todo job ja processado com lifting tem o lean e deve ser reprocessado.

### Verificado

* pytest **125/125**; validacao FK inalterada (12,63 cm / 0,01°).
* Editor reproduzido num Chromium headless real: sincronizacao correta dos sliders em scrutacao rapida, preview ao vivo atualiza as duas vistas, zero erros de console.

## [1.4.8] — Cabeça estável: orientação pelos olhos + profundidade sem singularidade

### Corrigido (relatado: "a cabeça do esqueleto se move mesmo com a cabeça parada no vídeo")

* **Profundidade do nariz**: o `solve("neck")` usava distância rígida com o termo
  `sqrt(L²−d²)`; quando a distância 2D ombro→nariz caía abaixo de 0,28 m, o nariz
  "mergulhava" até ~0,19 m em UM frame — a cabeça ganhava yaw/pitch de ~30° sozinha.
  Agora o nariz fica no plano do tórax (z do centro dos ombros).
* **Orientação da cabeça pela linha dos olhos**: o vetor `nariz − ombros` mede posição,
  não orientação — com o corpo se deslocando no quadro ele gira (medido: 40° de roll no
  vídeo A com a cabeça parada; 28,9° no B). A direção da cabeça agora vem da **linha dos
  olhos**, com suavização adaptativa (EMA com peso reduzido quando os olhos ficam curtos)
  e guardas para frames degenerados (oclusão/perfil).
* **Neutralização no MUNDO pelo frame 0**: a calibração anterior zerava só as rotações
  locais; a contribuição da coluna sobrevivia como viés constante (24° de lado, medido).
  Agora `calibrate_head` neutraliza Neck/Head também no mundo no frame de referência.

### Medido (A/B, 60 frames)

* A: tilt visível **40° → 0,71°** de amplitude (a linha dos olhos do vídeo mede 0,88°).
* B: **40° → 10,4°** (concentrados no giro rápido; média 1,5°, std 2,2°); início em 0,00°.

### Técnico

* O teste de skinning do usuário passava **por causa do bug** (a cabeça balançava e o
  cabelo é ~metade dos vértices); agora mede a fração de vértices de **membros** que se
  movem — nova métrica `moved_fraction_limbs` no `tools/verify_glb_skinning.py`.
* 107 testes passando.


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
  editor de malha (FBX/GLB) e preview 3D com vídeo de referência. Ver `README.pt-BR.md`.
