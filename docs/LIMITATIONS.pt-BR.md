# Limitações conhecidas

## English

This document is also available in English: [LIMITATIONS.md](LIMITATIONS.md).

## Retarget / animação

1. **Sem root motion absoluto.** O esqueleto canônico é ancorado no plano da
   imagem; o deslocamento do personagem pela cena não é recuperado (o quadril
   fica na altura pélvica com o movimento residual). Para locomoção, é preciso
   um estimador de câmera/3D global (GLAMR/GVHMR/TRAM — fora do escopo).
2. **Dedos em identidade.** O COCO-17 não tem juntas de mão; os 40 ossos de
   dedo ficam em T-pose. Pose de mão exigiria um backend de hand pose.
3. **Twist não determinado por um vetor.** O retarget usa arco mínimo
   (swing). Regiões onde o roll/twist importa (pronação do antebraço) podem
   aparecer levemente ambíguas. A validação de FK mede erro **direcional**
   (< 5°); o erro **posicional** residual (~7 cm) vem da diferença de
   proporções entre a fonte e os comprimentos de osso fixos do rig Mixamo.
4. **Uma pessoa por vídeo.** Vídeos multi-pessoa usam a pessoa de maior
   confiança (sem UI de seleção manual).

## Backends

5. **ViTPose** exige instalação própria (torch + MMPose/mmcv). Nesta build ele é
   **registrado e reporta disponibilidade honestamente**, mas a inferência
   depende do setup documentado em `docs/BACKENDS.pt-BR.md`.
6. **MediaPipe** roda em CPU — e não é escolha nossa: o wheel de desktop do pip
   é compilado sem suporte a GPU (`GPU processing is disabled in build flags`,
   verificado no mediapipe 1.0.1 desta máquina); o delegate de GPU só existe nas
   builds móveis (Android/iOS). Em CPU a inferência é rápida (~13 ms/frame para
   mãos) e continua sendo o backend livre mais barato de habilitar
   (`pip install mediapipe`).
7. **Licenças:** nada é escondido — cada backend traz sua **categoria** no dropdown
   (`livre` / `nao_comercial` / `licenca_a_parte`). Para um produto comercial, use apenas os `livre`
   (`vitpose`, `mediapipe`, `rtmpose`, `motionbert`). `openpose` e `simplebaseline` são não comerciais;
   `wham` depende do SMPL (non-commercial); `yolopose` é AGPL-3.0; `sam3dbody` tem licença própria.
   Matriz completa: `docs/LICENSING.pt-BR.md`.
8. **Pesos de terceiros** sempre merecem verificação de licença, mesmo quando o
   código é permissivo.

## Exportação

9. **FBX ASCII sem malha.** O FBX gerado contém esqueleto + animação (rig
   importável em Blender/Unity/Unreal). Não inclui skinning/malha nem
   materiais. Para FBX com malha, o caminho é Blender headless (`blender -b -P`),
   que não é dependência do projeto.
10. **FBX ASCII** foi validado estruturalmente (65 `LimbNode`, conexões,
    curvas). A validação de importação em Blender/Unity é manual.
11. **Malha de preview**: por padrão é de "capsule sticks" procedural; se você enviar um
    personagem **FBX** (o padrão do Mixamo) ou `.glb` com esqueleto Mixamo, a animação é aplicada
    na malha dele, sem conversão (ver `docs/MESH.pt-BR.md`). FBX ASCII não é lido.

## Infra

12. **Fila serializada.** Um worker (para não disputar GPU). Jobs longos ficam
    em fila.
13. **Sem autenticação.** É uma ferramenta local; não exponha à internet.
14. **Preview 3D** carrega three.js via CDN (precisa de internet na primeira
    carga).
15. **Vídeo de referência**: o visualizador toca o vídeo enviado ao lado do esqueleto,
    sincronizados pela mesma timeline. O vídeo é servido pelo próprio servidor
    (`/api/jobs/{id}/artifacts/video`, com Range) e fica em disco enquanto o job existir.
16. **Lifter analítico** é heurístico (priors antropométricos + continuidade de
    profundidade). Para 3D mais fiel, troque-o por MotionBERT (interface
    `Lifter` já preparada) ou use um backend que já entregue 3D.
