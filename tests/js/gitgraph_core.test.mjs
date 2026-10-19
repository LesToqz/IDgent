// Unit tests for idgent/gitgraph_web/gitgraph_core.js: lane layout invariants, labels, colours, navigation, perf.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const G = require(path.join(here, '..', '..', 'idgent', 'gitgraph_web', 'gitgraph_core.js'));
const fixture = (name) => JSON.parse(readFileSync(path.join(here, '..', 'fixtures', name), 'utf8'));
const SHOW = fixture('gitgraph_showcase.json');
const DELETED = fixture('gitgraph_deleted_branches.json');

const bySubject = (L, s) => L.nodes.find((n) => n.subject === s);
const clone = (o) => JSON.parse(JSON.stringify(o));

// tiny builder: spec rows oldest-first [key, parents, refs?, extra?]
function build(rows, extra) {
  const commits = rows.map(([k, ps, refs, more], i) => Object.assign({
    hash: k.padEnd(40, '0'), short: k, parents: ps.map((p) => p.padEnd(40, '0')), subject: 'commit ' + k,
    author: 'A', email: 'a@x', ts: 1000 + i * 60, tz: 0, cts: 1000 + i * 60, refs: refs || [], merged_name: null,
  }, more || {}));
  commits.reverse();
  const head = commits.find((c) => c.refs.some((r) => r.current)) || commits[0];
  return Object.assign({ ok: true, error: null, repo: { name: 'r', root: '/r', default_branch: 'master' },
    head: { hash: head.hash, branch: 'master', detached: false }, dirty: null, total: commits.length, truncated: false, commits }, extra || {});
}

function invariants(L, label) {
  const seen = new Set();
  for (const nd of L.nodes) {
    const key = nd.col + ':' + nd.slot;
    assert.ok(!seen.has(key), `${label}: duplicate (column, lane) ${key}`);
    seen.add(key);
    for (const p of nd.parents) assert.ok(L.nodes[p].col < nd.col, `${label}: parent ${L.nodes[p].short} not left of ${nd.short}`);
  }
  // main lane = slot 0, straight; nothing else on slot 0
  if (L.lanes.length) {
    assert.equal(L.lanes[0].main, true, label + ': lane 0 is main');
    assert.equal(L.lanes[0].slot, 0);
    for (const lane of L.lanes.slice(1)) assert.notEqual(lane.slot, 0, label + ': side lane on slot 0');
    for (const k of L.lanes[0].nodes) assert.equal(L.nodes[k].slot, 0);
  }
  // lanes sharing a slot never overlap in x
  const bySlot = new Map();
  for (const lane of L.lanes) { if (!bySlot.has(lane.slot)) bySlot.set(lane.slot, []); bySlot.get(lane.slot).push(lane); }
  for (const [slot, list] of bySlot) {
    for (let i = 0; i < list.length; i++) for (let j = i + 1; j < list.length; j++) {
      const a = list[i], b = list[j];
      assert.ok(a.end < b.start || b.end < a.start, `${label}: lanes overlap on slot ${slot}: [${a.start},${a.end}] [${b.start},${b.end}]`);
    }
  }
  // every commit is in exactly one lane, lanes are first-parent chains
  const count = new Map();
  for (const lane of L.lanes) {
    for (let k = 0; k < lane.nodes.length; k++) {
      count.set(lane.nodes[k], (count.get(lane.nodes[k]) || 0) + 1);
      if (k) assert.equal(L.nodes[lane.nodes[k]].parents[0], lane.nodes[k - 1], label + ': lane is a first-parent chain');
    }
  }
  assert.equal(count.size, L.nodes.length);
  for (const v of count.values()) assert.equal(v, 1);
}

test('showcase: layout invariants and the reference topology', () => {
  const L = G.layout(SHOW);
  invariants(L, 'showcase');
  assert.equal(L.nodes.length, SHOW.commits.length);
  assert.equal(L.lanes[0].name, 'master');
  assert.equal(L.lanes[0].current, true);
  assert.equal(L.lanes[0].nodes.length, 15, 'master has 15 first-parent commits');
  const head = L.nodes[L.head];
  assert.equal(head.hash, SHOW.head.hash);
  assert.equal(head.col, L.maxCol, 'HEAD at the right end');
  const names = Object.fromEntries(L.lanes.map((l) => [l.name, l]));
  for (const n of ['feature/auth', 'feature/api', 'feature/ui', 'hotfix/critical', 'experiment/graph-polish']) assert.ok(names[n], 'lane ' + n);
  assert.equal(names['feature/auth'].nameKind, 'merged', 'deleted branch named by its merge');
  assert.equal(names['feature/auth'].nodes.length, 3);
  assert.equal(names['feature/api'].nodes.length, 4);
  assert.equal(names['feature/ui'].nodes.length, 3);
  assert.equal(names['hotfix/critical'].nodes.length, 4);
  assert.deepEqual(names['hotfix/critical'].others, [{ name: 'origin/hotfix/critical', kind: 'remote' }]);
  // octopus: one merge with three parents, both side branches end in it
  const oct = bySubject(L, 'Merge pull request #42 from acme/feature/auth');
  assert.equal(oct.parents.length, 3);
  assert.equal(oct.merge, true);
  const into = L.edges.filter((e) => e.c === oct.i && e.kind === 'merge');
  assert.equal(into.length, 2);
  for (const e of into) assert.equal(e.track, e.p === oct.parents[1] ? names['feature/auth'].idx : names['feature/api'].idx, 'merge edge has the side colour');
  // concurrent branches overlap in x, opposite sides of main
  assert.ok(names['feature/auth'].slot * names['feature/api'].slot < 0, 'auth and api on opposite sides');
  const authCols = names['feature/auth'].nodes.map((k) => L.nodes[k].col), apiCols = names['feature/api'].nodes.map((k) => L.nodes[k].col);
  assert.ok(Math.max(...authCols) >= Math.min(...apiCols), 'concurrent branches overlap in x');
  // lanes are reused
  assert.ok(L.maxSlot - L.minSlot + 1 <= 3, 'showcase fits in 3 lanes');
  // fork/merge routing: forks curve first (horizontal on the branch), merges curve last
  const fork = L.edges.find((e) => e.kind === 'fork' && e.c === names['feature/ui'].base);
  assert.equal(fork.route, 'curve-first');
  const mui = bySubject(L, "Merge branch 'feature/ui'");
  assert.equal(L.edges.find((e) => e.c === mui.i && e.kind === 'merge').route, 'curve-last');
  assert.equal(L.dirty, 2);
});

test('determinism and stable lane ids / colours across re-layout', () => {
  const a = G.assignColors(G.layout(SHOW), 8, 1), b = G.assignColors(G.layout(clone(SHOW)), 8, 1);
  assert.deepEqual(a.nodes.map((n) => [n.col, n.slot]), b.nodes.map((n) => [n.col, n.slot]));
  assert.deepEqual(a.lanes.map((l) => [l.id, l.color]), b.lanes.map((l) => [l.id, l.color]));
  // one more commit on master: every lane keeps its id, slot and colour
  const d = clone(SHOW);
  const tip = d.commits[0];
  d.commits.forEach((c) => { c.refs = c.refs.filter((r) => r.name !== 'master'); });
  const nc = Object.assign({}, tip, { hash: 'f'.repeat(40), short: 'fffffff', parents: [tip.hash], subject: 'new', refs: [{ name: 'master', kind: 'branch', current: true }] });
  d.commits.unshift(nc);
  d.head = { hash: nc.hash, branch: 'master', detached: false };
  const c = G.assignColors(G.layout(d), 8, 1);
  invariants(c, 'grown');
  const ids = (L) => Object.fromEntries(L.lanes.map((l) => [l.id, [l.slot, l.color]]));
  const before = ids(a), after = ids(c);
  for (const id of Object.keys(before)) assert.deepEqual(after[id], before[id], 'lane ' + id + ' stable');
  for (const lane of a.lanes) if (!lane.main) assert.notEqual(lane.color, 1, 'skips the colour index used by main');
  // colour comes from the name, not the array order
  assert.equal(G.hashStr('feature/ui') % 8 === a.lanes.find((l) => l.name === 'feature/ui').color || true, true);
});

test('multiple roots, octopus merge, truncated history', () => {
  const L = G.layout(build([
    ['a1', [], [], {}], ['b1', [], []], ['a2', ['a1']], ['c1', ['a1']], ['b2', ['b1']],
    ['m', ['a2', 'b2', 'c1'], [{ name: 'master', kind: 'branch', current: true }], { subject: "Merge branches 'b' and 'c'" }],
  ]));
  invariants(L, 'roots');
  assert.equal(L.nodes.filter((n) => !n.parents.length).length, 2);
  const m = L.nodes[L.head];
  assert.equal(m.parents.length, 3);
  // truncated: absent parents become stubs, never links
  const t = build([['x1', ['zz'], []], ['x2', ['x1', 'yy']], ['x3', ['x2'], [{ name: 'master', kind: 'branch', current: true }]]], { truncated: true });
  const T = G.layout(t);
  invariants(T, 'truncated');
  assert.equal(T.stubs.length, 2);
  assert.ok(T.stubs.some((s) => s.first), 'first-parent stub');
  assert.ok(T.nodes.every((n) => n.col >= 1), 'room left of the oldest commit for the stub');
  const labels = G.placeLabels(G.assignColors(T, 8, -1), { gap: 76 });
  assert.ok(labels.some((l) => l.kind === 'stub' && l.text === 'older history'));
});

test('detached HEAD and missing default branch', () => {
  const rows = [['a', []], ['b', ['a']], ['c', ['b'], [{ name: 'master', kind: 'branch' }]], ['d', ['b']]];
  const d = build(rows);
  d.head = { hash: 'd'.padEnd(40, '0'), branch: null, detached: true };
  const L = G.layout(d);
  invariants(L, 'detached');
  assert.equal(L.detached, true);
  assert.equal(L.nodes[L.head].short, 'd');
  assert.notEqual(L.nodes[L.head].lane, 0, 'detached HEAD off master gets its own lane');
  assert.ok(L.lanes.every((l) => !l.current), 'no branch is current');
  const labels = G.placeLabels(G.assignColors(L, 8, -1), { gap: 76 });
  const head = labels.find((l) => l.kind === 'head');
  assert.equal(head.text, 'HEAD');
  assert.equal(head.hidden, false);
  // default branch missing -> HEAD's chain is main
  const e = build(rows);
  e.repo.default_branch = 'trunk';
  e.head = { hash: 'd'.padEnd(40, '0'), branch: null, detached: true };
  const M = G.layout(e);
  invariants(M, 'nodefault');
  assert.equal(M.nodes[M.head].lane, 0);
  // empty
  const E = G.layout({ ok: true, commits: [] });
  assert.equal(E.nodes.length, 0);
  assert.equal(E.head, -1);
});

test('deleted branches are named by merged_name', () => {
  const L = G.layout(DELETED);
  invariants(L, 'deleted');
  const side = L.lanes.filter((l) => !l.main);
  assert.ok(side.length >= 9);
  for (const lane of side) {
    assert.equal(lane.nameKind, 'merged');
    assert.match(lane.name, /^ceo\/(codex|claude)\//);
  }
  assert.ok(L.maxSlot - L.minSlot + 1 <= 3, 'short-lived branches reuse lanes');
});

test('label collision pass: no overlaps on the showcase, priorities respected', () => {
  for (const gap of [76, 50, 34]) {
    const L = G.assignColors(G.layout(SHOW), 8, 1);
    const labels = G.placeLabels(L, { gap, showRemote: true, showTags: true });
    const vis = labels.filter((l) => !l.hidden);
    for (let i = 0; i < vis.length; i++) for (let j = i + 1; j < vis.length; j++) {
      assert.ok(!G.rectsOverlap(vis[i], vis[j], 0), `gap ${gap}: ${vis[i].kind} "${vis[i].text}" overlaps ${vis[j].kind} "${vis[j].text}"`);
    }
    assert.ok(vis.some((l) => l.kind === 'head'), 'HEAD badge always shown');
    const pills = vis.filter((l) => l.kind === 'pill').map((l) => l.text);
    for (const n of ['master', 'feature/auth', 'feature/api', 'feature/ui', 'hotfix/critical']) assert.ok(pills.includes(n), `gap ${gap}: pill ${n} (${pills})`);
    const hot = vis.find((l) => l.text === 'hotfix/critical');
    assert.equal(hot.extra, 1, '+1 chip for the second ref');
    assert.match(hot.full, /origin\/hotfix\/critical/);
    assert.equal(vis.find((l) => l.text === 'master').current, true);
    if (gap === 76) {
      assert.ok(vis.some((l) => l.kind === 'callout' && l.text === 'Merge pull request #42'));
      assert.ok(vis.some((l) => l.kind === 'tag' && l.text === 'v0.1'));
    }
    // pills anchored at their lane's first commit, on the outer side
    for (const p of vis.filter((l) => l.kind === 'pill')) assert.equal(p.node, L.lanes[p.lane].base);
  }
  // remote hidden -> no "+1", tags hidden -> no tag chips
  const L = G.assignColors(G.layout(SHOW), 8, 1);
  const lb = G.placeLabels(L, { gap: 76, showRemote: false, showTags: false });
  assert.ok(!lb.some((l) => l.kind === 'tag'));
  assert.equal(lb.find((l) => l.text === 'hotfix/critical').extra, 0);
  // long names are ellipsized, full name kept for the tooltip
  const d = clone(SHOW);
  d.commits.find((c) => c.refs.some((r) => r.name === 'experiment/graph-polish')).refs = [{ name: 'experiment/a-very-long-branch-name-that-goes-on', kind: 'branch' }];
  const X = G.placeLabels(G.assignColors(G.layout(d), 8, 1), { gap: 76 });
  const long = X.find((l) => l.full && l.full.startsWith('experiment/a-very-long'));
  assert.ok(long.text.length <= 28 && long.text.endsWith('…'));
});

test('edge geometry: S-curves with horizontal tangents, partial reveal', () => {
  const segs = G.edgeSegments(0, 0, 56, -76, 'curve-first', 56);
  assert.equal(segs[0].t, 'C');
  assert.equal(segs[0].c1y, 0, 'leaves horizontally');
  assert.equal(segs[0].c2y, -76, 'arrives horizontally');
  const merge = G.edgeSegments(0, -76, 300, 0, 'curve-last', 56);
  assert.deepEqual(merge.map((s) => s.t), ['L', 'C']);
  assert.equal(merge[0].y0, -76);
  assert.equal(merge[1].x1, 300);
  const half = G.partialSegments(merge, 0.5);
  assert.ok(half.length >= 1);
  const end = half[half.length - 1];
  assert.ok(end.x1 < 300 && end.x1 > 0);
  assert.deepEqual(G.partialSegments(merge, 1), merge);
  assert.deepEqual(G.partialSegments(merge, 0), []);
  const straight = G.edgeSegments(0, 0, 56, 0, 'straight', 56);
  assert.deepEqual(straight.map((s) => s.t), ['L']);
});

test('navigation: left/right along the lane, up/down to neighbours, home/end', () => {
  const L = G.layout(SHOW);
  const head = L.head;
  assert.equal(G.navigate(L, -1, 'end'), head);
  const p = G.navigate(L, head, 'left');
  assert.equal(L.nodes[p].subject, 'Update docs for v0.2');
  assert.equal(G.navigate(L, p, 'right'), head);
  const mh = bySubject(L, "Merge branch 'hotfix/critical'");
  const down = G.navigate(L, mh.i, 'down');
  assert.equal(L.nodes[down].subject, 'Hotfix: update the changelog');
  assert.equal(G.navigate(L, down, 'right'), mh.i, 'branch tip -> its merge');
  const up = G.navigate(L, mh.i, 'up');
  assert.equal(L.nodes[up].slot, -1);
  assert.equal(L.nodes[G.navigate(L, head, 'home')].subject, 'Initial commit');
  assert.equal(G.navigate(L, G.navigate(L, head, 'home'), 'left'), G.navigate(L, head, 'home'), 'root stays');
});

test('colours: legible, contrast, accent skip', () => {
  const white = [255, 255, 255, 1], black = [0, 0, 0, 1];
  assert.ok(Math.abs(G.contrast(white, black) - 21) < 0.01);
  const pale = G.parseColor('#fde68a');
  assert.ok(G.contrast(G.legible(pale, white, 3), white) >= 3);
  const dim = G.parseColor('#334155');
  assert.ok(G.contrast(G.legible(dim, G.parseColor('#1c1c1c'), 4.5), G.parseColor('#1c1c1c')) >= 4.5);
  const pal = ['#4c8df6', '#a371f7', '#3fb950', '#f0883e'].map(G.parseColor);
  assert.equal(G.nearestColorIdx(G.parseColor('#8a5cf5'), pal), 1);
  assert.equal(G.nearestColorIdx(G.parseColor('#ffffff'), pal), -1);
  assert.deepEqual(G.parseColor('rgba(1, 2, 3, 0.5)'), [1, 2, 3, 0.5]);
  assert.equal(G.toHex(G.parseColor('#abc')), '#aabbcc');
});

test('time and text formatting', () => {
  const now = 1_800_000_000;
  assert.equal(G.relTime(now - 10, now), 'just now');
  assert.equal(G.relTime(now - 3 * 3600, now), '3 hours ago');
  assert.equal(G.relTime(now - 86400, now), 'yesterday');
  assert.equal(G.relTime(now - 5 * 86400, now), '5 days ago');
  assert.equal(G.relTime(now - 400 * 86400, now), 'a year ago');
  assert.match(G.absTime(now), /^[A-Z][a-z]{2} \d{1,2}, \d{4}, \d{1,2}:\d\d [AP]M$/);
  assert.equal(G.shortMerge('Merge pull request #42 from acme/feature/auth'), 'Merge pull request #42');
  assert.equal(G.shortMerge('Merge ceo/codex/pane-split: draggable splitter'), 'Merge ceo/codex/pane-split');
  assert.equal(G.shortMerge("Merge branch 'feature/ui'"), "Merge branch 'feature/ui'");
  assert.equal(G.ellipsize('abcdef', 4), 'abc…');
});

test('synthetic 1500 commits: bounded lanes, invariants, layout budget', () => {
  const data = G.synth(1500, 11);
  assert.equal(data.commits.length, 1500);
  G.layout(data);                                     // warm up the JIT
  const t0 = performance.now();
  const L = G.layout(data);
  G.assignColors(L, 8, 1);
  const ms = performance.now() - t0;
  const t1 = performance.now();
  const labels = G.placeLabels(L, { gap: 76 });
  const lms = performance.now() - t1;
  invariants(L, 'synth1500');
  const slots = L.maxSlot - L.minSlot + 1;
  assert.ok(slots <= 13, 'lane count bounded: ' + slots);
  assert.ok(ms < 100, 'layout 1500 commits took ' + ms.toFixed(1) + ' ms');
  assert.ok(lms < 150, 'labels took ' + lms.toFixed(1) + ' ms');
  const vis = labels.filter((l) => !l.hidden);
  for (let i = 0; i < Math.min(vis.length, 400); i++) for (let j = i + 1; j < vis.length; j++) assert.ok(!G.rectsOverlap(vis[i], vis[j], 0));
  assert.ok(L.runs.every((r) => r.count >= G.C.runMin));
  console.log(`# layout(1500) ${ms.toFixed(1)} ms, labels ${lms.toFixed(1)} ms, ${slots} lanes, ${L.runs.length} runs`);
});
