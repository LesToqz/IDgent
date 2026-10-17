"""Client-side titlebar layout, theme, folder title, and menu accelerators."""

import os
from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import patch

os.environ["GDK_BACKEND"] = "x11"

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, Gtk

from idgent.titlebar import TITLEBAR_CSS, TitleBar
from idgent.window import MainWindow
from tests.test_integration import alive, poll


def descendants(widget):
    yield widget
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            yield from descendants(child)


class TitleBarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = Gtk.Application.get_default() or Gtk.Application(
            application_id="dev.idgent.Tests", flags=Gio.ApplicationFlags.NON_UNIQUE)
        if not cls.application.get_is_registered():
            assert cls.application.register(None)

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="idgent-titlebar-")
        self.addCleanup(self.folder.cleanup)
        self.window = MainWindow(self.application, self.folder.name)
        self.addCleanup(self.destroy_window)
        self.window.show_all()
        self.assertTrue(poll(lambda: self.window.terminal_bottom._panes[0].pid is not None))

    def destroy_window(self):
        pids = [pane.pid for panel in (self.window.terminal_bottom, self.window.terminal_right)
                for pane in panel._panes if pane.pid is not None]
        self.window.destroy()
        stopped = poll(lambda: all(not alive(pid) for pid in pids))
        for pid in pids:
            if alive(pid):
                os.kill(pid, signal.SIGKILL)
        self.assertTrue(stopped, "shell survived window destruction")

    def test_menus_share_header_and_leave_content(self):
        bar = self.window.get_titlebar()
        self.assertIs(bar, self.window.titlebar)
        self.assertIsInstance(bar, TitleBar)
        self.assertIsInstance(bar, Gtk.HeaderBar)
        menus = [widget for widget in descendants(bar) if isinstance(widget, Gtk.MenuItem)]
        self.assertEqual([item.get_label() for item in menus], ["File", "View"])
        self.assertTrue(all(item.is_ancestor(bar) for item in menus))
        self.assertFalse(any(isinstance(widget, Gtk.MenuBar)
                             for widget in descendants(self.window.get_child())))
        self.assertEqual(bar.menu_bar.get_valign(), Gtk.Align.CENTER)
        self.assertEqual(bar.get_decoration_layout(), ":minimize,maximize,close")
        self.assertTrue(bar.get_show_close_button())

    def test_title_is_just_the_application_name(self):
        bar = self.window.titlebar
        self.assertIs(bar.get_custom_title(), bar.title_label)
        self.assertTrue(bar.title_label.get_style_context().has_class("idgent-title"))
        self.assertEqual(bar.title_label.get_text(), "IDgent")
        self.assertEqual(self.window.get_title(), "IDgent")
        with tempfile.TemporaryDirectory(prefix="idgent-titlebar-new-") as folder:
            self.window.set_root(folder)
            self.assertEqual(bar.title_label.get_text(), "IDgent")
            self.assertEqual(self.window.get_title(), "IDgent")
        self.assertFalse(hasattr(bar, "set_folder"))

    def test_css_parses_and_keeps_title_centered_in_compact_bar(self):
        provider = Gtk.CssProvider()
        errors = []
        provider.connect("parsing-error", lambda _, section, error: errors.append(str(error)))
        provider.load_from_data(TITLEBAR_CSS.encode("utf-8"))
        self.assertEqual(errors, [])
        screen = Gdk.Screen.get_default()
        Gtk.StyleContext.add_provider_for_screen(
            screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        self.addCleanup(Gtk.StyleContext.remove_provider_for_screen, screen, provider)
        bar = self.window.titlebar
        self.assertTrue(poll(lambda: 32 <= bar.get_allocated_height() <= 35))
        label = bar.title_label
        allocation = label.get_allocation()
        x, _ = label.translate_coordinates(bar, 0, 0)
        self.assertAlmostEqual(x + allocation.width / 2,
                               bar.get_allocated_width() / 2, delta=1)

    def accelerate(self, shortcut):
        key, modifiers = Gtk.accelerator_parse(shortcut)
        self.assertTrue(Gtk.accel_groups_activate(self.window, key, modifiers), shortcut)

    def test_all_accelerators_fire_actions(self):
        self.accelerate("<Control>grave")
        self.assertFalse(self.window.terminal_bottom.get_visible())
        self.accelerate("<Control>grave")
        self.assertTrue(self.window.terminal_bottom.get_visible())
        self.accelerate("<Control><Alt>grave")
        self.assertTrue(self.window.terminal_right.get_visible())
        self.assertTrue(poll(lambda: self.window.terminal_right._panes[0].pid is not None))
        self.window.terminal_right.focus_terminal()
        self.accelerate("<Control><Shift>5")
        self.assertEqual(self.window.terminal_right.terminal_count(), 2)
        self.assertTrue(poll(lambda: self.window.terminal_right._panes[-1].pid is not None))
        self.accelerate("<Control>b")
        self.assertFalse(self.window.explorer.get_visible())
        self.accelerate("<Control>b")
        self.assertTrue(self.window.explorer.get_visible())

        paths = [Path(self.folder.name) / name for name in ("one.py", "two.py")]
        for path in paths:
            path.write_text("original\n")
            self.window.editor.open_file(str(path))
            self.window.editor._current().buffer.set_text("saved all\n")
        self.accelerate("<Control>s")
        self.assertEqual(paths[1].read_text(), "saved all\n")
        self.assertEqual(paths[0].read_text(), "original\n")
        self.accelerate("<Control><Shift>s")
        self.assertEqual(paths[0].read_text(), "saved all\n")
        self.assertFalse(self.window.editor.has_unsaved())
        self.window.editor._current().buffer.set_text("quit cancelled\n")
        with patch.object(self.window, "_ask_quit", return_value=Gtk.ResponseType.CANCEL) as ask:
            self.accelerate("<Control>q")
            self.assertTrue(poll(lambda: ask.called))
            self.assertTrue(self.window.get_visible())


if __name__ == "__main__":
    unittest.main()
