"""Caret-anchored note commands, driven entirely from the text view."""
import re

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, GLib, Gtk

from . import motion, notes


def match_score(command, query):
    """Prefer exact aliases, prefixes, word starts, then compact subsequences."""
    query = query.casefold()
    if not query:
        return 0
    scores = []
    for value in (command.label, *command.keywords):
        value = value.casefold()
        if value == query:
            scores.append(0)
        elif value.startswith(query):
            scores.append(10 + len(value) - len(query))
        elif any(word.startswith(query) for word in re.findall(r'\w+', value)):
            scores.append(100 + len(value))
        else:
            at, positions = 0, []
            for char in query:
                at = value.find(char, at)
                if at < 0:
                    break
                positions.append(at)
                at += 1
            else:
                scores.append(200 + positions[-1] - positions[0] + len(value))
    return min(scores) if scores else None


def in_code(document, offset):
    """Check fenced and inline code, including an unfinished inline delimiter."""
    fence = None
    for line in document[:offset].split('\n')[:-1]:
        match = re.match(r'^\s*(?:>\s*)*(`{3,}|~{3,})(.*)$', line)
        if not match:
            continue
        token, rest = match.groups()
        if fence is None:
            fence = token
        elif token[0] == fence[0] and len(token) >= len(fence) and not rest.strip():
            fence = None
    if fence:
        return True
    line = document[document.rfind('\n', 0, offset) + 1:offset]
    if re.match(r'^\s*(?:>\s*)*(`{3,}|~{3,})', line):
        return True
    delimiter = None
    for match in re.finditer(r'(?<!\\)(`+)', line):
        token = match.group()
        if delimiter is None:
            delimiter = token
        elif token == delimiter:
            delimiter = None
    return delimiter is not None


class SlashMenu:
    def __init__(self, view):
        self.view, self.buffer = view, view.get_buffer()
        self.start = self.end = None
        self.query = ''
        self.matches, self.rows = [], []
        self.index = 0
        self.composing = self._grouped = self._destroyed = False
        self._idle = self._ime_idle = self._scroll_idle = 0
        self._ime_commit = False
        self._blocked_insert = False
        self._top_handler = None
        self._handlers = []
        self.popover = Gtk.Popover.new(view)
        self.popover.set_modal(False)
        self.popover.set_can_focus(False)
        self.popover.set_position(Gtk.PositionType.BOTTOM)
        self.popover.get_style_context().add_class('idgent-slash-menu')
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.popover.add(box)
        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_propagate_natural_height(True)
        self.scroll.set_max_content_height(8 * 34)
        self.scroll.set_size_request(340, -1)
        self.scroll.set_can_focus(False)
        self.scroll.get_vscrollbar().set_can_focus(False)
        self.scroll.get_hscrollbar().set_can_focus(False)
        box.add(self.scroll)
        self.list = Gtk.ListBox()
        self.list.set_can_focus(False)
        self.list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.list.set_activate_on_single_click(True)
        self.scroll.add(self.list)
        self.list.connect('row-activated', self._activate_row)
        footer = Gtk.EventBox()
        footer.get_style_context().add_class('slash-footer')
        footer.add(self._labels('Close menu', 'esc'))
        footer.connect('button-press-event', lambda *_: self.close() or True)
        box.pack_end(footer, False, False, 0)
        self._connect(self.buffer, 'insert-text', self._insert)
        self._connect(self.buffer, 'changed', self._queue)
        self._connect(self.buffer, 'mark-set', self._mark_set)
        self._connect(view, 'preedit-changed', self._preedit)
        self._connect(view, 'key-press-event', self.handle_key)
        self._connect(view, 'focus-out-event', self._dismiss)
        self._connect(view, 'button-press-event', self._dismiss)
        self._connect(view, 'scroll-event', self._dismiss)
        self._connect(view, 'destroy', lambda *_: self.destroy())
        self._connect(view, 'hierarchy-changed', self._hierarchy_changed)
        self._hierarchy_changed()
        parent = view.get_parent()
        if isinstance(parent, Gtk.ScrolledWindow):
            for adjustment in (parent.get_vadjustment(), parent.get_hadjustment()):
                self._connect(adjustment, 'value-changed', self._scrolled)

    def _hierarchy_changed(self, *_):
        if self._top_handler:
            obj, signal = self._top_handler
            obj.disconnect(signal)
            self._handlers.remove(self._top_handler)
            self._top_handler = None
        top = self.view.get_toplevel()
        if isinstance(top, Gtk.Window):
            self._connect(top, 'button-press-event', self._outside_click)
            self._top_handler = self._handlers[-1]

    @property
    def opened(self):
        return self.start is not None

    def _connect(self, obj, signal, callback):
        self._handlers.append((obj, obj.connect(signal, callback)))

    def _text(self):
        return self.buffer.get_text(self.buffer.get_start_iter(), self.buffer.get_end_iter(), True)

    def _preedit(self, _view, text):
        if self._ime_idle:
            GLib.source_remove(self._ime_idle)
            self._ime_idle = 0
        self.composing = bool(text)
        if self.composing:
            self._ime_commit = True
            self.close()
        else:
            # GTK clears preedit before inserting the committed text.
            def finished():
                self._ime_idle = 0
                self._ime_commit = False
                return False
            self._ime_idle = GLib.idle_add(finished)

    def _mark_set(self, _buffer, _iterator, mark):
        if mark.get_name() in ('insert', 'selection_bound'):
            self._queue()

    def _scrolled(self, *_):
        if self.opened:
            # GTK can animate a caret scroll through intermediate offscreen positions.
            if self._scroll_idle:
                GLib.source_remove(self._scroll_idle)
            self._scroll_idle = GLib.timeout_add(250, self._check_visible)
            self._queue()

    def _check_visible(self):
        self._scroll_idle = 0
        if self.opened:
            rect = self.view.get_iter_location(self.buffer.get_iter_at_mark(self.buffer.get_insert()))
            visible = self.view.get_visible_rect()
            if (rect.y + rect.height < visible.y or rect.y > visible.y + visible.height or
                    rect.x < visible.x or rect.x > visible.x + visible.width):
                self.close()
        return False

    def _insert(self, buffer, location, text, _length):
        blocked, self._blocked_insert = self._blocked_insert, False
        if blocked or self.opened or self.composing or self._ime_commit or buffer.get_has_selection() or text != '/':
            return
        offset = location.get_offset()
        document = self._text()
        if (offset and not document[offset - 1].isspace()) or in_code(document, offset):
            return
        self.start = buffer.create_mark(None, location, True)
        self.end = buffer.create_mark(None, location, False)
        # Keep the typed query together; executing the command starts a new action.
        buffer.begin_user_action()
        self._grouped = True
        self._queue()

    def _queue(self, *_):
        if self.opened and not self._idle:
            self._idle = GLib.idle_add(self._refresh)

    def _refresh(self):
        self._idle = 0
        if not self.opened:
            return False
        start = self.buffer.get_iter_at_mark(self.start).get_offset()
        end = self.buffer.get_iter_at_mark(self.end).get_offset()
        cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert()).get_offset()
        document = self._text()
        if (self.buffer.get_has_selection() or not start < cursor <= end or
                document[start:start + 1] != '/' or '\n' in document[start:cursor]):
            self.close()
            return False
        query = document[start + 1:cursor]
        if query.endswith(' ') and not self.matches and self.query:
            self.close()
            return False
        changed = query != self.query or not self.popover.get_visible()
        self.query = query
        ranked = [(score, i, command) for i, command in enumerate(notes.SLASH_COMMANDS)
                  if (score := match_score(command, query)) is not None]
        self.matches = [item[2] for item in sorted(ranked, key=lambda item: item[:2])]
        if changed:
            self.index = 0
            self._build_rows()
        rect = self.view.get_iter_location(self.buffer.get_iter_at_mark(self.buffer.get_insert()))
        rect.x, rect.y = self.view.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, rect.x, rect.y)
        rect.width = max(1, rect.width)
        self.popover.set_pointing_to(rect)
        was_visible = self.popover.get_visible()
        self.popover.show_all()
        if not was_visible:
            self.popover.set_opacity(0)
            motion.fade(self.popover, True, duration=100)
        return False

    def _labels(self, text, hint='', secondary=''):
        box = Gtk.Box(spacing=10)
        box.pack_start(Gtk.Label(label=text, xalign=0), False, False, 0)
        if secondary:
            box.pack_start(self._muted(secondary), False, False, 0)
        box.pack_end(self._muted(hint), False, False, 0)
        return box

    def _muted(self, text):
        label = Gtk.Label(label=text, xalign=0)
        label.get_style_context().add_class('slash-muted')
        return label

    def _heading(self, text):
        row = Gtk.ListBoxRow(selectable=False, activatable=False, can_focus=False)
        label = self._muted(text)
        label.get_style_context().add_class('slash-heading')
        row.add(label)
        self.list.add(row)

    def _build_rows(self):
        for child in self.list.get_children():
            child.destroy()
        self.rows = []
        group = None
        if self.query:
            self._heading('Filtered results')
        for index, command in enumerate(self.matches):
            if not self.query and group != command.group:
                group = command.group
                self._heading(group)
            row = Gtk.ListBoxRow(can_focus=False)
            box = self._labels(command.label, command.hint, command.secondary)
            theme = Gtk.IconTheme.get_default()
            icon = next((name for name in command.icons if theme and theme.has_icon(name)), None)
            widget = Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.MENU) if icon else Gtk.Label(label=command.glyph)
            widget.set_size_request(24, -1)
            box.pack_start(widget, False, False, 0)
            box.reorder_child(widget, 0)
            row.add(box)
            row.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK)
            row.connect('enter-notify-event', lambda _, event, i=index: self._select(i) or False)
            self.rows.append(row)
            self.list.add(row)
        if not self.matches:
            self._heading('No results')
        self._select(0)

    def _select(self, index):
        if not self.rows:
            return
        self.index = index % len(self.rows)
        row = self.rows[self.index]
        self.list.select_row(row)
        allocation = row.get_allocation()
        self.scroll.get_vadjustment().clamp_page(allocation.y, allocation.y + allocation.height)

    def _activate_row(self, _list, row):
        if row in self.rows:
            self.execute(self.matches[self.rows.index(row)])

    def execute(self, command=None):
        if not self.opened or not self.matches:
            return
        command = command or self.matches[self.index]
        first = self.buffer.get_iter_at_mark(self.start).get_offset()
        cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert()).get_offset()
        document = self._text()
        cleaned = document[:first] + document[cursor:]
        edit = command.edit(cleaned, first, first)
        self.close()
        self.buffer.begin_user_action()
        try:
            self.buffer.delete(self.buffer.get_iter_at_offset(first), self.buffer.get_iter_at_offset(cursor))
            self.buffer.delete(self.buffer.get_iter_at_offset(edit.start), self.buffer.get_iter_at_offset(edit.end))
            self.buffer.insert(self.buffer.get_iter_at_offset(edit.start), edit.text)
            self.buffer.select_range(self.buffer.get_iter_at_offset(edit.select_end),
                                     self.buffer.get_iter_at_offset(edit.select_start))
        finally:
            self.buffer.end_user_action()
        self.view.scroll_mark_onscreen(self.buffer.get_insert())

    def handle_key(self, _view, event):
        if not self.opened or self.composing:
            if event.keyval == Gdk.KEY_slash and self.buffer.get_has_selection():
                self._blocked_insert = True
            return False
        if self._idle:
            GLib.source_remove(self._idle)
            self._idle = 0
            self._refresh()
        if not self.opened:
            return False
        key = event.keyval
        if event.state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK | Gdk.ModifierType.SUPER_MASK):
            self.close()
            return False
        if key in (Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Home, Gdk.KEY_End):
            self._select({Gdk.KEY_Up: self.index - 1, Gdk.KEY_Down: self.index + 1,
                          Gdk.KEY_Home: 0, Gdk.KEY_End: len(self.rows) - 1}[key])
        elif key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab):
            self.execute()
        elif key == Gdk.KEY_Escape:
            self.close()
        elif key in (Gdk.KEY_Left, Gdk.KEY_Right):
            cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert())
            (cursor.backward_char if key == Gdk.KEY_Left else cursor.forward_char)()
            self.buffer.place_cursor(cursor)
            self._refresh_now()
        elif key == Gdk.KEY_BackSpace:
            cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert())
            before = cursor.copy()
            before.backward_char()
            self.buffer.begin_user_action()
            self.buffer.delete(before, cursor)
            self.buffer.end_user_action()
            self._refresh_now()
        else:
            return False
        return True

    def _refresh_now(self):
        if self._idle:
            GLib.source_remove(self._idle)
            self._idle = 0
        self._refresh()

    def _dismiss(self, *_):
        self.close()
        return False

    def _outside_click(self, _window, event):
        widget = Gtk.get_event_widget(event)
        if widget and (widget == self.popover or widget.is_ancestor(self.popover)):
            return False
        return self._dismiss()

    def close(self):
        if self._scroll_idle:
            GLib.source_remove(self._scroll_idle)
            self._scroll_idle = 0
        if self._idle:
            GLib.source_remove(self._idle)
            self._idle = 0
        if self.opened:
            self.buffer.delete_mark(self.start)
            self.buffer.delete_mark(self.end)
            self.start = self.end = None
        if self._grouped:
            self._grouped = False
            self.buffer.end_user_action()
        self.popover.hide()

    def destroy(self):
        if self._destroyed:
            return
        self._destroyed = True
        if self._ime_idle:
            GLib.source_remove(self._ime_idle)
            self._ime_idle = 0
        self.close()
        for obj, signal in self._handlers:
            obj.disconnect(signal)
        self._handlers.clear()
        self.popover.destroy()
