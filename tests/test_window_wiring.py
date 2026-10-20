"""Main window wiring: prompts, hotkeys, settings, style (real GTK/X11)."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ['GDK_BACKEND'] = 'x11'

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('Vte', '2.91')
from gi.repository import Gdk, Gio, Gtk, Vte

from idgent import motion, notes, settings, style
from idgent.palette import CommandPalette, QuickSwitcher
from idgent.themes import THEMES, get_theme
from idgent.window import MainWindow

CTRL = Gdk.ModifierType.CONTROL_MASK
SHIFT = Gdk.ModifierType.SHIFT_MASK

# A non-default value for every settings-window key, and how to read it back live.
CHANGES = {
    'line_numbers': False, 'highlight_line': False,
    **({'md_live_preview': False} if 'md_live_preview' in settings.NEW_KEYS else
       {'md_default_preview': True}),
    'md_readable_width': False, 'indent_tabs': True, 'indent_width': 2, 'auto_pair': False,
    'show_hidden': False, 'confirm_delete': False, 'delete_mode': 'trash',
    'terminal_cursor': 'underline', 'terminal_blink': False, 'terminal_scrollback': 5000,
    'reduce_motion': True, 'accent': '#22a55b', 'ui_font': 'DejaVu Serif',
    'editor_font': 'DejaVu Sans Mono', 'terminal_autostart': False,
}
EDITOR_ATTRS = ('line_numbers', 'highlight_line', 'md_live_preview', 'md_readable_width',
                'indent_tabs', 'indent_width', 'auto_pair')
EXPLORER_ATTRS = ('show_hidden', 'confirm_delete')
VTE_SHAPES = {'block': Vte.CursorShape.BLOCK, 'ibeam': Vte.CursorShape.IBEAM,
              'underline': Vte.CursorShape.UNDERLINE}


def spin(seconds=.25):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        while Gtk.events_pending():
            Gtk.main_iteration()
        time.sleep(.005)


def provider_css():
    return style._provider.to_string()


def css_rgb(color):
    """GTK serialises colours as rgb(r,g,b)."""
    return 'rgb({},{},{})'.format(*(int(color[i:i + 2], 16) for i in (1, 3, 5)))


class WindowWiringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Gtk.Application.get_default() or Gtk.Application(
            application_id='dev.idgent.WindowWiringTests',
            flags=Gio.ApplicationFlags.NON_UNIQUE)
        if not cls.app.get_is_registered():
            assert cls.app.register(None)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, XDG_CONFIG_HOME=os.path.join(self.temp.name, 'config'))
        env.start()
        self.addCleanup(env.stop)
        self.root = Path(self.temp.name) / 'project'
        (self.root / 'pkg').mkdir(parents=True)
        (self.root / 'main.py').write_text('print("hi")\n')
        (self.root / 'pkg' / 'helper.py').write_text('x = 1\n')
        (self.root / 'notes.md').write_text('# Notes\n')
        (self.root / '.hidden.txt').write_text('secret\n')
        (self.root / 'node_modules').mkdir()
        (self.root / 'node_modules' / 'dep.js').write_text('\n')
        self.windows = []
        self.addCleanup(self.cleanup_windows)
        self.addCleanup(motion.set_reduce_motion, False)
        # Later test modules share the screen-wide provider: drop accent/font overrides.
        theme = style.current_theme()
        if theme is not None:
            self.addCleanup(style.apply_theme, get_theme(theme.id))

    def cleanup_windows(self):
        for window in self.windows:
            if window._settings_dialog is not None:
                window._settings_dialog.destroy()
            window.destroy()
        spin(.1)

    def window(self, theme=None):
        window = MainWindow(self.app, str(self.root), theme=theme)
        self.windows.append(window)
        window.show_all()
        spin()
        return window

    def press(self, window, keyval, state=0):
        """Dispatch a real key event (with its X11 hardware keycode) to the window."""
        display = Gdk.Display.get_default()
        event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        event.window = window.get_window()
        event.set_device(display.get_default_seat().get_keyboard())
        event.keyval, event.state = keyval, state
        found, keys = Gdk.Keymap.get_for_display(display).get_entries_for_keyval(keyval)
        self.assertTrue(found)
        event.hardware_keycode, event.group = keys[0].keycode, keys[0].group
        handled = window.emit('key-press-event', event)
        spin(.05)
        return handled

    def open_file(self, window, name='main.py'):
        window.editor.open_file(str(self.root / name))
        spin()
        view = window.editor._current().view
        view.grab_focus()
        self.assertIs(window.get_focus(), view)
        return view

    # Prompts and hotkeys -----------------------------------------------------

    def test_editor_focus_hotkeys_open_prompts_and_escape_restores_focus(self):
        window = self.window()
        view = self.open_file(window)
        for keyval, state, kind in ((Gdk.KEY_p, CTRL, QuickSwitcher),
                                    (Gdk.KEY_o, CTRL, QuickSwitcher),
                                    (Gdk.KEY_P, CTRL | SHIFT, CommandPalette)):
            with self.subTest(keyval=Gdk.keyval_name(keyval)):
                self.assertTrue(self.press(window, keyval, state))
                prompt = window.overlay.prompt
                self.assertIsInstance(prompt, kind)
                self.assertIs(window.get_focus(), prompt.entry)
                self.assertTrue(prompt.get_mapped())
                self.press(window, Gdk.KEY_Escape)
                self.assertIsNone(window.overlay.prompt)
                self.assertIs(window.get_focus(), view)

    def test_explorer_focus_opens_switcher(self):
        window = self.window()
        window.explorer.focus_tree()
        spin(.05)
        self.assertTrue(self.press(window, Gdk.KEY_p, CTRL))
        self.assertIsInstance(window.overlay.prompt, QuickSwitcher)
        window.overlay.hide_prompt()

    def test_terminal_focus_keeps_readline_keys_but_ctrl_shift_p_opens_palette(self):
        window = self.window()
        window.toggle_terminal_right()
        spin()
        for panel in (window.terminal_bottom, window.terminal_right):
            terminal = panel._panes[0].terminal
            with self.subTest(panel=panel):
                terminal.grab_focus()
                self.assertIs(window.get_focus(), terminal)
                with patch.object(window, 'open_quick_switcher') as switcher, \
                        patch.object(window, 'open_settings') as opened:
                    self.press(window, Gdk.KEY_p, CTRL)
                    self.press(window, Gdk.KEY_o, CTRL)
                    self.press(window, Gdk.KEY_F1)
                    switcher.assert_not_called()
                    opened.assert_not_called()
                self.assertIsNone(window.overlay.prompt)
                self.assertIs(window.get_focus(), terminal)
                self.assertTrue(self.press(window, Gdk.KEY_P, CTRL | SHIFT))
                self.assertIsInstance(window.overlay.prompt, CommandPalette)
                self.press(window, Gdk.KEY_Escape)
                self.assertIs(window.get_focus(), terminal)

    def test_switcher_lists_project_files_recent_first_and_opens_choice(self):
        window = self.window()
        self.open_file(window, 'notes.md')
        self.open_file(window, 'main.py')
        self.press(window, Gdk.KEY_p, CTRL)
        prompt = window.overlay.prompt
        shown = [item.data for item in prompt.items]
        self.assertEqual(shown[:2], [str(self.root / 'main.py'), str(self.root / 'notes.md')])
        self.assertIn(str(self.root / 'pkg' / 'helper.py'), shown)
        self.assertIn(str(self.root / '.hidden.txt'), shown)
        self.assertFalse(any('node_modules' in path for path in shown))
        prompt.set_query('helper')
        self.assertEqual(prompt.selected_item.data, str(self.root / 'pkg' / 'helper.py'))
        self.press(window, Gdk.KEY_Return)
        self.assertIsNone(window.overlay.prompt)
        self.assertEqual(window.editor.current_path(), str(self.root / 'pkg' / 'helper.py'))
        self.assertIs(window.get_focus(), window.editor._current().view)

    def test_palette_runs_chosen_command_and_lists_it_first_next_time(self):
        window = self.window()
        self.open_file(window)
        self.press(window, Gdk.KEY_P, CTRL | SHIFT)
        prompt = window.overlay.prompt
        prompt.set_query('toggle explorer sidebar')
        self.assertEqual(prompt.selected_item.label, 'View: Toggle Explorer Sidebar')
        self.assertEqual(prompt.selected_item.hotkeys, ('Ctrl+B',))
        self.press(window, Gdk.KEY_Return)
        self.assertFalse(window.explorer.get_visible())
        self.press(window, Gdk.KEY_P, CTRL | SHIFT)
        self.assertEqual(window.overlay.prompt.items[0].label, 'View: Toggle Explorer Sidebar')
        window.overlay.hide_prompt()

    def test_command_list_has_every_menu_action_with_its_accelerator(self):
        window = self.window()
        commands = {command.label: command.hotkey for command in window.commands()}
        for top in window.titlebar.menu_bar.get_children():
            group = top.get_label()
            for entry in top.get_submenu().get_children():
                label = f"{group}: {entry.get_label().rstrip('…')}"
                # Graph menu entries use the requested palette command names.
                label = {'View: Graph view': 'Graph view: Open graph view',
                         'View: Git graph': 'Git graph: Open git graph',
                         'View: Local graph': 'Graph view: Open local graph'}.get(label, label)
                self.assertIn(label, commands)
                key, modifiers = entry.get_child().get_accel()
                if key:
                    expected = Gtk.accelerator_get_label(key, modifiers)
                    shown = Gtk.accelerator_get_label(*Gtk.accelerator_parse(
                        commands[label][0].replace('Ctrl+', '<Control>').replace(
                            'Shift+', '<Shift>').replace('Alt+', '<Alt>').replace('`', 'grave')))
                    self.assertEqual(shown, expected, label)
        self.assertEqual(commands['File: Save'], ('Ctrl+S',))
        self.assertEqual(commands['File: Quick Switcher'], ('Ctrl+P', 'Ctrl+O'))
        self.assertEqual(commands['View: Command Palette'], ('Ctrl+Shift+P',))
        self.assertEqual(commands['View: Terminal Below'], ('Ctrl+`',))
        self.assertEqual(commands['View: Terminal on Right'], ('Ctrl+Alt+`',))
        view = window.titlebar.menu_bar.get_children()[1].get_submenu()
        self.assertNotIn('Terminal on Right', [item.get_label() for item in view.get_children()])
        labels = [command.label for command in window.commands()]
        self.assertEqual(labels[labels.index('Graph view: Open local graph') + 1],
                         'Git graph: Open git graph')
        self.assertEqual(labels[labels.index('Git graph: Open git graph') + 1],
                         'View: Terminal on Right')
        self.assertEqual(labels[labels.index('View: Terminal on Right') + 1],
                         'Settings: Open settings')
        for label in ('Settings: Open settings', 'Theme: Next theme', 'View: Search in Files',
                      'Markdown: Toggle source mode', 'Editor: Zoom in', 'Editor: Zoom out',
                      'Editor: Reset zoom', 'View: Split Terminal', 'View: Terminal on Right'):
            self.assertIn(label, commands)
            self.assertTrue(commands[label])
        file_menu = window.titlebar.menu_bar.get_children()[0].get_submenu()
        self.assertFalse(any('Settings' in (item.get_label() or '')
                             for item in file_menu.get_children()))

    def test_palette_commands_zoom_and_markdown_toggle(self):
        window = self.window()
        self.open_file(window, 'notes.md')
        commands = {command.label: command for command in window.commands()}
        size = window.editor.font_size
        commands['Editor: Zoom in'].callback()
        self.assertEqual(window.editor.font_size, size + 1)
        commands['Editor: Reset zoom'].callback()
        self.assertEqual(window.editor.font_size, window.editor.default_font_size)
        mode = window.editor.markdown_mode()
        commands['Markdown: Toggle source mode'].callback()
        self.assertNotEqual(window.editor.markdown_mode(), mode)
        theme = window.theme.id
        commands['Theme: Next theme'].callback()
        self.assertNotEqual(window.theme.id, theme)

    # Settings ------------------------------------------------------------------

    def assert_live(self, window, key, value):
        terminals = (window.terminal_bottom, window.terminal_right)
        if key in EDITOR_ATTRS:
            self.assertEqual(getattr(window.editor, key), value)
        elif key in EXPLORER_ATTRS:
            self.assertEqual(getattr(window.explorer, key), value)
        elif key == 'delete_mode':
            self.assertEqual(window.explorer.delete_mode,
                             {'trash': 'trash', 'permanent': 'delete'}[value])
        elif key == 'terminal_cursor':
            for panel in terminals:
                self.assertEqual(panel._cursor_shape, value)
                for pane in panel._panes:
                    self.assertEqual(pane.terminal.get_cursor_shape(), VTE_SHAPES[value])
        elif key == 'terminal_blink':
            for panel in terminals:
                self.assertEqual(panel._cursor_blink, value)
                for pane in panel._panes:
                    self.assertEqual(pane.terminal.get_cursor_blink_mode(),
                                     Vte.CursorBlinkMode.ON if value else Vte.CursorBlinkMode.OFF)
        elif key == 'terminal_scrollback':
            for panel in terminals:
                self.assertEqual(panel._scrollback, value)
                for pane in panel._panes:
                    self.assertEqual(pane.terminal.get_scrollback_lines(), value)
        elif key == 'reduce_motion':
            self.assertEqual(motion.animations_enabled(), False if value else self.animations)
        elif key == 'accent':
            accent = value or window.theme.ui['accent']
            self.assertEqual(style.current_theme().ui['accent'], accent)
            self.assertIn(css_rgb(accent), provider_css())
            if value is None:
                self.assertIs(style.current_theme(), window.theme)
        elif key == 'ui_font':
            css = provider_css()
            if value:
                self.assertIn(f'font-family: "{value}"', css)
                family = window.titlebar.get_style_context().get_property(
                    'font', Gtk.StateFlags.NORMAL).get_family()
                self.assertEqual(family, value)
            else:
                self.assertNotIn('* {\n  font-family', css)
        elif key == 'editor_font':
            family = window.editor._current().view.get_style_context().get_property(
                'font', Gtk.StateFlags.NORMAL).get_family()
            self.assertEqual(family, value or 'monospace')
        elif key == 'terminal_autostart':
            self.assertTrue(window.terminal_bottom.get_visible())

    def test_every_setting_changes_the_live_widgets_and_persists(self):
        self.animations = motion.animations_enabled()
        window = self.window()
        self.open_file(window)
        window.toggle_terminal_right()
        spin()
        self.assertTrue(window.terminal_right._panes)
        window.open_settings()
        dialog = window._settings_dialog
        defaults = settings.NEW_DEFAULTS
        self.assertEqual(set(CHANGES), set(settings.NEW_KEYS))
        for key, value in CHANGES.items():
            with self.subTest(key=key):
                self.assert_live(window, key, defaults[key])
                dialog._changed(key, value)
                spin(.05)
                self.assert_live(window, key, value)
                self.assertEqual(settings.get_value(key), value)
        # Unset accent/fonts revert to the theme defaults.
        for key in ('accent', 'ui_font', 'editor_font', 'reduce_motion'):
            with self.subTest(revert=key):
                dialog._changed(key, defaults[key])
                spin(.05)
                self.assert_live(window, key, defaults[key])
        # A new terminal pane gets the current terminal settings.
        window.terminal_bottom.new_terminal()
        spin()
        self.assert_live(window, 'terminal_cursor', 'underline')
        self.assert_live(window, 'terminal_scrollback', 5000)

    def test_live_markdown_setting_dispatch(self):
        window = self.window()
        window._values['md_live_preview'] = False
        window._apply_setting('md_live_preview')
        self.assertFalse(window.editor.md_live_preview)
        self.open_file(window, 'notes.md')
        self.assertEqual(window.editor.markdown_mode(), 'source')
        window._values['md_live_preview'] = True
        window._apply_setting('md_live_preview')
        self.assertTrue(window.editor.md_live_preview)

    def test_startup_applies_stored_values(self):
        stored = dict(CHANGES, theme='idgent-light', editor_font_size=14)
        self.assertTrue(settings.set_values(stored))
        window = self.window()
        self.open_file(window)
        self.assertEqual(window.theme.id, 'idgent-light')
        self.assertEqual(window.editor.font_size, 14)
        self.animations = True
        for key, value in CHANGES.items():
            if key != 'terminal_autostart':
                with self.subTest(key=key):
                    self.assert_live(window, key, value)
        self.assertFalse(window.terminal_bottom.get_visible())
        self.assertEqual(window.terminal_bottom.terminal_count(), 0)
        window.toggle_terminal_bottom()
        spin()
        self.assertTrue(window.terminal_bottom.get_visible())
        self.assertEqual(window.terminal_bottom.terminal_count(), 1)
        self.assert_live(window, 'terminal_cursor', 'underline')
        # Hidden files and ignored folders stay out of the switcher.
        window.open_quick_switcher()
        shown = [item.data for item in window.overlay.prompt.items]
        self.assertNotIn(str(self.root / '.hidden.txt'), shown)
        window.overlay.hide_prompt()

    def test_notes_slash_commands_dispatch_startup_and_live(self):
        # The settings owner adds this key independently; exercise the agreed dispatch now.
        values = settings.load()
        values['notes_slash_commands'] = False
        keys = tuple(dict.fromkeys((*settings.NEW_KEYS, 'notes_slash_commands')))
        with patch.object(settings, 'load', return_value=values), patch.object(settings, 'NEW_KEYS', keys):
            window = self.window()
        self.assertFalse(window.editor.slash_commands)
        path = Path(notes.create_note(str(self.root), 'Commands'))
        window.editor.open_file(str(path))
        tab = window.editor._current()
        self.assertIsNone(tab.slash_menu)
        window._values['notes_slash_commands'] = True
        window._apply_setting('notes_slash_commands')
        self.assertIsNotNone(tab.slash_menu)
        window._values['notes_slash_commands'] = False
        window._apply_setting('notes_slash_commands')
        self.assertIsNone(tab.slash_menu)

    def test_settings_window_shows_current_values_and_real_hotkeys(self):
        window = self.window()
        view = self.open_file(window)
        window._values['line_numbers'] = False  # live value wins over the file
        window.open_settings()
        dialog = window._settings_dialog
        self.assertTrue(dialog.get_visible())
        self.assertEqual(dialog.values['line_numbers'], False)
        self.assertEqual(dialog.values['theme'], window.theme.id)
        expected = window.hotkeys()
        self.assertEqual(len(dialog.hotkey_rows), len(expected))
        self.assertIn(('File: Save', 'Ctrl+S'), expected)
        self.assertIn(('View: Command Palette', 'Ctrl+Shift+P'), expected)
        self.assertIn(('File: Quick Switcher', 'Ctrl+P / Ctrl+O'), expected)
        self.assertIn(('Settings: Show hotkeys', 'F1'), expected)
        self.assertIn(('Undo', 'Ctrl+Z'), expected)  # panel-local shortcuts are kept
        self.assertNotIn(('Save file', 'Ctrl+S'), expected)  # no duplicates
        dialog.hide()
        view.grab_focus()
        self.assertTrue(self.press(window, Gdk.KEY_F1))
        self.assertTrue(dialog.get_visible())
        self.assertEqual(dialog.stack.get_visible_child_name(), 'hotkeys')
        dialog.hide()
        key, modifiers = Gtk.accelerator_parse('<Control>comma')
        view.grab_focus()
        self.assertTrue(Gtk.accel_groups_activate(window, key, modifiers))
        self.assertTrue(dialog.get_visible())

    def test_failed_settings_save_shows_one_error_notice(self):
        window = self.window()
        window.open_settings()
        with patch.object(settings, 'save', return_value=False):
            window._settings_dialog._changed('auto_pair', False)
        spin(.05)
        self.assertFalse(window.editor.auto_pair)
        self.assertEqual([notice.level for notice in window.overlay.notices], ['error'])
        self.assertIn('settings', window.overlay.notices[0].text)

    # Style ------------------------------------------------------------------------

    def test_every_theme_parses_with_composed_css_and_overrides(self):
        for theme in THEMES:
            with self.subTest(theme=theme.id):
                css = style.build_css(theme)
                self.assertIn('.idgent-prompt', css)
                self.assertIn('.idgent-notice', css)
                # Prompt rules come after the base entry/row rules they override.
                self.assertGreater(css.index('.idgent-prompt entry'), css.index('\nentry {'))
                style._create_provider(css)
                themed = style.with_accent(theme, '#e8762d')
                style._create_provider(style.build_css(themed)
                                       + style.build_overrides('Ubuntu "Sans";{}', 'Mono'))
                self.assertEqual(set(themed.ui), set(theme.ui))
                self.assertTrue(all(len(v) == 7 and v[0] == '#' for v in themed.ui.values()))

    def test_accent_roles_are_derived_contrast_safe(self):
        from idgent.themes import _contrast
        for theme in THEMES:
            for accent in settings.ACCENT_PRESETS:
                with self.subTest(theme=theme.id, accent=accent):
                    ui = style.with_accent(theme, accent).ui
                    self.assertEqual(ui['accent'], accent)
                    self.assertGreaterEqual(_contrast(ui['accent_text'], ui['bg']), 3)
                    self.assertGreaterEqual(_contrast(ui['accent_fg'], accent), 3)
                    self.assertNotEqual(ui['accent_hover'], accent)
        self.assertIs(style.with_accent(THEMES[0], None), THEMES[0])

    def test_full_window_smoke_under_both_idgent_themes(self):
        for theme_id in ('idgent-dark', 'idgent-light'):
            with self.subTest(theme=theme_id):
                window = self.window(get_theme(theme_id))
                self.open_file(window)
                self.assertEqual(style.current_theme().id, theme_id)
                window.open_quick_switcher()
                window.overlay.prompt.set_query('main')
                spin(.05)
                frame = window.overlay.prompt.frame.get_allocation()
                self.assertLessEqual(frame.width, 700)
                self.assertGreater(frame.height, 50)
                window.open_command_palette()
                self.assertIsInstance(window.overlay.prompt, CommandPalette)
                window.overlay.hide_prompt()
                window.notify('Hello')
                spin(.2)
                self.assertEqual([n.text for n in window.overlay.notices], ['Hello'])
                window.destroy()
                self.windows.remove(window)
                spin(.1)

    # Titlebar toggles and notes ------------------------------------------------

    def test_titlebar_toggles_explorer_and_bottom_dock_and_stay_synced(self):
        window = self.window()
        bar = window.titlebar
        for button, widget, toggle in (
                (bar.explorer_button, window.explorer, window.toggle_explorer),
                (bar.bottom_dock_button, window.terminal_bottom, window.toggle_terminal_bottom)):
            with self.subTest(button=button.get_accessible().get_name()):
                self.assertTrue(widget.get_visible())
                self.assertTrue(button.get_active())
                button.clicked()
                spin(.05)
                self.assertFalse(widget.get_visible())
                self.assertFalse(button.get_active())
                toggle()  # menu, hotkey and palette paths keep the icon in sync
                spin(.05)
                self.assertTrue(widget.get_visible())
                self.assertTrue(button.get_active())
        self.assertTrue(window.explorer.tree.get_mapped())

    def test_right_dock_toggle_opens_a_graph_view_by_default(self):
        window = self.window()
        panel = window.terminal_right
        window.titlebar.dock_button.clicked()
        spin(.2)
        self.assertTrue(panel.get_visible())
        self.assertTrue(window.titlebar.dock_button.get_active())
        self.assertEqual((panel.graph_count(), panel.terminal_count()), (1, 0))
        window.titlebar.dock_button.clicked()
        spin(.05)
        self.assertFalse(panel.get_visible())
        self.assertFalse(window.titlebar.dock_button.get_active())
        # Reopening shows the existing graph instead of adding panes.
        window.titlebar.dock_button.clicked()
        spin(.05)
        self.assertEqual(panel.pane_count(), 1)
        self.assertTrue(panel.has_graph())

    def test_note_toolbar_only_on_notes_and_buttons_insert_markdown(self):
        window = self.window()
        main = self.open_file(window)
        self.assertIsNone(getattr(window.editor._current(), '_note_toolbar', None))
        with patch.object(window.explorer, '_name_dialog',
                          side_effect=lambda title, initial, action: action('coffi shop')):
            window.new_note()
        spin()
        path = str(self.root / '.IDgent' / 'Notes' / 'coffi shop.md')
        self.assertTrue(window.explorer.notes_section_visible)
        self.assertEqual(window.editor.current_path(), path)
        tab = window.editor._current()
        toolbar = tab._note_toolbar
        self.assertTrue(toolbar.get_mapped())
        self.assertEqual(tab.source_scroll.get_margin_top(), toolbar.get_allocated_height())
        y = toolbar.translate_coordinates(tab.page, 0, 0)[1]
        self.assertEqual(y, 0)
        self.assertIs(tab.page.get_parent(), window.editor.notebook)
        tab.buffer.place_cursor(tab.buffer.get_end_iter())
        toolbar.buttons['table'].clicked()
        self.assertEqual(window.editor.buffer_text(path), '# coffi shop\n\n' + notes.TABLE)
        self.assertFalse(tab.buffer.get_has_selection())
        spin(.1)
        self.assertTrue(any(block.kind == 'table' and block.widgets for block in tab.md_renderer.blocks))
        tab.undo()
        self.assertEqual(window.editor.buffer_text(path), '# coffi shop\n\n')
        # A toolbar command edits the same buffer while retaining live mode.
        window.editor.set_markdown_mode('preview')
        tab.buffer.place_cursor(tab.buffer.get_end_iter())
        toolbar.buttons['checklist'].clicked()
        self.assertEqual(tab.md_mode, 'preview')
        self.assertEqual(window.editor.buffer_text(path), '# coffi shop\n\n- [ ] ')
        # Other files keep the plain editor; the toolbar leaves a renamed-away note.
        self.assertIsNone(getattr(window.editor.tabs[0], '_note_toolbar', None))
        window.explorer._rename(path, 'coffi shop.txt')
        spin(.05)
        self.assertIsNone(tab._note_toolbar)
        self.assertEqual(tab.source_scroll.get_margin_top(), 0)
        self.assertIs(main, window.editor.tabs[0].view)

    def test_palette_lists_note_commands_and_runs_them(self):
        window = self.window()
        labels = [command.label for command in window.commands()]
        expected = ['Notes: New note', 'Notes: Show notes'] + [
            f'Notes: {label}' for label in ('Heading', 'Bold', 'Italic', 'Bullet list',
                                            'Numbered list', 'Checklist', 'Table', 'Code block',
                                            'Link', 'Horizontal rule')]
        self.assertEqual(labels[-len(expected):], expected)
        self.open_file(window, 'notes.md')
        tab = window.editor._current()
        tab.buffer.set_text('')
        self.press(window, Gdk.KEY_P, CTRL | SHIFT)
        prompt = window.overlay.prompt
        prompt.set_query('notes checklist')
        self.assertEqual(prompt.selected_item.label, 'Notes: Checklist')
        self.press(window, Gdk.KEY_Return)
        self.assertEqual(window.editor.buffer_text(str(self.root / 'notes.md')), '- [ ] ')
        # Plain files are left alone, with a notice.
        self.open_file(window, 'main.py')
        commands = {command.label: command for command in window.commands()}
        commands['Notes: Bold'].callback()
        spin(.1)
        self.assertEqual(window.editor.buffer_text(str(self.root / 'main.py')), 'print("hi")\n')
        self.assertIn('Open a note or Markdown file to use note commands.',
                      [notice.text for notice in window.overlay.notices])
        window.toggle_explorer()
        commands['Notes: Show notes'].callback()
        self.assertTrue(window.explorer.get_visible())
        self.assertTrue(window.explorer.notes_section_visible)


if __name__ == '__main__':
    unittest.main()
