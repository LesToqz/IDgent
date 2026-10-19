"""Right dock titlebar control, including all visibility and activation paths."""

import os
import tempfile
import unittest
from unittest.mock import patch

os.environ['GDK_BACKEND'] = 'x11'

import gi

gi.require_version('Gtk', '3.0')
from gi.repository import Gio, Gtk

from idgent.titlebar import GLYPH_OPACITY, TitleBar
from idgent.window import MainWindow
from tests.test_integration import poll


class DockButtonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = Gtk.Application.get_default() or Gtk.Application(
            application_id='dev.idgent.Tests', flags=Gio.ApplicationFlags.NON_UNIQUE)
        if not cls.application.get_is_registered():
            assert cls.application.register(None)

    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='idgent-dock-button-')
        self.addCleanup(folder.cleanup)
        environment = patch.dict(os.environ, XDG_CONFIG_HOME=folder.name)
        environment.start()
        self.addCleanup(environment.stop)
        self.window = MainWindow(self.application, folder.name)
        self.addCleanup(self.window.destroy)
        self.window.show_all()
        self.assertTrue(poll(lambda: self.window.titlebar.dock_button.get_mapped()))

    def assert_synced(self, visible):
        self.assertEqual(self.window.terminal_right.get_visible(), visible)
        self.assertEqual(self.window.titlebar.dock_button.get_active(), visible)

    def test_button_accessibility_geometry_fade_and_keyboard_activation(self):
        bar = self.window.titlebar
        button = bar.dock_button
        self.assertTrue(button.get_can_focus())
        self.assertEqual(button.get_accessible().get_name(), 'Toggle right dock')
        self.assertEqual(button.get_tooltip_text(),
                         'Toggle right terminal / graph dock (Ctrl+Alt+`)')
        self.assertEqual(button.get_child().get_icon_name()[0], 'sidebar-show-right-symbolic')
        self.assertEqual((button.get_allocated_width(), button.get_allocated_height()), (28, 28))
        x, y = button.translate_coordinates(bar, 0, 0)
        gear_x, gear_y = bar.settings_button.translate_coordinates(bar, 0, 0)
        self.assertEqual((x + 28 + button.get_margin_end(), y), (gear_x, gear_y))
        self.assertTrue(button.get_style_context().has_class('idgent-settings-button'))
        self.assertAlmostEqual(button.get_child().get_opacity(), GLYPH_OPACITY, delta=1 / 255)
        self.assertEqual(bar.dock_fader.duration, 'clickable_icon_opacity')
        # GtkButton's activate signal is the action used by keyboard Space/Enter.
        button.grab_focus()
        self.assertTrue(button.activate())
        self.assertTrue(poll(lambda: button.get_active()))
        self.assert_synced(True)
        self.assertEqual(self.window.terminal_right.terminal_count(), 1)
        self.assertTrue(button.get_state_flags() & Gtk.StateFlags.CHECKED)
        checked = button.get_style_context().get_background_color(Gtk.StateFlags.CHECKED)
        self.assertAlmostEqual(checked.alpha, .12, delta=.002)
        button.clicked()
        self.assert_synced(False)

    def test_shortcut_palette_hide_last_closed_and_fullscreen_sync(self):
        window = self.window
        self.assert_synced(False)
        key, modifiers = Gtk.accelerator_parse('<Control><Alt>grave')
        self.assertTrue(Gtk.accel_groups_activate(window, key, modifiers))
        self.assert_synced(True)
        command = next(c for c in window.commands() if c.label == 'View: Terminal on Right')
        command.callback()
        self.assert_synced(False)
        window.toggle_terminal_right()
        self.assert_synced(True)
        window._hide_terminal(window.terminal_right)
        self.assert_synced(False)
        window.toggle_terminal_right()
        window.terminal_bottom.toggle_maximize()
        self.assert_synced(False)
        window.terminal_bottom.restore()
        self.assert_synced(True)
        window.terminal_right.toggle_maximize()
        self.assert_synced(True)
        window.titlebar.dock_button.clicked()
        self.assertIsNone(window._fullscreen_panel)
        self.assert_synced(False)
        window.toggle_terminal_right()
        panel = window.terminal_right
        self.assertTrue(poll(lambda: panel._panes[0].pid is not None))
        panel._close_pane(panel._panes[0])
        self.assertTrue(poll(lambda: not panel.get_visible()))
        self.assert_synced(False)

    def test_programmatic_sync_does_not_emit_request_and_icon_fallbacks(self):
        bar = self.window.titlebar
        requested = []
        bar.connect('dock-toggle-requested', lambda _: requested.append(True))
        bar.set_dock_active(True)
        bar.set_dock_active(False)
        self.assertEqual(requested, [])
        for available in ('panel-right-symbolic', None):
            with self.subTest(available=available), patch.object(Gtk.IconTheme, 'get_default') as theme:
                theme.return_value.has_icon.side_effect = lambda name: name == available
                other = TitleBar(Gtk.MenuBar())
                try:
                    child = other.dock_button.get_child()
                    if available:
                        self.assertEqual(child.get_icon_name()[0], available)
                    else:
                        self.assertEqual(child.get_text(), '▯')
                finally:
                    other.destroy()
