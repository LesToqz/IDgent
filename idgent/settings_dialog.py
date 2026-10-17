"""Standalone live preferences UI; the owner handles persistence and application.

Close, Escape and the window close control hide the dialog so it can be reused.
Install build_css(theme.ui) alongside the application's shared CSS to theme it.
"""
from __future__ import annotations

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, GObject, Gtk, Pango

from idgent import settings
from idgent.themes import THEMES, get_theme


def build_css(ui: dict[str, str]) -> str:
    return f'''
.idgent-settings {{ background: {ui['bg']}; color: {ui['foreground']}; }}
.idgent-settings .idgent-settings-section {{ font-weight: bold; font-size: 14px; }}
.idgent-settings .idgent-settings-muted {{ color: {ui['muted']}; font-size: 12px; }}
.idgent-settings .idgent-settings-themes {{ background: {ui['sidebar_bg']}; color: {ui['foreground']}; }}
.idgent-settings .idgent-settings-theme {{ padding: 5px 10px; border: 0; }}
.idgent-settings .idgent-settings-theme:hover {{ background: {ui['hover']}; }}
.idgent-settings .idgent-settings-theme:selected {{ background: {ui['selection_active']}; color: {ui['selection_fg']}; }}
.idgent-settings .idgent-settings-theme:selected label {{ color: {ui['selection_fg']}; }}
.idgent-settings .idgent-settings-preview {{ background: {ui['sidebar_bg']}; padding: 5px 8px; border-radius: 3px; }}
.idgent-settings button, .idgent-settings spinbutton {{ background: {ui['input_bg']}; color: {ui['foreground']};
    border: 1px solid {ui['border']}; border-radius: 3px; box-shadow: none; text-shadow: none; }}
.idgent-settings button {{ padding: 3px 9px; min-height: 22px; }}
.idgent-settings button:hover {{ background: {ui['button_hover']}; }}
.idgent-settings button:disabled {{ color: {ui['muted']}; }}
.idgent-settings spinbutton entry {{ background: transparent; color: {ui['foreground']}; min-width: 30px; }}
.idgent-settings spinbutton button {{ padding: 0 5px; min-width: 12px; }}
.idgent-settings *:focus {{ outline-color: {ui['accent']}; outline-width: 1px; outline-style: solid; }}
'''


SETTINGS_CSS = build_css(get_theme(None).ui)


def _class(widget, name):
    widget.get_style_context().add_class('idgent-settings-' + name)
    return widget


class SettingsDialog(Gtk.Dialog):
    __gsignals__ = {
        'theme-selected': (GObject.SignalFlags.RUN_LAST, None, (str,)),
        'font-size-changed': (GObject.SignalFlags.RUN_LAST, None, (str, int)),
    }

    def __init__(self, parent=None, theme_id=None, sizes=None):
        super().__init__(title='Settings', transient_for=parent, destroy_with_parent=True)
        self.get_style_context().add_class('idgent-settings')
        self.set_default_size(560, 560)
        self.set_border_width(0)
        self._updating = True
        self._theme_id = None
        self.theme_rows = {}
        self.theme_buttons = {}
        self.spins = {}
        self.reset_buttons = {}
        self.samples = {}
        self.add_button('_Close', Gtk.ResponseType.CLOSE)
        self.connect('response', lambda *_: self.hide())
        self.connect('delete-event', self._delete)
        self.connect('key-press-event', self._key)

        content = self.get_content_area()
        content.set_spacing(8)
        content.set_border_width(20)
        content.pack_start(_class(Gtk.Label(label='Appearance', xalign=0), 'section'), False, False, 0)
        content.pack_start(Gtk.Label(label='Color theme', xalign=0), False, False, 0)
        self.theme_list = _class(Gtk.ListBox(), 'themes')
        self.theme_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        group = None
        for theme in THEMES:
            row = _class(Gtk.ListBoxRow(), 'theme')
            row.theme_id = theme.id
            box = Gtk.Box(spacing=8)
            radio = Gtk.RadioButton.new_from_widget(group)
            group = radio
            radio.set_can_focus(False)  # ListBox owns keyboard navigation.
            radio.set_tooltip_text(theme.name)
            box.pack_start(radio, False, False, 0)
            box.pack_start(Gtk.Label(label=theme.name, xalign=0), True, True, 0)
            strip = Gtk.Box()
            colors = [theme.ui['bg'], theme.ui['editor_fg']]
            colors += [theme.syntax[k].foreground for k in ('keyword', 'string', 'function', 'comment')]
            for color in colors:
                swatch = Gtk.Box()
                swatch.set_size_request(12, 14)
                swatch.set_valign(Gtk.Align.CENTER)
                provider = Gtk.CssProvider()
                provider.load_from_data(f'* {{ background: {color}; }}'.encode())
                swatch.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
                strip.pack_start(swatch, False, False, 0)
            box.pack_start(strip, False, False, 4)
            hint = _class(Gtk.Label(label='Dark' if theme.dark else 'Light', xalign=1), 'muted')
            hint.set_width_chars(5)
            box.pack_start(hint, False, False, 0)
            row.add(box)
            self.theme_list.add(row)
            self.theme_rows[theme.id] = row
            self.theme_buttons[theme.id] = radio
            radio.connect('toggled', self._radio_toggled, row)
        self.theme_list.connect('row-selected', self._theme_selected)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(180)
        scroll.set_shadow_type(Gtk.ShadowType.IN)
        scroll.add(self.theme_list)
        content.pack_start(scroll, True, True, 0)
        heading = _class(Gtk.Label(label='Text size', xalign=0), 'section')
        heading.set_margin_top(8)
        content.pack_start(heading, False, False, 0)
        for key, title in zip(settings.FONT_KEYS, ('Terminal', 'Editor (file content)', 'Explorer (file names)')):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            controls = Gtk.Box(spacing=6)
            label = Gtk.Label(label=title, xalign=0)
            controls.pack_start(label, True, True, 0)
            low, high = settings.FONT_SIZE_LIMITS[key]
            spin = Gtk.SpinButton.new_with_range(low, high, 1)
            spin.set_numeric(True)
            spin.set_digits(0)
            label.set_mnemonic_widget(spin)
            spin.get_accessible().set_name(title + ' text size')
            controls.pack_start(spin, False, False, 0)
            controls.pack_start(_class(Gtk.Label(label='pt'), 'muted'), False, False, 0)
            reset = Gtk.Button(label='Reset')
            reset.set_tooltip_text(f'Reset to {settings.DEFAULT_FONT_SIZES[key]} pt')
            controls.pack_start(reset, False, False, 0)
            box.pack_start(controls, False, False, 0)
            sample = _class(Gtk.Label(label='AaBbCc 0123 def f(x):', xalign=0), 'preview')
            sample.set_ellipsize(Pango.EllipsizeMode.END)
            box.pack_start(sample, False, False, 0)
            content.pack_start(box, False, False, 0)
            self.spins[key], self.reset_buttons[key], self.samples[key] = spin, reset, sample
            spin.connect('value-changed', self._size_changed, key)
            reset.connect('clicked', lambda _, k=key: self.spins[k].set_value(settings.DEFAULT_FONT_SIZES[k]))
        note = _class(Gtk.Label(label='Changes apply immediately and are saved.', xalign=0), 'muted')
        note.set_margin_top(4)
        content.pack_start(note, False, False, 0)
        self.set_theme_id(settings.get_theme_id() if theme_id is None else theme_id)
        self.set_sizes(settings.get_font_sizes() if sizes is None else {**settings.DEFAULT_FONT_SIZES, **sizes})
        self._updating = False
        content.show_all()

    @property
    def theme_id(self) -> str:
        return self._theme_id

    @property
    def sizes(self) -> dict[str, int]:
        return {key: spin.get_value_as_int() for key, spin in self.spins.items()}

    def set_theme_id(self, theme_id):
        """Select without emission; unknown IDs fall back to Dark+."""
        previous, self._updating = self._updating, True
        try:
            self.theme_list.select_row(self.theme_rows[get_theme(theme_id).id])
        finally:
            self._updating = previous

    def set_sizes(self, sizes: dict):
        """Update supplied known keys silently; invalid values use defaults."""
        previous, self._updating = self._updating, True
        try:
            for key, spin in self.spins.items():
                if key in sizes:
                    spin.set_value(settings._font_size(key, sizes[key]))
                self._update_sample(key)
        finally:
            self._updating = previous

    def _radio_toggled(self, radio, row):
        if radio.get_active():
            self.theme_list.select_row(row)

    def _theme_selected(self, _, row):
        if row is None:
            return
        changed = row.theme_id != self._theme_id
        self._theme_id = row.theme_id
        self.theme_buttons[row.theme_id].set_active(True)
        if changed and not self._updating:
            self.emit('theme-selected', self._theme_id)

    def _update_sample(self, key):
        size = self.spins[key].get_value_as_int()
        attrs = Pango.AttrList()
        attrs.insert(Pango.attr_size_new(size * Pango.SCALE))
        if key != 'explorer_font_size':
            attrs.insert(Pango.attr_family_new('monospace'))
        self.samples[key].set_attributes(attrs)
        self.reset_buttons[key].set_sensitive(size != settings.DEFAULT_FONT_SIZES[key])

    def _size_changed(self, spin, key):
        self._update_sample(key)
        if not self._updating:
            self.emit('font-size-changed', key, spin.get_value_as_int())

    def _delete(self, *_):
        self.hide()
        return True

    def _key(self, _, event):
        if event.keyval == Gdk.KEY_Escape:
            self.response(Gtk.ResponseType.CLOSE)
            return True
        return False
