"""Top bar of a terminal dock: one chip per terminal, a "+" button and a limit notice."""
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
        # Clicking the bar must not steal keyboard focus from the terminal.
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


class _Chip:
    """Label, fullscreen and delete controls of one terminal."""

    def __init__(self, pane_id: int, number: int, text: str):
        self.id = pane_id
        self.number = number
        self.closing = False
        self.revealer = Gtk.Revealer()
        self.revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_RIGHT)
        self.box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.box.get_style_context().add_class("idgent-terminal-chip")
        self.label = Gtk.Label(label=text)
        # Ellipsizing keeps chips shrinkable in a narrow dock.
        self.label.set_ellipsize(Pango.EllipsizeMode.END)
        self.label_button = Gtk.Button()
        self.label_button.set_relief(Gtk.ReliefStyle.NONE)
        self.label_button.set_focus_on_click(False)
        self.label_button.get_style_context().add_class("idgent-terminal-chip-label")
        self.label_button.add(self.label)
        self.fullscreen_button = _IconButton("view-fullscreen-symbolic", "□", "Fullscreen")
        self.delete_button = _IconButton("edit-delete-symbolic", "✕", "Kill terminal")
        for widget in (self.label_button, self.fullscreen_button, self.delete_button):
            self.box.pack_start(widget, False, False, 0)
        self.revealer.add(self.box)
        self.revealer.show_all()

    @property
    def text(self) -> str:
        return self.label.get_text()


class TerminalBar(Gtk.Box):
    __gsignals__ = {
        "add-requested": (GObject.SignalFlags.RUN_LAST, None, ()),
        "focus-requested": (GObject.SignalFlags.RUN_LAST, None, (int,)),
        "fullscreen-requested": (GObject.SignalFlags.RUN_LAST, None, (int,)),
        "delete-requested": (GObject.SignalFlags.RUN_LAST, None, (int,)),
    }

    NOTICE_MS = 2500
    NOTICE_FADE_MS = 300

    def __init__(self, limit: int = 3):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._limit = limit
        self._destroyed = False
        self._chips: list[_Chip] = []
        self._closing: list[_Chip] = []
        self._timers: set[int] = set()
        self._notice_timer: int | None = None
        self._maximized: int | None = None
        self.get_style_context().add_class("idgent-terminal-bar")
        self.set_hexpand(True)
        self._chip_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.pack_start(self._chip_box, False, True, 0)
        self.add_button = _IconButton("list-add-symbolic", "+", "New terminal")
        self.add_button.get_style_context().add_class("idgent-terminal-add")
        self.add_button.connect("clicked", lambda _b: self.emit("add-requested"))
        # pack_end puts the first widget at the far right and later ones to its left.
        self.pack_end(self.add_button, False, False, 0)
        self.notice = Gtk.Label(label=self._notice_text())
        self.notice.get_style_context().add_class("idgent-terminal-notice")
        self.notice.set_no_show_all(True)
        self.pack_end(self.notice, False, False, 0)
        self.connect("destroy", self._on_destroy)

    def _notice_text(self) -> str:
        return f"Cannot add terminal (limit {self._limit})"

    # -- chips ---------------------------------------------------------------

    def chip_labels(self) -> list[str]:
        """Labels of the live chips in pane order."""
        return [chip.text for chip in self._chips]

    def chip(self, pane_id: int) -> _Chip | None:
        return next((chip for chip in self._chips if chip.id == pane_id), None)

    def add_chip(self, pane_id: int, index: int | None = None, shell_name: str = "bash",
                 duration: int = 0) -> str:
        """Add a chip at live position `index` (default: end); returns its label."""
        used = {chip.number for chip in self._chips}
        number = next(n for n in range(1, len(used) + 2) if n not in used)
        chip = _Chip(pane_id, number, f"{shell_name}{number}")
        chip.label_button.connect("clicked", lambda _b: self.emit("focus-requested", pane_id))
        chip.fullscreen_button.connect("clicked", lambda _b: self.emit("fullscreen-requested", pane_id))
        chip.delete_button.connect("clicked", lambda _b: self.emit("delete-requested", pane_id))
        index = len(self._chips) if index is None else max(0, min(index, len(self._chips)))
        position = (self._chip_box.get_children().index(self._chips[index].revealer)
                    if index < len(self._chips) else len(self._chip_box.get_children()))
        self._chips.insert(index, chip)
        animated = duration > 0 and self.get_mapped()
        chip.revealer.set_transition_duration(duration if animated else 0)
        chip.revealer.set_reveal_child(not animated)
        self._chip_box.pack_start(chip.revealer, False, True, 0)
        self._chip_box.reorder_child(chip.revealer, position)
        if animated:
            fade(chip.revealer, 0.0, 1.0, duration)
            chip.revealer.set_reveal_child(True)
        self._apply_maximized(chip)
        return chip.text

    def remove_chip(self, pane_id: int, duration: int = 0) -> None:
        chip = self.chip(pane_id)
        if chip is None:
            return
        self._chips.remove(chip)
        if self._maximized == pane_id:
            self._maximized = None
        if duration > 0 and self.get_mapped() and not self._destroyed:
            chip.closing = True
            self._closing.append(chip)
            chip.revealer.set_transition_duration(duration)
            chip.revealer.set_reveal_child(False)
            fade(chip.revealer, 1.0, 0.0, duration)
            self._later(duration + 30, self._finish_remove, chip)
        else:
            self._finish_remove(chip)

    def _finish_remove(self, chip: _Chip) -> None:
        if chip in self._closing:
            self._closing.remove(chip)
        if not self._destroyed:
            self._chip_box.remove(chip.revealer)
            chip.revealer.destroy()

    def set_active(self, pane_id: int | None) -> None:
        for chip in self._chips:
            context = chip.box.get_style_context()
            if chip.id == pane_id:
                context.add_class("active")
            else:
                context.remove_class("active")

    def set_maximized(self, pane_id: int | None) -> None:
        self._maximized = pane_id
        for chip in self._chips:
            self._apply_maximized(chip)

    def _apply_maximized(self, chip: _Chip) -> None:
        if chip.id == self._maximized:
            chip.fullscreen_button.set_icon("view-restore-symbolic", "❐")
            chip.fullscreen_button.set_tooltip_text("Restore")
        else:
            chip.fullscreen_button.set_icon("view-fullscreen-symbolic", "□")
            chip.fullscreen_button.set_tooltip_text("Fullscreen")

    # -- limit ---------------------------------------------------------------

    def set_full(self, full: bool) -> None:
        context = self.add_button.get_style_context()
        if full:
            context.add_class("limit")
            self.add_button.set_tooltip_text(f"Cannot add terminal (limit {self._limit})")
        else:
            context.remove_class("limit")
            self.add_button.set_tooltip_text("New terminal")

    def show_limit_notice(self) -> None:
        """Show the notice; it fades out after NOTICE_MS (timer restarts on repeats)."""
        if self._destroyed:
            return
        self._cancel_notice()
        cancel_fade(self.notice)
        self.notice.set_opacity(1.0)
        self.notice.show()
        self._notice_timer = GLib.timeout_add(self.NOTICE_MS, self._fade_notice)

    def _fade_notice(self) -> bool:
        self._notice_timer = None
        if not self._destroyed:
            fade(self.notice, 1.0, 0.0, self.NOTICE_FADE_MS, self._hide_notice)
        return GLib.SOURCE_REMOVE

    def _hide_notice(self) -> None:
        if not self._destroyed:
            self.notice.hide()
            self.notice.set_opacity(1.0)

    def _cancel_notice(self) -> None:
        if self._notice_timer is not None:
            GLib.source_remove(self._notice_timer)
            self._notice_timer = None

    # -- plumbing ------------------------------------------------------------

    def _later(self, delay_ms: int, callback, *args) -> None:
        def run():
            self._timers.discard(source)
            if not self._destroyed:
                callback(*args)
            return GLib.SOURCE_REMOVE
        source = GLib.timeout_add(delay_ms, run)
        self._timers.add(source)

    def _on_destroy(self, _widget: Gtk.Widget) -> None:
        self._destroyed = True
        self._cancel_notice()
        for source in self._timers:
            GLib.source_remove(source)
        self._timers.clear()
        self._chips.clear()
        self._closing.clear()
