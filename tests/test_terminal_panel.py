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

# Keep GTK on the Xvfb display even when WAYLAND_DISPLAY is inherited.
os.environ["GDK_BACKEND"] = "x11"

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Vte", "2.91")
from gi.repository import Gdk, GLib, Gtk, Vte

from idgent.terminal import TerminalPanel, pick_shell, safe_cwd

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
        self.wait_spawn(self.panel._panes[0])

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

    def test_initial_pane_and_appearance(self):
        self.assertEqual(self.panel.terminal_count(), 1)
        tab = self.panel._panes[0]
        self.assertEqual(tab.terminal.get_scrollback_lines(), 10000)
        self.assertEqual(tab.terminal.get_font().to_string(), "Monospace 11")
        scrollbar = tab.page.get_children()[1]
        self.assertEqual(scrollbar.get_adjustment(), tab.terminal.get_vadjustment())
        self.assertTrue(poll(lambda: self.window.get_focus() == tab.terminal))
        self.send(tab.terminal, "printf '%s:%s\\n' \"$TERM\" \"$COLORTERM\"")
        self.expect(tab.terminal, "xterm-256color:truecolor")

    def test_new_and_close_current(self):
        first = self.panel._panes[0]
        self.panel.new_terminal()
        second = self.panel._panes[1]
        self.wait_spawn(second)
        self.assertEqual(self.panel.terminal_count(), 2)
        pid = second.pid
        self.panel.close_current()
        self.assertEqual(self.panel.terminal_count(), 1)
        self.assertIs(self.panel._panes[0], first)
        self.assertTrue(poll(lambda: not alive(pid)))

    def test_close_last_emits_once_without_respawn(self):
        events = []
        self.panel.connect("last-closed", lambda _: events.append(True))
        pid = self.panel._panes[0].pid
        self.panel.close_current()
        self.assertEqual(self.panel.terminal_count(), 0)
        self.assertEqual(events, [True])
        self.panel.close_current()
        self.assertEqual(events, [True])
        self.assertTrue(poll(lambda: not alive(pid)))

    def test_empty_panel_has_no_header_or_notebook(self):
        empty = TerminalPanel(self.directory.name, autostart=False)
        self.assertEqual(empty.terminal_count(), 0)
        self.assertEqual(empty.get_children(), [])
        empty.destroy()
        def descendants(widget):
            yield widget
            if isinstance(widget, Gtk.Container):
                for child in widget.get_children():
                    yield from descendants(child)
        self.assertFalse(any(isinstance(w, (Gtk.Notebook, Gtk.Label, Gtk.Button))
                             for w in descendants(self.panel)))

    def test_equal_horizontal_panes_and_insertion(self):
        first = self.panel._panes[0]
        self.panel.new_terminal()
        second = self.panel._panes[1]
        self.wait_spawn(second)
        first.terminal.grab_focus()
        self.assertTrue(poll(lambda: self.window.get_focus() == first.terminal))
        self.panel.new_terminal()
        middle = self.panel._panes[1]
        self.wait_spawn(middle)
        self.assertIs(self.panel._panes[2], second)
        self.assertTrue(poll(lambda: middle.page.get_allocated_width() > 1))
        allocations = [p.page.get_allocation() for p in self.panel._panes]
        self.assertEqual(len({a.y for a in allocations}), 1)
        self.assertLess(allocations[0].x, allocations[1].x)
        self.assertLess(allocations[1].x, allocations[2].x)
        self.assertLessEqual(max(a.width for a in allocations) - min(a.width for a in allocations), 1)
        self.panel.close_current()
        self.assertTrue(poll(lambda: first.page.get_allocated_width() > allocations[0].width))

    def test_set_cwd_only_changes_new_shells(self):
        first = self.panel._panes[0]
        with tempfile.TemporaryDirectory() as new_directory:
            self.panel.set_cwd(new_directory)
            self.panel.new_terminal()
            second = self.panel._panes[1]
            self.wait_spawn(second)
            for tab, expected in ((first, self.directory.name), (second, new_directory)):
                self.send(tab.terminal, "pwd")
                self.assertTrue(poll(lambda: expected in self.text(tab.terminal).splitlines()))

    def test_invalid_cwd_uses_home(self):
        self.panel.set_cwd("/does/not/exist")
        self.panel.new_terminal()
        terminal = self.wait_spawn(self.panel._panes[1])
        self.send(terminal, "pwd")
        self.assertTrue(poll(lambda: str(Path.home()) in self.text(terminal).splitlines()))

    def test_shell_exit_removes_panes_and_emits_only_for_last(self):
        events = []
        self.panel.connect("last-closed", lambda _: events.append(True))
        self.panel.new_terminal()
        second = self.panel._panes[1]
        self.wait_spawn(second)
        self.send(second.terminal, "exit")
        self.assertTrue(poll(lambda: self.panel.terminal_count() == 1))
        self.assertEqual(events, [])
        first = self.panel._panes[0]
        self.send(first.terminal, "exit")
        self.assertTrue(poll(lambda: first not in self.panel._panes))
        self.assertEqual(self.panel.terminal_count(), 0)
        self.assertEqual(events, [True])

    def test_reparent_keeps_shell_and_output(self):
        tab = self.panel._panes[0]
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
        first = self.panel._panes[0]
        self.panel.new_terminal()
        self.wait_spawn(self.panel._panes[1])
        first.terminal.grab_focus()
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

    def test_focus_navigation_and_split_close_keys(self):
        first = self.panel._panes[0]
        event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        event.state = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK
        for key in (Gdk.KEY_t, Gdk.KEY_percent):
            event.keyval = key
            self.assertTrue(self.panel._key_press(self.panel._active.terminal, event))
            self.wait_spawn(self.panel._active)
        self.assertEqual(self.panel.terminal_count(), 3)
        event.state = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK
        event.keyval = Gdk.KEY_Right
        self.assertTrue(self.panel._key_press(self.panel._active.terminal, event))
        self.assertTrue(poll(lambda: self.window.get_focus() == first.terminal))
        event.keyval = Gdk.KEY_Left
        self.panel._key_press(first.terminal, event)
        self.assertTrue(poll(lambda: self.window.get_focus() == self.panel._panes[-1].terminal))
        event.state = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK
        event.keyval = Gdk.KEY_w
        self.panel._key_press(self.panel._active.terminal, event)
        self.assertEqual(self.panel.terminal_count(), 2)

    def test_focus_events_toggle_style_class(self):
        terminal = self.panel._panes[0].terminal
        event = Gdk.Event.new(Gdk.EventType.FOCUS_CHANGE)
        terminal.emit("focus-in-event", event)
        self.assertTrue(terminal.get_style_context().has_class("focused"))
        terminal.emit("focus-out-event", event)
        self.assertFalse(terminal.get_style_context().has_class("focused"))

    def test_copy_paste_shortcuts(self):
        terminal = self.panel._panes[0].terminal
        self.send(terminal, "printf '%s%s\\n' 'clipboard_' 'ok'")
        self.expect(terminal, "clipboard_ok")
        terminal.select_all()
        event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        event.state = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK
        event.keyval = Gdk.KEY_c
        self.assertTrue(self.panel._key_press(terminal, event))
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        self.assertIn("clipboard_ok", clipboard.wait_for_text())
        clipboard.set_text("printf '%s%s\\n' 'paste_' 'ok'\n", -1)
        event.keyval = Gdk.KEY_v
        self.assertTrue(self.panel._key_press(terminal, event))
        # Bash bracketed paste leaves pasted commands pending for Return.
        self.expect(terminal, "'paste_' 'ok'")
        terminal.feed_child(b"\n")
        self.expect(terminal, "paste_ok")

    def test_context_menu_split_and_kill(self):
        terminal = self.panel._panes[0].terminal
        event = Gdk.EventButton()
        event.type = Gdk.EventType.BUTTON_PRESS
        event.button = 3
        with patch.object(Gtk.Menu, "popup_at_pointer") as popup:
            self.assertTrue(self.panel._button_press(terminal, event))
            popup.assert_called_once_with(event)
        menu = next(w for w in Gtk.Menu.get_for_attach_widget(terminal))
        items = menu.get_children()
        self.assertEqual([w.get_label() for w in items if isinstance(w, Gtk.MenuItem)
                          and not isinstance(w, Gtk.SeparatorMenuItem)],
                         ["Copy", "Paste", "Select All", "Clear", "Split Terminal", "Kill Terminal"])
        items[-2].activate()
        self.wait_spawn(self.panel._panes[-1])
        self.assertEqual(self.panel.terminal_count(), 2)
        items[-1].activate()
        self.assertEqual(self.panel.terminal_count(), 1)
        menu.destroy()

    def test_destroy_terminates_children(self):
        self.panel.new_terminal()
        self.wait_spawn(self.panel._panes[1])
        self.panel.destroy()
        self.assertTrue(poll(lambda: all(not alive(pid) for pid in self.pids)))
        self.assertEqual(self.panel._panes, [])

    def test_destroy_during_spawn(self):
        self.panel.new_terminal()
        pending = self.panel._panes[-1]
        self.panel.destroy()
        self.assertTrue(poll(lambda: pending.cancellable.is_cancelled()))
        # Let the async cancellation callback run as well.
        self.assertTrue(poll(lambda: not self.panel._panes))


if __name__ == "__main__":
    unittest.main()
