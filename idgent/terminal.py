"""Tabbed VTE terminal panel, independent of its docking location."""
from __future__ import annotations

from dataclasses import dataclass
import os
import signal

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Vte", "2.91")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango, Vte


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


def tab_title(shell: str, n: int) -> str:
    return f"{os.path.basename(shell)} {n}"


@dataclass(eq=False)
class _Tab:
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


def _hangup(tab: _Tab) -> None:
    if tab.pid is not None:
        try:
            os.kill(tab.pid, signal.SIGHUP)
        except ProcessLookupError:
            pass
        tab.pid = None


class TerminalPanel(Gtk.Box):
    def __init__(self, cwd: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._cwd = cwd
        self._counter = 0
        self._destroyed = False
        self._tabs: list[_Tab] = []
        self.set_hexpand(True)
        self.set_vexpand(True)
        header = Gtk.Box(spacing=4)
        header.set_border_width(4)
        header.pack_start(Gtk.Label(label="TERMINAL"), False, False, 4)
        header.pack_end(self._button("×", "Close terminal (Ctrl+Shift+W)", self.close_current), False, False, 0)
        header.pack_end(self._button("+", "New terminal (Ctrl+Shift+T)", self.new_terminal), False, False, 0)
        self.pack_start(header, False, False, 0)
        self.notebook = Gtk.Notebook(scrollable=True, tab_pos=Gtk.PositionType.TOP)
        self.notebook.connect("switch-page", self._switch_page)
        self.pack_start(self.notebook, True, True, 0)
        self.connect("destroy", self._on_destroy)
        self.new_terminal()

    @staticmethod
    def _button(label: str, tooltip: str, action) -> Gtk.Button:
        button = Gtk.Button(label=label, relief=Gtk.ReliefStyle.NONE)
        button.set_tooltip_text(tooltip)
        button.connect("clicked", lambda _button: action())
        return button

    def set_cwd(self, path: str) -> None:
        self._cwd = path

    def new_terminal(self) -> None:
        if self._destroyed:
            return
        shell = pick_shell()
        self._counter += 1
        terminal = Vte.Terminal()
        self._configure(terminal)
        page = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        page.pack_start(terminal, True, True, 0)
        page.pack_end(Gtk.Scrollbar(orientation=Gtk.Orientation.VERTICAL,
                                   adjustment=terminal.get_vadjustment()), False, False, 0)
        tab = _Tab(page, terminal, Gio.Cancellable())
        self._tabs.append(tab)
        label = Gtk.Box(spacing=4)
        label.pack_start(Gtk.Label(label=tab_title(shell, self._counter)), False, False, 0)
        label.pack_start(self._button("×", "Close terminal", lambda: self._close_tab(tab)), False, False, 0)
        terminal.connect("child-exited", self._child_exited, tab)
        terminal.connect("key-press-event", self._key_press)
        terminal.connect("button-press-event", self._button_press)
        index = self.notebook.append_page(page, label)
        page.show_all()
        label.show_all()
        self.notebook.set_current_page(index)
        env = dict(os.environ, TERM="xterm-256color", COLORTERM="truecolor")
        terminal.spawn_async(
            Vte.PtyFlags.DEFAULT, safe_cwd(self._cwd), [shell, "-i"],
            [f"{key}={value}" for key, value in env.items()],
            GLib.SpawnFlags.DEFAULT, None, -1, tab.cancellable,
            self._spawned, tab,
        )
        GLib.idle_add(self._focus_idle)

    @staticmethod
    def _configure(terminal: Vte.Terminal) -> None:
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

    def _spawned(self, terminal: Vte.Terminal, pid: int, error: GLib.Error | None, tab: _Tab) -> None:
        if pid > 0:
            tab.pid = pid
        if tab.closed or self._destroyed:
            _hangup(tab)
        elif error is not None:
            terminal.feed(f"Unable to start shell: {error.message}\r\n".encode())

    def _child_exited(self, _terminal: Vte.Terminal, _status: int, tab: _Tab) -> None:
        tab.pid = None
        self._close_tab(tab)

    def close_current(self) -> None:
        tab = self._current_tab()
        if tab is not None:
            self._close_tab(tab)

    def _current_tab(self) -> _Tab | None:
        page = self.notebook.get_nth_page(self.notebook.get_current_page())
        return next((tab for tab in self._tabs if tab.page == page), None)

    def _close_tab(self, tab: _Tab) -> None:
        if tab.closed:
            return
        tab.closed = True
        tab.cancellable.cancel()
        _hangup(tab)
        self._tabs.remove(tab)
        self.notebook.remove_page(self.notebook.page_num(tab.page))
        tab.page.destroy()
        if not self._tabs and not self._destroyed:
            self.new_terminal()
        GLib.idle_add(self._focus_idle)

    def focus_terminal(self) -> None:
        if not self._destroyed:
            tab = self._current_tab()
            if tab is not None:
                tab.terminal.grab_focus()

    def _focus_idle(self) -> bool:
        self.focus_terminal()
        return GLib.SOURCE_REMOVE

    def _switch_page(self, _notebook: Gtk.Notebook, _page: Gtk.Widget, _number: int) -> None:
        GLib.idle_add(self._focus_idle)

    def _key_press(self, terminal: Vte.Terminal, event: Gdk.EventKey) -> bool:
        mask = Gtk.accelerator_get_default_mod_mask()
        required = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK
        if event.state & mask != required:
            return False
        key = Gdk.keyval_name(event.keyval).lower()
        actions = {"c": lambda: terminal.copy_clipboard_format(Vte.Format.TEXT),
                   "v": terminal.paste_clipboard, "t": self.new_terminal,
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
        menu.attach_to_widget(terminal, None)
        menu.connect("selection-done", lambda widget: widget.destroy())
        menu.show_all()
        menu.popup_at_pointer(event)
        return True

    def _on_destroy(self, _widget: Gtk.Widget) -> None:
        self._destroyed = True
        for tab in self._tabs:
            tab.closed = True
            tab.cancellable.cancel()
            _hangup(tab)
        self._tabs.clear()
