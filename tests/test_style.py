"""Theme registration, strict CSS parsing, and realized GTK widget checks."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from idgent import style
from idgent.editor import EditorPanel
from idgent.explorer import ExplorerPanel
from gi.repository import Gtk


class StyleTest(unittest.TestCase):
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
