"""Search core and GTK wrapper contracts (run under X11/xvfb)."""
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from idgent.search import (MAX_FILE_SIZE, SKIP_DIRS, SEARCH_CSS, SearchError,
                           SearchPanel, build_css, find_in_text, iter_project_files, search)
from idgent.themes import THEMES, get_theme
from idgent import style
from gi.repository import Gdk, GLib, Gtk, Pango


def pump_until(condition, timeout=3):
    context = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() >= deadline:
            raise AssertionError('Timed out pumping GTK main loop')
        # A bounded GLib wait avoids arbitrary sleeps, even when no work is queued.
        wake = GLib.timeout_add(5, lambda: False)
        context.iteration(True)
        if GLib.MainContext.default().find_source_by_id(wake):
            GLib.source_remove(wake)
    while context.pending():
        context.iteration(False)


class FilesFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding='utf-8')
        return str(path)

    def search(self, query='needle', **options):
        return search(str(self.root), query, case_sensitive=options.pop('case_sensitive', False),
                      regex=options.pop('regex', False), **options)


class CoreTests(FilesFixture):
    def test_skips_directories_and_sorts_relative_paths(self):
        for name in SKIP_DIRS:
            self.write(f'{name}/hidden.txt', 'needle')
        paths = [self.write(name, 'needle') for name in ('z.txt', 'a/x.txt', 'B.txt', 'A.txt')]
        self.assertEqual(list(iter_project_files(self.root)), [paths[3], paths[1], paths[2], paths[0]])

    def test_skips_large_binary_invalid_utf8_and_symlinks(self):
        good = self.write('good', 'yes')
        self.write('large', b'x' * (MAX_FILE_SIZE + 1))
        self.write('binary', b'text\0more')
        self.write('invalid', b'x' * 9000 + b'\xff')
        (self.root / 'link').symlink_to(good)
        (self.root / 'cycle').symlink_to(self.root, target_is_directory=True)
        self.assertEqual(list(iter_project_files(self.root)), [good])

    def test_size_boundary(self):
        path = self.write('boundary', b'x' * MAX_FILE_SIZE)
        self.assertEqual(list(iter_project_files(self.root)), [path])

    def test_literal_case_regex_and_multiple_matches(self):
        self.assertEqual(find_in_text('A.b aXb a.b', 'a.b'),
                         [(0, 0, 3, 'A.b aXb a.b'), (0, 8, 3, 'aXb a.b')])
        self.assertEqual(len(find_in_text('A.b aXb a.b', 'a.b', True)), 1)
        self.assertEqual(len(find_in_text('A.b aXb a.b', 'a.b', regex=True)), 3)
        self.assertEqual(find_in_text('abc', ''), [])
        self.assertEqual(find_in_text('abc', '^|$', regex=True), [])

    def test_invalid_regex(self):
        with self.assertRaisesRegex(SearchError, 'Invalid regular expression'):
            find_in_text('x', '[', regex=True)
        with self.assertRaises(SearchError):
            self.search('[', regex=True)

    def test_unicode_character_coordinates_and_preview_offset(self):
        match = find_in_text('é\n' + 'α' * 350 + '<needle>' + 'x' * 300 + '   ', '<needle>')[0]
        self.assertEqual(match[:3], (1, 350, 8))
        self.assertLessEqual(len(match.line_text), 200)
        start = match.column - match.preview_offset
        self.assertEqual(match.line_text[start:start + match.length], '<needle>')
        self.assertEqual(find_in_text('needle  ', 'needle')[0].line_text, 'needle')

    def assert_preview(self, line, query, offset, preview):
        match = find_in_text('header\n' + line, query)[0]
        self.assertEqual(match[:3], (1, line.index(query), len(query)))
        self.assertEqual(match.preview_offset, offset)
        self.assertEqual(match.line_text, preview)
        start = match.column - match.preview_offset
        self.assertLessEqual(start, 4)
        self.assertEqual(match.line_text[start:start + match.length], query)

    def test_late_match_in_line_shorter_than_preview_limit(self):
        line = 'x' * 120 + 'needle' + 'y' * 64
        self.assertEqual(len(line), 190)
        self.assert_preview(line, 'needle', 116, line[116:])

    def test_early_match_and_indentation(self):
        self.assert_preview('  foo needle tail  ', 'needle', 2, 'foo needle tail')
        self.assert_preview('needle tail', 'needle', 0, 'needle tail')

    def test_match_at_end_does_not_restore_dropped_context(self):
        line = 'context ' * 20 + 'needle'
        self.assert_preview(line, 'needle', 156, line[156:])

    def test_wide_characters_use_original_character_offsets(self):
        line = '　\té界🙂' * 30 + '針é' + '尾' * 10
        self.assert_preview(line, '針é', 147, line[147:])

    def test_very_long_line_keeps_preview_bounded(self):
        line = 'x' * 10000 + 'needle' + 'y' * 10000
        self.assert_preview(line, 'needle', 9996, line[9996:10196])
        self.assertEqual(len(find_in_text(line, 'needle')[0].line_text), 200)

    def test_tabs_trimmed_but_matched_whitespace_retained(self):
        self.assert_preview('\t\tfoo\tneedle\t', 'needle', 2, 'foo\tneedle')
        self.assert_preview('\t\tneedle\t ', '\tneedle\t ', 1, '\tneedle\t ')

    def test_order_and_basename_literal_matches(self):
        a = self.write('a.txt', 'needle')
        z = self.write('z.txt', 'needle')
        current = self.write('sub/current.txt', 'needle')
        name = self.write('sub/NEEDLE.txt', 'nothing')
        result = self.search(current_path=current)
        self.assertEqual([g.path for g in result.groups], [current, a, z])
        self.assertTrue(result.groups[0].is_current)
        self.assertEqual(result.file_names, [name])
        self.assertEqual((result.total_matches, result.total_files), (3, 3))
        self.assertEqual(self.search('NEEDLE', case_sensitive=True).file_names, [name])
        self.assertEqual(self.search('.*', regex=True).file_names, [])

    def test_live_provider_overrides_disk_and_supports_unsaved_file(self):
        disk = self.write('disk', 'needle')
        unsaved = str(self.root / 'new.txt')
        result = self.search(current_path=unsaved,
                             text_provider=lambda p: 'needle needle' if p == unsaved else 'changed')
        self.assertEqual([g.path for g in result.groups], [unsaved])
        self.assertEqual(result.total_matches, 2)
        self.assertEqual(self.search(text_provider=lambda p: None).groups[0].path, disk)

    def test_per_file_and_total_limits(self):
        self.write('a', 'needle needle needle')
        self.write('b', 'needle needle')
        result = self.search(max_per_file=2)
        self.assertEqual([len(g.matches) for g in result.groups], [2, 2])
        self.assertTrue(result.truncated)
        result = self.search(max_total=3)
        self.assertEqual([len(g.matches) for g in result.groups], [3])
        self.assertTrue(result.truncated)
        self.assertEqual(self.search(max_total=0).total_matches, 0)
        self.assertTrue(self.search(max_total=0).truncated)
        self.assertFalse(self.search(max_total=5).truncated)

    def test_cancellation_between_files(self):
        current = self.write('z', 'needle')
        self.write('a', 'needle')
        cancel = threading.Event()
        cancel.set()
        self.assertEqual(self.search(current_path=current, cancel=cancel).groups, [])
        cancel.clear()

        def provider(path):
            cancel.set()
            return 'needle'

        result = self.search(current_path=current, text_provider=provider, cancel=cancel)
        self.assertEqual([g.path for g in result.groups], [current])

    def test_current_provider_runs_before_project_enumeration(self):
        current = self.write('z', 'needle')
        calls = []

        def listing(root):
            self.assertEqual(calls, [current])
            return iter([current])

        with patch('idgent.search.iter_project_files', listing):
            result = self.search(current_path=current, text_provider=lambda p: calls.append(p) or 'needle')
        self.assertTrue(result.groups[0].is_current)

    def test_empty_query_and_missing_root(self):
        self.assertEqual(self.search('').total_matches, 0)
        self.assertEqual(search('/does/not/exist', 'x', case_sensitive=False, regex=False).groups, [])


class WidgetTests(FilesFixture):
    def setUp(self):
        super().setUp()
        self.a = self.write('a.py', 'needle Needle\n<needle> & more')
        self.z = self.write('z.py', 'needle')
        self.name = self.write('needle.txt', 'unrelated')
        self.window = Gtk.OffscreenWindow()
        self.panel = SearchPanel(Gtk.Label(label='Explorer tree'), str(self.root))
        self.window.add(self.panel)
        self.window.set_default_size(480, 350)
        self.window.show_all()
        self.addCleanup(self.window.destroy)

    def wait_scan(self):
        pump_until(lambda: not self.panel._timeout and not self.panel._searching)

    def test_pages_and_clear(self):
        self.assertEqual(self.panel.stack.get_visible_child_name(), 'content')
        self.panel.entry.set_text('needle')
        self.assertEqual(self.panel.stack.get_visible_child_name(), 'results')
        self.wait_scan()
        self.assertEqual(self.panel.results.total_matches, 4)
        self.panel.clear()
        self.assertEqual(self.panel.query, '')
        self.assertEqual(self.panel.stack.get_visible_child_name(), 'content')

    def test_current_group_and_resort(self):
        self.panel.set_current_file(self.z)
        self.panel.search_now('needle')
        self.assertEqual(self.panel.store[0][3], self.z)
        self.assertIn('current file', self.panel.store[0][2])
        self.panel.set_current_file(self.a)
        self.assertEqual(self.panel.store[0][3], self.a)
        self.wait_scan()
        self.assertTrue(self.panel.results.groups[0].is_current)

    def test_signal_match_and_file_name(self):
        received = []
        self.panel.connect('result-activated', lambda _, *args: received.append(args))
        self.panel.search_now('needle')
        self.panel.tree.row_activated(Gtk.TreePath.new_from_string('0:1'), self.panel.tree.get_column(0))
        self.panel.tree.row_activated(Gtk.TreePath.new_from_string('2:0'), self.panel.tree.get_column(0))
        self.assertEqual(received, [(self.a, 0, 7, 6), (self.name, 0, 0, 0)])

    def test_group_activation_toggles(self):
        self.panel.search_now('needle')
        path = Gtk.TreePath.new_from_string('0')
        self.assertTrue(self.panel.tree.get_activate_on_single_click())
        self.assertTrue(self.panel.tree.row_expanded(path))
        self.panel._activate(path)
        self.assertFalse(self.panel.tree.row_expanded(path))
        self.panel._activate(path)
        self.assertTrue(self.panel.tree.row_expanded(path))

    def test_escape_entry_and_list(self):
        event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        event.keyval = Gdk.KEY_Escape
        for widget in (self.panel.entry, self.panel.tree):
            self.panel.search_now('needle')
            self.assertTrue(widget.emit('key-press-event', event))
            self.assertEqual(self.panel.query, '')
        self.panel.search_now('needle')
        self.panel.entry.emit('stop-search')
        self.assertEqual(self.panel.query, '')

    def test_toggles_change_results(self):
        self.panel.search_now('Needle')
        self.assertEqual(self.panel.results.total_matches, 4)
        self.panel.case_button.set_active(True)
        self.wait_scan()
        self.assertEqual(self.panel.results.total_matches, 1)
        self.panel.case_button.set_active(False)
        self.panel.search_now('n.edle')
        self.assertEqual(self.panel.results.total_matches, 0)
        self.panel.regex_button.set_active(True)
        self.wait_scan()
        self.assertEqual(self.panel.results.total_matches, 4)

    def test_invalid_regex_message_sync_and_async(self):
        self.panel.regex_button.set_active(True)
        self.panel.search_now('[')
        self.assertIn('Invalid regular expression:', self.panel.summary.get_text())
        self.panel.entry.set_text('(')
        self.wait_scan()
        self.assertIn('Invalid regular expression:', self.panel.summary.get_text())
        self.assertEqual(len(self.panel.store), 0)

    def test_markup_escaped_and_highlighted(self):
        self.panel.search_now('<needle>')
        markup = self.panel.store['0:0'][2]
        self.assertIn('<b><u>&lt;needle&gt;</u></b>', markup)
        self.assertIn('&amp;', markup)

    def test_narrow_result_keeps_late_match_near_start_and_ellipsizes_tail(self):
        provider = Gtk.CssProvider()
        provider.load_from_data(SEARCH_CSS.encode())
        screen = Gdk.Screen.get_default()
        Gtk.StyleContext.add_provider_for_screen(
            screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.addCleanup(Gtk.StyleContext.remove_provider_for_screen, screen, provider)
        line = 'context ' * 15 + 'needle' + ' tail' * 12 + ' end'
        self.assertEqual((line.index('needle'), len(line)), (120, 190))
        self.write('a.py', line)
        self.window.destroy()
        self.window = Gtk.OffscreenWindow()
        self.panel = SearchPanel(Gtk.Label(label='Explorer tree'), str(self.root))
        self.window.add(self.panel)
        self.window.set_default_size(260, 350)
        self.window.show_all()
        self.addCleanup(self.window.destroy)
        self.panel.set_font_size(10)  # Explorer's default.
        self.panel.search_now('needle')
        column = self.panel.tree.get_column(0)
        row = self.panel.store.get_iter_from_string('0:0')
        column.cell_set_cell_data(self.panel.store, row, False, False)
        number, preview = self.panel._cells
        # Read the text produced by the renderer's markup parser.
        displayed = preview.get_property('text')
        self.assertTrue(displayed.startswith('…'))
        self.assertLess(displayed.index('needle'), 30)
        self.assertIn('<b><u>needle</u></b>', self.panel.store[row][2])
        self.assertEqual(tuple(self.panel.store[row][4:7]), (0, 120, 6))
        self.assertEqual(preview.get_property('ellipsize'), Pango.EllipsizeMode.END)
        self.assertEqual(number.get_property('ellipsize'), Pango.EllipsizeMode.NONE)
        # The navigator now reserves a 12 px inset on either side.
        pump_until(lambda: self.panel.tree.get_allocated_width() == 260 - 24)
        scroll = self.panel.tree.get_parent()
        adjustment = scroll.get_hadjustment()
        self.assertLessEqual(adjustment.get_upper(), adjustment.get_page_size())
        self.assertFalse(scroll.get_hscrollbar().get_child_visible())
        self.assertGreater(column.cell_get_position(number)[1], 0)

    def test_trimmed_markup_escapes_context_and_match(self):
        self.write('a.py', '    ' + 'x' * 120 + ' & <needle> tail')
        self.panel.search_now('<needle>')
        markup = self.panel.store['0:0'][2]
        self.assertTrue(markup.startswith('…'))
        self.assertIn('&amp;', markup)
        self.assertIn('<b><u>&lt;needle&gt;</u></b>', markup)
        self.write('a.py', '<needle>')
        self.panel.search_now('<needle>')
        self.assertFalse(self.panel.store['0:0'][2].startswith('…'))

    def test_current_results_before_slow_scan_and_superseding_query(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        from idgent.search import _project_files
        original = _project_files

        def slow(root, cancel=None):
            blocked = cancel is not None and not entered.is_set()
            if blocked:
                entered.set()
                release.wait(3)
            try:
                yield from original(root, cancel)
            finally:
                if blocked:
                    finished.set()

        self.addCleanup(release.set)
        with patch('idgent.search._project_files', slow):
            self.panel.set_current_file(self.z)
            self.panel.entry.set_text('needle')
            pump_until(entered.is_set)
            self.assertEqual(self.panel.store[0][3], self.z)
            self.assertTrue(self.panel._searching)
            old_generation = self.panel._generation
            self.panel.entry.set_text('unrelated')
            self.wait_scan()
            self.assertGreater(self.panel._generation, old_generation)
            release.set()
            pump_until(finished.is_set)
        self.assertEqual(self.panel.results.total_matches, 1)
        self.assertEqual(self.panel.results.groups[0].path, self.name)
        # Explicit late delivery must be discarded as well.
        from idgent.search import SearchResults
        self.panel._deliver(old_generation, SearchResults(), True)
        self.assertEqual(self.panel.results.total_matches, 1)

    def test_destroy_during_scan(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()

        def slow(root, cancel=None):
            entered.set()
            release.wait(3)
            try:
                yield self.a
            finally:
                finished.set()

        self.addCleanup(release.set)
        with patch('idgent.search._project_files', slow):
            self.panel.entry.set_text('needle')
            pump_until(entered.is_set)
            generation = self.panel._generation
            self.window.destroy()
            release.set()
            pump_until(finished.is_set)
            self.assertFalse(self.panel._deliver(generation, self.panel.results, True))

    def test_provider_called_on_main_thread(self):
        thread_ids = []
        self.panel.set_text_provider(lambda p: thread_ids.append(threading.get_ident()) or 'live')
        self.panel.entry.set_text('live')
        self.wait_scan()
        self.assertEqual(self.panel.results.total_matches, 3)
        self.assertEqual(set(thread_ids), {threading.get_ident()})

    def test_root_provider_focus_and_down(self):
        self.panel.set_root('')
        self.panel.search_now('needle')
        self.assertEqual(self.panel.results.groups, [])
        self.panel.set_root(str(self.root))
        self.panel.set_text_provider(lambda p: 'buffer')
        self.panel.search_now('buffer')
        self.assertEqual(self.panel.results.total_matches, 3)
        self.panel.focus_search()
        self.assertEqual(self.panel.entry.get_selection_bounds(), (0, 6))
        event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        event.keyval = Gdk.KEY_Down
        self.assertTrue(self.panel.entry.emit('key-press-event', event))
        self.assertEqual(self.panel.tree.get_cursor()[0].to_string(), '0')

    def test_font_size_and_escaped_signal(self):
        self.panel.set_font_size(15)
        self.assertEqual([c.get_property('size-points') for c in self.panel._cells], [15, 15])
        escaped = []
        self.panel.connect('escaped', lambda *_: escaped.append(1))
        event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        event.keyval = Gdk.KEY_Escape
        self.panel.search_now('needle')
        self.panel.entry.emit('key-press-event', event)
        self.panel.tree.emit('key-press-event', event)
        self.assertEqual(escaped, [1, 1])


    def test_css_uses_all_theme_roles_and_parses(self):
        self.assertIn('.idgent-search-entry', SEARCH_CSS)
        for theme in THEMES:
            provider = Gtk.CssProvider()
            provider.load_from_data(build_css(theme.ui).encode())

    def show_styled_window(self):
        self.window.destroy()
        style.apply_theme(get_theme('idgent-dark'))
        self.window = Gtk.Window()
        self.window.set_default_size(300, 350)
        self.panel = SearchPanel(Gtk.Label(label='Explorer tree'), str(self.root))
        self.window.add(self.panel)
        self.window.show_all()
        self.window.present()
        self.addCleanup(self.window.destroy)
        pump_until(self.window.is_active)

    def pixel(self, widget, x, y):
        x, y = widget.translate_coordinates(self.window, x, y)
        return tuple(Gdk.pixbuf_get_from_window(self.window.get_window(), x, y, 1, 1).get_pixels()[:3])

    def test_entry_geometry_native_clear_icon_and_focus_ring(self):
        self.show_styled_window()
        entry = self.panel.entry
        secondary = Gtk.EntryIconPosition.SECONDARY
        self.assertEqual(entry.get_allocated_height(), 30)
        self.assertIsNotNone(entry.get_icon_name(Gtk.EntryIconPosition.PRIMARY))
        self.assertIsNone(entry.get_icon_name(secondary))
        self.panel.search_now('needle')
        self.window.show_all()
        self.assertEqual(entry.get_icon_name(secondary), 'edit-clear-symbolic')
        self.assertTrue(entry.get_icon_activatable(secondary))
        event = Gdk.Event.new(Gdk.EventType.BUTTON_RELEASE)
        event.button = 1
        entry.emit('icon-release', secondary, event)
        self.assertEqual(self.panel.query, '')
        self.assertEqual(self.panel.stack.get_visible_child_name(), 'content')
        self.window.show_all()
        self.assertIsNone(entry.get_icon_name(secondary))
        self.assertEqual(entry.get_allocated_height(), 30)
        for name in ('idgent-dark', 'idgent-light'):
            with self.subTest(theme=name):
                theme = get_theme(name)
                style.apply_theme(theme)
                entry.grab_focus()
                self.window.set_focus_visible(True)
                ring = tuple(int(theme.ui['border_focus'][i:i + 2], 16) for i in (1, 3, 5))
                pump_until(lambda: self.pixel(entry, -1, 15) == ring)
                self.assertEqual(entry.get_style_context().get_property(
                    'border-radius', entry.get_state_flags()), 5)

    def test_result_rows_share_geometry_and_rounded_overlay_in_both_themes(self):
        self.show_styled_window()
        self.panel.search_now('needle')
        tree = self.panel.tree
        path = Gtk.TreePath.new_from_string('0:0')
        tree.set_cursor(path)
        self.panel.entry.grab_focus()
        self.window.set_focus_visible(False)
        pump_until(lambda: tree.get_allocated_width() > 1)
        for name in ('idgent-dark', 'idgent-light'):
            with self.subTest(theme=name):
                theme = get_theme(name)
                style.apply_theme(theme)
                area = tree.get_background_area(path, tree.get_column(0))
                group_area = tree.get_background_area(Gtk.TreePath.new_from_string('0'), tree.get_column(0))
                self.assertTrue(24 <= area.height <= 26, area.height)
                self.assertEqual(group_area.height, area.height)
                sidebar = tuple(int(theme.ui['sidebar_bg'][i:i + 2], 16) for i in (1, 3, 5))
                tint = tuple(round(a * .933 + int(theme.ui['overlay'][i:i + 2], 16) * .067)
                             for a, i in zip(sidebar, (1, 3, 5)))
                x, y = tree.convert_bin_window_to_widget_coords(area.x, area.y)
                pump_until(lambda: self.pixel(tree, x, y) == sidebar)
                pump_until(lambda: self.pixel(tree, x + area.width - 15, y + area.height // 2) == tint)
                self.assertNotIn('transition:', build_css(theme.ui))

    def test_group_and_match_attributes_follow_live_theme_switches(self):
        self.show_styled_window()
        self.panel.search_now('<needle>')
        column = self.panel.tree.get_column(0)
        cell = self.panel._cells[1]
        for theme in THEMES:
            with self.subTest(theme=theme.id):
                style.apply_theme(theme)
                # Resolving a style query and cell data also works before paint.
                group = self.panel.store.get_iter_from_string('0')
                match = self.panel.store.get_iter_from_string('0:0')
                column.cell_set_cell_data(self.panel.store, group, False, False)
                attrs = cell.get_property('attributes').to_string()
                self.assertIn('absolute-size 12288', attrs)
                self.assertIn('weight semibold', attrs)
                faint = Pango.Color()
                faint.parse(theme.ui['faint'])
                self.assertIn(faint.to_string(), attrs)
                column.cell_set_cell_data(self.panel.store, match, False, False)
                attrs = cell.get_property('attributes').to_string()
                accent = Pango.Color()
                accent.parse(theme.ui['accent_text'])
                self.assertIn(accent.to_string(), attrs)
                self.assertNotIn('absolute-size', attrs)
                self.assertEqual(cell.get_property('text'), '<needle> & more')

    def test_search_toggles_have_compact_geometry_and_accessible_names(self):
        self.show_styled_window()
        for button, name in ((self.panel.case_button, 'Match case'),
                             (self.panel.regex_button, 'Use regular expression')):
            self.assertEqual(button.get_accessible().get_name(), name)
            self.assertLessEqual(button.get_allocated_height(), 30)
            self.assertLessEqual(button.get_allocated_width(), 30)
            button.set_active(True)
            context = button.get_style_context()
            self.assertAlmostEqual(context.get_background_color(button.get_state_flags()).alpha, .1)
