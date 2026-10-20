/* IDgent Git graph page: Canvas2D renderer, camera, animation, hover card, keyboard, toolbar, host bridge.
 * Pure layout/maths live in gitgraph_core.js (window.GitGraphCore).
 *
 * API (contract B), host -> page:   IDGit.setData(data)  setState(kind, message)  setTheme(colors)
 *   setSettings({reduceMotion, showRemote?, showTags?})  select(hash|null)  focus(hash|'HEAD')  pause()  resume()
 * page -> host (webkit.messageHandlers.idgent):  {type:'ready'} {type:'open-commit', hash} {type:'refresh'} {type:'error', message}
 * Rendering is on demand: a requestAnimationFrame loop runs only while something moves and stops by itself;
 * the HEAD "breathing" ring is a CSS animation on one small element that sleeps after a few seconds of no interaction. */
(function () {
  'use strict';
  const G = window.GitGraphCore;
  const C = G.C;
  const IDGit = window.IDGit = window.IDGit || {};
  IDGit.outbox = IDGit.outbox || [];

  // ------------------------------------------------------------ bridge
  function post(msg) {
    try {
      const h = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.idgent;
      if (h) h.postMessage(msg); else { IDGit.outbox.push(msg); if (IDGit.outbox.length > 500) IDGit.outbox.splice(0, 250); }
    } catch (e) { IDGit.outbox.push(msg); }
  }
  window.addEventListener('error', (e) => post({ type: 'error', message: String((e && e.message) || e) + (e && e.filename ? ' @' + e.filename.split('/').pop() + ':' + e.lineno : '') }));
  window.addEventListener('unhandledrejection', (e) => post({ type: 'error', message: 'unhandled rejection: ' + String(e && e.reason) }));
  function safe(fn) {
    return function () {
      try { return fn.apply(null, arguments); } catch (e) { post({ type: 'error', message: 'IDGit: ' + ((e && e.stack) || e) }); return undefined; }
    };
  }

  const $ = (id) => document.getElementById(id);
  const canvas = $('gg'), ctx = canvas.getContext('2d');
  const tipEl = $('tip'), pulseEl = $('pulse'), stateEl = $('state'), barEl = $('bar'), liveEl = $('live');
  pulseEl.innerHTML = '<i></i><i></i>';

  // ------------------------------------------------------------ state
  const settings = { reduceMotion: false, showRemote: true, showTags: true, timeFormat: 'relative' };
  let orientation = 'horizontal', turn = null;
  let labelGlides = new Map();
  const mql = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
  const reduce = () => !!settings.reduceMotion || !!(mql && mql.matches);

  let L = null;                       // current layout
  let labels = [];                    // placed labels (world coords)
  let colMap = new Map();             // col -> [node idx]
  let gap = C.laneGap;
  let dataRaw = null, viewState = 'loading';
  let selected = -1, cursor = -1, kbFocus = false;
  let hover = -1, hoverT0 = 0, unhover = -1, unhoverT0 = 0, hoverLabel = null, hoverTail = false;
  let anim = [];                      // per node: {fx, fy, pt0, pd, at0, ad, ak, sx}
  let ghosts = [];                    // vanished commits fading out
  let labelT0 = 0, labelD = 0;        // global label fade-in
  let headGlide = null;               // {fx, fy, t0, d}: HEAD badge offset glide
  let animUntil = 0;
  let edgeCache = null;               // per edge segments when nothing moves
  let W = 0, H = 0, dpr = 1;
  let paused = false, hiddenDoc = false, raf = 0, lastTs = 0, readySent = false;
  const cam = { x: 0, y: 0, s: 1 };
  let camTw = null;                   // {fx, fy, fs, tx, ty, ts, t0, d}
  let zoomT = null;                   // {s, ax, ay, wx, wy}
  let inertia = null;                 // {vx, vy}  screen px per ms
  let camHome = true;                 // camera still at its automatic position (re-home on resize)
  let press = null;
  const pointer = { x: -1, y: -1, inside: false };
  const perf = { frames: 0, layoutMs: 0, labelMs: 0, drawMs: 0, lastFrameTs: 0, revealMs: 0 };
  let tipTimer = 0, tipFor = null, pulseTimer = 0, pulseOn = false;

  // ------------------------------------------------------------ theme
  const DEFAULT_THEME = {
    background: '#1c1c1c', surface: '#282828', surface_alt: '#232323', border: '#333333', text: '#dadada', text_muted: '#b3b3b3',
    accent: '#8a5cf5', accent_fg: '#ffffff', head: '#8a5cf5',
    branch_colors: ['#4c8df6', '#a371f7', '#3fb950', '#f0883e', '#f85149', '#39c5cf', '#db61a2', '#d29922'],
    mono_font: '"JetBrains Mono", "DejaVu Sans Mono", monospace', ui_font: 'Inter, "Segoe UI", Cantarell, system-ui, sans-serif', dark: true,
  };
  let theme = Object.assign({}, DEFAULT_THEME);
  let pal = null;
  function fontStack(f, fallback) {
    f = String(f || '').trim();
    if (!f) return fallback;
    f = f.replace(/\s+\d+(\.\d+)?(px|pt)?$/, '');               // GTK "Name 11" descriptions
    if (!/[,"']/.test(f) && /\s/.test(f)) f = '"' + f + '"';
    return f + ', ' + fallback;
  }
  function buildPalette(t) {
    const P = (c, d) => G.parseColor(c) || G.parseColor(d);
    const bg = P(t.background, '#1c1c1c'), text = P(t.text, '#dadada');
    const dark = typeof t.dark === 'boolean' ? t.dark : G.luminance(bg) < 0.3;
    const surface = P(t.surface, G.toHex(G.mix(bg, text, 0.06))), border = P(t.border, G.toHex(G.mix(bg, text, 0.14)));
    const muted = G.legible(P(t.text_muted, G.toHex(G.mix(bg, text, 0.6))), bg, 4.5);
    const accent = P(t.accent, '#8a5cf5');
    const headC = P(t.head, t.accent || '#8a5cf5');
    const fgFor = (fill, want) => {
      const w = want && G.parseColor(want);
      if (w && G.contrast(w, fill) >= 4.5) return w;
      return G.contrast([255, 255, 255], fill) >= G.contrast([0, 0, 0], fill) ? [255, 255, 255, 1] : [0, 0, 0, 1];
    };
    let src = Array.isArray(t.branch_colors) && t.branch_colors.length ? t.branch_colors : DEFAULT_THEME.branch_colors;
    src = src.map((c) => G.parseColor(c)).filter(Boolean);
    if (!src.length) src = DEFAULT_THEME.branch_colors.map(G.parseColor);
    const lane = (c) => {
      const line = G.legible(c, bg, dark ? 3 : 3.2);
      const fill = G.mix(bg, line, dark ? 0.14 : 0.11);
      return { line, css: G.toHex(line), fill: G.toHex(fill), text: G.toHex(G.legible(line, fill, 4.6)), glow: G.rgba(line, 0.16), halo: G.rgba(line, dark ? 0.2 : 0.16),
        soft: G.rgba(line, 0.5), light: G.toHex(G.mix(line, dark ? [255, 255, 255] : [255, 255, 255], dark ? 0.35 : 0.45)), chip: G.rgba(line, dark ? 0.24 : 0.16) };
    };
    const lanes = src.map(lane);
    const main = lane(accent);
    const headFill = G.legible(headC, bg, 2.2);
    return {
      dark, bg, bgCss: G.toHex(bg), text: G.toHex(text), muted: G.toHex(muted), surface: G.toHex(surface), border: G.toHex(border),
      accent: G.toHex(G.legible(accent, bg, 3)), accentText: G.toHex(G.legible(accent, surface, 4.5)),
      head: G.toHex(headFill), headFg: G.toHex(fgFor(headFill, t.accent_fg)), headGlow: G.rgba(headFill, 0.22),
      calloutBg: G.rgba(G.mix(bg, text, dark ? 0.07 : 0.03), 0.94), calloutBorder: G.toHex(G.mix(bg, text, dark ? 0.2 : 0.18)),
      calloutText: G.toHex(G.legible(muted, G.mix(bg, text, 0.07), 4.5)),
      tagBg: G.toHex(G.mix(bg, text, dark ? 0.08 : 0.05)), tagBorder: G.toHex(G.mix(bg, text, dark ? 0.22 : 0.2)),
      tail: G.rgba(G.mix(bg, text, 0.5), 0.9), tailDot: G.toHex(G.mix(bg, text, dark ? 0.32 : 0.36)),
      warn: G.toHex(G.legible(G.parseColor('#e9973f'), bg, 3)),
      lanes, main, skip: G.nearestColorIdx(accent, src),
      ui: fontStack(t.ui_font, DEFAULT_THEME.ui_font), mono: fontStack(t.mono_font, DEFAULT_THEME.mono_font),
    };
  }
  function applyCss() {
    const r = document.documentElement.style, p = pal;
    const acc = G.parseColor(p.accent), bg = p.bg;
    const set = (k, v) => r.setProperty(k, v);
    set('--bg', p.bgCss); set('--surface', p.surface); set('--border', p.border); set('--text', p.text); set('--muted', p.muted);
    set('--accent', p.accent); set('--accent-text', p.accentText); set('--head', p.head); set('--accent-fg', p.headFg);
    set('--shadow', p.dark ? 'rgba(0,0,0,0.45)' : 'rgba(0,0,0,0.14)');
    set('--hover', G.rgba(G.parseColor(p.text), p.dark ? 0.08 : 0.06));
    set('--bar-bg', G.rgba(G.parseColor(p.surface), p.dark ? 0.8 : 0.86));
    set('--accent-55', G.rgba(acc, 0.55)); set('--accent-13', G.rgba(acc, 0.13));
    set('--accent-45b', G.toHex(G.mix(G.parseColor(p.border), acc, 0.45))); set('--accent-22s', G.toHex(G.mix(G.parseColor(p.surface), acc, 0.22)));
    set('--ui-font', p.ui); set('--mono-font', p.mono);
    document.body.style.background = p.bgCss;
    void bg;
  }
  const laneCol = (li) => {
    if (!L || li < 0) return pal.main;
    const ln = L.lanes[li];
    return ln.main || ln.color < 0 ? pal.main : pal.lanes[ln.color % pal.lanes.length];
  };

  // ------------------------------------------------------------ text measurement
  const measureCache = new Map();
  function measure(text, size, weight) {
    const key = (weight || 400) + '|' + size + '|' + text;
    let w = measureCache.get(key);
    if (w === undefined) {
      ctx.save(); ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.font = (weight || 400) + ' ' + size + 'px ' + pal.ui;
      w = ctx.measureText(text).width;
      ctx.restore();
      if (measureCache.size > 4000) measureCache.clear();
      measureCache.set(key, w);
    }
    return w;
  }

  // ------------------------------------------------------------ geometry helpers
  const vertical = () => orientation === 'vertical';
  const nx = (i) => G.position(L, i, gap).x;
  const ny = (i) => G.position(L, i, gap).y;
  function viewAngle(now) {
    const target = vertical() ? Math.PI / 2 : 0;
    if (!turn) return target;
    const t = G.clamp((now - turn.t0) / turn.d, 0, 1);
    return turn.from + (target - turn.from) * G.ease.inOutCubic(t);
  }
  function routedEdge(e, now) {
    const a = viewAngle(now), c = Math.cos(a), s = Math.sin(a);
    const px = D.x[e.p], py = D.y[e.p], cx = D.x[e.c], cy = D.y[e.c];
    return G.edgeSegments(px * c - py * s, px * s + py * c, cx * c - cy * s, cx * s + cy * c, e.route, C.colW).map((seg) => {
      const out = { t: seg.t };
      for (const [x, y] of [['x0', 'y0'], ['x1', 'y1'], ...(seg.t === 'C' ? [['c1x', 'c1y'], ['c2x', 'c2y']] : [])]) {
        out[x] = seg[x] * c + seg[y] * s; out[y] = -seg[x] * s + seg[y] * c;
      }
      return out;
    });
  }
  const toSX = (wx) => (wx - cam.x) * cam.s + W / 2;
  const toSY = (wy) => (wy - cam.y) * cam.s + H / 2;
  const toWX = (sx) => (sx - W / 2) / cam.s + cam.x;
  const toWY = (sy) => (sy - H / 2) / cam.s + cam.y;

  function bounds() {
    if (!L || !L.nodes.length) return { x0: 0, x1: 1, y0: 0, y1: 1 };
    let x0 = -20, x1 = L.maxCol * C.colW + (L.head >= 0 ? C.tail + 30 : 20);
    if (L.stubs.length) x0 = -C.colW * 1.6;
    let y0 = L.minSlot * gap - gap * 0.75, y1 = L.maxSlot * gap + gap * 0.75;
    if (vertical()) {
      x0 = L.minSlot * gap - gap * 0.75; x1 = L.maxSlot * gap + gap * 0.75;
      y0 = -C.tail - 30; y1 = L.maxCol * C.colW + (L.stubs.length ? 90 : 30);
    }
    for (const lb of labels) if (!lb.hidden) { x0 = Math.min(x0, lb.x - 8); x1 = Math.max(x1, lb.x + lb.w + 8); y0 = Math.min(y0, lb.y - 6); y1 = Math.max(y1, lb.y + lb.h + 6); }
    return { x0, x1, y0, y1 };
  }
  function homeScale() { return G.clamp(((vertical() ? H : W) - 40) / (12 * C.colW + C.tail + 70), 0.8, 1); }
  function computeGap() {
    if (!L) return C.laneGap;
    const span = L.maxSlot - L.minSlot;
    if (vertical()) return Math.round(G.clamp(((W || 700) - 80) / Math.max(1, span + 1.5), 110, 190));
    if (!span) return C.laneGap;
    const avail = (H || 400) / homeScale() - 70;
    return Math.round(G.clamp(avail / (span + 1.3), C.minLaneGap, C.laneGap));
  }
  function homeView() {
    const b = bounds();
    // the newest ~12 columns at the base scale; a short history that fits is shown larger (up to 1.3x)
    const s0 = homeScale(), sFit = Math.min((W - 60) / (b.x1 - b.x0), (H - 60) / (b.y1 - b.y0));
    const s = sFit > s0 ? Math.min(sFit, 1.3) : s0;
    const vw = W / s, vh = H / s;
    let x, y;
    if (b.x1 - b.x0 <= vw) x = (b.x0 + b.x1) / 2;
    else {
      const hx = L.head >= 0 ? nx(L.head) : L.maxCol * C.colW;
      x = Math.max(b.x0 + vw / 2, hx + C.tail + 50 - vw / 2);
    }
    if (b.y1 - b.y0 <= vh) y = (b.y0 + b.y1) / 2;
    else y = G.clamp(L.head >= 0 ? ny(L.head) : 0, b.y0 + vh / 2, b.y1 - vh / 2);
    if (vertical()) {
      x = (b.x0 + b.x1) / 2;
      y = b.y1 - b.y0 <= vh ? (b.y0 + b.y1) / 2 : b.y0 + vh / 2 - 10;
    }
    return { x, y, s };
  }
  function fitView() {
    const b = bounds();
    const s = G.clamp(Math.min((W - 48) / (b.x1 - b.x0), (H - 56) / (b.y1 - b.y0)), C.minScale, 1.6);
    return { x: (b.x0 + b.x1) / 2, y: (b.y0 + b.y1) / 2, s };
  }
  function clampCam() {
    if (!L) return;
    const b = bounds(), m = 70 / cam.s, hw = W / 2 / cam.s, hh = H / 2 / cam.s;
    cam.x = G.clamp(cam.x, b.x0 - hw + m, Math.max(b.x0 - hw + m, b.x1 + hw - m));
    cam.y = G.clamp(cam.y, b.y0 - hh + m, Math.max(b.y0 - hh + m, b.y1 + hh - m));
  }
  function moveCamera(v, ms) {
    zoomT = null; inertia = null;
    const d = reduce() ? 0 : (ms == null ? 480 : ms);
    if (!d) { cam.x = v.x; cam.y = v.y; cam.s = v.s; camTw = null; draw(); return; }
    camTw = { fx: cam.x, fy: cam.y, fs: cam.s, tx: v.x, ty: v.y, ts: v.s, t0: performance.now(), d };
    kick();
  }

  // ------------------------------------------------------------ animation state
  function nodeDisp(i, now) {
    const a = anim[i];
    let x = nx(i), y = ny(i), al = 1, sc = 1, u = 1;
    if (a) {
      if (a.pd && now < a.pt0 + a.pd) {
        const e = G.ease.inOutCubic(G.clamp((now - a.pt0) / a.pd, 0, 1));
        x = a.fx + (x - a.fx) * e; y = a.fy + (y - a.fy) * e;
      }
      if (a.ad) {
        const t = G.clamp((now - a.at0) / a.ad, 0, 1);
        u = t;
        if (a.ak === 'pop') { sc = t <= 0 ? 0 : G.ease.outBack(t); al = Math.min(1, t * 2.2); }
        else if (a.ak === 'slide') { const e = G.ease.outCubic(t); al = e; if (vertical()) y -= (1 - e) * C.colW * 1.4; else x += (1 - e) * C.colW * 1.4; }
        else al = G.ease.outCubic(t);
      }
    }
    return { x, y, a: al, sc, u };
  }
  let D = null;                        // per-frame display cache
  function computeDisplay(now) {
    const n = L ? L.nodes.length : 0;
    if (!D || D.x.length !== n) D = { x: new Float32Array(n), y: new Float32Array(n), a: new Float32Array(n), sc: new Float32Array(n), u: new Float32Array(n) };
    let moving = false;
    for (let i = 0; i < n; i++) {
      const d = nodeDisp(i, now);
      D.x[i] = d.x; D.y[i] = d.y; D.a[i] = d.a; D.sc[i] = d.sc; D.u[i] = d.u;
      if (d.u < 1 || d.x !== nx(i) || d.y !== ny(i)) moving = true;
    }
    return moving;
  }

  // ------------------------------------------------------------ frame loop
  function kick() { if (!paused && !hiddenDoc && !raf) raf = requestAnimationFrame(frame); }
  function stepCamera(now, dt) {
    let busy = false;
    if (camTw) {
      const t = G.clamp((now - camTw.t0) / camTw.d, 0, 1), e = G.ease.inOutCubic(t);
      // zoom interpolated in log space so the motion feels uniform
      cam.s = Math.exp(Math.log(camTw.fs) + (Math.log(camTw.ts) - Math.log(camTw.fs)) * e);
      cam.x = camTw.fx + (camTw.tx - camTw.fx) * e; cam.y = camTw.fy + (camTw.ty - camTw.fy) * e;
      if (t >= 1) camTw = null; else busy = true;
    }
    if (zoomT) {
      const k = reduce() ? 1 : G.approach(dt, 65);
      cam.s += (zoomT.s - cam.s) * k;
      if (Math.abs(zoomT.s - cam.s) / zoomT.s < 0.002) cam.s = zoomT.s;
      cam.x = zoomT.wx - (zoomT.ax - W / 2) / cam.s; cam.y = zoomT.wy - (zoomT.ay - H / 2) / cam.s;
      clampCam();
      if (cam.s === zoomT.s) zoomT = null; else busy = true;
    }
    if (inertia) {
      cam.x -= inertia.vx * dt / cam.s; cam.y -= inertia.vy * dt / cam.s;
      const f = Math.exp(-dt / 300);
      inertia.vx *= f; inertia.vy *= f;
      const bx = cam.x, by = cam.y;
      clampCam();
      if (bx !== cam.x) inertia.vx = 0;
      if (by !== cam.y) inertia.vy = 0;
      if (Math.hypot(inertia.vx, inertia.vy) < 0.012) inertia = null; else busy = true;
    }
    return busy;
  }
  function frame(ts) {
    raf = 0;
    if (paused || hiddenDoc) return;
    const now = performance.now();
    const dt = lastTs ? Math.min(50, now - lastTs) : 16.7;
    lastTs = now;
    let busy = stepCamera(now, dt);
    if (now < animUntil) busy = true;
    draw(now);
    perf.frames++; perf.lastFrameTs = ts;
    if (busy) raf = requestAnimationFrame(frame);
    else { lastTs = 0; ghosts = ghosts.filter((g) => now < g.t0 + C.fadeMs); }
  }

  // ------------------------------------------------------------ drawing primitives
  function rr(x, y, w, h, r) {
    r = Math.min(r, h / 2, w / 2);
    ctx.moveTo(x + r, y); ctx.lineTo(x + w - r, y); ctx.arcTo(x + w, y, x + w, y + r, r);
    ctx.lineTo(x + w, y + h - r); ctx.arcTo(x + w, y + h, x + w - r, y + h, r);
    ctx.lineTo(x + r, y + h); ctx.arcTo(x, y + h, x, y + h - r, r);
    ctx.lineTo(x, y + r); ctx.arcTo(x, y, x + r, y, r); ctx.closePath();
  }
  function addSegs(path, segs) {
    let first = true;
    for (const s of segs) {
      if (first) { path.moveTo(s.x0, s.y0); first = false; }
      if (s.t === 'L') path.lineTo(s.x1, s.y1);
      else path.bezierCurveTo(s.c1x, s.c1y, s.c2x, s.c2y, s.x1, s.y1);
    }
  }
  function branchIcon(x, y, sz, color, lw) {   // git-branch glyph, (x, y) = top-left of an sz box
    const u = sz / 16;
    ctx.save();
    ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = lw; ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.moveTo(x + 5 * u, y + 5.6 * u); ctx.lineTo(x + 5 * u, y + 10.4 * u);
    ctx.moveTo(x + 11 * u, y + 5.6 * u); ctx.bezierCurveTo(x + 11 * u, y + 9 * u, x + 5 * u, y + 8 * u, x + 5 * u, y + 10.6 * u);
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(x + 5 * u, y + 3.6 * u, Math.max(0.01, 2 * u), 0, Math.PI * 2);
    ctx.moveTo(x + 5 * u + 2 * u, y + 12.4 * u); ctx.arc(x + 5 * u, y + 12.4 * u, Math.max(0.01, 2 * u), 0, Math.PI * 2);
    ctx.moveTo(x + 11 * u + 2 * u, y + 3.6 * u); ctx.arc(x + 11 * u, y + 3.6 * u, Math.max(0.01, 2 * u), 0, Math.PI * 2);
    ctx.stroke();
    ctx.restore();
  }
  function tagIcon(x, y, sz, color, lw) {
    const u = sz / 16;
    ctx.save();
    ctx.strokeStyle = color; ctx.lineWidth = lw; ctx.lineJoin = 'round';
    ctx.beginPath();
    ctx.moveTo(x + 2 * u, y + 2.5 * u); ctx.lineTo(x + 8.5 * u, y + 2.5 * u); ctx.lineTo(x + 14 * u, y + 8 * u);
    ctx.lineTo(x + 8.5 * u, y + 13.5 * u); ctx.lineTo(x + 2 * u, y + 8 * u); ctx.closePath();
    ctx.stroke();
    ctx.beginPath(); ctx.arc(x + 5.6 * u, y + 6 * u, Math.max(0.01, 1.2 * u), 0, Math.PI * 2); ctx.fillStyle = color; ctx.fill();
    ctx.restore();
  }

  // level-of-detail fades by zoom
  const lod = (s, a, b) => G.clamp((s - a) / (b - a), 0, 1);

  function draw(now) {
    now = now || performance.now();
    const t0 = performance.now();
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = pal.bgCss;
    ctx.fillRect(0, 0, W, H);
    if (!L || !L.nodes.length || viewState !== 'data') { updateOverlays(now); return; }
    const moving = computeDisplay(now);
    const s = cam.s;
    ctx.setTransform(dpr * s, 0, 0, dpr * s, dpr * (W / 2 - cam.x * s), dpr * (H / 2 - cam.y * s));
    const pad = 90 / s;
    const vx0 = toWX(0) - pad, vx1 = toWX(W) + pad, vy0 = toWY(0) - pad, vy1 = toWY(H) + pad;
    const N = L.nodes;
    const lineW = 2 * Math.pow(s, 0.35) / s * (s < 0.4 ? 0.7 : 1);
    const collapse = s < 0.32;

    // ---- edges, batched per colour
    if (moving || !edgeCache) {
      const cache = new Array(L.edges.length);
      for (let k = 0; k < L.edges.length; k++) {
        const e = L.edges[k];
        let segs = routedEdge(e, now);
        if (D.u[e.c] < 1 && anim[e.c] && anim[e.c].ak !== 'fade') segs = G.partialSegments(segs, G.ease.outCubic(D.u[e.c]));
        cache[k] = segs;
      }
      edgeCache = moving ? null : cache;
      drawEdges(cache, vx0, vx1, vy0, vy1, lineW, collapse);
    } else drawEdges(edgeCache, vx0, vx1, vy0, vy1, lineW, collapse);

    // ---- "older history" stubs
    for (const st of L.stubs) {
      const i = st.node;
      if (D.x[i] < vx0 - 80 || D.x[i] > vx1 + 80 || D.y[i] < vy0 || D.y[i] > vy1) continue;
      ctx.save();
      const c = laneCol(N[i].lane), x = D.x[i], y = D.y[i];
      if (vertical()) { ctx.translate(x, y); ctx.rotate(-Math.PI / 2); ctx.translate(-x, -y); }
      const g = ctx.createLinearGradient(x, y, x - 66, y);
      g.addColorStop(0, G.rgba(c.line, 0.85 * D.a[i])); g.addColorStop(1, G.rgba(c.line, 0));
      ctx.strokeStyle = g; ctx.lineWidth = lineW; ctx.lineCap = 'round';
      ctx.setLineDash(st.first ? [] : [3 / s, 3 / s]);
      ctx.beginPath();
      if (st.first) { ctx.moveTo(x, y); ctx.lineTo(x - 66, y); }
      else { const dy = (N[i].slot <= 0 ? -1 : 1) * gap * 0.45; ctx.moveTo(x, y); ctx.bezierCurveTo(x - 24, y, x - 30, y + dy, x - 56, y + dy); }
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.restore();
    }

    // ---- tail from HEAD to the working tree
    if (L.head >= 0) drawTail(now);

    // ---- ghosts (vanished commits)
    for (const g of ghosts) {
      const t = G.clamp((now - g.t0) / C.fadeMs, 0, 1);
      if (t >= 1) continue;
      ctx.globalAlpha = 1 - t;
      ctx.fillStyle = g.color;
      ctx.beginPath(); ctx.arc(g.x, g.y, Math.max(0.01, g.r * (1 - 0.4 * t)), 0, Math.PI * 2); ctx.fill();
      ctx.globalAlpha = 1;
    }

    // ---- dots
    drawDots(now, vx0, vx1, vy0, vy1, collapse);

    // ---- labels
    drawLabels(now, vx0, vx1, vy0, vy1);
    if (collapse) drawRunChips(vx0, vx1);

    perf.drawMs = performance.now() - t0;
    updateOverlays(now);
  }

  function drawEdges(cache, vx0, vx1, vy0, vy1, lineW, collapse) {
    const groups = new Map();
    const faint = [];
    const N = L.nodes;
    for (let k = 0; k < L.edges.length; k++) {
      const e = L.edges[k], segs = cache[k];
      if (!segs || !segs.length) continue;
      const xa = Math.min(D.x[e.p], D.x[e.c]), xb = Math.max(D.x[e.p], D.x[e.c]);
      if (xb < vx0 || xa > vx1) continue;
      const ya = Math.min(D.y[e.p], D.y[e.c]), yb = Math.max(D.y[e.p], D.y[e.c]);
      if (yb < vy0 || ya > vy1) continue;
      const a = Math.min(D.a[e.p], D.a[e.c] > 0 ? 1 : 0, anim[e.c] && anim[e.c].ak === 'fade' ? D.a[e.c] : 1);
      if (a < 0.999) { faint.push([e, segs, a]); continue; }
      const key = L.lanes[e.track].main ? -1 : L.lanes[e.track].color;
      let g = groups.get(key);
      if (!g) { g = { lane: e.track, path: new Path2D(), hi: null }; groups.set(key, g); }
      addSegs(g.path, segs);
      void N;
    }
    ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    const glow = pal.dark && cam.s > 0.3;
    for (const g of groups.values()) {
      const c = laneCol(g.lane);
      if (glow) { ctx.strokeStyle = c.glow; ctx.lineWidth = lineW * 3.6; ctx.stroke(g.path); }
      ctx.strokeStyle = c.css; ctx.lineWidth = lineW * (collapse ? 1.6 : 1); ctx.stroke(g.path);
    }
    for (const [e, segs, a] of faint) {
      const c = laneCol(e.track);
      ctx.globalAlpha = a;
      const p = new Path2D(); addSegs(p, segs);
      if (glow) { ctx.strokeStyle = c.glow; ctx.lineWidth = lineW * 3.6; ctx.stroke(p); }
      ctx.strokeStyle = c.css; ctx.lineWidth = lineW; ctx.stroke(p);
      ctx.globalAlpha = 1;
    }
    // hovered commit: brighten its own edges
    if (hover >= 0 && hover < N.length) {
      const e0 = hoverEase(performance.now());
      if (e0 > 0) {
        ctx.globalAlpha = 0.9 * e0;
        for (let k = 0; k < L.edges.length; k++) {
          const e = L.edges[k];
          if (e.p !== hover && e.c !== hover) continue;
          const segs = cache[k]; if (!segs || !segs.length) continue;
          const p = new Path2D(); addSegs(p, segs);
          const c = laneCol(e.track);
          ctx.strokeStyle = c.light; ctx.lineWidth = lineW * 1.5; ctx.stroke(p);
        }
        ctx.globalAlpha = 1;
      }
    }
  }

  function hoverEase(now) {
    if (hover < 0) return 0;
    if (reduce()) return 1;
    return G.ease.outCubic(G.clamp((now - hoverT0) / 120, 0, 1));
  }
  function unhoverEase(now) {
    if (unhover < 0 || reduce()) return 0;
    return 1 - G.ease.outCubic(G.clamp((now - unhoverT0) / 120, 0, 1));
  }
  function dotRadius(i) {
    const nd = L.nodes[i];
    if (i === L.head) return C.headR;
    if (nd.merge) return C.mergeR;
    if (nd.tip && !L.lanes[nd.lane].main) return C.mergeR - 0.5;
    return C.dotR;
  }
  function drawDots(now, vx0, vx1, vy0, vy1, collapse) {
    const N = L.nodes, s = cam.s;
    const shrink = s < 0.55 ? 0.82 : 1;
    const minR = 1.9 / s;
    const bgPath = new Path2D();
    const fills = new Map();
    const special = [];
    for (let i = 0; i < N.length; i++) {
      const x = D.x[i], y = D.y[i];
      if (x < vx0 || x > vx1 || y < vy0 || y > vy1) continue;
      const nd = N[i];
      if (collapse && nd.run && nd.run.nodes.length >= C.runMin && i !== nd.run.from && i !== nd.run.to) continue;
      const ring = nd.merge || i === L.head || (nd.tip && !L.lanes[nd.lane].main);
      if (ring || D.a[i] < 0.999 || D.sc[i] !== 1 || i === hover || i === unhover || i === selected || (kbFocus && i === cursor)) { special.push(i); continue; }
      const r = Math.max(C.dotR * shrink, minR);
      bgPath.moveTo(x + r + 1.5, y); bgPath.arc(x, y, r + 1.5, 0, Math.PI * 2);
      const key = nd.lane;
      const ck = L.lanes[key].main ? -1 : L.lanes[key].color;
      let f = fills.get(ck);
      if (!f) { f = { lane: key, path: new Path2D() }; fills.set(ck, f); }
      f.path.moveTo(x + r, y); f.path.arc(x, y, r, 0, Math.PI * 2);
    }
    ctx.fillStyle = pal.bgCss; ctx.fill(bgPath);
    for (const f of fills.values()) { ctx.fillStyle = laneCol(f.lane).css; ctx.fill(f.path); }
    // rings, HEAD, animated, hovered, selected: one by one, hovered last
    special.sort((a, b) => (a === hover) - (b === hover));
    const he = hoverEase(now), ue = unhoverEase(now);
    for (const i of special) {
      const nd = N[i], c = laneCol(nd.lane), x = D.x[i], y = D.y[i];
      const hv = i === hover ? he : (i === unhover ? ue : 0);
      const sc = D.sc[i] * (1 + 0.35 * hv);
      if (sc <= 0.01) continue;
      const r = Math.max(dotRadius(i) * shrink, minR) * sc;
      ctx.globalAlpha = D.a[i];
      if (hv > 0) { ctx.fillStyle = c.halo; ctx.beginPath(); ctx.arc(x, y, Math.max(0.01, r + 7 * hv), 0, Math.PI * 2); ctx.fill(); }
      if (i === L.head) {
        ctx.fillStyle = pal.headGlow; ctx.beginPath(); ctx.arc(x, y, Math.max(0.01, r + 8), 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = pal.bgCss; ctx.beginPath(); ctx.arc(x, y, Math.max(0.01, r + 5.5), 0, Math.PI * 2); ctx.fill();
        ctx.strokeStyle = c.soft; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(x, y, Math.max(0.01, r + 4), 0, Math.PI * 2); ctx.stroke();
        ctx.strokeStyle = c.css; ctx.lineWidth = 2.4; ctx.beginPath(); ctx.arc(x, y, Math.max(0.01, r), 0, Math.PI * 2); ctx.stroke();
        ctx.fillStyle = c.light; ctx.beginPath(); ctx.arc(x, y, Math.max(0.01, r - 3.3), 0, Math.PI * 2); ctx.fill();
      } else if (nd.merge || (nd.tip && !L.lanes[nd.lane].main)) {
        if (nd.merge) { ctx.fillStyle = c.halo; ctx.beginPath(); ctx.arc(x, y, Math.max(0.01, r + 4.5), 0, Math.PI * 2); ctx.fill(); }
        ctx.fillStyle = pal.bgCss; ctx.beginPath(); ctx.arc(x, y, Math.max(0.01, r + 1.5), 0, Math.PI * 2); ctx.fill();
        ctx.strokeStyle = c.css; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(x, y, Math.max(0.01, r - 1), 0, Math.PI * 2); ctx.stroke();
        ctx.fillStyle = nd.merge ? c.light : c.css; ctx.beginPath(); ctx.arc(x, y, Math.max(1, r - 3.6), 0, Math.PI * 2); ctx.fill();
      } else {
        ctx.fillStyle = pal.bgCss; ctx.beginPath(); ctx.arc(x, y, Math.max(0.01, r + 1.5), 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = c.css; ctx.beginPath(); ctx.arc(x, y, Math.max(0.01, r), 0, Math.PI * 2); ctx.fill();
      }
      if (i === selected) {
        ctx.strokeStyle = pal.accent; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(x, y, Math.max(0.01, r + 5.5), 0, Math.PI * 2); ctx.stroke();
      }
      if (kbFocus && i === cursor) {
        ctx.strokeStyle = pal.accent; ctx.lineWidth = 1.5; ctx.setLineDash([3, 2.5]);
        ctx.beginPath(); ctx.arc(x, y, Math.max(0.01, r + 9), 0, Math.PI * 2); ctx.stroke(); ctx.setLineDash([]);
      }
      ctx.globalAlpha = 1;
    }
  }

  function tailGeom() {
    const i = L.head;
    const x = D ? D.x[i] : nx(i), y = D ? D.y[i] : ny(i);
    const a = viewAngle(performance.now()), dx = Math.cos(a), dy = -Math.sin(a);
    return { x0: x + dx * (C.headR + 6), y0: y + dy * (C.headR + 6),
      x: x + dx * C.tail, y: y + dy * C.tail, dx, dy };
  }
  function drawTail(now) {
    const i = L.head, tg = tailGeom();
    const a = D.a[i] * (labelAlpha(now));
    if (a <= 0) return;
    ctx.globalAlpha = a;
    ctx.strokeStyle = pal.tail; ctx.lineWidth = 1.6; ctx.setLineDash([5, 5]); ctx.lineCap = 'butt';
    ctx.beginPath(); ctx.moveTo(tg.x0, tg.y0); ctx.lineTo(tg.x - tg.dx * 8.5, tg.y - tg.dy * 8.5); ctx.stroke();
    if (L.dirty) {
      ctx.setLineDash([2.6, 2.2]); ctx.strokeStyle = pal.warn; ctx.lineWidth = 1.6;
      ctx.beginPath(); ctx.arc(tg.x, tg.y, Math.max(0.01, 6.5), 0, Math.PI * 2); ctx.stroke(); ctx.setLineDash([]);
      const txt = String(L.dirty > 99 ? '99+' : L.dirty), fs = 10;
      ctx.font = '700 ' + fs + 'px ' + pal.ui;
      const w = Math.max(15, ctx.measureText(txt).width + 8), h = 14;
      const bx = tg.x + 5, by = tg.y - 17;
      ctx.fillStyle = pal.warn; ctx.beginPath(); rr(bx, by, w, h, 7); ctx.fill();
      ctx.fillStyle = pal.dark ? '#1a1a1a' : '#ffffff'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(txt, bx + w / 2, by + h / 2 + 0.5);
      ctx.textAlign = 'left';
    } else {
      ctx.setLineDash([]);
      ctx.fillStyle = pal.tailDot; ctx.beginPath(); ctx.arc(tg.x, tg.y, Math.max(0.01, 5.5), 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = pal.bgCss; ctx.beginPath(); ctx.arc(tg.x, tg.y, Math.max(0.01, 3.2), 0, Math.PI * 2); ctx.fill();
    }
    ctx.setLineDash([]);
    ctx.globalAlpha = 1;
  }

  function labelAlpha(now) {
    if (!labelD) return 1;
    return G.ease.outCubic(G.clamp((now - labelT0) / labelD, 0, 1));
  }
  function labelPos(lb, now) {
    let x = lb.x, y = lb.y;
    const glide = labelGlides.get(labelKey(lb));
    if (glide) {
      const t = reduce() ? 1 : G.clamp((now - glide.t0) / 400, 0, 1), e = G.ease.inOutCubic(t);
      if (t < 1) return { x: glide.x + (x - glide.x) * e, y: glide.y + (y - glide.y) * e };
      labelGlides.delete(labelKey(lb));
    }
    if (D && lb.node >= 0 && lb.node < D.x.length) { x += D.x[lb.node] - lb.ax; y += D.y[lb.node] - lb.ay; }
    if (lb.kind === 'head' && headGlide) {
      const t = reduce() ? 1 : G.clamp((now - headGlide.t0) / headGlide.d, 0, 1);
      const e = G.ease.inOutCubic(t);
      x += (headGlide.fx - lb.x) * (1 - e); y += (headGlide.fy - lb.y) * (1 - e);
      if (t >= 1) headGlide = null;
    }
    return { x, y };
  }
  function labelVisible(lb, s) {
    if (lb.hidden) return 0;
    switch (lb.kind) {
      case 'head': return 1;
      case 'pill': return lod(s, 0.36, 0.46);
      case 'callout': return lod(s, 0.58, 0.68);
      case 'tag': case 'ref': return lod(s, 0.5, 0.6);
      case 'stub': return lod(s, 0.45, 0.55);
      default: return 1;
    }
  }
  function drawLabels(now, vx0, vx1, vy0, vy1) {
    const s = cam.s, la = labelAlpha(now);
    if (la <= 0) return;
    const order = { stub: 0, tag: 1, ref: 1, callout: 2, pill: 3, head: 4 };
    const list = labels.slice().sort((a, b) => order[a.kind] - order[b.kind]);
    ctx.textBaseline = 'middle'; ctx.textAlign = 'left';
    for (const lb of list) {
      const v = labelVisible(lb, s) * la * (lb.node >= 0 && D ? D.a[lb.node] : 1);
      if (v <= 0.01) continue;
      const p = labelPos(lb, now);
      if (p.x > vx1 || p.x + lb.w < vx0 || p.y > vy1 || p.y + lb.h < vy0) continue;
      ctx.globalAlpha = v;
      if (lb.kind === 'pill') drawPill(lb, p);
      else if (lb.kind === 'head') drawHead(lb, p);
      else if (lb.kind === 'callout') drawCallout(lb, p);
      else if (lb.kind === 'tag' || lb.kind === 'ref') drawChip(lb, p);
      else if (lb.kind === 'stub') {
        ctx.fillStyle = pal.muted; ctx.globalAlpha = v * 0.8;
        ctx.font = 'italic 400 ' + (lb.h * 0.75).toFixed(2) + 'px ' + pal.ui;
        ctx.fillText(lb.text, p.x + 2, p.y + lb.h / 2);
      }
      ctx.globalAlpha = 1;
    }
  }
  function drawPill(lb, p) {
    const c = laneCol(lb.lane), x = p.x, y = p.y, w = lb.w, h = lb.h, k = h / 24;
    const dx = (D ? D.x[lb.node] : lb.ax), dy = (D ? D.y[lb.node] : lb.ay);
    // connector: a thin curve from the dot to the pill's nearest edge
    ctx.strokeStyle = c.soft; ctx.lineWidth = 1.2; ctx.lineCap = 'round';
    ctx.beginPath();
    const cy = y + h / 2, r = C.dotR + 1.5;
    const sy = dy + (cy < dy ? -r : r);
    if (x > dx + 4) { ctx.moveTo(dx, sy); ctx.quadraticCurveTo(dx, cy, x, cy); }
    else { const ex = G.clamp(dx, x + 8, x + w - 8), ey = cy < dy ? y + h : y; ctx.moveTo(dx, sy); ctx.bezierCurveTo(dx, (sy + ey) / 2, ex, (sy + ey) / 2, ex, ey); }
    ctx.stroke();
    ctx.fillStyle = c.fill; ctx.strokeStyle = lb.dashed ? pal.calloutBorder : c.css; ctx.lineWidth = 1;
    if (lb.dashed) ctx.setLineDash([3.5, 2.5]);
    ctx.beginPath(); rr(x + 0.5, y + 0.5, w - 1, h - 1, 6.5 * k); ctx.fill(); ctx.stroke();
    ctx.setLineDash([]);
    const tc = lb.dashed ? pal.muted : c.text;
    branchIcon(x + 9 * k, y + h / 2 - 6.5 * k, 13 * k, tc, 1.35 * k);
    ctx.fillStyle = tc;
    ctx.font = '600 ' + (13 * k).toFixed(2) + 'px ' + pal.ui;
    let tx = x + 9 * k + 13 * k + 6;
    ctx.fillText(lb.text, tx, y + h / 2 + 0.5);
    tx += measure(lb.text, 13 * k, 600) + 6;
    if (lb.extra) {
      const t = '+' + lb.extra, cw = measure(t, 11 * k, 600) + 10, ch = 15 * k;
      ctx.fillStyle = c.chip; ctx.beginPath(); rr(tx, y + (h - ch) / 2, cw, ch, ch / 2); ctx.fill();
      ctx.fillStyle = tc; ctx.font = '600 ' + (11 * k).toFixed(2) + 'px ' + pal.ui;
      ctx.fillText(t, tx + 5, y + h / 2 + 0.5);
      tx += cw + 6;
    }
    if (lb.current) { ctx.fillStyle = tc; ctx.beginPath(); ctx.arc(tx + 2, y + h / 2, Math.max(0.01, 3 * k), 0, Math.PI * 2); ctx.fill(); }
  }
  function drawHead(lb, p) {
    const x = p.x, y = p.y, w = lb.w, h = lb.h;
    const below = lb.side > 0;
    ctx.fillStyle = pal.head;
    ctx.shadowColor = pal.dark ? 'rgba(0,0,0,0.45)' : 'rgba(0,0,0,0.18)'; ctx.shadowBlur = 6; ctx.shadowOffsetY = 1.5;
    ctx.beginPath(); rr(x, y, w, h, 5); ctx.fill();
    ctx.shadowColor = 'transparent'; ctx.shadowBlur = 0; ctx.shadowOffsetY = 0;
    ctx.fillStyle = pal.headFg; ctx.textAlign = 'center';
    ctx.font = '700 ' + (h * 0.575).toFixed(2) + 'px ' + pal.ui;
    ctx.fillText('HEAD', x + w / 2, y + h / 2 + 0.5);
    ctx.textAlign = 'left';
    // chevron pointing at the commit
    const cx = x + w / 2, cy = below ? y - 5 : y + h + 5;
    ctx.strokeStyle = pal.head; ctx.lineWidth = 1.8; ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    ctx.beginPath();
    if (lb.vertical) {
      const ex = lb.side > 0 ? x - 5 : x + w + 5, ey = y + h / 2, d = lb.side > 0 ? -1 : 1;
      ctx.moveTo(ex - d * 1.5, ey - 4); ctx.lineTo(ex + d * 2, ey); ctx.lineTo(ex - d * 1.5, ey + 4);
    }
    else if (below) { ctx.moveTo(cx - 4, cy + 1.5); ctx.lineTo(cx, cy - 2); ctx.lineTo(cx + 4, cy + 1.5); }
    else { ctx.moveTo(cx - 4, cy - 1.5); ctx.lineTo(cx, cy + 2); ctx.lineTo(cx + 4, cy - 1.5); }
    ctx.stroke();
  }
  function drawCallout(lb, p) {
    const x = p.x, y = p.y, w = lb.w, h = lb.h;
    const below = lb.side > 0;
    if (lb.vertical) {
      ctx.fillStyle = pal.calloutBg; ctx.strokeStyle = pal.calloutBorder; ctx.lineWidth = 1;
      ctx.beginPath(); rr(x, y, w, h, 6); ctx.fill(); ctx.stroke();
      const ex = lb.side > 0 ? x : x + w, ey = G.clamp(D.y[lb.node], y + 6, y + h - 6);
      ctx.beginPath(); ctx.moveTo(ex, ey - 4); ctx.lineTo(ex + (lb.side > 0 ? -6 : 6), ey); ctx.lineTo(ex, ey + 4); ctx.fill(); ctx.stroke();
      ctx.fillStyle = pal.calloutText; ctx.font = '400 ' + (h * 12 / 22).toFixed(2) + 'px ' + pal.ui;
      ctx.fillText(lb.text, x + 10, y + h / 2 + 0.5); return;
    }
    const px = lb.px + (p.x - lb.x);
    ctx.fillStyle = pal.calloutBg; ctx.strokeStyle = pal.calloutBorder; ctx.lineWidth = 1;
    ctx.beginPath();
    const r = 6, by = below ? y : y + h, ty = below ? y - 6 : y + h + 6;
    // rounded rect with a pointer on the edge facing the commit
    ctx.moveTo(x + r, y);
    if (below) { ctx.lineTo(px - 5, y); ctx.lineTo(px, ty); ctx.lineTo(px + 5, y); }
    ctx.lineTo(x + w - r, y); ctx.arcTo(x + w, y, x + w, y + r, r);
    ctx.lineTo(x + w, y + h - r); ctx.arcTo(x + w, y + h, x + w - r, y + h, r);
    if (!below) { ctx.lineTo(px + 5, by); ctx.lineTo(px, ty); ctx.lineTo(px - 5, by); }
    ctx.lineTo(x + r, y + h); ctx.arcTo(x, y + h, x, y + h - r, r);
    ctx.lineTo(x, y + r); ctx.arcTo(x, y, x + r, y, r); ctx.closePath();
    ctx.fill(); ctx.stroke();
    ctx.fillStyle = pal.calloutText;
    ctx.font = '400 ' + (h * 12 / 22).toFixed(2) + 'px ' + pal.ui;
    ctx.fillText(lb.text, x + 10, y + h / 2 + 0.5);
  }
  function drawChip(lb, p) {
    const x = p.x, y = p.y, w = lb.w, h = lb.h, k = h / 17;
    const isRef = lb.kind === 'ref', c = laneCol(lb.lane);
    ctx.fillStyle = pal.tagBg; ctx.strokeStyle = isRef && !lb.dashed ? c.soft : pal.tagBorder; ctx.lineWidth = 1;
    if (lb.dashed) ctx.setLineDash([3, 2]);
    ctx.beginPath(); rr(x + 0.5, y + 0.5, w - 1, h - 1, 4.5 * k); ctx.fill(); ctx.stroke();
    ctx.setLineDash([]);
    const ic = isRef && !lb.dashed ? c.text : pal.muted;
    if (isRef) branchIcon(x + 6 * k, y + h / 2 - 5 * k, 10 * k, ic, 1.1 * k); else tagIcon(x + 6 * k, y + h / 2 - 5 * k, 10 * k, ic, 1.1 * k);
    ctx.fillStyle = isRef && !lb.dashed ? c.text : pal.muted;
    ctx.font = '500 ' + (11 * k).toFixed(2) + 'px ' + pal.ui;
    ctx.fillText(lb.text, x + 6 * k + 10 * k + 4, y + h / 2 + 0.5);
  }
  function drawRunChips(vx0, vx1) {
    const s = cam.s;
    for (const r of L.runs) {
      if (vertical()) {
        const x = D.x[r.from], y0 = D.y[r.from], y1 = D.y[r.to], c = laneCol(r.lane);
        ctx.strokeStyle = c.css; ctx.lineWidth = 4.2 / s; ctx.lineCap = 'round';
        ctx.beginPath(); ctx.moveTo(x, y0); ctx.lineTo(x, y1); ctx.stroke();
        continue;
      }
      const xa = D.x[r.from], xb = D.x[r.to];
      if (xb < vx0 || xa > vx1) continue;
      const y = D.y[r.from], c = laneCol(r.lane);
      ctx.strokeStyle = c.css; ctx.lineWidth = 4.2 / s; ctx.lineCap = 'round';
      ctx.beginPath(); ctx.moveTo(xa, y); ctx.lineTo(xb, y); ctx.stroke();
      const t = r.count + ' commits', fs = 10.5 / s;
      ctx.font = '600 ' + fs.toFixed(2) + 'px ' + pal.ui;
      const w = ctx.measureText(t).width + 12 / s, h = 15 / s, cx = (xa + xb) / 2;
      if (xb - xa < w + 10 / s) continue;
      ctx.fillStyle = pal.tagBg; ctx.strokeStyle = c.soft; ctx.lineWidth = 1 / s;
      ctx.beginPath(); rr(cx - w / 2, y - h - 6 / s, w, h, h / 2); ctx.fill(); ctx.stroke();
      ctx.fillStyle = c.text; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(t, cx, y - h / 2 - 6 / s + 0.5 / s);
      ctx.textAlign = 'left';
    }
  }

  // ------------------------------------------------------------ DOM overlays (pulse, tooltip)
  function updateOverlays(now) {
    // HEAD pulse ring
    const showPulse = viewState === 'data' && L && L.head >= 0 && !reduce() && !paused && !hiddenDoc && pulseOn && D && D.a[L.head] > 0.99;
    if (showPulse) {
      const x = toSX(D.x[L.head]), y = toSY(D.y[L.head]);
      const vis = x > -30 && x < W + 30 && y > -30 && y < H + 30;
      const sz = (C.headR + 4) * 2 * cam.s * (cam.s < 0.55 ? 0.82 : 1);
      pulseEl.style.transform = 'translate(' + x.toFixed(1) + 'px,' + y.toFixed(1) + 'px) scale(' + (sz / 30).toFixed(3) + ')';
      pulseEl.style.opacity = vis ? '1' : '0';
      if (!pulseEl.classList.contains('on')) pulseEl.classList.add('on');
    } else if (pulseEl.classList.contains('on')) { pulseEl.classList.remove('on'); pulseEl.style.opacity = '0'; }
    if (tipEl.classList.contains('show') && tipFor) positionTip();
    void now;
  }
  function wakePulse() {
    if (reduce() || !L || L.head < 0) return;
    clearTimeout(pulseTimer);
    if (!pulseOn) { pulseOn = true; updateOverlays(performance.now()); }
    pulseTimer = setTimeout(() => { pulseOn = false; updateOverlays(performance.now()); }, 9000);
  }

  function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }
  function tipAnchor() {
    if (!tipFor || !L) return null;
    if (tipFor.kind === 'node') {
      const i = tipFor.i;
      if (i < 0 || i >= L.nodes.length) return null;
      const r = dotRadius(i) * cam.s * 1.35 + 6;
      const x = toSX(D ? D.x[i] : nx(i)), y = toSY(D ? D.y[i] : ny(i));
      return { x0: x - r, x1: x + r, y0: y - r, y1: y + r, cx: x };
    }
    if (tipFor.kind === 'label') {
      const lb = tipFor.lb, p = labelPos(lb, performance.now());
      const x0 = toSX(p.x), y0 = toSY(p.y);
      return { x0, x1: x0 + lb.w * cam.s, y0, y1: y0 + lb.h * cam.s, cx: x0 + lb.w * cam.s / 2 };
    }
    if (tipFor.kind === 'tail') {
      const tg = tailGeom(), x = toSX(tg.x), y = toSY(tg.y), r = 12;
      return { x0: x - r, x1: x + r, y0: y - r, y1: y + r, cx: x };
    }
    return null;
  }
  function positionTip() {
    const a = tipAnchor();
    if (!a) { hideTip(); return; }
    const w = tipEl.offsetWidth, h = tipEl.offsetHeight, m = 8;
    let x = a.cx - w / 2, y = a.y0 - h - 8;
    if (y < m) y = a.y1 + 8;                                   // flip below
    if (y + h > H - m) y = Math.max(m, H - m - h);
    x = G.clamp(x, m, Math.max(m, W - m - w));
    tipEl.style.transform = 'translate(' + Math.round(x) + 'px,' + Math.round(y) + 'px)';
  }
  function tipHtml(t) {
    if (t.kind === 'node') {
      const nd = L.nodes[t.i];
      const refs = nd.refs.filter((r) => (r.kind !== 'tag' || settings.showTags) && (r.kind !== 'remote' || settings.showRemote)).map((r) => r.name);
      const nowS = Date.now() / 1000;
      const kind = t.i === L.head ? 'HEAD' : (nd.merge ? (nd.parents.length + nd.absent.length > 2 ? 'octopus merge' : 'merge') : '');
      return '<div class="t-hash"><span>' + esc(nd.short) + '</span>' + (kind ? '<span class="t-kind">' + esc(kind) + '</span>' : '') + '</div>' +
        '<div class="t-subj">' + esc(nd.subject || '(no message)') + '</div>' +
        '<div class="t-time"><b>' + esc(settings.timeFormat === 'absolute' ? G.absTime(nd.ts) : G.relTime(nd.ts, nowS)) + '</b>' + '</div>' +
        '<div class="t-meta">' + esc(nd.author) + (refs.length ? ' · ' + esc(refs.slice(0, 4).join(', ')) + (refs.length > 4 ? ' …' : '') : '') + '</div>';
    }
    if (t.kind === 'label') {
      const lines = String(t.lb.full || t.lb.text).split('\n');
      return lines.map((l, k) => '<div class="t-line' + (k ? ' m' : '') + '">' + esc(l) + '</div>').join('');
    }
    if (t.kind === 'tail') {
      return L.dirty ? '<div class="t-line">' + L.dirty + ' uncommitted change' + (L.dirty === 1 ? '' : 's') + '</div><div class="t-line m">Working tree</div>'
        : '<div class="t-line">Working tree is clean</div>';
    }
    return '';
  }
  function sameTip(a, b) { return a && b && a.kind === b.kind && a.i === b.i && a.lb === b.lb; }
  function requestTip(t, immediate) {
    if (sameTip(t, tipFor) && (tipEl.classList.contains('show') || tipTimer)) return;
    clearTimeout(tipTimer); tipTimer = 0;
    tipFor = t;
    const show = () => {
      tipTimer = 0;
      if (!sameTip(t, tipFor)) return;
      tipEl.innerHTML = tipHtml(t);
      tipEl.setAttribute('aria-hidden', 'false');
      positionTip();
      tipEl.classList.add('show');
      if (t.kind === 'node') liveEl.textContent = L.nodes[t.i].short + ' ' + L.nodes[t.i].subject;
    };
    if (immediate || reduce()) show(); else tipTimer = setTimeout(show, C.hoverDelay);
  }
  function hideTip() {
    clearTimeout(tipTimer); tipTimer = 0; tipFor = null;
    tipEl.classList.remove('show'); tipEl.setAttribute('aria-hidden', 'true');
  }

  // ------------------------------------------------------------ hit testing
  function hitNode(sx, sy) {
    if (!L || viewState !== 'data') return -1;
    const wx = toWX(sx), wy = toWY(sy);
    const c0 = Math.round(vertical() ? L.maxCol - wy / C.colW : wx / C.colW);
    let best = -1, bd = Infinity;
    const lim = Math.max(10, 9 * cam.s + 3);
    const moving = performance.now() < animUntil;
    for (let c = moving ? 0 : c0 - 2; c <= (moving ? L.maxCol : c0 + 2); c++) {
      const list = colMap.get(c);
      if (!list) continue;
      for (const i of list) {
        const x = D ? D.x[i] : nx(i), y = D ? D.y[i] : ny(i);
        const d = Math.hypot((x - wx) * cam.s, (y - wy) * cam.s);
        if (d < bd) { bd = d; best = i; }
      }
    }
    return bd <= lim ? best : -1;
  }
  function hitLabel(sx, sy) {
    if (!L || viewState !== 'data') return null;
    const wx = toWX(sx), wy = toWY(sy), now = performance.now();
    for (let k = labels.length - 1; k >= 0; k--) {
      const lb = labels[k];
      if (labelVisible(lb, cam.s) < 0.5) continue;
      const p = labelPos(lb, now);
      if (wx >= p.x && wx <= p.x + lb.w && wy >= p.y && wy <= p.y + lb.h) return lb;
    }
    return null;
  }
  function hitTail(sx, sy) {
    if (!L || L.head < 0 || viewState !== 'data') return false;
    const tg = tailGeom();
    return Math.hypot(toSX(tg.x) - sx, toSY(tg.y) - sy) < Math.max(10, 9 * cam.s);
  }
  function setHover(i) {
    if (i === hover) return;
    const now = performance.now();
    if (hover >= 0) { unhover = hover; unhoverT0 = now; }
    hover = i; hoverT0 = now;
    if (i === unhover) unhover = -1;
    animUntil = Math.max(animUntil, now + 140);
    canvas.classList.toggle('over', i >= 0);
    kick();
  }

  // ------------------------------------------------------------ pointer / wheel / keys
  function localXY(e) { const r = canvas.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }
  function updateHoverAt(x, y) {
    const i = hitNode(x, y);
    setHover(i);
    if (i >= 0) { hoverLabel = null; hoverTail = false; requestTip({ kind: 'node', i }); return; }
    const lb = hitLabel(x, y);
    if (lb) { hoverLabel = lb; requestTip({ kind: 'label', lb, i: -2 }); canvas.classList.add('over'); return; }
    if (hitTail(x, y)) { hoverTail = true; requestTip({ kind: 'tail', i: -3 }); return; }
    hoverLabel = null; hoverTail = false;
    if (tipFor && !(kbFocus && tipFor.kind === 'node' && tipFor.i === cursor)) hideTip();
  }
  canvas.addEventListener('pointerdown', safe((e) => {
    if (e.button !== 0) return;
    const [x, y] = localXY(e);
    canvas.focus({ preventScroll: true });
    kbFocus = false;
    try { canvas.setPointerCapture(e.pointerId); } catch (err) { /* synthetic events */ }
    camTw = null; zoomT = null; inertia = null;
    press = { x, y, cx: cam.x, cy: cam.y, node: hitNode(x, y), moved: false, samples: [[performance.now(), x, y]] };
    wakePulse();
  }));
  canvas.addEventListener('pointermove', safe((e) => {
    const [x, y] = localXY(e);
    pointer.x = x; pointer.y = y; pointer.inside = true;
    if (!pulseOn) wakePulse();
    if (press) {
      const dx = x - press.x, dy = y - press.y;
      if (!press.moved && Math.hypot(dx, dy) > 4) { press.moved = true; canvas.classList.add('drag'); hideTip(); setHover(-1); camHome = false; }
      if (press.moved) {
        cam.x = press.cx - dx / cam.s; cam.y = press.cy - dy / cam.s;
        clampCam();
        const now = performance.now();
        press.samples.push([now, x, y]);
        while (press.samples.length > 2 && now - press.samples[0][0] > 90) press.samples.shift();
        kick();                                   // coalesced: drawn once in the next animation frame
      }
      return;
    }
    updateHoverAt(x, y);
  }));
  function endPress(e, cancel) {
    if (!press) return;
    const p = press; press = null;
    canvas.classList.remove('drag');
    try { canvas.releasePointerCapture(e.pointerId); } catch (err) { /* ignore */ }
    if (cancel) return;
    if (!p.moved) {
      if (p.node >= 0) openCommit(p.node);
      return;
    }
    const sm = p.samples, a = sm[0], b = sm[sm.length - 1], dt = b[0] - a[0];
    if (!reduce() && dt > 0 && performance.now() - b[0] < 60) {
      const vx = (b[1] - a[1]) / dt, vy = (b[2] - a[2]) / dt;
      if (Math.hypot(vx, vy) > 0.08) { inertia = { vx, vy }; lastTs = 0; kick(); }
    }
  }
  canvas.addEventListener('pointerup', safe((e) => endPress(e, false)));
  canvas.addEventListener('pointercancel', safe((e) => endPress(e, true)));
  canvas.addEventListener('pointerleave', safe(() => {
    pointer.inside = false;
    if (!press) { setHover(-1); if (!(kbFocus && tipFor && tipFor.kind === 'node')) hideTip(); }
  }));
  canvas.addEventListener('dblclick', safe((e) => {
    const [x, y] = localXY(e);
    if (hitNode(x, y) >= 0 || hitLabel(x, y)) return;
    camHome = false;
    moveCamera(fitView());
  }));
  canvas.addEventListener('wheel', safe((e) => {
    e.preventDefault();
    if (!L || viewState !== 'data') return;
    const [x, y] = localXY(e);
    const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? H : 1;
    let dx = e.deltaX * unit, dy = e.deltaY * unit;
    camTw = null; inertia = null; camHome = false;
    hideTip();
    if (e.shiftKey && !e.ctrlKey) { if (!dx) { dx = dy; dy = 0; } }
    if (!e.ctrlKey && (e.shiftKey || Math.abs(dx) > Math.abs(dy))) {
      zoomT = null;
      cam.x += dx / cam.s; cam.y += (e.shiftKey ? 0 : dy) / cam.s;
      clampCam(); kick();
      return;
    }
    const base = zoomT ? zoomT.s : cam.s;
    const ts = G.clamp(base * Math.exp(-G.clamp(dy, -300, 300) * 0.0022), C.minScale, C.maxScale);
    zoomT = { s: ts, ax: x, ay: y, wx: toWX(x), wy: toWY(y) };
    lastTs = 0;
    kick();
  }, { passive: false }));
  function zoomBy(f) {
    if (!L) return;
    camHome = false;
    const ts = G.clamp((zoomT ? zoomT.s : cam.s) * f, C.minScale, C.maxScale);
    if (reduce()) { cam.s = ts; clampCam(); draw(); return; }
    zoomT = { s: ts, ax: W / 2, ay: H / 2, wx: cam.x, wy: cam.y };
    lastTs = 0; kick();
  }
  function ensureVisible(i) {
    const x = toSX(nx(i)), y = toSY(ny(i)), m = 60;
    if (x >= m && x <= W - m && y >= m && y <= H - m) return;
    camHome = false;
    moveCamera({ x: x < m || x > W - m ? nx(i) : cam.x, y: y < m || y > H - m ? ny(i) : cam.y, s: cam.s }, 300);
  }
  function openCommit(i) {
    if (!L || i < 0) return;
    selected = i; cursor = i;
    post({ type: 'open-commit', hash: L.nodes[i].hash });
    draw();
  }
  canvas.addEventListener('keydown', safe((e) => {
    if (!L || viewState !== 'data') return;
    const dirs = { ArrowLeft: 'left', ArrowRight: 'right', ArrowUp: 'up', ArrowDown: 'down', Home: 'home', End: 'end' };
    if (dirs[e.key]) {
      e.preventDefault();
      kbFocus = true;
      const start = cursor >= 0 ? cursor : (selected >= 0 ? selected : (L.head >= 0 ? L.head : 0));
      cursor = (cursor < 0 && dirs[e.key] !== 'home' && dirs[e.key] !== 'end') ? start : G.navigate(L, start, dirs[e.key]);
      ensureVisible(cursor);
      requestTip({ kind: 'node', i: cursor }, true);
      draw(); wakePulse();
      return;
    }
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); if (cursor >= 0) openCommit(cursor); else if (L.head >= 0) { cursor = L.head; kbFocus = true; draw(); } return; }
    if (e.key === 'Escape') { setHover(-1); hideTip(); kbFocus = false; draw(); return; }
    if (e.key === '+' || e.key === '=') { e.preventDefault(); zoomBy(1.25); return; }
    if (e.key === '-' || e.key === '_') { e.preventDefault(); zoomBy(0.8); return; }
    if (e.key === '0') { e.preventDefault(); camHome = false; moveCamera(fitView()); }
  }));
  canvas.addEventListener('blur', () => { if (kbFocus) { kbFocus = false; hideTip(); draw(); } });

  // toolbar
  $('b-fit').addEventListener('click', safe(() => { camHome = false; moveCamera(fitView()); }));
  $('b-head').addEventListener('click', safe(() => jumpHead()));
  $('b-in').addEventListener('click', safe(() => zoomBy(1.25)));
  $('b-out').addEventListener('click', safe(() => zoomBy(0.8)));
  $('b-refresh').addEventListener('click', safe(() => { post({ type: 'refresh' }); $('b-refresh').classList.add('spin'); setTimeout(() => $('b-refresh').classList.remove('spin'), 900); }));
  function setChip(id, on) { const el = $(id); el.classList.toggle('on', on); el.setAttribute('aria-pressed', on ? 'true' : 'false'); }
  $('c-remote').addEventListener('click', safe(() => { settings.showRemote = !settings.showRemote; setChip('c-remote', settings.showRemote); relabel(); draw(); }));
  $('c-tags').addEventListener('click', safe(() => { settings.showTags = !settings.showTags; setChip('c-tags', settings.showTags); relabel(); draw(); }));
  $('state-btn').addEventListener('click', safe(() => post({ type: 'refresh' })));
  function jumpHead() {
    if (!L) return;
    camHome = true;
    moveCamera(homeView());
  }

  function labelKey(lb) { return lb.kind + ':' + lb.full + ':' + lb.lane; }
  function setOrientation(value) {
    if (value !== 'horizontal' && value !== 'vertical' || value === orientation) return;
    const now = performance.now(), angle = viewAngle(now);
    const prev = new Map(), oldLabels = new Map();
    if (L && L.nodes.length) {
      computeDisplay(now);
      for (const nd of L.nodes) prev.set(nd.hash, { x: D.x[nd.i], y: D.y[nd.i] });
      for (const lb of labels) oldLabels.set(labelKey(lb), labelPos(lb, now));
    }
    orientation = value;
    setChip('b-horizontal', !vertical()); setChip('b-vertical', vertical());
    canvas.setAttribute('aria-label', 'Git commit graph, ' + value + ' view');
    hideTip(); hover = unhover = -1;
    if (!L || !L.nodes.length) return;
    L.orientation = value; gap = computeGap(); relabel();
    anim = new Array(L.nodes.length); labelGlides.clear(); ghosts = []; headGlide = null; labelD = 0;
    if (!reduce()) {
      for (const nd of L.nodes) {
        const p = prev.get(nd.hash);
        anim[nd.i] = { fx: p.x, fy: p.y, pt0: now, pd: 400 };
      }
      for (const lb of labels) {
        const p = oldLabels.get(labelKey(lb));
        if (p) labelGlides.set(labelKey(lb), { x: p.x, y: p.y, t0: now });
      }
      turn = { from: angle, t0: now, d: 400 }; animUntil = now + 420;
    } else { turn = null; animUntil = 0; }
    edgeCache = null; camHome = true; moveCamera(homeView(), 400); draw(now); kick();
  }
  for (const value of ['horizontal', 'vertical']) $('b-' + value).addEventListener('click', safe(() => {
    if (orientation === value) return;
    setOrientation(value); post({ type: 'orientation', value });
  }));
  function setOptions(o) {
    o = o || {};
    if ('showRemotes' in o) { settings.showRemote = !!o.showRemotes; setChip('c-remote', settings.showRemote); }
    if ('showTags' in o) { settings.showTags = !!o.showTags; setChip('c-tags', settings.showTags); }
    if (o.timeFormat === 'relative' || o.timeFormat === 'absolute') settings.timeFormat = o.timeFormat;
    if (L) relabel();
    if (tipFor) { tipEl.innerHTML = tipHtml(tipFor); positionTip(); }
    draw();
  }

  // ------------------------------------------------------------ layout plumbing
  function relabel() {
    if (!L) { labels = []; return; }
    const t0 = performance.now();
    labels = G.placeLabels(L, { gap, colW: C.colW, measure, showRemote: settings.showRemote, showTags: settings.showTags });
    perf.labelMs = performance.now() - t0;
    if (hoverLabel) hoverLabel = null;
    edgeCache = null;
  }
  function rebuildIndex() {
    colMap = new Map();
    if (!L) return;
    for (const nd of L.nodes) { if (!colMap.has(nd.col)) colMap.set(nd.col, []); colMap.get(nd.col).push(nd.i); }
  }

  // ------------------------------------------------------------ states
  const ICONS = {
    loading: '<svg viewBox="0 0 48 48"><path d="M5 31h38"/><path d="M14 31c6 0 6-13 13-13h6" class="a"/><circle cx="10" cy="31" r="4"/><circle cx="24" cy="31" r="4"/><circle cx="38" cy="31" r="4"/><circle cx="36" cy="18" r="4" class="a"/></svg>',
    empty: '<svg viewBox="0 0 48 48"><circle cx="24" cy="24" r="6" class="a"/><path d="M6 24h12M30 24h12" stroke-dasharray="3 3"/></svg>',
    'not-a-repo': '<svg viewBox="0 0 48 48"><path d="M8 14h12l4 4h16v20H8z"/><path d="M19 28l10 0" class="a"/></svg>',
    'git-missing': '<svg viewBox="0 0 48 48"><path d="M24 6l18 18-18 18L6 24z"/><path d="M24 17v10M24 31v.5" class="a"/></svg>',
    error: '<svg viewBox="0 0 48 48"><circle cx="24" cy="24" r="16"/><path d="M24 15v11M24 31v.5" class="a"/></svg>',
  };
  const STATES = {
    loading: ['Loading history…', 'Reading commits and branches.'],
    empty: ['No commits yet', 'Make the first commit and the graph will appear here.'],
    'not-a-repo': ['Not a Git repository', 'Open a folder that contains a Git repository, or run “git init”.'],
    'git-missing': ['Git is not installed', 'Install git, then press Try again.'],
    error: ['Could not read the Git history', 'Something went wrong while running git.'],
  };
  function setState(kind, message) {
    kind = STATES[kind] ? kind : 'error';
    if (kind === 'loading' && viewState === 'data' && L) {      // a refresh: keep the graph, spin the button
      $('b-refresh').classList.add('spin');
      return;
    }
    viewState = kind;
    const st = STATES[kind];
    $('state-icon').innerHTML = ICONS[kind];
    $('state-title').textContent = st[0];
    $('state-hint').textContent = message && kind !== 'loading' ? String(message) : st[1];
    if (message && kind === 'error') $('state-hint').textContent = String(message).slice(0, 300);
    $('state-btn').hidden = !(kind === 'error' || kind === 'git-missing' || kind === 'not-a-repo');
    stateEl.classList.toggle('loading', kind === 'loading');
    stateEl.dataset.kind = kind;
    stateEl.hidden = false;
    barEl.style.visibility = 'hidden';
    hideTip(); setHover(-1);
    draw();
  }
  function hideState() { stateEl.hidden = true; barEl.style.visibility = ''; $('b-refresh').classList.remove('spin'); }

  // ------------------------------------------------------------ data
  function setData(data) {
    if (!data || typeof data !== 'object') { setState('error', 'No graph data'); return; }
    if (data.ok === false) { setState(STATES[data.error] ? data.error : 'error', STATES[data.error] ? '' : data.error); return; }
    const t0 = performance.now();
    const NL = G.layout(data, orientation);
    if (!NL.nodes.length) { L = NL; selected = cursor = hover = unhover = -1; anim = []; ghosts = []; setState('empty'); return; }
    G.assignColors(NL, pal.lanes.length, pal.skip);
    perf.layoutMs = performance.now() - t0;
    const now = performance.now();
    // previous display positions by hash
    const prev = new Map();
    let prevHeadLabel = null;
    if (L && L.nodes.length && viewState === 'data') {
      computeDisplay(now);
      for (let i = 0; i < L.nodes.length; i++) prev.set(L.nodes[i].hash, { x: D.x[i], y: D.y[i], a: D.a[i], lane: L.nodes[i].lane });
      const hl = labels.find((l) => l.kind === 'head');
      if (hl) { const p = labelPos(hl, now); prevHeadLabel = { x: p.x, y: p.y, hash: L.nodes[L.head].hash }; }
    }
    const oldL = L;
    const selHash = hashAt(selected), curHash = hashAt(cursor);
    let overlap = 0;
    for (const nd of NL.nodes) if (prev.has(nd.hash)) overlap++;
    L = NL; dataRaw = data;
    gap = computeGap();
    rebuildIndex();
    relabel();
    selected = selHash && L.byHash.has(selHash) ? L.byHash.get(selHash) : -1;
    cursor = curHash && L.byHash.has(curHash) ? L.byHash.get(curHash) : -1;
    hover = -1; unhover = -1;
    hideTip();
    anim = new Array(L.nodes.length);
    ghosts = [];
    headGlide = null;
    const wasData = viewState === 'data';
    hideState();
    viewState = 'data';
    const rm = reduce();
    if (!overlap || !wasData) {
      // first reveal: lanes draw in along their paths in time order, dots pop, labels fade in last
      const v = homeView();
      cam.x = v.x; cam.y = v.y; cam.s = v.s; camTw = null; zoomT = null; inertia = null; camHome = true;
      if (!rm) {
        const n = L.nodes.length;
        const vx0 = toWX(0), vx1 = toWX(W);
        const c0 = Math.max(0, Math.floor(vx0 / C.colW)), c1 = Math.max(c0 + 1, Math.ceil(vx1 / C.colW));
        const stagger = n <= C.staggerMax;
        for (let i = 0; i < n; i++) {
          const col = L.nodes[i].col;
          const f = G.clamp((col - c0) / (c1 - c0), 0, 1);
          anim[i] = stagger ? { at0: now + f * C.revealMs, ad: C.popMs, ak: 'pop' } : { at0: now, ad: 260, ak: 'fade' };
        }
        labelT0 = now + (stagger ? C.revealMs * 0.85 : 120); labelD = C.labelFadeMs;
        animUntil = labelT0 + labelD + 20;
        perf.revealMs = animUntil - now;
      } else { labelD = 0; perf.revealMs = 0; }
    } else {
      labelD = 0;
      if (!rm) {
        for (let i = 0; i < L.nodes.length; i++) {
          const p = prev.get(L.nodes[i].hash);
          if (p) {
            if (Math.abs(p.x - nx(i)) > 0.5 || Math.abs(p.y - ny(i)) > 0.5) anim[i] = { fx: p.x, fy: p.y, pt0: now, pd: C.moveMs };
          } else anim[i] = { at0: now, ad: C.moveMs + 60, ak: 'slide' };
        }
        const keep = new Set(L.nodes.map((nd) => nd.hash));
        for (const [h, p] of prev) {
          if (keep.has(h)) continue;
          const oi = oldL.byHash.get(h);
          ghosts.push({ x: p.x, y: p.y, r: C.dotR, color: oldL ? colorOfOld(oldL, oi) : pal.main.css, t0: now });
        }
        const hl = labels.find((l) => l.kind === 'head');
        if (prevHeadLabel && hl && (Math.abs(prevHeadLabel.x - hl.x) > 0.5 || Math.abs(prevHeadLabel.y - hl.y) > 0.5)) {
          headGlide = { fx: prevHeadLabel.x, fy: prevHeadLabel.y, t0: now, d: C.headMs };
        }
        animUntil = now + Math.max(C.moveMs + 80, C.headMs, C.fadeMs) + 20;
      }
      if (camHome) { const v = homeView(); moveCamera(v, rm ? 0 : C.headMs); }
      else clampCam();
    }
    edgeCache = null;
    wakePulse();
    draw(now);
    kick();
  }
  function colorOfOld(oldL, i) {
    if (i === undefined) return pal.main.css;
    const ln = oldL.lanes[oldL.nodes[i].lane];
    return ln.main || ln.color < 0 ? pal.main.css : pal.lanes[ln.color % pal.lanes.length].css;
  }

  // ------------------------------------------------------------ resize / visibility
  function resize() {
    const r = canvas.getBoundingClientRect();
    const nw = Math.max(1, Math.round(r.width)), nh = Math.max(1, Math.round(r.height));
    const nd = Math.min(2, window.devicePixelRatio || 1);
    if (nw === W && nh === H && nd === dpr) return;
    const cx = cam.x, cy = cam.y;
    W = nw; H = nh; dpr = nd;
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
    barEl.classList.toggle('narrow', W < 520);
    if (L && L.nodes.length) {
      const ng = computeGap();
      if (ng !== gap) { const k = ng / gap; gap = ng; relabel(); if (vertical()) cam.x = cx * k; else cam.y = cy * k; }
      if (camHome) { const v = homeView(); cam.x = v.x; cam.y = v.y; cam.s = v.s; }
      else { if (!vertical()) cam.x = cx; clampCam(); }
    }
    draw();
  }
  if (typeof ResizeObserver !== 'undefined') new ResizeObserver(safe(resize)).observe(canvas);
  window.addEventListener('resize', safe(resize));
  document.addEventListener('visibilitychange', safe(() => {
    hiddenDoc = document.visibilityState === 'hidden';
    document.body.classList.toggle('paused', paused || hiddenDoc);
    if (hiddenDoc) { if (raf) { cancelAnimationFrame(raf); raf = 0; } updateOverlays(); }
    else { lastTs = 0; draw(); kick(); }
  }));
  if (mql && mql.addEventListener) mql.addEventListener('change', safe(() => { document.body.classList.toggle('reduce', reduce()); draw(); }));

  function hashAt(i) { return L && i >= 0 && i < L.nodes.length ? L.nodes[i].hash : null; }

  // ------------------------------------------------------------ API
  Object.assign(IDGit, {
    setData: safe(setData),
    setOrientation: safe(setOrientation),
    setOptions: safe(setOptions),
    setState: safe((kind, message) => setState(kind, message)),
    setTheme: safe((colors) => {
      theme = Object.assign({}, DEFAULT_THEME, colors || {});
      pal = buildPalette(theme);
      measureCache.clear();
      applyCss();
      if (L && L.nodes.length) { G.assignColors(L, pal.lanes.length, pal.skip); relabel(); }
      draw();
    }),
    setSettings: safe((o) => {
      o = o || {};
      if ('reduceMotion' in o) settings.reduceMotion = !!o.reduceMotion;
      if ('showRemote' in o) { settings.showRemote = !!o.showRemote; setChip('c-remote', settings.showRemote); }
      if ('showTags' in o) { settings.showTags = !!o.showTags; setChip('c-tags', settings.showTags); }
      document.body.classList.toggle('reduce', reduce());
      if (reduce()) {
        turn = null; labelGlides.clear();
        anim = L ? new Array(L.nodes.length) : []; ghosts = []; labelD = 0; headGlide = null; animUntil = 0;
        if (camTw) { cam.x = camTw.tx; cam.y = camTw.ty; cam.s = camTw.ts; camTw = null; }
        if (zoomT) { cam.s = zoomT.s; zoomT = null; }
        inertia = null; pulseOn = false; clearTimeout(pulseTimer);
      }
      if (L) relabel();
      draw();
    }),
    select: safe((hash) => {
      selected = hash && L && L.byHash.has(hash) ? L.byHash.get(hash) : -1;
      draw();
    }),
    focus: safe((hash) => {
      if (!L || !L.nodes.length) return;
      if (hash === 'HEAD' || hash == null) { jumpHead(); return; }
      let i = L.byHash.has(hash) ? L.byHash.get(hash) : -1;
      if (i < 0) for (const nd of L.nodes) if (nd.hash.startsWith(String(hash)) || nd.short === hash) { i = nd.i; break; }
      if (i < 0) return;
      camHome = false;
      moveCamera({ x: nx(i), y: ny(i), s: G.clamp(cam.s, 0.7, 1.6) });
    }),
    pause: safe(() => {
      paused = true;
      if (raf) { cancelAnimationFrame(raf); raf = 0; }
      document.body.classList.add('paused');
      clearTimeout(tipTimer);
      updateOverlays();
    }),
    resume: safe(() => {
      if (!paused) return;
      paused = false; lastTs = 0;
      document.body.classList.toggle('paused', hiddenDoc);
      draw(); kick();
    }),
    stats: () => ({
      orientation, options: { showRemotes: settings.showRemote, showTags: settings.showTags, timeFormat: settings.timeFormat },
      frames: perf.frames, running: !!raf, paused, state: viewState, commits: L ? L.nodes.length : 0, lanes: L ? L.lanes.length : 0,
      slots: L ? L.maxSlot - L.minSlot + 1 : 0, laneGap: gap, scale: cam.s, cam: { x: cam.x, y: cam.y }, width: W, height: H,
      animating: performance.now() < animUntil || !!camTw || !!zoomT || !!inertia, layoutMs: perf.layoutMs, labelMs: perf.labelMs,
      drawMs: perf.drawMs, revealMs: perf.revealMs, selected: hashAt(selected), cursor: hashAt(cursor), hover: hashAt(hover),
      pulse: pulseEl.classList.contains('on'), reduceMotion: reduce(), tip: tipEl.classList.contains('show'),
    }),
  });
  IDGit._debug = {
    screenPos: (hash) => {
      if (!L || !L.byHash.has(hash)) return null;
      const i = L.byHash.get(hash);
      const now = performance.now(); computeDisplay(now);
      return [toSX(D.x[i]), toSY(D.y[i])];
    },
    nodeAlpha: (hash) => { if (!L || !L.byHash.has(hash)) return null; const d = nodeDisp(L.byHash.get(hash), performance.now()); return d.a; },
    nodeWorld: (hash) => { if (!L || !L.byHash.has(hash)) return null; const i = L.byHash.get(hash); return { x: nx(i), y: ny(i), col: L.nodes[i].col, slot: L.nodes[i].slot, lane: L.lanes[L.nodes[i].lane].id }; },
    labels: () => labels.filter((lb) => labelVisible(lb, cam.s) > 0.5).map((lb) => {
      const p = labelPos(lb, performance.now());
      return { kind: lb.kind, text: lb.text, x: toSX(p.x), y: toSY(p.y), w: lb.w * cam.s, h: lb.h * cam.s, side: lb.side, node: L.nodes[lb.node].hash };
    }),
    tipText: () => (tipEl.classList.contains('show') ? tipEl.innerText : ''),
    tipParts: () => Array.from(tipEl.children).map((c) => ({ cls: c.className, text: c.innerText })),
    tipRect: () => { const r = tipEl.getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height }; },
    head: () => (L && L.head >= 0 ? L.nodes[L.head].hash : null),
    hashes: () => (L ? L.nodes.map((n) => n.hash) : []),
    view: () => ({ x: cam.x, y: cam.y, s: cam.s }),
    fit: () => moveCamera(fitView(), 0),
    home: () => moveCamera(homeView(), 0),
    setView: (x, y, s) => { camTw = null; zoomT = null; inertia = null; cam.x = x; cam.y = y; cam.s = s; camHome = false; draw(); },
    pulseNow: () => wakePulse(),
    palette: () => ({ bg: pal.bgCss, dark: pal.dark, main: pal.main, lanes: pal.lanes, head: pal.head, headFg: pal.headFg, muted: pal.muted, calloutText: pal.calloutText }),
  };

  // ------------------------------------------------------------ boot
  pal = buildPalette(theme);
  applyCss();
  document.body.classList.toggle('reduce', reduce());
  resize();
  setState('loading');
  if (!readySent) { readySent = true; post({ type: 'ready' }); }
  // standalone (file:// without the bridge): ?demo shows the built-in synthetic history
  try {
    const q = new URLSearchParams(location.search);
    if (!(window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.idgent) && q.has('demo')) setData(G.synth(+q.get('demo') || 120, 7));
  } catch (e) { /* ignore */ }
})();
