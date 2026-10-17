"""Gutter, current-line, bracket, chrome-colour and zoom tests (real GTK under xvfb)."""
import os
import tempfile
import time
import unittest

os.environ["GDK_BACKEND"] = "x11"


from idgent import editor, gutter
from gi.repository import Gdk, Gtk


def pump(count=20):
    for _ in range(count):
        while Gtk.events_pending():
            Gtk.main_iteration()


class PolishTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.panel = editor.EditorPanel()
        self.window = Gtk.Window()
        self.window.set_default_size(700, 400)
        self.window.add(self.panel)
        self.window.show_all()
        self.addCleanup(self.window.destroy)

    def open(self, text, name='a.py'):
        path = os.path.join(self.directory.name, name)
        with open(path, 'w') as stream:
            stream.write(text)
        self.panel.open_file(path)
        pump()
        return self.panel.tabs[-1]

    def tagged(self, tab, name):
        tag = tab.buffer.get_tag_table().lookup(name)
        result, it = [], tab.buffer.get_start_iter()
        while True:
            if it.has_tag(tag):
                result.append(it.get_offset())
            if not it.forward_char():
                return result

    def test_gutter_width_grows_with_digits(self):
        widths = {}
        for lines in (9, 10, 99, 100, 1000):
            tab = self.open('\n'.join('x' * 1 for _ in range(lines)), f'f{lines}.txt')
            widths[lines] = tab.chrome.width
        self.assertEqual(widths[9], widths[10])
        self.assertEqual(widths[10], widths[99])
        self.assertGreater(widths[100], widths[99] - 1)
        self.assertEqual(widths[100], widths[99])
        self.assertGreater(widths[1000], widths[100])

    def test_width_follows_typing(self):
        tab = self.open('\n' * 98)
        before = tab.chrome.width
        tab.buffer.insert(tab.buffer.get_end_iter(), '\n' * 1000)
        self.assertGreater(tab.chrome.width, before)

    def shown(self, tab):
        return [(label.get_text(), label) for label in tab.chrome.labels if label.get_visible()]

    def test_paints_only_visible_lines(self):
        tab = self.open('\n'.join(f'line {i}' for i in range(5000)))
        shown = self.shown(tab)
        self.assertTrue(shown)
        self.assertLess(len(shown), 60)
        self.assertEqual(shown[0][0], '1')
        top, _ = tab.view.get_line_yrange(tab.buffer.get_iter_at_line(3000))
        Gtk.Scrollable.get_vadjustment(tab.view).set_value(top)
        pump()
        shown = self.shown(tab)
        self.assertLess(len(shown), 60)
        self.assertIn(3000 + 1, [int(text) for text, _ in shown])
        self.assertGreater(int(shown[0][0]), 2900)
        self.assertEqual([int(text) for text, _ in shown], list(range(int(shown[0][0]), int(shown[0][0]) + len(shown))))

    def test_last_empty_line_is_numbered(self):
        tab = self.open('a\nb\n')
        self.assertEqual([text for text, _ in self.shown(tab)], ['1', '2', '3'])

    def test_active_line_colour_differs(self):
        tab = self.open('a\nb\nc\n')
        tab.buffer.place_cursor(tab.buffer.get_iter_at_line(1))
        colours = {text: label.get_style_context().get_color(Gtk.StateFlags.NORMAL).to_string()
                   for text, label in self.shown(tab)}
        self.assertNotEqual(colours['2'], colours['1'])
        self.assertEqual(colours['1'], colours['3'])
        self.assertEqual(tab.chrome.active_line, 1)

    def test_current_line_follows_caret_edits_and_undo(self):
        tab = self.open('one\ntwo\nthree\n')
        buffer = tab.buffer
        buffer.place_cursor(buffer.get_iter_at_line(1))
        self.assertEqual(self.tagged(tab, 'idgent-current-line'), [4, 5, 6, 7])
        buffer.place_cursor(buffer.get_iter_at_line(2))
        self.assertEqual(self.tagged(tab, 'idgent-current-line')[0], 8)
        buffer.insert_at_cursor('x\n')
        self.assertEqual(self.tagged(tab, 'idgent-current-line')[0], 10)
        tab.undo()
        self.assertEqual(self.tagged(tab, 'idgent-current-line')[0], 0)
        self.assertEqual(len(tab.history), 2)
        buffer.select_range(buffer.get_iter_at_line(0), buffer.get_iter_at_line(2))
        self.assertEqual(self.tagged(tab, 'idgent-current-line'), [])

    def test_brackets(self):
        tab = self.open('f(a[1], {2})\n')
        buffer = tab.buffer
        buffer.place_cursor(buffer.get_iter_at_offset(1))
        self.assertEqual(self.tagged(tab, 'idgent-bracket'), [1, 11])
        buffer.place_cursor(buffer.get_iter_at_offset(12))
        self.assertEqual(self.tagged(tab, 'idgent-bracket'), [1, 11])
        buffer.place_cursor(buffer.get_iter_at_offset(7))
        self.assertEqual(self.tagged(tab, 'idgent-bracket'), [])
        tab2 = self.open('(' + 'x' * 25000 + ')\n', 'long.txt')
        tab2.buffer.place_cursor(tab2.buffer.get_iter_at_offset(0))
        self.assertEqual(self.tagged(tab2, 'idgent-bracket'), [])
        self.assertEqual(gutter.find_match('<a>', 0), None)

    def test_set_chrome(self):
        tab = self.open('x\n')
        self.panel.set_chrome({'gutter_bg': '#101010', 'bogus': '#fff', 'bracket_bg': 'not a colour'})
        self.assertEqual(self.panel.chrome['gutter_bg'], '#101010')
        self.assertEqual(self.panel.chrome['bracket_bg'], '#3a3d41')
        self.assertNotIn('bogus', self.panel.chrome)
        self.assertEqual(tab.chrome.colors['gutter_bg'], '#101010')
        self.panel.set_chrome({'current_line_bg': '#303030'})
        self.assertEqual(self.panel.chrome['gutter_bg'], '#101010')
        new = self.open('y\n', 'b.py')
        self.assertEqual(new.chrome.colors['gutter_bg'], '#101010')
        self.assertEqual(new.chrome.colors['current_line_bg'], '#303030')
        self.assertEqual(tab.chrome.line_tag.get_property('paragraph-background-rgba').to_string(), 'rgb(48,48,48)')

    def test_zoom(self):
        tab = self.open('x\n')
        width = tab.chrome.width

        def press(key):
            event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
            event.keyval = key
            event.state = Gdk.ModifierType.CONTROL_MASK
            return self.panel._key_press(tab.view, event, tab)

        self.assertTrue(press(Gdk.KEY_equal))
        self.assertEqual(self.panel.font_size, 12)
        self.assertGreater(tab.chrome.width, width)
        for _ in range(40):
            press(Gdk.KEY_plus)
        self.assertEqual(self.panel.font_size, 32)
        for _ in range(40):
            press(Gdk.KEY_minus)
        self.assertEqual(self.panel.font_size, 7)
        press(Gdk.KEY_0)
        self.assertEqual(self.panel.font_size, 11)
        self.assertEqual(tab.chrome.width, width)
        self.assertIn('11pt', tab.chrome.provider.to_string())

    def test_big_file_draw_is_cheap(self):
        text = '\n'.join(f'v{i} = ({i})' for i in range(100000))
        tab = self.open(text, 'big.py')
        pump(50)
        tab.view.scroll_to_iter(tab.buffer.get_iter_at_line(60000), 0, True, 0, 0.5)
        pump(50)
        started = time.perf_counter()
        tab.chrome.update_labels()
        Gtk.Scrollable.get_vadjustment(tab.view).set_value(Gtk.Scrollable.get_vadjustment(tab.view).get_value() + 500)
        self.assertLess(time.perf_counter() - started, 0.05)
        self.assertFalse(tab.buffer.get_modified())

    def test_modified_stays_false_and_find_works(self):
        tab = self.open('def f(x):\n    return (x)\n')
        buffer = tab.buffer
        buffer.place_cursor(buffer.get_iter_at_offset(5))
        buffer.select_range(buffer.get_iter_at_offset(0), buffer.get_iter_at_offset(12))
        self.panel.zoom(2)
        self.panel.set_chrome({'gutter_fg': '#ffffff'})
        pump(30)
        self.assertFalse(buffer.get_modified())
        self.assertTrue(any(t.get_property('name').startswith('hl-') for t in self.hl_tags(buffer)))
        self.panel.search_entry.set_text('return')
        self.panel._find()
        start, end = buffer.get_selection_bounds()
        self.assertEqual(buffer.get_text(start, end, True), 'return')

    @staticmethod
    def hl_tags(buffer):
        found = []
        it = buffer.get_start_iter()
        while True:
            found.extend(t for t in it.get_tags())
            if not it.forward_char():
                return found


if __name__ == '__main__':
    unittest.main()
