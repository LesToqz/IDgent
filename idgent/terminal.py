"""Side-by-side VTE terminals, independent of their docking location."""
from __future__ import annotations

from dataclasses import dataclass
import os
import signal

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Vte", "2.91")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Pango, Vte

from idgent.terminal_bar import TerminalBar, cancel_fade, fade

MAX_TERMINALS = 3


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
    page: Gtk.Box
    terminal: Vte.Terminal
    cancellable: Gio.Cancellable
    pid: int | None = None
    closed: bool = False
    # Closing panes are already out of TerminalPanel._panes but still on screen.
    closing: bool = False
    id: int = 0
    slot: Gtk.Revealer | None = None
    timer: int | None = None


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

    # Milliseconds for pane and chip enter/exit animations; 0 makes them synchronous.
    ANIMATION_MS = 220

    def __init__(self, cwd: str, autostart: bool = True):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._cwd = cwd
        self._destroyed = False
        self._panes: list[_Pane] = []
        self._closing: list[_Pane] = []
        self._active: _Pane | None = None
        self._maximized: _Pane | None = None
        self._next_id = 1
        self.get_style_context().add_class("idgent-terminal")
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.bar = TerminalBar(MAX_TERMINALS)
        self.bar.connect("add-requested", lambda _bar: self.new_terminal())
        self.bar.connect("focus-requested", self._chip_focus)
        self.bar.connect("fullscreen-requested", lambda _bar, pane_id: self.toggle_maximize(
            self._pane_by_id(pane_id)))
        self.bar.connect("delete-requested", lambda _bar, pane_id: self._chip_delete(pane_id))
        self.pack_start(self.bar, False, False, 0)
        self._body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0, homogeneous=True)
        self.pack_start(self._body, True, True, 0)
        self.bar.show_all()
        self._body.show()
        self.connect("destroy", self._on_destroy)
        if autostart:
            self.new_terminal()

    def terminal_count(self) -> int:
        """Live terminals; panes playing their exit animation do not count."""
        return len(self._panes)

    def is_full(self) -> bool:
        return len(self._panes) >= MAX_TERMINALS

    def set_cwd(self, path: str) -> None:
        self._cwd = path

    def _animated(self) -> bool:
        return self.ANIMATION_MS > 0 and self.get_mapped() and not self._destroyed

    def _pane_by_id(self, pane_id: int) -> _Pane | None:
        return next((pane for pane in self._panes if pane.id == pane_id), None)

    def _set_active(self, pane: _Pane | None) -> None:
        self._active = pane
        self.bar.set_active(pane.id if pane is not None else None)

    def _sync_bar(self) -> None:
        self.bar.set_full(self.is_full())

    def new_terminal(self) -> bool:
        """Add a terminal; False when the dock is full or destroyed."""
        if self._destroyed:
            return False
        if self.is_full():
            self.bar.show_limit_notice()
            self.emit("limit-reached")
            return False
        if self._maximized is not None:
            self.restore()
        shell = pick_shell()
        terminal = Vte.Terminal()
        self._configure(terminal)
        page = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        page.pack_start(terminal, True, True, 0)
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
                if page.get_parent() is not None:
                    update_scrollbar(adjustment)
                    page.queue_resize()
                return GLib.SOURCE_REMOVE
            GLib.idle_add(refresh)
        adjustment.connect("changed", adjustment_changed)
        adjustment.connect("value-changed", adjustment_changed)
        update_scrollbar(adjustment)
        page.pack_start(scrollbar, False, False, 0)
        page.pack_start(gutter, False, False, 0)
        pane = _Pane(page, terminal, Gio.Cancellable(), id=self._next_id)
        self._next_id += 1
        animated = self._animated()
        slot = Gtk.Revealer()
        slot.set_transition_type(Gtk.RevealerTransitionType.SLIDE_RIGHT)
        slot.set_transition_duration(self.ANIMATION_MS if animated else 0)
        slot.set_reveal_child(not animated)
        slot.add(page)
        pane.slot = slot
        active = self._current_pane()
        index = self._panes.index(active) + 1 if active else len(self._panes)
        slots = self._body.get_children()
        position = slots.index(self._panes[index].slot) if index < len(self._panes) else len(slots)
        self._panes.insert(index, pane)
        self._body.pack_start(slot, True, True, 0)
        self._body.reorder_child(slot, position)
        divider = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        divider.set_size_request(1, -1)
        divider.get_style_context().add_class("idgent-terminal-sep")
        page.pack_start(divider, False, False, 0)
        terminal.connect("child-exited", self._child_exited, pane)
        terminal.connect("key-press-event", self._key_press)
        terminal.connect("button-press-event", self._button_press)
        terminal.connect("focus-in-event", self._focus_changed, pane, True)
        terminal.connect("focus-out-event", self._focus_changed, pane, False)
        self._set_active(pane)
        page.show_all()
        slot.show()
        self._update_dividers()
        self.bar.add_chip(pane.id, index, os.path.basename(shell),
                          self.ANIMATION_MS if animated else 0)
        self._sync_bar()
        if animated:
            fade(slot, 0.0, 1.0, self.ANIMATION_MS)
            slot.set_reveal_child(True)
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

    @staticmethod
    def _configure(terminal: Vte.Terminal) -> None:
        terminal.get_style_context().add_class("idgent-vte")
        terminal.set_margin_start(8)
        terminal.set_margin_end(8)
        terminal.set_margin_top(8)
        terminal.set_margin_bottom(8)
        terminal.set_font(Pango.FontDescription("Monospace 11"))
        terminal.set_colors(_color("#d4d4d4"), _color("#1e1e1e"), [_color(c) for c in _PALETTE])
        terminal.set_cursor_blink_mode(Vte.CursorBlinkMode.ON)
        terminal.set_scrollback_lines(10000)
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
        if focused:
            if not pane.closed:
                self._set_active(pane)
            context.add_class("focused")
        else:
            context.remove_class("focused")
        return False

    def _update_dividers(self):
        for index, pane in enumerate(self._panes):
            divider = pane.page.get_children()[-1]
            visible = index < len(self._panes) - 1 and self._maximized is None
            divider.set_no_show_all(not visible)
            divider.set_visible(visible)

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
        self._update_dividers()
        self._sync_bar()
        if self._animated():
            pane.closing = True
            self._closing.append(pane)
            self.bar.remove_chip(pane.id, self.ANIMATION_MS)
            pane.slot.set_transition_duration(self.ANIMATION_MS)
            pane.slot.set_reveal_child(False)
            fade(pane.slot, 1.0, 0.0, self.ANIMATION_MS)
            # A timeout rather than the revealer signal: it also fires if frames stall.
            pane.timer = GLib.timeout_add(self.ANIMATION_MS + 30, self._finish_close, pane)
        else:
            self.bar.remove_chip(pane.id)
            self._finish_close(pane)
        if refocus and self.get_visible():
            GLib.idle_add(self._focus_idle)

    def _finish_close(self, pane: _Pane) -> bool:
        pane.timer = None
        if pane in self._closing:
            self._closing.remove(pane)
        if self._destroyed:
            return GLib.SOURCE_REMOVE
        cancel_fade(pane.slot)
        self._body.remove(pane.slot)
        pane.slot.destroy()
        if not self._panes and not self._closing:
            self.emit("last-closed")
        return GLib.SOURCE_REMOVE

    def _chip_focus(self, _bar: TerminalBar, pane_id: int) -> None:
        pane = self._pane_by_id(pane_id)
        if pane is None:
            return
        if self._maximized is not None and pane is not self._maximized:
            self.toggle_maximize(pane)
        self._set_active(pane)
        self.focus_terminal()

    def _chip_delete(self, pane_id: int) -> None:
        pane = self._pane_by_id(pane_id)
        if pane is not None:
            self._close_pane(pane)

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
                other.slot.set_no_show_all(True)
                other.slot.hide()
        pane.slot.set_no_show_all(False)
        pane.slot.show()
        self._update_dividers()
        self.bar.set_maximized(pane.id)
        self._set_active(pane)
        self.focus_terminal()
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
            pane.slot.set_no_show_all(False)
            pane.slot.show()
        self._update_dividers()
        self.bar.set_maximized(None)
        self.focus_terminal()
        self.emit("maximize-toggled", False)

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
                and key in ("left", "right")):
            pane = next((p for p in self._panes if p.terminal is terminal), None)
            if pane:
                index = self._panes.index(pane)
                self._set_active(self._panes[(index + (1 if key == "right" else -1)) % len(self._panes)])
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
