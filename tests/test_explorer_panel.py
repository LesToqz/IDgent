import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

# Keep GTK on the Xvfb display even when WAYLAND_DISPLAY is inherited.
os.environ["GDK_BACKEND"] = "x11"

import gi

gi.require_version('Gtk', '3.0')
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk

from idgent.explorer import ExplorerPanel, build_css
from idgent import fsops, style
from idgent.fsops import FsOpError
from idgent.themes import THEMES, get_theme


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


    def test_search_box_under_header_and_forwarding(self):
        children = self.panel.get_children()
        self.assertIs(children[1], self.panel.search)
        self.assertIs(self.panel.search.get_children()[0].get_children()[0], self.panel.search.entry)
        received = []
        self.panel.connect('search-result-activated', lambda _, *args: received.append(args))
        self.panel.search.emit('result-activated', str(self.root / 'b.py'), 3, 4, 5)
        self.assertEqual(received, [(str(self.root / 'b.py'), 3, 4, 5)])
        escaped = []
        self.panel.connect('search-escaped', lambda *_: escaped.append(1))
        self.panel.search.emit('escaped')
        self.assertEqual(escaped, [1])
        self.panel.set_current_file(str(self.root / 'A.py'))
        self.assertEqual(self.panel.search.current_path, str(self.root / 'A.py'))
        self.panel.set_current_file(None)
        self.assertIsNone(self.panel.search.current_path)
        self.panel.set_font_size(13)
        self.assertEqual(self.panel.search.font_size, 13)
        other = self.root / 'Zdir'
        self.panel.set_root(str(other))
        self.assertEqual(self.panel.search.root, str(other))

    def test_focus_search_with_query_and_clear(self):
        (self.root / 'b.py').write_text('needle')
        self.panel.focus_search('needle')
        self.assertEqual(self.panel.search.query, 'needle')
        self.assertEqual(self.panel.search.results.total_matches, 1)
        self.panel.clear_search()
        self.assertEqual(self.panel.search.query, '')
        self.assertEqual(self.panel.search.stack.get_visible_child_name(), 'content')

    def test_shortcuts_ignored_when_search_has_focus(self):
        window = Gtk.Window()
        window.add(self.panel)
        window.show_all()
        self.addCleanup(window.destroy)
        event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        event.keyval, event.state = Gdk.KEY_F2, 0
        for widget in (self.panel.search.entry, self.panel.search.tree):
            widget.grab_focus()
            with patch.object(self.panel, '_rename_dialog') as action:
                self.assertFalse(self.panel._on_key(self.panel.tree, event))
                action.assert_not_called()
        self.panel.tree.grab_focus()
        with patch.object(self.panel, '_rename_dialog') as action:
            self.assertTrue(self.panel._on_key(self.panel.tree, event))
            action.assert_called_once()

    def test_hidden_preference_filters_lazy_children_and_preserves_expansion(self):
        folder = self.root / 'Zdir'
        (folder / '.secret').touch()
        (self.root / '.private').mkdir()
        self.panel.refresh()
        self.expand(folder)
        self.panel._select(str(self.root / 'A.py'))
        self.panel.set_show_hidden(False)
        self.assertIsNone(self.panel._find(str(self.root / '.hidden')))
        self.assertIsNone(self.panel._find(str(self.root / '.private')))
        self.assertIsNone(self.panel._find(str(folder / '.secret')))
        self.assertIn(str(folder), self.panel._expanded_paths())
        self.assertEqual(self.panel._selected_path(), str(self.root / 'A.py'))
        self.panel.set_show_hidden(True)
        self.assertIsNotNone(self.panel._find(str(folder / '.secret')))
        self.assertIsNotNone(self.panel._find(str(self.root / '.hidden')))

    def test_current_file_reveals_nested_path_without_activating_it(self):
        activated = []
        self.panel.connect('file-activated', lambda _, path: activated.append(path))
        path = str(self.root / 'Zdir' / 'inner' / 'deep.py')
        self.panel.set_current_file(path)
        self.assertEqual(self.panel._selected_path(), path)
        self.assertIn(str(self.root / 'Zdir' / 'inner'), self.panel._expanded_paths())
        self.assertEqual(self.panel.search.current_path, path)
        self.panel.set_current_file(None)
        self.assertIsNone(self.panel._selected_path())
        self.panel.set_current_file('/outside-project.py')
        self.assertIsNone(self.panel._selected_path())
        self.assertEqual(activated, [])

    def test_delete_defaults_confirmation_and_cancel(self):
        path = self.root / 'A.py'
        self.panel._select(str(path))
        self.assertTrue(self.panel.show_hidden)
        self.assertTrue(self.panel.confirm_delete)
        self.assertEqual(self.panel.delete_mode, 'delete')
        with patch.object(Gtk.Dialog, 'run', return_value=Gtk.ResponseType.CANCEL) as prompt:
            self.panel._delete_dialog()
            prompt.assert_called_once()
        self.assertTrue(path.exists())
        with patch.object(Gtk.Dialog, 'run', return_value=Gtk.ResponseType.OK) as prompt:
            self.panel._delete_dialog()
            prompt.assert_called_once()
        self.assertFalse(path.exists())

    def test_disable_confirmation_skips_prompt_and_invalid_mode_keeps_setting(self):
        path = self.root / 'A.py'
        self.panel._select(str(path))
        self.panel.set_confirm_delete(False)
        with self.assertRaises(ValueError):
            self.panel.set_delete_mode('invalid')
        self.assertEqual(self.panel.delete_mode, 'delete')
        with patch.object(Gtk.Dialog, 'run') as prompt:
            self.panel._delete_dialog()
            prompt.assert_not_called()
        self.assertFalse(path.exists())

    def test_trash_success_updates_tree_and_emits_once(self):
        path = self.root / 'A.py'
        destination = self.root / 'trashed.py'
        self.panel._select(str(path))
        self.panel.set_delete_mode('trash')
        self.panel.set_confirm_delete(False)
        deleted = []
        self.panel.connect('path-deleted', lambda _, item: deleted.append(item))

        def move(file, cancellable):
            Path(file.get_path()).rename(destination)
            return True

        with patch.object(fsops.Gio.File, 'trash', move), patch.object(Gtk.Dialog, 'run') as prompt:
            self.panel._delete_dialog()
            prompt.assert_not_called()
        self.assertEqual(deleted, [str(path)])
        self.assertIsNone(self.panel._find(str(path)))
        self.assertTrue(destination.exists())

    def test_trash_fallback_requires_separate_permanent_confirmation(self):
        for confirm in (False, True):
            for accept in (False, True):
                with self.subTest(confirm=confirm, accept=accept):
                    path = self.root / 'A.py'
                    path.write_text('keep')
                    self.panel.refresh()
                    self.panel._select(str(path))
                    self.panel.set_delete_mode('trash')
                    self.panel.set_confirm_delete(confirm)
                    prompts = []

                    def respond(dialog):
                        prompts.append((dialog.props.text, dialog.props.secondary_text))
                        if 'Move ' in dialog.props.text:
                            return Gtk.ResponseType.OK
                        return Gtk.ResponseType.OK if accept else Gtk.ResponseType.CANCEL

                    with patch.object(fsops.Gio.File, 'trash', return_value=False), \
                            patch.object(Gtk.Dialog, 'run', respond):
                        self.panel._delete_dialog()
                    self.assertEqual(len(prompts), 2 if confirm else 1)
                    self.assertIn('Delete permanently instead?', prompts[-1][1])
                    self.assertEqual(path.exists(), not accept)

    def test_trash_permission_failure_reports_error_without_fallback(self):
        path = self.root / 'A.py'
        self.panel._select(str(path))
        self.panel.set_delete_mode('trash')
        self.panel.set_confirm_delete(False)
        with patch.object(fsops, 'trash', side_effect=FsOpError('Permission denied')), \
                patch.object(self.panel, '_confirm_removal') as confirm, \
                patch.object(Gtk.Dialog, 'run', return_value=Gtk.ResponseType.CLOSE) as error:
            self.panel._delete_dialog()
            confirm.assert_not_called()
            error.assert_called_once()
        self.assertTrue(path.exists())

    @staticmethod
    def poll(predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            while Gtk.events_pending():
                Gtk.main_iteration()
            if predicate():
                return True
            time.sleep(.005)
        return False

    def show_panel(self, theme='idgent-dark'):
        style.apply_theme(get_theme(theme))
        window = Gtk.Window()
        window.set_default_size(300, 400)
        window.add(self.panel)
        self.addCleanup(window.destroy)
        window.show_all()
        window.present()
        self.assertTrue(self.poll(window.is_active))
        return window

    def row_pixel(self, window, tree, x, y):
        x, y = tree.convert_bin_window_to_widget_coords(x, y)
        x, y = tree.translate_coordinates(window, x, y)
        pixels = Gdk.pixbuf_get_from_window(window.get_window(), x, y, 1, 1)
        return tuple(pixels.get_pixels()[:3])

    def test_tree_geometry_rounded_selection_hover_and_live_themes(self):
        window = self.show_panel()
        tree = self.panel.tree
        self.panel._select(str(self.root / 'A.py'))
        self.panel.search.entry.grab_focus()
        window.set_focus_visible(False)
        path = tree.get_selection().get_selected_rows()[1][0]
        for name in ('idgent-dark', 'idgent-light'):
            with self.subTest(theme=name):
                theme = get_theme(name)
                style.apply_theme(theme)
                self.assertTrue(self.poll(lambda: tree.get_allocated_width() > 1))
                area = tree.get_background_area(path, tree.get_column(0))
                self.assertTrue(24 <= area.height <= 26, area.height)
                self.assertEqual(tree.get_margin_start(), 12)
                self.assertEqual(tree.get_margin_end(), 12)
                sidebar = tuple(int(theme.ui['sidebar_bg'][i:i + 2], 16) for i in (1, 3, 5))
                overlay = tuple(int(theme.ui['overlay'][i:i + 2], 16) for i in (1, 3, 5))
                tint = tuple(round(a * .933 + b * .067) for a, b in zip(sidebar, overlay))
                corner = lambda: self.row_pixel(window, tree, area.x, area.y)
                centre = lambda: self.row_pixel(window, tree, area.width - 15, area.y + area.height // 2)
                self.assertTrue(self.poll(lambda: corner() == sidebar), (corner(), sidebar))
                self.assertTrue(self.poll(lambda: all(abs(a - b) <= 1 for a, b in zip(centre(), tint))))
                # A real pointer hover produces the same tint, without a timed fade.
                tree.get_selection().unselect_all()
                origin = tree.get_bin_window().get_origin()
                pointer = Gdk.Display.get_default().get_default_seat().get_pointer()
                pointer.warp(Gdk.Screen.get_default(), origin.x + area.width - 15,
                             origin.y + area.y + area.height // 2)
                self.assertTrue(self.poll(lambda: centre() == tint))
                pointer.warp(Gdk.Screen.get_default(), 0, 0)
                self.assertTrue(self.poll(lambda: centre() == sidebar))
                self.panel._select(str(self.root / 'A.py'))
                self.panel.search.entry.grab_focus()

    def test_header_icons_accessibility_and_instant_css_for_all_themes(self):
        self.show_panel()
        header = self.panel.get_children()[0]
        self.assertEqual(header.get_accessible().get_name(), 'EXPLORER')
        self.assertEqual(header.get_tooltip_text(), 'EXPLORER')
        actions = header.get_children()[0]
        self.assertEqual(actions.get_halign(), Gtk.Align.CENTER)
        self.assertEqual([button.get_accessible().get_name() for button in actions.get_children()],
                         ['New File', 'New Folder', 'Refresh'])
        for theme in THEMES:
            with self.subTest(theme=theme.id):
                provider = Gtk.CssProvider()
                errors = []
                provider.connect('parsing-error', lambda *args: errors.append(args[-1]))
                provider.load_from_data(style.build_css(theme).encode())
                self.assertEqual(errors, [])
                self.assertNotIn('transition:', build_css(theme.ui))

    def test_header_actions_create_in_selected_folder_and_refresh(self):
        self.show_panel()
        folder = self.root / 'Zdir'
        self.panel._select(str(folder))
        new_file, new_folder, refresh = self.panel.get_children()[0].get_children()[0].get_children()

        def accept_name(title, initial, action):
            action('created.txt' if title == 'New File' else 'created-folder')

        with patch.object(self.panel, '_name_dialog', side_effect=accept_name):
            new_file.clicked()
            self.assertTrue((folder / 'created.txt').is_file())
            self.assertEqual(self.panel._selected_path(), str(folder / 'created.txt'))
            new_folder.clicked()
            self.assertTrue((folder / 'created-folder').is_dir())
            self.assertEqual(self.panel._selected_path(), str(folder / 'created-folder'))

        external = folder / 'external.txt'
        external.touch()
        self.assertIsNone(self.panel._find(str(external)))
        refresh.clicked()
        self.assertIsNotNone(self.panel._find(str(external)))
        self.assertEqual(self.panel._selected_path(), str(folder / 'created-folder'))

    def test_delete_menu_item_inherits_base_padding_radius_and_danger(self):
        self.show_panel()
        self.panel._select(str(self.root / 'A.py'))
        menus = []
        with patch.object(Gtk.Menu, 'popup_at_widget', lambda menu, *args: menus.append(menu)):
            self.panel._menu()
        menu = menus[0]
        self.addCleanup(menu.destroy)
        item = next(item for item in menu.get_children() if item.get_label() == 'Delete')
        self.assertTrue(item.get_sensitive())
        context = item.get_style_context()
        self.assertTrue(context.has_class('destructive-action'))
        for state in (Gtk.StateFlags.NORMAL, Gtk.StateFlags.PRELIGHT):
            color = context.get_color(state)
            expected = get_theme('idgent-dark').ui['danger']
            self.assertEqual(tuple(round(c * 255) for c in (color.red, color.green, color.blue)),
                             tuple(int(expected[i:i + 2], 16) for i in (1, 3, 5)))
            padding = context.get_padding(state)
            self.assertEqual((padding.top, padding.right, padding.bottom, padding.left), (4, 8, 4, 8))
            self.assertEqual(context.get_property('border-radius', state), 4)


if __name__ == '__main__':
    unittest.main()
