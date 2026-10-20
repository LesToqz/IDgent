/* IDgent graph view: pure logic (no DOM). Force simulation, Barnes-Hut quadtree,
 * size/alpha formulas, easing, camera math, hit-testing, local-graph weights,
 * colour helpers, demo generator. Loaded as a classic <script> (window.GraphCore)
 * and as a CommonJS module by the node tests. */
(function (root) {
  'use strict';

  // ---------------------------------------------------------------- constants
  // IDgent tuning, selected for readable project maps (docs/graph-spec.md).
  const C = {
    alphaMin: 0.002,
    alphaDecay: 1 - Math.pow(0.002, 1 / 240),
    velocityDecay: 0.45,
    theta: 0.65,
    distanceMin2: 64,
    collideRadius: 32,
    collideStrength: 0.7,
    reheat: 0.4,
    tickHz: 60,
    minScale: 0.04,
    maxScale: 6,
    hoverDim: 0.28,
    easeK: 0.16,
    zoomK: 0.18,
    panFilter: 0.25,
    panDecay: 0.86,
    wheelBase: Math.exp(120 / 480),
    labelDrop: 12,
    labelWrap: 224,
    clickSlop2: 36,
  };

  const DEFAULTS = {
    "search": "",
    "showTags": false,
    "showAttachments": false,
    "hideUnresolved": false,
    "showOrphans": true,
    "showCode": true,
    "showArrow": true,
    "textFadeMultiplier": 0.2,
    "nodeSizeMultiplier": 1.15,
    "lineSizeMultiplier": 1.25,
    "centerStrength": 0.12,
    "repelStrength": 6,
    "linkStrength": 0.35,
    "linkDistance": 160,
    "colorGroups": [],
    "collapse-filter": false,
    "collapse-display": true,
    "collapse-forces": true,
    "collapse-color-groups": true,
    "scale": 1,
    "localDepth": 2,
    "localIncoming": true,
    "localOutgoing": true,
    "close": false,
    "reduceMotion": false
};
  const LIMITS = {
    "textFadeMultiplier": [
        -1,
        2
    ],
    "nodeSizeMultiplier": [
        0.5,
        3
    ],
    "lineSizeMultiplier": [
        0.5,
        4
    ],
    "centerStrength": [
        0,
        0.6
    ],
    "repelStrength": [
        0,
        24
    ],
    "linkStrength": [
        0,
        1.5
    ],
    "linkDistance": [
        48,
        480
    ],
    "scale": [
        0.04,
        6
    ],
    "localDepth": [
        1,
        6
    ]
};
  const STEPS = {
    "textFadeMultiplier": 0.1,
    "nodeSizeMultiplier": 0.05,
    "lineSizeMultiplier": 0.25,
    "centerStrength": 0.02,
    "repelStrength": 0.5,
    "linkStrength": 0.05,
    "linkDistance": 8,
    "scale": null,
    "localDepth": 1
};
  const DISPLAY_KEYS = ['showArrow', 'textFadeMultiplier', 'nodeSizeMultiplier', 'lineSizeMultiplier'];
  const FORCE_KEYS = ['centerStrength', 'repelStrength', 'linkStrength', 'linkDistance'];
  const FILTER_KEYS = ['showTags', 'showAttachments', 'hideUnresolved', 'showOrphans', 'showCode'];

  // ---------------------------------------------------------------- formulas
  const clamp = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);

  /** Node size: clamp before the multiplier. */
  function nodeSize(weight, mult) {
    return (mult == null ? 1 : mult) * Math.min(24, 6 + 2 * Math.log2(1 + Math.max(0, weight)));
  }
  /** On-screen radius: size * sqrt(s). */
  function screenRadius(size, s) { return size * Math.sqrt(s); }
  /** Label alpha. */
  function textAlpha(s, tf) { return clamp((s - 0.12) / 0.7 - (tf || 0) * 0.25, 0, 1); }
  /** World line width -> constant mult px on screen. */
  function lineWorldWidth(mult, s) { return mult / s; }
  /** Arrow alpha factor. */
  function arrowFactor(s) { return clamp((s - 0.18) / 0.62, 0, 1); }
  /** Arrow on-screen length/height in px. */
  function arrowSize(mult) { return 6 * Math.sqrt(mult); }
  /** Label font size in text units. */
  function labelFont(size) { return 12 + size * 0.2; }

  /** Direct, bounded controls: no hidden taper or minimum repulsion. */
  function boundedSetting(st, key) {
    const [lo, hi] = LIMITS[key];
    return clamp(num(st[key], DEFAULTS[key]), lo, hi);
  }
  function forceParams(st) {
    return {
      center: boundedSetting(st, 'centerStrength'),
      link: boundedSetting(st, 'linkStrength'),
      distance: boundedSetting(st, 'linkDistance'),
      repel: -120 * boundedSetting(st, 'repelStrength'),
    };
  }
  function num(v, d) { return typeof v === 'number' && isFinite(v) ? v : d; }

  /** Convert a per-frame (60 Hz) approach fraction k to a dt-based one. */
  function easeFactor(k, dt) { return 1 - Math.pow(1 - k, clamp(dt, 0, 0.05) * 60); }
  /** Ease value toward target; reduce => jump. */
  function ease(v, target, k, dt, reduce) {
    if (reduce) return target;
    return v + (target - v) * easeFactor(k, dt);
  }
  /** Wheel delta -> zoom factor. */
  function wheelDelta(ev) {
    let dy = ev.deltaY || 0;
    if (ev.deltaMode === 1) dy *= 24; else if (ev.deltaMode === 2) dy *= 640;
    // Trackpad pinch arrives as ctrl+wheel with small pixel deltas: make it as lively as a notch.
    if (ev.ctrlKey && ev.deltaMode === 0 && Math.abs(dy) < 32) dy *= 2;
    return dy;
  }
  function wheelFactor(dy) { return Math.exp(-dy / 480); }
  function clampScale(s) { return clamp(s, C.minScale, C.maxScale); }
  /** Keep the world point under `anchor` fixed when going from s0 to s1. */
  function zoomAround(pan, s0, s1, anchor) {
    return anchor - (anchor - pan) * (s1 / s0);
  }
  function timelapseSpeed(total) { return clamp(total / 12, 8, 72); }
  function localWeight(layer) { return 12 / (Math.max(0, layer) + 1); }

  // ---------------------------------------------------------------- colours
  function parseColor(c, fallback) {
    if (c == null) return fallback || { r: 136, g: 136, b: 136, a: 1 };
    if (typeof c === 'object') {
      if (typeof c.rgb === 'number') return { r: (c.rgb >> 16) & 255, g: (c.rgb >> 8) & 255, b: c.rgb & 255, a: num(c.a, 1) };
      if ('r' in c) return { r: c.r, g: c.g, b: c.b, a: num(c.a, 1) };
    }
    if (typeof c === 'number') return { r: (c >> 16) & 255, g: (c >> 8) & 255, b: c & 255, a: 1 };
    const s = String(c).trim();
    let m = /^#([0-9a-f]{3,8})$/i.exec(s);
    if (m) {
      let h = m[1];
      if (h.length === 3 || h.length === 4) h = h.split('').map((x) => x + x).join('');
      const v = parseInt(h.slice(0, 6), 16);
      return { r: (v >> 16) & 255, g: (v >> 8) & 255, b: v & 255, a: h.length === 8 ? parseInt(h.slice(6), 16) / 255 : 1 };
    }
    m = /^rgba?\(([^)]+)\)$/i.exec(s);
    if (m) {
      const p = m[1].split(/[\s,/]+/).filter(Boolean).map(parseFloat);
      if (p.length >= 3) return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
    }
    return fallback || { r: 136, g: 136, b: 136, a: 1 };
  }
  const hex2 = (v) => (v < 16 ? '0' : '') + (v | 0).toString(16);
  function toHex(c) { return '#' + hex2(clamp(Math.round(c.r), 0, 255)) + hex2(clamp(Math.round(c.g), 0, 255)) + hex2(clamp(Math.round(c.b), 0, 255)); }
  function toCss(c, a) {
    return 'rgba(' + Math.round(c.r) + ',' + Math.round(c.g) + ',' + Math.round(c.b) + ',' + (a == null ? c.a : a).toFixed(3) + ')';
  }
  function mix(a, b, t) {
    return { r: a.r + (b.r - a.r) * t, g: a.g + (b.g - a.g) * t, b: a.b + (b.b - a.b) * t, a: a.a + (b.a - a.a) * t };
  }
  function hslToRgb(h, s, l) {
    s /= 100; l /= 100;
    const k = (n) => (n + h / 30) % 12;
    const a = s * Math.min(l, 1 - l);
    const f = (n) => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
    return { r: Math.round(f(0) * 255), g: Math.round(f(8) * 255), b: Math.round(f(4) * 255), a: 1 };
  }
  /** New colour group default. */
  function newGroupColor(count, dark) {
    const c = hslToRgb((210 + 137.5 * count) % 360, 65, dark ? 64 : 42);
    return toHex(c);
  }

  const DARK_THEME = {
    background: '#17212b', node: '#adc2d4', node_focused: '#63b3ed', node_tag: '#70d6a0',
    node_attachment: '#e7c66b', node_unresolved: '#7d8f9e', node_code: '#65d3df',
    line: '#506779', line_highlight: '#63b3ed', text: '#e4edf5', text_muted: '#a1b5c6',
    accent: '#63b3ed', dark: true,
    groups: ['#63b3ed', '#ed9e63', '#70d6a0', '#c9a0ed', '#e7c66b', '#65d3df',
      '#ed839d', '#a6cf72', '#a4a9f0', '#d5ad88', '#8abfbc', '#c4bead'],
    font_ui: 'system-ui, sans-serif', font_text: 'system-ui, sans-serif',
  };

  /** Resolve the renderer palette from a theme object. */
  function palette(theme) {
    const t = Object.assign({}, DARK_THEME, theme || {});
    const P = (k, d) => parseColor(t[k], parseColor(DARK_THEME[k] || d));
    const unres = P('node_unresolved'); unres.a *= 0.65;
    const text = P('text');
    const arrow = Object.assign({}, text, { a: text.a * 0.7 });
    const accent = P('accent');
    return {
      background: P('background'), fill: P('node'), focused: P('node_focused'),
      tag: P('node_tag'), attachment: P('node_attachment'), unresolved: unres,
      code: P('node_code'), line: P('line'),
      lineHighlight: t.line_highlight ? P('line_highlight') : accent,
      highlight: accent, circle: P('node_focused'), text, textMuted: P('text_muted'),
      arrow, accent, groups: (t.groups || DARK_THEME.groups).map((g) => parseColor(g)),
      dark: t.dark !== false, fontUi: t.font_ui || DARK_THEME.font_ui,
    };
  }

  // ---------------------------------------------------------------- RNG
  function rng(seed) {
    let a = (seed >>> 0) || 0x9e3779b9;
    return function () {
      a = (a + 0x6d2b79f5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  // ---------------------------------------------------------------- quadtree
  /** Flat Barnes-Hut quadtree with aggregated count/centroid per cell. */
  class QuadTree {
    constructor() { this.cap = 0; this._grow(256); this.ncap = 0; }
    _grow(n) {
      const old = this.cap ? this : null;
      const cap = Math.max(n, this.cap * 2);
      const child = new Int32Array(cap * 4).fill(-1);
      const cnt = new Float64Array(cap), sx = new Float64Array(cap), sy = new Float64Array(cap);
      const leaf = new Int32Array(cap).fill(-1), size = new Float64Array(cap);
      const ox = new Float64Array(cap), oy = new Float64Array(cap);
      if (old) {
        child.set(this.child); cnt.set(this.cnt); sx.set(this.sx); sy.set(this.sy);
        leaf.set(this.leaf); size.set(this.size); ox.set(this.ox); oy.set(this.oy);
      }
      Object.assign(this, { cap, child, cnt, sx, sy, leaf, size, ox, oy });
    }
    _cell(x0, y0, w) {
      if (this.n >= this.cap) this._grow(this.n + 1);
      const c = this.n++;
      this.child.fill(-1, c * 4, c * 4 + 4);
      this.cnt[c] = 0; this.sx[c] = 0; this.sy[c] = 0; this.leaf[c] = -1;
      this.ox[c] = x0; this.oy[c] = y0; this.size[c] = w;
      return c;
    }
    /** Build over points (xs, ys) of length n. */
    build(xs, ys, n) {
      if (this.ncap < n) { this.next = new Int32Array(Math.max(n, 64)); this.ncap = this.next.length; }
      this.n = 0;
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      for (let i = 0; i < n; i++) {
        const x = xs[i], y = ys[i];
        if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
      }
      if (!(x1 >= x0)) { x0 = y0 = 0; x1 = y1 = 1; }
      const w = Math.max(x1 - x0, y1 - y0, 1) * 1.0001 + 1;
      this._cell(x0, y0, w);
      for (let i = 0; i < n; i++) this._insert(i, xs[i], ys[i]);
    }
    _insert(i, x, y) {
      let c = 0, depth = 0;
      const next = this.next;
      for (;;) {
        this.cnt[c] += 1; this.sx[c] += x; this.sy[c] += y;
        const ci = c * 4;
        const isLeaf = this.child[ci] < 0 && this.child[ci + 1] < 0 && this.child[ci + 2] < 0 && this.child[ci + 3] < 0;
        if (isLeaf) {
          const j = this.leaf[c];
          if (j < 0 && this.cnt[c] === 1) { this.leaf[c] = i; next[i] = -1; return; }
          // occupied leaf: coincident points (or too deep) chain in the same leaf
          const px = this.xs[j], py = this.ys[j];
          if ((px === x && py === y) || depth > 40) { next[i] = next[j]; next[j] = i; return; }
          // split: push the chain of j down into a child
          this.leaf[c] = -1;
          const h = this.size[c] / 2;
          const q = (px >= this.ox[c] + h ? 1 : 0) | (py >= this.oy[c] + h ? 2 : 0);
          const nc = this._cell(this.ox[c] + (q & 1 ? h : 0), this.oy[c] + (q & 2 ? h : 0), h);
          this.child[c * 4 + q] = nc;
          let k = j, m = 0; while (k >= 0) { m++; k = next[k]; }
          this.cnt[nc] = m; this.sx[nc] = px * m; this.sy[nc] = py * m; this.leaf[nc] = j;
        }
        const h = this.size[c] / 2;
        const q = (x >= this.ox[c] + h ? 1 : 0) | (y >= this.oy[c] + h ? 2 : 0);
        let nc = this.child[c * 4 + q];
        if (nc < 0) {
          nc = this._cell(this.ox[c] + (q & 1 ? h : 0), this.oy[c] + (q & 2 ? h : 0), h);
          this.child[c * 4 + q] = nc;
        }
        c = nc; depth++;
      }
    }
  }

  // ---------------------------------------------------------------- simulation
  class Simulation {
    constructor() {
      this.n = 0; this.cap = 0;
      this.alpha = 1; this.alphaTarget = 0;
      this.alphaDecay = C.alphaDecay; this.alphaMin = C.alphaMin;
      this.velocityDecay = C.velocityDecay; this.theta = C.theta;
      this.params = forceParams(DEFAULTS);
      this.ids = []; this.index = new Map();
      this.ls = new Int32Array(0); this.lt = new Int32Array(0);
      this.lbias = new Float64Array(0); this.lstr = new Float64Array(0); this.m = 0;
      this.qt = new QuadTree();
      this.tickMs = 0; this.ticks = 0;
      this.repelBudgetMs = Infinity; this.adaptTheta = C.theta;
      this._ensure(64);
    }
    _ensure(n) {
      if (n <= this.cap) return;
      const cap = Math.max(n, this.cap * 2, 64);
      const grow = (a, T, fill) => { const b = new T(cap); if (fill !== undefined) b.fill(fill); if (a) b.set(a.subarray(0, this.n)); return b; };
      this.x = grow(this.x, Float32Array); this.y = grow(this.y, Float32Array);
      this.vx = grow(this.vx, Float32Array); this.vy = grow(this.vy, Float32Array);
      this.fx = grow(this.fx, Float32Array, NaN); this.fy = grow(this.fy, Float32Array, NaN);
      this.cx = new Float64Array(cap); this.cy = new Float64Array(cap);
      this.cap = cap;
    }
    /** Replace the node set. nodes: [{id, x, y}] (x/y optional for kept ids). Kept ids keep state. */
    setNodes(list) {
      const oldIndex = this.index, ox = this.x, oy = this.y, ovx = this.vx, ovy = this.vy, ofx = this.fx, ofy = this.fy;
      const n = list.length;
      const x = new Float32Array(Math.max(n, 64)), y = new Float32Array(x.length), vx = new Float32Array(x.length), vy = new Float32Array(x.length);
      const fx = new Float32Array(x.length).fill(NaN), fy = new Float32Array(x.length).fill(NaN);
      const index = new Map(), ids = new Array(n);
      for (let i = 0; i < n; i++) {
        const it = list[i]; ids[i] = it.id; index.set(it.id, i);
        const j = oldIndex.get(it.id);
        if (j !== undefined) { x[i] = ox[j]; y[i] = oy[j]; vx[i] = ovx[j]; vy[i] = ovy[j]; fx[i] = ofx[j]; fy[i] = ofy[j]; }
        else { x[i] = it.x || 0; y[i] = it.y || 0; }
      }
      Object.assign(this, { x, y, vx, vy, fx, fy, ids, index, n, cap: x.length });
      this.cx = new Float64Array(this.cap); this.cy = new Float64Array(this.cap);
    }
    /** links: array of [s, t] index pairs (self links ignored). */
    setLinks(pairs) {
      const m = pairs.length;
      const ls = new Int32Array(m), lt = new Int32Array(m);
      const count = new Int32Array(this.n);
      let k = 0;
      for (let i = 0; i < m; i++) {
        const s = pairs[i][0], t = pairs[i][1];
        if (s === t || s < 0 || t < 0 || s >= this.n || t >= this.n) continue;
        ls[k] = s; lt[k] = t; k++; count[s]++; count[t]++;
      }
      this.m = k; this.ls = ls; this.lt = lt;
      this.lbias = new Float64Array(k); this.lstr = new Float64Array(k);
      for (let i = 0; i < k; i++) {
        const cs = count[ls[i]], ct = count[lt[i]];
        this.lbias[i] = cs / (cs + ct);
        this.lstr[i] = 1 / Math.min(cs, ct);
      }
      this.degreeCount = count;
    }
    setForces(st) { this.params = forceParams(st); }
    reheat(a) { this.alpha = Math.max(this.alpha, a == null ? C.reheat : a); }
    fix(i, x, y) { this.fx[i] = x; this.fy[i] = y; }
    unfix(i) { this.fx[i] = NaN; this.fy[i] = NaN; }
    get running() { return this.alpha >= this.alphaMin || this.alphaTarget > 0; }

    tick() {
      const t0 = now();
      this.alpha += (this.alphaTarget - this.alpha) * this.alphaDecay;
      const alpha = this.alpha, p = this.params, n = this.n;
      const x = this.x, y = this.y, vx = this.vx, vy = this.vy;
      // 1. centre (forceX/forceY toward the origin)
      const kc = alpha * p.center;
      for (let i = 0; i < n; i++) { vx[i] -= x[i] * kc; vy[i] -= y[i] * kc; }
      // 2. links
      const ls = this.ls, lt = this.lt, lb = this.lbias, lst = this.lstr, kl = alpha * p.link, dist = p.distance;
      for (let i = 0, m = this.m; i < m; i++) {
        const s = ls[i], t = lt[i];
        let dx = x[t] + vx[t] - x[s] - vx[s], dy = y[t] + vy[t] - y[s] - vy[s];
        if (dx === 0) dx = 0.1; if (dy === 0) dy = 0.1;
        let l = Math.sqrt(dx * dx + dy * dy);
        l = (l - dist) / l * kl * lst[i];
        dx *= l; dy *= l;
        const b = lb[i];
        vx[t] -= dx * b; vy[t] -= dy * b;
        vx[s] += dx * (1 - b); vy[s] += dy * (1 - b);
      }
      // 3. many-body (Barnes-Hut)
      const tr = now();
      this.manyBody(alpha * p.repel);
      this.repelMs = now() - tr;
      // 4. collide
      this.collide();
      // 5. integrate
      const keep = 1 - this.velocityDecay, fx = this.fx, fy = this.fy;
      for (let i = 0; i < n; i++) {
        if (fx[i] === fx[i]) { x[i] = fx[i]; vx[i] = 0; } else { vx[i] *= keep; x[i] += vx[i]; }
        if (fy[i] === fy[i]) { y[i] = fy[i]; vy[i] = 0; } else { vy[i] *= keep; y[i] += vy[i]; }
      }
      this.ticks++;
      const dt = now() - t0;
      this.tickMs = this.tickMs ? this.tickMs * 0.9 + dt * 0.1 : dt;
      // Accuracy first: only loosen theta if repel alone blows the budget.
      if (this.repelMs > this.repelBudgetMs) this.adaptTheta = Math.min(1.3, this.adaptTheta + 0.05);
      else if (this.adaptTheta > this.theta && this.repelMs < this.repelBudgetMs * 0.6) this.adaptTheta = Math.max(this.theta, this.adaptTheta - 0.02);
      return dt;
    }

    manyBody(strength) {
      const n = this.n; if (n < 2) return;
      const x = this.x, y = this.y, vx = this.vx, vy = this.vy;
      const qt = this.qt; qt.xs = x; qt.ys = y; qt.build(x, y, n);
      const th2 = this.adaptTheta * this.adaptTheta, dmin2 = C.distanceMin2;
      const child = qt.child, cnt = qt.cnt, sx = qt.sx, sy = qt.sy, leaf = qt.leaf, size = qt.size, next = qt.next;
      let stack = this._stack || (this._stack = new Int32Array(4096));
      for (let i = 0; i < n; i++) {
        const xi = x[i], yi = y[i];
        let ax = 0, ay = 0, sp = 0;
        stack[sp++] = 0;
        while (sp) {
          const c = stack[--sp];
          const m = cnt[c]; if (!m) continue;
          let dx = sx[c] / m - xi, dy = sy[c] / m - yi;
          const w = size[c];
          let l = dx * dx + dy * dy;
          const ci = c * 4;
          const internal = child[ci] >= 0 || child[ci + 1] >= 0 || child[ci + 2] >= 0 || child[ci + 3] >= 0;
          if (w * w / th2 < l) {
            if (l < dmin2) l = Math.sqrt(dmin2 * l);
            const f = m * strength / l; ax += dx * f; ay += dy * f;
            continue;
          }
          if (internal) {
            if (sp + 4 > stack.length) { const s2 = new Int32Array(stack.length * 2); s2.set(stack); stack = this._stack = s2; }
            for (let q = 0; q < 4; q++) { const k = child[ci + q]; if (k >= 0) stack[sp++] = k; }
            continue;
          }
          for (let j = leaf[c]; j >= 0; j = next[j]) {
            if (j === i) continue;
            dx = x[j] - xi; dy = y[j] - yi;
            if (dx === 0) dx = i < j ? 0.5 : -0.5;
            if (dy === 0) dy = i < j ? 0.5 : -0.5;
            l = dx * dx + dy * dy;
            if (l < dmin2) l = Math.sqrt(dmin2 * l);
            const f = strength / l; ax += dx * f; ay += dy * f;
          }
        }
        vx[i] += ax; vy[i] += ay;
      }
    }

    /** d3 forceCollide with equal radii via a uniform spatial hash (cell = 2r). */
    collide() {
      const n = this.n; if (n < 2) return;
      const r = C.collideRadius, R = 2 * r, R2 = R * R, str = C.collideStrength;
      const x = this.x, y = this.y, vx = this.vx, vy = this.vy, px = this.cx, py = this.cy;
      let size = 1; while (size < n * 2) size <<= 1;
      if (!this._head || this._head.length !== size) this._head = new Int32Array(size);
      if (!this._hnext || this._hnext.length < n) this._hnext = new Int32Array(this.cap);
      const head = this._head, hnext = this._hnext, mask = size - 1;
      const gx = this._gx && this._gx.length >= n ? this._gx : (this._gx = new Int32Array(this.cap));
      const gy = this._gy && this._gy.length >= n ? this._gy : (this._gy = new Int32Array(this.cap));
      head.fill(-1);
      const hash = (a, b) => ((Math.imul(a, 73856093) ^ Math.imul(b, 19349663)) & mask);
      for (let i = 0; i < n; i++) {
        px[i] = x[i] + vx[i]; py[i] = y[i] + vy[i];
        const a = Math.floor(px[i] / R), b = Math.floor(py[i] / R);
        gx[i] = a; gy[i] = b;
        const h = hash(a, b); hnext[i] = head[h]; head[h] = i;
      }
      for (let i = 0; i < n; i++) {
        const xi = px[i], yi = py[i], a = gx[i], b = gy[i];
        for (let da = -1; da <= 1; da++) for (let db = -1; db <= 1; db++) {
          const ca = a + da, cb = b + db;
          for (let j = head[hash(ca, cb)]; j >= 0; j = hnext[j]) {
            if (j <= i || gx[j] !== ca || gy[j] !== cb) continue;
            let dx = xi - x[j] - vx[j], dy = yi - y[j] - vy[j];
            let l = dx * dx + dy * dy;
            if (l < R2) {
              if (dx === 0) { dx = i < j ? -0.01 : 0.01; l += dx * dx; }
              if (dy === 0) { dy = i < j ? -0.01 : 0.01; l += dy * dy; }
              l = Math.sqrt(l);
              l = (R - l) / l * str;
              dx *= l; dy *= l;
              vx[i] += dx * 0.5; vy[i] += dy * 0.5;
              vx[j] -= dx * 0.5; vy[j] -= dy * 0.5;
            }
          }
        }
      }
    }

    /** Exact O(n^2) many-body for tests. Returns {vx, vy} deltas. */
    bruteRepel(strength) {
      const n = this.n, ax = new Float64Array(n), ay = new Float64Array(n);
      for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) {
        if (i === j) continue;
        let dx = this.x[j] - this.x[i], dy = this.y[j] - this.y[i];
        let l = dx * dx + dy * dy; if (l === 0) continue;
        if (l < C.distanceMin2) l = Math.sqrt(C.distanceMin2 * l);
        ax[i] += dx * strength / l; ay[i] += dy * strength / l;
      }
      return { ax, ay };
    }
  }

  const now = (typeof performance !== 'undefined' && performance.now) ? () => performance.now() : () => Date.now();

  // ---------------------------------------------------------------- graph model
  /** Normalise contract data into the internal model: distinct directed pairs, degree, neighbours. */
  function buildModel(g) {
    const nodes = (g && Array.isArray(g.nodes)) ? g.nodes : [];
    const rawLinks = (g && Array.isArray(g.links)) ? g.links : [];
    const n = nodes.length;
    const seen = new Set(), pairs = [];
    for (let i = 0; i < rawLinks.length; i++) {
      const l = rawLinks[i]; if (!l) continue;
      const s = l.s | 0, t = l.t | 0;
      if (s < 0 || t < 0 || s >= n || t >= n) continue;
      const key = s * n + t;
      if (seen.has(key)) continue;
      seen.add(key);
      pairs.push([s, t, l.kind || 'link', l.resolved !== false]);
    }
    const fwd = Array.from({ length: n }, () => []), rev = Array.from({ length: n }, () => []);
    for (const [s, t] of pairs) { fwd[s].push(t); rev[t].push(s); }
    const weight = new Float64Array(n);
    for (let i = 0; i < n; i++) weight[i] = fwd[i].length + rev[i].length;
    // Draw list: one line per unordered pair (mutual pairs collapse), arrows for every direction.
    const lineSeen = new Set(), lines = [];
    for (const [s, t] of pairs) {
      const a = Math.min(s, t), b = Math.max(s, t);
      if (s === t) continue;
      const k = a * n + b;
      if (lineSeen.has(k)) continue;
      lineSeen.add(k); lines.push([s, t]);
    }
    return { nodes, pairs, fwd, rev, weight, lines, n };
  }

  /** BFS layers from focus for the local graph weights. */
  function localLayers(model, focus, depth, incoming, outgoing) {
    const layer = new Int32Array(model.n).fill(-1);
    if (focus < 0 || focus >= model.n) return layer;
    layer[focus] = 0;
    let frontier = [focus];
    for (let p = 1; p <= depth && frontier.length; p++) {
      const nextF = [];
      for (const u of frontier) {
        if (outgoing !== false) for (const v of model.fwd[u]) if (layer[v] < 0) { layer[v] = p; nextF.push(v); }
        if (incoming !== false) for (const v of model.rev[u]) if (layer[v] < 0) { layer[v] = p; nextF.push(v); }
      }
      frontier = nextF;
    }
    return layer;
  }

  /** Initial placement of new ids. existing: Map id -> [x, y]. */
  function placeNodes(model, existing, random) {
    const n = model.n, out = new Array(n);
    let existingR = 0, newCount = 0;
    for (let i = 0; i < n; i++) {
      const p = existing.get(model.nodes[i].id);
      if (p) { out[i] = p; existingR = Math.max(existingR, Math.hypot(p[0], p[1])); } else newCount++;
    }
    if (!newCount) return out;
    const area = 72 * 72 * newCount, jitter = 48;
    const outer = Math.sqrt(area / Math.PI + existingR * existingR);
    const disc = () => {
      const ang = 2 * Math.PI * random();
      const rad = existingR + Math.sqrt(random()) * (outer - existingR);
      return [rad * Math.cos(ang), rad * Math.sin(ang)];
    };
    if (existing.size === 0 || newCount === n) {
      for (let i = 0; i < n; i++) if (!out[i]) out[i] = disc();
      return out;
    }
    // BFS from placed nodes so chains of new nodes still appear next to a neighbour.
    let pending = [];
    for (let i = 0; i < n; i++) if (!out[i]) pending.push(i);
    let progress = true;
    while (pending.length && progress) {
      progress = false;
      const rest = [];
      for (const i of pending) {
        let sx = 0, sy = 0, k = 0;
        for (const j of model.fwd[i]) if (out[j]) { sx += out[j][0]; sy += out[j][1]; k++; }
        for (const j of model.rev[i]) if (out[j]) { sx += out[j][0]; sy += out[j][1]; k++; }
        if (k) {
          out[i] = [sx / k + (random() - 0.5) * jitter, sy / k + (random() - 0.5) * jitter];
          progress = true;
        } else rest.push(i);
      }
      pending = rest;
    }
    for (const i of pending) out[i] = disc();
    return out;
  }

  /** Topmost node under a world point. order: draw order (last = top). */
  function hitTest(px, py, xs, ys, radii, order, slack) {
    for (let k = order.length - 1; k >= 0; k--) {
      const i = order[k];
      const dx = xs[i] - px, dy = ys[i] - py, r = radii[i] + (slack || 0);
      if (dx * dx + dy * dy <= r * r) return i;
    }
    return -1;
  }

  // ---------------------------------------------------------------- timelapse
  /** Items in timelapse order: returns {order, total, subset(progression)}. */
  function timelapse(g, showOrphans) {
    const nodes = g.nodes || [], links = g.links || [];
    const idx = nodes.map((_, i) => i);
    const hasTime = nodes.some((nd) => typeof nd.ctime === 'number');
    if (hasTime) idx.sort((a, b) => (num(nodes[a].ctime, Infinity) - num(nodes[b].ctime, Infinity)) || a - b);
    const out = idx.map(() => []);
    const rank = new Int32Array(nodes.length); idx.forEach((v, r) => { rank[v] = r; });
    for (const l of links) if (l && l.s >= 0 && l.s < nodes.length && l.t >= 0 && l.t < nodes.length) out[rank[l.s]].push(l);
    let total = 0;
    for (let r = 0; r < idx.length; r++) total += (showOrphans !== false ? 1 : 0) + out[r].length;
    function subset(progression) {
      let f = 0, cut = idx.length;
      const keepLinks = [];
      outer: for (let r = 0; r < idx.length; r++) {
        if (showOrphans !== false) { if (f === progression) { cut = r; break; } f++; }
        for (const l of out[r]) {
          if (f < progression) keepLinks.push(l);
          else { cut = r + 1; break outer; }
          f++;
        }
      }
      const keep = new Set(idx.slice(0, cut));
      // link targets that are files after the cut disappear with them
      const map = new Map(); const nn = [];
      for (const i of idx) if (keep.has(i)) { map.set(i, nn.length); nn.push(nodes[i]); }
      const ll = [];
      for (const l of keepLinks) if (map.has(l.s) && map.has(l.t)) ll.push(Object.assign({}, l, { s: map.get(l.s), t: map.get(l.t) }));
      return { nodes: nn, links: ll, truncated: false, stats: g.stats || {} };
    }
    return { total, subset };
  }

  // ---------------------------------------------------------------- demo data
  function demo(n, seed) {
    n = Math.max(1, Math.min(20000, n | 0 || 300));
    const R = rng(seed == null ? 1 : seed);
    const nodes = [], links = [];
    const words = ['alpha', 'beta', 'gamma', 'delta', 'orbit', 'nova', 'atlas', 'comet', 'lumen', 'vector', 'quartz',
      'ember', 'harbor', 'signal', 'pulse', 'cedar', 'raven', 'tidal', 'zenith', 'ion', 'prism', 'nimbus', 'echo', 'drift'];
    const folders = ['notes', 'projects', 'daily', 'people', 'src', 'docs'];
    const add = (kind, label, folder) => {
      const i = nodes.length;
      const ext = kind === 'code' ? '.py' : kind === 'attachment' ? '.png' : kind === 'note' ? '.md' : '';
      const path = kind === 'tag' ? '' : kind === 'unresolved' ? '' : folder + '/' + label + ext;
      nodes.push({ id: kind === 'tag' ? '#' + label : kind === 'unresolved' ? label : path, label: kind === 'tag' ? label : label,
        kind, path, folder: kind === 'tag' || kind === 'unresolved' ? '' : folder, tags: [], in_degree: 0, out_degree: 0,
        size_hint: 1, group: null, ctime: 1600000000 + i * 3600 });
      return i;
    };
    const link = (s, t, kind) => { if (s !== t) links.push({ s, t, kind: kind || 'link', resolved: nodes[t].kind !== 'unresolved', count: 1, self: false }); };
    const nTags = Math.max(1, Math.round(n * 0.03)), nUnres = Math.max(1, Math.round(n * 0.03));
    const nAtt = Math.max(1, Math.round(n * 0.04)), nCode = Math.max(1, Math.round(n * 0.08));
    const nOrph = Math.max(1, Math.round(n * 0.04)), nChain = Math.max(2, Math.round(n * 0.05));
    const nNotes = Math.max(1, n - nTags - nUnres - nAtt - nCode - nOrph - nChain);
    const clusters = Math.max(1, Math.round(Math.sqrt(nNotes / 12)));
    const name = (i) => words[i % words.length] + '-' + (i + 1);
    const notes = [];
    for (let i = 0; i < nNotes; i++) notes.push(add('note', name(i), folders[(i % clusters) % 4]));
    const hubs = [];
    for (let c = 0; c < clusters; c++) hubs.push(notes[c]);
    for (let k = clusters; k < notes.length; k++) {
      const c = k % clusters, i = notes[k];
      link(i, hubs[c]);
      const deg = 1 + Math.floor(R() * 3);
      for (let d = 0; d < deg; d++) {
        const same = R() < 0.85;
        const peers = same ? notes.filter((_, q) => q % clusters === c) : notes;
        link(i, peers[Math.floor(R() * peers.length)]);
      }
    }
    for (let c = 1; c < clusters; c++) link(hubs[c], hubs[Math.floor(R() * c)]);
    for (let i = 0; i < nCode; i++) { const k = add('code', 'mod_' + words[i % words.length] + i, 'src'); link(k, notes[Math.floor(R() * notes.length)], 'import'); if (i) link(k, k - 1, 'import'); }
    for (let i = 0; i < nAtt; i++) { const k = add('attachment', 'image-' + (i + 1), 'attachments'); link(notes[Math.floor(R() * notes.length)], k, 'embed'); }
    for (let i = 0; i < nTags; i++) { const k = add('tag', words[(i * 7) % words.length], ''); for (let d = 0; d < 6; d++) link(notes[Math.floor(R() * notes.length)], k, 'tag'); }
    for (let i = 0; i < nUnres; i++) { const k = add('unresolved', 'missing ' + (i + 1), ''); link(notes[Math.floor(R() * notes.length)], k); }
    let prev = -1;
    for (let i = 0; i < nChain; i++) { const k = add('note', 'chain-' + (i + 1), 'docs'); if (prev >= 0) link(prev, k); else link(notes[0], k); prev = k; }
    for (let i = 0; i < nOrph; i++) add('note', 'orphan-' + (i + 1), 'notes');
    // dedupe
    const seen = new Set();
    const uniq = links.filter((l) => { const k = l.s + ':' + l.t; if (seen.has(k)) return false; seen.add(k); return true; });
    for (const l of uniq) { nodes[l.s].out_degree++; nodes[l.t].in_degree++; }
    for (const nd of nodes) nd.size_hint = 1 + nd.in_degree + nd.out_degree;
    return { nodes, links: uniq, truncated: false, stats: { counts: {}, orphans: nOrph, unresolved: [], ambiguous: [], components: 0 } };
  }

  const api = {
    C, DEFAULTS, LIMITS, STEPS, DISPLAY_KEYS, FORCE_KEYS, FILTER_KEYS, DARK_THEME,
    clamp, nodeSize, screenRadius, textAlpha, lineWorldWidth, arrowFactor, arrowSize, labelFont,
    boundedSetting, forceParams, easeFactor, ease, wheelDelta, wheelFactor, clampScale, zoomAround,
    timelapseSpeed, localWeight, parseColor, toHex, toCss, mix, hslToRgb, newGroupColor, palette,
    rng, QuadTree, Simulation, buildModel, localLayers, placeNodes, hitTest, timelapse, demo, now,
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.GraphCore = api;
})(typeof self !== 'undefined' ? self : this);
