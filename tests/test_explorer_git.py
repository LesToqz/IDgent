"""Explorer decorations and worker lifecycle on a real GTK main context."""
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ['GDK_BACKEND'] = 'x11'
import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, Gio, Gtk, Pango

from idgent import gitstatus, style
from idgent.explorer import ExplorerPanel
from idgent.themes import get_theme
from test_gitstatus import RepoTest


class ExplorerGitTests(RepoTest):
    def setUp(self):
        super().setUp()
        style.apply_theme(get_theme('idgent-dark'))
        self.addCleanup(style.apply_style)
        self.new = self.write('untracked.py')
        self.modified = self.write('clean.txt', 'changed\n')
        self.nested = self.write('collapsed/inner/nested.py')
        self.panel = ExplorerPanel(str(self.root))
        self.addCleanup(self.panel.destroy)
        self.assertTrue(self.poll(lambda: self.new in self.panel._git_snapshot.files))

    @staticmethod
    def poll(predicate, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            while Gtk.events_pending():
                Gtk.main_iteration()
            if predicate():
                return True
            time.sleep(.005)
        return False

    def cells(self, path):
        row = self.panel._find(str(path))
        self.assertIsNotNone(row)
        column = self.panel.tree.get_column(0)
        column.cell_set_cell_data(self.panel.store, row, False, False)
        return self.panel._text_cell

    def assert_decoration(self, path, kind=None, theme='idgent-dark'):
        cell = self.cells(path)
        self.assertEqual(cell.props.text, Path(path).name)
        self.assertEqual(cell.props.foreground_set, kind is not None)
        if kind:
            expected = Gdk.RGBA()
            expected.parse(get_theme(theme).ui['git_' + kind])
            self.assertTrue(cell.props.foreground_rgba.equal(expected),
                            (cell.props.foreground_rgba.to_string(), expected.to_string()))

    def test_name_colours_and_undecorated_row_reset(self):
        self.assert_decoration(self.new, 'new')
        self.assert_decoration(self.modified, 'modified')
        clean = self.root / 'unchanged.txt'
        clean.write_text('clean')
        self.git('add', 'unchanged.txt')
        self.git('commit', '-qm', 'clean row')
        self.panel.refresh()
        self.assertTrue(self.poll(lambda: not self.panel._git_running and not self.panel._git_timeout))
        self.assert_decoration(clean)
        self.assertEqual(self.panel.store.get_n_columns(), 3)

    def test_long_name_uses_right_edge_without_a_badge_gap(self):
        path = self.write('a_very_long_untracked_filename_' * 4 + '.py')
        self.panel.refresh()
        self.assertTrue(self.poll(lambda: path in self.panel._git_snapshot.files))
        window = Gtk.OffscreenWindow()
        window.set_default_size(300, 400)
        window.add(self.panel)
        self.addCleanup(window.destroy)
        window.show_all()
        tree = self.panel.tree
        self.assertTrue(self.poll(lambda: tree.get_allocated_width() > 1))
        column = tree.get_column(0)
        cells = column.get_cells()
        self.assertEqual(len(cells), 2)
        self.assertIsInstance(cells[0], Gtk.CellRendererPixbuf)
        self.assertIs(cells[1], self.panel._text_cell)
        self.assertEqual(self.cells(path).props.ellipsize, Pango.EllipsizeMode.END)
        start, width = column.cell_get_position(self.panel._text_cell)
        self.assertEqual(start + width, column.get_width())
        self.assertLess(column.get_width(), 300)
        self.assert_decoration(path, 'new')

    def test_ignored_files_keep_normal_name_and_foreground(self):
        self.write('.gitignore', '*.ignored\n')
        ignored = self.write('file.ignored')
        self.panel.refresh()
        self.assertTrue(self.poll(lambda: not self.panel._git_running and not self.panel._git_timeout))
        self.assert_decoration(ignored)

    def test_explicit_refresh_after_staging(self):
        previous = self.panel._git_snapshot
        self.git('add', 'untracked.py')
        self.panel.refresh_git()
        self.assertTrue(self.poll(lambda: self.panel._git_snapshot is not previous and
                                  self.panel._git_snapshot.files.get(self.new) == gitstatus.Status('new')))
        self.assert_decoration(self.new, 'new')

    def test_metadata_monitor_detects_staging_and_commit(self):
        self.assertIsNotNone(self.panel._git_monitor)
        previous = self.panel._git_snapshot
        self.git('add', 'untracked.py')
        self.assertTrue(self.poll(lambda: self.panel._git_snapshot is not previous and
                                  self.panel._git_snapshot.files.get(self.new) == gitstatus.Status('new')))
        self.git('commit', '-qm', 'new file')
        self.assertTrue(self.poll(lambda: self.new not in self.panel._git_snapshot.files))
        self.assert_decoration(self.new)

    def test_collapsed_folder_and_lazily_loaded_descendants(self):
        folder = self.root / 'collapsed'
        self.assertNotIn(str(folder), self.panel._loaded)
        self.assert_decoration(folder, 'new')
        self.assertIsNone(self.panel._find(self.nested))
        self.panel.set_current_file(self.nested)
        self.assert_decoration(self.nested, 'new')
        self.assert_decoration(folder / 'inner', 'new')
        self.assert_decoration(self.root, 'modified')

    def test_live_theme_switch_changes_foregrounds(self):
        for theme in ('dark-plus', 'idgent-light', 'solarized-light', 'idgent-dark'):
            style.apply_theme(get_theme(theme))
            self.assert_decoration(self.new, 'new', theme)
            self.assert_decoration(self.modified, 'modified', theme)

    def test_no_repo_root_switch_clears_immediately_and_drops_old_result(self):
        with tempfile.TemporaryDirectory() as outside:
            path = Path(outside) / 'plain.txt'
            path.touch()
            old = self.panel._git_snapshot
            generation = self.panel._git_generation
            self.panel.set_root(outside)
            self.assert_decoration(path)
            self.panel._deliver_git(generation, old)
            self.assertEqual(self.panel._git_snapshot.files, {})
            self.assertTrue(self.poll(lambda: not self.panel._git_timeout and not self.panel._git_running))
            self.assert_decoration(path)
            self.assertIsNone(self.panel._git_monitor)

    def test_missing_git_clears_existing_decorations(self):
        with patch.dict(os.environ, PATH=''):
            self.panel.refresh_git()
            self.assertTrue(self.poll(lambda: not self.panel._git_snapshot.files))
            self.assert_decoration(self.new)
            self.assertIsNone(self.panel._git_monitor)

    def test_renamed_and_conflicted_rows_keep_status_colours(self):
        self.git('checkout', '--', 'clean.txt')
        self.git('mv', 'clean.txt', 'renamed.txt')
        self.panel.refresh()
        renamed = str(self.root / 'renamed.txt')
        self.assertTrue(self.poll(lambda: renamed in self.panel._git_snapshot.files))
        self.assert_decoration(renamed, 'modified')
        self.git('commit', '-qm', 'rename')
        self.git('checkout', '-qb', 'other')
        self.write('renamed.txt', 'other\n')
        self.git('commit', '-qam', 'other')
        self.git('checkout', '-q', 'base')
        self.write('renamed.txt', 'base change\n')
        self.git('commit', '-qam', 'base change')
        self.git('merge', 'other', check=False)
        self.panel.refresh_git()
        self.assertTrue(self.poll(lambda: self.panel._git_snapshot.files.get(renamed) == gitstatus.Status('conflict')))
        self.assert_decoration(renamed, 'conflict')
        self.assert_decoration(self.root, 'conflict')

    def test_disable_clears_colours_and_reenable_scans(self):
        generation = self.panel._git_generation
        old = self.panel._git_snapshot
        self.panel.set_git_enabled(False)
        self.assert_decoration(self.new)
        self.panel._deliver_git(generation, old)
        self.assert_decoration(self.new)
        self.assertEqual(self.panel._git_timeout, 0)
        self.panel.set_git_enabled(True)
        self.assertTrue(self.poll(lambda: self.new in self.panel._git_snapshot.files))
        self.assert_decoration(self.new, 'new')

    def test_file_monitor_tracks_repeated_atomic_disk_replacements(self):
        self.panel.set_current_file(str(self.root / 'clean.txt'))
        self.git('checkout', '--', 'clean.txt')
        self.assertTrue(self.poll(lambda: self.modified not in self.panel._git_snapshot.files))
        for text in ('first replacement', 'second replacement'):
            replacement = self.root / 'replacement'
            replacement.write_text(text)
            replacement.replace(self.root / 'clean.txt')
            self.assertTrue(self.poll(lambda: self.modified in self.panel._git_snapshot.files))
            self.git('checkout', '--', 'clean.txt')
            self.assertTrue(self.poll(lambda: self.modified not in self.panel._git_snapshot.files))

    def test_create_rename_delete_and_refresh_button_schedule_scans(self):
        created = self.panel._create(str(self.root), 'created.py')
        self.assertTrue(self.poll(lambda: created in self.panel._git_snapshot.files))
        self.assert_decoration(created, 'new')
        renamed = self.panel._rename(created, 'renamed.py')
        self.assertTrue(self.poll(lambda: renamed in self.panel._git_snapshot.files and
                                 created not in self.panel._git_snapshot.files))
        self.panel._delete(renamed)
        self.assertTrue(self.poll(lambda: renamed not in self.panel._git_snapshot.files))
        self.write('button.py')
        actions = self.panel.get_children()[0].get_children()[0].get_children()
        next(button for button in actions if button.get_tooltip_text() == 'Refresh').clicked()
        self.assertTrue(self.poll(lambda: str(self.root / 'button.py') in self.panel._git_snapshot.files))

    def test_bursts_coalesce_worker_is_off_main_thread_and_stale_result_dropped(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        latest = gitstatus.Snapshot(files={self.new: gitstatus.Status('new')})

        def slow(root):
            calls.append(threading.current_thread())
            if len(calls) == 1:
                entered.set()
                release.wait(3)
                return gitstatus.Snapshot(files={self.new: gitstatus.Status('conflict')})
            return latest

        with patch('idgent.explorer.gitstatus.collect_status', side_effect=slow):
            self.panel.refresh_git()
            self.assertTrue(self.poll(entered.is_set))
            self.assertIsNot(calls[0], threading.main_thread())
            for _ in range(30):
                self.panel.refresh_git()
            # Main-loop work proceeds while Git is deliberately blocked.
            self.assert_decoration(self.new, 'new')
            release.set()
            self.assertTrue(self.poll(lambda: self.panel._git_snapshot is latest))
            self.assertEqual(len(calls), 2)
            self.assert_decoration(self.new, 'new')

    def test_destroy_cancels_pending_and_running_delivery(self):
        entered, release = threading.Event(), threading.Event()
        finished = threading.Event()

        def slow(root):
            entered.set()
            release.wait(3)
            finished.set()
            return gitstatus.Snapshot()

        with patch('idgent.explorer.gitstatus.collect_status', side_effect=slow):
            self.panel.refresh_git()
            self.assertTrue(self.poll(entered.is_set))
            self.panel.destroy()
            release.set()
            self.assertTrue(finished.wait(2))
            self.assertTrue(self.panel._git_closed)
            self.assertEqual(self.panel._git_timeout, 0)
            self.assertEqual(self.panel._git_idle, 0)
            self.panel.refresh_git()
            self.assertEqual(self.panel._git_timeout, 0)

    def test_window_wires_editor_save_and_focus_refresh(self):
        from idgent import settings
        from idgent.window import MainWindow

        app = Gtk.Application.get_default() or Gtk.Application(
            application_id='dev.idgent.ExplorerGitTests', flags=Gio.ApplicationFlags.NON_UNIQUE)
        if not app.get_is_registered():
            self.assertTrue(app.register(None))
        values = dict(settings.get_values(), terminal_autostart=False)
        with patch.object(settings, 'get_values', return_value=values):
            window = MainWindow(app, str(self.root))
        self.addCleanup(window.destroy)
        path = str(self.root / 'clean.txt')
        window.editor.open_file(path)
        tab = window.editor.tabs[0]
        with patch.object(window.explorer, 'refresh_git', wraps=window.explorer.refresh_git) as refresh:
            tab.buffer.set_text('saved from editor\n')
            self.assertTrue(window.editor.save_current())
            refresh.assert_called()
            refresh.reset_mock()
            event = Gdk.Event.new(Gdk.EventType.FOCUS_CHANGE)
            window.emit('focus-in-event', event)
            refresh.assert_called_once_with()
        self.assertTrue(self.poll(lambda: path in window.explorer._git_snapshot.files))
        self.assertEqual(window.explorer._git_snapshot.files[path], gitstatus.Status('modified'))

    def test_hidden_filter_and_search_have_no_git_renderer(self):
        hidden = self.write('.hidden.py')
        self.panel.refresh()
        self.assertTrue(self.poll(lambda: hidden in self.panel._git_snapshot.files))
        self.assert_decoration(hidden, 'new')
        self.panel.set_show_hidden(False)
        self.assertIsNone(self.panel._find(hidden))
        self.panel.focus_search('changed')
        self.assertNotIn(self.panel._text_cell,
                         [cell for column in self.panel.search.tree.get_columns() for cell in column.get_cells()])
