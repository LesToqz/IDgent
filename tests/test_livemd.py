"""Live decoration preserves source, editing history and a responsive GTK loop."""
import os
os.environ['GDK_BACKEND'] = 'x11'
from pathlib import Path
import random
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from idgent import editor, highlight, notes, style
from idgent.themes import get_theme
from gi.repository import Gdk, GLib, Gtk


def drain(predicate=lambda: True, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if Gtk.events_pending():
            Gtk.main_iteration_do(False)
        else:
            time.sleep(.001)
        if predicate():
            return
    raise AssertionError('GTK did not settle')


class LiveMarkdownTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.window = Gtk.Window()
        self.window.set_default_size(1000, 1000)
        self.panel = editor.EditorPanel()
        self.window.add(self.panel)
        self.window.show_all()
        self.addCleanup(self.window.destroy)
        def reset_style():
            style.apply_style()
            end = time.monotonic() + .05
            while time.monotonic() < end:
                if Gtk.events_pending():
                    Gtk.main_iteration_do(False)
                else:
                    time.sleep(.001)
        self.addCleanup(reset_style)

    def open(self, text, name='note.md'):
        self.path = Path(self.directory.name) / name
        self.path.write_text(text)
        self.panel.open_file(str(self.path))
        self.tab = self.panel._current()
        self.live = self.tab.md_renderer
        self.at(len(text))
        self.ready()
        return self.tab

    def ready(self):
        drain(lambda: not self.live.pending and not self.tab._md_restore_idle)

    def at(self, offset):
        self.tab.buffer.place_cursor(self.tab.buffer.get_iter_at_offset(offset))

    def text(self):
        return editor._text(self.tab.buffer)

    def hidden(self, offset):
        return self.tab.buffer.get_iter_at_offset(offset).has_tag(self.live.tags['hidden'])

    def key(self, key, state=0):
        event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        event.keyval, event.state = key, Gdk.ModifierType(state)
        return self.panel._key_press(self.tab.view, event, self.tab)

    def widgets(self, kind):
        return [widget for block in self.live.blocks for widget, _, role, _ in block.widgets if role == kind]

    def test_markers_follow_caret_without_source_changes(self):
        source = '# Heading\n\n**bold** *italic* ***both*** ~~gone~~ `code` [web](https://example.org)\n\n[[note|alias]] https://example.org\n\nend'
        self.open(source)
        markers = [0, source.index('**'), source.index('*italic'), source.index('***both'),
                   source.index('~~'), source.index('`'), source.index('[web]'), source.index('[[note')]
        for marker in markers:
            self.at(len(source))
            self.ready()
            self.assertTrue(self.hidden(marker), marker)
            self.at(marker + 1)
            self.ready()
            self.assertFalse(self.hidden(marker), marker)
            self.assertEqual(self.text(), source)
            self.assertEqual(self.tab.buffer.get_char_count(), len(source))
            self.assertEqual(self.tab.buffer.get_slice(*self.tab.buffer.get_bounds(), True), source)
            self.assertFalse(self.tab.buffer.get_modified())
        for name, word in (('strong', 'bold'), ('emphasis', 'italic'), ('strike', 'gone'), ('code', 'code'), ('link', 'alias')):
            at = self.tab.buffer.get_iter_at_offset(source.index(word))
            self.assertTrue(at.has_tag(self.live.tags[name]))

    def test_scripted_edits_always_equal_expected_source(self):
        expected = '# Title\n\n- [ ] todo\n\n```python\nprint(1)\n```\n\n| A | B |\n| --- | --- |\n| x | y |\n\nend'
        self.open(expected)
        randomizer = random.Random(917)
        for index in range(45):
            a = randomizer.randrange(len(expected) + 1)
            b = min(len(expected), a + randomizer.randrange(0, 5))
            replacement = randomizer.choice(('**x**', '\n', '# ', '|', 'ø', '', '`'))
            buffer = self.tab.buffer
            buffer.begin_user_action()
            buffer.delete(buffer.get_iter_at_offset(a), buffer.get_iter_at_offset(b))
            buffer.insert(buffer.get_iter_at_offset(a), replacement)
            buffer.end_user_action()
            expected = expected[:a] + replacement + expected[b:]
            self.at(randomizer.randrange(len(expected) + 1))
            self.ready()
            self.assertEqual(self.text(), expected, index)
            self.assertEqual(buffer.get_char_count(), len(expected), index)
            self.assertEqual(buffer.get_slice(*buffer.get_bounds(), True), expected, index)
            self.assertEqual(''.join(block.text for block in self.live.blocks), expected, index)
        self.assertTrue(self.panel.save_current())
        self.assertEqual(self.path.read_text(), expected)

    def test_checkbox_is_one_undoable_edit(self):
        source = '- [ ] **todo**\n- [x] done\n\nend'
        self.open(source)
        checks = self.widgets('marker')
        self.assertEqual([check.get_active() for check in checks], [False, True])
        self.assertTrue(all(check.get_sensitive() for check in checks))
        history = self.tab.history_index
        checks[0].clicked()
        self.ready()
        self.assertEqual(self.text(), source.replace('[ ]', '[x]', 1))
        self.assertEqual(self.tab.history_index, history + 1)
        self.assertTrue(self.tab.buffer.get_iter_at_offset(source.index('todo')).has_tag(self.live.tags['checked']))
        self.tab.undo()
        self.ready()
        self.assertEqual(self.text(), source)
        self.tab.undo(True)
        self.ready()
        self.assertEqual(self.text(), source.replace('[ ]', '[x]', 1))

    def test_list_continuation_termination_indent_and_backspace(self):
        cases = (('- item', '\n- '), ('3. item', '\n4. '), ('- [x] item', '\n- [ ] '), ('> quote', '\n> '))
        for source, continuation in cases:
            with self.subTest(source=source):
                self.open(source, str(len(source)) + str(source[0].encode().hex()) + '.md')
                self.assertTrue(self.key(Gdk.KEY_Return))
                self.ready()
                self.assertEqual(self.text(), source + continuation)
                self.key(Gdk.KEY_Return)
                self.assertEqual(self.text(), source + '\n')
                self.tab.undo()
                self.assertEqual(self.text(), source + continuation)
        self.open('- item', 'indent.md')
        self.panel.set_indent(False, 2)
        self.key(Gdk.KEY_Tab)
        self.assertEqual(self.text(), '  - item')
        self.assertEqual(self.tab.cursor(), 8)
        self.key(Gdk.KEY_ISO_Left_Tab)
        self.assertEqual(self.text(), '- item')
        self.at(2)
        self.ready()
        self.assertTrue(self.key(Gdk.KEY_BackSpace))
        self.assertEqual(self.text(), 'item')
        self.tab.undo()
        self.assertEqual(self.text(), '- item')
        self.open('- one\n- two\n', 'selected.md')
        self.tab.buffer.select_range(*self.tab.buffer.get_bounds())
        self.key(Gdk.KEY_Tab)
        self.assertEqual(self.text(), '  - one\n  - two\n')
        self.key(Gdk.KEY_ISO_Left_Tab)
        self.assertEqual(self.text(), '- one\n- two\n')

    def test_fenced_copy_exact_code_highlighting_and_feedback(self):
        source = '```python\ndef f():\n\treturn "<&>"\n```\n\n~~~sh\nprintf hi\n~~~\n\n```\n```\n\nend'
        self.open(source)
        boxes = self.widgets('tools')
        self.assertEqual(len(boxes), 3)
        for box, expected in zip(boxes, ('def f():\n\treturn "<&>"\n', 'printf hi\n', '')):
            button = next(w for w in box.get_children() if isinstance(w, Gtk.Button))
            button.clicked()
            self.assertEqual(Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).wait_for_text() or '', expected)
            self.assertEqual(button.get_label(), 'Copied')
        drain(lambda: all(next(w for w in box.get_children() if isinstance(w, Gtk.Button)).get_label() == 'Copy' for box in boxes), 3)
        if highlight.AVAILABLE:
            at = self.tab.buffer.get_iter_at_offset(source.index('return'))
            self.assertTrue(at.has_tag(self.live.tags['syntax-control']))
        self.assertTrue(self.hidden(0))
        self.at(source.index('return'))
        self.ready()
        self.assertFalse(self.hidden(0))
        self.assertEqual(self.text(), source)

    def test_tables_grid_alignment_wrapping_escaped_pipes_and_entry(self):
        source = '| A | B | C |\n| :--- | :---: | ---: |\n| **bold** | left \\| right | `code` |\n| next | cell | 2 |\n\nend'
        self.open(source)
        grids = self.widgets('table')
        self.assertEqual(len(grids), 1)
        grid = grids[0]
        self.assertIsInstance(grid, Gtk.Grid)
        for column, alignment in enumerate((0, .5, 1)):
            label = grid.get_child_at(column, 0).get_child()
            self.assertEqual(label.get_xalign(), alignment)
            self.assertTrue(label.get_line_wrap())
            self.assertTrue(label.get_style_context().has_class('live-md-header'))
        self.assertEqual(grid.get_child_at(1, 1).get_child().get_text(), 'left | right')
        self.assertIn('<b>bold</b>', grid.get_child_at(0, 1).get_child().get_label())
        grid.get_child_at(1, 1).emit('button-press-event', self.button_event())
        self.ready()
        self.assertEqual(self.widgets('table'), [])
        self.assertFalse(self.hidden(1))
        self.assertEqual(self.text(), source)
        self.at(len(source))
        self.ready()
        self.assertEqual(len(self.widgets('table')), 1)
        self.assertEqual(self.text(), source)
        self.at(source.index('A'))
        self.ready()
        self.assertEqual(self.widgets('table'), [])

    @staticmethod
    def button_event():
        event = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
        event.button, event.state = 1, Gdk.ModifierType(0)
        return event

    def test_table_insert_renders_after_caret_leaves(self):
        self.open('before\n\n')
        notes.apply_to_buffer('table', self.tab.buffer)
        self.assertEqual(self.text(), 'before\n\n' + notes.TABLE)
        self.at(self.tab.buffer.get_char_count())
        self.ready()
        self.assertEqual(len(self.widgets('table')), 1)
        self.assertEqual(self.tab.md_mode, 'preview')
        self.tab.undo()
        self.ready()
        self.assertEqual(self.widgets('table'), [])
        self.assertEqual(self.text(), 'before\n\n')

    def test_links_plain_click_ctrl_click_hover_and_wiki(self):
        self.open('[web](https://example.org) [[other|alias]]\n\nend')
        other = Path(self.directory.name) / 'other.md'
        other.write_text('other')
        with patch.object(Gtk, 'show_uri_on_window') as show:
            self.assertTrue(self.live.activate_link('https://example.org'))
            show.assert_called_once()
        with patch.object(self.panel, 'open_file') as opened:
            self.live.open_file = opened
            self.assertTrue(self.live.activate_link('[[other]]'))
            opened.assert_called_once_with(str(other))
        event = SimpleNamespace(button=1, state=0, x=0, y=0)
        with patch.object(self.live, '_link_at', return_value=('https://example.org', False)), patch.object(self.live, 'activate_link', return_value=True) as activate:
            self.assertTrue(self.live._press(self.tab.view, event))
            activate.assert_called_once()
        with patch.object(self.live, '_link_at', return_value=('https://example.org', True)), patch.object(self.live, 'activate_link', return_value=True) as activate:
            self.assertFalse(self.live._press(self.tab.view, event))
            event.state = Gdk.ModifierType.CONTROL_MASK
            self.assertTrue(self.live._press(self.tab.view, event))
            activate.assert_called_once()
        with patch.object(self.live, '_link_at', return_value=('https://example.org', False)):
            self.live._motion(self.tab.view, event)
            self.assertIsNotNone(self.tab.view.get_window(Gtk.TextWindowType.TEXT).get_cursor())

    def test_local_images_placeholder_and_no_network(self):
        pixbuf = Gdk.pixbuf_get_from_window(self.window.get_window(), 0, 0, 30, 20)
        pixbuf.savev(str(Path(self.directory.name) / 'image.png'), 'png', [], [])
        source = '![local](image.png)\n\n![missing](none.png)\n\n![remote](https://example.org/x.png)\n\nend'
        with patch('urllib.request.urlopen', side_effect=AssertionError('network')):
            self.open(source)
        self.assertEqual(len(self.widgets('image')), 1)
        self.assertTrue(self.tab.buffer.get_iter_at_offset(source.index('missing')).has_tag(self.live.tags['image']))
        self.assertEqual(self.text(), source)

    def test_incremental_5000_lines_and_typing_burst(self):
        source = '# Heading\n\n' + 'paragraph **bold** and `code`\n' * 4997 + '\nend'
        self.open(source)
        self.assertEqual(len(self.live.blocks), 5001)
        self.assertLess(self.live.max_slice_time, .016)
        before = self.live.painted_blocks
        self.at(source.index('paragraph') + 4)
        self.ready()
        expected = source
        at = self.tab.cursor()
        ticks = []
        heartbeat = GLib.timeout_add(1, lambda: (ticks.append(time.monotonic()), True)[1])
        try:
            for char in 'typing burst':
                self.tab.buffer.insert_at_cursor(char)
                expected = expected[:at] + char + expected[at:]
                at += 1
            self.ready()
        finally:
            GLib.source_remove(heartbeat)
        self.assertEqual(self.text(), expected)
        self.assertLess(self.live.painted_blocks - before, 20)
        self.assertTrue(ticks)
        self.assertLess(self.live.max_slice_time, .016)

    def test_theme_font_accent_and_sample_screenshots(self):
        source = '# A living document\n\n## Working notes\n\n**Bold ideas**, *gentle emphasis*, and `inline code`.\n\n- A first item\n  - A nested item\n- [ ] A task to finish\n- [x] A completed task\n\n> A quote worth remembering.\n\n```python\ndef greet(name):\n    return f"Hello {name}"\n```\n\n| Task | Owner | State |\n| :--- | :---: | ---: |\n| Review | Team | Ready |\n| Write | Me | Next |\n\n[Project link](https://example.org) and [[note|a related note]]\n\n'
        self.open(source)
        for theme_id in ('idgent-dark', 'idgent-light'):
            theme = get_theme(theme_id)
            style.apply_theme(theme, accent='#c15d84')
            self.panel.set_syntax_style(theme.syntax)
            self.panel.set_chrome(theme.chrome)
            self.panel.set_font_size(12)
            self.ready()
            drain(lambda: self.tab.view.get_allocated_width() > 900)
            stop = time.monotonic() + .2
            while time.monotonic() < stop:
                if Gtk.events_pending():
                    Gtk.main_iteration_do(False)
                else:
                    time.sleep(.002)
            expected = Gdk.RGBA()
            expected.parse(theme.syntax['heading'].foreground)
            self.assertEqual(self.live.tags['h1'].get_property('foreground-rgba'), expected)
            self.assertEqual(self.live.size, 12)
            self.assertEqual(self.live.chrome, self.panel.chrome)
            self.assertIn('12pt', self.live.provider.to_string())
            self.assertEqual(self.text(), source)
            self.assertFalse(self.tab.buffer.get_modified())
            folder = os.environ.get('LIVE_MD_SCREENSHOTS')
            if folder:
                pixels = Gdk.pixbuf_get_from_window(self.window.get_window(), 0, 0, self.window.get_allocated_width(), self.window.get_allocated_height())
                pixels.savev(str(Path(folder) / (theme_id + '.png')), 'png', [], [])
        before_accent = self.live.tags['link'].get_property('foreground-rgba')
        style.apply_theme(theme, accent='#36995f')
        drain(lambda: self.live.tags['link'].get_property('foreground-rgba') != before_accent)
        expected_accent = Gdk.RGBA()
        expected_accent.parse(style.current_theme().ui['accent_text'])
        self.assertEqual(self.live.tags['link'].get_property('foreground-rgba'), expected_accent)
        self.panel.set_md_readable_width(False)
        self.ready()
        self.assertEqual(self.tab.view.get_left_margin(), 32)
        self.panel.set_md_readable_width(True)
        self.ready()
        self.assertLessEqual(self.live._column_width(), 700)
