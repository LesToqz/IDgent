"""Terminal dock bar: chips, "+" button, 3-terminal limit, maximize and animations."""
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
from idgent.terminal_bar import TerminalBar

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


class BarTest(unittest.TestCase):
    """The bar widget on its own."""

    def setUp(self):
        self.bar = TerminalBar()
        self.window = Gtk.Window()
        self.window.add(self.bar)
        self.window.show_all()
        self.addCleanup(self.window.destroy)

    def test_css_class_and_height(self):
        self.assertTrue(self.bar.get_style_context().has_class("idgent-terminal-bar"))
        self.assertTrue(poll(lambda: self.bar.get_allocated_height() > 1))
        self.assertTrue(28 <= self.bar.get_allocated_height() <= 34, self.bar.get_allocated_height())

    def test_chip_numbers_follow_position_after_delete_and_add(self):
        for pane_id in (1, 2, 3):
            self.bar.add_chip(pane_id)
        self.assertEqual(self.bar.chip_labels(), ["bash1", "bash2", "bash3"])
        self.bar.remove_chip(2)
        self.assertEqual(self.bar.chip_labels(), ["bash1", "bash2"])
        self.assertEqual(self.bar.add_chip(4), "bash3")
        self.assertEqual(self.bar.chip_labels(), ["bash1", "bash2", "bash3"])
        self.assertEqual(self.bar.chip(4).text, "bash3")

    def test_middle_insertion_and_reordering_preserve_shell_names(self):
        self.bar.add_chip(1)
        self.bar.add_chip(2, shell_name="zsh")
        self.assertEqual(self.bar.add_chip(3, index=1), "bash2")
        self.assertEqual(self.bar.chip_labels(), ["bash1", "bash2", "zsh3"])
        self.bar._chip_box.reorder_child(self.bar.chip(2).revealer, 0)
        self.assertEqual(self.bar.chip_labels(), ["zsh1", "bash2", "bash3"])
        self.assertEqual([chip.id for chip in self.bar._chips], [2, 1, 3])

    def test_fading_chip_keeps_text_without_consuming_a_number(self):
        for pane_id in (1, 2, 3):
            self.bar.add_chip(pane_id)
        self.assertTrue(poll(lambda: self.bar.chip(2).revealer.get_mapped()))
        fading = self.bar.chip(2)
        self.bar.remove_chip(2, duration=220)
        self.assertIn(fading, self.bar._closing)
        self.assertEqual(fading.text, "bash2")
        self.assertEqual(self.bar.chip(3).text, "bash2")
        self.assertEqual(self.bar.chip_labels(), ["bash1", "bash2"])
        self.assertEqual(self.bar.add_chip(4), "bash3")
        self.assertEqual(self.bar.chip_labels(), ["bash1", "bash2", "bash3"])
        self.assertEqual(fading.text, "bash2")
        self.assertTrue(poll(lambda: not self.bar._closing))
        self.assertEqual(self.bar.chip_labels(), ["bash1", "bash2", "bash3"])

    def test_chip_controls_and_signals(self):
        self.bar.add_chip(7, shell_name="zsh")
        chip = self.bar.chip(7)
        self.assertEqual(chip.text, "zsh1")
        self.assertEqual(chip.fullscreen_button.icon_name, "view-fullscreen-symbolic")
        self.assertEqual(chip.fullscreen_button.get_tooltip_text(), "Fullscreen")
        self.assertEqual(chip.delete_button.icon_name, "edit-delete-symbolic")
        self.assertEqual(chip.delete_button.get_tooltip_text(), "Kill terminal")
        self.assertEqual(self.bar.add_button.icon_name, "list-add-symbolic")
        self.assertEqual(self.bar.add_button.get_tooltip_text(), "New terminal")
        events = []
        for name in ("add-requested", "focus-requested", "fullscreen-requested", "delete-requested"):
            self.bar.connect(name, lambda _bar, *args, name=name: events.append((name, *args)))
        self.bar.add_button.clicked()
        chip.label_button.clicked()
        chip.fullscreen_button.clicked()
        chip.delete_button.clicked()
        self.assertEqual(events, [("add-requested",), ("focus-requested", 7),
                                  ("fullscreen-requested", 7), ("delete-requested", 7)])

    def test_active_and_maximized_state(self):
        self.bar.add_chip(1)
        self.bar.add_chip(2)
        self.bar.set_active(2)
        classes = [self.bar.chip(i).box.get_style_context().has_class("active") for i in (1, 2)]
        self.assertEqual(classes, [False, True])
        self.bar.set_maximized(1)
        self.assertEqual(self.bar.chip(1).fullscreen_button.icon_name, "view-restore-symbolic")
        self.assertEqual(self.bar.chip(1).fullscreen_button.get_tooltip_text(), "Restore")
        self.assertEqual(self.bar.chip(2).fullscreen_button.icon_name, "view-fullscreen-symbolic")
        self.bar.set_maximized(None)
        self.assertEqual(self.bar.chip(1).fullscreen_button.get_tooltip_text(), "Fullscreen")

    def test_set_full_marks_button(self):
        context = self.bar.add_button.get_style_context()
        self.bar.set_full(True)
        self.assertTrue(context.has_class("limit"))
        self.assertEqual(self.bar.add_button.get_tooltip_text(), LIMIT_TEXT)
        self.bar.set_full(False)
        self.assertFalse(context.has_class("limit"))
        self.assertEqual(self.bar.add_button.get_tooltip_text(), "New terminal")

    def test_text_glyph_fallback_without_icon(self):
        with patch.object(Gtk.IconTheme, "has_icon", return_value=False):
            self.bar.add_chip(1)
            button = self.bar.chip(1).delete_button
            self.assertIsInstance(button.get_child(), Gtk.Label)
            self.assertEqual(button.get_child().get_text(), "✕")
        self.assertIsInstance(self.bar.add_button.get_child(), Gtk.Image)

    def test_notice_shows_then_fades_and_restarts(self):
        self.bar.NOTICE_MS = 300
        self.bar.NOTICE_FADE_MS = 100
        self.assertFalse(self.bar.notice.get_visible())
        self.bar.show_limit_notice()
        self.assertTrue(self.bar.notice.get_visible())
        self.assertEqual(self.bar.notice.get_text(), LIMIT_TEXT)
        pump(0.2)
        self.bar.show_limit_notice()  # restarts the timer
        pump(0.2)
        self.assertTrue(self.bar.notice.get_visible())
        self.assertTrue(poll(lambda: not self.bar.notice.get_visible(), 3))

    def test_destroy_cancels_timers(self):
        self.bar.NOTICE_MS = 50
        self.bar.show_limit_notice()
        self.bar.add_chip(1)
        self.bar.remove_chip(1, duration=100)
        self.window.destroy()
        pump(0.4)  # must not touch destroyed widgets
        self.assertIsNone(self.bar._notice_timer)


class PanelFixture:
    def setUp(self):
        self.environment = patch.dict(os.environ, {"SHELL": "/bin/bash", "PROMPT_COMMAND": "", "PS1": "$ "})
        self.environment.start()
        self.directory = tempfile.TemporaryDirectory()
        self.pids: set[int] = set()
        self.windows: list[Gtk.Window] = []
        self.panel = self.dock(self.directory.name)

    def dock(self, cwd: str) -> TerminalPanel:
        window = Gtk.Window()
        window.set_default_size(900, 300)
        panel = TerminalPanel(cwd)
        panel.ANIMATION_MS = 0
        panel.bar.NOTICE_MS = 300
        panel.bar.NOTICE_FADE_MS = 100
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
        return (panel or self.panel).bar.chip_labels()

    def exercise_limit(self, panel):
        reached = []
        panel.connect("limit-reached", lambda _p: reached.append(True))
        self.assertEqual(self.labels(panel), ["bash1"])
        self.add(panel)
        self.add(panel)
        self.assertEqual(self.labels(panel), ["bash1", "bash2", "bash3"])
        self.assertTrue(panel.is_full())
        self.assertTrue(panel.bar.add_button.get_style_context().has_class("limit"))
        self.assertFalse(panel.bar.notice.get_visible())
        self.assertFalse(panel.new_terminal())
        self.assertEqual(panel.terminal_count(), MAX_TERMINALS)
        self.assertEqual(reached, [True])
        self.assertTrue(panel.bar.notice.get_visible())
        self.assertEqual(panel.bar.notice.get_text(), LIMIT_TEXT)
        self.assertTrue(poll(lambda: not panel.bar.notice.get_visible(), 3))
        self.assertTrue(panel.bar.add_button.get_style_context().has_class("limit"))
        panel.close_current()
        self.assertFalse(panel.is_full())
        self.assertFalse(panel.bar.add_button.get_style_context().has_class("limit"))
        self.assertTrue(panel.new_terminal())
        self.assertEqual(panel.terminal_count(), MAX_TERMINALS)
        self.assertEqual(reached, [True])


class PanelBarTest(PanelFixture, unittest.TestCase):
    def test_limit_three_with_notice_below_and_right_are_independent(self):
        right = self.dock(self.directory.name)
        self.exercise_limit(self.panel)
        # The first dock being full does not affect the second one.
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
        self.assertTrue(self.panel.bar.notice.get_visible())

    def test_new_terminal_returns_false_when_destroyed(self):
        panel = self.dock(self.directory.name)
        panel.destroy()
        self.assertFalse(panel.new_terminal())

    def test_chip_labels_after_delete_and_add(self):
        self.add()
        self.add()
        self.assertEqual(self.labels(), ["bash1", "bash2", "bash3"])
        second = self.panel._panes[1]
        self.panel.bar.chip(second.id).delete_button.clicked()
        self.assertEqual(self.labels(), ["bash1", "bash2"])
        self.add()
        self.assertEqual(self.labels(), ["bash1", "bash2", "bash3"])
        # Chips follow pane order: the new terminal opens right of the active one.
        self.assertEqual([self.panel.bar.chip(p.id).text for p in self.panel._panes], self.labels())

    def test_add_after_focusing_first_pane_numbers_left_to_right(self):
        first = self.panel._panes[0]
        second = self.add()
        self.panel.bar.chip(first.id).label_button.clicked()
        self.assertTrue(poll(lambda: self.windows[0].get_focus() == first.terminal))
        third = self.add()
        self.assertEqual(self.panel._panes, [first, third, second])
        self.assertEqual(self.labels(), ["bash1", "bash2", "bash3"])
        self.assertEqual([self.panel.bar.chip(p.id).text for p in self.panel._panes], self.labels())
        self.assertTrue(poll(lambda: all(self.panel.bar.chip(p.id).box.get_allocated_width() > 1
                                        for p in self.panel._panes)))
        positions = [self.panel.bar.chip(p.id).box.translate_coordinates(self.panel.bar, 0, 0)[0]
                     for p in self.panel._panes]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(len(set(positions)), 3)

    def test_plus_button_is_rightmost_and_adds(self):
        self.add()
        bar = self.panel.bar
        self.assertTrue(poll(lambda: bar.add_button.get_allocated_width() > 1))
        plus_x = bar.add_button.translate_coordinates(bar, 0, 0)[0]
        for pane in self.panel._panes:
            chip = bar.chip(pane.id)
            self.assertLess(chip.box.translate_coordinates(bar, 0, 0)[0], plus_x)
        self.assertGreater(plus_x + bar.add_button.get_allocated_width(),
                           bar.get_allocated_width() - 20)
        bar.add_button.clicked()
        self.track(self.panel._active)
        self.assertEqual(self.panel.terminal_count(), 3)
        bar.add_button.clicked()
        self.assertEqual(self.panel.terminal_count(), 3)
        self.assertTrue(bar.notice.get_visible())
        self.assertEqual(bar.notice.get_text(), LIMIT_TEXT)
        # The notice sits next to the + button, not over the chips.
        self.assertTrue(poll(lambda: bar.notice.get_allocated_width() > 1))
        notice_x = bar.notice.translate_coordinates(bar, 0, 0)[0]
        self.assertLess(notice_x, plus_x)
        last = bar.chip(self.panel._panes[-1].id).box
        self.assertGreater(notice_x, last.translate_coordinates(bar, 0, 0)[0] + last.get_allocated_width() - 1)

    def test_delete_button_kills_that_shell_only(self):
        first = self.panel._panes[0]
        second = self.add()
        third = self.add()
        pids = {id(p): p.pid for p in (first, second, third)}
        victim = self.panel._panes[1]
        victim_pid = victim.pid
        self.panel.bar.chip(victim.id).delete_button.clicked()
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
        bar = self.panel.bar
        bar.chip(panes[1].id).fullscreen_button.clicked()
        self.assertTrue(self.panel.is_maximized())
        self.assertEqual(toggled, [True])
        self.assertEqual([p.page.get_visible() for p in panes], [False, True, False])
        self.assertTrue(all(alive(p.pid) for p in panes))
        self.assertEqual(len(self.labels()), 3)
        self.assertEqual(bar.chip(panes[1].id).fullscreen_button.icon_name, "view-restore-symbolic")
        self.assertEqual(bar.chip(panes[1].id).fullscreen_button.get_tooltip_text(), "Restore")
        self.assertTrue(poll(lambda: panes[1].page.get_allocated_width() > 800))
        # Maximizing another pane switches without a second True.
        bar.chip(panes[2].id).fullscreen_button.clicked()
        self.assertEqual(toggled, [True])
        self.assertEqual([p.page.get_visible() for p in panes], [False, False, True])
        self.assertEqual(bar.chip(panes[1].id).fullscreen_button.icon_name, "view-fullscreen-symbolic")
        bar.chip(panes[2].id).fullscreen_button.clicked()
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
        self.panel.bar.chip(second.id).delete_button.clicked()
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

    def test_escape_is_not_intercepted(self):
        event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        event.state = 0
        event.keyval = Gdk.KEY_Escape
        terminal = self.panel._panes[0].terminal
        self.panel.toggle_maximize()
        self.assertFalse(self.panel._key_press(terminal, event))
        self.assertTrue(self.panel.is_maximized())

    def test_active_chip_follows_focus_and_label_click_focuses(self):
        first = self.panel._panes[0]
        second = self.add()
        bar = self.panel.bar
        active = lambda: [bar.chip(p.id).box.get_style_context().has_class("active")
                          for p in self.panel._panes]
        self.assertEqual(active(), [False, True])
        bar.chip(first.id).label_button.clicked()
        self.assertTrue(poll(lambda: self.windows[0].get_focus() == first.terminal))
        self.assertEqual(active(), [True, False])
        second.terminal.grab_focus()
        # An unfocused Xvfb toplevel never gets the real event; deliver it by hand.
        second.terminal.emit("focus-in-event", Gdk.Event.new(Gdk.EventType.FOCUS_CHANGE))
        self.assertEqual(active(), [False, True])

    def test_panel_hidden_hides_bar(self):
        self.panel.hide()
        self.assertFalse(self.panel.bar.get_mapped())


class AnimationTest(PanelFixture, unittest.TestCase):
    """Same fixture, animations on."""

    def dock(self, cwd):
        panel = super().dock(cwd)
        panel.ANIMATION_MS = 220
        return panel

    def test_add_animates_in(self):
        self.assertTrue(poll(lambda: self.panel.get_mapped()))
        self.assertTrue(self.panel.new_terminal())
        pane = self.panel._active
        chip = self.panel.bar.chip(pane.id)
        self.assertLess(pane.page.get_opacity(), 1.0)
        self.assertFalse(chip.revealer.get_child_revealed())
        self.assertEqual(self.panel.terminal_count(), 2)
        self.assertTrue(poll(lambda: pane.page.get_opacity() == 1.0 and chip.revealer.get_child_revealed(), 0.6))
        self.assertEqual(chip.revealer.get_opacity(), 1.0)
        self.track(pane)

    def test_delete_animates_out_and_last_closed_follows(self):
        second = self.add()
        events = []
        self.panel.connect("last-closed", lambda _p: events.append(True))
        first = self.panel._panes[0]
        victim_pid = second.pid
        chip = self.panel.bar.chip(second.id)
        self.panel.bar.chip(second.id).delete_button.clicked()
        # The shell is hung up at the start, the widgets linger, and the limit is freed.
        self.assertEqual(self.panel.terminal_count(), 1)
        self.assertIsNotNone(second.page.get_parent())
        self.assertIsNotNone(chip.revealer.get_parent())
        self.assertEqual(self.labels(), ["bash1"])
        self.assertTrue(poll(lambda: not alive(victim_pid)))
        self.assertTrue(poll(lambda: second.page.get_parent() is None
                             and chip.revealer.get_parent() is None, 0.6))
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
        self.assertTrue(self.panel.bar.chip(pane.id).revealer.get_child_revealed())
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
        self.panel.bar.show_limit_notice()
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
        self.assertTrue(self.panel.bar.chip(pane.id).revealer.get_child_revealed())
        self.panel.close_current()
        self.assertIsNone(pane.page.get_parent())
        self.assertEqual(self.panel.bar.chip(pane.id), None)
        self.panel.close_current()
        self.assertEqual(events, [True])


if __name__ == "__main__":
    unittest.main()
