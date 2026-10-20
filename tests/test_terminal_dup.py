"""Duplicated prompts after divider drags and dock close/reopen (Xvfb, real bash).

The user's report: after sliding a dock divider, closing the dock and opening it
again, the bottom and the right terminal each showed a stack of copies of the
prompt's first row (``lestoq@wall-e:~/projects/`` four times, then the prompt).

The cause is a race in ResizeGate. VTE emits ``contents-changed`` for its own
resize a frame later, even when the shell has written nothing. The gate took
that as the shell's SIGWINCH answer and committed the next width while the
redraw, computed for the previous width, was still in flight. A one-row prompt
drawn for a wide grid, parsed after a narrower commit, autowraps onto two rows;
readline still believes it is one row, so its next redraw clears only the lower
row and leaves the upper one behind, once per race.

On a fast machine bash usually answers before VTE's next update. The shells
here run behind a PTY proxy that delays their output (like a loaded VM), which
makes the race deterministic.
"""
from __future__ import annotations

import os
import signal
import time
import unittest
from unittest.mock import patch

os.environ["GDK_BACKEND"] = "x11"

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Vte", "2.91")
from gi.repository import Vte

from tests.test_resizegate import poll, setUpModule, spin  # noqa: F401  (warning filter)
from tests import test_terminal_resize as resize

# Output delay of the proxy: longer than VTE's update interval (~16 ms), shorter
# than ResizeGate.PACE_MS, so only a gate that waits for real output is safe.
DELAY_S = 0.025

# Forward input and window size at once; hand the shell's output on later.
SLOW_PTY = r"""
import fcntl, os, pty, select, signal, sys, termios, time, tty
delay = float(sys.argv[1])
size = fcntl.ioctl(0, termios.TIOCGWINSZ, b"\0" * 8)
pid, fd = pty.fork()
if pid == 0:
    fcntl.ioctl(0, termios.TIOCSWINSZ, size)
    os.execvp(sys.argv[2], sys.argv[2:])
def winch(*_args):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, fcntl.ioctl(0, termios.TIOCGWINSZ, b"\0" * 8))
signal.signal(signal.SIGWINCH, winch)
tty.setraw(0)
queue = []
while True:
    timeout = max(0.0, queue[0][0] - time.monotonic()) if queue else None
    ready = select.select([0, fd], [], [], timeout)[0]
    if 0 in ready:
        data = os.read(0, 4096)
        if not data:
            break
        os.write(fd, data)
    if fd in ready:
        try:
            data = os.read(fd, 4096)
        except OSError:
            data = b""
        if not data:
            break
        queue.append((time.monotonic() + delay, data))
    while queue and queue[0][0] <= time.monotonic():
        os.write(1, queue.pop(0)[1])
for _due, data in queue:
    os.write(1, data)
"""


def user_locale():
    """The locale the user runs IDgent in, or C.UTF-8 when the session has none."""
    for key in ("LC_ALL", "LC_CTYPE", "LANG"):
        value = os.environ.get(key, "")
        if value and value not in ("C", "POSIX"):
            return value
    return "C.UTF-8"


class SlowDockWindow(resize.DockWindow):
    """DockWindow whose bash shells answer through the delaying proxy."""

    def __init__(self, lc_all, **kwargs):
        original = Vte.Terminal.spawn_async

        def slow_spawn(terminal, *args, **kwargs):
            args = list(args)
            args[2] = ["/usr/bin/env", "python3", "-c", SLOW_PTY, str(DELAY_S)] + list(args[2])
            args[3] = [item for item in args[3] if not item.startswith("LC_ALL=")]
            args[3].append(f"LC_ALL={lc_all}")
            return original(terminal, *args, **kwargs)

        with patch.object(Vte.Terminal, "spawn_async", slow_spawn):
            super().__init__(**kwargs)


class TerminalDuplicateTest(unittest.TestCase):
    assert_prompt = resize.TerminalResizeTest.assert_prompt
    assert_typing_wraps = resize.TerminalResizeTest.assert_typing_wraps

    def open_slow(self, lc_all, **kwargs):
        dock = SlowDockWindow(lc_all, **kwargs)
        self.addCleanup(dock.close)
        return dock

    @staticmethod
    def prompt_rows(dock, pane):
        """Visible rows holding the start of a prompt."""
        return [row for row in dock.rows(pane.terminal) if "lestoq@" in row]

    def assert_single_prompt(self, dock, pane, label):
        # The ``lestoq@`` count is independent of bash53-020: in UTF-8 that bug
        # moves readline's cursor, it does not stack prompt rows.
        self.assertTrue(poll(lambda: pane.frame._answers == [], 2), label)
        spin(4 * DELAY_S * 1000)
        self.assertEqual(len(self.prompt_rows(dock, pane)), 1,
                         (label, dock.geometry(pane), dock.rows(pane.terminal)))
        self.assertEqual(dock.text(pane.terminal).count("lestoq@wall-e"), 1,
                         (label, dock.rows(pane.terminal)))

    def drag_close_reopen(self, lc_all, side):
        """Narrow one dock below the prompt width, widen it, close and reopen it."""
        dock = self.open_slow(lc_all, bottom=1, right=1)
        window = dock.window
        bottom, right = dock.panes
        pane = right if side == "right" else bottom
        toggle = window.toggle_terminal_right if side == "right" else window.toggle_terminal_bottom
        paned = window.dock
        if side == "bottom":
            # The bottom dock's minimum width is fixed in pixels; at the user's
            # cell size it holds ~25 columns, fewer than the prompt.
            for panel in (window.terminal_bottom, window.terminal_right):
                panel.set_font_size(15)
            dock.settle(time.monotonic())
            for other in (bottom, right):
                self.assert_single_prompt(dock, other, "font")
        start = paned.get_position()
        # The right dock narrows towards the window edge, the bottom one when
        # the right dock takes the width. Pointer motion every 8 ms is faster
        # than the delayed shell answers.
        narrow = paned.get_allocated_width() - 5 if side == "right" else 0

        def drag(label, target):
            dock.drag(paned, paned.get_position(), target, steps=40, step_ms=8)
            for other in (bottom, right):
                if other.terminal.get_mapped():
                    self.assert_single_prompt(dock, other, f"{side} {label}")

        drag("narrow", narrow)
        self.assertLess(pane.terminal.get_column_count(), len(resize.PROMPT))
        drag("wide", start)
        for label in ("close", "reopen"):
            toggle()
            dock.settle(time.monotonic(), 300)
            for other in (bottom, right):
                if other.terminal.get_mapped():
                    self.assert_single_prompt(dock, other, f"{side} {label}")
        drag("narrow after reopen", narrow)
        drag("wide after reopen", start)
        return dock

    def test_right_dock_user_locale(self):
        self.drag_close_reopen(user_locale(), "right")

    def test_bottom_dock_user_locale(self):
        self.drag_close_reopen(user_locale(), "bottom")

    def test_both_docks_c_locale(self):
        for side in ("right", "bottom"):
            with self.subTest(side=side):
                dock = self.drag_close_reopen("C", side)
                for pane in dock.panes:
                    self.assert_prompt(dock, pane, f"C {side}")
                    self.assert_typing_wraps(dock, pane, f"C {side}")

    def test_gate_waits_for_shell_output_not_vte_resize_signal(self):
        dock = resize.DockWindow(bottom=1, right=1)
        self.addCleanup(dock.close)
        pane = dock.panes[-1]
        gate = pane.frame
        paned = dock.window.dock
        columns = pane.terminal.get_column_count()
        # A stopped shell cannot answer; only VTE's own signal can arrive.
        os.kill(pane.pid, signal.SIGSTOP)
        self.addCleanup(os.kill, pane.pid, signal.SIGCONT)
        changed = []
        pane.terminal.connect("contents-changed", lambda *_: changed.append(time.monotonic()))
        paned.set_position(paned.get_position() + 80)
        self.assertTrue(poll(lambda: pane.terminal.get_column_count() < columns, 1))
        committed = time.monotonic()
        self.assertTrue(gate._answers)
        self.assertTrue(poll(lambda: changed, 1), "VTE did not report its resize")
        if changed[0] - committed < gate.PACE_MS / 1000 * 0.8:
            self.assertTrue(gate._answers, "gate released on VTE's own resize signal")
        # Without an answer the gate still lets go after PACE_MS.
        self.assertTrue(poll(lambda: not gate._answers, 1))
        os.kill(pane.pid, signal.SIGCONT)
        dock.settle(time.monotonic())
        self.assert_prompt(dock, pane, "after SIGCONT")


if __name__ == "__main__":
    unittest.main()
