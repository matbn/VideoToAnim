// VideoToAnim — SPA (three.js). Dropdown de backends, upload (vídeo + malha),
// status do job e comparação: vídeo de referência × esqueleto × malha.
//
// Sincronismo: o VÍDEO é a única fonte de tempo. Todos os visualizadores 3D são
// posicionados a partir do tempo do vídeo (p = t / span), o loop é manual no fim
// do trecho processado e o play só começa quando o vídeo e os GLB estão prontos.
//
// O painel "esqueleto" mostra o rig (ossos como linhas, via SkeletonHelper) sem
// a malha. Se o job não tiver malha do usuário, os dois painéis 3D mostram o
// esqueleto.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { t, applyStatic, langSwitcher, getLang } from './i18n.js?v=5';

const $ = (id) => document.getElementById(id);

const CAMERAS = {
  persp: [2.2, 1.6, 2.6],
  front: [0, 1.1, 3.2],
  back: [0, 1.1, -3.2],
  side: [3.4, 1.1, 0],
  top: [0, 4.0, 0.001],
};

let clock = null, pollTimer = null;
let videoEl = null;
let spanS = 0, videoReady = false;
let playing = false;
// Sincronismo com o video. `mediaTime` e o instante do frame REALMENTE
// apresentado na tela (requestVideoFrameCallback); `video.currentTime` e o
// relogio de reproducao, que corre À FRENTE do que ja foi pintado — usar ele
// para dirigir o 3D produz um atraso constante de 1-2 frames (medido: 33-66 ms
// a 30 fps), visivel na comparacao lado a lado.
let mediaTime = null;
let rvfcHandle = 0;
let rvfcRunning = false;   // trava: uma cadeia so de requestVideoFrameCallback
let scrubbing = false;      // o usuario esta arrastando a timeline
let resumeAfterScrub = false;
let vFps = 30, tFps = 30;      // fps do video de origem e fps da animacao exportada
let lastFrameT = 0;            // instante do ULTIMO frame processado = (T-1)/vFps
const PELVIS_Y = 0.98;         // altura pelvica de referencia (core/lifter.py)
const SPINE_M = 0.52;          // comprimento da coluna usado pelo lifter
const viewers = [];
let no3d = false;
const backendsByName = {};

// escapa texto para atributos HTML (US-06: tooltips com o valor completo)
const attresc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
  .replace(/>/g, '&gt;').replace(/"/g, '&quot;');

// categorias de licenca (espelham core/adapter.py LICENSE_CATEGORIES)
const CAT_INFO = {
  livre: { label: 'livre', color: '#4f6d5c' },
  nao_comercial: { label: 'não comercial', color: '#b06a48' },
  licenca_a_parte: { label: 'licença à parte', color: '#8a7a4a' },
};
const catLabel = (c) => {
  const k = `cat.${c}`;
  const v = t(k);
  return v === k ? (CAT_INFO[c] || { label: c }).label : v;
};

// ------------------------------------------------------------------ Viewer
class Viewer {
  constructor(hostId, emptyId, { skeletonOnly = false } = {}) {
    this.hostId = hostId;
    this.emptyId = emptyId;
    this.skeletonOnly = skeletonOnly;
    this.mixer = null;
    this.action = null;
    this.clipDuration = 0;
    this.model = null;
    this.ready = false;
    this._init();
  }

  _init() {
    const wrap = $(this.hostId);
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setSize(wrap.clientWidth || 320, wrap.clientHeight || 300);
    wrap.appendChild(this.renderer.domElement);

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(
      38, (wrap.clientWidth || 320) / (wrap.clientHeight || 300), 0.05, 100);
    this.camera.position.set(...CAMERAS.persp);

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.target.set(0, 0.95, 0);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;

    this.scene.add(new THREE.HemisphereLight(0xffffff, 0xbfb8ab, 2.1));
    const key = new THREE.DirectionalLight(0xffffff, 1.4);
    key.position.set(3, 6, 4);
    this.scene.add(key);
    const rim = new THREE.DirectionalLight(0xdfe8e0, 0.6);
    rim.position.set(-4, 2, -3);
    this.scene.add(rim);
    this.scene.add(new THREE.GridHelper(6, 24, 0xcfc9bd, 0xe4e0d7));
    this.onResize();
  }

  onResize() {
    const wrap = $(this.hostId);
    const w = wrap.clientWidth || 320, h = wrap.clientHeight || 300;
    this.renderer.setSize(w, h);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  load(url) {
    const loader = new GLTFLoader();
    loader.load(url, (gltf) => {
      this.clear();
      this.model = gltf.scene;
      this.scene.add(this.model);
      this._criarLabels();

      if (this.skeletonOnly) {
        // esconde a geometria e desenha o rig como linhas entre os ossos
        this.model.traverse((o) => { if (o.isMesh || o.isSkinnedMesh) o.visible = false; });
        const helper = new THREE.SkeletonHelper(this.model);
        helper.material.color = new THREE.Color(0x4f6d5c);
        helper.material.depthTest = false;
        helper.material.transparent = true;
        helper.material.opacity = 0.95;
        helper.renderOrder = 10;
        this.scene.add(helper);
        this.helper = helper;
      }

      if (gltf.animations && gltf.animations.length) {
        this.mixer = new THREE.AnimationMixer(this.model);
        this.action = this.mixer.clipAction(gltf.animations[0]);
        this.clipDuration = this.action.getClip().duration;
        this.action.play();
      }
      this.ready = true;
      $(this.emptyId).style.display = 'none';
      maybeStartPlayback();
    }, undefined, (err) => console.error('erro ao carregar GLB', this.hostId, err));
  }

  _criarLabels() {
    this.labelSprites = [];
    const filtro = /(Index|Middle|Ring|Pinky|Thumb)\d$/;
    this.model.traverse((o) => {
      if (o.isBone && !filtro.test(o.name)) {
        const spr = makeTextSprite(o.name.replace(/^(?:mixamorig|mixamo)[:_]?/i, ''));
        spr.position.set(0, 0.025, 0);
        spr.visible = !!this._labelsOn;
        o.add(spr);
        this.labelSprites.push(spr);
      }
    });
  }
  setLabels(v) {
    this._labelsOn = !!v;
    (this.labelSprites || []).forEach((s) => { s.visible = this._labelsOn; });
  }

  // `force` ignora o guarda de mudanca (usado no loop e no seek).
  // Re-pinar o mixer 60x por segundo e desperdicado e reintroduz jitter: so
  // reescreve quando o instante realmente mudou.
  setTime(t, force) {
    if (!this.mixer || !(this.clipDuration > 0)) return;
    const at = Math.max(0, Math.min(t, this.clipDuration));
    if (!force && this._lastT !== undefined && Math.abs(at - this._lastT) < 1e-4) return;
    this._lastT = at;
    this.mixer.setTime(at);
  }

  update(dt) {
    if (this.mixer && this.ready && !videoReady) this.mixer.update(dt);
  }

  setCamera(name) {
    if (name === 'video') { this.setCameraMatchVideo(); return; }
    this.camera.position.set(...(CAMERAS[name] || CAMERAS.persp));
    this.controls.target.set(0, 0.95, 0);
    this.controls.update();
  }

  // Camera que reenquadra o 3D como o video de referencia esta enquadrado.
  // Com a camera em (camX, camY, d) olhando para -Z, um ponto de mundo Y cai na
  // fracao de altura  f = 1/2 - (Y - camY) / (2 d tan(fov/2)). O lifter pôs o
  // quadril do primeiro frame em Y = 0.98 m e o tronco com 0.52 m, entao
  // igualando as fracoes do video:
  //   d    = 0.52 / (2 * (tronco_px / altura_video) * tan(fov/2))
  //   camY = 0.98 - d tan(fov/2) * (1 - 2 py_quadril / altura_video)
  // As fracoes batem exatamente (provado em tests/test_preview_sync.py).
  setCameraMatchVideo() {
    const cal = typeof videoCalibration === 'function' ? videoCalibration() : null;
    if (!cal) {                       // sem video carregado: cai na perspective
      this.camera.position.set(...CAMERAS.persp);
      this.controls.target.set(0, 0.95, 0);
      this.controls.update();
      return;
    }
    const half = Math.tan((this.camera.fov * Math.PI / 180) / 2);
    const fv = cal.torsoPx / cal.videoH;               // tronco como fracao da altura
    const d = SPINE_M / (2 * Math.max(fv, 1e-3) * half);
    const camY = PELVIS_Y - d * half * (1 - 2 * cal.hipPy / cal.videoH);
    // eixo X: o personagem pode estar fora do centro do quadro
    const s = SPINE_M / Math.max(cal.torsoPx, 1e-3);   // m por px do video
    const xWorld = (cal.hipPx - cal.videoW / 2) * s;   // onde o lifter botou o quadril
    const camX = xWorld - (2 * (cal.hipPx / cal.videoW) - 1) * d * half * this.camera.aspect;
    this.camera.position.set(camX, camY, d);
    this.controls.target.set(camX, camY, 0);
    this.controls.update();
  }

  render() {
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }

  clear() {
    if (this.mixer) { this.mixer.stopAllAction(); this.mixer = null; }
    this.action = null;
    (this.labelSprites || []).forEach((s) => {
      if (s.material) { if (s.material.map) s.material.map.dispose(); s.material.dispose(); }
    });
    this.labelSprites = [];
    if (this.helper) { this.scene.remove(this.helper); this.helper = null; }
    if (this.model) { this.scene.remove(this.model); this.model = null; }
    this.ready = false;
  }
}

// ------------------------------------------- debug: overlay do esqueleto 2D
const SKELETON_EDGES = [
  [0, 1], [0, 2], [1, 3], [2, 4], [5, 7], [7, 9], [6, 8], [8, 10],
  [5, 6], [5, 11], [6, 12], [11, 12], [11, 13], [13, 15], [12, 14], [14, 16],
];
let kpData = null;
let handsData = null;
let kpOn = false;

function kpFrameIndex() {
  if (!kpData || !videoEl) return 0;
  const fps = kpData.fps || 30;
  const last = (kpData.frames || []).length - 1;
  return Math.max(0, Math.min(last, Math.floor((videoEl.currentTime || 0) * fps)));
}

async function loadKp(id) {
  kpData = null;
  handsData = null;
  const chk = $('kp-overlay');
  if (chk) { chk.disabled = false; }
  if (!id) { drawKpOverlay(); return; }
  try {
    const r = await fetch(`/api/jobs/${id}/artifacts/kp2d`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    kpData = await r.json();
  } catch (e) {
    kpData = null;
    if (chk) chk.disabled = true;
    const lab = chk && chk.closest('label');
    if (lab) lab.title = t('ui.kpUnavailable');
  }
  if (id) {
    try {
      const rh = await fetch(`/api/jobs/${id}/artifacts/hands`);
      handsData = rh.ok ? await rh.json() : null;
    } catch (e) { handsData = null; }
  }
  drawKpOverlay();
  reapplyCamera();
}

function drawKpOverlay(idx) {
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
  const fr = kpData.frames[idx === undefined ? kpFrameIndex() : idx];
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
  drawHandsOverlay(ctx, s, ox, oy, idx === undefined ? kpFrameIndex() : idx);
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

// ------------------------------------------------------------------ loop
// Enquadra o 3D como o video: usa as juntas 2D do primeiro frame (mid-hip e
// mid-ombro) + o tamanho do video, que e exatamente a mesma geometria que o
// lifter usou para liftar a pose. Sem isso a camera fica num lugar fixo e a
// comparacao lado a lado fica com deslocamento/escala apparent.
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

// Instante do video -> instante da animacao. O frame i do video corresponde ao
// frame i da animacao (um frame processado -> um keyframe), entao
//   animTime = (mediaTime * vFps) / tFps.
// Usar o tempo normalizado (mediaTime/spanS * clipDuration) compressa a
// animacao: o GLB termina no ULTIMO keyframe, (T-1)/fps, e nao em T/fps — o
// erro crescia de 0 a ~1 frame no fim do clipe.
function animTimeFor(mediaT) {
  if (!(vFps > 0) || !(tFps > 0)) return mediaT;
  return (mediaT * vFps) / tFps;
}

function tick() {
  const dt = clock.getDelta();
  const drivingByVideo = videoReady && spanS > 0 && lastFrameT > 0;

  if (drivingByVideo && videoEl && !videoEl.paused) {
    // O loop e decidido pelo RELOGIO DO VIDEO (currentTime), nunca pelo
    // mediaTime do requestVideoFrameCallback. O mediaTime so avanca quando o
    // navegador APRESENTA um frame novo: usar ele aqui criava um travamento —
    // ao dar wrap, a busca por 0 levava alguns ms até o frame 0 ser pintado,
    // mediaTime ficava preso no ultimo instante e o wrap disparava de novo a
    // CADA rAF (~60 buscas/s). O video tremia sem sair do lugar e o 3D
    // ficava congelado no primeiro frame. currentTime realmente zera na busca.
    if (videoEl.currentTime >= lastFrameT) {
      videoEl.currentTime = 0;                    // loop manual (vídeo + 3D juntos)
      mediaTime = null;                           // descarta o instante antigo
      viewers.forEach((v) => v.setTime(0, true));
      setTimelineUI(0, true);
    } else {
      // a pose usa o frame JA APRESENTADO; currentTime e so a reserva quando o
      // navegador nao tem requestVideoFrameCallback (ou ainda nao pintou nada)
      const t = (mediaTime !== null && mediaTime <= videoEl.currentTime)
        ? mediaTime : videoEl.currentTime;
      viewers.forEach((v) => v.setTime(animTimeFor(t)));
      setTimelineUI(Math.max(0, Math.min(1, videoEl.currentTime / lastFrameT)));
    }
  } else if (!drivingByVideo) {
    viewers.forEach((v) => v.update(dt));
    const first = viewers.find((v) => v.clipDuration > 0 && v.action);
    if (first && playing) {
      setTimelineUI((first.action.time % first.clipDuration) / first.clipDuration);
    }
  }
  if (kpOn) drawKpOverlay();
  viewers.forEach((v) => v.render());
}

function onResizeAll() {
  viewers.forEach((v) => v.onResize());
  reapplyCamera();
  drawKpOverlay();
}

let activeCam = 'persp';
function setCameraAll(name) {
  activeCam = name;
  document.querySelectorAll('[data-cam]').forEach((b) =>
    b.classList.toggle('active', b.dataset.cam === name));
  viewers.forEach((v) => v.setCamera(name));
}
// Reaplica a camera ativa depois que o kp2d chega: a preset "video" so pode ser
// calculada depois das juntas 2D (o clique no botao pode ter vindo antes).
function reapplyCamera() { if (activeCam === 'video') viewers.forEach((v) => v.setCamera('video')); }


// ------------------------------------------------------------------ abas
let activeTab = 'new';
let histJobs = [];

function switchTab(tab) {
  activeTab = tab;
  document.querySelectorAll('.tab').forEach((b) =>
    b.classList.toggle('active', b.dataset.tab === tab));
  $('tab-new').classList.toggle('hidden', tab !== 'new');
  $('tab-history').classList.toggle('hidden', tab !== 'history');
  if (tab === 'history') loadHistory();
}

async function loadHistory() {
  const st = $('hist-status').value;
  const be = $('hist-backend').value;
  const q = new URLSearchParams();
  if (st) q.set('status', st);
  if (be) q.set('backend', be);
  const data = await (await fetch('/api/jobs' + (q.toString() ? '?' + q : ''))).json();
  histJobs = data.jobs;
  $('hist-count').textContent = String(histJobs.length);
  const box = $('hist-table');
  if (!histJobs.length) {
    box.innerHTML = `<p class="hint">${t('hist.empty')}</p>`;
    return;
  }
  const rows = histJobs.map((j) => {
    const d = new Date((j.finished_at || j.created_at || 0) * 1000);
    const when = d.getTime()
      ? d.toLocaleString(getLang() === 'pt' ? 'pt-BR' : 'en-US', { dateStyle: 'short', timeStyle: 'short' }) : '—';
    const ok = j.status === 'done';
    return `<tr data-id="${j.id}" class="hist-row ${j.status}">
      <td class="mono" title="${attresc(j.id)}">${j.id.slice(0, 8)}</td>
      <td>${when}</td>
      <td title="${attresc(j.video_name)}">${(j.video_name || '').slice(0, 22)}</td>
      <td>${j.backend}</td>
      <td><span class="status-${j.status}">${stLabel(j.status)}</span></td>
      <td>${(j.metrics && j.metrics.frames) ? j.metrics.frames : '—'}</td>
      <td class="hist-actions">
        <button data-open="${j.id}" class="ghost small">${t('hist.open')}</button>
        ${ok ? `<a href="/api/jobs/${j.id}/artifacts/glb" class="ghost small" download>GLB</a>` : ''}
        ${ok ? `<a href="/api/jobs/${j.id}/artifacts/fbx" class="ghost small" download>FBX</a>` : ''}
        ${ok ? `<a href="/refine?job=${j.id}" class="ghost small">${t('hist.refine')}</a>` : ''}
      </td>
    </tr>`;
  }).join('');
  box.innerHTML = '<table class="hist-table"><thead><tr>' +
    `<th>id</th><th>${t('hist.colWhen')}</th><th>${t('hist.colVideo')}</th><th>backend</th><th>status</th>` +
    `<th>${t('hist.colFrames')}</th><th></th></tr></thead><tbody>` + rows + '</tbody></table>';
  box.querySelectorAll('button[data-open]').forEach((b) =>
    b.addEventListener('click', () => openJob(b.dataset.open)));
  box.querySelectorAll('tr.hist-row').forEach((tr) =>
    tr.addEventListener('dblclick', () => openJob(tr.dataset.id)));
}

// abre um job do historico no visualizador (sem reprocessar)
function openJobResult(job) {
  const jobId = job.id;
  $('job-id').textContent = jobId;
  $('job-status').textContent = stLabel(job.status);
  $('job-backend').textContent = job.backend;
  $('job-license').textContent = (backendsByName[job.backend] || {}).license_category || '—';
  $('job-mesh').textContent = (job.mesh_report && job.mesh_report.format) || t('job.noMesh');
  if (job.metrics && Object.keys(job.metrics).length) {
    const m = job.metrics;
    $('job-metrics').textContent =
      `${m.frames} frames · score ${(m.mean_score ?? 0).toFixed(2)} · FK ${(100 * (m.fk_max_position_m ?? 0)).toFixed(1)} cm · ${m.elapsed_s}s`;
  }
  $('job-log').textContent = job.log || '';
  renderMeshReport(job.mesh_report);

  const done = job.status === 'done';
  const art = job.artifacts || {};
  const glbOk = done && art.glb && Number(art.glb.size) > 0;
  const fbxOk = done && art.fbx && Number(art.fbx.size) > 0;
  const a1 = $('dl-glb'), a2 = $('dl-fbx');
  a1.href = glbOk ? `/api/jobs/${jobId}/artifacts/glb` : '#';
  a1.classList.toggle('disabled', !glbOk);
  a2.href = fbxOk ? `/api/jobs/${jobId}/artifacts/fbx` : '#';
  a2.classList.toggle('disabled', !fbxOk);
  const or = $('open-refine');
  or.href = `/refine?job=${jobId}`;
  or.classList.toggle('disabled', !done);
  // o editor de refino abre automaticamente o job que estiver aberto aqui (aba padrao)
  try { localStorage.setItem('v2m:lastJob', jobId); } catch (e) { /* sem storage */ }
  const navRef = $('nav-refine');
  if (navRef) navRef.href = `/refine?job=${jobId}`;

  const hasMesh = job.metrics && job.metrics.mesh === 'user_mesh';
  $('title-skel').textContent = t('viewer.skel');
  $('title-mesh').textContent = hasMesh
    ? t('viewer.meshNamed') : t('viewer.skel2');
  if (!no3d) {
    viewers[1].skeletonOnly = !hasMesh;
    viewers.forEach((v) => v.clear());
    if (done) {
      viewers[0].load(`/api/jobs/${jobId}/artifacts/glb`);
      viewers[1].load(`/api/jobs/${jobId}/artifacts/glb`);
    }
  }
  vFps = (job.metrics && job.metrics.video_fps) || 30;
  tFps = (job.metrics && job.metrics.fps) || 30;
  setSpan((job.metrics && job.metrics.video_span_s) || 0);
  mediaTime = null;
  videoEl.src = `/api/jobs/${jobId}/artifacts/video`;
  videoEl.load();
  $('video-empty').style.display = 'none';
  loadKp(jobId);
  const note = $('hist-note');
  if (note) note.textContent = t('note.reopened', { id: jobId });
}

async function openJob(id) {
  const job = await (await fetch(`/api/jobs/${id}`)).json();
  openJobResult(job);
}

// -------------------------------------------------------------- backends
// rotulo curto do dispositivo de execucao (vem de install.compute)
function deviceLabel(c) {
  if (!c || !c.device || c.device === 'cpu') return 'CPU';
  if (c.device === 'cuda') return 'GPU CUDA';
  if (c.device === 'directml') return 'GPU DirectML';
  if (c.device === 'mps') return 'GPU MPS';
  return `GPU ${String(c.device).toUpperCase()}`;
}

// motivo do dispositivo em PT/EN (o servidor manda codigo + variaveis)
function computeReason(c) {
  if (!c) return '';
  if (c.reason_code) {
    const k = `compute.${c.reason_code}`;
    const v = t(k, c.reason_vars || {});
    if (v !== k) return v;
  }
  return c.reason || '';
}

function manualReason(inst) {
  if (!inst) return '';
  if (inst.manual_code) {
    const k = `install.manual.${inst.manual_code}`;
    const v = t(k);
    if (v !== k) return v;
  }
  return inst.manual_reason || '';
}

function stLabel(s) {
  const k = `st.${s}`;
  const v = t(k);
  return v === k ? s : v;
}

async function loadBackends() {
  const sel = $('backend');
  const health = $('health');
  if (health) health.textContent = t('be.loading');   // US-03: estado visivel na sondagem
  if (sel) sel.disabled = true;
  let data;
  try {
    data = await (await fetch('/api/backends')).json();
  } finally {
    if (sel) sel.disabled = false;
  }
  sel.innerHTML = '';
  for (const b of data.backends) {
    backendsByName[b.name] = b;
    const opt = document.createElement('option');
    opt.value = b.name;
    opt.textContent = `${b.display_name} · ${catLabel(b.license_category)}`
      + ` · ${deviceLabel(b.install && b.install.compute)}`
      + (b.available ? ''
        : ` · ${b.install && b.install.installable ? t('be.installable') : t('be.manual')}`);
    opt.dataset.reason = b.reason || '';
    if (b.name === data.default) opt.selected = true;
    const cat = CAT_INFO[b.license_category];
    opt.style.color = b.available ? (cat ? cat.color : '#2b2a27') : '#a89f92';
    sel.appendChild(opt);
  }
  const cats = {};
  for (const b of data.backends) cats[b.license_category] = (cats[b.license_category] || 0) + 1;
  const parts = [t('be.counts', { n: data.backends.length, def: data.default })];
  parts.push(Object.entries(cats).map(([c, n]) => `${n} ${catLabel(c)}`).join(' · '));
  if (data.preferred && data.preferred !== data.default) parts.push(t('be.preferredMissing', { name: data.preferred }));
  if (data.hidden_count) parts.push(`· ${t('be.hidden', { n: data.hidden_count })}`);
  $('health').textContent = parts.join(' · ');
  const hb = $('hist-backend');
  if (hb && hb.options.length <= 1) {
    for (const b of data.backends) {
      const o = document.createElement('option');
      o.value = b.name;
      o.textContent = b.name;
      hb.appendChild(o);
    }
  }
  updateHint();
}

function updateHint() {
  const sel = $('backend');
  const b = backendsByName[sel.value];
  const btn = $('install-btn');
  if (!b) { $('backend-hint').textContent = ''; btn.classList.add('hidden'); return; }
  const partes = [t('hint.license', { cat: catLabel(b.license_category) })];
  if (b.license) partes.push(b.license);
  const comp = b.install && b.install.compute;
  if (comp) {
    const rot = deviceLabel(comp);
    if (comp.device !== 'cpu' && comp.gpu) {
      partes.push(t('hint.deviceGpu', { label: rot, gpu: comp.gpu }));
    } else {
      const mot = computeReason(comp);
      partes.push(mot
        ? `${t('hint.device', { label: rot })} (${mot.length > 140 ? mot.slice(0, 137) + '…' : mot})`
        : t('hint.device', { label: rot }));
    }
  }
  if (!b.available) {
    if (b.install && b.install.installable) {
      partes.push(t(b.install.total_mb ? 'hint.installableMb' : 'hint.installableLight',
        { label: t('install.kind.' + (b.install.kind || 'auto')), mb: b.install.total_mb }));
      btn.classList.remove('hidden');
      btn.textContent = t('btn.installX', { name: b.display_name.split(' (')[0].split(' — ')[0] });
      $('install-log').classList.add('hidden');
    } else if (b.install && b.install.manual_reason) {
      partes.push(t('hint.manual', { reason: manualReason(b.install) }));
      btn.classList.add('hidden');
    }
  } else {
    btn.classList.add('hidden');
  }
  $('backend-hint').textContent = partes.join(' · ');
}

// ------------------------------------------------------------------ instalar
async function installSelected() {
  const name = $('backend').value;
  const btn = $('install-btn');
  const out = $('install-log');
  btn.disabled = true;
  btn.textContent = t('btn.installing');
  out.classList.remove('hidden');
  out.textContent = t('install.starting');
  try {
    const r = await fetch(`/api/backends/${name}/install`, { method: 'POST' });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || `HTTP ${r.status}`);
    pollInstall(name);
  } catch (err) {
    out.textContent = t('err.prefix', { msg: err.message });
    btn.disabled = false;
    btn.textContent = t('btn.install');
  }
}

function pollInstall(name) {
  const btn = $('install-btn');
  const out = $('install-log');
  const timer = setInterval(async () => {
    try {
      const st = await (await fetch(`/api/backends/${name}/install`)).json();
      out.textContent = st.log || '…';
      if (st.status === 'done' || st.status === 'error') {
        clearInterval(timer);
        btn.disabled = false;
        btn.textContent = t('btn.install');
        const rep = st.report || {};
        out.textContent += (rep.available_after ? `\n${t('install.availableAfter')}`
          : rep.ok ? `\n${t('install.manualStill')}`
          : `\n${t('install.failed')}`);
        await loadBackends();
        $('backend').value = name;
        updateHint();
      }
    } catch (e) {
      clearInterval(timer);
      btn.disabled = false;
    }
  }, 1200);
}

// filtros de estabilizacao vem da API do refinamento (nao ficam hardcoded aqui)
async function loadRefineFilters() {
  const sel = $('refine-filter');
  if (!sel) return;
  try {
    const d = await (await fetch('/api/refine/filters')).json();
    sel.innerHTML = '';
    for (const f of d.filters) {
      const o = document.createElement('option');
      o.value = f.name;
      o.textContent = f.name.replace(/_/g, ' ');
      o.title = (getLang() === 'en' && f.description_en) ? f.description_en : f.description;
      if (f.name === 'one_euro') o.selected = true;
      sel.appendChild(o);
    }
  } catch (e) {
    console.error('nao foi possivel listar os filtros de refino', e);
  }
}

function updateMeshHint() {
  const f = $('mesh').files[0];
  $('mesh-hint').textContent = f
    ? t('hint.meshWith', { name: f.name, mb: (f.size / 1e6).toFixed(1) })
    : t('hint.meshNone');
}

async function checkMesh() {
  const f = $('mesh').files[0];
  if (!f) { alert(t('alert.selectMesh')); return; }
  const btn = $('check-mesh');
  btn.disabled = true; btn.textContent = t('btn.checking');
  try {
    const fd = new FormData();
    fd.append('mesh', f);
    fd.append('lang', getLang());
    const r = await fetch('/api/mesh/inspect', { method: 'POST', body: fd });
    renderMeshInspect(await r.json());
  } catch (err) {
    alert(t('alert.checkFail', { msg: err.message }));
  } finally {
    btn.disabled = false; btn.textContent = t('btn.checkMesh');
  }
}

function renderMeshInspect(j) {
  const el = $('mesh-report');
  el.classList.remove('hidden');
  const r = j.report || {};
  const ok = !!(j.ok && r.compatible && j.attach && j.attach.ok);
  el.classList.toggle('ok', ok);
  el.classList.toggle('warn', !ok);
  const linhas = [`<b>${t('mesh.title')}</b> ${j.filename || '-'} (${((j.bytes || 0) / 1e6).toFixed(1)} MB)`];
  if (j.error) {
    linhas.push(`<ul><li>${j.error}</li></ul>`);
  } else {
    const total = (r.matched || 0) + (r.missing_count || 0);
    linhas.push('<ul>' +
      `<li>${t('mesh.format')}: <b>${r.format}</b></li>` +
      `<li>${t('mesh.matched')}: <b>${r.matched}/${total}</b>` +
      (r.missing_count ? ` — ${t('mesh.missing', { list: (r.missing || []).slice(0, 6).join(', ') })}` : '') + '</li>' +
      `<li>${t('mesh.compatible')}: <b>${r.compatible ? t('mesh.yes') : t('mesh.noU')}</b> · ${t('mesh.attachable')}: <b>${r.attachable ? t('mesh.yes') : t('mesh.no')}</b></li>` +
      (j.attach ? `<li>${t('mesh.attach')} ${j.attach.ok
        ? t('mesh.attachOk', { v: j.attach.vertices, t: j.attach.triangles })
        : j.attach.error}</li>` : '') +
      ((r.messages || []).map((m) => `<li>${m}</li>`).join('')) +
      '</ul>');
  }
  el.innerHTML = linhas.join('');
}

// ------------------------------------------------------------------ jobs
async function submitJob() {
  const file = $('video').files[0];
  if (!file) { alert(t('alert.selectVideo')); return; }
  const params = {
    fps: Number($('fps').value) || 30,
    smoothing: 'oneeuro',
    lift_2d_to_3d: $('lifter').value === '1',
  };
  const mf = $('maxframes').value;
  if (mf) params.max_frames = Number(mf);
  // camada de refino (filtro escolhido + constraints articulares)
  const rf = $('refine-filter') ? $('refine-filter').value : '';
  const rc = $('refine-constraints') ? $('refine-constraints').value === '1' : false;
  const rcol = $('use-collision') ? $('use-collision').checked : true;
  if (rf || rc || rcol) {
    params.refine = {};
    if (rf) params.refine.filters = { apply_to_translation: true, filters: [{ name: rf }] };
    if (rc) params.refine.constraints = true;
    if (rcol) params.refine.collision = true;   // US-07
  }
  if ($('hands') && $('hands').checked) {
    params.hands = { enabled: true, num_hands: 2, mirror: true };
  }

  const fd = new FormData();
  fd.append('video', file);
  fd.append('backend', $('backend').value);
  fd.append('params', JSON.stringify(params));
  fd.append('lang', getLang());
  const mesh = $('mesh').files[0];
  if (mesh) fd.append('mesh', mesh);

  $('submit').disabled = true;
  $('submit').textContent = t('btn.sending');
  try {
    const res = await fetch('/api/jobs', { method: 'POST', body: fd });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    $('job-id').textContent = data.job_id;
    $('job-backend').textContent = $('backend').value;
    const bsel = backendsByName[$('backend').value];
    $('job-license').textContent = bsel ? catLabel(bsel.license_category) : '—';
    $('job-mesh').textContent = data.mesh || t('job.noMesh');
    $('mesh-report').classList.add('hidden');
    resetViewer();
    startPolling(data.job_id);
  } catch (err) {
    alert(t('alert.sendFail', { msg: err.message }));
  } finally {
    $('submit').disabled = false;
    $('submit').textContent = t('btn.process');
  }
}

function startPolling(jobId) {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(() => refreshJob(jobId), 1200);
  refreshJob(jobId);
}

async function refreshJob(jobId) {
  const res = await fetch(`/api/jobs/${jobId}`);
  if (!res.ok) return;
  const job = await res.json();
  $('job-status').textContent = stLabel(job.status);
  $('job-log').textContent = job.log || '';
  if (job.metrics && Object.keys(job.metrics).length) {
    const m = job.metrics;
    $('job-metrics').textContent =
      `${m.frames} frames · score ${(m.mean_score ?? 0).toFixed(2)} · FK ${(100 * (m.fk_max_position_m ?? 0)).toFixed(1)} cm · ${m.elapsed_s}s`;
  }
  renderMeshReport(job.mesh_report);
  if (job.status === 'done') {
    clearInterval(pollTimer); pollTimer = null;
    $('submit').disabled = false;
    openJobResult(job);
    if (activeTab === 'history') loadHistory();
  } else if (job.status === 'error') {
    clearInterval(pollTimer); pollTimer = null;
    $('submit').disabled = false;
  }
}

function renderMeshReport(rep) {
  const el = $('mesh-report');
  if (!rep) { el.classList.add('hidden'); return; }
  const total = rep.matched + rep.missing_count;
  const good = rep.compatible && rep.attachable;
  el.classList.remove('hidden');
  el.classList.toggle('ok', good);
  el.classList.toggle('warn', !good);
  const head = `<b>${t('mesh.skelHead')}</b> ${t('mesh.format')} ${rep.format} · ${t('mesh.matched')} ` +
    `<b>${rep.matched}/${total}</b> · ${t('mesh.compatible')}: <b>${rep.compatible ? t('mesh.yes') : t('mesh.noU')}</b> · ` +
    `${t('mesh.attachedOut')}: <b>${rep.attachable ? t('mesh.yes') : t('mesh.no')}</b>`;
  const msgs = (rep.messages || []).length
    ? '<ul>' + rep.messages.map((m) => `<li>${m}</li>`).join('') + '</ul>' : '';
  el.innerHTML = head + msgs;
}

function enableDownload(id, url) {
  const a = $(id);
  a.href = url;
  a.classList.remove('disabled');
}

// ------------------------------------------------------------------ vídeo
// `video_span_s` (T/fps) e o que o backend gravou; o GLB, porem, termina no
// ultimo keyframe, em (T-1)/fps. Recalcula o instante do ultimo frame sempre
// que o span muda.
function setSpan(span) {
  spanS = Number(span) || 0;
  const n = Math.max(0, Math.round(spanS * vFps));
  lastFrameT = n > 0 ? (n - 1) / vFps : spanS;
}

function attachVideo(url, span) {
  if (!videoEl) return;
  setSpan(span);
  mediaTime = null;
  videoReady = false;
  videoEl.src = url;
  videoEl.load();
  $('video-empty').style.display = 'none';
  $('sync-note').textContent = t('hint.loadingVideo');
}

function maybeStartPlayback() {
  if (!videoReady || !videoEl.src) return;
  if (!viewers.every((v) => v.ready)) return;      // espera os dois 3D
  if (!spanS || spanS > videoEl.duration) { setSpan(videoEl.duration); spanS = videoEl.duration; }
  const full = videoEl.duration - spanS;
  $('sync-note').textContent = full > 0.05
    ? t('hint.span', { a: spanS.toFixed(2), b: videoEl.duration.toFixed(2) })
    : t('hint.timeline');
  videoEl.currentTime = 0;
  mediaTime = 0;
  viewers.forEach((v) => v.setTime(0, true));
  setTimelineUI(0);
  videoEl.play().then(() => { setPlaying(true); pumpVideoFrame(); }).catch(() => setPlaying(false));
}

function setPlaying(v) {
  playing = v;
  $('playpause').textContent = v ? '❚❚' : '▶︎';
}

function togglePlay() {
  if (videoEl && videoEl.src && videoReady) {
    if (videoEl.paused) videoEl.play().then(() => setPlaying(true)).catch(() => {});
    else { videoEl.pause(); setPlaying(false); }
    return;
  }
  setPlaying(!playing);
}

function resetViewer() {
  stopVideoFramePump();
  if (videoEl) { videoEl.pause(); videoEl.removeAttribute('src'); videoEl.load(); }
  mediaTime = null;
  videoReady = false; spanS = 0; lastFrameT = 0; setPlaying(false);
  kpData = null;
  viewers.forEach((v) => { v.clear(); $(v.emptyId).style.display = ''; });
  $('video-empty').style.display = '';
  $('sync-note').textContent = t('hint.timeline');
}

function setTimelineUI(p, force) {
  // Durante o arraste o slider e do USUARIO: escrever nele 60x/s (o que o
  // playback fazia) fazia o arrasto voltar na mao e parecer que o evento
  // nao funcionava. `force` deixa o proprio seek() atualizar o rotulo.
  if (scrubbing && !force) return;
  p = Math.max(0, Math.min(1, p));
  $('timeline').value = String(Math.round(p * 1000));
  const span = (videoReady && spanS > 0) ? lastFrameT : Math.max(...viewers.map((v) => v.clipDuration), 0);
  $('time').textContent = `${(p * span).toFixed(2)} / ${span.toFixed(2)} s`;
}

function seek(value) {
  const p = Number(value) / 1000;
  if (videoReady && spanS > 0) videoEl.currentTime = p * lastFrameT;
  mediaTime = p * lastFrameT;
  viewers.forEach((v) => v.setTime(animTimeFor(mediaTime), true));
  setTimelineUI(p, true);
}

// Agenda o proximo callback de frame apresentado. Cada callback entrega o
// instante do frame que o navegador JA PINTOU, que e o que o olho compara
// com o 3D ao lado. Sem essa API (Safari < 15.4, Firefox < 132) cai para
// `video.currentTime`, que adelanta 1-2 frames.
//
// `pumpVideoFrame` e idempotente: a cadeia se auto-reagenda, e o evento 'play'
// tambem chama aqui — sem a trava, cada 'play' criava OUTRA cadeia que nunca
// morria, e o mediaTime passava a ser reescrito varias vezes por frame.
function pumpVideoFrame() {
  if (!videoEl || rvfcRunning) return;
  if (typeof videoEl.requestVideoFrameCallback !== 'function') return;
  rvfcRunning = true;
  rvfcHandle = videoEl.requestVideoFrameCallback((now, meta) => {
    mediaTime = (meta && typeof meta.mediaTime === 'number') ? meta.mediaTime : videoEl.currentTime;
    pumpVideoFrame();                       // a cadeia continua
  });
}

function stopVideoFramePump() {
  if (rvfcHandle && videoEl && videoEl.cancelVideoFrameCallback) {
    videoEl.cancelVideoFrameCallback(rvfcHandle);
  }
  rvfcHandle = 0; rvfcRunning = false;
}

// 3D indisponivel: degrada com mensagem e mantem o resto da interface viva (US-01)
function showNo3D() {
  for (const id of ['empty-skel', 'empty-mesh']) {
    const el = $(id);
    if (el) { el.style.display = ''; el.textContent = t('viewer.no3d'); }
  }
  document.querySelectorAll('[data-cam]').forEach((b) => { b.disabled = true; });
  for (const id of ['sync-rot', 'bone-labels']) {
    const el = $(id);
    if (el) { el.disabled = true; el.checked = false; }
  }
}

// ------------------------------------------------------------------ wire
window.addEventListener('DOMContentLoaded', () => {
  clock = new THREE.Clock();
  videoEl = $('refvideo');
  videoEl.muted = true;
  videoEl.playsInline = true;
  videoEl.addEventListener('loadeddata', () => { videoReady = true; maybeStartPlayback(); });
  videoEl.addEventListener('pause', () => setPlaying(false));
  videoEl.addEventListener('ended', () => setPlaying(false));
  videoEl.addEventListener('play', () => { setPlaying(true); pumpVideoFrame(); });
  videoEl.addEventListener('seeked', () => { if (videoEl.paused) mediaTime = videoEl.currentTime; });

  try {
    viewers.push(new Viewer('canvas-skel', 'empty-skel', { skeletonOnly: true }));
    viewers.push(new Viewer('canvas-mesh', 'empty-mesh'));
  } catch (err) {
    console.error('falha ao iniciar o 3D (WebGL):', err);
    viewers.length = 0;
    no3d = true;
  }
  applyStatic();
  langSwitcher('lang-switch');
  ligarSyncRot();
  if (no3d) showNo3D();

  switchTab('new');
  loadHistory();
  $('hist-status').addEventListener('change', loadHistory);
  $('hist-backend').addEventListener('change', loadHistory);
  $('hist-refresh').addEventListener('click', loadHistory);
  $('hist-open-last').addEventListener('click', () => {
    if (histJobs.length) openJob(histJobs[0].id); else loadHistory();
  });
  document.querySelectorAll('.tab').forEach((b) =>
    b.addEventListener('click', () => switchTab(b.dataset.tab)));

  loadBackends();
  loadRefineFilters();
  $('backend').addEventListener('change', updateHint);
  $('install-btn').addEventListener('click', installSelected);
  $('mesh').addEventListener('change', updateMeshHint);
  $('check-mesh').addEventListener('click', checkMesh);
  $('submit').addEventListener('click', submitJob);
  $('playpause').addEventListener('click', togglePlay);
  // arrastar a timeline durante a reproduicao: pausa, deixa o arraste
  // acontecer, e volta a tocar quando o usuario solta.
  $('timeline').addEventListener('pointerdown', () => {
    scrubbing = true;
    if (videoEl && !videoEl.paused) { resumeAfterScrub = true; videoEl.pause(); }
  });
  window.addEventListener('pointerup', () => {
    if (!scrubbing) return;
    scrubbing = false;
    if (resumeAfterScrub && videoEl) { resumeAfterScrub = false; videoEl.play().catch(() => {}); }
  });
  $('timeline').addEventListener('input', (e) => seek(e.target.value));
  document.querySelectorAll('[data-cam]').forEach((b) =>
    b.addEventListener('click', () => setCameraAll(b.dataset.cam)));
  $('kp-overlay').addEventListener('change', () => {
    kpOn = $('kp-overlay').checked;
    drawKpOverlay();
  });
  $('bone-labels').addEventListener('change', () => {
    const on = $('bone-labels').checked;
    viewers.forEach((v) => v.setLabels(on));
  });
  window.addEventListener('resize', onResizeAll);
  rendererLoopStart();
});

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

function rendererLoopStart() {
  const loop = () => { tick(); requestAnimationFrame(loop); };
  requestAnimationFrame(loop);
}
