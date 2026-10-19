"""Real UTF-8 bash startup, without postponing spawn in the test harness."""
from __future__ import annotations

import locale
import os
from pathlib import Path
import shlex
import tempfile
import time
import unittest
from unittest.mock import patch

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Vte", "2.91")
from gi.repository import Gdk, Gio, GLib, Gtk, Vte

from idgent import settings, style
from idgent.terminal import TerminalPanel
from idgent.window import MainWindow
from tests.test_resizegate import poll, spin, setUpModule  # noqa: F401
from tests.test_terminal_resize import PS1, PROMPT, DockWindow


def utf8_locale():
    previous = locale.setlocale(locale.LC_CTYPE)
    try:
        for name in ("en_US.UTF-8", "C.UTF-8"):
            try:
                locale.setlocale(locale.LC_CTYPE, name)
            except locale.Error:
                continue
            return name
    finally:
        locale.setlocale(locale.LC_CTYPE, previous)
    raise unittest.SkipTest("No en_US.UTF-8 or C.UTF-8 locale for real bash startup")


class StartupWindow:
    def __init__(self, maximized=False, before_show=0, autostart=True, right=False):
        self.directory = tempfile.TemporaryDirectory(prefix="idgent-startup-")
        self.environment = patch.dict(os.environ, {
            "XDG_CONFIG_HOME": str(Path(self.directory.name) / "config")})
        self.environment.start()
        settings.set_value("terminal_autostart", autostart)
        self.spawns = []
        self.started = time.monotonic()
        self.locale = utf8_locale()
        self.original_spawn = Vte.Terminal.spawn_async
        self.spawn_patch = patch.object(Vte.Terminal, "spawn_async",
                                        lambda terminal, *a, **kw: self.spawn(terminal, *a, **kw))
        self.spawn_patch.start()
        application = Gtk.Application.get_default() or Gtk.Application(
            application_id="dev.idgent.StartupTests", flags=Gio.ApplicationFlags.NON_UNIQUE)
        if not application.get_is_registered():
            assert application.register(None)
        style.apply_style()
        self.window = MainWindow(application, self.directory.name)
        if before_show:
            spin(before_show)
        self.window.show_all()
        self.shown_at = time.monotonic()
        if right:
            self.window.toggle_terminal_right()
        if maximized:
            # Xvfb has no WM. Deliver the large allocation a maximizing WM
            # would send just after map, on the next main-loop turn.
            def maximize():
                self.window.maximize()
                self.window.resize(1800, 1000)
                return GLib.SOURCE_REMOVE
            GLib.idle_add(maximize)

    def spawn(self, terminal, *args, **kwargs):
        args = list(args)
        index = len(self.spawns)
        log = Path(self.directory.name) / f"sizes-{index}"
        rc = Path(self.directory.name) / f"rc-{index}"
        rc.write_text("PS1=" + shlex.quote(PS1) + "\n" +
                      f"stty size >> {shlex.quote(str(log))}\n" +
                      f"trap 'stty size >> {shlex.quote(str(log))}' WINCH\n")
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith("LC_") and key not in
                       ("LANG", "BASH_ENV", "ENV", "PROMPT_COMMAND", "PS1")}
        environment.update(LANG=self.locale, HOME=self.directory.name,
                           HISTFILE="/dev/null", TERM="xterm-256color")
        args[2] = ["/bin/bash", "--noprofile", "--rcfile", str(rc), "-i"]
        args[3] = [f"{key}={value}" for key, value in environment.items()]
        self.spawns.append(dict(terminal=terminal, log=log,
                                grid=(terminal.get_column_count(), terminal.get_row_count()),
                                at=time.monotonic() - self.started,
                                shown_delay=time.monotonic() - self.shown_at
                                if hasattr(self, "shown_at") else None))
        return self.original_spawn(terminal, *args, **kwargs)

    def ready(self):
        panes = [pane for panel in (self.window.terminal_bottom, self.window.terminal_right)
                 for pane in panel._panes]
        return bool(panes) and all(p.pid and PROMPT.strip() in DockWindow.text(p.terminal)
                                   for p in panes)

    def measurements(self):
        return [dict(grid=s["grid"], at=round(s["at"], 3),
                     shown_delay=s["shown_delay"],
                     sizes=s["log"].read_text().splitlines() if s["log"].exists() else [],
                     cursor=tuple(s["terminal"].get_cursor_position()),
                     final=(s["terminal"].get_column_count(), s["terminal"].get_row_count()),
                     text=DockWindow.text(s["terminal"])) for s in self.spawns]

    def close(self):
        self.window.destroy()
        self.spawn_patch.stop()
        spin(100)
        self.environment.stop()
        self.directory.cleanup()


class TerminalStartupTest(unittest.TestCase):
    def open(self, **kwargs):
        dock = StartupWindow(**kwargs)
        self.addCleanup(dock.close)
        self.assertTrue(poll(dock.ready, 10), dock.measurements())
        spin(250)
        return dock

    def assert_prompt(self, dock, terminal):
        spawn = next(s for s in dock.spawns if s["terminal"] is terminal)
        self.assertEqual(spawn["grid"], (terminal.get_column_count(), terminal.get_row_count()),
                         dock.measurements())
        self.assertEqual(spawn["log"].read_text().splitlines(),
                         [f"{terminal.get_row_count()} {terminal.get_column_count()}"],
                         dock.measurements())
        columns = terminal.get_column_count()
        self.assertEqual(tuple(terminal.get_cursor_position()),
                         (len(PROMPT) % columns, len(PROMPT) // columns), dock.measurements())
        self.assertEqual(DockWindow.text(terminal).replace("\n", "").strip(),
                         PROMPT.strip(), dock.measurements())
        ok, rows, columns = terminal.get_pty().get_size()
        self.assertTrue(ok)
        self.assertEqual((columns, rows), (terminal.get_column_count(), terminal.get_row_count()))

    def assert_wraps(self, dock, terminal):
        columns = terminal.get_column_count()
        typed = "x" * (columns + 7)
        line = PROMPT + typed
        terminal.feed_child(typed.encode())
        self.assertTrue(poll(lambda: tuple(terminal.get_cursor_position()) ==
                             (len(line) % columns, len(line) // columns), 10), dock.measurements())
        rows = DockWindow.rows(terminal)
        expected = [line[i:i + columns] for i in range(0, len(line), columns)]
        self.assertEqual([r.rstrip() for r in rows[:len(expected)]], expected, dock.measurements())

    def test_initial_default_window(self):
        dock = self.open()
        print("STARTUP default", dock.measurements(), flush=True)
        terminal = dock.window.terminal_bottom._panes[0].terminal
        self.assert_prompt(dock, terminal)
        self.assert_wraps(dock, terminal)

    def test_initial_maximized_after_show(self):
        dock = self.open(maximized=True)
        print("STARTUP maximized", dock.measurements(), flush=True)
        terminal = dock.window.terminal_bottom._panes[0].terminal
        self.assert_prompt(dock, terminal)
        self.assert_wraps(dock, terminal)

    def test_startup_before_first_map(self):
        dock = self.open(before_show=100)
        print("STARTUP late map", dock.measurements(), flush=True)
        terminal = dock.window.terminal_bottom._panes[0].terminal
        self.assert_prompt(dock, terminal)
        self.assert_wraps(dock, terminal)

    def check_right_dock(self, maximized):
        dock = self.open(maximized=maximized, right=True)
        for panel in (dock.window.terminal_bottom, dock.window.terminal_right):
            terminal = panel._panes[0].terminal
            self.assert_prompt(dock, terminal)
            self.assert_wraps(dock, terminal)

    def test_right_dock_default(self):
        self.check_right_dock(False)

    def test_right_dock_maximized(self):
        self.check_right_dock(True)

    def test_later_add_and_split_in_both_docks(self):
        dock = self.open(maximized=True, right=True)
        for panel in (dock.window.terminal_bottom, dock.window.terminal_right):
            # Add and Split Terminal both use new_terminal(). Exercise actual
            # enter animations and the complete three-pane layout.
            for _ in range(2):
                self.assertTrue(panel.new_terminal())
                pane = panel._panes[-1]
                self.assertTrue(poll(lambda: pane.pid and PROMPT.strip() in
                                     DockWindow.text(pane.terminal).replace("\n", ""), 10))
                self.assert_prompt(dock, pane.terminal)
            for pane in panel._panes:
                self.assert_wraps(dock, pane.terminal)

    def test_first_terminal_opened_from_hidden_without_autostart(self):
        dock = StartupWindow(autostart=False)
        self.addCleanup(dock.close)
        self.assertEqual(dock.spawns, [])
        self.assertEqual(dock.window.terminal_bottom.terminal_count(), 0)
        for panel, toggle in ((dock.window.terminal_bottom, dock.window.toggle_terminal_bottom),
                              (dock.window.terminal_right, dock.window.toggle_terminal_right)):
            toggle()
            pane = panel._panes[0]
            self.assertTrue(poll(lambda: pane.pid and PROMPT.strip() in DockWindow.text(pane.terminal), 10))
            self.assert_prompt(dock, pane.terminal)
            self.assert_wraps(dock, pane.terminal)
            panel.close_current()
            self.assertTrue(poll(lambda: not panel.get_visible(), 10))
            toggle()
            pane = panel._panes[0]
            self.assertTrue(poll(lambda: pane.pid and PROMPT.strip() in DockWindow.text(pane.terminal), 10))
            self.assert_prompt(dock, pane.terminal)
            self.assert_wraps(dock, pane.terminal)

    def test_early_keyboard_input_is_preserved(self):
        dock = StartupWindow()
        self.addCleanup(dock.close)
        terminal = dock.window.terminal_bottom._panes[0].terminal
        self.assertIsNone(terminal.get_pty())
        self.assertTrue(terminal.get_mapped())
        # A real VTE key event emits commit before a PTY exists.
        event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        event.window = terminal.get_window()
        event.send_event = True
        event.keyval = Gdk.KEY_a
        event.hardware_keycode = 38
        event.state = 0
        terminal.event(event)
        self.assertTrue(poll(lambda: dock.ready() and
                             tuple(terminal.get_cursor_position()) == (len(PROMPT) + 1, 0), 10),
                        dock.measurements())
        self.assertEqual(DockWindow.text(terminal).strip(), PROMPT + "a")

    def test_unmapped_fallback_and_unattached_panel(self):
        window = Gtk.Window()
        self.addCleanup(window.destroy)
        panel = TerminalPanel("/tmp")
        window.add(panel)
        pane = panel._panes[0]
        self.assertIsNone(pane.pid)
        started = time.monotonic()
        self.assertTrue(poll(lambda: pane.pid is not None, 5))
        self.assertLess(time.monotonic() - started, 3)
        self.assertFalse(panel.get_mapped())
        unattached = TerminalPanel("/tmp")
        self.addCleanup(unattached.destroy)
        self.assertTrue(poll(lambda: unattached._panes[0].pid is not None, .8))

    def test_close_before_spawn_cancels_startup(self):
        dock = StartupWindow()
        self.addCleanup(dock.close)
        panel = dock.window.terminal_bottom
        pane = panel._panes[0]
        self.assertIsNone(pane.pid)
        panel.close_current()
        self.assertTrue(pane.cancellable.is_cancelled())
        spin(1100)
        self.assertEqual(dock.spawns, [])
