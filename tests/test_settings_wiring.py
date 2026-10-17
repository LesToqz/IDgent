"""Window settings integration against real GTK/VTE under Xvfb."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, Gtk, Pango

from idgent import settings
from idgent.window import MainWindow


class SettingsWiringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Gtk.Application(application_id='dev.idgent.SettingsTests')
        cls.app.register(None)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, XDG_CONFIG_HOME=self.temp.name)
        env.start()
        self.addCleanup(env.stop)
        self.root = Path(self.temp.name)
        self.windows = []
        self.addCleanup(self.cleanup_windows)

    def cleanup_windows(self):
        for window in self.windows:
            window.destroy()
        self.spin()

    def window(self):
        window = MainWindow(self.app, str(self.root))
        self.windows.append(window)
        return window

    @staticmethod
    def spin():
        deadline = time.monotonic() + .25
        while time.monotonic() < deadline:
            while Gtk.events_pending():
                Gtk.main_iteration()
            time.sleep(.005)

    def assert_sizes(self, window, sizes):
        self.assertEqual(window.editor.font_size, sizes['editor_font_size'])
        for tab in window.editor.tabs:
            font = tab.view.get_style_context().get_font(Gtk.StateFlags.NORMAL)
            self.assertEqual(font.get_size() / Pango.SCALE, sizes['editor_font_size'])
        self.assertEqual(window.explorer.font_size, sizes['explorer_font_size'])
        cell = window.explorer.tree.get_column(0).get_cells()[1]
        self.assertEqual(cell.get_property('size-points'), sizes['explorer_font_size'])
        for panel in (window.terminal_bottom, window.terminal_right):
            self.assertEqual(panel.font_size, sizes['terminal_font_size'])
            for pane in panel._panes:
                self.assertEqual(pane.terminal.get_font().get_size() / Pango.SCALE,
                                 sizes['terminal_font_size'])

    def test_menu_accelerator_lazy_reusable_nonmodal_dialog(self):
        window = self.window()
        self.assertIsNone(window._settings_dialog)
        window.show_all()
        menu = window.titlebar.menu_bar.get_children()[0].get_submenu()
        entry = next(i for i in menu.get_children() if i.get_label() == 'Settings…')
        entry.activate()
        dialog = window._settings_dialog
        self.assertIs(dialog.get_transient_for(), window)
        self.assertFalse(dialog.get_modal())
        dialog.response(Gtk.ResponseType.CLOSE)
        key, modifiers = Gtk.accelerator_parse('<Control>comma')
        with patch.object(dialog, 'present', wraps=dialog.present) as present:
            self.assertTrue(Gtk.accel_groups_activate(window, key, modifiers))
            present.assert_called_once()
        self.assertIs(window._settings_dialog, dialog)
        self.assertTrue(dialog.get_visible())

    def test_live_sizes_future_content_reset_and_reopen(self):
        path = self.root / 'first.py'
        path.write_text('print("hello")\n')
        window = self.window()
        window.editor.open_file(str(path))
        window.show_all()
        window.open_settings()
        sizes = dict(zip(settings.FONT_KEYS, (17, 18, 16)))
        for key, value in sizes.items():
            window._settings_dialog.spins[key].set_value(value)
        window.toggle_terminal_right()
        for panel in (window.terminal_bottom, window.terminal_right):
            panel.new_terminal()
        second = self.root / 'second.py'
        second.write_text('pass\n')
        window.editor.open_file(str(second))
        window.explorer.refresh()
        self.spin()
        self.assert_sizes(window, sizes)
        self.assertEqual(settings.get_font_sizes(), sizes)
        cell = window.explorer.tree.get_column(0).get_cells()[1]
        self.assertGreater(cell.get_fixed_size()[1], 20)
        other = self.window()
        self.assertFalse(other.get_realized())
        self.assert_sizes(other, sizes)
        other.open_settings()
        self.assertEqual(other._settings_dialog.sizes, sizes)
        for key in settings.FONT_KEYS:
            window._settings_dialog.reset_buttons[key].clicked()
        self.spin()
        self.assert_sizes(window, settings.DEFAULT_FONT_SIZES)
        self.assertEqual(settings.get_font_sizes(), settings.DEFAULT_FONT_SIZES)

    def test_failed_save_still_changes_live_and_reused_dialog(self):
        window = self.window()
        window.open_settings()
        with patch.object(settings, 'save', return_value=False):
            for key in settings.FONT_KEYS:
                window._settings_dialog.spins[key].set_value(19)
        self.assert_sizes(window, dict.fromkeys(settings.FONT_KEYS, 19))
        window._settings_dialog.hide()
        window.open_settings()
        self.assertEqual(window._settings_dialog.sizes, dict.fromkeys(settings.FONT_KEYS, 19))

    def test_invalid_saved_sizes_are_safe_before_first_frame(self):
        target = self.root / 'idgent/settings.json'
        target.parent.mkdir()
        for raw, expected in ((dict.fromkeys(settings.FONT_KEYS, 'bad'), settings.DEFAULT_FONT_SIZES),
                              (dict(zip(settings.FONT_KEYS, (-100, 9999, None))),
                               dict(zip(settings.FONT_KEYS, (7, 32, 10))))):
            with self.subTest(raw=raw):
                target.write_text(json.dumps(raw))
                window = self.window()
                self.assertFalse(window.get_realized())
                self.assert_sizes(window, expected)
                window.open_settings()
                self.assertEqual(window._settings_dialog.sizes, expected)

    def test_terminal_base_font_preserves_zoom_and_commits_gate(self):
        window = self.window()
        window.show_all()
        self.spin()
        panel = window.terminal_bottom
        terminal = panel._panes[0].terminal
        terminal.set_font_scale(1.3)
        with patch.object(panel._panes[0].frame, 'commit',
                          wraps=panel._panes[0].frame.commit) as commit:
            panel.set_font_size(18)
            self.spin()
            self.assertGreater(commit.call_count, 0)
        self.assertAlmostEqual(terminal.get_font_scale(), 1.3)
        event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        event.keyval = Gdk.KEY_0
        event.state = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK
        self.assertTrue(panel._key_press(terminal, event))
        self.assertEqual(terminal.get_font_scale(), 1)
        self.assertEqual(terminal.get_font().get_size() / Pango.SCALE, 18)

    def test_font_changes_preserve_real_shell_prompt_in_both_docks(self):
        from idgent.terminal import Vte
        from tests.test_resizegate import BashDrag, PS1, poll, spin

        cwd = self.root / 'projects' / 'long-prompt-directory'
        cwd.mkdir(parents=True)
        original_spawn = Vte.Terminal.spawn_async

        pending = []

        def defer_spawn(terminal, *args, **kwargs):
            pending.append((terminal, args, kwargs))

        with patch.object(Vte.Terminal, 'spawn_async', defer_spawn):
            window = MainWindow(self.app, str(cwd))
            self.windows.append(window)
            window.show_all()
            window.toggle_terminal_right()
            window.dock.set_position(500)
            for panel in (window.terminal_bottom, window.terminal_right):
                panel.new_terminal()
            spin(350)
        for terminal, args, kwargs in pending:
            args = list(args)
            args[2] = ['/bin/bash', '--norc', '-i']
            environment = dict(os.environ, PS1=PS1, TERM='xterm-256color',
                               PROMPT_COMMAND="printf '\\n%.0s' {1..100}; unset PROMPT_COMMAND")
            args[3] = [f'{k}={v}' for k, v in environment.items()]
            original_spawn(terminal, *args, **kwargs)
        terminals = [pane.terminal for panel in
                     (window.terminal_bottom, window.terminal_right) for pane in panel._panes]
        self.assertTrue(poll(lambda: all('long-prompt-directory' in BashDrag.text(t)
                                        for t in terminals)))
        spin(350)
        harness = BashDrag.__new__(BashDrag)
        harness.terminals, harness.cwd = terminals, cwd
        harness.logs = [self.root / f'unused-{i}' for i in range(len(terminals))]
        for metric in harness.metrics():
            self.assertFalse(metric["garbage"], metric)
        for size in (16, 20, 11):
            for panel in (window.terminal_bottom, window.terminal_right):
                panel.set_font_size(size)
            spin(350)
            for metric in harness.metrics():
                self.assertEqual(metric['history_copies'], 1, metric)
                self.assertFalse(metric['garbage'], metric)
