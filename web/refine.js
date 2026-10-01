// VideoToAnim — editor de refinamento (bone editor + filtros + constraints).
// Modo Redesign/Refinement: mesma linguagem visual do pipeline, página nova.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { t, applyStatic, langSwitcher, getLang } from './i18n.js?v=5';

const $ = (id) => document.getElementById(id);
const CAMERAS = { persp: [2.2, 1.6, 2.6], front: [0, 1.1, 3.2], side: [3.4, 1.1, 0] };
const PELVIS_Y = 0.98;   // altura pelvica de referencia (core/lifter.py)
const SPINE_M = 0.52;    // comprimento da coluna usado pelo lifter

// Enquadramento do 3D igual ao do video de referencia. Mesma geometria que o
// lifter usou para liftar a pose, entao as fracoes de quadro batem exatamente.
// Com a camera em (camX, camY, d) olhando para -Z, um ponto de mundo Y cai na
// fracao de altura  f = 1/2 - (Y - camY) / (2 d tan(fov/2)):
//   d    = SPINE_M / (2 * (tronco_px / altura_video) * tan(fov/2))
//   camY = PELVIS_Y - d tan(fov/2) * (1 - 2 py_quadril / altura_video)
// As fracoes batem exatamente (provado em tests/test_preview_sync.py).
function videoCalibration() {
  if (!kpData || !kpData.frames || !kpData.frames[0] || !kpData.width || !kpData.height) return null;
  const f = kpData.frames[0];
  if (!f[5] || !f[6] || !f[11] || !f[12]) return null;      // ombros/quadris
  const mid = (a, b) => [(f[a][0] + f[b][0]) / 2, (f[a][1] + f[b][1]) / 2];
  const hip = mid(11, 12);
  const sho = mid(5, 6);
  const torsoPx = Math.hypot(sho[0] - hip[0], sho[1] - hip[1]);
  if (!(torsoPx > 1)) return null;
  return { hipPx: hip[0], hipPy: hip[1], torsoPx, videoW: kpData.width, videoH: kpData.height };
}

function cameraForVideo(camera) {
  const cal = videoCalibration();
  if (!cal) return null;
  const half = Math.tan((camera.fov * Math.PI / 180) / 2);
  const fv = cal.torsoPx / cal.videoH;                    // tronco como fracao da altura
  const d = SPINE_M / (2 * Math.max(fv, 1e-3) * half);
  const camY = PELVIS_Y - d * half * (1 - 2 * cal.hipPy / cal.videoH);
  // eixo X: o personagem pode estar fora do centro do quadro
  const s = SPINE_M / Math.max(cal.torsoPx, 1e-3);         // m por px do video
  const xWorld = (cal.hipPx - cal.videoW / 2) * s;         // onde o lifter botou o quadril
  const camX = xWorld - (2 * (cal.hipPx / cal.videoW) - 1) * d * half * camera.aspect;
  return { x: camX, y: camY, z: d };
}

let jobId = null, clip = null, currentFrame = 0, playing = false, spanS = 0;
let syncSeq = 0;   // guarda contra respostas de sync fora de ordem
const viewers = [];
let no3d = false;
let activeCam = 'persp';
// A preset "video" so pode ser calculada depois que o kp2d chega; reaplica a
// camera ativa quando ele carrega (ou quando o painel muda de tamanho).
function reapplyCamera() { if (activeCam === 'video') viewers.forEach((v) => v.setCamera('video')); }

function log(msg) {
  const el = $('log');
  el.textContent = `${msg}\n` + el.textContent.split('\n').slice(0, 40).join('\n');
}

// ------------------------------------------------------------------ viewer
class Viewer {
  constructor(host, empty, skeletonOnly = false) {
    this.host = host; this.empty = empty; this.skeletonOnly = skeletonOnly;
    this.mixer = null; this.clipDuration = 0; this.model = null; this.ready = false;
    const wrap = $(host);
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    wrap.appendChild(this.renderer.domElement);
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(38, 1, 0.05, 100);
    this.camera.position.set(...CAMERAS.persp);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.target.set(0, 0.95, 0);
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0xbfb8ab, 2.1));
    const k = new THREE.DirectionalLight(0xffffff, 1.4); k.position.set(3, 6, 4); this.scene.add(k);
    this.scene.add(new THREE.GridHelper(6, 24, 0xcfc9bd, 0xe4e0d7));
    this.resize();
    // O painel 3D e flex (a linha da grade decide a altura), entao a caixa
    // pode mudar SEM evento de `resize` da janela: trocar de aba, abrir o
    // filtro de constraints, redimensionar a coluna. O three.js so mede
    // quando alguem chama `resize()`, e o canvas ficava esticado.
    if (typeof ResizeObserver !== 'undefined') {
      this._ro = new ResizeObserver(() => this.resize());
      this._ro.observe(wrap);
    }
    this.renderer.setAnimationLoop(() => this.tick());
  }
  resize() {
    const host = $(this.host);
    // enquanto oculto a caixa mede 0: `setSize(0, 0)` deixaria o renderer
    // com aspect invalido, e nao ha o que corrigir ainda.
    if (!host) return;
    const w = host.clientWidth, h = host.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }
  load(url) {
    // descarrega o clipe anterior JA — o antigo nao pode ficar na cena
    this.clear();
    const token = this._loadToken = (this._loadToken || 0) + 1;
    const empty = $(this.empty);
    if (empty) { empty.style.display = ''; empty.textContent = t('ref.loading'); }
    return new Promise((resolve) => {
      new GLTFLoader().load(url, (gltf) => {
        if (token !== this._loadToken) { resolve(this); return; }   // trocou de clipe
        this.model = gltf.scene;
        this.scene.add(this.model);
        this._criarLabels();
        if (this.skeletonOnly) {
          this.model.traverse((o) => { if (o.isMesh || o.isSkinnedMesh) o.visible = false; });
          const helper = new THREE.SkeletonHelper(this.model);
          helper.material.color = new THREE.Color(0x4f6d5c);
          helper.material.depthTest = false;
          this.scene.add(helper);
          this.helper = helper;                  // guardado: o clear() precisa remover
        }
        if (gltf.animations && gltf.animations.length) {
          this.mixer = new THREE.AnimationMixer(this.model);
          const a = this.mixer.clipAction(gltf.animations[0]);
          this.clipDuration = a.getClip().duration;
          a.paused = true;                       // scrub manual via setFrame
          a.play();
          this.action = a;
        }
        this.ready = true;
        if (empty) empty.style.display = 'none';
        resolve(this);
      }, undefined, (e) => {
        if (token === this._loadToken) {
          log(t('ref.glbLoadErr', { msg: (e && e.message ? e.message : e) }));
          if (empty) empty.textContent = t('ref.loadFail');
        }
        resolve(this);
      });
    });
  }
  _criarLabels() {
    this.labelSprites = [];
    this._bonesByName = {};
    const filtro = /(Index|Middle|Ring|Pinky|Thumb)\d$/;
    const tirarPrefixo = (n) => n.replace(/^(?:mixamorig|mixamo)[:_]?/i, '');
    this.model.traverse((o) => {
      if (!o.isBone) return;
      this._bonesByName[tirarPrefixo(o.name)] = o;
      if (!filtro.test(o.name)) {
        const spr = makeTextSprite(tirarPrefixo(o.name));
        spr.position.set(0, 0.025, 0);
        spr.visible = !!this._labelsOn;
        o.add(spr);
        this.labelSprites.push(spr);
      }
    });
  }
  getBone(nome) { return this._bonesByName ? this._bonesByName[nome] : undefined; }

  setLabels(v) {
    this._labelsOn = !!v;
    (this.labelSprites || []).forEach((s) => { s.visible = this._labelsOn; });
  }
  setFrame(t, fps) {
    // scrub de acao PAUSADA: setTime() trava no instante 0 (timeScale efetivo = 0
    // quando pausado — reproduzido com three.js 0.169). O caminho certo e
    // action.time + mixer.update(0).
    if (!this.mixer || !this.action) return;
    const dur = Math.max(this.clipDuration, 1e-6);
    this.action.paused = true;
    this.action.time = Math.min(Math.max(t, 0) / Math.max(fps, 1e-6), dur - 1e-4);
    this.mixer.update(0);
  }
  clear() {
    if (this.mixer) { this.mixer.stopAllAction(); this.mixer = null; }
    this.action = null;
    this.clipDuration = 0;
    if (this.helper) {
      this.scene.remove(this.helper);
      if (this.helper.dispose) this.helper.dispose();
      this.helper = null;
    }
    (this.labelSprites || []).forEach((s) => {
      if (s.material) { if (s.material.map) s.material.map.dispose(); s.material.dispose(); }
    });
    this.labelSprites = [];
    if (this.model) {
      this.scene.remove(this.model);
      this.model.traverse((o) => {
        if (o.geometry) o.geometry.dispose();
        if (o.material) (Array.isArray(o.material) ? o.material : [o.material]).forEach((m) => m.dispose());
      });
      this.model = null;
    }
    this.ready = false;
    const empty = $(this.empty);
    if (empty) { empty.style.display = ''; empty.textContent = t('ref.loadClip'); }
  }
  tick() { this.controls.update(); this.renderer.render(this.scene, this.camera); }
  setCamera(n) {
    const v = (n === 'video') ? cameraForVideo(this.camera) : null;
    if (n === 'video' && !v) {                 // sem kp2d: cai na perspectiva
      this.camera.position.set(...CAMERAS.persp);
      this.controls.target.set(0, 0.95, 0);
    } else if (v) {
      this.camera.position.set(v.x, v.y, v.z);
      this.controls.target.set(v.x, v.y, 0);
    } else {
      this.camera.position.set(...(CAMERAS[n] || CAMERAS.persp));
      this.controls.target.set(0, 0.95, 0);
    }
    this.controls.update();
  }
}

// ------------------------------------------------------------------ estado
async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let d = '';
    try { d = (await r.json()).detail || ''; } catch { d = await r.text(); }
    throw new Error(d || `HTTP ${r.status}`);
  }
  return r.json();
}

async function loadJobs() {
  const data = await api('/api/jobs');
  const sel = $('job');
  sel.innerHTML = '';
  const done = data.jobs.filter((j) => j.status === 'done');
  for (const j of done) {
    const o = document.createElement('option');
    o.value = j.id;
    o.textContent = `${j.id.slice(0, 8)} · ${j.backend} · ${j.video_name}`;
    sel.appendChild(o);
  }
  if (!done.length) {
    $('job-hint').textContent = t('ref.noJobs');
    return;
  }
  $('job-hint').textContent = t('ref.clipsAvailable', { n: done.length });
  // prioridade: ?job= da URL -> job aberto na aba principal (aba padrao) -> mais recente
  const want = new URLSearchParams(location.search).get('job');
  let preferido = null;
  try { preferido = localStorage.getItem('v2m:lastJob'); } catch (e) { /* sem storage */ }
  const alvo = (want && done.some((j) => j.id === want)) ? want
    : (preferido && done.some((j) => j.id === preferido)) ? preferido
    : done[0].id;
  sel.value = alvo;
  await selectJob(alvo);
}

async function selectJob(id) {
  jobId = id;
  try {
    clip = await api(`/api/refine/animation/${id}`);
  } catch (e) {
    clip = null;
    viewers.forEach((v) => v.clear());           // o clipe antigo sai da cena
    const vid = $('refvideo');
    if (vid) { vid.removeAttribute('src'); vid.load(); }
    const ov = $('video-empty');
    if (ov) ov.style.display = '';
    $('job-hint').textContent = t('ref.openFail', { msg: e.message });
    return;
  }
  $('info-frames').textContent = clip.num_frames;
  $('info-bones').textContent = clip.animated_bones.length;
  $('info-session').textContent = t('ref.editsCount', { n: clip.history.edits.length });
  $('job-hint').textContent = t('ref.clipLoaded', { fps: clip.fps });
  const d1 = $('dl-refined-glb'), d2 = $('dl-refined-fbx');
  d1.classList.add('disabled'); d1.href = '#';
  d2.classList.add('disabled'); d2.href = '#';
  try {
    const j = await api(`/api/jobs/${id}`);
    const arts = j.artifacts || {};
    if (arts.glb_refined) { d1.href = `/api/jobs/${id}/artifacts/glb_refined`; d1.classList.remove('disabled'); }
    if (arts.fbx_refined) { d2.href = `/api/jobs/${id}/artifacts/fbx_refined`; d2.classList.remove('disabled'); }
  } catch (e) { /* sem artefatos: links seguem desativados */ }
  const vid = $('refvideo');
  if (vid) {
    vid.src = `/api/jobs/${id}/artifacts/video`;
    vid.load();
    $('video-empty').style.display = 'none';
  }
  loadKp();
  const f = $('frame'), st = $('start'), en = $('end');
  f.max = clip.num_frames - 1; f.value = 0; st.max = clip.num_frames - 1; en.max = clip.num_frames - 1;
  // janela centrada no frame alvo, com rampa dos dois lados (ver syncRangeToFrame)
  rangeWidth = DEFAULT_RANGE_W;
  const w0 = Math.min(DEFAULT_RANGE_W, clip.num_frames - 1);
  st.value = 0; en.value = String(w0);
  const bs = $('bone');
  bs.innerHTML = '';
  for (const b of clip.animated_bones) {
    const o = document.createElement('option');
    o.value = b; o.textContent = b.replace('mixamorig:', '');
    bs.appendChild(o);
  }
  await refreshPreview();
  await syncSliders();
  refreshTransplantTarget();
  try { localStorage.setItem('v2m:lastJob', id); } catch (e) { /* sem storage */ }
  history.replaceState(null, '', `/refine?job=${id}`);
  log(t('ref.clipLoadedLog', { id, n: clip.num_frames, b: clip.animated_bones.length }));
}

// A janela [start, end] tem de CONTER o frame alvo. Duas consequencias do
// editor original nao fazer isso:
//
// 1. o padrao era [0, 10] e nunca acompanhava o frame — sair do frame 10
//    fazia o servidor rejeitar a edicao inteira ("o frame editado (36) precisa
//    estar dentro de [0, 10]"), sem nenhuma pista na interface de que o
//    problema era a janela;
// 2. mesmo quando dava certo, uma janela assimetrica espreme o rampa inteiro
//    de um lado so: o smoothstep do rebake e suave nas pontas, mas se o lado
//    curto tem de percorrer todo o angulo, o meio vira um chicote.
//
// Aqui a largura escolhida pelo usuario e preservada e a janela e recentrada no
// frame, o que da o mesmo espaço de rampa antes e depois do alvo.
// Medido no job 4ac4511714b6 (osso LeftArm, alvo no frame 36, -120 graus em Z):
// o osso se move a 1,34 graus/frame na mediana, e o pico do rebake depende da
// largura da janela -- 7,1x a taxa natural com 10 frames, 4,0x com 20, 2,2x com
// 40. Abaixo de ~40 o "smoothstep" fica suave so nas pontas e o meio vira um
// chicote. 40 frames = ~0,7 s de rampa para cada lado.
const DEFAULT_RANGE_W = 40;
let rangeWidth = DEFAULT_RANGE_W;

function syncRangeToFrame(t) {
  if (!clip) return;
  const last = clip.num_frames - 1;
  const st = Number($('start').value);
  const en = Number($('end').value);
  const w = en - st;
  if (w > 0) rangeWidth = w;         // largura vale enquanto couber o frame
  if (t >= st && t <= en) return;    // ja contem o alvo: nao mexe
  let ns = Math.round(t - rangeWidth / 2);
  ns = Math.max(0, Math.min(ns, last - rangeWidth));
  $('start').value = String(ns);
  $('end').value = String(Math.min(last, ns + rangeWidth));
}

// ------------------------------------------------------------------ abas
// Duas abas no editor: o refino de verdade (osso, filtros, constraints) e a
// mistura A/B. A barra de tempo e o log ficam FORA das abas, entao o playhead
// e o video de referencia servem as duas — na aba da mistura os paineis A/B
// acompanham o mesmo frame.
function switchRefineTab(tab) {
  for (const b of document.querySelectorAll('.refine-tabs .tab')) {
    b.classList.toggle('active', b.dataset.tab === tab);
  }
  const mr = $('pane-refine'), mm = $('pane-merge');
  if (mr) mr.hidden = tab !== 'refine';
  if (mm) mm.hidden = tab !== 'merge';
  // o 3D precisa de resize ao voltar a ser visivel (o canvas media 0 quando oculto)
  requestAnimationFrame(() => {
    viewers.forEach((v) => v.resize());
    [cmpA, cmpB].forEach((v) => { if (v) v.resize(); });
    reapplyCamera();
    if (cmpA) syncCompareCamera(viewers[0], cmpA);
    drawKpOverlay();
  });
}

// ---------------------------------------------- mistura de partes entre jobs
let trParts = [];      // [{id,label,bones}] vindas do servidor
let cmpA = null;       // painel da origem
let cmpB = null;       // painel do destino
let cmpSourceJob = ''; // job carregado em cmpA

// Os dois paineis A/B dividem a camera com o preview principal, para dar para
// comparar o mesmo membro girando a camera uma vez so.
function syncCompareCamera(origem, destino) {
  if (!origem || !destino) return;
  destino.camera.position.copy(origem.camera.position);
  destino.controls.target.copy(origem.controls.target);
  destino.controls.update();
}

function cmpFrames() {
  const t = Number($('frame').value) || 0;
  const fps = (clip && clip.fps) || 30;
  if (cmpA) cmpA.setFrame(t, fps);
  if (cmpB) cmpB.setFrame(t, fps);
}

async function loadCompareClips(force) {
  const src = $('tr-source') ? $('tr-source').value : '';
  const alvo = jobId;
  if (!cmpA || !cmpB || !src || !alvo) return;
  if (src !== cmpSourceJob || force) {
    cmpSourceJob = src;
    await cmpA.load(`/api/jobs/${src}/artifacts/glb`);
    $('tr-title-a').textContent = `${t('tr.fromA')} ${src.slice(0, 8)}`;
  }
  await cmpB.load(`/api/refine/animation/${alvo}/glb`);
  $('tr-title-b').textContent = `${t('tr.toB')} ${alvo.slice(0, 8)}`;
  cmpFrames();
  if (clip) syncRangeBounds();
}

function syncRangeBounds() {
  const last = (clip ? clip.num_frames - 1 : 0);
  for (const id of ['tr-t0', 'tr-t1']) { const e = $(id); if (e) e.max = String(last); }
  for (const id of ['tr-s0', 'tr-s1']) { const e = $(id); if (e) e.max = String(last); }
  const f = Number($('frame').value) || 0;
  if (!$('tr-usar-mapa').checked) {
    $('tr-s0').value = '0'; $('tr-s1').value = String(last);
    $('tr-t0').value = '0'; $('tr-t1').value = String(last);
  } else {
    $('tr-t0').value = String(f); $('tr-t1').value = String(f);
    $('tr-s0').value = String(f); $('tr-s1').value = String(f);
  }
}

function trFrameMap() {
  if (!$('tr-usar-mapa') || !$('tr-usar-mapa').checked) return null;
  const s0 = Number($('tr-s0').value), s1 = Number($('tr-s1').value);
  const t0 = Number($('tr-t0').value), t1 = Number($('tr-t1').value);
  if (![s0, s1, t0, t1].every(Number.isFinite)) return null;
  return { source: [s0, s1], target: [t0, t1] };
}

async function initTransplant() {
  const sel = $('tr-source');
  if (!sel) return;
  try {
    const jobs = await api('/api/jobs?status=done&limit=60');
    sel.innerHTML = '';
    for (const j of jobs.jobs || []) {
      const o = document.createElement('option');
      o.value = j.id;
      o.textContent = `${j.id} · ${j.backend} · ${j.video_name || ''}`;
      sel.appendChild(o);
    }
  } catch (e) { /* sem lista: o painel fica inerte */ }
  try {
    const p = await api('/api/refine/transplant/parts');
    trParts = p.parts || [];
    const order = new Map((p.order || []).map((id, i) => [id, i]));
    trParts.sort((a, b) => (order.get(a.id) ?? 99) - (order.get(b.id) ?? 99));
    const box = $('tr-parts');
    box.innerHTML = '';
    for (const part of trParts) {
      const lab = document.createElement('label');
      lab.className = 'inline-chk tr-part';
      lab.title = `${part.bones.length} ossos: ${part.bones.join(', ')}`;
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.value = part.id;
      const sp = document.createElement('span');
      sp.textContent = `${part.label} (${part.bones.length})`;
      lab.append(cb, sp);
      box.appendChild(lab);
    }
  } catch (e) { /* sem catalogo */ }
}

function trSelectedParts() {
  return Array.from($('tr-parts').querySelectorAll('input:checked')).map((c) => c.value);
}

async function applyTransplant() {
  if (!jobId) return;
  const source = $('tr-source').value;
  const parts = trSelectedParts();
  const out = $('tr-result');
  if (!source) { out.textContent = t('tr.noSource'); return; }
  if (!parts.length) { out.textContent = t('tr.noParts'); return; }
  const body = { source_job: source, parts };
  if ($('tr-root').checked) body.copy_root_translation = true;
  const mapa = trFrameMap();
  if (mapa) { body.source_range = mapa.source; body.target_range = mapa.target; }
  out.textContent = t('tr.working');
  try {
    const r = await api(`/api/refine/transplant/${jobId}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    const rep = r.report;
    out.textContent = t('tr.done', {
      n: rep.grafted_bones.length,
      src: source.slice(0, 8),
      mean: rep.vs_source.mean_deg.toFixed(3),
      max: rep.vs_source.max_deg.toFixed(3),
    });
    log(t('ref.transplantApplied', {
      parts: rep.parts.join(', '), src: source.slice(0, 8), n: rep.grafted_bones.length,
    }));
    await refreshPreview();
    await loadCompareClips(true);
  } catch (e) { out.textContent = `${t('err.upper')}: ${e.message}`; }
}

async function undoTransplant() {
  if (!jobId) return;
  const out = $('tr-result');
  try {
    const r = await api(`/api/refine/transplant/${jobId}/undo`, { method: 'POST' });
    out.textContent = t('tr.undone', { parts: r.undone.join(', ') });
    log(t('ref.transplantUndone', { parts: r.undone.join(', ') }));
    await refreshPreview();
    await loadCompareClips(true);
  } catch (e) { out.textContent = `${t('err.upper')}: ${e.message}`; }
}

function refreshTransplantTarget() {
  const el = $('tr-target');
  // texto puro (o destino nao e editavel) — por isso textContent, nao value
  if (el) el.textContent = jobId || '—';
  // carrega os dois paineis assim que ha um destino (e uma origem escolhida)
  loadCompareClips(true);
}

async function syncSliders() {
  if (!jobId || !clip) return;
  const t = Number($('frame').value);
  currentFrame = t;
  $('frame-label').textContent = t;
  syncRangeToFrame(t);
  // Video de referencia acompanha o frame (scrub e playback).
  // O frame t da animacao corresponde ao frame t do VIDEO, e o frame t do video
  // foi apresentado em t / vFps SEGUNDOS. A inversa de `animTimeFor` do app.js.
  // A guarda e de MEIO frame: abaixo disso nao re-busca, acima disso o video
  // ficaria ate meio frame atras do esqueleto.
  const vid = $('refvideo');
  if (vid && clip.fps > 0 && vid.readyState >= 2) {
    const vFps = (kpData && kpData.fps) || clip.fps;
    // frame t -> segundo t/vFps. (Um `t * vFps / clip.fps` aqui mandava o video
    // para o segundo t: num clipe de 5,57 s ele chegava ao fim no frame 6.)
    const alvo = Math.min(t / vFps, Math.max(vid.duration - 0.001, 0));
    if (Math.abs(vid.currentTime - alvo) > 0.5 / vFps) vid.currentTime = alvo;
  }
  // a pose na cena vem ANTES do fetch: o playback nao depende da rede
  viewers.forEach((v) => v.setFrame(t, clip.fps));
  drawKpOverlay();
  $('time').textContent = `${(t / clip.fps).toFixed(2)} / ${(clip.num_frames / clip.fps).toFixed(2)} s`;
  $('timeline').value = String(Math.round((t / Math.max(clip.num_frames - 1, 1)) * 1000));
  const seq = ++syncSeq;
  try {
    const sp = editSpace();
    cmpFrames();
  const pose = await api(`/api/refine/animation/${jobId}/frame/${t}?space=${sp}`);
    if (seq !== syncSeq) return;   // resposta antiga: outro sync ja foi disparado depois
    const e = pose.bones[$('bone').value]?.euler_deg || [0, 0, 0];
    $('rx').value = Math.round(e[0]); $('ry').value = Math.round(e[1]); $('rz').value = Math.round(e[2]);
    $('rx-label').textContent = `${Math.round(e[0])}°`;
    $('ry-label').textContent = `${Math.round(e[1])}°`;
    $('rz-label').textContent = `${Math.round(e[2])}°`;
  } catch (e) { /* painel de euler mantem o valor anterior */ }
}

async function refreshPreview() {
  if (!jobId) return;
  const url = `/api/refine/animation/${jobId}/glb?t=${Date.now()}`;
  await Promise.all(viewers.map((v) => v.load(url)));
  await syncSliders();                            // reaplica o frame no modelo novo
}


// --------------------------------------------- preview ao vivo das edicoes
function editSpace() {
  const v = $('space') ? $('space').value : 'local';
  return (v === 'global') ? 'global' : 'local';
}

function eulerXYZDegToQuat(ex, ey, ez) {
  // mesma ordem do servidor (euler_xyz_deg_to_quat): qx * qy * qz
  const x = THREE.MathUtils.degToRad(ex) / 2, y = THREE.MathUtils.degToRad(ey) / 2,
        z = THREE.MathUtils.degToRad(ez) / 2;
  const qx = new THREE.Quaternion(Math.sin(x), 0, 0, Math.cos(x));
  const qy = new THREE.Quaternion(0, Math.sin(y), 0, Math.cos(y));
  const qz = new THREE.Quaternion(0, 0, Math.sin(z), Math.cos(z));
  return qx.multiply(qy).multiply(qz).normalize();
}

// Rotacao MUNDO do pai, percorrendo SO a hierarquia de ossos. Subir por
// .parent pegaria o no/grupo da cena, que tem transformacao propria e
// contaminaria a conta.
function parentWorldQuat(viewer, nome) {
  const map = (clip && clip.bone_parents) || {};
  const q = new THREE.Quaternion();
  let cur = nome;
  while (map[cur]) {
    const pb = viewer.getBone && viewer.getBone(map[cur]);
    if (!pb) break;
    q.premultiply(pb.quaternion);
    cur = map[cur];
  }
  return q;
}

function livePreview() {
  if (!clip) return;
  const nome = $('bone').value;
  const space = editSpace();
  viewers.forEach((v) => {
    const b = v.getBone && v.getBone(nome);
    if (!b) return;
    const q = eulerXYZDegToQuat(Number($('rx').value), Number($('ry').value), Number($('rz').value));
    // o three.js guarda o quaternion LOCAL do osso: em global e preciso
    // tirar a rotacao do pai (mesma conta que o servidor faz no apply)
    b.quaternion.copy(space === 'global'
      ? parentWorldQuat(v, nome).invert().multiply(q)
      : q);
  });
}

// ------------------------------------------------------------------ edicao
async function applyEdit() {
  if (!jobId) return;
  const body = {
    bone: $('bone').value,
    frame: Number($('frame').value),
    rotation_euler_deg: [Number($('rx').value), Number($('ry').value), Number($('rz').value)],
    space: editSpace(),
    start: Number($('start').value),
    end: Number($('end').value),
    author: 'editor',
    note: $('note').value || '',
  };
  try {
    const r = await api(`/api/refine/animation/${jobId}/edit`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    const rep = r.report;
    log(t('ref.editApplied', {
      bone: rep.bone.replace('mixamorig:', ''), f: rep.frame,
      a: rep.affected[0], b: rep.affected[1], n: rep.frames_rewritten,
      x: rep.anchors[0], y: rep.anchors[1],
    }));
    $('info-session').textContent = t('ref.editsCount', { n: r.history_len });
    await refreshPreview();
  } catch (e) { log(`${t('err.upper')}: ${e.message}`); }
}

async function undoRedo(kind) {
  if (!jobId) return;
  try {
    const r = await api(`/api/refine/animation/${jobId}/${kind}`, { method: 'POST' });
    log(t('ref.undoRedoLog', { kind, n: r.history_len }));
    $('info-session').textContent = t('ref.editsCount', { n: r.history_len });
    await refreshPreview();
  } catch (e) { log(`${t('err.upper')}: ${e.message}`); }
}

async function resetEdits() {
  if (!jobId) return;
  await api(`/api/refine/animation/${jobId}/reset`, { method: 'POST' });
  log(t('ref.clearedLog'));
  $('info-session').textContent = t('ref.editsCount', { n: 0 });
  await refreshPreview();
}

// ------------------------------------------------------------------ filtros
async function loadFilters() {
  const data = await api('/api/refine/filters');
  const box = $('filters');
  box.innerHTML = '';
  for (const f of data.filters) {
    const id = `flt_${f.name}`;
    const wrap = document.createElement('label');
    wrap.className = 'chk';
    const desc = (getLang() === 'en' && f.description_en) ? f.description_en : f.description;
    wrap.innerHTML = `<input type="checkbox" id="${id}" value="${f.name}" checked>`
      + `<span><b>${f.name}</b><br><small>${desc}</small></span>`;
    wrap.title = JSON.stringify(f.params);
    box.appendChild(wrap);
  }
}

function chosenFilters() {
  return Array.from(document.querySelectorAll('#filters input:checked')).map((i) => i.value);
}

async function compare() {
  if (!jobId) return;
  const el = $('cmp');
  el.classList.remove('hidden');
  el.textContent = t('ref.comparing');
  try {
    const r = await api(`/api/refine/compare/${jobId}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filters: chosenFilters() }),
    });
    const cols = Object.keys(r.rows[0]);
    el.innerHTML = '<table><thead><tr>' + cols.map((c) => `<th>${c}</th>`).join('')
      + '</tr></thead><tbody>'
      + r.rows.map((row) => '<tr>' + cols.map((c) => `<td>${row[c]}</td>`).join('') + '</tr>').join('')
      + '</tbody></table>';
    log(t('ref.compDone'));
  } catch (e) { el.textContent = t('err.prefix', { msg: e.message }); }
}

async function applyRefine() {
  if (!jobId) return;
  try {
    const r = await api(`/api/refine/animation/${jobId}/apply`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        use_filters: true, use_constraints: true,
        use_collision: $('use-collision') ? $('use-collision').checked : true,
        filters: chosenFilters(),
      }),
    });
    log(t('ref.appliedLog', { stages: r.stages.join(' → '), bytes: r.glb.bytes }));
    if (r.constraints) {
      const c = r.constraints;
      log(t('ref.constraintsLog', { bones: c.bones_corrected, frames: c.frames_corrected_total }));
    }
    if (r.collision) {   // US-07: resumo da anticolisao visivel no editor
      const k = r.collision;
      log(t('ref.collisionLog', { n: k.frames_corrected_total || 0,
        corrections: k.corrections_total || 0, max_deg: k.max_correction_deg || 0,
        source: k.skeleton_source || 'reference' }));
    }
    const a = $('dl-refined-glb'), b = $('dl-refined-fbx');
    a.href = `/api/jobs/${jobId}/artifacts/glb_refined`; a.classList.remove('disabled');
    b.href = `/api/jobs/${jobId}/artifacts/fbx_refined`; b.classList.remove('disabled');
  } catch (e) { log(`${t('err.upper')}: ${e.message}`); }
}

// ------------------------------------------------------------------ limits
// agrupamento por regiao do corpo (se o YAML ganhar juntas novas, "outras" cobre)
const REGIOES = [
  { id: 'cabeca', rotulo: 'Cabeça e pescoço', re: /^(Neck|Head)/ },
  { id: 'tronco', rotulo: 'Tronco', re: /^(Spine|Hips|Pelvis)/ },
  { id: 'braco-e', rotulo: 'Braço esquerdo', re: /^Left(Shoulder|Arm|ForeArm|Hand$)/ },
  { id: 'braco-d', rotulo: 'Braço direito', re: /^Right(Shoulder|Arm|ForeArm|Hand$)/ },
  { id: 'perna-e', rotulo: 'Perna esquerda', re: /^Left(UpLeg|Leg|Foot|Toe)/ },
  { id: 'perna-d', rotulo: 'Perna direita', re: /^Right(UpLeg|Leg|Foot|Toe)/ },
  { id: 'maos', rotulo: 'Mãos (dedos)', re: /^(Left|Right)(Hand)?(Index|Middle|Ring|Pinky|Thumb)/ },
  { id: 'outros', rotulo: 'Outras juntas', re: /./ },
];

function regiaoDe(nome) {
  const curto = String(nome || '').replace(/^mixamorig:/, '');
  for (const r of REGIOES) if (r.re.test(curto)) return r;
  return REGIOES[REGIOES.length - 1];
}

function semAcento(s) {
  return String(s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
}

let limitsOrig = [];       // referencia carregada/salva (para marcar pendencias)
let resumoLimits = '';

function marcarPendentes() {
  const pendentes = document.querySelectorAll('#limits .limit-row.dirty').length;
  $('save-limits').textContent = pendentes
    ? t('btn.saveLimitsN', { n: pendentes }) : t('btn.saveLimits');
  if (pendentes) $('limits-msg').textContent = t('hint.limitsPending', { n: pendentes });
  return pendentes;
}

function atualizarBarra(row) {
  const min = Number(row.querySelector('[data-f="min_deg"]').value);
  const max = Number(row.querySelector('[data-f="max_deg"]').value);
  const clamp = (v) => Math.max(-180, Math.min(180, Number.isFinite(v) ? v : 0));
  const a = (clamp(min) + 180) / 360 * 100;
  const b = (clamp(max) + 180) / 360 * 100;
  const seg = row.querySelector('.l-range i');
  seg.style.left = `${Math.min(a, b)}%`;
  seg.style.width = `${Math.max(1.5, Math.abs(b - a))}%`;
}

function filtrarLimits(q) {
  const busca = semAcento(q).trim();
  let gruposVisiveis = 0;
  document.querySelectorAll('#limits .limit-group').forEach((g) => {
    let visiveis = 0;
    g.querySelectorAll('.limit-row').forEach((row) => {
      const ok = !busca || row.dataset.busca.includes(busca);
      row.hidden = !ok;
      if (ok) visiveis += 1;
    });
    g.hidden = visiveis === 0;
    if (visiveis) gruposVisiveis += 1;
    const conta = g.querySelector('.g-conta');
    conta.textContent = busca ? `${visiveis}/${conta.dataset.total}` : conta.dataset.total;
    if (busca && visiveis) g.open = true;
  });
  $('limits-meta').textContent = busca
    ? t('limits.searchResult', { g: gruposVisiveis, q })
    : resumoLimits;
}

async function loadLimits() {
  const data = await api('/api/refine/constraints');
  const box = $('limits');
  box.innerHTML = '';
  limitsOrig = data.preset.limits.map((l) => ({ ...l }));

  const grupos = new Map();
  data.preset.limits.forEach((l, i) => {
    const r = regiaoDe(l.bone);
    if (!grupos.has(r.id)) grupos.set(r.id, { regiao: r, itens: [] });
    grupos.get(r.id).itens.push({ l, i });
  });
  resumoLimits = t('limits.summary', { n: data.preset.limits.length, g: grupos.size });

  const barra = document.createElement('div');
  barra.className = 'limits-bar';
  barra.innerHTML =
    `<input type="search" id="limits-search" aria-label="${t('limits.searchAria')}"`
    + ` placeholder="${t('limits.searchPh')}" />`
    + `<button id="limits-toggle" class="ghost" type="button">${t('btn.collapseAll')}</button>`;
  box.appendChild(barra);

  const meta = document.createElement('p');
  meta.className = 'limits-meta';
  meta.id = 'limits-meta';
  meta.textContent = resumoLimits;
  box.appendChild(meta);

  const legenda = document.createElement('div');
  legenda.className = 'limits-legend';
  legenda.innerHTML = `<span>${t('limits.legendJoint')}</span><span>${t('limits.legendMin')}</span>`
    + `<span>${t('limits.legendMax')}</span><span>${t('limits.legendStiff')}</span>`;
  box.appendChild(legenda);

  for (const { regiao, itens } of grupos.values()) {
    const det = document.createElement('details');
    det.className = 'limit-group';
    det.open = true;
    const sum = document.createElement('summary');
    sum.innerHTML = `<span class="g-nome">${t('region.' + regiao.id)}</span>`
      + `<span class="g-conta" data-total="${itens.length}">${itens.length}</span>`;
    det.appendChild(sum);

    for (const { l, i } of itens) {
      const curto = String(l.bone).replace(/^mixamorig:/, '');
      const row = document.createElement('div');
      row.className = 'limit-row';
      row.dataset.i = String(i);
      const nota = (getLang() === 'en') ? (l.note_en || l.note || '') : (l.note || l.note_en || '');
      row.dataset.busca = semAcento(`${curto} ${l.kind} ${nota}`);
      row.title = nota;
      row.innerHTML =
        `<span class="l-nome"><b>${curto}</b>`
        + `<span class="k-chip k-${l.kind}"><i></i>${l.kind}</span></span>`
        + `<input type="number" data-i="${i}" data-f="min_deg" value="${l.min_deg}"`
        + ` title="${t('limits.titleMin')}" aria-label="${t('limits.ariaMin', { bone: curto })}">`
        + `<input type="number" data-i="${i}" data-f="max_deg" value="${l.max_deg}"`
        + ` title="${t('limits.titleMax')}" aria-label="${t('limits.ariaMax', { bone: curto })}">`
        + `<input type="number" data-i="${i}" data-f="stiffness" value="${l.stiffness}" step="0.1" min="0" max="1"`
        + ` title="${t('limits.titleStiff')}" aria-label="${t('limits.ariaStiff', { bone: curto })}">`
        + '<span class="l-range"><i></i></span>';
      det.appendChild(row);
      atualizarBarra(row);
    }
    box.appendChild(det);
  }

  $('limits-toggle').addEventListener('click', () => {
    const todos = Array.from(box.querySelectorAll('.limit-group'));
    const algumFechado = todos.some((g) => !g.open);
    todos.forEach((g) => { g.open = algumFechado; });
    $('limits-toggle').textContent = t(algumFechado ? 'btn.collapseAll' : 'btn.expandAll');
  });

  box.addEventListener('input', (ev) => {
    const inp = ev.target;
    if (inp.closest && inp.closest('.limit-row')) {
      const row = inp.closest('.limit-row');
      const i = Number(row.dataset.i);
      const orig = limitsOrig[i] || {};
      const val = (f) => Number(row.querySelector(`[data-f="${f}"]`).value);
      row.classList.toggle('dirty',
        ['min_deg', 'max_deg', 'stiffness'].some((f) => val(f) !== Number(orig[f])));
      row.classList.toggle('invalid',
        val('min_deg') > val('max_deg') || val('stiffness') < 0 || val('stiffness') > 1);
      atualizarBarra(row);
      marcarPendentes();
    } else if (inp.id === 'limits-search') {
      filtrarLimits(inp.value);
    }
  });

  marcarPendentes();
  $('limits-msg').textContent = '';
  log(t('limits.loadedLog', { n: data.preset.limits.length, f: data.path.split('\\').pop() }));
}

async function saveLimits() {
  const data = await api('/api/refine/constraints');
  const preset = data.preset;
  document.querySelectorAll('#limits .limit-row input[data-f]').forEach((inp) => {
    const i = Number(inp.dataset.i), f = inp.dataset.f;
    preset.limits[i][f] = Number(inp.value);
  });
  try {
    const r = await api('/api/refine/constraints', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ preset }),
    });
    // os valores salvos viram a nova referência (limpa o estado "pendente")
    limitsOrig = data.preset.limits.map((l) => ({ ...l }));
    document.querySelectorAll('#limits .limit-row').forEach((row) => row.classList.remove('dirty'));
    marcarPendentes();
    $('limits-msg').textContent = t('limits.savedMsg', { n: r.limits, f: r.saved.split('\\').pop() });
    log(t('limits.savedLog'));
  } catch (e) {
    $('limits-msg').textContent = t('err.prefix', { msg: e.message });
  }
}

// 3D indisponivel: degrada com mensagem e mantem o resto do editor vivo (US-01)
function showNo3D() {
  for (const id of ['empty-skel', 'empty-mesh']) {
    const el = $(id);
    if (el) { el.style.display = ''; el.textContent = t('viewer.no3d'); }
  }
  document.querySelectorAll('[data-cam]').forEach((b) => { b.disabled = true; });
  for (const id of ['sync-rot']) {
    const el = $(id);
    if (el) { el.disabled = true; el.checked = false; }
  }
}

// ------------------------------------------------------------------ wire
window.addEventListener('DOMContentLoaded', () => {
  try {
    viewers.push(new Viewer('canvas-skel', 'empty-skel', true));
    viewers.push(new Viewer('canvas-mesh', 'empty-mesh', false));
    // paineis A/B da mistura: esqueleto dos dois jobs, camera e frame compartilhados
    cmpA = new Viewer('canvas-a', 'empty-a', true);
    cmpB = new Viewer('canvas-b', 'empty-b', true);
  } catch (err) {
    console.error('falha ao iniciar o 3D (WebGL):', err);
    viewers.length = 0;
    no3d = true;
  }
  const v0 = $('refvideo');
  if (v0) {
    v0.muted = true;
    v0.playsInline = true;
    v0.addEventListener('error', () => {
      const o = $('video-empty');
      if (o) { o.style.display = ''; o.textContent = t('ref.loadFail'); }
    });
  }
  $('playpause').textContent = '▶';   // estado inicial: pausado
  applyStatic();
  langSwitcher('lang-switch');
  ligarSyncRot();
  if (no3d) showNo3D();
  window.addEventListener('resize', () => { viewers.forEach((v) => v.resize()); [cmpA, cmpB].forEach((v) => { if (v) v.resize(); }); reapplyCamera(); syncCompareCamera(viewers[0], cmpA); drawKpOverlay(); });

  document.querySelectorAll('.refine-tabs .tab').forEach((b) =>
    b.addEventListener('click', () => switchRefineTab(b.dataset.tab)));
  if ($('tr-apply')) $('tr-apply').addEventListener('click', applyTransplant);
  if ($('tr-undo')) $('tr-undo').addEventListener('click', undoTransplant);
  if ($('tr-source')) $('tr-source').addEventListener('change', () => loadCompareClips(true));
  if ($('tr-usar-mapa')) $('tr-usar-mapa').addEventListener('change', (e) => {
    if ($('tr-mapa')) $('tr-mapa').hidden = !e.target.checked;
    syncRangeBounds();
  });
  initTransplant();
  loadJobs();
  loadFilters();
  loadLimits();

  $('job').addEventListener('change', (e) => selectJob(e.target.value));
  $('frame').addEventListener('input', syncSliders);
  $('bone').addEventListener('change', syncSliders);
  // a largura escolhida a mao e preservada quando a janela recentra no frame
  ['start', 'end'].forEach((k) => $(k).addEventListener('input', () => {
    const w = Number($('end').value) - Number($('start').value);
    if (w > 0) rangeWidth = w;
  }));
  // trocar o referencial muda o significado dos angulos: recarrega o painel
  // (e restaura a pose, senao a previa ficaria com o osso torto).
  if ($('space')) $('space').addEventListener('change', () => {
    viewers.forEach((v) => v.setFrame(currentFrame, clip ? clip.fps : 30));
    syncSliders();
  });
  ['rx', 'ry', 'rz'].forEach((k) =>
    $(k).addEventListener('input', () => {
      $(`${k}-label`).textContent = `${$(k).value}°`;
      livePreview();   // gira o osso selecionado na hora (sem gravar)
    }));
  $('apply-edit').addEventListener('click', applyEdit);
  $('undo').addEventListener('click', () => undoRedo('undo'));
  $('redo').addEventListener('click', () => undoRedo('redo'));
  $('reset').addEventListener('click', resetEdits);
  $('compare').addEventListener('click', compare);
  $('apply-refine').addEventListener('click', applyRefine);
  $('save-limits').addEventListener('click', saveLimits);
  $('timeline').addEventListener('input', (e) => {
    if (!clip) return;
    const t = Math.round((Number(e.target.value) / 1000) * (clip.num_frames - 1));
    $('frame').value = t;
    syncSliders();
  });
  $('playpause').addEventListener('click', () => {
    playing = !playing;
    $('playpause').textContent = playing ? '❚❚' : '▶︎';
    if (playing) step();
  });
  document.querySelectorAll('[data-cam]').forEach((b) =>
    b.addEventListener('click', () => { activeCam = b.dataset.cam; viewers.forEach((v) => v.setCamera(activeCam)); [cmpA, cmpB].forEach((v) => { if (v) v.setCamera(activeCam); }); }));
  $('kp-overlay').addEventListener('change', () => {
    kpOn = $('kp-overlay').checked;
    drawKpOverlay();
  });
  $('bone-labels').addEventListener('change', () => {
    const on = $('bone-labels').checked;
    viewers.forEach((v) => v.setLabels(on));
  });
});

// ------------------------------------------- debug: overlay do esqueleto 2D
const SKELETON_EDGES = [
  [0, 1], [0, 2], [1, 3], [2, 4], [5, 7], [7, 9], [6, 8], [8, 10],
  [5, 6], [5, 11], [6, 12], [11, 12], [11, 13], [13, 15], [12, 14], [14, 16],
];
let kpData = null;
let handsData = null;
let kpOn = false;

async function loadKp() {
  kpData = null;
  handsData = null;
  const chk = $('kp-overlay');
  if (chk) { chk.disabled = false; }
  if (!jobId) return;
  try {
    const r = await fetch(`/api/jobs/${jobId}/artifacts/kp2d`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    kpData = await r.json();
  } catch (e) {
    kpData = null;
    if (chk) { chk.disabled = kpOn ? false : true; }
    const lab = chk && chk.closest('label');
    if (lab) lab.title = t('ui.kpUnavailable');
  }
  if (jobId) {
    try {
      const rh = await fetch(`/api/jobs/${jobId}/artifacts/hands`);
      handsData = rh.ok ? await rh.json() : null;
    } catch (e) { handsData = null; }
  }
  drawKpOverlay();
  reapplyCamera();
}

function drawKpOverlay() {
  const cv = $('kp-canvas');
  if (!cv) return;
  const ctx = cv.getContext('2d');
  const w = cv.clientWidth, h = cv.clientHeight;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
    cv.width = Math.round(w * dpr);
    cv.height = Math.round(h * dpr);
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  if (!kpOn || !kpData || !kpData.frames) return;
  const fr = kpData.frames[currentFrame];
  if (!fr) return;
  const vw = kpData.width || w, vh = kpData.height || h;
  const s = Math.min(w / vw, h / vh);
  const ox = (w - vw * s) / 2, oy = (h - vh * s) / 2;
  const pt = (i) => [ox + fr[i][0] * s, oy + fr[i][1] * s];
  ctx.lineWidth = 2;
  ctx.strokeStyle = 'rgba(95, 125, 106, .95)';
  for (const [a, b2] of SKELETON_EDGES) {
    if (!fr[a] || !fr[b2]) continue;
    const [x1, y1] = pt(a), [x2, y2] = pt(b2);
    ctx.globalAlpha = Math.min(1, 0.35 + 0.65 * Math.min(fr[a][2] || 0, fr[b2][2] || 1));
    ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
  }
  ctx.globalAlpha = 1;
  for (let i = 0; i < fr.length; i += 1) {
    if (!fr[i]) continue;
    const [x, y] = pt(i);
    ctx.fillStyle = (fr[i][2] ?? 1) > 0.5 ? '#b06a48' : '#8a8175';
    ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI * 2); ctx.fill();
  }
  drawHandsOverlay(ctx, s, ox, oy, currentFrame);
}

// ------------------------------- maos detectadas (hand tracking) sobre o video
const HAND_EDGES = [
  [0, 1], [1, 2], [2, 3], [3, 4], [0, 5], [5, 6], [6, 7], [7, 8],
  [5, 9], [9, 10], [10, 11], [11, 12], [9, 13], [13, 14], [14, 15],
  [15, 16], [13, 17], [17, 18], [18, 19], [19, 20], [0, 17],
];

function drawHandsOverlay(ctx, s, ox, oy, frameIdx) {
  if (!handsData || !handsData.frames) return;
  const hf = handsData.frames[frameIdx];
  if (!hf) return;
  const desenha = (lm, cor) => {
    if (!lm) return;
    ctx.strokeStyle = cor;
    ctx.fillStyle = cor;
    ctx.lineWidth = 1.6;
    for (const par of HAND_EDGES) {
      const p1 = lm[par[0]], p2 = lm[par[1]];
      if (!p1 || !p2) continue;
      ctx.beginPath();
      ctx.moveTo(ox + p1[0] * s, oy + p1[1] * s);
      ctx.lineTo(ox + p2[0] * s, oy + p2[1] * s);
      ctx.stroke();
    }
    for (const p of lm) {
      ctx.beginPath(); ctx.arc(ox + p[0] * s, oy + p[1] * s, 2, 0, Math.PI * 2); ctx.fill();
    }
  };
  desenha(hf.left, '#3d6fa8');    // mao esquerda
  desenha(hf.right, '#b06a48');   // mao direita
}

function makeTextSprite(texto) {
  const c = document.createElement('canvas');
  c.width = 256; c.height = 64;
  const g = c.getContext('2d');
  g.font = '600 30px Inter, system-ui, sans-serif';
  g.textAlign = 'center'; g.textBaseline = 'middle';
  const w = g.measureText(texto).width + 18;
  g.fillStyle = 'rgba(251, 250, 247, .86)';
  g.fillRect(128 - w / 2, 9, w, 46);
  g.fillStyle = '#2b2a27';
  g.fillText(texto, 128, 33);
  const tex = new THREE.CanvasTexture(c);
  const mat = new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true });
  const spr = new THREE.Sprite(mat);
  spr.scale.set(0.22, 0.055, 1);
  spr.renderOrder = 999;
  return spr;
}

// ---------------------------------------------------- sync rotation entre paineis
let syncRot = true;
try { syncRot = localStorage.getItem('v2m:syncRot') !== '0'; } catch (e) { /* sem storage */ }

function ligarSyncRot() {
  const chk = $('sync-rot');
  if (!chk) return;
  chk.checked = syncRot;
  const copiar = (origem, destino) => {
    if (!origem || !destino) return;
    destino.camera.position.copy(origem.camera.position);
    destino.controls.target.copy(origem.controls.target);
    destino.controls.update();
  };
  let eco = false;
  viewers.forEach((v, i) => {
    v.controls.addEventListener('change', () => {
      if (!syncRot || eco) return;
      eco = true;
      copiar(v, viewers[1 - i]);
      eco = false;
    });
  });
  chk.addEventListener('change', () => {
    syncRot = chk.checked;
    try { localStorage.setItem('v2m:syncRot', syncRot ? '1' : '0'); } catch (e) { /* ok */ }
    if (syncRot && viewers.length === 2) {
      eco = true;
      copiar(viewers[0], viewers[1]);
      eco = false;
    }
  });
}

function step() {
  if (!playing || !clip) return;
  currentFrame = (currentFrame + 1) % clip.num_frames;
  $('frame').value = currentFrame;
  syncSliders();
  setTimeout(step, 1000 / Math.max(clip.fps, 1));
}
