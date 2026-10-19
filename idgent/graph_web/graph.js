/* IDgent graph view: DOM, Canvas2D renderer, camera, interaction, controls panel, host bridge.
 * Pure maths/physics live in graph_core.js (window.GraphCore). */
(function () {
  'use strict';
  const G = window.GraphCore;
  const IDGraph = window.IDGraph = window.IDGraph || {};
  IDGraph.outbox = IDGraph.outbox || [];

  // ------------------------------------------------------------ bridge
  function post(msg) {
    try {
      const h = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.idgent;
      if (h) h.postMessage(msg); else { IDGraph.outbox.push(msg); if (IDGraph.outbox.length > 2000) IDGraph.outbox.splice(0, 1000); }
    } catch (e) { IDGraph.outbox.push(msg); }
  }
  window.addEventListener('error', (e) => post({ type: 'error', message: String((e && e.message) || e) + (e && e.filename ? ' @' + e.filename.split('/').pop() + ':' + e.lineno : '') }));
  window.addEventListener('unhandledrejection', (e) => post({ type: 'error', message: 'unhandled rejection: ' + String(e && e.reason) }));

  const $ = (id) => document.getElementById(id);
  const canvas = $('graph'), ctx = canvas.getContext('2d', { alpha: true });
  const statusEl = $('status'), noticeEl = $('notice'), controlsEl = $('controls');
  const menuEl = $('menu'), popupEl = $('popup'), tipEl = $('tip');

  // ------------------------------------------------------------ state
  const settings = Object.assign({}, G.DEFAULTS, { colorGroups: [] });
  const local = { enabled: false, path: null, depth: 1, incoming: true, outgoing: true };
  const disp = { node: 1, line: 1, fade: 0 };                    // eased display multipliers
  let themeRaw = Object.assign({}, G.DARK_THEME);
  let pal = G.palette(themeRaw), palFrom = pal, palTo = pal, palT = 1;
  let random = G.rng(1);

  const sim = new G.Simulation();
  let model = G.buildModel({ nodes: [], links: [] });
  let N = 0, ids = [], kinds = [], labels = [], paths = [], groups = [];
  let weight = new Float64Array(0), size = new Float32Array(0);
  let na = new Float32Array(0), tr = new Float32Array(0), tg = new Float32Array(0), tb = new Float32Array(0), drop = new Float32Array(0);
  let sx = new Float32Array(0), sy = new Float32Array(0), sr = new Float32Array(0);
  let nb = new Uint8Array(0), order = [];
  let lines = [], la = new Float32Array(0), lh = new Float32Array(0);
  let arrows = [], aa = new Float32Array(0);
  let ghosts = [];
  let labelCache = [];
  let hasData = false, rawData = null, activeId = null, activeIdx = -1, focusIdx = -1;
  let hover = -1, drag = -1, highlight = -1, lastHighlight = -2;

  const cam = { s: 1, ts: 1, px: 0, py: 0, ax: 0, ay: 0, vx: 0, vy: 0, track: -1, trackT: 0, moving: false };
  let W = 0, H = 0, dpr = 1, lowQuality = false, lastMoveTs = 0;
  let paused = false, raf = 0, lastTs = 0, acc = 0, idle = 0, readySent = false, firstFrame = true;
  const keys = new Set();
  const pointer = { x: -1e9, y: -1e9, inside: false };
  let press = null;                 // active pointer gesture
  const perf = { fps: 0, frameMs: 0, interval: 0, frames: 0, lastFrameTs: 0, slow: 0 };
  let timelapseState = null;

  // ------------------------------------------------------------ helpers
  function safe(fn) {
    return function () {
      try { return fn.apply(null, arguments); } catch (e) { post({ type: 'error', message: 'IDGraph: ' + (e && e.stack || e) }); return undefined; }
    };
  }
  function kick() { idle = 0; if (!paused && !raf) { raf = requestAnimationFrame(frame); } }
  const reduce = () => !!settings.reduceMotion;
  function nodeIndex(id) { const i = sim.index.get(id); return i === undefined ? -1 : i; }

  // ------------------------------------------------------------ colours
  function setCssVars(p) {
    p = p || pal;
    const bg = p.background, tx = p.text;
    const m = (t) => G.toHex(G.mix(bg, tx, t));
    const r = document.documentElement.style;
    r.setProperty('--bg', G.toHex(bg));
    r.setProperty('--text', G.toHex(tx));
    r.setProperty('--muted', G.toHex(p.textMuted));
    r.setProperty('--accent', G.toHex(p.accent));
    r.setProperty('--panel', m(p.dark ? 0.06 : 0.035));
    r.setProperty('--border', m(p.dark ? 0.13 : 0.11));
    r.setProperty('--hover', m(p.dark ? 0.11 : 0.07));
    r.setProperty('--track', m(p.dark ? 0.24 : 0.18));
    r.setProperty('--input', m(p.dark ? 0.02 : 0.0));
    r.setProperty('--shadow', p.dark ? 'rgba(0,0,0,0.4)' : 'rgba(0,0,0,0.12)');
    r.setProperty('--font', p.fontUi);
    document.documentElement.style.colorScheme = p.dark ? 'dark' : 'light';
  }
  function groupColor(g) {
    const cg = settings.colorGroups;
    if (Array.isArray(cg) && cg[g] && cg[g].color != null) return G.parseColor(cg[g].color);
    return pal.groups[g % pal.groups.length] || pal.fill;
  }
  /** Target fill (spec C.1 priority). */
  function targetColor(i) {
    if (i === highlight) return pal.highlight;
    const k = kinds[i];
    if (i === activeIdx && pal.focused.a > 0) return pal.focused;
    if (groups[i] != null && k !== 'tag' && k !== 'unresolved') return groupColor(groups[i]);
    if (k === 'tag') return pal.tag;
    if (k === 'unresolved') return pal.unresolved;
    if (k === 'attachment') return pal.attachment;
    if (k === 'code') return pal.code;
    return pal.fill;
  }

  // ------------------------------------------------------------ data
  function computeWeights() {
    weight = new Float64Array(N);
    let focus = -1;
    if (local.enabled) {
      const f = (rawData && rawData.stats && rawData.stats.focus) || local.path;
      if (f != null) { focus = nodeIndex(f); if (focus < 0) focus = paths.indexOf(f); }
    }
    if (focus >= 0) {
      const depth = Math.max(1, Math.min(5, local.depth | 0 || 1));
      const layer = G.localLayers(model, focus, depth, local.incoming, local.outgoing);
      for (let i = 0; i < N; i++) weight[i] = layer[i] >= 0 ? Math.max(0, G.localWeight(layer[i], depth)) : 0;
    } else {
      for (let i = 0; i < N; i++) weight[i] = model.weight[i];
    }
    focusIdx = focus;
  }

  function applyData(g, internal) {
    if (!internal) { rawData = g; stopTimelapse(); }
    const nm = G.buildModel(g || {});
    const oldIndex = sim.index, oldN = N;
    const existing = new Map();
    for (const [id, i] of oldIndex) existing.set(id, [sim.x[i], sim.y[i]]);
    // structural change?
    let same = nm.n === oldN && nm.pairs.length === model.pairs.length;
    if (same) for (let i = 0; i < nm.n; i++) if (ids[i] !== nm.nodes[i].id) { same = false; break; }
    if (same) for (let k = 0; k < nm.pairs.length; k++) if (nm.pairs[k][0] !== model.pairs[k][0] || nm.pairs[k][1] !== model.pairs[k][1]) { same = false; break; }
    const pos = G.placeNodes(nm, existing, random);
    // A complete replacement (nothing survives) recentres like a fresh view.
    if (hasData && oldN && !nm.nodes.some((n) => oldIndex.has(String(n.id)))) {
      cam.px = W / 2; cam.py = H / 2; cam.vx = cam.vy = 0; cam.track = -1;
    }
    // ghosts of removed nodes fade out
    const newIds = new Set(nm.nodes.map((n) => n.id));
    if (!reduce()) {
      for (let i = 0; i < oldN; i++) {
        if (!newIds.has(ids[i]) && na[i] > 0.02 && ghosts.length < 3000) {
          ghosts.push({ x: sim.x[i], y: sim.y[i], size: size[i], r: tr[i], g: tg[i], b: tb[i], a: na[i] });
        }
      }
    }
    const oNa = na, oTr = tr, oTg = tg, oTb = tb, oDrop = drop, oLabels = labelCache, oIds = ids;
    const oldLineA = new Map();
    for (let k = 0; k < lines.length; k++) oldLineA.set(oIds[lines[k][0]] + '\u0000' + oIds[lines[k][1]], k);
    const oLa = la, oLh = lh;
    model = nm; N = nm.n;
    ids = nm.nodes.map((n) => String(n.id));
    kinds = nm.nodes.map((n) => n.kind || 'note');
    labels = nm.nodes.map((n) => String(n.label != null ? n.label : n.id));
    paths = nm.nodes.map((n) => n.path || '');
    groups = nm.nodes.map((n) => (typeof n.group === 'number' ? n.group : null));
    sim.setNodes(nm.nodes.map((n, i) => ({ id: ids[i], x: pos[i][0], y: pos[i][1] })));
    sim.setLinks(nm.pairs);
    // per node render state (kept ids keep their state)
    na = new Float32Array(N); tr = new Float32Array(N); tg = new Float32Array(N); tb = new Float32Array(N); drop = new Float32Array(N);
    sx = new Float32Array(N); sy = new Float32Array(N); sr = new Float32Array(N); nb = new Uint8Array(N);
    labelCache = new Array(N);
    activeIdx = activeId != null ? nodeIndex(activeId) : -1;
    if (hover >= 0) { const id = oIds[hover]; hover = nodeIndex(id); }
    if (drag >= 0) { const id = oIds[drag]; drag = nodeIndex(id); if (drag < 0) press = null; }
    highlight = drag >= 0 ? drag : hover; lastHighlight = -2;
    computeWeights();
    size = new Float32Array(N);
    for (let i = 0; i < N; i++) {
      const j = oldIndex.get(ids[i]);
      if (j !== undefined && j < oNa.length) {
        na[i] = oNa[j]; tr[i] = oTr[j]; tg[i] = oTg[j]; tb[i] = oTb[j]; drop[i] = oDrop[j];
        if (oLabels[j] && oLabels[j].text === labels[i]) labelCache[i] = oLabels[j];
      } else {
        const c = targetColor(i); tr[i] = c.r; tg[i] = c.g; tb[i] = c.b; na[i] = reduce() ? 1 : 0;
      }
    }
    lines = nm.lines; la = new Float32Array(lines.length); lh = new Float32Array(lines.length);
    for (let k = 0; k < lines.length; k++) {
      const key = ids[lines[k][0]] + '\u0000' + ids[lines[k][1]];
      const o = oldLineA.get(key);
      if (o !== undefined) { la[k] = oLa[o]; lh[k] = oLh[o]; } else la[k] = reduce() ? 1 : 0.2;
    }
    arrows = nm.pairs.filter((p) => p[0] !== p[1]);
    aa = new Float32Array(arrows.length).fill(1);
    order = new Array(N); for (let i = 0; i < N; i++) order[i] = i;
    if (!hasData) { sim.alpha = 1; hasData = true; }
    else if (!same) sim.reheat(G.C.reheat);
    updateStatus(g);
    kick();
    return { nodes: N, links: lines.length };
  }

  function updateStatus(g) {
    if (!hasData) { statusEl.textContent = 'Building graph…'; statusEl.classList.remove('is-hidden'); return; }
    if (N === 0) { statusEl.textContent = 'No linked files yet'; statusEl.classList.remove('is-hidden'); }
    else statusEl.classList.add('is-hidden');
    const truncated = g && g.truncated;
    noticeEl.hidden = !truncated;
    if (truncated) noticeEl.textContent = 'Showing the ' + N + ' most connected files';
  }

  // ------------------------------------------------------------ canvas sizing
  function resize() {
    const r = canvas.getBoundingClientRect();
    const w = Math.max(1, Math.round(r.width)), h = Math.max(1, Math.round(r.height));
    const d = lowQuality ? 1 : Math.min(2, window.devicePixelRatio || 1);
    if (W && H) { cam.px += (w - W) / 2; cam.py += (h - H) / 2; }
    else { cam.px = w / 2; cam.py = h / 2; }
    W = w; H = h;
    if (d !== dpr) { dpr = d; labelCache = new Array(N); }
    const bw = Math.round(w * dpr), bh = Math.round(h * dpr);
    if (canvas.width !== bw || canvas.height !== bh) { canvas.width = bw; canvas.height = bh; }
    kick();
  }

  // ------------------------------------------------------------ label cache
  const measureCtx = document.createElement('canvas').getContext('2d');
  function wrapLines(text, font, maxW) {
    measureCtx.font = font;
    if (measureCtx.measureText(text).width <= maxW) return [text];
    const words = text.split(/(\s+|(?<=[-_/.]))/).filter((w) => w && !/^\s+$/.test(w));
    const out = []; let cur = '';
    for (const w of words) {
      const t = cur ? (/[-_/.]$/.test(cur) ? cur + w : cur + ' ' + w) : w;
      if (measureCtx.measureText(t).width <= maxW || !cur) cur = t; else { out.push(cur); cur = w; }
    }
    if (cur) out.push(cur);
    return out;
  }
  let labelRenders = 0;
  function labelBitmap(i, fontPx) {
    // Render at the next half-octave above the needed device size; reuse a stale bitmap when over budget.
    const bucket = Math.ceil(Math.log2(Math.max(fontPx * dpr, 2)) * 2) / 2;
    const c = labelCache[i];
    if (c && c.color === pal.textKey && c.bucket === bucket) return c;
    if (c && labelRenders > 60) return c;
    if (!c && labelRenders > 200) return null;
    labelRenders++;
    const px = Math.pow(2, bucket);
    const font = '400 ' + px.toFixed(2) + 'px ' + pal.fontUi;
    const wrapW = G.C.labelWrap * px / labelFontOf(i);            // 300 text units in bitmap px
    const lns = wrapLines(labels[i], font, wrapW);
    measureCtx.font = font;
    let w = 0; for (const l of lns) w = Math.max(w, measureCtx.measureText(l).width);
    const lh = Math.ceil(px * 1.25), pad = 2;
    const cv = (c && c.canvas) || document.createElement('canvas');
    cv.width = Math.max(1, Math.ceil(w) + pad * 2); cv.height = Math.max(1, lh * lns.length + pad * 2);
    const g = cv.getContext('2d');
    g.clearRect(0, 0, cv.width, cv.height);
    g.font = font; g.textAlign = 'center'; g.textBaseline = 'top'; g.fillStyle = pal.textCss;
    for (let k = 0; k < lns.length; k++) g.fillText(lns[k], cv.width / 2, pad + k * lh + (lh - px) / 2);
    return (labelCache[i] = { canvas: cv, bucket, px, color: pal.textKey, text: labels[i], w: cv.width, h: cv.height });
  }
  function labelFontOf(i) { return G.labelFont(size[i]); }

  // ------------------------------------------------------------ frame loop
  function frame(ts) {
    raf = 0;
    if (paused) return;
    const t0 = performance.now();
    let dt = lastTs ? (ts - lastTs) / 1000 : 1 / 60;
    if (!(dt > 0)) dt = 1 / 60;
    if (perf.lastFrameTs && idle === 0) {
      const iv = ts - perf.lastFrameTs;
      if (iv > 0 && iv < 250) perf.interval = perf.interval ? perf.interval * 0.9 + iv * 0.1 : iv;
    }
    perf.lastFrameTs = ts;
    lastTs = ts;
    dt = Math.min(dt, 0.05);
    let active = false;

    // physics at a fixed 60 Hz, independent of the display rate
    if (N && (sim.running || drag >= 0)) {
      acc += dt;
      let k = 0;
      while (acc >= 1 / 60 - 1e-6 && k < 3) { sim.tick(); acc -= 1 / 60; k++; }
      if (k === 3) acc = 0;
      if (sim.alpha < sim.alphaMin && sim.alphaTarget === 0 && drag < 0) sim.alpha = 0;
      active = true;
    } else acc = 1 / 60;

    if (stepCamera(dt)) active = true;
    if (stepTheme(dt)) active = true;
    if (stepDisplay(dt)) active = true;
    if (stepElements(dt)) active = true;
    if (ghosts.length) active = true;

    if (active && pointer.inside && !press) updateHover();
    const moving = cam.moving || !!press;
    if (moving) lastMoveTs = ts;
    if (lowQuality && ts - lastMoveTs > 300 && !sim.running) { lowQuality = false; resize(); }
    else if (!lowQuality && perf.interval > 28 && moving && ++perf.slow > 20) { lowQuality = true; resize(); }
    if (perf.interval <= 28) perf.slow = 0;

    draw(dt, lowQuality && moving);
    perf.frameMs = perf.frameMs ? perf.frameMs * 0.9 + (performance.now() - t0) * 0.1 : performance.now() - t0;
    perf.frames++;
    if (perf.interval) perf.fps = 1000 / perf.interval;

    if (!readySent) { readySent = true; post({ type: 'ready' }); }
    firstFrame = false;
    if (active || keys.size) idle = 0; else idle++;
    if (idle < 3) raf = requestAnimationFrame(frame);
    else { lastTs = 0; perf.lastFrameTs = 0; }
  }

  function stepCamera(dt) {
    let moved = false;
    const s0 = cam.s;
    // keyboard zoom (= / -), anchored at the centre
    let kz = 0;
    if (keys.has('=') || keys.has('+')) kz += 1;
    if (keys.has('-') || keys.has('_')) kz -= 1;
    if (kz) {
      const f = Math.pow(keys.has('shift') ? 1.1 : 1.03, kz * dt * 60);
      cam.ts = G.clampScale(cam.ts * f); cam.ax = W / 2; cam.ay = H / 2;
    }
    cam.ts = G.clampScale(cam.ts);
    if (Math.abs(cam.s / cam.ts - 1) >= 0.01) {
      const s1 = G.ease(cam.s, cam.ts, G.C.zoomK, dt, reduce());
      cam.px = G.zoomAround(cam.px, cam.s, s1, cam.ax); cam.py = G.zoomAround(cam.py, cam.s, s1, cam.ay);
      cam.s = s1; moved = true;
    } else if (cam.s !== cam.ts) {
      cam.px = G.zoomAround(cam.px, cam.s, cam.ts, cam.ax); cam.py = G.zoomAround(cam.py, cam.s, cam.ts, cam.ay);
      cam.s = cam.ts; moved = true;
    }
    // focus tracking (eased pan toward a node)
    if (cam.track >= 0 && cam.track < N) {
      const tx = W / 2 - cam.s * sim.x[cam.track], ty = H / 2 - cam.s * sim.y[cam.track];
      const nx = G.ease(cam.px, tx, G.C.zoomK, dt, reduce()), ny = G.ease(cam.py, ty, G.C.zoomK, dt, reduce());
      cam.trackT += dt;
      if (Math.abs(nx - tx) < 0.5 && Math.abs(ny - ty) < 0.5 && !sim.running) { cam.px = tx; cam.py = ty; cam.track = -1; }
      else { cam.px = nx; cam.py = ny; }
      if (cam.trackT > 2.5) cam.track = -1;
      moved = true;
    }
    // inertia + keyboard pan (px/ms velocities, decay 0.9 per 60 Hz frame)
    const panning = press && press.mode === 'pan';
    if (!panning) {
      let kx = 0, ky = 0;
      const sp = keys.has('shift') ? 3 : 1;
      if (keys.has('ArrowLeft')) kx += sp; if (keys.has('ArrowRight')) kx -= sp;
      if (keys.has('ArrowUp')) ky += sp; if (keys.has('ArrowDown')) ky -= sp;
      if (reduce() && !kx && !ky) { cam.vx = 0; cam.vy = 0; }
      if (cam.vx || cam.vy || kx || ky) {
        const ms = dt * 1000;
        if (reduce() && (kx || ky)) { cam.vx = kx; cam.vy = ky; }
        cam.px += cam.vx * ms; cam.py += cam.vy * ms;
        const d = Math.pow(G.C.panDecay, dt * 60);
        cam.vx = cam.vx * d + kx * (1 - d); cam.vy = cam.vy * d + ky * (1 - d);
        if (!kx && !ky && Math.abs(cam.vx) < 0.002 && Math.abs(cam.vy) < 0.002) { cam.vx = 0; cam.vy = 0; }
        moved = true;
      }
    }
    if (cam.s !== s0) scheduleScaleSave();
    cam.moving = moved;
    return moved;
  }

  function stepTheme(dt) {
    if (palT >= 1) return false;
    palT = reduce() ? 1 : Math.min(1, palT + dt / 0.18);
    const t = palT, m = (a, b) => G.mix(a, b, t);
    const p = Object.assign({}, palTo);
    for (const k of ['background', 'fill', 'focused', 'tag', 'attachment', 'unresolved', 'code', 'line', 'lineHighlight', 'highlight', 'circle', 'arrow']) p[k] = m(palFrom[k], palTo[k]);
    p.text = t < 0.5 ? palFrom.text : palTo.text;
    setPal(p);
    return palT < 1;
  }
  function setPal(p) {
    pal = p;
    pal.textCss = G.toCss(pal.text, 1);
    pal.textKey = pal.textCss + '|' + pal.fontUi;
  }

  function stepDisplay(dt) {
    const tn = G.clamp(+settings.nodeSizeMultiplier || 1, 0.1, 5), tl = G.clamp(+settings.lineSizeMultiplier || 1, 0.1, 5);
    const tf = G.clamp(+settings.textFadeMultiplier || 0, -3, 3);
    let ch = false;
    const e = (v, t) => { if (Math.abs(v - t) < 1e-3) return t; ch = true; return G.ease(v, t, 0.25, dt, reduce()); };
    disp.node = e(disp.node, tn); disp.line = e(disp.line, tl); disp.fade = e(disp.fade, tf);
    return ch;
  }

  /** Ease per-element alpha / tint / label drop toward their targets. */
  function stepElements(dt) {
    if (highlight !== lastHighlight) {
      nb.fill(0);
      if (highlight >= 0) {
        nb[highlight] = 1;
        for (const j of model.fwd[highlight]) nb[j] = 1;
        for (const j of model.rev[highlight]) nb[j] = 1;
      }
      lastHighlight = highlight;
    }
    const f = reduce() ? 1 : G.easeFactor(G.C.easeK, dt);
    let busy = false;
    const hl = highlight;
    for (let i = 0; i < N; i++) {
      size[i] = G.nodeSize(weight[i], disp.node);
      const ta = hl < 0 || nb[i] ? 1 : G.C.hoverDim;
      let d = ta - na[i];
      if (d > 1e-3 || d < -1e-3) { na[i] += d * f; busy = true; } else na[i] = ta;
      const c = targetColor(i);
      d = c.r - tr[i]; if (d > 0.3 || d < -0.3) { tr[i] += d * f; busy = true; } else tr[i] = c.r;
      d = c.g - tg[i]; if (d > 0.3 || d < -0.3) { tg[i] += d * f; busy = true; } else tg[i] = c.g;
      d = c.b - tb[i]; if (d > 0.3 || d < -0.3) { tb[i] += d * f; busy = true; } else tb[i] = c.b;
      const tdrop = i === hl ? G.C.labelDrop : 0;
      d = tdrop - drop[i]; if (d > 0.05 || d < -0.05) { drop[i] += d * f; busy = true; } else drop[i] = tdrop;
    }
    for (let k = 0; k < lines.length; k++) {
      const s = lines[k][0], t = lines[k][1];
      const touch = hl >= 0 && (s === hl || t === hl);
      const ta = hl < 0 || touch ? 1 : G.C.hoverDim, th = touch ? 1 : 0;
      let d = ta - la[k]; if (d > 1e-3 || d < -1e-3) { la[k] += d * f; busy = true; } else la[k] = ta;
      d = th - lh[k]; if (d > 1e-3 || d < -1e-3) { lh[k] += d * f; busy = true; } else lh[k] = th;
    }
    if (settings.showArrow) {
      for (let k = 0; k < arrows.length; k++) {
        const s = arrows[k][0], t = arrows[k][1];
        const ta = hl < 0 || s === hl || t === hl ? 1 : G.C.hoverDim;
        const d = ta - aa[k]; if (d > 1e-3 || d < -1e-3) { aa[k] += d * f; busy = true; } else aa[k] = ta;
      }
    }
    for (let g = ghosts.length - 1; g >= 0; g--) {
      ghosts[g].a -= ghosts[g].a * f + dt * 0.5;
      if (ghosts[g].a <= 0.01) ghosts.splice(g, 1);
    }
    return busy;
  }

  // ------------------------------------------------------------ drawing
  const lineBuckets = new Map(), nodeBuckets = new Map(), arrowBuckets = new Map();
  function bucketPush(map, key, v) { let a = map.get(key); if (!a) { a = []; map.set(key, a); } a.push(v); }
  function clearBuckets(map) { for (const a of map.values()) a.length = 0; }

  function draw(dt, hideLabels) {
    labelRenders = 0;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    if (!N && !ghosts.length) return;
    const s = cam.s, rs = Math.sqrt(s), px = cam.px, py = cam.py;
    const X = sim.x, Y = sim.y;
    for (let i = 0; i < N; i++) { sx[i] = px + s * X[i]; sy[i] = py + s * Y[i]; sr[i] = size[i] * rs; }

    // ghosts (removed nodes fading out)
    for (const gh of ghosts) {
      const gx = px + s * gh.x, gy = py + s * gh.y, r = gh.size * rs;
      if (gx + r < 0 || gx - r > W || gy + r < 0 || gy - r > H) continue;
      ctx.fillStyle = 'rgba(' + (gh.r | 0) + ',' + (gh.g | 0) + ',' + (gh.b | 0) + ',' + gh.a.toFixed(3) + ')';
      ctx.beginPath(); ctx.arc(gx, gy, r, 0, 6.2832); ctx.fill();
    }

    // links: one path per (colour mix, alpha) bucket
    const lw = disp.line;
    clearBuckets(lineBuckets);
    const lineA = pal.line.a;
    for (let k = 0; k < lines.length; k++) {
      const a = lines[k][0], b = lines[k][1];
      const x0 = sx[a], y0 = sy[a], x1 = sx[b], y1 = sy[b];
      if ((x0 < 0 && x1 < 0) || (x0 > W && x1 > W) || (y0 < 0 && y1 < 0) || (y0 > H && y1 > H)) continue;
      const qa = Math.round(la[k] * lineA * 40), qm = Math.round(lh[k] * 10);
      if (qa <= 0) continue;
      bucketPush(lineBuckets, qa * 16 + qm, k);
    }
    ctx.lineWidth = lw; ctx.lineCap = 'butt';
    for (const [key, list] of lineBuckets) {
      if (!list.length) continue;
      const qa = key >> 4, qm = key & 15;
      ctx.strokeStyle = G.toCss(G.mix(pal.line, pal.lineHighlight, qm / 10), qa / 40);
      ctx.beginPath();
      for (const k of list) {
        const a = lines[k][0], b = lines[k][1];
        const dx = sx[b] - sx[a], dy = sy[b] - sy[a], d = Math.sqrt(dx * dx + dy * dy);
        const len = d - sr[a] - sr[b];
        if (len <= 0 || d === 0) continue;
        const ux = dx / d, uy = dy / d;
        ctx.moveTo(sx[a] + ux * sr[a], sy[a] + uy * sr[a]);
        ctx.lineTo(sx[b] - ux * sr[b], sy[b] - uy * sr[b]);
      }
      ctx.stroke();
    }

    // arrows (both directions of a mutual pair)
    const af = G.arrowFactor(s);
    if (settings.showArrow && af > 0) {
      clearBuckets(arrowBuckets);
      for (let k = 0; k < arrows.length; k++) {
        const t = arrows[k][1];
        if (sx[t] < -20 || sx[t] > W + 20 || sy[t] < -20 || sy[t] > H + 20) continue;
        const qa = Math.round(aa[k] * pal.arrow.a * af * 40);
        if (qa > 0) bucketPush(arrowBuckets, qa, k);
      }
      const u = 2 * Math.sqrt(lw);
      for (const [qa, list] of arrowBuckets) {
        if (!list.length) continue;
        ctx.fillStyle = G.toCss(pal.arrow, qa / 40);
        ctx.beginPath();
        for (const k of list) {
          const a = arrows[k][0], b = arrows[k][1];
          const dx = sx[b] - sx[a], dy = sy[b] - sy[a], d = Math.sqrt(dx * dx + dy * dy);
          if (d - sr[a] - sr[b] <= lw || d === 0) continue;
          const ux = dx / d, uy = dy / d, back = sr[b] + s;
          const tx = sx[b] - ux * back, ty = sy[b] - uy * back;
          // dart: tip (0,0), (-4,-2), (-3,0), (-4,2) in units of u along (ux,uy)
          const p = (lx, ly) => [tx + (ux * lx - uy * ly) * u, ty + (uy * lx + ux * ly) * u];
          const q1 = p(-4, -2), q2 = p(-3, 0), q3 = p(-4, 2);
          ctx.moveTo(tx, ty); ctx.lineTo(q1[0], q1[1]); ctx.lineTo(q2[0], q2[1]); ctx.lineTo(q3[0], q3[1]); ctx.closePath();
        }
        ctx.fill();
      }
    }

    // nodes: one path per (colour, alpha) bucket; highlighted node on top
    clearBuckets(nodeBuckets);
    for (let i = 0; i < N; i++) {
      if (i === highlight) continue;
      const r = sr[i];
      if (sx[i] + r + 1 < 0 || sx[i] - r - 1 > W || sy[i] + r + 1 < 0 || sy[i] - r - 1 > H) continue;
      const fa = kinds[i] === 'unresolved' && i !== activeIdx && groups[i] == null ? pal.unresolved.a : 1;
      const qa = Math.round(na[i] * fa * 50);
      if (qa <= 0) continue;
      const key = ((tr[i] | 0) * 65536 + (tg[i] | 0) * 256 + (tb[i] | 0)) * 64 + qa;
      bucketPush(nodeBuckets, key, i);
    }
    for (const [key, list] of nodeBuckets) {
      if (!list.length) continue;
      const qa = key % 64, rgb = (key - qa) / 64;
      ctx.fillStyle = 'rgba(' + ((rgb >> 16) & 255) + ',' + ((rgb >> 8) & 255) + ',' + (rgb & 255) + ',' + (qa / 50).toFixed(3) + ')';
      ctx.beginPath();
      for (const i of list) { ctx.moveTo(sx[i] + sr[i], sy[i]); ctx.arc(sx[i], sy[i], sr[i], 0, 6.2832); }
      ctx.fill();
    }
    if (highlight >= 0 && highlight < N) {
      const i = highlight;
      ctx.fillStyle = 'rgba(' + (tr[i] | 0) + ',' + (tg[i] | 0) + ',' + (tb[i] | 0) + ',' + na[i].toFixed(3) + ')';
      ctx.beginPath(); ctx.arc(sx[i], sy[i], sr[i], 0, 6.2832); ctx.fill();
      // hover ring: ~1 px, radius size + w/2
      ctx.lineWidth = 1; ctx.strokeStyle = G.toCss(pal.circle);
      ctx.beginPath(); ctx.arc(sx[i], sy[i], sr[i] + 0.5, 0, 6.2832); ctx.stroke();
    }

    // labels (cached bitmaps); in low-quality motion only the highlighted label stays
    const ta = G.textAlpha(s, disp.fade);
    const textA = pal.text.a;
    ctx.imageSmoothingEnabled = true;
    const drawLabel = (i) => {
      const hov = i === highlight;
      const alpha = hov ? textA : ta * na[i] * textA;
      if (alpha <= 0.001) return;
      if (sx[i] < -300 || sx[i] > W + 300 || sy[i] < -20 - sr[i] * 3 || sy[i] > H + 200) return;
      const font = labelFontOf(i);
      const scr = hov && s < 1 ? font : font * rs;          // on-screen font px
      if (scr < 3) return;
      const bm = labelBitmap(i, scr);
      if (!bm) return;
      const k = scr / bm.px;                                  // css px per bitmap px
      const w = bm.w * k, h = bm.h * k;
      const ly = sy[i] + (size[i] + 5) * rs + drop[i];
      ctx.globalAlpha = alpha;
      ctx.drawImage(bm.canvas, sx[i] - w / 2, ly - 2 * k, w, h);
    };
    if (ta > 0.001 && !hideLabels) for (let i = 0; i < N; i++) if (i !== highlight) drawLabel(i);
    if (highlight >= 0 && highlight < N) drawLabel(highlight);
    ctx.globalAlpha = 1;
  }

  // ------------------------------------------------------------ hit testing
  function pick(x, y, slack) {
    const s = cam.s, rs = Math.sqrt(s);
    let best = -1;
    for (let i = N - 1; i >= 0; i--) {
      const dx = cam.px + s * sim.x[i] - x, dy = cam.py + s * sim.y[i] - y;
      const r = size[i] * rs + (slack || 0);
      if (dx * dx + dy * dy <= r * r) { if (i === highlight) return i; if (best < 0) best = i; }
    }
    return best;
  }
  function localPos(e) { const r = canvas.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }
  function setHover(i) {
    if (i === hover) return;
    hover = i;
    highlight = drag >= 0 ? drag : hover;
    canvas.classList.toggle('is-node', hover >= 0);
    post({ type: 'hover', id: hover >= 0 ? ids[hover] : null });
    kick();
  }
  function updateHover() {
    if (press || !pointer.inside) return;
    if (hover >= 0 && hover < N) {
      const s = cam.s, dx = cam.px + s * sim.x[hover] - pointer.x, dy = cam.py + s * sim.y[hover] - pointer.y;
      const r = size[hover] * Math.sqrt(s) + 2 * s;
      if (dx * dx + dy * dy <= r * r) return;
    }
    setHover(pick(pointer.x, pointer.y));
  }

  // ------------------------------------------------------------ actions
  function activate(i, newTab) {
    if (i < 0 || i >= N) return;
    const k = kinds[i], nd = model.nodes[i];
    if (k === 'tag') post({ type: 'search', query: 'tag:' + (ids[i].charAt(0) === '#' ? ids[i] : '#' + labels[i]) });
    else if (k === 'unresolved') post({ type: 'create', name: String(nd.label != null ? nd.label : ids[i]) });
    else {
      const m = { type: 'open', id: ids[i], path: paths[i] || ids[i], newTab: !!newTab };
      if (typeof nd.line === 'number') m.line = nd.line;
      post(m);
    }
  }

  // ------------------------------------------------------------ pointer events
  canvas.addEventListener('pointerdown', safe((e) => {
    hideTip(); closeMenu(); closePopup();
    canvas.focus({ preventScroll: true });
    const [x, y] = localPos(e);
    pointer.x = x; pointer.y = y; pointer.inside = true;
    if (e.button === 2) return;
    if (e.button === 1) e.preventDefault();
    if (press) return;
    const hit = pick(x, y);
    try { canvas.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
    cam.track = -1;
    if (hit >= 0) {
      press = { mode: 'node', node: hit, button: e.button, x0: x, y0: y, click: true, id: e.pointerId, ctrl: e.ctrlKey || e.metaKey };
      if (e.button === 0) { drag = hit; highlight = drag; }
    } else {
      press = { mode: 'pan', button: e.button, x0: x, y0: y, px0: cam.px, py0: cam.py, id: e.pointerId, lastT: e.timeStamp, dtAvg: 16, lastMove: e.timeStamp };
      cam.vx = cam.vy = 0;
      canvas.classList.add('is-grabbing');
    }
    kick();
  }));
  canvas.addEventListener('pointermove', safe((e) => {
    const [x, y] = localPos(e);
    pointer.x = x; pointer.y = y; pointer.inside = true;
    if (!press) { updateHover(); return; }
    if (e.pointerId !== press.id) return;
    const dx = x - press.x0, dy = y - press.y0;
    if (press.mode === 'node') {
      if (dx * dx + dy * dy > G.C.clickSlop2) press.click = false;
      if (drag >= 0 && !press.click) {
        sim.fix(drag, (x - cam.px) / cam.s, (y - cam.py) / cam.s);
        sim.reheat(G.C.reheat); sim.alphaTarget = G.C.reheat;
        canvas.classList.add('is-grabbing');
      }
    } else {
      const npx = press.px0 + dx, npy = press.py0 + dy;
      const ddx = npx - cam.px, ddy = npy - cam.py;
      cam.vx = cam.vx * 0.8 + ddx * 0.2; cam.vy = cam.vy * 0.8 + ddy * 0.2;
      const pdt = Math.max(1, e.timeStamp - press.lastT);
      press.dtAvg = press.dtAvg * 0.8 + pdt * 0.2; press.lastT = e.timeStamp; press.lastMove = e.timeStamp;
      cam.px = npx; cam.py = npy;
    }
    kick();
  }));
  function endPress(e, cancelled) {
    if (!press || (e && e.pointerId !== press.id)) return;
    const p = press; press = null;
    canvas.classList.remove('is-grabbing');
    if (p.mode === 'node') {
      if (drag >= 0) { sim.unfix(drag); sim.alphaTarget = 0; }
      const node = p.node;
      drag = -1; highlight = hover;
      if (!cancelled && p.click && (p.button === 0 || p.button === 1)) activate(node, p.button === 1 || p.ctrl || (e && (e.ctrlKey || e.metaKey)));
    } else {
      const t = e ? e.timeStamp : performance.now();
      if (reduce() || cancelled || t - p.lastMove > 100) { cam.vx = cam.vy = 0; }
      else { cam.vx /= p.dtAvg; cam.vy /= p.dtAvg; }
    }
    updateHover();
    kick();
  }
  canvas.addEventListener('pointerup', safe((e) => endPress(e, false)));
  canvas.addEventListener('pointercancel', safe((e) => endPress(e, true)));
  canvas.addEventListener('lostpointercapture', safe((e) => { if (press && e.pointerId === press.id) endPress(e, false); }));
  canvas.addEventListener('pointerleave', safe(() => { pointer.inside = false; if (!press) setHover(-1); }));
  canvas.addEventListener('auxclick', (e) => { if (e.button === 1) e.preventDefault(); });
  canvas.addEventListener('dblclick', (e) => e.preventDefault());

  canvas.addEventListener('wheel', safe((e) => {
    e.preventDefault();
    hideTip(); closeMenu();
    const [x, y] = localPos(e);
    const dy = G.wheelDelta(e);
    if (!dy) return;
    cam.track = -1;
    const t = G.clampScale(cam.ts * G.wheelFactor(dy));
    if (t < cam.s) { cam.ax = W / 2; cam.ay = H / 2; } else { cam.ax = x; cam.ay = y; }
    cam.ts = t;
    kick();
  }), { passive: false });

  canvas.addEventListener('contextmenu', safe((e) => {
    e.preventDefault();
    const [x, y] = localPos(e);
    const i = pick(x, y);
    if (i < 0) return;
    const k = kinds[i];
    if (k === 'tag') { activate(i, false); return; }
    if (k === 'unresolved') return;
    openMenu(i, x, y);
  }));

  // ------------------------------------------------------------ keyboard
  const KEYS = new Set(['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', '=', '+', '-', '_']);
  canvas.addEventListener('keydown', safe((e) => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;            // leave shortcuts to the IDE
    if (KEYS.has(e.key)) {
      e.preventDefault();
      keys.add(e.key); if (e.shiftKey) keys.add('shift'); else keys.delete('shift');
      cam.track = -1; kick();
    } else if (e.key === 'Enter' && hover >= 0) activate(hover, false);
  }));
  window.addEventListener('keyup', (e) => {
    keys.delete(e.key);
    if (e.key === 'Shift') keys.delete('shift');
    if (e.key === '+' || e.key === '=') { keys.delete('+'); keys.delete('='); }
    if (e.key === '-' || e.key === '_') { keys.delete('-'); keys.delete('_'); }
  });
  window.addEventListener('blur', () => keys.clear());
  canvas.addEventListener('blur', () => keys.clear());
  document.addEventListener('keydown', safe((e) => {
    if (e.key !== 'Escape') return;
    if (!menuEl.hidden) { closeMenu(); canvas.focus(); return; }
    if (!popupEl.hidden) { closePopup(); return; }
    if (!controlsEl.classList.contains('is-close')) { setPanelOpen(false, true); canvas.focus(); return; }
    setHover(-1);
  }));

  // ------------------------------------------------------------ context menu
  let menuItems = [], menuSel = -1, menuNode = -1;
  function openMenu(i, x, y) {
    closePopup(); hideTip();
    menuNode = i;
    menuEl.textContent = '';
    const title = document.createElement('div'); title.className = 'title'; title.textContent = labels[i]; menuEl.appendChild(title);
    const defs = [
      ['Open', () => activate(i, false)],
      ['Open in new tab', () => ctxAction('open-new-tab', i)],
      ['Open to the right', () => ctxAction('open-right', i)],
      null,
      ['Reveal in explorer', () => ctxAction('reveal', i)],
      ['Copy path', () => ctxAction('copy-path', i)],
    ];
    menuItems = [];
    for (const d of defs) {
      if (!d) { const s = document.createElement('div'); s.className = 'sep'; menuEl.appendChild(s); continue; }
      const it = document.createElement('div'); it.className = 'item'; it.setAttribute('role', 'menuitem'); it.textContent = d[0];
      const idx = menuItems.length;
      it.addEventListener('pointerenter', () => selMenu(idx));
      it.addEventListener('click', (e) => { e.stopPropagation(); closeMenu(); d[1](); canvas.focus(); });
      menuEl.appendChild(it); menuItems.push(it);
    }
    menuEl.hidden = false; menuEl.tabIndex = -1;
    const mw = menuEl.offsetWidth, mh = menuEl.offsetHeight;
    menuEl.style.left = Math.max(4, Math.min(x, W - mw - 4)) + 'px';
    menuEl.style.top = Math.max(4, Math.min(y, H - mh - 4)) + 'px';
    selMenu(-1);
    menuEl.focus({ preventScroll: true });
  }
  function selMenu(k) { menuSel = k; menuItems.forEach((it, j) => it.classList.toggle('is-sel', j === k)); }
  function closeMenu() { if (!menuEl.hidden) { menuEl.hidden = true; menuNode = -1; } }
  function ctxAction(action, i) { post({ type: 'context', action, id: ids[i], path: paths[i] || ids[i] }); }
  menuEl.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); selMenu((menuSel + 1) % menuItems.length); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); selMenu((menuSel - 1 + menuItems.length) % menuItems.length); }
    else if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); if (menuSel >= 0) menuItems[menuSel].click(); }
    else if (e.key === 'Tab') { e.preventDefault(); selMenu((menuSel + (e.shiftKey ? -1 : 1) + menuItems.length) % menuItems.length); }
  });
  document.addEventListener('pointerdown', (e) => {
    if (!menuEl.hidden && !menuEl.contains(e.target)) closeMenu();
    if (!popupEl.hidden && !popupEl.contains(e.target) && !(e.target.classList && e.target.classList.contains('swatch'))) closePopup();
  }, true);

  // ------------------------------------------------------------ tooltips
  let tipTimer = 0;
  function showTip(el) {
    clearTimeout(tipTimer);
    tipTimer = setTimeout(() => {
      const text = el.getAttribute('data-tip'); if (!text || !document.body.contains(el)) return;
      tipEl.textContent = text; tipEl.hidden = false;
      const r = el.getBoundingClientRect(), tw = tipEl.offsetWidth, th = tipEl.offsetHeight;
      let left = r.left + r.width / 2 - tw / 2, top = r.bottom + 6;
      if (top + th > H - 4) top = r.top - th - 6;
      left = Math.max(4, Math.min(left, W - tw - 4));
      tipEl.style.left = left + 'px'; tipEl.style.top = Math.max(4, top) + 'px';
    }, 450);
  }
  function hideTip() { clearTimeout(tipTimer); tipEl.hidden = true; }
  document.addEventListener('pointerover', (e) => { const el = e.target.closest && e.target.closest('[data-tip]'); if (el) showTip(el); });
  document.addEventListener('pointerout', (e) => { const el = e.target.closest && e.target.closest('[data-tip]'); if (el && !el.contains(e.relatedTarget)) hideTip(); });
  document.addEventListener('focusin', (e) => { if (e.target.matches && e.target.matches(':focus-visible') && e.target.hasAttribute('data-tip')) showTip(e.target); });
  document.addEventListener('focusout', hideTip);

  // ------------------------------------------------------------ messages to the host
  let pendingSettings = {}, settingsTimer = 0, optionsTimer = 0, scaleTimer = 0;
  function emitSettings(obj) {
    Object.assign(pendingSettings, obj);
    clearTimeout(settingsTimer);
    settingsTimer = setTimeout(flushSettings, 250);
  }
  function flushSettings() {
    clearTimeout(settingsTimer); settingsTimer = 0;
    if (!Object.keys(pendingSettings).length) return;
    const s = pendingSettings; pendingSettings = {};
    post({ type: 'settings', settings: s });
  }
  function currentOptions() {
    return {
      query: String(settings.search || ''), showTags: !!settings.showTags, showAttachments: !!settings.showAttachments,
      hideUnresolved: !!settings.hideUnresolved, showOrphans: !!settings.showOrphans, showCode: !!settings.showCode,
      groups: (settings.colorGroups || []).map((g) => ({ query: String(g.query || ''), color: g.color })),
      local: { enabled: !!local.enabled, depth: local.depth, incoming: !!local.incoming, outgoing: !!local.outgoing },
    };
  }
  function emitOptions(now) {
    clearTimeout(optionsTimer);
    const send = () => { optionsTimer = 0; post({ type: 'options', options: currentOptions() }); };
    if (now) send(); else optionsTimer = setTimeout(send, 80);
  }
  function scheduleScaleSave() {
    clearTimeout(scaleTimer);
    scaleTimer = setTimeout(() => { if (Math.abs((settings.scale || 1) - cam.ts) > 1e-6) { settings.scale = cam.ts; emitSettings({ scale: cam.ts }); } }, 500);
  }

  // ------------------------------------------------------------ controls panel
  const ICONS = {
    settings: '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
    wand: '<path d="m21.64 3.64-1.28-1.28a1.21 1.21 0 0 0-1.72 0L2.36 18.64a1.21 1.21 0 0 0 0 1.72l1.28 1.28a1.2 1.2 0 0 0 1.72 0L21.64 5.36a1.2 1.2 0 0 0 0-1.72"/><path d="m14 7 3 3"/><path d="M5 6v4"/><path d="M19 14v4"/><path d="M10 2v2"/><path d="M7 8H3"/><path d="M21 16h-4"/><path d="M11 3H9"/>',
    x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    reset: '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/>',
    chev: '<path d="m6 9 6 6 6-6"/>',
  };
  function el(tag, cls, attrs) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (attrs) for (const k in attrs) { if (k === 'text') e.textContent = attrs[k]; else e.setAttribute(k, attrs[k]); }
    return e;
  }
  function iconBtn(icon, tip, onClick) {
    const b = el('button', 'icon-btn', { type: 'button', 'data-tip': tip, 'aria-label': tip });
    b.innerHTML = '<svg viewBox="0 0 24 24">' + ICONS[icon] + '</svg>';
    b.addEventListener('click', safe((e) => { e.stopPropagation(); hideTip(); onClick(); }));
    return b;
  }
  const ui = { toggles: {}, sliders: {}, sections: {}, rows: {} };

  function toggleRow(key, label, tip, onChange) {
    const row = el('div', 'row');
    const name = el('div', 'name', { text: label });
    if (tip) name.setAttribute('data-tip', tip);
    const t = el('button', 'toggle', { type: 'button', role: 'switch', 'aria-checked': 'false', 'aria-label': label });
    t.addEventListener('click', safe(() => { const v = t.getAttribute('aria-checked') !== 'true'; t.setAttribute('aria-checked', String(v)); onChange(v); }));
    row.appendChild(name); row.appendChild(t);
    ui.toggles[key] = t; ui.rows[key] = row;
    return row;
  }
  function sliderRow(key, label, min, max, step, onChange, fmt, tip) {
    const row = el('div', 'row is-slider');
    const name = el('div', 'name'); const nm = el('span', '', { text: label }); const val = el('span', 'val');
    if (tip) nm.setAttribute('data-tip', tip);
    name.appendChild(nm); name.appendChild(val);
    const ctl = el('div', 'ctl');
    const r = el('input', '', { type: 'range', min, max, step, 'aria-label': label });
    const show = () => {
      const v = +r.value; r.style.setProperty('--pct', ((v - min) / (max - min) * 100).toFixed(2) + '%');
      val.textContent = fmt ? fmt(v) : String(v);
    };
    r.addEventListener('input', safe(() => { show(); onChange(+r.value); }));
    r.addEventListener('keydown', (e) => e.stopPropagation());
    ctl.appendChild(r); row.appendChild(name); row.appendChild(ctl);
    ui.sliders[key] = { input: r, show }; ui.rows[key] = row;
    return row;
  }
  function section(key, title, build) {
    const sec = el('div', 'sec is-collapsed');
    const head = el('div', 'sec-head', { role: 'button', tabindex: '0', 'aria-expanded': 'false' });
    const chev = el('span', 'chev'); chev.innerHTML = '<svg viewBox="0 0 24 24">' + ICONS.chev + '</svg>';
    head.appendChild(chev); head.appendChild(el('span', '', { text: title }));
    const body = el('div', 'sec-body'), inner = el('div', 'sec-inner'), pad = el('div', 'sec-pad');
    build(pad); inner.appendChild(pad); body.appendChild(inner);
    sec.appendChild(head); sec.appendChild(body);
    const toggle = () => { const c = !sec.classList.contains('is-collapsed'); setCollapsed(key, c); settings[key] = c; emitSettings({ [key]: c }); };
    head.addEventListener('click', safe(toggle));
    head.addEventListener('auxclick', safe((e) => { if (e.button === 1) toggle(); }));
    head.addEventListener('keydown', safe((e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); } }));
    ui.sections[key] = { sec, head };
    return sec;
  }
  function setCollapsed(key, c) {
    const s = ui.sections[key]; if (!s) return;
    s.sec.classList.toggle('is-collapsed', !!c);
    s.head.setAttribute('aria-expanded', String(!c));
  }

  function setOption(key, v) { settings[key] = v; emitSettings({ [key]: v }); emitOptions(); }

  const fmt2 = (v) => v.toFixed(2), fmt1 = (v) => v.toFixed(1);
  let searchInput, searchClear, groupsBox, animateBtn, wandBtn;
  function buildPanel() {
    controlsEl.textContent = '';
    const chip = el('div', 'gc-chip');
    chip.appendChild(iconBtn('settings', 'Open graph settings', () => setPanelOpen(true, true)));
    wandBtn = iconBtn('wand', 'Start timelapse animation', () => startTimelapse());
    chip.appendChild(wandBtn);
    controlsEl.appendChild(chip);
    const panel = el('div', 'gc-panel');
    const head = el('div', 'gc-head');
    head.appendChild(iconBtn('reset', 'Restore default settings', restoreDefaults));
    head.appendChild(iconBtn('x', 'Close', () => setPanelOpen(false, true)));
    panel.appendChild(head);

    panel.appendChild(section('collapse-filter', 'Filters', (b) => {
      const box = el('div', 'search');
      searchInput = el('input', '', { type: 'text', placeholder: 'Search files...', spellcheck: 'false', 'aria-label': 'Search files' });
      searchClear = iconBtn('x', 'Clear search', () => { searchInput.value = ''; onSearch(true); searchInput.focus(); });
      searchClear.classList.add('clear');
      searchInput.addEventListener('input', safe(() => onSearch(false)));
      searchInput.addEventListener('keydown', safe((e) => { if (e.key === 'Enter') { e.preventDefault(); onSearch(true); } else if (e.key === 'Escape' && searchInput.value) { e.stopPropagation(); searchInput.value = ''; onSearch(true); } }));
      box.appendChild(searchInput); box.appendChild(searchClear); b.appendChild(box);
      b.appendChild(sliderRow('localDepth', 'Depth', 1, 5, 1, (v) => { local.depth = v; settings.localDepth = v; emitSettings({ localDepth: v }); emitOptions(); computeWeights(); kick(); }, (v) => String(v), 'Show nodes this number of links away'));
      b.appendChild(toggleRow('localIncoming', 'Incoming links', 'Show links from other files', (v) => { local.incoming = v; settings.localIncoming = v; emitSettings({ localIncoming: v }); emitOptions(); }));
      b.appendChild(toggleRow('localOutgoing', 'Outgoing links', 'Show links to other files', (v) => { local.outgoing = v; settings.localOutgoing = v; emitSettings({ localOutgoing: v }); emitOptions(); }));
      b.appendChild(toggleRow('showTags', 'Tags', 'Tags are linked to the files that contain them', (v) => setOption('showTags', v)));
      b.appendChild(toggleRow('showAttachments', 'Attachments', 'Show attachments', (v) => setOption('showAttachments', v)));
      b.appendChild(toggleRow('hideUnresolved', 'Existing files only', 'When enabled, links to nonexistent files are not shown', (v) => setOption('hideUnresolved', v)));
      b.appendChild(toggleRow('showOrphans', 'Orphans', 'Show files that are not linked to any other file', (v) => setOption('showOrphans', v)));
      b.appendChild(toggleRow('showCode', 'Code files', 'Show source code files and their imports', (v) => setOption('showCode', v)));
    }));
    panel.appendChild(section('collapse-color-groups', 'Groups', (b) => {
      groupsBox = el('div', 'groups'); b.appendChild(groupsBox);
      const add = el('button', 'btn groups-new', { type: 'button', text: 'New group' });
      add.addEventListener('click', safe(() => {
        const list = (settings.colorGroups || []).slice();
        list.push({ query: '', color: G.newGroupColor(list.length, pal.dark) });
        settings.colorGroups = list; groupsKey = null; renderGroupsIfChanged(); groupsChanged();
        const inputs = groupsBox.querySelectorAll('input'); if (inputs.length) inputs[inputs.length - 1].focus();
      }));
      b.appendChild(add);
    }));
    panel.appendChild(section('collapse-display', 'Display', (b) => {
      b.appendChild(toggleRow('showArrow', 'Arrows', 'Show arrows when zoomed in', (v) => { settings.showArrow = v; emitSettings({ showArrow: v }); kick(); }));
      b.appendChild(sliderRow('textFadeMultiplier', 'Text fade threshold', -3, 3, 0.1, (v) => display('textFadeMultiplier', v), fmt1));
      b.appendChild(sliderRow('nodeSizeMultiplier', 'Node size', 0.1, 5, 0.01, (v) => display('nodeSizeMultiplier', v), fmt2));
      b.appendChild(sliderRow('lineSizeMultiplier', 'Link thickness', 0.1, 5, 0.01, (v) => display('lineSizeMultiplier', v), fmt2));
      const row = el('div', 'row'); ui.rows.animate = row;
      animateBtn = el('button', 'btn', { type: 'button', text: 'Animate', 'data-tip': 'Start timelapse animation' });
      animateBtn.addEventListener('click', safe(() => startTimelapse()));
      row.appendChild(animateBtn); b.appendChild(row);
    }));
    panel.appendChild(section('collapse-forces', 'Forces', (b) => {
      b.appendChild(sliderRow('centerStrength', 'Center force', 0, 1, 0.001, (v) => force('centerStrength', v), fmt2));
      b.appendChild(sliderRow('repelStrength', 'Repel force', 0, 20, 0.01, (v) => force('repelStrength', v), fmt1));
      b.appendChild(sliderRow('linkStrength', 'Link force', 0, 1, 0.001, (v) => force('linkStrength', v), fmt2));
      b.appendChild(sliderRow('linkDistance', 'Link distance', 30, 500, 1, (v) => force('linkDistance', v), (v) => String(Math.round(v))));
    }));
    controlsEl.appendChild(panel);
    controlsEl.addEventListener('pointerdown', (e) => e.stopPropagation());
    controlsEl.addEventListener('wheel', (e) => e.stopPropagation(), { passive: true });
    syncPanel();
  }
  function onSearch(now) {
    settings.search = searchInput.value;
    searchClear.hidden = !searchInput.value;
    emitSettings({ search: settings.search });
    emitOptions(now);
  }
  function display(key, v) { settings[key] = v; emitSettings({ [key]: v }); kick(); }
  function force(key, v) { settings[key] = v; sim.setForces(settings); sim.reheat(G.C.reheat); emitSettings({ [key]: v }); kick(); }
  function groupsChanged() { emitSettings({ colorGroups: settings.colorGroups }); emitOptions(); kick(); }

  function renderGroups() {
    if (!groupsBox) return;
    groupsBox.textContent = '';
    (settings.colorGroups || []).forEach((g, k) => {
      const row = el('div', 'group');
      const q = el('input', '', { type: 'text', placeholder: 'Enter query...', spellcheck: 'false', 'aria-label': 'Group query' });
      q.value = g.query || '';
      q.addEventListener('input', safe(() => {
        settings.colorGroups = settings.colorGroups.slice();
        settings.colorGroups[k] = Object.assign({}, settings.colorGroups[k], { query: q.value });
        groupsKey = JSON.stringify(settings.colorGroups); groupsChanged();
      }));
      const sw = el('button', 'swatch', { type: 'button', 'data-tip': 'Click to change color', 'aria-label': 'Group colour' });
      sw.style.background = G.toHex(G.parseColor(g.color));
      sw.addEventListener('click', safe((e) => { e.stopPropagation(); openPopup(k, sw); }));
      const del = iconBtn('x', 'Delete group', () => { settings.colorGroups = settings.colorGroups.filter((_, j) => j !== k); renderGroupsIfChanged(); groupsChanged(); });
      row.appendChild(q); row.appendChild(sw); row.appendChild(del);
      groupsBox.appendChild(row);
    });
  }
  function colorValue(k, c) {
    // Keep the representation the host used for this group (graph.json {a, rgb} by default).
    const old = settings.colorGroups[k] && settings.colorGroups[k].color;
    if (typeof old === 'string') return G.toHex(c);
    return { a: 1, rgb: (Math.round(c.r) << 16) | (Math.round(c.g) << 8) | Math.round(c.b) };
  }
  function openPopup(k, anchor) {
    closeMenu(); hideTip();
    popupEl.textContent = '';
    const setColor = (c) => {
      settings.colorGroups = settings.colorGroups.slice();
      settings.colorGroups[k] = Object.assign({}, settings.colorGroups[k], { color: colorValue(k, c) });
      anchor.style.background = G.toHex(c); groupsKey = JSON.stringify(settings.colorGroups); groupsChanged();
    };
    for (const c of pal.groups) {
      const b = el('button', 'swatch', { type: 'button', 'aria-label': G.toHex(c) });
      b.style.background = G.toHex(c);
      b.addEventListener('click', safe(() => { setColor(c); closePopup(); }));
      popupEl.appendChild(b);
    }
    const custom = el('label', 'custom', { text: 'Custom' });
    const inp = el('input', '', { type: 'color', 'aria-label': 'Custom colour' });
    inp.value = G.toHex(G.parseColor(settings.colorGroups[k].color));
    inp.addEventListener('input', safe(() => setColor(G.parseColor(inp.value))));
    custom.appendChild(inp); popupEl.appendChild(custom);
    popupEl.hidden = false;
    const r = anchor.getBoundingClientRect(), pw = popupEl.offsetWidth, ph = popupEl.offsetHeight;
    let left = r.right - pw, top = r.bottom + 6;
    if (top + ph > H - 4) top = Math.max(4, r.top - ph - 6);
    popupEl.style.left = Math.max(4, Math.min(left, W - pw - 4)) + 'px';
    popupEl.style.top = top + 'px';
  }
  function closePopup() { popupEl.hidden = true; }

  function syncPanel() {
    if (!searchInput) return;
    if (document.activeElement !== searchInput) searchInput.value = settings.search || '';
    searchClear.hidden = !searchInput.value;
    for (const k in ui.toggles) {
      const v = k === 'localIncoming' ? local.incoming : k === 'localOutgoing' ? local.outgoing : !!settings[k];
      ui.toggles[k].setAttribute('aria-checked', String(!!v));
    }
    for (const k in ui.sliders) {
      const v = k === 'localDepth' ? local.depth : settings[k];
      if (typeof v === 'number' && isFinite(v)) ui.sliders[k].input.value = String(v);
      ui.sliders[k].show();
    }
    for (const k of ['collapse-filter', 'collapse-color-groups', 'collapse-display', 'collapse-forces']) setCollapsed(k, settings[k] !== false);
    const loc = !!local.enabled;
    for (const k of ['localDepth', 'localIncoming', 'localOutgoing']) ui.rows[k].classList.toggle('is-hidden', !loc);
    ui.rows.showOrphans.classList.toggle('is-hidden', loc);
    ui.rows.animate.classList.toggle('is-hidden', loc);
    wandBtn.style.display = loc ? 'none' : '';
    renderGroupsIfChanged();
    document.body.classList.toggle('reduce-motion', reduce());
  }
  let groupsKey = null;
  function renderGroupsIfChanged() {
    const key = JSON.stringify(settings.colorGroups || []);
    if (key === groupsKey || !groupsBox) return;
    groupsKey = key;
    // keep the caret in the query box being edited across a re-render
    const inputs = [...groupsBox.querySelectorAll('input')];
    const at = inputs.indexOf(document.activeElement);
    const caret = at >= 0 ? document.activeElement.selectionStart : 0;
    renderGroups();
    const again = groupsBox.querySelectorAll('input');
    if (at >= 0 && again[at]) { again[at].focus(); try { again[at].setSelectionRange(caret, caret); } catch (e) { /* ignore */ } }
  }
  let panelTimer = 0;
  function setPanelOpen(open, user) {
    const isOpen = !controlsEl.classList.contains('is-close') && !controlsEl.classList.contains('is-closing');
    if (open === isOpen) return;
    clearTimeout(panelTimer);
    closePopup(); hideTip();
    settings.close = !open;
    if (user) emitSettings({ close: !open });
    if (open) {
      controlsEl.classList.remove('is-close', 'is-closing');
      if (reduce()) return;
      controlsEl.classList.add('is-opening');
      void controlsEl.offsetWidth;
      requestAnimationFrame(() => controlsEl.classList.remove('is-opening'));
    } else {
      if (reduce()) { controlsEl.classList.add('is-close'); return; }
      controlsEl.classList.add('is-closing');
      panelTimer = setTimeout(() => { controlsEl.classList.remove('is-closing'); controlsEl.classList.add('is-close'); }, 120);
    }
  }

  function restoreDefaults() {
    const D = G.DEFAULTS, changed = {};
    for (const k of G.FILTER_KEYS.concat(G.DISPLAY_KEYS, G.FORCE_KEYS)) { settings[k] = D[k]; changed[k] = D[k]; }
    settings.colorGroups = []; changed.colorGroups = [];
    if (local.enabled) {
      local.depth = 1; local.incoming = true; local.outgoing = true;
      Object.assign(changed, { localDepth: 1, localIncoming: true, localOutgoing: true });
      Object.assign(settings, { localDepth: 1, localIncoming: true, localOutgoing: true });
    }
    sim.setForces(settings); sim.reheat(G.C.reheat);
    groupsKey = null; syncPanel();
    emitSettings(changed); emitOptions();
    computeWeights(); kick();
  }

  // ------------------------------------------------------------ timelapse (spec D.6)
  function startTimelapse() {
    if (!rawData || local.enabled) return;
    stopTimelapse();
    const full = rawData;
    const tl = G.timelapse(full, settings.showOrphans !== false);
    if (!tl.total) return;
    const speed = G.timelapseSpeed(tl.total);
    const t0 = performance.now();
    const st = timelapseState = { prog: 0, timer: 0 };
    if (animateBtn) animateBtn.classList.add('is-active');
    const step = () => {
      if (timelapseState !== st || paused) return;
      const n = 1 + Math.floor(speed * (performance.now() - t0) / 1000);
      if (n !== st.prog) {
        st.prog = n;
        if (n > tl.total) { applyData(full, true); stopTimelapse(); return; }
        applyData(tl.subset(n), true);
      }
      st.timer = setTimeout(step, 1000 / 30);
    };
    step();
  }
  function stopTimelapse() {
    if (timelapseState) clearTimeout(timelapseState.timer);
    timelapseState = null;
    if (animateBtn) animateBtn.classList.remove('is-active');
  }

  // ------------------------------------------------------------ public API
  IDGraph.setData = safe((g) => applyData(g || { nodes: [], links: [] }, false));

  IDGraph.setTheme = safe((c) => {
    themeRaw = Object.assign({}, G.DARK_THEME, c || {});
    const next = G.palette(themeRaw);
    palFrom = pal; palTo = next; palT = reduce() ? 1 : 0;
    if (palT >= 1) setPal(Object.assign({}, next)); else stepTheme(0);
    setCssVars(next);
    if (!popupEl.hidden) closePopup();
    kick();
  });

  IDGraph.setSettings = safe((s) => {
    if (!s || typeof s !== 'object') return;
    let forces = false;
    for (const k in s) {
      const v = s[k];
      if (!(k in G.DEFAULTS) && k !== 'colorGroups') continue;
      if (k === 'colorGroups') { settings.colorGroups = Array.isArray(v) ? v.map((g) => ({ query: String((g && g.query) || ''), color: g && g.color })) : []; continue; }
      const d = G.DEFAULTS[k];
      if (typeof d === 'number' && (typeof v !== 'number' || !isFinite(v))) continue;
      if (typeof d === 'boolean' && typeof v !== 'boolean') continue;
      if (typeof d === 'string' && typeof v !== 'string') continue;
      settings[k] = v;
      if (G.FORCE_KEYS.includes(k)) forces = true;
    }
    if ('localDepth' in s && typeof s.localDepth === 'number') local.depth = Math.max(1, Math.min(5, Math.round(s.localDepth)));
    if ('localIncoming' in s && typeof s.localIncoming === 'boolean') local.incoming = s.localIncoming;
    if ('localOutgoing' in s && typeof s.localOutgoing === 'boolean') local.outgoing = s.localOutgoing;
    if (forces) { sim.setForces(settings); if (hasData) sim.reheat(G.C.reheat); }
    if (typeof s.scale === 'number' && isFinite(s.scale) && Math.abs(s.scale / cam.ts - 1) > 0.01) {
      cam.ts = G.clampScale(s.scale); cam.ax = W / 2; cam.ay = H / 2;
    }
    if (typeof s.close === 'boolean') setPanelOpen(!s.close, false);
    if ('localDepth' in s || 'localIncoming' in s || 'localOutgoing' in s) computeWeights();
    syncPanel();
    kick();
  });

  IDGraph.setActive = safe((id) => {
    activeId = id == null ? null : String(id);
    activeIdx = activeId != null ? nodeIndex(activeId) : -1;
    if (activeIdx < 0 && activeId != null) activeIdx = paths.indexOf(activeId);
    kick();
  });

  IDGraph.focus = safe((id) => {
    let i = nodeIndex(String(id)); if (i < 0) i = paths.indexOf(String(id));
    if (i < 0) return false;
    if (cam.ts < 0.5) { cam.ts = 1; cam.ax = W / 2; cam.ay = H / 2; }
    cam.track = i; cam.trackT = 0; cam.vx = cam.vy = 0;
    kick();
    return true;
  });

  IDGraph.pause = safe(() => {
    paused = true;
    if (raf) { cancelAnimationFrame(raf); raf = 0; }
    lastTs = 0; perf.lastFrameTs = 0; keys.clear();
    if (timelapseState) clearTimeout(timelapseState.timer);
  });
  IDGraph.resume = safe(() => {
    if (!paused) { kick(); return; }
    paused = false; lastTs = 0; perf.lastFrameTs = 0;
    if (timelapseState) { const st = timelapseState; stopTimelapse(); void st; }
    resize(); kick();
  });

  IDGraph.setLocal = safe((o) => {
    o = o || {};
    const wasPath = local.path, wasEnabled = local.enabled;
    if (typeof o.enabled === 'boolean') local.enabled = o.enabled;
    if ('path' in o) local.path = o.path == null ? null : String(o.path);
    if (typeof o.depth === 'number') local.depth = Math.max(1, Math.min(5, Math.round(o.depth)));
    if (typeof o.incoming === 'boolean') local.incoming = o.incoming;
    if (typeof o.outgoing === 'boolean') local.outgoing = o.outgoing;
    if (local.enabled && (local.path !== wasPath || !wasEnabled)) { cam.px = W / 2; cam.py = H / 2; cam.vx = cam.vy = 0; cam.track = -1; }
    if (local.enabled) stopTimelapse();
    computeWeights(); syncPanel(); kick();
  });

  IDGraph.stats = safe(() => ({
    fps: Math.round(perf.fps * 10) / 10, frameMs: Math.round(perf.frameMs * 100) / 100,
    tickMs: Math.round(sim.tickMs * 100) / 100, nodes: N, links: lines.length,
    alpha: sim.alpha, scale: cam.s, motion: motion(), targetScale: cam.ts, paused, running: !!raf,
    simRunning: N > 0 && (sim.running || drag >= 0), frames: perf.frames, dpr, lowQuality,
    pan: [cam.px, cam.py], hover: hover >= 0 ? ids[hover] : null, width: W, height: H,
  }));

  /** Mean on-screen node speed in px per tick (visual settle metric). */
  function motion() {
    if (!N) return 0;
    let m = 0; for (let i = 0; i < N; i++) m += Math.hypot(sim.vx[i], sim.vy[i]);
    return Math.round(m / N * cam.s * 1000) / 1000;
  }

  IDGraph.demo = safe((n, seed) => {
    random = G.rng(seed == null ? 1 : seed);
    const g = G.demo(n || 300, seed == null ? 1 : seed);
    applyData(g, false);
    return { nodes: g.nodes.length, links: g.links.length };
  });

  // Test / host helpers (not part of the contract, read-only).
  IDGraph._debug = {
    screenPos: (id) => { const i = nodeIndex(String(id)); if (i < 0) return null; return [cam.px + cam.s * sim.x[i], cam.py + cam.s * sim.y[i], size[i] * Math.sqrt(cam.s)]; },
    worldPos: (id) => { const i = nodeIndex(String(id)); return i < 0 ? null : [sim.x[i], sim.y[i]]; },
    nodeAlpha: (id) => { const i = nodeIndex(String(id)); return i < 0 ? null : na[i]; },
    nodeColor: (id) => { const i = nodeIndex(String(id)); return i < 0 ? null : G.toHex({ r: tr[i], g: tg[i], b: tb[i] }); },
    ids: () => ids.slice(),
    settings: () => JSON.parse(JSON.stringify(settings)),
    local: () => Object.assign({}, local),
    flush: () => { flushSettings(); if (optionsTimer) { clearTimeout(optionsTimer); post({ type: 'options', options: currentOptions() }); optionsTimer = 0; } },
    menuOpen: () => !menuEl.hidden,
    panelOpen: () => !controlsEl.classList.contains('is-close'),
    timelapse: () => !!timelapseState,
    weight: (id) => { const i = nodeIndex(String(id)); return i < 0 ? null : weight[i]; },
  };

  // ------------------------------------------------------------ boot
  setPal(G.palette(themeRaw)); palFrom = palTo = pal;
  setCssVars();
  buildPanel();
  setPanelOpen(!settings.close, false);
  sim.setForces(settings);
  if (typeof ResizeObserver !== 'undefined') new ResizeObserver(() => resize()).observe(canvas);
  window.addEventListener('resize', () => resize());
  resize();
  const q = /[?&]demo=(\d+)/.exec(location.search || '');
  if (q) IDGraph.demo(+q[1], 1);
  kick();
})();
