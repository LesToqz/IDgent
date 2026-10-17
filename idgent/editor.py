"""Tabbed GTK text editor, with independently testable file and text helpers."""
from __future__ import annotations

import os
from pathlib import Path
import re
import stat
import tempfile

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, Gtk, Pango

MAX_FILE_SIZE = 5 * 1024 * 1024
COMMENT_EXTENSIONS = {'.py', '.sh', '.toml', '.yaml', '.yml', '.conf'}


def detect_line_ending(text: str) -> str:
    return 'CRLF' if '\r\n' in text else 'LF'


def read_file(path: str) -> tuple[str, str, int]:
    """Validate a regular UTF-8 file and return normalized text, EOL and mtime."""
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK), 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('Cannot open a non-regular file')
        if info.st_size > MAX_FILE_SIZE:
            raise ValueError('Cannot open files larger than 5 MB')
        data = stream.read(MAX_FILE_SIZE + 1)
    if len(data) > MAX_FILE_SIZE:
        raise ValueError('Cannot open files larger than 5 MB')
    if b'\0' in data:
        raise ValueError('cannot open binary or non-UTF-8 file')
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError as error:
        raise ValueError('cannot open binary or non-UTF-8 file') from error
    return text.replace('\r\n', '\n'), detect_line_ending(text), info.st_mtime_ns


def atomic_write(path: str, text: str, ending: str) -> int:
    """Replace a file atomically, preserving its permissions and exact ending."""
    target = os.path.realpath(path)
    try:
        mode = stat.S_IMODE(os.stat(target).st_mode)
    except FileNotFoundError:
        mode = 0o644
    fd, temporary = tempfile.mkstemp(prefix='.idgent-', dir=os.path.dirname(target))
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write((text.replace('\n', '\r\n') if ending == 'CRLF' else text).encode('utf-8'))
            os.fchmod(stream.fileno(), mode)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return os.stat(target).st_mtime_ns


def leading_whitespace(line: str) -> str:
    return re.match(r'[ \t]*', line).group(0)


def unindent(line: str) -> str:
    return line[1:] if line.startswith('\t') else line[min(4, len(line) - len(line.lstrip(' '))):]


def toggle_comments(text: str) -> str:
    lines = text.split('\n')
    populated = [line for line in lines if line.strip()]
    uncomment = bool(populated) and all(line.lstrip().startswith('#') for line in populated)
    result = []
    for line in lines:
        prefix = leading_whitespace(line)
        body = line[len(prefix):]
        if body:
            body = (body[2:] if body.startswith('# ') else body[1:]) if uncomment else '# ' + body
        result.append(prefix + body)
    return '\n'.join(result)


def _message(parent: Gtk.Widget, text: str) -> None:
    dialog = Gtk.MessageDialog(transient_for=_window(parent), modal=True,
                               message_type=Gtk.MessageType.ERROR,
                               buttons=Gtk.ButtonsType.CLOSE, text=text)
    dialog.run()
    dialog.destroy()


def _window(widget: Gtk.Widget) -> Gtk.Window | None:
    top = widget.get_toplevel()
    return top if isinstance(top, Gtk.Window) else None


def _ask(parent: Gtk.Widget, text: str, choices: tuple[str, ...]) -> str:
    dialog = Gtk.MessageDialog(transient_for=_window(parent), modal=True,
                               message_type=Gtk.MessageType.QUESTION,
                               buttons=Gtk.ButtonsType.NONE, text=text)
    for index, choice in enumerate(choices):
        dialog.add_button(choice.title(), index)
    response = dialog.run()
    dialog.destroy()
    return choices[response] if 0 <= response < len(choices) else 'cancel'


def _text(buffer: Gtk.TextBuffer) -> str:
    return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)


class _EditorTab:
    def __init__(self, panel: EditorPanel, path: str, text: str, ending: str, mtime: int):
        self.panel, self.path, self.ending, self.mtime = panel, path, ending, mtime
        self.deleted = False
        self.saved_text = text
        self.buffer = Gtk.TextBuffer()
        self.buffer.set_text(text)
        self.buffer.set_modified(False)
        self.view = Gtk.TextView(buffer=self.buffer)
        self.view.override_font(Pango.FontDescription('Monospace 11'))
        self.view.set_wrap_mode(Gtk.WrapMode.NONE)
        self.view.set_left_margin(12)
        self.view.set_right_margin(12)
        layout = self.view.create_pango_layout('    ')
        tabs = Pango.TabArray.new(1, True)
        tabs.set_tab(0, Pango.TabAlign.LEFT, layout.get_pixel_size()[0])
        self.view.set_tabs(tabs)
        self.page = Gtk.ScrolledWindow()
        self.page.add(self.view)
        self.label = Gtk.Label()
        self.header = Gtk.Box(spacing=6)
        self.header.pack_start(self.label, True, True, 0)
        close = Gtk.Button.new_with_label('×')
        close.set_relief(Gtk.ReliefStyle.NONE)
        close.set_tooltip_text('Close file')
        close.connect('clicked', lambda *_: panel._close_tab(self))
        self.header.pack_start(close, False, False, 0)
        self.history: list[tuple[str, int]] = [(text, 0)]
        self.history_index = 0
        self.action_depth = 0
        self.restoring = False
        self.buffer.connect('begin-user-action', self._begin)
        self.buffer.connect('end-user-action', self._end)
        self.buffer.connect('changed', self._changed)
        self.buffer.connect('modified-changed', lambda *_: self.update_title())
        self.buffer.connect('mark-set', lambda *_: panel._update_status())
        self.view.connect('key-press-event', panel._key_press, self)
        self.update_title()

    def update_title(self) -> None:
        self.label.set_text(('● ' if self.buffer.get_modified() else '') +
                            os.path.basename(self.path) + (' (deleted)' if self.deleted else ''))
        self.header.set_tooltip_text(self.path)

    def _begin(self, *_: object) -> None:
        if not self.action_depth and not self.restoring:
            self.history[self.history_index] = (_text(self.buffer), self.cursor())
        self.action_depth += 1

    def _end(self, *_: object) -> None:
        self.action_depth -= 1
        if not self.action_depth:
            self._record()

    def cursor(self) -> int:
        return self.buffer.get_iter_at_mark(self.buffer.get_insert()).get_offset()

    def _changed(self, *_: object) -> None:
        if not self.restoring:
            if not self.action_depth:
                self._record()
            self.buffer.set_modified(self.deleted or _text(self.buffer) != self.saved_text)
        self.panel._update_status()

    def _record(self) -> None:
        if self.restoring or _text(self.buffer) == self.history[self.history_index][0]:
            return
        del self.history[self.history_index + 1:]
        self.history.append((_text(self.buffer), self.cursor()))
        self.history_index += 1

    def undo(self, redo: bool = False) -> None:
        index = self.history_index + (1 if redo else -1)
        if not 0 <= index < len(self.history):
            return
        self.history_index = index
        text, cursor = self.history[index]
        self.restoring = True
        self.buffer.set_text(text)
        self.buffer.place_cursor(self.buffer.get_iter_at_offset(min(cursor, len(text))))
        self.restoring = False
        self.buffer.set_modified(self.deleted or text != self.saved_text)
        self.panel._update_status()


class EditorPanel(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.tabs: list[_EditorTab] = []
        self.stack = Gtk.Stack()
        self.placeholder = Gtk.Label(label='IDgent  -  open a file from the explorer')
        self.stack.add_named(self.placeholder, 'empty')
        self.notebook = Gtk.Notebook(scrollable=True)
        self.notebook.connect_after('switch-page', lambda *_: self._update_status())
        self.stack.add_named(self.notebook, 'editor')
        self.pack_start(self.stack, True, True, 0)
        self.search_bar = Gtk.SearchBar()
        search_box = Gtk.Box(spacing=4)
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.connect('search-changed', lambda *_: self._find(False, True))
        self.search_entry.connect('activate', lambda *_: self._find())
        search_box.pack_start(self.search_entry, True, True, 0)
        for label, backwards in [('Previous', True), ('Next', False)]:
            button = Gtk.Button(label=label)
            button.connect('clicked', lambda _, back=backwards: self._find(back))
            search_box.pack_start(button, False, False, 0)
        self.search_bar.add(search_box)
        self.search_bar.connect_entry(self.search_entry)
        self.pack_start(self.search_bar, False, False, 0)
        self.status = Gtk.Label(xalign=0)
        self.pack_start(self.status, False, False, 4)
        self.show_all()
        self.stack.set_visible_child_name('empty')
        self._update_status()

    def _current(self) -> _EditorTab | None:
        page = self.notebook.get_nth_page(self.notebook.get_current_page())
        return next((tab for tab in self.tabs if tab.page == page), None)

    def open_file(self, path: str) -> None:
        path = os.path.abspath(path)
        for tab in self.tabs:
            if tab.path == path:
                self.notebook.set_current_page(self.notebook.page_num(tab.page))
                tab.view.grab_focus()
                return
        try:
            # Reject FIFOs before open(), which could otherwise block the UI.
            if not stat.S_ISREG(os.stat(path).st_mode):
                raise ValueError('Cannot open a non-regular file')
            text, ending, mtime = read_file(path)
        except (OSError, ValueError) as error:
            _message(self, str(error))
            return
        tab = _EditorTab(self, path, text, ending, mtime)
        self.tabs.append(tab)
        self.notebook.append_page(tab.page, tab.header)
        tab.page.show_all()
        tab.header.show_all()
        self.stack.set_visible_child_name('editor')
        self.notebook.set_current_page(self.notebook.page_num(tab.page))
        tab.view.grab_focus()
        self._update_status()

    def _save(self, tab: _EditorTab) -> bool:
        if not tab.buffer.get_modified() and not tab.deleted:
            return True
        try:
            try:
                current_mtime = os.stat(tab.path).st_mtime_ns
            except FileNotFoundError:
                current_mtime = None
            if current_mtime != tab.mtime and not (tab.deleted and current_mtime is None):
                if _ask(self, 'File changed on disk. Overwrite?', ('overwrite', 'cancel')) != 'overwrite':
                    return False
            text = _text(tab.buffer)
            tab.mtime = atomic_write(tab.path, text, tab.ending)
        except (OSError, UnicodeError) as error:
            _message(self, f'Cannot save {tab.path}: {error}')
            return False
        tab.saved_text = text
        tab.deleted = False
        tab.buffer.set_modified(False)
        tab.update_title()
        return True

    def save_current(self) -> bool:
        tab = self._current()
        return self._save(tab) if tab else True

    def save_all(self) -> bool:
        results = [self._save(tab) for tab in self.tabs]
        return all(results)

    def has_unsaved(self) -> bool:
        return any(tab.buffer.get_modified() or tab.deleted for tab in self.tabs)

    def on_path_renamed(self, old: str, new: str) -> None:
        old, new = os.path.abspath(old), os.path.abspath(new)
        for tab in self.tabs:
            if tab.path == old or tab.path.startswith(old + os.sep):
                tab.path = new + tab.path[len(old):]
                tab.update_title()

    def on_path_deleted(self, path: str) -> None:
        path = os.path.abspath(path)
        for tab in self.tabs:
            if tab.path == path or tab.path.startswith(path + os.sep):
                tab.deleted = True
                tab.buffer.set_modified(True)
                tab.update_title()

    def _close_tab(self, tab: _EditorTab) -> bool:
        if tab.buffer.get_modified() or tab.deleted:
            answer = _ask(self, f'Save changes to {os.path.basename(tab.path)}?', ('save', 'discard', 'cancel'))
            if answer == 'cancel' or (answer == 'save' and not self._save(tab)):
                return False
        self.tabs.remove(tab)
        self.notebook.remove_page(self.notebook.page_num(tab.page))
        tab.page.destroy()
        if not self.tabs:
            self.stack.set_visible_child_name('empty')
            self.search_bar.set_search_mode(False)
        self._update_status()
        return True

    def _update_status(self) -> None:
        if not hasattr(self, 'status'):
            return
        tab = self._current()
        cursor = tab.buffer.get_iter_at_mark(tab.buffer.get_insert()) if tab else None
        self.status.set_text(f'Ln {cursor.get_line() + 1 if cursor else 1}, Col '
                             f'{cursor.get_line_offset() + 1 if cursor else 1}  |  UTF-8  |  '
                             f'{tab.ending if tab else "LF"}')

    def _edit(self, tab: _EditorTab, command: str) -> None:
        buffer = tab.buffer
        cursor = buffer.get_iter_at_mark(buffer.get_insert())
        start = cursor.copy()
        start.set_line_offset(0)
        if command == 'comment' and Path(tab.path).suffix.lower() not in COMMENT_EXTENSIONS:
            return
        buffer.begin_user_action()
        try:
            if command in ('enter', 'tab'):
                content = '\n' + leading_whitespace(buffer.get_text(start, cursor, True)) if command == 'enter' else '    '
                buffer.delete_selection(True, True)
                buffer.insert_at_cursor(content)
            else:
                selection = buffer.get_selection_bounds()
                end = cursor.copy()
                if selection:
                    start, end = selection
                    # An exclusive selection ending at the next line excludes that line.
                    if end.get_line_offset() == 0 and end.get_offset() > start.get_offset():
                        end.backward_char()
                    start.set_line_offset(0)
                if not end.ends_line():
                    end.forward_to_line_end()
                content = buffer.get_text(start, end, True)
                replacement = toggle_comments(content) if command == 'comment' else '\n'.join(unindent(line) for line in content.split('\n'))
                offset = start.get_offset()
                buffer.delete(start, end)
                buffer.insert(buffer.get_iter_at_offset(offset), replacement)
        finally:
            buffer.end_user_action()

    def _key_press(self, _view: Gtk.TextView, event: Gdk.EventKey, tab: _EditorTab) -> bool:
        key = event.keyval
        ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(event.state & Gdk.ModifierType.SHIFT_MASK)
        if ctrl:
            if key in (Gdk.KEY_z, Gdk.KEY_Z):
                tab.undo(shift)
            elif key in (Gdk.KEY_w, Gdk.KEY_W):
                self._close_tab(tab)
            elif key in (Gdk.KEY_f, Gdk.KEY_F):
                self.search_bar.set_search_mode(True)
                self.search_entry.grab_focus()
            elif key in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab, Gdk.KEY_Page_Down, Gdk.KEY_Page_Up):
                backwards = shift or key in (Gdk.KEY_ISO_Left_Tab, Gdk.KEY_Page_Up)
                self.notebook.set_current_page((self.notebook.get_current_page() + (-1 if backwards else 1)) % len(self.tabs))
            elif key == Gdk.KEY_slash:
                self._edit(tab, 'comment')
            else:
                return False
            return True
        command = {Gdk.KEY_Return: 'enter', Gdk.KEY_KP_Enter: 'enter',
                   Gdk.KEY_Tab: 'unindent' if shift else 'tab', Gdk.KEY_ISO_Left_Tab: 'unindent'}.get(key)
        if command:
            self._edit(tab, command)
            return True
        return False

    def _find(self, backwards: bool = False, from_cursor: bool = False) -> None:
        tab = self._current()
        query = self.search_entry.get_text()
        if not tab or not query:
            self.search_entry.get_style_context().remove_class('error')
            return
        buffer = tab.buffer
        selection = buffer.get_selection_bounds()
        origin = (selection[0 if backwards or from_cursor else 1] if selection else
                  buffer.get_iter_at_mark(buffer.get_insert()))
        flags = Gtk.TextSearchFlags.CASE_INSENSITIVE | Gtk.TextSearchFlags.TEXT_ONLY
        search = 'backward_search' if backwards else 'forward_search'
        match = getattr(origin, search)(query, flags, None)
        if match is None:
            origin = buffer.get_end_iter() if backwards else buffer.get_start_iter()
            match = getattr(origin, search)(query, flags, None)
        style = self.search_entry.get_style_context()
        if match:
            style.remove_class('error')
            buffer.select_range(*match)
            tab.view.scroll_to_iter(match[0], 0.1, False, 0, 0)
        else:
            style.add_class('error')
