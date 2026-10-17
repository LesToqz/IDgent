"""Window layout and command wiring against real panels."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, Gtk

from idgent.window import MainWindow


class WindowLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = Gtk.Application(application_id="dev.idgent.LayoutTests",
                                          flags=Gio.ApplicationFlags.NON_UNIQUE)
        cls.application.register(None)

    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory(prefix="idgent-layout-")
        self.addCleanup(self.folder.cleanup)
        self.window = MainWindow(self.application, self.folder.name)
        self.window.show_all()
        self.addCleanup(self.window.destroy)

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
