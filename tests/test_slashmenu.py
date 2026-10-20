"""Real GTK note command interaction and live rendering."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ['GDK_BACKEND'] = 'x11'

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, Gtk
from idgent import editor, motion, notes, slashmenu, style
from idgent.themes import get_theme


def spin(seconds=.04):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while Gtk.events_pending():
            Gtk.main_iteration()
        time.sleep(.002)


class SlashMenuTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / '.IDgent' / 'Notes' / 'note.md'
        self.path.parent.mkdir(parents=True)
        self.path.write_text('')
        self.panel = editor.EditorPanel()
        self.window = Gtk.Window()
        self.window.set_default_size(780, 740)
        self.window.add(self.panel)
        self.window.show_all()
        self.window.present()
        self.panel.open_file(str(self.path))
        self.tab = self.panel._current()
        self.view, self.buffer = self.tab.view, self.tab.buffer
        self.view.grab_focus()
        self.addCleanup(self.window.destroy)
        motion.set_reduce_motion(True)
        self.addCleanup(motion.set_reduce_motion, False)
        spin()

    @property
    def menu(self):
        return self.tab.slash_menu

    def text(self):
        return editor._text(self.buffer)

    def reset(self, text=''):
        self.menu.close()
        self.buffer.set_text(text)
        self.buffer.place_cursor(self.buffer.get_end_iter())
        spin()

    def type(self, text):
        for char in text:
            self.buffer.begin_user_action()
            self.buffer.insert_at_cursor(char)
            self.buffer.end_user_action()
            spin(.005)
        spin()

    def key(self, key, state=0):
        event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        event.keyval, event.state = key, state
        event.window = self.view.get_window(Gtk.TextWindowType.WIDGET)
        event.send_event = True
        return self.view.emit('key-press-event', event)

    def test_trigger_rules(self):
        for before, expected in [('', True), ('hello ', True), ('hello\n', True),
                                 ('a', False), ('`', False), ('`closed` ', True),
                                 ('```python\n', False), ('~~~\ncode\n', False),
                                 ('```\nx\n```\n', True), ('``code ` ', False)]:
            with self.subTest(before=before):
                self.reset(before)
                self.type('/')
                self.assertEqual(self.menu.opened, expected)
        self.reset('selected')
        self.buffer.select_range(self.buffer.get_start_iter(), self.buffer.get_end_iter())
        self.buffer.insert(self.buffer.get_end_iter(), '/')
        spin()
        self.assertFalse(self.menu.opened)
        self.reset()
        self.view.emit('preedit-changed', 'compose')
        self.type('/')
        self.assertFalse(self.menu.opened)
        self.view.emit('preedit-changed', '')
        self.buffer.insert_at_cursor('/')
        spin()
        self.assertFalse(self.menu.opened)

    def test_filter_ranking_and_group_headers(self):
        for query, first in [('h1', 'h1'), ('todo', 'todo'), ('check', 'todo'),
                             ('ul', 'bullet'), ('ol', 'numbered'), ('hr', 'divider'),
                             ('code', 'code'), ('tab', 'table'), ('tbl', 'table')]:
            with self.subTest(query=query):
                self.reset()
                self.type('/' + query)
                self.assertEqual(self.menu.matches[0].id, first)
                self.assertEqual(self.menu.list.get_children()[0].get_child().get_text(), 'Filtered results')
        self.reset()
        self.type('/')
        headings = [row.get_child().get_text() for row in self.menu.list.get_children()
                    if not row.get_selectable()]
        self.assertEqual(headings, ['Basic blocks', 'Insert'])
        self.assertEqual(len(self.menu.matches), 16)
        self.assertLessEqual(self.menu.scroll.get_allocated_height(), 274)
        self.assertTrue(self.view.has_focus())

    def test_navigation_wrap_swallowed_and_focus(self):
        self.type('/')
        self.assertTrue(self.key(Gdk.KEY_Up))
        self.assertEqual(self.menu.index, 15)
        self.assertTrue(self.key(Gdk.KEY_Down))
        self.assertEqual(self.menu.index, 0)
        self.key(Gdk.KEY_End)
        self.assertEqual(self.menu.index, 15)
        self.key(Gdk.KEY_Home)
        self.assertEqual(self.menu.index, 0)
        self.assertEqual(self.text(), '/')
        self.assertTrue(self.view.has_focus())
        self.key(Gdk.KEY_Escape)
        self.assertFalse(self.menu.opened)
        self.assertEqual(self.text(), '/')
        self.key(Gdk.KEY_Return)
        self.assertEqual(self.text(), '/\n')
        self.key(Gdk.KEY_Tab)
        self.assertEqual(self.text(), '/\n    ')

    def test_closing_backspace_arrows_space_focus_click_scroll(self):
        self.type('/h1')
        self.key(Gdk.KEY_Left)
        self.assertTrue(self.menu.opened)
        self.key(Gdk.KEY_Left)
        self.assertTrue(self.menu.opened)
        self.key(Gdk.KEY_Left)
        self.assertFalse(self.menu.opened)
        self.reset()
        self.type('/h1')
        self.key(Gdk.KEY_Right)
        # At document end Right cannot move beyond the query.
        self.assertTrue(self.menu.opened)
        self.reset('tail')
        self.buffer.place_cursor(self.buffer.get_start_iter())
        self.type('/h1')
        self.key(Gdk.KEY_Right)
        self.assertFalse(self.menu.opened)
        self.reset()
        self.type('/')
        self.key(Gdk.KEY_BackSpace)
        self.assertFalse(self.menu.opened)
        self.assertEqual(self.text(), '')
        self.type('/zzzz')
        self.assertEqual(self.menu.matches, [])
        self.key(Gdk.KEY_Return)
        self.assertEqual(self.text(), '/zzzz')
        self.type(' ')
        self.assertFalse(self.menu.opened)
        for signal, event_type in [('focus-out-event', Gdk.EventType.FOCUS_CHANGE),
                                   ('button-press-event', Gdk.EventType.BUTTON_PRESS),
                                   ('scroll-event', Gdk.EventType.SCROLL)]:
            self.reset()
            self.type('/')
            self.view.emit(signal, Gdk.Event.new(event_type))
            self.assertFalse(self.menu.opened)

    def test_enter_tab_mouse_and_two_undo_actions(self):
        for key in (Gdk.KEY_Return, Gdk.KEY_Tab, None):
            self.reset()
            self.type('/h1')
            if key:
                self.key(key)
            else:
                row = self.menu.rows[0]
                row.emit('enter-notify-event', Gdk.Event.new(Gdk.EventType.ENTER_NOTIFY))
                allocation = row.get_allocation()
                for kind in (Gdk.EventType.BUTTON_PRESS, Gdk.EventType.BUTTON_RELEASE):
                    event = Gdk.Event.new(kind)
                    event.window = self.menu.list.get_window()
                    event.x, event.y = allocation.x + 10, allocation.y + 10
                    event.button = 1
                    event.set_device(Gdk.Display.get_default().get_default_seat().get_pointer())
                    Gtk.main_do_event(event)
                spin()
            self.assertEqual(self.text(), '# ')
            self.assertFalse(self.menu.opened)
            self.assertTrue(self.view.has_focus())
            self.tab.undo()
            self.assertEqual(self.text(), '/h1')
            self.tab.undo()
            self.assertEqual(self.text(), '')

    def test_every_command_is_one_undo_and_keeps_text(self):
        for command in notes.SLASH_COMMANDS:
            for before in ('', '## keep '):
                with self.subTest(command=command.id, before=before):
                    self.reset(before)
                    query = '/' + command.id
                    self.type(query)
                    expected = command.edit(before, len(before), len(before))
                    self.menu.execute(command)
                    self.assertEqual(self.text(), expected.apply(before))
                    bounds = self.buffer.get_selection_bounds()
                    actual = tuple(it.get_offset() for it in bounds) if bounds else (self.tab.cursor(),) * 2
                    self.assertEqual(actual, (expected.select_start, expected.select_end))
                    self.tab.undo()
                    self.assertEqual(self.text(), before + query)
                    self.tab.undo()
                    self.assertEqual(self.text(), before)

    def test_live_blocks_render_and_checkbox_works(self):
        for query, kind in [('table', 'table'), ('code', 'codeblock'), ('todo', 'line')]:
            self.reset()
            self.type('/' + query)
            self.key(Gdk.KEY_Return)
            self.buffer.insert_at_cursor('\n\nend')
            self.buffer.place_cursor(self.buffer.get_end_iter())
            spin(.16)
            blocks = [b for b in self.tab.md_renderer.blocks if b.kind == kind]
            self.assertTrue(blocks, query)
            widgets = [item[0] for b in blocks for item in b.widgets]
            self.assertTrue(widgets, query)
            if query == 'todo':
                check = next(w for w in widgets if isinstance(w, Gtk.CheckButton))
                check.set_active(True)
                self.assertIn('[x]', self.text())
            if query == 'code':
                self.assertTrue(any(isinstance(child, Gtk.Button) for w in widgets
                                    if isinstance(w, Gtk.Container) for child in w.get_children()))

    def test_fade_honours_motion_preference(self):
        self.type('/')
        self.assertEqual(self.menu.popover.get_opacity(), 1)
        self.reset()
        motion.set_reduce_motion(False)
        self.type('/')
        self.assertLess(self.menu.popover.get_opacity(), 1)
        spin(.16)
        self.assertEqual(self.menu.popover.get_opacity(), 1)

    def test_caret_scroll_at_bottom_and_reduce_motion(self):
        self.reset('line\n' * 80)
        self.view.scroll_mark_onscreen(self.buffer.get_insert())
        spin()
        self.type('/')
        self.assertTrue(self.menu.opened)
        self.assertEqual(self.menu.popover.get_opacity(), 1)
        adjustment = self.tab.source_scroll.get_vadjustment()
        adjustment.set_value(0)
        spin(.3)
        self.assertFalse(self.menu.opened)

    def test_selection_replacement_and_non_note_source_disabled_typing(self):
        self.reset('selected')
        self.buffer.select_range(self.buffer.get_start_iter(), self.buffer.get_end_iter())
        self.key(Gdk.KEY_slash)
        self.buffer.delete_selection(True, True)
        self.type('/')
        self.assertFalse(self.menu.opened)
        for mode in ('source', 'disabled', 'non-note'):
            self.reset()
            if mode == 'source':
                self.panel.set_markdown_mode('source')
            elif mode == 'disabled':
                self.panel.set_slash_commands(False)
            else:
                self.tab.set_path(str(self.root / 'other.md'))
            self.type('/code')
            self.assertIsNone(self.menu)
            self.panel.set_slash_commands(True)
            self.tab.set_path(str(self.path))
            self.panel.set_markdown_mode('preview')

    def test_theme_font_and_fallback(self):
        with patch.object(Gtk.IconTheme, 'get_default', return_value=None):
            self.type('/code')
        first = self.menu.rows[0].get_child().get_children()[0]
        self.assertIsInstance(first, Gtk.Label)
        self.assertEqual(first.get_text(), '</>')
        style.apply_theme(get_theme('idgent-dark'), ui_font='DejaVu Serif', editor_font='DejaVu Sans Mono')
        spin()
        label = self.menu.rows[0].get_child().get_children()[1]
        font = label.get_style_context().get_font(Gtk.StateFlags.NORMAL)
        self.assertEqual(font.get_family(), 'DejaVu Serif')
        style.apply_style()

    def test_note_mode_disable_rename_and_destroy_lifecycle(self):
        self.type('/')
        old = self.menu
        self.panel.set_slash_commands(False)
        self.assertIsNone(self.menu)
        self.assertTrue(old._destroyed)
        self.assertFalse(old._handlers)
        self.panel.set_slash_commands(True)
        self.assertIsNotNone(self.menu)
        self.panel.set_markdown_mode('source')
        self.assertIsNone(self.menu)
        self.panel.set_markdown_mode('preview')
        self.assertIsNotNone(self.menu)
        other = self.root / 'other.md'
        self.panel.on_path_renamed(str(self.path), str(other))
        self.assertIsNone(self.menu)
        self.panel.on_path_renamed(str(other), str(self.path))
        self.assertIsNotNone(self.menu)
        old = self.menu
        self.buffer.set_modified(False)
        self.panel._close_tab(self.tab)
        self.assertTrue(old._destroyed)

    def test_screenshots(self):
        folder = Path('.ceo/screenshots')
        folder.mkdir(exist_ok=True)
        for theme in ('idgent-dark', 'idgent-light'):
            style.apply_theme(get_theme(theme))
            self.panel.set_syntax_style(get_theme(theme).syntax)
            self.panel.set_chrome(get_theme(theme).chrome)
            for query in ('', 'code'):
                self.reset('# Field notes\n\nA place for ideas and small experiments.\n\n- [x] Collect ideas\n- [ ] Try a new block\n\n')
                self.type('/' + query)
                spin(.15)
                gwindow = self.window.get_window()
                pixbuf = Gdk.pixbuf_get_from_window(gwindow, 0, 0, 780, 740)
                pixbuf.savev(str(folder / f'{theme}-{query or "all"}.png'), 'png', [], [])
        style.apply_style()


if __name__ == '__main__':
    unittest.main()
