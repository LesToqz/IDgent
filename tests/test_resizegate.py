"""Real GTK/VTE resize experiments and ResizeGate regression checks (Xvfb)."""
from __future__ import annotations

import os
from pathlib import Path
import signal
import tempfile
import time
import unittest
import warnings

os.environ["GDK_BACKEND"] = "x11"

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Vte", "2.91")
from gi.repository import GLib, Gtk, Vte

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
            # Colour escapes disappear in TEXT. A colon + absolute /tmp prompt
            # prefix survives line wrapping, unlike a complete prompt per line.
            prefix = flat.split(":/", 1)[0] + ":/"
            result.append(dict(copies=flat.count(prefix),
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


class BaselineExperiment(unittest.TestCase):
    def test_plain_drag(self):
        harness = BashDrag()
        try:
            steps = harness.drag()
            print("BASELINE", steps, harness.metrics(), flush=True)
        finally:
            harness.close()
