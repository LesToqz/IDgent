"""Theme registration, strict CSS parsing, and realized GTK widget checks."""
import os
from pathlib import Path
import re
import tempfile
import time
import unittest
from unittest.mock import patch

# Keep GTK on the Xvfb display even when WAYLAND_DISPLAY is inherited.
os.environ["GDK_BACKEND"] = "x11"

from idgent import style
from idgent.settings_dialog import build_css as build_settings_css
from idgent.search import build_css as build_search_css
from idgent.editor import EditorPanel, build_css as build_editor_css
from idgent.explorer import ExplorerPanel, build_css as build_explorer_css
from idgent.terminal import TerminalPanel
from idgent.terminal_bar import AddButton, LimitToast, PaneHeader, build_css as build_terminal_css
from idgent.titlebar import TITLEBAR_CSS, TitleBar
from idgent.themes import THEMES, get_theme
from dataclasses import replace
from gi.repository import Gdk
from gi.repository import Gtk, Vte


class StyleTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(style.apply_style)

    def assert_color(self, widget, expected, *, background=False):
        color = Gdk.RGBA()
        self.assertTrue(color.parse(expected))
        context = widget.get_style_context()
        getter = context.get_background_color if background else context.get_color

        def matches():
            actual = getter(Gtk.StateFlags.NORMAL)
            return all(abs(a - b) < .005 for a, b in zip(
                (actual.red, actual.green, actual.blue, actual.alpha),
                (color.red, color.green, color.blue, color.alpha)))

        self.assertTrue(self.poll(matches),
                        f'{getter(Gtk.StateFlags.NORMAL).to_string()} != {expected}')

    def test_every_theme_parses(self):
        for theme in THEMES:
            with self.subTest(theme=theme.id):
                provider = Gtk.CssProvider()
                errors = []
                provider.connect('parsing-error', lambda _, section, error: errors.append(str(error)))
                provider.load_from_data(style.build_css(theme).encode('utf-8'))
                self.assertEqual(errors, [])
                self.assertIn('.idgent-resize-gate', style.build_css(theme))
                self.assertIn(build_settings_css(theme.ui), style.build_css(theme))
                self.assertIn(build_search_css(theme.ui), style.build_css(theme))
                for builder in (build_explorer_css, build_editor_css, build_terminal_css):
                    self.assertIn(builder(theme.ui), style.build_css(theme))

    def test_idgent_geometry_and_css_tokens(self):
        self.assertEqual(style.TOKENS["spacing"], {
            "tight": 3, "compact": 6, "control": 9, "section": 15, "panel": 21, "canvas": 33,
        })
        self.assertEqual(style.TOKENS["radius"], {
            "button": 7, "input": 7, "menu": 11, "item": 5, "tooltip": 6, "scrollbar": 4,
        })
        from idgent.motion import DURATIONS, _EASING_POINTS
        self.assertEqual(style.TOKENS["durations"], {"input_border": DURATIONS["state"], "resize_handle": DURATIONS["state"]})
        controls = tuple(float(v) for v in re.findall(r"[\d.]+", style.TOKENS["easings"]["state"]))
        self.assertEqual(controls, _EASING_POINTS["smooth"])
        css = style.build_css(get_theme(None))
        self.assertIn("font-size: 14px", css)
        self.assertIn("border-radius: 11px", css)
        self.assertIn("0 5px 17px alpha(#11191d, 0.38)", css)
        self.assertIn("0 5px 17px alpha(#11191d, 0.16)", style.build_css(get_theme("idgent-light")))


    def test_base_rules_and_instant_states(self):
        for theme in THEMES:
            with self.subTest(theme=theme.id):
                css = style.build_css(theme)
                rules = dict(re.findall(r'([^{}]+)\{([^{}]*)\}', css))
                for selector, body in rules.items():
                    if ':hover' in selector or ':selected' in selector or ':checked' in selector:
                        self.assertNotIn('transition:', body, selector)
                transitions = [(selector, body) for selector, body in rules.items()
                               if 'transition:' in body and 'transition: none;' not in body]
                self.assertEqual({selector.strip() for selector, _ in transitions},
                                 {'entry', 'paned > separator'})
                self.assertIn('border-color 128ms', css)
                self.assertIn('background-color 128ms', css)
                self.assertIn(f'box-shadow: 0 0 0 2px {theme.ui["border_focus"]}', css)
                self.assertIn(f'background: alpha({theme.ui["overlay"]}, 0.09)', css)
                self.assertIn('paned > separator:hover, paned > separator:active', css)
                self.assertIn('-gtk-outline-radius: 7px', css)
                self.assertNotIn(':focus-visible', css)

    def controls_window(self, theme):
        style.apply_theme(theme)
        window = Gtk.Window()
        self.addCleanup(window.destroy)
        window.set_default_size(320, 180)
        fixed = Gtk.Fixed()
        window.add(fixed)
        button, entry = Gtk.Button(label='Hover'), Gtk.Entry()
        button.set_size_request(160, 30)
        entry.set_size_request(160, 30)
        fixed.put(button, 24, 24)
        fixed.put(entry, 24, 88)
        window.show_all()
        window.present()
        self.assertTrue(self.poll(window.is_active))
        return window, button, entry

    def pixel(self, window, widget, x, y):
        x, y = widget.translate_coordinates(window, x, y)
        pixbuf = Gdk.pixbuf_get_from_window(window.get_window(), x, y, 1, 1)
        self.assertIsNotNone(pixbuf)
        return tuple(pixbuf.get_pixels()[:3])

    def test_hovered_button_pixels_are_overlay_tinted(self):
        for name in ('idgent-dark', 'idgent-light', 'dracula'):
            with self.subTest(theme=name):
                theme = get_theme(name)
                window, button, entry = self.controls_window(theme)
                entry.grab_focus()
                self.assertEqual(button.get_allocated_height(), 34)
                button.set_state_flags(Gtk.StateFlags.PRELIGHT, False)
                bg, overlay = theme.ui['bg'], theme.ui['overlay']
                expected = tuple(round(int(bg[i:i + 2], 16) * .933 +
                                       int(overlay[i:i + 2], 16) * .067) for i in (1, 3, 5))
                # Style resolves immediately; only the next paint needs polling.
                color = button.get_style_context().get_background_color(button.get_state_flags())
                self.assertAlmostEqual(color.alpha, .067, places=3)
                self.assertTrue(self.poll(lambda: all(abs(a - b) <= 1 for a, b in zip(
                    self.pixel(window, button, 8, 15), expected))))
                window.destroy()

    def test_focused_entry_pixels_show_border_and_two_pixel_ring(self):
        for name in ('idgent-dark', 'idgent-light', 'dracula'):
            with self.subTest(theme=name):
                theme = get_theme(name)
                window, button, entry = self.controls_window(theme)
                button.grab_focus()
                background = tuple(int(theme.ui['bg'][i:i + 2], 16) for i in (1, 3, 5))
                self.assertTrue(self.poll(lambda: self.pixel(window, entry, -1, 15) == background))
                self.assertEqual(entry.get_allocated_height(), 34)
                window.set_focus_visible(True)
                entry.grab_focus()
                self.assertTrue(self.poll(entry.has_focus))
                ring = tuple(int(theme.ui['border_focus'][i:i + 2], 16) for i in (1, 3, 5))
                self.assertTrue(self.poll(lambda: all(self.pixel(window, entry, x, 15) == ring
                                                     for x in (-2, -1, 0))))
                self.assertEqual(self.pixel(window, entry, -3, 15), background)
                window.destroy()

    def test_button_outline_follows_keyboard_focus_visibility(self):
        theme = get_theme('idgent-dark')
        window, button, _ = self.controls_window(theme)
        button.grab_focus()
        self.assertTrue(self.poll(button.has_focus))
        ring = (0x55, 0x55, 0x55)
        window.set_focus_visible(False)
        button.queue_draw()
        self.assertTrue(self.poll(lambda: self.pixel(window, button, -2, 15) != ring))
        window.set_focus_visible(True)
        # Setting this property directly does not invalidate the widget as an
        # actual keyboard-focus event does; request its next paint explicitly.
        button.queue_draw()
        self.assertTrue(self.poll(lambda: self.pixel(window, button, -2, 15) == ring))
        window.set_focus_visible(False)
        button.queue_draw()
        self.assertTrue(self.poll(lambda: self.pixel(window, button, -2, 15) != ring))

    def test_live_switch_uses_one_provider_and_updates_widgets(self):
        style.apply_style()
        provider = style._provider
        window = Gtk.Window()
        self.addCleanup(window.destroy)
        bar = TitleBar(Gtk.MenuBar())
        window.set_titlebar(bar)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sidebar = Gtk.Box()
        sidebar.get_style_context().add_class('idgent-explorer')
        editor = EditorPanel()
        box.pack_start(sidebar, False, False, 0)
        box.pack_start(editor, True, True, 0)
        window.add(box)
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'example.py'
            path.write_text('def answer():\n    return 42\n')
            editor.open_file(str(path))
            window.show_all()
            self.assertTrue(self.poll(window.get_mapped))
            view = editor._current().view
            with patch.object(Gtk.StyleContext, 'add_provider_for_screen') as register:
                for theme in THEMES:
                    with self.subTest(theme=theme.id):
                        style.apply_theme(theme)
                        editor.set_syntax_style(theme.syntax)
                        editor.set_chrome(theme.chrome)
                        self.assertIs(style.current_theme(), theme)
                        self.assertIs(style._provider, provider)
                        self.assertEqual(Gtk.Settings.get_default().get_property(
                            'gtk-application-prefer-dark-theme'), theme.dark)
                        self.assert_color(sidebar, theme.ui['foreground'])
                        self.assert_color(sidebar, theme.ui['sidebar_bg'], background=True)
                        self.assert_color(view, theme.ui['editor_fg'])
                        self.assert_color(view, theme.ui['bg'], background=True)
                        # updated by tabs-polish
                        self.assert_color(editor.status, theme.ui['muted'])
                        self.assert_color(editor.status, theme.ui['sidebar_bg'], background=True)
                        self.assert_color(bar, theme.ui['sidebar_bg'], background=True)
                        self.assert_color(bar.title_label, theme.ui['title_fg'])
                        self.assertEqual(editor.syntax_style, theme.syntax)
                        self.assertEqual(editor.chrome, theme.chrome)
                register.assert_not_called()

    def test_invalid_switch_preserves_provider_theme_and_preference(self):
        theme = get_theme('light-plus')
        style.apply_theme(theme)
        provider = style._provider
        bad = replace(get_theme('nord'), ui={**get_theme('nord').ui, 'bg': 'invalid-color'})
        with self.assertRaisesRegex(RuntimeError, 'IDgent theme CSS failed to parse'):
            style.apply_theme(bad)
        with patch.object(style, 'CSS', 'label { made-up-property: invalid; }'):
            with self.assertRaisesRegex(RuntimeError, 'IDgent theme CSS failed to parse'):
                style.apply_style()
        self.assertIs(style.current_theme(), theme)
        self.assertIs(style._provider, provider)
        self.assertFalse(Gtk.Settings.get_default().get_property('gtk-application-prefer-dark-theme'))
        window = Gtk.Window()
        self.addCleanup(window.destroy)
        window.show_all()
        self.assert_color(window, theme.ui['bg'], background=True)

    def test_requires_display(self):
        with patch.object(Gdk.Screen, 'get_default', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'initialized GTK display'):
                style.apply_style()

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
            scrollbar = pane.content.get_children()[1]
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
            self.assertEqual(scrollbar.get_allocated_width(), 10)
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

    def test_non_overlay_scrollbars_are_ten_pixels(self):
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
        self.assertEqual(scrolled.get_vscrollbar().get_allocated_width(), 10)
        self.assertEqual(scrolled.get_hscrollbar().get_allocated_height(), 10)

    def test_pane_header_and_toast_are_styled(self):
        style.apply_style(get_theme('dark-plus'))
        window = Gtk.Window()
        self.addCleanup(window.destroy)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        inactive, active = PaneHeader(), PaneHeader()
        for header in (inactive, active):
            header.set_valign(Gtk.Align.START)
            box.pack_start(header, False, False, 0)
        active.set_active(True)
        active.set_corner(True)
        toast = LimitToast()
        toast.set_valign(Gtk.Align.START)
        box.pack_start(toast, False, False, 0)
        window.add(box)
        window.show_all()
        toast.show()
        self.assertTrue(self.poll(lambda: inactive.get_allocated_height() > 1))
        self.assertTrue(28 <= inactive.get_allocated_height() <= 34)
        self.assertEqual(inactive.get_allocated_height(), active.get_allocated_height())
        warning = toast.get_style_context().get_color(Gtk.StateFlags.NORMAL)
        self.assertAlmostEqual(warning.red, 0xcc / 255, places=2)
        self.assertAlmostEqual(warning.green, 0xa7 / 255, places=2)
        inactive_style, active_style = inactive.get_style_context(), active.get_style_context()
        self.assertAlmostEqual(active_style.get_color(Gtk.StateFlags.NORMAL).red, 0xcc / 255, places=2)
        self.assertAlmostEqual(inactive_style.get_color(Gtk.StateFlags.NORMAL).red, 0x85 / 255, places=2)
        self.assertEqual(inactive_style.get_border(Gtk.StateFlags.NORMAL).bottom, 1)
        self.assertEqual(active_style.get_border(Gtk.StateFlags.NORMAL).bottom, 1)
        # The corner header reserves room for the "+" button; others do not.
        self.assertGreaterEqual(active_style.get_padding(Gtk.StateFlags.NORMAL).right,
                                AddButton().get_preferred_width()[0])
        self.assertLess(inactive_style.get_padding(Gtk.StateFlags.NORMAL).right, 8)
        flat = inactive.delete_button.get_style_context()
        self.assertEqual(flat.get_border(Gtk.StateFlags.NORMAL).left, 0)

    def test_realized_add_button_normal_hover_and_limit_colours(self):
        style.apply_style(get_theme('dark-plus'))
        window = Gtk.Window()
        self.addCleanup(window.destroy)
        button = AddButton()
        window.add(button)
        window.show_all()
        self.assertTrue(self.poll(lambda: button.get_mapped()))
        context = button.get_style_context()

        def assert_colour(expected, *, hover=False, background=False):
            button.unset_state_flags(Gtk.StateFlags.PRELIGHT)
            if hover:
                button.set_state_flags(Gtk.StateFlags.PRELIGHT, False)
            getter = context.get_background_color if background else context.get_color

            def matches():
                actual = getter(context.get_state())
                return all(abs(channel - value / 255) < 0.005
                           for channel, value in zip((actual.red, actual.green, actual.blue), expected))

            self.assertTrue(self.poll(matches), getter(context.get_state()).to_string())

        assert_colour((0xcc, 0xcc, 0xcc))
        assert_colour((255, 255, 255), hover=True)
        assert_colour((0x2a, 0x2d, 0x2e), hover=True, background=True)
        button.set_full(True)
        assert_colour((0x7d, 0x6f, 0x2a))
        assert_colour((0xcc, 0xa7, 0), hover=True)
        button.set_full(False)
        assert_colour((0xcc, 0xcc, 0xcc))

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

    def test_titlebar_css_is_in_applied_theme(self):
        self.assertIn(TITLEBAR_CSS, style.CSS)
        style.apply_style()
        self.assertIsNotNone(style._provider)

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
        style.apply_style(get_theme('dark-plus'))
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
            # updated by tabs-polish
            self.assertAlmostEqual(background.red, 37 / 255, places=2)
            self.assertAlmostEqual(background.green, 37 / 255, places=2)
            self.assertAlmostEqual(background.blue, 38 / 255, places=2)
            # The five-pixel separator retains a useful drag target around its one-pixel line.
            handle = paned.get_handle_window().get_geometry()
            self.assertGreaterEqual(handle.width, 5)
            nodes = editor.notebook.get_style_context().to_string(Gtk.StyleContextPrintFlags.RECURSE)
            self.assertIn('header.top', nodes)
            self.assertIn('tabs:', nodes)
            # updated by tabs-polish
            self.assertRegex(nodes, r'\btab(?:\.[\w-]+)*:')
