"""Client-side titlebar layout, theme, folder title, and menu accelerators."""

import os
from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import patch

os.environ["GDK_BACKEND"] = "x11"

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, Gtk

from idgent import motion
from idgent.titlebar import (GLYPH_OPACITY, HEADER_HEIGHT, TITLEBAR_CSS, WINDOW_BUTTON_WIDTH,
                             TitleBar, build_titlebar_css)
from idgent.themes import THEMES, get_theme
from idgent.window import MainWindow
from tests.test_integration import alive, poll


def descendants(widget):
    yield widget
    if isinstance(widget, Gtk.Container):
        children = []
        widget.forall(children.append)
        for child in children:
            yield from descendants(child)


def rgba_close(actual, expected, tolerance=0.002):
    return all(abs(getattr(actual, channel) - getattr(expected, channel)) <= tolerance
               for channel in ('red', 'green', 'blue', 'alpha'))


def rgb(hex_colour):
    return tuple(int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))


def blend(base, overlay, alpha):
    return tuple(round(b * (1 - alpha) + o * alpha) for b, o in zip(rgb(base), rgb(overlay)))


def find_class(root, name):
    return next(w for w in descendants(root) if w.get_style_context().has_class(name))


class TitleBarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = Gtk.Application.get_default() or Gtk.Application(
            application_id="dev.idgent.Tests", flags=Gio.ApplicationFlags.NON_UNIQUE)
        if not cls.application.get_is_registered():
            assert cls.application.register(None)

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="idgent-titlebar-")
        self.addCleanup(self.folder.cleanup)
        environment = patch.dict(os.environ, XDG_CONFIG_HOME=self.folder.name)
        environment.start()
        self.addCleanup(environment.stop)
        self.window = MainWindow(self.application, self.folder.name)
        self.addCleanup(self.destroy_window)
        self.window.show_all()
        self.assertTrue(poll(lambda: self.window.terminal_bottom._panes[0].pid is not None))

    def destroy_window(self):
        pids = [pane.pid for panel in (self.window.terminal_bottom, self.window.terminal_right)
                for pane in panel._panes if pane.pid is not None]
        self.window.destroy()
        stopped = poll(lambda: all(not alive(pid) for pid in pids))
        for pid in pids:
            if alive(pid):
                os.kill(pid, signal.SIGKILL)
        self.assertTrue(stopped, "shell survived window destruction")

    def test_builder_matches_legacy_dark_plus_and_parses_all_themes(self):
        self.assertEqual(TITLEBAR_CSS, build_titlebar_css(get_theme(None).ui))
        for theme in THEMES:
            with self.subTest(theme=theme.id):
                provider = Gtk.CssProvider()
                errors = []
                provider.connect('parsing-error', lambda _, section, error: errors.append(str(error)))
                css = build_titlebar_css(theme.ui)
                provider.load_from_data(css.encode('utf-8'))
                self.assertEqual(errors, [])
                self.assertIn(theme.ui['sidebar_bg'], css)
                self.assertIn(theme.ui['danger'], css)
                self.assertIn(f"border-bottom: 1px solid {theme.ui['border']}", css)
                self.assertIn('button.idgent-settings-button:hover', css)

    def test_settings_button_accessibility_and_signal(self):
        bar = self.window.titlebar
        button = bar.settings_button
        self.assertIsInstance(button, Gtk.Button)
        self.assertTrue(button.get_visible())
        self.assertEqual(button.get_relief(), Gtk.ReliefStyle.NONE)
        self.assertTrue(button.get_style_context().has_class('flat'))
        self.assertTrue(button.get_style_context().has_class('idgent-settings-button'))
        self.assertEqual(button.get_tooltip_text(), 'Settings (Ctrl+,)')
        self.assertEqual(button.get_accessible().get_name(), 'Settings')
        requested = []
        bar.connect('settings-requested', lambda widget: requested.append(widget))
        button.clicked()
        self.assertEqual(requested, [bar])
        self.assertTrue(self.window._settings_dialog.get_visible())

    def test_settings_icon_fallback_chain_and_glyph(self):
        names = ('preferences-system-symbolic', 'emblem-system-symbolic',
                 'preferences-other-symbolic')
        for available in (*names, None):
            with self.subTest(available=available), patch.object(Gtk.IconTheme, 'get_default') as get_theme:
                get_theme.return_value.has_icon.side_effect = lambda name: name == available
                bar = TitleBar(Gtk.MenuBar())
                try:
                    child = bar.settings_button.get_child()
                    if available is None:
                        self.assertIsInstance(child, Gtk.Label)
                        self.assertEqual(child.get_text(), '⚙')
                    else:
                        self.assertIsInstance(child, Gtk.Image)
                        self.assertEqual(child.get_icon_name()[0], available)
                    expected = names if available is None else names[:names.index(available) + 1]
                    self.assertEqual([call.args[0] for call in get_theme.return_value.has_icon.call_args_list],
                                     list(expected))
                finally:
                    bar.destroy()

    def test_settings_immediately_precedes_minimize_in_real_allocations(self):
        bar = self.window.titlebar
        gear = bar.settings_button
        widgets = list(descendants(bar))
        minimize = next(w for w in widgets if isinstance(w, Gtk.Button)
                        and w.get_style_context().has_class('minimize'))
        self.assertTrue(gear.get_mapped())
        self.assertTrue(minimize.get_mapped())
        gear_x, _ = gear.translate_coordinates(bar, 0, 0)
        right = gear_x + gear.get_allocated_width()
        minimize_x, _ = minimize.translate_coordinates(bar, 0, 0)
        self.assertLessEqual(right, minimize_x)
        self.assertEqual((gear.get_allocated_width(), gear.get_allocated_height()), (28, 28))
        for widget in widgets:
            if not widget.get_mapped():
                continue
            x, _ = widget.translate_coordinates(bar, 0, 0)
            if right <= x < minimize_x and x + widget.get_allocated_width() <= minimize_x:
                # GTK inserts a decoration separator, painted fully transparent by our CSS.
                self.assertIsInstance(widget, Gtk.Separator)
                self.assertEqual(widget.get_style_context().get_background_color(
                    Gtk.StateFlags.NORMAL).alpha, 0)

    def test_settings_button_style_tracks_live_theme_switch(self):
        gear = self.window.titlebar.settings_button
        context = gear.get_style_context()
        minimize = next(w for w in descendants(self.window.titlebar)
                        if isinstance(w, Gtk.Button) and w.get_style_context().has_class('minimize'))
        for theme in (get_theme('dark-plus'), next(t for t in THEMES if not t.dark)):
            with self.subTest(theme=theme.id):
                self.window.set_theme(theme.id, persist=False)
                foreground = Gdk.RGBA()
                foreground.parse(theme.ui['muted'])
                hover = Gdk.RGBA()
                hover.parse(theme.ui['overlay'])
                hover.alpha = 0.067
                self.assertTrue(poll(lambda: context.get_color(Gtk.StateFlags.NORMAL).equal(foreground)))
                self.assertEqual(context.get_color(Gtk.StateFlags.NORMAL),
                                 minimize.get_style_context().get_color(Gtk.StateFlags.NORMAL))
                gear.set_state_flags(Gtk.StateFlags.PRELIGHT, False)
                try:
                    self.assertTrue(poll(lambda: rgba_close(
                        context.get_background_color(Gtk.StateFlags.PRELIGHT), hover)))
                    self.assertEqual((gear.get_allocated_width(), gear.get_allocated_height()), (28, 28))
                finally:
                    gear.unset_state_flags(Gtk.StateFlags.PRELIGHT)

    def test_menus_share_header_and_leave_content(self):
        bar = self.window.get_titlebar()
        self.assertIs(bar, self.window.titlebar)
        self.assertIsInstance(bar, TitleBar)
        self.assertIsInstance(bar, Gtk.HeaderBar)
        menus = [widget for widget in descendants(bar) if isinstance(widget, Gtk.MenuItem)]
        self.assertEqual([item.get_label() for item in menus], ["File", "View"])
        self.assertTrue(all(item.is_ancestor(bar) for item in menus))
        self.assertFalse(any(isinstance(widget, Gtk.MenuBar)
                             for widget in descendants(self.window.get_child())))
        self.assertEqual(bar.menu_bar.get_valign(), Gtk.Align.CENTER)
        self.assertEqual(bar.get_decoration_layout(), ":minimize,maximize,close")
        self.assertTrue(bar.get_show_close_button())

    def test_title_is_just_the_application_name(self):
        bar = self.window.titlebar
        self.assertIs(bar.get_custom_title(), bar.title_label)
        self.assertTrue(bar.title_label.get_style_context().has_class("idgent-title"))
        self.assertEqual(bar.title_label.get_text(), "IDgent")
        self.assertEqual(self.window.get_title(), "IDgent")
        with tempfile.TemporaryDirectory(prefix="idgent-titlebar-new-") as folder:
            self.window.set_root(folder)
            self.assertEqual(bar.title_label.get_text(), "IDgent")
            self.assertEqual(self.window.get_title(), "IDgent")
        self.assertFalse(hasattr(bar, "set_folder"))

    def test_css_parses_and_keeps_title_centered_in_compact_bar(self):
        provider = Gtk.CssProvider()
        errors = []
        provider.connect("parsing-error", lambda _, section, error: errors.append(str(error)))
        provider.load_from_data(TITLEBAR_CSS.encode("utf-8"))
        self.assertEqual(errors, [])
        screen = Gdk.Screen.get_default()
        Gtk.StyleContext.add_provider_for_screen(
            screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        self.addCleanup(Gtk.StyleContext.remove_provider_for_screen, screen, provider)
        bar = self.window.titlebar
        self.assertTrue(poll(lambda: bar.get_allocated_height() == HEADER_HEIGHT))
        label = bar.title_label
        allocation = label.get_allocation()
        x, _ = label.translate_coordinates(bar, 0, 0)
        self.assertAlmostEqual(x + allocation.width / 2,
                               bar.get_allocated_width() / 2, delta=1)

    def pixel(self, widget, x, y):
        wx, wy = widget.translate_coordinates(self.window, x, y)
        return tuple(Gdk.pixbuf_get_from_window(self.window.get_window(), wx, wy, 1, 1).get_pixels()[:3])

    def assert_pixel(self, widget, x, y, expected, tolerance=2):
        matches = lambda: all(abs(a - b) <= tolerance for a, b in zip(self.pixel(widget, x, y), expected))
        self.assertTrue(poll(matches), f"{self.pixel(widget, x, y)} != {expected}")

    def glyph_pixels(self, widget, background):
        """Pixels of a widget that differ clearly from its background colour."""
        x, y = widget.translate_coordinates(self.window, 0, 0)
        width, height = widget.get_allocated_width(), widget.get_allocated_height()
        pixbuf = Gdk.pixbuf_get_from_window(self.window.get_window(), x, y, width, height)
        data, stride, channels = pixbuf.get_pixels(), pixbuf.get_rowstride(), pixbuf.get_n_channels()
        found = []
        for row in range(height):
            for column in range(width):
                offset = row * stride + column * channels
                colour = tuple(data[offset:offset + 3])
                if max(abs(a - b) for a, b in zip(colour, background)) > 40:
                    found.append((column, row, colour))
        return found

    def use_theme(self, theme_id):
        self.window.set_theme(theme_id, persist=False)
        self.assertTrue(poll(lambda: self.window.titlebar.get_allocated_height() == HEADER_HEIGHT))

    def hover(self, widget):
        widget.set_state_flags(Gtk.StateFlags.PRELIGHT, False)
        self.addCleanup(widget.unset_state_flags, Gtk.StateFlags.PRELIGHT)

    def test_header_is_39_plus_divider_on_secondary_background(self):
        bar = self.window.titlebar
        for theme_id in ('idgent-dark', 'idgent-light', 'dark-plus'):
            with self.subTest(theme=theme_id):
                self.use_theme(theme_id)
                ui = get_theme(theme_id).ui
                self.assertEqual(bar.get_preferred_height().minimum_height, HEADER_HEIGHT)
                x = bar.title_label.translate_coordinates(bar, 0, 0)[0] - 60
                self.assertGreater(x, bar.menu_bar.get_allocated_width() + 20)
                self.assert_pixel(bar, x, 20, rgb(ui['sidebar_bg']))
                self.assert_pixel(bar, x, 0, rgb(ui['sidebar_bg']))
                self.assert_pixel(bar, x, HEADER_HEIGHT - 2, rgb(ui['sidebar_bg']))
                self.assert_pixel(bar, x, HEADER_HEIGHT - 1, rgb(ui['border']))
                bar.set_state_flags(Gtk.StateFlags.BACKDROP, False)
                try:
                    # Same background unfocused; text drops to muted.
                    self.assert_pixel(bar, x, 20, rgb(ui['sidebar_bg']))
                    muted = Gdk.RGBA()
                    muted.parse(ui['muted'])
                    context = bar.get_style_context()
                    self.assertTrue(poll(lambda: context.get_color(context.get_state()).equal(muted)))
                finally:
                    bar.unset_state_flags(Gtk.StateFlags.BACKDROP)

    def test_window_buttons_are_flat_44px_with_12px_glyphs_and_overlay_hover(self):
        bar = self.window.titlebar
        self.use_theme('idgent-dark')
        ui = get_theme('idgent-dark').ui
        buttons = [find_class(bar, name) for name in ('minimize', 'maximize', 'close')]
        positions = [button.translate_coordinates(bar, 0, 0) for button in buttons]
        for button, (x, y) in zip(buttons, positions):
            self.assertEqual((button.get_allocated_width(), button.get_allocated_height()),
                             (WINDOW_BUTTON_WIDTH, HEADER_HEIGHT - 1))
            self.assertEqual(y, 0)
            glyph = self.glyph_pixels(button, rgb(ui['sidebar_bg']))
            self.assertTrue(glyph, button.get_style_context().list_classes())
            columns = [column for column, _, _ in glyph]
            rows = [row for _, row, _ in glyph]
            self.assertLessEqual(max(columns) - min(columns) + 1, 12)
            self.assertLessEqual(max(rows) - min(rows) + 1, 12)
        # One contiguous 132 px block flush with the right edge.
        self.assertEqual([x for x, _ in positions],
                         [positions[0][0] + WINDOW_BUTTON_WIDTH * i for i in range(3)])
        self.assertEqual(positions[2][0] + WINDOW_BUTTON_WIDTH, bar.get_allocated_width())
        minimize, maximize, close = buttons
        self.assert_pixel(minimize, 2, 2, rgb(ui['sidebar_bg']))
        self.hover(minimize)
        self.assert_pixel(minimize, 2, 2, blend(ui['sidebar_bg'], ui['overlay'], 0.067))
        self.assert_pixel(maximize, 2, 2, rgb(ui['sidebar_bg']))
        foreground = Gdk.RGBA()
        foreground.parse(ui['foreground'])
        self.assertTrue(minimize.get_style_context().get_color(Gtk.StateFlags.PRELIGHT).equal(foreground))

    def test_close_hover_is_danger_with_white_glyph(self):
        bar = self.window.titlebar
        close = find_class(bar, 'close')
        for theme_id in ('idgent-dark', 'idgent-light'):
            with self.subTest(theme=theme_id):
                self.use_theme(theme_id)
                ui = get_theme(theme_id).ui
                close.set_state_flags(Gtk.StateFlags.PRELIGHT, False)
                try:
                    for x, y in ((0, 0), (2, 2), (WINDOW_BUTTON_WIDTH - 1, HEADER_HEIGHT - 2)):
                        self.assert_pixel(close, x, y, rgb(ui['danger']))
                    white = Gdk.RGBA()
                    white.parse('#ffffff')
                    self.assertTrue(close.get_style_context().get_color(Gtk.StateFlags.PRELIGHT).equal(white))
                    glyph = self.glyph_pixels(close, rgb(ui['danger']))
                    self.assertTrue(glyph)
                    self.assertGreaterEqual(max(min(colour) for _, _, colour in glyph), 200)
                finally:
                    close.unset_state_flags(Gtk.StateFlags.PRELIGHT)
                self.assert_pixel(close, 2, 2, rgb(ui['sidebar_bg']))

    def test_menubar_items_are_flat_13px_clickable_icons(self):
        bar = self.window.titlebar
        self.use_theme('idgent-light')
        ui = get_theme('idgent-light').ui
        item = bar.menu_bar.get_children()[0]
        context = item.get_style_context()
        self.assertEqual(context.get_property('font-size', Gtk.StateFlags.NORMAL), 13)
        # Same hit-area height as the settings cog, centred in the 39 px strip.
        self.assertEqual(item.get_allocated_height(), bar.settings_button.get_allocated_height())
        _, y = item.translate_coordinates(bar, 0, 0)
        self.assertAlmostEqual(y + item.get_allocated_height() / 2, (HEADER_HEIGHT - 1) / 2, delta=1)
        height = item.get_allocated_height()
        self.assert_pixel(item, 2, height // 2, rgb(ui['sidebar_bg']))
        self.hover(item)
        hover = blend(ui['sidebar_bg'], ui['overlay'], 0.067)
        self.assert_pixel(item, 2, height // 2, hover)
        # Flat: no underline/inset shadow at the bottom edge; rounded corners.
        self.assert_pixel(item, item.get_allocated_width() // 2, height - 1, hover)
        self.assert_pixel(item, 0, 0, rgb(ui['sidebar_bg']))

    def test_title_is_13px_semibold_muted(self):
        bar = self.window.titlebar
        for theme_id in ('idgent-dark', 'idgent-light'):
            with self.subTest(theme=theme_id):
                self.use_theme(theme_id)
                context = bar.title_label.get_style_context()
                colour = Gdk.RGBA()
                colour.parse(get_theme(theme_id).ui['title_fg'])
                self.assertTrue(poll(lambda: context.get_color(Gtk.StateFlags.NORMAL).equal(colour)))
                self.assertEqual(get_theme(theme_id).ui['title_fg'], get_theme(theme_id).ui['muted'])
                self.assertEqual(context.get_property('font-size', Gtk.StateFlags.NORMAL), 13)
                self.assertEqual(int(context.get_property('font-weight', Gtk.StateFlags.NORMAL)), 600)
                _, y = bar.title_label.translate_coordinates(bar, 0, 0)
                self.assertAlmostEqual(y + bar.title_label.get_allocated_height() / 2,
                                       (HEADER_HEIGHT - 1) / 2, delta=1)

    def crossing(self, widget, event_type, signal):
        event = Gdk.Event.new(event_type)
        event.crossing.detail = Gdk.NotifyType.NONLINEAR
        widget.emit(signal, event)

    def test_settings_cog_is_a_clickable_icon_with_glyph_fade(self):
        bar = self.window.titlebar
        gear = bar.settings_button
        glyph = gear.get_child()
        self.use_theme('idgent-dark')
        ui = get_theme('idgent-dark').ui
        self.assertAlmostEqual(glyph.get_opacity(), GLYPH_OPACITY, delta=1 / 255)
        self.assertEqual(bar.settings_fader.duration, 'clickable_icon_opacity')
        self.assertEqual(motion.duration_ms(bar.settings_fader.duration), 140)
        self.hover(gear)
        height = gear.get_allocated_height()
        self.assert_pixel(gear, 2, height // 2, blend(ui['sidebar_bg'], ui['overlay'], 0.067))
        self.assert_pixel(gear, 0, 0, rgb(ui['sidebar_bg']))  # radius 4
        self.crossing(gear, Gdk.EventType.ENTER_NOTIFY, 'enter-notify-event')
        self.assertLess(glyph.get_opacity(), 1)  # fades on the frame clock, not instantly
        seen = []
        self.assertTrue(poll(lambda: seen.append(glyph.get_opacity()) or glyph.get_opacity() == 1))
        self.assertEqual(seen, sorted(seen))
        self.crossing(gear, Gdk.EventType.LEAVE_NOTIFY, 'leave-notify-event')
        self.assertTrue(poll(lambda: abs(glyph.get_opacity() - GLYPH_OPACITY) <= 1 / 255))

    def test_settings_glyph_fade_honours_reduced_motion(self):
        motion.set_reduce_motion(True)
        self.addCleanup(motion.set_reduce_motion, False)
        gear = self.window.titlebar.settings_button
        self.crossing(gear, Gdk.EventType.ENTER_NOTIFY, 'enter-notify-event')
        self.assertEqual(gear.get_child().get_opacity(), 1)
        self.crossing(gear, Gdk.EventType.LEAVE_NOTIFY, 'leave-notify-event')
        self.assertAlmostEqual(gear.get_child().get_opacity(), GLYPH_OPACITY, delta=1 / 255)

    def accelerate(self, shortcut):
        key, modifiers = Gtk.accelerator_parse(shortcut)
        self.assertTrue(Gtk.accel_groups_activate(self.window, key, modifiers), shortcut)

    def test_all_accelerators_fire_actions(self):
        self.accelerate("<Control>grave")
        self.assertFalse(self.window.terminal_bottom.get_visible())
        self.accelerate("<Control>grave")
        self.assertTrue(self.window.terminal_bottom.get_visible())
        self.accelerate("<Control><Alt>grave")
        self.assertTrue(self.window.terminal_right.get_visible())
        self.assertTrue(poll(lambda: self.window.terminal_right._panes[0].pid is not None))
        self.window.terminal_right.focus_terminal()
        self.accelerate("<Control><Shift>5")
        self.assertEqual(self.window.terminal_right.terminal_count(), 2)
        self.assertTrue(poll(lambda: self.window.terminal_right._panes[-1].pid is not None))
        self.accelerate("<Control>b")
        self.assertFalse(self.window.explorer.get_visible())
        self.accelerate("<Control>b")
        self.assertTrue(self.window.explorer.get_visible())

        paths = [Path(self.folder.name) / name for name in ("one.py", "two.py")]
        for path in paths:
            path.write_text("original\n")
            self.window.editor.open_file(str(path))
            self.window.editor._current().buffer.set_text("saved all\n")
        self.accelerate("<Control>s")
        self.assertEqual(paths[1].read_text(), "saved all\n")
        self.assertEqual(paths[0].read_text(), "original\n")
        self.accelerate("<Control><Shift>s")
        self.assertEqual(paths[0].read_text(), "saved all\n")
        self.assertFalse(self.window.editor.has_unsaved())
        self.window.editor._current().buffer.set_text("quit cancelled\n")
        with patch.object(self.window, "_ask_quit", return_value=Gtk.ResponseType.CANCEL) as ask:
            self.accelerate("<Control>q")
            self.assertTrue(poll(lambda: ask.called))
            self.assertTrue(self.window.get_visible())


if __name__ == "__main__":
    unittest.main()
