"""Allocated gutter geometry, checked against independently validated GTK text layout.

Run under xvfb with GDK_BACKEND=x11 and a temporary XDG_CONFIG_HOME.
The scale test starts a fresh GTK process because GDK_SCALE is read at startup.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from idgent import editor, style
from idgent.themes import THEMES
from gi.repository import Gdk, Gtk


def settle(seconds=.08):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        while Gtk.events_pending():
            Gtk.main_iteration()
        time.sleep(.002)


class GutterSyncTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        style.apply_theme(THEMES[0])
        self.window = Gtk.OffscreenWindow()
        self.window.set_default_size(900, 600)
        self.panel = editor.EditorPanel()
        self.dock = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        self.dock.pack1(self.panel, True, True)
        self.dock.pack2(Gtk.Label(label='Dock'), False, True)
        self.dock.set_position(450)
        self.window.add(self.dock)
        self.window.show_all()
        self.addCleanup(self.close)

    def close(self):
        self.window.destroy()
        settle(.02)

    def open(self, text=None, name='a.txt'):
        if text is None:
            text = '\n'.join(f'row {i + 1}: text' for i in range(250))
        path = Path(self.directory.name) / name
        path.write_text(text)
        self.panel.open_file(str(path))
        settle()
        return self.panel.tabs[-1]

    def at(self, tab, line, offset=0):
        it = tab.buffer.get_iter_at_line_offset(line, offset)
        tab.buffer.place_cursor(it)
        tab.view.scroll_to_iter(it, 0, True, 0, .5)
        settle()
        Gtk.Scrollable.get_hadjustment(tab.view).set_value(0)

    def assert_aligned(self, tab):
        # Validate independently of ViewChrome. In particular, never call
        # update_labels here: that would conceal a missing GTK signal hook.
        view, buffer = tab.view, tab.buffer
        for line in range(buffer.get_line_count()):
            view.get_iter_location(buffer.get_iter_at_line(line))
        settle()
        rect = view.get_visible_rect()
        expected = {}
        for line in range(buffer.get_line_count()):
            top, height = view.get_line_yrange(buffer.get_iter_at_line(line))
            if top < rect.y + rect.height and top + height > rect.y:
                expected[line + 1] = (top - rect.y, height)
        labels = {int(label.get_text()): label for label in tab.chrome.labels
                  if label.get_visible()}
        self.assertEqual(sorted(labels), sorted(expected), 'every visible text row needs its true number')
        active = buffer.get_iter_at_mark(buffer.get_insert()).get_line() + 1
        for number, label in labels.items():
            with self.subTest(number=number):
                allocation = label.get_allocation()
                self.assertEqual((allocation.y, allocation.height), expected[number])
                self.assertEqual(label.get_style_context().has_class('active'), number == active)
        if active in labels:
            cursor = buffer.get_iter_at_line(active - 1)
            self.assertTrue(cursor.has_tag(tab.chrome.line_tag))
        self.assertLessEqual(len(tab.chrome.labels), 2 * rect.height + 4)
        return labels

    def key(self, tab, key, control=False):
        # OffscreenWindow has no X keyboard focus. Dispatch the application's
        # handler and GTK's actual key bindings without X focus/keymap lookup.
        event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        event.keyval = key
        event.state = Gdk.ModifierType.CONTROL_MASK if control else Gdk.ModifierType(0)
        if not self.panel._key_press(tab.view, event, tab):
            self.assertTrue(Gtk.bindings_activate(tab.view, key, event.state))
        settle()

    def test_first_open_and_background_tab_mapping(self):
        first = self.open()
        self.assert_aligned(first)
        # Construct exactly as open_file does, but leave this page unselected
        # until later, so it has never had a mapped layout at construction.
        path = str(Path(self.directory.name) / 'background.txt')
        text = '\n'.join(f'background {i}' for i in range(400))
        tab = editor._EditorTab(self.panel, path, text, 'LF', 0)
        self.panel.tabs.append(tab)
        self.panel.notebook.append_page(tab.page, tab.header)
        tab.page.show_all()
        tab.header.show_all()
        self.assertFalse(tab.view.get_mapped())
        tab.buffer.place_cursor(tab.buffer.get_iter_at_line(110))
        self.panel.set_font_size(16)
        self.panel.notebook.set_current_page(1)
        self.at(tab, 110)
        self.assert_aligned(tab)
        self.panel.notebook.set_current_page(0)
        settle()
        self.assert_aligned(first)
        self.panel.notebook.set_current_page(1)
        settle()
        self.assert_aligned(tab)

    def test_click_arrows_pages_home_end(self):
        tab = self.open()
        self.at(tab, 80)
        # Real text-window mouse event, then native GTK navigation bindings.
        location = tab.view.get_iter_location(tab.buffer.get_iter_at_line(76))
        x, y = tab.view.buffer_to_window_coords(Gtk.TextWindowType.TEXT, location.x, location.y)
        event = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
        event.window = tab.view.get_window(Gtk.TextWindowType.TEXT)
        event.button = 1
        event.x, event.y = x + 2, y + 3
        event.time = Gdk.CURRENT_TIME
        event.set_device(Gdk.Display.get_default().get_default_seat().get_pointer())
        tab.view.event(event)
        settle()
        self.assertEqual(tab.buffer.get_iter_at_mark(tab.buffer.get_insert()).get_line(), 76)
        self.assert_aligned(tab)
        for key, control in ((Gdk.KEY_Down, False), (Gdk.KEY_Up, False),
                             (Gdk.KEY_Page_Down, False), (Gdk.KEY_Page_Up, False),
                             (Gdk.KEY_End, True), (Gdk.KEY_Home, True)):
            with self.subTest(key=key):
                before = tab.buffer.get_iter_at_mark(tab.buffer.get_insert()).get_offset()
                self.key(tab, key, control)
                after = tab.buffer.get_iter_at_mark(tab.buffer.get_insert()).get_offset()
                self.assertNotEqual(before, after, 'navigation must actually move the cursor')
                self.assert_aligned(tab)

    def test_wheel_and_scrollbar_drag(self):
        tab = self.open()
        self.at(tab, 70)
        adjustment = Gtk.Scrollable.get_vadjustment(tab.view)
        before = adjustment.get_value()
        event = Gdk.Event.new(Gdk.EventType.SCROLL)
        event.window = tab.view.get_window(Gtk.TextWindowType.TEXT)
        event.direction = Gdk.ScrollDirection.DOWN
        event.state = Gdk.ModifierType(0)
        event.x, event.y = 100, 100
        event.set_device(Gdk.Display.get_default().get_default_seat().get_pointer())
        tab.view.event(event)
        settle(.2)
        self.assertGreater(adjustment.get_value(), before)
        self.assert_aligned(tab)
        for fraction in (.7, .23, 1, 0):
            adjustment.set_value(fraction * (adjustment.get_upper() - adjustment.get_page_size()))
            settle()
            self.assert_aligned(tab)

    def test_typing_enter_delete_paste_undo_redo(self):
        tab = self.open()
        self.at(tab, 70)
        tab.buffer.begin_user_action()
        tab.buffer.insert_at_cursor('typed')
        tab.buffer.end_user_action()
        self.assert_aligned(tab)
        self.key(tab, Gdk.KEY_Return)
        self.assert_aligned(tab)
        tab.buffer.begin_user_action()
        tab.buffer.delete(tab.buffer.get_iter_at_line(20), tab.buffer.get_iter_at_line(50))
        tab.buffer.end_user_action()
        self.assert_aligned(tab)
        tab.buffer.begin_user_action()
        tab.buffer.insert_at_cursor('\n'.join(f'paste {i}' for i in range(300)))
        tab.buffer.end_user_action()
        self.at(tab, 150)
        self.assert_aligned(tab)
        tab.undo()
        self.assert_aligned(tab)
        tab.undo(True)
        self.at(tab, 180)
        self.assert_aligned(tab)
        tab.buffer.set_text('replacement\n' * 100)
        self.at(tab, 50)
        self.assert_aligned(tab)

    def test_zoom_resize_dock_and_theme(self):
        tab = self.open()
        self.at(tab, 70)
        for key in (Gdk.KEY_plus, Gdk.KEY_plus, Gdk.KEY_minus, Gdk.KEY_0):
            self.key(tab, key, True)
            self.assert_aligned(tab)
        for size, position in (((1100, 700), 540), ((760, 450), 230)):
            self.window.resize(*size)
            self.dock.set_position(position)
            settle()
            self.assert_aligned(tab)
        for theme in THEMES:
            style.apply_theme(theme)
            self.panel.set_chrome(theme.chrome)
            settle()
            self.assert_aligned(tab)

    def test_hide_show_gutter_preserves_scroll_alignment_and_highlight_toggle(self):
        tab = self.open()
        self.at(tab, 90)
        self.panel.set_line_numbers(False)
        self.panel.set_highlight_line(False)
        settle()
        self.assertEqual(tab.chrome.width, 0)
        self.at(tab, 130)
        self.assertFalse(tab.buffer.get_iter_at_line(130).has_tag(tab.chrome.line_tag))
        self.panel.set_line_numbers(True)
        self.panel.set_highlight_line(True)
        settle()
        self.assert_aligned(tab)
        self.assertTrue(tab.buffer.get_iter_at_line(130).has_tag(tab.chrome.line_tag))

    def test_twenty_thousand_lines_digit_growth_and_cost(self):
        tab = self.open('row\n' * 98)
        width = tab.chrome.width
        tab.buffer.set_text('\n'.join(f'row {i}' for i in range(20000)))
        self.at(tab, 15000)
        self.assertEqual(tab.chrome.digits, 5)
        self.assertGreater(tab.chrome.width, width)
        self.assert_aligned(tab)
        self.assertLess(len(tab.chrome.labels), 80)
        started = time.perf_counter()
        for _ in range(10):
            tab.chrome.update_labels()
        self.assertLess(time.perf_counter() - started, .1)

    def test_fallback_and_taller_line(self):
        tab = self.open(('ordinary\n' * 50) + '漢字 😀 🐈\n' + ('ordinary\n' * 100))
        self.at(tab, 50)
        self.assert_aligned(tab)
        # Force a tall row on every machine, independent of installed fallback
        # fonts, and keep the real emoji/CJK fallback case above as well.
        tall = tab.buffer.create_tag('tall-test', size_points=30)
        tab.buffer.apply_tag(tall, tab.buffer.get_iter_at_line(50), tab.buffer.get_iter_at_line(51))
        settle()
        self.assertGreater(tab.view.get_line_yrange(tab.buffer.get_iter_at_line(50))[1],
                           tab.view.get_line_yrange(tab.buffer.get_iter_at_line(49))[1])
        self.assert_aligned(tab)
        self.at(tab, 90)
        self.assert_aligned(tab)

    def test_explorer_line_37_offscreen_screenshot(self):
        tab = self.open(Path('idgent/explorer.py').read_text(), 'explorer.py')
        if os.environ.get('IDGENT_GUTTER_SCALE_CHILD') == '1':
            self.assertEqual(tab.view.get_scale_factor(), 2)
        self.at(tab, 36, 32)
        self.assert_aligned(tab)
        self.assertIn('Ln 37, Col 33', self.panel.status.get_text())
        pixbuf = self.window.get_pixbuf()
        self.assertIsNotNone(pixbuf)
        shots = Path('.ceo/shots')
        shots.mkdir(parents=True, exist_ok=True)
        pixbuf.savev(str(shots / f'explorer-line37-scale{tab.view.get_scale_factor()}.png'), 'png', [], [])

    @unittest.skipIf(os.environ.get('IDGENT_GUTTER_SCALE_CHILD') == '1', 'already in scaled child')
    def test_scale_two(self):
        with tempfile.TemporaryDirectory() as config:
            result = subprocess.run(
                [sys.executable, '-W', 'ignore::DeprecationWarning', '-m', 'unittest',
                 'tests.test_gutter_sync.GutterSyncTest.test_explorer_line_37_offscreen_screenshot',
                 'tests.test_gutter_sync.GutterSyncTest.test_fallback_and_taller_line',
                 'tests.test_gutter_sync.GutterSyncTest.test_zoom_resize_dock_and_theme'],
                env={**os.environ, 'GDK_SCALE': '2', 'GDK_BACKEND': 'x11',
                     'XDG_CONFIG_HOME': config, 'IDGENT_GUTTER_SCALE_CHILD': '1'},
                capture_output=True, text=True, timeout=40)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
