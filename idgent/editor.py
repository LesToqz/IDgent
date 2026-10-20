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
from gi.repository import Gdk, GLib, GObject, Gtk, Pango

from idgent import gutter, highlight, livemd, motion, notes, slashmenu

MAX_FILE_SIZE = 5 * 1024 * 1024
COMMENT_EXTENSIONS = {'.py', '.sh', '.toml', '.yaml', '.yml', '.conf'}
DEFAULT_FONT_SIZE, MIN_FONT_SIZE, MAX_FONT_SIZE = 11, 7, 32


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


def unindent(line: str, width: int = 4) -> str:
    return line[1:] if line.startswith('\t') else line[min(width, len(line) - len(line.lstrip(' '))):]


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


class _StatusBar(Gtk.Box):
    """Separate hover targets while retaining the status label's get_text API."""
    def __init__(self):
        super().__init__(spacing=4)
        self.get_style_context().add_class('idgent-status')
        self.set_halign(Gtk.Align.END)
        self.set_valign(Gtk.Align.END)
        self.items = []
        for index in range(4):
            if index:
                self.pack_start(Gtk.Label(label='|'), False, False, 0)
            item = Gtk.EventBox()
            surface = Gtk.Box()
            surface.get_style_context().add_class('idgent-status-item')
            label = Gtk.Label()
            surface.add(label)
            item.add(surface)
            item.connect('enter-notify-event', lambda widget, _: widget.get_child().set_state_flags(Gtk.StateFlags.PRELIGHT, False))
            item.connect('leave-notify-event', lambda widget, _: widget.get_child().unset_state_flags(Gtk.StateFlags.PRELIGHT))
            self.pack_start(item, False, False, 0)
            self.items.append(label)

    def set_text(self, text):
        for label, value in zip(self.items, text.split('|')):
            label.set_text(value.strip())

    def get_text(self):
        return '  |  '.join(label.get_text() for label in self.items)


class _TabHeader:
    """Shared file/view tab chrome; content and lifetime belong to the panel."""
    def _build_header(self, close, close_tooltip='Close tab', icon_name=None):
        self._close = close
        self.label = Gtk.Label(xalign=0)
        self.label.set_ellipsize(Pango.EllipsizeMode.END)
        self.label.set_max_width_chars(32)
        self.header = Gtk.EventBox()
        self.header.set_visible_window(False)
        self.header.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.header.connect('enter-notify-event', lambda *_: self._hover_header(True))
        self.header.connect('leave-notify-event', lambda *_: self._hover_header(False))
        self.header.connect('button-press-event', self._header_press)
        self.header.connect('unmap', lambda *_: self.header.set_margin_start(0))
        self.header_inner = Gtk.Box(spacing=2)
        self.header_inner.get_style_context().add_class('idgent-tab-inner')
        self.header_inner.set_margin_top(3)
        self.header_inner.set_margin_bottom(3)
        self.header.add(self.header_inner)
        if icon_name and Gtk.IconTheme.get_default().has_icon(icon_name):
            image = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.MENU)
            image.set_pixel_size(14)
            self.header_inner.pack_start(image, False, False, 0)
        self.header_inner.pack_start(self.label, True, True, 0)
        self.close = Gtk.Button()
        icon = Gtk.Image.new_from_icon_name('window-close-symbolic', Gtk.IconSize.MENU)
        icon.set_pixel_size(14)
        self.close.add(icon)
        self.close.get_style_context().add_class('idgent-tab-close')
        self.close.set_relief(Gtk.ReliefStyle.NONE)
        self.close.set_tooltip_text(close_tooltip)
        self.close.connect('clicked', lambda *_: self._close())
        self.header_inner.pack_start(self.close, False, False, 0)
        self.divider = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        self.divider.get_style_context().add_class('idgent-tab-divider')
        self.divider.set_valign(Gtk.Align.CENTER)
        self.header_inner.pack_end(self.divider, False, False, 0)

    def _hover_header(self, hovered):
        context = self.header_inner.get_style_context()
        (context.add_class if hovered else context.remove_class)('hover')
        return False

    def _header_press(self, _widget, event):
        if event.button == 2:
            self._close()
            return True
        return False


class _ViewTab(_TabHeader):
    def __init__(self, panel, key, title, widget, tooltip, icon_name):
        self.key, self.page = key, widget
        self._build_header(lambda: panel.close_view_tab(key), icon_name=icon_name)
        self.label.set_text(title)
        self.header.set_tooltip_text(tooltip)


class _EditorTab(_TabHeader):
    def __init__(self, panel: EditorPanel, path: str, text: str, ending: str, mtime: int):
        self.panel, self.path, self.ending, self.mtime = panel, path, ending, mtime
        self.deleted = False
        self.saved_text = text
        self.buffer = Gtk.TextBuffer()
        self.buffer.set_text(text)
        self.buffer.set_modified(False)
        self.view = Gtk.TextView(buffer=self.buffer)
        self.view.set_pixels_above_lines(1)
        self.view.set_pixels_below_lines(1)
        self.view.set_wrap_mode(Gtk.WrapMode.NONE)
        self.view.set_left_margin(0)
        self.view.set_right_margin(12)
        self.view.set_cursor_visible(True)
        self.highlighter = highlight.Highlighter(self.buffer, panel.syntax_style)
        self.chrome = gutter.ViewChrome(self.view, panel.chrome)
        self.chrome.set_line_numbers(panel.line_numbers)
        self.chrome.set_highlight_line(panel.highlight_line)
        self.slash_menu = None
        self.md_mode = 'source'
        self.md_renderer = None
        self._md_restore_idle = 0
        self._md_closed = False
        self.md_large = len(text.encode('utf-8')) > livemd.MAX_BYTES
        self.set_font_size(panel.font_size)
        self.view.connect('scroll-event', panel._scroll)
        self.view.connect('style-updated', lambda *_: self._set_tab_stops())
        self.detect_language()
        self.source_scroll = Gtk.ScrolledWindow()
        self.source_scroll.add(self.view)
        self.page = Gtk.Overlay()
        self.page.add(self.source_scroll)
        self.page.connect('destroy', self._destroy_markdown)
        self._sync_markdown()
        self._build_header(lambda: panel._close_tab(self), 'Close file')
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

    def set_font_size(self, size: int) -> None:
        self.chrome.set_font_size(size)
        self._set_tab_stops()
        if self.md_renderer:
            self.style_markdown()

    def _set_tab_stops(self) -> None:
        layout = gutter.layout_for(self.view, ' ' * self.panel.indent_width)
        tabs = Pango.TabArray.new(1, True)
        tabs.set_tab(0, Pango.TabAlign.LEFT, layout.get_pixel_size()[0])
        self.view.set_tabs(tabs)

    def _sync_markdown(self) -> None:
        enabled = Path(self.path).suffix.lower() in ('.md', '.markdown')
        if enabled == (self.md_renderer is not None):
            return
        if not enabled:
            self.set_markdown_mode('source')
            self.md_renderer.destroy()
            self.md_renderer = None
            self.detect_language()
            return
        self.md_renderer = livemd.LiveMarkdown(self.view, self.panel.open_file, lambda: self.path)
        self.md_renderer.set_readable_width(self.panel.md_readable_width)
        self.style_markdown()
        if self.panel.md_live_preview and not self.md_large:
            self.set_markdown_mode('preview')

    def _sync_slash_menu(self) -> None:
        enabled = (self.panel.slash_commands and self.md_mode == 'preview'
                   and notes.is_note_path(self.path) and not self._md_closed)
        if enabled and self.slash_menu is None:
            self.slash_menu = slashmenu.SlashMenu(self.view)
        elif not enabled and self.slash_menu is not None:
            self.slash_menu.destroy()
            self.slash_menu = None

    def style_markdown(self) -> None:
        if self.md_renderer:
            self.md_renderer.set_style(self.panel.syntax_style, self.panel.chrome, self.panel.font_size)

    def _destroy_markdown(self, *_: object) -> None:
        self._md_closed = True
        self._sync_slash_menu()
        if self._md_restore_idle:
            GLib.source_remove(self._md_restore_idle)
            self._md_restore_idle = 0
        if self.md_renderer:
            self.md_renderer.destroy()

    def set_markdown_mode(self, mode: str) -> None:
        if not self.md_renderer or mode == self.md_mode:
            return
        if mode == 'preview' and len(_text(self.buffer).encode('utf-8')) > livemd.MAX_BYTES:
            self.md_large = True
            self.panel._update_status()
            return
        adjustment = self.source_scroll.get_vadjustment()
        offset, cursor = adjustment.get_value(), self.cursor()
        self.md_mode = mode
        live = mode == 'preview'
        self.highlighter.set_lexer(None if live else highlight.lexer_for(self.path))
        self.chrome.set_line_numbers(self.panel.line_numbers and not live)
        self.chrome.set_highlight_line(self.panel.highlight_line and not live)
        self.md_renderer.set_enabled(live)
        self._sync_slash_menu()
        self.buffer.place_cursor(self.buffer.get_iter_at_offset(cursor))
        adjustment.set_value(offset)
        if self._md_restore_idle:
            GLib.source_remove(self._md_restore_idle)
        def restore():
            self._md_restore_idle = 0
            if not self._md_closed:
                adjustment.set_value(offset)
            return False
        self._md_restore_idle = GLib.idle_add(restore, priority=GLib.PRIORITY_LOW)
        self.view.grab_focus()
        self.panel._update_status()

    @property
    def language(self) -> str:
        if Path(self.path).suffix.lower() in ('.md', '.markdown'):
            mode = 'Live' if self.md_mode == 'preview' else 'Source'
            return 'Markdown (' + mode + ')' + (' — live preview disabled above 1 MiB' if self.md_large else '')
        return highlight.language_name(self.highlighter.lexer)

    def detect_language(self) -> None:
        start = self.buffer.get_start_iter()
        end = start.copy()
        end.forward_to_line_end()
        self.highlighter.set_lexer(None if self.md_mode == 'preview' else
                                   highlight.lexer_for(self.path, self.buffer.get_text(start, end, False)[:200]))

    def set_path(self, path: str) -> None:
        self.path = path
        self.detect_language()
        self._sync_markdown()
        self._sync_slash_menu()
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
    __gsignals__ = {
        # Absolute path of the active tab, '' when none is open.
        'current-file-changed': (GObject.SignalFlags.RUN_LAST, None, (str,)),
        'view-tab-closed': (GObject.SignalFlags.RUN_LAST, None, (str,)),
    }

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.get_style_context().add_class('idgent-editor')
        self.syntax_style: dict[str, highlight.SyntaxStyle] = dict(highlight.DARK_PLUS)
        self.chrome: dict[str, str] = dict(gutter.DEFAULT_CHROME)
        self.font_size = self.default_font_size = DEFAULT_FONT_SIZE
        self.line_numbers = self.highlight_line = True
        self.indent_tabs, self.indent_width = False, 4
        self.auto_pair = False
        self.md_live_preview = self.md_readable_width = True
        self.slash_commands = True
        self._announced_path = ''
        self.tabs: list[_EditorTab] = []
        self._view_tabs: dict[str, _ViewTab] = {}
        self.connect('destroy', self._destroy_view_tabs)
        self.connect('key-press-event', self._view_key_press)
        self.stack = Gtk.Stack()
        self.placeholder = Gtk.Label(label='IDgent  -  open a file from the explorer')
        self.placeholder.get_style_context().add_class('idgent-empty')
        self.stack.add_named(self.placeholder, 'empty')
        self.notebook = Gtk.Notebook(scrollable=True)
        self.notebook.connect_after('switch-page', self._switched_page)
        self.notebook.connect('page-reordered', lambda *_: self._update_tabs())
        self.notebook.connect('size-allocate', lambda *_: self._size_tabs())
        self.stack.add_named(self.notebook, 'editor')
        self.overlay = Gtk.Overlay()
        self.overlay.add(self.stack)
        self.pack_start(self.overlay, True, True, 0)
        self.search_bar = Gtk.SearchBar()
        self.search_bar.get_style_context().add_class('idgent-find-bar')
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
        self.status = _StatusBar()
        language_item = self.status.items[-1].get_parent().get_parent()
        language_item.set_tooltip_text('Markdown: Toggle source mode (Ctrl+Shift+M)')
        language_item.connect('button-press-event', self._language_press)
        self.overlay.add_overlay(self.status)
        # Reserve an entire bottom row, including at maximum zoom and with find open.
        self.status.connect('size-allocate', lambda _, rect: self.stack.set_margin_bottom(rect.height))
        self.stack.set_margin_bottom(self.status.get_preferred_height().natural_height)
        self.show_all()
        self.stack.set_visible_child_name('empty')
        self._update_status()

    def _all_tabs(self):
        return [*self.tabs, *self._view_tabs.values()]

    def _switched_page(self, *_):
        self._update_tabs()
        self._update_status()
        self._announce_current()
        if self.current_view_key() is not None:
            self.search_bar.set_search_mode(False)
            self.focus_current()

    def add_view_tab(self, key: str, title: str, widget: Gtk.Widget,
                     tooltip: str | None = None, *, icon_name: str | None = None) -> bool:
        """Append and select arbitrary content, or select an existing key."""
        if key in self._view_tabs:
            self.notebook.set_current_page(self.notebook.page_num(self._view_tabs[key].page))
            self.focus_current()
            return False
        tab = _ViewTab(self, key, title, widget, tooltip, icon_name)
        self._view_tabs[key] = tab
        self.notebook.append_page(tab.page, tab.header)
        self.notebook.set_tab_reorderable(tab.page, True)
        tab.page.show_all()
        tab.header.show_all()
        self.stack.set_visible_child_name('editor')
        self.notebook.set_current_page(self.notebook.page_num(tab.page))
        self._switched_page()
        return True

    def has_view_tab(self, key) -> bool:
        return key in self._view_tabs

    def view_tab_widget(self, key) -> Gtk.Widget | None:
        tab = self._view_tabs.get(key)
        return tab.page if tab else None

    def set_view_tab_title(self, key, title, tooltip=None) -> None:
        tab = self._view_tabs.get(key)
        if tab:
            tab.label.set_text(title)
            tab.header.set_tooltip_text(tooltip)

    def current_view_key(self) -> str | None:
        page = self.notebook.get_nth_page(self.notebook.get_current_page())
        return next((key for key, tab in self._view_tabs.items() if tab.page == page), None)

    def close_view_tab(self, key) -> bool:
        tab = self._view_tabs.pop(key, None)
        if tab is None:
            return False
        shutdown = getattr(tab.page, 'shutdown', None)
        if callable(shutdown):
            shutdown()
        self._remove_page(tab)
        self.emit('view-tab-closed', key)
        return True

    def _destroy_view_tabs(self, *_):
        # destroy runs before GTK recursively destroys notebook children.
        for key in list(self._view_tabs):
            self.close_view_tab(key)

    def _view_key_press(self, _widget, event):
        if self.current_view_key() is None:
            return False
        return self._tab_shortcut(event)

    def _tab_shortcut(self, event):
        if not event.state & Gdk.ModifierType.CONTROL_MASK:
            return False
        key = event.keyval
        if key in (Gdk.KEY_w, Gdk.KEY_W):
            view_key = self.current_view_key()
            if view_key is not None:
                self.close_view_tab(view_key)
            elif self._current():
                self._close_tab(self._current())
        elif key in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab, Gdk.KEY_Page_Down, Gdk.KEY_Page_Up):
            count = self.notebook.get_n_pages()
            if count:
                backwards = event.state & Gdk.ModifierType.SHIFT_MASK or key in (Gdk.KEY_ISO_Left_Tab, Gdk.KEY_Page_Up)
                self.notebook.set_current_page((self.notebook.get_current_page() + (-1 if backwards else 1)) % count)
                self.focus_current()
        else:
            return False
        return True

    def _size_tabs(self):
        width = max(120, min(200, (self.notebook.get_allocated_width() - 16) // max(1, self.notebook.get_n_pages())))
        for tab in self._all_tabs():
            tab.header.set_size_request(width - 8, 32)

    def _update_tabs(self):
        current = self.notebook.get_current_page()
        for tab in self._all_tabs():
            index = self.notebook.page_num(tab.page)
            context = tab.header_inner.get_style_context()
            (context.add_class if index == current else context.remove_class)('active')
            tab.divider.set_opacity(0 if index in (current - 1, current) else 1)
        self._size_tabs()

    def set_line_numbers(self, enabled: bool) -> None:
        """Show/hide line numbers in existing and future source views."""
        self.line_numbers = bool(enabled)
        for tab in self.tabs:
            tab.chrome.set_line_numbers(self.line_numbers and tab.md_mode != 'preview')

    def set_highlight_line(self, enabled: bool) -> None:
        """Enable/disable the current-line background without changing selection."""
        self.highlight_line = bool(enabled)
        for tab in self.tabs:
            tab.chrome.set_highlight_line(self.highlight_line and tab.md_mode != 'preview')

    def set_indent(self, tabs: bool, width: int) -> None:
        """Configure Tab/Shift+Tab insertion and displayed tab stops (default: four spaces)."""
        width = int(width)
        if width < 1 or width > 16:
            raise ValueError('Indent width must be between 1 and 16')
        self.indent_tabs, self.indent_width = bool(tabs), width
        for tab in self.tabs:
            tab.set_font_size(self.font_size)

    def set_auto_pair(self, enabled: bool) -> None:
        """Pair brackets and quotes on typing (disabled by default)."""
        self.auto_pair = bool(enabled)

    def set_md_live_preview(self, enabled: bool) -> None:
        """Choose the initial mode of subsequently opened Markdown tabs."""
        self.md_live_preview = bool(enabled)

    def set_slash_commands(self, enabled: bool) -> None:
        """Enable note slash commands on every live tab, including future tabs."""
        self.slash_commands = bool(enabled)
        for tab in self.tabs:
            tab._sync_slash_menu()

    def set_md_readable_width(self, enabled: bool) -> None:
        """Cap preview text at 700 logical pixels, centred in the viewport."""
        self.md_readable_width = bool(enabled)
        for tab in self.tabs:
            if tab.md_renderer:
                tab.md_renderer.set_readable_width(self.md_readable_width)

    def set_syntax_style(self, styles: dict[str, highlight.SyntaxStyle]) -> None:
        """Recolour every open tab, and every tab opened later, with a kind -> SyntaxStyle dict."""
        self.syntax_style = dict(styles)
        for tab in self.tabs:
            tab.highlighter.set_style(self.syntax_style)
            tab.style_markdown()

    def set_chrome(self, colors: dict[str, str]) -> None:
        """Set gutter / current-line / bracket colours (keys of gutter.DEFAULT_CHROME) on all tabs."""
        known = {key: value for key, value in colors.items() if key in gutter.DEFAULT_CHROME}
        for tab in self.tabs:
            tab.chrome.apply(known)
        # Keep only values GTK accepted, so the dict mirrors what is painted.
        self.chrome.update({key: value for key, value in known.items()
                            if gutter.valid_color(value)})
        for tab in self.tabs:
            tab.style_markdown()

    def set_font_size(self, size: int) -> None:
        size = max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, int(size)))
        if size == self.font_size:
            return
        self.font_size = size
        for tab in self.tabs:
            tab.set_font_size(size)

    def set_default_font_size(self, size: int) -> None:
        """Set the size Ctrl+0 restores (the configured editor size)."""
        self.default_font_size = max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, int(size)))

    def _markdown_tab(self, path=None):
        return self._current() if path is None else next((tab for tab in self.tabs if tab.path == os.path.abspath(path)), None)

    def set_markdown_mode(self, mode: str, path=None) -> None:
        """Select source/preview on the current (or named) Markdown tab."""
        if mode not in ('source', 'preview'):
            raise ValueError('Markdown mode must be source or preview')
        tab = self._markdown_tab(path)
        if tab:
            tab.set_markdown_mode(mode)

    def markdown_mode(self, path=None):
        """Return source/preview, or None for a non-Markdown/missing tab."""
        tab = self._markdown_tab(path)
        return tab.md_mode if tab and tab.md_renderer else None

    def zoom(self, step: int) -> None:
        if self.current_view_key() is not None:
            return
        self.set_font_size(self.font_size + step)

    def _scroll(self, _view: Gtk.TextView, event: Gdk.EventScroll) -> bool:
        if not event.state & Gdk.ModifierType.CONTROL_MASK:
            return False
        if event.direction == Gdk.ScrollDirection.UP:
            self.zoom(1)
        elif event.direction == Gdk.ScrollDirection.DOWN:
            self.zoom(-1)
        elif event.direction == Gdk.ScrollDirection.SMOOTH and event.delta_y:
            self.zoom(-1 if event.delta_y > 0 else 1)
        return True

    def _current(self) -> _EditorTab | None:
        page = self.notebook.get_nth_page(self.notebook.get_current_page())
        return next((tab for tab in self.tabs if tab.page == page), None)

    def current_path(self) -> str | None:
        tab = self._current()
        return tab.path if tab else None

    def _announce_current(self) -> None:
        path = self.current_path() or ''
        if path != self._announced_path:
            self._announced_path = path
            self.emit('current-file-changed', path)

    def buffer_text(self, path: str) -> str | None:
        """Live text (unsaved edits included) of an open tab, else None."""
        path = os.path.abspath(path)
        tab = next((tab for tab in self.tabs if tab.path == path), None)
        # A deleted file is gone from the project; its tab must not be a search hit.
        return _text(tab.buffer) if tab and not tab.deleted else None

    def selected_text(self) -> str:
        """Markdown selections retain their exact source text."""
        tab = self._current()
        if not tab:
            return ''
        bounds = tab.buffer.get_selection_bounds()
        return tab.buffer.get_text(*bounds, True) if bounds else ''

    def focus_current(self) -> bool:
        """Give keyboard focus to the active tab; False when no tab is open."""
        tab = self._current()
        if tab:
            tab.view.grab_focus()
            return True
        page = self.view_tab_widget(self.current_view_key())
        if page is None:
            return False
        top = _window(self)
        focused = top.get_focus() if top else None
        if focused is page or (focused is not None and focused.is_ancestor(page)):
            return True
        if page.get_can_focus():
            page.grab_focus()
        else:
            page.child_focus(Gtk.DirectionType.TAB_FORWARD)
        return True

    def open_file_at(self, path: str, line: int, column: int = 0, length: int = 0) -> None:
        """Open or focus a file and select [column, column+length) on a zero-based line."""
        path = os.path.abspath(path)
        self.open_file(path)
        tab = next((tab for tab in self.tabs if tab.path == path), None)
        if tab is None:
            return
        if tab.md_renderer and tab.md_mode == 'preview':
            tab.set_markdown_mode('source')
        buffer = tab.buffer
        start = buffer.get_iter_at_line(max(0, min(line, buffer.get_line_count() - 1)))
        limit = start.copy()
        if not limit.ends_line():
            limit.forward_to_line_end()
        start.set_line_offset(min(max(0, column), limit.get_line_offset()))
        end = start.copy()
        end.forward_chars(min(max(0, length), limit.get_line_offset() - start.get_line_offset()))
        buffer.select_range(start, end)
        tab.view.scroll_to_iter(start, 0, True, 0, .5)
        tab.view.grab_focus()
        # A freshly shown tab has no layout yet and GTK drops the request above;
        # repeat it once the view has been allocated and validated.
        mark = buffer.create_mark(None, start, True)

        def centre() -> bool:
            if mark.get_deleted():
                return False
            if tab in self.tabs and tab.view.get_buffer() is buffer:
                tab.view.scroll_to_mark(mark, 0, True, 0, .5)
            buffer.delete_mark(mark)
            return False

        GLib.idle_add(centre, priority=GLib.PRIORITY_LOW)

    def open_file(self, path: str) -> None:
        path = os.path.abspath(path)
        for tab in self.tabs:
            if tab.path == path:
                self.notebook.set_current_page(self.notebook.page_num(tab.page))
                tab.view.grab_focus()
                self._announce_current()
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
        self.notebook.set_tab_reorderable(tab.page, True)
        tab.page.show_all()
        tab.header.show_all()
        self.stack.set_visible_child_name('editor')
        self.notebook.set_current_page(self.notebook.page_num(tab.page))
        self.focus_current()
        self._update_tabs()
        self._update_status()
        self._announce_current()

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
                tab.set_path(new + tab.path[len(old):])
        self._update_status()
        self._announce_current()

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
        tab._destroy_markdown()
        tab.highlighter.destroy()
        self._remove_page(tab)
        return True

    def _remove_page(self, tab):
        index = self.notebook.page_num(tab.page)
        successor = next((other for other in self._all_tabs()
                          if self.notebook.page_num(other.page) == index + 1), None)
        gap = tab.header.get_allocated_width() + 8
        self.notebook.remove_page(index)
        tab.page.destroy()
        self._update_tabs()
        if successor and successor.header.get_mapped():
            # GTK3 has no transforms/FLIP: a shrinking leading margin keeps the
            # surviving labels at their old positions while the strip reflows.
            motion.animate(successor.header, 'tab_close_reflow',
                           lambda progress: successor.header.set_margin_start(round(gap * (1 - progress))),
                           easing='reflow', key='tab-reflow',
                           on_done=lambda: successor.header.set_margin_start(0))
        if not self.notebook.get_n_pages():
            self.stack.set_visible_child_name('empty')
            self.search_bar.set_search_mode(False)
        self._update_status()
        self._announce_current()
        return True

    def _language_press(self, _widget, event):
        tab = self._current()
        if event.button == 1 and tab and tab.md_renderer:
            tab.set_markdown_mode('source' if tab.md_mode == 'preview' else 'preview')
            return True
        return False

    def _update_status(self) -> None:
        if not hasattr(self, 'status'):
            return
        tab = self._current()
        if self.current_view_key() is not None:
            self.status.set_text(' | | | ')
            return
        cursor = tab.buffer.get_iter_at_mark(tab.buffer.get_insert()) if tab else None
        self.status.set_text(f'Ln {cursor.get_line() + 1 if cursor else 1}, Col '
                             f'{cursor.get_line_offset() + 1 if cursor else 1}  |  UTF-8  |  '
                             f'{tab.ending if tab else "LF"}  |  {tab.language if tab else "Plain Text"}')

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
                content = '\n' + leading_whitespace(buffer.get_text(start, cursor, True)) if command == 'enter' else ('\t' if self.indent_tabs else ' ' * self.indent_width)
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
                replacement = toggle_comments(content) if command == 'comment' else '\n'.join(unindent(line, self.indent_width) for line in content.split('\n'))
                offset = start.get_offset()
                buffer.delete(start, end)
                buffer.insert(buffer.get_iter_at_offset(offset), replacement)
        finally:
            buffer.end_user_action()

    def _key_press(self, _view: Gtk.TextView, event: Gdk.EventKey, tab: _EditorTab) -> bool:
        if tab.slash_menu and tab.slash_menu.handle_key(_view, event):
            return True
        if self._tab_shortcut(event):
            return True
        key = event.keyval
        ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(event.state & Gdk.ModifierType.SHIFT_MASK)
        if ctrl:
            if shift and key in (Gdk.KEY_m, Gdk.KEY_M) and tab.md_renderer:
                tab.set_markdown_mode('preview' if tab.md_mode == 'source' else 'source')
            elif key in (Gdk.KEY_z, Gdk.KEY_Z):
                tab.undo(shift)
            elif key in (Gdk.KEY_f, Gdk.KEY_F) and not shift:
                self.search_bar.set_search_mode(True)
                self.search_entry.grab_focus()
            elif key == Gdk.KEY_slash:
                self._edit(tab, 'comment')
            elif key in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
                self.zoom(1)
            elif key in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
                self.zoom(-1)
            elif key in (Gdk.KEY_0, Gdk.KEY_KP_0):
                self.set_font_size(self.default_font_size)
            else:
                return False
            return True
        if tab.md_renderer and tab.md_renderer.edit_key(event, self.indent_tabs, self.indent_width):
            return True
        if self.auto_pair and not event.state & (Gdk.ModifierType.MOD1_MASK | Gdk.ModifierType.SUPER_MASK):
            char = chr(Gdk.keyval_to_unicode(key))
            pairs = {**gutter.PAIRS, '"': '"', "'": "'"}
            cursor = tab.buffer.get_iter_at_mark(tab.buffer.get_insert())
            selection = tab.buffer.get_selection_bounds()
            if char in pairs.values() and not selection and cursor.get_char() == char:
                cursor.forward_char()
                tab.buffer.place_cursor(cursor)
                return True
            if char in pairs:
                start = selection[0].get_offset() if selection else cursor.get_offset()
                selected = tab.buffer.get_text(*selection, True) if selection else ''
                tab.buffer.begin_user_action()
                tab.buffer.delete_selection(True, True)
                tab.buffer.insert_at_cursor(char + selected + pairs[char])
                if selection:
                    tab.buffer.select_range(tab.buffer.get_iter_at_offset(start + 1),
                                            tab.buffer.get_iter_at_offset(start + 1 + len(selected)))
                else:
                    tab.buffer.place_cursor(tab.buffer.get_iter_at_offset(start + 1))
                tab.buffer.end_user_action()
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


def build_css(ui: dict[str, str]) -> str:
    """Editor chrome, composed by the shared theme provider."""
    return f'''
.idgent-editor, .idgent-editor stack, .idgent-editor notebook,
.idgent-editor notebook > stack {{ background: {ui['bg']}; color: {ui['foreground']}; border: 0; }}
.idgent-editor notebook > header.top {{ background: {ui['sidebar_bg']};
    border: 0; border-bottom: 1px solid {ui['border']}; padding: 5px 8px 0; margin: 0; }}
.idgent-editor notebook > header.top > tabs {{ padding: 0; margin: 0; border: 0; }}
.idgent-editor notebook > header.top > tabs > tab {{ background: transparent;
    color: {ui['faint']}; border: 1px solid transparent; border-radius: 10px 10px 0 0;
    box-shadow: none; min-height: 32px; padding: 0 3px; margin: 0; font-size: 13px; }}
.idgent-editor notebook > header.top > tabs > tab:hover {{ color: {ui['muted']}; }}
.idgent-editor notebook > header.top > tabs > tab:checked {{ background: {ui['bg']};
    color: {ui['foreground']}; border-color: {ui['border']}; border-bottom-color: {ui['bg']}; }}
.idgent-tab-inner {{ padding: 0 6px; border-radius: 4px; }}
tab:hover .idgent-tab-inner:not(.active), .idgent-tab-inner.hover:not(.active) {{
    background: alpha({ui['overlay']}, .067); color: {ui['muted']}; }}
.idgent-editor .idgent-tab-close {{ opacity: 0; min-width: 14px; min-height: 14px;
    padding: 2px; margin: 0; border: 0; border-radius: 8px; box-shadow: none;
    background: transparent; color: {ui['muted']}; transition: none; }}
tab:hover .idgent-tab-close, tab:checked .idgent-tab-close,
.idgent-tab-inner.hover .idgent-tab-close, .idgent-tab-close:focus {{ opacity: 1; }}
.idgent-editor .idgent-tab-close:hover {{ background: alpha({ui['overlay']}, .067); color: {ui['foreground']}; }}
.idgent-tab-divider {{ min-width: 1px; min-height: 13px; background: {ui['border_hover']}; margin: 0; }}
.idgent-editor textview, .idgent-editor textview text {{ background-color: {ui['bg']};
    color: {ui['editor_fg']}; caret-color: {ui['cursor']}; font-family: monospace; font-size: 11pt; }}
.idgent-editor textview text selection {{ background-color: {ui['editor_selection']}; color: {ui['selection_fg']}; }}
.idgent-empty {{ color: {ui['muted']}; font-size: 16px; padding: 12px; }}
.idgent-find-bar > revealer > box {{ background: {ui['sidebar_bg']}; border-top: 1px solid {ui['border']};
    border-bottom: 0; box-shadow: none; padding: 8px; }}
.idgent-status {{ background: {ui['sidebar_bg']}; color: {ui['muted']}; font-size: 12px;
    font-feature-settings: "tnum"; padding: 4px; border-top: 1px solid {ui['border']};
    border-left: 1px solid {ui['border']}; border-radius: 8px 0 0 0; }}
.idgent-status-item {{ padding: 3px 4px; border-radius: 4px; }}
.idgent-status-item:hover {{ background: alpha({ui['overlay']}, .067); color: {ui['foreground']}; }}
''' + gutter.build_css(ui)
