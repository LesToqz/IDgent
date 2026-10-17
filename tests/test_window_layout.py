"""Window layout and command wiring against real panels."""

from pathlib import Path
import os
import signal
import time
import tempfile
import unittest
from unittest.mock import patch

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
        self.assertTrue(self.poll(lambda: self.window.terminal._tabs[0].pid is not None))
        self.shell_pid = self.window.terminal._tabs[0].pid

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
        self.assertEqual(self.window.terminal_position, "bottom")
        self.assertEqual(self.window.dock.get_orientation(), Gtk.Orientation.VERTICAL)
        self.assertEqual(self.window.get_default_size(), (1200, 750))
        self.assertIn(Path(self.folder.name).name, self.window.get_title())
        self.assertEqual(self.window.sidebar.get_position(), 260)

    def test_move_preserves_terminal(self) -> None:
        terminal = self.window.terminal
        for position, orientation, label in (
            ("right", Gtk.Orientation.HORIZONTAL, "Move Terminal to Bottom"),
            ("bottom", Gtk.Orientation.VERTICAL, "Move Terminal to Right"),
        ):
            self.window.toggle_terminal_position()
            self.assertEqual(self.window.terminal_position, position)
            self.assertEqual(self.window.dock.get_orientation(), orientation)
            self.assertIs(self.window.terminal, terminal)
            self.assertIs(self.window.dock.get_child2(), terminal)
            self.assertIs(terminal.get_parent(), self.window.dock)
            self.assertEqual(self.window.move_terminal_item.get_label(), label)

    def test_terminal_hide_move_show(self) -> None:
        self.window.toggle_terminal()
        self.assertFalse(self.window.terminal.get_visible())
        self.window.toggle_terminal_position()
        self.window.show_all()
        self.assertFalse(self.window.terminal.get_visible())
        self.window.toggle_terminal()
        self.assertTrue(self.window.terminal.get_visible())

    def settle_layout(self) -> None:
        deadline = time.monotonic() + 1
        self.assertTrue(self.poll(lambda: time.monotonic() >= deadline))

    def assert_right_terminal_size(self, window) -> None:
        width = window.terminal.get_allocated_width()
        dock_width = window.dock.get_allocated_width()
        self.assertGreaterEqual(width, 380)
        self.assertLessEqual(width, dock_width * .6)
        self.assertAlmostEqual(width, max(380, round(dock_width * .4)), delta=1)

    def test_allocated_terminal_dock_sizes(self) -> None:
        self.window.resize(1200, 750)
        terminal = self.window.terminal
        self.window.toggle_terminal_position()
        self.settle_layout()
        self.assert_right_terminal_size(self.window)
        self.window.toggle_terminal_position()
        self.settle_layout()
        height = terminal.get_allocated_height()
        self.assertGreaterEqual(height, 150)
        self.assertAlmostEqual(height, self.window.dock.get_allocated_height() * .3,
                               delta=1)
        self.assertIs(self.window.terminal, terminal)

    def test_move_before_realize(self) -> None:
        window = MainWindow(self.application, self.folder.name)
        self.addCleanup(window.destroy)
        self.assertFalse(window.get_realized())
        window.toggle_terminal_position()
        window.show_all()
        self.settle_layout()
        self.assert_right_terminal_size(window)

    def test_hidden_terminal_dock_size(self) -> None:
        self.window.toggle_terminal()
        self.window.toggle_terminal_position()
        self.settle_layout()
        self.window.toggle_terminal()
        self.window.toggle_explorer()
        self.settle_layout()
        self.assert_right_terminal_size(self.window)
        self.window.toggle_explorer()
        self.settle_layout()
        self.assertGreaterEqual(self.window.terminal.get_allocated_width(), 380)

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
             patch.object(self.window.terminal, "set_cwd") as terminal:
            self.window.set_root(root)
            explorer.assert_called_once_with(root)
            terminal.assert_called_once_with(root)
            self.assertEqual(self.window.get_title(), f"IDgent - {Path(root).name}")

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
