"""Integration checks against real interactive shells under Xvfb."""
from __future__ import annotations

import os
from pathlib import Path
import signal
import tempfile
import time
import unittest
import warnings
from unittest.mock import patch

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Vte", "2.91")
from gi.repository import Gdk, GLib, Gtk, Vte

from idgent.terminal import TerminalPanel, pick_shell, safe_cwd, tab_title

def setUpModule() -> None:
    # PyGObject emits these Python 3.14 deprecations on every GLib iteration.
    warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"gi\.events")


def poll(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    context = GLib.MainContext.default()
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class HelpersTest(unittest.TestCase):
    def test_pick_shell(self):
        with patch.dict(os.environ, {"SHELL": "/bin/sh"}):
            self.assertEqual(pick_shell(), "/bin/sh")
        with patch.dict(os.environ, {"SHELL": "/missing/shell"}):
            self.assertEqual(pick_shell(), "/bin/bash")
        with tempfile.NamedTemporaryFile() as file:
            with patch.dict(os.environ, {"SHELL": file.name}):
                self.assertEqual(pick_shell(), "/bin/bash")

    def test_safe_cwd(self):
        with tempfile.TemporaryDirectory() as home:
            with patch.dict(os.environ, {"HOME": home}):
                self.assertEqual(safe_cwd(home), home)
                self.assertEqual(safe_cwd("/does/not/exist"), home)
                self.assertEqual(safe_cwd(""), home)
        with patch.dict(os.environ, {"HOME": "/does/not/exist"}):
            self.assertEqual(safe_cwd("/missing"), "/")

    def test_tab_title(self):
        self.assertEqual(tab_title("/bin/bash", 1), "bash 1")
        self.assertEqual(tab_title("/usr/bin/zsh", 12), "zsh 12")


class TerminalPanelTest(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"SHELL": "/bin/bash", "PROMPT_COMMAND": "", "PS1": "$ "})
        self.environment.start()
        self.directory = tempfile.TemporaryDirectory()
        self.window = Gtk.Window()
        self.window.set_default_size(800, 400)
        self.panel = TerminalPanel(self.directory.name)
        self.window.add(self.panel)
        self.window.show_all()
        self.pids: set[int] = set()
        self.wait_spawn(self.panel._tabs[0])

    def tearDown(self):
        self.window.destroy()
        stopped = poll(lambda: all(not alive(pid) for pid in self.pids))
        # Keep the test runner free of shells even if a cleanup assertion fails.
        for pid in self.pids:
            if alive(pid):
                os.kill(pid, signal.SIGKILL)
        self.directory.cleanup()
        self.environment.stop()
        self.assertTrue(stopped, "shell process survived panel destruction")

    def wait_spawn(self, tab):
        self.assertTrue(poll(lambda: tab.pid is not None), "shell did not spawn")
        self.pids.add(tab.pid)
        return tab.terminal

    def text(self, terminal):
        # VTE >= 0.72 rejects the non-null attributes array supplied by
        # PyGObject get_text(); use its supported text-only replacement.
        return terminal.get_text_format(Vte.Format.TEXT) or ""

    def send(self, terminal, command):
        terminal.feed_child((command + "\n").encode())

    def expect(self, terminal, value):
        self.assertTrue(poll(lambda: value in self.text(terminal)), repr(self.text(terminal)))

    def test_initial_tab_and_appearance(self):
        self.assertEqual(self.panel.notebook.get_n_pages(), 1)
        tab = self.panel._tabs[0]
        self.assertEqual(tab.terminal.get_scrollback_lines(), 10000)
        self.assertEqual(tab.terminal.get_font().to_string(), "Monospace 11")
        scrollbar = tab.page.get_children()[1]
        self.assertEqual(scrollbar.get_adjustment(), tab.terminal.get_vadjustment())
        self.assertTrue(poll(lambda: self.window.get_focus() == tab.terminal))
        self.send(tab.terminal, "printf '%s:%s\\n' \"$TERM\" \"$COLORTERM\"")
        self.expect(tab.terminal, "xterm-256color:truecolor")

    def test_new_and_close_current(self):
        first = self.panel._tabs[0]
        self.panel.new_terminal()
        second = self.panel._tabs[1]
        self.wait_spawn(second)
        self.assertEqual(self.panel.notebook.get_n_pages(), 2)
        pid = second.pid
        self.panel.close_current()
        self.assertEqual(self.panel.notebook.get_n_pages(), 1)
        self.assertIs(self.panel._tabs[0], first)
        self.assertTrue(poll(lambda: not alive(pid)))

    def test_close_last_replaces_shell(self):
        previous = self.panel._tabs[0]
        pid = previous.pid
        self.panel.close_current()
        self.assertEqual(self.panel.notebook.get_n_pages(), 1)
        self.assertIsNot(self.panel._tabs[0], previous)
        self.wait_spawn(self.panel._tabs[0])
        self.assertTrue(poll(lambda: not alive(pid)))

    def test_set_cwd_only_changes_new_shells(self):
        first = self.panel._tabs[0]
        with tempfile.TemporaryDirectory() as new_directory:
            self.panel.set_cwd(new_directory)
            self.panel.new_terminal()
            second = self.panel._tabs[1]
            self.wait_spawn(second)
            for tab, expected in ((first, self.directory.name), (second, new_directory)):
                self.send(tab.terminal, "pwd")
                self.assertTrue(poll(lambda: expected in self.text(tab.terminal).splitlines()))

    def test_invalid_cwd_uses_home(self):
        self.panel.set_cwd("/does/not/exist")
        self.panel.new_terminal()
        terminal = self.wait_spawn(self.panel._tabs[1])
        self.send(terminal, "pwd")
        self.assertTrue(poll(lambda: str(Path.home()) in self.text(terminal).splitlines()))

    def test_shell_exit_removes_tab_and_replaces_last(self):
        self.panel.new_terminal()
        second = self.panel._tabs[1]
        self.wait_spawn(second)
        self.send(second.terminal, "exit")
        self.assertTrue(poll(lambda: self.panel.notebook.get_n_pages() == 1))
        first = self.panel._tabs[0]
        self.send(first.terminal, "exit")
        self.assertTrue(poll(lambda: first not in self.panel._tabs))
        self.assertEqual(self.panel.notebook.get_n_pages(), 1)
        self.wait_spawn(self.panel._tabs[0])

    def test_reparent_keeps_shell_and_output(self):
        tab = self.panel._tabs[0]
        pid = tab.pid
        self.window.remove(self.panel)
        vertical = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        horizontal = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.window.add(vertical)
        vertical.pack1(horizontal, True, False)
        vertical.pack2(self.panel, True, False)
        self.window.show_all()
        self.panel.reparent(horizontal)
        self.window.show_all()
        self.send(tab.terminal, "printf '%s%s\\n' 'reparent-' 'ok'")
        self.expect(tab.terminal, "reparent-ok")
        self.assertEqual(tab.pid, pid)
        self.assertTrue(alive(pid))
        self.assertGreater(tab.terminal.get_allocated_width(), 0)

    def test_switch_focus_and_zoom_shortcuts(self):
        first = self.panel._tabs[0]
        self.panel.new_terminal()
        self.wait_spawn(self.panel._tabs[1])
        self.panel.notebook.set_current_page(0)
        self.assertTrue(poll(lambda: self.window.get_focus() == first.terminal))
        event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        event.state = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK
        for key, expected in ((Gdk.KEY_plus, 1.1), (Gdk.KEY_minus, 1.0), (Gdk.KEY_0, 1.0)):
            event.keyval = key
            self.assertTrue(self.panel._key_press(first.terminal, event))
            self.assertAlmostEqual(first.terminal.get_font_scale(), expected)
        for key, expected in ((Gdk.KEY_plus, 3.0), (Gdk.KEY_minus, 0.5)):
            event.keyval = key
            for _ in range(40):
                self.panel._key_press(first.terminal, event)
            self.assertAlmostEqual(first.terminal.get_font_scale(), expected)

    def test_destroy_terminates_children(self):
        self.panel.new_terminal()
        self.wait_spawn(self.panel._tabs[1])
        self.panel.destroy()
        self.assertTrue(poll(lambda: all(not alive(pid) for pid in self.pids)))
        self.assertEqual(self.panel._tabs, [])

    def test_destroy_during_spawn(self):
        self.panel.new_terminal()
        pending = self.panel._tabs[-1]
        self.panel.destroy()
        self.assertTrue(poll(lambda: pending.cancellable.is_cancelled()))
        # Let the async cancellation callback run as well.
        self.assertTrue(poll(lambda: not self.panel._tabs))


if __name__ == "__main__":
    unittest.main()
