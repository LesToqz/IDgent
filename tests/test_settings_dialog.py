"""Settings window contract; run with GDK_BACKEND=x11 under xvfb."""
import os
import tempfile
import unittest
from unittest.mock import patch

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, Gtk

from idgent import settings, style
from idgent.settings_dialog import DEFAULT_HOTKEYS, PAGES, SettingsDialog, build_css
from idgent.themes import DEFAULT_THEME_ID, THEMES, get_theme
from idgent.widgets import SettingRow, Toggle
from tests.test_widgets import pump, send_key


def hex_rgb(color):
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


class SettingsDialogTest(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        env = patch.dict(os.environ, XDG_CONFIG_HOME=root.name)
        env.start()
        self.addCleanup(env.stop)
        style.apply_theme(get_theme('idgent-dark'))
        self.dialog = SettingsDialog()
        self.addCleanup(self.dialog.destroy)

    def show(self):
        self.dialog.show_all()
        self.dialog.present()
        pump(80)

    def changes(self):
        events = []
        self.dialog.connect('setting-changed', lambda _, key, value: events.append((key, value)))
        return events

    # Existing API -------------------------------------------------------------

    def test_window_defaults_and_theme_order(self):
        dialog = self.dialog
        self.assertIsInstance(dialog, Gtk.Window)
        self.assertEqual(dialog.get_title(), 'Settings')
        self.assertEqual(dialog.get_default_size(), (900, 700))
        self.assertEqual(dialog.get_size_request(), (600, 400))
        self.assertEqual(dialog.theme_id, settings.get_theme_id())
        self.assertEqual(dialog.sizes, settings.DEFAULT_FONT_SIZES)
        self.assertEqual([row[1] for row in dialog.theme_combo.get_model()], [t.id for t in THEMES])
        self.assertTrue(all(not b.get_sensitive() for b in dialog.reset_buttons.values()))
        self.assertEqual(dialog.values, {**settings.DEFAULTS, 'graph': settings.GRAPH_DEFAULTS})
        self.assertEqual(dialog.stack.get_transition_type(), Gtk.StackTransitionType.NONE)
        self.assertEqual([r.page_id for r in dialog.nav_list.get_children()], [p for p, _, _ in PAGES])

    def test_theme_selection_live_and_silent_setter(self):
        events = []
        self.dialog.connect('theme-selected', lambda _, theme: events.append(theme))
        self.dialog.theme_combo.set_active_id('nord')
        self.assertEqual(events, ['nord'])
        self.dialog.theme_list.select_row(self.dialog.theme_rows['dracula'])  # Former API.
        self.assertEqual(events, ['nord', 'dracula'])
        self.assertEqual(list(self.dialog.theme_rows), [t.id for t in THEMES])
        self.dialog.set_theme_id('light-plus')
        self.assertEqual(self.dialog.theme_id, 'light-plus')
        self.dialog.set_theme_id('unknown')
        self.assertEqual(self.dialog.theme_id, DEFAULT_THEME_ID)
        self.dialog.set_values({'theme': 'monokai'})
        self.assertEqual(self.dialog.theme_id, 'monokai')
        self.assertEqual(events, ['nord', 'dracula'])
        self.assertIn('Monokai', self.dialog.theme_preview.get_tooltip_text())

    def test_font_sliders_reset_limits_and_silent_setter(self):
        events, changes = [], self.changes()
        self.dialog.connect('font-size-changed', lambda _, k, v: events.append((k, v)))
        for key in settings.FONT_KEYS:
            scale = self.dialog.spins[key]
            low, high = settings.FONT_SIZE_LIMITS[key]
            self.assertEqual((scale.get_adjustment().get_lower(), scale.get_adjustment().get_upper()),
                             (low, high))
            scale.set_value(18)
            self.assertEqual(events[-1], (key, 18))
            self.assertEqual(self.dialog.value_labels[key].get_text(), '18')
            self.assertTrue(self.dialog.reset_buttons[key].get_sensitive())
            self.dialog.reset_buttons[key].clicked()
            self.assertEqual(events[-1], (key, settings.DEFAULT_FONT_SIZES[key]))
            self.assertFalse(self.dialog.reset_buttons[key].get_sensitive())
            scale.set_value(1000)
            self.assertEqual(events[-1], (key, high))
            scale.set_value(-1000)
            self.assertEqual(events[-1], (key, low))
        before = events[:]
        self.dialog.set_sizes({k: 13 for k in settings.FONT_KEYS})
        self.assertEqual(events, before)
        self.assertEqual(self.dialog.sizes, dict.fromkeys(settings.FONT_KEYS, 13))
        snapshot = self.dialog.sizes
        snapshot['terminal_font_size'] = 25
        self.assertEqual(self.dialog.sizes['terminal_font_size'], 13)
        self.assertEqual(settings.load(), {})  # The owner persists font sizes and the theme.
        self.assertEqual(changes, [])  # Existing keys keep their own signals only.

    def test_initial_persistence_partial_arguments_and_parent(self):
        settings.set_theme_id('nord')
        settings.set_font_size('terminal_font_size', 17)
        settings.set_values({'indent_width': 2, 'terminal_cursor': 'underline', 'accent': '#e5484d'})
        dialog = SettingsDialog(parent=self.dialog)
        self.addCleanup(dialog.destroy)
        self.assertIs(dialog.get_transient_for(), self.dialog)
        self.assertTrue(dialog.get_destroy_with_parent())
        self.assertFalse(dialog.get_modal())
        self.assertEqual(dialog.theme_id, 'nord')
        self.assertEqual(dialog.sizes['terminal_font_size'], 17)
        self.assertEqual(dialog.values['indent_width'], 2)
        self.assertEqual(dialog.controls['terminal_cursor'].get_active_id(), 'underline')
        self.assertEqual(dialog.accent_picker.value, '#e5484d')
        other = SettingsDialog(theme_id='dracula', sizes={'editor_font_size': 19})
        self.addCleanup(other.destroy)
        self.assertEqual(other.sizes, {**settings.DEFAULT_FONT_SIZES, 'editor_font_size': 19})
        self.assertEqual(other.theme_id, 'dracula')

    def test_close_escape_window_control_and_reuse(self):
        self.show()
        send_key(self.dialog, Gdk.KEY_Escape)
        self.assertFalse(self.dialog.get_visible())
        self.show()
        self.dialog.response(Gtk.ResponseType.CLOSE)
        self.assertFalse(self.dialog.get_visible())
        self.show()
        self.assertTrue(self.dialog.emit('delete-event', Gdk.Event.new(Gdk.EventType.DELETE)))
        self.assertFalse(self.dialog.get_visible())
        self.show()  # Reusable: still working after hiding.
        changes = self.changes()
        self.dialog.controls['line_numbers'].clicked()
        self.assertEqual(changes, [('line_numbers', False)])
        self.assertTrue(self.dialog.get_visible())

    # New keys -----------------------------------------------------------------

    def test_every_new_key_persists_clamps_and_emits(self):
        changes = self.changes()
        controls = self.dialog.controls
        expected = {}
        for key in settings.NEW_KEYS:
            control = controls[key]
            if isinstance(control, Toggle):
                control.clicked()
                expected[key] = not settings.DEFAULTS[key]
            elif isinstance(control, Gtk.ComboBoxText):
                value = next(v for v in settings.CHOICES[key] if v != settings.DEFAULTS[key])
                control.set_active_id(value)
                expected[key] = value
            elif isinstance(control, (Gtk.Scale, Gtk.SpinButton)):
                low, high = settings.LIMITS[key]
                control.set_value(high + 1000)
                expected[key] = high
            elif key == 'accent':
                self.dialog.accent_picker.buttons['#22a55b'].clicked()
                expected[key] = '#22a55b'
            elif isinstance(control, Gtk.Entry):
                control.set_text('/bin/bash')
                expected[key] = '/bin/bash'
            elif isinstance(control, Gtk.FontButton):
                control.set_font('Serif 12')
                control.emit('font-set')
                expected[key] = 'Serif'
            else:
                self.fail(f'untested control for {key}')
            self.assertEqual(changes[-1], (key, expected[key]))
        self.assertEqual([key for key, _ in changes], list(settings.NEW_KEYS))
        stored = settings.get_values()
        self.assertEqual({key: stored[key] for key in expected}, expected)
        self.assertEqual({key: self.dialog.values[key] for key in expected}, expected)
        # Reset extra buttons restore defaults (accent back to the theme's own).
        for key in ('accent', 'ui_font', 'editor_font'):
            self.assertTrue(self.dialog.reset_buttons[key].get_sensitive())
            self.dialog.reset_buttons[key].clicked()
            self.assertEqual(changes[-1], (key, settings.DEFAULTS[key]))
            self.assertEqual(settings.get_value(key), settings.DEFAULTS[key])
            self.assertFalse(self.dialog.reset_buttons[key].get_sensitive())
        self.assertIsNone(self.dialog.accent_picker.value)
        controls['indent_width'].set_value(-5)
        self.assertEqual(changes[-1], ('indent_width', 2))
        count = len(changes)
        controls['indent_width'].set_value(2)  # No change, no emission.
        self.assertEqual(len(changes), count)

    def test_feature_page_order_sensitivity_search_and_keyboard(self):
        self.assertEqual([title for _, title, _ in PAGES],
                         ['Appearance', 'Editor', 'File explorer', 'Notes', 'Terminal',
                          'Graph view', 'Git graph', 'Hotkeys', 'About'])
        self.show()
        changes = self.changes()
        for page, feature in self.dialog._feature_pages.items():
            rows = [r for _, card in self.dialog._groups[page] for r in card.rows]
            self.assertEqual(rows[0].key, feature)
            self.dialog.nav_list.select_page(page)
            self.assertTrue(self.dialog.focus_content())
            self.assertIs(self.dialog.get_focus(), rows[0])
            send_key(self.dialog, Gdk.KEY_Down)
            self.assertIs(self.dialog.get_focus(), rows[1])
            self.dialog.controls[feature].clicked()
            self.assertEqual(changes[-1], (feature, False))
            self.assertTrue(all(not r.get_sensitive() for r in rows[1:]))
            self.assertTrue(all(r.get_opacity() < 0.5 for r in rows[1:]))
            self.assertTrue(rows[0].get_sensitive())
            self.dialog.search(rows[1].name)
            self.assertTrue(rows[1].get_visible())
            self.dialog.search('')
            self.dialog.set_values({feature: True})
            self.assertTrue(all(r.get_sensitive() for r in rows))
        for query, key in (('slash', 'notes_slash_commands'), ('login executable', 'terminal_shell'),
                           ('connection width', 'graph.lineSizeMultiplier'),
                           ('remote refs', 'gitgraph_show_remotes'),
                           ('density', 'explorer_compact')):
            self.dialog.search(query)
            self.assertTrue(self.dialog.rows[key].get_visible())
        self.dialog.search('')

    def test_all_controls_silent_initialization_and_graph_roundtrip(self):
        changes = self.changes()
        values = {}
        for key, default in settings.NEW_DEFAULTS.items():
            if isinstance(default, bool):
                values[key] = not default
            elif key in settings.CHOICES:
                values[key] = settings.CHOICES[key][-1]
            elif key in settings.LIMITS:
                values[key] = settings.LIMITS[key][1]
            else:
                values[key] = default
        self.dialog.set_values(values)
        self.assertEqual(changes, [])
        self.assertEqual({k: self.dialog.values[k] for k in values}, values)
        for key, value in values.items():
            control = self.dialog.controls[key]
            if isinstance(control, Toggle):
                self.assertEqual(control.get_active(), value)
            elif isinstance(control, Gtk.ComboBoxText):
                self.assertEqual(control.get_active_id(), value)
            elif isinstance(control, (Gtk.Scale, Gtk.SpinButton)):
                self.assertEqual(control.get_value(), value)
        self.dialog.set_values(dict.fromkeys(self.dialog._feature_pages.values(), True))
        groups = [{'query': 'tag:a', 'color': '#abcdef'}]
        graph = {**settings.GRAPH_DEFAULTS, 'search': 'path:src', 'colorGroups': groups,
                 'future': {'x': 2}}
        self.dialog.set_values({'graph': graph})
        self.assertEqual(changes, [])
        self.assertNotIn('future', self.dialog.values['graph'])
        for row_key, control in self.dialog.controls.items():
            if not row_key.startswith('graph.'):
                continue
            key = row_key.split('.', 1)[1]
            if isinstance(control, Toggle):
                control.clicked()
            else:
                control.set_value(settings.GRAPH_LIMITS[key][1])
            self.assertEqual(changes[-1][0], 'graph')
            self.assertEqual(changes[-1][1], settings.get_graph())
            self.assertNotIn('future', changes[-1][1])
            self.assertEqual(changes[-1][1]['search'], 'path:src')
            self.assertEqual(changes[-1][1]['colorGroups'], groups)
            self.dialog.reset_buttons[row_key].clicked()
            self.assertEqual(self.dialog.values['graph'][key], settings.GRAPH_DEFAULTS[key])
        snapshot = self.dialog.values
        snapshot['graph']['colorGroups'][0]['query'] = 'changed'
        self.assertEqual(self.dialog.values['graph']['colorGroups'], groups)
        self.dialog.controls['terminal_shell'].set_text('/missing/idgent-shell')
        self.assertIn('not found', self.dialog.shell_hint.get_text())
        self.assertEqual(changes[-1], ('terminal_shell', '/missing/idgent-shell'))

    def test_graph_edit_retains_preferences_changed_in_an_open_graph(self):
        settings.set_graph({'linkDistance': 220, 'search': 'new query', 'future': 7})
        self.dialog.controls['graph.showTags'].clicked()
        self.assertEqual(settings.get_graph()['linkDistance'], 220)
        self.assertEqual(settings.get_graph()['search'], 'new query')
        self.assertNotIn('future', settings.get_graph())
        self.assertTrue(settings.get_graph()['showTags'])

    def test_set_values_is_silent_and_validated(self):
        changes = self.changes()
        self.dialog.set_values({'line_numbers': False, 'indent_width': 99, 'delete_mode': 'bogus',
                                'accent': '#3B82F6', 'terminal_scrollback': 5, 'unknown': 1,
                                'explorer_font_size': 20, 'ui_font': 'Cantarell'})
        self.assertEqual(changes, [])
        values = self.dialog.values
        self.assertEqual((values['line_numbers'], values['indent_width'], values['delete_mode']),
                         (False, 8, 'permanent'))
        self.assertEqual((values['accent'], values['terminal_scrollback']), ('#3b82f6', 1000))
        self.assertEqual(values['explorer_font_size'], 20)
        self.assertFalse(self.dialog.controls['line_numbers'].get_active())
        self.assertEqual(self.dialog.controls['ui_font'].get_font(), 'Cantarell')
        self.assertNotIn('unknown', values)
        self.assertEqual(settings.load(), {})

    # Layout and geometry ------------------------------------------------------

    def test_nav_width_clamp_rows_and_column(self):
        self.show()
        dialog = self.dialog
        self.assertEqual(dialog.nav_pane.get_allocated_width(), 225)
        for row in dialog.nav_list.get_children():
            self.assertTrue(24 <= row.get_allocated_height() - 2 <= 26)
        page = dialog.pages['appearance']
        column = dialog._bodies['appearance']
        x = column.translate_coordinates(page, 0, 0)[0]
        width = column.get_allocated_width()
        self.assertLessEqual(width, 700)
        self.assertEqual(x, 32)
        self.assertEqual(column.translate_coordinates(page, 0, 0)[1], 48)
        self.assertFalse(dialog.rows['theme'].narrow)
        for width, expected in ((600, 200), (1400, 250)):
            dialog.resize(width, 700)
            pump(150)
            self.assertEqual(dialog.nav_pane.get_allocated_width(), expected, width)
            if width == 600:  # 336 px column: controls stack under the text, toggles stay.
                self.assertTrue(dialog.rows['theme'].narrow)
                self.assertTrue(dialog.hotkey_card.rows[0].narrow)
                self.assertFalse(dialog.rows['reduce_motion'].narrow)
                theme = dialog.rows['theme']
                self.assertGreater(theme.controls.translate_coordinates(theme, 0, 0)[1],
                                   theme.name_label.translate_coordinates(theme, 0, 0)[1])
        self.assertFalse(dialog.rows['theme'].narrow)
        page_width = page.get_allocated_width()
        x = column.translate_coordinates(page, 0, 0)[0]
        self.assertEqual(column.get_allocated_width(), 700)
        self.assertLessEqual(abs(x - (page_width - 700 - x)), 1)  # Centred.

    def test_card_radius_and_row_metrics(self):
        self.show()
        ui = get_theme('idgent-dark').ui
        card = self.dialog._groups['appearance'][0][1]
        x, y = card.translate_coordinates(self.dialog, 0, 0)
        window = self.dialog.get_window()

        def pixel(px, py):
            data = Gdk.pixbuf_get_from_window(window, px, py, 1, 1).get_pixels()
            return tuple(data[:3])

        self.assertEqual(pixel(x, y), hex_rgb(ui['bg']))  # Rounded corner shows the page.
        self.assertEqual(pixel(x + 2, y + 2), hex_rgb(ui['bg']))
        self.assertEqual(pixel(x + 12, y + 12), hex_rgb(ui['surface_alt']))
        self.assertEqual(pixel(x + card.get_allocated_width() // 2, y), hex_rgb(ui['surface_alt']))
        row = self.dialog.rows['reduce_motion']
        self.assertTrue(70 <= row.get_allocated_height() <= 82, row.get_allocated_height())
        toggle = self.dialog.controls['reduce_motion']
        rx = row.translate_coordinates(self.dialog, 0, 0)[0]
        tx = toggle.translate_coordinates(self.dialog, 0, 0)[0]
        self.assertEqual(rx + row.get_allocated_width() - 20, tx + toggle.get_allocated_width())
        self.assertEqual((toggle.get_allocated_width(), toggle.get_allocated_height()), (40, 22))

    # Keyboard -----------------------------------------------------------------

    def test_keyboard_navigation_model(self):
        self.show()
        dialog = self.dialog
        nav = dialog.nav_list
        nav.rows['appearance'].grab_focus()
        send_key(nav, Gdk.KEY_Down)
        self.assertEqual(nav.page_id, 'editor')
        self.assertEqual(dialog.stack.get_visible_child_name(), 'editor')
        send_key(nav, Gdk.KEY_k)
        self.assertEqual(dialog.stack.get_visible_child_name(), 'appearance')
        send_key(nav, Gdk.KEY_Return)
        self.assertIs(dialog.get_focus(), dialog.rows['theme'])
        send_key(dialog, Gdk.KEY_Down)
        self.assertIs(dialog.get_focus(), dialog.rows['accent'])
        send_key(dialog, Gdk.KEY_Down)
        send_key(dialog, Gdk.KEY_Down)  # Across cards: into the "Font" group.
        self.assertIs(dialog.get_focus(), dialog.rows['ui_font'])
        send_key(dialog, Gdk.KEY_Up)
        self.assertIs(dialog.get_focus(), dialog.rows['reduce_motion'])
        changes = self.changes()
        send_key(dialog, Gdk.KEY_space)
        send_key(dialog, Gdk.KEY_Return)
        self.assertEqual(changes, [('reduce_motion', True), ('reduce_motion', False)])
        send_key(dialog, Gdk.KEY_Left)
        self.assertIs(dialog.get_focus(), nav.rows['appearance'])
        send_key(nav, Gdk.KEY_ISO_Left_Tab, Gdk.ModifierType.SHIFT_MASK)
        self.assertIs(dialog.get_focus(), dialog.search_entry)
        nav.rows['appearance'].grab_focus()
        send_key(nav, Gdk.KEY_Tab)
        self.assertIs(dialog.get_focus(), dialog.rows['theme'])
        send_key(dialog, Gdk.KEY_Tab)  # Tab moves from the row into its controls.
        self.assertTrue(dialog.get_focus().is_ancestor(dialog.rows['theme']))
        send_key(dialog, Gdk.KEY_f, Gdk.ModifierType.CONTROL_MASK)
        self.assertIs(dialog.get_focus(), dialog.search_entry)
        nav.rows['appearance'].grab_focus()
        send_key(dialog, Gdk.KEY_slash)
        self.assertIs(dialog.get_focus(), dialog.search_entry)

    def test_toggle_space_and_focus_ring_rule(self):
        self.show()
        toggle = self.dialog.controls['reduce_motion']
        toggle.grab_focus()
        changes = self.changes()
        send_key(toggle, Gdk.KEY_space)
        pump(300)  # GtkButton keyboard activation clicks after a short press.
        self.assertEqual(changes, [('reduce_motion', True)])
        css = build_css(get_theme('idgent-dark').ui)
        self.assertIn('list.idgent-card > row:focus', css)
        self.assertIn('button.idgent-toggle:focus { outline-width: 2px', ' '.join(css.split()))

    # Search -------------------------------------------------------------------

    def test_search_filters_groups_and_escape_order(self):
        self.show()
        dialog = self.dialog
        dialog.search_entry.grab_focus()
        dialog.search_entry.set_text('cursor')
        pump()
        self.assertEqual(dialog.stack.get_visible_child_name(), 'results')
        visible = [r.key for r in dialog._visible_rows()]
        self.assertEqual(visible, ['highlight_line', 'terminal_cursor', 'terminal_blink'])
        self.assertEqual(dialog.nav_list.rows['terminal'].count_label.get_text(), '2')
        self.assertFalse(dialog.nav_list.rows['files'].get_child_visible())
        self.assertTrue(dialog._sections['terminal'].get_visible())
        self.assertFalse(dialog._sections['files'].get_visible())
        for query, key in (('brand', 'accent'), ('gutter', 'line_numbers'), ('trash', 'delete_mode'),
                           ('Ctrl+Shift+S', None)):
            dialog.search_entry.set_text(query)
            rows = dialog._visible_rows()
            if key:
                self.assertIn(dialog.rows[key], rows, query)
            else:
                self.assertEqual([r.name for r in rows], ['Save all files'])
        dialog.search_entry.set_text('zzzz')
        dialog.show_all()  # e.g. MainWindow.open_settings() while a search is active.
        self.assertTrue(dialog.empty_label.get_visible())
        self.assertFalse(dialog._sections['terminal'].get_visible())
        self.assertFalse(dialog.hotkey_box.get_visible())
        self.assertIn('zzzz', dialog.empty_label.get_text())
        self.assertEqual(dialog._visible_rows(), [])
        send_key(dialog, Gdk.KEY_Escape)  # Clears first...
        self.assertEqual(dialog.search_entry.get_text(), '')
        self.assertTrue(dialog.get_visible())
        self.assertEqual(dialog.stack.get_visible_child_name(), 'appearance')
        self.assertFalse(dialog.empty_label.get_visible())
        self.assertIs(dialog._bodies['appearance'].get_parent(), dialog._columns['appearance'])
        self.assertEqual(len(dialog._visible_rows()), 4)
        dialog.show_all()  # show_all() must not resurrect filtered widgets.
        self.assertFalse(dialog.empty_label.get_visible())
        send_key(dialog, Gdk.KEY_Escape)  # ...then closes.
        self.assertFalse(dialog.get_visible())

    def test_hotkeys_page_list_filter_and_feed(self):
        dialog = self.dialog
        names = [row.name for row in dialog.hotkey_card.rows]
        self.assertEqual(names, [label for label, _ in DEFAULT_HOTKEYS])
        self.assertIn(('Open settings', 'Ctrl+,'), DEFAULT_HOTKEYS)
        row = dialog.hotkey_card.rows[names.index('Next editor tab')]
        chips = [c.get_text() for c in row.control.get_children()]
        self.assertEqual(chips, ['Ctrl+Tab', 'Ctrl+PageDown'])
        self.show()
        dialog.nav_list.select_page('hotkeys')
        dialog.hotkey_filter.set_text('terminal')
        self.assertTrue(all('terminal' in r.name.lower() for r in dialog.hotkey_card.visible_rows()))
        dialog.hotkey_filter.set_text('nothing-here')
        self.assertTrue(dialog.hotkeys_empty.get_visible())
        dialog.hotkey_filter.grab_focus()
        send_key(dialog, Gdk.KEY_Escape)
        self.assertEqual(dialog.hotkey_filter.get_text(), '')
        self.assertTrue(dialog.get_visible())
        dialog.set_hotkeys([('Do things', 'Ctrl+D'), ('Other', 'F9 / F10')])
        self.assertEqual([r.name for r in dialog.hotkey_card.rows], ['Do things', 'Other'])
        self.assertEqual(len(dialog.hotkey_card.visible_rows()), 2)

    def test_about_page_and_copy_path(self):
        dialog = self.dialog
        path = str(settings.settings_path())
        self.assertEqual(dialog.settings_path_label.get_text(), path)
        dialog.copy_button.clicked()
        self.assertEqual(Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).wait_for_text(), path)
        self.assertIsInstance(dialog.rows['settings_file'], SettingRow)

    def test_css_parses_for_every_theme(self):
        for theme in THEMES:
            provider = Gtk.CssProvider()
            errors = []
            provider.connect('parsing-error', lambda _, section, error: errors.append(str(error)))
            css = build_css(theme.ui)
            provider.load_from_data(css.encode())
            self.assertEqual(errors, [], theme.id)
            self.assertIn('button.idgent-toggle', css)
            self.assertIn(build_css(theme.ui), style.build_css(theme))
