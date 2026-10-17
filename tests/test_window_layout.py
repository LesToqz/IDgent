"""Window layout and command wiring against real panels."""

from pathlib import Path
import os
import signal
import time
import tempfile
import unittest
from unittest.mock import patch

# Keep GTK on the Xvfb display even when WAYLAND_DISPLAY is inherited.
os.environ["GDK_BACKEND"] = "x11"

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk

from idgent.window import MainWindow


class WindowLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = Gtk.Application.get_default() or Gtk.Application(
            application_id="dev.idgent.Tests",
                                          flags=Gio.ApplicationFlags.NON_UNIQUE)
        if not cls.application.get_is_registered():
            assert cls.application.register(None)

    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory(prefix="idgent-layout-")
        self.addCleanup(self.folder.cleanup)
        self.window = MainWindow(self.application, self.folder.name)
        self.window.show_all()
        self.addCleanup(self.destroy_window)
        self.assertTrue(self.poll(lambda: self.window.terminal_bottom._panes[0].pid is not None))
        self.shell_pid = self.window.terminal_bottom._panes[0].pid

    @staticmethod
    def poll(predicate):
        deadline = time.monotonic() + 5
        context = GLib.MainContext.default()
        while time.monotonic() < deadline:
            while context.pending():
                context.iteration(False)
            if predicate():
                return True
            time.sleep(.01)
        return bool(predicate())

    def destroy_window(self):
        self.window.destroy()
        pid = getattr(self, "shell_pid", None)
        if pid is None:
            return

        def stopped():
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            return False

        exited = self.poll(stopped)
        if not exited:
            os.kill(pid, signal.SIGKILL)
        self.assertTrue(exited, "shell survived window destruction")

    def test_default_layout_and_title(self) -> None:
        self.assertTrue(self.window.terminal_bottom.get_visible())
        self.assertFalse(self.window.terminal_right.get_visible())
        self.assertEqual(self.window.terminal_right.terminal_count(), 0)
        self.assertEqual(self.window.dock.get_orientation(), Gtk.Orientation.HORIZONTAL)
        self.assertEqual(self.window.dock_bottom.get_orientation(), Gtk.Orientation.VERTICAL)
        self.assertIs(self.window.dock.get_child1(), self.window.dock_bottom)
        self.assertIs(self.window.dock.get_child2(), self.window.terminal_right)
        self.assertIs(self.window.dock_bottom.get_child2(), self.window.terminal_bottom)
        self.assertEqual(self.window.get_default_size(), (1200, 750))
        self.assertIn(Path(self.folder.name).name, self.window.get_title())
        self.assertEqual(self.window.get_child().get_children(), [self.window.sidebar])
        self.assertEqual(self.window.sidebar.get_position(), 260)

    def settle_layout(self) -> None:
        deadline = time.monotonic() + .2
        self.assertTrue(self.poll(lambda: time.monotonic() >= deadline))

    def assert_right_terminal_size(self, window) -> None:
        width = window.terminal_right.get_allocated_width()
        dock_width = window.dock.get_allocated_width()
        self.assertGreaterEqual(width, 380)
        self.assertLessEqual(width, dock_width * .6)
        self.assertAlmostEqual(width, max(380, round(dock_width * .4)), delta=1)
        self.assertGreaterEqual(window.editor.get_allocated_width(), 300)
        self.assertGreaterEqual(window.editor.get_allocated_height(), 150)

    def test_both_docks_allocated(self) -> None:
        self.window.toggle_terminal_right()
        self.settle_layout()
        self.assertTrue(self.window.terminal_bottom.get_visible())
        self.assertTrue(self.window.terminal_right.get_visible())
        self.assert_right_terminal_size(self.window)
        height = self.window.terminal_bottom.get_allocated_height()
        self.assertGreaterEqual(height, 150)
        self.assertAlmostEqual(height, self.window.dock_bottom.get_allocated_height() * .3,
                               delta=1)

    def test_show_right_before_realize(self) -> None:
        window = MainWindow(self.application, self.folder.name)
        self.addCleanup(window.destroy)
        self.assertFalse(window.get_realized())
        window.toggle_terminal_right()
        window.show_all()
        self.settle_layout()
        self.assert_right_terminal_size(window)

    def test_hidden_dock_stays_hidden_after_show_all(self) -> None:
        self.window.toggle_terminal_bottom()
        self.window.toggle_terminal_right()
        self.window.show_all()
        self.assertFalse(self.window.terminal_bottom.get_visible())
        self.assertTrue(self.window.terminal_right.get_visible())
        self.window.toggle_explorer()
        self.settle_layout()
        self.assert_right_terminal_size(self.window)
        self.window.toggle_terminal_bottom()
        self.assertTrue(self.window.terminal_bottom.get_visible())

    def test_last_closed_hides_each_dock_and_show_respawns(self) -> None:
        self.window.toggle_terminal_right()
        for panel, toggle in ((self.window.terminal_right, self.window.toggle_terminal_right),
                              (self.window.terminal_bottom, self.window.toggle_terminal_bottom)):
            panel.close_current()
            self.assertFalse(panel.get_visible())
            self.assertEqual(panel.terminal_count(), 0)
            toggle()
            self.assertTrue(panel.get_visible())
            self.assertEqual(panel.terminal_count(), 1)

    def test_explorer_toggle(self) -> None:
        self.window.toggle_explorer()
        self.assertFalse(self.window.explorer.get_visible())
        self.window.toggle_explorer()
        self.assertTrue(self.window.explorer.get_visible())

    def test_explorer_signals(self) -> None:
        with patch.object(self.window.editor, "open_file") as opened, \
             patch.object(self.window.editor, "on_path_renamed") as renamed, \
             patch.object(self.window.editor, "on_path_deleted") as deleted:
            self.window.explorer.emit("file-activated", "/tmp/a")
            self.window.explorer.emit("path-renamed", "/tmp/a", "/tmp/b")
            self.window.explorer.emit("path-deleted", "/tmp/b")
            opened.assert_called_once_with("/tmp/a")
            renamed.assert_called_once_with("/tmp/a", "/tmp/b")
            deleted.assert_called_once_with("/tmp/b")

    def test_folder_change(self) -> None:
        with tempfile.TemporaryDirectory(prefix="idgent-new-root-") as root, \
             patch.object(self.window.explorer, "set_root") as explorer, \
             patch.object(self.window.terminal_bottom, "set_cwd") as terminal, \
             patch.object(self.window.terminal_right, "set_cwd") as right:
            self.window.set_root(root)
            explorer.assert_called_once_with(root)
            terminal.assert_called_once_with(root)
            right.assert_called_once_with(root)
            self.assertEqual(self.window.get_title(), f"IDgent - {Path(root).name}")
            self.assertEqual(self.window.titlebar.title_label.get_text(), self.window.get_title())

    def test_quit_confirmation(self) -> None:
        self.assertFalse(self.window._on_delete(self.window, None))
        with patch.object(self.window.editor, "has_unsaved", return_value=True), \
             patch.object(self.window, "_ask_quit") as ask:
            ask.return_value = Gtk.ResponseType.CANCEL
            self.assertTrue(self.window._on_delete(self.window, None))
            ask.return_value = Gtk.ResponseType.REJECT
            self.assertFalse(self.window._on_delete(self.window, None))
            ask.return_value = Gtk.ResponseType.ACCEPT
            with patch.object(self.window.editor, "save_all", return_value=False):
                self.assertTrue(self.window._on_delete(self.window, None))
            with patch.object(self.window.editor, "has_unsaved", side_effect=[True, False]), \
                 patch.object(self.window.editor, "save_all", return_value=True):
                self.assertFalse(self.window._on_delete(self.window, None))


if __name__ == "__main__":
    unittest.main()
