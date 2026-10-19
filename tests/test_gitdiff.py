"""Commit details against real trees, including machine paths and display caps."""

import importlib
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from gitfixtures import GitRepo, build_linear, build_octopus, build_showcase
from idgent import gitdiff, gitlog


class PatchParsing(unittest.TestCase):
    def test_headers_section_counts_markers_tabs_and_crlf(self):
        data = (b'diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -4,2 +4,3 @@ def example():\n'
                b' kept\r\n-\told\r\n+\tnew\r\n+last\n\\ No newline at end of file\n'
                b'@@ -10 +11 @@ other\n-old\n+new\n')
        hunks, too_large, cut = gitdiff.parse_patch(data)
        self.assertFalse(too_large or cut)
        self.assertEqual(len(hunks), 2)
        h = hunks[0]
        self.assertEqual((h['old_start'], h['old_lines'], h['new_start'], h['new_lines']), (4, 2, 4, 3))
        self.assertEqual(h['section'], 'def example():')
        self.assertEqual([(l['t'], l['o'], l['n']) for l in h['lines']],
                         [(' ', 4, 4), ('-', 5, None), ('+', None, 5), ('+', None, 6), ('\\', None, None)])
        self.assertEqual(h['lines'][1]['text'], '\told\r')
        self.assertEqual(h['lines'][-1]['text'], '\\ No newline at end of file')
        self.assertEqual(hunks[1]['old_lines'], 1)
        self.assertEqual(hunks[1]['new_lines'], 1)
        self.assertTrue(gitdiff.parse_patch(data, max_lines=2)[1])

    def test_long_lines_and_controls(self):
        hunks, too_large, cut = gitdiff.parse_patch(b'@@ -0,0 +1 @@\n+' + b'x' * 3000 + b'\x1b\n')
        self.assertTrue(cut)
        self.assertFalse(too_large)
        line = hunks[0]['lines'][0]
        self.assertTrue(line['cut'])
        self.assertEqual(len(line['text']), 2000)
        self.assertTrue(line['text'].endswith('…'))
        self.assertNotIn('\x1b', line['text'])


class CommitData(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.repo = GitRepo(self.path)

    def detail(self, rev='HEAD', **kwargs):
        result = gitdiff.commit_detail(self.path, rev, **kwargs)
        self.assertTrue(result['ok'], result)
        return result

    def test_root_metadata_body_huge_message_and_refs(self):
        body = ('Many details 🌍\n' * 30000).strip()
        sha = self.repo.commit({'a.py': 'def hello():\n    return "world"\n'},
                               message='Subject\n\n' + body, author=('名', 'name@example.test'),
                               date='2025-01-01T12:00:00-03:30')
        self.repo.tag('release', annotated=True)
        result = self.detail('release')
        self.assertEqual(result['hash'], sha)
        self.assertEqual(result['subject'], 'Subject')
        self.assertEqual(result['body'], body)
        self.assertEqual(result['author'], dict(name='名', email='name@example.test', ts=result['author']['ts'], tz=-210))
        self.assertEqual(result['committer']['name'], 'Grace Example')
        self.assertEqual(result['committer']['tz'], -210)
        self.assertEqual(result['parents'], [])
        self.assertIsNone(result['diff_base'])
        self.assertFalse(result['merge'])
        self.assertEqual(result['stats'], dict(files=1, additions=2, deletions=0))
        self.assertEqual({r['name'] for r in result['refs']}, {'master', 'release'})
        for rev in (sha, sha[:9], 'master', 'HEAD'):
            self.assertEqual(self.detail(rev)['hash'], sha)

    def test_metadata_control_sanitising(self):
        self.repo.commit({'a.txt': 'hello\x1b world\n'}, message='Subject\x1b\n\nBody\x01\n\tformatted',
                         author=('Author\x01', 'mail@example.test'))
        result = self.detail()
        self.assertEqual(result['subject'], 'Subject')
        self.assertEqual(result['body'], 'Body\n\tformatted')
        self.assertEqual(result['author']['name'], 'Author')
        self.assertEqual(result['files'][0]['hunks'][0]['lines'][0]['text'], 'hello world')

    def test_empty_commit_and_empty_file(self):
        self.repo.commit({'empty': ''}, message='Empty file')
        file = self.detail()['files'][0]
        self.assertEqual(file['status'], 'added')
        self.assertEqual(file['hunks'], [])
        self.assertFalse(file['too_large'])
        self.repo.commit(message='Empty commit')
        result = self.detail()
        self.assertEqual(result['files'], [])
        self.assertEqual(result['stats'], dict(files=0, additions=0, deletions=0))

    def test_statuses_renames_copies_typechange_mode_binary_and_delete(self):
        content = ''.join(f'line {i} with enough content for similarity\n' for i in range(30))
        self.repo.commit({'source.txt': content, 'rename.txt': content.replace('line', 'entry'),
                          'modified.py': 'old = 1\n', 'deleted.txt': 'gone\n', 'type.txt': 'regular\n',
                          'mode.sh': 'echo hello\n', 'z-after-type.txt': 'before\n'})
        (self.path / 'type.txt').unlink()
        (self.path / 'type.txt').symlink_to('source.txt')
        self.repo.commit({'copy.txt': content, 'rename.txt': None,
                          'renamed.txt': content.replace('line', 'entry').replace('entry 0 ', 'changed 0 '),
                          'modified.py': 'new = 2\n', 'deleted.txt': None, 'added.txt': 'new\n',
                          'mode.sh': ('echo hello\n', 0o755), 'image.png': b'\x89PNG\x00binary\n',
                          'z-after-type.txt': 'after\n'})
        result = self.detail()
        files = {f['path']: f for f in result['files']}
        self.assertEqual(files['copy.txt']['status'], 'copied')
        self.assertEqual(files['copy.txt']['old_path'], 'source.txt')
        self.assertEqual(files['copy.txt']['similarity'], 100)
        self.assertEqual(files['renamed.txt']['status'], 'renamed')
        self.assertEqual(files['renamed.txt']['old_path'], 'rename.txt')
        self.assertGreater(files['renamed.txt']['similarity'], 90)
        self.assertEqual(files['modified.py']['status'], 'modified')
        self.assertEqual(files['deleted.txt']['status'], 'deleted')
        self.assertEqual(files['added.txt']['status'], 'added')
        self.assertEqual(files['type.txt']['status'], 'typechange')
        self.assertEqual(len(files['type.txt']['hunks']), 2)
        link = gitdiff.file_context(self.path, 'HEAD', 'type.txt', 1, 2)
        self.assertTrue(link['ok'])
        self.assertEqual(link['lines'][0]['text'], 'source.txt')
        self.assertEqual([l['text'] for l in files['z-after-type.txt']['hunks'][0]['lines']], ['before', 'after'])
        self.assertEqual(files['mode.sh']['mode_change'], ['100644', '100755'])
        self.assertEqual(files['mode.sh']['hunks'], [])
        self.assertTrue(files['image.png']['binary'])
        self.assertEqual(files['image.png']['hunks'], [])
        self.assertEqual(result['stats']['files'], len(files))
        expected = self.repo.git('diff', '--numstat', '-M', '-C', '--find-copies-harder', 'HEAD~1', 'HEAD').splitlines()
        self.assertEqual(result['stats']['additions'], sum(int(l.split('\t')[0]) for l in expected if not l.startswith('-')))

    def test_machine_paths_spaces_unicode_quotes_newlines_tabs_and_leading_dash(self):
        names = ['Привет мир.py', 'a "quoted" file.txt', 'line\nbreak.py', 'tab\tname.py',
                 '-option.py', 'a:b.py', 'literal[1].py', 'back\\slash.py']
        self.repo.commit({name: 'value = "🌍"\n' for name in names})
        result = self.detail()
        self.assertEqual({f['path'] for f in result['files']}, set(names))
        for file in result['files']:
            self.assertEqual(file['hunks'][0]['lines'][0]['text'], 'value = "🌍"')
            context = gitdiff.file_context(self.path, 'HEAD', file['path'], 1, 2)
            self.assertTrue(context['ok'], context)
            self.assertEqual(context['lines'][0]['text'], 'value = "🌍"')
        self.repo.commit({names[2]: None, 'renamed\nnew.py': 'value = "🌍"\n'})
        file = next(f for f in self.detail()['files'] if f['path'] == 'renamed\nnew.py')
        self.assertEqual(file['old_path'], names[2])
        self.assertEqual(file['status'], 'renamed')

    def test_first_parent_merge_and_octopus(self):
        # Build into a separate, empty child repository.
        repo = build_octopus(self.path / 'octopus')
        result = gitdiff.commit_detail(repo.path, 'HEAD')
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['merge'])
        self.assertEqual(len(result['parents']), 3)
        self.assertEqual(result['diff_base'], repo.git('rev-parse', 'HEAD^1'))
        self.assertEqual({f['path'] for f in result['files']}, {'feature/auth.py', 'feature/api.py'})
        repo.checkout('topic', create=True)
        repo.commit({'side.txt': 'side\n'})
        repo.checkout('master')
        repo.commit({'main-only.txt': 'main\n'})
        repo.merge('topic')
        result = gitdiff.commit_detail(repo.path, 'HEAD')
        self.assertEqual([f['path'] for f in result['files']], ['side.txt'])

    def test_crlf_no_newline_multi_hunks_and_sections(self):
        lines = ['def first():\n', '\treturn 1\n'] + [f'# gap {i}\n' for i in range(25)] + ['def last():\n', '\treturn 2\n']
        self.repo.commit({'code.py': ''.join(lines), 'crlf.txt': b'one\r\ntwo\r\n', 'no-final.txt': 'old'})
        lines[1], lines[-1] = '\treturn 3\n', '\treturn 4\n'
        self.repo.commit({'code.py': ''.join(lines), 'crlf.txt': b'one\r\nchanged\r\n', 'no-final.txt': 'new'})
        files = {f['path']: f for f in self.detail()['files']}
        self.assertEqual(len(files['code.py']['hunks']), 2)
        self.assertTrue(files['code.py']['hunks'][1]['section'])
        self.assertTrue(any(l['text'].startswith('\t') for h in files['code.py']['hunks'] for l in h['lines']))
        self.assertEqual([l['text'] for l in files['crlf.txt']['hunks'][0]['lines']], ['one\r', 'two\r', 'changed\r'])
        markers = [l for l in files['no-final.txt']['hunks'][0]['lines'] if l['t'] == '\\']
        self.assertEqual(len(markers), 2)
        self.assertTrue(all(l['o'] is None and l['n'] is None and l['k'] == [] for l in markers))

    def test_caps_keep_true_counts(self):
        self.repo.commit({f'{i}.py': ''.join(f'x = {j}\n' for j in range(100)) for i in range(4)})
        full = self.detail()
        self.assertFalse(full['truncated'])
        for kwargs in ({'max_files': 2}, {'max_files': 0}, {'max_lines_per_file': 3}, {'max_bytes': 100}, {'max_bytes': 0}):
            with self.subTest(kwargs=kwargs):
                result = self.detail(**kwargs)
                self.assertEqual(result['stats'], full['stats'])
                self.assertTrue(result['truncated'])
                if 'max_files' in kwargs:
                    self.assertEqual(len(result['files']), kwargs['max_files'])
                else:
                    self.assertTrue(any(f['too_large'] for f in result['files']))
                    self.assertTrue(all(not f['hunks'] for f in result['files'] if f['too_large']))
        self.assertEqual(full['stats'], dict(files=4, additions=400, deletions=0))

    def test_long_line_cap_sets_marker_and_bounds_runs(self):
        self.repo.commit({'long.py': 'value = "' + 'x' * 5000 + '"\n'})
        result = self.detail()
        line = result['files'][0]['hunks'][0]['lines'][0]
        self.assertEqual(len(line['text']), 2000)
        self.assertTrue(line['cut'])
        self.assertTrue(result['truncated'])
        self.assertTrue(all(0 <= a < b <= len(line['text']) for a, b, _kind in line['k']))

    def test_unknown_revisions_git_missing_and_injections(self):
        self.repo.commit({'safe.txt': 'safe'})
        for rev in ('missing', '--output=owned', '-x', ';', 'HEAD;touch owned', 'HEAD\n--output=owned', '', 'HEAD\0x'):
            with self.subTest(rev=rev):
                result = gitdiff.commit_detail(self.path, rev)
                self.assertFalse(result['ok'])
                self.assertEqual(result['error'], 'unknown-revision')
        self.assertFalse((self.path / 'owned').exists())
        with patch.dict(os.environ, {'PATH': ''}):
            self.assertEqual(gitdiff.commit_detail(self.path, 'HEAD')['error'], 'git-missing')
        self.assertEqual(gitdiff.commit_detail(self.path / 'missing', 'HEAD')['error'], 'not-a-repo')
        self.assertEqual(gitdiff.commit_detail(self.path.parent, 'HEAD')['error'], 'not-a-repo')
        with patch.object(gitdiff, '_run', side_effect=gitlog.GitError('timeout')):
            self.assertEqual(gitdiff.commit_detail(self.path, 'HEAD')['error'], 'timeout')

    def test_context_clamp_missing_binary_and_traversal(self):
        self.repo.commit({'large.py': ''.join(f'x = {i}\n' for i in range(400)), 'binary': b'\0binary', 'empty': ''})
        result = gitdiff.file_context(self.path, 'HEAD', 'large.py', -10, 50000)
        self.assertTrue(result['ok'])
        self.assertEqual((result['start'], result['end'], len(result['lines'])), (1, 200, 200))
        result = gitdiff.file_context(self.path, 'HEAD', 'large.py', 398, 900)
        self.assertEqual((result['start'], result['end'], len(result['lines'])), (398, 400, 3))
        self.assertEqual(result['lines'][0]['text'], 'x = 397')
        self.assertEqual(result['lines'][-1]['n'], 400)
        self.assertTrue(gitdiff.file_context(self.path, 'HEAD', 'empty', 1, 5)['ok'])
        self.assertEqual(gitdiff.file_context(self.path, 'HEAD', 'large.py', 900, 1000)['lines'], [])
        for path in ('../large.py', '/etc/passwd', 'a/../large.py', 'missing', 'binary', './large.py', 'large.py\0'):
            with self.subTest(path=path):
                self.assertFalse(gitdiff.file_context(self.path, 'HEAD', path, 1, 2)['ok'])
        self.assertFalse(gitdiff.file_context(self.path, '-x', 'large.py', 1, 2)['ok'])

    def test_submodule_changes_without_external_access(self):
        first = self.repo.commit({'a.txt': 'a\n'})
        second = self.repo.commit({'a.txt': 'b\n'})
        self.repo.git('update-index', '--add', '--cacheinfo', '160000,' + first + ',vendor/module')
        self.repo.git('commit', '-m', 'Add submodule', env=self.repo._dated())
        result = self.detail()
        self.assertEqual(result['files'][0]['path'], 'vendor/module')
        self.assertEqual(result['files'][0]['mode_change'], ['000000', '160000'])
        self.repo.git('update-index', '--cacheinfo', '160000,' + second + ',vendor/module')
        self.repo.git('commit', '-m', 'Update submodule', env=self.repo._dated())
        file = self.detail()['files'][0]
        self.assertEqual(file['status'], 'modified')
        self.assertEqual((file['additions'], file['deletions']), (1, 1))
        self.assertFalse(gitdiff.file_context(self.path, 'HEAD', 'vendor/module', 1, 2)['ok'])

    def test_missing_highlight_import_and_available_flag(self):
        self.repo.commit({'a.py': 'def hello():\n    return "world"\n'})
        gitdiff._highlight.cache_clear()
        self.addCleanup(gitdiff._highlight.cache_clear)
        with patch.object(gitdiff.importlib, 'import_module', side_effect=ImportError('no pygments')) as load:
            result = self.detail()
            self.assertIsNone(result['files'][0]['language'])
            self.assertTrue(all(not l['k'] for h in result['files'][0]['hunks'] for l in h['lines']))
            self.detail()
            self.assertEqual(load.call_count, 1)
        gitdiff._highlight.cache_clear()
        try:
            highlight = importlib.import_module('idgent.highlight')
        except Exception:
            self.skipTest('app highlighter unavailable')
        with patch.object(highlight, 'AVAILABLE', False):
            result = self.detail()
            self.assertIsNone(result['files'][0]['language'])
            self.assertTrue(all(not l['k'] for h in result['files'][0]['hunks'] for l in h['lines']))

    def test_syntax_runs_per_hunk_per_side_and_context_new_side(self):
        gitdiff._highlight.cache_clear()
        highlight = gitdiff._highlight()
        if not highlight or not highlight.AVAILABLE:
            self.skipTest('Pygments/app highlighter unavailable')
        self.repo.commit({'a.py': '"""old opening\ncontext inside\nold ending"""\ndef hello():\n    return "world"\n'})
        self.repo.commit({'a.py': '# new opening\ncontext inside\n# new ending\ndef hello():\n    return "world"\n'})
        file = self.detail()['files'][0]
        self.assertEqual(file['language'], 'Python')
        lines = file['hunks'][0]['lines']
        context = next(l for l in lines if l['text'] == 'context inside')
        self.assertNotIn('doc', [k[2] for k in context['k']])
        removed = next(l for l in lines if l['t'] == '-' and l['text'] == 'old ending"""')
        self.assertIn('doc', [k[2] for k in removed['k']])
        kinds = {kind for line in lines for _a, _b, kind in line['k']}
        self.assertTrue({'keyword', 'string'}.issubset(kinds), kinds)
        for line in lines:
            for a, b, kind in line['k']:
                self.assertIn(kind, highlight.KINDS)
                self.assertTrue(0 <= a < b <= len(line['text']))
        context = gitdiff.file_context(self.path, 'HEAD', 'a.py', 4, 5)
        self.assertTrue(any(kind == 'string' for line in context['lines'] for a, b, kind in line['k']))

    def test_twenty_file_and_three_thousand_line_performance(self):
        content = ''.join(f'value_{i} = "item {i}"\n' for i in range(3000))
        self.repo.commit({'large.py': content, **{f'module_{i}.py': 'def value():\n    return "ready"\n' for i in range(19)}})
        gitdiff._highlight.cache_clear()
        gitdiff._syntax('warm.py')
        start = time.perf_counter()
        result = self.detail()
        elapsed = time.perf_counter() - start
        self.assertEqual(result['stats']['files'], 20)
        self.assertFalse(result['truncated'])
        self.assertLess(elapsed, .3)


if __name__ == '__main__':
    unittest.main()
