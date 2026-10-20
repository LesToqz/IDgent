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

from idgent import motion, style
from idgent.terminal import MAX_TERMINALS, TerminalPanel
from idgent.terminal_bar import (GLYPH_OPACITY, AddButton, LimitToast, PaneHeader, build_css,
                                 cancel_fade, fade)
from idgent.themes import THEMES, _contrast, get_theme

LIMIT_TEXT = "Cannot add (limit 3)"


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


def pixel(widget: Gtk.Widget, x: int, y: int) -> tuple[int, int, int]:
    """Rendered colour at widget-relative (x, y), read back from the toplevel window."""
    toplevel = widget.get_toplevel()
    wx, wy = widget.translate_coordinates(toplevel, x, y)
    pixbuf = Gdk.pixbuf_get_from_window(toplevel.get_window(), wx, wy, 1, 1)
    return tuple(pixbuf.get_pixels()[:3])


def hex_rgb(value: str) -> tuple[int, int, int]:
    return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))


def near(a, b, tolerance: int = 3) -> bool:
    return all(abs(x - y) <= tolerance for x, y in zip(a, b))


def rgba(color: Gdk.RGBA) -> tuple[int, int, int, float]:
    return (round(color.red * 255), round(color.green * 255), round(color.blue * 255),
            round(color.alpha, 3))


class ThemedTest(unittest.TestCase):
    """Applies IDgent Dark for the test and restores the default theme afterwards."""

    theme_id = "idgent-dark"

    def setUp(self):
        self.theme = get_theme(self.theme_id)
        self.ui = self.theme.ui
        style.apply_theme(self.theme)
        self.addCleanup(style.apply_style)
        self.addCleanup(motion.set_reduce_motion, False)


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
        self.assertEqual(button.get_tooltip_text(), "New pane")
        self.assertEqual((button.get_halign(), button.get_valign()),
                         (Gtk.Align.END, Gtk.Align.START))
        button.set_full(True)
        self.assertTrue(context.has_class("limit"))
        self.assertEqual(button.get_tooltip_text(), LIMIT_TEXT)
        button.set_full(False)
        self.assertFalse(context.has_class("limit"))
        self.assertEqual(button.get_tooltip_text(), "New pane")
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


class ChromeCssTest(unittest.TestCase):
    def test_css_parses_for_every_theme_without_state_transitions(self):
        for theme in THEMES:
            with self.subTest(theme=theme.id):
                css = build_css(theme.ui)
                provider = Gtk.CssProvider()
                errors = []
                provider.connect("parsing-error", lambda _p, _s, error: errors.append(str(error)))
                provider.load_from_data(css.encode())
                self.assertEqual(errors, [])
                # Hover/active states are instant; glyph opacity is the only motion (StateFader).
                self.assertNotIn("transition", css.replace("transition: none;", ""))
                self.assertIn('.idgent-pane-handle:hover .idgent-terminal-sep', css)
                self.assertIn('.idgent-pane-handle:active .idgent-terminal-sep', css)
                self.assertIn('.idgent-pane-handle:focus .idgent-terminal-sep', css)
                self.assertIn(f"background: {theme.ui['accent']}", css)
                self.assertIn(f"background: alpha({theme.ui['overlay']}, 0.067)", css)
                self.assertIn(f"inset 0 -2px {theme.ui['accent']}", css)

    def test_notice_text_is_legible_on_black_in_every_theme(self):
        import re
        for theme in THEMES:
            with self.subTest(theme=theme.id):
                css = build_css(theme.ui)
                rule = re.search(r"\.idgent-terminal-toast \{([^}]*)\}", css)[1]
                colour = re.search(r"[^-]color: (#[0-9a-f]{6})", rule)[1]
                self.assertGreaterEqual(_contrast(colour, "#1a1a1a"), 4.5)
                if _contrast(theme.ui["warning"], "#1a1a1a") >= 4.5:
                    self.assertEqual(colour, theme.ui["warning"])


class HeaderChromeTest(ThemedTest):
    """Pane header and clickable icons in the sidebar tab strips."""

    def setUp(self):
        super().setUp()
        pointer = Gdk.Display.get_default().get_default_seat().get_pointer()
        screen, pointer_x, pointer_y = pointer.get_position()
        # Pixel probes need an unhovered header, regardless of earlier tests.
        # Restore the pointer after destroying this fixture's window.
        self.addCleanup(pointer.warp, screen, pointer_x, pointer_y)
        pointer.warp(screen, screen.get_width() - 1, screen.get_height() - 1)
        self.window = Gtk.Window()
        self.window.set_default_size(400, 100)
        self.addCleanup(self.window.destroy)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.inactive, self.active = PaneHeader(), PaneHeader()
        for header in (self.inactive, self.active):
            box.pack_start(header, False, False, 0)
        self.active.set_active(True)
        self.add_button = AddButton()
        box.pack_start(self.add_button, False, False, 0)
        self.window.add(box)
        self.window.show_all()
        self.assertTrue(poll(lambda: self.active.get_allocated_height() > 1))

    def test_compact_secondary_strip_with_13px_labels(self):
        for header in (self.inactive, self.active):
            self.assertTrue(28 <= header.get_allocated_height() <= 32, header.get_allocated_height())
            context = header.get_style_context()
            self.assertEqual(rgba(context.get_background_color(Gtk.StateFlags.NORMAL)),
                             hex_rgb(self.ui["sidebar_bg"]) + (1.0,))
            label = header.label_button.get_style_context()
            self.assertEqual(label.get_property("font-size", Gtk.StateFlags.NORMAL), 13)
        self.assertEqual(self.inactive.get_allocated_height(), self.active.get_allocated_height())
        muted = self.inactive.label_button.get_style_context().get_color(Gtk.StateFlags.NORMAL)
        normal = self.active.label_button.get_style_context().get_color(Gtk.StateFlags.NORMAL)
        self.assertEqual(rgba(muted)[:3], hex_rgb(self.ui["muted"]))
        self.assertEqual(rgba(normal)[:3], hex_rgb(self.ui["foreground"]))
        self.assertTrue(poll(lambda: near(pixel(self.inactive, 2, 2), hex_rgb(self.ui["sidebar_bg"]))))

    def test_underline_only_on_the_focused_active_pane(self):
        accent = hex_rgb(self.ui["accent"])

        def underline(header):
            button = header.label_button
            height = button.get_allocated_height()
            return [pixel(button, button.get_allocated_width() // 2, height - row) for row in (1, 2, 3)]

        # Active but unfocused: normal label, no underline.
        self.assertTrue(poll(lambda: not near(underline(self.active)[0], accent, 40), 1))
        self.active.set_focused(True)
        self.inactive.set_focused(False)
        self.assertTrue(poll(lambda: all(near(p, accent) for p in underline(self.active)[:2])),
                        underline(self.active))
        # Exactly 2 px thick, and absent from the other header.
        self.assertFalse(near(underline(self.active)[2], accent, 40))
        self.assertFalse(any(near(p, accent, 40) for p in underline(self.inactive)))
        self.active.set_focused(False)
        self.assertTrue(poll(lambda: not near(underline(self.active)[0], accent, 40)))
        # A focused but inactive pane (e.g. while closing) gets no underline either.
        self.inactive.set_focused(True)
        pump(0.1)
        self.assertFalse(any(near(p, accent, 40) for p in underline(self.inactive)))

    def test_clickable_icon_states(self):
        overlay = hex_rgb(self.ui["overlay"])
        for button in (self.active.fullscreen_button, self.active.delete_button):
            context = button.get_style_context()
            self.assertEqual(context.get_property("border-radius", Gtk.StateFlags.NORMAL), 4)
            self.assertEqual(rgba(context.get_background_color(Gtk.StateFlags.NORMAL))[3], 0)
            self.assertEqual(rgba(context.get_color(Gtk.StateFlags.NORMAL))[:3], hex_rgb(self.ui["muted"]))
        fullscreen = self.active.fullscreen_button.get_style_context()
        self.assertEqual(rgba(fullscreen.get_background_color(Gtk.StateFlags.PRELIGHT)), overlay + (0.067,))
        self.assertEqual(rgba(fullscreen.get_color(Gtk.StateFlags.PRELIGHT))[:3], hex_rgb(self.ui["foreground"]))
        delete = self.active.delete_button.get_style_context()
        danger = hex_rgb(self.ui["danger"])
        self.assertEqual(rgba(delete.get_background_color(Gtk.StateFlags.PRELIGHT)), danger + (0.15,))
        self.assertEqual(rgba(delete.get_color(Gtk.StateFlags.PRELIGHT))[:3], danger)
        # The hover background is drawn at once (pixel probe on the realised button).
        button = self.active.fullscreen_button
        base = hex_rgb(self.ui["sidebar_bg"])
        # Left of the centred 16 px glyph and clear of the 4 px rounded corners.
        probe = lambda: pixel(button, 2, button.get_allocated_height() // 2)
        self.assertTrue(poll(lambda: near(probe(), base)), probe())
        button.set_state_flags(Gtk.StateFlags.PRELIGHT, False)
        blended = tuple(round(b + (o - b) * 0.067) for b, o in zip(base, overlay))
        self.assertTrue(poll(lambda: near(probe(), blended, 2), 0.3), probe())
        self.assertNotEqual(blended, base)

    def test_glyph_opacity_fades_on_hover(self):
        for button in (self.active.fullscreen_button, self.active.delete_button, self.add_button):
            with self.subTest(button=button.icon_name):
                glyph = button.get_child()
                self.assertAlmostEqual(glyph.get_opacity(), GLYPH_OPACITY, places=2)
                started = time.monotonic()
                button.emit("enter-notify-event", Gdk.Event.new(Gdk.EventType.ENTER_NOTIFY))
                self.assertLess(glyph.get_opacity(), 1.0)
                self.assertTrue(poll(lambda: glyph.get_opacity() == 1.0, 1))
                elapsed = time.monotonic() - started
                self.assertGreater(elapsed, 0.08)
                self.assertLess(elapsed, 0.6)
                button.emit("leave-notify-event", Gdk.Event.new(Gdk.EventType.LEAVE_NOTIFY))
                self.assertTrue(poll(lambda: abs(glyph.get_opacity() - GLYPH_OPACITY) < 0.01, 1))
        # A replaced icon keeps the current glyph opacity.
        self.active.set_maximized(True)
        self.assertAlmostEqual(self.active.fullscreen_button.get_child().get_opacity(), GLYPH_OPACITY, places=2)

    def test_glyph_hover_is_instant_with_reduced_motion(self):
        motion.set_reduce_motion(True)
        button = self.active.delete_button
        button.emit("enter-notify-event", Gdk.Event.new(Gdk.EventType.ENTER_NOTIFY))
        self.assertEqual(button.get_child().get_opacity(), 1.0)
        button.emit("leave-notify-event", Gdk.Event.new(Gdk.EventType.LEAVE_NOTIFY))
        self.assertAlmostEqual(button.get_child().get_opacity(), GLYPH_OPACITY, places=2)


class NoticeToastTest(ThemedTest):
    """The limit toast notice: style, motion and 4 s auto-hide."""

    def setUp(self):
        super().setUp()
        self.window = Gtk.Window()
        self.window.set_default_size(400, 200)
        self.addCleanup(self.window.destroy)
        overlay = Gtk.Overlay()
        overlay.add(Gtk.Label(label="body"))
        self.toast = LimitToast()
        self.toast.set_margin_top(36)
        self.toast.set_margin_end(10)
        overlay.add_overlay(self.toast)
        self.window.add(overlay)
        self.window.show_all()
        self.assertTrue(poll(lambda: self.window.get_mapped()))

    def popup_with_fake_clock(self):
        """popup(), capturing the auto-hide timer instead of waiting for it."""
        timers = []
        real = GLib.timeout_add

        def capture(interval, callback, *args):
            timers.append((interval, callback))
            return real(3_600_000, callback, *args)

        with patch.object(GLib, "timeout_add", capture):
            self.toast.popup()
        self.assertEqual(len(timers), 1)
        return timers[0]

    def test_notice_style(self):
        context = self.toast.get_style_context()
        self.assertEqual(rgba(context.get_background_color(Gtk.StateFlags.NORMAL)), (0, 0, 0, 0.9))
        self.assertEqual(context.get_property("border-radius", Gtk.StateFlags.NORMAL), 8)
        self.assertEqual(context.get_property("font-size", Gtk.StateFlags.NORMAL), 13)
        padding = context.get_padding(Gtk.StateFlags.NORMAL)
        self.assertEqual((padding.top, padding.right, padding.bottom, padding.left), (8, 12, 8, 12))
        self.assertEqual(context.get_border(Gtk.StateFlags.NORMAL).left, 0)
        self.assertEqual(rgba(context.get_color(Gtk.StateFlags.NORMAL))[:3], hex_rgb(self.ui["warning"]))
        self.assertEqual(self.toast.get_text(), LIMIT_TEXT)

    def test_timings_follow_the_spec(self):
        self.assertEqual((LimitToast.SHOW_MS, LimitToast.ENTER_MS, LimitToast.FADE_MS),
                         (motion.DURATIONS["notice_hold"], motion.DURATIONS["travel"],
                          motion.DURATIONS["state"]))
        interval, _callback = self.popup_with_fake_clock()
        self.assertEqual(interval, motion.DURATIONS["notice_hold"])

    def test_slides_in_then_fades_out_and_hides(self):
        interval, expire = self.popup_with_fake_clock()
        # Entrance: starts transparent, SLIDE_PX lower, and settles within ~100 ms.
        self.assertTrue(self.toast.get_visible())
        self.assertEqual(self.toast.get_opacity(), 0.0)
        self.assertEqual(self.toast.get_margin_top(), 36 + LimitToast.SLIDE_PX)
        started = time.monotonic()
        self.assertTrue(poll(lambda: self.toast.get_opacity() == 1.0
                             and self.toast.get_margin_top() == 36, 1))
        self.assertGreater(time.monotonic() - started, 0.05)
        # Geometry: top-right, under the margins, 8 px corners drawn over the body.
        self.assertTrue(poll(lambda: self.toast.get_allocated_width() > 1))
        x, y = self.toast.translate_coordinates(self.window, 0, 0)
        width = self.toast.get_allocated_width()
        self.assertEqual((y, x + width), (36, self.window.get_allocated_width() - 10))
        self.assertLessEqual(width, 300)
        self.assertTrue(poll(lambda: max(pixel(self.toast, width // 2, 2)) < 40))
        bg = hex_rgb(self.ui["bg"])
        self.assertTrue(near(pixel(self.toast, 0, 0), bg, 25), pixel(self.toast, 0, 0))
        # The "clock" reaches 4 s: the notice fades out over ~120 ms, then hides.
        started = time.monotonic()
        self.assertFalse(expire())
        self.assertTrue(self.toast.get_visible())
        self.assertTrue(poll(lambda: not self.toast.get_visible(), 1))
        self.assertGreater(time.monotonic() - started, 0.08)
        self.assertEqual(self.toast.get_opacity(), 1.0)
        self.assertEqual(self.toast.get_margin_top(), 36)

    def test_repeat_during_exit_brings_it_back(self):
        _interval, expire = self.popup_with_fake_clock()
        self.assertTrue(poll(lambda: self.toast.get_opacity() == 1.0, 1))
        expire()
        pump(0.05)
        self.toast.popup()
        self.assertTrue(poll(lambda: self.toast.get_opacity() == 1.0, 1))
        pump(motion.DURATIONS["travel"] / 1000)
        self.assertTrue(self.toast.get_visible())
        self.assertEqual(self.toast.get_margin_top(), 36)

    def test_reduced_motion_is_instant(self):
        motion.set_reduce_motion(True)
        _interval, expire = self.popup_with_fake_clock()
        self.assertTrue(self.toast.get_visible())
        self.assertEqual((self.toast.get_opacity(), self.toast.get_margin_top()), (1.0, 36))
        expire()
        self.assertFalse(self.toast.get_visible())
        self.assertEqual(self.toast.get_opacity(), 1.0)

    def test_unmap_settles_a_running_notice(self):
        self.toast.popup()
        self.window.hide()
        self.assertFalse(self.toast.get_visible())
        self.assertIsNone(self.toast._timer)
        self.assertEqual((self.toast.get_opacity(), self.toast.get_margin_top()), (1.0, 36))


class FadeWrapperTest(ThemedTest):
    """terminal_bar.fade() is a compatibility wrapper over motion.animate."""

    def setUp(self):
        super().setUp()
        self.window = Gtk.Window()
        self.addCleanup(self.window.destroy)
        self.box = Gtk.Box()
        self.box.set_size_request(50, 50)
        self.window.add(self.box)
        self.window.show_all()
        self.assertTrue(poll(lambda: self.box.get_mapped()))

    def test_animates_and_calls_done(self):
        done = []
        fade(self.box, 0.0, 1.0, 150, lambda: done.append(True))
        self.assertEqual(self.box.get_opacity(), 0.0)
        self.assertTrue(poll(lambda: 0 < self.box.get_opacity() < 1, 1))
        self.assertTrue(poll(lambda: done == [True], 1))
        self.assertEqual(self.box.get_opacity(), 1.0)

    def test_cancel_leaves_the_current_opacity(self):
        fade(self.box, 1.0, 0.0, 300)
        self.assertTrue(poll(lambda: self.box.get_opacity() < 0.9, 1))
        cancel_fade(self.box)
        value = self.box.get_opacity()
        pump(0.4)
        self.assertEqual(self.box.get_opacity(), value)
        self.assertGreater(value, 0)

    def test_jumps_when_unmapped_disabled_or_reduced(self):
        done = []
        fade(self.box, 1.0, 0.0, 0, lambda: done.append(0))
        self.assertEqual((self.box.get_opacity(), done), (0.0, [0]))
        motion.set_reduce_motion(True)
        fade(self.box, 0.0, 1.0, 200, lambda: done.append(1))
        self.assertEqual((self.box.get_opacity(), done), (1.0, [0, 1]))
        motion.set_reduce_motion(False)
        self.window.hide()
        fade(self.box, 1.0, 0.5, 200, lambda: done.append(2))
        self.assertAlmostEqual(self.box.get_opacity(), 0.5, places=2)
        self.assertEqual(done, [0, 1, 2])

    def test_unmap_mid_fade_settles_at_the_end_value(self):
        done = []
        fade(self.box, 0.0, 1.0, 400, lambda: done.append(True))
        self.assertTrue(poll(lambda: self.box.get_opacity() > 0, 1))
        self.window.hide()
        self.assertEqual((self.box.get_opacity(), done), (1.0, [True]))
        self.window.show()
        pump(0.2)
        self.assertEqual(done, [True])


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
                panel.add_button.choose("terminal")
                self.track(panel._active)
                self.assertEqual(panel.terminal_count(), 3)
                self.settle()
        panel.add_button.choose("terminal")
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
        self.assertIsNone(victim.frame.get_parent())
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
        self.assertEqual([p.frame.get_visible() for p in panes], [False, True, False])
        self.assertTrue(all(alive(p.pid) for p in panes))
        self.assertEqual(len(self.labels()), 3)
        self.assertEqual(panes[1].header.fullscreen_button.icon_name, "view-restore-symbolic")
        self.assertEqual(panes[1].header.fullscreen_button.get_tooltip_text(), "Restore")
        self.assertTrue(poll(lambda: panes[1].frame.get_allocated_width() > 800))
        self.assertTrue(poll(lambda: panes[1].frame.get_allocated_height()
                             >= self.panel.get_allocated_height() - 2))
        # Maximizing another pane switches without a second True.
        panes[2].header.fullscreen_button.clicked()
        self.assertEqual(toggled, [True])
        self.assertEqual([p.frame.get_visible() for p in panes], [False, False, True])
        self.assertEqual(panes[1].header.fullscreen_button.icon_name, "view-fullscreen-symbolic")
        panes[2].header.fullscreen_button.clicked()
        self.assertFalse(self.panel.is_maximized())
        self.assertEqual(toggled, [True, False])
        self.assertTrue(all(p.frame.get_visible() for p in panes))
        # A window-wide show_all() must not resurrect hidden panes.
        self.panel.toggle_maximize()
        self.windows[0].show_all()
        self.assertEqual(sum(p.frame.get_visible() for p in panes), 1)
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
        self.assertTrue(first.frame.get_visible())
        self.panel.toggle_maximize(first)
        self.assertEqual(toggled, [True, False, True])
        self.assertTrue(self.panel.new_terminal())
        self.assertEqual(toggled, [True, False, True, False])
        self.assertEqual(self.panel.terminal_count(), 2)
        self.track(self.panel._active)
        self.assertTrue(all(p.frame.get_visible() for p in self.panel._panes))

    def test_dividers_between_panes_only(self):
        self.add()
        self.add()
        self.settle()
        handles = self.panel._body.handles
        self.assertEqual(len(handles), 2)
        self.assertTrue(all(h.get_visible() for h in handles))
        orientation = Gtk.Orientation.HORIZONTAL if self.stacked else Gtk.Orientation.VERTICAL
        self.assertTrue(all(h.line.get_orientation() == orientation for h in handles))
        divider = rect(handles[0], self.panel)
        line = rect(handles[0].line, self.panel)
        if self.stacked:
            self.assertEqual(divider[3], 6)
            self.assertEqual(line[3], 1)
            self.assertEqual(divider[2], self.panel.get_allocated_width())
        else:
            self.assertEqual(divider[2], 6)
            self.assertEqual(line[2], 1)
            self.assertEqual(divider[3], self.panel.get_allocated_height())
        self.panel.toggle_maximize(self.panel._panes[1])
        self.assertFalse(any(h.get_visible() for h in handles))
        self.panel.restore()
        self.panel._panes[2].header.delete_button.clicked()
        self.assertEqual(len(self.panel._body.handles), 1)
        self.assertTrue(self.panel._body.handles[0].get_visible())

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

    def test_focus_moves_the_header_underline(self):
        first, second = self.panel._panes[0], self.add()
        event = Gdk.Event.new(Gdk.EventType.FOCUS_CHANGE)
        focused = lambda: [p.header.get_style_context().has_class("focused") for p in self.panel._panes]
        first.terminal.emit("focus-in-event", event)
        self.assertEqual(focused(), [True, False])
        self.assertIs(self.panel._active, first)
        first.terminal.emit("focus-out-event", event)
        second.terminal.emit("focus-in-event", event)
        self.assertEqual(focused(), [False, True])
        self.assertIs(self.panel._active, second)
        second.terminal.emit("focus-out-event", event)
        self.assertEqual(focused(), [False, False])
        # Heights and the "+" placement are unchanged by the new header style.
        self.assertTrue(poll(lambda: second.header.get_allocated_height() == self.panel.HEADER_HEIGHT))
        self.assertLessEqual(self.panel.HEADER_HEIGHT, 32)

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
        self.assertLess(pane.frame.get_opacity(), 1.0)
        self.assertEqual(self.panel.terminal_count(), 2)
        self.assertEqual(self.labels(), ["bash1", "bash2"])
        self.assertTrue(poll(lambda: pane.frame.get_opacity() == 1.0, 0.6))
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
        self.assertIsNotNone(second.frame.get_parent())
        self.assertEqual(self.labels(), ["bash1"])
        self.assertEqual(second.header.text, "bash2")
        self.assertTrue(poll(lambda: not alive(victim_pid)))
        self.assertTrue(poll(lambda: second.frame.get_parent() is None, 0.6))
        self.assertEqual(events, [])
        # Closing the final pane emits last-closed only after the exit animation.
        first_pid = first.pid
        self.panel.close_current()
        self.assertEqual(self.panel.terminal_count(), 0)
        self.assertEqual(events, [])
        self.assertIsNotNone(first.frame.get_parent())
        self.assertTrue(poll(lambda: events == [True], 0.8))
        self.assertIsNone(first.frame.get_parent())
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
        self.assertTrue(poll(lambda: all(p.frame.get_opacity() == 1 for p in self.panel._panes), 1))
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
        self.assertTrue(poll(lambda: second.frame.get_parent() is None, 1))

    def test_unmapped_panel_skips_animation(self):
        self.panel.hide()
        self.assertFalse(self.panel.get_mapped())
        self.assertTrue(self.panel.new_terminal())
        pane = self.panel._active
        self.assertEqual(pane.frame.get_opacity(), 1.0)
        self.track(pane)
        self.panel.close_current()
        self.assertIsNone(pane.frame.get_parent())
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

    def test_reduced_motion_adds_and_removes_instantly(self):
        self.addCleanup(motion.set_reduce_motion, False)
        motion.set_reduce_motion(True)
        self.assertTrue(poll(lambda: self.panel.get_mapped()))
        events = []
        self.panel.connect("last-closed", lambda _p: events.append(True))
        self.assertTrue(self.panel.new_terminal())
        pane = self.panel._active
        self.track(pane)
        self.assertEqual(pane.frame.get_opacity(), 1.0)
        self.assertEqual(self.panel.terminal_count(), 2)
        self.panel.close_current()
        self.assertIsNone(pane.frame.get_parent())
        self.assertEqual(self.panel._closing, [])
        self.assertEqual(self.labels(), ["bash1"])
        self.panel.close_current()
        self.assertEqual(events, [True])

    def test_synchronous_when_animation_disabled(self):
        self.panel.ANIMATION_MS = 0
        events = []
        self.panel.connect("last-closed", lambda _p: events.append(True))
        self.assertTrue(self.panel.new_terminal())
        pane = self.panel._active
        self.track(pane)
        self.assertEqual(pane.frame.get_opacity(), 1.0)
        self.panel.close_current()
        self.assertIsNone(pane.frame.get_parent())
        self.assertEqual(self.labels(), ["bash1"])
        self.panel.close_current()
        self.assertEqual(events, [True])


class SideBySideAnimationTest(AnimationMixin, unittest.TestCase):
    stacked = False


class StackedAnimationTest(AnimationMixin, unittest.TestCase):
    stacked = True


if __name__ == "__main__":
    unittest.main()


class PaneChooserTest(unittest.TestCase):
    def setUp(self):
        self.window = Gtk.Window()
        self.button = AddButton()
        self.window.add(self.button)
        self.window.show_all()
        self.addCleanup(self.window.destroy)
        self.events = []
        self.button.connect('choose', lambda _b, kind: self.events.append(kind))

    def key(self, value):
        event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        event.keyval = value
        return self.button._chooser_key(self.button.popover, event)

    def test_click_opens_without_choosing_and_keyboard_selects(self):
        self.assertEqual([r.kind for r in self.button.choices.get_children()],
                         ['terminal', 'graph', 'gitgraph'])
        self.button.clicked()
        self.assertTrue(self.button.popover.get_visible())
        self.assertEqual(self.events, [])
        self.assertEqual(self.button.choices.get_selected_row().kind, 'terminal')
        for key, expected in ((Gdk.KEY_Down, 'graph'),
                              (Gdk.KEY_Down, 'gitgraph'), (Gdk.KEY_Up, 'graph'),
                              (Gdk.KEY_End, 'gitgraph'), (Gdk.KEY_Home, 'terminal')):
            self.assertTrue(self.key(key))
            self.assertEqual(self.button.choices.get_selected_row().kind, expected)
        self.key(Gdk.KEY_End)
        self.assertTrue(self.key(Gdk.KEY_Return))
        self.assertEqual(self.events, ['gitgraph'])
        self.assertTrue(poll(lambda: not self.button.popover.get_visible()))
        self.button.clicked()
        self.assertEqual(self.button.choices.get_selected_row().kind, 'terminal')
        self.key(Gdk.KEY_space)
        self.assertEqual(self.events, ['gitgraph', 'terminal'])

    def test_mouse_activation_escape_and_reduced_motion(self):
        with patch.object(motion, 'animations_enabled', return_value=False):
            self.button.clicked()
            self.assertFalse(self.button.popover.get_transitions_enabled())
            self.button.choices.emit('row-activated', self.button.choices.get_row_at_index(1))
            self.assertEqual(self.events, ['graph'])
            self.assertFalse(self.button.popover.get_visible())
            self.button.clicked()
            self.assertTrue(self.key(Gdk.KEY_Escape))
            self.assertFalse(self.button.popover.get_visible())
            self.assertTrue(self.button.is_focus())
        theme = Gtk.IconTheme.get_default()
        self.assertTrue(theme.has_icon(self.button.graph_icon))
