"""Read-only commit changes and bounded file context, independent of GTK APIs."""

from functools import lru_cache
import bisect
import importlib
import os
import re
import time

from .gitlog import GitError, MAX_OUTPUT, TIMEOUT, _run, clean, read_refs, timezone_minutes

_METADATA = '%x00'.join(('%H', '%h', '%P', '%s', '%b', '%an', '%ae', '%at', '%ai',
                            '%cn', '%ce', '%ct', '%ci'))
_DIFF_OPTIONS = ['-r', '--no-commit-id', '--root', '-M', '-C', '--find-copies-harder',
                 '--no-color', '--no-ext-diff', '--no-textconv', '--ignore-submodules=none',
                 '--submodule=short', '--src-prefix=a/', '--dst-prefix=b/', '--no-relative',
                 '--inter-hunk-context=0']
_HUNK = re.compile(rb'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: ?(.*))?$')
_STATUS = dict(A='added', M='modified', D='deleted', R='renamed', C='copied', T='typechange')


@lru_cache(maxsize=1)
def _highlight():
    """The app highlighter imports GTK; never import it until syntax is requested."""
    try:
        module = importlib.import_module('idgent.highlight')
        return module if module.AVAILABLE else None
    except Exception:
        return None


def _syntax(path):
    module = _highlight()
    try:
        lexer = module.lexer_for(path) if module and module.AVAILABLE else None
        return module, lexer, module.language_name(lexer) if lexer is not None else None
    except Exception:
        return None, None, None


def _paint(lines, module, lexer):
    """Tokenise one side of a hunk and intersect runs with its individual lines."""
    if not module or lexer is None or not lines:
        return
    starts, position = [], 0
    for line in lines:
        starts.append(position)
        position += len(line['text']) + 1
    try:
        for start, end, kind in module.classify(lexer, '\n'.join(line['text'] for line in lines)):
            index = max(0, bisect.bisect_right(starts, start) - 1)
            while index < len(lines) and starts[index] < end:
                lo = max(0, start - starts[index])
                hi = min(len(lines[index]['text']), end - starts[index])
                if lo < hi:
                    lines[index]['k'].append([lo, hi, kind])
                index += 1
    except Exception:
        for line in lines:
            line['k'] = []


def highlight_hunks(file):
    module, lexer, file['language'] = _syntax(file['path'])
    for hunk in file['hunks']:
        # Context uses new-side classification, never a union of conflicting runs.
        old = [dict(line, k=[]) for line in hunk['lines'] if line['t'] in (' ', '-')]
        _paint(old, module, lexer)
        removed = iter(line for line in old if line['t'] == '-')
        for line in hunk['lines']:
            if line['t'] == '-':
                line['k'] = next(removed)['k']
        _paint([line for line in hunk['lines'] if line['t'] in (' ', '+')], module, lexer)


def _path(data):
    # Filenames are identifiers: keep their exact Unicode spelling, including LF/TAB.
    # JSON transport escapes these characters; display text is sanitised separately.
    return data.decode('utf-8', 'replace')


def parse_changes(data):
    """Parse diff-tree --raw --numstat -z; never infer paths from patch headers."""
    records, index, files = data.split(b'\0'), 0, []
    while index < len(records) and records[index].startswith(b':'):
        header = records[index][1:].split()
        old_mode, mode, _old_sha, _sha, status = header
        index += 1
        old_path = None
        path = records[index]
        index += 1
        if status[:1] in (b'R', b'C'):
            old_path, path = path, records[index]
            index += 1
        files.append(dict(path=_path(path), old_path=_path(old_path) if old_path is not None else None,
                          status=_STATUS.get(chr(status[0]), 'modified'),
                          similarity=int(status[1:]) if status[1:] else None,
                          mode_change=[old_mode.decode(), mode.decode()] if old_mode != mode else None,
                          binary=False, additions=0, deletions=0, too_large=False, language=None, hunks=[]))
    by_path = {file['path']: file for file in files}
    while index < len(records):
        row = records[index]
        index += 1
        if not row:
            continue
        counts = row.split(b'\t', 2)
        if len(counts) != 3:
            continue
        added, deleted, path = counts
        if not path:  # rename/copy: empty pathname, then old and new NUL records
            index += 1
            path = records[index]
            index += 1
        file = by_path.get(_path(path))
        if file is not None:
            file.update(binary=added == b'-' or deleted == b'-',
                        additions=0 if added == b'-' else int(added),
                        deletions=0 if deleted == b'-' else int(deleted))
    return files


def parse_patch(data, max_lines=3000):
    """Parse a single file patch; preserve tabs/CR and explicit no-newline markers."""
    hunks, hunk, old, new, line_count, clipped = [], None, 0, 0, 0, False
    for row in data.split(b'\n'):
        if row.startswith(b'diff --git '):
            hunk = None
            continue
        match = _HUNK.match(row)
        if match:
            old, new = int(match[1]), int(match[3])
            hunk = dict(old_start=old, old_lines=int(match[2] or 1), new_start=new,
                        new_lines=int(match[4] or 1), section=clean(match[5] or b''), lines=[])
            hunks.append(hunk)
            continue
        if hunk is None or row[:1] not in (b' ', b'+', b'-', b'\\'):
            continue
        kind = chr(row[0])
        if kind == '\\' and not row.startswith(b'\\ No newline at end of file'):
            continue
        line_count += 1
        if line_count > max_lines:
            return [], True, clipped
        text = clean(row[1:], layout=True) if kind != '\\' else clean(row)
        line = dict(t=kind, o=old if kind in (' ', '-') else None,
                    n=new if kind in (' ', '+') else None, text=text, k=[])
        if len(text) > 2000:
            line.update(text=text[:1999] + '…', cut=True)
            clipped = True
        hunk['lines'].append(line)
        old += kind in (' ', '-')
        new += kind in (' ', '+')
    return hunks, False, clipped


def _resolve(root, rev, deadline):
    if not isinstance(rev, str) or not rev or rev.startswith('-') or any(c in rev for c in '\0\n\r'):
        raise GitError('unknown-revision')
    try:
        data, _ = _run(root, ['rev-parse', '--verify', '--end-of-options', rev + '^{commit}'], deadline, cap=256)
        sha = data.strip().decode('ascii')
        if not re.fullmatch('[0-9a-f]{40}', sha):
            raise GitError('unknown-revision')
        return sha
    except GitError as exc:
        raise GitError('unknown-revision' if str(exc) == 'git-failed' else str(exc)) from exc


def _check_repo(root, deadline):
    try:
        _run(root, ['rev-parse', '--git-dir'], deadline, cap=65536)
    except GitError as exc:
        raise GitError('not-a-repo' if str(exc) == 'git-failed' else str(exc)) from exc


def commit_detail(root, rev, *, max_files=300, max_lines_per_file=3000, max_bytes=8 * 1024 * 1024):
    """Describe a commit against its first parent (or the empty root tree)."""
    deadline = time.monotonic() + TIMEOUT
    try:
        _check_repo(root, deadline)
        sha = _resolve(root, rev, deadline)
        data, _ = _run(root, ['show', '--no-patch', '--encoding=UTF-8', '--format=' + _METADATA, sha, '--'], deadline)
        fields = data.rstrip(b'\n').split(b'\0')
        (sha_b, short, parents, subject, body, an, ae, at, ai, cn, ce, ct, ci) = fields
        parents = parents.decode('ascii').split()
        refs, _ = read_refs(root, deadline)
        result = dict(ok=True, error=None, hash=sha_b.decode(), short=short.decode(), parents=parents,
                      subject=clean(subject, 300), body=clean(body, layout=True).strip(),
                      author=dict(name=clean(an, 120), email=clean(ae, 120), ts=int(at), tz=timezone_minutes(ai.decode())),
                      committer=dict(name=clean(cn, 120), email=clean(ce, 120), ts=int(ct), tz=timezone_minutes(ci.decode())),
                      refs=refs.get(sha, []), merge=len(parents) >= 2, diff_base=parents[0] if parents else None,
                      stats={}, truncated=False, files=[])
        revisions = [parents[0], sha] if parents else [sha]
        raw, _ = _run(root, ['diff-tree', *_DIFF_OPTIONS, '--raw', '--numstat', '-z', *revisions, '--'], deadline)
        files = parse_changes(raw)
        result['stats'] = dict(files=len(files), additions=sum(f['additions'] for f in files),
                               deletions=sum(f['deletions'] for f in files))
        max_files, max_lines_per_file = max(0, min(int(max_files), 10000)), max(0, min(int(max_lines_per_file), 100000))
        max_bytes = max(0, min(int(max_bytes), MAX_OUTPUT))
        result['files'] = files[:max_files]
        result['truncated'] = len(files) > max_files
        if not result['files']:
            return result
        # One bounded patch process. Its order is the same raw-tree order; path headers
        # may be quoted, so associate by index instead of parsing human path spellings.
        patch, clipped = _run(root, ['diff-tree', *_DIFF_OPTIONS, '-p', '-U3', '-z', *revisions, '--'],
                              deadline, cap=max_bytes, partial=True)
        sections = re.split(rb'(?m)^diff --git ', patch)[1:]
        index = 0
        for file in result['files']:
            # Git emits a deletion AND an addition patch for one raw T record.
            # Treat both as one file; otherwise every following patch shifts a slot.
            width = 2 if file['status'] == 'typechange' else 1
            end = index + width
            incomplete = end > len(sections) or (clipped and end >= len(sections))
            if incomplete and not file['binary']:
                file['too_large'] = True
            elif not file['binary']:
                content = b''.join(b'diff --git ' + section for section in sections[index:end])
                file['hunks'], file['too_large'], cut = parse_patch(content, max_lines_per_file)
                result['truncated'] |= cut
            result['truncated'] |= file['too_large']
            highlight_hunks(file)
            index = end
        result['truncated'] |= clipped
        return result
    except GitError as exc:
        return dict(ok=False, error=str(exc))


def file_context(root, rev, path, start, end):
    """Return at most 200 new-side lines of a validated tree blob."""
    start = max(1, int(start))
    end = max(start, min(int(end), start + 199))
    result = dict(ok=False, path=path, start=start, end=end, lines=[])
    deadline = time.monotonic() + TIMEOUT
    try:
        if (not isinstance(path, str) or not path or '\0' in path or os.path.isabs(path)
                or any(part in ('..', '.', '') for part in path.split('/'))):
            raise GitError('missing-path')
        _check_repo(root, deadline)
        sha = _resolve(root, rev, deadline)
        tree, _ = _run(root, ['ls-tree', '-z', '--full-tree', sha, '--', ':(literal)' + path], deadline)
        entries = tree.split(b'\0')[:-1]
        match = next((row.split(b'\t', 1)[0].split() for row in entries
                      if row.split(b'\t', 1)[1] == path.encode('utf-8')), None)
        if not match or match[1] != b'blob':
            raise GitError('missing-path')
        # Resolve via tree object ID, so colon syntax, symlinks and pathspecs cannot escape.
        blob, _ = _run(root, ['cat-file', 'blob', match[2].decode('ascii')], deadline, cap=8 * 1024 * 1024)
        if b'\0' in blob:
            raise GitError('binary')
        lines = blob.split(b'\n')
        if lines and lines[-1] == b'':
            lines.pop()
        last = min(end, len(lines))
        result.update(ok=True, end=last, lines=[dict(n=i + 1, text=clean(lines[i], 2000, layout=True), k=[])
                                               for i in range(start - 1, last)])
        module, lexer, _ = _syntax(path)
        _paint(result['lines'], module, lexer)
    except GitError as exc:
        result['error'] = str(exc)
    return result
