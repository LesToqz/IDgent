"""Temporary panels for developing the application shell independently."""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GObject, Gtk


class ExplorerPanel(Gtk.Box):
    __gsignals__ = {
        "file-activated": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "path-renamed": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        "path-deleted": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, root: str) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add(Gtk.Label(label="Explorer"))

    def set_root(self, path: str) -> None:
        pass

    def refresh(self) -> None:
        pass


class EditorPanel(Gtk.Box):
    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add(Gtk.Label(label="Editor"))

    def open_file(self, path: str) -> None:
        pass

    def save_current(self) -> bool:
        return True

    def save_all(self) -> bool:
        return True

    def has_unsaved(self) -> bool:
        return False

    def on_path_renamed(self, old: str, new: str) -> None:
        pass

    def on_path_deleted(self, path: str) -> None:
        pass


class TerminalPanel(Gtk.Box):
    def __init__(self, cwd: str) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add(Gtk.Label(label="Terminal"))

    def set_cwd(self, path: str) -> None:
        pass

    def new_terminal(self) -> None:
        pass

    def close_current(self) -> None:
        pass
