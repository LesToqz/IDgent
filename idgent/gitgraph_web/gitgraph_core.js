/* IDgent Git graph: pure logic (no DOM). Lane layout, edge routing, label collision pass,
 * colours, time formatting, keyboard navigation, easing and a synthetic history generator.
 * Loaded as a classic <script> (window.GitGraphCore) and as a CommonJS module by the node tests. */
(function (root) {
  'use strict';

  // ---------------------------------------------------------------- constants
  const C = {
    colW: 56,            // world px between columns
    laneGap: 76,         // world px between lanes (compressed to fit the pane, never below minLaneGap)
    minLaneGap: 34,
    tail: 120,           // dashed tail from HEAD to the "working tree" dot
    minScale: 0.25,
    maxScale: 3,
    dotR: 6, mergeR: 7.5, headR: 9,
    nameMax: 28, calloutMax: 34, tagMax: 22,
    runMin: 8,           // linear runs at least this long collapse at very low zoom
    revealMs: 650, popMs: 180, moveMs: 320, headMs: 400, fadeMs: 250, labelFadeMs: 260,
    staggerMax: 400,     // above this many commits the first reveal is a plain fade
    hoverDelay: 100,
  };

  const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);

  // ---------------------------------------------------------------- hashing / rng
  function hashStr(s) {                         // FNV-1a 32-bit, unsigned
    let h = 0x811c9dc5;
    s = String(s);
    for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193); }
    return h >>> 0;
  }
  function rng(seed) {                          // mulberry32
    let a = (seed >>> 0) || 1;
    return function () {
      a = (a + 0x6d2b79f5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  // ---------------------------------------------------------------- easing
  const ease = {
    linear: (t) => t,
    outCubic: (t) => 1 - Math.pow(1 - t, 3),
    inOutCubic: (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2),
    outBack: (t) => { const c1 = 1.5, c3 = c1 + 1; return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2); },
  };
  /** Frame-rate independent exponential approach: fraction of the remaining distance covered in dt ms. */
  function approach(dtMs, tauMs) { return 1 - Math.exp(-Math.max(0, dtMs) / Math.max(1e-6, tauMs)); }

  // ---------------------------------------------------------------- text helpers
  function ellipsize(s, max) {
    s = String(s == null ? '' : s);
    if (s.length <= max) return s;
    return s.slice(0, Math.max(1, max - 1)).replace(/\s+$/, '') + '…';
  }
  /** Callout text for a merge subject: "Merge pull request #42 from a/b" -> "Merge pull request #42". */
  function shortMerge(subject) {
    const s = String(subject || '').trim();
    let m = /^(Merge pull request #\d+)\b/.exec(s);
    if (m) return m[1];
    m = /^(Merge [^:\n]{1,80}?):\s/.exec(s);
    if (m) return m[1];
    m = /^(Merge (?:remote-tracking )?branch '[^']+')/.exec(s);
    if (m) return m[1];
    return s;
  }

  // ---------------------------------------------------------------- time
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  /** "Oct 19, 2026, 8:58 PM" in the viewer's local time. */
  function absTime(ts) {
    const d = new Date(ts * 1000);
    if (!isFinite(d.getTime())) return '';
    let h = d.getHours();
    const ap = h >= 12 ? 'PM' : 'AM';
    h = h % 12; if (h === 0) h = 12;
    const mm = String(d.getMinutes()).padStart(2, '0');
    return MONTHS[d.getMonth()] + ' ' + d.getDate() + ', ' + d.getFullYear() + ', ' + h + ':' + mm + ' ' + ap;
  }
  function relTime(ts, nowSec) {
    const s = Math.round(nowSec - ts);
    if (s < -60) return 'in the future';
    if (s < 45) return 'just now';
    const plural = (n, u) => n + ' ' + u + (n === 1 ? '' : 's') + ' ago';
    const m = Math.round(s / 60);
    if (m < 60) return m <= 1 ? 'a minute ago' : plural(m, 'minute');
    const h = Math.round(s / 3600);
    if (h < 24) return h === 1 ? 'an hour ago' : plural(h, 'hour');
    const d = Math.round(s / 86400);
    if (d === 1) return 'yesterday';
    if (d < 30) return plural(d, 'day');
    const mo = Math.round(d / 30.44);
    if (mo < 12) return mo <= 1 ? 'a month ago' : plural(mo, 'month');
    const y = Math.round(d / 365.25);
    return y <= 1 ? 'a year ago' : plural(y, 'year');
  }

  // ---------------------------------------------------------------- colours
  function parseColor(c) {
    if (Array.isArray(c)) return c.slice(0, 3).concat([c.length > 3 ? c[3] : 1]);
    const s = String(c || '').trim();
    let m = /^#([0-9a-f]{3,8})$/i.exec(s);
    if (m) {
      let h = m[1];
      if (h.length === 3 || h.length === 4) h = h.split('').map((x) => x + x).join('');
      const n = parseInt(h.slice(0, 6), 16);
      const a = h.length === 8 ? parseInt(h.slice(6, 8), 16) / 255 : 1;
      return [(n >> 16) & 255, (n >> 8) & 255, n & 255, a];
    }
    m = /^rgba?\(([^)]+)\)$/i.exec(s);
    if (m) {
      const p = m[1].split(/[ ,/]+/).filter(Boolean).map(parseFloat);
      return [p[0] || 0, p[1] || 0, p[2] || 0, p.length > 3 ? p[3] : 1];
    }
    return null;
  }
  function toHex(c) {
    const h = (v) => clamp(Math.round(v), 0, 255).toString(16).padStart(2, '0');
    return '#' + h(c[0]) + h(c[1]) + h(c[2]);
  }
  function rgba(c, a) { return 'rgba(' + Math.round(c[0]) + ',' + Math.round(c[1]) + ',' + Math.round(c[2]) + ',' + (+a).toFixed(3) + ')'; }
  function mix(a, b, t) { return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t, 1]; }
  function luminance(c) {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
  }
  function contrast(a, b) {
    const la = luminance(a), lb = luminance(b);
    return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
  }
  /** Move `fg` toward black or white (whichever gains contrast against `bg`) until contrast >= ratio. */
  function legible(fg, bg, ratio) {
    if (contrast(fg, bg) >= ratio) return fg.slice(0, 3).concat([1]);
    const target = luminance(bg) > 0.35 ? [0, 0, 0, 1] : [255, 255, 255, 1];
    let lo = 0, hi = 1;
    for (let i = 0; i < 18; i++) {
      const mid = (lo + hi) / 2;
      if (contrast(mix(fg, target, mid), bg) >= ratio) hi = mid; else lo = mid;
    }
    return mix(fg, target, hi);
  }
  function nearestColorIdx(accent, colors) {
    let best = -1, bd = Infinity;
    for (let i = 0; i < colors.length; i++) {
      const c = colors[i];
      if (!c) continue;
      const d = (c[0] - accent[0]) ** 2 + (c[1] - accent[1]) ** 2 + (c[2] - accent[2]) ** 2;
      if (d < bd) { bd = d; best = i; }
    }
    return bd < 95 * 95 ? best : -1;               // only skip a palette colour that really looks like the accent
  }

  // ---------------------------------------------------------------- layout
  function refName(r) { return r && typeof r.name === 'string' ? r.name : ''; }
  function stripRemote(name) { const i = name.indexOf('/'); return i > 0 ? name.slice(i + 1) : name; }

  /**
   * Lane layout of a graph-data dict (contract A). Returns
   * { nodes[], byHash: Map, lanes[], edges[], stubs[], runs[], head, mainLane, maxCol, minSlot, maxSlot, bySlot }.
   * nodes[i] mirrors commits[i] (newest first) plus col/lane/slot; lanes[0] is the default branch (slot 0).
   */
  function layout(data) {
    const commits = (data && Array.isArray(data.commits)) ? data.commits : [];
    const nodes = [];
    const byHash = new Map();
    for (const c of commits) {
      if (!c || typeof c.hash !== 'string' || byHash.has(c.hash)) continue;
      const i = nodes.length;
      byHash.set(c.hash, i);
      nodes.push({
        i, hash: c.hash, short: c.short || c.hash.slice(0, 7), subject: String(c.subject || ''),
        author: String(c.author || ''), email: String(c.email || ''), ts: +c.ts || 0, tz: +c.tz || 0, cts: +c.cts || +c.ts || 0,
        refs: Array.isArray(c.refs) ? c.refs.filter((r) => r && typeof r.name === 'string') : [],
        mergedName: c.merged_name || null, rawParents: Array.isArray(c.parents) ? c.parents : [],
        parents: [], absent: [], children: [], col: 0, lane: -1, slot: 0, merge: false, tip: false,
      });
    }
    const n = nodes.length;
    for (const nd of nodes) {
      for (const ph of nd.rawParents) {
        const j = byHash.get(ph);
        if (j !== undefined && j > nd.i) { nd.parents.push(j); nodes[j].children.push(nd.i); }
        else nd.absent.push(ph);                 // truncated history (or a malformed order we refuse to follow)
      }
      nd.merge = nd.rawParents.length >= 2;
      delete nd.rawParents;
    }
    // columns: longest path from the roots, so concurrent branches overlap in x
    for (let i = n - 1; i >= 0; i--) {
      const nd = nodes[i];
      let c = 0;
      for (const p of nd.parents) c = Math.max(c, nodes[p].col + 1);
      if (nd.absent.length && !nd.parents.length) c = Math.max(c, 1);   // room for the "older history" stub
      nd.col = c;
    }
    const head = data && data.head ? data.head : {};
    let headIdx = head.hash && byHash.has(head.hash) ? byHash.get(head.hash) : -1;
    let maxCol = 0;
    for (const nd of nodes) maxCol = Math.max(maxCol, nd.col);
    if (headIdx >= 0 && !nodes[headIdx].children.length && nodes[headIdx].col < maxCol) nodes[headIdx].col = maxCol;

    // ---- chains (lanes)
    const lanes = [];
    const findRef = (name, kind) => {
      if (!name) return -1;
      for (const nd of nodes) for (const r of nd.refs) if (r.name === name && (!kind || r.kind === kind)) return nd.i;
      return -1;
    };
    const def = data && data.repo ? data.repo.default_branch : null;
    let mainTip = -1;
    if (def) {
      mainTip = findRef(def, 'branch');
      if (mainTip < 0) mainTip = findRef(def, 'remote');
      if (mainTip < 0) mainTip = findRef('origin/' + def, 'remote');
      if (mainTip < 0 && def.indexOf('/') > 0) mainTip = findRef(stripRemote(def), 'branch');
    }
    if (mainTip < 0) mainTip = headIdx;
    if (mainTip < 0 && n) mainTip = 0;

    function claim(start) {
      const lane = { idx: lanes.length, nodes: [], slot: 0, main: lanes.length === 0 && start === mainTip };
      let k = start;
      while (k >= 0 && nodes[k].lane < 0) {
        nodes[k].lane = lane.idx;
        lane.nodes.push(k);
        k = nodes[k].parents.length ? nodes[k].parents[0] : -1;
      }
      lane.nodes.reverse();                      // oldest -> newest
      lanes.push(lane);
      return lane;
    }
    if (mainTip >= 0) claim(mainTip);
    if (headIdx >= 0 && nodes[headIdx].lane < 0) claim(headIdx);
    for (let i = 0; i < n; i++) if (nodes[i].lane < 0) claim(i);

    for (const lane of lanes) {
      const base = nodes[lane.nodes[0]], tip = nodes[lane.nodes[lane.nodes.length - 1]];
      tip.tip = true;
      lane.base = base.i; lane.tipIdx = tip.i;
      lane.forkParent = base.parents.length ? base.parents[0] : -1;
      lane.start = lane.forkParent >= 0 ? nodes[lane.forkParent].col : (base.absent.length ? base.col - 1 : base.col);
      lane.end = tip.col;
      lane.id = lane.main ? 'main' : 'b:' + base.hash;
    }

    // ---- edges + routing: the horizontal run of a cross-lane edge lies on its "track" lane
    const edges = [];
    for (const nd of nodes) {
      nd.parents.forEach((p, k) => {
        const pn = nodes[p];
        let kind, track;
        if (pn.lane === nd.lane) { kind = 'lane'; track = nd.lane; }
        else if (k === 0) { kind = 'fork'; track = nd.lane; }
        else {
          kind = 'merge';
          track = lanes[pn.lane].main ? nd.lane : pn.lane;
        }
        const route = kind === 'lane' ? 'straight' : (track === nd.lane ? 'curve-first' : 'curve-last');
        edges.push({ p, c: nd.i, kind, track, route, k });
        if (kind !== 'lane') {
          const L = lanes[track];
          L.start = Math.min(L.start, pn.col);
          L.end = Math.max(L.end, nd.col);
        }
      });
    }
    // ---- slots: interval packing, nearest free lane first, alternating above/below
    const occupied = new Map();                  // slot -> [[s, e], ...]
    const free = (slot, s, e) => {
      const list = occupied.get(slot);
      if (!list) return true;
      for (const iv of list) if (s <= iv[1] && iv[0] <= e) return false;
      return true;
    };
    const order = lanes.filter((l) => !l.main).sort((a, b) => (a.start - b.start) || (a.idx - b.idx));
    let minSlot = 0, maxSlot = 0;
    for (const lane of order) {
      if (lane.main) continue;
      const parentSlot = lane.forkParent >= 0 && lanes[nodes[lane.forkParent].lane].slotted ? lanes[nodes[lane.forkParent].lane].slot : 0;
      let chosen = 0;
      for (let d = 1; d < 4096 && !chosen; d++) {
        // candidates at distance d from the parent's slot, preferring the side away from main
        const cands = [];
        const away = parentSlot === 0 ? -1 : Math.sign(parentSlot);
        cands.push(parentSlot + away * d, parentSlot - away * d);
        for (const s of cands) if (s !== 0 && free(s, lane.start, lane.end)) { chosen = s; break; }
      }
      lane.slot = chosen;
      lane.slotted = true;
      if (!occupied.has(chosen)) occupied.set(chosen, []);
      occupied.get(chosen).push([lane.start, lane.end]);
      minSlot = Math.min(minSlot, chosen); maxSlot = Math.max(maxSlot, chosen);
    }
    for (const lane of lanes) delete lane.slotted;
    for (const nd of nodes) nd.slot = lanes[nd.lane].slot;
    maxCol = 0;
    for (const nd of nodes) maxCol = Math.max(maxCol, nd.col);

    // ---- names (pill text)
    const curBranch = head.detached ? null : head.branch || null;
    for (const lane of lanes) {
      const tip = nodes[lane.tipIdx];
      const locals = tip.refs.filter((r) => r.kind === 'branch');
      locals.sort((a, b) => (b.current ? 1 : 0) - (a.current ? 1 : 0));
      const remotes = tip.refs.filter((r) => r.kind === 'remote');
      let name = null, kind = null;
      if (lane.main && def) {
        const hit = locals.find((r) => r.name === def) || remotes.find((r) => r.name === def || r.name === 'origin/' + def);
        name = hit ? hit.name : def; kind = hit ? hit.kind : 'branch';
      }
      if (!name && locals.length) { name = locals[0].name; kind = 'branch'; }
      if (!name && remotes.length) { name = remotes[0].name; kind = 'remote'; }
      if (!name) {
        // merged-and-deleted: the merge that joined the lane names it
        for (let k = lane.nodes.length - 1; k >= 0 && !name; k--) {
          const nd = nodes[lane.nodes[k]];
          for (const ci of nd.children) {
            const ch = nodes[ci];
            if (ch.lane !== lane.idx && ch.parents[1] === nd.i && ch.mergedName) { name = ch.mergedName; kind = 'merged'; break; }
          }
        }
      }
      if (!name && lane.main) { name = curBranch || null; kind = name ? 'branch' : null; }
      lane.name = name;
      lane.nameKind = kind;
      lane.others = tip.refs.filter((r) => (r.kind === 'branch' || r.kind === 'remote') && r.name !== name).map((r) => ({ name: r.name, kind: r.kind }));
      lane.current = !!curBranch && tip.refs.some((r) => r.kind === 'branch' && r.name === curBranch);
      lane.colorKey = name ? (kind === 'remote' ? stripRemote(name) : name) : base40(nodes[lane.base].hash);
    }
    function base40(h) { return 'c:' + h; }

    // ---- stubs for absent parents
    const stubs = [];
    for (const nd of nodes) nd.absent.forEach((h, k) => stubs.push({ node: nd.i, first: k === 0 && !nd.parents.length, hash: h }));

    // ---- linear runs (level of detail)
    const runs = [];
    const plain = (nd) => !nd.merge && !nd.refs.length && nd.i !== headIdx && nd.children.length <= 1 &&
      nd.children.every((c) => nodes[c].lane === nd.lane) && nd.parents.length === 1 && nodes[nd.parents[0]].lane === nd.lane;
    for (const lane of lanes) {
      let run = [];
      const flush = () => { if (run.length >= C.runMin) runs.push({ lane: lane.idx, from: run[0], to: run[run.length - 1], count: run.length, nodes: run }); run = []; };
      for (const k of lane.nodes) { if (plain(nodes[k])) run.push(k); else flush(); }
      flush();
    }
    for (const r of runs) for (const k of r.nodes) nodes[k].run = r;

    // ---- per-slot index (navigation)
    const bySlot = new Map();
    for (const nd of nodes) { if (!bySlot.has(nd.slot)) bySlot.set(nd.slot, []); bySlot.get(nd.slot).push(nd.i); }
    for (const list of bySlot.values()) list.sort((a, b) => nodes[a].col - nodes[b].col);

    return {
      nodes, byHash, lanes, edges, stubs, runs, bySlot, maxCol, minSlot, maxSlot,
      head: headIdx, detached: !!head.detached, headBranch: curBranch, mainLane: lanes.length && lanes[0].main ? 0 : -1,
      dirty: data && data.dirty && +data.dirty.changed > 0 ? +data.dirty.changed : 0,
      truncated: !!(data && data.truncated), total: data && +data.total || n,
    };
  }

  /** Colour index per lane: from the branch name / base hash, skipping `skip`, avoiding a neighbour's colour. */
  function assignColors(L, count, skip) {
    count = count || 8;
    const done = [];
    const order = L.lanes.filter((l) => !l.main).sort((a, b) => (a.start - b.start) || (a.idx - b.idx));
    for (const lane of L.lanes) if (lane.main) lane.color = -1;
    for (const lane of order) {
      const pref = hashStr(lane.colorKey) % count;
      let pick = -1, fallback = -1;
      for (let k = 0; k < count; k++) {
        const ci = (pref + k) % count;
        if (ci === skip) continue;
        if (fallback < 0) fallback = ci;
        const clash = done.some((o) => o.color === ci && Math.abs(o.slot - lane.slot) <= 2 && lane.start <= o.end + 6 && o.start <= lane.end + 6);
        if (!clash) { pick = ci; break; }
      }
      lane.color = pick >= 0 ? pick : fallback;
      done.push(lane);
    }
    return L;
  }

  // ---------------------------------------------------------------- edge geometry
  /** Horizontal run + cubic S-curve with horizontal tangents. Returns [{t:'L',x0,y0,x1,y1}|{t:'C',x0,y0,c1x,c1y,c2x,c2y,x1,y1}]. */
  function edgeSegments(px, py, cx, cy, route, colW) {
    if (route === 'straight' || Math.abs(py - cy) < 0.5) return [{ t: 'L', x0: px, y0: py, x1: cx, y1: cy }];
    const dx = cx - px, dy = Math.abs(cy - py);
    const w = Math.max(1, Math.min(dx, clamp(dy * 0.9, colW * 0.95, colW * 2.2)));
    const seg = [];
    if (route === 'curve-first') {
      const x1 = px + w;
      seg.push({ t: 'C', x0: px, y0: py, c1x: px + w * 0.55, c1y: py, c2x: x1 - w * 0.55, c2y: cy, x1, y1: cy });
      if (cx - x1 > 0.5) seg.push({ t: 'L', x0: x1, y0: cy, x1: cx, y1: cy });
    } else {
      const x0 = cx - w;
      if (x0 - px > 0.5) seg.push({ t: 'L', x0: px, y0: py, x1: x0, y1: py });
      seg.push({ t: 'C', x0, y0: py, c1x: x0 + w * 0.55, c1y: py, c2x: cx - w * 0.55, c2y: cy, x1: cx, y1: cy });
    }
    return seg;
  }
  function segLength(s) {
    if (s.t === 'L') return Math.hypot(s.x1 - s.x0, s.y1 - s.y0);
    const chord = Math.hypot(s.x1 - s.x0, s.y1 - s.y0);
    const poly = Math.hypot(s.c1x - s.x0, s.c1y - s.y0) + Math.hypot(s.c2x - s.c1x, s.c2y - s.c1y) + Math.hypot(s.x1 - s.c2x, s.y1 - s.c2y);
    return (chord + poly) / 2;
  }
  /** The first `u` (0..1) of a cubic (de Casteljau split). */
  function splitCubic(s, u) {
    const lerp = (a, b) => a + (b - a) * u;
    const ax = lerp(s.x0, s.c1x), ay = lerp(s.y0, s.c1y), bx = lerp(s.c1x, s.c2x), by = lerp(s.c1y, s.c2y);
    const cx = lerp(s.c2x, s.x1), cy = lerp(s.c2y, s.y1);
    const dx = lerp(ax, bx), dy = lerp(ay, by), ex = lerp(bx, cx), ey = lerp(by, cy);
    const fx = lerp(dx, ex), fy = lerp(dy, ey);
    return { t: 'C', x0: s.x0, y0: s.y0, c1x: ax, c1y: ay, c2x: dx, c2y: dy, x1: fx, y1: fy };
  }
  /** Segments truncated to fraction `u` of the total length (reveal / slide-in animation). */
  function partialSegments(segs, u) {
    if (u >= 1) return segs;
    if (u <= 0) return [];
    const lens = segs.map(segLength), total = lens.reduce((a, b) => a + b, 0);
    let left = total * u;
    const out = [];
    for (let k = 0; k < segs.length; k++) {
      if (left <= 0) break;
      const s = segs[k];
      if (left >= lens[k]) { out.push(s); left -= lens[k]; continue; }
      const f = left / lens[k];
      if (s.t === 'L') out.push({ t: 'L', x0: s.x0, y0: s.y0, x1: s.x0 + (s.x1 - s.x0) * f, y1: s.y0 + (s.y1 - s.y0) * f });
      else out.push(splitCubic(s, f));
      left = 0;
    }
    return out;
  }

  // ---------------------------------------------------------------- labels + collision pass
  function rectsOverlap(a, b, pad) {
    return a.x < b.x + b.w + pad && b.x < a.x + a.w + pad && a.y < b.y + b.h + pad && b.y < a.y + a.h + pad;
  }
  function Grid(cell) { this.cell = cell; this.m = new Map(); }
  Grid.prototype.keys = function (r, pad) {
    const c = this.cell, out = [];
    const x0 = Math.floor((r.x - pad) / c), x1 = Math.floor((r.x + r.w + pad) / c);
    const y0 = Math.floor((r.y - pad) / c), y1 = Math.floor((r.y + r.h + pad) / c);
    for (let x = x0; x <= x1; x++) for (let y = y0; y <= y1; y++) out.push(x + ',' + y);
    return out;
  };
  Grid.prototype.add = function (r) { for (const k of this.keys(r, 0)) { if (!this.m.has(k)) this.m.set(k, []); this.m.get(k).push(r); } };
  Grid.prototype.area = function (r) {
    let a = 0;
    const seen = new Set();
    for (const k of this.keys(r, 0)) {
      const list = this.m.get(k);
      if (!list) continue;
      for (const o of list) {
        if (seen.has(o)) continue;
        seen.add(o);
        const w = Math.min(r.x + r.w, o.x + o.w) - Math.max(r.x, o.x), h = Math.min(r.y + r.h, o.y + o.h) - Math.max(r.y, o.y);
        if (w > 0 && h > 0) a += w * h;
      }
    }
    return a;
  };
  Grid.prototype.hits = function (r, pad) {
    let n = 0;
    const seen = new Set();
    for (const k of this.keys(r, pad)) {
      const list = this.m.get(k);
      if (!list) continue;
      for (const o of list) if (!seen.has(o) && rectsOverlap(r, o, pad)) { seen.add(o); n++; }
    }
    return n;
  };

  const PRIORITY = { head: 100, pill: 80, callout: 60, tag: 50, ref: 40, stub: 30 };

  /**
   * Deterministic label placement in world coordinates for lane gap `o.gap`.
   * o = {gap, colW, measure(text, sizePx, weight) -> width, showRemote, showTags}
   * Returns labels [{kind, node, lane, text, full, x, y, w, h, ax, ay, side, extra, dashed, current, priority, hidden, px}].
   */
  function placeLabels(L, o) {
    const gap = o.gap || C.laneGap, colW = o.colW || C.colW;
    const measure = o.measure || ((t, size) => t.length * size * 0.58);
    const k = clamp(gap / C.laneGap, 0.8, 1);
    const showRemote = o.showRemote !== false, showTags = o.showTags !== false;
    const X = (i) => L.nodes[i].col * colW, Y = (i) => L.nodes[i].slot * gap;
    const want = [];
    const S = { pill: 13 * k, callout: 12 * k, tag: 11 * k, head: 11.5 * k };

    // HEAD badge
    if (L.head >= 0) {
      const w = measure('HEAD', S.head, 700) + 18, h = 20 * k;
      const ax = X(L.head), ay = Y(L.head);
      const up = L.nodes[L.head].slot <= 0 ? -1 : 1;
      const off = C.headR + 15 * k;
      const cand = (side) => ({ x: ax - w / 2, y: side < 0 ? ay - off - h : ay + off, side });
      want.push({ kind: 'head', node: L.head, lane: L.nodes[L.head].lane, text: 'HEAD', full: L.detached ? 'HEAD (detached)' : 'HEAD -> ' + (L.headBranch || ''), w, h, ax, ay, cands: [cand(up), cand(-up)], priority: PRIORITY.head });
    }
    // branch pills
    for (const lane of L.lanes) {
      if (!lane.name) continue;
      if (lane.nameKind === 'remote' && !showRemote) continue;
      const others = lane.others.filter((r) => showRemote || r.kind !== 'remote');
      const text = ellipsize(lane.name, C.nameMax);
      const extra = others.length;
      let w = 10 + 13 * k + 6 + measure(text, S.pill, 600) + 11;
      if (extra) w += measure('+' + extra, 11 * k, 600) + 12;
      if (lane.current) w += 10;
      const h = 24 * k;
      const anchor = lane.base, ax = X(anchor), ay = Y(anchor);
      const side = lane.slot > 0 ? 1 : -1;
      const yFor = (sd) => ay + sd * gap / 2 - h / 2;
      const cands = [];
      for (const sd of [side, -side]) for (const dx of [16, 16 + colW * 0.5, 16 + colW, 16 + colW * 1.5, 16 + colW * 2, 16 + colW * 3, -w * 0.4]) cands.push({ x: ax + dx, y: yFor(sd), side: sd });
      const full = [lane.name].concat(others.map((r) => r.name)).join('\n') + (lane.nameKind === 'merged' ? '\n(merged; branch deleted)' : '');
      want.push({ kind: 'pill', node: anchor, lane: lane.idx, text, full, w, h, ax, ay, cands, extra, dashed: lane.nameKind === 'remote', merged: lane.nameKind === 'merged', current: lane.current, priority: PRIORITY.pill + (lane.current ? 10 : 0) + (lane.main ? 5 : 0) });
    }
    // merge callouts
    for (const nd of L.nodes) {
      if (!nd.merge) continue;
      const text = ellipsize(shortMerge(nd.subject), C.calloutMax);
      const w = measure(text, S.callout, 400) + 20, h = 22 * k, ax = X(nd.i), ay = Y(nd.i);
      const side = nd.slot > 0 ? 1 : -1;
      const cands = [];
      for (const sd of [side, -side]) {
        const y = ay + sd * gap / 2 - h / 2 + sd * 2;
        for (const x of [ax - 12, ax - w / 2, ax - w + 12, ax + 10]) cands.push({ x, y, side: sd });
      }
      want.push({ kind: 'callout', node: nd.i, lane: nd.lane, text, full: nd.subject, w, h, ax, ay, cands, priority: PRIORITY.callout + nd.col / 1e6 });
    }
    // tags + refs that are not shown as a lane pill
    for (const nd of L.nodes) {
      const lane = L.lanes[nd.lane];
      for (const r of nd.refs) {
        let kind;
        if (r.kind === 'tag') { if (!showTags) continue; kind = 'tag'; }
        else if (r.kind === 'branch' || r.kind === 'remote') {
          if (nd.i === lane.tipIdx) continue;          // in the pill / "+N"
          if (r.kind === 'remote' && !showRemote) continue;
          kind = 'ref';
        } else continue;
        const text = ellipsize(r.name, C.tagMax);
        const w = 8 + 10 * k + 4 + measure(text, S.tag, 500) + 8, h = 17 * k, ax = X(nd.i), ay = Y(nd.i);
        const inward = nd.slot < 0 ? 1 : (nd.slot > 0 ? -1 : 1);
        const off = C.dotR + 7 * k;
        const yFor = (sd) => (sd > 0 ? ay + off : ay - off - h);
        const cands = [];
        for (const sd of [inward, -inward]) for (const dx of [-w / 2, 4, -w + 4, colW * 0.5, -colW * 0.5 - w]) cands.push({ x: ax + dx, y: yFor(sd), side: sd });
        want.push({ kind, node: nd.i, lane: nd.lane, text, full: (r.kind === 'tag' ? 'tag ' : r.kind === 'remote' ? 'remote ' : 'branch ') + r.name, w, h, ax, ay, cands, dashed: r.kind === 'remote', refKind: r.kind, priority: (kind === 'tag' ? PRIORITY.tag : PRIORITY.ref) - nd.i / 1e7 });
      }
    }
    // "older history" stubs
    const stubSeen = new Set();
    for (const st of L.stubs) {
      if (!st.first || stubSeen.has(st.node)) continue;
      stubSeen.add(st.node);
      const text = 'older history', w = measure(text, 10.5 * k, 400) + 4, h = 14 * k;
      const ax = X(st.node), ay = Y(st.node);
      const cands = [{ x: ax - 60 - w / 2, y: ay + 8, side: 1 }, { x: ax - 60 - w / 2, y: ay - 8 - h, side: -1 }, { x: ax - 64 - w, y: ay - h / 2, side: 0 }];
      want.push({ kind: 'stub', node: st.node, lane: L.nodes[st.node].lane, text, full: 'History is truncated here: older commits are not shown', w, h, ax, ay, cands, priority: PRIORITY.stub });
    }

    // obstacles: dots (hard), curve boxes (soft)
    const hard = new Grid(64), soft = new Grid(64);
    for (const nd of L.nodes) {
      const r = (nd.i === L.head ? C.headR + 2 : nd.merge ? C.mergeR : C.dotR) + 1;
      hard.add({ x: X(nd.i) - r, y: Y(nd.i) - r, w: 2 * r, h: 2 * r, dot: true });
    }
    for (const e of L.edges) {
      if (e.kind === 'lane') continue;
      const p = L.nodes[e.p], c = L.nodes[e.c];
      const segs = edgeSegments(X(e.p), Y(e.p), X(e.c), Y(e.c), e.route, colW);
      for (const s of segs) {
        if (s.t !== 'C') continue;
        const x0 = Math.min(s.x0, s.x1), x1 = Math.max(s.x0, s.x1), y0 = Math.min(s.y0, s.y1), y1 = Math.max(s.y0, s.y1);
        soft.add({ x: x0 + (x1 - x0) * 0.15, y: y0 + 4, w: (x1 - x0) * 0.7, h: Math.max(1, y1 - y0 - 8) });
      }
      void p; void c;
    }
    if (L.head >= 0) {                                    // the tail
      const ax = X(L.head), ay = Y(L.head);
      hard.add({ x: ax + 12, y: ay - 3, w: C.tail, h: 6 });
      hard.add({ x: ax + C.tail - 9, y: ay - 9, w: 18 + (L.dirty ? 16 : 0), h: 18 });
    }
    want.sort((a, b) => b.priority - a.priority);
    const placed = new Grid(64);
    const out = [];
    for (const lb of want) {
      let best = null, bestSoft = Infinity;
      for (const c of lb.cands) {
        const r = { x: c.x, y: c.y, w: lb.w, h: lb.h };
        if (placed.hits(r, 5) || hard.hits(r, 0)) continue;
        const s = soft.area(r);
        if (s < bestSoft) { best = c; bestSoft = s; if (!s) break; }
      }
      const res = Object.assign({}, lb, { x: best ? best.x : lb.cands[0].x, y: best ? best.y : lb.cands[0].y, side: best ? best.side : lb.cands[0].side, hidden: !best });
      delete res.cands;
      if (res.kind === 'callout') res.px = clamp(res.ax, res.x + 9, res.x + res.w - 9);
      if (best) placed.add({ x: res.x, y: res.y, w: res.w, h: res.h });
      out.push(res);
    }
    return out;
  }

  // ---------------------------------------------------------------- navigation
  function navigate(L, idx, dir) {
    if (!L || !L.nodes.length) return -1;
    const N = L.nodes;
    if (dir === 'home') {
      let best = -1;
      for (const nd of N) if (best < 0 || nd.col < N[best].col || (nd.col === N[best].col && Math.abs(nd.slot) < Math.abs(N[best].slot))) best = nd.i;
      return best;
    }
    if (dir === 'end') return L.head >= 0 ? L.head : (L.mainLane >= 0 ? L.lanes[0].tipIdx : 0);
    if (idx < 0 || idx >= N.length) return L.head >= 0 ? L.head : 0;
    const nd = N[idx], lane = L.lanes[nd.lane];
    const pos = lane.nodes.indexOf(idx);
    if (dir === 'left') {
      if (nd.parents.length) return nd.parents[0];
      return idx;
    }
    if (dir === 'right') {
      if (pos < lane.nodes.length - 1) return lane.nodes[pos + 1];
      const out = nd.children.slice().sort((a, b) => N[a].col - N[b].col);
      return out.length ? out[0] : idx;
    }
    if (dir === 'up' || dir === 'down') {
      const step = dir === 'up' ? -1 : 1;
      for (let s = nd.slot + step; s >= L.minSlot && s <= L.maxSlot; s += step) {
        const list = L.bySlot.get(s);
        if (!list || !list.length) continue;
        let best = list[0], bd = Infinity;
        for (const j of list) { const d = Math.abs(N[j].col - nd.col); if (d < bd) { bd = d; best = j; } }
        return best;
      }
      return idx;
    }
    return idx;
  }

  // ---------------------------------------------------------------- synthetic history
  /** A plausible history of `n` commits: main + short-lived feature branches merged back, a few unmerged, tags. */
  function synth(n, seed, opts) {
    opts = opts || {};
    const rand = rng(seed || 1);
    const hex = () => { let s = ''; for (let i = 0; i < 40; i++) s += '0123456789abcdef'[Math.floor(rand() * 16)]; return s; };
    const made = [];
    let t = 1700000000;
    const mk = (parents, subject, extra) => {
      t += 600 + Math.floor(rand() * 7200);
      const c = Object.assign({ hash: hex(), parents, subject, author: 'Dev ' + (1 + Math.floor(rand() * 5)), email: 'dev@example.com', ts: t, tz: 0, cts: t, refs: [], merged_name: null }, extra || {});
      c.short = c.hash.slice(0, 7);
      made.push(c);
      return c;
    };
    let main = mk([], 'Initial commit');
    const active = [];
    let feat = 0;
    while (made.length < n) {
      const r = rand();
      if (active.length < (opts.maxActive || 3) && r < 0.12) {
        feat++;
        active.push({ name: 'feature/f' + feat, tip: mk([main.hash], 'feature f' + feat + ': start'), len: 1, target: 2 + Math.floor(rand() * 7), keep: rand() < 0.08 });
      } else if (active.length && r < 0.62) {
        const b = active[Math.floor(rand() * active.length)];
        if (b.len >= b.target) {
          active.splice(active.indexOf(b), 1);
          if (b.keep) { b.tip.refs.push({ name: b.name, kind: 'branch' }); continue; }
          main = mk([main.hash, b.tip.hash], "Merge branch '" + b.name + "'", { merged_name: b.name });
        } else { b.tip = mk([b.tip.hash], b.name + ': step ' + (b.len + 1)); b.len++; }
      } else {
        main = mk([main.hash], 'Main work ' + made.length);
        if (rand() < 0.02) main.refs.push({ name: 'v' + made.length, kind: 'tag' });
      }
    }
    for (const b of active) b.tip.refs.push({ name: b.name, kind: 'branch' });
    main.refs.push({ name: 'master', kind: 'branch', current: true });
    const commits = made.slice(0, n).reverse();
    const keep = new Set(commits.map((c) => c.hash));
    for (const c of commits) c.refs = c.refs.filter(() => true);
    if (!keep.has(main.hash)) commits[0].refs.push({ name: 'master', kind: 'branch', current: true });
    const headC = commits.find((c) => c.refs.some((r) => r.name === 'master')) || commits[0];
    return {
      ok: true, error: null, repo: { name: 'synthetic', root: '/tmp/synthetic', default_branch: 'master' },
      head: { hash: headC.hash, branch: 'master', detached: false }, dirty: { changed: 0 }, total: commits.length, truncated: false, commits,
    };
  }

  const api = {
    C, clamp, hashStr, rng, ease, approach, ellipsize, shortMerge, absTime, relTime,
    parseColor, toHex, rgba, mix, luminance, contrast, legible, nearestColorIdx,
    layout, assignColors, edgeSegments, segLength, splitCubic, partialSegments, rectsOverlap, placeLabels, navigate, synth,
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.GitGraphCore = api;
})(typeof self !== 'undefined' ? self : this);
