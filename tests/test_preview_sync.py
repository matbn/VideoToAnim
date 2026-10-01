"""Enquadramento da previa 3D e sincronismo com o video de referencia.

Duas garantias de que a comparacao "video x esqueleto x malha" faz sentido:

1. O GLB tem UM keyframe por frame processado e o ultimo fica em (T-1)/fps,
   enquanto `video_span_s` (T/fps) e o que o backend publica. O preview precisa
   dos dois: usar o span comprimia a animacao em (T-1)/T (~0,6%) e o
   desalinhamento crescia de 0 ate ~1 frame no fim do clipe.

2. A preset de camera "video" reenquadra o 3D com a MESMA geometria que o
   lifter usou para liftar a pose, de modo que as fracoes de quadro do 3D batem
   com as do video. A camera fixa (olhar em 0.95 m, ~3,6 m) introduz
   deslocamento/escala aparente e mascara o erro real de pose.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from core import lifter as lifter_mod

PELVIS_Y = lifter_mod._PELVIS_HEIGHT      # 0.98 m
SPINE_M = lifter_mod._BONE["spine"]       # 0.52 m, mid-hip -> mid-shoulder
FOV_DEG = 38.0                            # o fov que o Viewer usa no three.js


# ----------------------------------------------------------------- camera
def camera_for_video(hip_px, sho_px, width, height, fov_deg=FOV_DEG, aspect=1.0):
    """Replica exata de `cameraForVideo` (web/app.js e web/refine.js)."""
    torso_px = math.hypot(sho_px[0] - hip_px[0], sho_px[1] - hip_px[1])
    half = math.tan(math.radians(fov_deg) / 2.0)
    fv = torso_px / height
    d = SPINE_M / (2.0 * fv * half)
    cam_y = PELVIS_Y - d * half * (1.0 - 2.0 * hip_px[1] / height)
    s = SPINE_M / torso_px
    x_world = (hip_px[0] - width / 2.0) * s
    cam_x = x_world - (2.0 * (hip_px[0] / width) - 1.0) * d * half * aspect
    return {"x": cam_x, "y": cam_y, "z": d}


def project(x, y, cam, height, aspect=1.0, fov_deg=FOV_DEG):
    """Ponto de mundo -> fracao de quadro (0..1) de cima para baixo."""
    half = math.tan(math.radians(fov_deg) / 2.0)
    ndc_y = (y - cam["y"]) / (cam["z"] * half)
    ndc_x = (x - cam["x"]) / (cam["z"] * half * aspect)
    return (ndc_x + 1.0) / 2.0, (1.0 - ndc_y) / 2.0


def lift_frame(kp, width, height):
    """Reproduz o mapeamento do lifter (core/lifter.py::_frame_plane)."""
    cx, cy = width / 2.0, height / 2.0
    ms = (np.asarray(kp[5][:2]) + np.asarray(kp[6][:2])) / 2.0
    mh = (np.asarray(kp[11][:2]) + np.asarray(kp[12][:2])) / 2.0
    torso_px = float(np.linalg.norm(ms - mh))
    s = (SPINE_M / torso_px) if torso_px > 5.0 else 0.0
    shift = PELVIS_Y - (cy - mh[1]) * s

    def to_world(p):
        return ((p[0] - cx) * s, (cy - p[1]) * s + shift)

    return to_world(mh), to_world(ms)


def _frame(width=960, height=540, hip=(479.6, 257.3), sho=(479.6, 146.9)):
    """kp2d sintetico: 17 juntas, so ombros e quadris importam para a camera."""
    kp = [None] * 17
    kp[5], kp[6] = [sho[0] - 20, sho[1], 0.9], [sho[0] + 20, sho[1], 0.9]
    kp[11], kp[12] = [hip[0] - 18, hip[1], 0.9], [hip[0] + 18, hip[1], 0.9]
    return kp, width, height, hip, sho


def test_camera_reproduz_o_quadro_do_video():
    """Quadril e ombros projetados pela camera nova caem nos pixels do video."""
    kp, w, h, hip_px, sho_px = _frame()
    hip_w, sho_w = lift_frame(kp, w, h)
    cam = camera_for_video(hip_px, sho_px, w, h)
    fx, fy = project(hip_w[0], hip_w[1], cam, h)
    assert fy == pytest.approx(hip_px[1] / h, abs=1e-9)
    assert fx == pytest.approx(hip_px[0] / w, abs=1e-9)
    _, fy = project(sho_w[0], sho_w[1], cam, h)
    assert fy == pytest.approx(sho_px[1] / h, abs=1e-9)


def test_camera_preserva_a_escala_do_tronco():
    """O tronco ocupa no 3D a mesma fracao de altura que ocupa no video."""
    kp, w, h, hip_px, sho_px = _frame()
    hip_w, sho_w = lift_frame(kp, w, h)
    cam = camera_for_video(hip_px, sho_px, w, h)
    fy_hip = project(hip_w[0], hip_w[1], cam, h)[1]
    fy_sho = project(sho_w[0], sho_w[1], cam, h)[1]
    trunk_px = math.hypot(sho_px[0] - hip_px[0], sho_px[1] - hip_px[1])
    assert abs(fy_hip - fy_sho) == pytest.approx(trunk_px / h, abs=1e-9)


@pytest.mark.parametrize("aspect", [0.75, 1.0, 1.6])
@pytest.mark.parametrize("hip,sho", [
    ((300.0, 300.0), (300.0, 190.0)),        # personagem a esquerda do centro
    ((660.0, 200.0), (660.0, 110.0)),        # a direita e no alto do quadro
    ((480.0, 400.0), (520.0, 300.0)),        # fora do centro e com tronco inclinado
])
def test_camera_ignora_posicao_do_personagem_e_aspecto_do_painel(hip, sho, aspect):
    """Fora do centro e painel largo/alto: as fracoes de quadro ainda batem."""
    w, h = 960, 540
    kp, _, _, hip_px, sho_px = _frame(hip=hip, sho=sho)
    hip_w, sho_w = lift_frame(kp, w, h)
    cam = camera_for_video(hip_px, sho_px, w, h, aspect=aspect)
    fx, fy = project(hip_w[0], hip_w[1], cam, h, aspect)
    assert fy == pytest.approx(hip[1] / h, abs=1e-9)
    assert fx == pytest.approx(hip[0] / w, abs=1e-9)
    assert project(sho_w[0], sho_w[1], cam, h, aspect)[1] == pytest.approx(sho[1] / h, abs=1e-9)


def test_lifter_e_a_camera_ficam_de_acordo_na_escala():
    """A escala da camera e a mesma que o lifter aplicou; nada degenera."""
    kp, w, h, hip_px, sho_px = _frame()
    hip_w, _ = lift_frame(kp, w, h)
    cam = camera_for_video(hip_px, sho_px, w, h)
    assert hip_w[1] == pytest.approx(PELVIS_Y, abs=1e-9)   # quadril na altura de referencia
    assert 0.5 < cam["z"] < 50.0                           # distancia plausivel


# ---------------------------------------------------------------- timebase
def test_glb_termina_no_ultimo_keyframe(tmp_path, synthetic_poses):
    """O ultimo keyframe fica em (T-1)/fps, um frame ANTES de video_span_s."""
    pygltflib = pytest.importorskip("pygltflib")
    from core.export_glb import build_glb
    from core.retarget import Retargeter

    anim = Retargeter(fps=30.0).retarget(synthetic_poses)
    out = tmp_path / "model.glb"
    build_glb(anim, out)

    g = pygltflib.GLTF2().load(str(out))
    acc = g.accessors[g.animations[0].samplers[0].input]
    view = g.bufferViews[acc.bufferView]
    off = (view.byteOffset or 0) + (acc.byteOffset or 0)
    times = np.frombuffer(g.binary_blob(), dtype=np.float32, count=acc.count, offset=off)

    T, fps = acc.count, anim.fps
    assert times[0] == pytest.approx(0.0, abs=1e-6)
    assert times[-1] == pytest.approx((T - 1) / fps, abs=1e-5)
    assert (T - 1) / fps < T / fps                      # o span publicado e maior
    assert (T / fps) - ((T - 1) / fps) == pytest.approx(1.0 / fps, abs=1e-9)


# ------------------------------------------------------- wrap do preview
APP_JS = Path(__file__).resolve().parent.parent / "web" / "app.js"


def test_wrap_usa_o_relogio_do_video_e_nao_o_mediaTime():
    """Guarda de regressao do bug relatado (preview travado/ralando).

    O wrap NAO pode ser decidido pelo `mediaTime` do requestVideoFrameCallback.
    Um seek descarta o pipeline do navegador: o frame so e apresentado (e o
    rVFC so dispara) se nao houver outro seek antes. Seek em cima de seek
    congela o mediaTime no ultimo frame, o wrap volta a disparar, e o
    preview ficaSeekando sem sair do lugar com a pose presa no inicio/fim.

    Medido no navegador (Edge headless, job e63f073c2149, clipe de 5,53 s):
      logica antiga -> video parado em 5,566 s, pose presa em "5.53 / 5.53 s",
                       ZERO frames apresentados, 0 loops;
      logica nova    -> 30 frames/s, 1 busca por ciclo de 5,53 s, rotulo
                       acompanhando ("3.28 / 5.53" ... "5.30 / 5.53" -> "0.25").

    Um simulador em Python dessa semantica nao reproduz o problema, entao a
    prova fica no navegador e o teste aqui trava a FORMA do codigo.
    """
    src = APP_JS.read_text(encoding="utf-8")
    i = src.index("function tick()")
    corpo = src[i:src.index("\n}", i)]
    assert "videoEl.currentTime >= lastFrameT" in corpo, (
        "o wrap tem de ser decidido pelo relogio do video (currentTime)")
    # o mediaTime nao pode participar da CONDIÇÃO de wrap
    cond = corpo[corpo.index("if ("):corpo.index(")", corpo.index("if (")) + 1]
    assert "mediaTime" not in cond, f"condicao de wrap nao pode usar mediaTime: {cond}"
    # e o instante antigo tem de ser descartado no wrap
    wrap = corpo[corpo.index("videoEl.currentTime = 0"):]
    assert "mediaTime = null" in wrap, "o wrap precisa descartar o mediaTime antigo"


def test_cadeia_de_requestVideoFrameCallback_e_unica():
    """Cada 'play' criava outra cadeia, que nunca morria."""
    src = APP_JS.read_text(encoding="utf-8")
    assert "let rvfcRunning = false;" in src
    i = src.index("function pumpVideoFrame()")
    corpo = src[i:src.index("\n}", i)]
    assert "if (!videoEl || rvfcRunning) return;" in corpo, corpo


# ------------------------------------------- refine editor: frame -> segundo
def anim_to_video_seconds(frame, video_fps, duration):
    """Replica o calculo do alvo de busca em `syncSliders` (web/refine.js).

    O frame `frame` da animacao corresponde ao frame `frame` do video, e o
    frame `frame` do video foi apresentado em `frame / video_fps` SEGUNDOS.
    """
    return min(frame / video_fps, max(duration - 0.001, 0))


def test_refine_mapeia_frame_para_segundo_do_video():
    """Regressao: a formula mandava o video para o segundo `frame` (30x)."""
    T, VFPS, DUR = 167, 30.0, 5.566
    for frame, esperado in ((0, 0.0), (1, 1 / 30), (36, 1.2), (90, 3.0), (166, 5.5333)):
        got = anim_to_video_seconds(frame, VFPS, DUR)
        assert got == pytest.approx(esperado, abs=1e-4), (frame, got, esperado)
    # o ultimo frame tem de cair dentro do video, e nao colado no fim
    assert anim_to_video_seconds(T - 1, VFPS, DUR) < DUR - 0.01


def test_refine_nao_usa_a_inversa_errada():
    """`frame * videoFps / targetFps` e a inversa (so para o app.js)."""
    src = (Path(__file__).resolve().parent.parent / "web" / "refine.js").read_text(encoding="utf-8")
    i = src.index("async function syncSliders()")
    corpo = src[i:src.index("\n}", i)]
    assert "const alvo = Math.min(t / vFps" in corpo, corpo
    assert "(t * vFps) / clip.fps" not in corpo, "alvo do video nao e o instante da animacao"
    # o guarda de re-busca tambem tem de estar em segundos de video
    assert "0.5 / vFps" in corpo, corpo


# ------------------------------- editor de refino: janela [start, end]
def sync_range_to_frame(t, start, end, last, width):
    """Replica `syncRangeToFrame` (web/refine.js).

    Preserva a largura escolhida e recentra a janela no frame alvo, para ela
    sempre CONTER o alvo: o servidor rejeita a edicao caso contrario.
    """
    w = end - start
    if w > 0:
        width = w
    if start <= t <= end:
        return start, end, width
    ns = int(round(t - width / 2.0))
    ns = max(0, min(ns, last - width))
    return ns, min(last, ns + width), width


def test_janela_do_editor_sempre_contem_o_frame_alvo():
    """O padrao [0,10] rejeitava qualquer edicao fora do frame 10."""
    last, t = 166, 36
    s, e, w = sync_range_to_frame(t, 0, 10, last, 40)
    assert s <= t <= e, (s, e, t)
    assert e - s == 10, "a largura escolhida (10) foi preservada"


def test_janela_preserva_a_largura_escolhida():
    last = 166
    for width in (4, 10, 20, 40, 90):
        s, e, _ = sync_range_to_frame(80, 0, width, last, 40)
        assert e - s == width, (width, s, e)
        assert s <= 80 <= e, (width, s, e)


def test_janela_preserva_escolha_explicita_que_ja_contem_o_alvo():
    """Se o usuario montou a janela de proposito, ela nao e mexida."""
    s, e, w = sync_range_to_frame(36, 20, 60, 166, 40)
    assert (s, e) == (20, 60), (s, e)
    assert w == 40


def test_janela_nunca_sai_do_clipe():
    last = 166
    for t in (0, 1, 5, 83, 164, 165, 166):
        s, e, _ = sync_range_to_frame(t, 0, 10, last, 40)
        assert 0 <= s <= e <= last, (t, s, e)
        assert s <= t <= e, (t, s, e)
