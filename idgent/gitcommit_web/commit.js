(function () {
  'use strict';
  const C = window.CommitCore, app = document.getElementById('app');
  const root = document.documentElement;
  let detail = null, models = [], pending = new Map(), split = false, reduceMotion = false;
  let observer = null, copyTimer = null, focusedFile = -1;
  const theme = {syntax: {}};
  const defaults = {
    bg: '#202127', fg: '#dcdde4', muted: '#989ba9', surface: '#25262e', surface2: '#2c2d37',
    border: '#3b3d49', hover: '#343541', accent: '#8a5cf5', accent_fg: '#ffffff',
    add: '#90ce9b', del: '#f39499', add_bg: 'rgba(78,157,93,.12)', del_bg: 'rgba(210,68,79,.14)',
    mono: '"DejaVu Sans Mono", monospace', ui: 'system-ui, sans-serif', size: 13, dark: true,
    syntax: {}
  };
  function post(message) {
    const handler = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.idgent;
    if (handler) { handler.postMessage(message); return true; }
    return false;
  }
  window.addEventListener('error', e => post({type: 'error', message: e.message || 'Commit page error'}));
  window.addEventListener('unhandledrejection', e => post({type: 'error', message: String(e.reason)}));
  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function icon(kind) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor'); svg.setAttribute('stroke-width', '1.6');
    svg.setAttribute('stroke-linecap', 'round'); svg.setAttribute('stroke-linejoin', 'round');
    svg.setAttribute('aria-hidden', 'true'); svg.classList.add('icon');
    const path = document.createElementNS(svg.namespaceURI, 'path');
    path.setAttribute('d', ({copy: 'M9 9h11v12H9z M5 15H3V3h11v2', chevron: 'm6 9 6 6 6-6',
      check: 'm5 12 4 4L19 6', commit: 'M3 12h5m8 0h5 M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0',
      error: 'M12 8v5m0 3h.01 M10.3 3.8 1.8 18.5A1 1 0 0 0 2.7 20h18.6a1 1 0 0 0 .9-1.5L13.7 3.8a2 2 0 0 0-3.4 0',
      search: 'M16 16l5 5 M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0'})[kind] || '');
    svg.append(path); return svg;
  }
  function button(text, cls, action, label) {
    const b = el('button', cls, text); b.type = 'button';
    if (label) b.setAttribute('aria-label', label);
    if (action) b.addEventListener('click', action);
    return b;
  }
  function counts(add, del, cls = 'file-stats') {
    const node = el('div', cls);
    node.append(el('span', 'add-count', '+' + (add || 0)), el('span', 'del-count', '−' + (del || 0)));
    return node;
  }
  function clear() {
    if (observer) observer.disconnect();
    clearTimeout(copyTimer); pending.clear(); models = []; focusedFile = -1;
    app.replaceChildren(); window.scrollTo(0, 0);
  }
  function updateCollapseButton() {
    const b = document.getElementById('toggle-all');
    if (b) b.textContent = models.some(m => !m.collapsed) ? 'Collapse all' : 'Expand all';
  }
  function toolbar() {
    const bar = el('div', 'toolbar');
    bar.setAttribute('aria-label', 'Changes summary');
    const stats = detail.stats || {};
    bar.append(counts(stats.additions, stats.deletions, 'totals'),
      el('span', 'file-total', `${stats.files || 0} ${(stats.files || 0) === 1 ? 'file' : 'files'} changed`));
    const actions = el('div', 'toolbar-actions');
    const all = button('Collapse all', '', () => {
      const collapse = models.some(m => !m.collapsed);
      models.forEach(m => toggleFile(m, collapse, false)); updateCollapseButton();
    });
    all.id = 'toggle-all';
    const segment = el('div', 'segment'); segment.setAttribute('role', 'group'); segment.setAttribute('aria-label', 'Diff layout');
    ['Unified', 'Split'].forEach((label, index) => {
      const b = button(label, '', () => {
        if (split === !!index) return;
        split = !!index;
        segment.querySelectorAll('button').forEach((node, i) => node.setAttribute('aria-pressed', String(i === index)));
        models.forEach(m => { if (m.rendered) { m.rendered = false; renderBody(m); } });
      });
      b.setAttribute('aria-pressed', String(split === !!index)); segment.append(b);
    });
    actions.append(all, segment); bar.append(actions); return bar;
  }
  function copySha() {
    if (!detail) return;
    post({type: 'copy', text: detail.hash});
    // A standalone page has no GTK clipboard; browser clipboard support is optional.
    if (!(window.webkit && window.webkit.messageHandlers) && navigator.clipboard) {
      navigator.clipboard.writeText(detail.hash).catch(() => {});
    }
    const b = document.getElementById('copy-sha');
    if (!b) return;
    b.replaceChildren(icon('check'), el('span', '', 'Copied')); b.classList.add('copied');
    document.getElementById('announcement').textContent = 'Commit SHA copied';
    clearTimeout(copyTimer);
    copyTimer = setTimeout(() => { b.replaceChildren(icon('copy'), el('span', '', 'Commit SHA')); b.classList.remove('copied'); }, 1200);
  }
  function header() {
    const h = el('header', 'commit-header'), who = el('div', 'identity');
    const author = detail.author || {}, committer = detail.committer || {};
    const name = author.name || 'Unknown author';
    const initials = name.trim().split(/\s+/).filter(Boolean).slice(0, 2).map(n => Array.from(n)[0]).join('').toUpperCase();
    const avatar = el('div', 'avatar', initials); avatar.setAttribute('aria-hidden', 'true');
    let hash = 0;
    for (const ch of author.email || name) hash = ((hash << 5) - hash + ch.codePointAt(0)) | 0;
    avatar.dataset.slot = Math.abs(hash) % 4;
    styleAvatar(avatar);
    const block = el('div', 'author-block');
    const date = el('div', 'date-line', C.dateLabel(author.ts || 0) + (author.email ? ' • ' + author.email : ''));
    date.title = C.fullDate(author.ts || 0, author.tz || 0);
    block.append(el('div', 'author', name), date);
    if (committer.name && ['name', 'email', 'ts', 'tz'].some(k => committer[k] !== author[k])) {
      const line = el('div', 'committer', `Committed by ${committer.name}${committer.email ? ' • ' + committer.email : ''} • ${C.dateLabel(committer.ts || 0)}`);
      line.title = C.fullDate(committer.ts || 0, committer.tz || 0); block.append(line);
    }
    const copy = button(undefined, 'copy-sha', copySha, 'Copy full commit SHA'); copy.id = 'copy-sha';
    copy.title = detail.hash; copy.append(icon('copy'), el('span', '', 'Commit SHA'));
    who.append(avatar, block, copy); h.append(who);
    const message = el('div', 'message'); message.append(el('h1', 'subject', detail.subject || '(No commit message)'));
    if (detail.body) message.append(el('p', 'body', detail.body));
    const chips = el('div', 'chips');
    (detail.refs || []).forEach(ref => {
      const chip = button(undefined, 'chip ref', () => post({type: 'open-commit', hash: detail.hash}));
      chip.title = ref.kind + ': ' + ref.name;
      chip.append(el('span', 'ref-icon', ref.kind === 'tag' ? '◇' : '⑂'), document.createTextNode(ref.name)); chips.append(chip);
    });
    (detail.parents || []).forEach(parent => {
      const chip = button('Parent ' + parent.slice(0, 7), 'chip parent', () => post({type: 'open-commit', hash: parent}));
      chip.title = parent; chips.append(chip);
    });
    if (chips.childElementCount) message.append(chips);
    if (detail.merge) message.append(el('div', 'banner', 'Merge commit - showing changes against the first parent ' + (detail.diff_base || '').slice(0, 7)));
    else if (!(detail.parents || []).length) message.append(el('div', 'banner', 'Root commit - showing the whole tree'));
    if (detail.truncated) message.append(el('div', 'banner', 'Some changes are omitted because this commit exceeds the display limit. Totals include all files.'));
    h.append(message); return h;
  }
  function toggleFile(m, collapse = !m.collapsed, immediate = true) {
    m.collapsed = collapse; m.section.classList.toggle('collapsed', collapse);
    m.head.setAttribute('aria-expanded', String(!collapse));
    m.clip.inert = collapse;
    if (!collapse && immediate) renderBody(m);
    if (!collapse && !m.rendered) observer && observer.observe(m.section);
    updateCollapseButton();
  }
  function fileSection(file, index, collapsed) {
    const section = el('section', 'file' + (collapsed ? ' collapsed' : ''));
    section.dataset.path = file.path; section.dataset.status = file.status;
    section.style.setProperty('--delay', Math.min(index * 12, 120) + 'ms');
    const head = el('div', 'file-header'); head.tabIndex = 0;
    head.setAttribute('role', 'button'); head.setAttribute('aria-expanded', String(!collapsed));
    head.setAttribute('aria-label', 'Toggle changes in ' + file.path);
    const chev = el('span', 'chevron'); chev.append(icon('chevron')); head.append(chev);
    const path = el('span', 'file-path');
    const slash = file.path.lastIndexOf('/');
    const basename = file.path.slice(slash + 1), dirname = file.path.slice(0, slash + 1);
    const name = file.old_path && file.old_path !== file.path ? file.old_path + ' → ' + file.path : basename;
    const changed = (file.hunks || []).flatMap(h => h.lines).find(l => l.t === '+' || l.t === '-');
    const open = button(name, 'file-name', e => {
      e.stopPropagation(); post({type: 'open-file', path: file.path, line: changed ? (changed.n || changed.o || 1) : 1});
    }, 'Open ' + file.path);
    path.append(open);
    if (dirname && !(file.old_path && file.old_path !== file.path)) path.append(el('span', 'directory', dirname));
    head.append(path, counts(file.additions, file.deletions));
    const body = el('div', 'file-body'), clip = el('div', 'body-clip');
    body.id = 'file-body-' + index; head.setAttribute('aria-controls', body.id);
    body.append(clip); section.append(head, body);
    const m = {file, section, head, body, clip, collapsed, rendered: false, gaps: [], context: new Map()};
    clip.inert = collapsed;
    if (!collapsed) {
      const estimate = (file.hunks || []).reduce((sum, h) => sum + h.lines.length, 0);
      const placeholder = el('div', 'body-placeholder'); placeholder.style.height = Math.max(45, estimate * theme.size * 1.7) + 'px';
      clip.append(placeholder);
    }
    head.addEventListener('click', () => toggleFile(m));
    head.addEventListener('keydown', e => {
      if (e.target !== head || !['Enter', ' '].includes(e.key)) return;
      e.preventDefault(); toggleFile(m);
    });
    head.addEventListener('focus', () => { focusedFile = index; });
    return m;
  }
  function paintCode(code, line, emphasis) {
    const chars = Array.from(line.text || '');
    const runs = (line.k || []).filter(r => r[0] < r[1]);
    const bounds = new Set([0, chars.length]);
    runs.forEach(r => { bounds.add(Math.max(0, Math.min(chars.length, r[0]))); bounds.add(Math.max(0, Math.min(chars.length, r[1]))); });
    if (emphasis) emphasis.forEach(p => bounds.add(p));
    const points = [...bounds].sort((a, b) => a - b);
    for (let i = 1; i < points.length; i++) {
      const start = points[i - 1], end = points[i];
      if (start === end) continue;
      const run = runs.find(r => start >= r[0] && start < r[1]);
      const marked = emphasis && start >= emphasis[0] && start < emphasis[1];
      const text = chars.slice(start, end).join('');
      if (run || marked) {
        const span = el('span', marked ? 'emphasis' : '', text);
        if (run) { span.classList.add('syntax'); span.dataset.kind = run[2]; span.style.color = 'var(--syntax-' + run[2] + ', var(--fg))'; }
        code.append(span);
      } else code.append(document.createTextNode(text));
    }
    if (!chars.length) code.append(document.createTextNode('\u200b'));
  }
  function row(line, emphasis, animated = false) {
    const cls = {'+': 'added', '-': 'removed', ' ': 'context', '\\': 'no-newline'}[line.t] || 'context';
    const r = el('div', 'diff-row ' + cls + (animated ? ' context-in' : ''));
    r.dataset.old = line.o == null ? '' : line.o; r.dataset.new = line.n == null ? '' : line.n;
    const old = el('span', 'gutter old-number', line.o == null ? '' : line.o);
    const next = el('span', 'gutter new-number', line.n == null ? '' : line.n);
    const marker = el('span', 'marker', line.t === '+' || line.t === '-' ? line.t : '');
    [old, next, marker].forEach(n => n.setAttribute('aria-hidden', 'true'));
    const code = el('pre', 'code'); paintCode(code, line, emphasis);
    r.append(old, next, marker, code); return r;
  }
  function lineGroup(lines, animated = false) {
    const fragment = document.createDocumentFragment(), emphasis = C.pairs(lines);
    if (!split) {
      lines.forEach((line, i) => fragment.append(row(line, emphasis.get(i), animated)));
      return fragment;
    }
    const pair = (left, right) => {
      const group = el('div', 'split-pair');
      group.append(left || el('div', 'split-empty'), right || el('div', 'split-empty')); fragment.append(group);
    };
    for (let i = 0; i < lines.length;) {
      if (lines[i].t === ' ' || lines[i].t === '\\') {
        pair(row(lines[i], null, animated), row(lines[i], null, animated)); i++; continue;
      }
      const removed = [], added = [];
      while (i < lines.length && lines[i].t === '-') removed.push(i++);
      while (i < lines.length && lines[i].t === '+') added.push(i++);
      for (let j = 0; j < Math.max(removed.length, added.length); j++) {
        const a = removed[j], b = added[j];
        pair(a === undefined ? null : row(lines[a], emphasis.get(a), animated), b === undefined ? null : row(lines[b], emphasis.get(b), animated));
      }
    }
    return fragment;
  }
  function requestContext(m, gap, direction) {
    if (gap.pending) return;
    const [start, end] = C.requestRange(gap, direction);
    const key = JSON.stringify([m.file.path, start, end]);
    if (!post({type: 'expand', path: m.file.path, start, end})) {
      gap.label.textContent = 'Open in IDgent to load context'; return;
    }
    gap.pending = true; gap.label.textContent = 'Loading context…';
    gap.bar.querySelectorAll('button').forEach(b => { b.disabled = true; });
    pending.set(key, {m, gap, start, end});
  }
  function updateGap(m, gap) {
    if (gap.end !== null && gap.start > gap.end || gap.exhausted) { gap.bar.remove(); return; }
    gap.bar.replaceChildren();
    const label = gap.end === null ? 'Show next 20 lines' : (gap.end - gap.start + 1).toLocaleString('en-US') + ' hidden lines';
    const up = button('↑', '', () => requestContext(m, gap, 'up'), 'Expand context above');
    const down = button('↓', '', () => requestContext(m, gap, 'down'), 'Expand context below');
    gap.label = button(label, 'gap-label', () => requestContext(m, gap, gap.before === 0 ? 'up' : 'down'));
    if (gap.end !== null) gap.bar.append(up);
    gap.bar.append(down, gap.label);
  }
  function gapNode(m, spec) {
    const gap = {...spec, pending: false, exhausted: false};
    gap.node = el('div', 'gap'); gap.bar = el('div', 'gap-bar'); gap.node.append(gap.bar);
    m.gaps.push(gap); updateGap(m, gap); return gap.node;
  }
  function renderBody(m) {
    if (m.rendered) return;
    // A layout switch rebuilds from the loaded-context model, without fetching again.
    const cached = m.gaps.map(g => ({...g, bar: null, node: null}));
    m.gaps = []; m.rendered = true; m.section.dataset.rendered = 'true';
    const fragment = document.createDocumentFragment(), file = m.file;
    if (file.mode_change) fragment.append(el('span', 'chip mode', 'mode ' + file.mode_change.join(' → ')));
    if (file.binary || file.too_large || !(file.hunks || []).length) {
      fragment.append(el('div', 'file-note', file.binary ? 'Binary file not shown' : file.too_large ?
        `Large diff not shown · +${file.additions} −${file.deletions}` : file.mode_change ? 'File permissions changed; contents are unchanged.' : 'No text changes in this file.'));
    } else {
      const diff = el('div', 'diff');
      const specs = file.status === 'deleted' ? [] : C.gaps(file.hunks);
      file.hunks.forEach((hunk, i) => {
        const spec = specs.find(g => g.before === i);
        if (spec) diff.append(gapNode(m, cached.find(g => g.before === i) || spec));
        diff.append(lineGroup(hunk.lines));
      });
      const tail = specs.find(g => g.before === file.hunks.length);
      if (tail) diff.append(gapNode(m, cached.find(g => g.before === file.hunks.length) || tail));
      // Keep fetched rows on either side of their gap when switching layouts.
      m.gaps.forEach(g => {
        const loaded = m.context.get(g.before);
        if (loaded) { g.node.prepend(lineGroup(loaded.top)); g.node.append(lineGroup(loaded.bottom)); }
        if (cached.find(old => old.before === g.before)?.exhausted) { g.exhausted = true; g.bar.remove(); }
      });
      for (const request of pending.values()) {
        if (request.m !== m) continue;
        const replacement = m.gaps.find(g => g.before === request.gap.before);
        if (replacement) {
          request.gap = replacement; replacement.pending = true; replacement.label.textContent = 'Loading context…';
          replacement.bar.querySelectorAll('button').forEach(b => { b.disabled = true; });
        }
      }
      fragment.append(diff);
    }
    m.clip.replaceChildren(fragment);
    observer && observer.unobserve(m.section);
  }
  function provideContext(path, start, end, lines) {
    const key = JSON.stringify([path, start, end]), request = pending.get(key);
    if (!request) return;
    pending.delete(key);
    const {m, gap} = request;
    gap.pending = false;
    const accepted = (Array.isArray(lines) ? lines : []).filter(l => Number.isInteger(l.n) && l.n >= start && l.n <= end)
      .sort((a, b) => a.n - b.n).map(l => ({...l, t: ' ', o: l.n + gap.offset}));
    const loaded = m.context.get(gap.before) || {top: [], bottom: []};
    if (start === gap.start) {
      loaded.top.push(...accepted); gap.node.insertBefore(lineGroup(accepted, true), gap.bar);
      gap.start = end + 1;
    } else {
      loaded.bottom.unshift(...accepted); gap.bar.after(lineGroup(accepted, true)); gap.end = start - 1;
    }
    if (gap.end === null && accepted.length < end - start + 1) gap.exhausted = true;
    m.context.set(gap.before, loaded); updateGap(m, gap);
  }
  function setCommit(data) {
    clear(); delete app.dataset.state; detail = data;
    if (!data || data.ok === false) { setState(data?.error === 'unknown-revision' ? 'not-found' : 'error', data?.error); return; }
    app.append(toolbar(), header());
    const files = el('div', 'files'); app.append(files);
    const list = data.files || [];
    const lineCount = list.reduce((sum, f) => sum + (f.hunks || []).reduce((n, h) => n + h.lines.length, 0), 0);
    const collapsed = list.length > 40 || lineCount > 4000;
    if ('IntersectionObserver' in window) observer = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        if (entry.isIntersecting) { const m = models.find(model => model.section === entry.target); if (m && !m.collapsed) renderBody(m); }
      });
    }, {rootMargin: '500px 0px'});
    const fragment = document.createDocumentFragment();
    list.forEach((file, i) => { const m = fileSection(file, i, collapsed); models.push(m); fragment.append(m.section); });
    files.append(fragment);
    models.forEach(m => { if (observer) observer.observe(m.section); else if (!m.collapsed) renderBody(m); });
    // Ensure meaningful first paint before the asynchronous visibility callback.
    if (models.length && !collapsed) renderBody(models[0]);
    if (!list.length) files.append(el('div', 'empty-files', 'This commit has no file changes.'));
    updateCollapseButton();
  }
  function setState(kind, message) {
    clear(); detail = null;
    app.dataset.state = kind;
    if (kind === 'loading') {
      const top = el('div', 'toolbar'); top.append(el('span', 'file-total', 'Loading commit…')); app.append(top);
      const skeleton = el('div', 'skeleton'); skeleton.setAttribute('role', 'status'); skeleton.setAttribute('aria-label', 'Loading commit');
      [['skeleton-avatar', ''], ['', '32%'], ['', '46%'], ['skeleton-title', ''], ['skeleton-file', ''],
        ['skeleton-code', '58%'], ['skeleton-code', '72%'], ['skeleton-code', '48%'], ['skeleton-file', ''], ['skeleton-code', '65%']].forEach(([cls, width]) => {
        const line = el('div', 'skeleton-line ' + cls); if (width) line.style.width = width; skeleton.append(line);
      }); app.append(skeleton); return;
    }
    const state = el('div', 'state'); state.setAttribute('role', 'status');
    const emblem = el('div', 'state-icon'); emblem.append(icon(kind === 'not-found' ? 'search' : 'error'));
    state.append(emblem, el('h1', '', kind === 'not-found' ? 'Commit not found' : 'Unable to load this commit'),
      el('p', '', message || (kind === 'not-found' ? 'It may no longer be available in this repository. Open the graph and choose another commit.' : 'The repository may have moved or Git could not read it. Refresh the commit to try again.')));
    if (message) state.append(el('p', 'state-hint', kind === 'not-found' ? 'Open the graph and choose another commit.' : 'Refresh the commit to try again.'));
    app.append(state);
  }
  // Resolve theme colours once, then keep code rows CSS-driven, including live theme changes.
  const canvas = document.createElement('canvas'), ctx = canvas.getContext('2d');
  function rgb(color) {
    ctx.fillStyle = '#000'; ctx.fillStyle = color;
    const value = ctx.fillStyle;
    if (value[0] === '#') return [1, 3, 5].map(i => parseInt(value.slice(i, i + 2), 16));
    return (value.match(/[\d.]+/g) || []).slice(0, 3).map(Number);
  }
  function luminance(color) {
    return rgb(color).map(x => x / 255).map(x => x <= .04045 ? x / 12.92 : ((x + .055) / 1.055) ** 2.4)
      .reduce((n, x, i) => n + x * [.2126, .7152, .0722][i], 0);
  }
  function contrast(a, b) { const x = luminance(a), y = luminance(b); return (Math.max(x, y) + .05) / (Math.min(x, y) + .05); }
  function styleAvatar(avatar) {
    const palette = [theme.accent, theme.add, theme.del, theme.modified || theme.syntax.number || theme.accent];
    avatar.style.backgroundColor = palette[Number(avatar.dataset.slot)];
    avatar.style.color = contrastInk(avatar.style.backgroundColor);
  }
  function contrastInk(background) { return contrast(theme.bg, background) > contrast(theme.fg, background) ? theme.bg : theme.fg; }
  function mix(a, b, t) { const x = rgb(a), y = rgb(b); return 'rgb(' + x.map((v, i) => Math.round(v + (y[i] - v) * t)).join(',') + ')'; }
  function tinted(tint) {
    const alpha = tint.match(/rgba\([^,]+,[^,]+,[^,]+,\s*([\d.]+)\s*\)/);
    return alpha ? mix(theme.bg, tint, Number(alpha[1])) : tint;
  }
  function legible(color) {
    const backgrounds = [theme.bg, tinted(theme.add_bg), tinted(theme.del_bg),
      mix(tinted(theme.add_bg), theme.add, .19), mix(tinted(theme.del_bg), theme.del, .19)];
    for (let i = 0; i <= 100; i++) {
      const candidate = mix(color, theme.fg, i / 100);
      if (backgrounds.every(bg => contrast(candidate, bg) >= 4.5)) return candidate;
    }
    return theme.fg;
  }
  function setTheme(colors = {}) {
    Object.assign(theme, defaults, colors); theme.syntax = colors.syntax || {};
    const names = {accent_fg: 'accent-fg', add_bg: 'add-bg', del_bg: 'del-bg'};
    ['bg', 'fg', 'muted', 'surface', 'surface2', 'border', 'hover', 'accent', 'accent_fg', 'add', 'del', 'add_bg', 'del_bg', 'mono', 'ui'].forEach(k => {
      root.style.setProperty('--' + (names[k] || k), theme[k]);
    });
    root.style.setProperty('--size', Math.max(9, Math.min(32, Number(theme.size) || 13)) + 'px');
    root.style.setProperty('--modified', theme.modified || theme.syntax.number || theme.accent);
    root.style.setProperty('--add-emphasis', mix(tinted(theme.add_bg), theme.add, .19));
    root.style.setProperty('--del-emphasis', mix(tinted(theme.del_bg), theme.del, .19));
    // Clear stale syntax variables when the new theme omits a kind.
    [...root.style].filter(k => k.startsWith('--syntax-')).forEach(k => root.style.removeProperty(k));
    Object.entries(theme.syntax).forEach(([kind, color]) => { if (/^[a-z]+$/.test(kind)) root.style.setProperty('--syntax-' + kind, legible(color)); });
    root.style.colorScheme = theme.dark ? 'dark' : 'light';
    document.querySelectorAll('.avatar').forEach(styleAvatar);
  }
  function setSettings(settings = {}) {
    if ('reduceMotion' in settings) {
      reduceMotion = !!settings.reduceMotion;
      root.classList.toggle('reduce-motion', reduceMotion); root.classList.toggle('allow-motion', !reduceMotion);
    }
  }
  document.addEventListener('keydown', e => {
    if (e.ctrlKey || e.metaKey || e.altKey || e.target.closest?.('input, textarea, [contenteditable="true"]') || !detail) return;
    if (e.key === 'c' && !window.getSelection().toString()) { e.preventDefault(); copySha(); }
    if (e.key !== 'j' && e.key !== 'k' || !models.length) return;
    e.preventDefault();
    focusedFile = Math.max(0, Math.min(models.length - 1, focusedFile + (e.key === 'j' ? 1 : -1)));
    const m = models[focusedFile]; m.head.focus({preventScroll: true});
    m.section.scrollIntoView({block: 'start', behavior: reduceMotion ? 'auto' : 'smooth'});
  });
  // Selection copying strips the non-code UI and gutter columns, including WebKit
  // versions where user-select:none text can still appear in Selection.toString().
  document.addEventListener('copy', e => {
    const selection = window.getSelection();
    if (!selection.rangeCount || selection.isCollapsed || !e.clipboardData) return;
    const range = selection.getRangeAt(0);
    const ancestor = range.commonAncestorContainer.nodeType === 1 ? range.commonAncestorContainer : range.commonAncestorContainer.parentElement;
    if (!ancestor.closest('.diff')) return;
    const fragment = range.cloneContents();
    const code = fragment.querySelectorAll('.code');
    if (code.length) {
      e.clipboardData.setData('text/plain', [...code].map(n => n.textContent.replace(/\u200b/g, '')).join('\n'));
      e.preventDefault();
    }
  });
  window.IDCommit = {setCommit, setState, setTheme, provideContext, setSettings};
  setTheme(); setState('loading'); post({type: 'ready'});
})();
