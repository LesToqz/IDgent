"""Pure timing/colour checks and real GTK3 frame-clock integration under X11."""
from __future__ import annotations

import os
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

os.environ["GDK_BACKEND"] = "x11"

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from idgent.motion import (
    DURATIONS, EASINGS, StateFader, animate, animate_size_request,
    animations_enabled, cubic_bezier, duration_ms, fade, lerp_color,
    set_reduce_motion, slide_in,
)


def poll(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    context = GLib.MainContext.default()
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        if predicate():
            return True
        time.sleep(0.002)
    return bool(predicate())


def pump(seconds):
    poll(lambda: False, seconds)


class EasingTest(unittest.TestCase):
    def test_endpoints_and_clamping(self):
        for name, ease in EASINGS.items():
            with self.subTest(easing=name):
                self.assertEqual([ease(t) for t in (-10, 0, 1, 10)], [0, 0, 1, 1])

    def test_linear_identity(self):
        for i in range(101):
            self.assertEqual(EASINGS["linear"](i / 100), i / 100)

    def test_non_spring_easings_are_monotonic_without_overshoot(self):
        for name, ease in EASINGS.items():
            if name == "jumpy":
                continue
            with self.subTest(easing=name):
                samples = [ease(i / 1000) for i in range(1001)]
                self.assertEqual(samples, sorted(samples))
                self.assertGreaterEqual(min(samples), 0)
                self.assertLessEqual(max(samples), 1)

    def test_jumpy_preserves_overshoot(self):
        self.assertGreater(max(EASINGS["jumpy"](i / 100) for i in range(101)), 1)
        self.assertLess(EASINGS["jumpy"](0.1), 0)

    def test_idgent_control_points(self):
        from idgent.motion import _EASING_POINTS
        expected = {
            "smooth": (0.24, 0.08, 0.28, 1.0),
            "arrive": (0.18, 0.66, 0.26, 1.0),
            "depart": (0.48, 0.0, 0.82, 0.42),
            "balanced": (0.38, 0.0, 0.62, 1.0),
            "jumpy": (0.32, -0.18, 0.42, 1.16),
        }
        for name, controls in expected.items():
            self.assertEqual(_EASING_POINTS[name], controls)
            x1, y1, x2, y2 = controls
            for s in (0.125, 0.375, 0.625, 0.875):
                x = 3 * (1 - s) ** 2 * s * x1 + 3 * (1 - s) * s ** 2 * x2 + s ** 3
                y = 3 * (1 - s) ** 2 * s * y1 + 3 * (1 - s) * s ** 2 * y2 + s ** 3
                self.assertAlmostEqual(EASINGS[name](x), y, places=6)

    def test_balanced_curve_is_symmetric(self):
        self.assertAlmostEqual(EASINGS["balanced"](0.5), 0.5)
        for t in (0.1, 0.3, 0.7):
            self.assertAlmostEqual(EASINGS["balanced"](t), 1 - EASINGS["balanced"](1 - t))

    def test_idgent_motion_scale(self):
        expected = {"instant": 0, "feedback": 96, "state": 128, "travel": 224, "settle": 352, "pause": 640}
        for name, duration in expected.items():
            self.assertEqual(duration_ms(name), duration)
        self.assertLess(DURATIONS["feedback"], DURATIONS["state"])
        self.assertLess(DURATIONS["state"], DURATIONS["travel"])
        self.assertLess(DURATIONS["travel"], DURATIONS["settle"])

    def test_default_curve_finishes_most_travel_before_half_time(self):
        self.assertGreater(EASINGS["smooth"](0.5), 0.7)
        self.assertLess(EASINGS["smooth"](0.5), 0.9)

    def test_flat_derivatives_use_bisection(self):
        # x(s)=s^3, y(s)=3s^2-2s^3 has a flat derivative near the origin.
        ease = cubic_bezier(0, 0, 0, 1)
        for t in (1e-6, 0.01, 0.1, 0.5, 0.99):
            s = t ** (1 / 3)
            self.assertAlmostEqual(ease(t), 3 * s * s - 2 * s ** 3, delta=1e-6)
        # x controls can cross, including the stationary midpoint of (1, 0).
        self.assertAlmostEqual(cubic_bezier(1, 0, 0, 1)(0.5), 0.5)

    def test_invalid_controls_and_inputs(self):
        for controls in ((-0.1, 0, 1, 1), (0, 0, 1.1, 1), (0, float("nan"), 1, 1)):
            with self.assertRaises(ValueError):
                cubic_bezier(*controls)
        with self.assertRaises(ValueError):
            EASINGS["smooth"](float("nan"))

    def test_duration_tokens_and_numbers(self):
        for name, expected in {'none': 0, 'superfast': 96, 'fast': 128, 'moderate': 224, 'slow': 352, 'collapse': 128, 'toggle': 128, 'notice_in': 224, 'notice_out': 128, 'reflow': 224, 'resize_handle': 128}.items():
            self.assertEqual(duration_ms(name), expected)
        for name, expected in DURATIONS.items():
            self.assertEqual(duration_ms(name), expected)
        self.assertEqual(duration_ms(12.5), 12.5)
        self.assertEqual(duration_ms(-1), -1)
        with self.assertRaises(KeyError):
            duration_ms("typo")
        with self.assertRaises(ValueError):
            duration_ms(float("inf"))

    def test_component_duration_contracts(self):
        # Existing component call sites keep their keys with IDgent timings.
        self.assertEqual(duration_ms("tab_close_reflow"), DURATIONS["travel"])
        self.assertEqual(duration_ms("clickable_icon_opacity"), DURATIONS["state"])
        self.assertEqual(duration_ms("notice_hover_grace"), DURATIONS["pause"])


class ColorTest(unittest.TestCase):
    def test_hex_and_rgb_midpoints(self):
        self.assertEqual(lerp_color("#000", "#ffffff", 0.5), "rgba(128, 128, 128, 1)")
        self.assertEqual(lerp_color("rgb(255, 0, 0)", "#00f", 0.5), "rgba(128, 0, 128, 1)")

    def test_alpha_and_short_hex(self):
        self.assertEqual(lerp_color("#f000", "#0000ffff", 0.5), "rgba(128, 0, 128, 0.5)")
        self.assertEqual(lerp_color("rgba(10, 20, 30, 0.2)", "rgba(30, 40, 50, 0.8)", 0.5),
                         "rgba(20, 30, 40, 0.5)")

    def test_percentages_endpoints_and_overshoot(self):
        self.assertEqual(lerp_color("rgba(100%, 0%, 0%, 50%)", "#000", 0),
                         "rgba(255, 0, 0, 0.5)")
        self.assertEqual(lerp_color("#000", "#fff", 2), "rgba(255, 255, 255, 1)")
        self.assertEqual(lerp_color("#000", "#fff", -1), "rgba(0, 0, 0, 1)")

    def test_invalid_color(self):
        for color in ("#abcde", "#ggg", "rgb(1, 2)", "rgba(1, 2, 3)", "rgba(1, 2, 3, nan)"):
            with self.subTest(color=color), self.assertRaises(ValueError):
                lerp_color(color, "#fff", 0.5)

    def test_import_and_pure_functions_without_display_or_side_effects(self):
        script = """
import sys
from unittest.mock import patch
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import GLib, Gtk
def forbidden(*args, **kwargs):
    raise AssertionError('import touched GTK state or registered a source')
with patch.object(Gtk.Widget, '__init__', forbidden), \
     patch.object(Gtk.Settings, 'get_default', forbidden), \
     patch.object(GLib, 'timeout_add', forbidden), \
     patch.object(GLib, 'idle_add', forbidden):
    from idgent import motion
    assert 0.7 < motion.EASINGS['ease'](0.5) < 0.9
    assert motion.duration_ms('fast') == 128
    assert motion.lerp_color('#000', '#fff', 1) == 'rgba(255, 255, 255, 1)'
    assert 'idgent.settings' not in sys.modules
    motion.set_reduce_motion(True)
    assert not motion.animations_enabled()
    motion.set_reduce_motion(False)
assert Gtk.Window.list_toplevels() == []
"""
        env = dict(os.environ)
        env.pop("DISPLAY", None)
        env.pop("WAYLAND_DISPLAY", None)
        result = subprocess.run([sys.executable, "-c", script], env=env,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


class GtkFixture(unittest.TestCase):
    def setUp(self):
        if not Gtk.init_check()[0]:
            self.skipTest("GTK tests need an X11 display (use xvfb-run)")
        env = patch.dict(os.environ, {"IDGENT_REDUCE_MOTION": "0"})
        env.start()
        self.addCleanup(env.stop)
        set_reduce_motion(False)
        self.addCleanup(set_reduce_motion, False)
        self.settings = Gtk.Settings.get_default()
        original = self.settings.get_property("gtk-enable-animations")
        self.settings.set_property("gtk-enable-animations", True)
        self.addCleanup(self.settings.set_property, "gtk-enable-animations", original)
        self.window = Gtk.Window()
        self.widget = Gtk.EventBox()
        self.window.add(self.widget)
        self.window.set_default_size(200, 120)
        self.window.show_all()
        self.addCleanup(self.window.destroy)
        self.assertTrue(poll(lambda: self.widget.get_mapped() and
                             self.widget.get_allocated_width() > 1))
        # PyGObject reports callback exceptions instead of propagating through
        # MainContext.iteration; turn those into test failures, including teardown.
        self.errors = []
        hook = patch.object(sys, "excepthook", lambda *exc: self.errors.append(exc))
        hook.start()
        self.addCleanup(hook.stop)
        self.addCleanup(lambda: self.assertEqual(self.errors, []))

    def finish(self, handle):
        self.assertTrue(poll(lambda: handle.finished))


class TweenTest(GtkFixture):
    def test_component_durations_animate_and_respect_reduced_motion(self):
        for reduced in (False, True):
            set_reduce_motion(reduced)
            for duration in ("tab_close_reflow", "clickable_icon_opacity"):
                with self.subTest(reduced=reduced, duration=duration):
                    values, done = [], []
                    handle = animate(self.widget, duration, values.append,
                                     on_done=lambda: done.append(True))
                    self.assertEqual(handle.finished, reduced)
                    self.assertEqual(values, [1] if reduced else [0])
                    self.finish(handle)
                    self.assertEqual(values[-1], 1)
                    self.assertEqual(done, [True])

    def test_150ms_tween_finishes_in_8_to_12_real_frames(self):
        frames, values = [], []
        clock = self.widget.get_frame_clock()

        def update(value):
            # animate() emits its initial value synchronously, before any tick.
            values.append(value)
            if len(values) > 1:
                frames.append(clock.get_frame_counter())

        with patch.object(GLib, "timeout_add", side_effect=AssertionError("visible timer")):
            self.finish(animate(self.widget, 150, update, easing="linear"))
        self.assertEqual((values[0], values[-1]), (0, 1))
        self.assertEqual(frames, sorted(set(frames)))
        self.assertGreaterEqual(len(frames), 8)
        self.assertLessEqual(len(frames), 12)

    def test_frame_clock_timing_progress_and_done_once(self):
        samples, done = [], []
        start = time.monotonic()
        with patch.object(GLib, "timeout_add", side_effect=AssertionError("visible timer")):
            handle = animate(self.widget, 140, samples.append, on_done=lambda: done.append(1))
            self.assertFalse(handle.finished)
            self.finish(handle)
        elapsed = time.monotonic() - start
        self.assertGreaterEqual(elapsed, 0.12)
        self.assertLess(elapsed, 0.6)
        self.assertGreater(len(samples), 3)
        self.assertEqual(samples, sorted(samples))
        self.assertEqual((samples[0], samples[-1]), (0, 1))
        pump(0.04)
        self.assertEqual(done, [1])

    def test_cancel_stops_updates_without_done(self):
        samples, done = [], []
        handle = animate(self.widget, 200, samples.append, on_done=lambda: done.append(1))
        self.assertTrue(poll(lambda: samples[-1] > 0.1))
        handle.cancel()
        count = len(samples)
        handle.cancel()
        pump(0.25)
        self.assertTrue(handle.finished)
        self.assertEqual(len(samples), count)
        self.assertEqual(done, [])

    def test_replacement_captures_current_value(self):
        values, done = [], []
        first = animate(self.widget, 200, values.append, key="amount", on_done=lambda: done.append(1))
        self.assertTrue(poll(lambda: values[-1] > 0.15))
        current = values[-1]
        second = animate(self.widget, 70, lambda p: values.append(current * (1 - p)), key="amount")
        self.assertEqual(values[-1], current)
        self.assertTrue(first.finished)
        self.finish(second)
        self.assertEqual(values[-1], 0)
        self.assertEqual(done, [])

    def test_independent_keys(self):
        done = []
        a = animate(self.widget, 70, lambda _p: None, key="a", on_done=lambda: done.append("a"))
        b = animate(self.widget, 70, lambda _p: None, key="b", on_done=lambda: done.append("b"))
        self.finish(a)
        self.finish(b)
        self.assertCountEqual(done, ["a", "b"])

    def test_destroy_and_unrealize_cancel_quietly(self):
        for action in ("destroy", "unrealize", "hide"):
            with self.subTest(action=action):
                window = Gtk.Window()
                window.show()
                self.addCleanup(window.destroy)
                values, done = [], []
                handle = animate(window, 140, values.append, on_done=lambda: done.append(1))
                self.assertTrue(poll(lambda: values[-1] > 0))
                getattr(window, action)()
                count = len(values)
                pump(0.05)
                self.assertTrue(handle.finished)
                self.assertEqual(len(values), count)
                self.assertEqual(done, [])

    def test_hidden_fallback_completes(self):
        for realized in (False, True):
            widget = Gtk.Window()
            if realized:
                widget.realize()
            self.addCleanup(widget.destroy)
            values, done = [], []
            with patch.object(widget, "add_tick_callback", side_effect=AssertionError("hidden tick")):
                handle = animate(widget, 70, values.append, on_done=lambda: done.append(1))
                self.finish(handle)
            self.assertEqual(values[-1], 1)
            self.assertEqual(done, [1])

    def test_hidden_destroy_stops_fallback(self):
        widget = Gtk.Label()
        values, done = [], []
        handle = animate(widget, 100, values.append, on_done=lambda: done.append(1))
        widget.destroy()
        pump(0.12)
        self.assertTrue(handle.finished)
        self.assertEqual(values, [0])
        self.assertEqual(done, [])

    def test_hidden_then_mapped_switches_to_frame_clock(self):
        widget = Gtk.Window()
        self.addCleanup(widget.destroy)
        values = []
        handle = animate(widget, 140, values.append)
        self.assertTrue(poll(lambda: values[-1] > 0.1))
        with patch.object(widget, "add_tick_callback", wraps=widget.add_tick_callback) as tick:
            widget.show()
            self.finish(handle)
            tick.assert_called_once()
        self.assertEqual(values[-1], 1)
        self.assertEqual(values, sorted(values))

    def test_delay_and_callable_easing(self):
        values = []
        start = time.monotonic()
        handle = animate(self.widget, 70, values.append, delay=70, easing=lambda t: t * t)
        pump(0.035)
        self.assertEqual(values, [0])
        self.finish(handle)
        self.assertGreaterEqual(time.monotonic() - start, 0.13)
        self.assertEqual(values[-1], 1)

    def test_engine_preserves_overshoot(self):
        values = []
        self.finish(animate(self.widget, 140, values.append, easing="jumpy"))
        self.assertGreater(max(values), 1)

    def test_immediate_durations_ignore_delay(self):
        for duration in (0, -1, "none"):
            values, done = [], []
            handle = animate(self.widget, duration, values.append, delay=999,
                             on_done=lambda: done.append(1))
            self.assertTrue(handle.finished)
            self.assertEqual(values, [1])
            self.assertEqual(done, [1])

    def test_application_env_and_gtk_setting_force_synchronous_completion(self):
        for override in ("application", "env", "setting"):
            with self.subTest(override=override):
                os.environ["IDGENT_REDUCE_MOTION"] = "1" if override == "env" else "0"
                self.settings.set_property("gtk-enable-animations", override != "setting")
                set_reduce_motion(override == "application")
                self.assertFalse(animations_enabled())
                values, done = [], []
                handle = animate(self.widget, 300, values.append, delay=100,
                                 on_done=lambda: done.append(1))
                self.assertTrue(handle.finished)
                self.assertEqual(values, [1])
                self.assertEqual(done, [1])

    def test_clearing_application_preference_respects_other_overrides(self):
        set_reduce_motion(True)
        self.assertFalse(animations_enabled())
        set_reduce_motion(False)
        self.assertTrue(animations_enabled())
        with patch.dict(os.environ, {"IDGENT_REDUCE_MOTION": "1"}):
            set_reduce_motion(False)
            self.assertFalse(animations_enabled())
        self.settings.set_property("gtk-enable-animations", False)
        set_reduce_motion(False)
        self.assertFalse(animations_enabled())

    def test_update_can_replace_itself(self):
        replacements, done = [], []

        def update(p):
            if p > 0 and not replacements:
                replacements.append(animate(self.widget, 0, lambda _p: None))

        handle = animate(self.widget, 70, update, on_done=lambda: done.append(1))
        self.finish(handle)
        self.assertEqual(len(replacements), 1)
        self.assertEqual(done, [])

    def test_done_can_start_next_tween(self):
        next_handles = []
        first = animate(self.widget, 70, lambda _p: None,
                        on_done=lambda: next_handles.append(animate(self.widget, 70, lambda _p: None)))
        self.finish(first)
        self.assertFalse(next_handles[0].finished)
        self.finish(next_handles[0])

    def test_update_exception_cleans_up(self):
        def fail(_p):
            raise ValueError("caller failure")

        with self.assertRaisesRegex(ValueError, "caller failure"):
            animate(self.widget, 100, fail)
        self.assertEqual(self.widget._motion_tweens, {})

    def test_update_can_destroy_widget(self):
        done = []

        def update(p):
            if p > 0:
                self.widget.destroy()

        handle = animate(self.widget, 70, update, on_done=lambda: done.append(1))
        self.finish(handle)
        self.assertEqual(done, [])
        self.assertEqual(self.widget._motion_tweens, {})


class ValueHelperTest(GtkFixture):
    def test_application_reduce_motion_settles_helpers_and_can_be_cleared(self):
        set_reduce_motion(True)
        values, done = [], []
        handle = animate(self.widget, 150, values.append, delay=100,
                         on_done=lambda: done.append("animate"))
        self.assertTrue(handle.finished)
        self.assertEqual(values, [1])
        self.widget.set_margin_top(7)
        for show in (False, True):
            handle = fade(self.widget, show, on_done=lambda: done.append("fade"))
            self.assertTrue(handle.finished)
            self.assertEqual(self.widget.get_visible(), show)
            self.assertEqual(self.widget.get_opacity(), float(show))
        self.widget.hide()
        handle = slide_in(self.widget, on_done=lambda: done.append("slide"))
        self.assertTrue(handle.finished)
        self.assertTrue(self.widget.get_visible())
        self.assertEqual((self.widget.get_opacity(), self.widget.get_margin_top()), (1, 7))
        self.assertEqual(done, ["animate", "fade", "fade", "slide"])
        self.assertEqual(self.widget._motion_tweens, {})

        set_reduce_motion(False)
        self.assertTrue(animations_enabled())
        values.clear()
        handle = animate(self.widget, 150, values.append)
        self.assertFalse(handle.finished)
        self.assertEqual(values, [0])
        self.finish(handle)
        self.assertEqual(values[-1], 1)
        handle = fade(self.widget, False)
        self.assertFalse(handle.finished)
        self.finish(handle)
        self.assertFalse(self.widget.get_visible())
        handle = slide_in(self.widget)
        self.assertFalse(handle.finished)
        self.assertEqual(self.widget.get_opacity(), 0)
        self.finish(handle)
        self.assertEqual((self.widget.get_opacity(), self.widget.get_margin_top()), (1, 7))

    def test_instant_entrances_can_be_requested_explicitly(self):
        self.widget.hide()
        self.assertTrue(fade(self.widget, True, duration="none").finished)
        self.assertEqual(self.widget.get_opacity(), 1)
        self.widget.hide()
        self.widget.set_margin_top(7)
        self.assertTrue(slide_in(self.widget, duration="none").finished)
        self.assertEqual((self.widget.get_opacity(), self.widget.get_margin_top()), (1, 7))

    def test_fade_exit_and_entrance(self):
        done = []
        self.finish(fade(self.widget, False, 70, on_done=lambda: done.append(self.widget.get_visible())))
        self.assertEqual(self.widget.get_opacity(), 0)
        self.assertFalse(self.widget.get_visible())
        self.assertEqual(done, [False])
        handle = fade(self.widget, True, 70)
        self.assertTrue(self.widget.get_visible())
        self.assertEqual(self.widget.get_opacity(), 0)
        self.finish(handle)
        self.assertEqual(self.widget.get_opacity(), 1)

    def test_fade_reverses_from_current_opacity(self):
        done = []
        first = fade(self.widget, False, 200, on_done=lambda: done.append(1))
        self.assertTrue(poll(lambda: self.widget.get_opacity() < 0.8))
        current = self.widget.get_opacity()
        second = fade(self.widget, True, 70)
        self.assertEqual(self.widget.get_opacity(), current)
        self.assertTrue(first.finished)
        self.finish(second)
        self.assertEqual(self.widget.get_opacity(), 1)
        self.assertEqual(done, [])

    def test_fade_can_keep_transparent_widget_visible(self):
        self.finish(fade(self.widget, False, 70, hide_when_done=False))
        self.assertTrue(self.widget.get_visible())
        self.assertEqual(self.widget.get_opacity(), 0)

    def test_slide_restores_margin_and_restarts_without_jump(self):
        self.widget.hide()
        self.widget.set_margin_top(8)
        first = slide_in(self.widget, offset=20, duration=200, easing="smooth")
        self.assertEqual(self.widget.get_margin_top(), 28)
        self.assertEqual(self.widget.get_opacity(), 0)
        self.assertTrue(poll(lambda: self.widget.get_opacity() > 0.2))
        current = (self.widget.get_margin_top(), self.widget.get_opacity())
        done = []
        second = slide_in(self.widget, offset=99, duration=70,
                          on_done=lambda: done.append(self.widget.get_margin_top()))
        self.assertTrue(first.finished)
        self.assertEqual((self.widget.get_margin_top(), self.widget.get_opacity()), current)
        self.finish(second)
        self.assertEqual((self.widget.get_margin_top(), self.widget.get_opacity()), (8, 1))
        self.assertEqual(done, [8])

    def test_fade_settles_interrupted_slide(self):
        self.widget.set_margin_top(6)
        first = slide_in(self.widget, offset=30, duration=200, easing="smooth")
        self.assertTrue(poll(lambda: self.widget.get_margin_top() < 30))
        current = self.widget.get_margin_top()
        second = fade(self.widget, False, 70)
        self.assertEqual(self.widget.get_margin_top(), current)
        self.assertTrue(first.finished)
        self.finish(second)
        self.assertEqual(self.widget.get_margin_top(), 6)
        self.assertFalse(self.widget.get_visible())

    def test_size_restores_original_and_clears_natural_request(self):
        self.widget.set_size_request(24, -1)
        done = []
        width = animate_size_request(self.widget, "width", 80, 70,
                                     on_done=lambda: done.append(self.widget.get_size_request()[0]))
        height = animate_size_request(self.widget, "height", 60, 70)
        self.finish(width)
        self.finish(height)
        self.assertEqual(tuple(self.widget.get_size_request()), (24, -1))
        self.assertEqual(done, [24])

    def test_size_reverses_from_current_request_and_can_retain_target(self):
        self.widget.set_size_request(10, 20)
        first = animate_size_request(self.widget, "width", 110, 200)
        self.assertTrue(poll(lambda: self.widget.get_size_request()[0] > 30))
        current = self.widget.get_size_request()[0]
        second = animate_size_request(self.widget, "width", 0, 70, restore=False)
        self.assertEqual(self.widget.get_size_request()[0], current)
        self.assertTrue(first.finished)
        self.finish(second)
        self.assertEqual(tuple(self.widget.get_size_request()), (0, 20))

    def test_size_replacement_keeps_original_baseline(self):
        self.widget.set_size_request(10, -1)
        first = animate_size_request(self.widget, "width", 90, 200)
        self.assertTrue(poll(lambda: self.widget.get_size_request()[0] > 20))
        self.finish(animate_size_request(self.widget, "width", 40, 70))
        self.assertTrue(first.finished)
        self.assertEqual(self.widget.get_size_request()[0], 10)

    def test_invalid_size_arguments(self):
        with self.assertRaises(ValueError):
            animate_size_request(self.widget, "depth", 40)
        with self.assertRaises(ValueError):
            animate_size_request(self.widget, "width", -1)

    def test_helpers_settle_synchronously_with_reduced_motion(self):
        os.environ["IDGENT_REDUCE_MOTION"] = "1"
        done = []
        self.widget.set_margin_top(7)
        self.widget.set_size_request(-1, 23)
        handles = [fade(self.widget, False, on_done=lambda: done.append("out")),
                   fade(self.widget, True, on_done=lambda: done.append("in")),
                   slide_in(self.widget, on_done=lambda: done.append("slide")),
                   animate_size_request(self.widget, "width", 100, on_done=lambda: done.append("size")),
                   animate_size_request(self.widget, "height", 50, restore=False)]
        self.assertTrue(all(h.finished for h in handles))
        self.assertEqual(done, ["out", "in", "slide", "size"])
        self.assertEqual(self.widget.get_opacity(), 1)
        self.assertEqual(self.widget.get_margin_top(), 7)
        self.assertEqual(tuple(self.widget.get_size_request()), (-1, 50))


class StateFaderTest(GtkFixture):
    def test_reverse_and_end_state(self):
        values, done = [], []
        fader = StateFader(self.widget, values.append, duration=200)
        self.addCleanup(fader.disconnect)
        first = fader.set(1, lambda: done.append(1))
        self.assertTrue(poll(lambda: fader.value > 0.2))
        current = fader.value
        fader.duration = 70
        second = fader.set(0)
        self.assertEqual(fader.value, current)
        self.assertEqual(values[-1], current)
        self.assertTrue(first.finished)
        start = len(values) - 1
        self.finish(second)
        self.assertEqual(values[start:], sorted(values[start:], reverse=True))
        self.assertEqual(fader.value, 0)
        self.assertEqual(done, [])

    def test_enter_leave_events(self):
        fader = StateFader(self.widget, lambda _v: None, duration=70)
        self.addCleanup(fader.disconnect)
        for event_type, signal, target in ((Gdk.EventType.ENTER_NOTIFY, "enter-notify-event", 1),
                                            (Gdk.EventType.LEAVE_NOTIFY, "leave-notify-event", 0)):
            event = Gdk.Event.new(event_type)
            event.crossing.detail = Gdk.NotifyType.NONLINEAR
            self.widget.emit(signal, event)
            self.assertTrue(poll(lambda: fader.value == target))

    def test_inferior_crossing_does_not_change_target(self):
        fader = StateFader(self.widget, lambda _v: None, duration=0, initial=1)
        self.addCleanup(fader.disconnect)
        event = Gdk.Event.new(Gdk.EventType.LEAVE_NOTIFY)
        event.crossing.detail = Gdk.NotifyType.INFERIOR
        self.widget.emit("leave-notify-event", event)
        self.assertEqual(fader.value, 1)

    def test_disconnect_and_destroy_cancel(self):
        values = []
        fader = StateFader(self.widget, values.append)
        first = fader.set(1)
        fader.disconnect()
        count = len(values)
        pump(0.16)
        self.assertTrue(first.finished)
        self.assertEqual(len(values), count)
        with self.assertRaises(RuntimeError):
            fader.set(0)
        other = StateFader(self.widget, values.append)
        second = other.set(1)
        self.widget.destroy()
        self.assertTrue(second.finished)
        other.disconnect()

    def test_reduced_motion_and_clamped_values(self):
        os.environ["IDGENT_REDUCE_MOTION"] = "1"
        values, done = [], []
        fader = StateFader(self.widget, values.append, initial=-1)
        self.addCleanup(fader.disconnect)
        handle = fader.set(2, lambda: done.append(1))
        self.assertTrue(handle.finished)
        self.assertEqual(values, [0, 1])
        self.assertEqual(done, [1])
        self.assertEqual(fader.value, 1)

    def test_update_can_disconnect_before_set_returns(self):
        values, done = [], []
        fader = StateFader(self.widget, values.append)

        def update(value):
            values.append(value)
            fader.disconnect()

        fader._update = update
        handle = fader.set(1, lambda: done.append(1))
        self.assertTrue(handle.finished)
        count = len(values)
        pump(0.16)
        self.assertEqual(len(values), count)
        self.assertEqual(done, [])

    def test_unparented_widget_finalised_by_gc_is_quiet(self):
        import gc
        errors = []
        record = lambda *args: errors.append(args)

        def make():
            StateFader(Gtk.Button(label="x"), lambda _v: None)

        with patch.object(sys, "excepthook", record), patch.object(sys, "unraisablehook", record):
            for _ in range(3):
                make()
            gc.collect()
            gc.collect()
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
