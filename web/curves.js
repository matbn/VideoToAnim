//VideoToAnim — editor de curvas (graph editor) da pagina de refino.
//
//A animacao do pipeline e 100% bakeada (uma pose por frame), mas o rebake
//das keyframes e uma interpolacao explicita. Este modulo reproduz ESSA
//interpolacao no navegador, com a mesma matematica do servidor
//(core/refine/boneedit.py), para o preview do arraste ser exatamente o que
//vai para o disco — e nao uma aproximacao.
//
//O canvas e a TIMELINE inteira: a regua de frames em cima, a linha das
//keyframes do osso, e a area de curvas embaixo — tudo com o mesmo playhead
//e a mesma escala de tempo, como nos DCCs.
//
//Tudo aqui e matematica pura (quaternions [x, y, z, w]) para nao depender
//do three.js nem dos objetos do DOM: o `refine.js` cuida da ligacao.

export const CHANNELS = ['x', 'y', 'z'];
export const CH_COLOR = { x: '#c0553f', y: '#6f8f4e', z: '#3f6f9e' };
export const CH_LABEL = { x: 'X', y: 'Y', z: 'Z' };
export const INTERPS = ['smooth', 'linear', 'step', 'bezier'];

//A cor da curva ORIGINAL de um canal: a mesma familia da curva editavel, mas
//mais clara — assim da para saber que e a referencia e nao o que se mexe.
export function chBaseColor(ch) {
  const hex = CH_COLOR[ch] || '#8a8175';
  const n = parseInt(hex.slice(1), 16);
  const r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
  const mix = (c) => Math.round(c + (255 - c) * 0.62);
  return `rgb(${mix(r)}, ${mix(g)}, ${mix(b)})`;
}
//Mesma metade de janela do servidor (be.DEFAULT_KEY_HALF): 20 frames de
//rampa para cada lado de uma keyframe sem vizinha.
export const KEY_HALF = 20;

const DEG = Math.PI / 180;
const RAD = 180 / Math.PI;

//--------------------------------------------------------------- quaternions
export function qMul(a, b) {
  const [ax, ay, az, aw] = a;
  const [bx, by, bz, bw] = b;
  return [
    aw * bx + ax * bw + ay * bz - az * by,
    aw * by - ax * bz + ay * bw + az * bx,
    aw * bz + ax * by - ay * bx + az * bw,
    aw * bw - ax * bx - ay * by - az * bz,
  ];
}

export function qConj(q) { return [-q[0], -q[1], -q[2], q[3]]; }

export function qNorm(q) {
  const n = Math.hypot(q[0], q[1], q[2], q[3]) || 1;
  return [q[0] / n, q[1] / n, q[2] / n, q[3] / n];
}

//Mesma funcao do servidor (core/refine/constraints.py): q e -q sao a mesma
//rotacao, e o slerp precisa do sinal certo para nao dar a volta curta.
export function qSlerp(q0, q1, t) {
  const a = qNorm(q0);
  let b = qNorm(q1);
  let d = a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3];
  if (d < 0) { b = [-b[0], -b[1], -b[2], -b[3]]; d = -d; }
  if (d > 0.9995) {
    return qNorm([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t,
                  a[2] + (b[2] - a[2]) * t, a[3] + (b[3] - a[3]) * t]);
  }
  const th = Math.acos(Math.max(-1, Math.min(1, d)));
  const s = Math.sin(th);
  const w0 = Math.sin((1 - t) * th) / s;
  const w1 = Math.sin(t * th) / s;
  return qNorm([a[0] * w0 + b[0] * w1, a[1] * w0 + b[1] * w1,
                a[2] * w0 + b[2] * w1, a[3] * w0 + b[3] * w1]);
}

//q = qx * qy * qz (a convencao do projeto) — o inverso exato de quatToEuler.
export function eulerToQuat(e) {
  const rx = e[0] * DEG / 2, ry = e[1] * DEG / 2, rz = e[2] * DEG / 2;
  const qx = [Math.sin(rx), 0, 0, Math.cos(rx)];
  const qy = [0, Math.sin(ry), 0, Math.cos(ry)];
  const qz = [0, 0, Math.sin(rz), Math.cos(rz)];
  return qNorm(qMul(qMul(qx, qy), qz));
}

//Mesma formula do servidor (quat_to_euler_xyz_deg), com o mesmo tratamento
//de gimbal: perto de +-90 graus em Y o X vai todo para o Z.
export function quatToEuler(q) {
  const [x, y, z, w] = qNorm(q);
  const m00 = 1 - 2 * (y * y + z * z);
  const m01 = 2 * (x * y - w * z);
  const m02 = 2 * (x * z + w * y);
  const m10 = 2 * (x * y + w * z);
  const m11 = 1 - 2 * (x * x + z * z);
  const m12 = 2 * (y * z - w * x);
  const m22 = 1 - 2 * (x * x + y * y);
  const sy = Math.max(-1, Math.min(1, m02));
  const ry = Math.asin(sy);
  let rx, rz;
  if (Math.abs(sy) < 0.999999) {
    rx = Math.atan2(-m12, m22);
    rz = Math.atan2(-m01, m00);
  } else {
    rx = 0;
    rz = Math.atan2(m10, m00);
  }
  return [rx * RAD, ry * RAD, rz * RAD];
}

//------------------------------------------------------------- interpolacao
//As familias classicas de easing: cada uma tem uma variante de ENTRADA (a
//curva acelera ate o alvo) e uma de SAIDA (a curva freia saindo do alvo) —
//e o mesmo catalogo do servidor (core/refine/boneedit.py).
const clamp01 = (t) => Math.max(0, Math.min(1, t));

function sineIn(t) { return 1 - Math.cos(t * Math.PI / 2); }
function sineOut(t) { return Math.sin(t * Math.PI / 2); }
function quadIn(t) { return t * t; }
function quadOut(t) { return 1 - (1 - t) ** 2; }
function cubicIn(t) { return t ** 3; }
function cubicOut(t) { return 1 - (1 - t) ** 3; }
function quartIn(t) { return t ** 4; }
function quartOut(t) { return 1 - (1 - t) ** 4; }
function quintIn(t) { return t ** 5; }
function quintOut(t) { return 1 - (1 - t) ** 5; }
function expoIn(t) { return t <= 0 ? 0 : 2 ** (10 * (t - 1)); }
function expoOut(t) { return t >= 1 ? 1 : 1 - 2 ** (-10 * t); }
function circIn(t) { return 1 - Math.sqrt(Math.max(0, 1 - t * t)); }
function circOut(t) { return Math.sqrt(Math.max(0, 1 - (t - 1) ** 2)); }
function backIn(t) { const c1 = 1.70158, c3 = 2.70158; return c3 * t ** 3 - c1 * t ** 2; }
function backOut(t) {
  const c1 = 1.70158, c3 = 2.70158;
  return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2;
}
function elasticIn(t) {
  if (t <= 0) return 0;
  if (t >= 1) return 1;
  const c4 = (2 * Math.PI) / 3;
  return -(2 ** (10 * t - 10)) * Math.sin((t * 10 - 10.75) * c4);
}
function elasticOut(t) {
  if (t <= 0) return 0;
  if (t >= 1) return 1;
  const c4 = (2 * Math.PI) / 3;
  return 2 ** (-10 * t) * Math.sin((t * 10 - 0.75) * c4) + 1;
}
function bounceOut(t) {
  const n1 = 7.5625, d1 = 2.75;
  if (t < 1 / d1) return n1 * t * t;
  if (t < 2 / d1) { t -= 1.5 / d1; return n1 * t * t + 0.75; }
  if (t < 2.5 / d1) { t -= 2.25 / d1; return n1 * t * t + 0.9375; }
  t -= 2.625 / d1;
  return n1 * t * t + 0.984375;
}
function bounceIn(t) { return 1 - bounceOut(1 - t); }

export const EASINGS = {
  smooth: [(t) => t * t * (3 - 2 * t), (t) => t * t * (3 - 2 * t)],
  linear: [(t) => t, (t) => t],
  step: [(t) => (t >= 1 - 1e-9 ? 1 : 0), () => 0],
  sine: [sineIn, sineOut],
  quad: [quadIn, quadOut],
  cubic: [cubicIn, cubicOut],
  quart: [quartIn, quartOut],
  quint: [quintIn, quintOut],
  expo: [expoIn, expoOut],
  circ: [circIn, circOut],
  back: [backIn, backOut],
  elastic: [elasticIn, elasticOut],
  bounce: [bounceIn, bounceOut],
};

export function easeIn(u, mode) {
  const fn = (EASINGS[mode] || EASINGS.smooth)[0];
  return fn(clamp01(u));
}

export function easeOut(u, mode) {
  const fn = (EASINGS[mode] || EASINGS.smooth)[1];
  return fn(clamp01(u));
}

//------------------------------------------------------ janelas de keyframe
//Mesma regra do servidor (boneedit.keyframe_windows): a janela de uma
//keyframe vai do frame SEGUINTE a vizinha anterior ate o frame ANTERIOR a
//vizinha seguinte — assim as pontas da janela sao as vizinhas, que viram
//as ancoras do rebake. Se a keyframe tiver um intervalo proprio (arrastado
//na timeline), ele tem prioridade.
export function keyframeWindows(keys, total, half = KEY_HALF) {
  const lista = (keys && keys.length && typeof keys[0] === 'object')
    ? [...keys] : keys.map((f) => ({ frame: f }));
  const ordered = [...lista].sort((a, b) => a.frame - b.frame);
  const last = Math.max(total - 1, 0);
  return ordered.map((k, i) => {
    const f = Math.round(k.frame);
    if (k.start !== null && k.start !== undefined
        && k.end !== null && k.end !== undefined) {
      let s = Math.max(0, Math.min(Math.round(k.start), last));
      let e = Math.max(s, Math.min(Math.round(k.end), last));
      s = Math.max(0, Math.min(s, f));
      e = Math.min(last, Math.max(e, f));
      return [s, e];
    }
    const prev = i > 0 ? ordered[i - 1].frame : null;
    const next = i + 1 < ordered.length ? ordered[i + 1].frame : null;
    let start = prev !== null ? prev + 1 : Math.max(0, f - half);
    let end = next !== null ? next - 1 : Math.min(last, f + half);
    start = Math.max(0, Math.min(start, f));
    end = Math.min(last, Math.max(end, f));
    return [start, end];
  });
}

//------------------------------------------------------ simulacao do rebake
//Reproduz `core.refine.boneedit.apply_edit` sobre a serie local do osso e
//devolve a serie de angulos no referencial pedido.
//
//`keys`: [{frame, value: [x, y, z], interp, handle_in, handle_out,
//handle_channel}]  (ja ordenadas por frame)
//`chain`: [{bone, quats}] — ancestrais, da raiz para baixo.

//uma bezier cubica (o mesmo do servidor: core/refine/boneedit.py)
function bezierPoint(p0, p1, p2, p3, u) {
  const mu = 1 - u;
  const a = mu * mu * mu;
  const b = 3 * mu * mu * u;
  const c = 3 * mu * u * u;
  const d = u * u * u;
  return [a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
          a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]];
}

//o parametro u em que a bezier passa por x (bisseccao, como no servidor)
function bezierParamForX(p0, p1, p2, p3, x) {
  let lo = 0, hi = 1;
  for (let i = 0; i < 48; i += 1) {
    const mid = (lo + hi) / 2;
    if (bezierPoint(p0, p1, p2, p3, mid)[0] < x) lo = mid;
    else hi = mid;
  }
  return (lo + hi) / 2;
}

//a alca automatica: um terco do segmento, sobre a linha dos valores
function autoHandle(span, dv) { return [span / 3, dv / 3]; }

//normaliza uma alca: o dx nunca passa do segmento (a bezier deixaria de
//ser monotonica em x)
function normHandle(h, span, dv, sign) {
  if (!h) return autoHandle(span, dv);
  const dx = Math.max(0, Math.min(Math.abs(Number(h[0]) || 0), Math.max(span, 1e-6)));
  return [dx, (Number(h[1]) || 0) * sign];
}

//o fator do slerp no frame `t` do segmento (fa -> fb), com as alcas
function bezierFactor(side, fa, fb, eA, eB, t, hPrev, hSelf, chan) {
  const span = Math.max(fb - fa, 1e-9);
  const dv = (eB[chan] || 0) - (eA[chan] || 0);
  let h1, h2;
  if (side === 'in') {
    h1 = normHandle(hPrev, span, dv, 1);
    h2 = normHandle(hSelf, span, dv, -1);
  } else {
    h1 = normHandle(hSelf, span, dv, 1);
    h2 = normHandle(null, span, dv, -1);
  }
  const p0 = [fa, eA[chan] || 0];
  const p1 = [fa + h1[0], (eA[chan] || 0) + h1[1]];
  const p2 = [fb - h2[0], (eB[chan] || 0) + h2[1]];
  const p3 = [fb, eB[chan] || 0];
  const u = bezierParamForX(p0, p1, p2, p3, t);
  const val = bezierPoint(p0, p1, p2, p3, u)[1];
  if (Math.abs(dv) < 1e-9) return Math.max(0, Math.min(1, u));
  return Math.max(0, Math.min(1, (val - p0[1]) / dv));
}

//como o segmento entre duas keyframes avanca: a primeira metade segue a
//saida de quem deixa, a segunda a entrada de quem chega (a mesma regra do
//servidor: core/refine/boneedit.py)
function segmentEase(modeOut, modeIn, x) {
  const xx = Math.max(0, Math.min(1, x));
  if (modeOut === modeIn) return easeIn(xx, modeOut);
  if (xx <= 0.5) return 0.5 * easeIn(xx * 2, modeOut);
  return 0.5 + 0.5 * easeOut((xx - 0.5) * 2, modeIn);
}

export function simularRebake(quats, chain, keys, space, total) {
  const half = KEY_HALF;
  const ordered = [...keys].sort((a, b) => a.frame - b.frame);
  const windows = keyframeWindows(ordered, total, half);
  const series = quats.map((q) => q.slice());

  const parentWorld = (frame) => {
    let w = [0, 0, 0, 1];
    for (const anc of chain) w = qMul(w, anc.quats[frame]);
    return w;
  };

  const eulerLocal = (q) => quatToEuler(q);
  let prevOut = null;
  let prevMode = '';
  ordered.forEach((k, i) => {
    const [start, end] = windows[i];
    const frame = Math.max(start, Math.min(end, k.frame));
    const aBefore = Math.max(0, start - 1);
    const aAfter = Math.min(total - 1, end + 1);
    //o alvo: em `global` os angulos descrevem a orientacao absoluta, entao
    //viram a rotacao local tira a rotacao mundial do pai (como no servidor)
    const qTarget = space === 'global'
      ? qMul(qConj(parentWorld(frame)), eulerToQuat(k.value))
      : eulerToQuat(k.value);
    const qBefore = series[aBefore];
    const qAfter = series[aAfter];
    const chan = Math.max(0, Math.min(Number(k.handle_channel ?? 1), 2));
    const modeIn = k.interp_in || k.interp || 'smooth';
    const modeOut = k.interp_out || modeIn;
    //a primeira metade do segmento que CHEGA aqui segue a saida da
    //keyframe anterior; a segunda, a entrada desta
    const modePrev = prevMode || modeIn;
    if (modeIn === 'bezier' || modePrev === 'bezier') {
      const eB = eulerLocal(qBefore);
      const eE = eulerLocal(qTarget);
      const eA = eulerLocal(qAfter);
      for (let t = start; t < frame; t += 1) {
        const u = bezierFactor('in', aBefore, frame, eB, eE, t, prevOut, k.handle_in, chan);
        series[t] = qSlerp(qBefore, qTarget, u);
      }
      series[frame] = qTarget;
      for (let t = frame + 1; t <= end; t += 1) {
        const u = bezierFactor('out', frame, aAfter, eE, eA, t, null, k.handle_out, chan);
        series[t] = qSlerp(qTarget, qAfter, u);
      }
    } else {
      for (let t = start; t < frame; t += 1) {
        const x = (t - aBefore) / Math.max(1e-9, frame - aBefore);
        const u = segmentEase(modePrev, modeIn, x);
        series[t] = qSlerp(qBefore, qTarget, u);
      }
      series[frame] = qTarget;
      for (let t = frame + 1; t <= end; t += 1) {
        const x = (t - frame) / Math.max(1e-9, aAfter - frame);
        const u = easeOut(x, modeOut);
        series[t] = qSlerp(qTarget, qAfter, u);
      }
    }
    prevOut = k.handle_out || null;
    prevMode = modeOut;
  });

  const out = { x: [], y: [], z: [] };
  for (let t = 0; t < total; t += 1) {
    const w = space === 'global' ? qMul(parentWorld(t), series[t]) : series[t];
    const e = quatToEuler(w);
    out.x.push(e[0]);
    out.y.push(e[1]);
    out.z.push(e[2]);
  }
  return out;
}

//--------------------------------------------------------------- editor
//O canvas e a timeline: regua em cima, linha das keyframes, curvas embaixo.
const PAD_L = 116;     //a calha da esquerda (os canais e seus angulos)
const PAD_R = 14;      //a calha da direita (a regua de graus fica na borda do grafico)
const RULER_H = 22;    //a regua de frames
const ROW_H = 26;      //a linha das keyframes do osso
const PAD_B = 20;

export class CurveEditor {
  /**
   * @param {HTMLCanvasElement} canvas
   * @param {{onScrub?:Function,onCommit?:Function,onSelect?:Function,
   *          onInsert?:Function,onPreview?:Function}} opts
   */
  constructor(canvas, opts = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.opts = opts;
    this.data = null;            //payload do servidor
    this.keys = [];              //[{frame, value:[x,y,z], interp}]
    this.baseChannels = null;    //a curva original (sem keyframes)
    this.sel = -1;
    this.playhead = 0;
    this.visible = { x: true, y: true, z: true };
    this.view = { f0: 0, f1: 1, d0: -180, d1: 180 };
    this.drag = null;
    this.hover = -1;
    this.boneLabel = '';
    this._bind();
    this._initValueInput();
  }

  //O input de valor: aparece em cima do chip do canal quando o usuario
  //clica (e solta) nele, para digitar o numero exato.
  _initValueInput() {
    const wrap = this.canvas.parentElement;
    if (!wrap) return;
    const inp = document.createElement('input');
    inp.type = 'number';
    inp.step = '0.1';
    inp.className = 'curve-value-input';
    inp.style.display = 'none';
    wrap.appendChild(inp);
    this.valueInput = inp;
    const fechar = (aplicar) => {
      const valor = Number(inp.value);
      const canal = this._inputChannel;
      inp.style.display = 'none';
      this._inputChannel = null;
      if (aplicar && Number.isFinite(valor) && canal !== null) {
        if (this.opts.onSetValue) this.opts.onSetValue(canal, valor);
      }
      this.draw();
    };
    inp.addEventListener('keydown', (ev) => {
      ev.stopPropagation();
      if (ev.key === 'Enter') { ev.preventDefault(); fechar(true); }
      else if (ev.key === 'Escape') { ev.preventDefault(); fechar(false); }
    });
    inp.addEventListener('blur', () => fechar(true));
  }

  //abre o input de valor (no losango clicado ou na linha do canal)
  openValueInput(ch, rect) {
    if (!this.valueInput) return;
    const r = rect || this.leftRowRect(ch);
    const wrap = this.canvas.parentElement;
    const box = wrap.getBoundingClientRect();
    const cbox = this.canvas.getBoundingClientRect();
    this._inputChannel = ch;
    //o input mostra o valor que vai MUDAR: o da keyframe selecionada (e o
    //do frame atual quando nao ha keyframe nenhuma)
    const k = this.sel >= 0 ? this.keys[this.sel] : null;
    const v = k ? k.value[ch] : this._valueAtPlayhead(ch);
    this.valueInput.value = (Number.isFinite(v) ? v : 0).toFixed(2);
    this.valueInput.style.display = 'block';
    this.valueInput.style.left = `${r.x + (cbox.left - box.left)}px`;
    this.valueInput.style.top = `${r.y + (cbox.top - box.top)}px`;
    this.valueInput.style.width = `${Math.max(r.w || 60, 76)}px`;
    this.valueInput.focus();
    this.valueInput.select();
  }

  //a caixa do losango de um canal (onde o input abre)
  diamondRect(channel, index) {
    const k = this.keys[index];
    if (!k) return null;
    return {
      x: this.frameToPx(k.frame) - 34,
      y: this.degToPy(k.value[channel]) - 9,
      w: 68, h: 18,
    };
  }

  //------------------------------------------------------------- dados
  setData(data) {
    this.data = data;
    this.keys = ((data && data.keyframes) || []).map((k) => ({
      frame: k.frame,
      value: (k.value || [0, 0, 0]).slice(),
      //a entrada (como a curva chega) e a saida (como ela sai)
      interp_in: k.interp_in || k.interp || 'smooth',
      interp_out: k.interp_out || k.interp_in || k.interp || 'smooth',
      note: k.note || '',
      handle_in: k.handle_in ? k.handle_in.slice() : null,
      handle_out: k.handle_out ? k.handle_out.slice() : null,
      handle_channel: Number.isFinite(k.handle_channel) ? k.handle_channel : 1,
      //o intervalo de efeito: `explicit` diz se o usuario escolheu (senao a
      //janela sai das vizinhas)
      start: Number.isFinite(k.start) ? k.start : null,
      end: Number.isFinite(k.end) ? k.end : null,
      explicit: !!k.range_explicit,
    }));
    this.sel = -1;
    //a curva original (a base, sem keyframes) serve de referencia
    if (data) {
      const base = data.base_quats || data.quats;
      this.baseChannels = simularRebake(base, data.chain || [], [], data.space, data.num_frames);
    } else {
      this.baseChannels = null;
    }
    this.fit();
  }

  setPlayhead(frame) {
    this.playhead = frame;
    this.draw();
  }

  setChannelVisible(ch, on) {
    this.visible[ch] = !!on;
    this.draw();
  }

  //------------------------------------------------------------- geometria
  get plot() {
    const r = this.canvas.getBoundingClientRect();
    return {
      x: PAD_L, y: RULER_H + ROW_H,
      w: Math.max(10, r.width - PAD_L - PAD_R),
      h: Math.max(10, r.height - RULER_H - ROW_H - PAD_B),
    };
  }

  get rowY() { return RULER_H + ROW_H / 2; }   //a linha das keyframes

  frameToPx(f) {
    const p = this.plot;
    const { f0, f1 } = this.view;
    const span = Math.max(f1 - f0, 1e-6);
    return p.x + ((f - f0) / span) * p.w;
  }

  pxToFrame(px) {
    const p = this.plot;
    const { f0, f1 } = this.view;
    const span = Math.max(f1 - f0, 1e-6);
    return f0 + ((px - p.x) / p.w) * span;
  }

  degToPy(d) {
    const p = this.plot;
    const { d0, d1 } = this.view;
    const span = Math.max(d1 - d0, 1e-6);
    return p.y + p.h - ((d - d0) / span) * p.h;
  }

  pyToDeg(py) {
    const p = this.plot;
    const { d0, d1 } = this.view;
    const span = Math.max(d1 - d0, 1e-6);
    return d0 + ((p.y + p.h - py) / p.h) * span;
  }

  //a curva que esta sendo desenhada: a serie real, ou o preview do arraste
  currentChannels() {
    if (this.preview) return this.preview;
    if (!this.data) return null;
    return this.data.channels;
  }

  fit() {
    if (!this.data) return;
    const total = this.data.num_frames || 1;
    this.view.f0 = 0;
    this.view.f1 = Math.max(total - 1, 1);
    const ch = this.currentChannels();
    let lo = 0, hi = 0;
    for (const c of CHANNELS) {
      if (!this.visible[c] || !ch) continue;
      for (const v of ch[c]) {
        if (!Number.isFinite(v)) continue;
        if (v < lo) lo = v;
        if (v > hi) hi = v;
      }
    }
    if (lo === hi) { lo -= 10; hi += 10; }
    const pad = (hi - lo) * 0.12;
    this.view.d0 = lo - pad;
    this.view.d1 = hi + pad;
    this.draw();
  }

  zoom(factor, anchorPx) {
    const p = this.plot;
    const ax = anchorPx === undefined ? p.x + p.w / 2 : anchorPx;
    const f = this.pxToFrame(ax);
    const d = this.pyToDeg(this.degToPy(0));
    const nf1 = f + (this.view.f1 - f) * factor;
    const nf0 = f + (this.view.f0 - f) * factor;
    const nd1 = d + (this.view.d1 - d) * factor;
    const nd0 = d + (this.view.d0 - d) * factor;
    this.view = {
      f0: Math.max(0, Math.min(nf0, f - 1)),
      f1: Math.max(this.view.f0 + 1, nf1),
      d0: Math.min(nd0, d),
      d1: Math.max(nd1, d),
    };
    this.draw();
  }

  //-------------------------------------------------------------- desenho
  resize() {
    const cv = this.canvas;
    const r = cv.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.max(1, Math.round(r.width * dpr));
    const h = Math.max(1, Math.round(r.height * dpr));
    if (cv.width !== w || cv.height !== h) {
      cv.width = w;
      cv.height = h;
    }
    this.draw();
  }

  draw() {
    const cv = this.canvas;
    const ctx = this.ctx;
    if (!ctx) return;
    const r = cv.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, r.width, r.height);
    const p = this.plot;

    ctx.fillStyle = '#fdfcf9';
    ctx.fillRect(p.x, 0, p.w, r.height);

    if (!this.data) {
      ctx.fillStyle = '#8a8175';
      ctx.font = '12px Inter, system-ui, sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('—', p.x + p.w / 2, p.y + p.h / 2);
      return;
    }

    this.drawGrid(ctx, p);
    this.drawBand(ctx, p);
    this.drawBase(ctx, p);
    this.drawCurves(ctx, p);
    this.drawKeys(ctx, p);
    this.drawHandles(ctx, p);
    this.drawRuler(ctx, p);
    this.drawKeyRow(ctx, p);
    this.drawAxes(ctx, p);
    this.drawChips(ctx, p);
    this.drawPlayhead(ctx, p);
  }

  //o segmento e bezier quando a entrada OU a saida da keyframe e "curva"
  _isBezier(k) {
    return (k.interp_in === 'bezier') || (k.interp_out === 'bezier');
  }

  //as alcas da bezier da keyframe selecionada (as "handles" dos DCCs)
  drawHandles(ctx, p) {
    const k = this.keys[this.sel];
    if (!k || !this._isBezier(k)) return;
    const ch = Math.max(0, Math.min(Number(k.handle_channel) || 0, 2));
    const x = this.frameToPx(k.frame);
    const y = this.degToPy(k.value[ch]);
    const desenha = (h, sinal) => {
      if (!h) return;
      const hx = x + sinal * h[0];
      const hy = y - sinal * h[1];
      ctx.strokeStyle = 'rgba(43, 42, 39, .55)';
      ctx.lineWidth = 1.2;
      ctx.setLineDash([3, 2]);
      ctx.beginPath();
      ctx.moveTo(x, y);
      ctx.lineTo(hx, hy);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = '#fdfcf9';
      ctx.strokeStyle = '#2b2a27';
      ctx.lineWidth = 1.4;
      ctx.beginPath();
      ctx.arc(hx, hy, 4, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
    };
    desenha(k.handle_in, -1);
    desenha(k.handle_out, 1);
  }

  drawGrid(ctx, p) {
    const step = this._niceStep((this.view.d1 - this.view.d0) / 6);
    ctx.lineWidth = 1;
    ctx.strokeStyle = '#e9e4da';
    ctx.beginPath();
    for (let d = Math.ceil(this.view.d0 / step) * step; d <= this.view.d1; d += step) {
      const y = Math.round(this.degToPy(d)) + 0.5;
      ctx.moveTo(p.x, y);
      ctx.lineTo(p.x + p.w, y);
    }
    const fstep = this._niceStep((this.view.f1 - this.view.f0) / 9);
    for (let f = Math.ceil(this.view.f0 / fstep) * fstep; f <= this.view.f1; f += fstep) {
      const x = Math.round(this.frameToPx(f)) + 0.5;
      ctx.moveTo(x, p.y);
      ctx.lineTo(x, p.y + p.h);
    }
    ctx.stroke();
  }

  drawBand(ctx, p) {
    const k = this.keys[this.sel];
    if (!k) return;
    const total = this.data.num_frames;
    const frames = this.keys.map((x) => x.frame);
    const i = frames.indexOf(k.frame);
    const win = keyframeWindows(this.keys, total)[i];
    if (!win) return;
    const x0 = this.frameToPx(win[0]);
    const x1 = this.frameToPx(win[1]);
    ctx.fillStyle = 'rgba(95, 125, 106, .12)';
    ctx.fillRect(x0, p.y, Math.max(1, x1 - x0), p.h);
    ctx.strokeStyle = 'rgba(95, 125, 106, .45)';
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    ctx.moveTo(Math.round(x0) + 0.5, p.y);
    ctx.lineTo(Math.round(x0) + 0.5, p.y + p.h);
    ctx.moveTo(Math.round(x1) + 0.5, p.y);
    ctx.lineTo(Math.round(x1) + 0.5, p.y + p.h);
    ctx.stroke();
    ctx.setLineDash([]);
    //as ancoras: de onde a curva sai e onde ela volta
    ctx.fillStyle = 'rgba(95, 125, 106, .8)';
    for (const f of [win[0] - 1, win[1] + 1]) {
      if (f < 0 || f >= total) continue;
      const x = Math.round(this.frameToPx(f)) + 0.5;
      ctx.fillRect(x - 1, p.y, 2, 6);
    }
  }

  //a curva original (a base, antes das keyframes) como referencia
  drawBase(ctx, p) {
    if (!this.baseChannels) return;
    const total = this.data.num_frames;
    ctx.save();
    ctx.beginPath();
    ctx.rect(p.x, p.y, p.w, p.h);
    ctx.clip();
    ctx.setLineDash([3, 3]);
    ctx.lineWidth = 1.4;
    for (const c of CHANNELS) {
      if (!this.visible[c]) continue;
      const serie = this.baseChannels[c];
      ctx.strokeStyle = chBaseColor(c);
      ctx.beginPath();
      const step = Math.max(1, Math.floor(total / Math.max(p.w, 1)));
      let started = false;
      for (let f = 0; f < total; f += step) {
        const v = serie[f];
        if (!Number.isFinite(v)) continue;
        const x = this.frameToPx(f);
        const y = this.degToPy(v);
        if (!started) { ctx.moveTo(x, y); started = true; } else { ctx.lineTo(x, y); }
      }
      ctx.stroke();
    }
    ctx.setLineDash([]);
    ctx.restore();
  }

  drawCurves(ctx, p) {
    const ch = this.currentChannels();
    if (!ch) return;
    const total = this.data.num_frames;
    //a curva e recortada na area do grafico: sem isso, um valor fora da
    //escala (o preview de um arraste, por exemplo) desenhava por cima da
    //regua de graus, do lado esquerdo
    ctx.save();
    ctx.beginPath();
    ctx.rect(p.x, p.y, p.w, p.h);
    ctx.clip();
    ctx.lineWidth = 1.6;
    ctx.lineJoin = 'round';
    for (const c of CHANNELS) {
      if (!this.visible[c]) continue;
      const serie = ch[c];
      if (!serie || !serie.length) continue;
      ctx.strokeStyle = CH_COLOR[c];
      ctx.globalAlpha = this.preview ? 0.35 : 1;
      ctx.beginPath();
      //no maximo um ponto por pixel: o canvas nao aguenta mais
      const step = Math.max(1, Math.floor(total / Math.max(p.w, 1)));
      let started = false;
      for (let f = 0; f < total; f += step) {
        const v = serie[f];
        if (!Number.isFinite(v)) continue;
        const x = this.frameToPx(f);
        const y = this.degToPy(v);
        if (!started) { ctx.moveTo(x, y); started = true; } else { ctx.lineTo(x, y); }
      }
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
    //o preview do arraste por cima, em cor cheia
    if (this.preview) {
      ctx.lineWidth = 2;
      for (const c of CHANNELS) {
        if (!this.visible[c]) continue;
        const serie = this.preview[c];
        if (!serie || !serie.length) continue;
        ctx.strokeStyle = CH_COLOR[c];
        ctx.beginPath();
        const step = Math.max(1, Math.floor(total / Math.max(p.w, 1)));
        let started = false;
        for (let f = 0; f < total; f += step) {
          const v = serie[f];
          if (!Number.isFinite(v)) continue;
          const x = this.frameToPx(f);
          const y = this.degToPy(v);
          if (!started) { ctx.moveTo(x, y); started = true; } else { ctx.lineTo(x, y); }
        }
        ctx.stroke();
      }
    }
    ctx.restore();
  }

  drawKeys(ctx, p) {
    //os marcadores tambem ficam dentro da area do grafico (um losango na
    //borda e cortado ao meio, como nos DCCs)
    ctx.save();
    ctx.beginPath();
    ctx.rect(p.x, p.y, p.w, p.h);
    ctx.clip();
    this.keys.forEach((k, i) => {
      CHANNELS.forEach((c, ci) => {
        if (!this.visible[c]) return;
        const x = this.frameToPx(k.frame);
        const y = this.degToPy(k.value[ci]);
        if (x < p.x - 8 || x > p.x + p.w + 8) return;
        const sel = i === this.sel;
        const hov = i === this.hover;
        const s = sel ? 6 : (hov ? 5.5 : 4.5);
        ctx.save();
        ctx.translate(x, y);
        ctx.rotate(Math.PI / 4);
        ctx.fillStyle = sel ? '#2b2a27' : CH_COLOR[c];
        ctx.strokeStyle = '#fdfcf9';
        ctx.lineWidth = 1.4;
        ctx.fillRect(-s, -s, s * 2, s * 2);
        ctx.strokeRect(-s, -s, s * 2, s * 2);
        ctx.restore();
      });
    });
    ctx.restore();
  }

  //a regua de frames (e de segundos) no topo do canvas
  drawRuler(ctx, p) {
    const fps = this.data.fps || 30;
    ctx.fillStyle = '#f3f0e9';
    ctx.fillRect(0, 0, this.canvas.getBoundingClientRect().width, RULER_H);
    ctx.strokeStyle = '#e0dbd1';
    ctx.beginPath();
    ctx.moveTo(0, RULER_H - 0.5);
    ctx.lineTo(this.canvas.getBoundingClientRect().width, RULER_H - 0.5);
    ctx.stroke();
    ctx.fillStyle = '#8a8175';
    ctx.font = '10px "SF Mono", ui-monospace, monospace';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';
    ctx.fillText('frames', 10, RULER_H / 2);
    ctx.textAlign = 'right';
    ctx.fillText(`${fps} fps`, PAD_L - 10, RULER_H / 2);

    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    const fstep = this._niceStep((this.view.f1 - this.view.f0) / 12);
    for (let f = Math.ceil(this.view.f0 / fstep) * fstep; f <= this.view.f1; f += fstep) {
      const x = this.frameToPx(f);
      if (x < p.x - 2 || x > p.x + p.w + 2) continue;
      const fr = Math.round(f);
      ctx.fillStyle = '#6b6862';
      ctx.fillText(String(fr), x, 4);
      //a marca do segundo (so no frame redondo em fps)
      if (fps > 0 && Math.abs(fr % fps) < 1e-6) {
        ctx.strokeStyle = '#cfc9bd';
        ctx.beginPath();
        ctx.moveTo(Math.round(x) + 0.5, RULER_H - 8);
        ctx.lineTo(Math.round(x) + 0.5, RULER_H);
        ctx.stroke();
        ctx.fillStyle = '#9a948a';
        ctx.fillText(`${(fr / fps).toFixed(0)}s`, x, RULER_H - 14);
      }
    }
  }

  //a linha das keyframes do osso (a "timeline" propriamente dita)
  drawKeyRow(ctx, p) {
    const w = this.canvas.getBoundingClientRect().width;
    ctx.fillStyle = '#efece5';
    ctx.fillRect(0, RULER_H, w, ROW_H);
    ctx.strokeStyle = '#e0dbd1';
    ctx.beginPath();
    ctx.moveTo(0, RULER_H + ROW_H - 0.5);
    ctx.lineTo(w, RULER_H + ROW_H - 0.5);
    ctx.stroke();
    ctx.fillStyle = '#6b6862';
    ctx.font = '11px Inter, system-ui, sans-serif';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';
    ctx.fillText(this.boneLabel || '—', 10, RULER_H + ROW_H / 2);

    //a barra de cada keyframe: o intervalo em que ela age
    const wins = keyframeWindows(this.keys, this.data.num_frames);
    this.keys.forEach((k, i) => {
      const win = wins[i];
      if (!win) return;
      const x0 = this.frameToPx(win[0]);
      const x1 = this.frameToPx(win[1]);
      const y = this.rowY;
      const sel = i === this.sel;
      ctx.fillStyle = sel ? 'rgba(95, 125, 106, .35)' : 'rgba(95, 125, 106, .18)';
      ctx.fillRect(x0, y - 4, Math.max(2, x1 - x0), 8);
      //as pontas (arrastaveis)
      for (const x of [x0, x1]) {
        if (x < p.x - 4 || x > p.x + p.w + 4) continue;
        ctx.fillStyle = sel ? '#5f7d6a' : '#8fa595';
        ctx.fillRect(Math.round(x) - 1.5, y - 7, 3, 14);
      }
      //o losango da keyframe
      const x = this.frameToPx(k.frame);
      const s = sel ? 6 : 4.5;
      ctx.save();
      ctx.translate(x, y);
      ctx.rotate(Math.PI / 4);
      ctx.fillStyle = sel ? '#2b2a27' : '#5f7d6a';
      ctx.strokeStyle = '#efece5';
      ctx.lineWidth = 1.2;
      ctx.fillRect(-s, -s, s * 2, s * 2);
      ctx.strokeRect(-s, -s, s * 2, s * 2);
      ctx.restore();
    });
  }

  drawAxes(ctx, p) {
    ctx.fillStyle = '#6b6862';
    ctx.font = '10px "SF Mono", ui-monospace, monospace';
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    const step = this._niceStep((this.view.d1 - this.view.d0) / 6);
    for (let d = Math.ceil(this.view.d0 / step) * step; d <= this.view.d1; d += step) {
      const y = this.degToPy(d);
      if (y < p.y - 2 || y > p.y + p.h + 2) continue;
      ctx.fillText(`${Math.round(d)}°`, p.x - 8, y);
    }
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    ctx.fillStyle = '#9a948a';
    ctx.fillText('frames', p.x + p.w / 2, p.y + p.h + 4);
  }

  //os canais, na calha da esquerda: o rotulo (liga/desliga a curva), o ANGULO
  //do keyframe selecionado e o reset do canal
  drawChips(ctx, p) {
    const sel = this.sel >= 0 ? this.keys[this.sel] : null;
    const y0 = p.y + 14;
    ctx.textBaseline = 'middle';
    CHANNELS.forEach((c, i) => {
      const y = y0 + i * 20;
      const on = this.visible[c];
      //o rotulo do canal (liga/desliga)
      ctx.fillStyle = on ? CH_COLOR[c] : '#e6e2da';
      roundRect(ctx, 8, y - 8, 26, 16, 8);
      ctx.fill();
      ctx.strokeStyle = on ? 'rgba(0,0,0,.15)' : '#d8d3c8';
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.fillStyle = on ? '#fff' : '#9a948a';
      ctx.font = '600 10px Inter, system-ui, sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText(CH_LABEL[c], 21, y);

      //o angulo do keyframe selecionado (ou do frame atual, sem keyframe)
      const v = sel ? sel.value[i] : this._valueAtPlayhead(i);
      const txt = `${(Number.isFinite(v) ? v : 0).toFixed(1)}°`;
      ctx.font = '600 11px "SF Mono", ui-monospace, monospace';
      ctx.fillStyle = on ? '#4a4842' : '#a9a49a';
      ctx.textAlign = 'right';
      ctx.fillText(txt, 88, y);

      //o botao de reset (volta o canal ao valor original)
      const rx = 100;
      ctx.beginPath();
      ctx.arc(rx, y, 7, 0, Math.PI * 2);
      ctx.fillStyle = '#f3f0e9';
      ctx.fill();
      ctx.strokeStyle = '#d8d3c8';
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.fillStyle = '#8a8175';
      ctx.font = '9px Inter, system-ui, sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('↺', rx, y + 0.5);
    });
  }

  //o valor de um canal no frame atual (quando nao ha keyframe selecionada)
  _valueAtPlayhead(channel) {
    const ch = this.currentChannels();
    if (!ch) return 0;
    const v = ch[CHANNELS[channel]] ? ch[CHANNELS[channel]][this.playhead] : 0;
    return Number.isFinite(v) ? v : 0;
  }

  //onde fica a linha de um canal na calha da esquerda
  leftRowRect(channel) {
    const p = this.plot;
    const y = p.y + 14 + channel * 20;
    return { x: 8, y: y - 9, w: 26, h: 18, label: { x: 8, w: 26 },
             value: { x: 38, w: 50 }, reset: { x: 100, w: 14 } };
  }

  //o que foi clicado na calha dos canais
  hitLeftRow(px, py) {
    if (px > 112) return null;
    const p = this.plot;
    for (let i = 0; i < CHANNELS.length; i += 1) {
      const y = p.y + 14 + i * 20;
      if (Math.abs(py - y) > 11) continue;
      if (px >= 8 && px <= 34) return { channel: i, part: 'label' };
      if (px >= 93 && px <= 107) return { channel: i, part: 'reset' };
      if (px >= 36 && px <= 92) return { channel: i, part: 'value' };
    }
    return null;
  }

  drawPlayhead(ctx, p) {
    const x = Math.round(this.frameToPx(this.playhead)) + 0.5;
    if (x < p.x || x > p.x + p.w) return;
    ctx.strokeStyle = '#b06a48';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, p.y + p.h);
    ctx.stroke();
    //a etiqueta do frame no topo
    const lbl = String(this.playhead);
    ctx.font = '600 10px "SF Mono", ui-monospace, monospace';
    const w = ctx.measureText(lbl).width + 10;
    ctx.fillStyle = '#b06a48';
    roundRect(ctx, x - w / 2, 3, w, 15, 4);
    ctx.fill();
    ctx.fillStyle = '#fff';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(lbl, x, 10.5);
  }

  _niceStep(raw) {
    if (!(raw > 0)) return 1;
    const pow = Math.pow(10, Math.floor(Math.log10(raw)));
    const n = raw / pow;
    let m = 1;
    if (n > 5) m = 10; else if (n > 2) m = 5; else if (n > 1) m = 2;
    return Math.max(1, Math.round(m * pow));
  }

  //------------------------------------------------------------- interacao
  _pos(ev) {
    const r = this.canvas.getBoundingClientRect();
    return { x: ev.clientX - r.left, y: ev.clientY - r.top };
  }

  //o canal de um chip (clicar liga/desliga a curva)
  hitChip(px, py) {
    const p = this.plot;
    if (px > 44 || px < 4) return null;
    const i = Math.round((py - (p.y + 14)) / 20);
    if (i < 0 || i >= CHANNELS.length) return null;
    if (Math.abs(py - (p.y + 14 + i * 20)) > 11) return null;
    return CHANNELS[i];
  }

  hitKey(px, py) {
    for (let i = 0; i < this.keys.length; i += 1) {
      const k = this.keys[i];
      for (let ci = 0; ci < CHANNELS.length; ci += 1) {
        const c = CHANNELS[ci];
        if (!this.visible[c]) continue;
        const dx = this.frameToPx(k.frame) - px;
        const dy = this.degToPy(k.value[ci]) - py;
        if (Math.hypot(dx, dy) <= 9) return { index: i, channel: c, ci };
      }
    }
    return null;
  }

  //a curva mais proxima do clique (para inserir keyframe)
  hitCurve(px, py) {
    const ch = this.currentChannels();
    if (!ch) return null;
    let best = null;
    for (const c of CHANNELS) {
      if (!this.visible[c]) continue;
      const serie = ch[c];
      for (let f = 0; f < serie.length; f += 1) {
        const dy = Math.abs(this.degToPy(serie[f]) - py);
        if (dy > 9) continue;
        const d = Math.hypot(this.frameToPx(f) - px, dy);
        if (!best || d < best.d) best = { d, frame: f, channel: c, value: serie[f] };
      }
    }
    return best;
  }

  //a alca da bezier sob o cursor (da keyframe selecionada)
  hitHandle(px, py) {
    const k = this.keys[this.sel];
    if (!k || !this._isBezier(k)) return null;
    const ch = Math.max(0, Math.min(Number(k.handle_channel) || 0, 2));
    const x = this.frameToPx(k.frame);
    const y = this.degToPy(k.value[ch]);
    for (const side of ['in', 'out']) {
      const h = side === 'in' ? k.handle_in : k.handle_out;
      if (!h) continue;
      const sinal = side === 'in' ? -1 : 1;
      const hx = x + sinal * h[0];
      const hy = y - sinal * h[1];
      if (Math.hypot(hx - px, hy - py) <= 8) return { side, channel: ch };
    }
    return null;
  }

  //a ponta da barra de efeito de uma keyframe (inicio ou fim do intervalo)
  hitKeyRange(px, py) {
    if (!this.data || py < RULER_H || py > RULER_H + ROW_H) return null;
    const wins = keyframeWindows(this.keys, this.data.num_frames);
    let melhor = null;
    this.keys.forEach((k, i) => {
      const win = wins[i];
      if (!win) return;
      for (const [which, f] of [['start', win[0]], ['end', win[1]]]) {
        const x = this.frameToPx(f);
        const d = Math.abs(x - px);
        if (d <= 7 && (!melhor || d < melhor.d)) {
          melhor = { d, index: i, which };
        }
      }
    });
    return melhor;
  }

  //o intervalo de efeito de cada keyframe (o que o usuario escolheu ou o que
  //saiu das vizinhas)
  windowsFor() {
    if (!this.data) return [];
    return keyframeWindows(this.keys, this.data.num_frames);
  }

  //o valor ORIGINAL de um canal num frame (a curva base, sem keyframes)
  baseValueAt(frame, channel) {
    if (!this.baseChannels) return null;
    const c = CHANNELS[channel];
    const serie = this.baseChannels[c];
    if (!serie) return null;
    const f = Math.max(0, Math.min(serie.length - 1, Math.round(frame)));
    return serie[f];
  }

  //as alcas automaticas de uma keyframe (o que o Blender chama de "auto"):
  //um terco do segmento, sobre a linha dos valores. Sao elas que o editor
  //grava quando a keyframe passa para o modo "curva".
  autoHandles(index) {
    const k = this.keys[index];
    if (!k || !this.data) return null;
    const total = this.data.num_frames;
    const windows = keyframeWindows(this.keys, total);
    const win = windows[index];
    if (!win) return null;
    const ch = Math.max(0, Math.min(Number(k.handle_channel) || 0, 2));
    const canal = CHANNELS[ch];
    const base = this.baseChannels;
    const prev = this.keys[index - 1];
    const next = this.keys[index + 1];
    const aFrame = Math.max(0, win[0] - 1);
    const bFrame = Math.min(total - 1, win[1] + 1);
    const va = prev ? prev.value[ch] : (base && base[canal] ? base[canal][aFrame] : 0);
    const vb = next ? next.value[ch] : (base && base[canal] ? base[canal][bFrame] : 0);
    const spanIn = prev ? (k.frame - prev.frame) : Math.max(1, k.frame - aFrame);
    const spanOut = next ? (next.frame - k.frame) : Math.max(1, bFrame - k.frame);
    return {
      handle_in: [spanIn / 3, (k.value[ch] - va) / 3],
      handle_out: [spanOut / 3, (vb - k.value[ch]) / 3],
    };
  }

  _bind() {
    const cv = this.canvas;
    cv.style.touchAction = 'none';
    cv.addEventListener('pointerdown', (ev) => {
      if (!this.data) return;
      const { x, y } = this._pos(ev);
      const p = this.plot;
      //o capture e o que faz o arraste continuar fora do canvas; se o
      //navegador recusar (ponteiro ja liberado), o arraste segue normal
      try { cv.setPointerCapture(ev.pointerId); } catch (e) { /* sem capture */ }
      if (y < RULER_H) {                     //regua: arrasta o playhead
        this.drag = { kind: 'scrub' };
        this._scrub(x);
        return;
      }
      const chip = this.hitChip(x, y);
      if (chip) {
        this.setChannelVisible(chip, !this.visible[chip]);
        this.drag = null;
        return;
      }
      //a calha dos canais: o rotulo (liga/desliga), o angulo (abre o input)
      //e o reset do canal
      const linha = this.hitLeftRow(x, y);
      if (linha) {
        if (linha.part === 'label') {
          this.setChannelVisible(CHANNELS[linha.channel], !this.visible[CHANNELS[linha.channel]]);
          this.drag = null;
        } else {
          this.drag = { kind: 'leftrow', channel: linha.channel, part: linha.part,
                        x0: x, y0: y, moved: false };
        }
        return;
      }
      //a alca da bezier da keyframe selecionada
      const alca = this.hitHandle(x, y);
      if (alca) {
        this.drag = {
          kind: 'handle', side: alca.side, channel: alca.channel,
          x0: x, y0: y, moved: false,
        };
        return;
      }
      if (y < RULER_H + ROW_H) {             //linha das keyframes
        //as pontas da barra de efeito (o intervalo em que a keyframe age)
        const ponta = this.hitKeyRange(x, y);
        if (ponta) {
          this.drag = { kind: 'krange', index: ponta.index, which: ponta.which };
          return;
        }
        const hit = this._hitRowKey(x);
        if (hit !== null) {
          this.select(hit);
          this.drag = { kind: 'rowkey' };
        } else {
          const f = Math.round(this.pxToFrame(x));
          this.drag = { kind: 'rowinsert', frame: f };
          if (this.opts.onInsert) this.opts.onInsert(f);
        }
        return;
      }
      const hit = this.hitKey(x, y);
      if (hit) {
        this.select(hit.index);
        this.drag = {
          kind: 'key', index: hit.index, channel: hit.channel, ci: hit.ci,
          x0: x, y0: y, axis: null, moved: false,
          frame0: this.keys[hit.index].frame,
          value0: this.keys[hit.index].value.slice(),
        };
        this._preview();
        return;
      }
      this.drag = { kind: 'maybe', x0: x, y0: y, moved: false };
    });

    cv.addEventListener('pointermove', (ev) => {
      if (!this.data) return;
      const { x, y } = this._pos(ev);
      if (!this.drag) {
        const hit = this.hitKey(x, y);
        const h = hit ? hit.index : -1;
        if (h !== this.hover) { this.hover = h; this.draw(); }
        if (hit) cv.style.cursor = 'grab';
        else if (this.hitHandle(x, y)) cv.style.cursor = 'move';
        else if (this.hitKeyRange(x, y)) cv.style.cursor = 'ew-resize';
        else if (this.hitLeftRow(x, y)) cv.style.cursor = 'pointer';
        else cv.style.cursor = 'crosshair';
        return;
      }
      if (this.drag.kind === 'scrub') { this._scrub(x); return; }
      if (this.drag.kind === 'maybe') {
        if (Math.hypot(x - this.drag.x0, y - this.drag.y0) > 3) {
          this.drag.kind = 'pan';
          this._pan(x - this.drag.x0, y - this.drag.y0);
          this.drag.x0 = x; this.drag.y0 = y;
        }
        return;
      }
      if (this.drag.kind === 'pan') {
        this._pan(x - this.drag.x0, y - this.drag.y0);
        this.drag.x0 = x; this.drag.y0 = y;
        return;
      }
      if (this.drag.kind === 'krange') {
        //o intervalo de efeito da keyframe (as pontas da barra)
        const k = this.keys[this.drag.index];
        if (!k) return;
        const total = this.data.num_frames;
        const f = Math.max(0, Math.min(total - 1, Math.round(this.pxToFrame(x))));
        const win = keyframeWindows(this.keys, total)[this.drag.index] || [0, total - 1];
        let s = win[0], e = win[1];
        if (this.drag.which === 'start') s = Math.min(f, k.frame);
        else e = Math.max(f, k.frame);
        k.start = s;
        k.end = e;
        k.explicit = true;
        this.drag.moved = true;
        this._preview();
        return;
      }
      if (this.drag.kind === 'handle') {
        const k = this.keys[this.sel];
        if (!k) return;
        this.drag.moved = true;
        const ch = this.drag.channel;
        const sinal = this.drag.side === 'in' ? -1 : 1;
        //a alca em (frames, graus): o dx nunca passa do vizinho
        const kx = this.frameToPx(k.frame);
        const ky = this.degToPy(k.value[ch]);
        const dx = Math.abs(x - kx);
        const dy = sinal * (ky - y);
        const nome = this.drag.side === 'in' ? 'handle_in' : 'handle_out';
        k[nome] = [dx, dy];
        k.handle_channel = ch;
        this._preview();
        return;
      }
      //arrastando uma keyframe
      const dx = x - this.drag.x0;
      const dy = y - this.drag.y0;
      if (!this.drag.axis && Math.hypot(dx, dy) > 4) {
        this.drag.axis = Math.abs(dx) > Math.abs(dy) ? 'x' : 'y';
      }
      if (!this.drag.axis) return;
      this.drag.moved = true;
      const k = this.keys[this.drag.index];
      if (!k) return;
      if (this.drag.axis === 'y') {
        const v = this.pyToDeg(y);
        k.value = k.value.slice();
        k.value[this.drag.ci] = v;
      } else {
        const total = this.data.num_frames;
        let f = Math.round(this.pxToFrame(x));
        f = Math.max(0, Math.min(total - 1, f));
        //nao pode passar por cima de outra keyframe
        for (let i = 0; i < this.keys.length; i += 1) {
          if (i === this.drag.index) continue;
          if (Math.abs(this.keys[i].frame - f) < 1) { f = this.keys[i].frame + (f > this.keys[i].frame ? 1 : -1); }
        }
        k.frame = Math.max(0, Math.min(total - 1, f));
      }
      this._preview();
    });

    const end = (ev) => {
      if (!this.drag) return;
      const d = this.drag;
      this.drag = null;
      try { cv.releasePointerCapture(ev.pointerId); } catch (e) { /* ja liberado */ }
      if (d.kind === 'key') {
        this.preview = null;
        if (d.moved) {
          this.commit();
        } else {
          //um clique (sem arrastar) no losango abre o input do valor
          this.openValueInput(d.ci, this.diamondRect(d.ci, d.index));
        }
        this.draw();
        return;
      }
      if (d.kind === 'leftrow') {
        if (!d.moved) {
          if (d.part === 'reset') {
            if (this.opts.onResetValue) this.opts.onResetValue(d.channel);
          } else {
            this.openValueInput(d.channel, this.leftRowRect(d.channel));
          }
        }
        this.draw();
        return;
      }
      if (d.kind === 'handle') {
        this.preview = null;
        if (d.moved) this.commit();
        else this.draw();
        return;
      }
      if (d.kind === 'krange') {
        this.preview = null;
        if (d.moved) this.commit();
        else this.draw();
        return;
      }
      if (d.kind === 'maybe') {
        //clique simples: insere keyframe na curva mais proxima
        const hit = this.hitCurve(d.x0, d.y0);
        if (hit && this.opts.onInsert) {
          this.opts.onInsert(hit.frame, hit.channel, hit.value);
        }
      }
      this.draw();
    };
    cv.addEventListener('pointerup', end);
    cv.addEventListener('pointercancel', end);
    cv.addEventListener('pointerleave', () => {
      if (!this.drag && this.hover !== -1) { this.hover = -1; this.draw(); }
    });
    cv.addEventListener('wheel', (ev) => {
      if (!this.data) return;
      ev.preventDefault();
      const { x } = this._pos(ev);
      this.zoom(ev.deltaY > 0 ? 1.12 : 0.89, x);
    }, { passive: false });
  }

  _hitRowKey(px) {
    for (let i = 0; i < this.keys.length; i += 1) {
      const x = this.frameToPx(this.keys[i].frame);
      if (Math.abs(x - px) <= 8) return i;
    }
    return null;
  }

  _scrub(px) {
    const f = Math.round(this.pxToFrame(px));
    const total = this.data ? this.data.num_frames : 0;
    const clamped = Math.max(0, Math.min(total - 1, f));
    if (clamped === this.playhead) return;
    this.playhead = clamped;
    if (this.opts.onScrub) this.opts.onScrub(clamped);
    this.draw();
  }

  _pan(dx, dy) {
    const span = this.view.f1 - this.view.f0;
    const df = (dx / this.plot.w) * span;
    const dd = (-dy / this.plot.h) * (this.view.d1 - this.view.d0);
    this.view.f0 += df; this.view.f1 += df;
    this.view.d0 += dd; this.view.d1 += dd;
    this.draw();
  }

  select(index) {
    this.sel = index;
    if (this.opts.onSelect) this.opts.onSelect(index);
    this.draw();
  }

  //recalcula a curva do preview (a serie que o rebake vai produzir) e avisa
  //a pagina, para a previa 3D mudar em TEMPO REAL
  _preview() {
    if (!this.data) return;
    //a serie que o servidor vai usar como base: a do osso SEM keyframes
    const base = this.data.base_quats || this.data.quats;
    this.preview = simularRebake(
      base, this.data.chain || [], this.keys, this.data.space,
      this.data.num_frames);
    this.draw();
    if (this.opts.onPreview) this.opts.onPreview(this.preview, this.playhead);
  }

  commit() {
    if (this.opts.onCommit) this.opts.onCommit(this.keys);
  }
}

//um retangulo de cantos arredondados (o Path2D do canvas nem sempre esta)
function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}
