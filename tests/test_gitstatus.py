"""Real repositories exercise porcelain parsing without importing GTK."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from idgent.gitstatus import MAX_ENTRIES, Status, collect_status, parse_porcelain


class RepoTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git('init', '-q', '--initial-branch=base')
        self.write('clean.txt', 'base\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'base')

    def git(self, *args, check=True):
        return subprocess.run(['git', '-C', str(self.root), '-c', 'user.name=Test',
                               '-c', 'user.email=test@example.invalid', *args],
                              check=check, capture_output=True)

    def write(self, name, content='new\n'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return str(path)

    def snapshot(self, root=None, **kwargs):
        return collect_status(str(root or self.root), **kwargs)


class GitStatusTests(RepoTest):
    def test_untracked_staged_and_unchanged(self):
        new = self.write('new.txt')
        self.assertEqual(self.snapshot().files, {new: Status('new', 'U')})
        self.git('add', 'new.txt')
        self.assertEqual(self.snapshot().files, {new: Status('new', 'A')})
        self.assertEqual(self.snapshot().folders[str(self.root)], Status('new'))

    def test_modified_staged_and_modified_again(self):
        path = self.write('clean.txt', 'changed\n')
        self.assertEqual(self.snapshot().files[path], Status('modified', 'M'))
        self.git('add', 'clean.txt')
        self.assertEqual(self.snapshot().files[path], Status('modified', 'M'))
        self.write('clean.txt', 'changed again\n')
        self.assertEqual(self.snapshot().files[path], Status('modified', 'M'))
        self.assertIn(b'MM clean.txt', self.git('status', '--porcelain=v1').stdout)

    def test_added_then_modified_is_modified(self):
        path = self.write('new.txt')
        self.git('add', 'new.txt')
        self.write('new.txt', 'edited after staging')
        self.assertEqual(self.snapshot().files[path], Status('modified', 'M'))

    def test_rename_destination_and_old_folder(self):
        self.write('old/file name.txt')
        self.git('add', '.')
        self.git('commit', '-qm', 'nested')
        (self.root / 'new').mkdir()
        self.git('mv', 'old/file name.txt', 'new/rénamed file.txt')
        snapshot = self.snapshot()
        self.assertEqual(snapshot.files[str(self.root / 'new/rénamed file.txt')], Status('modified', 'R'))
        self.assertNotIn(str(self.root / 'old/file name.txt'), snapshot.files)
        self.assertEqual(snapshot.folders[str(self.root / 'old')], Status('modified'))
        self.assertEqual(snapshot.folders[str(self.root / 'new')], Status('modified'))
        self.assertEqual(self.snapshot(self.root / 'old').folders[str(self.root / 'old')], Status('modified'))

    def test_deleted_only_decorates_parents(self):
        self.git('rm', 'clean.txt')
        snapshot = self.snapshot()
        self.assertEqual(snapshot.files, {})
        self.assertEqual(snapshot.folders, {str(self.root): Status('modified')})

    def test_unicode_spaces_newlines_and_nested_aggregate(self):
        path = self.write('outer/inner/你好 spaced\nname.txt')
        snapshot = self.snapshot()
        self.assertEqual(snapshot.files[path], Status('new', 'U'))
        for folder in ('', 'outer', 'outer/inner'):
            self.assertEqual(snapshot.folders[str(self.root / folder)], Status('new'))
        self.write('clean.txt', 'modified')
        self.assertEqual(self.snapshot().folders[str(self.root)], Status('modified'))

    def test_explorer_subfolder_filters_siblings(self):
        path = self.write('sub/nested/a.txt')
        self.write('outside.txt')
        snapshot = self.snapshot(self.root / 'sub')
        self.assertEqual(snapshot.files, {path: Status('new', 'U')})
        self.assertEqual(set(snapshot.folders), {str(self.root / 'sub'), str(self.root / 'sub/nested')})

    def test_real_merge_conflict_overrides_modified_and_new(self):
        self.git('checkout', '-qb', 'other')
        self.write('clean.txt', 'other\n')
        self.git('commit', '-qam', 'other')
        self.git('checkout', '-q', 'base')
        self.write('clean.txt', 'base change\n')
        self.git('commit', '-qam', 'base change')
        self.assertNotEqual(self.git('merge', 'other', check=False).returncode, 0)
        self.write('untracked.txt')
        snapshot = self.snapshot()
        self.assertEqual(snapshot.files[str(self.root / 'clean.txt')], Status('conflict', '!'))
        self.assertEqual(snapshot.folders[str(self.root)], Status('conflict'))

    def test_all_conflict_codes_typechange_copy_and_unstaged_rename(self):
        for code in ('UU', 'AA', 'DD', 'AU', 'UA', 'DU', 'UD'):
            with self.subTest(code=code):
                result = parse_porcelain((code + ' f\0').encode(), str(self.root), str(self.root))
                self.assertEqual(result.files[str(self.root / 'f')], Status('conflict', '!'))
        for code, badge in ((' T', 'M'), ('T ', 'M'), (' C', 'M'), (' R', 'R')):
            data = (code + ' f\0' + ('old\0' if code.strip() in ('C', 'R') else '')).encode()
            result = parse_porcelain(data, str(self.root), str(self.root))
            self.assertEqual(result.files[str(self.root / 'f')], Status('modified', badge))

    def test_no_repository_missing_binary_and_timeout(self):
        with tempfile.TemporaryDirectory() as outside:
            self.assertEqual(collect_status(outside).files, {})
            self.assertIsNone(collect_status(outside).git_dir)
        with patch.dict(os.environ, PATH=''):
            self.assertEqual(self.snapshot().folders, {})
            self.assertIsNone(self.snapshot().git_dir)
        with patch('idgent.gitstatus._run', side_effect=subprocess.TimeoutExpired('git', 5)):
            self.assertEqual(self.snapshot().files, {})

    def test_status_caps_real_repo_and_large_parser_input(self):
        for i in range(35):
            self.write(f'file-{i}')
        result = self.snapshot(max_entries=10)
        self.assertEqual(len(result.files), 10)
        self.assertTrue(result.truncated)
        data = b''.join(f'?? file-{i}\0'.encode() for i in range(MAX_ENTRIES + 1))
        result = parse_porcelain(data, str(self.root), str(self.root))
        self.assertEqual(len(result.files), MAX_ENTRIES)
        self.assertTrue(result.truncated)

    def test_ignored_files_stay_undecorated(self):
        self.write('.gitignore', '*.ignored\n')
        path = self.write('file.ignored')
        self.assertNotIn(path, self.snapshot().files)

    def test_commands_use_readonly_environment_and_list_args(self):
        real_popen = subprocess.Popen
        with patch('idgent.gitstatus.subprocess.Popen', wraps=real_popen) as run:
            self.snapshot()
        for call in run.call_args_list:
            self.assertIsInstance(call.args[0], list)
            self.assertEqual(call.kwargs['env']['LC_ALL'], 'C')
            self.assertEqual(call.kwargs['env']['GIT_OPTIONAL_LOCKS'], '0')
            self.assertFalse(call.kwargs.get('shell', False))
