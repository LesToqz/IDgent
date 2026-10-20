"""IDgent settings window (spec 4.15/5.3); the owner applies changes.

A transient, non-modal ``Gtk.Window``: navigation pane on the left (search,
"Options" pages), a ``Gtk.Stack`` of scrolled pages on the right with a
centred 700 px column of cards. Close, Escape and the window close control
hide the window so it can be reused; Escape first clears a search.

Signals: ``theme-selected(str)`` and ``font-size-changed(str, int)`` for the
existing keys (not persisted here; the owner saves them), and
``setting-changed(str, object)`` for every other key in
``settings.NEW_KEYS`` and the full ``graph`` dictionary (validated and
persisted here before emission).
Programmatic setters (``set_theme_id``, ``set_sizes``, ``set_values``,
``set_hotkeys``) never emit. ``build_css(ui)`` also contains
``widgets.build_css(ui)``; ``style.build_css`` installs both.
"""
from __future__ import annotations

import copy
import importlib.metadata
import os
import shutil
from pathlib import Path

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, GLib, GObject, Gtk, Pango

from idgent import settings, widgets
from idgent.search import SKIP_DIRS
from idgent.themes import THEMES, get_theme
from idgent.widgets import (CenteredColumn, HotkeyChip, NavList, SearchField, SettingCard,
                            SettingRow, SwatchPicker, Toggle, WrapBox)

PAGES = (
    ('appearance', 'Appearance', 'preferences-desktop-appearance-symbolic'),
    ('editor', 'Editor', 'accessories-text-editor-symbolic'),
    ('files', 'File explorer', 'folder-symbolic'),
    ('notes', 'Notes', 'text-x-generic-symbolic'),
    ('terminal', 'Terminal', 'utilities-terminal-symbolic'),
    ('graph', 'Graph view', 'view-grid-symbolic'),
    ('gitgraph', 'Git graph', 'view-list-symbolic'),
    ('hotkeys', 'Hotkeys', 'input-keyboard-symbolic'),
    ('about', 'About', 'help-about-symbolic'),
)
# Today's app shortcuts (window.py accelerators and the README table); the
# window may replace them with set_hotkeys(). " / " separates alternatives.
DEFAULT_HOTKEYS = (
    ('Save file', 'Ctrl+S'),
    ('Save all files', 'Ctrl+Shift+S'),
    ('Quit', 'Ctrl+Q'),
    ('Open settings', 'Ctrl+,'),
    ('Next theme', 'Ctrl+Alt+T'),
    ('Toggle explorer sidebar', 'Ctrl+B'),
    ('Search in files', 'Ctrl+Shift+F'),
    ('Toggle bottom terminal', 'Ctrl+`'),
    ('Toggle right terminal', 'Ctrl+Alt+`'),
    ('Split terminal', 'Ctrl+Shift+5'),
    ('Toggle terminal fullscreen', 'Ctrl+Shift+F11'),
    ('Close editor tab', 'Ctrl+W'),
    ('Undo', 'Ctrl+Z'),
    ('Redo', 'Ctrl+Shift+Z'),
    ('Find in file', 'Ctrl+F'),
    ('Toggle Markdown preview', 'Ctrl+Shift+V'),
    ('Next editor tab', 'Ctrl+Tab / Ctrl+PageDown'),
    ('Previous editor tab', 'Ctrl+Shift+Tab / Ctrl+PageUp'),
    ('Toggle line comment', 'Ctrl+/'),
    ('Zoom editor in', 'Ctrl+Plus / Ctrl+='),
    ('Zoom editor out', 'Ctrl+Minus'),
    ('Reset editor zoom', 'Ctrl+0'),
    ('Split focused terminal', 'Ctrl+Shift+T'),
    ('Kill focused terminal', 'Ctrl+Shift+W'),
    ('Previous / next terminal', 'Ctrl+Alt+Left / Ctrl+Alt+Right'),
    ('Previous / next stacked terminal', 'Ctrl+Alt+Up / Ctrl+Alt+Down'),
    ('Copy in terminal', 'Ctrl+Shift+C'),
    ('Paste in terminal', 'Ctrl+Shift+V'),
    ('Zoom terminal in', 'Ctrl+Shift+Plus'),
    ('Zoom terminal out', 'Ctrl+Shift+Minus'),
    ('Reset terminal zoom', 'Ctrl+Shift+0'),
    ('Rename in explorer', 'F2'),
    ('Delete in explorer', 'Delete'),
    ('Refresh explorer', 'F5'),
    ('New folder in explorer', 'Ctrl+Shift+N'),
)
NAV_WIDTH = (0.25, 200, 250)
NARROW_CONTENT = 400  # Below this column width rows stack their controls (spec 4.15).
FONT_ROWS = {
    'terminal_font_size': ('Terminal text size', 'Text size of every terminal, in points.', 'terminal'),
    'editor_font_size': ('Editor text size',
                         'Default size of file content, in points. Ctrl+0 returns to it.', 'editor'),
    'explorer_font_size': ('Explorer text size', 'Size of file names in the explorer, in points.',
                           'files'),
}
LICENSE_NOTE = ('IDgent runs on GTK 3, VTE and PyGObject (LGPL-2.1-or-later libraries). '
                'Icons come from your system icon theme and the interface uses your system '
                'fonts; no third-party fonts or icons are bundled.')


def build_css(ui: dict[str, str]) -> str:
    return f'''
window.idgent-settings, .idgent-settings-content, .idgent-settings-content viewport {{
    background-color: {ui['bg']}; color: {ui['foreground']}; }}
.idgent-settings-nav {{ background-color: {ui['bg']}; border-right: 1px solid {ui['border']}; }}
.idgent-settings-nav viewport {{ background-color: transparent; }}
.idgent-settings-group-title {{ font-size: 12px; font-weight: 600; color: {ui['faint']}; }}
.idgent-settings-heading {{ font-size: 15px; font-weight: 600; color: {ui['foreground']}; }}
.idgent-settings-page-title {{ font-size: 20px; font-weight: 600; color: {ui['foreground']}; }}
.idgent-settings-empty {{ font-size: 13px; color: {ui['muted']}; }}
.idgent-settings-swatch {{ border-radius: 3px; min-width: 10px; min-height: 16px;
    border: 1px solid {ui['border_hover']}; }}
.idgent-settings combobox button, .idgent-settings button.font {{ font-size: 13px; }}
.idgent-settings button.font label {{ font-size: 13px; }}
.idgent-settings scale {{ padding: 0; }}
.idgent-settings scale trough {{ min-height: 4px; border-radius: 4px; border: 0;
    background: {ui['border_hover']}; box-shadow: none; outline-width: 0; }}
.idgent-settings scale highlight {{ min-height: 4px; border-radius: 4px; border: 0;
    background: {ui['accent']}; }}
.idgent-settings scale slider {{ min-width: 18px; min-height: 18px; margin: -7px;
    border-radius: 9px; border: 1px solid alpha(#000000, 0.12); background: #ffffff;
    box-shadow: 0 1px 2px alpha(#000000, 0.2); }}
.idgent-settings scale slider:hover {{ border-color: {ui['border_focus']}; }}
.idgent-settings scale:focus slider {{ box-shadow: 0 0 0 2px {ui['border_focus']}; }}
.idgent-settings scale value {{ font-size: 13px; color: {ui['muted']}; min-width: 20px; }}
.idgent-settings spinbutton {{ background: {ui['input_bg']}; color: {ui['foreground']};
    border: 1px solid {ui['border']}; border-radius: 5px; box-shadow: none; font-size: 13px; }}
.idgent-settings spinbutton entry, .idgent-settings spinbutton entry:focus {{
    background: transparent; border: 0; box-shadow: none; min-height: 20px; padding: 4px 8px; }}
.idgent-settings spinbutton button {{ background: transparent; box-shadow: none; border: 0;
    border-left: 1px solid {ui['border']}; border-radius: 0; padding: 0 6px; min-height: 20px;
    color: {ui['muted']}; }}
.idgent-settings spinbutton button:hover {{ background: alpha({ui['overlay']}, 0.067);
    color: {ui['foreground']}; }}
.idgent-settings spinbutton:focus {{ border-color: {ui['border_focus']};
    box-shadow: 0 0 0 2px {ui['border_focus']}; }}
''' + widgets.build_css(ui)


SETTINGS_CSS = build_css(get_theme(None).ui)


def _version() -> str:
    try:
        return importlib.metadata.version('idgent')
    except importlib.metadata.PackageNotFoundError:
        pass
    try:
        import tomllib
        project = Path(__file__).resolve().parents[1] / 'pyproject.toml'
        return tomllib.loads(project.read_text(encoding='utf-8'))['project']['version']
    except (OSError, ValueError, KeyError):
        return 'unknown'


class _ThemeRow:
    def __init__(self, theme_id):
        self.theme_id = theme_id


class _ThemeListCompat:
    """The former ListBox API (``theme_list.select_row(theme_rows[id])``) on the dropdown."""

    def __init__(self, combo):
        self._combo = combo

    def select_row(self, row):
        self._combo.set_active_id(row.theme_id)


def _class(widget, name):
    widget.get_style_context().add_class('idgent-settings-' + name)
    return widget


class SettingsDialog(Gtk.Window):
    __gsignals__ = {
        'theme-selected': (GObject.SignalFlags.RUN_LAST, None, (str,)),
        'font-size-changed': (GObject.SignalFlags.RUN_LAST, None, (str, int)),
        'setting-changed': (GObject.SignalFlags.RUN_LAST, None, (str, object)),
    }

    def __init__(self, parent=None, theme_id=None, sizes=None):
        super().__init__(title='Settings', transient_for=parent, destroy_with_parent=True)
        self.get_style_context().add_class('idgent-settings')
        self.set_default_size(900, 700)
        self.set_size_request(600, 400)
        self._updating = True
        self._theme_id = None
        self._values: dict[str, object] = {}
        self._graph = settings.get_graph()
        self._saved_graph = copy.deepcopy(self._graph)
        self._feature_pages = {}
        self._setters = {}
        self._searching = False
        self._narrow = False
        self._nav_counts: dict[str, int] | None = None
        self.rows: dict[str, SettingRow] = {}
        self.controls: dict[str, Gtk.Widget] = {}
        self.spins: dict[str, Gtk.Scale] = {}  # Font size sliders (historic name).
        self.reset_buttons: dict[str, Gtk.Button] = {}
        self.value_labels: dict[str, Gtk.Label] = {}
        self.pages: dict[str, Gtk.ScrolledWindow] = {}
        self._bodies: dict[str, Gtk.Box] = {}
        self._columns: dict[str, CenteredColumn] = {}
        self._groups: dict[str, list[tuple[Gtk.Box, SettingCard]]] = {}
        self.connect('delete-event', self._delete)
        self.connect('key-press-event', self._key)
        self.connect('configure-event', lambda _, event: self._update_nav_width(event.width))

        root = Gtk.Box()
        self.add(root)
        root.pack_start(self._build_nav(), False, False, 0)
        self.stack = _class(Gtk.Stack(), 'content')
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.stack.set_hexpand(True)
        root.pack_start(self.stack, True, True, 0)
        builders = dict(appearance=self._build_appearance, editor=self._build_editor,
                        files=self._build_files, notes=self._build_notes, terminal=self._build_terminal,
                        graph=self._build_graph, gitgraph=self._build_gitgraph,
                        hotkeys=self._build_hotkeys, about=self._build_about)
        for page_id, title, icon in PAGES:
            self.nav_list.add_page(page_id, title, icon)
            body = self._page(page_id)
            builders[page_id](body)
        self._build_results()
        self.set_hotkeys(DEFAULT_HOTKEYS)

        self.set_theme_id(settings.get_theme_id() if theme_id is None else theme_id)
        self.set_sizes(settings.get_font_sizes() if sizes is None else {**settings.DEFAULT_FONT_SIZES, **sizes})
        stored = settings.get_values()
        self.set_values({key: stored[key] for key in settings.NEW_KEYS})
        self._update_nav_width(900)
        root.show_all()
        # Visibility of these is managed by search; keep show_all() away from them.
        for groups in self._groups.values():
            for group, _ in groups:
                group.set_no_show_all(True)
        for widget in (*self._sections.values(), self.empty_label, self.hotkey_box):
            widget.set_no_show_all(True)
        self.empty_label.hide()
        self.hotkeys_empty.hide()
        self.nav_list.select_page('appearance')
        self.set_focus(self.nav_list.rows['appearance'])
        self._updating = False

    # Layout ---------------------------------------------------------------

    def _build_nav(self):
        pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.nav_pane = _class(pane, 'nav')
        self.search_entry = SearchField('Search settings…')
        for side in ('start', 'end', 'top'):
            getattr(self.search_entry, 'set_margin_' + side)(12)
        self.search_entry.connect('changed', lambda entry: self.search(entry.get_text()))
        self.search_entry.connect('key-press-event', self._search_key)
        pane.pack_start(self.search_entry, False, False, 0)
        self.group_title = _class(Gtk.Label(label='Options', xalign=0), 'group-title')
        self.group_title.set_margin_top(12 + 8)
        self.group_title.set_margin_bottom(8)
        self.group_title.set_margin_start(12 + 8)
        pane.pack_start(self.group_title, False, False, 0)
        self.nav_list = NavList()
        self.nav_list.set_margin_start(12)
        self.nav_list.set_margin_end(12)
        self.nav_list.set_margin_bottom(12)
        self.nav_list.set_filter_func(self._nav_filter)
        self.nav_list.connect('row-selected', self._nav_selected)
        self.nav_list.connect('key-press-event', self._nav_key)
        self.nav_list.get_accessible().set_name('Settings pages')
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(self.nav_list)
        pane.pack_start(scroll, True, True, 0)
        return pane

    def _update_nav_width(self, width):
        fraction, low, high = NAV_WIDTH
        target = max(low, min(high, round(width * fraction)))
        if self.nav_pane.get_size_request()[0] != target:
            self.nav_pane.set_size_request(target, -1)
        column = min(700, width - target - 1 - 2 * 32)
        narrow = column < NARROW_CONTENT
        if narrow != self._narrow:
            self._narrow = narrow
            for row in self._all_rows():
                row.set_narrow(narrow)
        return False

    def _all_rows(self):
        return [row for groups in self._groups.values() for _, card in groups for row in card.rows]

    def _scrolled(self, child):
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        column = CenteredColumn()
        column.set_focus_vadjustment(scroll.get_vadjustment())
        column.add(child)
        scroll.add(column)
        return scroll, column

    def _page(self, page_id):
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        scroll, column = self._scrolled(body)
        self.stack.add_named(scroll, page_id)
        self.pages[page_id], self._bodies[page_id], self._columns[page_id] = scroll, body, column
        self._groups[page_id] = []
        return body

    def _group(self, body, heading, rows):
        group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page_id = next(p for p, b in self._bodies.items() if b is body)
        if self._groups[page_id]:
            group.set_margin_top(24)
        if heading:
            label = _class(Gtk.Label(label=heading, xalign=0), 'heading')
            label.set_margin_start(16)
            label.set_margin_end(16)
            label.set_margin_bottom(16)
            group.pack_start(label, False, False, 0)
        card = SettingCard()
        card.connect('key-press-event', self._row_key)
        for row in rows:
            card.add(row)
            row.page_id = page_id
        group.pack_start(card, False, False, 0)
        body.pack_start(group, False, False, 0)
        self._groups[page_id].append((group, card))
        return card

    def _build_results(self):
        self.results = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.results_scroll, _ = self._scrolled(self.results)
        self.stack.add_named(self.results_scroll, 'results')
        self._sections = {}
        for page_id, title, _ in PAGES:
            section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            heading = _class(Gtk.Label(label=title, xalign=0), 'page-title')
            heading.set_margin_bottom(16)
            heading.set_margin_start(16)
            section.set_margin_bottom(32)
            section.pack_start(heading, False, False, 0)
            self.results.pack_start(section, False, False, 0)
            self._sections[page_id] = section
        self.empty_label = _class(Gtk.Label(xalign=0.5), 'empty')
        self.empty_label.set_line_wrap(True)
        self.empty_label.set_margin_top(48)
        self.results.pack_start(self.empty_label, False, False, 0)

    # Row factories ----------------------------------------------------------

    def _row(self, key, name, description, control, setter, aliases=(), reset=False, extra=()):
        reset_cb = (lambda: self._changed(key, settings.DEFAULTS[key])) if reset else None
        row = SettingRow(name, description, control, key=key, aliases=aliases, reset=reset_cb,
                         extra=extra)
        self.rows[key], self.controls[key], self._setters[key] = row, control, setter
        if row.reset_button is not None:
            self.reset_buttons[key] = row.reset_button
        return row

    def _toggle(self, key, name, description, aliases=()):
        toggle = Toggle(label=name)
        toggle.connect('toggled', lambda t: self._changed(key, t.get_active()))
        return self._row(key, name, description, toggle, toggle.set_active, aliases, reset=True)

    def _dropdown(self, key, name, description, options, aliases=()):
        combo = Gtk.ComboBoxText()
        for value, label in options:
            combo.append(value, label)
        combo.connect('changed', lambda c: c.get_active_id() and self._changed(key, c.get_active_id()))
        return self._row(key, name, description, combo, combo.set_active_id, aliases, reset=True)

    def _slider(self, key, name, description, low, high, aliases=(), reset=True):
        scale = Gtk.Scale.new(Gtk.Orientation.HORIZONTAL, Gtk.Adjustment(
            value=low, lower=low, upper=high, step_increment=1, page_increment=1, page_size=0))
        scale.set_digits(0)
        scale.set_round_digits(0)
        scale.set_draw_value(False)
        scale.set_size_request(120, -1)
        value = _class(Gtk.Label(label=str(low), xalign=1), 'value')
        value.get_style_context().add_class('idgent-setting-value')
        value.set_width_chars(len(str(high)))
        scale.connect('value-changed', lambda s: value.set_text(str(round(s.get_value()))))
        self.value_labels[key] = value
        return scale, self._row(key, name, description, scale, scale.set_value, aliases, reset,
                                extra=(value,))

    def _font_button(self, key, name, description, fallback, aliases=()):
        button = Gtk.FontButton()
        button.set_level(Gtk.FontChooserLevel.FAMILY)
        button.set_show_size(False)
        button.set_show_style(False)
        button.set_use_font(False)
        button.set_title(name)

        def chosen(widget):
            family = widget.get_font_family()
            text = family.get_name() if family else Pango.FontDescription.from_string(
                widget.get_font() or '').get_family()
            self._changed(key, text or '')

        def show(value):
            button.set_font(value or fallback())

        button.connect('font-set', chosen)
        return self._row(key, name, description, button, show, aliases, reset=True)

    def _font_size_row(self, key):
        name, description, _ = FONT_ROWS[key]
        low, high = settings.FONT_SIZE_LIMITS[key]
        scale, row = self._slider(key, name, description, low, high, ('font text zoom',))
        scale.get_accessible().set_name(name)
        scale.connect('value-changed', self._size_changed, key)
        self.spins[key] = scale
        return row

    @staticmethod
    def _ui_font():
        name = Gtk.Settings.get_default().get_property('gtk-font-name') or 'Sans'
        return Pango.FontDescription.from_string(name).get_family() or 'Sans'

    # Pages --------------------------------------------------------------------

    def _build_appearance(self, body):
        self.theme_combo = Gtk.ComboBoxText()
        for theme in THEMES:
            self.theme_combo.append(theme.id, theme.name)
        self.theme_combo.connect('changed', self._theme_changed)
        self.theme_rows = {theme.id: _ThemeRow(theme.id) for theme in THEMES}
        self.theme_list = _ThemeListCompat(self.theme_combo)
        self.theme_preview = Gtk.Box(spacing=2)
        self.theme_preview.set_valign(Gtk.Align.CENTER)
        self.theme_preview.get_accessible().set_name('Theme preview')
        self._preview_providers = []
        for _ in range(6):
            swatch = _class(Gtk.Box(), 'swatch')
            swatch.set_size_request(10, 16)
            provider = Gtk.CssProvider()
            swatch.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
            self._preview_providers.append(provider)
            self.theme_preview.pack_start(swatch, False, False, 0)
        theme_row = SettingRow('Color theme', 'Colours for the whole app; applies as you choose.',
                               self.theme_combo, key='theme', extra=(self.theme_preview,),
                               aliases=('color colour scheme dark light mode appearance base',))
        self.rows['theme'], self.controls['theme'] = theme_row, self.theme_combo

        self.accent_picker = SwatchPicker()
        self.accent_picker.connect('value-changed', lambda _, value: self._changed('accent', value))
        accent = self._row('accent', 'Accent color',
                           "Colour of toggles, sliders and highlights. Restore to use the theme's own.",
                           self.accent_picker, self.accent_picker.set_value,
                           ('highlight brand primary colour',), reset=True)
        motion_row = self._toggle('reduce_motion', 'Reduce motion',
                                  'Turn off interface animations such as sliding toggles.',
                                  ('animation accessibility',))
        self._group(body, None, (theme_row, accent, motion_row))
        ui_font = self._font_button('ui_font', 'Interface font',
                                    'Font for menus, sidebars and dialogs. Restore to use the system font.',
                                    self._ui_font, ('typeface family',))
        self._group(body, 'Font', (ui_font,))

    def _build_editor(self, body):
        editor_font = self._font_button('editor_font', 'Editor font',
                                        'Font for file content. Restore to use the default monospace font.',
                                        lambda: 'Monospace', ('typeface family monospace',))
        self._group(body, None, (self._font_size_row('editor_font_size'), editor_font))
        self._group(body, 'Display', (
            self._toggle('line_numbers', 'Line numbers', 'Show line numbers in the editor gutter.',
                         ('gutter',)),
            self._toggle('highlight_line', 'Highlight current line',
                         'Tint the line that contains the cursor.', ('active cursor line',)),
        ))
        scale, indent = self._slider('indent_width', 'Indent width',
                                     'Number of columns per indentation level.',
                                     *settings.LIMITS['indent_width'], ('tab size spaces',), reset=False)
        scale.connect('value-changed', lambda s: self._changed('indent_width', round(s.get_value())))
        self._group(body, 'Behavior', (
            self._toggle('indent_tabs', 'Indent using tabs',
                         'Insert tab characters instead of spaces when indenting.', ('spaces',)),
            indent,
            self._toggle('auto_pair', 'Auto-pair brackets',
                         'Insert the closing bracket or quote when typing an opening one.',
                         ('brackets quotes close',)),
        ))

    def _feature(self, body, key, name, description):
        page_id = next(p for p, b in self._bodies.items() if b is body)
        self._feature_pages[page_id] = key
        self._group(body, None, (self._toggle(key, 'Enable ' + name, description,
                                             ('feature enabled disable',)),))

    def _sync_features(self):
        for page_id, feature in self._feature_pages.items():
            for _, card in self._groups[page_id]:
                for row in card.rows:
                    if row.key != feature:
                        enabled = self._values.get(feature, True)
                        row.set_sensitive(enabled)
                        row.set_opacity(1 if enabled else 0.48)

    def _spin(self, key, name, description, step=1, aliases=()):
        spin = Gtk.SpinButton.new_with_range(*settings.LIMITS[key], step)
        spin.set_numeric(True)
        spin.set_width_chars(6)
        spin.connect('value-changed', lambda s: self._changed(key, s.get_value_as_int()))
        return self._row(key, name, description, spin, spin.set_value, aliases, reset=True)

    def _build_files(self, body):
        self._feature(body, 'feature_explorer', 'file explorer', 'Browse and manage project files.')
        self._group(body, None, (
            self._toggle('explorer_git_colors', 'Git status colours',
                         'Colour file names by their Git status.', ('version control colors',)),
            self._toggle('explorer_folders_first', 'Folders first',
                         'List folders before files.', ('directory sort order',)),
            self._toggle('explorer_compact', 'Compact rows',
                         'Use less space between file names.', ('density spacing',)),
            self._font_size_row('explorer_font_size'),
        ))
        self._group(body, None, (self._toggle(
            'show_hidden', 'Show hidden files',
            'Show files and folders whose names start with a dot in the explorer.', ('dotfiles',)),))
        self._group(body, 'Trash', (
            self._toggle('confirm_delete', 'Confirm before deleting',
                         'Ask before deleting files and folders from the explorer.', ('delete',)),
            self._dropdown('delete_mode', 'Deleted files',
                           'What happens to files deleted from the explorer.',
                           (('trash', 'Move to system trash'), ('permanent', 'Delete permanently')),
                           ('trash bin remove',)),
        ))
        ignored = SettingRow('Ignored folders', 'Folders skipped by Search in Files.',
                             aliases=('excluded exclude search', *sorted(SKIP_DIRS)))
        chips = WrapBox()
        chips.set_margin_top(10)
        for name in sorted(SKIP_DIRS):
            chips.add(HotkeyChip(name))
        ignored.desc_label.get_parent().pack_start(chips, False, False, 0)
        self.rows['ignored_folders'] = ignored
        self._group(body, 'Search', (ignored,))

    def _build_notes(self, body):
        self._feature(body, 'feature_notes', 'notes', 'Write and organise project notes.')
        self._group(body, None, (
            self._toggle('md_live_preview', 'Edit Markdown with live preview',
                         'Render Markdown while you edit.', ('markdown source reading view',)),
            self._toggle('md_readable_width', 'Readable line length',
                         'Keep Markdown lines at a comfortable reading width.', ('markdown readable width',)),
            self._toggle('notes_slash_commands', 'Slash commands',
                         'Type / for a command menu in notes.', ('slash insert menu',)),
            self._toggle('notes_toolbar', 'Insert toolbar',
                         'Show the formatting and insert toolbar in notes.', ('formatting buttons',)),
            self._dropdown('notes_sort', 'Sort notes by', 'Order notes in the explorer.',
                           (('name', 'Name'), ('modified', 'Last modified')), ('order recent',)),
            self._toggle('notes_open_after_create', 'Open a note after creating it',
                         'Start editing each newly created note.', ('new note focus',)),
        ))
        root = getattr(self.get_transient_for(), 'root', '<project>')
        path = str(Path(root) / '.IDgent' / 'Notes')
        row = SettingRow('Notes folder', path, aliases=('notes directory path location',))
        row.desc_label.set_selectable(True)
        self.rows['notes_folder'] = row
        self._group(body, 'Location', (row,))

    def _build_terminal(self, body):
        self._feature(body, 'feature_terminal', 'terminal', 'Run shells in project terminal docks.')
        entry = Gtk.Entry()
        entry.set_width_chars(18)
        entry.set_placeholder_text('Login shell')
        hint = Gtk.Label(xalign=0)
        hint.set_line_wrap(True)
        hint.get_style_context().add_class('idgent-setting-desc')

        def shell_hint(text):
            hint.set_text('Empty uses your login shell.' if not text else
                          'Shell will be checked when a terminal opens.' if
                          shutil.which(text) or (Path(text).is_file() and os.access(text, os.X_OK))
                          else 'Shell was not found; check its name or executable path.')

        def shell_set(text):
            entry.set_text(text)
            shell_hint(text)

        entry.connect('changed', lambda e: (shell_hint(e.get_text()),
                                            self._changed('terminal_shell', e.get_text())))
        shell = self._row('terminal_shell', 'Shell', 'Executable name or path; empty uses the login shell.',
                          entry, shell_set, ('bash zsh command login executable',), reset=True)
        shell.desc_label.get_parent().pack_start(hint, False, False, 0)
        self.shell_hint = hint
        self._group(body, None, (
            self._spin('terminal_max_panes', 'Maximum terminals per dock',
                       'Counts every pane of a dock: terminals, graph and Git graph.',
                       aliases=('limit split panes',)),
            shell,
            self._font_size_row('terminal_font_size'),
            self._dropdown('terminal_cursor', 'Cursor shape', 'Shape of the cursor in terminals.',
                           (('block', 'Block'), ('ibeam', 'I-beam'), ('underline', 'Underline')),
                           ('caret',)),
            self._toggle('terminal_blink', 'Blinking cursor', 'Blink the cursor in terminals.',
                         ('caret blink',)),
            self._spin('terminal_scrollback', 'Scrollback lines',
                       'Lines each terminal keeps for scrolling back (1,000 to 100,000).',
                       1000, ('history buffer',)),
            self._toggle('terminal_copy_on_select', 'Copy on select',
                         'Copy selected terminal text to the clipboard.', ('selection clipboard',)),
            self._toggle('terminal_bell', 'Audible bell',
                         'Play a sound when a terminal rings its bell.', ('sound beep',)),
            self._toggle('terminal_autostart', 'Open bottom terminal at start-up',
                         'Show the bottom terminal dock when IDgent starts.', ('dock startup launch',)),
        ))

    def _graph_row(self, key, name, description, aliases=()):
        row_key = 'graph.' + key
        default = settings.GRAPH_DEFAULTS[key]
        if isinstance(default, bool):
            control = Toggle(label=name)
            setter = control.set_active
            control.connect('toggled', lambda t: self._graph_changed(key, t.get_active()))
        else:
            control = Gtk.SpinButton.new_with_range(*settings.GRAPH_LIMITS[key],
                                                   settings.GRAPH_STEPS[key])
            control.set_digits(2)
            control.set_numeric(True)
            control.set_width_chars(5)
            setter = control.set_value
            control.connect('value-changed', lambda c: self._graph_changed(key, c.get_value()))
        setter(self._graph[key])
        row = SettingRow(name, description, control, key=row_key, aliases=aliases,
                         reset=lambda: self._graph_changed(key, default))
        self.rows[row_key], self.controls[row_key] = row, control
        self._setters[row_key] = setter
        self.reset_buttons[row_key] = row.reset_button
        row.reset_button.set_sensitive(self._graph[key] != default)
        return row

    def _build_graph(self, body):
        self._feature(body, 'feature_graph', 'graph view', 'Explore project files and their references.')
        self._group(body, None, (
            self._dropdown('graph_default_type', 'Default graph type', 'Scope of newly opened graphs.',
                           (('global', 'Global'), ('local', 'Local'), ('note', 'Note')),
                           ('scope neighbourhood',)),
            self._graph_row('showTags', 'Show tags', 'Include tag markers.', ('tag markers',)),
            self._graph_row('showAttachments', 'Show attachments', 'Include referenced assets.',
                            ('images assets',)),
            self._graph_row('showOrphans', 'Show orphans', 'Include unlinked files.', ('unlinked files',)),
            self._graph_row('showArrow', 'Show arrows', 'Show the direction of references.', ('direction',)),
            self._graph_row('nodeSizeMultiplier', 'Node size', 'Size of file markers.', ('marker size',)),
            self._graph_row('lineSizeMultiplier', 'Link thickness', 'Width of connections.',
                            ('connection width',)),
            self._graph_row('textFadeMultiplier', 'Label fade', 'Detail level of file labels.',
                            ('label detail text',)),
            self._graph_row('reduceMotion', 'Reduce graph motion', 'Skip graph animation and easing.',
                            ('animate on open animation',)),
        ))

    def _build_gitgraph(self, body):
        self._feature(body, 'feature_gitgraph', 'Git graph', 'Browse branches and commit history.')
        self._group(body, None, (
            self._dropdown('gitgraph_orientation', 'Default orientation', 'Direction of commit history.',
                           (('horizontal', 'Horizontal'), ('vertical', 'Vertical')), ('layout direction',)),
            self._spin('gitgraph_max_commits', 'Maximum commits to load',
                       'Limit the amount of commit history loaded (100 to 10,000).', 100,
                       ('history limit',)),
            self._toggle('gitgraph_show_remotes', 'Show remote branches',
                         'Include remote branch references.', ('remote refs',)),
            self._toggle('gitgraph_show_tags', 'Show tags', 'Include Git tags.', ('release refs',)),
            self._dropdown('gitgraph_time_format', 'Time format in tooltips',
                           'How commit timestamps appear.',
                           (('relative', 'Relative'), ('absolute', 'Absolute')), ('date timestamp',)),
        ))

    def _build_hotkeys(self, body):
        self.hotkey_filter = SearchField('Filter hotkeys…')
        self.hotkey_filter.set_margin_bottom(16)
        self.hotkey_filter.connect('changed', lambda e: self._filter_hotkeys(e.get_text()))
        self.hotkey_filter.connect('key-press-event', self._search_key)
        self.hotkey_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.hotkey_box.pack_start(self.hotkey_filter, False, False, 0)
        body.pack_start(self.hotkey_box, False, False, 0)
        self.hotkey_card = self._group(body, None, ())
        self.hotkeys_empty = _class(Gtk.Label(label='No hotkeys match.', xalign=0.5), 'empty')
        self.hotkeys_empty.set_margin_top(24)
        self.hotkeys_empty.set_no_show_all(True)
        body.pack_start(self.hotkeys_empty, False, False, 0)

    def _build_about(self, body):
        path = str(settings.settings_path())
        version = _class(Gtk.Label(label=_version()), 'value')
        version.get_style_context().add_class('idgent-setting-value')
        version.set_selectable(True)
        self.copy_button = Gtk.Button(label='Copy path')
        self.copy_button.connect('clicked', self._copy_path)
        file_row = SettingRow('Settings file', path, self.copy_button,
                              aliases=('config configuration json location',))
        file_row.desc_label.set_selectable(True)
        self.settings_path_label = file_row.desc_label
        self.rows['settings_file'] = file_row
        self._group(body, None, (
            SettingRow('IDgent version', 'The version of IDgent that is running.', version,
                       aliases=('about release',)),
            file_row,
            SettingRow('Licenses', LICENSE_NOTE, aliases=('license licence legal credits',)),
        ))

    # Public API -------------------------------------------------------------

    @property
    def theme_id(self) -> str:
        return self._theme_id

    @property
    def sizes(self) -> dict[str, int]:
        return {key: round(scale.get_value()) for key, scale in self.spins.items()}

    @property
    def values(self) -> dict:
        """Every settings key as currently shown (a copy)."""
        return {'theme': self._theme_id, **self.sizes, **self._values, 'graph': copy.deepcopy(self._graph)}

    def set_theme_id(self, theme_id):
        """Select without emission; unknown IDs fall back to the default theme."""
        previous, self._updating = self._updating, True
        try:
            self.theme_combo.set_active_id(get_theme(theme_id).id)
        finally:
            self._updating = previous

    def set_sizes(self, sizes: dict):
        """Update supplied known keys silently; invalid values use defaults."""
        previous, self._updating = self._updating, True
        try:
            for key, scale in self.spins.items():
                if key in sizes:
                    scale.set_value(settings._font_size(key, sizes[key]))
                self._update_reset(key)
        finally:
            self._updating = previous

    def set_values(self, values: dict):
        """Show any known keys silently (validated); unknown keys are ignored."""
        if 'theme' in values:
            self.set_theme_id(values['theme'] if isinstance(values['theme'], str) else None)
        sizes = {key: values[key] for key in settings.FONT_KEYS if key in values}
        if sizes:
            self.set_sizes(sizes)
        previous, self._updating = self._updating, True
        try:
            for key in settings.NEW_KEYS:
                if key in values:
                    value = settings.normalize(key, values[key])
                    self._values[key] = value
                    self._setters[key](value)
                    self._update_reset(key)
            if 'graph' in values:
                self._graph = settings.normalize_graph(values['graph'])
                for key in settings.GRAPH_DEFAULTS:
                    row_key = 'graph.' + key
                    if row_key in self._setters:
                        self._setters[row_key](self._graph[key])
                        self.reset_buttons[row_key].set_sensitive(
                            self._graph[key] != settings.GRAPH_DEFAULTS[key])
            self._sync_features()
        finally:
            self._updating = previous

    def set_hotkeys(self, hotkeys):
        """Replace the Hotkeys page list with (command label, accelerator text) pairs."""
        for row in self.hotkey_card.rows:
            self.hotkey_card.remove(row)
        self.hotkey_rows = []
        for label, accel in hotkeys:
            chips = Gtk.Box(spacing=4)
            for part in (p.strip() for p in str(accel).split(' / ')):
                if part:
                    chips.pack_start(HotkeyChip(part), False, False, 0)
            row = SettingRow(str(label), control=chips, aliases=(str(accel), 'hotkey shortcut'))
            row.page_id = 'hotkeys'
            row.set_narrow(self._narrow)
            row.show_all()
            self.hotkey_card.add(row)
            self.hotkey_rows.append(row)
        self._filter_hotkeys(self.hotkey_filter.get_text())
        if self._searching:
            self.search(self.search_entry.get_text())

    def search(self, query: str):
        """Filter rows across pages (name, description, aliases), grouped by page."""
        query = query.strip()
        if query and not self._searching:
            self._searching = True
            self.hotkey_box.hide()
            self.hotkeys_empty.hide()
            for page_id, body in self._bodies.items():
                self._columns[page_id].remove(body)
                self._sections[page_id].pack_start(body, False, False, 0)
            self.stack.set_visible_child(self.results_scroll)
        elif not query and self._searching:
            self._searching = False
            for page_id, body in self._bodies.items():
                self._sections[page_id].remove(body)
                self._columns[page_id].add(body)
            self.hotkey_box.show()
            self.stack.set_visible_child_name(self.nav_list.page_id or 'appearance')
        counts = {}
        for page_id, groups in self._groups.items():
            total = 0
            for group, card in groups:
                card.set_query(query)
                visible = len(card.visible_rows())
                group.set_visible(not query or visible > 0)
                total += visible
            counts[page_id] = total
            self._sections[page_id].set_visible(total > 0)
        if not query:
            self._filter_hotkeys(self.hotkey_filter.get_text())
        self._nav_counts = counts if query else None
        for page_id, row in self.nav_list.rows.items():
            row.set_count(counts[page_id] if query else None)
        self.nav_list.invalidate_filter()
        hits = sum(counts.values())
        self.empty_label.set_text(f'No settings match “{query}”.')
        self.empty_label.set_visible(bool(query) and hits == 0)
        return hits if query else None

    def response(self, _response_id=None):
        """Compatibility with the former Gtk.Dialog API: any response hides."""
        self.hide()

    # Handlers ---------------------------------------------------------------

    def _graph_changed(self, key, value):
        if self._updating:
            return
        # Read current persisted preferences so edits retain graph-panel changes.
        pending = {k: v for k, v in self._graph.items() if v != self._saved_graph.get(k)}
        graph = settings.normalize_graph({**settings.get_graph(), **pending, key: value})
        if graph == self._graph:
            return
        self.set_values({'graph': graph})
        settings.set_graph(graph)
        self._saved_graph = copy.deepcopy(graph)
        self.emit('setting-changed', 'graph', copy.deepcopy(graph))

    def _changed(self, key, value):
        if self._updating:
            return
        value = settings.normalize(key, value)
        if key in settings.FONT_KEYS:
            self.spins[key].set_value(value)
            return
        if value != self._values.get(key):
            self._values[key] = value
            previous, self._updating = self._updating, True
            try:
                self._setters[key](value)
            finally:
                self._updating = previous
            self._update_reset(key)
            self._sync_features()
            settings.set_value(key, value)
            self.emit('setting-changed', key, value)

    def _update_reset(self, key):
        button = self.reset_buttons.get(key)
        if button is not None:
            current = round(self.spins[key].get_value()) if key in self.spins else self._values.get(key)
            button.set_sensitive(current != settings.DEFAULTS[key])

    def _theme_changed(self, combo):
        theme_id = combo.get_active_id()
        if theme_id is None:
            return
        theme = get_theme(theme_id)
        colors = [theme.ui['bg'], theme.ui['editor_fg']]
        colors += [theme.syntax[k].foreground for k in ('keyword', 'string', 'function', 'comment')]
        for provider, color in zip(self._preview_providers, colors):
            provider.load_from_data(f'* {{ background: {color}; }}'.encode())
        self.theme_preview.set_tooltip_text(f"{theme.name} ({'dark' if theme.dark else 'light'})")
        changed = theme_id != self._theme_id
        self._theme_id = theme_id
        if changed and not self._updating:
            self.emit('theme-selected', theme_id)

    def _size_changed(self, scale, key):
        self._update_reset(key)
        if not self._updating:
            self.emit('font-size-changed', key, round(scale.get_value()))

    def _filter_hotkeys(self, text):
        if self._searching:
            return
        self.hotkey_card.set_query(text)
        self.hotkeys_empty.set_visible(bool(text.strip()) and not self.hotkey_card.visible_rows())

    def _copy_path(self, button):
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(str(settings.settings_path()), -1)
        button.set_label('Copied')
        GLib.timeout_add(1500, lambda: (button.set_label('Copy path'), False)[1])

    def _nav_filter(self, row):
        return self._nav_counts is None or self._nav_counts.get(row.page_id, 0) > 0

    def _nav_selected(self, _, row):
        if row is None:
            return
        if self._searching:
            section = self._sections[row.page_id]
            coords = section.translate_coordinates(self.results, 0, 0)
            if coords is not None:
                self.results_scroll.get_vadjustment().set_value(coords[1])
        else:
            self.stack.set_visible_child_name(row.page_id)
            self.pages[row.page_id].get_vadjustment().set_value(0)

    def _visible_rows(self):
        """Focusable rows of the current view, in order."""
        if self._searching:
            page_ids = [p for p, _, _ in PAGES if self._sections[p].get_visible()]
        else:
            page_ids = [self.nav_list.page_id or 'appearance']
        rows = []
        for page_id in page_ids:
            for group, card in self._groups[page_id]:
                if group.get_visible():
                    rows += [r for r in card.visible_rows() if r.is_sensitive()]
        return rows

    def focus_content(self) -> bool:
        rows = self._visible_rows()
        if rows:
            rows[0].grab_focus()
        return bool(rows)

    def _nav_key(self, _, event):
        key, state = event.keyval, event.state & Gtk.accelerator_get_default_mod_mask()
        if key == Gdk.KEY_ISO_Left_Tab or (key == Gdk.KEY_Tab and state & Gdk.ModifierType.SHIFT_MASK):
            self.search_entry.grab_focus()
            return True
        if state & ~Gdk.ModifierType.SHIFT_MASK:
            return False
        if key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_Right, Gdk.KEY_l, Gdk.KEY_Tab):
            return self.focus_content() or key != Gdk.KEY_Tab
        return False

    def _row_key(self, _, event):
        row = self.get_focus()
        if not isinstance(row, SettingRow):
            return False
        key = event.keyval
        if event.state & Gtk.accelerator_get_default_mod_mask():
            return False
        if key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            row.activate_control()
            return True
        if key in (Gdk.KEY_Left, Gdk.KEY_h):
            selected = self.nav_list.get_selected_row()
            if selected is not None:
                selected.grab_focus()
            return True
        if key in (Gdk.KEY_Down, Gdk.KEY_j, Gdk.KEY_Up, Gdk.KEY_k):
            rows = self._visible_rows()
            if row in rows:
                step = 1 if key in (Gdk.KEY_Down, Gdk.KEY_j) else -1
                rows[max(0, min(len(rows) - 1, rows.index(row) + step))].grab_focus()
            return True
        return False

    def _search_key(self, entry, event):
        if event.keyval in (Gdk.KEY_Down, Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if entry is self.search_entry and not self._searching:
                selected = self.nav_list.get_selected_row()
                if selected is not None and event.keyval == Gdk.KEY_Down:
                    selected.grab_focus()
                    return True
                return False
            return self.focus_content()
        return False

    def _delete(self, *_):
        self.hide()
        return True

    def _key(self, _, event):
        key = event.keyval
        state = event.state & Gtk.accelerator_get_default_mod_mask()
        if key == Gdk.KEY_Escape:
            if self.search_entry.get_text():
                self.search_entry.set_text('')
                self.search_entry.grab_focus()
            elif self.hotkey_filter.get_text() and not self._searching:
                self.hotkey_filter.set_text('')
                self.hotkey_filter.grab_focus()
            else:
                self.hide()
            return True
        if state == Gdk.ModifierType.CONTROL_MASK and key in (Gdk.KEY_f, Gdk.KEY_F):
            self.search_entry.grab_focus()
            return True
        if key == Gdk.KEY_slash and not state and not isinstance(self.get_focus(), Gtk.Editable):
            self.search_entry.grab_focus()
            return True
        return False
