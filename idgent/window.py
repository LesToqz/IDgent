"""Application window, panel layout, menus, and shared commands.

The content sits in an ``OverlayHost`` (quick switcher, command palette and
notices). Every settings key is applied at start-up and live from the
settings window's signals. Window-level hotkeys (Ctrl+P/Ctrl+O quick
switcher, F1 hotkeys page) yield to a focused terminal so readline/vim keep
them; Ctrl+Shift+P (command palette) works everywhere.
"""

import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Vte", "2.91")
from gi.repository import Gdk, GLib, Gtk, Vte

from idgent import fsops, gitlog, motion, settings, style
from idgent.graphdata import SEMANTICS
from idgent.graphview import GraphView
from idgent.gitviews import CommitView, GitGraphView
from idgent.overlay import OverlayHost
from idgent.palette import Command, CommandPalette, QuickSwitcher
from idgent.search import SKIP_DIRS
from idgent.themes import THEMES, Theme, get_theme
from idgent.explorer import ExplorerPanel
from idgent.editor import EditorPanel
from idgent.terminal import TerminalPanel
from idgent.titlebar import TitleBar
from idgent.settings_dialog import DEFAULT_HOTKEYS, SettingsDialog

# Upper bound for the quick switcher's file walk (keeps huge trees responsive).
MAX_SWITCHER_FILES = 20000
RECENT_FILES = 50
_CTRL = Gdk.ModifierType.CONTROL_MASK
_CTRL_SHIFT = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK
_DELETE_MODES = {'permanent': 'delete', 'trash': 'trash'}


def accel_text(accelerator: str) -> str:
    """'<Control><Shift>s' -> 'Ctrl+Shift+S' (the settings Hotkeys format)."""
    key, modifiers = Gtk.accelerator_parse(accelerator)
    parts = [name for mask, name in ((Gdk.ModifierType.CONTROL_MASK, 'Ctrl'),
                                     (Gdk.ModifierType.SHIFT_MASK, 'Shift'),
                                     (Gdk.ModifierType.MOD1_MASK, 'Alt')) if modifiers & mask]
    return '+'.join(parts + [Gtk.accelerator_get_label(key, 0)])


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, application: Gtk.Application, root: str,
                 theme: Theme | None = None) -> None:
        super().__init__(application=application)
        self.set_default_size(1200, 750)
        self.root = str(Path(root).resolve())
        self._fullscreen_panel = None
        self._fullscreen_layout = None
        self.theme = theme if theme is not None else get_theme(settings.get_theme_id())

        self._graph_tabs = {}
        self._graph_timers = {}
        self._graph_refresh_timer = None
        self._graph_errors = set()
        self._git_errors = set()
        self._commit_views = {}
        self._commit_pending = set()
        self._commit_executor = None
        self._git_lock = threading.Lock()
        self._git_sources = set()
        self._git_epoch = 0
        self._git_closed = False
        self._git_full_notice = False
        self._settings_dialog = None
        self._font_sizes = settings.get_font_sizes()
        stored = settings.get_values()
        self._values = {key: stored[key] for key in settings.NEW_KEYS}
        self._commands: list[Command] = []
        self._recent_files: list[str] = []
        self._switcher = self._palette = None
        motion.set_reduce_motion(self._values['reduce_motion'])
        self.explorer = ExplorerPanel(self.root)
        self.editor = EditorPanel()
        self.explorer.set_font_size(self._font_sizes["explorer_font_size"])
        self.editor.set_font_size(self._font_sizes["editor_font_size"])
        self.editor.set_default_font_size(self._font_sizes["editor_font_size"])
        terminal_size = self._font_sizes["terminal_font_size"]
        autostart = self._values['terminal_autostart']
        self.terminal_bottom = TerminalPanel(self.root, autostart=autostart,
                                             font_size=terminal_size)
        self.terminal_right = TerminalPanel(self.root, autostart=False, stacked=True,
                                            font_size=terminal_size)
        self.terminal_right.set_no_show_all(True)
        if not autostart:
            self.terminal_bottom.set_no_show_all(True)
        for panel in (self.terminal_bottom, self.terminal_right):
            panel.graph_factory = self._make_graph_view
            panel.gitgraph_factory = self._make_gitgraph_view
            panel.connect("last-closed", self._hide_terminal)
            panel.connect("maximize-toggled", self._terminal_maximize_toggled)
        for key in settings.NEW_KEYS:
            if key not in ('accent', 'ui_font', 'editor_font', 'reduce_motion'):
                self._apply_setting(key)
        self.explorer.connect("file-activated", lambda _, path: self.editor.open_file(path))
        self.explorer.connect("path-renamed", lambda _, old, new: self.editor.on_path_renamed(old, new))
        self.explorer.connect("path-deleted", lambda _, path: self.editor.on_path_deleted(path))
        self.explorer.connect("search-result-activated",
                              lambda _, path, line, column, length:
                              self.editor.open_file_at(path, line, column, length))
        self.explorer.connect("search-escaped", self._search_escaped)
        self.editor.connect("current-file-changed", self._current_file_changed)
        self.connect("focus-in-event", lambda *_: (self.explorer.refresh_git(), False)[1])
        self.explorer.set_text_provider(self.editor.buffer_text)
        self.explorer.connect('path-renamed', self._graph_paths_changed)
        self.explorer.connect('path-deleted', self._graph_paths_changed)
        # Explorer create/refresh rebuilds its model without a path-created signal.
        # Coalesce those notifications so one operation performs one index scan.
        for signal in ('row-inserted', 'row-deleted'):
            self.explorer.store.connect(signal, self._graph_tree_changed)
        self.editor.notebook.connect('page-removed', self._graph_tab_closed)
        self.editor.notebook.connect_after('switch-page', self._git_selection_changed)
        self.editor.connect('view-tab-closed', self._commit_tab_closed)
        self.connect('destroy', self._graph_cleanup)
        self.connect('destroy', self._git_cleanup)

        self.dock_bottom = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        self.dock_bottom.pack1(self.editor, resize=True, shrink=True)
        self.dock_bottom.pack2(self.terminal_bottom, resize=False, shrink=True)
        self.dock = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.dock.pack1(self.dock_bottom, resize=True, shrink=True)
        self.dock.pack2(self.terminal_right, resize=False, shrink=True)
        self._size_pending = {self.terminal_bottom: True, self.terminal_right: True}
        for dock, panel in ((self.dock_bottom, self.terminal_bottom), (self.dock, self.terminal_right)):
            dock.connect_after("size-allocate", self._size_terminal, panel)
            # A dock shown into a laid-out window gets its final position before
            # its first allocation, so its shells never see an interim grid.
            panel.connect("show", lambda panel, dock: self._size_terminal(
                dock, dock.get_allocation(), panel, shown=True), dock)
        self.sidebar = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.sidebar.pack1(self.explorer, resize=False, shrink=False)
        self.sidebar.pack2(self.dock, resize=True, shrink=False)
        self.sidebar.set_position(300)

        self.titlebar = TitleBar(self._build_menu())
        self.titlebar.connect("settings-requested", lambda _: self.open_settings())
        self.titlebar.connect("dock-toggle-requested", lambda _: self.toggle_terminal_right())
        self.terminal_right.connect("notify::visible", lambda panel, _:
                                    self.titlebar.set_dock_active(panel.get_visible()))
        self.titlebar.set_dock_active(self.terminal_right.get_visible())
        self.set_titlebar(self.titlebar)
        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        layout.pack_start(self.sidebar, True, True, 0)
        self.overlay = OverlayHost(layout)
        self.add(self.overlay)
        self._add_commands()
        self._update_title()
        self.connect("delete-event", self._on_delete)
        self.set_theme(self.theme.id, persist=False)

    def _build_menu(self) -> Gtk.MenuBar:
        bar = Gtk.MenuBar()
        accelerators = Gtk.AccelGroup()
        self.add_accel_group(accelerators)

        def item(menu: Gtk.Menu, group: str, label: str, callback: Callable[[], object],
                 shortcut: str | None = None, hint: tuple[str, ...] = ()) -> Gtk.MenuItem:
            """``shortcut`` is a real accelerator; ``hint`` accelerators are only shown
            (the window's key handler owns them so a focused terminal keeps them)."""
            entry = Gtk.MenuItem(label=label)
            entry.connect("activate", lambda _: callback())
            if shortcut:
                key, modifiers = Gtk.accelerator_parse(shortcut)
                entry.add_accelerator("activate", accelerators, key, modifiers,
                                      Gtk.AccelFlags.VISIBLE)
            elif hint:
                entry.get_child().set_accel(*Gtk.accelerator_parse(hint[0]))
            keys = (shortcut,) if shortcut else hint
            self._commands.append(Command(f"{group}: {label.rstrip('…')}", callback,
                                          tuple(accel_text(key) for key in keys)))
            menu.append(entry)
            return entry

        file_menu = Gtk.Menu()
        view_menu = Gtk.Menu()
        for label, menu in (("File", file_menu), ("View", view_menu)):
            entry = Gtk.MenuItem(label=label)
            entry.set_submenu(menu)
            bar.append(entry)
        item(file_menu, "File", "Quick Switcher…", self.open_quick_switcher,
             hint=("<Control>p", "<Control>o"))
        item(file_menu, "File", "Open Folder…", self.open_folder)
        item(file_menu, "File", "Save", lambda: self.editor.save_current(), "<Control>s")
        item(file_menu, "File", "Save All", lambda: self.editor.save_all(), "<Control><Shift>s")
        item(file_menu, "File", "Quit", self.close, "<Control>q")
        item(view_menu, "View", "Command Palette…", self.open_command_palette,
             "<Control><Shift>p")
        item(view_menu, "View", "Terminal Below", self.toggle_terminal_bottom, "<Control>grave")
        item(view_menu, "View", "Split Terminal", self.split_terminal, "<Control><Shift>5")
        item(view_menu, "View", "Toggle Terminal Fullscreen", self.toggle_terminal_fullscreen,
             "<Control><Shift>F11")
        item(view_menu, "View", "Toggle Explorer Sidebar", self.toggle_explorer, "<Control>b")
        item(view_menu, "View", "Search in Files", self.search_in_files, "<Control><Shift>f")
        item(view_menu, "Graph view", "Graph view", self.open_graph, "<Control><Alt>g")
        self._commands[-1] = Command("Graph view: Open graph view", self.open_graph, ("Ctrl+Alt+G",))
        item(view_menu, "Graph view", "Local graph", self.open_local_graph,
             "<Control><Shift><Alt>g")
        self._commands[-1] = Command("Graph view: Open local graph", self.open_local_graph,
                                     ("Ctrl+Shift+Alt+G",))
        item(view_menu, "Git graph", "Git graph", self.open_gitgraph, "<Control><Alt>h")
        self._commands[-1] = Command("Git graph: Open git graph", self.open_gitgraph,
                                     ("Ctrl+Alt+H",))
        key, modifiers = Gtk.accelerator_parse("<Control><Alt>grave")
        accelerators.connect(key, modifiers, Gtk.AccelFlags.VISIBLE,
                             lambda *_: (self.toggle_terminal_right(), True)[1])
        key, modifiers = Gtk.accelerator_parse("<Control>comma")
        accelerators.connect(key, modifiers, Gtk.AccelFlags.VISIBLE,
                             lambda *_: (self.open_settings(), True)[1])
        key, modifiers = Gtk.accelerator_parse("<Control><Alt>t")
        accelerators.connect(key, modifiers, Gtk.AccelFlags.VISIBLE,
                             lambda *_: (self.next_theme(), True)[1])
        return bar

    def _add_commands(self) -> None:
        """Palette-only commands (after the menu actions, in this order)."""
        for label, callback, keys in (
            ("View: Terminal on Right", self.toggle_terminal_right, ("Ctrl+Alt+`",)),
            ("Settings: Open settings", self.open_settings, ("Ctrl+,",)),
            ("Settings: Show hotkeys", lambda: self.open_settings("hotkeys"), ("F1",)),
            ("Graph view: Rebuild graph", self.rebuild_graph, ()),
            ("Graph view: Reset camera", self.reset_graph_camera, ()),
            ("Git graph: Refresh", self.refresh_gitgraph, ()),
            ("Git graph: Jump to HEAD", self.focus_git_head, ()),
            ("Git graph: Close commit tabs", self.close_commit_tabs, ()),
            ("Theme: Next theme", self.next_theme, ("Ctrl+Alt+T",)),
            ("Editor: Toggle Markdown preview", self.toggle_markdown_view, ("Ctrl+Shift+V",)),
            ("Editor: Zoom in", lambda: self._editor_zoom(1), ("Ctrl+=",)),
            ("Editor: Zoom out", lambda: self._editor_zoom(-1), ("Ctrl+-",)),
            ("Editor: Reset zoom",
             self._editor_zoom, ("Ctrl+0",)),
        ):
            self._commands.append(Command(label, callback, keys))

    def commands(self) -> list[Command]:
        """Every command-palette entry (menu actions first) with its hotkey labels."""
        return list(self._commands)

    def hotkeys(self) -> list[tuple[str, str]]:
        """(label, accelerator) pairs for the settings Hotkeys page: the commands,
        then the panel-local shortcuts not already listed."""
        pairs = [(command.label, " / ".join(command.hotkey))
                 for command in self._commands if command.hotkey]
        used = {key for _label, keys in pairs for key in keys.split(" / ")}
        return pairs + [(label, keys) for label, keys in DEFAULT_HOTKEYS
                        if not set(keys.split(" / ")) & used]

    # Overlay prompts -----------------------------------------------------------

    def _project_files(self) -> list[str]:
        """Files under the root (search ignore list; hidden ones per the setting)."""
        hidden = self._values["show_hidden"]
        files: list[str] = []
        for top, dirs, names in os.walk(self.root):
            dirs[:] = sorted(name for name in dirs if name not in SKIP_DIRS
                             and (hidden or not name.startswith(".")))
            for name in sorted(names):
                if hidden or not name.startswith("."):
                    files.append(os.path.join(top, name))
                    if len(files) >= MAX_SWITCHER_FILES:
                        return files
        return files

    def _switcher_recent(self) -> list[str]:
        """Recently active files first, then the other open tabs."""
        open_paths = [tab.path for tab in self.editor.tabs if tab.path]
        return self._recent_files + [path for path in open_paths
                                     if path not in self._recent_files]

    def _current_file_changed(self, _editor: EditorPanel, path: str) -> None:
        self.explorer.set_current_file(path or None)
        self.explorer.refresh_git()
        for tab in self.editor.tabs:
            if not getattr(tab, '_explorer_git_connected', False):
                tab._explorer_git_connected = True
                tab.buffer.connect('modified-changed', lambda buffer:
                                   self.explorer.refresh_git() if not buffer.get_modified() else None)
        for view in self._graph_views():
            view.set_active_file(path or None)
        for tab in self.editor.tabs:
            if not getattr(tab, '_graph_connected', False):
                tab._graph_connected = True
                self._graph_tabs[tab.page] = tab
                tab.buffer.connect('changed', lambda _buffer, tab=tab: self._graph_text_changed(tab))
                tab.buffer.connect('modified-changed', lambda buffer, tab=tab:
                                   self._graph_saved(tab) if not buffer.get_modified() else None)
        if path:
            if path in self._recent_files:
                self._recent_files.remove(path)
            self._recent_files.insert(0, path)
            del self._recent_files[RECENT_FILES:]

    def open_quick_switcher(self) -> None:
        """Show the file switcher (recent files first on an empty query)."""
        if self._switcher is None:
            # Every file already opens in its own tab: no "open in new tab" variant.
            self._switcher = QuickSwitcher(
                self._project_files, root=self.root, recent_provider=self._switcher_recent,
                instructions=(("↑↓", "to navigate"), ("↵", "to open"), ("esc", "to dismiss")))
            self._switcher.connect("chosen",
                                   lambda _, item, _mods: self.editor.open_file(item.data))
        self._switcher.root = self.root
        self._restore_terminal_fullscreen()
        self.overlay.show_prompt(self._switcher)

    def open_command_palette(self) -> None:
        """Show the command palette (recently run commands first)."""
        if self._palette is None:
            self._palette = CommandPalette(self.commands)
        self.overlay.show_prompt(self._palette)

    def notify(self, text: str, level: str = "info") -> None:
        self.overlay.notify(text, level=level)

    def _notify_unsaved_settings(self) -> None:
        self.notify(f"Could not save settings to {settings.settings_path()}", "error")

    def toggle_markdown_view(self) -> None:
        mode = self.editor.markdown_mode()
        if mode is not None:
            self.editor.set_markdown_mode("preview" if mode == "source" else "source")

    # Graph panes ---------------------------------------------------------------

    def _graph_views(self):
        return [view for panel in (self.terminal_bottom, self.terminal_right)
                for view in panel.graph_views()]

    def _make_graph_view(self):
        view = GraphView(self.root, settings=settings.get_graph())
        view.connect('open-file', self._graph_open_file)
        view.connect('settings-changed', lambda _view, values: settings.set_graph(values))
        view.connect('page-action', self._graph_action)
        view.connect('error', self._graph_error)
        view.set_theme(self.theme.ui, self._values['accent'] or self.theme.ui['accent'])
        view.post('setSettings', {'reduceMotion': self._values['reduce_motion']})
        view.set_active_file(self.editor.current_path())
        # Include edits made before the graph was opened.
        for tab in self.editor.tabs:
            if tab.buffer.get_modified() and not tab.deleted and self._graph_indexed(tab.path):
                view.set_text(tab.path, self.editor.buffer_text(tab.path))
        return view

    def _graph_theme(self):
        for view in self._graph_views():
            view.set_theme(self.theme.ui, self._values['accent'] or self.theme.ui['accent'])
        self._git_theme()

    def _gitgraph_views(self):
        return [view for panel in (self.terminal_bottom, self.terminal_right)
                for view in panel.gitgraph_views()]

    def _git_theme_ui(self):
        return {**self.theme.ui, 'ui_font': self._values['ui_font'],
                'editor_font': self._values['editor_font']}

    def _theme_git_view(self, view):
        ui, accent = self._git_theme_ui(), self._values['accent'] or self.theme.ui['accent']
        if isinstance(view, CommitView):
            view.set_theme(ui, accent, self.editor.syntax_style)
            view.set_font_size(self.editor.font_size)
        else:
            view.set_theme(ui, accent)
        view.post('setSettings', {'reduceMotion': self._values['reduce_motion']})

    def _git_theme(self):
        for view in [*self._gitgraph_views(), *self._commit_views.values()]:
            self._theme_git_view(view)

    def _selected_commit(self):
        key = self.editor.current_view_key()
        return key[7:] if key in self._commit_views else None

    def _make_gitgraph_view(self):
        view = GitGraphView(self.root)
        view.connect('open-commit', lambda source, rev: self.open_commit(rev, source))
        view.connect('error', self._git_error)
        self._theme_git_view(view)
        view.select(self._selected_commit())
        return view

    def open_gitgraph(self):
        """Reuse any Git graph, otherwise prefer the wide bottom dock."""
        self._restore_terminal_fullscreen()
        panels = (self.terminal_bottom, self.terminal_right)
        panel = next((p for p in panels if p.has_gitgraph()), None)
        if panel is None:
            panel = next((p for p in panels if not p.is_full()), None)
            if panel is None:
                if not self._git_full_notice:
                    self.notify('Both docks are full. Close a pane to open Git graph.')
                    self._git_full_notice = True
                return None
        self._git_full_notice = False
        panel.set_no_show_all(False)
        panel.show_all()
        panel.add_gitgraph()
        view = panel.gitgraph_views()[0]
        view.grab_focus()
        return view

    def refresh_gitgraph(self):
        for view in self._gitgraph_views():
            view.refresh()

    def focus_git_head(self):
        view = self.open_gitgraph()
        if view is not None:
            view.focus_head()

    def _git_error(self, _view, message):
        if message in self._git_errors:
            return
        print('Git graph: ' + message, file=sys.stderr)
        if not self._git_errors:
            self.notify('Git view encountered an error. See the application log.')
        self._git_errors.add(message)

    def open_commit(self, rev, source_view=None):
        """Open a hex object name; resolve abbreviated names off the GTK thread."""
        if self._git_closed:
            return None
        if not isinstance(rev, str) or not re.fullmatch(r'[0-9a-fA-F]{4,40}', rev):
            self.notify('Invalid commit hash')
            return None
        rev = rev.lower()
        views = self._gitgraph_views()
        if source_view is not None:
            views = [source_view, *views]
        commits = {c['hash']: c for view in views for c in (view.last_data or {}).get('commits', [])}
        matches = [sha for sha in commits if sha.startswith(rev)]
        if len(rev) < 40 and len(matches) == 1:
            rev = matches[0]
        if len(rev) == 40:
            return self._open_full_commit(rev, commits.get(rev, {}).get('subject', ''))
        request = (self._git_epoch, self.root, rev)
        if request in self._commit_pending:
            return None
        self._commit_pending.add(request)
        if self._commit_executor is None:
            self._commit_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='idgent-commit-resolve')
        def resolve():
            try:
                raw, _ = gitlog._run(request[1], ['rev-parse', '--verify', '--end-of-options',
                                                  rev + '^{commit}'], time.monotonic() + 5, cap=256)
                full = raw.decode('ascii').strip()
                return full if re.fullmatch(r'[0-9a-f]{40}', full) else None
            except (gitlog.GitError, UnicodeError):
                return None
        def deliver(full):
            source = GLib.main_current_source()
            with self._git_lock:
                self._git_sources.discard(source.get_id())
            self._commit_pending.discard(request)
            if not self._git_closed and request[:2] == (self._git_epoch, self.root):
                if full is None:
                    self.notify('Commit not found')
                else:
                    self._open_full_commit(full)
            return GLib.SOURCE_REMOVE
        def done(future):
            if future.cancelled():
                return
            with self._git_lock:
                if not self._git_closed:
                    self._git_sources.add(GLib.idle_add(deliver, future.result()))
        self._commit_executor.submit(resolve).add_done_callback(done)
        return None

    def _open_full_commit(self, full_hash, subject=''):
        key = 'commit:' + full_hash
        view = self.editor.view_tab_widget(key)
        if view is None:
            view = CommitView(self.root, full_hash)
            self._commit_views[key] = view
            self._theme_git_view(view)
            view.connect('open-commit', lambda source, rev: self.open_commit(rev, source))
            view.connect('open-file', self._commit_open_file)
            view.connect('error', self._git_error)
        icons = Gtk.IconTheme.get_default()
        icon = next((name for name in ('git-commit-symbolic', 'vcs-commit-symbolic',
                                      'document-open-recent-symbolic', 'text-x-generic-symbolic')
                     if icons.has_icon(name)), None)
        self._restore_terminal_fullscreen()
        self.editor.add_view_tab(key, full_hash[:7], view, tooltip=subject or full_hash,
                                 icon_name=icon)
        self._git_selection_changed()
        return view

    def _commit_open_file(self, _view, path, line):
        if os.path.isabs(path) and os.path.isfile(path):
            self.editor.open_file_at(path, max(0, line - 1))
        else:
            self.notify('File not in the working tree')

    def _git_selection_changed(self, *_args):
        selected = self._selected_commit()
        for view in self._gitgraph_views():
            view.select(selected)
        self._git_font_size()

    def _git_font_size(self):
        for view in self._commit_views.values():
            if (view._theme or {}).get('size') != self.editor.font_size:
                view.set_font_size(self.editor.font_size)

    def _editor_zoom(self, step=None):
        if step is None:
            self.editor.set_font_size(self.editor.default_font_size)
        else:
            self.editor.zoom(step)
        self._git_font_size()

    def _commit_tab_closed(self, _editor, key):
        self._commit_views.pop(key, None)
        self._git_selection_changed()

    def close_commit_tabs(self):
        self._git_epoch += 1
        self._commit_pending.clear()
        for key in list(self._commit_views):
            self.editor.close_view_tab(key)

    def _git_cleanup(self, _window):
        with self._git_lock:
            self._git_closed = True
            sources, self._git_sources = self._git_sources, set()
        for source in sources:
            GLib.source_remove(source)
        if self._commit_executor is not None:
            self._commit_executor.shutdown(wait=True, cancel_futures=True)
        self.close_commit_tabs()
        for view in self._gitgraph_views():
            view.shutdown()

    def open_graph(self):
        self._restore_terminal_fullscreen()
        panel = self.terminal_right
        panel.set_no_show_all(False)
        panel.show_all()
        panel.add_graph()
        if not panel.has_graph():
            return None
        view = panel.graph_views()[0]
        view.show_global()
        view.grab_focus()
        return view

    def open_local_graph(self):
        path = self.editor.current_path()
        if not path:
            self.notify('Open a file to show its local graph.')
            return None
        view = self.open_graph()
        if view is not None:
            view.show_local(path, 1)
        return view

    def rebuild_graph(self):
        for view in self._graph_views():
            view.refresh_file(self.root)

    def reset_graph_camera(self):
        for view in self._graph_views():
            if view.last_data is not None:
                # The page recentres a full replacement; clear first to reset positions.
                view.post('setData', {'nodes': [], 'links': []})
                view.post('setSettings', {'scale': 1})
                view.post('setData', view.last_data)

    def _graph_open_file(self, _view, path, line=1):
        if not os.path.isfile(path):
            self.notify('Cannot open graph target: ' + path)
            return
        self._restore_terminal_fullscreen()
        self.editor.open_file_at(path, max(0, line - 1))
        self.explorer.set_current_file(path)
        self.editor.focus_current()

    def _graph_action(self, view, message):
        kind = message['type']
        if kind == 'search' and isinstance(message.get('query'), str):
            self.search_in_files()
            self.explorer.focus_search(message['query'])
        elif kind == 'create':
            name = message.get('name')
            if not isinstance(name, str) or not name.strip() or '/' in name or '\\' in name or '..' in name:
                self.notify('Graph note names must be simple file names.')
                return
            name = name if name.lower().endswith('.md') else name + '.md'
            try:
                path = fsops.create_file(self.root, name)
            except fsops.FsOpError as error:
                self.notify(str(error))
                return
            self.explorer.refresh()
            self._graph_paths_changed(None, path)
            self._graph_open_file(view, path)
        elif kind == 'context' and isinstance(message.get('path'), str):
            path = os.path.abspath(os.path.join(self.root, message['path']))
            if os.path.commonpath((self.root, path)) != self.root:
                return
            action = message.get('action')
            if action == 'copy-path':
                Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(path, -1)
            elif action == 'reveal':
                self._restore_terminal_fullscreen()
                if not self.explorer.get_visible():
                    self._toggle(self.explorer)
                self.explorer.set_current_file(path)
            elif action in ('open-new-tab', 'open-right'):
                self._graph_open_file(view, path)

    def _graph_error(self, _view, message):
        if message in self._graph_errors:
            return
        print('Graph view: ' + message, file=sys.stderr)
        if not self._graph_errors:
            self.notify('Graph view encountered an error. See the application log.')
        self._graph_errors.add(message)

    @staticmethod
    def _graph_indexed(path):
        return path and Path(path).suffix.lower() in (
            set(SEMANTICS['note_extensions']) | SEMANTICS['code_extensions'])

    def _graph_text_changed(self, tab):
        if tab in self._graph_timers:
            GLib.source_remove(self._graph_timers.pop(tab))
        if self._graph_views() and not tab.deleted and self._graph_indexed(tab.path):
            self._graph_timers[tab] = GLib.timeout_add(400, self._graph_send_text, tab)

    def _graph_send_text(self, tab):
        self._graph_timers.pop(tab, None)
        if tab in self.editor.tabs and not tab.deleted:
            text = self.editor.buffer_text(tab.path)
            for view in self._graph_views():
                view.set_text(tab.path, text)
        return GLib.SOURCE_REMOVE

    def _graph_saved(self, tab):
        if tab in self._graph_timers:
            GLib.source_remove(self._graph_timers.pop(tab))
        if tab.path and not tab.deleted:
            self._graph_paths_changed(None, tab.path)

    def _graph_tab_closed(self, _notebook, page, _number):
        tab = self._graph_tabs.pop(page, None)
        if tab is not None:
            self._graph_saved(tab)

    def _graph_paths_changed(self, _explorer, *paths):
        for tab in self.editor.tabs:
            if tab.deleted and tab in self._graph_timers:
                GLib.source_remove(self._graph_timers.pop(tab))
        for view in self._graph_views():
            for path in paths:
                view.refresh_file(path)
            # Directory removals/renames need to remove old descendants as well.
            view.refresh_file(self.root)
            for tab in self.editor.tabs:
                if tab.buffer.get_modified() and not tab.deleted and self._graph_indexed(tab.path):
                    view.set_text(tab.path, self.editor.buffer_text(tab.path))

    def _graph_tree_changed(self, *_args):
        if self._graph_views() and self._graph_refresh_timer is None:
            self._graph_refresh_timer = GLib.timeout_add(80, self._graph_tree_refresh)

    def _graph_tree_refresh(self):
        self._graph_refresh_timer = None
        self.rebuild_graph()
        return GLib.SOURCE_REMOVE

    def _graph_cleanup(self, _window):
        for source in self._graph_timers.values():
            GLib.source_remove(source)
        self._graph_timers.clear()
        if self._graph_refresh_timer is not None:
            GLib.source_remove(self._graph_refresh_timer)
            self._graph_refresh_timer = None

    # Settings -------------------------------------------------------------------

    def open_settings(self, page: str | None = None) -> None:
        """Present the window's reusable, non-modal live settings dialog."""
        if self._settings_dialog is None:
            self._settings_dialog = SettingsDialog(
                parent=self, theme_id=self.theme.id, sizes=self._font_sizes)
            self._settings_dialog.connect("theme-selected",
                                          lambda _, theme_id: self.set_theme(theme_id))
            self._settings_dialog.connect("font-size-changed", self._font_size_changed)
            self._settings_dialog.connect("setting-changed", self._setting_changed)
        dialog = self._settings_dialog
        # Live values: they win over the file when a save failed.
        dialog.set_values({**settings.get_values(), "theme": self.theme.id,
                           **self._font_sizes, **self._values})
        dialog.set_hotkeys(self.hotkeys())
        dialog.show_all()
        if page is not None:
            dialog.nav_list.select_page(page)
            dialog.set_focus(dialog.nav_list.rows[page])
        dialog.present()

    def _font_size_changed(self, _dialog, key: str, size: int) -> None:
        self._font_sizes[key] = size
        if key == "terminal_font_size":
            for panel in (self.terminal_bottom, self.terminal_right):
                panel.set_font_size(size)
        elif key == "editor_font_size":
            self.editor.set_font_size(size)
            self.editor.set_default_font_size(size)
            self._git_theme()
        elif key == "explorer_font_size":
            self.explorer.set_font_size(size)
        if not settings.set_font_size(key, size):
            self._notify_unsaved_settings()

    def _setting_changed(self, _dialog, key: str, value: object) -> None:
        """Apply a key the dialog already validated and persisted."""
        self._values[key] = value
        self._apply_setting(key)
        if key == "terminal_autostart":
            self.notify("The start-up terminal setting applies from the next start.")
        if settings.get_value(key) != value:
            self._notify_unsaved_settings()

    def _apply_setting(self, key: str) -> None:
        values = self._values
        value = values[key]
        terminals = (self.terminal_bottom, self.terminal_right)
        if key in ("accent", "ui_font", "editor_font"):
            self._apply_style()
            if key == "accent":
                self._graph_theme()
            else:
                self._git_theme()
            if key == "editor_font":
                # Re-measure gutter width and tab stops for the new family.
                for tab in self.editor.tabs:
                    tab.set_font_size(self.editor.font_size)
        elif key == "reduce_motion":
            motion.set_reduce_motion(value)
            for view in self._graph_views():
                view.post("setSettings", {"reduceMotion": value})
            for view in [*self._gitgraph_views(), *self._commit_views.values()]:
                view.post("setSettings", {"reduceMotion": value})
        elif key == "line_numbers":
            self.editor.set_line_numbers(value)
        elif key == "highlight_line":
            self.editor.set_highlight_line(value)
        elif key in ("indent_tabs", "indent_width"):
            self.editor.set_indent(values["indent_tabs"], values["indent_width"])
        elif key == "auto_pair":
            self.editor.set_auto_pair(value)
        elif key == "md_default_preview":
            self.editor.set_md_default_preview(value)
        elif key == "md_readable_width":
            self.editor.set_md_readable_width(value)
        elif key == "show_hidden":
            self.explorer.set_show_hidden(value)
        elif key == "confirm_delete":
            self.explorer.set_confirm_delete(value)
        elif key == "delete_mode":
            self.explorer.set_delete_mode(_DELETE_MODES[value])
        elif key == "terminal_cursor":
            for panel in terminals:
                panel.set_cursor_shape(value)
        elif key == "terminal_blink":
            for panel in terminals:
                panel.set_cursor_blink(value)
        elif key == "terminal_scrollback":
            for panel in terminals:
                panel.set_scrollback(value)
        # terminal_autostart is read once, when the window is built.

    def _apply_style(self, theme: Theme | None = None) -> None:
        style.apply_theme(self.theme if theme is None else theme, accent=self._values["accent"],
                          ui_font=self._values["ui_font"],
                          editor_font=self._values["editor_font"])

    def set_theme(self, theme_id: str, persist: bool = True) -> None:
        """Recolour existing widgets and defaults without restarting any shells."""
        theme = get_theme(theme_id)
        self._apply_style(theme)
        self.editor.set_syntax_style(theme.syntax)
        self.editor.set_chrome(theme.chrome)
        colors = theme.terminal
        for panel in (self.terminal_bottom, self.terminal_right):
            panel.apply_palette(colors.foreground, colors.background, colors.ansi, colors.cursor)
        self.theme = theme
        self._graph_theme()
        if self._settings_dialog is not None:
            self._settings_dialog.set_theme_id(theme.id)
        if persist and not settings.set_theme_id(theme.id):
            self._notify_unsaved_settings()

    def next_theme(self) -> None:
        index = next(i for i, theme in enumerate(THEMES) if theme.id == self.theme.id)
        self.set_theme(THEMES[(index + 1) % len(THEMES)].id)

    def _size_terminal(self, dock: Gtk.Paned, allocation: Gdk.Rectangle,
                       panel: TerminalPanel, shown: bool = False) -> None:
        if (self._fullscreen_panel is not None or not self._size_pending[panel]
                or not panel.get_visible()):
            return
        right = panel is self.terminal_right
        extent = allocation.width if right else allocation.height
        if extent <= 1:
            return
        if shown:
            # Not allocated yet: the handle is not measurable from the children.
            handle = dock.style_get_property("handle-size")
        else:
            first, second = dock.get_child1().get_allocation(), panel.get_allocation()
            handle = max(0, extent - (first.width + second.width if right
                                      else first.height + second.height))
        available = extent - handle
        if not shown and self._size_pending[panel] == ("shown", extent):
            self._size_pending[panel] = False
            return
        size = (min(max(round(extent * .4), 380), int(extent * .6), available - 300)
                if right else min(max(round(extent * .3), 150), available - 150))
        # A show-time size is confirmed by the first allocation at the same extent.
        self._size_pending[panel] = ("shown", extent) if shown else False
        # The initial allocation may precede this queued divider move by a
        # frame. New shells must wait until the panel receives this extent.
        panel._initial_dock_extent = (right, max(0, size))
        dock.set_position(max(0, available - max(0, size)))

    def _restore_terminal_fullscreen(self) -> None:
        if self._fullscreen_panel is not None:
            self._fullscreen_panel.restore()

    def _terminal_maximize_toggled(self, panel: TerminalPanel, maximized: bool) -> None:
        if not maximized:
            if panel is not self._fullscreen_panel:
                return
            widgets, positions, pending = self._fullscreen_layout
            self._fullscreen_panel = self._fullscreen_layout = None
            for widget, visible, no_show_all in widgets:
                widget.set_no_show_all(no_show_all)
                widget.set_visible(visible)
            for paned, position in positions:
                paned.set_position(position)
            self._size_pending = pending
            return
        self._restore_terminal_fullscreen()
        widgets = (self.explorer, self.editor, self.dock_bottom,
                   self.terminal_bottom, self.terminal_right)
        self._fullscreen_layout = (
            [(widget, widget.get_visible(), widget.get_no_show_all()) for widget in widgets],
            [(paned, paned.get_position()) for paned in
             (self.sidebar, self.dock, self.dock_bottom)],
            self._size_pending.copy(),
        )
        self._fullscreen_panel = panel
        hidden = ((self.explorer, self.editor, self.terminal_right)
                  if panel is self.terminal_bottom else (self.explorer, self.dock_bottom))
        for widget in hidden:
            widget.set_no_show_all(True)
            widget.hide()

    def toggle_terminal_fullscreen(self) -> None:
        if self._fullscreen_panel is not None:
            self._restore_terminal_fullscreen()
            return
        focus = self.get_focus()
        panels = (self.terminal_bottom, self.terminal_right)
        panel = next((panel for panel in panels if panel.get_visible()
                      and focus is not None and focus.is_ancestor(panel)), None)
        if panel is None:
            panel = next((panel for panel in panels if panel.get_visible()), None)
        if panel is None:
            panel = self.terminal_bottom
            self._show_terminal(panel)
        panel.toggle_maximize()

    def _hide_terminal(self, panel: TerminalPanel) -> None:
        self._restore_terminal_fullscreen()
        panel.set_no_show_all(True)
        panel.hide()

    def _show_terminal(self, panel: TerminalPanel) -> None:
        panel.set_no_show_all(False)
        panel.show_all()
        if panel.pane_count() == 0:
            panel.new_terminal()
        panel.focus_terminal()

    def _toggle_terminal(self, panel: TerminalPanel) -> None:
        self._restore_terminal_fullscreen()
        if panel.get_visible():
            self._hide_terminal(panel)
        else:
            self._show_terminal(panel)

    def toggle_terminal_bottom(self) -> None:
        self._toggle_terminal(self.terminal_bottom)

    def toggle_terminal_right(self) -> None:
        self._toggle_terminal(self.terminal_right)

    def split_terminal(self) -> None:
        focus = self.get_focus()
        panel = (self.terminal_right if focus is not None
                 and focus.is_ancestor(self.terminal_right) else self.terminal_bottom)
        self._restore_terminal_fullscreen()
        # Opening an empty dock already creates its initial terminal.
        empty = panel.pane_count() == 0
        self._show_terminal(panel)
        if not empty:
            panel.new_terminal()

    @staticmethod
    def _toggle(widget: Gtk.Widget) -> None:
        visible = not widget.get_visible()
        widget.set_no_show_all(not visible)
        if visible:
            widget.show_all()
        else:
            widget.hide()

    def toggle_explorer(self) -> None:
        self._restore_terminal_fullscreen()
        self._toggle(self.explorer)

    def search_in_files(self) -> None:
        """Show the explorer and focus its search box, seeded from a one-line selection."""
        self._restore_terminal_fullscreen()
        if not self.explorer.get_visible():
            self._toggle(self.explorer)
        seed = self.editor.selected_text()
        if not seed.strip() or "\n" in seed or "\r" in seed or len(seed) > 200:
            seed = ""
        self.explorer.focus_search(seed)

    def _search_escaped(self, _explorer: ExplorerPanel) -> None:
        if not self.editor.focus_current():
            self.explorer.focus_tree()

    def _update_title(self) -> None:
        self.set_title("IDgent")

    def set_root(self, path: str) -> None:
        self._restore_terminal_fullscreen()
        root = Path(path).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"not a directory: {path}")
        self.close_commit_tabs()
        self.root = str(root)
        self.explorer.set_root(self.root)
        self.terminal_bottom.set_cwd(self.root)
        self.terminal_right.set_cwd(self.root)
        for view in self._graph_views():
            view.set_root(self.root)
            view.set_active_file(self.editor.current_path())
        for view in self._gitgraph_views():
            view.set_root(self.root)
        self._update_title()

    def open_folder(self) -> None:
        self._restore_terminal_fullscreen()
        dialog = Gtk.FileChooserDialog(title="Open Folder", transient_for=self,
                                       action=Gtk.FileChooserAction.SELECT_FOLDER)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Open", Gtk.ResponseType.OK)
        dialog.set_current_folder(self.root)
        try:
            if dialog.run() == Gtk.ResponseType.OK:
                path = dialog.get_filename()
                if path is not None:
                    self.set_root(path)
        finally:
            dialog.destroy()

    def _terminal_focused(self) -> bool:
        focus = self.get_focus()
        # A closing VTE can retain focus after leaving the panel's live pane list.
        return isinstance(focus, Vte.Terminal)

    def do_key_press_event(self, event: Gdk.EventKey) -> bool:
        focus = self.get_focus()
        modifiers = event.state & Gtk.accelerator_get_default_mod_mask()
        if (modifiers == _CTRL | Gdk.ModifierType.MOD1_MASK
                and event.keyval in (Gdk.KEY_h, Gdk.KEY_H)):
            self.open_gitgraph()
            return True
        if modifiers == _CTRL_SHIFT and event.keyval in (Gdk.KEY_p, Gdk.KEY_P):
            self.open_command_palette()
            return True
        if modifiers == _CTRL and event.keyval == Gdk.KEY_comma:
            self.open_settings()
            return True
        # Ctrl+P/Ctrl+O/F1 stay with a focused terminal (readline, vim, mc, htop).
        if not self._terminal_focused():
            if modifiers == _CTRL and event.keyval in (Gdk.KEY_p, Gdk.KEY_o):
                self.open_quick_switcher()
                return True
            if not modifiers and event.keyval == Gdk.KEY_F1:
                self.open_settings("hotkeys")
                return True
        # Handle before GTK/editor bindings can consume the menu shortcut.
        if (modifiers == Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK
                and event.keyval == Gdk.KEY_F11):
            self.toggle_terminal_fullscreen()
            return True
        if (modifiers == Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK
                and event.keyval in (Gdk.KEY_f, Gdk.KEY_F)):
            self.search_in_files()
            return True
        if (modifiers == Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK
                and event.keyval in (Gdk.KEY_5, Gdk.KEY_percent)):
            self.split_terminal()
            return True
        if (modifiers == Gdk.ModifierType.CONTROL_MASK
                and event.keyval == Gdk.KEY_s):
            if self._terminal_focused():
                # Preserve the terminal's XOFF binding, bypassing the Save accelerator.
                return self.propagate_key_event(event)
            # A TextView's input method can consume this key without saving.
            # Handle it once here, before either propagation or menu activation.
            self.editor.save_current()
            return True
        if (focus is not None and focus.is_ancestor(self.editor)
                and modifiers == Gdk.ModifierType.CONTROL_MASK
                and event.keyval in (Gdk.KEY_w, Gdk.KEY_slash)):
            # Let the focused editor handle its commands before menu accelerators.
            if self.propagate_key_event(event):
                return True
        handled = Gtk.ApplicationWindow.do_key_press_event(self, event)
        self._git_font_size()
        return handled

    def _ask_quit(self) -> int:
        dialog = Gtk.MessageDialog(transient_for=self, modal=True,
                                   message_type=Gtk.MessageType.WARNING,
                                   text="Save changes before quitting?")
        dialog.format_secondary_text("Unsaved changes will be lost if you discard them.")
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL,
                           "Discard", Gtk.ResponseType.REJECT,
                           "Save All", Gtk.ResponseType.ACCEPT)
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        try:
            response = dialog.run()
        finally:
            dialog.destroy()
        return response

    def _on_delete(self, _window: Gtk.Window, _event: object) -> bool:
        if not self.editor.has_unsaved():
            return False
        response = self._ask_quit()
        if response == Gtk.ResponseType.REJECT:
            return False
        if response == Gtk.ResponseType.ACCEPT:
            return not self.editor.save_all() or self.editor.has_unsaved()
        return True
