"""Widgets of a terminal dock: per-pane header, "+" button and limit toast."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, GObject, Gtk, Pango

from idgent import motion
from idgent.themes import _legible

# Key of the opacity tween started by `fade()`; `cancel_fade()` stops only that one.
_FADE_KEY = object()
# Header glyphs rest slightly dimmed and reach full opacity on hover (spec 4.3).
GLYPH_OPACITY = 0.85
# The notice background is black at 90%, so its text must stay legible on near-black.
_NOTICE_BG = "#1a1a1a"


def cancel_fade(widget: Gtk.Widget) -> None:
    """Stop a running `fade()` on `widget`, leaving its opacity as it is."""
    handler = getattr(widget, "_fade_settle", None)
    if handler is not None:
        widget._fade_settle = None
        if widget.handler_is_connected(handler):
            widget.disconnect(handler)
    tween = getattr(widget, "_motion_tweens", {}).get(_FADE_KEY)
    if tween is not None:
        tween.cancel()


def fade(widget: Gtk.Widget, start: float, end: float, duration_ms: int, done=None) -> None:
    """Animate `widget` opacity from `start` to `end`; jump there when it cannot animate.

    Compatibility wrapper over `motion.animate` (linear, so reduced motion is
    honoured). Unlike a bare tween, an unmap mid-way settles at `end` instead of
    leaving a half-transparent widget behind.
    """
    cancel_fade(widget)
    widget.set_opacity(start)
    if duration_ms <= 0 or not widget.get_mapped():
        widget.set_opacity(end)
        if done is not None:
            done()
        return

    def finish():
        handler, widget._fade_settle = widget._fade_settle, None
        if handler is not None and widget.handler_is_connected(handler):
            widget.disconnect(handler)
        if done is not None:
            done()

    def settle(_widget):
        widget.set_opacity(end)
        finish()

    # Connected before the tween so it runs before motion cancels on unmap.
    widget._fade_settle = widget.connect("unmap", settle)
    motion.animate(widget, duration_ms, lambda t: widget.set_opacity(start + (end - start) * t),
                   easing="linear", on_done=finish, key=_FADE_KEY)


class _IconButton(Gtk.Button):
    """Clickable icon: falls back to a text glyph when the theme lacks the icon.

    The glyph rests at GLYPH_OPACITY and fades to 1 on hover; the hover
    background itself is instant CSS.
    """

    def __init__(self, icon_name: str, glyph: str, tooltip: str):
        super().__init__()
        self.set_relief(Gtk.ReliefStyle.NONE)
        # Clicking a header must not steal keyboard focus from the terminal.
        self.set_focus_on_click(False)
        self.set_tooltip_text(tooltip)
        self.get_style_context().add_class("idgent-terminal-icon")
        self.icon_name = icon_name
        self.glyph_opacity = GLYPH_OPACITY
        self.set_icon(icon_name, glyph)
        self.glyph_fader = motion.StateFader(self, self._set_glyph_opacity,
                                             duration="clickable_icon_opacity",
                                             easing="ease_in_out")

    def _set_glyph_opacity(self, hover: float) -> None:
        self.glyph_opacity = GLYPH_OPACITY + (1 - GLYPH_OPACITY) * hover
        child = self.get_child()
        if child is not None:
            child.set_opacity(self.glyph_opacity)

    def set_icon(self, icon_name: str, glyph: str) -> None:
        self.icon_name = icon_name
        old = self.get_child()
        if old is not None:
            self.remove(old)
        theme = Gtk.IconTheme.get_default()
        if theme is not None and theme.has_icon(icon_name):
            content = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.MENU)
        else:
            content = Gtk.Label(label=glyph)
        content.set_opacity(self.glyph_opacity)
        content.show()
        self.add(content)


class PaneHeader(Gtk.Box):
    """Label, fullscreen and delete controls sitting directly above one terminal."""

    __gsignals__ = {
        "focus-requested": (GObject.SignalFlags.RUN_LAST, None, ()),
        "fullscreen-requested": (GObject.SignalFlags.RUN_LAST, None, ()),
        "delete-requested": (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(self, shell_name: str = "bash"):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.shell_name = shell_name
        self.get_style_context().add_class("idgent-terminal-header")
        self.set_hexpand(True)
        self.label = Gtk.Label(xalign=0)
        # Ellipsizing keeps headers shrinkable in a narrow dock.
        self.label.set_ellipsize(Pango.EllipsizeMode.END)
        self.label_button = Gtk.Button()
        self.label_button.set_relief(Gtk.ReliefStyle.NONE)
        self.label_button.set_focus_on_click(False)
        self.label_button.get_style_context().add_class("idgent-terminal-header-label")
        self.label_button.add(self.label)
        self.label_button.connect("clicked", lambda _b: self.emit("focus-requested"))
        self.fullscreen_button = _IconButton("view-fullscreen-symbolic", "□", "Fullscreen")
        self.fullscreen_button.connect("clicked", lambda _b: self.emit("fullscreen-requested"))
        self.delete_button = _IconButton("edit-delete-symbolic", "✕", "Kill terminal")
        self.delete_button.get_style_context().add_class("idgent-terminal-delete")
        self.delete_button.connect("clicked", lambda _b: self.emit("delete-requested"))
        self.pack_start(self.label_button, False, True, 0)
        # pack_end puts the first widget at the far right and later ones to its left.
        self.pack_end(self.delete_button, False, False, 0)
        self.pack_end(self.fullscreen_button, False, False, 0)
        self.set_number(1)
        self.set_maximized(False)

    @property
    def text(self) -> str:
        return self.label.get_text()

    def set_number(self, position: int) -> None:
        """Label the header `<shell><position>` (1-based)."""
        self.label.set_text(f"{self.shell_name}{position}")

    def set_active(self, active: bool) -> None:
        """The dock's current pane: its label switches to the normal text colour."""
        self._set_class("active", active)

    def set_focused(self, focused: bool) -> None:
        """Keyboard focus is in this pane's terminal: an active pane gets the accent underline."""
        self._set_class("focused", focused)

    def set_corner(self, corner: bool) -> None:
        """Reserve room on the right for the dock's "+" button."""
        self._set_class("corner", corner)

    def set_maximized(self, maximized: bool) -> None:
        if maximized:
            self.fullscreen_button.set_icon("view-restore-symbolic", "❐")
            self.fullscreen_button.set_tooltip_text("Restore")
        else:
            self.fullscreen_button.set_icon("view-fullscreen-symbolic", "□")
            self.fullscreen_button.set_tooltip_text("Fullscreen")

    def _set_class(self, name: str, on: bool) -> None:
        context = self.get_style_context()
        if on:
            context.add_class(name)
        else:
            context.remove_class(name)


class AddButton(_IconButton):
    """The dock's "+" button; marked `limit` when no more terminals fit."""

    def __init__(self, limit: int = 3):
        super().__init__("list-add-symbolic", "+", "New terminal")
        self._limit = limit
        self.get_style_context().add_class("idgent-terminal-add")
        self.set_halign(Gtk.Align.END)
        self.set_valign(Gtk.Align.START)

    def set_full(self, full: bool) -> None:
        context = self.get_style_context()
        if full:
            context.add_class("limit")
            self.set_tooltip_text(f"Cannot add terminal (limit {self._limit})")
        else:
            context.remove_class("limit")
            self.set_tooltip_text("New terminal")


class LimitToast(Gtk.Label):
    """Reference app-style notice that hides by itself; meant to live in a Gtk.Overlay.

    It slides in over ENTER_MS, stays SHOW_MS and fades out over FADE_MS
    (spec 4.9 / 3.2); with reduced motion it appears and disappears at once.
    """

    SHOW_MS = motion.DURATIONS["notice_autohide"]
    ENTER_MS = motion.DURATIONS["notice_enter"]
    FADE_MS = motion.DURATIONS["notice_exit"]
    # Rises this far into place; margins cannot go negative, so no slide from the right.
    SLIDE_PX = 8

    def __init__(self, limit: int = 3):
        super().__init__(label=f"Cannot add terminal (limit {limit})")
        self._timer: int | None = None
        self._destroyed = False
        self._leaving = False
        self.get_style_context().add_class("idgent-terminal-toast")
        self.set_halign(Gtk.Align.END)
        self.set_valign(Gtk.Align.START)
        self.set_no_show_all(True)
        self.connect("unmap", self._on_unmap)
        self.connect("destroy", self._on_destroy)

    def popup(self) -> None:
        """Show the toast; it hides after SHOW_MS (the timer restarts on repeats)."""
        if self._destroyed:
            return
        self._cancel()
        if self._leaving or not self.get_visible():
            self._leaving = False
            motion.slide_in(self, self.SLIDE_PX, self.ENTER_MS, "ease_in_out")
        self._timer = GLib.timeout_add(self.SHOW_MS, self._fade)

    def _fade(self) -> bool:
        self._timer = None
        if not self._destroyed:
            self._leaving = True
            motion.fade(self, False, self.FADE_MS, "ease_in_out", on_done=self._hidden)
        return GLib.SOURCE_REMOVE

    def _hidden(self) -> None:
        self._leaving = False
        if not self._destroyed:
            self.set_opacity(1.0)

    def _on_unmap(self, _widget: Gtk.Widget) -> None:
        # A notice of a hidden dock is stale: settle it hidden, margin restored.
        # Connected before any tween, so this runs before motion cancels on unmap.
        if self._timer is not None or self._leaving:
            self._cancel()
            self._leaving = True
            motion.fade(self, False, "none", on_done=self._hidden)

    def _cancel(self) -> None:
        if self._timer is not None:
            GLib.source_remove(self._timer)
            self._timer = None

    def _on_destroy(self, _widget: Gtk.Widget) -> None:
        self._destroyed = True
        self._cancel()


def build_css(ui: dict[str, str]) -> str:
    """Terminal dock rules: sidebar-tab-strip headers, clickable icons and a notice toast."""
    hover = f"alpha({ui['overlay']}, 0.067)"
    notice_fg = _legible(ui['warning'], _NOTICE_BG, 4.5)
    return f'''
.idgent-terminal {{ background: {ui['panel_bg']}; color: {ui['foreground']}; }}
.idgent-terminal-sep {{ background: {ui['border']}; min-width: 1px; min-height: 1px; border: 0; }}
.idgent-vte {{ padding: 8px; }}
.idgent-terminal-header {{ background: {ui['sidebar_bg']}; color: {ui['muted']}; border: 0;
    border-bottom: 1px solid {ui['border']}; box-shadow: none; min-height: 29px; padding: 0 4px 0 0; }}
.idgent-terminal-header.corner {{ padding-right: 34px; }}
.idgent-terminal-header.active {{ color: {ui['foreground']}; }}
.idgent-terminal-header button, .idgent-terminal-add {{ background: transparent; color: {ui['muted']};
    border: 0; border-radius: 4px; box-shadow: none; text-shadow: none; outline-offset: -1px;
    min-height: 22px; min-width: 24px; padding: 0; }}
.idgent-terminal-header button {{ margin: 3px 0 3px 2px; }}
.idgent-terminal-header button:hover {{ background: {hover}; color: {ui['foreground']}; box-shadow: none; }}
.idgent-terminal-header button.idgent-terminal-delete:hover {{ background: alpha({ui['danger']}, 0.15);
    color: {ui['danger']}; }}
.idgent-terminal-header .idgent-terminal-header-label {{ min-width: 0; margin: 0; padding: 0 12px;
    border-radius: 0; font-size: 13px; color: {ui['muted']}; }}
.idgent-terminal-header .idgent-terminal-header-label:hover {{ background: {hover};
    color: {ui['foreground']}; }}
.idgent-terminal-header.active .idgent-terminal-header-label {{ color: {ui['foreground']}; }}
.idgent-terminal-header.active.focused .idgent-terminal-header-label {{
    box-shadow: inset 0 -2px {ui['accent']}; }}
/* The "+" sits on the header strip, where the opaque `hover` role equals the overlay. */
.idgent-terminal-add {{ margin: 3px 4px 0 0; color: {ui['foreground']}; }}
.idgent-terminal-add:hover {{ background: {ui['hover']}; color: {ui['hover_fg']}; box-shadow: none; }}
.idgent-terminal-add.limit {{ color: {ui['limit_tint']}; }}
.idgent-terminal-add.limit:hover {{ color: {ui['warning']}; }}
.idgent-terminal-toast {{ background: alpha(#000000, 0.9); color: {notice_fg}; font-size: 13px;
    font-feature-settings: "tnum"; border: 0; border-radius: 8px; padding: 8px 12px;
    box-shadow: 0 1px 2px alpha(#000000, 0.121), 0 3.4px 6.7px alpha(#000000, 0.179); }}
'''
