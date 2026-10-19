"""Real Git and pure parsing checks for the bounded graph contract."""

import builtins
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from gitfixtures import (GitRepo, build_clone_with_remote, build_deleted_branches,
                         build_detached, build_linear, build_octopus, build_showcase)
from idgent import gitlog


def fast_history(path, count=3000):
    repo = GitRepo(path)
    commands = [b'blob\nmark :1\ndata 2\nx\n\n']
    for i in range(count):
        message = f'Generated commit {i}'.encode()
        commands.append((f'commit refs/heads/master\nmark :{i + 2}\n'
                         f'author Fast <fast@example.test> {1735732800 + i} +0530\n'
                         f'committer Fast <fast@example.test> {1735732800 + i} +0000\n'
                         f'data {len(message)}\n').encode() + message + b'\n' +
                        (f'from :{i + 1}\n'.encode() if i else b'') + b'M 100644 :1 file.txt\n\n')
    repo.git('fast-import', '--quiet', input=b''.join(commands))
    return repo


class MergeNames(unittest.TestCase):
    def test_table(self):
        subjects = {
            "Merge branch 'feature/ui'": 'feature/ui',
            "Merge branch 'feature/ui' into master": 'feature/ui',
            'Merge branch "feature/quotes"': 'feature/quotes',
            "Merge remote-tracking branch 'origin/feature/x'": 'feature/x',
            "Merge remote-tracking branch 'upstream/release' into master": 'release',
            "Merge tag 'v2.0'": 'v2.0',
            'Merge pull request #42 from acme/feature/auth': 'feature/auth',
            'Merge pull request #47 from acme/hotfix/critical\n': 'hotfix/critical',
            'Merge ceo/codex/pane-split: draggable dividers between the panes of both docks (terminal/terminal/graph), live resize, keyboard + double-click reset': 'ceo/codex/pane-split',
            'Merge ceo/codex/explorer-no-badges: git status shown by green/orange name colours only (U/M/A/R/! letters removed)': 'ceo/codex/explorer-no-badges',
            'Merge ceo/claude/graph-web: Graph view page (force engine, Canvas2D renderer, camera, interaction, controls panel) + node and WebKit tests': 'ceo/claude/graph-web',
            'Merge ceo/codex/terminal-startup: shells spawn on the final dock geometry (fixes the start-up cursor drift in UTF-8), early input buffered, UTF-8 start-up regression tests': 'ceo/codex/terminal-startup',
            'Ordinary change': None, 'Merge something unknown': None,
            "Merge branch 'unterminated": None, '': None,
            'x' * 20000: None,
            'Merge ceo/codex/large: ' + 'x' * 20000: 'ceo/codex/large',
        }
        for subject, expected in subjects.items():
            with self.subTest(subject=subject[:90]):
                self.assertEqual(gitlog.infer_merged_name(subject), expected)

    def test_sanitising_and_parser_without_git(self):
        fields = [b'a' * 40, b'aaaaaaa', b'', b'a\x1f\x1b' + '🌍'.encode() * 400,
                  b'name\x7f' * 40, b'e\x01mail' * 40, b'1234', b'2001-01-01 12:00:00 -0330', b'4567']
        commit = gitlog.parse_log(b'\0'.join(fields) + b'\0')[0]
        self.assertEqual(len(commit['subject']), 300)
        self.assertTrue(commit['subject'].endswith('…'))
        self.assertNotIn('\x1f', commit['subject'])
        self.assertLessEqual(len(commit['author']), 120)
        self.assertLessEqual(len(commit['email']), 120)
        self.assertEqual(commit['tz'], -210)
        self.assertEqual(gitlog.clean(b'bad\xff'), 'bad�')


class GraphData(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)

    def assert_topological(self, graph):
        positions = {c['hash']: i for i, c in enumerate(graph['commits'])}
        for index, commit in enumerate(graph['commits']):
            self.assertRegex(commit['hash'], r'^[a-f0-9]{40}$')
            for parent in commit['parents']:
                if parent in positions:
                    self.assertLess(index, positions[parent])

    def test_linear_limit_refs_and_dirty(self):
        repo = build_linear(self.path)
        repo.tag('lightweight').tag('annotated', annotated=True)
        (self.path / 'new file').write_text('hello')
        (self.path / 'main.py').write_text('changed')
        graph = gitlog.collect_graph(self.path, limit=2)
        self.assertTrue(graph['ok'], graph)
        self.assertEqual(graph['total'], 5)
        self.assertEqual(len(graph['commits']), 2)
        self.assertTrue(graph['truncated'])
        self.assertEqual(graph['dirty'], {'changed': 2})
        self.assertEqual(graph['repo']['default_branch'], 'master')
        self.assertEqual(graph['head']['branch'], 'master')
        self.assertEqual({r['name'] for r in graph['commits'][0]['refs']}, {'master', 'lightweight', 'annotated'})
        self.assert_topological(graph)
        self.assertNotIn(graph['commits'][-1]['parents'][0], {c['hash'] for c in graph['commits']})

    def test_staged_rename_is_one_dirty_path(self):
        repo = build_linear(self.path, 1)
        repo.git('mv', 'main.py', 'renamed.py')
        self.assertEqual(gitlog.collect_graph(self.path)['dirty'], {'changed': 1})

    def test_octopus(self):
        build_octopus(self.path)
        graph = gitlog.collect_graph(self.path)
        self.assertEqual(graph['total'], 5)
        self.assertEqual(len(graph['commits'][0]['parents']), 3)
        self.assertEqual(graph['commits'][0]['merged_name'], 'feature/auth')
        self.assert_topological(graph)

    def test_deleted_branches(self):
        build_deleted_branches(self.path)
        graph = gitlog.collect_graph(self.path)
        merges = [c for c in graph['commits'] if len(c['parents']) > 1]
        self.assertEqual({c['merged_name'] for c in merges},
                         {'ceo/codex/pane-split', 'ceo/codex/explorer-no-badges', 'ceo/codex/git-data'})
        self.assertEqual(sum(len(c['refs']) for c in graph['commits']), 1)
        self.assert_topological(graph)

    def test_detached_head_only_commit_is_included(self):
        repo = build_detached(self.path)
        graph = gitlog.collect_graph(self.path)
        self.assertEqual(graph['head'], dict(hash=repo.git('rev-parse', 'HEAD'), branch=None, detached=True))
        self.assertIn(graph['head']['hash'], {c['hash'] for c in graph['commits']})
        self.assertEqual(graph['total'], 6)
        self.assert_topological(graph)

    def test_unborn_empty_not_repo_and_git_missing(self):
        self.assertEqual(gitlog.collect_graph(self.path)['error'], 'not-a-repo')
        GitRepo(self.path)
        graph = gitlog.collect_graph(self.path)
        self.assertEqual(graph['error'], 'empty')
        self.assertFalse(graph['ok'])
        self.assertEqual(graph['head']['branch'], 'master')
        with patch.dict(os.environ, {'PATH': ''}):
            self.assertEqual(gitlog.collect_graph(self.path)['error'], 'git-missing')

    def test_remote_default_and_current_ref(self):
        repo = build_clone_with_remote(self.path)
        repo.git('checkout', '-b', 'topic')
        graph = gitlog.collect_graph(repo.path)
        self.assertEqual(graph['repo']['default_branch'], 'origin/master')
        refs = graph['commits'][0]['refs']
        self.assertNotIn('origin/HEAD', [r['name'] for r in refs])
        self.assertIn(dict(name='origin/master', kind='remote'), refs)
        self.assertIn(dict(name='topic', kind='branch', current=True), refs)
        self.assertIn(dict(name='v1', kind='tag'), refs)

    def test_default_precedence_and_subdirectory_root(self):
        repo = build_linear(self.path, 1)
        repo.branch('main').checkout('master')
        (self.path / 'nested').mkdir()
        graph = gitlog.collect_graph(self.path / 'nested')
        self.assertEqual(graph['repo']['root'], str(self.path))
        self.assertEqual(graph['repo']['default_branch'], 'main')
        repo.git('branch', '-m', 'master', 'topic')
        repo.git('branch', '-D', 'main')
        self.assertEqual(gitlog.collect_graph(self.path)['repo']['default_branch'], 'topic')

    def test_multiple_roots_exclude_notes_and_stash(self):
        repo = build_linear(self.path, 2)
        original = repo.git('rev-parse', 'HEAD')
        repo.git('checkout', '--orphan', 'unrelated')
        repo.git('rm', '-rf', '.')
        repo.commit({'other.txt': 'second root'}, message='Unrelated root')
        repo.checkout('master')
        (repo.path / 'main.py').write_text('stash me')
        repo.git('stash', 'push', '-m', 'not a graph commit')
        repo.git('notes', 'add', '-m', 'not a graph commit', original, env=repo._dated())
        graph = gitlog.collect_graph(self.path)
        self.assertEqual(graph['total'], 3)
        self.assertEqual(sum(not c['parents'] for c in graph['commits']), 2)
        self.assert_topological(graph)

    def test_unicode_controls_and_author_dates(self):
        repo = GitRepo(self.path)
        repo.commit({'Привет мир.py': 'print("🌍")\n'}, message='Привет\x1b ' + 's' * 400,
                    author=('名\x01' + 'a' * 140, 'e' * 140 + '@example.test'), date='2025-02-01T12:00:00+05:30')
        commit = gitlog.collect_graph(self.path)['commits'][0]
        self.assertTrue(commit['subject'].startswith('Привет '))
        self.assertEqual(len(commit['subject']), 300)
        self.assertEqual(len(commit['author']), 120)
        self.assertEqual(len(commit['email']), 120)
        self.assertEqual(commit['tz'], 330)

    def test_three_thousand_commits_performance(self):
        fast_history(self.path)
        before = time.perf_counter()
        graph = gitlog.collect_graph(self.path, limit=1500)
        elapsed = time.perf_counter() - before
        self.assertTrue(graph['ok'], graph)
        self.assertEqual(graph['total'], 3000)
        self.assertEqual(len(graph['commits']), 1500)
        self.assertTrue(graph['truncated'])
        self.assertLess(elapsed, 1.5)
        self.assert_topological(graph)

    def test_bounded_runner_reaps_on_timeout_and_cap(self):
        executable = self.path / 'git'
        executable.write_text('#!' + sys.executable + '\nimport time,sys\n'
                              'if "hang" in sys.argv: time.sleep(10)\n'
                              'else: sys.stdout.write("x" * 1000000)\n')
        executable.chmod(0o755)
        processes = []
        popen = subprocess.Popen
        def capture(*args, **kwargs):
            process = popen(*args, **kwargs)
            processes.append(process)
            return process
        with patch.dict(os.environ, {'PATH': str(self.path)}), patch.object(gitlog.subprocess, 'Popen', capture):
            with self.assertRaisesRegex(gitlog.GitError, 'timeout'):
                gitlog._run(self.path, ['hang'], time.monotonic() + .1)
            data, clipped = gitlog._run(self.path, [], time.monotonic() + 2, cap=100, partial=True)
            self.assertEqual(len(data), 100)
            self.assertTrue(clipped)
            with self.assertRaisesRegex(gitlog.GitError, 'output-too-large'):
                gitlog._run(self.path, [], time.monotonic() + 2, cap=100)
        self.assertTrue(all(p.poll() is not None and p.stdout.closed for p in processes))

    def test_dirty_failure_and_count_fallback(self):
        build_linear(self.path, 4)
        run = gitlog._run
        def fake(root, args, *rest, **kwargs):
            if args[0] in ('status', 'rev-list'):
                raise gitlog.GitError('timeout')
            return run(root, args, *rest, **kwargs)
        with patch.object(gitlog, '_run', fake):
            graph = gitlog.collect_graph(self.path, limit=2)
        self.assertTrue(graph['ok'])
        self.assertIsNone(graph['dirty'])
        self.assertGreater(graph['total'], 2)
        self.assertTrue(graph['truncated'])

    def test_import_and_call_with_gi_blocked(self):
        build_linear(self.path, 1)
        code = '''import builtins, sys
original = builtins.__import__
def blocked(name, *args, **kwargs):
    if name == 'gi' or name.startswith('gi.'):
        raise ImportError('gi blocked by test')
    return original(name, *args, **kwargs)
builtins.__import__ = blocked
from idgent.gitlog import collect_graph
from idgent.gitdiff import commit_detail
assert collect_graph(sys.argv[1])['ok']
detail = commit_detail(sys.argv[1], 'HEAD')
assert detail['ok'], detail
assert detail['files'][0]['language'] is None, detail
assert all(not line['k'] for h in detail['files'][0]['hunks'] for line in h['lines'])
assert 'gi' not in sys.modules
'''
        result = subprocess.run([sys.executable, '-c', code, str(self.path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class Showcase(unittest.TestCase):
    def test_fixture_is_deterministic_and_matches_topology(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            first, second = build_showcase(a), build_showcase(b)
            self.assertEqual(first.git('show-ref'), second.git('show-ref'))
            graph = gitlog.collect_graph(a)
            self.assertEqual(graph['dirty'], {'changed': 2})
            self.assertEqual(graph['head']['branch'], 'master')
            self.assertEqual(int(first.git('rev-list', '--first-parent', '--count', 'master')), 14)
            self.assertEqual(sorted(len(c['parents']) for c in graph['commits'] if len(c['parents']) > 1), [2, 2, 3])
            branches = set(first.git('branch', '--format=%(refname:short)').splitlines())
            self.assertEqual(branches, {'master', 'feature/auth', 'feature/api', 'feature/ui',
                                        'hotfix/critical', 'experiment/graph-polish'})
            self.assertEqual(first.git('rev-list', '--count', 'master..experiment/graph-polish'), '2')


if __name__ == '__main__':
    unittest.main()
