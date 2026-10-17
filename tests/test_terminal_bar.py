"""Pane headers, "+" button, limit toast, maximize and animations, in both orientations."""
from __future__ import annotations

import os
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
from gi.repository import Gdk, GLib, Gtk

from idgent import style
from idgent.terminal import MAX_TERMINALS, TerminalPanel
from idgent.terminal_bar import AddButton, LimitToast, PaneHeader

LIMIT_TEXT = "Cannot add terminal (limit 3)"


def setUpModule() -> None:
    warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"gi\.events")
    style.apply_style()


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


def pump(seconds: float) -> None:
    poll(lambda: False, seconds)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def rect(widget: Gtk.Widget, ref: Gtk.Widget) -> tuple[int, int, int, int]:
    x, y = widget.translate_coordinates(ref, 0, 0)
    return x, y, widget.get_allocated_width(), widget.get_allocated_height()


def overlaps(a, b) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


class HeaderTest(unittest.TestCase):
    """The header widget on its own."""

    def setUp(self):
        self.header = PaneHeader()
        self.header.set_valign(Gtk.Align.START)
        self.window = Gtk.Window()
        self.window.set_default_size(300, 100)
        self.window.add(self.header)
        self.window.show_all()
        self.addCleanup(self.window.destroy)

    def test_css_class_and_height(self):
        self.assertTrue(self.header.get_style_context().has_class("idgent-terminal-header"))
        self.assertTrue(poll(lambda: self.header.get_allocated_height() > 1))
        self.assertTrue(28 <= self.header.get_allocated_height() <= 34,
                        self.header.get_allocated_height())

    def test_label_numbering_and_shell_name(self):
        self.assertEqual(self.header.text, "bash1")
        self.header.set_number(3)
        self.assertEqual(self.header.text, "bash3")
        zsh = PaneHeader("zsh")
        zsh.set_number(2)
        self.assertEqual(zsh.text, "zsh2")
        zsh.destroy()

    def test_controls_and_signals(self):
        header = self.header
        self.assertEqual(header.fullscreen_button.icon_name, "view-fullscreen-symbolic")
        self.assertEqual(header.fullscreen_button.get_tooltip_text(), "Fullscreen")
        self.assertEqual(header.delete_button.icon_name, "edit-delete-symbolic")
        self.assertEqual(header.delete_button.get_tooltip_text(), "Kill terminal")
        events = []
        for name in ("focus-requested", "fullscreen-requested", "delete-requested"):
            header.connect(name, lambda _h, name=name: events.append(name))
        header.label_button.clicked()
        header.fullscreen_button.clicked()
        header.delete_button.clicked()
        self.assertEqual(events, ["focus-requested", "fullscreen-requested", "delete-requested"])

    def test_active_corner_and_maximized_state(self):
        context = self.header.get_style_context()
        self.header.set_active(True)
        self.header.set_corner(True)
        self.assertTrue(context.has_class("active") and context.has_class("corner"))
        self.header.set_active(False)
        self.header.set_corner(False)
        self.assertFalse(context.has_class("active") or context.has_class("corner"))
        self.header.set_maximized(True)
        self.assertEqual(self.header.fullscreen_button.icon_name, "view-restore-symbolic")
        self.assertEqual(self.header.fullscreen_button.get_tooltip_text(), "Restore")
        self.header.set_maximized(False)
        self.assertEqual(self.header.fullscreen_button.icon_name, "view-fullscreen-symbolic")
        self.assertEqual(self.header.fullscreen_button.get_tooltip_text(), "Fullscreen")

    def test_text_glyph_fallback_without_icon(self):
        with patch.object(Gtk.IconTheme, "has_icon", return_value=False):
            header = PaneHeader()
            self.assertIsInstance(header.delete_button.get_child(), Gtk.Label)
            self.assertEqual(header.delete_button.get_child().get_text(), "✕")
            header.destroy()
        self.assertIsInstance(self.header.delete_button.get_child(), Gtk.Image)

    def test_add_button_limit_state(self):
        button = AddButton()
        context = button.get_style_context()
        self.assertEqual(button.icon_name, "list-add-symbolic")
        self.assertEqual(button.get_tooltip_text(), "New terminal")
        self.assertEqual((button.get_halign(), button.get_valign()),
                         (Gtk.Align.END, Gtk.Align.START))
        button.set_full(True)
        self.assertTrue(context.has_class("limit"))
        self.assertEqual(button.get_tooltip_text(), LIMIT_TEXT)
        button.set_full(False)
        self.assertFalse(context.has_class("limit"))
        self.assertEqual(button.get_tooltip_text(), "New terminal")
        button.destroy()


class ToastTest(unittest.TestCase):
    def setUp(self):
        self.toast = LimitToast()
        self.toast.SHOW_MS = 300
        self.toast.FADE_MS = 100
        self.window = Gtk.Window()
        overlay = Gtk.Overlay()
        overlay.add(Gtk.Label(label="body"))
        overlay.add_overlay(self.toast)
        self.window.add(overlay)
        self.window.show_all()
        self.addCleanup(self.window.destroy)

    def test_shows_then_fades_and_restarts(self):
        self.assertFalse(self.toast.get_visible())
        self.toast.popup()
        self.assertTrue(self.toast.get_visible())
        self.assertEqual(self.toast.get_text(), LIMIT_TEXT)
        pump(0.2)
        self.toast.popup()  # restarts the timer
        pump(0.2)
        self.assertTrue(self.toast.get_visible())
        self.assertTrue(poll(lambda: not self.toast.get_visible(), 3))
        self.assertEqual(self.toast.get_opacity(), 1.0)

    def test_destroy_cancels_timer(self):
        self.toast.SHOW_MS = 50
        self.toast.popup()
        self.window.destroy()
        pump(0.4)  # must not touch destroyed widgets
        self.assertIsNone(self.toast._timer)


class PanelFixture:
    stacked = False

    def setUp(self):
        self.environment = patch.dict(os.environ, {"SHELL": "/bin/bash", "PROMPT_COMMAND": "", "PS1": "$ "})
        self.environment.start()
        self.directory = tempfile.TemporaryDirectory()
        self.pids: set[int] = set()
        self.windows: list[Gtk.Window] = []
        self.panel = self.dock(self.directory.name)

    def dock(self, cwd: str) -> TerminalPanel:
        window = Gtk.Window()
        window.set_default_size(900, 700 if self.stacked else 300)
        panel = TerminalPanel(cwd, stacked=self.stacked)
        panel.ANIMATION_MS = 0
        panel.toast.SHOW_MS = 300
        panel.toast.FADE_MS = 100
        window.add(panel)
        window.show_all()
        self.windows.append(window)
        self.track(panel._panes[0])
        return panel

    def tearDown(self):
        for window in self.windows:
            window.destroy()
        stopped = poll(lambda: all(not alive(pid) for pid in self.pids))
        for pid in self.pids:
            if alive(pid):
                os.kill(pid, signal.SIGKILL)
        self.directory.cleanup()
        self.environment.stop()
        self.assertTrue(stopped, "shell process survived panel destruction")

    def track(self, pane):
        self.assertTrue(poll(lambda: pane.pid is not None), "shell did not spawn")
        self.pids.add(pane.pid)
        return pane

    def add(self, panel=None) -> object:
        panel = panel or self.panel
        self.assertTrue(panel.new_terminal())
        return self.track(panel._active)

    def labels(self, panel=None) -> list[str]:
        return (panel or self.panel).header_labels()

    def settle(self, panel=None):
        panel = panel or self.panel
        self.assertTrue(poll(lambda: all(p.content.get_allocated_height() > 1
                                         and p.content.get_allocated_width() > 1
                                         for p in panel._panes)))

    def assert_headers_above_terminals(self, panel=None):
        panel = panel or self.panel
        self.settle(panel)
        for pane in panel._panes:
            header, content = rect(pane.header, panel), rect(pane.content, panel)
            self.assertEqual(header[1] + header[3], content[1], pane.header.text)
            self.assertEqual(header[0], content[0])
            self.assertEqual(header[2], content[2])
            terminal = rect(pane.terminal, panel)
            self.assertGreaterEqual(terminal[1], content[1])
            self.assertLessEqual(terminal[0] + terminal[2], content[0] + content[2])

    def exercise_limit(self, panel):
        reached = []
        panel.connect("limit-reached", lambda _p: reached.append(True))
        self.assertEqual(self.labels(panel), ["bash1"])
        self.add(panel)
        self.add(panel)
        self.assertEqual(self.labels(panel), ["bash1", "bash2", "bash3"])
        self.assertTrue(panel.is_full())
        self.assertTrue(panel.add_button.get_style_context().has_class("limit"))
        self.assertEqual(panel.add_button.get_tooltip_text(), LIMIT_TEXT)
        self.assertFalse(panel.toast.get_visible())
        self.assertFalse(panel.new_terminal())
        self.assertEqual(panel.terminal_count(), MAX_TERMINALS)
        self.assertEqual(reached, [True])
        self.assertTrue(panel.toast.get_visible())
        self.assertEqual(panel.toast.get_text(), LIMIT_TEXT)
        self.assertTrue(poll(lambda: not panel.toast.get_visible(), 3))
        self.assertTrue(panel.add_button.get_style_context().has_class("limit"))
        panel.close_current()
        self.assertFalse(panel.is_full())
        self.assertFalse(panel.add_button.get_style_context().has_class("limit"))
        self.assertTrue(panel.new_terminal())
        self.assertEqual(panel.terminal_count(), MAX_TERMINALS)
        self.assertEqual(reached, [True])


class PanelBarMixin(PanelFixture):
    """Behaviour that must hold for side-by-side and stacked docks alike."""

    def test_limit_three_with_toast_and_independent_docks(self):
        right = self.dock(self.directory.name)
        self.exercise_limit(self.panel)
        self.assertTrue(self.panel.is_full())
        self.assertEqual(right.terminal_count(), 1)
        self.exercise_limit(right)
        self.assertEqual((self.panel.terminal_count(), right.terminal_count()), (3, 3))

    def test_limit_applies_to_every_add_path(self):
        self.add()
        self.add()
        event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        event.state = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK
        for key in (Gdk.KEY_t, Gdk.KEY_percent):
            event.keyval = key
            self.assertTrue(self.panel._key_press(self.panel._active.terminal, event))
            self.assertEqual(self.panel.terminal_count(), 3)
        terminal = self.panel._panes[0].terminal
        button = Gdk.EventButton()
        button.type = Gdk.EventType.BUTTON_PRESS
        button.button = 3
        with patch.object(Gtk.Menu, "popup_at_pointer"):
            self.panel._button_press(terminal, button)
        menu = next(iter(Gtk.Menu.get_for_attach_widget(terminal)))
        menu.get_children()[-2].activate()
        self.assertEqual(self.panel.terminal_count(), 3)
        menu.destroy()
        self.assertTrue(self.panel.toast.get_visible())

    def test_new_terminal_returns_false_when_destroyed(self):
        panel = self.dock(self.directory.name)
        panel.destroy()
        self.assertFalse(panel.new_terminal())

    def test_labels_follow_position_after_delete_and_add(self):
        self.add()
        self.add()
        self.assertEqual(self.labels(), ["bash1", "bash2", "bash3"])
        self.panel._panes[1].header.delete_button.clicked()
        self.assertEqual(self.labels(), ["bash1", "bash2"])
        self.add()
        self.assertEqual(self.labels(), ["bash1", "bash2", "bash3"])
        self.assertEqual([p.header.text for p in self.panel._panes], self.labels())

    def test_insert_in_the_middle_numbers_by_position(self):
        first = self.panel._panes[0]
        second = self.add()
        first.header.label_button.clicked()
        self.assertTrue(poll(lambda: self.windows[0].get_focus() == first.terminal))
        third = self.add()
        self.assertEqual(self.panel._panes, [first, third, second])
        self.assertEqual(self.labels(), ["bash1", "bash2", "bash3"])
        self.assert_headers_above_terminals()
        index = 1 if self.stacked else 0
        starts = [rect(p.header, self.panel)[index] for p in self.panel._panes]
        self.assertEqual(starts, sorted(starts))
        self.assertEqual(len(set(starts)), 3)

    def test_headers_sit_directly_above_their_terminals(self):
        self.assert_headers_above_terminals()
        self.add()
        self.add()
        self.assert_headers_above_terminals()
        self.panel._panes[1].header.delete_button.clicked()
        self.assert_headers_above_terminals()
        self.panel._panes[0].header.label_button.clicked()
        self.add()  # middle insertion
        self.assert_headers_above_terminals()
        self.assertEqual(len(self.panel._panes), 3)

    def test_plus_is_in_the_top_right_corner_and_clear_of_header_controls(self):
        self.add()
        panel = self.panel
        self.settle()
        for count in (2, 3):
            plus = rect(panel.add_button, panel)
            self.assertLessEqual(plus[1], 4)
            self.assertLessEqual(plus[1] + plus[3], panel.HEADER_HEIGHT)
            self.assertGreaterEqual(plus[0] + plus[2], panel.get_allocated_width() - 8)
            for pane in panel._panes:
                for widget in (pane.header.label_button, pane.header.fullscreen_button,
                               pane.header.delete_button):
                    self.assertFalse(overlaps(plus, rect(widget, panel)),
                                     f"{pane.header.text} {widget}")
            corner = panel._panes[0] if self.stacked else panel._panes[-1]
            self.assertTrue(corner.header.get_style_context().has_class("corner"))
            self.assertEqual([p.header.get_style_context().has_class("corner")
                              for p in panel._panes].count(True), 1)
            if count == 2:
                panel.add_button.clicked()
                self.track(panel._active)
                self.assertEqual(panel.terminal_count(), 3)
                self.settle()
        panel.add_button.clicked()
        self.assertEqual(panel.terminal_count(), 3)

    def test_corner_follows_pane_changes_and_maximize(self):
        panel = self.panel
        self.add()
        self.add()
        self.settle()
        corner = (lambda: panel._panes[0] if self.stacked else panel._panes[-1])
        panel._panes[0 if self.stacked else -1].header.delete_button.clicked()
        self.settle()
        plus = rect(panel.add_button, panel)
        for pane in panel._panes:
            self.assertFalse(overlaps(plus, rect(pane.header.delete_button, panel)))
        self.assertTrue(corner().header.get_style_context().has_class("corner"))
        # A maximized pane fills the dock, so it must carry the corner padding.
        other = panel._panes[1 if self.stacked else 0]
        panel.toggle_maximize(other)
        self.settle()
        plus = rect(panel.add_button, panel)
        self.assertTrue(other.header.get_style_context().has_class("corner"))
        self.assertFalse(overlaps(plus, rect(other.header.delete_button, panel)))
        panel.restore()
        self.assertTrue(corner().header.get_style_context().has_class("corner"))

    def test_toast_sits_under_the_header_row_over_the_terminals(self):
        self.add()
        self.add()
        panel = self.panel
        self.settle()
        panel.toast.SHOW_MS = 5000
        self.assertFalse(panel.new_terminal())
        self.assertTrue(poll(lambda: panel.toast.get_allocated_width() > 1))
        toast = rect(panel.toast, panel)
        self.assertEqual(panel.toast.get_text(), LIMIT_TEXT)
        self.assertGreaterEqual(toast[1], panel.HEADER_HEIGHT)
        self.assertLess(toast[1], panel.HEADER_HEIGHT + 20)
        self.assertGreaterEqual(toast[0] + toast[2], panel.get_allocated_width() - 24)
        self.assertLessEqual(toast[0] + toast[2], panel.get_allocated_width())
        top_pane = panel._panes[0 if self.stacked else -1]
        self.assertTrue(overlaps(toast, rect(top_pane.content, panel)))
        # It does not swallow clicks aimed at the terminal below it.
        self.assertTrue(panel.overlay.get_overlay_pass_through(panel.toast))

    def test_delete_button_kills_that_shell_only(self):
        first = self.panel._panes[0]
        second = self.add()
        third = self.add()
        pids = {id(p): p.pid for p in (first, second, third)}
        victim = self.panel._panes[1]
        victim_pid = victim.pid
        victim.header.delete_button.clicked()
        self.assertTrue(poll(lambda: not alive(victim_pid)))
        self.assertEqual(self.panel.terminal_count(), 2)
        self.assertNotIn(victim, self.panel._panes)
        self.assertIsNone(victim.page.get_parent())
        for pane in self.panel._panes:
            self.assertTrue(alive(pane.pid))
            self.assertEqual(pane.pid, pids[id(pane)])
        self.assertEqual(self.labels(), ["bash1", "bash2"])

    def test_fullscreen_button_maximizes_and_restores(self):
        self.add()
        self.add()
        toggled = []
        self.panel.connect("maximize-toggled", lambda _p, value: toggled.append(value))
        panes = list(self.panel._panes)
        panes[1].header.fullscreen_button.clicked()
        self.assertTrue(self.panel.is_maximized())
        self.assertEqual(toggled, [True])
        self.assertEqual([p.page.get_visible() for p in panes], [False, True, False])
        self.assertTrue(all(alive(p.pid) for p in panes))
        self.assertEqual(len(self.labels()), 3)
        self.assertEqual(panes[1].header.fullscreen_button.icon_name, "view-restore-symbolic")
        self.assertEqual(panes[1].header.fullscreen_button.get_tooltip_text(), "Restore")
        self.assertTrue(poll(lambda: panes[1].page.get_allocated_width() > 800))
        self.assertTrue(poll(lambda: panes[1].page.get_allocated_height()
                             >= self.panel.get_allocated_height() - 2))
        # Maximizing another pane switches without a second True.
        panes[2].header.fullscreen_button.clicked()
        self.assertEqual(toggled, [True])
        self.assertEqual([p.page.get_visible() for p in panes], [False, False, True])
        self.assertEqual(panes[1].header.fullscreen_button.icon_name, "view-fullscreen-symbolic")
        panes[2].header.fullscreen_button.clicked()
        self.assertFalse(self.panel.is_maximized())
        self.assertEqual(toggled, [True, False])
        self.assertTrue(all(p.page.get_visible() for p in panes))
        # A window-wide show_all() must not resurrect hidden panes.
        self.panel.toggle_maximize()
        self.windows[0].show_all()
        self.assertEqual(sum(p.page.get_visible() for p in panes), 1)
        self.panel.restore()
        self.assertEqual(toggled, [True, False, True, False])
        self.panel.restore()
        self.assertEqual(toggled, [True, False, True, False])

    def test_delete_or_add_while_maximized_restores_first(self):
        self.add()
        toggled = []
        self.panel.connect("maximize-toggled", lambda _p, value: toggled.append(value))
        first, second = self.panel._panes
        self.panel.toggle_maximize(second)
        second.header.delete_button.clicked()
        self.assertEqual(toggled, [True, False])
        self.assertFalse(self.panel.is_maximized())
        self.assertTrue(first.page.get_visible())
        self.panel.toggle_maximize(first)
        self.assertEqual(toggled, [True, False, True])
        self.assertTrue(self.panel.new_terminal())
        self.assertEqual(toggled, [True, False, True, False])
        self.assertEqual(self.panel.terminal_count(), 2)
        self.track(self.panel._active)
        self.assertTrue(all(p.page.get_visible() for p in self.panel._panes))

    def test_dividers_between_panes_only(self):
        self.add()
        self.add()
        self.settle()
        self.assertEqual([p.divider.get_visible() for p in self.panel._panes], [True, True, False])
        orientation = Gtk.Orientation.HORIZONTAL if self.stacked else Gtk.Orientation.VERTICAL
        self.assertTrue(all(p.divider.get_orientation() == orientation for p in self.panel._panes))
        divider = rect(self.panel._panes[0].divider, self.panel)
        if self.stacked:
            self.assertEqual(divider[3], 1)
            self.assertEqual(divider[2], self.panel.get_allocated_width())
        else:
            self.assertEqual(divider[2], 1)
            self.assertEqual(divider[3], self.panel.get_allocated_height())
        self.panel.toggle_maximize(self.panel._panes[1])
        self.assertFalse(any(p.divider.get_visible() for p in self.panel._panes))
        self.panel.restore()
        self.panel._panes[2].header.delete_button.clicked()
        self.assertEqual([p.divider.get_visible() for p in self.panel._panes], [True, False])

    def test_escape_is_not_intercepted(self):
        event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        event.state = 0
        event.keyval = Gdk.KEY_Escape
        terminal = self.panel._panes[0].terminal
        self.panel.toggle_maximize()
        self.assertFalse(self.panel._key_press(terminal, event))
        self.assertTrue(self.panel.is_maximized())

    def test_active_header_follows_focus_and_label_click_focuses(self):
        first = self.panel._panes[0]
        second = self.add()
        active = lambda: [p.header.get_style_context().has_class("active")
                          for p in self.panel._panes]
        self.assertEqual(active(), [False, True])
        first.header.label_button.clicked()
        self.assertTrue(poll(lambda: self.windows[0].get_focus() == first.terminal))
        self.assertEqual(active(), [True, False])
        second.terminal.grab_focus()
        # An unfocused Xvfb toplevel never gets the real event; deliver it by hand.
        second.terminal.emit("focus-in-event", Gdk.Event.new(Gdk.EventType.FOCUS_CHANGE))
        self.assertEqual(active(), [False, True])

    def test_panel_hidden_hides_overlay_widgets(self):
        self.panel.hide()
        self.assertFalse(self.panel.add_button.get_mapped())


class SideBySideBarTest(PanelBarMixin, unittest.TestCase):
    stacked = False


class StackedBarTest(PanelBarMixin, unittest.TestCase):
    stacked = True


class AnimationMixin(PanelFixture):
    """Same fixture, animations on."""

    def dock(self, cwd):
        panel = super().dock(cwd)
        panel.ANIMATION_MS = 220
        return panel

    def test_add_animates_in(self):
        self.assertTrue(poll(lambda: self.panel.get_mapped()))
        self.assertTrue(self.panel.new_terminal())
        pane = self.panel._active
        self.assertLess(pane.page.get_opacity(), 1.0)
        self.assertEqual(self.panel.terminal_count(), 2)
        self.assertEqual(self.labels(), ["bash1", "bash2"])
        self.assertTrue(poll(lambda: pane.page.get_opacity() == 1.0, 0.6))
        self.track(pane)

    def test_delete_animates_out_and_last_closed_follows(self):
        second = self.add()
        events = []
        self.panel.connect("last-closed", lambda _p: events.append(True))
        first = self.panel._panes[0]
        victim_pid = second.pid
        second.header.delete_button.clicked()
        # The shell is hung up at the start, the widgets linger, and the limit is freed.
        self.assertEqual(self.panel.terminal_count(), 1)
        self.assertIsNotNone(second.page.get_parent())
        self.assertEqual(self.labels(), ["bash1"])
        self.assertEqual(second.header.text, "bash2")
        self.assertTrue(poll(lambda: not alive(victim_pid)))
        self.assertTrue(poll(lambda: second.page.get_parent() is None, 0.6))
        self.assertEqual(events, [])
        # Closing the final pane emits last-closed only after the exit animation.
        first_pid = first.pid
        self.panel.close_current()
        self.assertEqual(self.panel.terminal_count(), 0)
        self.assertEqual(events, [])
        self.assertIsNotNone(first.page.get_parent())
        self.assertTrue(poll(lambda: events == [True], 0.8))
        self.assertIsNone(first.page.get_parent())
        pump(0.3)
        self.assertEqual(events, [True])
        self.assertTrue(poll(lambda: not alive(first_pid)))

    def test_fading_pane_does_not_consume_a_number(self):
        self.add()
        self.add()
        fading = self.panel._panes[1]
        fading.header.delete_button.clicked()
        self.assertEqual(self.labels(), ["bash1", "bash2"])
        self.assertEqual(self.panel._panes[1].header.text, "bash2")
        self.assertTrue(self.panel.new_terminal())
        self.track(self.panel._active)
        self.assertEqual(self.labels(), ["bash1", "bash2", "bash3"])
        self.assertTrue(poll(lambda: not self.panel._closing, 1))
        self.assertEqual(self.labels(), ["bash1", "bash2", "bash3"])

    def test_headers_above_terminals_after_animations(self):
        self.add()
        self.add()
        self.assertTrue(poll(lambda: all(p.page.get_opacity() == 1 for p in self.panel._panes), 1))
        self.panel._panes[0].header.delete_button.clicked()
        self.assertTrue(poll(lambda: not self.panel._closing, 1))
        self.assert_headers_above_terminals()

    def test_limit_is_freed_while_pane_is_closing(self):
        self.add()
        self.add()
        self.panel.close_current()
        self.assertEqual(self.panel.terminal_count(), 2)
        self.assertTrue(self.panel.new_terminal())
        self.assertEqual(self.panel.terminal_count(), 3)
        self.track(self.panel._active)
        self.assertTrue(poll(lambda: not self.panel._closing, 1))

    def test_shell_exit_animates(self):
        second = self.add()
        second.terminal.feed_child(b"exit\n")
        self.assertTrue(poll(lambda: self.panel.terminal_count() == 1))
        self.assertTrue(poll(lambda: second.page.get_parent() is None, 1))

    def test_unmapped_panel_skips_animation(self):
        self.panel.hide()
        self.assertFalse(self.panel.get_mapped())
        self.assertTrue(self.panel.new_terminal())
        pane = self.panel._active
        self.assertEqual(pane.page.get_opacity(), 1.0)
        self.track(pane)
        self.panel.close_current()
        self.assertIsNone(pane.page.get_parent())
        self.assertEqual(self.panel._closing, [])

    def test_destroy_during_animation_is_quiet(self):
        second = self.add()
        second_pid = second.pid
        events = []
        self.panel.connect("last-closed", lambda _p: events.append(True))
        self.panel.close_current()
        self.assertTrue(self.panel.new_terminal())  # entering pane while another exits
        self.panel.toast.popup()
        self.windows[0].destroy()
        pump(0.6)
        self.assertEqual(events, [])
        self.assertEqual(self.panel._closing, [])
        self.assertTrue(poll(lambda: not alive(second_pid)))

    def test_synchronous_when_animation_disabled(self):
        self.panel.ANIMATION_MS = 0
        events = []
        self.panel.connect("last-closed", lambda _p: events.append(True))
        self.assertTrue(self.panel.new_terminal())
        pane = self.panel._active
        self.track(pane)
        self.assertEqual(pane.page.get_opacity(), 1.0)
        self.panel.close_current()
        self.assertIsNone(pane.page.get_parent())
        self.assertEqual(self.labels(), ["bash1"])
        self.panel.close_current()
        self.assertEqual(events, [True])


class SideBySideAnimationTest(AnimationMixin, unittest.TestCase):
    stacked = False


class StackedAnimationTest(AnimationMixin, unittest.TestCase):
    stacked = True


if __name__ == "__main__":
    unittest.main()
