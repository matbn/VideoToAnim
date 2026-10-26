//VideoToAnim — SPA (three.js). Dropdown backends, upload (video + mesh),
//job status and comparison: reference video × skeleton × mesh.
//
//Timing: VIDEO is the only source of time. All 3D viewers are
//positioned from the video time (p = t / span), the loop is manual at the end
//of the processed portion and play only starts when the video and GLB are ready.
//
//The "skeleton" panel shows the rig (bones as lines, via SkeletonHelper) without
//the mesh. If the job does not have a user mesh, the two 3D panels show the
//skeleton.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { t, applyStatic, langSwitcher, getLang } from './i18n.js?v=15';

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
//Synchronization with the video. `mediaTime` is the frame instant REALLY
//presented on the screen (requestVideoFrameCallback); `video.currentTime` and the
//clock, which runs AHEAD of what has already been painted — use it
//to drive 3D produces a constant delay of 1-2 frames (measured: 33-66 ms
//at 30 fps), visible in the side-by-side comparison.
let mediaTime = null;
let rvfcHandle = 0;
let rvfcRunning = false;   //hangs: a string of requestVideoFrameCallback
let scrubbing = false;      //the user is dragging the timeline
let resumeAfterScrub = false;
let vFps = 30, tFps = 30;      //fps of the source video and fps of the exported animation
let lastFrameT = 0;            //instant of the LAST frame processed = (T-1)/vFps
const PELVIS_Y = 0.98;         //reference pelvic height (core/lifter.py)
const SPINE_M = 0.52;          //length of spine used by the lifter
const viewers = [];
let no3d = false;
const backendsByName = {};

//escapes text for HTML attributes (US-06: tooltips with full value)
const attresc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
  .replace(/>/g, '&gt;').replace(/"/g, '&quot;');

//license categories (mirror core/adapter.py LICENSE_CATEGORIES)
const CAT_INFO = {
  livre: { label: 'livre', color: '#4f6d5c' },
  nao_comercial: { label: 'não comercial', color: '#b06a48' },
  licenca_a_parte: { label: 'separate license', color: '#8a7a4a' },
};
const catLabel = (c) => {
  const k = `cat.${c}`;
  const v = t(k);
  return v === k ? (CAT_INFO[c] || { label: c }).label : v;
};

//------------------------------------------------------------------ Viewer
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
        //hides the geometry and draws the rig as lines between the bones
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
    }, undefined, (err) => console.error('error loading GLB', this.hostId, err));
  }

  _criarLabels() {
    this.labelSprites = [];
    const filter = /(Index|Middle|Ring|Pinky|Thumb)\d$/;
    this.model.traverse((o) => {
      if (o.isBone && !filter.test(o.name)) {
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

  //`force` ignores the shift guard (used in loop and seek).
  //Re-pinning the mixer 60x per second is wasted and reintroduces jitter: so
  //rewrites when the instant actually changes.
  //
  //We use `action.time` + `mixer.update(0)` and NOT `mixer.setTime()`:
  //`setTime` resets the internal time of the action to 0 and re-evaluates everything
  //from the stopped state. In a recently processed job, this left the pose
  //stuck on the first frame — the video played and the 3D only started when the
  //cycle would go around and `setTime(0, true)` would rewrite the instant. To write
  //`action.time` and asking `update(0)` applies the interpolated value at the time.
  setTime(t, force) {
    if (!this.mixer || !(this.clipDuration > 0)) return;
    const at = Math.max(0, Math.min(t, this.clipDuration));
    if (!force && this._lastT !== undefined && Math.abs(at - this._lastT) < 1e-4) return;
    this._lastT = at;
    if (this.action) {
      this.action.time = Math.min(at, Math.max(0, this.clipDuration - 1e-4));
      this.mixer.update(0);
    } else {
      this.mixer.setTime(at);
    }
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

  //Camera that reframes the 3D as the reference video is framed.
  //With the camera at (camX, camY, d) looking at -Z, a world point Y falls into
  //height fraction f = 1/2 - (Y - camY) / (2 d tan(fov/2)). The lifter put the
  //hip of the first frame in Y = 0.98 m and the torso with 0.52 m, so
  //equating the fractions of the video:
  //d = 0.52 / (2 * (trunk_px / video_height) * tan(fov/2))
  //camY = 0.98 - d tan(fov/2) * (1 - 2 py_hip / height_video)
  //The fractions match exactly (proven in tests/test_preview_sync.py).
  setCameraMatchVideo() {
    const cal = typeof videoCalibration === 'function' ? videoCalibration() : null;
    if (!cal) {                       //no video loaded: drops into perspective
      this.camera.position.set(...CAMERAS.persp);
      this.controls.target.set(0, 0.95, 0);
      this.controls.update();
      return;
    }
    const half = Math.tan((this.camera.fov * Math.PI / 180) / 2);
    const fv = cal.torsoPx / cal.videoH;               //trunk as a fraction of height
    const d = SPINE_M / (2 * Math.max(fv, 1e-3) * half);
    const camY = PELVIS_Y - d * half * (1 - 2 * cal.hipPy / cal.videoH);
    //X axis: the character may be off center of the frame
    const s = SPINE_M / Math.max(cal.torsoPx, 1e-3);   //m per px of the video
    const xWorld = (cal.hipPx - cal.videoW / 2) * s;   //where the lifter put the hip
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

//------------------------------------------- debug: 2D skeleton overlay
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

//------------------------------- hands detected (hand tracking) on ​​the video
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
  desenha(hf.left, '#3d6fa8');    //left hand
  desenha(hf.right, '#b06a48');   //right hand
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

//------------------------------------------------------------------ loop
//Frame the 3D like the video: use the 2D joints of the first frame (mid-hip and
//mid-shoulder) + the video size, which is exactly the same geometry as the
//lifter used to lift the pose. Without this, the camera remains in a fixed place and the
//Side-by-side comparison has apparent displacement/scale.
function videoCalibration() {
  if (!kpData || !kpData.frames || !kpData.frames[0] || !kpData.width || !kpData.height) return null;
  const f = kpData.frames[0];
  if (!f[5] || !f[6] || !f[11] || !f[12]) return null;      //shoulders/hips
  const mid = (a, b) => [(f[a][0] + f[b][0]) / 2, (f[a][1] + f[b][1]) / 2];
  const hip = mid(11, 12);
  const sho = mid(5, 6);
  const torsoPx = Math.hypot(sho[0] - hip[0], sho[1] - hip[1]);
  if (!(torsoPx > 1)) return null;
  return { hipPx: hip[0], hipPy: hip[1], torsoPx, videoW: kpData.width, videoH: kpData.height };
}

//Video instant -> animation instant. Frame i of the video corresponds to
//frame i of the animation (a processed frame -> a keyframe), then
//animTime = (mediaTime * vFps) / tFps.
//Using normalized time (mediaTime/spanS * clipDuration) compresses the
//animation: the GLB ends at the LAST keyframe, (T-1)/fps, and not at T/fps — the
//error grew from 0 to ~1 frame at the end of the clip.
function animTimeFor(mediaT) {
  if (!(vFps > 0) || !(tFps > 0)) return mediaT;
  return (mediaT * vFps) / tFps;
}

function tick() {
  const dt = clock.getDelta();
  const drivingByVideo = videoReady && spanS > 0 && lastFrameT > 0;

  if (drivingByVideo && videoEl && !videoEl.paused) {
    //The loop is decided by the VIDEO CLOCK (currentTime), never by
    //requestVideoFrameCallback mediaTime. mediaTime only advances when the
    //browser PRESENTS a new frame: using it here created a crash —
    //when wrapping, the search for 0 took a few ms until frame 0 was painted,
    //mediaTime would get stuck at the last moment and the wrap would trigger again
    //EACH rAF (~60 searches/s). The video shook without moving in place and the 3D
    //it was frozen in the first frame. currentTime actually zeroes in the search.
    if (videoEl.currentTime >= lastFrameT) {
      videoEl.currentTime = 0;                    //manual loop (video + 3D together)
      mediaTime = null;                           //discard the old instant
      viewers.forEach((v) => v.setTime(0, true));
      setTimelineUI(0, true);
    } else {
      //the pose uses the frame ALREADY PRESENTED; currentTime and only the reserve when the
      //browser does not have requestVideoFrameCallback (or has not painted anything yet)
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
//Reapply the active camera after kp2d arrives: the "video" preset can only be
//calculated after the 2D joints (the button click may have come before).
function reapplyCamera() { if (activeCam === 'video') viewers.forEach((v) => v.setCamera('video')); }


//------------------------------------------------------------------ tabs
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

//open a job from the history in the viewer (without reprocessing)
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
  //the refinement editor automatically opens the job that is open here (default tab)
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

//-------------------------------------------------------------- backends
//short execution device label (comes from install.compute)
function deviceLabel(c) {
  if (!c || !c.device || c.device === 'cpu') return 'CPU';
  if (c.device === 'cuda') return 'GPU CUDA';
  if (c.device === 'directml') return 'GPU DirectML';
  if (c.device === 'mps') return 'GPU MPS';
  return `GPU ${String(c.device).toUpperCase()}`;
}

//reason for the device in PT/EN (the server sends code + variables)
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
  if (health) health.textContent = t('be.loading');   //US-03: visible status in the survey
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

//------------------------------------------------------------------ install
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

//stabilization filters come from the refinement API (not hardcoded here)
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
    console.error('could not list refine filters', e);
  }
}

function updateMeshHint() {
  const f = $('mesh').files[0];
  $('mesh-hint').textContent = f
    ? t('hint.meshWith', { name: f.name, mb: (f.size / 1e6).toFixed(1) })
    : t('hint.meshNone');
}

//The `<input type=file>` has the button and the text "no file chosen"
//DESIGNED BY THE BROWSER, in the browser language. How the input looks
//transparent (see `.file-pick` in CSS), we write the chosen name in the
//label next to it — and it returns to neutral text when the field empties
//(the browser's "clear" button triggers `change` with empty `files`).
function updateFileName(inputId, labelId) {
  const f = $(inputId) && $(inputId).files[0];
  const el = $(labelId);
  if (!el) return;
  el.textContent = f ? f.name : t('file.none');
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

//------------------------------------------------------------------ jobs
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
  //refinement layer (chosen filter + joint constraints)
  const rf = $('refine-filter') ? $('refine-filter').value : '';
  const rc = $('refine-constraints') ? $('refine-constraints').value === '1' : false;
  const rcol = $('use-collision') ? $('use-collision').checked : true;
  if (rf || rc || rcol) {
    params.refine = {};
    if (rf) params.refine.filters = { apply_to_translation: true, filters: [{ name: rf }] };
    if (rc) params.refine.constraints = true;
    if (rcol) params.refine.collision = true;   //US-07
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

//------------------------------------------------------------------ video
//`video_span_s` (T/fps) and what the backend recorded; the GLB, however, ends at
//last keyframe, in (T-1)/fps. Recalculates the instant of the last frame every time
//that the span changes.
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
  if (!viewers.every((v) => v.ready)) return;      //wait for both 3D
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
  //While dragging the slider and USER: write to it 60x/s (which
  //playback did) made the drag return in hand and appear as if the event
  //it didn't work. `force` lets seek() itself update the label.
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

//Schedules the next presented frame callback. Each callback delivers the
//instant of the frame that the browser has ALREADY PAINTED, which is what the eye compares
//with 3D on the side. Without this API (Safari < 15.4, Firefox < 132) it drops to
//`video.currentTime`, which takes 1-2 frames.
//
//`pumpVideoFrame` is idempotent: the chain reschedules itself, and the 'play' event
//also called here — without the lock, each 'play' created ANOTHER chain that never
//died, and mediaTime started to be rewritten several times per frame.
function pumpVideoFrame() {
  if (!videoEl || rvfcRunning) return;
  if (typeof videoEl.requestVideoFrameCallback !== 'function') return;
  rvfcRunning = true;
  rvfcHandle = videoEl.requestVideoFrameCallback((now, meta) => {
    mediaTime = (meta && typeof meta.mediaTime === 'number') ? meta.mediaTime : videoEl.currentTime;
    pumpVideoFrame();                       //the chain continues
  });
}

function stopVideoFramePump() {
  if (rvfcHandle && videoEl && videoEl.cancelVideoFrameCallback) {
    videoEl.cancelVideoFrameCallback(rvfcHandle);
  }
  rvfcHandle = 0; rvfcRunning = false;
}

//3D unavailable: degrades with message and keeps the rest of the interface alive (US-01)
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

//------------------------------------------------------------------ wire
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
    console.error('failed to start 3D (WebGL):', err);
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
  //the name of the chosen file, in the label next to the invisible input
  $('video').addEventListener('change', () => updateFileName('video', 'video-name'));
  $('mesh').addEventListener('change', () => updateFileName('mesh', 'mesh-name'));
  $('check-mesh').addEventListener('click', checkMesh);
  $('submit').addEventListener('click', submitJob);
  $('playpause').addEventListener('click', togglePlay);
  //drag the timeline during playback: pause, stop dragging
  //happens, and plays again when the user releases it.
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

//------------------------------------------------------- sync rotation between panels
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
