"""GTK application and command-line entry point."""

import argparse
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from idgent import settings
from idgent.themes import get_theme
from idgent.style import apply_style
from idgent.window import MainWindow


class Application(Gtk.Application):
    def __init__(self, root: str) -> None:
        super().__init__(application_id="dev.idgent.IDgent")
        self.root = root

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        self.theme = get_theme(settings.get_theme_id())
        apply_style(self.theme)

    def do_activate(self) -> None:
        window = self.get_active_window()
        if window is None:
            window = MainWindow(self, self.root, theme=self.theme)
            window.show_all()
        window.present()


def main() -> int:
    parser = argparse.ArgumentParser(description="IDgent desktop IDE")
    parser.add_argument("folder", nargs="?", default=str(Path.cwd()))
    args = parser.parse_args()
    root = Path(args.folder).expanduser().resolve()
    if not root.is_dir():
        parser.error(f"not a directory: {args.folder}")
    return Application(str(root)).run([])
