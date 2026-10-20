"""Real GTK coverage for the Notes panel below the explorer tree."""
import ctypes
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

from idgent import fsops, gitstatus, motion, notes, style
from idgent.explorer import ExplorerPanel


def spin(seconds=.08):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        time.sleep(.005)


def wait_for(predicate):
    deadline = time.monotonic() + 2
    while not predicate() and time.monotonic() < deadline:
        spin(.02)
    return bool(predicate())


class NotesSectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / '.IDgent' / 'Notes'
        self.panel = ExplorerPanel(str(self.root))
        self.window = Gtk.Window()
        self.window.set_default_size(300, 700)
        self.window.add(self.panel)
        self.addCleanup(self.window.destroy)
        style.apply_style()
        self.window.show_all()
        spin()

    def names(self):
        return [row[1] for row in self.panel.notes_store]

    def key(self, keyval):
        self.panel.notes_tree.grab_focus()
        event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        event.keyval, event.state = keyval, Gdk.ModifierType(0)
        return self.panel.notes_tree.emit('key-press-event', event)

    def test_initial_visibility_lazy_folder_and_new_file_action(self):
        p = self.panel
        self.assertFalse(p.notes_section_visible)
        self.assertFalse(p.notes_section.get_visible())
        self.assertFalse(p.notes_button.get_active())
        p.notes_button.clicked()
        spin()
        self.assertTrue(p.notes_section_visible)
        self.assertTrue(p.tree.is_drawable())
        self.assertFalse(self.folder.exists())
        self.assertEqual(p.notes_stack.get_visible_child_name(), 'empty')
        with patch.object(p, '_new_dialog') as create:
            p.header_buttons['New File'].clicked()
        create.assert_called_once_with(False)
        self.assertEqual(p.header_buttons['New File'].get_tooltip_text(), 'New File')
        self.assertFalse(hasattr(p, 'show_tab'))
        self.assertFalse(hasattr(p, 'current_tab'))
        self.assertFalse(hasattr(p, 'notes_page'))
        p.notes_button.clicked()
        self.assertFalse(p.notes_section_visible)

    def test_existing_folder_at_open_and_root_change(self):
        notes.create_note(str(self.root), 'First')
        other = ExplorerPanel(str(self.root))
        self.addCleanup(other.destroy)
        self.assertTrue(other.notes_section_visible)
        self.assertTrue(other.notes_button.get_active())
        self.assertEqual([r[1] for r in other.notes_store], ['First'])
        with tempfile.TemporaryDirectory() as empty:
            other.set_root(empty)
            self.assertFalse(other.notes_section_visible)
        self.panel.refresh()
        self.assertTrue(self.panel.notes_section_visible)

    def test_vertical_layout_default_size_and_minimums(self):
        p = self.panel
        p.create_note('One')
        spin()
        split = p.notes_split
        self.assertEqual(split.get_orientation(), Gtk.Orientation.VERTICAL)
        self.assertIs(split.get_child1(), p.search)
        self.assertIs(split.get_child2(), p.notes_section)
        self.assertGreater(p.notes_section.translate_coordinates(p, 0, 0)[1],
                           p.search.translate_coordinates(p, 0, 0)[1])
        available = split.get_allocated_height() - 6
        self.assertAlmostEqual(p.notes_section.get_allocated_height() / available, .3, delta=.03)
        self.assertEqual(split.get_handle_window().get_height(), 6)
        context = split.get_style_context()
        self.assertEqual(context.has_class('idgent-notes-split'), True)
        for position in (-100, 10000):
            split.set_position(position)
            spin()
            self.assertGreaterEqual(p.search.get_allocated_height(), 120)
            self.assertGreaterEqual(p.notes_section.get_allocated_height(), 96)
        self.assertEqual(p.search.get_size_request()[1], 120)
        self.assertEqual(p.notes_section.get_size_request()[1], 96)

    def test_real_divider_drag_and_height_remembered_across_collapse_and_hide(self):
        p = self.panel
        p.create_note('One')
        spin()
        # XTest drives GTK's actual native paned handle, including its pointer grab.
        x11 = ctypes.CDLL('libX11.so.6')
        xtest = ctypes.CDLL('libXtst.so.6')
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XFlush.argtypes = [ctypes.c_void_p]
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        xtest.XTestFakeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                               ctypes.c_int, ctypes.c_ulong]
        xtest.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                               ctypes.c_int, ctypes.c_ulong]
        display = x11.XOpenDisplay(None)
        self.assertTrue(display)
        self.addCleanup(x11.XCloseDisplay, display)
        before = p.notes_split.get_position()
        handle = p.notes_split.get_handle_window()
        origin = handle.get_origin()
        x, y = origin[-2] + handle.get_width() // 2, origin[-1] + 3
        xtest.XTestFakeMotionEvent(display, -1, x, y, 0)
        xtest.XTestFakeButtonEvent(display, 1, True, 0)
        x11.XFlush(display)
        spin()
        xtest.XTestFakeMotionEvent(display, -1, x, y - 65, 0)
        x11.XFlush(display)
        spin()
        xtest.XTestFakeButtonEvent(display, 1, False, 0)
        x11.XFlush(display)
        spin()
        self.assertAlmostEqual(p.notes_split.get_position(), before - 65, delta=3)
        remembered = p._notes_height
        p.notes_collapse.clicked()
        self.assertTrue(p._notes_collapsed)
        self.assertFalse(p.notes_body.get_reveal_child())
        self.assertTrue(p.notes_collapse.get_visible())
        spin(.25)
        self.assertLess(p.notes_section.get_allocated_height(), 50)
        self.assertEqual(p._notes_height, remembered)
        p.notes_collapse.clicked()
        self.assertTrue(p.notes_body.get_reveal_child())
        spin(.25)
        self.assertAlmostEqual(p.notes_section.get_allocated_height(), remembered, delta=2)
        p.show_notes_section(False)
        spin()
        p.show_notes_section(True)
        spin()
        self.assertAlmostEqual(p.notes_section.get_allocated_height(), remembered, delta=2)
        self.window.resize(300, 800)
        spin()
        self.assertAlmostEqual(p.notes_section.get_allocated_height(), remembered, delta=2)

    def test_reduced_motion_and_immediate_logical_collapse_state(self):
        self.panel.create_note('One')
        with patch.object(motion, 'animations_enabled', return_value=False):
            self.panel.set_notes_collapsed(True)
        self.assertEqual(self.panel.notes_body.get_transition_duration(), 0)
        self.assertFalse(self.panel.notes_body.get_reveal_child())
        with patch.object(motion, 'animations_enabled', return_value=True):
            self.panel.set_notes_collapsed(False)
        self.assertEqual(self.panel.notes_body.get_transition_duration(), 150)
        self.assertTrue(self.panel.notes_body.get_reveal_child())

    def test_create_activate_and_active_editor_highlight(self):
        p = self.panel
        opened = []
        p.connect('file-activated', lambda _, path: opened.append(path))
        first = p.create_note('First')
        second = p.create_note('Second')
        self.assertTrue(p.notes_section_visible)
        self.assertEqual(opened, [first, second])
        self.assertEqual(p.notes_count.get_text(), '2')
        p.notes_tree.row_activated(Gtk.TreePath(0), p.notes_tree.get_column(0))
        self.assertEqual(opened[-1], first)
        self.assertTrue(p.notes_tree.get_activate_on_single_click())
        p.set_current_file(first)
        p.refresh_notes()
        self.assertEqual(p._selected_note(), first)
        p.set_current_file(str(self.root / 'plain.py'))
        self.assertIsNone(p._selected_note())
        p.open_after_create = False
        p.create_note('Third')
        self.assertEqual(len(opened), 3)
        self.assertIn('Third', self.names())

    def test_section_rename_delete_keys_and_tree_sync(self):
        p = self.panel
        path = p.create_note('First')
        p.set_current_file(path)
        with patch.object(p, '_name_dialog', side_effect=lambda title, initial, action: action('Renamed')):
            self.assertTrue(self.key(Gdk.KEY_F2))
        renamed = str(self.folder / 'Renamed.md')
        self.assertEqual(self.names(), ['Renamed'])
        self.assertEqual(p._selected_note(), renamed)
        p._reveal_note()
        self.assertEqual(p._selected_path(), renamed)
        self.assertIsNotNone(p._find(renamed))
        opened = []
        p.connect('file-activated', lambda _, path: opened.append(path))
        self.assertTrue(self.key(Gdk.KEY_Return))
        self.assertEqual(opened, [renamed])
        p.set_confirm_delete(False)
        self.assertTrue(self.key(Gdk.KEY_Delete))
        self.assertEqual(self.names(), [])
        self.assertIsNone(p._find(renamed))
        self.assertEqual(p.notes_stack.get_visible_child_name(), 'empty')

    def test_tree_operations_refresh_notes_and_use_confirmation_and_trash(self):
        p = self.panel
        self.folder.mkdir(parents=True)
        path = p._create(str(self.folder), 'From tree.md')
        self.assertEqual(self.names(), ['From tree'])
        p._rename(path, 'Changed.md')
        path = str(self.folder / 'Changed.md')
        self.assertEqual(self.names(), ['Changed'])
        p._select_note(path)
        with patch.object(p, '_confirm_removal', return_value=False) as confirm:
            self.key(Gdk.KEY_Delete)
        confirm.assert_called_once_with(path)
        self.assertTrue(Path(path).exists())
        p.set_delete_mode('trash')
        p.set_confirm_delete(False)
        with patch.object(fsops, 'trash', side_effect=fsops.TrashUnavailable('unavailable')):
            with patch.object(p, '_confirm_removal', return_value=False) as confirm:
                self.key(Gdk.KEY_Delete)
        confirm.assert_called_once_with(path, fallback=True)
        self.assertTrue(Path(path).exists())
        p._delete(path, permanent=True)
        self.assertEqual(self.names(), [])

    def test_context_menu_and_reveal_hidden_notes(self):
        p = self.panel
        path = p.create_note('One')
        p.set_show_hidden(False)
        active = str(self.root / "editor.py")
        p.set_current_file(active)
        p._select_note(path)
        with patch.object(Gtk.Menu, 'popup_at_widget'):
            p._notes_menu()
        # Menus are attached to the tree and retained by GTK until dismissed.
        menu = next(widget for widget in Gtk.Menu.get_for_attach_widget(p.notes_tree))
        self.addCleanup(menu.destroy)
        items = menu.get_children()
        self.assertEqual([item.get_label() for item in items], ['Open', 'Rename', 'Delete', 'Reveal in file tree'])
        items[-1].activate()
        self.assertTrue(p.show_hidden)
        self.assertEqual(p._active_file, active)
        self.assertEqual(p._selected_path(), path)

    def test_external_changes_monitor_refresh_and_focus(self):
        p = self.panel
        path = Path(notes.create_note(str(self.root), 'External'))
        self.assertTrue(wait_for(lambda: self.names() == ['External']))
        self.assertTrue(p.notes_section_visible)
        path = path.rename(self.folder / 'Moved.md')
        self.assertTrue(wait_for(lambda: self.names() == ['Moved']))
        path.unlink()
        self.assertTrue(wait_for(lambda: self.names() == []))
        # F5 and window focus also refresh a list even if a monitor event is missed.
        with patch.object(p, 'refresh', wraps=p.refresh) as refresh:
            self.key(Gdk.KEY_F5)
            self.window.emit('focus-in-event', Gdk.Event.new(Gdk.EventType.FOCUS_CHANGE))
        self.assertGreaterEqual(refresh.call_count, 2)
        self.assertEqual(p.notes_count.get_text(), '0')

    def test_feature_gate_prevents_show_all_and_restores_visibility(self):
        p = self.panel
        p.create_note('One')
        p.set_notes_enabled(False)
        self.window.show_all()
        self.assertFalse(p.notes_button.get_visible())
        self.assertFalse(p.notes_section.get_visible())
        self.assertFalse(p.notes_section_visible)
        self.assertFalse(p.notes_button.get_active())
        p.show_notes_section(True)
        self.assertFalse(p.notes_section_visible)
        p.set_notes_enabled(True)
        self.assertTrue(p.notes_button.get_visible())
        self.assertTrue(p.notes_section_visible)
        p.show_notes_section(False)
        p.refresh()
        self.assertFalse(p.notes_section_visible)

    def test_sort_compact_folders_first_and_git_colors(self):
        p = self.panel
        first = p.create_note('Alpha')
        second = p.create_note('Zulu')
        os.utime(first, ns=(1, 1))
        os.utime(second, ns=(2, 2))
        p.set_notes_sort('modified')
        self.assertEqual(self.names(), ['Zulu', 'Alpha'])
        p.set_notes_sort('name')
        self.assertEqual(self.names(), ['Alpha', 'Zulu'])
        with self.assertRaises(ValueError):
            p.set_notes_sort('other')
        (self.root / 'aaa.py').touch()
        (self.root / 'zzz').mkdir()
        p.set_show_hidden(False)
        p.tree.expand_row(Gtk.TreePath(0), False)
        p.set_folders_first(False)
        rows = list(p.store[p.store.get_iter_first()].iterchildren())
        self.assertEqual([r[1] for r in rows], ['aaa.py', 'zzz'])
        p.set_folders_first(True)
        self.assertEqual([r[1] for r in p.store[p.store.get_iter_first()].iterchildren()], ['zzz', 'aaa.py'])
        full_height = p._text_cell.get_fixed_size()[1]
        p.set_compact(True)
        self.assertLess(p._text_cell.get_fixed_size()[1], full_height)
        self.assertTrue(p.get_style_context().has_class('idgent-explorer-compact'))
        p.set_compact(False)
        self.assertEqual(p._text_cell.get_fixed_size()[1], full_height)
        row = p._find(str(self.root / 'aaa.py'))
        status = type('Status', (), {'kind': 'modified'})()
        p._git_snapshot = gitstatus.Snapshot(files={str(self.root / 'aaa.py'): status})
        p._git_cell_data(None, p._text_cell, p.store, row)
        self.assertTrue(p._text_cell.get_property('foreground-set'))
        p.set_git_colors(False)
        p._git_cell_data(None, p._text_cell, p.store, row)
        self.assertFalse(p._text_cell.get_property('foreground-set'))
        p.set_git_colors(True)
        p._git_cell_data(None, p._text_cell, p.store, row)
        self.assertTrue(p._text_cell.get_property('foreground-set'))


if __name__ == '__main__':
    unittest.main()
