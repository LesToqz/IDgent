"""Window layout and command wiring against real panels."""

from pathlib import Path
import os
import signal
import subprocess
import sys
import time
import tempfile
import unittest
from unittest.mock import patch

# Keep GTK on the Xvfb display even when WAYLAND_DISPLAY is inherited.
os.environ["GDK_BACKEND"] = "x11"

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk

from idgent import settings, style
from idgent.themes import THEMES, DEFAULT_THEME_ID
from idgent.window import MainWindow


class WindowLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = Gtk.Application.get_default() or Gtk.Application(
            application_id="dev.idgent.Tests",
                                          flags=Gio.ApplicationFlags.NON_UNIQUE)
        if not cls.application.get_is_registered():
            assert cls.application.register(None)

    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory(prefix="idgent-layout-")
        self.addCleanup(self.folder.cleanup)
        config = tempfile.TemporaryDirectory(prefix="idgent-config-")
        self.addCleanup(config.cleanup)
        environment = patch.dict(os.environ, {"XDG_CONFIG_HOME": config.name})
        environment.start()
        self.addCleanup(environment.stop)
        self.window = MainWindow(self.application, self.folder.name)
        self.window.show_all()
        self.addCleanup(self.destroy_window)
        self.assertTrue(self.poll(lambda: self.window.terminal_bottom._panes[0].pid is not None))
        self.shell_pid = self.window.terminal_bottom._panes[0].pid

    @staticmethod
    def poll(predicate):
        deadline = time.monotonic() + 5
        context = GLib.MainContext.default()
        while time.monotonic() < deadline:
            while context.pending():
                context.iteration(False)
            if predicate():
                return True
            time.sleep(.01)
        return bool(predicate())

    def destroy_window(self):
        self.window.destroy()
        pid = getattr(self, "shell_pid", None)
        if pid is None:
            return

        def stopped():
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            return False

        exited = self.poll(stopped)
        if not exited:
            os.kill(pid, signal.SIGKILL)
        self.assertTrue(exited, "shell survived window destruction")

    def test_settings_theme_selection_persistence_and_reopen(self):
        view_menu = self.window.titlebar.menu_bar.get_children()[1].get_submenu()
        self.assertFalse({'Theme', 'Next Theme'} &
                         {item.get_label() for item in view_menu.get_children()})
        self.window.open_settings()
        dialog = self.window._settings_dialog
        self.assertEqual(list(dialog.theme_rows), [t.id for t in THEMES])
        self.assertEqual(dialog.theme_id, DEFAULT_THEME_ID)
        dialog.theme_list.select_row(dialog.theme_rows[THEMES[1].id])
        self.assertIs(self.window.theme, THEMES[1])
        self.assertEqual(settings.get_theme_id(), THEMES[1].id)
        other = MainWindow(self.application, self.folder.name)
        self.addCleanup(other.destroy)
        self.assertFalse(other.get_realized())
        self.assertIs(other.theme, THEMES[1])
        other.open_settings()
        self.assertEqual(other._settings_dialog.theme_id, THEMES[1].id)
        self.assertIs(style.current_theme(), THEMES[1])

    def test_theme_fallback_and_nonpersisting_switch(self):
        target = Path(os.environ["XDG_CONFIG_HOME"]) / "idgent/settings.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        for payload in ('{"theme": "unknown"}', '{broken json'):
            with self.subTest(payload=payload):
                target.write_text(payload)
                other = MainWindow(self.application, self.folder.name)
                try:
                    self.assertEqual(other.theme.id, DEFAULT_THEME_ID)
                finally:
                    other.destroy()
        settings.set_theme_id(THEMES[1].id)
        self.window.set_theme(THEMES[2].id, persist=False)
        self.assertEqual(settings.get_theme_id(), THEMES[1].id)
        self.window.set_theme("unknown")
        self.assertEqual(self.window.theme.id, DEFAULT_THEME_ID)
        self.assertEqual(settings.get_theme_id(), DEFAULT_THEME_ID)

    def test_next_theme_accelerator_cycles_and_wraps(self):
        self.window.set_theme('dark-plus', persist=False)
        self.window.open_settings()
        key, modifiers = Gtk.accelerator_parse("<Control><Alt>t")
        for theme in (*THEMES[1:], THEMES[0]):
            self.assertTrue(Gtk.accel_groups_activate(self.window, key, modifiers))
            self.assertIs(self.window.theme, theme)
            self.assertEqual(self.window._settings_dialog.theme_id, theme.id)
            self.assertEqual(settings.get_theme_id(), theme.id)

    def test_default_layout_and_title(self) -> None:
        self.assertTrue(self.window.terminal_bottom.get_visible())
        self.assertFalse(self.window.terminal_right.get_visible())
        self.assertEqual(self.window.terminal_right.terminal_count(), 0)
        self.assertEqual(self.window.dock.get_orientation(), Gtk.Orientation.HORIZONTAL)
        self.assertEqual(self.window.dock_bottom.get_orientation(), Gtk.Orientation.VERTICAL)
        self.assertIs(self.window.dock.get_child1(), self.window.dock_bottom)
        self.assertIs(self.window.dock.get_child2(), self.window.terminal_right)
        self.assertIs(self.window.dock_bottom.get_child2(), self.window.terminal_bottom)
        self.assertEqual(self.window.get_default_size(), (1200, 750))
        self.assertEqual(self.window.get_title(), "IDgent")
        self.assertEqual(self.window.titlebar.title_label.get_text(), "IDgent")
        self.assertTrue(self.window.terminal_right.stacked)
        self.assertFalse(self.window.terminal_bottom.stacked)
        self.assertEqual(self.window.terminal_right._body.get_orientation(), Gtk.Orientation.VERTICAL)
        self.assertEqual(self.window.terminal_bottom._body.get_orientation(), Gtk.Orientation.HORIZONTAL)
        self.assertIs(self.window.get_child(), self.window.overlay)
        self.assertEqual(self.window.overlay.get_child().get_children(), [self.window.sidebar])
        self.assertEqual(self.window.sidebar.get_position(), 300)

    def settle_layout(self) -> None:
        deadline = time.monotonic() + .2
        self.assertTrue(self.poll(lambda: time.monotonic() >= deadline))

    def assert_right_terminal_size(self, window) -> None:
        width = window.terminal_right.get_allocated_width()
        dock_width = window.dock.get_allocated_width()
        self.assertGreaterEqual(width, 380)
        self.assertLessEqual(width, dock_width * .6)
        self.assertAlmostEqual(width, max(380, round(dock_width * .4)), delta=1)
        self.assertGreaterEqual(window.editor.get_allocated_width(), 300)
        self.assertGreaterEqual(window.editor.get_allocated_height(), 150)

    def test_both_docks_allocated(self) -> None:
        self.window.toggle_terminal_right()
        self.settle_layout()
        self.assertTrue(self.window.terminal_bottom.get_visible())
        self.assertTrue(self.window.terminal_right.get_visible())
        self.assert_right_terminal_size(self.window)
        height = self.window.terminal_bottom.get_allocated_height()
        self.assertGreaterEqual(height, 150)
        self.assertAlmostEqual(height, self.window.dock_bottom.get_allocated_height() * .3,
                               delta=1)

    def test_show_right_before_realize(self) -> None:
        window = MainWindow(self.application, self.folder.name)
        self.addCleanup(window.destroy)
        self.assertFalse(window.get_realized())
        window.toggle_terminal_right()
        window.show_all()
        self.settle_layout()
        self.assert_right_terminal_size(window)

    def test_hidden_dock_stays_hidden_after_show_all(self) -> None:
        self.window.toggle_terminal_bottom()
        self.window.toggle_terminal_right()
        self.window.show_all()
        self.assertFalse(self.window.terminal_bottom.get_visible())
        self.assertTrue(self.window.terminal_right.get_visible())
        self.window.toggle_explorer()
        self.settle_layout()
        self.assert_right_terminal_size(self.window)
        self.window.toggle_terminal_bottom()
        self.assertTrue(self.window.terminal_bottom.get_visible())

    def test_last_closed_hides_each_dock_and_show_respawns(self) -> None:
        self.window.toggle_terminal_right()
        for panel, toggle in ((self.window.terminal_right, self.window.toggle_terminal_right),
                              (self.window.terminal_bottom, self.window.toggle_terminal_bottom)):
            panel.close_current()
            self.assertTrue(self.poll(lambda: not panel.get_visible()))
            self.assertEqual(panel.terminal_count(), 0)
            toggle()
            self.assertTrue(panel.get_visible())
            self.assertEqual(panel.terminal_count(), 1)

    def fullscreen_button(self, panel):
        return panel._panes[0].header.fullscreen_button

    def layout_state(self):
        window = self.window
        return ([widget.get_visible() for widget in
                 (window.explorer, window.editor, window.dock_bottom,
                  window.terminal_bottom, window.terminal_right)],
                [paned.get_position() for paned in
                 (window.sidebar, window.dock, window.dock_bottom)])

    def test_header_fullscreen_restores_visibility_positions_and_survives_show_all(self):
        window = self.window
        for right in (False, True):
            for hidden_explorer in (False, True):
                with self.subTest(right=right, hidden_explorer=hidden_explorer):
                    if window.explorer.get_visible() == hidden_explorer:
                        window.toggle_explorer()
                    if right and not window.terminal_right.get_visible():
                        window.toggle_terminal_right()
                    panel = window.terminal_right if right else window.terminal_bottom
                    panel.new_terminal()
                    self.settle_layout()
                    before = self.layout_state()
                    self.fullscreen_button(panel).clicked()
                    window.show_all()
                    self.settle_layout()
                    self.assertTrue(panel.is_maximized())
                    self.assertTrue(window.titlebar.get_visible())
                    self.assertFalse(window.explorer.get_visible())
                    if right:
                        self.assertFalse(window.dock_bottom.get_visible())
                    else:
                        self.assertFalse(window.editor.get_visible())
                        self.assertFalse(window.terminal_right.get_visible())
                    self.assertFalse(panel._panes[1].frame.get_visible())
                    self.assertEqual(panel.get_allocated_width(), window.sidebar.get_allocated_width())
                    self.assertEqual(panel.get_allocated_height(), window.sidebar.get_allocated_height())
                    self.fullscreen_button(panel).clicked()
                    self.settle_layout()
                    self.assertEqual(self.layout_state(), before)
                    self.assertTrue(panel._panes[1].frame.get_visible())

    def test_fullscreen_command_target_and_empty_layout(self):
        window = self.window
        window.toggle_terminal_bottom()
        window.toggle_terminal_fullscreen()
        self.assertTrue(window.terminal_bottom.is_maximized())
        window.toggle_terminal_fullscreen()
        window.toggle_terminal_right()
        window.terminal_right.focus_terminal()
        window.toggle_terminal_fullscreen()
        self.assertTrue(window.terminal_right.is_maximized())
        window.toggle_terminal_fullscreen()
        window.toggle_terminal_bottom()
        window.explorer.tree.grab_focus()
        window.toggle_terminal_fullscreen()
        self.assertTrue(window.terminal_right.is_maximized())
        window.toggle_terminal_fullscreen()

    def test_layout_commands_restore_fullscreen_first(self):
        window = self.window
        for command, widget in ((window.toggle_explorer, window.explorer),
                                (window.toggle_terminal_bottom, window.terminal_bottom),
                                (window.toggle_terminal_right, window.terminal_right)):
            before = widget.get_visible()
            window.toggle_terminal_fullscreen()
            command()
            self.assertIsNone(window._fullscreen_panel)
            self.assertEqual(widget.get_visible(), not before)
        window.toggle_terminal_fullscreen()
        with patch('idgent.window.Gtk.FileChooserDialog') as chooser:
            chooser.return_value.run.return_value = Gtk.ResponseType.CANCEL
            window.open_folder()
        self.assertIsNone(window._fullscreen_panel)

    def test_last_close_in_fullscreen_restores_and_hides_dock(self):
        window = self.window
        for panel in (window.terminal_bottom, window.terminal_right):
            window._show_terminal(panel)
            self.settle_layout()
            panel.toggle_maximize()
            panel.close_current()
            self.assertTrue(self.poll(lambda: not panel.get_visible()))
            self.assertIsNone(window._fullscreen_panel)
            self.assertTrue(window.editor.get_visible())
            self.assertTrue(window.explorer.get_visible())
            self.assertTrue(window.dock_bottom.get_visible())

    def test_show_during_last_exit_animation_respawns(self):
        panel = self.window.terminal_bottom
        panel.close_current()
        self.window._show_terminal(panel)
        self.settle_layout()
        self.settle_layout()
        self.assertTrue(panel.get_visible())
        self.assertEqual(panel.terminal_count(), 1)

    def test_each_dock_limit_and_split_toast(self):
        self.window.toggle_terminal_right()
        for panel in (self.window.terminal_bottom, self.window.terminal_right):
            panel.set_max_panes(3)
            # The dock + opens a chooser; choose Terminal to create a pane.
            panel.add_button.choose("terminal")
            panel.add_button.choose("terminal")
            self.assertEqual(panel.terminal_count(), 3)
            panel.add_button.choose("terminal")
            self.assertEqual(panel.terminal_count(), 3)
            self.assertTrue(panel.toast.get_visible())
            self.assertEqual(panel.toast.get_text(), 'Cannot add (limit 3)')
            panel.toast.hide()
            panel.focus_terminal()
            self.window.split_terminal()
            self.assertTrue(panel.get_visible())
            self.assertEqual(panel.terminal_count(), 3)
            self.assertTrue(panel.toast.get_visible())
        self.assertEqual(self.window.terminal_bottom.terminal_count(), 3)
        self.assertEqual(self.window.terminal_right.terminal_count(), 3)

    def test_right_dock_stacks_and_bottom_dock_is_side_by_side(self):
        window = self.window
        window.toggle_terminal_right()
        for panel in (window.terminal_bottom, window.terminal_right):
            panel.new_terminal()
            panel.new_terminal()
            self.assertEqual(panel.header_labels(), ["bash1", "bash2", "bash3"])
        self.settle_layout()
        for panel, axis in ((window.terminal_bottom, 0), (window.terminal_right, 1)):
            pages = [p.page.translate_coordinates(panel, 0, 0) for p in panel._panes]
            self.assertEqual(len({xy[1 - axis] for xy in pages}), 1)
            self.assertEqual([xy[axis] for xy in pages], sorted(xy[axis] for xy in pages))
            self.assertEqual(len({xy[axis] for xy in pages}), 3)
            for pane in panel._panes:
                header = pane.header.translate_coordinates(panel, 0, 0)
                content = pane.content.translate_coordinates(panel, 0, 0)
                self.assertEqual(header[1] + pane.header.get_allocated_height(), content[1])
                self.assertEqual(header[0], content[0])
                self.assertEqual(pane.header.get_allocated_width(), pane.content.get_allocated_width())

    def content_top(self) -> int:
        """y of the editor/terminal area (right under the title bar) in window coordinates."""
        return self.window.sidebar.translate_coordinates(self.window, 0, 0)[1]

    def handle_width(self, paned: Gtk.Paned) -> int:
        # Measure the CSS allocation, including the transparent hit borders.
        handle = paned.get_handle_window()
        return (handle.get_width() if paned.get_orientation() == Gtk.Orientation.HORIZONTAL
                else handle.get_height())

    def test_bottom_divider_collapses_editor_and_restores(self) -> None:
        window = self.window
        self.settle_layout()
        before = window.terminal_bottom.get_allocated_height()
        window.dock_bottom.set_position(0)
        self.settle_layout()
        self.assertFalse(window.editor.get_mapped())
        top = window.terminal_bottom.translate_coordinates(window, 0, 0)[1] - self.content_top()
        self.assertLessEqual(top, self.handle_width(window.dock_bottom))
        self.assertEqual(window.terminal_bottom.get_allocated_height(),
                         window.dock_bottom.get_allocated_height() - top)
        self.assertGreater(window.terminal_bottom.get_allocated_height(), before)
        self.assertEqual(window.sidebar.get_position(), 300)
        header = window.terminal_bottom._panes[0].header
        self.assertTrue(header.get_mapped())
        self.assertEqual(header.translate_coordinates(window, 0, 0)[1] - self.content_top(), top)
        window.dock_bottom.set_position(300)
        self.settle_layout()
        self.assertTrue(window.editor.get_mapped())
        self.assertGreaterEqual(window.editor.get_allocated_height(), 150)
        self.assertGreaterEqual(window.editor.get_allocated_width(), 300)
        self.assertGreaterEqual(window.terminal_bottom.get_allocated_height(), 150)

    def test_right_divider_collapses_left_part_and_restores(self) -> None:
        window = self.window
        window.toggle_terminal_right()
        self.settle_layout()
        window.dock.set_position(0)
        self.settle_layout()
        self.assertFalse(window.dock_bottom.get_mapped())
        explorer_edge = window.explorer.get_allocated_width() + self.handle_width(window.sidebar)
        right = window.terminal_right.translate_coordinates(window.sidebar, 0, 0)[0]
        # Only the two divider handles separate the right dock from the explorer.
        self.assertLessEqual(right - explorer_edge, self.handle_width(window.dock) + 1)
        self.assertEqual(right + window.terminal_right.get_allocated_width(),
                         window.sidebar.get_allocated_width())
        self.assertEqual(window.explorer.get_allocated_width(), 300)
        self.assertEqual(window.sidebar.get_position(), 300)
        self.assertTrue(window.terminal_right.get_mapped())
        window.dock.set_position(window.dock.get_allocated_width() // 2)
        self.settle_layout()
        self.assertTrue(window.dock_bottom.get_mapped())
        self.assertGreaterEqual(window.editor.get_allocated_width(), 300)
        self.assertGreaterEqual(window.terminal_right.get_allocated_width(), 380)
        self.assertGreaterEqual(window.editor.get_allocated_height(), 150)

    def test_both_collapsed_then_restored(self) -> None:
        window = self.window
        window.toggle_terminal_right()
        self.settle_layout()
        window.dock_bottom.set_position(0)
        window.dock.set_position(0)
        self.settle_layout()
        self.assertFalse(window.editor.get_mapped())
        self.assertTrue(window.terminal_right.get_mapped())
        window.dock_bottom.set_position(300)
        window.dock.set_position(window.dock.get_allocated_width() // 2)
        self.settle_layout()
        self.assertTrue(window.editor.get_mapped())
        self.assertTrue(window.terminal_bottom.get_mapped())
        self.assertGreater(window.editor.get_allocated_height(), 100)

    def test_collapse_then_fullscreen_restores_positions(self) -> None:
        window = self.window
        window.toggle_terminal_right()
        self.settle_layout()
        window.dock_bottom.set_position(0)
        window.dock.set_position(0)
        self.settle_layout()
        before = self.layout_state()
        window.terminal_bottom.toggle_maximize()
        self.settle_layout()
        window.terminal_bottom.restore()
        self.settle_layout()
        self.assertEqual(self.layout_state(), before)

    COLLAPSE_SCRIPT = """
import os, tempfile, time
os.environ.update(GDK_BACKEND="x11", SHELL="/bin/bash", PS1="$ ")
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk
from idgent import style
from idgent.window import MainWindow

def pump(seconds):
    end = time.monotonic() + seconds
    context = GLib.MainContext.default()
    while time.monotonic() < end:
        while context.pending():
            context.iteration(False)
        time.sleep(0.01)

app = Gtk.Application(application_id="dev.idgent.Collapse", flags=Gio.ApplicationFlags.NON_UNIQUE)
app.register(None)
style.apply_style()
window = MainWindow(app, tempfile.mkdtemp())
window.set_default_size(1400, 850)
window.show_all()
pump(0.4)
window.toggle_terminal_right()
for panel in (window.terminal_bottom, window.terminal_right):
    while not panel.is_full():
        panel.new_terminal()
        pump(0.15)
for bottom, right in ((0, 700), (100000, 700), (300, 0), (300, 100000), (0, 0), (300, 700)):
    window.dock_bottom.set_position(bottom)
    window.dock.set_position(right)
    pump(0.3)
window.terminal_right.toggle_maximize()
pump(0.2)
window.terminal_right.restore()
pump(0.2)
window.destroy()
print("scenario-ok")
"""

    def test_collapse_scenario_logs_no_gtk_critical(self) -> None:
        root = Path(__file__).resolve().parent.parent
        env = dict(os.environ, G_DEBUG="fatal-criticals", PYTHONPATH=str(root))
        result = subprocess.run([sys.executable, "-W", "ignore", "-c", self.COLLAPSE_SCRIPT],
                                env=env, cwd=root, capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("scenario-ok", result.stdout)
        self.assertNotIn("CRITICAL", result.stderr)

    def test_explorer_toggle(self) -> None:
        self.window.toggle_explorer()
        self.assertFalse(self.window.explorer.get_visible())
        self.window.toggle_explorer()
        self.assertTrue(self.window.explorer.get_visible())

    def test_explorer_signals(self) -> None:
        with patch.object(self.window.editor, "open_file") as opened, \
             patch.object(self.window.editor, "on_path_renamed") as renamed, \
             patch.object(self.window.editor, "on_path_deleted") as deleted:
            self.window.explorer.emit("file-activated", "/tmp/a")
            self.window.explorer.emit("path-renamed", "/tmp/a", "/tmp/b")
            self.window.explorer.emit("path-deleted", "/tmp/b")
            opened.assert_called_once_with("/tmp/a")
            renamed.assert_called_once_with("/tmp/a", "/tmp/b")
            deleted.assert_called_once_with("/tmp/b")

    def test_folder_change(self) -> None:
        with tempfile.TemporaryDirectory(prefix="idgent-new-root-") as root, \
             patch.object(self.window.explorer, "set_root") as explorer, \
             patch.object(self.window.terminal_bottom, "set_cwd") as terminal, \
             patch.object(self.window.terminal_right, "set_cwd") as right:
            self.window.set_root(root)
            explorer.assert_called_once_with(root)
            terminal.assert_called_once_with(root)
            right.assert_called_once_with(root)
            self.assertEqual(self.window.get_title(), "IDgent")
            self.assertEqual(self.window.titlebar.title_label.get_text(), "IDgent")

    def test_quit_confirmation(self) -> None:
        self.assertFalse(self.window._on_delete(self.window, None))
        with patch.object(self.window.editor, "has_unsaved", return_value=True), \
             patch.object(self.window, "_ask_quit") as ask:
            ask.return_value = Gtk.ResponseType.CANCEL
            self.assertTrue(self.window._on_delete(self.window, None))
            ask.return_value = Gtk.ResponseType.REJECT
            self.assertFalse(self.window._on_delete(self.window, None))
            ask.return_value = Gtk.ResponseType.ACCEPT
            with patch.object(self.window.editor, "save_all", return_value=False):
                self.assertTrue(self.window._on_delete(self.window, None))
            with patch.object(self.window.editor, "has_unsaved", side_effect=[True, False]), \
                 patch.object(self.window.editor, "save_all", return_value=True):
                self.assertFalse(self.window._on_delete(self.window, None))


class WindowResizeRegressionTests(unittest.TestCase):
    """Real app docks, real shells; the ungated run guards the reproduction."""

    def test_both_dock_drags_preserve_six_prompts(self):
        from idgent import style
        from idgent.terminal import TerminalPanel, Vte
        from tests.test_resizegate import BashDrag, PS1, poll, spin

        application = Gtk.Application.get_default() or Gtk.Application(
            application_id="dev.idgent.ResizeRegression", flags=Gio.ApplicationFlags.NON_UNIQUE)
        if not application.get_is_registered():
            self.assertTrue(application.register(None))
        style.apply_style()
        measurements = []
        original_spawn = Vte.Terminal.spawn_async
        with tempfile.TemporaryDirectory(prefix="idgent-window-resize-") as folder:
            cwd = Path(folder) / "projects" / "long-prompt-directory"
            cwd.mkdir(parents=True)
            for gated in (False, True):
                pending = []

                def defer_spawn(terminal, *args, **kwargs):
                    pending.append((terminal, args, kwargs))

                with patch.object(TerminalPanel, "RESIZE_GATE", gated), \
                     patch.object(TerminalPanel, "ANIMATION_MS", 0), \
                     patch.object(Vte.Terminal, "spawn_async", defer_spawn):
                    window = MainWindow(application, str(cwd))
                    window.set_default_size(1400, 850)
                    window.show_all()
                    window.toggle_terminal_right()
                    for panel in (window.terminal_bottom, window.terminal_right):
                        while not panel.is_full():
                            panel.new_terminal()
                    window.dock.set_position(400)
                    window.dock_bottom.set_position(350)
                    spin(350)
                terminals = [p.terminal for panel in (window.terminal_bottom, window.terminal_right)
                             for p in panel._panes]
                harness = BashDrag.__new__(BashDrag)
                harness.terminals, harness.cwd = terminals, cwd
                harness.logs = [Path(folder) / f"unused-{i}" for i in range(6)]
                try:
                    for terminal, args, kwargs in pending:
                        args = list(args)
                        args[2] = ["/bin/bash", "--norc", "-i"]
                        environment = dict(os.environ, PS1=PS1, TERM="xterm-256color",
                                           COLORTERM="truecolor",
                                           PROMPT_COMMAND="printf '\\n%.0s' {1..100}; unset PROMPT_COMMAND")
                        args[3] = [f"{k}={v}" for k, v in environment.items()]
                        original_spawn(terminal, *args, **kwargs)
                    self.assertTrue(poll(lambda: all("long-prompt-directory" in BashDrag.text(t)
                                                    for t in terminals)))
                    spin(150)
                    initial = harness.metrics()
                    self.assertTrue(all(m["history_copies"] == 1 and not m["garbage"]
                                        for m in initial), initial)
                    for dock, start, end in ((window.dock, 400, 900),
                                             (window.dock_bottom, 350, 650)):
                        for sweep in range(3):
                            first, last = (start, end) if sweep % 2 == 0 else (end, start)
                            step = 4 if last > first else -4
                            for position in range(first, last + step, step):
                                dock.set_position(position)
                                spin(8)
                        spin(250)
                    metrics = harness.metrics()
                    measurements.append(metrics)
                    print("WINDOW", "AFTER" if gated else "BASELINE",
                          [{k: v for k, v in m.items() if k != "text"} for m in metrics],
                          flush=True)
                    shots = Path(__file__).resolve().parent.parent / ".ceo" / "shots"
                    shots.mkdir(parents=True, exist_ok=True)
                    pixbuf = Gdk.pixbuf_get_from_window(window.get_window(), 0, 0,
                                                       window.get_allocated_width(),
                                                       window.get_allocated_height())
                    pixbuf.savev(str(shots / ("after-drag-gated.png" if gated else
                                             "after-drag-control.png")), "png", [], [])
                    if gated:
                        for metric in metrics:
                            self.assertEqual(metric["copies"], 1, metric)
                            self.assertEqual(metric["history_copies"], 1, metric)
                            self.assertFalse(metric["garbage"], metric)
                            self.assertEqual(metric["prompt_ends"], 1, metric)
                finally:
                    pids = [p.pid for panel in (window.terminal_bottom, window.terminal_right)
                            for p in panel._panes if p.pid]
                    window.destroy()
                    self.assertTrue(poll(lambda: all(not Path(f"/proc/{pid}").exists()
                                                    for pid in pids)))
        before = sum(m["history_copies"] for m in measurements[0])
        after = sum(m["history_copies"] for m in measurements[1])
        self.assertGreater(before, after, measurements)


if __name__ == "__main__":
    unittest.main()
