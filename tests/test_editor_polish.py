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


class TabsPolishTest(unittest.TestCase):
    """WP-6: allocated geometry, actual rendered pixels and editing behaviours."""
    def setUp(self):
        from idgent import style, motion
        from idgent.themes import get_theme
        self.theme = get_theme('idgent-dark')
        style.apply_theme(self.theme)
        motion.set_reduce_motion(True)
        self.addCleanup(motion.set_reduce_motion, False)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.window = Gtk.Window()
        self.window.set_default_size(1000, 600)
        self.panel = editor.EditorPanel()
        self.panel.set_syntax_style(self.theme.syntax)
        self.panel.set_chrome(self.theme.chrome)
        self.window.add(self.panel)
        self.window.show_all()
        self.addCleanup(self.window.destroy)
        self.settle()

    @staticmethod
    def settle(seconds=.08):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            pump(1)
            time.sleep(.002)

    def open(self, name='a.py', text='one\ntwo\nthree'):
        from pathlib import Path
        path = Path(self.directory.name) / name
        path.write_text(text)
        self.panel.open_file(str(path))
        self.settle()
        return self.panel._current()

    def key(self, tab, key, state=0):
        event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        event.keyval, event.state = key, Gdk.ModifierType(state)
        return self.panel._key_press(tab.view, event, tab)

    def pixels(self):
        return Gdk.pixbuf_get_from_window(self.window.get_window(), 0, 0,
                                         self.window.get_allocated_width(), self.window.get_allocated_height())

    @staticmethod
    def pixel(pixbuf, x, y):
        offset = y * pixbuf.get_rowstride() + x * pixbuf.get_n_channels()
        return tuple(pixbuf.get_pixels()[offset:offset + 3])

    @staticmethod
    def rgb(value):
        return tuple(int(value[n:n + 2], 16) for n in (1, 3, 5))

    def test_header_tab_sizes_and_rounded_corner_pixels(self):
        from idgent import style
        from idgent.themes import get_theme
        first = self.open()
        self.open('b.py')
        self.panel.notebook.set_current_page(0)
        for theme_id in ('idgent-dark', 'idgent-light'):
            theme = get_theme(theme_id)
            style.apply_theme(theme)
            self.settle()
            hx, hy = first.header.translate_coordinates(self.window, 0, 0)
            # Tab CSS node adds 1px borders and 3px horizontal padding.
            self.assertEqual(first.header.get_allocated_height() + 2, 34)
            self.assertEqual(first.header.get_allocated_width() + 8, 200)
            self.assertEqual(first.page.translate_coordinates(self.panel.notebook, 0, 0)[1], 40)
            pb = self.pixels()
            self.assertEqual(self.pixel(pb, hx - 4, hy - 1), self.rgb(theme.ui['sidebar_bg']))
            self.assertEqual(self.pixel(pb, hx + 80, hy - 1), self.rgb(theme.ui['border']))
            self.assertEqual(self.pixel(pb, hx + 80, hy + 1), self.rgb(theme.ui['bg']))

    def test_close_and_dividers_hover_instant_and_dirty_stays_visible(self):
        first, second, third = self.open(), self.open('b.py'), self.open('c.py')
        def opacity(tab):
            return tab.close.get_style_context().get_property('opacity', tab.close.get_state_flags())
        self.assertEqual(opacity(first), 0)
        self.assertEqual(opacity(third), 1)
        event = Gdk.Event.new(Gdk.EventType.ENTER_NOTIFY)
        first.header.emit('enter-notify-event', event)
        self.assertEqual(opacity(first), 1)
        first.header.emit('leave-notify-event', Gdk.Event.new(Gdk.EventType.LEAVE_NOTIFY))
        self.assertEqual(opacity(first), 0)
        first.buffer.insert_at_cursor('dirty')
        self.assertTrue(first.label.get_text().startswith('● '))
        self.assertEqual(first.divider.get_opacity(), 1)
        self.assertEqual(second.divider.get_opacity(), 0)
        self.assertEqual(third.divider.get_opacity(), 0)
        self.assertEqual(first.divider.get_allocated_width(), 1)
        self.assertEqual(first.divider.get_allocated_height(), 13)

    def test_tab_widths_shrink_ellipsis_and_reorder_middle_close(self):
        tabs = [self.open(f'long-name-{n}-' + 'file' * 12 + '.py') for n in range(8)]
        for tab in tabs:
            self.assertTrue(self.panel.notebook.get_tab_reorderable(tab.page))
            self.assertGreaterEqual(tab.header.get_allocated_width() + 8, 120)
            self.assertLessEqual(tab.header.get_allocated_width() + 8, 320)
        self.panel.notebook.reorder_child(tabs[-1].page, 0)
        self.assertIs(self.panel._current(), tabs[-1])
        event = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
        event.button = 2
        tabs[0].header.emit('button-press-event', event)
        self.assertNotIn(tabs[0], self.panel.tabs)
        self.assertEqual(self.panel.notebook.get_n_pages(), 7)

    def test_status_bottom_right_reserves_last_line_with_find_and_zoom(self):
        tab = self.open(text='line\n' * 180 + 'LAST LINE')
        for size in (7, 11, 32):
            for find in (False, True):
                self.panel.set_font_size(size)
                self.panel.search_bar.set_search_mode(find)
                self.settle(.3)
                tab.view.scroll_to_iter(tab.buffer.get_end_iter(), 0, True, 0, 1)
                self.settle(.3)
                status = self.panel.status
                sx, sy = status.translate_coordinates(self.panel.overlay, 0, 0)
                self.assertEqual(sx + status.get_allocated_width(), self.panel.overlay.get_allocated_width())
                self.assertEqual(sy + status.get_allocated_height(), self.panel.overlay.get_allocated_height())
                self.assertLess(status.get_allocated_width(), self.panel.get_allocated_width())
                self.assertEqual(self.panel.stack.get_margin_bottom(), status.get_allocated_height())
                end = tab.view.get_iter_location(tab.buffer.get_end_iter())
                _, bottom = tab.view.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, end.x, end.y + end.height)
                _, bottom = tab.view.translate_coordinates(self.panel.overlay, 0, bottom)
                self.assertLessEqual(bottom, sy)
                self.assertIn('Ln 181, Col 10', status.get_text())

    def test_status_item_hover_has_real_overlay_pixels(self):
        self.open()
        item = self.panel.status.items[0].get_parent()
        x, y = item.translate_coordinates(self.window, 1, 5)
        before = self.pixel(self.pixels(), x, y)
        item.get_parent().emit('enter-notify-event', Gdk.Event.new(Gdk.EventType.ENTER_NOTIFY))
        self.settle()
        self.assertNotEqual(before, self.pixel(self.pixels(), x, y))
        item.get_parent().emit('leave-notify-event', Gdk.Event.new(Gdk.EventType.LEAVE_NOTIFY))
        self.settle()
        self.assertEqual(before, self.pixel(self.pixels(), x, y))

    def test_gutter_defaults_roles_gap_and_toggles_survive_show_all(self):
        tab = self.open()
        labels = [label for label in tab.chrome.labels if label.get_visible()]
        self.assertTrue(labels)
        label = labels[0]
        self.assertEqual(tab.chrome.width - label.get_allocation().x - label.get_allocated_width(), 24)
        font = label.get_style_context().get_property('font', Gtk.StateFlags.NORMAL)
        self.assertAlmostEqual(font.get_size() / 1024 * Gdk.Screen.get_default().get_resolution() / 72, 12)
        faint = Gdk.RGBA()
        faint.parse(self.theme.ui['faint'])
        self.assertEqual(label.get_style_context().get_color(Gtk.StateFlags.NORMAL), faint)
        active = next(label for label in labels if label.get_style_context().has_class('active'))
        muted = Gdk.RGBA()
        muted.parse(self.theme.ui['muted'])
        self.assertEqual(active.get_style_context().get_color(Gtk.StateFlags.NORMAL), muted)
        self.assertTrue(tab.buffer.get_iter_at_line(2).has_tag(tab.chrome.line_tag))
        self.panel.set_line_numbers(False)
        self.panel.set_highlight_line(False)
        self.window.show_all()
        self.settle()
        self.assertEqual(tab.chrome.width, 0)
        self.assertFalse(any(label.get_visible() for label in tab.chrome.labels))
        self.assertFalse(tab.buffer.get_iter_at_line(2).has_tag(tab.chrome.line_tag))
        new = self.open('new.py')
        self.assertEqual(new.chrome.width, 0)
        self.assertFalse(new.buffer.get_iter_at_line(2).has_tag(new.chrome.line_tag))
        self.panel.set_line_numbers(True)
        self.panel.set_highlight_line(True)
        self.settle()
        self.assertGreater(new.chrome.width, 0)
        self.assertTrue(any(label.get_visible() for label in new.chrome.labels))
        self.assertTrue(new.buffer.get_iter_at_line(2).has_tag(new.chrome.line_tag))

    def test_indent_changes_insertion_unindent_and_tab_stops(self):
        tab = self.open(text='')
        self.key(tab, Gdk.KEY_Tab)
        self.assertEqual(editor._text(tab.buffer), '    ')
        self.panel.set_indent(False, 2)
        width_two = tab.view.get_tabs().get_tab(0).location
        self.key(tab, Gdk.KEY_Tab)
        self.assertEqual(editor._text(tab.buffer), '      ')
        self.key(tab, Gdk.KEY_ISO_Left_Tab)
        self.assertEqual(editor._text(tab.buffer), '    ')
        self.panel.set_indent(True, 8)
        self.assertGreater(tab.view.get_tabs().get_tab(0).location, width_two)
        self.key(tab, Gdk.KEY_Tab)
        self.assertEqual(editor._text(tab.buffer), '    \t')
        new = self.open('new.py', '')
        self.assertEqual(new.view.get_tabs().get_tab(0).location, tab.view.get_tabs().get_tab(0).location)
        self.key(new, Gdk.KEY_Tab)
        self.assertEqual(editor._text(new.buffer), '\t')
        self.key(new, Gdk.KEY_ISO_Left_Tab)
        self.assertEqual(editor._text(new.buffer), '')
        with self.assertRaises(ValueError):
            self.panel.set_indent(False, 0)

    def test_auto_pair_default_toggle_skip_selection_and_undo(self):
        tab = self.open(text='')
        self.assertFalse(self.key(tab, Gdk.KEY_parenleft))
        self.panel.set_auto_pair(True)
        self.assertTrue(self.key(tab, Gdk.KEY_parenleft))
        self.assertEqual(editor._text(tab.buffer), '()')
        self.assertEqual(tab.cursor(), 1)
        self.key(tab, Gdk.KEY_parenright)
        self.assertEqual(editor._text(tab.buffer), '()')
        self.assertEqual(tab.cursor(), 2)
        tab.buffer.select_range(*tab.buffer.get_bounds())
        self.key(tab, Gdk.KEY_bracketleft)
        self.assertEqual(editor._text(tab.buffer), '[()]')
        self.assertEqual(tab.buffer.get_text(*tab.buffer.get_selection_bounds(), True), '()')
        tab.undo()
        self.assertEqual(editor._text(tab.buffer), '()')
        self.panel.set_auto_pair(False)
        self.assertFalse(self.key(tab, Gdk.KEY_quotedbl))
        self.panel.set_auto_pair(True)
        new = self.open('new.py', '')
        self.key(new, Gdk.KEY_quotedbl)
        self.assertEqual(editor._text(new.buffer), '""')

    def test_markdown_defaults_readable_width_and_new_tab_preview(self):
        tab = self.open('note.md', '# Heading\n\n' + 'readable text ' * 100)
        self.assertEqual(tab.md_mode, 'source')
        self.panel.set_markdown_mode('preview')
        self.settle()
        self.assertEqual(tab.md_preview_view.get_left_margin(), 32)
        self.panel.set_md_readable_width(True)
        self.settle()
        view = tab.md_preview_view
        self.assertLessEqual(view.get_allocated_width() - view.get_left_margin() - view.get_right_margin(), 700)
        self.panel.set_md_default_preview(True)
        other = self.open('next.md', '# New preview')
        self.assertEqual(other.md_mode, 'preview')
        self.assertTrue(other.md_renderer.readable_width)
        self.panel.set_md_default_preview(False)
        self.assertEqual(other.md_mode, 'preview', 'changing the default preserves an existing choice')
        self.panel.set_md_readable_width(False)
        self.assertEqual(view.get_left_margin(), 32)
        new = self.open('source.md')
        self.assertEqual(new.md_mode, 'source')
        self.panel.set_md_readable_width(True)
        self.window.resize(500, 600)
        self.panel.set_markdown_mode('preview')
        self.settle()
        self.assertEqual(new.md_preview_view.get_left_margin(), 32)

    def test_readable_width_positions_rendered_paragraphs_on_toggle_and_resize(self):
        text = 'body ' * 200 + '\n\n> quoted text\n\n```\n' + 'code ' * 100 + '\n```'
        tab = self.open('width.md', text)
        self.panel.set_markdown_mode('preview')
        self.panel.set_md_readable_width(True)
        self.settle()
        view, buffer = tab.md_preview_view, tab.md_renderer.buffer

        def assert_positions():
            margin = view.get_left_margin()
            rendered = editor._text(buffer)
            for word, inset in (('body', 0), ('quoted', 22), ('code', 16)):
                at = buffer.get_iter_at_offset(rendered.index(word))
                self.assertEqual(view.get_iter_location(at).x, margin + inset)
            # Check actual glyph positions, not just the TextView properties:
            # paragraph TextTags take precedence over view-level margins.
            at = buffer.get_start_iter()
            while not at.is_end():
                rect = view.get_iter_location(at)
                self.assertLessEqual(rect.x + rect.width, view.get_allocated_width() - margin)
                at.forward_char()

        assert_positions()
        self.window.resize(1200, 600)
        self.settle()
        assert_positions()
        self.panel.set_font_size(18)
        self.settle()
        assert_positions()
        self.panel.set_md_readable_width(False)
        self.settle()
        self.assertEqual(view.get_left_margin(), 32)
        assert_positions()

    def test_markdown_icon_pixels_follow_active_mode_and_theme(self):
        from idgent import style
        from idgent.themes import get_theme
        tab = self.open('note.md', '# Title')
        for theme_id in ('idgent-dark', 'idgent-light'):
            theme = get_theme(theme_id)
            style.apply_theme(theme)
            for mode in ('source', 'preview'):
                self.panel.set_markdown_mode(mode)
                self.settle()
                button = tab.md_buttons[mode]
                color = button.get_style_context().get_color(button.get_state_flags())
                expected = self.rgb(theme.ui['accent_text'])
                self.assertEqual(tuple(round(v * 255) for v in (color.red, color.green, color.blue)), expected)
                image = button.get_child()
                x, y = image.translate_coordinates(self.window, 0, 0)
                pixels = self.pixels()
                colors = [self.pixel(pixels, x + dx, y + dy) for dx in range(20) for dy in range(20)]
                self.assertIn(expected, colors, 'the actual icon stroke must use the accent colour')
        self.key(tab, Gdk.KEY_V, Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK)
        self.assertEqual(tab.md_mode, 'source')

    def test_close_reflow_motion_and_reduced_motion(self):
        from idgent import motion
        first, second, third = self.open(), self.open('b.py'), self.open('c.py')
        motion.set_reduce_motion(False)
        self.panel._close_tab(first)
        self.assertGreater(second.header.get_margin_start(), 0)
        initial = second.header.get_margin_start()
        self.settle(.1)
        self.assertGreater(second.header.get_margin_start(), 0)
        self.assertLess(second.header.get_margin_start(), initial)
        self.settle(.3)
        self.assertEqual(second.header.get_margin_start(), 0)
        motion.set_reduce_motion(True)
        self.panel._close_tab(second)
        self.assertEqual(third.header.get_margin_start(), 0)

    def test_component_css_parses_every_theme(self):
        from idgent.themes import THEMES
        self.assertEqual(len(THEMES), 14)
        for theme in THEMES:
            with self.subTest(theme=theme.id):
                provider = Gtk.CssProvider()
                provider.load_from_data(editor.build_css(theme.ui).encode())


if __name__ == '__main__':
    unittest.main()
