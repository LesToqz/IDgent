// Unit tests for idgent/graph_web/graph_core.js (spec section K vectors + simulation properties).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const G = require(path.join(here, '..', '..', 'idgent', 'graph_web', 'graph_core.js'));
const params = JSON.parse(readFileSync(path.join(here, '..', '..', 'docs', 'reference_app-graph-params.json'), 'utf8'));
const V = params.formulas;
const close = (a, b, eps = 1e-5, msg) => assert.ok(Math.abs(a - b) <= eps, `${msg || ''} expected ${b}, got ${a}`);

// ------------------------------------------------------------------ K vectors
test('K.1 node size', () => {
  for (const v of V.nodeSize.vectors) {
    if ('screenRadius' in v) close(G.screenRadius(G.nodeSize(v.weight, 1), v.scale), v.screenRadius, 1e-5, `r w=${v.weight} s=${v.scale}`);
    else close(G.nodeSize(v.weight, v.mult), v.size, 1e-5, `w=${v.weight} m=${v.mult}`);
  }
  const s20 = G.nodeSize(20, 1);
  for (const [s, r] of [[0.3, 7.530], [0.6, 10.649], [1, 13.748], [2, 19.442]]) close(G.screenRadius(s20, s), r, 1e-3);
});

test('K.2 label alpha', () => {
  for (const v of V.labelAlpha.vectors) close(G.textAlpha(v.scale, v.textFadeMultiplier), v.alpha, 1e-5, JSON.stringify(v));
});

test('K.3 line width, arrows', () => {
  for (const v of V.lineWidth.vectors) close(G.lineWorldWidth(v.lineSizeMultiplier, v.scale), v.worldWidth, 1e-5);
  for (const [s, f] of [[0.3, 0], [0.4, 0.2], [0.6, 0.6], [0.8, 1], [2, 1]]) close(G.arrowFactor(s), f, 1e-9);
  close(G.arrowSize(1), 8, 1e-9);
  close(G.arrowSize(2), 11.3137, 1e-3);
});

test('K.4 force slider mapping', () => {
  for (const v of V.forceSliderMap.vectors) {
    if ('slider' in v) close(G.F2(v.slider, 0.01), v.F2, 1e-6, `F2(${v.slider})`);
    else close(G.forceParams({ repelStrength: v.repelSlider }).repel, v.simRepel, 1e-9, `repel ${v.repelSlider}`);
  }
  close(G.R2(0.1, 0.01), 0.518713248970312, 1e-12);
  close(G.R2(1, 0.01), 1, 1e-12);
  const d = G.forceParams(G.DEFAULTS);
  close(d.center, 0.1, 1e-9); close(d.link, 1, 1e-12); assert.equal(d.distance, 250); assert.equal(d.repel, -1000);
});

test('K.5 link force bias/strength and one tick', () => {
  for (const v of V.linkForce.vectors) {
    const sim = new G.Simulation();
    // build a graph where deg(s)=degS, deg(t)=degT through leaf links
    const nodes = [{ id: 's' }, { id: 't' }];
    const pairs = [[0, 1]];
    for (let k = 1; k < v.degS; k++) { nodes.push({ id: 'a' + k }); pairs.push([0, nodes.length - 1]); }
    for (let k = 1; k < v.degT; k++) { nodes.push({ id: 'b' + k }); pairs.push([1, nodes.length - 1]); }
    sim.setNodes(nodes); sim.setLinks(pairs);
    close(sim.lbias[0], v.bias, 1e-6); close(sim.lstr[0], v.strengthFactor, 1e-6);
  }
  const sim = new G.Simulation();
  sim.setNodes([{ id: 's', x: 0, y: 0 }, { id: 't', x: 400, y: 0 }]);
  sim.setLinks([[0, 1]]);
  sim.alpha = 0.3; sim.alphaDecay = 0; sim.params = { center: 0, link: 1, distance: 250, repel: 0 };
  // only the link force: replicate step 2 by zeroing the others
  const mb = sim.manyBody, co = sim.collide;
  sim.manyBody = () => {}; sim.collide = () => {};
  // y components are 0 -> jiggle 0.1 replaces them; check x only (before velocity decay: v/0.6)
  sim.tick();
  close(sim.vx[1] / 0.6, -22.5, 1e-3); close(sim.vx[0] / 0.6, 22.5, 1e-3);
  sim.manyBody = mb; sim.collide = co;
});

test('K.6 alpha decay', () => {
  close(G.C.alphaDecay, 0.0227628, 1e-6);
  const ticks = (a0) => { let a = a0, n = 0; while (a >= 0.001) { a += (0 - a) * G.C.alphaDecay; n++; } return n; };
  assert.equal(ticks(0.3), 248);
  assert.ok(Math.abs(ticks(1) - 300) <= 1);
});

test('K.7 wheel zoom, clamp, anchor', () => {
  for (const v of V.wheelZoom.vectors) close(G.wheelFactor(v.deltaY), v.factor, 1e-5);
  assert.equal(G.wheelDelta({ deltaY: 3, deltaMode: 1 }), 120);
  assert.equal(G.wheelDelta({ deltaY: 1, deltaMode: 2 }), 800);
  assert.equal(G.clampScale(100), 8); assert.equal(G.clampScale(1e-5), 0.0078125);
  // anchor: world point under the anchor stays fixed
  const pan = 100, s0 = 1, s1 = 1.5, anchor = 300;
  const w = (anchor - pan) / s0;
  close(G.zoomAround(pan, s0, s1, anchor) + w * s1, anchor, 1e-9);
  // smoothed approach 1 -> 2 takes 25 frames at 60 Hz
  let s = 1, n = 0; while (Math.abs(s / 2 - 1) >= 0.01) { s = G.ease(s, 2, G.C.zoomK, 1 / 60); n++; }
  assert.equal(n, 25);
});

test('easing is frame-rate independent', () => {
  for (const hz of [30, 60, 72, 144]) {
    let v = 0; const dt = 1 / hz;
    for (let t = 0; t < 0.5 - 1e-9; t += dt) v = G.ease(v, 1, 0.1, dt);
    const ref = 1 - Math.pow(0.9, 30);
    close(v, ref, 1e-6, `${hz} Hz`);
  }
  close(G.easeFactor(0.1, 1 / 60), 0.1, 1e-12);
  close(G.easeFactor(0.1, 1), G.easeFactor(0.1, 0.05), 1e-12);   // dt clamped to 50 ms
  // F.8: fade 0 -> 1 reaches 95 % after 29 frames
  let v = 0, n = 0; while (v < 0.95) { v = G.ease(v, 1, 0.1, 1 / 60); n++; }
  assert.equal(n, V.ease.vectors[0].frames);
  assert.equal(G.ease(0, 1, 0.1, 1 / 60, true), 1, 'reduce motion jumps');
});

test('K.8 local-graph weights', () => {
  for (const v of V.localWeights.vectors) {
    const w = []; for (let p = 1; p <= v.depth; p++) w.push(G.localWeight(p, v.depth));
    w.forEach((x, k) => close(x, v.weights[k], 1e-9));
  }
  close(G.nodeSize(30, 1), 16.703, 1e-3);
  // BFS layers respect incoming / outgoing
  const m = G.buildModel({ nodes: [{ id: 'c' }, { id: 'o' }, { id: 'i' }, { id: 'oo' }], links: [{ s: 0, t: 1 }, { s: 2, t: 0 }, { s: 1, t: 3 }] });
  assert.deepEqual([...G.localLayers(m, 0, 2, true, true)], [0, 1, 1, 2]);
  assert.deepEqual([...G.localLayers(m, 0, 2, false, true)], [0, 1, -1, 2]);
  assert.deepEqual([...G.localLayers(m, 0, 2, true, false)], [0, -1, 1, -1]);
});

test('K.9 initial placement disc + neighbour anchoring', () => {
  for (const [I, R] of [[1, 33.85], [10, 107.05], [100, 338.51], [1000, 1070.47]]) {
    const m = G.buildModel({ nodes: Array.from({ length: I }, (_, i) => ({ id: 'n' + i })), links: [] });
    const pos = G.placeNodes(m, new Map(), G.rng(7));
    const maxR = Math.max(...pos.map((p) => Math.hypot(p[0], p[1])));
    assert.ok(maxR <= R + 1e-6, `I=${I} max ${maxR}`);
    if (I >= 100) assert.ok(maxR > R * 0.9);
  }
  const m = G.buildModel({ nodes: [{ id: 'a' }, { id: 'b' }, { id: 'new' }], links: [{ s: 2, t: 0 }] });
  const pos = G.placeNodes(m, new Map([['a', [500, 500]], ['b', [-500, 0]]]), G.rng(3));
  assert.ok(Math.abs(pos[2][0] - 500) <= 30 && Math.abs(pos[2][1] - 500) <= 30, 'new node next to its neighbour (jitter 60)');
});

test('K.10 / timelapse speed', () => {
  for (const v of V.timelapse.vectors) close(G.timelapseSpeed(v.totalItems), v.speed, 1e-9);
  const g = { nodes: [{ id: 'a', ctime: 3 }, { id: 'b', ctime: 1 }, { id: 'c', ctime: 2 }], links: [{ s: 1, t: 2 }, { s: 0, t: 1 }] };
  const tl = G.timelapse(g, true);
  assert.equal(tl.total, 5);
  assert.deepEqual(tl.subset(1).nodes.map((n) => n.id), ['b']);
  assert.deepEqual(tl.subset(3).nodes.map((n) => n.id), ['b', 'c']);
  assert.equal(tl.subset(3).links.length, 1);
  assert.deepEqual(tl.subset(99).nodes.map((n) => n.id), ['b', 'c', 'a']);
});

test('degree = distinct in + out partners (A.4), mutual pair draws one line', () => {
  const m = G.buildModel({ nodes: [{ id: 'a' }, { id: 'b' }, { id: 'c' }], links: [
    { s: 0, t: 1 }, { s: 1, t: 0 }, { s: 0, t: 1, kind: 'embed' }, { s: 0, t: 2 }, { s: 2, t: 2, self: true }] });
  assert.deepEqual([...m.weight], [3, 2, 1 + 2]);
  assert.equal(m.lines.length, 2);
  assert.equal(m.pairs.filter((p) => p[0] !== p[1]).length, 3);
});

test('hit testing picks the topmost node', () => {
  const xs = [0, 5, 100], ys = [0, 0, 0], r = [10, 10, 10];
  assert.equal(G.hitTest(4, 0, xs, ys, r, [0, 1, 2]), 1);
  assert.equal(G.hitTest(4, 0, xs, ys, r, [1, 0, 2]), 0);
  assert.equal(G.hitTest(50, 0, xs, ys, r, [0, 1, 2]), -1);
  assert.equal(G.hitTest(111, 0, xs, ys, r, [0, 1, 2], 2), 2);
});

test('colours', () => {
  assert.deepEqual(G.parseColor('#8a5cf5'), { r: 138, g: 92, b: 245, a: 1 });
  assert.deepEqual(G.parseColor({ a: 1, rgb: 0xff0000 }), { r: 255, g: 0, b: 0, a: 1 });
  assert.deepEqual(G.parseColor('rgba(1, 2, 3, 0.5)'), { r: 1, g: 2, b: 3, a: 0.5 });
  assert.equal(G.toHex({ r: 255, g: 0, b: 16 }), '#ff0010');
  assert.equal(G.palette({}).unresolved.a, 0.5);
  const c = G.newGroupColor(0, true); const rgb = G.parseColor(c);
  assert.ok(rgb.r > rgb.b, 'hue 0 is reddish');
});

// ------------------------------------------------------------------ simulation
function makeSim(g, seed = 1) {
  const m = G.buildModel(g);
  const sim = new G.Simulation();
  const pos = G.placeNodes(m, new Map(), G.rng(seed));
  sim.setNodes(g.nodes.map((n, i) => ({ id: n.id, x: pos[i][0], y: pos[i][1] })));
  sim.setLinks(m.pairs);
  return { sim, m };
}
function run(sim, max = 2000) { let n = 0; while (sim.running && n < max) { sim.tick(); n++; } return n; }
const ids = (n, p = 'n') => Array.from({ length: n }, (_, i) => ({ id: p + i }));

test('converges for chain / ring / star / clusters', () => {
  const shapes = {
    chain: { nodes: ids(20), links: Array.from({ length: 19 }, (_, i) => ({ s: i, t: i + 1 })) },
    ring: { nodes: ids(24), links: Array.from({ length: 24 }, (_, i) => ({ s: i, t: (i + 1) % 24 })) },
    star: { nodes: ids(31), links: Array.from({ length: 30 }, (_, i) => ({ s: 0, t: i + 1 })) },
    clusters: G.demo(200, 9),
  };
  for (const [name, g] of Object.entries(shapes)) {
    const { sim } = makeSim(g);
    const n = run(sim);
    assert.ok(sim.alpha < sim.alphaMin, `${name} alpha ${sim.alpha}`);
    assert.ok(n <= 301, `${name} took ${n} ticks`);
    for (let i = 0; i < sim.n; i++) assert.ok(Number.isFinite(sim.x[i]) && Number.isFinite(sim.y[i]), `${name} finite`);
    let v = 0; for (let i = 0; i < sim.n; i++) v = Math.max(v, Math.hypot(sim.vx[i], sim.vy[i]));
    assert.ok(v < 5, `${name} residual velocity ${v}`);
  }
});

test('star hub ends near the centre', () => {
  const { sim } = makeSim({ nodes: ids(31), links: Array.from({ length: 30 }, (_, i) => ({ s: 0, t: i + 1 })) });
  run(sim);
  let mx = 0, my = 0; for (let i = 0; i < sim.n; i++) { mx += sim.x[i]; my += sim.y[i]; }
  mx /= sim.n; my /= sim.n;
  const hub = Math.hypot(sim.x[0] - mx, sim.y[0] - my);
  let leaf = 0; for (let i = 1; i < sim.n; i++) leaf += Math.hypot(sim.x[i] - mx, sim.y[i] - my);
  leaf /= sim.n - 1;
  assert.ok(hub < leaf * 0.25, `hub ${hub} vs leaves ${leaf}`);
});

test('deterministic with a seeded RNG', () => {
  const g = G.demo(150, 4);
  const a = makeSim(g, 11).sim, b = makeSim(g, 11).sim;
  for (let k = 0; k < 120; k++) { a.tick(); b.tick(); }
  assert.deepEqual([...a.x.subarray(0, a.n)], [...b.x.subarray(0, b.n)]);
  assert.deepEqual(G.demo(100, 3), G.demo(100, 3));
});

test('diff conserves ids and state of kept nodes', () => {
  const g = G.demo(120, 2);
  const { sim } = makeSim(g);
  for (let k = 0; k < 50; k++) sim.tick();
  const before = new Map(sim.ids.map((id, i) => [id, [sim.x[i], sim.y[i], sim.vx[i]]]));
  const kept = sim.ids.slice(10).reverse();                     // drop 10, reorder the rest
  sim.setNodes(kept.map((id) => ({ id })).concat([{ id: 'brand-new', x: 1, y: 2 }]));
  assert.equal(sim.n, kept.length + 1);
  for (const id of kept) {
    const i = sim.index.get(id); const b = before.get(id);
    assert.equal(sim.x[i], b[0]); assert.equal(sim.y[i], b[1]); assert.equal(sim.vx[i], b[2]);
  }
  assert.equal(sim.x[sim.index.get('brand-new')], 1);
  assert.ok(!sim.index.has(sim.ids[0] === kept[0] ? '__none' : before.keys().next().value));
});

test('quadtree repel agrees with brute force (< 2 %)', () => {
  const g = G.demo(400, 5);
  const { sim } = makeSim(g);
  for (let k = 0; k < 30; k++) sim.tick();
  const strength = -1000 * 0.3;
  const brute = sim.bruteRepel(strength);
  const vx0 = Float64Array.from(sim.vx.subarray(0, sim.n)), vy0 = Float64Array.from(sim.vy.subarray(0, sim.n));
  sim.manyBody(strength);
  let err = 0, mag = 0;
  for (let i = 0; i < sim.n; i++) {
    const ax = sim.vx[i] - vx0[i], ay = sim.vy[i] - vy0[i];
    err += Math.hypot(ax - brute.ax[i], ay - brute.ay[i]);
    mag += Math.hypot(brute.ax[i], brute.ay[i]);
  }
  assert.ok(err / mag < 0.02, `relative error ${(err / mag * 100).toFixed(2)} %`);
});

test('collide keeps nodes >= ~120 apart for an unlinked crowd', () => {
  const nodes = ids(40); const sim = new G.Simulation();
  const R = G.rng(2);
  sim.setNodes(nodes.map((n) => ({ id: n.id, x: R() * 50, y: R() * 50 })));
  sim.setLinks([]);
  run(sim);
  let minD = Infinity;
  for (let i = 0; i < sim.n; i++) for (let j = i + 1; j < sim.n; j++) minD = Math.min(minD, Math.hypot(sim.x[i] - sim.x[j], sim.y[i] - sim.y[j]));
  assert.ok(minD > 80, `min distance ${minD}`);
});

test('pinned (dragged) node stays at the pointer', () => {
  const { sim } = makeSim(G.demo(60, 1));
  sim.fix(0, 123, -45); sim.alphaTarget = 0.3; sim.reheat(0.3);
  for (let k = 0; k < 20; k++) sim.tick();
  assert.equal(sim.x[0], 123); assert.equal(sim.y[0], -45);
  assert.ok(sim.alpha > 0.29);
  sim.unfix(0); sim.alphaTarget = 0;
  run(sim);
  assert.ok(!sim.running);
});

test('tick time budget (calibrated)', (t) => {
  // calibrate: skip on very slow machines
  const c0 = performance.now(); let acc = 0; for (let i = 0; i < 2e7; i++) acc += i % 7; const cal = performance.now() - c0;
  if (cal > 200 || acc < 0) { t.skip(`slow machine (${cal.toFixed(0)} ms calibration)`); return; }
  const measure = (n, links) => {
    const g = G.demo(n, 8);
    const R = G.rng(5);
    while (g.links.length < links) { const s = Math.floor(R() * n), tt = Math.floor(R() * n); if (s !== tt) g.links.push({ s, t: tt }); }
    const { sim } = makeSim(g);
    for (let k = 0; k < 20; k++) sim.tick();
    const t0 = performance.now(); for (let k = 0; k < 60; k++) sim.tick();
    return (performance.now() - t0) / 60;
  };
  const a = measure(300, 1000), b = measure(1500, 5000);
  assert.ok(a <= 4, `300/1000 tick ${a.toFixed(2)} ms`);
  assert.ok(b <= 12, `1500/5000 tick ${b.toFixed(2)} ms`);
});
