"""VTE terminals with a header above each one, side by side or stacked."""
from __future__ import annotations

from dataclasses import dataclass
import os
import signal

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Vte", "2.91")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Pango, Vte

from idgent import motion
from idgent.resizegate import ResizeGate
from idgent.terminal_bar import AddButton, LimitToast, PaneHeader, cancel_fade, fade

MAX_TERMINALS = 3
SCROLLBACK_RANGE = (1000, 100000)
CURSOR_SHAPES = {
    "block": Vte.CursorShape.BLOCK,
    "ibeam": Vte.CursorShape.IBEAM,
    "underline": Vte.CursorShape.UNDERLINE,
}


def pick_shell() -> str:
    """Return the user's executable shell, or bash."""
    shell = os.environ.get("SHELL", "")
    return shell if os.path.isfile(shell) and os.access(shell, os.X_OK) else "/bin/bash"


def safe_cwd(path: str) -> str:
    """Choose an existing, searchable directory for a new child."""
    for candidate in (path, os.path.expanduser("~"), "/"):
        if os.path.isdir(candidate) and os.access(candidate, os.X_OK):
            return os.path.abspath(candidate)
    return "/"


@dataclass(eq=False)
class _Pane:
    # page = [inner, divider]; inner = [header, content]; content = [terminal, scrollbar, gutter].
    page: Gtk.Box
    terminal: Vte.Terminal
    cancellable: Gio.Cancellable
    header: PaneHeader
    content: Gtk.Box
    divider: Gtk.Separator
    frame: Gtk.Widget
    pid: int | None = None
    closed: bool = False
    # Closing panes are already out of TerminalPanel._panes but still on screen.
    closing: bool = False
    id: int = 0
    timer: int | None = None


_FOREGROUND = "#d4d4d4"
_BACKGROUND = "#1e1e1e"
_PALETTE = (
    "#000000", "#cd3131", "#0dbc79", "#e5e510",
    "#2472c8", "#bc3fbc", "#11a8cd", "#e5e5e5",
    "#666666", "#f14c4c", "#23d18b", "#f5f543",
    "#3b8eea", "#d670d6", "#29b8db", "#ffffff",
)


def _color(value: str) -> Gdk.RGBA:
    color = Gdk.RGBA()
    color.parse(value)
    return color


def _hangup(pane: _Pane) -> None:
    if pane.pid is not None:
        try:
            os.kill(pane.pid, signal.SIGHUP)
        except ProcessLookupError:
            pass
        pane.pid = None


class TerminalPanel(Gtk.Box):
    __gsignals__ = {
        "last-closed": (GObject.SignalFlags.RUN_LAST, None, ()),
        "limit-reached": (GObject.SignalFlags.RUN_LAST, None, ()),
        "maximize-toggled": (GObject.SignalFlags.RUN_LAST, None, (bool,)),
    }

    RESIZE_GATE = True
    # Milliseconds for pane enter/exit animations; 0 makes them synchronous.
    ANIMATION_MS = 220
    # Height of the header row; the "+" overlay and the limit toast are placed against it.
    HEADER_HEIGHT = 30

    def __init__(self, cwd: str, autostart: bool = True, stacked: bool = False,
                 font_size: int = 11):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.font_size = font_size
        self._cwd = cwd
        self.stacked = stacked
        self._destroyed = False
        self._panes: list[_Pane] = []
        self._closing: list[_Pane] = []
        self._active: _Pane | None = None
        self._maximized: _Pane | None = None
        self._next_id = 1
        self._palette = (_FOREGROUND, _BACKGROUND, list(_PALETTE), None)
        self._cursor_shape = "block"
        self._cursor_blink = True
        self._scrollback = 10000
        self.get_style_context().add_class("idgent-terminal")
        self.set_hexpand(True)
        self.set_vexpand(True)
        orientation = Gtk.Orientation.VERTICAL if stacked else Gtk.Orientation.HORIZONTAL
        self._body = Gtk.Box(orientation=orientation, spacing=0, homogeneous=True)
        self.overlay = Gtk.Overlay()
        self.overlay.add(self._body)
        self.add_button = AddButton(MAX_TERMINALS)
        self.add_button.connect("clicked", lambda _b: self.new_terminal())
        self.toast = LimitToast(MAX_TERMINALS)
        self.toast.set_margin_top(self.HEADER_HEIGHT + 6)
        self.toast.set_margin_end(10)
        for widget in (self.add_button, self.toast):
            self.overlay.add_overlay(widget)
        # The toast is informational; clicks go through to the terminal below it.
        self.overlay.set_overlay_pass_through(self.toast, True)
        self.pack_start(self.overlay, True, True, 0)
        self.overlay.show()
        self._body.show()
        self.add_button.show_all()
        self.connect("destroy", self._on_destroy)
        self.connect("map", self._commit_layout)
        self.connect("unmap", self._commit_layout)
        if autostart:
            self.new_terminal()

    def _commit_layout(self, *_args) -> None:
        """Commit discrete layout changes after GTK has allocated all panels.

        Map/unmap also covers View menu dock changes and fullscreen ancestors.
        The frame clock runs this after painting; divider motion never schedules this.
        """
        toplevel = self.get_toplevel()
        if not isinstance(toplevel, Gtk.Widget) or self._destroyed:
            return
        if getattr(toplevel, "_terminal_commit_tick", None) is not None:
            return

        def cancel_commit(widget):
            handler = widget._terminal_commit_tick
            if handler is not None and clock.handler_is_connected(handler):
                clock.disconnect(handler)
            widget._terminal_commit_tick = None
            if widget.handler_is_connected(destroy_handler):
                widget.disconnect(destroy_handler)

        def after_layout(clock):
            widget = toplevel
            cancel_commit(widget)

            def commit_descendants(child):
                if isinstance(child, ResizeGate) and child.get_mapped():
                    child.commit()
                elif isinstance(child, Gtk.Container):
                    for descendant in child.get_children():
                        commit_descendants(descendant)
            commit_descendants(widget)
            return GLib.SOURCE_REMOVE

        clock = toplevel.get_frame_clock()
        if clock is not None:
            toplevel._terminal_commit_tick = clock.connect("after-paint", after_layout)
            destroy_handler = toplevel.connect("destroy", cancel_commit)
            toplevel.queue_resize()

    def terminal_count(self) -> int:
        """Live terminals; panes playing their exit animation do not count."""
        return len(self._panes)

    def is_full(self) -> bool:
        return len(self._panes) >= MAX_TERMINALS

    def set_cwd(self, path: str) -> None:
        self._cwd = path

    def _animated(self) -> bool:
        return (self.ANIMATION_MS > 0 and self.get_mapped() and not self._destroyed
                and motion.animations_enabled())

    def header_labels(self) -> list[str]:
        """Labels of the live panes in pane order."""
        return [pane.header.text for pane in self._panes]

    def _set_active(self, pane: _Pane | None) -> None:
        self._active = pane
        for other in self._panes:
            other.header.set_active(other is pane)

    def _update_layout(self) -> None:
        """Renumber headers, move the "+" padding to the corner pane, fix dividers."""
        for position, pane in enumerate(self._panes, 1):
            pane.header.set_number(position)
        shown = [pane for pane in self._pages() if pane.frame.get_visible()]
        corner = self._maximized or (shown[0] if self.stacked else shown[-1]) if shown else None
        for pane in self._panes + self._closing:
            pane.header.set_corner(pane is corner)
            last = pane is shown[-1] if shown else True
            visible = not last and self._maximized is None
            pane.divider.set_no_show_all(not visible)
            pane.divider.set_visible(visible)
        self.add_button.set_full(self.is_full())

    def _pages(self) -> list[_Pane]:
        """Live and closing panes in on-screen order."""
        by_page = {pane.frame: pane for pane in self._panes + self._closing}
        return [by_page[child] for child in self._body.get_children() if child in by_page]

    def new_terminal(self) -> bool:
        """Add a terminal; False when the dock is full or destroyed."""
        if self._destroyed:
            return False
        if self.is_full():
            self.toast.popup()
            self.emit("limit-reached")
            return False
        if self._maximized is not None:
            self.restore()
        shell = pick_shell()
        terminal = Vte.Terminal()
        self._configure(terminal)
        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        content.pack_start(terminal, True, True, 0)
        adjustment = terminal.get_vadjustment()
        scrollbar = Gtk.Scrollbar(orientation=Gtk.Orientation.VERTICAL,
                                  adjustment=adjustment)
        # Keep the narrow gutter allocated so history never reflows terminal text.
        gutter = Gtk.Box()
        gutter.set_size_request(8, -1)
        gutter.set_no_show_all(True)
        # show_all() must not reveal a scrollbar for a screen without history.
        scrollbar.set_no_show_all(True)
        def update_scrollbar(adjustment):
            has_history = adjustment.get_upper() > adjustment.get_page_size()
            scrollbar.set_visible(has_history)
            gutter.set_visible(not has_history)
        # VTE updates its adjustment during allocation. Reconcile visibility in
        # an idle so GTK can finish that allocation before resizing the pane.
        refresh_pending = False
        def adjustment_changed(adjustment):
            nonlocal refresh_pending
            if refresh_pending:
                return
            refresh_pending = True
            def refresh():
                nonlocal refresh_pending
                refresh_pending = False
                if content.get_parent() is not None:
                    update_scrollbar(adjustment)
                    content.queue_resize()
                return GLib.SOURCE_REMOVE
            GLib.idle_add(refresh)
        adjustment.connect("changed", adjustment_changed)
        adjustment.connect("value-changed", adjustment_changed)
        update_scrollbar(adjustment)
        content.pack_start(scrollbar, False, False, 0)
        content.pack_start(gutter, False, False, 0)
        header = PaneHeader(os.path.basename(shell))
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        inner.pack_start(header, False, False, 0)
        inner.pack_start(content, True, True, 0)
        # Columns are divided by a vertical line, stacked rows by a horizontal one.
        divider = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL if self.stacked
                                else Gtk.Orientation.VERTICAL)
        divider.get_style_context().add_class("idgent-terminal-sep")
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL if self.stacked
                       else Gtk.Orientation.HORIZONTAL)
        page.pack_start(inner, True, True, 0)
        page.pack_start(divider, False, False, 0)
        frame = ResizeGate() if self.RESIZE_GATE else page
        if isinstance(frame, ResizeGate):
            frame.CHILD_MINIMUM = True
            frame.set_background(self._palette[1])
            frame.add(page)
        pane = _Pane(page, terminal, Gio.Cancellable(), header, content, divider, frame,
                     id=self._next_id)
        self._next_id += 1
        header.connect("focus-requested", lambda _h: self._header_focus(pane))
        header.connect("fullscreen-requested", lambda _h: self.toggle_maximize(pane))
        header.connect("delete-requested", lambda _h: self._close_pane(pane))
        animated = self._animated()
        active = self._current_pane()
        index = self._panes.index(active) + 1 if active else len(self._panes)
        children = self._body.get_children()
        position = children.index(self._panes[index].frame) if index < len(self._panes) else len(children)
        self._panes.insert(index, pane)
        self._body.pack_start(frame, True, True, 0)
        self._body.reorder_child(frame, position)
        terminal.connect("child-exited", self._child_exited, pane)
        terminal.connect("key-press-event", self._key_press)
        terminal.connect("button-press-event", self._button_press)
        terminal.connect("focus-in-event", self._focus_changed, pane, True)
        terminal.connect("focus-out-event", self._focus_changed, pane, False)
        self._set_active(pane)
        if animated:
            frame.set_opacity(0.0)
        frame.show_all()
        self._update_layout()
        self._commit_layout()
        if animated:
            fade(frame, 0.0, 1.0, self.ANIMATION_MS)
        env = dict(os.environ, TERM="xterm-256color", COLORTERM="truecolor")
        terminal.spawn_async(
            Vte.PtyFlags.DEFAULT, safe_cwd(self._cwd), [shell, "-i"],
            [f"{key}={value}" for key, value in env.items()],
            # Keywords avoid the hidden child_setup_data positional argument.
            GLib.SpawnFlags.DEFAULT, child_setup=None, timeout=-1,
            cancellable=pane.cancellable, callback=self._spawned, user_data=pane,
        )
        GLib.idle_add(self._focus_idle, pane)
        return True

    def set_font_size(self, size: int) -> None:
        """Set the base font for existing and future panes, retaining zoom."""
        self.font_size = size
        for pane in self._panes + self._closing:
            terminal = pane.terminal
            # Font metrics change the PTY grid even at the same pixel allocation.
            # Use the gate's non-reflow policy for this discrete layout change too.
            rewrap = terminal.get_property("rewrap-on-resize")
            gated = isinstance(pane.frame, ResizeGate) and not pane.frame.REFLOW_VTE
            if gated:
                terminal.set_property("rewrap-on-resize", False)
            try:
                terminal.set_font(Pango.FontDescription(f"Monospace {size}"))
            finally:
                if gated:
                    terminal.set_property("rewrap-on-resize", rewrap)
            pane.frame.queue_resize()
        self._commit_layout()

    def apply_palette(self, foreground: str, background: str, ansi: list[str],
                      cursor: str | None = None) -> None:
        """Use these colours for every terminal, existing and future."""
        self._palette = (foreground, background, list(ansi), cursor)
        for pane in self._panes + self._closing:
            self._apply_colors(pane.terminal)
            if isinstance(pane.frame, ResizeGate):
                pane.frame.set_background(background)

    def set_cursor_shape(self, shape: str) -> None:
        """Use 'block', 'ibeam' or 'underline' cursors in existing and future panes."""
        if shape not in CURSOR_SHAPES:
            raise ValueError(f"unknown cursor shape: {shape!r}")
        self._cursor_shape = shape
        for pane in self._panes + self._closing:
            pane.terminal.set_cursor_shape(CURSOR_SHAPES[shape])

    def set_cursor_blink(self, blink: bool) -> None:
        """Make the cursor of existing and future panes blink, or keep it steady."""
        self._cursor_blink = bool(blink)
        for pane in self._panes + self._closing:
            self._apply_blink(pane.terminal)

    def set_scrollback(self, lines: int) -> None:
        """Keep `lines` of history (clamped to SCROLLBACK_RANGE) in existing and future panes."""
        low, high = SCROLLBACK_RANGE
        self._scrollback = max(low, min(high, int(lines)))
        for pane in self._panes + self._closing:
            pane.terminal.set_scrollback_lines(self._scrollback)

    def _apply_blink(self, terminal: Vte.Terminal) -> None:
        terminal.set_cursor_blink_mode(Vte.CursorBlinkMode.ON if self._cursor_blink
                                       else Vte.CursorBlinkMode.OFF)

    def _apply_colors(self, terminal: Vte.Terminal) -> None:
        foreground, background, ansi, cursor = self._palette
        terminal.set_colors(_color(foreground), _color(background), [_color(c) for c in ansi])
        terminal.set_color_cursor(_color(cursor) if cursor else None)

    def _configure(self, terminal: Vte.Terminal) -> None:
        terminal.get_style_context().add_class("idgent-vte")
        terminal.set_margin_start(8)
        terminal.set_margin_end(8)
        terminal.set_margin_top(8)
        terminal.set_margin_bottom(8)
        terminal.set_font(Pango.FontDescription(f"Monospace {self.font_size}"))
        self._apply_colors(terminal)
        terminal.set_cursor_shape(CURSOR_SHAPES[self._cursor_shape])
        self._apply_blink(terminal)
        terminal.set_scrollback_lines(self._scrollback)
        terminal.set_scroll_on_output(False)
        terminal.set_scroll_on_keystroke(True)
        terminal.set_audible_bell(False)
        terminal.set_mouse_autohide(True)
        terminal.set_hexpand(True)
        terminal.set_vexpand(True)
        terminal.set_size(20, 4)

    def _spawned(self, terminal: Vte.Terminal, pid: int, error: GLib.Error | None, pane: _Pane) -> None:
        if pid > 0:
            pane.pid = pid
        if pane.closed or self._destroyed:
            _hangup(pane)
        elif error is not None:
            terminal.feed(f"Unable to start shell: {error.message}\r\n".encode())

    def _child_exited(self, _terminal: Vte.Terminal, _status: int, pane: _Pane) -> None:
        pane.pid = None
        self._close_pane(pane)

    def close_current(self) -> None:
        pane = self._current_pane()
        if pane is not None:
            self._close_pane(pane)

    def _current_pane(self) -> _Pane | None:
        # get_focus also works before the toplevel receives focus-in events.
        toplevel = self.get_toplevel()
        focus = toplevel.get_focus() if isinstance(toplevel, Gtk.Window) else None
        return next((pane for pane in self._panes if pane.terminal is focus),
                    self._active if self._active in self._panes else None)

    def _focus_changed(self, terminal, _event, pane, focused):
        context = terminal.get_style_context()
        pane.header.set_focused(focused)
        if focused:
            if not pane.closed:
                self._set_active(pane)
            context.add_class("focused")
        else:
            context.remove_class("focused")
        return False

    def _close_pane(self, pane: _Pane) -> None:
        if pane.closed:
            return
        if pane is self._maximized:
            self.restore()
        refocus = pane.terminal.is_focus()
        self._set_active(self._current_pane())
        pane.closed = True
        pane.cancellable.cancel()
        _hangup(pane)
        index = self._panes.index(pane)
        self._panes.remove(pane)
        if self._active is pane:
            self._set_active(self._panes[min(index, len(self._panes) - 1)] if self._panes else None)
        if self._animated():
            pane.closing = True
            self._closing.append(pane)
            self._update_layout()
            fade(pane.frame, 1.0, 0.0, self.ANIMATION_MS)
            # A timeout rather than the revealer signal: it also fires if frames stall.
            pane.timer = GLib.timeout_add(self.ANIMATION_MS + 30, self._finish_close, pane)
        else:
            self._finish_close(pane)
        if refocus and self.get_visible():
            GLib.idle_add(self._focus_idle)

    def _finish_close(self, pane: _Pane) -> bool:
        pane.timer = None
        if pane in self._closing:
            self._closing.remove(pane)
        if self._destroyed:
            return GLib.SOURCE_REMOVE
        cancel_fade(pane.frame)
        self._body.remove(pane.frame)
        pane.frame.destroy()
        self._update_layout()
        self._commit_layout()
        if not self._panes and not self._closing:
            self.emit("last-closed")
        return GLib.SOURCE_REMOVE

    def _header_focus(self, pane: _Pane) -> None:
        if pane.closed:
            return
        if self._maximized is not None and pane is not self._maximized:
            self.toggle_maximize(pane)
        self._set_active(pane)
        self.focus_terminal()

    def is_maximized(self) -> bool:
        return self._maximized is not None

    def toggle_maximize(self, pane: _Pane | None = None) -> None:
        """Maximize `pane` (default: the active one) inside this panel; again to restore."""
        if self._destroyed:
            return
        pane = pane if pane is not None else self._current_pane()
        if pane is None or pane.closed:
            return
        if pane is self._maximized:
            self.restore()
            return
        was_normal = self._maximized is None
        self._maximized = pane
        for other in self._panes + self._closing:
            if other is not pane:
                other.frame.set_no_show_all(True)
                other.frame.hide()
        pane.frame.set_no_show_all(False)
        pane.frame.show()
        self._update_layout()
        for other in self._panes:
            other.header.set_maximized(other is pane)
        self._set_active(pane)
        self.focus_terminal()
        self._commit_layout()
        if was_normal:
            self.emit("maximize-toggled", True)

    def restore(self) -> None:
        """Show all panes again after a maximize."""
        if self._maximized is None:
            return
        self._maximized = None
        if self._destroyed:
            return
        for pane in self._panes:
            pane.frame.set_no_show_all(False)
            pane.frame.show()
        self._update_layout()
        for pane in self._panes:
            pane.header.set_maximized(False)
        self.focus_terminal()
        self.emit("maximize-toggled", False)
        self._commit_layout()

    def focus_terminal(self) -> None:
        if not self._destroyed and self._active in self._panes:
            self._active.terminal.grab_focus()

    def _focus_idle(self, pane: _Pane | None = None) -> bool:
        if pane is not None:
            if pane not in self._panes:
                return GLib.SOURCE_REMOVE
            self._set_active(pane)
        self.focus_terminal()
        return GLib.SOURCE_REMOVE

    def _key_press(self, terminal: Vte.Terminal, event: Gdk.EventKey) -> bool:
        mask = Gtk.accelerator_get_default_mod_mask()
        required = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK
        key = Gdk.keyval_name(event.keyval).lower()
        if (event.state & mask == Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK
                and key in (("left", "right", "up", "down") if self.stacked else ("left", "right"))):
            pane = next((p for p in self._panes if p.terminal is terminal), None)
            if pane:
                index = self._panes.index(pane)
                step = 1 if key in ("right", "down") else -1
                self._set_active(self._panes[(index + step) % len(self._panes)])
                if self._maximized not in (None, self._active):
                    self.toggle_maximize(self._active)
                self.focus_terminal()
            return True
        if event.state & mask != required:
            return False
        actions = {"c": lambda: terminal.copy_clipboard_format(Vte.Format.TEXT),
                   "v": terminal.paste_clipboard, "t": self.new_terminal,
                   "5": self.new_terminal, "percent": self.new_terminal,
                   "w": self.close_current}
        if key in actions:
            actions[key]()
        elif key in ("plus", "equal", "minus", "underscore", "0", "parenright", "kp_add", "kp_subtract", "kp_0"):
            scale = terminal.get_font_scale()
            if key in ("0", "parenright", "kp_0"):
                scale = 1.0
            else:
                scale += 0.1 if key in ("plus", "equal", "kp_add") else -0.1
            terminal.set_font_scale(max(0.5, min(3.0, scale)))
            self._commit_layout()
        else:
            return False
        return True

    def _button_press(self, terminal: Vte.Terminal, event: Gdk.EventButton) -> bool:
        if event.button != 3:
            return False
        terminal.grab_focus()
        menu = Gtk.Menu()
        actions = (("Copy", lambda: terminal.copy_clipboard_format(Vte.Format.TEXT)),
                   ("Paste", terminal.paste_clipboard), ("Select All", terminal.select_all),
                   ("Clear", lambda: terminal.reset(True, True)))
        for label, action in actions:
            item = Gtk.MenuItem(label=label)
            item.connect("activate", lambda _item, callback=action: callback())
            if label == "Copy":
                item.set_sensitive(terminal.get_has_selection())
            menu.append(item)
        menu.append(Gtk.SeparatorMenuItem())
        for label, action in (("Split Terminal", self.new_terminal),
                              ("Kill Terminal", self.close_current)):
            item = Gtk.MenuItem(label=label)
            item.connect("activate", lambda _item, callback=action: callback())
            menu.append(item)
        menu.attach_to_widget(terminal, None)
        menu.connect("selection-done", lambda widget: widget.destroy())
        menu.show_all()
        menu.popup_at_pointer(event)
        return True

    def _on_destroy(self, _widget: Gtk.Widget) -> None:
        self._destroyed = True
        for pane in self._panes + self._closing:
            pane.closed = True
            pane.cancellable.cancel()
            _hangup(pane)
            if pane.timer is not None:
                GLib.source_remove(pane.timer)
                pane.timer = None
        self._panes.clear()
        self._closing.clear()
        self._maximized = None
