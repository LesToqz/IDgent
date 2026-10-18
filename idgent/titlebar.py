"""Compact client-side titlebar with the application's existing menus."""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GObject, Gtk

from idgent.themes import get_theme


def build_titlebar_css(ui: dict[str, str]) -> str:
    """Build the titlebar rules from the same roles as the application theme."""
    return f"""
headerbar.idgent-titlebar {{
    background: {ui['titlebar_bg']}; color: {ui['titlebar_fg']}; min-height: 34px;
    padding: 0; border: 0; border-radius: 0; box-shadow: none;
}}
.idgent-titlebar .idgent-title {{ color: {ui['title_fg']}; font-size: 12px; }}
.idgent-titlebar menubar {{
    background: transparent; color: {ui['titlebar_fg']}; padding: 0; border: 0;
}}
.idgent-titlebar menubar > menuitem {{ padding: 4px 8px; }}
.idgent-titlebar menubar > menuitem:hover {{ background: {ui['button_hover']}; }}
.idgent-titlebar button.titlebutton,
.idgent-titlebar button.idgent-settings-button {{
    background: transparent; color: {ui['titlebar_fg']}; border: 0; border-radius: 0;
    box-shadow: none; text-shadow: none; min-height: 32px; min-width: 32px;
    padding: 0; margin: 0;
}}
.idgent-titlebar button.titlebutton:hover,
.idgent-titlebar button.idgent-settings-button:hover {{ background: {ui['button_hover']}; }}
.idgent-titlebar button.titlebutton.close:hover {{ background: {ui['close_hover']}; color: {ui['close_fg']}; }}
.idgent-titlebar separator {{ background: transparent; min-width: 0; margin: 0; }}
"""


TITLEBAR_CSS = build_titlebar_css(get_theme(None).ui)


class TitleBar(Gtk.HeaderBar):
    __gsignals__ = {
        "settings-requested": (GObject.SignalFlags.RUN_LAST, None, ()),
    }

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
        self.title_label.set_text("IDgent")
        self.settings_button = Gtk.Button()
        self.settings_button.set_relief(Gtk.ReliefStyle.NONE)
        self.settings_button.set_valign(Gtk.Align.CENTER)
        context = self.settings_button.get_style_context()
        context.add_class("flat")
        context.add_class("idgent-settings-button")
        icon_theme = Gtk.IconTheme.get_default()
        for name in ("preferences-system-symbolic", "emblem-system-symbolic",
                     "preferences-other-symbolic"):
            if icon_theme is not None and icon_theme.has_icon(name):
                self.settings_button.add(Gtk.Image.new_from_icon_name(name, Gtk.IconSize.MENU))
                break
        else:
            self.settings_button.add(Gtk.Label(label="⚙"))
        self.settings_button.set_tooltip_text("Settings (Ctrl+,)")
        self.settings_button.get_accessible().set_name("Settings")
        self.settings_button.connect("clicked", lambda _: self.emit("settings-requested"))
        self.pack_end(self.settings_button)
