"""Settings UI contract; run with GDK_BACKEND=x11 under xvfb."""
import os
import tempfile
import unittest
from unittest.mock import patch

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, Gtk, Pango

from idgent import settings
from idgent.settings_dialog import SettingsDialog, build_css
from idgent.themes import THEMES


class SettingsDialogTest(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        env = patch.dict(os.environ, XDG_CONFIG_HOME=root.name)
        env.start()
        self.addCleanup(env.stop)
        self.dialog = SettingsDialog()
        self.addCleanup(self.dialog.destroy)

    def test_defaults_and_theme_order(self):
        self.assertEqual(self.dialog.get_title(), 'Settings')
        self.assertEqual(self.dialog.theme_id, settings.get_theme_id())
        self.assertEqual(self.dialog.sizes, settings.DEFAULT_FONT_SIZES)
        self.assertEqual([r.theme_id for r in self.dialog.theme_list.get_children()], [t.id for t in THEMES])
        self.assertTrue(all(not b.get_sensitive() for b in self.dialog.reset_buttons.values()))

    def test_theme_selection_and_silent_setter(self):
        events = []
        self.dialog.connect('theme-selected', lambda _, theme: events.append(theme))
        self.dialog.theme_list.select_row(self.dialog.theme_rows['nord'])
        self.assertEqual(events, ['nord'])
        self.assertTrue(self.dialog.theme_buttons['nord'].get_active())
        self.dialog.theme_buttons['dracula'].set_active(True)
        self.assertEqual(events, ['nord', 'dracula'])
        self.dialog.set_theme_id('light-plus')
        self.assertEqual(self.dialog.theme_id, 'light-plus')
        self.dialog.set_theme_id('unknown')
        self.assertEqual(self.dialog.theme_id, 'dark-plus')
        self.assertEqual(events, ['nord', 'dracula'])

    def test_spin_reset_limits_samples_and_silent_setter(self):
        events = []
        self.dialog.connect('font-size-changed', lambda _, k, v: events.append((k, v)))
        for key in settings.FONT_KEYS:
            spin = self.dialog.spins[key]
            low, high = settings.FONT_SIZE_LIMITS[key]
            self.assertEqual((spin.get_adjustment().get_lower(), spin.get_adjustment().get_upper()), (low, high))
            spin.set_value(18)
            self.assertEqual(events[-1], (key, 18))
            attrs = self.dialog.samples[key].get_attributes().to_string()
            self.assertIn(f'size {18 * Pango.SCALE}', attrs)
            self.assertEqual('family "monospace"' in attrs, key != 'explorer_font_size')
            self.assertTrue(self.dialog.reset_buttons[key].get_sensitive())
            self.dialog.reset_buttons[key].clicked()
            self.assertEqual(events[-1], (key, settings.DEFAULT_FONT_SIZES[key]))
            self.assertFalse(self.dialog.reset_buttons[key].get_sensitive())
            spin.set_value(1000)
            self.assertEqual(events[-1], (key, high))
            spin.set_value(-1000)
            self.assertEqual(events[-1], (key, low))
        before = events[:]
        self.dialog.set_sizes({k: 13 for k in settings.FONT_KEYS})
        self.assertEqual(events, before)
        self.assertEqual(self.dialog.sizes, dict.fromkeys(settings.FONT_KEYS, 13))
        snapshot = self.dialog.sizes
        snapshot['terminal_font_size'] = 25
        self.assertEqual(self.dialog.sizes['terminal_font_size'], 13)
        self.assertEqual(settings.load(), {})  # UI never persists on its own.

    def test_initial_persistence_partial_arguments_and_parent(self):
        settings.set_theme_id('nord')
        settings.set_font_size('terminal_font_size', 17)
        dialog = SettingsDialog(parent=self.dialog)
        self.addCleanup(dialog.destroy)
        self.assertIs(dialog.get_transient_for(), self.dialog)
        self.assertTrue(dialog.get_destroy_with_parent())
        self.assertFalse(dialog.get_modal())
        dialog.set_modal(True)
        self.assertEqual(dialog.theme_id, 'nord')
        self.assertEqual(dialog.sizes['terminal_font_size'], 17)
        other = SettingsDialog(theme_id='dracula', sizes={'editor_font_size': 19})
        self.addCleanup(other.destroy)
        self.assertEqual(other.sizes, {**settings.DEFAULT_FONT_SIZES, 'editor_font_size': 19})
        self.assertEqual(other.theme_id, 'dracula')

    def test_close_escape_and_window_control_hide(self):
        self.dialog.show_all()
        event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        event.keyval = Gdk.KEY_Escape
        self.assertTrue(self.dialog.emit('key-press-event', event))
        self.assertFalse(self.dialog.get_visible())
        self.dialog.show_all()
        self.dialog.get_widget_for_response(Gtk.ResponseType.CLOSE).clicked()
        self.assertFalse(self.dialog.get_visible())
        self.dialog.show_all()
        self.assertTrue(self.dialog.emit('delete-event', Gdk.Event.new(Gdk.EventType.DELETE)))
        self.assertFalse(self.dialog.get_visible())

    def test_css_parses_for_every_theme(self):
        for theme in THEMES:
            provider = Gtk.CssProvider()
            errors = []
            provider.connect('parsing-error', lambda _, section, error: errors.append(str(error)))
            provider.load_from_data(build_css(theme.ui).encode())
            self.assertEqual(errors, [], theme.id)
