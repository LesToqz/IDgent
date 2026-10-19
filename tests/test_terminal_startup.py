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
gi.require_version("Vte", "2.91")
from gi.repository import Gio, GLib, Gtk, Vte

from idgent import style
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
    def __init__(self, maximized=False, before_show=0):
        self.directory = tempfile.TemporaryDirectory(prefix="idgent-startup-")
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
                                at=time.monotonic() - self.started))
        return self.original_spawn(terminal, *args, **kwargs)

    def ready(self):
        panes = [pane for panel in (self.window.terminal_bottom, self.window.terminal_right)
                 for pane in panel._panes]
        return bool(panes) and all(p.pid and PROMPT.strip() in DockWindow.text(p.terminal)
                                   for p in panes)

    def measurements(self):
        return [dict(grid=s["grid"], at=round(s["at"], 3),
                     sizes=s["log"].read_text().splitlines() if s["log"].exists() else [],
                     cursor=tuple(s["terminal"].get_cursor_position()),
                     final=(s["terminal"].get_column_count(), s["terminal"].get_row_count()),
                     text=DockWindow.text(s["terminal"])) for s in self.spawns]

    def close(self):
        self.window.destroy()
        self.spawn_patch.stop()
        spin(100)
        self.directory.cleanup()


class TerminalStartupTest(unittest.TestCase):
    def open(self, **kwargs):
        dock = StartupWindow(**kwargs)
        self.addCleanup(dock.close)
        self.assertTrue(poll(dock.ready, 10), dock.measurements())
        spin(250)
        return dock

    def assert_prompt(self, dock, terminal):
        self.assertEqual(tuple(terminal.get_cursor_position()), (len(PROMPT), 0),
                         dock.measurements())
        self.assertEqual(DockWindow.text(terminal).strip(), PROMPT.strip(), dock.measurements())
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
