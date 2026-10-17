"""Widgets of a terminal dock: per-pane header, "+" button and limit toast."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, GObject, Gtk, Pango


def cancel_fade(widget: Gtk.Widget) -> None:
    """Stop a running `fade()` on `widget`, leaving its opacity as it is."""
    tick = getattr(widget, "_fade_tick", None)
    if tick is not None:
        widget._fade_tick = None
        widget.remove_tick_callback(tick)


def fade(widget: Gtk.Widget, start: float, end: float, duration_ms: int, done=None) -> None:
    """Animate `widget` opacity from `start` to `end`; jump there when it cannot animate."""
    cancel_fade(widget)
    widget.set_opacity(start)
    if duration_ms <= 0 or not widget.get_mapped():
        widget.set_opacity(end)
        if done is not None:
            done()
        return
    origin = None

    def tick(target, clock):
        nonlocal origin
        now = clock.get_frame_time()
        origin = now if origin is None else origin
        progress = min(1.0, (now - origin) / (duration_ms * 1000))
        target.set_opacity(start + (end - start) * progress)
        if progress < 1.0:
            return GLib.SOURCE_CONTINUE
        target._fade_tick = None
        if done is not None:
            done()
        return GLib.SOURCE_REMOVE

    widget._fade_tick = widget.add_tick_callback(tick)


class _IconButton(Gtk.Button):
    """Flat icon button that falls back to a text glyph when the theme lacks the icon."""

    def __init__(self, icon_name: str, glyph: str, tooltip: str):
        super().__init__()
        self.set_relief(Gtk.ReliefStyle.NONE)
        # Clicking a header must not steal keyboard focus from the terminal.
        self.set_focus_on_click(False)
        self.set_tooltip_text(tooltip)
        self.icon_name = icon_name
        self.set_icon(icon_name, glyph)

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
        self._set_class("active", active)

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
    """Small amber notice that fades out by itself; meant to live in a Gtk.Overlay."""

    SHOW_MS = 2500
    FADE_MS = 300

    def __init__(self, limit: int = 3):
        super().__init__(label=f"Cannot add terminal (limit {limit})")
        self._timer: int | None = None
        self._destroyed = False
        self.get_style_context().add_class("idgent-terminal-toast")
        self.set_halign(Gtk.Align.END)
        self.set_valign(Gtk.Align.START)
        self.set_no_show_all(True)
        self.connect("destroy", self._on_destroy)

    def popup(self) -> None:
        """Show the toast; it fades after SHOW_MS (the timer restarts on repeats)."""
        if self._destroyed:
            return
        self._cancel()
        cancel_fade(self)
        self.set_opacity(1.0)
        self.show()
        self._timer = GLib.timeout_add(self.SHOW_MS, self._fade)

    def _fade(self) -> bool:
        self._timer = None
        if not self._destroyed:
            fade(self, 1.0, 0.0, self.FADE_MS, self._hide)
        return GLib.SOURCE_REMOVE

    def _hide(self) -> None:
        if not self._destroyed:
            self.hide()
            self.set_opacity(1.0)

    def _cancel(self) -> None:
        if self._timer is not None:
            GLib.source_remove(self._timer)
            self._timer = None

    def _on_destroy(self, _widget: Gtk.Widget) -> None:
        self._destroyed = True
        self._cancel()
