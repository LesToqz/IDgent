// IDgent graph contract and behavioral tests. See docs/graph-spec.md.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';

const require = createRequire(import.meta.url);
const G = require('../../idgent/graph_web/graph_core.js');
const contract = JSON.parse(readFileSync(new URL('../graph_params.json', import.meta.url), 'utf8'));
const close = (actual, expected, tolerance = 1e-6) =>
  assert.ok(Math.abs(actual - expected) <= tolerance, `expected ${expected}, got ${actual}`);
const files = (n) => Array.from({ length: n }, (_, i) => ({ id: `file-${i}` }));
const chain = (n) => ({ nodes: files(n), links: Array.from({ length: n - 1 }, (_, i) => ({ s: i, t: i + 1 })) });

function simulate(graph, seed = 17) {
  const model = G.buildModel(graph);
  const positions = G.placeNodes(model, new Map(), G.rng(seed));
  const sim = new G.Simulation();
  sim.setNodes(graph.nodes.map((node, i) => ({ ...node, x: positions[i][0], y: positions[i][1] })));
  sim.setLinks(model.pairs);
  return sim;
}
function settle(sim) {
  let ticks = 0;
  while (sim.running && ticks < 1200) { sim.tick(); ticks++; }
  assert.ok(!sim.running, `did not settle after ${ticks} ticks`);
  for (let i = 0; i < sim.n; i++) {
    assert.ok(Number.isFinite(sim.x[i]) && Number.isFinite(sim.y[i]), 'finite positions');
    assert.ok(Math.hypot(sim.vx[i], sim.vy[i]) < 4, 'small residual velocity');
  }
  return ticks;
}

test('Python and browser graph settings share the IDgent contract', () => {
  assert.deepEqual(G.DEFAULTS, contract.defaults);
  assert.deepEqual(G.LIMITS, contract.limits);
  assert.deepEqual(G.STEPS, contract.steps);
  close(G.C.minScale, contract.limits.scale[0]);
  close(G.C.maxScale, contract.limits.scale[1]);
});

test('marker sizes grow logarithmically and keep hubs bounded', () => {
  close(G.nodeSize(0, 1), 6);
  close(G.nodeSize(3, 1), 10);
  close(G.nodeSize(15, 1.5), 21);
  close(G.nodeSize(-2, 1), 6);
  close(G.nodeSize(2 ** 20, 2), 48);
  close(G.screenRadius(10, 0.25), 5);
  close(G.screenRadius(10, 4), 20);
  close(G.labelFont(10), 14);
});

test('label detail fades with zoom and increasing the control delays labels', () => {
  close(G.textAlpha(0.12, 0), 0);
  close(G.textAlpha(0.47, 0), 0.5);
  close(G.textAlpha(0.82, 0), 1);
  close(G.textAlpha(0.47, 1), 0.25);
  for (const zoom of [0.04, 0.2, 0.8, 3, 6]) {
    assert.ok(G.textAlpha(zoom, -1) >= G.textAlpha(zoom, 2));
  }
});

test('connection width is zoom independent and direction markers fade in', () => {
  for (const zoom of [0.04, 0.5, 1, 6]) close(G.lineWorldWidth(1.25, zoom) * zoom, 1.25);
  close(G.arrowFactor(0.18), 0);
  close(G.arrowFactor(0.49), 0.5);
  close(G.arrowFactor(0.8), 1);
  close(G.arrowSize(4), 12);
});

test('force controls use direct strengths and linear repulsion', () => {
  assert.deepEqual(G.forceParams(G.DEFAULTS), { center: 0.12, link: 0.35, distance: 160, repel: -720 });
  const off = G.forceParams({ centerStrength: 0, linkStrength: 0, repelStrength: 0 });
  close(off.center, 0); close(off.link, 0); close(off.repel, 0);
  close(G.forceParams({ repelStrength: 12 }).repel, -1440);
  for (const key of G.FORCE_KEYS) {
    const [lo, hi] = contract.limits[key];
    close(G.boundedSetting({ [key]: -Infinity }, key), contract.defaults[key]);
    close(G.boundedSetting({ [key]: -100 }, key), lo);
    close(G.boundedSetting({ [key]: 1e6 }, key), hi);
    close(G.boundedSetting({ [key]: '2' }, key), contract.defaults[key]);
  }
});

test('isolated link pulls endpoints together without moving its midpoint', () => {
  const sim = new G.Simulation();
  sim.setNodes([{ id: 'left', x: -150, y: 4 }, { id: 'right', x: 150, y: 4 }]);
  sim.setLinks([[0, 1]]);
  sim.params = { center: 0, link: 0.5, distance: 160, repel: 0 };
  sim.alpha = 1; sim.alphaDecay = 0;
  sim.manyBody = () => {}; sim.collide = () => {};
  sim.tick();
  close(sim.vx[0], 19.25, 1e-4);
  close(sim.vx[1], -19.25, 1e-4);
  close(sim.x[0] + sim.x[1], 0);
});

test('cooling settles a fresh graph within four seconds at 60 ticks per second', () => {
  const sim = new G.Simulation();
  sim.setNodes([]); sim.setLinks([]);
  const ticks = settle(sim);
  assert.ok(ticks >= 240 && ticks <= 241, `cooling took ${ticks} ticks`);
  sim.reheat(); close(sim.alpha, 0.4);
  assert.ok(sim.running);
});

test('wheel input normalizes devices, clamps zoom and preserves the anchor', () => {
  close(G.wheelDelta({ deltaY: 2, deltaMode: 1 }), 48);
  close(G.wheelDelta({ deltaY: 1, deltaMode: 2 }), 640);
  close(G.wheelDelta({ deltaY: 8, deltaMode: 0, ctrlKey: true }), 16);
  close(G.wheelFactor(480), Math.exp(-1));
  close(G.wheelFactor(480) * G.wheelFactor(-480), 1);
  close(G.clampScale(1e-8), 0.04); close(G.clampScale(100), 6);
  for (const [pan, before, after, anchor] of [[28, 1, 2, 180], [-14, 0.2, 0.7, 500]]) {
    const world = (anchor - pan) / before;
    close(G.zoomAround(pan, before, after, anchor) + world * after, anchor);
  }
});

test('easing is frame independent and reduced motion jumps to the target', () => {
  const results = [];
  for (const hz of [30, 60, 120]) {
    let value = 0;
    for (let i = 0; i < hz; i++) value = G.ease(value, 1, G.C.easeK, 1 / hz);
    results.push(value);
  }
  close(results[0], results[1]); close(results[1], results[2]);
  close(G.easeFactor(G.C.easeK, 1 / 60), 0.16);
  close(G.easeFactor(0.2, 2), G.easeFactor(0.2, 0.05));
  close(G.ease(0, 1, 0.2, 1 / 60, true), 1);
});

test('local traversal follows chosen directions and retains outer-hop size', () => {
  const model = G.buildModel({ nodes: files(4), links: [{ s: 0, t: 1 }, { s: 2, t: 0 }, { s: 1, t: 3 }] });
  assert.deepEqual([...G.localLayers(model, 0, 2, true, true)], [0, 1, 1, 2]);
  assert.deepEqual([...G.localLayers(model, 0, 2, false, true)], [0, 1, -1, 2]);
  assert.deepEqual([...G.localLayers(model, 0, 2, true, false)], [0, -1, 1, -1]);
  assert.deepEqual([...G.localLayers(model, -1, 2, true, true)], [-1, -1, -1, -1]);
  close(G.localWeight(0), 12); close(G.localWeight(1), 6); close(G.localWeight(2), 4);
  assert.ok(G.localWeight(6) > 0);
});

test('new placement is bounded and preserves surviving positions', () => {
  const graph = chain(128), model = G.buildModel(graph);
  const positions = G.placeNodes(model, new Map(), G.rng(23));
  const radius = Math.sqrt(72 * 72 * graph.nodes.length / Math.PI);
  assert.ok(positions.every(([x, y]) => Math.hypot(x, y) <= radius));
  const pair = G.buildModel(chain(2));
  const updated = G.placeNodes(pair, new Map([['file-0', [500, -300]]]), G.rng(23));
  assert.deepEqual(updated[0], [500, -300]);
  assert.ok(Math.abs(updated[1][0] - 500) <= 24);
  assert.ok(Math.abs(updated[1][1] + 300) <= 24);
});

test('directed pairs deduplicate while mutual references share a line', () => {
  const model = G.buildModel({ nodes: files(3), links: [
    { s: 0, t: 1 }, { s: 0, t: 1, kind: 'embed' }, { s: 1, t: 0 },
    { s: 0, t: 2 }, { s: 2, t: 2 }, { s: 90, t: 0 },
  ] });
  assert.deepEqual([...model.weight], [3, 2, 3]);
  assert.equal(model.lines.length, 2);
  assert.equal(model.pairs.length, 4);
});

test('hit testing uses drawing order and optional pointer slack', () => {
  assert.equal(G.hitTest(2, 0, [0, 3], [0, 0], [5, 5], [0, 1]), 1);
  assert.equal(G.hitTest(2, 0, [0, 3], [0, 0], [5, 5], [1, 0]), 0);
  assert.equal(G.hitTest(9, 0, [0], [0], [5], [0]), -1);
  assert.equal(G.hitTest(9, 0, [0], [0], [5], [0], 4), 0);
});

test('colour rules start blue, vary by rule and adapt to light backgrounds', () => {
  const first = G.newGroupColor(0, true);
  assert.match(first, /^#[0-9a-f]{6}$/);
  const color = G.parseColor(first);
  assert.ok(color.b > color.r);
  assert.notEqual(first, G.newGroupColor(1, true));
  const light = G.parseColor(G.newGroupColor(0, false));
  assert.ok(light.r + light.g + light.b < color.r + color.g + color.b);
  assert.deepEqual(G.parseColor('#63b3ed'), { r: 99, g: 179, b: 237, a: 1 });
  assert.deepEqual(G.parseColor({ a: 0.8, rgb: 0x123456 }), { r: 18, g: 52, b: 86, a: 0.8 });
  assert.deepEqual(G.parseColor('rgba(18, 52, 86, 0.8)'), { r: 18, g: 52, b: 86, a: 0.8 });
  close(G.palette({}).unresolved.a, 0.65);
  assert.equal(G.toHex(G.palette({}).background), '#17212b');
  assert.equal(G.toHex(G.palette({ background: '#ffffff' }).background), '#ffffff');
});

test('timeline reveals chronological files and ends with the complete graph', () => {
  const graph = { nodes: [{ id: 'new', ctime: 30 }, { id: 'old', ctime: 10 }, { id: 'middle', ctime: 20 }], links: [{ s: 1, t: 2 }, { s: 2, t: 0 }] };
  const timeline = G.timelapse(graph, true);
  assert.equal(timeline.total, 5);
  assert.equal(timeline.subset(0).nodes.length, 0);
  assert.deepEqual(timeline.subset(1).nodes.map((n) => n.id), ['old']);
  const end = timeline.subset(timeline.total);
  assert.deepEqual(end.nodes.map((n) => n.id), ['old', 'middle', 'new']);
  assert.equal(end.links.length, 2);
  assert.ok(end.links.every((link) => link.s < end.nodes.length && link.t < end.nodes.length));
  close(G.timelapseSpeed(12), 8); close(G.timelapseSpeed(240), 20); close(G.timelapseSpeed(1200), 72);
});

test('chain, ring, hub and clustered project layouts settle with finite positions', () => {
  const ring = chain(36); ring.links.push({ s: 35, t: 0 });
  const hub = { nodes: files(41), links: Array.from({ length: 40 }, (_, i) => ({ s: 0, t: i + 1 })) };
  for (const graph of [chain(28), ring, hub, G.demo(180, 31)]) settle(simulate(graph));
});

test('hub remains closer to the layout centre than its dependents', () => {
  const sim = simulate({ nodes: files(41), links: Array.from({ length: 40 }, (_, i) => ({ s: 0, t: i + 1 })) });
  settle(sim);
  const x = [...sim.x.subarray(0, sim.n)].reduce((a, b) => a + b, 0) / sim.n;
  const y = [...sim.y.subarray(0, sim.n)].reduce((a, b) => a + b, 0) / sim.n;
  const distance = (i) => Math.hypot(sim.x[i] - x, sim.y[i] - y);
  const leaves = Array.from({ length: 40 }, (_, i) => distance(i + 1)).reduce((a, b) => a + b, 0) / 40;
  assert.ok(distance(0) < leaves / 2);
});

test('seeded layout and graph generation are deterministic', () => {
  const graph = G.demo(160, 37);
  assert.deepEqual(graph, G.demo(160, 37));
  const first = simulate(graph), second = simulate(graph);
  for (let i = 0; i < 80; i++) { first.tick(); second.tick(); }
  assert.deepEqual(first.x, second.x); assert.deepEqual(first.y, second.y);
});

test('updates preserve coordinates and velocities of surviving file IDs', () => {
  const sim = simulate(chain(32));
  for (let i = 0; i < 40; i++) sim.tick();
  const previous = new Map(sim.ids.map((id, i) => [id, [sim.x[i], sim.y[i], sim.vx[i], sim.vy[i]]]));
  const kept = sim.ids.slice(4).reverse();
  sim.setNodes(kept.map((id) => ({ id })).concat([{ id: 'added', x: 14, y: -7 }]));
  for (const id of kept) {
    const i = sim.index.get(id);
    assert.deepEqual([sim.x[i], sim.y[i], sim.vx[i], sim.vy[i]], previous.get(id));
  }
  assert.ok(!sim.index.has('file-0'));
  close(sim.x[sim.index.get('added')], 14);
});

test('Barnes-Hut repulsion approximates exact pair forces within three percent', () => {
  const sim = simulate(G.demo(320, 43));
  for (let i = 0; i < 25; i++) sim.tick();
  const strength = -288;
  const exact = sim.bruteRepel(strength);
  const beforeX = sim.vx.slice(), beforeY = sim.vy.slice();
  sim.manyBody(strength);
  let error = 0, magnitude = 0;
  for (let i = 0; i < sim.n; i++) {
    error += Math.hypot(sim.vx[i] - beforeX[i] - exact.ax[i], sim.vy[i] - beforeY[i] - exact.ay[i]);
    magnitude += Math.hypot(exact.ax[i], exact.ay[i]);
  }
  assert.ok(error / magnitude < 0.03, `relative error ${error / magnitude}`);
});

test('collision separates crowded files even when other forces are disabled', () => {
  const sim = new G.Simulation(), random = G.rng(47);
  sim.setNodes(files(24).map((node) => ({ ...node, x: random() * 40, y: random() * 40 })));
  sim.setLinks([]);
  sim.params = { center: 0, link: 0, distance: 160, repel: 0 };
  settle(sim);
  for (let i = 0; i < sim.n; i++) for (let j = i + 1; j < sim.n; j++) {
    assert.ok(Math.hypot(sim.x[i] - sim.x[j], sim.y[i] - sim.y[j]) > 63);
  }
});

test('dragged file stays pinned, then resumes simulation when released', () => {
  const sim = simulate(chain(16));
  sim.fix(0, 73, -29); sim.alphaTarget = 0.4; sim.reheat();
  for (let i = 0; i < 32; i++) sim.tick();
  close(sim.x[0], 73); close(sim.y[0], -29);
  close(sim.vx[0], 0); close(sim.vy[0], 0);
  sim.unfix(0); sim.alphaTarget = 0;
  settle(sim);
});
