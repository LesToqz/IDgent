"""Generic notebook content alongside real file tabs (GTK under xvfb)."""
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from idgent import editor, motion
from gi.repository import Gdk, Gtk, Pango


def settle():
    end = time.monotonic() + .04
    while time.monotonic() < end:
        while Gtk.events_pending():
            Gtk.main_iteration()
        time.sleep(.001)


class ShutdownView(Gtk.Box):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def shutdown(self):
        self.calls += 1
        self.parent_at_shutdown = self.get_parent()


class ViewTabTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        motion.set_reduce_motion(True)
        self.addCleanup(motion.set_reduce_motion, False)
        self.window = Gtk.Window()
        self.window.set_default_size(800, 400)
        self.panel = editor.EditorPanel()
        self.window.add(self.panel)
        self.window.show_all()
        self.window.present()
        self.addCleanup(self.window.destroy)
        settle()

    def file(self, name='a.py'):
        path = Path(self.directory.name) / name
        path.write_text('one\ntwo')
        self.panel.open_file(str(path))
        settle()
        return self.panel._current()

    def view(self, key='commit', widget=None, **kwargs):
        widget = widget if widget is not None else Gtk.Entry()
        self.assertTrue(self.panel.add_view_tab(key, key, widget, **kwargs))
        settle()
        return widget

    def key(self, key, shift=False, widget=None):
        event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        event.keyval = key
        event.state = Gdk.ModifierType.CONTROL_MASK | (Gdk.ModifierType.SHIFT_MASK if shift else 0)
        if widget is not None:
            # Real propagation through GTK's focused widget and parent chain.
            event.window = Gtk.Widget.get_window(widget)
            event.send_event = True
            device = Gdk.Display.get_default().get_default_seat().get_keyboard()
            event.set_device(device)
            return self.window.propagate_key_event(event.key)
        tab = self.panel._current()
        return self.panel._key_press(tab.view, event, tab) if tab else self.panel._view_key_press(self.panel, event)

    def test_append_duplicate_title_and_icon(self):
        first, second = self.file(), self.file('b.py')
        view = self.view(icon_name='document-open-symbolic', tooltip='Commit details')
        other = self.view('other', icon_name='idgent-missing-icon')
        last = self.file('last.py')
        self.assertEqual([self.panel.notebook.get_nth_page(n) for n in range(5)],
                         [first.page, second.page, view, other, last.page])
        self.assertEqual(len(self.panel.tabs), 3)
        self.assertFalse(self.panel.add_view_tab('commit', 'ignored', Gtk.Label()))
        self.assertEqual(self.panel.current_view_key(), 'commit')
        self.assertIs(self.panel.view_tab_widget('commit'), view)
        self.assertTrue(view.is_focus())
        self.panel.set_view_tab_title('commit', 'abc123', 'new tooltip')
        header = self.panel.notebook.get_tab_label(view)
        tab = self.panel._view_tabs['commit']
        self.assertEqual(tab.label.get_text(), 'abc123')
        self.assertEqual(header.get_tooltip_text(), 'new tooltip')
        self.assertEqual(tab.label.get_ellipsize(), Pango.EllipsizeMode.END)
        self.assertEqual(tab.close.get_tooltip_text(), 'Close tab')
        self.assertEqual(len(tab.header_inner.get_children()), 4)
        self.assertEqual(len(self.panel._view_tabs['other'].header_inner.get_children()), 3)
        self.panel.set_view_tab_title('missing', 'ignored')
        self.assertIsNone(self.panel.view_tab_widget('missing'))

    def test_close_paths_signals_shutdown_placeholder(self):
        seen = []
        self.panel.connect('view-tab-closed', lambda panel, key:
                           seen.append((key, panel.has_view_tab(key), panel.notebook.get_n_pages())))
        for method in ('button', 'middle', 'api', 'keyboard'):
            widget = self.view(method, ShutdownView())
            tab = self.panel._view_tabs[method]
            self.assertEqual(self.panel.stack.get_visible_child_name(), 'editor')
            if method == 'button':
                tab.close.clicked()
            elif method == 'middle':
                event = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
                event.button = 2
                self.assertTrue(tab.header.emit('button-press-event', event))
            elif method == 'keyboard':
                self.assertTrue(self.key(Gdk.KEY_w))
            else:
                self.assertTrue(self.panel.close_view_tab(method))
            self.assertEqual(widget.calls, 1)
            self.assertIsNotNone(widget.parent_at_shutdown)
            self.assertIsNone(widget.get_parent())
            self.assertEqual(seen[-1], (method, False, 0))
            self.assertEqual(self.panel.stack.get_visible_child_name(), 'empty')
            self.assertFalse(self.panel.close_view_tab(method))
        self.assertEqual(len(seen), 4)

    def test_mixed_successors_status_and_current_signal(self):
        first = self.file()
        seen = []
        self.panel.connect('current-file-changed', lambda _, path: seen.append(path))
        self.panel.search_bar.set_search_mode(True)
        view = self.view()
        self.assertEqual(seen[-1], '')
        self.assertIsNone(self.panel._current())
        self.assertIsNone(self.panel.current_path())
        self.assertTrue(all(not label.get_text() for label in self.panel.status.items))
        self.assertFalse(self.panel.search_bar.get_search_mode())
        last = self.file('last.py')
        self.assertIn('UTF-8', self.panel.status.get_text())
        self.assertIsNone(self.panel.current_view_key())
        self.panel._close_tab(last)
        self.assertEqual(self.panel.current_view_key(), 'commit')
        self.panel.close_view_tab('commit')
        self.assertEqual(self.panel.current_path(), first.path)
        self.assertIn('UTF-8', self.panel.status.get_text())
        view = self.view('next')
        self.panel.notebook.set_current_page(0)
        self.panel._close_tab(first)
        self.assertEqual(self.panel.current_view_key(), 'next')
        self.assertEqual(self.panel.stack.get_visible_child_name(), 'editor')

    def test_cycle_mixed_reordered_pages(self):
        first = self.file()
        view = self.view()
        last = self.file('last.py')
        self.assertTrue(self.key(Gdk.KEY_Tab))
        self.assertIs(self.panel._current(), first)
        self.key(Gdk.KEY_Tab)
        self.assertEqual(self.panel.current_view_key(), 'commit')
        self.key(Gdk.KEY_Tab)
        self.assertIs(self.panel._current(), last)
        self.key(Gdk.KEY_ISO_Left_Tab, True)
        self.assertEqual(self.panel.current_view_key(), 'commit')
        reordered = []
        self.panel.notebook.connect('page-reordered', lambda *args: reordered.append(args))
        self.panel.notebook.reorder_child(view, 0)
        self.assertTrue(reordered)
        self.assertTrue(self.panel.notebook.get_tab_reorderable(view))
        self.assertTrue(self.panel._view_tabs['commit'].header_inner.get_style_context().has_class('active'))
        self.key(Gdk.KEY_Page_Down)
        self.assertIs(self.panel._current(), first)

    def test_file_commands_ignore_view(self):
        file = self.file('note.md')
        view = self.view()
        with patch.object(editor, '_ask') as ask, patch.object(self.panel, '_save', wraps=self.panel._save) as save:
            self.assertFalse(self.panel.has_unsaved())
            self.assertTrue(self.panel.save_current())
            save.assert_not_called()
            self.assertTrue(self.panel.save_all())
            self.assertEqual(save.call_count, 1)
            ask.assert_not_called()
        before = self.panel.font_size
        self.panel.zoom(1)
        self.assertEqual(before, self.panel.font_size)
        self.panel.set_markdown_mode('preview')
        self.assertIsNone(self.panel.markdown_mode())
        self.assertEqual(file.md_mode, 'preview')
        self.panel.search_entry.set_text('one')
        self.panel._find()
        self.assertEqual(self.panel.selected_text(), '')
        file.buffer.insert_at_cursor('dirty')
        self.assertTrue(self.panel.has_unsaved())
        self.assertIs(self.panel.view_tab_widget('commit'), view)

    def test_adaptive_width_and_shared_hover(self):
        for index in range(6):
            self.view(str(index))
        self.panel._size_tabs()
        expected = max(120, min(200, (self.panel.notebook.get_allocated_width() - 16) // 6)) - 8
        for tab in self.panel._view_tabs.values():
            self.assertEqual(tab.header.get_size_request().width, expected)
            self.assertEqual(tab.divider.get_opacity(), 0 if tab.key in ('4', '5') else 1)
        tab = self.panel._view_tabs['0']
        tab.header.emit('enter-notify-event', Gdk.Event.new(Gdk.EventType.ENTER_NOTIFY))
        self.assertTrue(tab.header_inner.get_style_context().has_class('hover'))
        tab.header.emit('leave-notify-event', Gdk.Event.new(Gdk.EventType.LEAVE_NOTIFY))
        self.assertFalse(tab.header_inner.get_style_context().has_class('hover'))

    def test_focus_nested_widgets_and_real_keyboard(self):
        file = self.file()
        box = Gtk.Box()
        entry, text = Gtk.Entry(), Gtk.TextView()
        box.pack_start(entry, True, True, 0)
        box.pack_start(text, True, True, 0)
        self.view(widget=box)
        text.grab_focus()
        self.panel.focus_current()
        self.assertTrue(text.is_focus())
        self.panel.notebook.set_current_page(0)
        self.panel.focus_current()
        self.assertTrue(file.view.is_focus())
        self.panel.notebook.set_current_page(1)
        settle()
        self.assertTrue(entry.is_focus() or text.is_focus())
        entry.grab_focus()
        self.key(Gdk.KEY_Tab, widget=entry)
        self.assertIs(self.panel._current(), file)
        self.key(Gdk.KEY_Tab, widget=file.view)
        self.assertEqual(self.panel.current_view_key(), 'commit')
        entry.grab_focus()
        self.assertTrue(self.key(Gdk.KEY_w, widget=entry))
        self.assertFalse(self.panel.has_view_tab('commit'))

    def test_destroy_panel_shuts_down_once(self):
        first = self.view('first', ShutdownView())
        second = self.view('second', ShutdownView())
        self.panel.close_view_tab('first')
        self.panel.destroy()
        settle()
        self.assertEqual((first.calls, second.calls), (1, 1))
        self.assertFalse(self.panel._view_tabs)
        self.assertIsNone(second.get_parent())
