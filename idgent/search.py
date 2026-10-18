"""Project search core and a standalone, cancellable Explorer wrapper.

Core functions use only the standard library. Match tuples retain original character
coordinates; their Preview string carries the offset of a shortened display line.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import os
import re
import threading
import time
from typing import NamedTuple

SKIP_DIRS = frozenset('.git .hg .svn __pycache__ node_modules .venv venv '
                      '.mypy_cache .pytest_cache .tox .idea .ceo'.split())
MAX_FILE_SIZE = 1024 * 1024


class SearchError(ValueError):
    """A query the user can correct."""


class Preview(str):
    """Display text, with its starting character offset in the original line."""
    def __new__(cls, text, offset=0):
        value = super().__new__(cls, text)
        value.offset = offset
        return value


class Match(NamedTuple):
    line: int
    column: int
    length: int
    line_text: Preview

    @property
    def preview_offset(self):
        return self.line_text.offset


@dataclass
class FileGroup:
    """Content hits for one absolute path, in source order."""
    path: str
    relative_path: str
    matches: list[Match]
    is_current: bool = False


@dataclass
class SearchResults:
    """Content groups followed by separate absolute basename hits.

    Totals describe retained content matches only. Cancelled scans return their
    partial results; truncated indicates an actual match omitted by a limit.
    """
    groups: list[FileGroup] = field(default_factory=list)
    file_names: list[str] = field(default_factory=list)
    truncated: bool = False
    total_matches: int = 0
    total_files: int = 0


def _read_text(path):
    try:
        if os.path.islink(path) or os.path.getsize(path) > MAX_FILE_SIZE:
            return None
        with open(path, 'rb') as source:
            data = source.read(MAX_FILE_SIZE + 1)
        if len(data) > MAX_FILE_SIZE or b'\0' in data[:8192]:
            return None
        return data.decode('utf-8', errors='strict')
    except (OSError, UnicodeError):
        return None


def _project_files(root, cancel=None):
    root = os.path.abspath(root)
    paths = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        if cancel is not None and cancel.is_set():
            return
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS
                   and not os.path.islink(os.path.join(directory, d))]
        paths.extend(os.path.join(directory, name) for name in files)
    paths.sort(key=lambda p: (os.path.relpath(p, root).casefold(), os.path.relpath(p, root)))
    for path in paths:
        if cancel is not None and cancel.is_set():
            return
        if _read_text(path) is not None:
            yield path


def iter_project_files(root):
    """Yield absolute eligible text-file paths in stable relative-path order."""
    yield from _project_files(root)


def _pattern(query, case_sensitive, regex):
    try:
        return re.compile(query if regex else re.escape(query),
                          0 if case_sensitive else re.IGNORECASE)
    except re.error as error:
        raise SearchError(f'Invalid regular expression: {error}') from error


def _matches(text, pattern):
    for line0, line in enumerate(text.splitlines()):
        for found in pattern.finditer(line):
            if found.end() == found.start():
                continue
            stripped = line.rstrip()
            # Retain whitespace that is itself part of the match.
            display = line[:max(len(stripped), found.end())]
            # Leave room for the highlighted match in a narrow Explorer sidebar.
            # Preserve whitespace when it belongs to the match itself.
            indent = len(display) - len(display.lstrip())
            offset = max(min(indent, found.start()), found.start() - 4)
            while offset < found.start() and display[offset].isspace():
                offset += 1
            yield Match(line0, found.start(), found.end() - found.start(),
                        Preview(display[offset:offset + 200], offset))


def find_in_text(text, query, case_sensitive=False, regex=False):
    """Return four-element Match tuples; preview_offset locates shortened text."""
    if not query:
        return []
    return list(_matches(text, _pattern(query, case_sensitive, regex)))


def _search_updates(root, query, *, case_sensitive, regex, current_path=None,
                    text_provider=None, max_per_file=200, max_total=1000, cancel=None):
    result = SearchResults()
    if not query:
        yield result
        return
    pattern = _pattern(query, case_sensitive, regex)
    root = os.path.abspath(root) if root else ''
    current = os.path.abspath(current_path) if current_path else None
    max_per_file, max_total = max(0, max_per_file), max(0, max_total)

    def cancelled():
        return cancel is not None and cancel.is_set()

    def scan(path):
        text = text_provider(path) if text_provider else None
        if text is None:
            text = _read_text(path)
        if text is None:
            return
        matches = []
        for match in _matches(text, pattern):
            if len(matches) >= max_per_file or result.total_matches >= max_total:
                result.truncated = True
                break
            matches.append(match)
            result.total_matches += 1
        if matches:
            result.groups.append(FileGroup(path, os.path.relpath(path, root) if root else path,
                                           matches, path == current))
            result.total_files += 1

    if current and not cancelled():
        scan(current)
    # The first yield precedes even enumerating the project.
    yield result
    if not root:
        return
    paths = _project_files(root, cancel) if cancel is not None else iter_project_files(root)
    for path in paths:
        if cancelled():
            return
        if query.casefold() in os.path.basename(path).casefold():
            result.file_names.append(path)
        if path != current:
            scan(path)
        yield result


def search(root, query, *, case_sensitive, regex, current_path=None,
           text_provider=None, max_per_file=200, max_total=1000, cancel=None):
    """Search current buffer first, then sorted project contents and basenames.

    Totals count displayed content matches and their file groups; file_names is
    separate. Truncation means at least one actual content match was omitted.
    """
    result = SearchResults()
    for result in _search_updates(root, query, case_sensitive=case_sensitive, regex=regex,
                                  current_path=current_path, text_provider=text_provider,
                                  max_per_file=max_per_file, max_total=max_total, cancel=cancel):
        pass
    return result


import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, GLib, GObject, Gtk, Pango
from .themes import get_theme


def build_css(ui: dict[str, str]) -> str:
    return f"""
@define-color idgent_search_match {ui['accent_text']};
@define-color idgent_search_group {ui['faint']};
.idgent-search-row {{ background: {ui['sidebar_bg']}; padding: 4px 12px 8px; }}
.idgent-search-entry {{ background: {ui['input_bg']}; color: {ui['foreground']};
    border: 1px solid {ui['border']}; border-radius: 5px; min-height: 20px; padding: 4px 8px;
    font-size: 13px; }}
.idgent-search-entry:hover {{ border-color: {ui['border_hover']}; }}
.idgent-search-entry:focus {{ border-color: {ui['border_focus']}; box-shadow: 0 0 0 2px {ui['border_focus']}; }}
.idgent-search-entry image {{ color: {ui['muted']}; }}
.idgent-search-entry image.right {{ min-width: 16px; padding: 0 6px; border-radius: 14px; }}
.idgent-search-entry image.right:hover {{ color: {ui['foreground']}; background: alpha({ui['overlay']}, 0.067); }}
.idgent-search-toggle {{ background: transparent; color: {ui['muted']};
    border: 0; border-radius: 4px; box-shadow: none; min-height: 16px; min-width: 16px;
    padding: 4px; font-size: 13px; }}
.idgent-search-toggle label {{ opacity: 0.85; }}
.idgent-search-toggle:hover {{ background: alpha({ui['overlay']}, 0.067); color: {ui['foreground']}; }}
.idgent-search-toggle:hover label, .idgent-search-toggle:checked label {{ opacity: 1; }}
.idgent-search-toggle:checked {{ background: alpha({ui['accent']}, 0.1); color: {ui['accent_text']}; }}
.idgent-search-summary {{ color: {ui['faint']}; background: {ui['sidebar_bg']};
    font-size: 12px; padding: 4px 12px; }}
treeview.idgent-search-results.view {{ background: {ui['sidebar_bg']}; color: {ui['muted']};
    font-size: 13px; -GtkTreeView-vertical-separator: 2; -GtkTreeView-horizontal-separator: 4;
    -GtkTreeView-expander-size: 10; }}
treeview.idgent-search-results.view:hover, treeview.idgent-search-results.view:selected {{
    background: alpha({ui['overlay']}, 0.067); color: {ui['foreground']}; border-radius: 8px; }}
treeview.idgent-search-results.view:focus {{ -gtk-outline-radius: 8px; }}
treeview.idgent-search-results expander {{ color: {ui['muted']}; }}
"""


SEARCH_CSS = build_css(get_theme(None).ui)


class SearchPanel(Gtk.Box):
    """Wrap Explorer content; emit absolute paths and zero-based match locations.

    Install SEARCH_CSS (or build_css(theme.ui)) in the application's provider.
    Public widget methods, including search_now, run on GTK's main thread.
    """
    __gsignals__ = {
        'result-activated': (GObject.SignalFlags.RUN_LAST, None, (str, int, int, int)),
        'escaped': (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(self, content: Gtk.Widget, root: str = ''):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.root = os.path.abspath(root) if root else ''
        self.current_path = None
        self.text_provider = None
        self.results = SearchResults()
        self._generation = 0
        self._timeout = 0
        self._cancel = threading.Event()
        self._destroyed = False
        self._searching = False
        self._main_thread = threading.get_ident()
        row = Gtk.Box(spacing=2)
        row.get_style_context().add_class('idgent-search-row')
        self.entry = Gtk.SearchEntry(placeholder_text='Search')
        # GtkSearchEntry provides the search icon and a secondary clear icon
        # only while text is present, including keyboard/accessibility support.
        self.entry.set_width_chars(6)
        self.entry.get_style_context().add_class('idgent-search-entry')
        row.pack_start(self.entry, True, True, 0)
        self.case_button = Gtk.ToggleButton(label='Aa', tooltip_text='Match case')
        self.regex_button = Gtk.ToggleButton(label='.*', tooltip_text='Use regular expression')
        for button in (self.case_button, self.regex_button):
            button.set_valign(Gtk.Align.CENTER)
            button.get_accessible().set_name(button.get_tooltip_text())
            button.get_style_context().add_class('idgent-search-toggle')
            row.pack_start(button, False, False, 0)
            button.connect('toggled', self._changed)
        self.pack_start(row, False, False, 0)
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.stack.add_named(content, 'content')
        results_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.summary = Gtk.Label(xalign=0)
        self.summary.set_ellipsize(3)
        self.summary.get_style_context().add_class('idgent-search-summary')
        results_box.pack_start(self.summary, False, False, 0)
        # icon, line-number markup, preview markup, path, line, column, length, kind
        self.store = Gtk.TreeStore(str, str, str, str, int, int, int, str)
        self.tree = Gtk.TreeView(model=self.store, headers_visible=False)
        self.tree.get_style_context().add_class('idgent-search-results')
        self.tree.set_enable_search(False)
        self.tree.set_activate_on_single_click(True)
        self.tree.set_margin_start(12)
        self.tree.set_margin_end(12)
        self.tree.set_margin_bottom(24)
        column = Gtk.TreeViewColumn()
        icon = Gtk.CellRendererPixbuf()
        column.pack_start(icon, False)
        column.add_attribute(icon, 'icon-name', 0)
        number = Gtk.CellRendererText(xalign=1, ypad=2)
        column.pack_start(number, False)
        column.add_attribute(number, 'markup', 1)
        label = Gtk.CellRendererText(ypad=2, ellipsize=Pango.EllipsizeMode.END)
        column.pack_start(label, True)
        column.add_attribute(label, 'markup', 2)
        column.set_cell_data_func(label, self._style_result)
        self._cells = [number, label]
        self.tree.append_column(column)
        self.set_font_size(10)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.add(self.tree)
        results_box.pack_start(scroll, True, True, 0)
        self.stack.add_named(results_box, 'results')
        self.pack_start(self.stack, True, True, 0)
        self.entry.connect('changed', self._changed)
        self.entry.connect('stop-search', lambda *_: self.clear())
        self.entry.connect('activate', self._entry_activate)
        self.entry.connect('key-press-event', self._key)
        self.tree.connect('key-press-event', self._key)
        self.tree.connect('row-activated', lambda tree, path, column: self._activate(path))
        self.connect('destroy', self._destroy)
        self.show_all()
        self.stack.set_visible_child_name('content')

    @property
    def query(self):
        return self.entry.get_text()

    def set_root(self, path):
        self.root = os.path.abspath(path) if path else ''
        self._changed()

    def set_current_file(self, path: str | None):
        self.current_path = os.path.abspath(path) if path else None
        # Reorder the already visible results immediately, then refresh buffers.
        for group in self.results.groups:
            group.is_current = group.path == self.current_path
        self.results.groups.sort(key=lambda g: (not g.is_current, g.relative_path.casefold(), g.relative_path))
        if self.query:
            self._render(self.results)
            self._changed()

    def set_text_provider(self, provider):
        """Set path -> live text | None; callbacks always run on GTK's thread."""
        self.text_provider = provider
        self._changed()

    def set_font_size(self, size):
        """Size the result rows (in points)."""
        self.font_size = size
        description = self.tree.get_pango_context().get_font_description().copy()
        description.set_size(size * Pango.SCALE)
        metrics = self.tree.get_pango_context().get_metrics(description, None)
        height = (metrics.get_ascent() + metrics.get_descent()) / Pango.SCALE
        for cell in self._cells:
            cell.set_property('size-points', size)
            cell.set_fixed_size(-1, max(20, int(height + .999) + 4))
        self.tree.columns_autosize()
        self.tree.queue_resize()

    def _style_result(self, column, cell, model, row, data=None):
        """Resolve Pango colours at paint time so live theme changes keep hits fresh."""
        group = model[row][7] == 'group'
        context = self.tree.get_style_context()
        found, colour = context.lookup_color('idgent_search_group' if group else 'idgent_search_match')
        markup = model[row][2]
        if found:
            hex_colour = '#{:02x}{:02x}{:02x}'.format(
                *(round(channel * 255) for channel in (colour.red, colour.green, colour.blue)))
            if group:
                markup = f'<span foreground="{hex_colour}" weight="600">{markup}</span>'
            elif model[row][7] == 'match':
                markup = markup.replace('<b><u>', f'<span foreground="{hex_colour}"><b><u>').replace(
                    '</u></b>', '</u></b></span>')
        cell.set_property('markup', markup)
        if group:
            attributes = cell.get_property('attributes')
            attributes.change(Pango.attr_size_new_absolute(12 * Pango.SCALE))
            attributes.change(Pango.attr_weight_new(Pango.Weight.SEMIBOLD))
            cell.set_property('attributes', attributes)

    def focus_search(self):
        self.entry.grab_focus()
        self.entry.select_region(0, -1)

    def clear(self):
        self.entry.set_text('')
        self._changed()

    def _invalidate(self):
        self._cancel.set()
        self._generation += 1
        if self._timeout:
            GLib.source_remove(self._timeout)
            self._timeout = 0
        self._searching = False

    def _changed(self, *_):
        if self._destroyed:
            return
        self._invalidate()
        self.stack.set_visible_child_name('results' if self.query else 'content')
        if self.query:
            self._timeout = GLib.timeout_add(200, self._start)
        else:
            self.results = SearchResults()
            self.store.clear()
            self.summary.set_text('No results')

    def _options(self, provider=None):
        return dict(case_sensitive=self.case_button.get_active(), regex=self.regex_button.get_active(),
                    current_path=self.current_path, text_provider=provider)

    def search_now(self, query=None):
        """Cancel pending work and search synchronously; display query errors."""
        if query is not None:
            self.entry.set_text(query)
        self._invalidate()
        self.stack.set_visible_child_name('results' if self.query else 'content')
        try:
            result = search(self.root, self.query, **self._options(self.text_provider))
        except SearchError as error:
            self._error(error)
            return self.results
        self._render(result)
        return result

    def _error(self, error):
        self.results = SearchResults()
        self.store.clear()
        self.summary.set_text(str(error))
        self.summary.set_tooltip_text(str(error))
        self._searching = False

    def _start(self):
        self._timeout = 0
        if self._destroyed:
            return False
        generation = self._generation
        cancel = self._cancel = threading.Event()
        provider = self.text_provider

        def live_text(path):
            if provider is None:
                return None
            if threading.get_ident() == self._main_thread:
                return provider(path)
            # Editor providers may read Gtk.TextBuffer, so always invoke on GTK's thread.
            ready = threading.Event()
            answer = [None]

            def fetch():
                if not cancel.is_set() and not self._destroyed:
                    try:
                        answer[0] = provider(path)
                    finally:
                        ready.set()
                else:
                    ready.set()
                return False

            GLib.idle_add(fetch)
            while not ready.wait(.02):
                if cancel.is_set():
                    return None
            return answer[0]

        updates = _search_updates(self.root, self.query, cancel=cancel, **self._options(live_text))
        try:
            first = next(updates)
        except SearchError as error:
            self._error(error)
            return False
        self._searching = True
        self._render(SearchResults(list(first.groups), list(first.file_names), first.truncated,
                                   first.total_matches, first.total_files))

        def post(result, done=False):
            snapshot = SearchResults(list(result.groups), list(result.file_names), result.truncated,
                                     result.total_matches, result.total_files)
            GLib.idle_add(self._deliver, generation, snapshot, done)

        def work():
            latest = first
            last_post = time.monotonic()
            for latest in updates:
                if cancel.is_set():
                    return
                if time.monotonic() - last_post >= .05:
                    post(latest)
                    last_post = time.monotonic()
            if not cancel.is_set():
                post(latest, True)

        threading.Thread(target=work, name='idgent-search', daemon=True).start()
        return False

    def _deliver(self, generation, result, done):
        if not self._destroyed and generation == self._generation:
            self._searching = not done
            self._render(result)
        return False

    def _render(self, result):
        self.results = result
        self.store.clear()
        esc = GLib.markup_escape_text
        for group in result.groups:
            directory = os.path.dirname(group.relative_path)
            tag = '  · current file' if group.is_current else ''
            # Count and tag first: a narrow sidebar clips the long directory, not these.
            markup = (f'<b>{esc(os.path.basename(group.path))}</b> '
                      f'<span alpha="60%">{len(group.matches)}</span><i>{tag}</i>'
                      f'<span alpha="60%">  {esc(directory)}</span>')
            parent = self.store.append(None, ['text-x-generic-symbolic', '', markup,
                                             group.path, 0, 0, 0, 'group'])
            for match in group.matches:
                preview = match.line_text
                start = match.column - match.preview_offset
                end = min(len(preview), start + match.length)
                # The ellipsis is decoration, outside Preview's source coordinates.
                prefix = '…' if match.preview_offset else ''
                markup = (prefix + esc(preview[:start]) + '<b><u>' + esc(preview[start:end])
                          + '</u></b>' + esc(preview[end:]))
                self.store.append(parent, [None, f'<span alpha="60%">{match.line + 1}</span>',
                                           markup, group.path, match.line, match.column,
                                           match.length, 'match'])
        if result.file_names:
            parent = self.store.append(None, ['folder-saved-search-symbolic', '', '<b>File names</b>',
                                             '', 0, 0, 0, 'group'])
            for path in result.file_names:
                relative = os.path.relpath(path, self.root)
                self.store.append(parent, ['text-x-generic-symbolic', '', esc(relative),
                                           path, 0, 0, 0, 'file'])
        self.tree.expand_all()
        if result.truncated:
            summary = f'Showing the first {result.total_matches} results'
        elif result.total_matches:
            summary = f'{result.total_matches} results in {result.total_files} files'
        elif result.file_names:
            summary = f'{len(result.file_names)} file name results'
        else:
            summary = 'Searching…' if self._searching else 'No results'
        self.summary.set_text(summary)
        self.summary.set_tooltip_text(summary)

    def _activate(self, path):
        row = self.store[path]
        if row[7] == 'group':
            if self.tree.row_expanded(path):
                self.tree.collapse_row(path)
            else:
                self.tree.expand_row(path, False)
        else:
            self.emit('result-activated', row[3], row[4], row[5], row[6])

    def _entry_activate(self, *_):
        self.search_now()
        if self.store.get_iter_first() is not None:
            self.tree.grab_focus()
            self.tree.set_cursor(Gtk.TreePath.new_from_indices([0]))

    def _key(self, widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.clear()
            self.entry.grab_focus()
            self.emit('escaped')
            return True
        if widget == self.entry and event.keyval == Gdk.KEY_Down:
            self.tree.grab_focus()
            if self.store.get_iter_first() is not None:
                self.tree.set_cursor(Gtk.TreePath.new_from_indices([0]))
            return True
        return False

    def _destroy(self, *_):
        self._destroyed = True
        self._invalidate()
