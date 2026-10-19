"""Shrink/expand terminals in the real window with real bash PTYs (Xvfb).

The user's report: dragging a divider made terminal text and padding catch up
only ~0.5 s after the drag, and after shrinking then expanding a pane the
cursor sat inside the prompt, so typed text overwrote it instead of wrapping.

The shells run with LC_ALL=C. Unpatched bash 5.3 (before bash53-020) has its
own readline bug in multibyte locales: after a SIGWINCH redraw at a width
narrower than a coloured prompt, a later wider redraw puts the cursor before
the end of the prompt. That happens in a plain PTY without any terminal widget,
so these tests isolate IDgent's part: grid == PTY size, one intact prompt, the
cursor right after it and typed text wrapping onto the next row.
"""
from __future__ import annotations

import os
from pathlib import Path
import signal
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ["GDK_BACKEND"] = "x11"

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Vte", "2.91")
from gi.repository import Gdk, Gio, GLib, Gtk, Vte

from idgent import style
from idgent.resizegate import ResizeGate
from idgent.terminal import TerminalPanel
from idgent.window import MainWindow
from tests.test_resizegate import poll, setUpModule, spin  # noqa: F401  (warning filter)

# The Debian colour prompt with fixed text, so its width is known everywhere.
PS1 = (r"\[\033[01;32m\]lestoq@wall-e\[\033[00m\]:"
       r"\[\033[01;34m\]~/projects/IDgent\[\033[00m\]$ ")
PROMPT = "lestoq@wall-e:~/projects/IDgent$ "
SENTENCE = "hi my name sulthan and im a student"


class DockWindow:
    """A MainWindow whose docks hold real `bash --norc` shells."""

    def __init__(self, bottom=3, right=0, size=(1900, 1000)):
        self.application = Gtk.Application.get_default() or Gtk.Application(
            application_id="dev.idgent.TerminalResize", flags=Gio.ApplicationFlags.NON_UNIQUE)
        if not self.application.get_is_registered():
            assert self.application.register(None)
        style.apply_style()
        self.home = tempfile.TemporaryDirectory(prefix="idgent-resize-home-")
        cwd = Path(self.home.name) / "projects" / "IDgent"
        cwd.mkdir(parents=True)
        pending = []
        original_spawn = Vte.Terminal.spawn_async

        def defer_spawn(terminal, *args, **kwargs):
            pending.append((terminal, args, kwargs))

        with patch.object(TerminalPanel, "ANIMATION_MS", 0), \
             patch.object(Vte.Terminal, "spawn_async", defer_spawn):
            self.window = MainWindow(self.application, str(cwd))
            self.window.set_default_size(*size)
            self.window.show_all()
            if right:
                self.window.toggle_terminal_right()
            for panel, count in ((self.window.terminal_bottom, bottom),
                                 (self.window.terminal_right, right)):
                while panel.terminal_count() < count:
                    panel.new_terminal()
            spin(300)
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith("LC_") and key not in ("LANG", "PROMPT_COMMAND")}
        environment.update(PS1=PS1, HOME=self.home.name, HISTFILE="/dev/null", LC_ALL="C",
                           TERM="xterm-256color", COLORTERM="truecolor")
        for terminal, args, kwargs in pending:
            args = list(args)
            args[2] = ["/bin/bash", "--norc", "--noprofile", "-i"]
            args[3] = [f"{key}={value}" for key, value in environment.items()]
            original_spawn(terminal, *args, **kwargs)
        self.panes = [pane for panel in (self.window.terminal_bottom, self.window.terminal_right)
                      for pane in panel._panes]
        assert poll(lambda: all(pane.pid for pane in self.panes) and
                    all(PROMPT.strip() in self.text(pane.terminal) for pane in self.panes), 10)
        spin(200)
        # Per frame: is any gate still showing an older geometry than its own?
        self.frames = []
        self.window.add_tick_callback(self._tick)
        # Last time each terminal's allocation changed.
        self.changed = {}
        for pane in self.panes:
            pane.terminal.connect("size-allocate", self._allocated)

    def _tick(self, _widget, _clock):
        self.frames.append((time.monotonic(), [pane.frame._committed != pane.frame._target
                                               for pane in self.panes]))
        return GLib.SOURCE_CONTINUE

    def _allocated(self, terminal, allocation):
        geometry = allocation.width, allocation.height
        if getattr(terminal, "_last_geometry", None) != geometry:
            terminal._last_geometry = geometry
            self.changed[terminal] = time.monotonic()

    @staticmethod
    def text(terminal):
        return terminal.get_text_format(Vte.Format.TEXT) or ""

    @staticmethod
    def rows(terminal):
        columns = terminal.get_column_count()
        result = []
        for row in range(terminal.get_row_count()):
            text, _length = terminal.get_text_range_format(Vte.Format.TEXT, row, 0, row, columns)
            result.append((text or "").rstrip("\n"))
        return result

    @staticmethod
    def geometry(pane):
        terminal = pane.terminal
        _ok, rows, columns = terminal.get_pty().get_size()
        return dict(grid=(terminal.get_column_count(), terminal.get_row_count()),
                    pty=(columns, rows), cursor=tuple(terminal.get_cursor_position()),
                    allocation=terminal.get_allocated_width(),
                    gate=pane.frame.get_allocated_width())

    def drag(self, paned, start, end, steps=60, step_ms=10):
        """Move a divider like a pointer drag; returns the time of the last move."""
        for index in range(1, steps + 1):
            paned.set_position(round(start + (end - start) * index / steps))
            spin(step_ms)
        return time.monotonic()

    def settle(self, moved_at, wait_ms=400):
        """Seconds from the last divider move until the last terminal resize."""
        spin(wait_ms)
        return max([0.0] + [at - moved_at for at in self.changed.values() if at >= moved_at])

    def longest_stale(self, since):
        """Longest time (s) any gate kept an older geometry, frame-sampled."""
        worst = 0.0
        for index in range(len(self.panes)):
            started = None
            for at, stale in self.frames:
                if at < since:
                    continue
                if stale[index] and started is None:
                    started = at
                elif not stale[index] and started is not None:
                    worst, started = max(worst, at - started), None
            if started is not None:
                worst = max(worst, self.frames[-1][0] - started)
        return worst

    def close(self):
        pids = [pane.pid for pane in self.panes if pane.pid]
        self.window.destroy()
        stopped = poll(lambda: all(not Path(f"/proc/{pid}").exists() for pid in pids))
        for pid in pids:
            if Path(f"/proc/{pid}").exists():
                os.kill(pid, signal.SIGKILL)
        self.home.cleanup()
        assert stopped, "bash survived window destruction"


class TerminalResizeTest(unittest.TestCase):
    def open(self, **kwargs):
        dock = DockWindow(**kwargs)
        self.addCleanup(dock.close)
        return dock

    def assert_prompt(self, dock, pane, label=""):
        """Grid == PTY, exactly one intact prompt, cursor right after it."""
        state = dock.geometry(pane)
        self.assertEqual(state["grid"], state["pty"], (label, state))
        columns = state["grid"][0]
        rows = dock.rows(pane.terminal)
        visible = [row for row in rows if row.strip()]
        expected = [PROMPT[start:start + columns] for start in range(0, len(PROMPT), columns)]
        self.assertEqual([row.rstrip() for row in visible],
                         [row.rstrip() for row in expected if row.strip()], (label, state, rows))
        self.assertEqual(dock.text(pane.terminal).count("lestoq@wall-e"), 1, (label, rows))
        if len(PROMPT) % columns:
            self.assertEqual(state["cursor"], (len(PROMPT) % columns, len(PROMPT) // columns),
                             (label, state, rows))

    def assert_typing_wraps(self, dock, pane, label=""):
        """Typed text continues on the next row; the prompt is not overwritten."""
        columns = pane.terminal.get_column_count()
        typed = SENTENCE + " " + "x" * (columns + 5)
        line = PROMPT + typed
        if len(line) % columns == 0:
            typed += "y"
            line += "y"
        pane.terminal.feed_child(typed.encode())
        self.assertTrue(poll(lambda: dock.text(pane.terminal).replace("\n", "").rstrip()
                             .endswith(typed.rstrip()), 3), dock.rows(pane.terminal))
        spin(50)
        rows = dock.rows(pane.terminal)
        expected = [line[start:start + columns] for start in range(0, len(line), columns)]
        self.assertEqual([row.rstrip() for row in rows[:len(expected)]],
                         [row.rstrip() for row in expected], (label, columns, rows))
        joined = "".join(row.ljust(columns) for row in rows[:len(expected)])
        self.assertTrue(joined.startswith(PROMPT), (label, rows))
        self.assertEqual(tuple(pane.terminal.get_cursor_position()),
                         (len(line) % columns, len(line) // columns), (label, rows))
        self.assertGreater(len(line) // columns, 0)

    def test_user_scenario_bottom_dock_sidebar_drag(self):
        dock = self.open(bottom=3)
        sidebar = dock.window.sidebar
        sidebar.set_position(300)
        spin(300)
        for pane in dock.panes:
            self.assert_prompt(dock, pane, "start")
            self.assertGreater(pane.terminal.get_column_count(), len(PROMPT))
        started = time.monotonic()
        moved = dock.drag(sidebar, 300, 1350)
        narrow_settle = dock.settle(moved)
        for pane in dock.panes:
            self.assertLess(pane.terminal.get_column_count(), len(PROMPT) - 5)
            self.assert_prompt(dock, pane, "narrow")
        moved = dock.drag(sidebar, 1350, 430)
        wide_settle = dock.settle(moved)
        stale = dock.longest_stale(started)
        print("SIDEBAR settle_ms", round(narrow_settle * 1000), round(wide_settle * 1000),
              "longest_stale_ms", round(stale * 1000), [dock.geometry(p) for p in dock.panes],
              flush=True)
        self.assertLessEqual(narrow_settle, .1)
        self.assertLessEqual(wide_settle, .1)
        self.assertLess(stale, .15)
        for pane in dock.panes:
            self.assertGreater(pane.terminal.get_column_count(), len(PROMPT))
            self.assert_prompt(dock, pane, "expanded")
            self.assert_typing_wraps(dock, pane, "expanded")

    def test_right_dock_shrink_then_expand(self):
        dock = self.open(bottom=1, right=3)
        paned = dock.window.dock
        width = paned.get_allocated_width()
        right = dock.panes[1:]
        start = paned.get_position()
        started = time.monotonic()
        moved = dock.drag(paned, start, width - 180, steps=40)
        dock.settle(moved)
        for pane in right:
            self.assertLess(pane.terminal.get_column_count(), len(PROMPT))
            self.assert_prompt(dock, pane, "right narrow")
        moved = dock.drag(paned, width - 180, width - 900, steps=40)
        latency = dock.settle(moved)
        print("RIGHT settle_ms", round(latency * 1000), "longest_stale_ms",
              round(dock.longest_stale(started) * 1000), flush=True)
        self.assertLessEqual(latency, .1)
        for pane in dock.panes:
            self.assert_prompt(dock, pane, "right expanded")
        for pane in right:
            self.assert_typing_wraps(dock, pane, "right expanded")

    def test_repeated_cycles_fast_slow_and_bottom_divider(self):
        dock = self.open(bottom=3)
        sidebar = dock.window.sidebar
        for cycle, (steps, step_ms) in enumerate(((60, 4), (20, 30), (8, 2), (60, 16))):
            dock.drag(sidebar, 300, 1400, steps, step_ms)
            spin(150 if cycle % 2 else 0)  # with and without a pause while narrow
            moved = dock.drag(sidebar, 1400, 300, steps, step_ms)
            self.assertLessEqual(dock.settle(moved), .1, (steps, step_ms))
            for pane in dock.panes:
                self.assert_prompt(dock, pane, f"cycle {cycle}")
        # The editor/terminal divider changes rows, not columns.
        paned = dock.window.dock_bottom
        height = paned.get_allocated_height()
        rows = dock.panes[0].terminal.get_row_count()
        moved = dock.drag(paned, paned.get_position(), height - 120, steps=30)
        self.assertLessEqual(dock.settle(moved), .1)
        self.assertLess(dock.panes[0].terminal.get_row_count(), rows)
        moved = dock.drag(paned, height - 120, 150, steps=30)
        self.assertLessEqual(dock.settle(moved), .1)
        self.assertGreater(dock.panes[0].terminal.get_row_count(), rows)
        for pane in dock.panes:
            self.assert_prompt(dock, pane, "rows")
        self.assert_typing_wraps(dock, dock.panes[1], "after cycles")

    def test_jumps_maximise_zoom_font_and_dock_toggle(self):
        dock = self.open(bottom=3, right=2)
        window = dock.window
        panel = window.terminal_bottom
        steps = (
            ("narrow jump", lambda: window.sidebar.set_position(1400)),
            ("wide jump", lambda: window.sidebar.set_position(300)),
            ("maximise", lambda: panel.toggle_maximize(panel._panes[1])),
            ("restore", panel.restore),
            ("zoom in", lambda: [p.terminal.set_font_scale(1.6) for p in dock.panes]),
            ("zoom out", lambda: [p.terminal.set_font_scale(1.0) for p in dock.panes]),
            ("font", lambda: [pnl.set_font_size(14) for pnl in
                              (window.terminal_bottom, window.terminal_right)]),
            ("hide right", window.toggle_terminal_right),
            ("show right", window.toggle_terminal_right),
            ("hide bottom", window.toggle_terminal_bottom),
            ("show bottom", window.toggle_terminal_bottom),
            ("terminal fullscreen", window.toggle_terminal_fullscreen),
            ("restore fullscreen", window.toggle_terminal_fullscreen),
        )
        for label, action in steps:
            moved = time.monotonic()
            action()
            latency = dock.settle(moved, 350)
            self.assertLessEqual(latency, .1, label)
            for pane in dock.panes:
                if pane.terminal.get_mapped():
                    self.assert_prompt(dock, pane, label)
        for pane in dock.panes:
            self.assert_typing_wraps(dock, pane, "after jumps")

    def test_gate_never_reflows_terminal_or_leaves_it_waiting(self):
        dock = self.open(bottom=2)
        observed = []
        for pane in dock.panes:
            pane.terminal.connect("size-allocate", lambda terminal, _a: observed.append(
                terminal.get_property("rewrap-on-resize")))
        moved = dock.drag(dock.window.sidebar, 300, 1300, steps=30)
        dock.settle(moved)
        self.assertTrue(observed)
        self.assertFalse(any(observed))
        for pane in dock.panes:
            self.assertIsInstance(pane.frame, ResizeGate)
            self.assertTrue(pane.terminal.get_property("rewrap-on-resize"))
            self.assertEqual(pane.frame._committed, pane.frame._target)
            self.assertEqual(pane.frame._answers, [])
            self.assertIsNone(pane.frame._release)


if __name__ == "__main__":
    unittest.main()
