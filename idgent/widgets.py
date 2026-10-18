"""Reusable Reference app-style controls for settings-like screens (GTK3, no cairo).

``build_css(ui)`` styles every widget in this module from hex theme roles.
``settings_dialog.build_css`` composes it, so ``style.build_css`` (which already
composes the settings CSS) installs it application-wide; do not add it twice.

* ``Toggle``: 40x22 pill switch (thumb 18, inset 2, travel 20) built on
  Gtk.ToggleButton, so ``active``/``toggled``, focus and Space/Enter come from
  GTK. The thumb slides in 150 ms via ``motion.animate`` (instant when motion
  is reduced or the toggle is not mapped). Accessible role: switch.
* ``SettingRow``: name, optional description, end-aligned control, optional
  reset extra button; ``matches(query)`` searches name/description/aliases.
* ``SettingCard``: rounded ListBox of rows with inset dividers and filtering.
* ``NavList`` / ``NavRow``: vertical tab header rows (16 px icon + label).
* ``SearchField``: 30 px search entry; listen to ``changed`` (no debounce).
* ``IconButton``, ``HotkeyChip``, ``SwatchPicker``, ``CenteredColumn``.
"""
from __future__ import annotations

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('Atk', '1.0')
from gi.repository import Atk, Gdk, GObject, Gtk, Pango

from idgent import motion
from idgent.settings import ACCENT_PRESETS

TOGGLE_SIZE = (40, 22)
THUMB_SIZE = 18
THUMB_INSET = 2
THUMB_TRAVEL = TOGGLE_SIZE[0] - THUMB_SIZE - 2 * THUMB_INSET
TOGGLE_DURATION = 150
SWATCH_SIZE = 22


def _swatch_class(color: str) -> str:
    return 'idgent-swatch-' + color.lstrip('#').lower()


def build_css(ui: dict[str, str]) -> str:
    hover = f"alpha({ui['overlay']}, 0.067)"
    swatches = ''.join(f'button.idgent-swatch.{_swatch_class(c)} {{ background: {c}; }}\n'
                       for c in ACCENT_PRESETS)
    return f'''
button.idgent-toggle, button.idgent-toggle:hover, button.idgent-toggle:active {{
    min-width: {TOGGLE_SIZE[0]}px; min-height: {TOGGLE_SIZE[1]}px; padding: 0; border: 0;
    border-radius: {TOGGLE_SIZE[1] // 2}px; background: {ui['border_hover']};
    box-shadow: inset 0 4px 10px alpha(#000000, 0.07); }}
button.idgent-toggle:checked, button.idgent-toggle:checked:hover,
button.idgent-toggle:checked:active {{ background: {ui['accent']}; }}
button.idgent-toggle:disabled {{ opacity: 0.5; }}
button.idgent-toggle:focus {{ outline-width: 2px; outline-offset: 2px;
    -gtk-outline-radius: {TOGGLE_SIZE[1] // 2 + 2}px; }}
.idgent-toggle-thumb {{ min-width: {THUMB_SIZE}px; min-height: {THUMB_SIZE}px;
    border-radius: {THUMB_SIZE // 2}px; background: #ffffff;
    box-shadow: 0 1px 2px alpha(#000000, 0.15); }}
list.idgent-card {{ background: {ui['surface_alt']}; color: {ui['foreground']};
    border-radius: 12px; border: 0; }}
list.idgent-card > row, list.idgent-card > row:hover, list.idgent-card > row:active {{
    background: transparent; padding: 20px; border-radius: 8px; }}
list.idgent-card > row:focus {{ outline-color: {ui['border_focus']}; outline-style: solid;
    outline-width: 2px; outline-offset: -2px; -gtk-outline-radius: 8px; }}
list.idgent-card separator.idgent-card-divider {{ background: {ui['border']};
    min-height: 1px; margin: 0 20px; }}
.idgent-setting-name {{ font-size: 13px; color: {ui['foreground']}; }}
.idgent-setting-desc {{ font-size: 12px; color: {ui['muted']}; }}
.idgent-setting-value {{ font-size: 13px; color: {ui['muted']}; }}
list.idgent-nav {{ background: transparent; color: {ui['foreground']}; }}
list.idgent-nav > row {{ padding: 4px 8px; margin-bottom: 2px; border-radius: 8px;
    font-size: 13px; background: transparent; color: {ui['foreground']}; min-height: 0; }}
list.idgent-nav > row:hover {{ background: transparent; }}
list.idgent-nav > row:selected, list.idgent-nav > row:selected:hover {{
    background: {hover}; color: {ui['foreground']}; }}
list.idgent-nav > row:focus {{ outline-color: {ui['border_focus']}; outline-width: 2px;
    outline-offset: -2px; -gtk-outline-radius: 8px; }}
list.idgent-nav > row image {{ color: {ui['muted']}; }}
list.idgent-nav .idgent-nav-count {{ color: {ui['muted']}; font-size: 12px; }}
entry.idgent-search-field {{ border-radius: 5px; font-size: 13px; }}
entry.idgent-search-field image {{ color: {ui['faint']}; }}
button.idgent-icon-button {{ background: transparent; box-shadow: none; color: {ui['muted']};
    padding: 4px; min-width: 0; min-height: 0; border-radius: 4px; }}
button.idgent-icon-button:hover {{ background: {hover}; color: {ui['foreground']}; }}
button.idgent-icon-button:disabled {{ background: transparent; color: {ui['faint']}; opacity: 0.5; }}
.idgent-hotkey-chip {{ font-size: 12px; padding: 2px 6px; border-radius: 10px;
    background: {hover}; color: {ui['foreground']}; }}
button.idgent-swatch {{ min-width: {SWATCH_SIZE}px; min-height: {SWATCH_SIZE}px; padding: 0;
    border: 0; border-radius: {SWATCH_SIZE // 2}px; box-shadow: none; }}
button.idgent-swatch:checked {{ box-shadow: inset 0 0 0 2px {ui['surface_alt']},
    inset 0 0 0 4px alpha(#ffffff, 0.85); }}
button.idgent-swatch:focus {{ outline-width: 2px; outline-offset: 1px;
    -gtk-outline-radius: {SWATCH_SIZE // 2 + 1}px; }}
{swatches}'''


class Toggle(Gtk.ToggleButton):
    """Reference app's settings switch; set ``active`` or listen to ``toggled``."""

    def __init__(self, active: bool = False, label: str | None = None):
        super().__init__()
        self.get_style_context().add_class('idgent-toggle')
        self.set_size_request(*TOGGLE_SIZE)
        self.set_halign(Gtk.Align.END)
        self.set_valign(Gtk.Align.CENTER)
        self.set_focus_on_click(False)
        self.thumb = Gtk.Box()
        self.thumb.get_style_context().add_class('idgent-toggle-thumb')
        self.thumb.set_size_request(THUMB_SIZE, THUMB_SIZE)
        self.thumb.set_halign(Gtk.Align.START)
        self.thumb.set_valign(Gtk.Align.CENTER)
        self.add(self.thumb)
        accessible = self.get_accessible()
        accessible.set_role(Atk.Role.SWITCH)
        if label:
            accessible.set_name(label)
        self.set_active(active)
        self._place(False)
        self.connect('toggled', lambda _: self._place(True))

    @property
    def thumb_offset(self) -> int:
        """Thumb margin from the track's left edge (2 off, 22 on)."""
        return self.thumb.get_margin_start()

    def _place(self, animated: bool):
        target = THUMB_INSET + (THUMB_TRAVEL if self.get_active() else 0)
        start = self.thumb.get_margin_start()
        if not animated or not self.get_mapped():
            motion.animate(self.thumb, 0, lambda _: self.thumb.set_margin_start(target),
                           key='toggle-thumb')
            return
        motion.animate(self.thumb, TOGGLE_DURATION,
                       lambda p: self.thumb.set_margin_start(round(start + (target - start) * p)),
                       easing='ease_in_out', key='toggle-thumb')


class IconButton(Gtk.Button):
    """Small flat clickable icon (16 px), e.g. a row's reset extra button."""

    def __init__(self, icon_name: str, tooltip: str):
        super().__init__()
        self.get_style_context().add_class('idgent-icon-button')
        self.set_relief(Gtk.ReliefStyle.NONE)
        self.set_focus_on_click(False)
        self.set_valign(Gtk.Align.CENTER)
        image = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.MENU)
        image.set_pixel_size(16)
        self.add(image)
        self.set_tooltip_text(tooltip)
        self.get_accessible().set_name(tooltip)


class HotkeyChip(Gtk.Label):
    def __init__(self, text: str):
        super().__init__(label=text)
        self.get_style_context().add_class('idgent-hotkey-chip')
        self.set_valign(Gtk.Align.CENTER)


class SearchField(Gtk.SearchEntry):
    """30 px search input; use ``changed`` for per-keystroke filtering."""

    def __init__(self, placeholder: str = 'Search settings…'):
        super().__init__()
        self.get_style_context().add_class('idgent-search-field')
        self.set_placeholder_text(placeholder)
        self.get_accessible().set_name(placeholder.rstrip('…'))


def _label(text: str, css: str, **props) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0, **props)
    label.get_style_context().add_class(css)
    return label


class SettingRow(Gtk.ListBoxRow):
    """One setting: info column (name, description) and an end-aligned control."""

    def __init__(self, name: str, description: str = '', control: Gtk.Widget | None = None, *,
                 key: str | None = None, aliases=(), reset=None, extra=()):
        super().__init__()
        self.get_style_context().add_class('idgent-setting-row')
        self.key, self.name, self.description, self.aliases = key, name, description, tuple(aliases)
        self.control = control
        self._haystack = ' '.join((name, description, *self.aliases)).casefold()
        box = Gtk.Box(spacing=16)
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        info.set_valign(Gtk.Align.CENTER)
        self.name_label = _label(name, 'idgent-setting-name', wrap=True)
        self.name_label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        info.pack_start(self.name_label, False, False, 0)
        self.desc_label = None
        if description:
            self.desc_label = _label(description, 'idgent-setting-desc', wrap=True)
            self.desc_label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            self.desc_label.set_margin_top(4)
            info.pack_start(self.desc_label, False, False, 0)
        box.pack_start(info, True, True, 0)
        self.controls = Gtk.Box(spacing=8)
        self.controls.set_valign(Gtk.Align.CENTER)
        self.controls.set_halign(Gtk.Align.END)
        self.reset_button = None
        if reset is not None:
            self.reset_button = IconButton('edit-undo-symbolic', 'Restore default')
            self.reset_button.connect('clicked', lambda _: reset())
            self.controls.pack_start(self.reset_button, False, False, 0)
        for widget in extra:
            self.controls.pack_start(widget, False, False, 0)
        if control is not None:
            control.set_valign(Gtk.Align.CENTER)
            self.controls.pack_start(control, False, False, 0)
            accessible = control.get_accessible()
            if accessible is not None and not accessible.get_name():
                accessible.set_name(name)
        box.pack_end(self.controls, False, False, 0)
        self.add(box)

    def matches(self, query: str) -> bool:
        return all(word in self._haystack for word in query.casefold().split())

    def activate_control(self) -> bool:
        """Keyboard Enter on the row: flip a toggle, open a dropdown, else focus."""
        control = self.control
        if control is None or not control.get_sensitive():
            return False
        if isinstance(control, (Gtk.ToggleButton, Gtk.Button)) and not isinstance(control, Gtk.FontButton):
            control.clicked()
            return True
        if isinstance(control, Gtk.ComboBox):
            control.grab_focus()
            control.popup()
            return True
        return control.child_focus(Gtk.DirectionType.TAB_FORWARD) or bool(control.grab_focus())


class SettingCard(Gtk.ListBox):
    """Rounded card of SettingRows with 1 px dividers inset 20 px; filterable."""

    def __init__(self):
        super().__init__()
        self.get_style_context().add_class('idgent-card')
        self.set_selection_mode(Gtk.SelectionMode.NONE)
        self.set_activate_on_single_click(False)
        self._query = ''
        self.set_filter_func(self._filter)
        self.set_header_func(self._header)

    @property
    def rows(self) -> list[SettingRow]:
        return [row for row in self.get_children() if isinstance(row, SettingRow)]

    def visible_rows(self) -> list[SettingRow]:
        return [row for row in self.rows if row.get_visible() and self._filter(row)]

    def set_query(self, query: str):
        self._query = query.strip()
        self.invalidate_filter()
        self.invalidate_headers()

    def _filter(self, row) -> bool:
        return not self._query or not isinstance(row, SettingRow) or row.matches(self._query)

    @staticmethod
    def _header(row, before):
        if before is None:
            row.set_header(None)
        elif row.get_header() is None:
            divider = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            divider.get_style_context().add_class('idgent-card-divider')
            divider.show()
            row.set_header(divider)


class NavRow(Gtk.ListBoxRow):
    def __init__(self, page_id: str, label: str, icon_name: str):
        super().__init__()
        self.page_id = page_id
        box = Gtk.Box(spacing=8)
        image = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.MENU)
        image.set_pixel_size(16)
        box.pack_start(image, False, False, 0)
        self.label = Gtk.Label(label=label, xalign=0)
        self.label.set_ellipsize(Pango.EllipsizeMode.END)
        box.pack_start(self.label, True, True, 0)
        self.count_label = Gtk.Label(xalign=1)
        self.count_label.get_style_context().add_class('idgent-nav-count')
        self.count_label.set_no_show_all(True)
        box.pack_end(self.count_label, False, False, 0)
        self.add(box)
        self.get_accessible().set_name(label)

    def set_count(self, count: int | None):
        self.count_label.set_text('' if count is None else str(count))
        self.count_label.set_visible(count is not None)


class NavList(Gtk.ListBox):
    """Vertical tab header; Up/Down (j/k), Home/End (gg/G) move the selection."""

    def __init__(self):
        super().__init__()
        self.get_style_context().add_class('idgent-nav')
        self.set_selection_mode(Gtk.SelectionMode.BROWSE)
        self.rows: dict[str, NavRow] = {}
        self._pending_g = False
        self.connect('key-press-event', self._key)

    def add_page(self, page_id: str, label: str, icon_name: str) -> NavRow:
        row = NavRow(page_id, label, icon_name)
        self.rows[page_id] = row
        self.add(row)
        return row

    @property
    def page_id(self) -> str | None:
        row = self.get_selected_row()
        return None if row is None else row.page_id

    def select_page(self, page_id: str):
        row = self.rows[page_id]
        self.select_row(row)
        if self.get_focus_child() is not None:
            row.grab_focus()

    def _move(self, step: int | None, absolute: int | None = None):
        rows = [r for r in self.get_children() if r.get_visible() and r.get_child_visible()]
        if not rows:
            return
        current = self.get_selected_row()
        if absolute is not None:
            index = absolute % len(rows)
        else:
            index = rows.index(current) + step if current in rows else 0
            index = max(0, min(len(rows) - 1, index))
        self.select_row(rows[index])
        rows[index].grab_focus()

    def _key(self, _, event):
        if event.state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK):
            return False
        key = event.keyval
        pending, self._pending_g = self._pending_g, False
        if key == Gdk.KEY_j:
            self._move(1)
        elif key == Gdk.KEY_k:
            self._move(-1)
        elif key == Gdk.KEY_G:
            self._move(None, -1)
        elif key == Gdk.KEY_g:
            if pending:
                self._move(None, 0)
            else:
                self._pending_g = True
        else:
            return False
        return True


class SwatchPicker(Gtk.Box):
    """Round colour presets; ``value`` is a preset hex or None (no choice)."""
    __gsignals__ = {'value-changed': (GObject.SignalFlags.RUN_LAST, None, (object,))}

    def __init__(self, colors=ACCENT_PRESETS):
        super().__init__(spacing=6)
        self.set_valign(Gtk.Align.CENTER)
        self.buttons: dict[str, Gtk.ToggleButton] = {}
        self._value = None
        self._updating = False
        for color in colors:
            button = Gtk.ToggleButton()
            for name in ('idgent-swatch', _swatch_class(color)):
                button.get_style_context().add_class(name)
            button.set_size_request(SWATCH_SIZE, SWATCH_SIZE)
            button.set_valign(Gtk.Align.CENTER)
            button.set_focus_on_click(False)
            button.set_tooltip_text(color)
            button.get_accessible().set_name(f'Accent {color}')
            button.connect('toggled', self._toggled, color)
            self.pack_start(button, False, False, 0)
            self.buttons[color] = button

    @property
    def value(self) -> str | None:
        return self._value

    def set_value(self, value: str | None, emit: bool = False):
        value = value if value in self.buttons else None
        previous, self._updating = self._updating, True
        try:
            for color, button in self.buttons.items():
                button.set_active(color == value)
        finally:
            self._updating = previous
        changed = value != self._value
        self._value = value
        if emit and changed:
            self.emit('value-changed', value)

    def _toggled(self, button, color):
        if self._updating:
            return
        if button.get_active():
            self.set_value(color, emit=True)
        elif self._value == color:
            # Clicking the chosen swatch again keeps it chosen (reset is explicit).
            self.set_value(color)

    def do_grab_focus(self):
        target = self.buttons.get(self._value) or next(iter(self.buttons.values()), None)
        if target is not None:
            target.grab_focus()


class CenteredColumn(Gtk.Bin):
    """Centre one child in a column at most ``max_width`` wide, >= ``side`` inset."""

    def __init__(self, max_width: int = 700, side: int = 32, top: int = 48, bottom: int = 64):
        super().__init__()
        self.max_width, self.side, self.top, self.bottom = max_width, side, top, bottom

    def _inner(self, width: int) -> int:
        return max(1, min(self.max_width, width - 2 * self.side))

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_get_preferred_width(self):
        child = self.get_child()
        low, natural = child.get_preferred_width() if child and child.get_visible() else (0, 0)
        return low + 2 * self.side, max(natural, self.max_width) + 2 * self.side

    def do_get_preferred_height_for_width(self, width):
        child = self.get_child()
        if child is None or not child.get_visible():
            return self.top + self.bottom, self.top + self.bottom
        low, natural = child.get_preferred_height_for_width(self._inner(width))
        return low + self.top + self.bottom, natural + self.top + self.bottom

    def do_get_preferred_height(self):
        return self.do_get_preferred_height_for_width(self.max_width + 2 * self.side)

    def do_get_preferred_width_for_height(self, _height):
        return self.do_get_preferred_width()

    def do_size_allocate(self, allocation):
        self.set_allocation(allocation)
        child = self.get_child()
        if child is None or not child.get_visible():
            return
        inner = self._inner(allocation.width)
        rect = Gdk.Rectangle()
        rect.x = allocation.x + (allocation.width - inner) // 2
        rect.y = allocation.y + self.top
        rect.width = inner
        rect.height = max(1, allocation.height - self.top - self.bottom)
        child.size_allocate(rect)


class WrapBox(Gtk.Container):
    """Children at natural size, left to right, wrapping into lines (for chips).

    Gtk.FlowBox aligns children into columns in GTK3, which stretches chips.
    """

    def __init__(self, spacing: int = 6, line_spacing: int = 6):
        super().__init__()
        self.set_has_window(False)
        self.spacing, self.line_spacing = spacing, line_spacing
        self._children: list[Gtk.Widget] = []

    def do_add(self, child):
        self._children.append(child)
        child.set_parent(self)
        self.queue_resize()

    def do_remove(self, child):
        if child in self._children:
            self._children.remove(child)
            child.unparent()
            self.queue_resize()

    def do_forall(self, _include_internals, callback, *data):
        for child in list(self._children):
            callback(child, *data)

    def do_child_type(self):
        return Gtk.Widget.__gtype__

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def _visible(self):
        return [child for child in self._children if child.get_visible()]

    def _lines(self, width):
        lines, line, used = [], [], 0
        for child in self._visible():
            w = child.get_preferred_width()[1]
            h = child.get_preferred_height_for_width(w)[1]
            if line and used + self.spacing + w > width:
                lines.append(line)
                line, used = [], 0
            used += (self.spacing if line else 0) + w
            line.append((child, w, h))
        if line:
            lines.append(line)
        return lines

    def do_get_preferred_width(self):
        widths = [child.get_preferred_width() for child in self._visible()]
        low = max((w[0] for w in widths), default=0)
        natural = sum(w[1] for w in widths) + self.spacing * max(0, len(widths) - 1)
        return low, natural

    def do_get_preferred_height_for_width(self, width):
        lines = self._lines(width)
        height = sum(max(h for _, _, h in line) for line in lines)
        height += self.line_spacing * max(0, len(lines) - 1)
        return height, height

    def do_get_preferred_height(self):
        return self.do_get_preferred_height_for_width(self.do_get_preferred_width()[1])

    def do_get_preferred_width_for_height(self, _height):
        return self.do_get_preferred_width()

    def do_size_allocate(self, allocation):
        self.set_allocation(allocation)
        y = allocation.y
        for line in self._lines(allocation.width):
            x, height = allocation.x, max(h for _, _, h in line)
            for child, w, _ in line:
                rect = Gdk.Rectangle()
                rect.x, rect.y, rect.width, rect.height = x, y, min(w, allocation.width), height
                child.size_allocate(rect)
                x += w + self.spacing
            y += height + self.line_spacing
