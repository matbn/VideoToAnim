// video2mixamo — editor de refinamento (bone editor + filtros + constraints).
// Modo Redesign/Refinement: mesma linguagem visual do pipeline, página nova.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { t, applyStatic, langSwitcher, getLang } from './i18n.js';

const $ = (id) => document.getElementById(id);
const CAMERAS = { persp: [2.2, 1.6, 2.6], front: [0, 1.1, 3.2], side: [3.4, 1.1, 0] };

let jobId = null, clip = null, currentFrame = 0, playing = false, spanS = 0;
const viewers = [];

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
    this.renderer.setAnimationLoop(() => this.tick());
  }
  resize() {
    const w = $(this.host).clientWidth || 320, h = $(this.host).clientHeight || 300;
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
  setCamera(n) { this.camera.position.set(...(CAMERAS[n] || CAMERAS.persp)); this.controls.target.set(0, 0.95, 0); this.controls.update(); }
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
  const f = $('frame'), st = $('start'), en = $('end');
  f.max = clip.num_frames - 1; f.value = 0; st.max = clip.num_frames - 1; en.max = clip.num_frames - 1;
  st.value = 0; en.value = Math.min(clip.num_frames - 1, 10);
  const bs = $('bone');
  bs.innerHTML = '';
  for (const b of clip.animated_bones) {
    const o = document.createElement('option');
    o.value = b; o.textContent = b.replace('mixamorig:', '');
    bs.appendChild(o);
  }
  await refreshPreview();
  await syncSliders();
  try { localStorage.setItem('v2m:lastJob', id); } catch (e) { /* sem storage */ }
  history.replaceState(null, '', `/refine?job=${id}`);
  log(t('ref.clipLoadedLog', { id, n: clip.num_frames, b: clip.animated_bones.length }));
}

async function syncSliders() {
  if (!jobId || !clip) return;
  const t = Number($('frame').value);
  currentFrame = t;
  $('frame-label').textContent = t;
  // video de referencia acompanha o frame (scrub e playback)
  const vid = $('refvideo');
  if (vid && clip.fps > 0 && vid.readyState >= 2) {
    const alvo = Math.min(t / clip.fps, Math.max(vid.duration - 0.001, 0));
    if (Math.abs(vid.currentTime - alvo) > 0.5 / clip.fps) vid.currentTime = alvo;
  }
  // a pose na cena vem ANTES do fetch: o playback nao depende da rede
  viewers.forEach((v) => v.setFrame(t, clip.fps));
  $('time').textContent = `${(t / clip.fps).toFixed(2)} / ${(clip.num_frames / clip.fps).toFixed(2)} s`;
  $('timeline').value = String(Math.round((t / Math.max(clip.num_frames - 1, 1)) * 1000));
  try {
    const pose = await api(`/api/refine/animation/${jobId}/frame/${t}`);
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

// ------------------------------------------------------------------ edicao
async function applyEdit() {
  if (!jobId) return;
  const body = {
    bone: $('bone').value,
    frame: Number($('frame').value),
    rotation_euler_deg: [Number($('rx').value), Number($('ry').value), Number($('rz').value)],
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
      body: JSON.stringify({ use_filters: true, use_constraints: true, filters: chosenFilters() }),
    });
    log(t('ref.appliedLog', { stages: r.stages.join(' → '), bytes: r.glb.bytes }));
    if (r.constraints) {
      const c = r.constraints;
      log(t('ref.constraintsLog', { bones: c.bones_corrected, frames: c.frames_corrected_total }));
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

// ------------------------------------------------------------------ wire
window.addEventListener('DOMContentLoaded', () => {
  viewers.push(new Viewer('canvas-skel', 'empty-skel', true));
  viewers.push(new Viewer('canvas-mesh', 'empty-mesh', false));
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
  window.addEventListener('resize', () => viewers.forEach((v) => v.resize()));

  loadJobs();
  loadFilters();
  loadLimits();

  $('job').addEventListener('change', (e) => selectJob(e.target.value));
  $('frame').addEventListener('input', syncSliders);
  $('bone').addEventListener('change', syncSliders);
  ['rx', 'ry', 'rz'].forEach((k) =>
    $(k).addEventListener('input', () => { $(`${k}-label`).textContent = `${$(k).value}°`; }));
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
    b.addEventListener('click', () => viewers.forEach((v) => v.setCamera(b.dataset.cam))));
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

function step() {
  if (!playing || !clip) return;
  currentFrame = (currentFrame + 1) % clip.num_frames;
  $('frame').value = currentFrame;
  syncSliders();
  setTimeout(step, 1000 / Math.max(clip.fps, 1));
}
