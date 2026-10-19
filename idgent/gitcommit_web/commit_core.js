/* Pure helpers shared by the page and node tests. Character offsets are Unicode
 * code points, matching Python's highlight runs (rather than UTF-16 offsets). */
(function (root) {
  'use strict';
  function dateLabel(ts, now = new Date()) {
    const d = new Date(ts * 1000);
    const day = x => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
    const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
    const date = day(d) === day(now) ? 'Today' : day(d) === day(yesterday) ? 'Yesterday' :
      d.toLocaleDateString('en-US', {month: 'short', day: 'numeric', year: 'numeric'});
    return date + ' at ' + d.toLocaleTimeString('en-US', {hour: 'numeric', minute: '2-digit'});
  }
  function fullDate(ts, tz = 0) {
    const d = new Date((ts + tz * 60) * 1000);
    const pad = v => String(v).padStart(2, '0');
    const offset = (tz < 0 ? '-' : '+') + pad(Math.floor(Math.abs(tz) / 60)) + ':' + pad(Math.abs(tz) % 60);
    return d.toISOString().slice(0, 19).replace('T', ' ') + ' ' + offset;
  }
  function changedRange(oldText, newText) {
    const a = Array.from(oldText), b = Array.from(newText);
    let prefix = 0, suffix = 0;
    while (prefix < Math.min(a.length, b.length) && a[prefix] === b[prefix]) prefix++;
    while (suffix < Math.min(a.length, b.length) - prefix && a[a.length - 1 - suffix] === b[b.length - 1 - suffix]) suffix++;
    return [[prefix, a.length - suffix], [prefix, b.length - suffix]];
  }
  function oldOffset(hunk) {
    return hunk.old_start + hunk.old_lines - hunk.new_start - hunk.new_lines;
  }
  function gaps(hunks) {
    if (!hunks.length) return [];
    const out = [];
    let start = 1, offset = hunks[0].old_start - hunks[0].new_start;
    hunks.forEach((h, index) => {
      const end = h.new_start - 1;
      if (end >= start) out.push({before: index, start, end, offset});
      start = Math.max(1, h.new_start + h.new_lines);
      offset = oldOffset(h);
    });
    out.push({before: hunks.length, start, end: null, offset});
    return out;
  }
  function requestRange(gap, direction) {
    if (gap.end === null) return [gap.start, gap.start + 19];
    if (gap.end - gap.start < 40) return [gap.start, gap.end];
    return direction === 'up' ? [Math.max(gap.start, gap.end - 19), gap.end] :
      [gap.start, Math.min(gap.end, gap.start + 19)];
  }
  function pairs(lines) {
    const ranges = new Map();
    for (let i = 0; i < lines.length;) {
      if (lines[i].t !== '-') { i++; continue; }
      const removed = [], added = [];
      while (i < lines.length && lines[i].t === '-') removed.push(i++);
      while (i < lines.length && lines[i].t === '+') added.push(i++);
      for (let j = 0; j < Math.min(removed.length, added.length); j++) {
        const range = changedRange(lines[removed[j]].text, lines[added[j]].text);
        ranges.set(removed[j], range[0]); ranges.set(added[j], range[1]);
      }
    }
    return ranges;
  }
  const api = {dateLabel, fullDate, changedRange, oldOffset, gaps, requestRange, pairs};
  root.CommitCore = api;
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window === 'undefined' ? globalThis : window);
