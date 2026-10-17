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
    __gsignals__ = {"last-closed": (GObject.SignalFlags.RUN_LAST, None, ())}

    def __init__(self, cwd: str, autostart: bool = True):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0,
                         homogeneous=True)
        self._cwd = cwd
        self._destroyed = False
        self._panes: list[_Pane] = []
        self._active: _Pane | None = None
        self.get_style_context().add_class("idgent-terminal")
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.connect("destroy", self._on_destroy)
        if autostart:
            self.new_terminal()

    def terminal_count(self) -> int:
        return len(self._panes)

    def set_cwd(self, path: str) -> None:
        self._cwd = path

    def new_terminal(self) -> None:
        if self._destroyed:
            return
        shell = pick_shell()
        terminal = Vte.Terminal()
        self._configure(terminal)
        page = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        page.pack_start(terminal, True, True, 0)
        page.pack_start(Gtk.Scrollbar(orientation=Gtk.Orientation.VERTICAL,
                                   adjustment=terminal.get_vadjustment()), False, False, 0)
        pane = _Pane(page, terminal, Gio.Cancellable())
        active = self._current_pane()
        index = self._panes.index(active) + 1 if active else len(self._panes)
        self._panes.insert(index, pane)
        self.pack_start(page, True, True, 0)
        self.reorder_child(page, index)
        divider = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        divider.set_size_request(1, -1)
        divider.get_style_context().add_class("idgent-terminal-sep")
        page.pack_start(divider, False, False, 0)
        terminal.connect("child-exited", self._child_exited, pane)
        terminal.connect("key-press-event", self._key_press)
        terminal.connect("button-press-event", self._button_press)
        terminal.connect("focus-in-event", self._focus_changed, pane, True)
        terminal.connect("focus-out-event", self._focus_changed, pane, False)
        self._active = pane
        page.show_all()
        self._update_dividers()
        env = dict(os.environ, TERM="xterm-256color", COLORTERM="truecolor")
        terminal.spawn_async(
            Vte.PtyFlags.DEFAULT, safe_cwd(self._cwd), [shell, "-i"],
            [f"{key}={value}" for key, value in env.items()],
            # Keywords avoid the hidden child_setup_data positional argument.
            GLib.SpawnFlags.DEFAULT, child_setup=None, timeout=-1,
            cancellable=pane.cancellable, callback=self._spawned, user_data=pane,
        )
        GLib.idle_add(self._focus_idle)

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
            self._active = pane
            context.add_class("focused")
        else:
            context.remove_class("focused")
        return False

    def _update_dividers(self):
        for index, pane in enumerate(self._panes):
            divider = pane.page.get_children()[-1]
            visible = index < len(self._panes) - 1
            divider.set_no_show_all(not visible)
            divider.set_visible(visible)

    def _close_pane(self, pane: _Pane) -> None:
        if pane.closed:
            return
        pane.closed = True
        pane.cancellable.cancel()
        _hangup(pane)
        index = self._panes.index(pane)
        self._panes.remove(pane)
        if self._active is pane:
            self._active = self._panes[min(index, len(self._panes) - 1)] if self._panes else None
        self.remove(pane.page)
        pane.page.destroy()
        self._update_dividers()
        if not self._panes and not self._destroyed:
            self.emit("last-closed")
        GLib.idle_add(self._focus_idle)

    def focus_terminal(self) -> None:
        if not self._destroyed:
            if self._active in self._panes:
                self._active.terminal.grab_focus()

    def _focus_idle(self) -> bool:
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
                self._active = self._panes[(index + (1 if key == "right" else -1)) % len(self._panes)]
                self.focus_terminal()
            return True
        if event.state & mask != required:
            return False
        key = Gdk.keyval_name(event.keyval).lower()
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
        for pane in self._panes:
            pane.closed = True
            pane.cancellable.cancel()
            _hangup(pane)
        self._panes.clear()
