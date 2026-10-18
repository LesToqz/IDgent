import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

# Keep GTK on the Xvfb display even when WAYLAND_DISPLAY is inherited.
os.environ["GDK_BACKEND"] = "x11"

from idgent import fsops
from gi.repository import Gio, GLib


class FsOpsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_validate_name(self):
        for name in ('ok', '.hidden', 'a b', 'λ.py'):
            self.assertIsNone(fsops.validate_name(name))
        for name in ('', '.', '..', 'a/b', '\0', 'a\0b', None):
            with self.subTest(name=name), self.assertRaisesRegex(fsops.FsOpError, 'Invalid name'):
                fsops.validate_name(name)

    def test_create_file_and_folder(self):
        file = fsops.create_file(str(self.root), 'a')
        folder = fsops.create_folder(str(self.root), 'b')
        self.assertTrue(Path(file).is_file())
        self.assertEqual(Path(file).read_text(), '')
        self.assertTrue(Path(folder).is_dir())
        self.assertTrue(os.path.isabs(file))
        self.assertTrue(os.path.isabs(folder))

    def test_create_errors(self):
        (self.root / 'existing').write_text('keep')
        for operation in (fsops.create_file, fsops.create_folder):
            with self.subTest(operation=operation.__name__):
                with self.assertRaisesRegex(fsops.FsOpError, 'Already exists'):
                    operation(str(self.root), 'existing')
                with self.assertRaisesRegex(fsops.FsOpError, 'Invalid name'):
                    operation(str(self.root), '../bad')
                with self.assertRaisesRegex(fsops.FsOpError, 'Not found'):
                    operation(str(self.root / 'missing'), 'child')
        self.assertEqual((self.root / 'existing').read_text(), 'keep')

    def test_rename_file_folder_and_symlink(self):
        for name, folder in (('file', False), ('folder', True)):
            path = self.root / name
            path.mkdir() if folder else path.touch()
            new = fsops.rename(str(path), name + '2')
            self.assertEqual(new, str(self.root / (name + '2')))
            self.assertFalse(path.exists())
            self.assertTrue(Path(new).exists())
        link = self.root / 'broken'
        link.symlink_to(self.root / 'absent')
        new = fsops.rename(str(link), 'renamed-link')
        self.assertTrue(Path(new).is_symlink())

    def test_rename_errors_and_no_overwrite(self):
        source = self.root / 'source'
        target = self.root / 'target'
        source.write_text('source')
        target.write_text('target')
        with self.assertRaisesRegex(fsops.FsOpError, 'Already exists'):
            fsops.rename(str(source), 'target')
        with self.assertRaisesRegex(fsops.FsOpError, 'Not found'):
            fsops.rename(str(self.root / 'missing'), 'new')
        with self.assertRaisesRegex(fsops.FsOpError, 'Invalid name'):
            fsops.rename(str(source), '..')
        link = self.root / 'link'
        link.symlink_to(self.root / 'missing')
        with self.assertRaisesRegex(fsops.FsOpError, 'Already exists'):
            fsops.rename(str(source), 'link')
        self.assertEqual(source.read_text(), 'source')
        self.assertEqual(target.read_text(), 'target')

    def test_rename_never_overwrites_a_racing_target(self):
        source = self.root / 'source'
        target = self.root / 'target'
        source.write_text('source')
        original = fsops._rename_no_replace

        def race(old, new):
            target.write_text('created concurrently')
            original(old, new)

        with patch('idgent.fsops._rename_no_replace', side_effect=race):
            with self.assertRaisesRegex(fsops.FsOpError, 'Already exists'):
                fsops.rename(str(source), 'target')
        self.assertEqual(source.read_text(), 'source')
        self.assertEqual(target.read_text(), 'created concurrently')

    def test_delete_files_and_recursive_folders(self):
        file = self.root / 'file'
        file.touch()
        self.assertEqual(fsops.delete(str(file)), str(file))
        folder = self.root / 'folder'
        (folder / 'nested').mkdir(parents=True)
        (folder / 'nested' / 'data').touch()
        self.assertEqual(fsops.delete(str(folder)), str(folder))
        self.assertFalse(folder.exists())
        with self.assertRaisesRegex(fsops.FsOpError, 'Not found'):
            fsops.delete(str(file))

    def test_delete_protected_roots(self):
        for path, project in (('/', None), ('/tmp/..', None), (str(self.root), str(self.root))):
            with self.subTest(path=path), self.assertRaisesRegex(fsops.FsOpError, 'root'):
                fsops.delete(path, project_root=project)
        self.assertTrue(self.root.is_dir())

    def test_delete_symlinks_without_following(self):
        target = self.root / 'target'
        target.mkdir()
        (target / 'keep').touch()
        for name, destination in (('folder-link', target), ('root-link', Path('/')), ('broken', self.root / 'absent')):
            link = self.root / name
            link.symlink_to(destination)
            fsops.delete(str(link), project_root=str(self.root))
            self.assertFalse(os.path.lexists(link))
        nested = self.root / 'nested'
        nested.mkdir()
        (nested / 'link').symlink_to(target)
        fsops.delete(str(nested))
        self.assertTrue((target / 'keep').is_file())

    def test_permission_errors(self):
        # Deterministic even when the test process has elevated privileges.
        source = self.root / 'file'
        source.touch()
        for operation, arguments, mocked in (
            (fsops.create_file, (str(self.root), 'new'), 'builtins.open'),
            (fsops.create_folder, (str(self.root), 'new'), 'idgent.fsops.os.mkdir'),
            (fsops.rename, (str(source), 'new'), 'idgent.fsops._rename_no_replace'),
            (fsops.delete, (str(source),), 'idgent.fsops.os.unlink'),
        ):
            with self.subTest(operation=operation.__name__), patch(mocked, side_effect=PermissionError()):
                with self.assertRaisesRegex(fsops.FsOpError, 'Permission denied'):
                    operation(*arguments)

    def test_trash_uses_gio_for_files_folders_and_symlinks(self):
        destination = self.root / 'mock-trash'
        destination.mkdir()
        (self.root / 'file').write_text('keep contents')
        (self.root / 'folder').mkdir()
        (self.root / 'folder' / 'child').touch()
        (self.root / 'link').symlink_to(self.root / 'file')
        calls = []

        def move(file, cancellable):
            path = Path(file.get_path())
            calls.append((path, cancellable))
            path.rename(destination / path.name)
            return True

        with patch.object(Gio.File, 'trash', move):
            for name in ('link', 'file', 'folder'):
                path = str(self.root / name)
                self.assertEqual(fsops.trash(path, project_root=str(self.root)), path)
                self.assertFalse(os.path.lexists(path))
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(cancel is None for _, cancel in calls))
        self.assertEqual((destination / 'file').read_text(), 'keep contents')
        self.assertTrue((destination / 'folder' / 'child').exists())
        self.assertTrue((destination / 'link').is_symlink())

    def test_trash_protects_roots_before_calling_gio(self):
        with patch.object(Gio.File, 'trash') as operation:
            for path in ('/', '/tmp/..', str(self.root)):
                with self.subTest(path=path), self.assertRaisesRegex(fsops.FsOpError, 'root'):
                    fsops.trash(path, project_root=str(self.root))
            operation.assert_not_called()

    def test_unavailable_trash_never_deletes(self):
        path = self.root / 'keep'
        path.write_text('safe')
        unsupported = GLib.Error.new_literal(Gio.io_error_quark(), 'No trash',
                                             Gio.IOErrorEnum.NOT_SUPPORTED)
        denied = GLib.Error.new_literal(Gio.io_error_quark(), 'Permission denied',
                                        Gio.IOErrorEnum.PERMISSION_DENIED)
        for failure, expected in ((unsupported, fsops.TrashUnavailable),
                                  (denied, fsops.FsOpError), (None, fsops.TrashUnavailable)):
            with self.subTest(failure=failure), patch.object(
                    Gio.File, 'trash', side_effect=failure, return_value=False):
                with self.assertRaises(expected) as caught:
                    fsops.trash(str(path))
                if failure is denied:
                    self.assertNotIsInstance(caught.exception, fsops.TrashUnavailable)
                self.assertEqual(path.read_text(), 'safe')


if __name__ == '__main__':
    unittest.main()
