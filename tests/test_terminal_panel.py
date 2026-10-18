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

from idgent.terminal import MAX_TERMINALS, TerminalPanel, pick_shell, safe_cwd
from idgent.terminal_bar import PaneHeader
from idgent.resizegate import ResizeGate

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

    def test_complete_page_is_gated_and_control_can_opt_out(self):
        pane = self.panel._panes[0]
        self.assertIsInstance(pane.frame, ResizeGate)
        self.assertIs(pane.frame.get_child(), pane.page)
        self.assertIs(pane.frame.get_parent(), self.panel._body)
        self.assertIs(pane.header.get_parent(), pane.content.get_parent())
        self.assertFalse(pane.frame.get_can_focus())
        self.assertEqual(pane.frame.get_preferred_width(), pane.page.get_preferred_width())
        control = TerminalPanel(self.directory.name, autostart=False)
        control.RESIZE_GATE = False
        self.addCleanup(control.destroy)
        control.new_terminal()
        self.wait_spawn(control._panes[0])
        self.assertIs(control._panes[0].frame, control._panes[0].page)
        control.destroy()

    def test_discrete_layout_commits_without_waiting_for_debounce(self):
        self.panel.new_terminal()
        pane = self.panel._panes[0]
        self.wait_spawn(self.panel._panes[1])
        self.assertTrue(poll(lambda: pane.frame._committed == pane.frame._target))
        # A long timer makes accidental fallback to debounce fail this test.
        for other in self.panel._panes:
            other.frame.SETTLE_MS = 10000
        self.panel.toggle_maximize(pane)
        self.assertTrue(poll(lambda: pane.frame._committed == pane.frame._target
                             and pane.frame.get_allocated_width() > 700, .5))
        self.panel.restore()
        self.assertTrue(poll(lambda: all(p.frame._committed == p.frame._target
                                        and p.frame.get_allocated_width() < 500
                                        for p in self.panel._panes), .5))

    def test_initial_pane_and_appearance(self):
        self.assertEqual(self.panel.terminal_count(), 1)
        tab = self.panel._panes[0]
        self.assertEqual(tab.terminal.get_scrollback_lines(), 10000)
        self.assertEqual(tab.terminal.get_font().to_string(), "Monospace 11")
        scrollbar = tab.content.get_children()[1]
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
        self.panel.ANIMATION_MS = 0
        events = []
        self.panel.connect("last-closed", lambda _: events.append(True))
        pid = self.panel._panes[0].pid
        self.panel.close_current()
        self.assertEqual(self.panel.terminal_count(), 0)
        self.assertEqual(events, [True])
        self.panel.close_current()
        self.assertEqual(events, [True])
        self.assertTrue(poll(lambda: not alive(pid)))

    def test_empty_panel_has_only_overlay_and_no_notebook(self):
        empty = TerminalPanel(self.directory.name, autostart=False)
        self.assertEqual(empty.terminal_count(), 0)
        self.assertEqual(empty.get_children(), [empty.overlay])
        self.assertEqual(empty._body.get_children(), [])
        self.assertEqual(empty.header_labels(), [])
        self.assertIn(empty.add_button, empty.overlay.get_children())
        self.assertIn(empty.toast, empty.overlay.get_children())
        empty.destroy()
        def descendants(widget):
            yield widget
            if isinstance(widget, Gtk.Container):
                for child in widget.get_children():
                    yield from descendants(child)
        self.assertFalse(any(isinstance(w, Gtk.Notebook) for w in descendants(self.panel)))
        self.assertEqual(self.panel.get_orientation(), Gtk.Orientation.VERTICAL)
        self.assertEqual(self.panel.get_children(), [self.panel.overlay])
        self.assertEqual(self.panel._body.get_orientation(), Gtk.Orientation.HORIZONTAL)

    def test_pane_structure_header_above_terminal(self):
        pane = self.panel._panes[0]
        self.assertIsInstance(pane.header, PaneHeader)
        inner = pane.page.get_children()[0]
        self.assertEqual(inner.get_orientation(), Gtk.Orientation.VERTICAL)
        self.assertEqual(inner.get_children(), [pane.header, pane.content])
        self.assertIn(pane.terminal, pane.content.get_children())
        self.assertEqual(pane.header.text, "bash1")

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
        self.assertTrue(poll(lambda: all(p.frame.get_opacity() == 1 for p in self.panel._panes)))
        self.assertTrue(poll(lambda: middle.frame.get_allocated_width() > 1))
        allocations = [p.frame.get_allocation() for p in self.panel._panes]
        xs = [p.frame.translate_coordinates(self.panel, 0, 0)[0] for p in self.panel._panes]
        self.assertEqual(len({a.y for a in allocations}), 1)
        self.assertLess(xs[0], xs[1])
        self.assertLess(xs[1], xs[2])
        self.assertLessEqual(max(a.width for a in allocations) - min(a.width for a in allocations), 1)
        self.panel.close_current()
        self.assertTrue(poll(lambda: first.frame.get_allocated_width() > allocations[0].width))

    def test_stacked_panes_are_rows_with_horizontal_dividers(self):
        window = Gtk.Window()
        window.set_default_size(600, 700)
        panel = TerminalPanel(self.directory.name, stacked=True)
        panel.ANIMATION_MS = 0
        window.add(panel)
        window.show_all()
        self.addCleanup(window.destroy)
        self.wait_spawn(panel._panes[0])
        first = panel._panes[0]
        panel.new_terminal()
        self.wait_spawn(panel._panes[1])
        first.terminal.grab_focus()
        self.assertTrue(poll(lambda: window.get_focus() == first.terminal))
        panel.new_terminal()
        middle = panel._panes[1]
        self.wait_spawn(middle)
        self.assertEqual(panel._body.get_orientation(), Gtk.Orientation.VERTICAL)
        self.assertEqual(panel.header_labels(), ["bash1", "bash2", "bash3"])
        self.assertTrue(poll(lambda: all(p.frame.get_allocated_height() > 1 for p in panel._panes)))
        rects = [(p.frame.translate_coordinates(panel, 0, 0), p.frame.get_allocation())
                 for p in panel._panes]
        self.assertEqual(len({xy[0] for xy, _ in rects}), 1)
        self.assertEqual(len({a.width for _, a in rects}), 1)
        self.assertEqual(rects[0][1].width, panel.get_allocated_width())
        ys = [xy[1] for xy, _ in rects]
        self.assertLess(ys[0], ys[1])
        self.assertLess(ys[1], ys[2])
        heights = [a.height for _, a in rects]
        self.assertLessEqual(max(heights) - min(heights), 1)
        for pane in panel._panes[:-1]:
            self.assertEqual(pane.divider.get_orientation(), Gtk.Orientation.HORIZONTAL)
            self.assertTrue(pane.divider.get_visible())
        self.assertFalse(panel._panes[-1].divider.get_visible())
        panel.destroy()

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
        self.panel.ANIMATION_MS = 0
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

    def test_stacked_focus_moves_with_up_down_only_when_stacked(self):
        up, down = Gdk.KEY_Up, Gdk.KEY_Down
        event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        event.state = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK
        first = self.panel._panes[0]
        self.panel.new_terminal()
        self.wait_spawn(self.panel._panes[1])
        # Side by side: Ctrl+Alt+Up/Down are left to the terminal.
        for key in (up, down):
            event.keyval = key
            self.assertFalse(self.panel._key_press(first.terminal, event))
        window = Gtk.Window()
        window.set_default_size(600, 600)
        stacked = TerminalPanel(self.directory.name, stacked=True)
        window.add(stacked)
        window.show_all()
        self.addCleanup(window.destroy)
        self.wait_spawn(stacked._panes[0])
        stacked.new_terminal()
        self.wait_spawn(stacked._panes[1])
        top, bottom = stacked._panes
        top.terminal.grab_focus()
        self.assertTrue(poll(lambda: window.get_focus() == top.terminal))
        event.keyval = down
        self.assertTrue(stacked._key_press(top.terminal, event))
        self.assertTrue(poll(lambda: window.get_focus() == bottom.terminal))
        event.keyval = up
        self.assertTrue(stacked._key_press(bottom.terminal, event))
        self.assertTrue(poll(lambda: window.get_focus() == top.terminal))
        event.keyval = Gdk.KEY_Right  # left/right keep working as well
        self.assertTrue(stacked._key_press(top.terminal, event))
        self.assertTrue(poll(lambda: window.get_focus() == bottom.terminal))
        stacked.destroy()

    def background_pixel(self, window, terminal):
        """Rendered colour near the bottom-right corner of an (empty) terminal area."""
        poll(lambda: False, 0.4)
        x, y = terminal.translate_coordinates(window, 0, 0)
        x += terminal.get_allocated_width() - 4
        y += terminal.get_allocated_height() - 4
        pixbuf = Gdk.pixbuf_get_from_window(window.get_window(), x, y, 1, 1)
        return tuple(pixbuf.get_pixels()[:3])

    def test_apply_palette_changes_existing_and_new_terminals(self):
        first = self.panel._panes[0]
        self.assertEqual(self.background_pixel(self.window, first.terminal), (0x1e, 0x1e, 0x1e))
        ansi = [f"#{i:02x}{i:02x}{i:02x}" for i in range(0x10, 0x10 + 16 * 0x10, 0x10)]
        calls = []
        original = Vte.Terminal.set_colors

        def record(terminal, fg, bg, palette):
            calls.append((terminal, fg.to_string(), bg.to_string(), len(palette)))
            return original(terminal, fg, bg, palette)

        with patch.object(Vte.Terminal, "set_colors", record):
            self.panel.apply_palette("#112233", "#445566", ansi, "#aabbcc")
            self.panel.new_terminal()
        second = self.panel._panes[1]
        self.wait_spawn(second)
        for pane in (first, second):
            bg = pane.frame.get_style_context().get_background_color(Gtk.StateFlags.NORMAL)
            self.assertEqual(bg.to_string(), "rgb(68,85,102)")
            self.assertIn((pane.terminal, "rgb(17,34,51)", "rgb(68,85,102)", 16), calls)
            self.assertEqual(self.background_pixel(self.window, pane.terminal), (0x44, 0x55, 0x66))
        # The default palette is used until a panel's own palette is changed.
        fresh = TerminalPanel(self.directory.name, autostart=False)
        self.addCleanup(fresh.destroy)
        self.assertEqual(fresh._palette[:2], ("#d4d4d4", "#1e1e1e"))

    def test_cursor_and_scrollback_apis_reach_existing_and_new_terminals(self):
        first = self.panel._panes[0].terminal
        self.assertEqual((first.get_cursor_shape(), first.get_cursor_blink_mode(),
                          first.get_scrollback_lines()),
                         (Vte.CursorShape.BLOCK, Vte.CursorBlinkMode.ON, 10000))
        self.panel.set_cursor_shape("ibeam")
        self.panel.set_cursor_blink(False)
        self.panel.set_scrollback(25000)
        self.assertEqual((first.get_cursor_shape(), first.get_cursor_blink_mode(),
                          first.get_scrollback_lines()),
                         (Vte.CursorShape.IBEAM, Vte.CursorBlinkMode.OFF, 25000))
        self.panel.new_terminal()
        second = self.wait_spawn(self.panel._panes[1])
        self.assertEqual((second.get_cursor_shape(), second.get_cursor_blink_mode(),
                          second.get_scrollback_lines()),
                         (Vte.CursorShape.IBEAM, Vte.CursorBlinkMode.OFF, 25000))
        self.panel.set_cursor_shape("underline")
        self.panel.set_cursor_blink(True)
        for terminal in (first, second):
            self.assertEqual((terminal.get_cursor_shape(), terminal.get_cursor_blink_mode()),
                             (Vte.CursorShape.UNDERLINE, Vte.CursorBlinkMode.ON))
        # Scrollback is clamped to 1000-100000 lines.
        for lines, expected in ((5, 1000), (-1, 1000), (10 ** 9, 100000), (1000, 1000), (100000, 100000)):
            self.panel.set_scrollback(lines)
            self.assertEqual([t.get_scrollback_lines() for t in (first, second)], [expected] * 2)
        with self.assertRaises(ValueError):
            self.panel.set_cursor_shape("beam")
        self.assertEqual(first.get_cursor_shape(), Vte.CursorShape.UNDERLINE)
        self.panel.set_cursor_shape("block")
        self.assertEqual(second.get_cursor_shape(), Vte.CursorShape.BLOCK)

    def test_focus_events_toggle_style_class(self):
        terminal = self.panel._panes[0].terminal
        event = Gdk.Event.new(Gdk.EventType.FOCUS_CHANGE)
        header = self.panel._panes[0].header.get_style_context()
        terminal.emit("focus-in-event", event)
        self.assertTrue(terminal.get_style_context().has_class("focused"))
        self.assertTrue(header.has_class("focused"))
        terminal.emit("focus-out-event", event)
        self.assertFalse(terminal.get_style_context().has_class("focused"))
        self.assertFalse(header.has_class("focused"))

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
