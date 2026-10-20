"""Compact client-side titlebar with the application's existing menus."""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GObject, Gtk

from idgent import motion
from idgent.themes import get_theme

# A 39 px strip plus a 1 px divider; window controls are 40 px wide.
HEADER_HEIGHT = 40
WINDOW_BUTTON_WIDTH = 40
# The symbolic assets have padding around the mark: a 32 px image renders
# a 16 px glyph, matching the MENU-sized cog.
WINDOW_ICON_SIZE = 32
# Resting cog glyph opacity; hovering fades it to 1 over 140 ms.
GLYPH_OPACITY = 0.85


def build_titlebar_css(ui: dict[str, str]) -> str:
    """Build the titlebar rules from the same roles as the application theme.

    IDgent window chrome: a 39 px strip plus a 1 px divider on the secondary
    background, 40 px flat window controls, and clickable-icon hovers (radius 4,
    translucent overlay) for the menus and the settings cog. Hovers are instant;
    only the cog glyph opacity fades (driven by ``motion``, see ``TitleBar``).
    ``margin: 0`` cancels the theme's -1 px solid-CSD margin that would eat the
    divider when no compositor is running.
    """
    hover = f"alpha({ui['overlay']}, 0.067)"
    pressed = f"alpha({ui['overlay']}, 0.12)"
    return f"""
headerbar.idgent-titlebar, headerbar.idgent-titlebar:backdrop {{
    background: {ui['sidebar_bg']}; color: {ui['titlebar_fg']}; min-height: {HEADER_HEIGHT - 1}px;
    padding: 0; margin: 0; border: 0; border-bottom: 1px solid {ui['border']}; border-radius: 0;
    box-shadow: none;
}}
headerbar.idgent-titlebar:backdrop {{ color: {ui['muted']}; }}
.idgent-titlebar .idgent-title {{ color: {ui['title_fg']}; font-size: 13px; font-weight: 600; }}
.idgent-titlebar menubar {{
    background: transparent; color: inherit; padding: 0; margin: 0 0 0 6px;
    border: 0; box-shadow: none;
}}
.idgent-titlebar menubar > menuitem {{
    color: inherit; font-size: 13px; padding: 4px 8px; min-height: 20px; margin: 0 1px;
    border: 0; border-radius: 4px; box-shadow: none; transition: none;
}}
.idgent-titlebar menubar > menuitem:hover {{
    background: {hover}; color: {ui['titlebar_fg']}; box-shadow: none;
}}
.idgent-titlebar button.titlebutton,
.idgent-titlebar button.idgent-settings-button {{
    background: transparent; color: {ui['muted']}; border: 0; box-shadow: none;
    text-shadow: none; -gtk-icon-shadow: none; margin: 0; transition: none;
}}
.idgent-titlebar button.titlebutton {{
    border-radius: 0; min-width: {WINDOW_BUTTON_WIDTH}px; min-height: {HEADER_HEIGHT - 1}px; padding: 0;
}}
.idgent-titlebar button.titlebutton image {{ -gtk-icon-transform: none; }}
.idgent-titlebar button.idgent-settings-button {{
    border-radius: 4px; -gtk-outline-radius: 4px; min-width: 16px; min-height: 16px; padding: 6px;
}}
.idgent-titlebar button.titlebutton:hover,
.idgent-titlebar button.idgent-settings-button:hover {{ background: {hover}; color: {ui['foreground']}; }}
.idgent-titlebar button.titlebutton:active,
.idgent-titlebar button.idgent-settings-button:checked,
.idgent-titlebar button.idgent-settings-button:active {{ background: {pressed}; color: {ui['foreground']}; }}
.idgent-titlebar button.titlebutton.close:hover,
.idgent-titlebar button.titlebutton.close:active {{ background: {ui['danger']}; color: #ffffff; }}
.idgent-titlebar separator {{ background: transparent; min-width: 0; margin: 0; }}
"""


TITLEBAR_CSS = build_titlebar_css(get_theme(None).ui)


class TitleBar(Gtk.HeaderBar):
    __gsignals__ = {
        "settings-requested": (GObject.SignalFlags.RUN_LAST, None, ()),
        "dock-toggle-requested": (GObject.SignalFlags.RUN_LAST, None, ()),
        "bottom-dock-toggle-requested": (GObject.SignalFlags.RUN_LAST, None, ()),
        "explorer-toggle-requested": (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(self, menu_bar: Gtk.MenuBar) -> None:
        super().__init__()
        self.get_style_context().add_class("idgent-titlebar")
        # Window controls form one contiguous 3 x 40 px block; margins space the rest.
        self.props.spacing = 0
        self.set_show_close_button(True)
        self.set_decoration_layout(":minimize,maximize,close")
        self.menu_bar = menu_bar
        menu_bar.set_valign(Gtk.Align.CENTER)
        self.pack_start(menu_bar)
        icon_theme = Gtk.IconTheme.get_default()
        self.explorer_button, self.explorer_fader, self._explorer_click_handler = self._toggle_icon(
            icon_theme, ("sidebar-show-symbolic", "panel-left-symbolic"), "▯",
            "Toggle file explorer (Ctrl+B)", "Toggle file explorer", "explorer-toggle-requested")
        self.explorer_button.set_margin_start(2)
        self.pack_start(self.explorer_button)
        self.title_label = Gtk.Label()
        self.title_label.get_style_context().add_class("idgent-title")
        self.set_custom_title(self.title_label)
        self.title_label.set_text("IDgent")
        self.settings_button = Gtk.Button()
        self.settings_button.set_relief(Gtk.ReliefStyle.NONE)
        self.settings_button.set_valign(Gtk.Align.CENTER)
        # A widget margin (outside the allocation) keeps the hit area 28 x 28.
        self.settings_button.set_margin_end(6)
        context = self.settings_button.get_style_context()
        context.add_class("flat")
        context.add_class("idgent-settings-button")
        for name in ("preferences-system-symbolic", "emblem-system-symbolic",
                     "preferences-other-symbolic"):
            if icon_theme is not None and icon_theme.has_icon(name):
                self.settings_button.add(Gtk.Image.new_from_icon_name(name, Gtk.IconSize.MENU))
                break
        else:
            self.settings_button.add(Gtk.Label(label="⚙"))
        self.settings_fader = motion.StateFader(
            self.settings_button, self._set_settings_glyph, "clickable_icon_opacity", "ease_in_out")
        self.settings_button.set_tooltip_text("Settings (Ctrl+,)")
        self.settings_button.get_accessible().set_name("Settings")
        self.settings_button.connect("clicked", lambda _: self.emit("settings-requested"))
        self.pack_end(self.settings_button)
        self.dock_button, self.dock_fader, self._dock_click_handler = self._toggle_icon(
            icon_theme, ("sidebar-show-right-symbolic", "panel-right-symbolic"), "▯",
            "Toggle right terminal / graph dock (Ctrl+Alt+`)", "Toggle right dock",
            "dock-toggle-requested")
        self.dock_button.set_margin_end(6)
        self.pack_end(self.dock_button)
        self.bottom_dock_button, self.bottom_dock_fader, self._bottom_dock_click_handler = (
            self._toggle_icon(icon_theme, ("panel-bottom-symbolic", "sidebar-show-bottom-symbolic"),
                              "▭", "Toggle terminal below (Ctrl+`)", "Toggle terminal below",
                              "bottom-dock-toggle-requested"))
        self.bottom_dock_button.set_margin_end(2)
        self.pack_end(self.bottom_dock_button)

    def _toggle_icon(self, icon_theme, names, fallback, tooltip, accessible, signal):
        """A 28 x 28 toggle in the cog's clickable-icon style, with its glyph fade."""
        button = Gtk.ToggleButton()
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.set_valign(Gtk.Align.CENTER)
        button.get_style_context().add_class("flat")
        button.get_style_context().add_class("idgent-settings-button")
        for name in names:
            if icon_theme is not None and icon_theme.has_icon(name):
                button.add(Gtk.Image.new_from_icon_name(name, Gtk.IconSize.MENU))
                break
        else:
            button.add(Gtk.Label(label=fallback))
        fader = motion.StateFader(
            button, lambda hover: button.get_child().set_opacity(
                GLYPH_OPACITY + (1 - GLYPH_OPACITY) * hover),
            "clickable_icon_opacity", "ease_in_out")
        button.set_tooltip_text(tooltip)
        button.get_accessible().set_name(accessible)
        handler = button.connect("clicked", lambda _: self.emit(signal))
        return button, fader, handler

    @staticmethod
    def _sync(button: Gtk.ToggleButton, handler: int, active: bool) -> None:
        # Gtk.ToggleButton.set_active emits clicked; syncing must not toggle anything.
        with button.handler_block(handler):
            button.set_active(active)

    def set_dock_active(self, active: bool) -> None:
        self._sync(self.dock_button, self._dock_click_handler, active)

    def set_bottom_dock_active(self, active: bool) -> None:
        self._sync(self.bottom_dock_button, self._bottom_dock_click_handler, active)

    def set_explorer_active(self, active: bool) -> None:
        self._sync(self.explorer_button, self._explorer_click_handler, active)

    def do_size_allocate(self, allocation) -> None:
        # GTK owns these children and rebuilds them when window state changes.
        # Set the native image size before allocation, including restore icons,
        # without changing the settings cog or depending on GTK's private boxes.
        def size_controls(widget):
            if isinstance(widget, Gtk.Button) and widget.get_style_context().has_class("titlebutton"):
                image = widget.get_child()
                if isinstance(image, Gtk.Image) and image.get_pixel_size() != WINDOW_ICON_SIZE:
                    image.set_pixel_size(WINDOW_ICON_SIZE)
            elif isinstance(widget, Gtk.Container):
                widget.forall(size_controls)

        self.forall(size_controls)
        Gtk.HeaderBar.do_size_allocate(self, allocation)

    def _set_settings_glyph(self, hover: float) -> None:
        self.settings_button.get_child().set_opacity(GLYPH_OPACITY + (1 - GLYPH_OPACITY) * hover)
