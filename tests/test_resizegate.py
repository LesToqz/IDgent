"""Real GTK/VTE resize experiments and ResizeGate regression checks (Xvfb)."""
from __future__ import annotations

import os
from pathlib import Path
import pwd
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import warnings

os.environ["GDK_BACKEND"] = "x11"

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Vte", "2.91")
from gi.repository import Gdk, GLib, Gtk, Vte

from idgent.resizegate import ResizeGate
from idgent.terminal import TerminalPanel

PS1 = r"\[\033[01;32m\]\u@\h\[\033[00m\]:\[\033[01;34m\]\w\[\033[00m\]\$ "


def setUpModule():
    warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"gi\.events")


def poll(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    context = GLib.MainContext.default()
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        if predicate():
            return True
        time.sleep(.002)
    return bool(predicate())


def spin(milliseconds):
    until = time.monotonic() + milliseconds / 1000
    assert poll(lambda: time.monotonic() >= until, milliseconds / 1000 + 2)


class BashDrag:
    """An actual paned allocation, PTY, bash/readline and Debian colour prompt."""

    def __init__(self, gate_type=None, count=1, trap=True):
        self.directory = tempfile.TemporaryDirectory(prefix="idgent-resize-")
        self.cwd = Path(self.directory.name) / "projects" / "long-prompt-directory"
        self.cwd.mkdir(parents=True)
        self.window = Gtk.Window()
        self.window.set_default_size(1100, 360)
        self.paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.window.add(self.paned)
        self.paned.pack1(Gtk.Box(), True, False)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True)
        self.paned.pack2(box, True, False)
        self.terminals = []
        self.gates = []
        self.pids = []
        self.logs = []
        for index in range(count):
            terminal = Vte.Terminal()
            TerminalPanel._configure(terminal)
            self.terminals.append(terminal)
            child = terminal
            if gate_type:
                child = gate_type()
                child.add(terminal)
                self.gates.append(child)
            box.pack_start(child, True, True, 0)
            self.logs.append(Path(self.directory.name) / f"winch-{index}")
        self.paned.set_position(180)
        self.window.show_all()
        spin(200)
        for terminal, log in zip(self.terminals, self.logs):
            environment = dict(os.environ, PS1=PS1, PROMPT_COMMAND="", TERM="xterm-256color",
                               COLORTERM="truecolor", RESIZE_LOG=str(log))
            def spawned(_terminal, pid, error, _data):
                assert error is None, error
                self.pids.append(pid)
            terminal.spawn_async(
                Vte.PtyFlags.DEFAULT, str(self.cwd), ["/bin/bash", "--norc", "-i"],
                [f"{key}={value}" for key, value in environment.items()],
                GLib.SpawnFlags.DEFAULT, child_setup=None, timeout=-1,
                cancellable=None, callback=spawned, user_data=None)
        assert poll(lambda: len(self.pids) == count and
                    all("long-prompt-directory" in self.text(t) for t in self.terminals))
        # Install instrumentation, then clear command echo and the startup prompt.
        for terminal in self.terminals:
            command = "trap 'printf \"%s\\n\" \"$COLUMNS\" >> \"$RESIZE_LOG\"' WINCH; " if trap else ""
            terminal.feed_child((command + "printf '\\033[2J\\033[H'\n").encode())
        assert poll(lambda: all(self.text(t).count("long-prompt-directory") == 1 and
                                "printf" not in self.text(t) for t in self.terminals))
        spin(150)

    @staticmethod
    def text(terminal):
        return terminal.get_text_format(Vte.Format.TEXT) or ""

    def drag(self, interval=8, start=180, end=760, sweeps=3):
        steps = 0
        for sweep in range(sweeps):
            first, last = (start, end) if sweep % 2 == 0 else (end, start)
            step = 4 if last > first else -4
            for position in range(first, last + step, step):
                self.paned.set_position(position)
                spin(interval)
                steps += 1
        spin(650)
        return steps

    def metrics(self):
        result = []
        for terminal, log in zip(self.terminals, self.logs):
            text = self.text(terminal)
            flat = text.replace("\n", "")
            # Use an independently known prefix so damaged text cannot redefine
            # the metric. Flatten physical wraps but retain all text fragments.
            prefix = pwd.getpwuid(os.getuid()).pw_name + "@" + socket.gethostname().split(".")[0] + ":"
            suffix = "# " if os.getuid() == 0 else "$ "
            expected = prefix + str(self.cwd) + suffix
            adjustment = terminal.get_vadjustment()
            history, _length = terminal.get_text_range_format(
                Vte.Format.TEXT, int(adjustment.get_lower()), 0,
                int(adjustment.get_upper()), -1)
            history = (history or "").replace("\n", "").strip()
            result.append(dict(copies=flat.count(prefix),
                               history_copies=history.count(prefix),
                               garbage=history != expected.strip(),
                               prompt_ends=flat.count("long-prompt-directory"),
                               nonempty_lines=sum(bool(line.strip()) for line in text.splitlines()),
                               winch=len(log.read_text().splitlines()) if log.exists() else 0,
                               columns=terminal.get_column_count(), text=text))
        return result

    def close(self):
        self.window.destroy()
        for pid in self.pids:
            try:
                os.kill(pid, signal.SIGHUP)
            except ProcessLookupError:
                pass
        def exited():
            return all(not Path(f"/proc/{pid}").exists() for pid in self.pids)
        stopped = poll(exited)
        for pid in self.pids:
            if Path(f"/proc/{pid}").exists():
                os.kill(pid, signal.SIGKILL)
        self.directory.cleanup()
        assert stopped, "bash survived window destruction"


class AllocationProbe(Gtk.DrawingArea):
    def __init__(self):
        super().__init__()
        self.allocations = []

    def do_get_preferred_width(self):
        return 1, 160

    def do_get_preferred_height(self):
        return 1, 90

    def do_size_allocate(self, allocation):
        self.allocations.append((allocation.x, allocation.y, allocation.width, allocation.height))
        self.set_allocation(allocation)


class AllocationHost(Gtk.Fixed):
    """Keep manual unit-test allocations when a child queues a new request.

    The shell harness separately exercises normal Gtk.Paned/Box negotiation.
    """

    def do_size_allocate(self, allocation):
        self.set_allocation(allocation)


def allocate(widget, width, height, x=20, y=20):
    allocation = Gdk.Rectangle()
    allocation.x, allocation.y = x, y
    allocation.width, allocation.height = width, height
    widget.size_allocate(allocation)


class ResizeGateTest(unittest.TestCase):
    def setUp(self):
        self.window = Gtk.Window()
        self.window.set_default_size(600, 320)
        self.fixed = AllocationHost()
        self.window.add(self.fixed)
        self.gate = ResizeGate()
        self.gate.SETTLE_MS = 160
        self.child = AllocationProbe()
        self.gate.add(self.child)
        self.fixed.put(self.gate, 20, 20)
        self.window.show_all()
        self.addCleanup(self.window.destroy)
        allocate(self.gate, 200, 120)
        self.gate.commit()
        self.assertTrue(poll(lambda: self.child.get_mapped()))
        self.assertTrue(poll(lambda: self.child.get_allocated_width() == 200))

    def test_first_allocation_and_realization_are_immediate(self):
        gate = ResizeGate()
        child = AllocationProbe()
        gate.add(child)
        child.show()
        gate.show()
        self.fixed.put(gate, 300, 10)
        allocate(gate, 180, 110, 300, 10)
        self.assertEqual(child.get_allocated_width(), 180)
        self.assertIsNone(gate._timer)
        self.assertTrue(gate.get_realized())
        self.assertEqual(gate.get_children(), [child])

    def test_debounce_and_reset_on_change(self):
        allocate(self.gate, 260, 130)
        first_timer = self.gate._timer
        self.assertEqual(self.child.get_allocated_width(), 200)
        spin(100)
        allocate(self.gate, 280, 140)
        self.assertNotEqual(self.gate._timer, first_timer)
        spin(100)  # Past the first deadline, before the reset deadline.
        self.assertEqual(self.child.get_allocated_width(), 200)
        self.assertTrue(poll(lambda: self.child.get_allocated_width() == 280))
        self.assertEqual(self.child.get_allocated_height(), 140)
        self.assertIsNone(self.gate._timer)

    def test_identical_allocation_does_not_postpone_commit(self):
        allocate(self.gate, 250, 130)
        timer = self.gate._timer
        spin(80)
        self.child.queue_resize()
        allocate(self.gate, 250, 130)
        self.assertEqual(self.gate._timer, timer)
        self.assertTrue(poll(lambda: self.child.get_allocated_width() == 250))

    def test_commit_and_return_to_committed_size_cancel_timeout(self):
        allocate(self.gate, 270, 140)
        self.gate.commit()
        self.assertEqual(self.child.get_allocated_width(), 270)
        self.assertIsNone(self.gate._timer)
        allocate(self.gate, 310, 150)
        allocate(self.gate, 270, 140)
        self.assertIsNone(self.gate._timer)
        spin(200)
        self.assertEqual(self.child.get_allocated_width(), 270)

    def test_destroy_cancels_live_timeout(self):
        allocate(self.gate, 250, 130)
        self.assertIsNotNone(self.gate._timer)
        self.gate.destroy()
        self.assertIsNone(self.gate._timer)
        count = len(self.child.allocations)
        self.gate.commit()
        spin(200)
        self.assertEqual(len(self.child.allocations), count)

    def test_hidden_removed_destroyed_and_replacement_children(self):
        self.child.hide()
        allocate(self.gate, 250, 130)
        self.gate.commit()
        self.assertEqual(self.child.get_allocated_width(), 200)
        self.gate.show_all()
        allocate(self.gate, 250, 130)
        self.assertTrue(poll(lambda: self.child.get_allocated_width() == 250))
        allocate(self.gate, 280, 140)
        self.gate.remove(self.child)
        self.assertIsNone(self.gate._timer)
        self.child.destroy()
        self.gate.commit()
        replacement = AllocationProbe()
        self.gate.add(replacement)
        self.gate.show_all()
        allocate(self.gate, 280, 140)
        self.assertTrue(poll(lambda: replacement.get_allocated_width() == self.gate.get_allocated_width()))
        replacement.destroy()
        allocate(self.gate, 260, 135)
        self.gate.commit()
        self.assertIsNone(self.gate.get_child())

    def test_reparent_during_timeout(self):
        allocate(self.gate, 280, 140)
        other = Gtk.Fixed()
        other_window = Gtk.Window()
        other_window.add(other)
        self.addCleanup(other_window.destroy)
        self.fixed.remove(self.gate)
        self.assertIsNone(self.gate._timer)
        other.put(self.gate, 0, 0)
        other_window.show_all()
        self.assertTrue(poll(lambda: self.child.get_mapped()))
        self.gate.commit()
        self.assertEqual(self.child.get_allocated_width(), self.gate.get_allocated_width())

    def test_minimum_natural_size_and_tiny_paned(self):
        self.assertEqual(self.gate.get_preferred_width(), (0, 160))
        self.assertEqual(self.gate.get_preferred_height(), (0, 90))
        self.assertEqual(self.gate.get_preferred_height_for_width(50), (0, 90))
        self.assertEqual(self.gate.get_preferred_width_for_height(50), (0, 160))
        self.fixed.remove(self.gate)
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.fixed.put(paned, 0, 0)
        paned.pack1(Gtk.Box(), True, False)
        paned.pack2(self.gate, True, True)
        paned.show_all()
        allocate(paned, 500, 150, 0, 0)
        paned.set_position(499)
        allocate(paned, 500, 150, 0, 0)
        self.assertTrue(poll(lambda: self.gate.get_allocated_width() <= 1))
        self.gate.commit()
        self.assertGreaterEqual(self.child.get_allocated_width(), 1)

    def test_nonpositive_allocation_and_child_margins(self):
        self.child.set_margin_start(8)
        self.child.set_margin_end(8)
        self.child.set_margin_top(8)
        self.child.set_margin_bottom(8)
        # GTK's public size_allocate rejects negatives before invoking us. Test
        # our defensive virtual method directly for that otherwise invalid input.
        allocation = Gdk.Rectangle()
        allocation.width, allocation.height = -10, 0
        self.gate.do_size_allocate(allocation)
        self.gate.commit()
        self.assertEqual(self.gate.get_allocated_width(), 1)
        self.assertGreaterEqual(self.child.get_allocated_width(), 1)
        self.assertGreaterEqual(self.child.get_allocated_height(), 1)

    def test_background_padding_and_native_child_clipping_pixels(self):
        self.gate.SETTLE_MS = 10000
        self.gate.remove(self.child)
        child = Gtk.EventBox()  # Windowed child exercises Gdk window clipping.
        provider = Gtk.CssProvider()
        provider.load_from_data(b"* { background-color: #00ff00; }")
        child.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.gate.add(child)
        self.gate.set_background("#ff0000")
        self.gate.show_all()
        allocate(self.gate, 200, 120)
        self.gate.commit()
        self.assertTrue(poll(lambda: child.get_mapped()))

        def pixel(x, y):
            pixbuf = Gdk.pixbuf_get_from_window(self.window.get_window(), x + 20, y + 20, 1, 1)
            return tuple(pixbuf.get_pixels()[:3])

        allocate(self.gate, 300, 180)
        self.assertEqual(child.get_allocated_width(), 200)
        self.assertTrue(poll(lambda: pixel(250, 30) == (255, 0, 0)))
        self.assertEqual(pixel(30, 30), (0, 255, 0))
        self.assertEqual(pixel(30, 150), (255, 0, 0))
        allocate(self.gate, 100, 70)
        self.assertEqual(child.get_allocated_width(), 200)
        self.assertTrue(poll(lambda: pixel(150, 30) != (0, 255, 0)))
        self.assertEqual(pixel(30, 30), (0, 255, 0))
        self.assertEqual(child.get_allocation().x, 0)
        self.assertEqual(child.get_allocation().y, 0)
        with self.assertRaises(ValueError):
            self.gate.set_background("not-a-colour")


class BashDragTest(unittest.TestCase):
    def test_drag_comparison(self):
        # Keep measurements visible in CI. Some bash/readline versions repair
        # every redraw themselves; never manufacture corrupt shell output to
        # make that baseline fail. Signal reduction remains independently tested.
        for interval, start, end, count in [(8, 180, 760, 1), (24, 760, 180, 3)]:
            with self.subTest(interval=interval, count=count):
                measurements = []
                for gate_type in (None, ResizeGate):
                    harness = BashDrag(gate_type=gate_type, count=count)
                    try:
                        if start != 180:
                            harness.paned.set_position(start)
                            if harness.gates:
                                spin(200)
                            spin(200)
                            for log in harness.logs:
                                log.write_text("")
                        steps = harness.drag(interval=interval, start=start, end=end)
                        metrics = harness.metrics()
                        measurements.append(metrics)
                        label = "AFTER" if gate_type else "BASELINE"
                        print(label, dict(interval=interval, steps=steps, terminals=count), metrics, flush=True)
                        if gate_type:
                            for metric in metrics:
                                self.assertEqual(metric["copies"], 1, metric)
                                self.assertEqual(metric["history_copies"], 1, metric)
                                self.assertFalse(metric["garbage"], metric)
                                self.assertEqual(metric["prompt_ends"], 1, metric)
                                self.assertLessEqual(metric["winch"], 1)
                    finally:
                        harness.close()
                for baseline, after in zip(*measurements):
                    self.assertGreater(baseline["winch"], after["winch"] + 10)
                    self.assertGreaterEqual(baseline["copies"], after["copies"])

    def test_fatal_criticals_subprocess(self):
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "tests.test_resizegate.ResizeGateTest"],
            env=dict(os.environ, G_DEBUG="fatal-criticals"),
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("Gtk-WARNING", result.stderr)
        self.assertNotIn("Gtk-CRITICAL", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
