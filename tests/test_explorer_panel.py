import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

# Keep GTK on the Xvfb display even when WAYLAND_DISPLAY is inherited.
os.environ["GDK_BACKEND"] = "x11"

import gi

gi.require_version('Gtk', '3.0')
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk

from idgent.explorer import ExplorerPanel
from idgent.fsops import FsOpError


class ExplorerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'Zdir' / 'inner').mkdir(parents=True)
        (self.root / 'adir').mkdir()
        (self.root / 'Zdir' / 'inner' / 'deep.py').touch()
        for name in ('b.py', 'A.py', '.hidden'):
            (self.root / name).touch()
        self.panel = ExplorerPanel(str(self.root))
        self.addCleanup(self.panel.destroy)

    def children(self, path):
        row = self.panel._find(str(path))
        return [child[:] for child in self.panel.store[row].iterchildren()]

    def expand(self, path):
        row = self.panel._find(str(path))
        self.panel.tree.expand_row(self.panel.store.get_path(row), False)

    def test_root_sort_and_hidden_files(self):
        root = self.panel.store.get_iter_first()
        self.assertEqual(self.panel.store[root][1], self.root.name)
        self.assertEqual([row[1] for row in self.children(self.root)], ['adir', 'Zdir', '.hidden', 'A.py', 'b.py'])
        self.assertEqual([row[0] for row in self.children(self.root)][:2], ['folder', 'folder'])

    def test_lazy_loading_one_scandir_per_expansion(self):
        folder = self.root / 'Zdir'
        self.assertEqual(self.children(folder), [[None, '', '']])
        with patch('idgent.explorer.os.scandir', wraps=os.scandir) as scan:
            self.expand(folder)
            self.assertEqual(scan.call_count, 1)
            self.assertEqual([row[1] for row in self.children(folder)], ['inner'])
            self.assertEqual(self.children(folder / 'inner'), [[None, '', '']])
            row = self.panel._find(str(folder))
            self.panel.tree.collapse_row(self.panel.store.get_path(row))
            self.expand(folder)
            self.assertEqual(scan.call_count, 1)

    def test_create_selects_file_and_folder(self):
        folder = self.root / 'Zdir'
        path = self.panel._create(str(folder), 'new.txt')
        self.assertTrue(Path(path).is_file())
        self.assertEqual(self.panel._selected_path(), path)
        self.assertIn(str(folder), self.panel._expanded_paths())
        created = self.panel._create(str(folder), 'new-folder', True)
        self.assertTrue(Path(created).is_dir())
        self.assertEqual(self.panel._selected_path(), created)

    def test_rename_delete_and_folder_signals(self):
        renamed, deleted = [], []
        self.panel.connect('path-renamed', lambda panel, old, new: renamed.append((old, new)))
        self.panel.connect('path-deleted', lambda panel, path: deleted.append(path))
        for name in ('A.py', 'Zdir'):
            old = str(self.root / name)
            new = self.panel._rename(old, 'renamed-' + name)
            self.assertEqual(renamed[-1], (old, new))
            self.assertIsNone(self.panel._find(old))
            self.assertEqual(self.panel._selected_path(), new)
            self.panel._delete(new)
            self.assertFalse(os.path.exists(new))
            self.assertEqual(deleted[-1], new)
        self.assertEqual(len(renamed), 2)
        self.assertEqual(len(deleted), 2)

    def test_root_protection_and_failed_operations_do_not_emit(self):
        signals = []
        self.panel.connect('path-renamed', lambda *args: signals.append(args))
        self.panel.connect('path-deleted', lambda *args: signals.append(args))
        with self.assertRaises(FsOpError):
            self.panel._rename(str(self.root), 'other')
        with self.assertRaises(FsOpError):
            self.panel._delete(str(self.root))
        with self.assertRaises(FsOpError):
            self.panel._rename(str(self.root / 'A.py'), 'b.py')
        with self.assertRaises(FsOpError):
            self.panel._create(str(self.root), 'A.py')
        self.assertEqual(signals, [])

    def test_activation_files_and_folder_toggle(self):
        activated = []
        self.panel.connect('file-activated', lambda panel, path: activated.append(path))
        file = str(self.root / 'A.py')
        row = self.panel._find(file)
        self.panel.tree.row_activated(self.panel.store.get_path(row), self.panel.tree.get_column(0))
        self.assertEqual(activated, [file])
        folder = str(self.root / 'Zdir')
        row = self.panel._find(folder)
        path = self.panel.store.get_path(row)
        self.panel.tree.row_activated(path, self.panel.tree.get_column(0))
        self.assertTrue(self.panel.tree.row_expanded(path))
        self.panel.tree.row_activated(path, self.panel.tree.get_column(0))
        self.assertFalse(self.panel.tree.row_expanded(path))
        self.assertEqual(activated, [file])

    def test_refresh_and_rename_preserve_nested_expansion(self):
        folder = self.root / 'Zdir'
        self.expand(folder)
        self.expand(folder / 'inner')
        self.panel._select(str(folder / 'inner' / 'deep.py'))
        expanded = self.panel._expanded_paths()
        self.panel.refresh()
        self.assertEqual(self.panel._expanded_paths(), expanded)
        self.assertEqual(self.panel._selected_path(), str(folder / 'inner' / 'deep.py'))
        new = self.panel._rename(str(folder), 'newdir')
        self.assertIn(str(Path(new) / 'inner'), self.panel._expanded_paths())
        self.assertIsNotNone(self.panel._find(str(Path(new) / 'inner' / 'deep.py')))

    def test_new_targets_and_set_root(self):
        self.panel.tree.get_selection().unselect_all()
        self.assertEqual(self.panel._target_folder(), str(self.root))
        self.panel._select(str(self.root / 'A.py'))
        self.assertEqual(self.panel._target_folder(), str(self.root))
        self.panel._select(str(self.root / 'Zdir'))
        self.assertEqual(self.panel._target_folder(), str(self.root / 'Zdir'))
        self.panel.set_root(str(self.root / 'Zdir'))
        self.assertEqual(self.panel.root, str(self.root / 'Zdir'))
        self.assertEqual([row[1] for row in self.children(self.root / 'Zdir')], ['inner'])

    def test_permission_error_and_symlink_cycle(self):
        with patch('idgent.explorer.os.scandir', side_effect=PermissionError()):
            self.expand(self.root / 'Zdir')
        self.assertEqual(self.children(self.root / 'Zdir'), [])
        link = self.root / 'loop'
        link.symlink_to(self.root)
        self.panel.refresh()
        self.assertEqual(self.children(link), [])

    def test_name_dialog_keeps_errors_open_and_selects_stem(self):
        attempts = []
        runs = []

        def action(name):
            attempts.append(name)
            if len(attempts) == 1:
                raise FsOpError('Already exists')
            return name

        def respond(dialog):
            content = dialog.get_content_area().get_children()
            entry = next(widget for widget in content if isinstance(widget, Gtk.Entry))
            error = next(widget for widget in content if isinstance(widget, Gtk.Label))
            runs.append(error.get_text())
            if len(runs) == 1:
                self.assertEqual(entry.get_selection_bounds(), (0, 6))
                entry.set_text('new.py')
            return Gtk.ResponseType.OK

        with patch.object(Gtk.Dialog, 'run', respond):
            self.panel._name_dialog('Rename', 'sample.py', action)
        self.assertEqual(attempts, ['new.py', 'new.py'])
        self.assertEqual(runs, ['', 'Already exists'])

    def test_shortcuts(self):
        for key, state, method, args in (
            (Gdk.KEY_F2, 0, '_rename_dialog', ()),
            (Gdk.KEY_Delete, 0, '_delete_dialog', ()),
            (Gdk.KEY_F5, 0, 'refresh', ()),
            (Gdk.KEY_N, Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK, '_new_dialog', (True,)),
        ):
            event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
            event.keyval, event.state = key, state
            with patch.object(self.panel, method) as action:
                self.assertTrue(self.panel._on_key(self.panel.tree, event))
                action.assert_called_once_with(*args)


if __name__ == '__main__':
    unittest.main()
