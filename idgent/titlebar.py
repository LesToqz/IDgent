"""Compact client-side titlebar with the application's existing menus."""

from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk


TITLEBAR_CSS = """
headerbar.idgent-titlebar {
    background: #333333; color: #cccccc; min-height: 34px;
    padding: 0; border: 0; border-radius: 0; box-shadow: none;
}
.idgent-titlebar .idgent-title { color: #9d9d9d; font-size: 12px; }
.idgent-titlebar menubar {
    background: transparent; color: #cccccc; padding: 0; border: 0;
}
.idgent-titlebar menubar > menuitem { padding: 4px 8px; }
.idgent-titlebar menubar > menuitem:hover { background: #505050; }
.idgent-titlebar button.titlebutton {
    background: transparent; color: #cccccc; border: 0; border-radius: 0;
    box-shadow: none; text-shadow: none; min-height: 32px; min-width: 32px;
    padding: 0; margin: 0;
}
.idgent-titlebar button.titlebutton:hover { background: #505050; }
.idgent-titlebar button.titlebutton.close:hover { background: #c42b1c; color: white; }
.idgent-titlebar separator { background: transparent; min-width: 0; margin: 0; }
"""


class TitleBar(Gtk.HeaderBar):
    def __init__(self, menu_bar: Gtk.MenuBar) -> None:
        super().__init__()
        self.get_style_context().add_class("idgent-titlebar")
        self.set_show_close_button(True)
        self.set_decoration_layout(":minimize,maximize,close")
        self.menu_bar = menu_bar
        menu_bar.set_valign(Gtk.Align.CENTER)
        self.pack_start(menu_bar)
        self.title_label = Gtk.Label()
        self.title_label.get_style_context().add_class("idgent-title")
        self.set_custom_title(self.title_label)
        self.set_folder("/")

    def set_folder(self, root: str) -> None:
        """Show the folder's basename, preserving the filesystem root."""
        folder = Path(root)
        self.title_label.set_text(f"IDgent - {folder.name or str(folder)}")
