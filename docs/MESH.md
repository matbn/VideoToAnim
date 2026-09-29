# Usar uma malha (personagem) Mixamo no output

Por padrão o GLB exportado usa uma malha de **“capsule sticks”** (um cilindro fino por osso) só
para visualizar o movimento. Você pode enviar um personagem seu — **FBX ou glTF/GLB** — e a
animação será aplicada **na malha dele**.

Não é preciso converter: o Mixamo exporta FBX por padrão e o FBX é lido **direto**, sem Blender
nem FBX SDK.

## Como usar

1. Na interface, campo **“Malha Mixamo (opcional)”**, envie o arquivo (`.fbx` ou `.glb`).
2. O job roda normalmente; no painel de Job aparece um bloco **“Esqueleto da malha”** com o veredito.

## O que o software verifica

O esqueleto do arquivo é comparado com o **contrato Mixamo de 65 ossos** (`Hips, Spine, Spine1,
Spine2, Neck, Head, HeadTop_End, LeftShoulder, LeftArm, …, LeftHandPinky4, LeftUpLeg, …, RightToe_End`),
aceitando o prefixo `mixamorig:` com ou sem dois-pontos.

| campo do relatório | significado |
|---|---|
| `matched` | quantos ossos do seu arquivo casam com o contrato (o ideal é 65) |
| `missing` | ossos exigidos que faltam |
| `extra` | ossos fora do padrão (ignorados — ex.: acessórios) |
| `compatible` | o esqueleto serve para receber a animação |
| `attachable` | a malha foi **de fato** usada no GLB de saída |
| `stats` | sub-malhas, vértices e quantos ficaram **sem peso** |

Só end-caps ausentes (ex.: `LeftToe_End`) são tolerados; a falta de um osso **animado** reprova a
malha. Quando reprova, a interface mostra o motivo e o pipeline **não quebra**: volta para os
capsule sticks e informa.

## Formatos

| formato | compatibilidade | anexo ao output |
|---|---|---|
| **FBX binário** (padrão do Mixamo) | sim | **sim** — leitor próprio em Python (`core/fbx.py`) |
| **FBX ASCII** (outra opção do Mixamo) | sim | **sim** — mesmo leitor, mesma árvore de nós |
| **GLB / glTF** | sim | **sim** — re-skin via `pygltflib` |

## Diagnóstico antes de processar

O campo de malha tem o botão **“Verificar malha”**, que chama `POST /api/mesh/inspect` e responde
**sem processar o vídeo**: formato detectado, ossos casados/faltando, se é compatível e se o anexo
funciona (vértices e triângulos lidos). É o caminho mais rápido para entender por que uma malha não
entrou, sem esperar um job inteiro.

Erros comuns já tratados com mensagem clara:

| sintoma | mensagem |
|---|---|
| FBX exportado **sem skin** (opção “Without Skin” do Mixamo) | *“o FBX nao tem skin … baixe com 'Skin: With Skin'”* |
| esqueleto incompleto/renomeado | lista os ossos ausentes |
| arquivo não reconhecido | informa que não é FBX/GLB |

## Como funciona o anexo

Em ambos os casos as juntas são remapeadas **pelo nome** para o nosso rig, os pesos são
normalizados por vértice (top-4 influências) e as **inverseBindMatrices são recalculadas** a partir
do nosso rest pose. Por isso a malha fica correta na T-pose mesmo que as proporções do seu
personagem sejam diferentes das nossas.

### FBX binário (leitor próprio)

`core/fbx.py` parseia a árvore de nós do FBX 7.x (offsets de 32/64 bits conforme a versão, arrays
com deflate, registro nulo como terminador) e `load_fbx_mesh` extrai:

- `Objects → Geometry`: `Vertices` (control points) e `PolygonVertexIndex` (triangulado por leque);
- `Objects → Deformer`: `Skin` e `Cluster` (`Indexes` + `Weights`);
- `Connections`: o `Skin` aponta para a `Geometry`; cada `Cluster` aponta para o `Skin` e o osso
  (`Model`) é ligado ao cluster;
- `GlobalSettings.UnitScaleFactor`: converte a unidade do FBX (cm) para metros.

Um `Cluster` **sem** `Indexes`/`Weights` é tratado como **osso que não influencia o mesh** e é
ignorado. Isso importa: o Mixamo emite um cluster para **todo** osso do esqueleto, e os que não
pesam em nada vêm vazios. Tratá-los como “influencia todos” (a leitura literal da doc do FBX) fazia
os 12 clusters vazios do corpo ganharem peso 1.0 em **todos** os 6.658 vértices e dominarem a
seleção top-4 — o corpo ficava preso a **pontas de dedo** em vez de Hips/Spine/pernas, e a malha
não se movia como deveria. Vértices sem nenhum peso caem no `Hips` e são contados em
`stats.unweighted`; clusters vazios ignorados aparecem em `stats.empty_clusters`.

## Verificar que a malha realmente deforma

Um GLB pode estar estruturalmente correto e mesmo assim não se mover. Para medir de verdade, o
projeto traz `tools/verify_glb_skinning.py`, que aplica a fórmula de skinning do glTF 2.0 em dois
instantes (t=0 e t=duração/2) e informa o deslocamento dos vértices:

```powershell
.\\.venv\\Scripts\\python.exe tools\\verify_glb_skinning.py storage\\jobs\\<id>\\model.glb
```

Ele mostra juntas em uso, distribuição de influências, normalização de pesos e o deslocamento
(`>>> RESULTADO: a malha DEFORMA corretamente`). No seu `MixamoChar.fbx`: **53 juntas em uso**,
pesos normalizados, 0 sem peso, e ~89% dos vértices amostrados se deslocam entre os dois instantes.

### GLB / glTF

Lê `skins[0].joints`, casa pelo nome e reaproveita `POSITION`, `JOINTS_0`, `WEIGHTS_0` e `indices`.

## Testado com o seu arquivo

`MixamoChar.fbx` (5,4 MB, FBX 7700): **65/65 ossos casados**, compatível, anexada — 6 sub-malhas,
9.285 vértices, **0 sem peso**, altura 1,80 m, envergadura 1,96 m. O GLB final saiu com esses
9.285 vértices (contra 624 dos capsule sticks).

## Limitações

- A malha precisa ter **skinning** — um arquivo sem skin é recusado com mensagem clara.
- Vértices com peso em uma junta que não existe no nosso rig são recusados (aviso), em vez de
  deformar silenciosamente.
- Sem suporte a blend shapes / morph targets e sem materiais/texturas no preview.
- FBX **ASCII** é lido normalmente. O caso que ainda não funciona é **sem skin** (exportar do
  Mixamo sem marcar “With Skin”) — aí não há como ancorar a animação e o software avisa.
