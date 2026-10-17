"""Editor chrome for one Gtk.TextView: line-number gutter, current-line and bracket highlights.

The gutter lives in the view's left border window.  Python cannot paint on a cairo context
without the optional gi-cairo bridge, so instead of a ``draw`` handler it keeps a small pool of
Gtk.Label children (one per *visible* line) placed in that window; CSS colours the border.
"""
from __future__ import annotations

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, Gtk, Pango

DEFAULT_CHROME = {
    'gutter_bg': '#1e1e1e', 'gutter_fg': '#858585', 'gutter_fg_active': '#c6c6c6',
    'gutter_border': '#1e1e1e', 'current_line_bg': '#282828', 'bracket_bg': '#3a3d41',
}
MIN_DIGITS = 3
PADDING_LEFT, PADDING_RIGHT = 10, 12
BRACKET_RANGE = 20000
PAIRS = {'(': ')', '[': ']', '{': '}'}
CLOSERS = {close: open_ for open_, close in PAIRS.items()}


def digits_for(lines: int) -> int:
    return max(MIN_DIGITS, len(str(max(lines, 1))))


def find_match(text: str, index: int) -> int | None:
    """Index of the bracket matching text[index], by a plain same-type scan, or None."""
    char = text[index]
    if char in PAIRS:
        other, step, stop = PAIRS[char], 1, len(text)
    elif char in CLOSERS:
        other, step, stop = CLOSERS[char], -1, -1
    else:
        return None
    depth = 0
    for position in range(index, stop, step):
        current = text[position]
        if current == char:
            depth += 1
        elif current == other:
            depth -= 1
            if not depth:
                return position
    return None


def layout_for(view: Gtk.TextView, text: str) -> Pango.Layout:
    """A layout in the view's current CSS font (the widget's own Pango context updates lazily)."""
    layout = view.create_pango_layout(text)
    layout.set_font_description(view.get_style_context().get_property('font', Gtk.StateFlags.NORMAL))
    return layout


class ViewChrome:
    """Owns the gutter and the two highlight tags of a text view; colours are set via apply()."""

    def __init__(self, view: Gtk.TextView, chrome: dict[str, str] | None = None):
        self.view, self.buffer = view, view.get_buffer()
        self.colors = dict(DEFAULT_CHROME)
        self.digits = 0
        self.active_line = -1
        self.font_size = 11
        self.labels: list[Gtk.Label] = []
        self.label_state: list[tuple | None] = []
        self.provider = Gtk.CssProvider()
        view.get_style_context().add_provider(self.provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)
        self.line_tag = self.buffer.create_tag('idgent-current-line')
        self.bracket_tag = self.buffer.create_tag('idgent-bracket')
        self.line_tag.set_priority(0)
        self.bracket_tag.set_priority(self.buffer.get_tag_table().get_size() - 1)
        self.apply(chrome or {})
        self.buffer.connect('changed', self._changed)
        self.buffer.connect('mark-set', self._mark_set)
        view.connect('button-press-event', self._press)
        view.connect('size-allocate', self.update_labels)
        self.adjustment = None
        view.connect('notify::vadjustment', self._hook_adjustment)
        self._hook_adjustment()
        self.update_width()
        self.refresh_tags()

    def _hook_adjustment(self, *_: object) -> None:
        # The ScrolledWindow swaps in its own adjustment when the view is added to it.
        adjustment = Gtk.Scrollable.get_vadjustment(self.view)
        if adjustment is not self.adjustment:
            self.adjustment = adjustment
            adjustment.connect('value-changed', self.update_labels)
            adjustment.connect('changed', self.update_labels)

    # --- colours and size -------------------------------------------------
    def apply(self, colors: dict[str, str]) -> None:
        for key, value in colors.items():
            if key in DEFAULT_CHROME and valid_color(value):
                self.colors[key] = value
        self.line_tag.set_property('paragraph-background', self.colors['current_line_bg'])
        self.bracket_tag.set_property('background', self.colors['bracket_bg'])
        self._load_css()

    def set_font_size(self, size: int) -> None:
        self.font_size = size
        self._load_css()
        self.update_width()
        self.update_labels()

    def _load_css(self) -> None:
        colors, size = self.colors, self.font_size
        family = self.view.get_style_context().get_property('font', Gtk.StateFlags.NORMAL).get_family()
        self.provider.load_from_data(f'''
            textview, textview text {{ font-size: {size}pt; }}
            textview border.left {{ background-color: {colors['gutter_bg']};
                border-right: 1px solid {colors['gutter_border']}; }}
            label.idgent-gutter-num {{ font-family: "{family}"; font-size: {size}pt;
                color: {colors['gutter_fg']}; }}
            label.idgent-gutter-num.active {{ color: {colors['gutter_fg_active']}; }}
        '''.encode())

    def update_width(self) -> None:
        """Resize the left border window for the line count and the view's current font."""
        digits = digits_for(self.buffer.get_line_count())
        layout = layout_for(self.view, '0' * digits)
        width = layout.get_pixel_size()[0] + PADDING_LEFT + PADDING_RIGHT
        if width != self.view.get_border_window_size(Gtk.TextWindowType.LEFT):
            self.view.set_border_window_size(Gtk.TextWindowType.LEFT, width)
        self.digits = digits
        self.update_labels()

    @property
    def width(self) -> int:
        return self.view.get_border_window_size(Gtk.TextWindowType.LEFT)

    # --- buffer signals -----------------------------------------------------
    def _changed(self, *_: object) -> None:
        if digits_for(self.buffer.get_line_count()) != self.digits:
            self.update_width()
        self.refresh_tags()
        self.update_labels()

    def _mark_set(self, buffer: Gtk.TextBuffer, _location: Gtk.TextIter, mark: Gtk.TextMark) -> None:
        if mark in (buffer.get_insert(), buffer.get_selection_bound()):
            self.refresh_tags()
            if buffer.get_iter_at_mark(buffer.get_insert()).get_line() != self.active_line:
                self.update_labels()

    # --- tags ---------------------------------------------------------------------
    def refresh_tags(self) -> None:
        buffer = self.buffer
        start, end = buffer.get_bounds()
        buffer.remove_tag(self.line_tag, start, end)
        buffer.remove_tag(self.bracket_tag, start, end)
        cursor = buffer.get_iter_at_mark(buffer.get_insert())
        selection = buffer.get_selection_bounds()
        if not selection or selection[0].get_line() == selection[1].get_line():
            first = cursor.copy()
            first.set_line_offset(0)
            last = first.copy()
            if not last.forward_line():
                last = end
            buffer.apply_tag(self.line_tag, first, last)
        if not selection:
            self._mark_brackets(cursor)

    def _mark_brackets(self, cursor: Gtk.TextIter) -> None:
        buffer = self.buffer
        for offset in (0, -1):
            here = cursor.copy()
            if offset and not here.backward_char():
                continue
            char = here.get_char()
            if char not in PAIRS and char not in CLOSERS:
                continue
            if char in PAIRS:
                limit = here.copy()
                limit.forward_chars(BRACKET_RANGE)
                base, text = here.get_offset(), buffer.get_text(here, limit, True)
            else:
                limit = here.copy()
                limit.backward_chars(BRACKET_RANGE)
                after = here.copy()
                after.forward_char()
                base, text = limit.get_offset(), buffer.get_text(limit, after, True)
            index = find_match(text, 0 if char in PAIRS else len(text) - 1)
            if index is None:
                continue
            for position in (here.get_offset(), base + index):
                first = buffer.get_iter_at_offset(position)
                last = first.copy()
                last.forward_char()
                buffer.apply_tag(self.bracket_tag, first, last)
            return

    # --- gutter labels ---------------------------------------------------------------
    def _label(self, index: int) -> Gtk.Label:
        while len(self.labels) <= index:
            label = Gtk.Label(xalign=1.0)
            label.get_style_context().add_class('idgent-gutter-num')
            label.get_style_context().add_provider(self.provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)
            self.view.add_child_in_window(label, Gtk.TextWindowType.LEFT, PADDING_LEFT, 0)
            self.labels.append(label)
            self.label_state.append(None)
        return self.labels[index]

    def update_labels(self, *_: object) -> None:
        """Place one label per visible line; only those lines are ever laid out or painted."""
        view, buffer = self.view, self.buffer
        rect = view.get_visible_rect()
        self.active_line = buffer.get_iter_at_mark(buffer.get_insert()).get_line()
        iterator, _ = view.get_line_at_y(rect.y)
        text_width = max(self.width - PADDING_LEFT - PADDING_RIGHT, 1)
        used = 0
        while True:
            line = iterator.get_line()
            top, height = view.get_line_yrange(iterator)
            if top > rect.y + rect.height or height <= 0:
                break
            # Unvalidated layouts can report every line at y == 0; never outgrow the viewport.
            if used > rect.height // height + 2:
                break
            _, y = view.buffer_to_window_coords(Gtk.TextWindowType.LEFT, 0, top)
            label = self._label(used)
            state = (line, y, height, text_width, line == self.active_line)
            if self.label_state[used] != state:
                old = self.label_state[used]
                if old is None or old[0] != line:
                    label.set_text(str(line + 1))
                if old is None or old[2:4] != state[2:4]:
                    label.set_size_request(text_width, height)
                if old is None or old[4] != state[4]:
                    context = label.get_style_context()
                    (context.add_class if state[4] else context.remove_class)('active')
                if old is None or old[1] != y:
                    view.move_child(label, PADDING_LEFT, y)
                self.label_state[used] = state
            label.show()
            used += 1
            iterator.forward_line()
            if iterator.get_line() == line:
                break
        for index in range(used, len(self.labels)):
            if self.label_state[index] is not None:
                self.labels[index].hide()
                self.label_state[index] = None

    def _press(self, view: Gtk.TextView, event: Gdk.EventButton) -> bool:
        if event.window != view.get_window(Gtk.TextWindowType.LEFT) or event.button != 1:
            return False
        _, y = view.window_to_buffer_coords(Gtk.TextWindowType.LEFT, 0, int(event.y))
        start, _ = view.get_line_at_y(y)
        end = start.copy()
        if not end.forward_line():
            end = self.buffer.get_end_iter()
        self.buffer.select_range(end, start)
        view.grab_focus()
        return True


def valid_color(value: object) -> bool:
    return isinstance(value, str) and Gdk.RGBA().parse(value)
