"""Explorer search wired to the editor in a real MainWindow under Xvfb."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

# Keep GTK on the Xvfb display even when WAYLAND_DISPLAY is inherited.
os.environ['GDK_BACKEND'] = 'x11'

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, Gio, GLib, Gtk

from idgent import settings
from idgent.window import MainWindow

CTRL, SHIFT = Gdk.ModifierType.CONTROL_MASK, Gdk.ModifierType.SHIFT_MASK


def poll(predicate, timeout=5):
    """Pump the main loop until predicate() holds; no fixed sleeps."""
    deadline = time.monotonic() + timeout
    context = GLib.MainContext.default()
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        if predicate():
            return True
        time.sleep(.005)
    return bool(predicate())


class SearchWiringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Gtk.Application.get_default() or Gtk.Application(
            application_id='dev.idgent.SearchWiringTests', flags=Gio.ApplicationFlags.NON_UNIQUE)
        if not cls.app.get_is_registered():
            assert cls.app.register(None)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, XDG_CONFIG_HOME=str(Path(self.temp.name) / 'config'))
        env.start()
        self.addCleanup(env.stop)
        self.root = Path(self.temp.name).resolve() / 'project'
        self.root.mkdir()
        (self.root / 'a.py').write_text('alpha needle one\nline two\n  needle two\n')
        (self.root / 'b.py').write_text('needle in b\n')
        (self.root / 'notes.md').write_text('# Title\n\nsome needle here\n')
        (self.root / 'big.py').write_text(''.join(f'line {i}\n' for i in range(300))
                                          + 'the needle far below\n' + 'tail\n' * 100)
        self.windows = []
        self.addCleanup(self.cleanup_windows)
        self.window = self.make_window()

    def make_window(self, root=None):
        window = MainWindow(self.app, str(root or self.root))
        self.windows.append(window)
        window.show_all()
        poll(lambda: window.get_window() is not None and window.get_window().is_visible())
        return window

    def cleanup_windows(self):
        for window in self.windows:
            window.destroy()
        poll(lambda: False, .05)

    # helpers -----------------------------------------------------------------
    @property
    def explorer(self):
        return self.window.explorer

    @property
    def search(self):
        return self.window.explorer.search

    @property
    def editor(self):
        return self.window.editor

    def groups(self):
        return [row[3] for row in self.search.store if row[7] == 'group' and row[3]]

    def search_for(self, query='needle'):
        """Type into the entry and wait for the asynchronous search to finish."""
        self.search.entry.set_text(query)
        self.assertTrue(poll(lambda: not self.search._searching and self.search.results.total_matches
                             and self.search._timeout == 0))

    def press(self, keyval, state=0, window=None):
        keymap = Gdk.Keymap.get_for_display(Gdk.Display.get_default())
        found, keys = keymap.get_entries_for_keyval(keyval)
        event = Gdk.EventKey()
        event.type = Gdk.EventType.KEY_PRESS
        event.window = self.window.get_window()
        event.keyval, event.state = keyval, state
        event.hardware_keycode = keys[0].keycode if found else 0
        event.group = 0
        event.send_event = True
        return self.window.do_key_press_event(event)

    def path(self, name):
        return str(self.root / name)

    # tests -------------------------------------------------------------------
    def test_search_box_is_first_under_header(self):
        children = self.explorer.get_children()
        self.assertEqual(len(children), 2)
        self.assertIn('idgent-explorer-header', [c for c in children[0].get_style_context().list_classes()])
        self.assertIs(children[1], self.search)
        row = self.search.get_children()[0]
        self.assertIs(row.get_children()[0], self.search.entry)
        self.assertEqual(self.search.stack.get_visible_child_name(), 'content')
        self.assertTrue(self.explorer.tree.is_ancestor(self.search))

    def test_typing_shows_results_and_escape_restores_tree(self):
        self.editor.open_file(self.path('a.py'))
        self.explorer.focus_search()
        self.assertIs(self.window.get_focus(), self.search.entry)
        self.search_for()
        self.assertEqual(self.search.stack.get_visible_child_name(), 'results')
        self.assertFalse(self.search.stack.get_child_by_name('content').get_mapped())
        self.assertTrue(self.press(Gdk.KEY_Escape) or self.search.query == '')
        self.assertEqual(self.search.query, '')
        self.assertEqual(self.search.stack.get_visible_child_name(), 'content')
        # With a tab open, Esc hands focus back to the editor.
        self.assertTrue(self.window.get_focus().is_ancestor(self.editor))

    def test_escape_without_tabs_focuses_tree(self):
        self.explorer.focus_search()
        self.search_for()
        self.press(Gdk.KEY_Escape)
        self.assertEqual(self.search.query, '')
        self.assertIs(self.window.get_focus(), self.explorer.tree)

    def test_active_tab_group_first_and_switching_resorts(self):
        self.editor.open_file(self.path('b.py'))
        self.editor.open_file(self.path('a.py'))
        self.search_for()
        poll(lambda: len(self.groups()) == 4)
        self.assertEqual(self.groups()[0], self.path('a.py'))
        self.assertEqual(self.groups()[1:], [self.path(n) for n in ('b.py', 'big.py', 'notes.md')])
        self.editor.open_file(self.path('b.py'))
        self.assertTrue(poll(lambda: self.groups()[0] == self.path('b.py')))
        self.assertEqual(self.groups()[1], self.path('a.py'))
        self.editor.notebook.set_current_page(self.editor.notebook.page_num(self.editor.tabs[1].page))
        self.assertTrue(poll(lambda: self.groups()[0] == self.path('a.py')))
        self.assertTrue(poll(lambda: not self.search._searching and self.search._timeout == 0))
        self.assertEqual(self.groups()[0], self.path('a.py'))
        self.assertEqual(len(self.groups()), 4)
        self.assertIn('current file', self.search.store[0][2])

    def test_unsaved_edits_are_searched(self):
        self.editor.open_file(self.path('b.py'))
        tab = self.editor.tabs[0]
        tab.buffer.set_text('zebra unsaved\nneedle edited\n')
        self.assertTrue(tab.buffer.get_modified())
        self.assertEqual(self.editor.buffer_text(self.path('b.py')), 'zebra unsaved\nneedle edited\n')
        self.assertIsNone(self.editor.buffer_text(self.path('a.py')))
        self.search_for('zebra')
        self.assertEqual(self.groups(), [self.path('b.py')])
        self.assertEqual(self.search.results.groups[0].matches[0].line, 0)
        self.assertEqual((self.root / 'b.py').read_text(), 'needle in b\n')

    def test_activating_result_opens_tab_and_selects_exact_match(self):
        self.editor.open_file(self.path('b.py'))
        self.search_for()
        group = next(r for r in self.search.store if r[3] == self.path('a.py'))
        second = group.iterchildren()
        next(second)
        child = next(second)
        self.assertEqual((child[4], child[5], child[6]), (2, 2, 6))
        self.search.tree.row_activated(child.path, self.search.tree.get_column(0))
        tab = self.editor.tabs[-1]
        self.assertEqual(tab.path, self.path('a.py'))
        self.assertEqual(len(self.editor.tabs), 2)
        self.assertIs(self.editor._current(), tab)
        self.assertEqual(tab.buffer.get_text(*tab.buffer.get_selection_bounds(), True), 'needle')
        start, end = tab.buffer.get_selection_bounds()
        self.assertEqual((start.get_line(), start.get_line_offset(), end.get_line_offset()), (2, 2, 8))
        self.assertTrue(self.editor.status.get_text().startswith('Ln 3,'))
        self.assertTrue(self.window.get_focus() is tab.view)
        # Activating again focuses the existing tab instead of duplicating it.
        self.search.emit('result-activated', self.path('b.py'), 0, 0, 6)
        self.assertEqual(len(self.editor.tabs), 2)
        self.assertEqual(self.editor.current_path(), self.path('b.py'))
        self.assertTrue(self.editor.status.get_text().startswith('Ln 1,'))

    def test_result_is_scrolled_into_view_near_the_middle(self):
        self.window.resize(1000, 700)
        self.search.emit('result-activated', self.path('big.py'), 300, 4, 6)
        tab = self.editor.tabs[0]
        self.assertEqual(self.editor.status.get_text()[:7], 'Ln 301,')

        def position():
            rect = tab.view.get_visible_rect()
            y, _ = tab.view.get_line_yrange(tab.buffer.get_iter_at_line(300))
            return (y - rect.y) / rect.height if rect.height > 0 else -1
        self.assertTrue(poll(lambda: .3 < position() < .7), position())

    def test_markdown_preview_switches_to_source(self):
        self.editor.open_file(self.path('notes.md'))
        self.editor.set_markdown_mode('preview')
        self.assertEqual(self.editor.markdown_mode(), 'preview')
        self.search.emit('result-activated', self.path('notes.md'), 2, 5, 6)
        self.assertEqual(self.editor.markdown_mode(), 'source')
        tab = self.editor.tabs[0]
        self.assertEqual(tab.buffer.get_text(*tab.buffer.get_selection_bounds(), True), 'needle')
        self.assertIs(self.window.get_focus(), tab.view)
        # The same via a background tab.
        self.editor.open_file(self.path('a.py'))
        self.editor.set_markdown_mode('preview', self.path('notes.md'))
        self.search.emit('result-activated', self.path('notes.md'), 2, 5, 6)
        self.assertEqual(self.editor.markdown_mode(self.path('notes.md')), 'source')
        self.assertEqual(self.editor.current_path(), self.path('notes.md'))

    def test_open_file_at_clamps(self):
        self.editor.open_file_at(self.path('a.py'), 999, 999, 999)
        tab = self.editor.tabs[0]
        self.assertFalse(tab.buffer.get_has_selection())
        self.editor.open_file_at(self.path('a.py'), 0, 6, 999)
        self.assertEqual(tab.buffer.get_text(*tab.buffer.get_selection_bounds(), True), 'needle one')
        self.editor.open_file_at(self.path('a.py'), -4, -4, -1)
        self.assertFalse(tab.buffer.get_has_selection())
        self.assertEqual(tab.cursor(), 0)

    def test_current_file_signal(self):
        seen = []
        self.editor.connect('current-file-changed', lambda _, path: seen.append(path))
        self.assertIsNone(self.editor.current_path())
        self.editor.open_file(self.path('a.py'))
        self.editor.open_file(self.path('b.py'))
        self.editor.open_file(self.path('a.py'))
        self.editor.open_file(self.path('a.py'))
        self.assertEqual(seen, [self.path('a.py'), self.path('b.py'), self.path('a.py')])
        self.explorer._rename(self.path('a.py'), 'renamed.py')
        self.assertEqual(seen[-1], self.path('renamed.py'))
        self.assertEqual(self.search.current_path, self.path('renamed.py'))
        self.editor._close_tab(self.editor._current())
        self.assertEqual(seen[-1], self.path('b.py'))
        self.assertEqual(self.search.current_path, self.path('b.py'))
        self.editor._close_tab(self.editor._current())
        self.assertEqual(seen[-1], '')
        self.assertIsNone(self.search.current_path)

    def test_ctrl_shift_f_focuses_entry_shows_explorer_and_prefills_selection(self):
        self.editor.open_file(self.path('a.py'))
        tab = self.editor.tabs[0]
        tab.buffer.select_range(tab.buffer.get_iter_at_line_offset(0, 6), tab.buffer.get_iter_at_line_offset(0, 12))
        self.window.toggle_explorer()
        self.assertFalse(self.explorer.get_visible())
        self.assertTrue(self.press(Gdk.KEY_F, CTRL | SHIFT))
        self.assertTrue(self.explorer.get_visible())
        self.assertIs(self.window.get_focus(), self.search.entry)
        self.assertEqual(self.search.query, 'needle')
        self.assertEqual(self.search.stack.get_visible_child_name(), 'results')
        self.assertEqual(self.groups()[0], self.path('a.py'))
        # Lowercase keyval (no Caps) behaves the same, and a multi-line selection is not used.
        self.search.clear()
        tab.buffer.select_range(tab.buffer.get_start_iter(), tab.buffer.get_end_iter())
        self.assertTrue(self.press(Gdk.KEY_f, CTRL | SHIFT))
        self.assertEqual(self.search.query, '')
        self.assertIs(self.window.get_focus(), self.search.entry)
        # An empty selection keeps the previous query, selected for overtyping.
        self.search.entry.set_text('keep')
        tab.buffer.place_cursor(tab.buffer.get_start_iter())
        self.press(Gdk.KEY_F, CTRL | SHIFT)
        self.assertEqual(self.search.query, 'keep')

    def test_ctrl_shift_f_restores_terminal_fullscreen_and_view_menu_item(self):
        self.window.toggle_terminal_fullscreen()
        self.assertTrue(poll(lambda: self.window._fullscreen_panel is not None))
        self.assertFalse(self.explorer.get_visible())
        self.press(Gdk.KEY_F, CTRL | SHIFT)
        self.assertIsNone(self.window._fullscreen_panel)
        self.assertTrue(self.explorer.get_visible())
        self.assertIs(self.window.get_focus(), self.search.entry)
        view = self.window.titlebar.menu_bar.get_children()[1].get_submenu()
        entry = next(i for i in view.get_children() if i.get_label() == 'Search in Files')
        self.window.editor.open_file(self.path('a.py'))
        self.assertTrue(self.window.get_focus().is_ancestor(self.editor))
        entry.activate()
        self.assertIs(self.window.get_focus(), self.search.entry)

    def test_ctrl_f_is_still_in_file_find(self):
        self.editor.open_file(self.path('a.py'))
        self.assertFalse(self.editor.search_bar.get_search_mode())
        self.press(Gdk.KEY_f, CTRL)
        self.assertTrue(self.editor.search_bar.get_search_mode())
        self.assertIs(self.window.get_focus(), self.editor.search_entry)
        self.editor.search_bar.set_search_mode(False)
        self.press(Gdk.KEY_F, CTRL | SHIFT)
        self.assertFalse(self.editor.search_bar.get_search_mode())

    def test_explorer_shortcuts_do_not_act_from_search_entry_or_results(self):
        (self.root / 'keep').mkdir()
        self.explorer._select(self.path('b.py'))
        self.assertEqual(self.explorer._selected_path(), self.path('b.py'))
        self.search_for()

        # Exceptions raised inside GTK callbacks are swallowed, so record calls instead.
        with patch.object(self.explorer, '_delete_dialog') as delete, \
                patch.object(self.explorer, '_rename_dialog') as rename, \
                patch.object(self.explorer, '_new_dialog') as new, \
                patch.object(self.explorer, 'refresh') as refresh, \
                patch.object(Gtk.Dialog, 'run', return_value=Gtk.ResponseType.CANCEL) as run:
            for target in (self.search.entry, self.search.tree):
                target.grab_focus()
                self.assertIs(self.window.get_focus(), target)
                if target is self.search.entry:
                    target.set_position(-1)  # grab_focus selects all; Delete would edit the query
                for keyval, state in ((Gdk.KEY_Delete, 0), (Gdk.KEY_F2, 0), (Gdk.KEY_F5, 0),
                                      (Gdk.KEY_N, CTRL | SHIFT), (Gdk.KEY_n, CTRL | SHIFT)):
                    self.press(keyval, state)
        for action in (delete, rename, new, refresh, run):
            action.assert_not_called()
        self.assertTrue((self.root / 'b.py').exists())
        self.assertEqual(sorted(p.name for p in self.root.iterdir()),
                         ['a.py', 'b.py', 'big.py', 'keep', 'notes.md'])
        self.assertEqual(self.search.query, 'needle')
        # The tree keeps its shortcuts when it has focus.
        self.explorer.tree.grab_focus()
        with patch.object(self.explorer, '_rename_dialog') as rename:
            self.press(Gdk.KEY_F2)
            rename.assert_called_once()

    def test_rename_and_delete_refresh_results(self):
        self.editor.open_file(self.path('a.py'))
        self.search_for()
        self.assertEqual(len(self.groups()), 4)
        self.explorer._rename(self.path('b.py'), 'c.py')
        self.assertEqual(sorted(os.path.basename(p) for p in self.groups()),
                         ['a.py', 'big.py', 'c.py', 'notes.md'])
        self.assertNotIn(self.path('b.py'), self.groups())
        # Renaming the open file keeps it first under its new name.
        self.explorer._rename(self.path('a.py'), 'z.py')
        poll(lambda: not self.search._searching and self.search._timeout == 0)
        self.assertEqual(self.groups()[0], self.path('z.py'))
        self.assertNotIn(self.path('a.py'), self.groups())
        self.explorer._delete(self.path('notes.md'))
        self.assertNotIn(self.path('notes.md'), self.groups())
        # A deleted file with an open tab is not offered either.
        self.explorer._delete(self.path('z.py'))
        poll(lambda: not self.search._searching and self.search._timeout == 0)
        self.assertNotIn(self.path('z.py'), self.groups())
        self.assertEqual(sorted(os.path.basename(p) for p in self.groups()), ['big.py', 'c.py'])
        # Refresh also re-runs the query for files created behind the app's back.
        (self.root / 'new.py').write_text('a needle\n')
        self.explorer.refresh()
        self.assertIn(self.path('new.py'), self.groups())

    def test_open_folder_changes_search_root(self):
        other = Path(self.temp.name).resolve() / 'other'
        other.mkdir()
        (other / 'only.py').write_text('needle only here\n')
        self.search_for()
        self.assertEqual(len(self.groups()), 4)
        self.window.set_root(str(other))
        self.assertEqual(self.search.root, str(other))
        self.assertTrue(poll(lambda: self.groups() == [str(other / 'only.py')]
                             and not self.search._searching and self.search._timeout == 0))

    def test_ctrl_zero_resets_to_configured_editor_size_and_settings_update_it(self):
        window = self.window
        settings.save({'editor_font_size': 15})
        window = self.make_window()
        self.window = window
        self.assertEqual(window.editor.font_size, 15)
        self.assertEqual(window.editor.default_font_size, 15)
        window.editor.open_file(self.path('a.py'))
        self.press(Gdk.KEY_plus, CTRL)
        self.press(Gdk.KEY_plus, CTRL)
        self.assertEqual(window.editor.font_size, 17)
        self.press(Gdk.KEY_0, CTRL)
        self.assertEqual(window.editor.font_size, 15)
        window.open_settings()
        window._settings_dialog.emit('font-size-changed', 'editor_font_size', 13)
        self.assertEqual(window.editor.font_size, 13)
        self.assertEqual(window.editor.default_font_size, 13)
        self.press(Gdk.KEY_minus, CTRL)
        self.press(Gdk.KEY_0, CTRL)
        self.assertEqual(window.editor.font_size, 13)
        self.assertEqual(json.loads((Path(os.environ['XDG_CONFIG_HOME']) / 'idgent' / 'settings.json').read_text())
                         ['editor_font_size'], 13)

    def test_explorer_font_size_reaches_result_rows(self):
        self.explorer.set_font_size(14)
        for cell in self.search._cells:
            self.assertEqual(cell.get_property('size-points'), 14)
        self.assertEqual(self.search.font_size, 14)


if __name__ == '__main__':
    unittest.main()
