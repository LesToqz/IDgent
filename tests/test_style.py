"""Theme registration, strict CSS parsing, and realized GTK widget checks."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

# Keep GTK on the Xvfb display even when WAYLAND_DISPLAY is inherited.
os.environ["GDK_BACKEND"] = "x11"

from idgent import style
from idgent.editor import EditorPanel
from idgent.explorer import ExplorerPanel
from idgent.terminal import TerminalPanel
from gi.repository import Gtk, Vte


class StyleTest(unittest.TestCase):
    @staticmethod
    def poll(predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            while Gtk.events_pending():
                Gtk.main_iteration()
            if predicate():
                return True
            time.sleep(0.01)
        return False

    def test_terminal_scrollbar_tracks_scrollback(self):
        style.apply_style()
        with tempfile.TemporaryDirectory() as root:
            window = Gtk.Window()
            self.addCleanup(window.destroy)
            window.set_default_size(640, 320)
            panel = TerminalPanel(root)
            window.add(panel)
            window.show_all()
            pane = panel._panes[0]
            terminal = pane.terminal
            scrollbar = pane.page.get_children()[1]
            adjustment = terminal.get_vadjustment()
            self.assertTrue(self.poll(lambda: pane.pid is not None))
            self.assertFalse(scrollbar.get_visible())
            self.assertLessEqual(adjustment.get_upper(), adjustment.get_page_size())
            self.assertLess(terminal.get_row_count(), 200)
            initial_width = terminal.get_allocated_width()
            terminal.feed_child(b'seq 1 200\n')
            self.assertTrue(self.poll(lambda: scrollbar.get_visible()
                                     and scrollbar.get_allocated_width() > 1
                                     and '200' in (terminal.get_text_format(Vte.Format.TEXT)
                                                   or '').splitlines()))
            self.assertGreater(adjustment.get_upper(), adjustment.get_page_size())
            self.assertEqual(scrollbar.get_allocated_width(), 8)
            width = terminal.get_allocated_width()
            self.assertEqual(width, initial_width)
            adjustment.set_value(adjustment.get_lower())
            self.assertTrue(scrollbar.get_visible())
            self.assertEqual(terminal.get_allocated_width(), width)
            terminal.reset(True, True)
            self.assertTrue(self.poll(lambda: not scrollbar.get_visible()))
            self.assertEqual(terminal.get_allocated_width(), initial_width)
            # Parent show_all() calls must preserve the hidden state after reset.
            window.show_all()
            self.assertFalse(scrollbar.get_visible())

    def test_non_overlay_scrollbars_are_eight_pixels(self):
        style.apply_style()
        window = Gtk.Window()
        self.addCleanup(window.destroy)
        window.set_default_size(320, 240)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_overlay_scrolling(False)
        content = Gtk.Label(label='Scrollable content')
        content.set_size_request(1000, 1000)
        scrolled.add(content)
        window.add(scrolled)
        window.show_all()
        self.assertTrue(self.poll(lambda: scrolled.get_vscrollbar().get_allocated_width() > 1
                                 and scrolled.get_hscrollbar().get_allocated_height() > 1))
        self.assertEqual(scrolled.get_vscrollbar().get_allocated_width(), 8)
        self.assertEqual(scrolled.get_hscrollbar().get_allocated_height(), 8)

    def test_apply_is_idempotent(self):
        # Start unregistered even if an application test already applied the theme.
        if style._provider is not None:
            Gtk.StyleContext.remove_provider_for_screen(style._screen, style._provider)
            style._provider = style._screen = None
        with patch.object(Gtk.StyleContext, 'add_provider_for_screen',
                          wraps=Gtk.StyleContext.add_provider_for_screen) as register:
            style.apply_style()
            provider = style._provider
            style.apply_style()
        self.assertIs(style._provider, provider)
        register.assert_called_once_with(style._screen, provider,
                                         Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.assertTrue(Gtk.Settings.get_default().get_property('gtk-application-prefer-dark-theme'))

    def test_css_has_no_parsing_errors(self):
        provider = Gtk.CssProvider()
        errors = []
        provider.connect('parsing-error', lambda _, section, error: errors.append(str(error)))
        provider.load_from_data(style.CSS.encode('utf-8'))
        self.assertEqual(errors, [])

    def test_invalid_css_raises_clear_exception(self):
        with patch.object(style, 'CSS', 'label { made-up-property: invalid; }'):
            with self.assertRaisesRegex(RuntimeError, 'IDgent theme CSS failed to parse'):
                style._create_provider()

    def test_realized_panels_and_status_background(self):
        style.apply_style()
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'example.py'
            path.write_text('print("Dark+")\n')
            window = Gtk.Window()
            self.addCleanup(window.destroy)
            window.set_default_size(900, 600)
            paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
            explorer, editor = ExplorerPanel(root), EditorPanel()
            paned.pack1(explorer, False, False)
            paned.pack2(editor, True, False)
            paned.set_position(240)
            window.add(paned)
            editor.open_file(str(path))
            window.show_all()
            while Gtk.events_pending():
                Gtk.main_iteration()
            self.assertTrue(explorer.get_realized())
            self.assertTrue(editor.get_realized())
            self.assertTrue(editor.status.get_style_context().has_class('idgent-status'))
            background = editor.status.get_style_context().get_background_color(Gtk.StateFlags.NORMAL)
            self.assertAlmostEqual(background.red, 0, places=2)
            self.assertAlmostEqual(background.green, 122 / 255, places=2)
            self.assertAlmostEqual(background.blue, 204 / 255, places=2)
            # The five-pixel separator retains a useful drag target around its one-pixel line.
            handle = paned.get_handle_window().get_geometry()
            self.assertGreaterEqual(handle.width, 5)
            nodes = editor.notebook.get_style_context().to_string(Gtk.StyleContextPrintFlags.RECURSE)
            self.assertIn('header.top', nodes)
            self.assertIn('tabs:', nodes)
            self.assertIn('tab:', nodes)
