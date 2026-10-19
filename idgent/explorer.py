"""Lazy filesystem explorer with theme-driven navigation and removal preferences."""

import os
import threading
from collections.abc import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Pango

from . import fsops, gitstatus
from .themes import get_theme
from .search import SearchPanel


class ExplorerPanel(Gtk.Box):
    __gsignals__ = {
        "file-activated": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        "path-renamed": (GObject.SignalFlags.RUN_LAST, None, (str, str)),
        "path-deleted": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        # absolute path, zero-based line and column, match length in characters
        "search-result-activated": (GObject.SignalFlags.RUN_LAST, None, (str, int, int, int)),
        "search-escaped": (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(self, root: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.get_style_context().add_class("idgent-explorer")
        self.root = ""
        self.show_hidden = True
        self.confirm_delete = True
        self.delete_mode = "delete"
        self._git_enabled = True
        self._git_snapshot = gitstatus.Snapshot()
        self._git_generation = 0
        self._git_timeout = 0
        self._git_running = False
        self._git_closed = False
        self._git_lock = threading.Lock()
        self._git_idle = 0
        self._git_monitor = None
        self._git_dir = None
        self._file_monitor = None
        self.connect("destroy", self._stop_git)
        self._loaded: set[str] = set()
        self.store = Gtk.TreeStore(str, str, str)
        self.tree = Gtk.TreeView(model=self.store)
        self.tree.set_headers_visible(False)
        self.tree.set_grid_lines(Gtk.TreeViewGridLines.NONE)
        self.tree.set_margin_start(12)
        self.tree.set_margin_end(12)
        self.tree.set_margin_top(4)
        self.tree.set_margin_bottom(24)
        column = Gtk.TreeViewColumn()
        icon = Gtk.CellRendererPixbuf()
        column.pack_start(icon, False)
        # Preserve model icon names (also used to identify directories), but
        # render their system symbolic variants in the row's muted colour.
        column.set_cell_data_func(icon, lambda col, cell, model, row, data:
                                  cell.set_property("icon-name", model[row][0] + "-symbolic"
                                                    if model[row][0] else None))
        label = Gtk.CellRendererText(ellipsize=Pango.EllipsizeMode.END)
        self._text_cell = label
        column.pack_start(label, True)
        column.add_attribute(label, "text", 1)
        column.set_cell_data_func(label, self._git_cell_data)
        self._badge_cell = Gtk.CellRendererText(xalign=1, scale=.85, width_chars=2)
        column.pack_end(self._badge_cell, False)
        column.set_cell_data_func(self._badge_cell, self._git_cell_data)
        self.tree.append_column(column)
        self.tree.connect("row-expanded", self._on_expanded)
        self.tree.connect("row-activated", self._on_activated)
        self.tree.connect("button-press-event", self._on_button)
        self.connect("key-press-event", self._on_key)
        self.tree.connect("popup-menu", self._on_popup)
        toolbar = Gtk.Box()
        toolbar.get_style_context().add_class("idgent-explorer-header")
        toolbar.set_tooltip_text("EXPLORER")
        toolbar.get_accessible().set_name("EXPLORER")
        actions = Gtk.Box(spacing=2, halign=Gtk.Align.CENTER)
        toolbar.pack_start(actions, True, True, 0)
        for name, tooltip, callback in (
            ("document-new-symbolic", "New File", lambda *_: self._new_dialog(False)),
            ("folder-new-symbolic", "New Folder", lambda *_: self._new_dialog(True)),
            ("view-refresh-symbolic", "Refresh", lambda *_: self.refresh()),
        ):
            button = Gtk.Button.new_from_icon_name(name, Gtk.IconSize.MENU)
            button.set_relief(Gtk.ReliefStyle.NONE)
            button.set_tooltip_text(tooltip)
            button.get_accessible().set_name(tooltip)
            button.connect("clicked", callback)
            actions.pack_start(button, False, False, 0)
        self.pack_start(toolbar, False, False, 0)
        self._scroll = Gtk.ScrolledWindow()
        self._scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self._scroll.add(self.tree)
        # The search row sits directly under the header; the tree is its idle content.
        self.search = SearchPanel(self._scroll, root)
        self.search.connect("result-activated", lambda _, *match: self.emit("search-result-activated", *match))
        self.search.connect("escaped", lambda _: self.emit("search-escaped"))
        self.pack_start(self.search, True, True, 0)
        self.set_font_size(10)
        self.set_root(root)

    def _git_cell_data(self, column, cell, model, row, data=None):
        icon, _name, path = model[row][:]
        statuses = self._git_snapshot.folders if icon == "folder" else self._git_snapshot.files
        status = statuses.get(path) if self._git_enabled else None
        if cell is self._badge_cell:
            cell.set_property("text", status.badge if status else "")
        if status:
            role = "git_" + status.kind
            found, color = self.tree.get_style_context().lookup_color("idgent_" + role)
            if not found:
                color = Gdk.RGBA()
                color.parse(get_theme(None).ui[role])
            cell.set_property("foreground-rgba", color)
        cell.set_property("foreground-set", status is not None)

    def refresh_git(self) -> None:
        """Debounce scans, immediately invalidating any in-flight result."""
        if self._git_closed:
            return
        self._git_generation += 1
        if self._git_timeout:
            GLib.source_remove(self._git_timeout)
            self._git_timeout = 0
        if self._git_enabled:
            self._git_timeout = GLib.timeout_add(150, self._start_git)

    def set_git_enabled(self, enabled: bool) -> None:
        self._git_enabled = bool(enabled)
        if not self._git_enabled:
            self._git_snapshot = gitstatus.Snapshot()
            self._set_git_monitor(None)
            self.tree.queue_draw()
        self.refresh_git()

    def _start_git(self):
        self._git_timeout = 0
        if self._git_running or self._git_closed:
            return GLib.SOURCE_REMOVE
        self._git_running = True
        generation, root = self._git_generation, self.root

        def work():
            snapshot = gitstatus.collect_status(root)
            with self._git_lock:
                if not self._git_closed:
                    self._git_idle = GLib.idle_add(self._deliver_git, generation, snapshot)

        threading.Thread(target=work, name="idgent-explorer-git", daemon=True).start()
        return GLib.SOURCE_REMOVE

    def _deliver_git(self, generation, snapshot):
        with self._git_lock:
            self._git_idle = 0
            if self._git_closed:
                return GLib.SOURCE_REMOVE
        self._git_running = False
        if generation == self._git_generation and self._git_enabled:
            self._git_snapshot = snapshot
            self._set_git_monitor(snapshot.git_dir)
            self.tree.queue_draw()
        elif self._git_enabled and not self._git_timeout:
            self._git_timeout = GLib.timeout_add(150, self._start_git)
        return GLib.SOURCE_REMOVE

    def _set_git_monitor(self, directory):
        if directory == self._git_dir:
            return
        if self._git_monitor:
            self._git_monitor.cancel()
            self._git_monitor = None
        self._git_dir = directory
        if directory:
            try:
                self._git_monitor = Gio.File.new_for_path(directory).monitor_directory(
                    Gio.FileMonitorFlags.WATCH_MOVES, None)
                self._git_monitor.connect("changed", self._git_metadata_changed)
            except GLib.Error:
                pass

    def _git_metadata_changed(self, monitor, file, other, event):
        if any(item and item.get_basename() in ("index", "HEAD", "packed-refs")
               for item in (file, other)):
            self.refresh_git()

    def _watch_current_file(self, path):
        if self._file_monitor:
            self._file_monitor.cancel()
            self._file_monitor = None
        if path:
            # Watch the parent so an editor's atomic replacement keeps working.
            path = os.path.abspath(path)
            try:
                self._file_monitor = Gio.File.new_for_path(os.path.dirname(path)).monitor_directory(
                    Gio.FileMonitorFlags.WATCH_MOVES, None)
                self._file_monitor.connect("changed", lambda monitor, file, other, event:
                    self.refresh_git() if any(item and item.get_path() == path
                                              for item in (file, other)) else None)
            except GLib.Error:
                pass

    def _stop_git(self, *_args):
        with self._git_lock:
            self._git_closed = True
            for source in (self._git_timeout, self._git_idle):
                if source:
                    GLib.source_remove(source)
            self._git_timeout = self._git_idle = 0
        self._set_git_monitor(None)
        self._watch_current_file(None)

    def set_font_size(self, size: int) -> None:
        """Resize file names and reserve enough height for their Pango metrics."""
        self.font_size = size
        self._text_cell.set_property("size-points", size)
        self._badge_cell.set_property("size-points", size)
        description = self.tree.get_pango_context().get_font_description().copy()
        description.set_size(size * Pango.SCALE)
        metrics = self.tree.get_pango_context().get_metrics(description, None)
        height = (metrics.get_ascent() + metrics.get_descent()) / Pango.SCALE
        self._text_cell.set_fixed_size(-1, max(20, int(height + .999) + 4))
        self.tree.columns_autosize()
        self.tree.queue_resize()
        self.search.set_font_size(size)

    def set_root(self, path: str) -> None:
        self._git_snapshot = gitstatus.Snapshot()
        self._set_git_monitor(None)
        self._watch_current_file(None)
        self.root = os.path.abspath(path)
        self.refresh_git()
        self._rebuild({self.root}, None)
        self.search.set_root(self.root)

    def refresh(self) -> None:
        self._rebuild(self._expanded_paths(), self._selected_path())
        self._refresh_search()
        self.refresh_git()

    def set_show_hidden(self, show: bool) -> None:
        """Show dot-files and dot-folders in the navigator (default True)."""
        if self.show_hidden != bool(show):
            self.show_hidden = bool(show)
            self.refresh()

    def set_confirm_delete(self, confirm: bool) -> None:
        """Confirm ordinary removals (default True); trash fallback always asks."""
        self.confirm_delete = bool(confirm)

    def set_delete_mode(self, mode: str) -> None:
        """Choose 'delete' (the default) or 'trash' for subsequent removals."""
        if mode not in ("trash", "delete"):
            raise ValueError("Delete mode must be 'trash' or 'delete'.")
        self.delete_mode = mode

    def set_current_file(self, path: str | None) -> None:
        """Reveal the active editor file and list its search results first."""
        self._watch_current_file(path)
        self.search.set_current_file(path or None)
        if not path:
            self.tree.get_selection().unselect_all()
            return
        path = os.path.abspath(path)
        if os.path.commonpath((path, self.root)) != self.root:
            self.tree.get_selection().unselect_all()
            return
        parent = self.root
        for part in os.path.relpath(path, self.root).split(os.sep):
            row = self._find(parent)
            if row is None:
                break
            self.tree.expand_row(self.store.get_path(row), False)
            parent = os.path.join(parent, part)
        self.tree.get_selection().unselect_all()
        self._select(path)

    def set_text_provider(self, provider: Callable[[str], str | None] | None) -> None:
        """Provide live (possibly unsaved) text for a path, or None to read the disk."""
        self.search.set_text_provider(provider)

    def focus_search(self, query: str | None = None) -> None:
        """Focus the search entry; a given query replaces its text and runs at once."""
        self.search.focus_search()
        if query:
            self.search.search_now(query)
            self.search.entry.set_position(-1)

    def clear_search(self) -> None:
        self.search.clear()

    def focus_tree(self) -> None:
        self.tree.grab_focus()

    def _refresh_search(self) -> None:
        # Never leave results pointing at renamed or deleted files.
        if self.search.query:
            self.search.search_now()

    def _expanded_paths(self) -> set[str]:
        expanded: set[str] = set()
        self.tree.map_expanded_rows(
            lambda tree, path, data: expanded.add(self.store[path][2]), None
        )
        return expanded

    def _rebuild(self, expanded: set[str], selected: str | None) -> None:
        self.store.clear()
        self._loaded.clear()
        root = self.store.append(None, ["folder", os.path.basename(self.root) or self.root, self.root])
        self.store.append(root, [None, "", ""])
        self._restore(root, expanded)
        if selected:
            self._select(selected)

    def _restore(self, row: Gtk.TreeIter, expanded: set[str]) -> None:
        if self.store[row][2] not in expanded:
            return
        self.tree.expand_row(self.store.get_path(row), False)
        child = self.store.iter_children(row)
        while child is not None:
            self._restore(child, expanded)
            child = self.store.iter_next(child)

    def _load(self, row: Gtk.TreeIter) -> None:
        path = self.store[row][2]
        if not path or path in self._loaded:
            return
        self._loaded.add(path)
        placeholder = self.store.iter_children(row)
        entries: list[tuple[bool, str, str]] = []
        try:
            with os.scandir(path) as listing:
                for entry in listing:
                    if not self.show_hidden and entry.name.startswith("."):
                        continue
                    try:
                        # Directory symlinks remain leaf entries, avoiding cycles.
                        entries.append((entry.is_dir(follow_symlinks=False), entry.name, entry.path))
                    except OSError:
                        continue
        except OSError:
            pass
        for directory, name, full_path in sorted(entries, key=lambda item: (not item[0], item[1].casefold(), item[1])):
            child = self.store.append(row, ["folder" if directory else "text-x-generic", name, full_path])
            if directory:
                self.store.append(child, [None, "", ""])
        # Keep the placeholder until real rows exist, or GTK collapses the row.
        if placeholder is not None:
            self.store.remove(placeholder)

    def _on_expanded(self, tree: Gtk.TreeView, row: Gtk.TreeIter, path: Gtk.TreePath) -> None:
        # GTK3 exposes neither animated child geometry nor a per-row chevron
        # widget for motion.animate(). Keep expansion instant, including when
        # reduced motion is enabled (UX spec 7.2).
        self._load(row)

    def _on_activated(self, tree: Gtk.TreeView, path: Gtk.TreePath, column: Gtk.TreeViewColumn) -> None:
        row = self.store.get_iter(path)
        full_path = self.store[row][2]
        if self.store[row][0] == "folder":
            if tree.row_expanded(path):
                tree.collapse_row(path)
            else:
                tree.expand_row(path, False)
        elif full_path:
            self.emit("file-activated", full_path)

    def _selected_path(self) -> str | None:
        model, row = self.tree.get_selection().get_selected()
        return model[row][2] if row is not None else None

    def _target_folder(self) -> str:
        selected = self._selected_path()
        if not selected:
            return self.root
        return selected if os.path.isdir(selected) and not os.path.islink(selected) else os.path.dirname(selected)

    def _find(self, path: str) -> Gtk.TreeIter | None:
        found: list[Gtk.TreeIter] = []

        def visit(model: Gtk.TreeModel, tree_path: Gtk.TreePath, row: Gtk.TreeIter, data: object) -> bool:
            if model[row][2] == path:
                found.append(row.copy())
                return True
            return False

        self.store.foreach(visit, None)
        return found[0] if found else None

    def _select(self, path: str) -> None:
        row = self._find(path)
        if row is not None:
            tree_path = self.store.get_path(row)
            self.tree.set_cursor(tree_path)
            self.tree.scroll_to_cell(tree_path, None, False, 0, 0)

    def _create(self, parent: str, name: str, folder: bool = False) -> str:
        result = (fsops.create_folder if folder else fsops.create_file)(parent, name)
        expanded = self._expanded_paths() | {parent, self.root}
        self._rebuild(expanded, result)
        self._refresh_search()
        self.refresh_git()
        return result

    def _rename(self, path: str, name: str) -> str:
        if os.path.abspath(path) == self.root:
            raise fsops.FsOpError("The project root cannot be renamed.")
        expanded = self._expanded_paths()
        result = fsops.rename(path, name)
        expanded = {result + old[len(path):] if old == path or old.startswith(path + os.sep) else old for old in expanded}
        self._rebuild(expanded, result)
        self.emit("path-renamed", path, result)
        # After the signal, so open tabs already carry their new paths.
        self._refresh_search()
        self.refresh_git()
        return result

    def _delete(self, path: str, *, permanent: bool = False) -> str:
        if os.path.abspath(path) == self.root:
            raise fsops.FsOpError("The project root cannot be deleted.")
        operation = fsops.trash if self.delete_mode == "trash" and not permanent else fsops.delete
        result = operation(path, project_root=self.root)
        self._rebuild(self._expanded_paths(), self._selected_path())
        self.emit("path-deleted", result)
        self._refresh_search()
        self.refresh_git()
        return result

    def _window(self) -> Gtk.Window | None:
        widget = self.get_toplevel()
        return widget if isinstance(widget, Gtk.Window) else None

    def _name_dialog(self, title: str, initial: str, action: Callable[[str], str]) -> None:
        dialog = Gtk.Dialog(title=title, transient_for=self._window(), modal=True)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "OK", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        content = dialog.get_content_area()
        content.set_spacing(8)
        content.set_border_width(12)
        entry = Gtk.Entry(text=initial, activates_default=True)
        entry.set_size_request(280, -1)
        error_label = Gtk.Label(xalign=0, wrap=True)
        content.add(entry)
        content.add(error_label)
        dialog.show_all()
        entry.grab_focus()
        stem = os.path.splitext(initial)[0]
        entry.select_region(0, len(stem) if stem else -1)
        try:
            while dialog.run() == Gtk.ResponseType.OK:
                try:
                    action(entry.get_text())
                    break
                except fsops.FsOpError as error:
                    error_label.set_text(str(error))
        finally:
            dialog.destroy()

    def _new_dialog(self, folder: bool) -> None:
        parent = self._target_folder()
        self._name_dialog("New Folder" if folder else "New File", "", lambda name: self._create(parent, name, folder))

    def _rename_dialog(self) -> None:
        path = self._selected_path()
        if path and path != self.root:
            self._name_dialog("Rename", os.path.basename(path), lambda name: self._rename(path, name))

    def _confirm_removal(self, path: str, *, fallback: bool = False) -> bool:
        trash = self.delete_mode == "trash" and not fallback
        title = f'Move "{os.path.basename(path)}" to trash?' if trash else f'Delete "{os.path.basename(path)}"?'
        dialog = Gtk.MessageDialog(
            transient_for=self._window(), modal=True, message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK_CANCEL, text=title,
        )
        detail = ("You can restore this item from the system trash." if trash else
                  "Folders and their contents are deleted recursively. This cannot be undone.")
        if fallback:
            detail = "Trash is unavailable here. Delete permanently instead?\n" + detail
        dialog.format_secondary_text(detail)
        accepted = dialog.run() == Gtk.ResponseType.OK
        dialog.destroy()
        return accepted

    def _delete_dialog(self) -> None:
        path = self._selected_path()
        if not path or path == self.root:
            return
        if self.confirm_delete and not self._confirm_removal(path):
            return
        try:
            try:
                self._delete(path)
            except fsops.TrashUnavailable:
                # Consent to trash is not consent to irreversible deletion,
                # even when ordinary confirmation prompts are disabled.
                if self._confirm_removal(path, fallback=True):
                    self._delete(path, permanent=True)
        except fsops.FsOpError as error:
            message = Gtk.MessageDialog(transient_for=self._window(), modal=True,
                                        message_type=Gtk.MessageType.ERROR, buttons=Gtk.ButtonsType.CLOSE,
                                        text=str(error))
            message.run()
            message.destroy()

    def _copy_path(self) -> None:
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(self._selected_path() or self.root, -1)

    def _menu(self, event: Gdk.EventButton | None = None) -> None:
        menu = Gtk.Menu()
        selected = self._selected_path()
        for title, callback in (
            ("New File...", lambda *_: self._new_dialog(False)),
            ("New Folder...", lambda *_: self._new_dialog(True)),
            ("Rename...", lambda *_: self._rename_dialog()),
            ("Delete", lambda *_: self._delete_dialog()),
            ("Refresh", lambda *_: self.refresh()),
            ("Copy Path", lambda *_: self._copy_path()),
        ):
            item = Gtk.MenuItem(label=title)
            if title == "Delete":
                item.get_style_context().add_class("destructive-action")
            item.connect("activate", callback)
            if title in ("Rename...", "Delete"):
                item.set_sensitive(bool(selected and selected != self.root))
            menu.append(item)
        menu.show_all()
        menu.attach_to_widget(self.tree, None)
        menu.connect("selection-done", lambda widget: widget.destroy())
        if event:
            menu.popup_at_pointer(event)
        else:
            menu.popup_at_widget(self.tree, Gdk.Gravity.CENTER, Gdk.Gravity.NORTH_WEST, None)

    def _on_button(self, tree: Gtk.TreeView, event: Gdk.EventButton) -> bool:
        if event.button != 3:
            return False
        hit = tree.get_path_at_pos(int(event.x), int(event.y))
        if hit:
            tree.set_cursor(hit[0])
        else:
            tree.get_selection().unselect_all()
        tree.grab_focus()
        self._menu(event)
        return True

    def _on_popup(self, tree: Gtk.TreeView) -> bool:
        self._menu()
        return True

    def _search_has_focus(self) -> bool:
        top = self.get_toplevel()
        focus = top.get_focus() if isinstance(top, Gtk.Window) else None
        # The file tree itself lives inside the search panel's stack.
        return (focus is not None and focus.is_ancestor(self.search)
                and not focus.is_ancestor(self._scroll))

    def _on_key(self, tree: Gtk.TreeView, event: Gdk.EventKey) -> bool:
        if self._search_has_focus():
            return False
        if event.keyval == Gdk.KEY_F2:
            self._rename_dialog()
        elif event.keyval in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete):
            self._delete_dialog()
        elif event.keyval == Gdk.KEY_F5:
            self.refresh()
        elif event.keyval in (Gdk.KEY_n, Gdk.KEY_N) and event.state & Gdk.ModifierType.CONTROL_MASK and event.state & Gdk.ModifierType.SHIFT_MASK:
            self._new_dialog(True)
        else:
            return False
        return True


def build_css(ui: dict[str, str]) -> str:
    """Render the navigator's instant states from theme UI roles."""
    return f'''
@define-color idgent_git_new {ui['git_new']};
@define-color idgent_git_modified {ui['git_modified']};
@define-color idgent_git_conflict {ui['git_conflict']};
.idgent-explorer, .idgent-explorer viewport, .idgent-explorer scrolledwindow,
.idgent-explorer treeview {{ background: {ui['sidebar_bg']}; color: {ui['foreground']}; }}
.idgent-explorer-header {{ background: {ui['sidebar_bg']}; min-height: 24px; padding: 8px; }}
.idgent-explorer-header button {{ background: transparent; border: 0; border-radius: 4px;
    box-shadow: none; color: {ui['muted']}; min-height: 16px; min-width: 16px; padding: 4px 6px; }}
.idgent-explorer-header button image {{ opacity: 0.85; }}
.idgent-explorer-header button:hover {{ background: alpha({ui['overlay']}, 0.067); color: {ui['foreground']}; }}
.idgent-explorer-header button:hover image {{ opacity: 1; }}
.idgent-explorer treeview.view {{ color: {ui['muted']}; font-size: 13px;
    -GtkTreeView-vertical-separator: 2; -GtkTreeView-horizontal-separator: 4;
    -GtkTreeView-expander-size: 10; -gtk-icon-style: symbolic; }}
.idgent-explorer treeview.view:hover, .idgent-explorer treeview.view:selected {{
    background: alpha({ui['overlay']}, 0.067); color: {ui['foreground']}; border-radius: 8px; }}
.idgent-explorer treeview.view:focus {{ -gtk-outline-radius: 8px; }}
.idgent-explorer treeview expander {{ color: {ui['muted']}; }}
'''
