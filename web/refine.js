//VideoToAnim — refinement editor (bone editor + filters + constraints).
//Redesign/Refinement mode: same visual language as the pipeline, new page.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { t, applyStatic, langSwitcher, getLang } from './i18n.js?v=15';
import { CurveEditor, CHANNELS, KEY_HALF } from './curves.js?v=11';

const $ = (id) => document.getElementById(id);
const CAMERAS = { persp: [2.2, 1.6, 2.6], front: [0, 1.1, 3.2], side: [3.4, 1.1, 0] };
const PELVIS_Y = 0.98;   //reference pelvic height (core/lifter.py)
const SPINE_M = 0.52;    //length of spine used by the lifter

//3D framing the same as the reference video. Same geometry as the
//lifter used to lift the pose, so the frame fractions match exactly.
//With the camera at (camX, camY, d) looking at -Z, a world point Y falls into
//height fraction f = 1/2 - (Y - camY) / (2 d tan(fov/2)):
//d = SPINE_M / (2 * (stem_px / video_height) * tan(fov/2))
//camY = PELVIS_Y - d tan(fov/2) * (1 - 2 py_hip / height_video)
//The fractions match exactly (proven in tests/test_preview_sync.py).
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

function cameraForVideo(camera) {
  const cal = videoCalibration();
  if (!cal) return null;
  const half = Math.tan((camera.fov * Math.PI / 180) / 2);
  const fv = cal.torsoPx / cal.videoH;                    //trunk as a fraction of height
  const d = SPINE_M / (2 * Math.max(fv, 1e-3) * half);
  const camY = PELVIS_Y - d * half * (1 - 2 * cal.hipPy / cal.videoH);
  //X axis: the character may be off center of the frame
  const s = SPINE_M / Math.max(cal.torsoPx, 1e-3);         //m per px of the video
  const xWorld = (cal.hipPx - cal.videoW / 2) * s;         //where the lifter put the hip
  const camX = xWorld - (2 * (cal.hipPx / cal.videoW) - 1) * d * half * camera.aspect;
  return { x: camX, y: camY, z: d };
}

let jobId = null, clip = null, currentFrame = 0, playing = false, spanS = 0;
const viewers = [];
let no3d = false;
let activeCam = 'persp';
//curve editor: clip pose drawn as rotation curves by
//channel, with editable keyframes directly on the graph. It is BELOW the previous one,
//in the refine editor (replaces sliders).
let curveEditor = null;    //CurveEditor instance
let curveBone = '';        //bone selected in the curve editor
let curveData = null;      //last payload of /curves
let curveSelectFrame = null;   //keyframe to select after reload
//The "video" preset can only be calculated after kp2d arrives; reapply to
//camera activates when it loads (or when the panel changes size).
function reapplyCamera() { if (activeCam === 'video') viewers.forEach((v) => v.setCamera('video')); }

function log(msg) {
  const el = $('log');
  el.textContent = `${msg}\n` + el.textContent.split('\n').slice(0, 40).join('\n');
}

//------------------------------------------------------------------ viewer
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
    //The 3D panel is flex (the grid line decides the height), so the box
    //can change WITHOUT the window's `resize` event: change tabs, open the
    //constraints filter, resize the column. three.js only measures
    //when someone called `resize()`, the canvas was stretched.
    if (typeof ResizeObserver !== 'undefined') {
      this._ro = new ResizeObserver(() => this.resize());
      this._ro.observe(wrap);
    }
    this.renderer.setAnimationLoop(() => this.tick());
  }
  resize() {
    const host = $(this.host);
    //while hidden the box measures 0: `setSize(0, 0)` would stop the renderer
    //looks invalid, and there is nothing to fix yet.
    if (!host) return;
    const w = host.clientWidth, h = host.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }
  load(url) {
    //download the previous clip JA — the old one cannot stay in the scene
    this.clear();
    const token = this._loadToken = (this._loadToken || 0) + 1;
    const empty = $(this.empty);
    if (empty) { empty.style.display = ''; empty.textContent = t('ref.loading'); }
    return new Promise((resolve) => {
      new GLTFLoader().load(url, (gltf) => {
        if (token !== this._loadToken) { resolve(this); return; }   //changed clip
        this.model = gltf.scene;
        this.scene.add(this.model);
        this._criarLabels();
        if (this.skeletonOnly) {
          this.model.traverse((o) => { if (o.isMesh || o.isSkinnedMesh) o.visible = false; });
          const helper = new THREE.SkeletonHelper(this.model);
          helper.material.color = new THREE.Color(0x4f6d5c);
          helper.material.depthTest = false;
          this.scene.add(helper);
          this.helper = helper;                  //saved: clear() needs to remove
        }
        if (gltf.animations && gltf.animations.length) {
          this.mixer = new THREE.AnimationMixer(this.model);
          const a = this.mixer.clipAction(gltf.animations[0]);
          this.clipDuration = a.getClip().duration;
          a.paused = true;                       //manual scrub via setFrame
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
    const filter = /(Index|Middle|Ring|Pinky|Thumb)\d$/;
    const tirarPrefixo = (n) => n.replace(/^(?:mixamorig|mixamo)[:_]?/i, '');
    this.model.traverse((o) => {
      if (!o.isBone) return;
      this._bonesByName[tirarPrefixo(o.name)] = o;
      if (!filter.test(o.name)) {
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
    //action scrub PAUSED: setTime() hangs at time 0 (effective timeScale = 0
    //when paused — played with three.js 0.169). The right way and
    //action.time + mixer.update(0).
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
    if (n === 'video' && !v) {                 //without kp2d: falls into perspective
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

//------------------------------------------------------------------ state
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
  //priority: ?job= from URL -> job open in the main tab (default tab) -> most recent
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
    viewers.forEach((v) => v.clear());           //the old clip leaves the scene
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
  currentFrame = 0;
  fillBoneSelects();
  await refreshPreview();
  await syncSliders();
  await loadCurves();
  refreshTransplantTarget();
  try { localStorage.setItem('v2m:lastJob', id); } catch (e) { /* sem storage */ }
  //preserves the other URL parameters (the tab, for example)
  try {
    const u = new URL(location.href);
    u.searchParams.set('job', id);
    history.replaceState(null, '', u.toString());
  } catch (e) { /* sem history: o job fica so na sessao */ }
  log(t('ref.clipLoadedLog', { id, n: clip.num_frames, b: clip.animated_bones.length }));
}


//------------------------------------------------------------------ tabs
//Two tabs in the editor: the real refinement (bone, filters, constraints) and the
//A/B mix. The time bar and log are OUTSIDE the tabs, so the playhead
//and the reference video serve both — in the mix tab, the A/B panels
//accompany the same frame.
function switchRefineTab(tab) {
  for (const b of document.querySelectorAll('.refine-tabs .tab')) {
    b.classList.toggle('active', b.dataset.tab === tab);
  }
  const mr = $('pane-refine'), mm = $('pane-merge');
  if (mr) mr.hidden = tab !== 'refine';
  if (mm) mm.hidden = tab !== 'merge';
  //the tab goes to the URL: a direct link to the mixing tab (and the way to
  //send someone to the editor without explaining anything)
  try {
    const u = new URL(location.href);
    u.searchParams.set('tab', tab);
    history.replaceState(null, '', u.toString());
  } catch (e) { /* sem history: a aba funciona igual */ }
  //the 3D needs to be resized when it becomes visible again (the canvas averages 0 when hidden)
  requestAnimationFrame(() => {
    viewers.forEach((v) => v.resize());
    [cmpA, cmpB].forEach((v) => { if (v) v.resize(); });
    reapplyCamera();
    if (cmpA) syncCompareCamera(viewers[0], cmpA);
    //the curves canvas also measures 0 while the flap is hidden
    if (curveEditor) curveEditor.resize();
    drawKpOverlay();
  });
}

//the tab requested in the URL (?tab=merge) — the tabs are "Refino" and "Mixture A/B"
function tabFromUrl() {
  const want = new URLSearchParams(location.search).get('tab');
  const tabs = Array.from(document.querySelectorAll('.refine-tabs .tab')).map((b) => b.dataset.tab);
  return (want && tabs.includes(want)) ? want : 'refine';
}

//---------------------------------------------- mix of parts between jobs
let trParts = [];      //[{id,label,bones}] coming from the server
let cmpA = null;       //source panel
let cmpB = null;       //destination panel
let cmpSourceJob = ''; //job loaded in cmpA

//The two A/B panels divide the camera with the main preview, to give
//compare the same member by rotating the camera just once.
function syncCompareCamera(origem, destino) {
  if (!origem || !destino) return;
  destino.camera.position.copy(origem.camera.position);
  destino.controls.target.copy(origem.controls.target);
  destino.controls.update();
}

function cmpFrames() {
  const t = currentFrame;
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
  const f = currentFrame;
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
  //pure text (the destination is not editable) — that's why textContent, not value
  if (el) el.textContent = jobId || '—';
  //loads both panels as soon as there is a destination (and an origin chosen)
  loadCompareClips(true);
}

//The current frame is the only source of truth (there is no longer a frame slider in
//panel: the footer timeline and the curve editor rule it).
async function syncSliders() {
  if (!jobId || !clip) return;
  const t = currentFrame;
  //Reference video accompanies the frame (scrub and playback).
  //The frame t of the animation corresponds to the frame t of the VIDEO, and the frame t of the video
  //was presented in t/vFps SECONDS. The inverse of `animTimeFor` from app.js.
  //The guard is HALF frame: below that there is no re-search, above that the video
  //it would be up to half a frame behind the skeleton.
  const vid = $('refvideo');
  if (vid && clip.fps > 0 && vid.readyState >= 2) {
    const vFps = (kpData && kpData.fps) || clip.fps;
    //frame t -> second t/vFps. (A `t * vFps / clip.fps` here would send the video
    //for the second t: in a clip of 5.57 s it came to an end in frame 6.)
    const alvo = Math.min(t / vFps, Math.max(vid.duration - 0.001, 0));
    if (Math.abs(vid.currentTime - alvo) > 0.5 / vFps) vid.currentTime = alvo;
  }
  //the pose in the scene comes BEFORE the fetch: playback does not depend on the network
  viewers.forEach((v) => v.setFrame(t, clip.fps));
  //the curve editor follows the same frame
  if (curveEditor) curveEditor.setPlayhead(t);
  drawKpOverlay();
  $('time').textContent = `${(t / clip.fps).toFixed(2)} / ${(clip.num_frames / clip.fps).toFixed(2)} s`;
  const tf = $('tl-frame');
  if (tf && document.activeElement !== tf) tf.value = String(t);
  cmpFrames();
}

async function refreshPreview() {
  if (!jobId) return;
  const url = `/api/refine/animation/${jobId}/glb?t=${Date.now()}`;
  await Promise.all(viewers.map((v) => v.load(url)));
  await syncSliders();                            //reapply the frame to the new model
}


//------------------------------------------------------------------ curves
//The reference (local/global) of the angles: the same for the curve editor and
//for the rest of the page.
function editSpace() {
  const v = $('space') ? $('space').value : 'local';
  return (v === 'global') ? 'global' : 'local';
}

async function undoRedo(kind) {
  if (!jobId) return;
  try {
    const r = await api(`/api/refine/animation/${jobId}/${kind}`, { method: 'POST' });
    log(t('ref.undoRedoLog', { kind, n: r.history_len }));
    $('info-session').textContent = t('ref.editsCount', { n: r.history_len });
    await refreshPreview();
    await loadCurves();
  } catch (e) { log(`${t('err.upper')}: ${e.message}`); }
}

async function resetEdits() {
  if (!jobId) return;
  await api(`/api/refine/animation/${jobId}/reset`, { method: 'POST' });
  log(t('ref.clearedLog'));
  $('info-session').textContent = t('ref.editsCount', { n: 0 });
  await refreshPreview();
  await loadCurves();
}

//------------------------------------------------------------------ curves
//The curve editor shows the rotation of the bone channel by channel (X, Y, Z)
//along the clip and lets you create, move and delete keyframes directly on the graph.
//
//The keyframes of this bone ARE the edits of history: what the graph
//draws and rebakes for real (the same math as the server, core/refine/
//boneedit.py) and what the drag predicts and what goes to disk.
function curveChannelValues(frame) {
  if (!curveData || !curveData.channels) return [0, 0, 0];
  const n = curveData.num_frames || 1;
  const f = Math.max(0, Math.min(n - 1, Math.round(frame)));
  return CHANNELS.map((c) => (curveData.channels[c][f] ?? 0));
}

//the two bone selectors ("Refine" tab and "Curves" tab) are the same bone
function fillBoneSelects() {
  const bones = (clip && clip.animated_bones) || [];
  const sel = $('curve-bone');
  if (sel) {
    sel.innerHTML = '';
    for (const b of bones) {
      const o = document.createElement('option');
      o.value = b;
      o.textContent = b.replace('mixamorig:', '');
      sel.appendChild(o);
    }
  }
  if (!curveBone || !bones.includes(curveBone)) curveBone = bones[0] || '';
  if (sel && curveBone) sel.value = curveBone;
  if (curveEditor) curveEditor.boneLabel = String(curveBone || '').replace('mixamorig:', '');
}

function setCurveBone(name) {
  curveBone = name;
  const cb = $('curve-bone');
  if (cb) cb.value = name;
  //the name of the bone appears in the timeline keyframes line
  if (curveEditor) curveEditor.boneLabel = String(name || '').replace('mixamorig:', '');
  loadCurves();
}

//the reference (local/global) of the curve editor angles
function setEditSpace(space) {
  const v = (space === 'global') ? 'global' : 'local';
  const el = $('space');
  if (el) el.value = v;
  return v;
}

async function loadCurves() {
  if (!curveEditor) return;
  if (!jobId || !clip || !curveBone) {
    curveData = null;
    curveEditor.setData(null);
      renderCurveKeys();
    return;
  }
  try {
    const d = await api(`/api/refine/animation/${jobId}/curves`
      + `?bone=${encodeURIComponent(curveBone)}&space=${editSpace()}`);
    curveData = d;
    curveEditor.setData(d);
    //the newly created keyframe (or the one that the user had selected) is
    //checked: and what the properties panel shows
    if (curveSelectFrame !== null) {
      const i = curveEditor.keys.findIndex((k) => k.frame === curveSelectFrame);
      if (i >= 0) { curveEditor.sel = i; curveEditor.draw(); }
      curveSelectFrame = null;
    }
    curveEditor.setPlayhead(currentFrame);
    renderCurveKeys();
    syncCurveProps();
  } catch (e) {
    curveData = null;
    curveEditor.setData(null);
    log(`${t('err.upper')}: ${e.message}`);
  }
}

//bone keyframe list
function renderCurveKeys() {
  const box = $('curve-keys');
  if (!box) return;
  box.innerHTML = '';
  const keys = (curveEditor && curveEditor.keys) || [];
  if (!keys.length) {
    const p = document.createElement('p');
    p.className = 'hint';
    p.textContent = t('curve.noKeys');
    box.appendChild(p);
    return;
  }
  const head = document.createElement('div');
  head.className = 'curve-key-head';
  head.innerHTML = `<span>${t('curve.keyFrame')}</span><span>X</span><span>Y</span><span>Z</span><span></span>`;
  box.appendChild(head);
  keys.forEach((k, i) => {
    const row = document.createElement('div');
    row.className = 'curve-key' + (curveEditor && curveEditor.sel === i ? ' sel' : '');
    row.innerHTML = `<b>${k.frame}</b>`
      + `<span>${k.value[0].toFixed(1)}</span>`
      + `<span>${k.value[1].toFixed(1)}</span>`
      + `<span>${k.value[2].toFixed(1)}</span>`;
    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'ghost';
    del.textContent = '×';
    del.title = t('btn.delKey');
    del.addEventListener('click', (ev) => { ev.stopPropagation(); deleteKeyframe(i); });
    row.appendChild(del);
    row.addEventListener('click', () => {
      if (curveEditor) curveEditor.select(i);
      goToFrame(k.frame);
    });
    box.appendChild(row);
  });
}

//properties panel of the selected keyframe
function syncCurveProps() {
  const keys = (curveEditor && curveEditor.keys) || [];
  const k = curveEditor && curveEditor.sel >= 0 ? keys[curveEditor.sel] : null;
  const ids = ['curve-frame', 'curve-vx', 'curve-vy', 'curve-vz',
               'curve-interp-in', 'curve-interp-out', 'curve-note',
               'curve-start', 'curve-end'];
  for (const id of ids) {
    const el = $(id);
    if (!el) continue;
    el.disabled = !k;
  }
  if (!k) {
    const hint = $('curve-sel-hint');
    if (hint) hint.textContent = t('hint.curveSel');
    const hh = $('curve-handle-hint');
    if (hh) hh.textContent = '—';
    return;
  }
  $('curve-frame').value = String(k.frame);
  $('curve-vx').value = k.value[0].toFixed(2);
  $('curve-vy').value = k.value[1].toFixed(2);
  $('curve-vz').value = k.value[2].toFixed(2);
  //the effect range: when the user did not move, the window comes from
  //neighbors and fields show the effective range (gray, editable)
  const wins = (curveEditor.windowsFor ? curveEditor.windowsFor() : null);
  const win = wins ? wins[curveEditor.sel] : null;
  $('curve-start').value = String(k.explicit && k.start !== null ? k.start : (win ? win[0] : k.frame));
  $('curve-end').value = String(k.explicit && k.end !== null ? k.end : (win ? win[1] : k.frame));
  $('curve-start').title = k.explicit ? '' : t('hint.curveRangeAuto');
  $('curve-end').title = k.explicit ? '' : t('hint.curveRangeAuto');
  $('curve-interp-in').value = k.interp_in || 'smooth';
  $('curve-interp-out').value = k.interp_out || k.interp_in || 'smooth';
  $('curve-note').value = k.note || '';
  const hh = $('curve-handle-hint');
  if (hh) {
    hh.textContent = (k.interp_in === 'bezier' || k.interp_out === 'bezier')
      ? t('hint.curveHandleOn') : t('hint.curveHandleOff');
  }
  const hint = $('curve-sel-hint');
  if (hint) hint.textContent = t('hint.curveSelOn', { f: k.frame });
}

//writes the set of keyframes to the server
async function commitCurves(keys) {
  if (!jobId || !curveBone) return;
  //the keyframe that was selected remains selected after reload
  const sel = curveEditor ? curveEditor.sel : -1;
  if (sel >= 0 && keys[sel]) curveSelectFrame = keys[sel].frame;
  const body = {
    bone: curveBone,
    space: editSpace(),
    half: KEY_HALF,
    keyframes: keys.map((k) => ({
      frame: k.frame,
      rotation_euler_deg: k.value.map((v) => Number(Number(v).toFixed(4))),
      //the entrance (how the curve arrives) and the exit (how it leaves)
      interp_in: k.interp_in || 'smooth',
      interp_out: k.interp_out || k.interp_in || 'smooth',
      note: k.note || '',
      //the bezier handles (null = automatic)
      handle_in: k.handle_in ? k.handle_in.map((v) => Number(Number(v).toFixed(4))) : null,
      handle_out: k.handle_out ? k.handle_out.map((v) => Number(Number(v).toFixed(4))) : null,
      handle_channel: Number(k.handle_channel ?? 1),
      //the keyframe effect range (null = the window leaves its neighbors)
      start: (k.start === null || k.start === undefined) ? null : Number(k.start),
      end: (k.end === null || k.end === undefined) ? null : Number(k.end),
    })),
  };
  try {
    const r = await api(`/api/refine/animation/${jobId}/keyframes`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    log(t('ref.keysApplied', {
      bone: curveBone.replace('mixamorig:', ''), n: r.report.keyframes,
    }));
    $('info-session').textContent = t('ref.editsCount', { n: r.history_len });
    await refreshPreview();     //the 3D clip already comes with the keyframes
    await loadCurves();         //the server recalculates the windows
  } catch (e) {
    log(`${t('err.upper')}: ${e.message}`);
    await loadCurves();
    //the previous one may have remained in the drag pose: go back to the pose in the clip
    syncSliders();
  }
}

//enter the value of a channel (the channel chip on the graph)
function setChannelValue(channel, value) {
  if (!curveEditor || curveEditor.sel < 0 || !Number.isFinite(value)) return;
  const keys = cloneKeys();
  const k = keys[curveEditor.sel];
  if (!k) return;
  k.value = k.value.slice();
  k.value[channel] = value;
  commitCurves(keys);
}

//return a channel to the ORIGINAL value (the base curve, before the keyframes)
function resetChannelValue(channel) {
  if (!curveEditor || curveEditor.sel < 0) return;
  const base = curveEditor.baseValueAt(curveEditor.keys[curveEditor.sel].frame, channel);
  if (base === null || base === undefined) return;
  setChannelValue(channel, base);
}

function cloneKeys() {
  return ((curveEditor && curveEditor.keys) || []).map((k) => ({
    frame: k.frame, value: k.value.slice(), note: k.note || '',
    interp_in: k.interp_in, interp_out: k.interp_out,
    handle_in: k.handle_in ? k.handle_in.slice() : null,
    handle_out: k.handle_out ? k.handle_out.slice() : null,
    handle_channel: k.handle_channel,
    //the effect range chosen by the user (null = comes from the neighbors)
    start: k.explicit ? k.start : null,
    end: k.explicit ? k.end : null,
  }));
}

//inserts a keyframe into the given frame (with the value of the curve in that frame: the
//curve does not "jump", the keyframe starts to control from there)
function insertKeyframe(frame) {
  if (!curveEditor) return;
  const total = (curveData && curveData.num_frames) || (clip && clip.num_frames) || 0;
  const f = Math.max(0, Math.min(total - 1, Math.round(frame)));
  const keys = cloneKeys();
  const jaTem = keys.findIndex((k) => k.frame === f);
  if (jaTem >= 0) {
    curveEditor.select(jaTem);
    goToFrame(f);
    return;
  }
  const interpIn = ($('curve-interp-in') && $('curve-interp-in').value) || 'smooth';
  const interpOut = ($('curve-interp-out') && $('curve-interp-out').value) || interpIn;
  keys.push({
    frame: f, value: curveChannelValues(f), note: '',
    interp_in: interpIn, interp_out: interpOut,
    handle_in: null, handle_out: null, handle_channel: 1,
  });
  keys.sort((a, b) => a.frame - b.frame);
  curveSelectFrame = f;      //the new keyframe is selected
  commitCurves(keys);
}

function deleteKeyframe(index) {
  if (!curveEditor) return;
  const keys = cloneKeys();
  if (index < 0 || index >= keys.length) return;
  keys.splice(index, 1);
  if (curveEditor) curveEditor.sel = -1;
  commitCurves(keys);
}

//takes the timeline (and preview) to a frame
function goToFrame(frame) {
  if (!clip) return;
  const f = Math.max(0, Math.min(clip.num_frames - 1, Math.round(frame)));
  currentFrame = f;
  syncSliders();
}

//the bone keyframe closest to the frame (for the navigation buttons)
function keyframeNear(frame, dir) {
  const keys = (curveEditor && curveEditor.keys) || [];
  if (!keys.length) return null;
  const frames = keys.map((k) => k.frame).sort((a, b) => a - b);
  if (dir < 0) {
    const antes = frames.filter((f) => f < frame - 1e-6);
    return antes.length ? antes[antes.length - 1] : null;
  }
  const depois = frames.filter((f) => f > frame + 1e-6);
  return depois.length ? depois[0] : null;
}

//------------------------------------------- real-time preview of drag
//When dragging a keyframe, the curve changes with each pixel: the pose of the
//bone in the current frame changes along with it, and the 3D preview has to keep up — without
//wait for the server. The account is the same as the server (the curve editor
//reproduces the rebake), only here the quaternion goes straight to the bone of the
//scene, as three.js guards.
function eulerXYZDegToQuat(ex, ey, ez) {
  //same server order (euler_xyz_deg_to_quat): qx * ​​qy * qz
  const x = THREE.MathUtils.degToRad(ex) / 2, y = THREE.MathUtils.degToRad(ey) / 2,
        z = THREE.MathUtils.degToRad(ez) / 2;
  const qx = new THREE.Quaternion(Math.sin(x), 0, 0, Math.cos(x));
  const qy = new THREE.Quaternion(0, Math.sin(y), 0, Math.cos(y));
  const qz = new THREE.Quaternion(0, 0, Math.sin(z), Math.cos(z));
  return qx.multiply(qy).multiply(qz).normalize();
}

//WORLD rotation of the father, traveling ONLY through the hierarchy of bones. Climb by
//.parent would take the no/group of the scene, which has its own transformation and
//would contaminate the account.
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

function previewPose(viewer, nome, eulerDeg, space) {
  if (!viewer || !viewer.getBone) return;
  const b = viewer.getBone(nome);
  if (!b) return;
  const q = eulerXYZDegToQuat(eulerDeg[0], eulerDeg[1], eulerDeg[2]);
  //in `global` the angles describe the absolute orientation: the quaternion
  //local and global taking the rotation of the parent (the same server account)
  b.quaternion.copy(space === 'global'
    ? parentWorldQuat(viewer, nome).invert().multiply(q)
    : q);
}

function previewCurves(channels) {
  if (!channels || !curveBone || !clip) return;
  const f = Math.max(0, Math.min(clip.num_frames - 1, currentFrame));
  const euler = CHANNELS.map((c) => channels[c][f]);
  viewers.forEach((v) => previewPose(v, curveBone, euler, editSpace()));
}

//------------------------------------------------------------------ filters
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
    if (r.collision) {   //US-07: anti-collision summary visible in the editor
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

//------------------------------------------------------------------ limits
//grouping by body region (if YAML gains new joints, "other" covers it)
const REGIONS = [
  { id: 'cabeca', rotulo: 'Head and neck', re: /^(Neck|Head)/ },
  { id: 'tronco', rotulo: 'Tronco', re: /^(Spine|Hips|Pelvis)/ },
  { id: 'braco-e', rotulo: 'Braço esquerdo', re: /^Left(Shoulder|Arm|ForeArm|Hand$)/ },
  { id: 'braco-d', rotulo: 'Braço direito', re: /^Right(Shoulder|Arm|ForeArm|Hand$)/ },
  { id: 'perna-e', rotulo: 'Perna esquerda', re: /^Left(UpLeg|Leg|Foot|Toe)/ },
  { id: 'perna-d', rotulo: 'Perna direita', re: /^Right(UpLeg|Leg|Foot|Toe)/ },
  { id: 'maos', rotulo: 'Mãos (dedos)', re: /^(Left|Right)(Hand)?(Index|Middle|Ring|Pinky|Thumb)/ },
  { id: 'outros', rotulo: 'Outras juntas', re: /./ },
];

function regionOf(nome) {
  const curto = String(nome || '').replace(/^mixamorig:/, '');
  for (const r of REGIONS) if (r.re.test(curto)) return r;
  return REGIONS[REGIONS.length - 1];
}

function stripAccents(s) {
  return String(s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
}

let limitsOrig = [];       //reference loaded/saved (to mark pending issues)
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
  const busca = stripAccents(q).trim();
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
    const r = regionOf(l.bone);
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
      row.dataset.busca = stripAccents(`${curto} ${l.kind} ${nota}`);
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
    //the saved values ​​become the new reference (clears the "pending" state)
    limitsOrig = data.preset.limits.map((l) => ({ ...l }));
    document.querySelectorAll('#limits .limit-row').forEach((row) => row.classList.remove('dirty'));
    marcarPendentes();
    $('limits-msg').textContent = t('limits.savedMsg', { n: r.limits, f: r.saved.split('\\').pop() });
    log(t('limits.savedLog'));
  } catch (e) {
    $('limits-msg').textContent = t('err.prefix', { msg: e.message });
  }
}

//3D unavailable: degrades with message and keeps the rest of the editor alive (US-01)
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

//------------------------------------------------------------------ wire
window.addEventListener('DOMContentLoaded', () => {
  try {
    viewers.push(new Viewer('canvas-skel', 'empty-skel', true));
    viewers.push(new Viewer('canvas-mesh', 'empty-mesh', false));
    //A/B mix panels: skeleton of the two jobs, shared camera and frame
    cmpA = new Viewer('canvas-a', 'empty-a', true);
    cmpB = new Viewer('canvas-b', 'empty-b', true);
  } catch (err) {
    console.error('failed to start 3D (WebGL):', err);
    viewers.length = 0;
    no3d = true;
  }
  //debug hook: the interface test reads the pose of the bone in the scene to
  //check that the preview follows the drag in real time
  window.__v2m_viewers = viewers;
  //the curve editor: the canvas measures 0 while the tab is hidden, so
  //ResizeObserver keeps the drawing up to date when it appears
  const cc = $('curve-canvas');
  if (cc) {
    curveEditor = new CurveEditor(cc, {
      onScrub: (f) => goToFrame(f),
      onSelect: () => { renderCurveKeys(); syncCurveProps(); },
      onInsert: (f) => insertKeyframe(f),
      onCommit: (keys) => commitCurves(keys),
      //3D preview follows the drag in real time
      onPreview: (ch) => previewCurves(ch),
      //the value of a channel (entered on the chip) and its reset
      onSetValue: (ch, v) => setChannelValue(ch, v),
      onResetValue: (ch) => resetChannelValue(ch),
    });
    //debug hook: and what the interface test uses to find the curve
    //on the screen and check what the server recorded
    window.__v2m_curveEditor = curveEditor;
    if (typeof ResizeObserver !== 'undefined') {
      const wrap = $('curve-wrap');
      if (wrap) new ResizeObserver(() => curveEditor.resize()).observe(wrap);
    }
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
  $('playpause').textContent = '▶';   //initial state: paused
  applyStatic();
  //the easing group label: `data-i18n` does not work here (the
  //applyStatic changes the textContent of the optgroup and deletes the options inside)
  document.querySelectorAll('optgroup.curve-easings').forEach((g) => {
    g.label = t('form.interpGroup');
  });
  langSwitcher('lang-switch');
  ligarSyncRot();
  if (no3d) showNo3D();
  window.addEventListener('resize', () => {
    viewers.forEach((v) => v.resize());
    [cmpA, cmpB].forEach((v) => { if (v) v.resize(); });
    reapplyCamera();
    syncCompareCamera(viewers[0], cmpA);
    if (curveEditor) curveEditor.resize();
    drawKpOverlay();
  });

  document.querySelectorAll('.refine-tabs .tab').forEach((b) =>
    b.addEventListener('click', () => switchRefineTab(b.dataset.tab)));
  //the tab can come in the URL (?tab=curves): the direct link to the curves editor
  switchRefineTab(tabFromUrl());
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
  if ($('curve-bone')) $('curve-bone').addEventListener('change', (e) => setCurveBone(e.target.value));
  //the reference (local/global) changes the meaning of the angles: reloads
  //the curves (and restores the pose, otherwise the previous one would have a crooked bone).
  const sp = $('space');
  if (sp) sp.addEventListener('change', () => {
    viewers.forEach((v) => v.setFrame(currentFrame, clip ? clip.fps : 30));
    syncSliders();
    loadCurves();
  });
  $('undo').addEventListener('click', () => undoRedo('undo'));
  $('redo').addEventListener('click', () => undoRedo('redo'));
  $('reset').addEventListener('click', resetEdits);
  $('compare').addEventListener('click', compare);
  $('apply-refine').addEventListener('click', applyRefine);
  $('save-limits').addEventListener('click', saveLimits);

  //---- curve editor ----------------------------------------------------
  if ($('curve-add')) $('curve-add').addEventListener('click', () => insertKeyframe(currentFrame));
  if ($('curve-del')) $('curve-del').addEventListener('click', () => {
    deleteKeyframe(curveEditor ? curveEditor.sel : -1);
  });
  if ($('curve-fit')) $('curve-fit').addEventListener('click', () => { if (curveEditor) curveEditor.fit(); });
  //the timeline: navigation by frame and keyframe
  if ($('tl-first')) $('tl-first').addEventListener('click', () => goToFrame(0));
  if ($('tl-last')) $('tl-last').addEventListener('click', () => goToFrame((clip ? clip.num_frames : 1) - 1));
  if ($('tl-prevkey')) $('tl-prevkey').addEventListener('click', () => {
    const f = keyframeNear(currentFrame, -1);
    if (f !== null) goToFrame(f);
  });
  if ($('tl-nextkey')) $('tl-nextkey').addEventListener('click', () => {
    const f = keyframeNear(currentFrame, 1);
    if (f !== null) goToFrame(f);
  });
  if ($('tl-frame')) $('tl-frame').addEventListener('change', (e) => {
    goToFrame(Number(e.target.value) || 0);
  });
  //properties of the selected keyframe: moving a field saves the keyframe
  const propFields = [
    ['curve-frame', (k, v) => { k.frame = Math.max(0, Math.round(v)); }],
    ['curve-vx', (k, v) => { k.value[0] = v; }],
    ['curve-vy', (k, v) => { k.value[1] = v; }],
    ['curve-vz', (k, v) => { k.value[2] = v; }],
  ];
  //the effect range: start/end (what the keyframe rewrites)
  for (const [id, campo] of [['curve-start', 'start'], ['curve-end', 'end']]) {
    const el = $(id);
    if (!el) continue;
    el.addEventListener('change', () => {
      if (!curveEditor || curveEditor.sel < 0) return;
      const keys = cloneKeys();
      const k = keys[curveEditor.sel];
      if (!k) return;
      const v = Number(el.value);
      if (!Number.isFinite(v)) return;
      //If the keyframe does not yet have a range chosen, start with
      //effective range (what came out of the neighbors)
      if (!k.explicit) {
        const wins = curveEditor.windowsFor();
        const win = wins[curveEditor.sel] || [k.frame, k.frame];
        k.start = win[0];
        k.end = win[1];
      }
      k[campo] = Math.max(0, Math.round(v));
      k.explicit = true;
      //the beginning cannot go beyond the end (and the frame has to stay inside)
      if (k.start > k.end) {
        const tmp = k.start; k.start = k.end; k.end = tmp;
      }
      k.start = Math.min(k.start, k.frame);
      k.end = Math.max(k.end, k.frame);
      commitCurves(keys);
    });
  }
  for (const [id, set] of propFields) {
    const el = $(id);
    if (!el) continue;
    el.addEventListener('change', () => {
      if (!curveEditor || curveEditor.sel < 0) return;
      const keys = cloneKeys();
      const k = keys[curveEditor.sel];
      if (!k) return;
      const v = Number(el.value);
      if (!Number.isFinite(v)) return;
      set(k, v);
      keys.sort((a, b) => a.frame - b.frame);
      commitCurves(keys);
    });
  }
  //the entry and exit of the keyframe (how the curve arrives and how it leaves)
  for (const [id, campo] of [['curve-interp-in', 'interp_in'],
                             ['curve-interp-out', 'interp_out']]) {
    const el = $(id);
    if (!el) continue;
    el.addEventListener('change', () => {
      if (!curveEditor || curveEditor.sel < 0) return;
      const keys = cloneKeys();
      const k = keys[curveEditor.sel];
      if (!k) return;
      k[campo] = el.value;
      //turning to "curve" materializes the automatic handles: without them there is no
      //nothing for the user to grab
      if (el.value === 'bezier' && !k.handle_in && !k.handle_out) {
        const auto = curveEditor.autoHandles(curveEditor.sel);
        if (auto) {
          k.handle_in = auto.handle_in;
          k.handle_out = auto.handle_out;
        }
      }
      commitCurves(keys);
    });
  }
  if ($('curve-note')) $('curve-note').addEventListener('change', () => {
    if (!curveEditor || curveEditor.sel < 0) return;
    const keys = cloneKeys();
    const k = keys[curveEditor.sel];
    if (!k) return;
    k.note = $('curve-note').value;
    commitCurves(keys);
  });
  //curve editor shortcuts (like in DCCs)
  document.addEventListener('keydown', (ev) => {
    if (!curveEditor || !clip) return;
    const tag = (ev.target && ev.target.tagName) || '';
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
    if (ev.key === 'k' || ev.key === 'K') {
      ev.preventDefault();
      insertKeyframe(currentFrame);
    } else if (ev.key === 'Delete' || ev.key === 'Backspace') {
      if (curveEditor.sel >= 0) { ev.preventDefault(); deleteKeyframe(curveEditor.sel); }
    }
  });

  $('playpause').addEventListener('click', () => {
    playing = !playing;
    $('playpause').textContent = playing ? '❚❚' : '▶';
    if (playing) step();
  });
  document.querySelectorAll('[data-cam]').forEach((b) =>
    b.addEventListener('click', () => {
      activeCam = b.dataset.cam;
      viewers.forEach((v) => v.setCamera(activeCam));
      [cmpA, cmpB].forEach((v) => { if (v) v.setCamera(activeCam); });
    }));
  $('kp-overlay').addEventListener('change', () => {
    kpOn = $('kp-overlay').checked;
    drawKpOverlay();
  });
  $('bone-labels').addEventListener('change', () => {
    const on = $('bone-labels').checked;
    viewers.forEach((v) => v.setLabels(on));
  });
});

//------------------------------------------- debug: 2D skeleton overlay
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

function step() {
  if (!playing || !clip) return;
  currentFrame = (currentFrame + 1) % clip.num_frames;
  syncSliders();
  setTimeout(step, 1000 / Math.max(clip.fps, 1));
}
