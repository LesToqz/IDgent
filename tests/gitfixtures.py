"""Deterministic real Git repositories for tests and the Git graph demo.

Run ``python3 tests/gitfixtures.py <empty-directory>`` to build the showcase.
Builders return a GitRepo; all Git state and content remain inside that directory.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import shutil
import subprocess
import sys


class GitRepo:
    def __init__(self, path, *, init=True):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.tick = 0
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1', GIT_TERMINAL_PROMPT='0',
                        GIT_AUTHOR_NAME='Ada Example', GIT_AUTHOR_EMAIL='ada@example.test',
                        GIT_COMMITTER_NAME='Grace Example', GIT_COMMITTER_EMAIL='grace@example.test',
                        LC_ALL='C', GIT_DEFAULT_HASH='sha1')
        if init:
            self.git('init', '--initial-branch=master', '.')
            self.git('config', 'user.name', 'Ada Example')
            self.git('config', 'user.email', 'ada@example.test')
            self.git('config', 'commit.gpgsign', 'false')
            self.git('config', 'core.autocrlf', 'false')

    def git(self, *args, input=None, env=None):
        command = ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgsign=false',
                   '-c', 'tag.gpgsign=false', '-c', 'init.defaultBranch=master', '-C', str(self.path), *args]
        result = subprocess.run(command, input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=dict(self.env, **(env or {})), check=True)
        return result.stdout.decode('utf-8', 'replace').strip()

    def _dated(self, date=None, author=None):
        stamp = date or (datetime(2025, 1, 1, 12, tzinfo=timezone.utc) + timedelta(hours=30 * self.tick)).isoformat()
        self.tick += 1
        env = dict(GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp)
        if author:
            name, email = author if isinstance(author, tuple) else (author, 'author@example.test')
            env.update(GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=email)
        return env

    def commit(self, files=None, message='Commit', date=None, author=None):
        for name, value in (files or {}).items():
            path = self.path / name
            if value is None:
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                elif path.exists() or path.is_symlink():
                    path.unlink()
                continue
            mode = None
            if isinstance(value, tuple):
                value, mode = value
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_symlink():
                path.unlink()
            path.write_bytes(value.encode('utf-8') if isinstance(value, str) else value)
            if mode is not None:
                path.chmod(mode)
        self.git('add', '--all')
        self.git('commit', '--allow-empty', '-F', '-', input=message.encode('utf-8'), env=self._dated(date, author))
        return self.git('rev-parse', 'HEAD')

    def branch(self, name, rev='HEAD'):
        self.git('branch', name, rev)
        return self

    def checkout(self, name, *, create=False):
        self.git('checkout', *(['-b'] if create else []), name)
        return self

    def merge(self, *branches, message=None, date=None):
        self.git('merge', '--no-ff', '-m', message or "Merge branch '" + branches[0] + "'", *branches,
                 env=self._dated(date))
        return self.git('rev-parse', 'HEAD')

    def tag(self, name, rev='HEAD', *, annotated=False):
        args = ['tag', '-a', name, '-m', name, rev] if annotated else ['tag', name, rev]
        self.git(*args, env=self._dated())
        return self


def build_linear(path, count=5):
    repo = GitRepo(path)
    for i in range(count):
        repo.commit({'main.py': f'def value():\n    return {i}\n'}, message=f'Linear commit {i + 1}')
    return repo


def build_octopus(path):
    repo = build_linear(path, 2)
    for name in ('feature/auth', 'feature/api'):
        repo.checkout(name, create=True)
        repo.commit({name + '.py': 'ENABLED = True\n'}, message='Add ' + name)
        repo.checkout('master')
    repo.merge('feature/auth', 'feature/api', message='Merge pull request #42 from acme/feature/auth')
    return repo


def build_detached(path):
    repo = build_linear(path)
    repo.git('checkout', '--detach', 'HEAD~1')
    repo.commit({'detached.txt': 'Only reachable from HEAD\n'}, message='Detached work')
    return repo


def build_clone_with_remote(path):
    path = Path(path)
    source = build_linear(path / 'source', 3)
    source.tag('v1', annotated=True)
    source.git('clone', '--no-local', str(source.path.resolve()), str((path / 'clone').resolve()))
    return GitRepo(path / 'clone', init=False)


def build_deleted_branches(path):
    repo = build_linear(path, 2)
    for slug in ('pane-split', 'explorer-no-badges', 'git-data'):
        name = 'ceo/codex/' + slug
        repo.checkout(name, create=True)
        repo.commit({slug + '.py': 'VALUE = "side branch"\n'}, message='Implement ' + slug)
        repo.checkout('master')
        repo.merge(name, message='Merge ' + name + ': implementation and tests')
        repo.git('branch', '-D', name)
    return repo


def build_showcase(path):
    repo = GitRepo(path)
    app = ('#!/usr/bin/env python3\n"""A small demo application.\n\n'
           'The graph shows the story behind each change.\n"""\n\n'
           'def greet(name):\n    return f"Hello, {name}"\n\n' +
           ''.join(f'# context line {i}\n' for i in range(24)) +
           '\ndef main():\n    print(greet("world"))\n\nmain()\n')
    repo.commit({'app.py': app, 'README.md': '# Acme workspace\n\nA graph demo.\n',
                 'obsolete.txt': 'Remove this after the migration.\n', 'empty.txt': '',
                 'docs/Привет мир.md': '# Привет, мир 🌍\n', 'no-newline.txt': 'last line',
                 'windows.txt': b'first\r\nsecond\r\n',
                 'public/icon.png': b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01',
                 'package.json': '{"name": "acme", "version": "0.1.0"}\n'}, message='Create workspace')
    repo.commit({'README.md': '# Acme workspace\n\nExplore commits, branches and changes.\n'}, message='Document the project')
    repo.tag('v0.1')
    repo.branch('feature/auth')
    repo.commit({'config.json': '{\n  "theme": "dark",\n  "graph": true\n}\n'}, message='Configure graph defaults')
    repo.branch('feature/api')
    repo.commit({'app.py': app.replace('Hello,', 'Welcome,').replace('greet("world")', 'greet("Acme")')},
                message='Improve greetings and startup')
    repo.checkout('feature/auth')
    for i, title in enumerate(('Add sign in', 'Validate sessions', 'Add access checks')):
        repo.commit({'auth.py': f'"""Authentication helpers.\nSession implementation {i}.\n"""\n\ndef allowed(user):\n    return bool(user)\n'}, message=title)
    repo.checkout('feature/api')
    for i, title in enumerate(('Create API client', 'Add retries', 'Handle API errors', 'Document endpoints')):
        repo.commit({'src/api.js': f'// API client revision {i}\nexport async function fetchItems() {{\n  return fetch("/api/items");\n}}\n'}, message=title)
    repo.checkout('master')
    repo.commit({'obsolete.txt': None, 'docs/guide.md': '# Migration\n\nUse the new client.\nExplore branches and inspect commits in the graph.\nThe commit tab shows source changes and syntax colours.\n'}, message='Remove legacy setup')
    repo.merge('feature/auth', 'feature/api', message='Merge pull request #42 from acme/feature/auth')
    repo.commit({'app.py': (repo.path.joinpath('app.py').read_bytes(), 0o755)}, message='Make the launcher executable')
    repo.branch('feature/ui')
    repo.commit({'no-newline.txt': 'still no final newline', 'windows.txt': b'first\r\nupdated\r\nthird\r\n'},
                message='Support mixed line endings')
    repo.checkout('feature/ui')
    for i, title in enumerate(('Add graph palette', 'Style commit cards', 'Polish graph controls')):
        repo.commit({'src/graph.css': f':root {{ --accent: #7c8cff; }}\n.commit {{ padding: {8 + i}px; color: white; }}\n',
                     'src/view.js': f'export const zoom = {i + 1};\n'}, message=title)
    repo.checkout('master')
    repo.branch('hotfix/critical')
    repo.merge('feature/ui', message="Merge branch 'feature/ui'")
    repo.tag('v0.2', annotated=True)
    old = repo.path.joinpath('docs/guide.md').read_text()
    repo.commit({'docs/guide.md': None, 'docs/getting-started.md': old + '\nStart with the graph view.\n'}, message='Rename the getting started guide')
    repo.commit({'package.json': '{"name": "acme", "version": "0.2.0"}\n'}, message='Publish package metadata')
    repo.branch('experiment/graph-polish')
    repo.commit({'src/layout.json': '{"lanes": "compact", "note": "' + 'x' * 350 + '"}\n'}, message='Tune graph spacing')
    repo.checkout('hotfix/critical')
    for i, title in enumerate(('Reproduce timeout', 'Bound worker requests', 'Handle cancellation', 'Cover timeout regression')):
        repo.commit({'reliability.py': f'"""Bound network work."""\nTIMEOUT = {5 - i}\n\ndef ready():\n    return True\n'}, message=title)
    repo.checkout('master')
    repo.merge('hotfix/critical', message='Merge pull request #47 from acme/hotfix/critical')
    repo.commit({'README.md': '# Acme workspace\n\nVersion 0.2 is ready.\n'}, message='Prepare the release')
    repo.checkout('experiment/graph-polish')
    for i in range(2):
        repo.commit({'src/experiment.js': f'export const smoothLanes = {i + 1};\n'}, message=f'Experiment with graph polish {i + 1}')
    repo.checkout('master')
    with (repo.path / 'README.md').open('a') as stream:
        stream.write('\nUncommitted release notes.\n')
    with (repo.path / 'app.py').open('a') as stream:
        stream.write('\n# Work in progress\n')
    return repo


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python3 tests/gitfixtures.py <empty-directory>')
    target = Path(sys.argv[1])
    if target.exists() and any(target.iterdir()):
        raise SystemExit('Showcase target must be an empty directory')
    repo = build_showcase(target)
    print(repo.git('log', '--graph', '--oneline', '--all', '--decorate'))
