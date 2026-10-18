"""OverlayHost prompt layer and notice stack; run with GDK_BACKEND=x11 under xvfb."""
import os
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ["GDK_BACKEND"] = "x11"

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, Gtk, Pango

from idgent import motion, overlay
from idgent.overlay import (MAX_NOTICES, NOTICE_GAP, NOTICE_MAX_WIDTH, NOTICE_PADDING, Notice,
                            OverlayHost)
from idgent.palette import Prompt, PromptItem
from idgent.themes import THEMES
from tests.test_palette import click_at, install_theme, parse_errors
from tests.test_widgets import pump, send_key


def poll(predicate, timeout=1.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        pump(5)
    return predicate()


class OverlayCase(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        env = patch.dict(os.environ, XDG_CONFIG_HOME=root.name)
        env.start()
        self.addCleanup(env.stop)
        install_theme(self)
        motion.set_reduce_motion(True)
        self.addCleanup(motion.set_reduce_motion, False)
        self.window = Gtk.Window()
        self.window.set_default_size(900, 600)
        self.addCleanup(self.window.destroy)
        self.text = Gtk.TextView()
        self.host = OverlayHost(self.text)
        self.window.add(self.host)
        self.window.show_all()
        self.window.present()
        pump(120)
        self.text.grab_focus()
        # Park the pointer away from the notice stack (hover pauses timers).
        window = self.window.get_window()
        _ok, x, y = window.get_origin()
        window.get_display().get_default_seat().get_pointer().warp(
            window.get_screen(), x + 450, y + 550)
        pump(20)

    def box_rect(self, notice):
        x, y = notice.box.translate_coordinates(self.host, 0, 0)
        allocation = notice.box.get_allocation()
        return x, y, allocation.width, allocation.height


class NoticeTest(OverlayCase):
    def test_stack_position_order_and_gap(self):
        first = self.host.notify('first', timeout=0)
        second = self.host.notify('second, a much longer notice that wraps onto more lines '
                                  'than one because it exceeds three hundred pixels', timeout=0)
        pump(60)
        self.assertEqual(self.host.notices, [first, second])
        width = self.host.get_allocated_width()
        x1, y1, w1, h1 = self.box_rect(first)
        x2, y2, w2, h2 = self.box_rect(second)
        self.assertEqual((y1, x1 + w1), (NOTICE_PADDING, width - NOTICE_PADDING))
        self.assertEqual((y2, x2 + w2), (y1 + h1 + NOTICE_GAP, width - NOTICE_PADDING))
        self.assertEqual(w2, NOTICE_MAX_WIDTH)
        self.assertGreater(h2, h1)
        self.assertLess(w1, NOTICE_MAX_WIDTH)
        self.assertTrue(second.label.get_line_wrap())

    def test_style_black_ninety_percent_and_text(self):
        notice = self.host.notify('styled', timeout=0)
        pump(60)
        context = notice.box.get_style_context()
        background = context.get_property('background-color', Gtk.StateFlags.NORMAL)
        self.assertEqual((background.red, background.green, background.blue), (0, 0, 0))
        self.assertAlmostEqual(background.alpha, .9, places=2)
        self.assertEqual(context.get_property('border-radius', Gtk.StateFlags.NORMAL), 8)
        font = notice.label.get_style_context().get_property('font', Gtk.StateFlags.NORMAL)
        self.assertEqual(font.get_size() / Pango.SCALE, 9.75)  # 13 px
        x, y, w, h = self.box_rect(notice)
        tx, ty = self.host.translate_coordinates(self.window, x + 4, y + h // 2)
        pixel = Gdk.pixbuf_get_from_window(self.window.get_window(), tx, ty, 1, 1).get_pixels()
        self.assertTrue(all(channel < 12 for channel in pixel[:3]), tuple(pixel[:3]))

    def test_max_five_dismisses_oldest(self):
        notices = [self.host.notify(f'n{index}', timeout=0) for index in range(MAX_NOTICES + 2)]
        pump(30)
        self.assertEqual(self.host.notices, notices[2:])
        self.assertTrue(all(notice.get_parent() is None for notice in notices[:2]))
        self.assertEqual(len(self.host._notices), MAX_NOTICES)

    def test_timeout_dismisses(self):
        notice = self.host.notify('short', timeout=60)
        sticky = self.host.notify('sticky', timeout=0)
        self.assertTrue(poll(lambda: notice not in self.host.notices, .8))
        self.assertIsNone(notice.get_parent())
        pump(100)
        self.assertEqual(self.host.notices, [sticky])
        _x, y, _w, _h = self.box_rect(sticky)
        self.assertEqual(y, NOTICE_PADDING)

    def test_click_dismisses(self):
        first = self.host.notify('click me', timeout=0)
        second = self.host.notify('stay', timeout=0)
        pump(60)
        x, y, w, h = self.box_rect(first)
        tx, ty = self.host.translate_coordinates(self.window, x + w // 2, y + h // 2)
        click_at(self.window, tx, ty)
        self.assertEqual(self.host.notices, [second])
        self.assertTrue(self.text.has_focus())

    def test_hover_pauses_and_leave_restarts_grace_timer(self):
        notice = self.host.notify('hover', timeout=200)
        enter = Gdk.Event.new(Gdk.EventType.ENTER_NOTIFY)
        enter.detail = Gdk.NotifyType.NONLINEAR
        notice.emit('enter-notify-event', enter)
        self.assertEqual(notice.timer, 0)
        pump(300)
        self.assertIn(notice, self.host.notices)
        leave = Gdk.Event.new(Gdk.EventType.LEAVE_NOTIFY)
        leave.detail = Gdk.NotifyType.NONLINEAR
        notice.emit('leave-notify-event', leave)
        self.assertNotEqual(notice.timer, 0)
        self.assertTrue(poll(lambda: notice not in self.host.notices, .8))

    def test_invalid_arguments(self):
        with self.assertRaises(ValueError):
            self.host.notify('x', level='fatal')
        with self.assertRaises(ValueError):
            self.host.notify('x', timeout=-1)
        for level in overlay.NOTICE_LEVELS:
            notice = self.host.notify(level, timeout=0, level=level)
            self.assertTrue(notice.box.get_style_context().has_class(f'idgent-notice-{level}'))

    def test_reduced_motion_is_instant(self):
        notice = self.host.notify('instant', timeout=0)
        self.assertEqual(notice.offset, 0)
        self.host.dismiss(notice)
        self.assertIsNone(notice.get_parent())
        self.assertEqual(self.host.notices, [])

    def test_slide_in_and_fade_out_animate(self):
        motion.set_reduce_motion(False)
        if not motion.animations_enabled():
            self.skipTest('animations disabled in this environment')
        notice = self.host.notify('animated', timeout=0)
        self.assertEqual(notice.offset, overlay.NOTICE_SLIDE)
        pump(40)
        x_mid = self.box_rect(notice)[0]
        self.assertTrue(poll(lambda: notice.offset == 0, .5))
        pump(30)
        x_end = self.box_rect(notice)[0]
        self.assertGreater(x_mid, x_end)
        below = self.host.notify('below', timeout=0)
        self.assertTrue(poll(lambda: below.offset == 0, .5))
        pump(30)
        y_before = self.box_rect(below)[1]
        self.host.dismiss(notice)
        self.assertIn(notice, self.host._notices)
        self.assertEqual(self.host.notices, [below])
        pump(50)
        self.assertLess(notice.get_opacity(), 1)
        self.assertTrue(poll(lambda: notice.get_parent() is None, .5))
        pump(30)
        self.assertLess(self.box_rect(below)[1], y_before)
        self.assertEqual(self.box_rect(below)[1], NOTICE_PADDING)

    def test_notices_stay_above_prompt(self):
        notice = self.host.notify('above', timeout=0)
        prompt = Prompt(lambda query: [PromptItem('x')])
        self.addCleanup(prompt.destroy)
        self.host.show_prompt(prompt)
        children = [child for child in self.host.get_children()]
        self.assertGreater(children.index(notice), children.index(prompt))

    def test_css_parses_for_every_theme(self):
        for theme in THEMES:
            with self.subTest(theme=theme.id):
                self.assertEqual(parse_errors(overlay.build_css(theme.ui)), [])


class HostTest(OverlayCase):
    def test_host_wraps_child_and_prompt_signals(self):
        self.assertIs(self.host.get_child(), self.text)
        events = []
        self.host.connect('prompt-shown', lambda _h, prompt: events.append('shown'))
        self.host.connect('prompt-hidden', lambda _h, prompt: events.append('hidden'))
        prompt = Prompt(lambda query: [])
        self.addCleanup(prompt.destroy)
        self.host.show_prompt(prompt)
        self.host.show_prompt(prompt)  # idempotent
        self.assertIs(self.host.prompt, prompt)
        self.host.hide_prompt()
        self.host.hide_prompt()
        self.assertEqual(events, ['shown', 'hidden'])
        self.assertIsNone(self.host._catcher.get_parent())

    def test_restore_skips_destroyed_previous_focus(self):
        entry = Gtk.Entry()
        box = Gtk.Box()
        self.host.remove(self.text)
        box.add(entry)
        self.host.add(box)
        box.show_all()
        pump(30)
        entry.grab_focus()
        prompt = Prompt(lambda query: [])
        self.addCleanup(prompt.destroy)
        self.host.show_prompt(prompt)
        entry.destroy()
        send_key(prompt.entry, Gdk.KEY_Escape)
        self.assertIsNone(self.host.prompt)

    def test_prompt_does_not_dim_the_content(self):
        before = self.pixel(5, 5)
        prompt = Prompt(lambda query: [])
        self.addCleanup(prompt.destroy)
        self.host.show_prompt(prompt)
        pump(60)
        self.assertEqual(self.pixel(5, 5), before)

    def pixel(self, x, y):
        tx, ty = self.host.translate_coordinates(self.window, x, y)
        return tuple(Gdk.pixbuf_get_from_window(self.window.get_window(), tx, ty, 1, 1)
                     .get_pixels()[:3])


if __name__ == '__main__':
    unittest.main()
