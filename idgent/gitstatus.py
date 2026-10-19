"""Bounded, display-independent Git decorations for a filesystem explorer."""

from dataclasses import dataclass, field
import os
import selectors
import subprocess
import time

MAX_ENTRIES = 20_000
MAX_OUTPUT = 16 * 1024 * 1024
TIMEOUT = 5.0
_PRIORITY = {'new': 1, 'modified': 2, 'conflict': 3}
_CONFLICTS = {'UU', 'AA', 'DD', 'AU', 'UA', 'DU', 'UD'}


@dataclass(frozen=True)
class Status:
    kind: str
    badge: str = ''


@dataclass
class Snapshot:
    files: dict[str, Status] = field(default_factory=dict)
    folders: dict[str, Status] = field(default_factory=dict)
    git_dir: str | None = None
    truncated: bool = False


def _run(root, args, deadline, record_limit=None):
    """Read a pipe incrementally so even pathological status output is bounded."""
    env = dict(os.environ, GIT_OPTIONAL_LOCKS='0', LC_ALL='C')
    process = subprocess.Popen(['git', '-C', root, *args], stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, env=env)
    output = bytearray()
    records = 0
    truncated = False
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(process.args, TIMEOUT)
                if not selector.select(remaining):
                    raise subprocess.TimeoutExpired(process.args, TIMEOUT)
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    break
                output.extend(chunk)
                records += chunk.count(b'\0')
                if len(output) >= MAX_OUTPUT or (record_limit and records >= record_limit):
                    truncated = True
                    process.kill()
                    break
        process.wait(timeout=max(.001, deadline - time.monotonic()))
        if process.returncode and not truncated:
            raise subprocess.CalledProcessError(process.returncode, process.args)
        return bytes(output), truncated
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()


def parse_porcelain(data: bytes, repo: str, root: str, max_entries=MAX_ENTRIES) -> Snapshot:
    """Parse v1 -z (rename destination first), including unloaded descendants."""
    result = Snapshot()
    records = iter(data.split(b'\0'))
    count = 0
    for record in records:
        if not record:
            continue
        if count >= max_entries:
            result.truncated = True
            break
        count += 1
        if len(record) < 4 or record[2:3] != b' ':
            continue
        code = record[:2].decode('ascii', errors='replace')
        path = os.path.abspath(os.path.join(repo, os.fsdecode(record[3:])))
        source = None
        if 'R' in code or 'C' in code:
            source = os.path.abspath(os.path.join(repo, os.fsdecode(next(records, b''))))
        if code in _CONFLICTS:
            status = Status('conflict', '!')
        elif code == '??':
            status = Status('new', 'U')
        elif 'D' in code:
            status = Status('modified')
        elif 'R' in code or 'C' in code:
            status = Status('modified', 'R' if 'R' in code else 'M')
        elif 'M' in code or 'T' in code:
            status = Status('modified', 'M')
        elif 'A' in code:
            status = Status('new', 'A')
        else:
            continue
        # Deleted paths contribute to folders, but have no file decoration.
        if os.path.commonpath((root, path)) == root:
            if 'D' not in code or code in _CONFLICTS:
                result.files[path] = status
            _aggregate(result, path, root, status.kind)
        # A rename also modifies its old parent, including moves out of root.
        if source and os.path.commonpath((root, source)) == root:
            _aggregate(result, source, root, 'modified')
    return result


def _aggregate(result, path, root, kind):
    parent = os.path.dirname(path)
    while os.path.commonpath((root, parent)) == root:
        previous = result.folders.get(parent)
        if previous is None or _PRIORITY[kind] > _PRIORITY[previous.kind]:
            result.folders[parent] = Status(kind)
        if parent == root:
            break
        parent = os.path.dirname(parent)


def collect_status(root: str, *, max_entries=MAX_ENTRIES) -> Snapshot:
    """Return empty on missing Git/repo, errors or timeout; never touch GTK."""
    root = os.path.abspath(root)
    deadline = time.monotonic() + TIMEOUT
    try:
        top, _ = _run(root, ['rev-parse', '--show-toplevel'], deadline)
        repo = os.fsdecode(top.rstrip(b'\n'))
        git_dir, _ = _run(root, ['rev-parse', '--absolute-git-dir'], deadline)
        output, truncated = _run(
            root, ['status', '--porcelain=v1', '-z', '--untracked-files=all', '--ignored=no'],
            deadline, record_limit=max(1, max_entries) * 2 + 1)
        result = parse_porcelain(output, repo, root, max_entries)
        result.git_dir = os.fsdecode(git_dir.rstrip(b'\n'))
        result.truncated |= truncated
        return result
    except (OSError, subprocess.SubprocessError, ValueError):
        return Snapshot()
