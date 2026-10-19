"""Bounded, read-only commit graph data; safe to import without a display."""

import os
import re
import selectors
import subprocess
import time
import unicodedata

TIMEOUT = 5.0
MAX_OUTPUT = 16 * 1024 * 1024
LOG_FORMAT = '%x00'.join(('%H', '%h', '%P', '%s', '%an', '%ae', '%at', '%ai', '%ct'))


class GitError(Exception):
    """A public error code rather than Git's locale-dependent stderr."""


def _run(root, args, deadline, cap=MAX_OUTPUT, partial=False):
    """Incrementally drain stdout, kill/reap on every exit, share a call deadline."""
    env = dict(os.environ, GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0',
               GIT_NO_LAZY_FETCH='1', LC_ALL='C', GIT_PAGER='cat')
    try:
        process = subprocess.Popen(
            ['git', '--no-pager', '-c', 'core.quotepath=off', '-C', os.fspath(root), *args],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
    except FileNotFoundError as exc:
        raise GitError('git-missing' if not exc.filename or exc.filename == 'git' else 'not-a-repo') from exc
    except (OSError, ValueError) as exc:
        raise GitError('not-a-repo') from exc
    output = bytearray()
    clipped = False
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise GitError('timeout')
                chunk = os.read(process.stdout.fileno(), min(65536, cap + 1 - len(output)))
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > cap:
                    clipped = True
                    process.kill()
                    break
        try:
            process.wait(timeout=max(.001, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as exc:
            raise GitError('timeout') from exc
        if clipped and not partial:
            raise GitError('output-too-large')
        if process.returncode and not clipped:
            raise GitError('git-failed')
        return bytes(output[:cap]), clipped
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()


def clean(value, cap=None, *, layout=False, path=False):
    """Replace invalid UTF-8; strip controls (escape them in paths to keep identity)."""
    if isinstance(value, bytes):
        value = value.decode('utf-8', 'replace')
    value = ''.join((f'\\u{ord(c):04x}' if path else '')
                    if unicodedata.category(c) in ('Cc', 'Cf', 'Cs')
                    and not (layout and c in '\n\r\t') else c for c in value)
    return value if cap is None or len(value) <= cap else value[:max(0, cap - 1)] + '…'


def timezone_minutes(date):
    offset = date.rsplit(' ', 1)[-1]
    if re.fullmatch(r'[+-]\d{4}', offset):
        return (1 if offset[0] == '+' else -1) * (int(offset[1:3]) * 60 + int(offset[3:]))
    return 0


def infer_merged_name(subject):
    """Infer a deleted side branch from conventional merge commit subjects."""
    subject = clean(subject, 4096)
    match = re.match(r'^Merge pull request #\d+ from [^/\s]+/(\S+)', subject)
    if match:
        return clean(match[1], 300)
    match = re.match(r'''^Merge (remote-tracking branch|branch|tag) (['"])(.*?)\2(?: into .*)?$''', subject)
    if match:
        name = match[3]
        if match[1] == 'remote-tracking branch':
            name = name.partition('/')[2] or name
        return clean(name, 300) or None
    match = re.match(r'^Merge (ceo/[^\s:]+):', subject)
    return clean(match[1], 300) if match else None


def parse_log(data):
    """Parse fixed-width NUL fields; message/identity controls cannot split records."""
    fields = data.split(b'\0')
    commits = []
    for offset in range(0, len(fields) - 8, 9):
        row = fields[offset:offset + 9]
        if not re.fullmatch(b'[0-9a-f]{40}', row[0]):
            continue
        sha, short, parents, subject, author, email, ts, date, cts = row
        parents = parents.decode('ascii').split()
        commits.append(dict(hash=sha.decode(), short=short.decode(), parents=parents,
                            subject=clean(subject, 300), author=clean(author, 120), email=clean(email, 120),
                            ts=int(ts), tz=timezone_minutes(date.decode()), cts=int(cts), refs=[],
                            merged_name=infer_merged_name(subject) if len(parents) >= 2 else None))
    return commits


def parse_refs(data, branch=None):
    refs, default = {}, None
    for row in data.split(b'\n'):
        fields = row.split(b'\0')
        if len(fields) != 5:
            continue
        raw, sha, peeled, object_type, target = (s.decode('utf-8', 'replace') for s in fields)
        if raw == 'refs/remotes/origin/HEAD' and target.startswith('refs/remotes/'):
            default = clean(target[len('refs/remotes/'):])
        if raw.startswith('refs/heads/'):
            name, kind = raw[11:], 'branch'
        elif raw.startswith('refs/remotes/') and not raw.endswith('/HEAD'):
            name, kind = raw[13:], 'remote'
        elif raw.startswith('refs/tags/'):
            name, kind = raw[10:], 'tag'
        else:
            continue
        if object_type != 'commit' and not peeled:
            continue
        ref = dict(name=clean(name), kind=kind)
        if kind == 'branch' and name == branch:
            ref['current'] = True
        refs.setdefault(peeled or sha, []).append(ref)
    names = {r['name'] for rows in refs.values() for r in rows if r['kind'] == 'branch'}
    return refs, default or next((n for n in ('main', 'master', branch) if n and n in names), branch)


def read_refs(root, deadline, branch=None):
    data, _ = _run(root, ['for-each-ref', '--format=%(refname)%00%(objectname)%00%(*objectname)%00%(objecttype)%00%(symref)',
                          'refs/heads', 'refs/remotes', 'refs/tags'], deadline)
    return parse_refs(data, branch)


def _dirty(root, deadline):
    try:
        data, clipped = _run(root, ['status', '--porcelain=v1', '-z', '--untracked-files=all', '--ignored=no'],
                             deadline, cap=2 * 1024 * 1024, partial=True)
        if clipped:
            return None
        records, count, index = data.split(b'\0')[:-1], 0, 0
        while index < len(records):
            code = records[index][:2]
            count += 1
            index += 2 if b'R' in code or b'C' in code else 1
        return {'changed': count}
    except GitError:
        return None


def collect_graph(root, limit=1500):
    """Return graph JSON in topo order, walking only branches, remotes, tags and HEAD."""
    deadline = time.monotonic() + TIMEOUT
    root = os.path.abspath(root)
    result = dict(ok=False, error=None, repo=dict(name=clean(os.path.basename(root)), root=clean(root, path=True),
                  default_branch=None), head=dict(hash=None, branch=None, detached=False), dirty=None,
                  total=0, truncated=False, commits=[])
    try:
        try:
            top, _ = _run(root, ['rev-parse', '--show-toplevel'], deadline)
        except GitError as exc:
            raise GitError('not-a-repo' if str(exc) == 'git-failed' else str(exc)) from exc
        actual_root = os.fsdecode(top.rstrip(b'\n'))
        result['repo'].update(name=clean(os.path.basename(actual_root)), root=clean(actual_root, path=True))
        try:
            branch, _ = _run(root, ['symbolic-ref', '--quiet', '--short', 'HEAD'], deadline)
            branch = branch.rstrip(b'\n').decode('utf-8', 'replace')
        except GitError as exc:
            if str(exc) != 'git-failed':
                raise
            branch = None
        result['head'].update(branch=clean(branch) if branch else None, detached=branch is None)
        refs, default = read_refs(root, deadline, branch)
        result['repo']['default_branch'] = default
        try:
            sha, _ = _run(root, ['rev-parse', '--verify', 'HEAD^{commit}'], deadline)
        except GitError as exc:
            raise GitError('empty' if str(exc) == 'git-failed' else str(exc)) from exc
        result['head']['hash'] = sha.strip().decode('ascii')
        limit = max(1, min(int(limit), 10000))
        data, _ = _run(root, ['log', '--topo-order', '-n', str(limit), '--branches', '--remotes', '--tags',
                              'HEAD', '-z', '--encoding=UTF-8', '--format=' + LOG_FORMAT, '--'], deadline)
        result['commits'] = parse_log(data)
        for commit in result['commits']:
            commit['refs'] = refs.get(commit['hash'], [])
        result['total'] = len(result['commits'])
        try:
            count, _ = _run(root, ['rev-list', '--count', '--branches', '--remotes', '--tags', 'HEAD', '--'],
                            min(deadline, time.monotonic() + .75), cap=128)
            result['total'] = max(result['total'], int(count))
        except GitError:
            # At least one unseen parent proves truncation even when counting is too expensive.
            shown = {c['hash'] for c in result['commits']}
            result['total'] += int(any(p not in shown for c in result['commits'] for p in c['parents']))
        result['truncated'] = result['total'] > len(result['commits'])
        result['dirty'] = _dirty(root, deadline)
        result.update(ok=True)
    except GitError as exc:
        result['error'] = str(exc)
    return result
