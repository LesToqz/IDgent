"""Application window, panel layout, menus, and shared commands."""

from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk

from idgent.explorer import ExplorerPanel
from idgent.editor import EditorPanel
from idgent.terminal import TerminalPanel
from idgent.titlebar import TitleBar


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, application: Gtk.Application, root: str) -> None:
        super().__init__(application=application)
        self.set_default_size(1200, 750)
        self.root = str(Path(root).resolve())

        self.explorer = ExplorerPanel(self.root)
        self.editor = EditorPanel()
        self.terminal_bottom = TerminalPanel(self.root)
        self.terminal_right = TerminalPanel(self.root, autostart=False)
        self.terminal_right.set_no_show_all(True)
        for panel in (self.terminal_bottom, self.terminal_right):
            panel.connect("last-closed", self._hide_terminal)
        self.explorer.connect("file-activated", lambda _, path: self.editor.open_file(path))
        self.explorer.connect("path-renamed", lambda _, old, new: self.editor.on_path_renamed(old, new))
        self.explorer.connect("path-deleted", lambda _, path: self.editor.on_path_deleted(path))

        self.editor.set_size_request(300, 150)
        self.dock_bottom = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        self.dock_bottom.pack1(self.editor, resize=True, shrink=False)
        self.dock_bottom.pack2(self.terminal_bottom, resize=False, shrink=True)
        self.dock = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.dock.pack1(self.dock_bottom, resize=True, shrink=False)
        self.dock.pack2(self.terminal_right, resize=False, shrink=True)
        self._size_pending = {self.terminal_bottom: True, self.terminal_right: True}
        self.dock_bottom.connect_after("size-allocate", self._size_terminal, self.terminal_bottom)
        self.dock.connect_after("size-allocate", self._size_terminal, self.terminal_right)
        self.sidebar = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.sidebar.pack1(self.explorer, resize=False, shrink=False)
        self.sidebar.pack2(self.dock, resize=True, shrink=False)
        self.sidebar.set_position(260)

        self.titlebar = TitleBar(self._build_menu())
        self.set_titlebar(self.titlebar)
        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        layout.pack_start(self.sidebar, True, True, 0)
        self.add(layout)
        self._update_title()
        self.connect("delete-event", self._on_delete)

    def _build_menu(self) -> Gtk.MenuBar:
        bar = Gtk.MenuBar()
        accelerators = Gtk.AccelGroup()
        self.add_accel_group(accelerators)

        def item(menu: Gtk.Menu, label: str, callback: Callable[[], object],
                 shortcut: str | None = None) -> Gtk.MenuItem:
            entry = Gtk.MenuItem(label=label)
            entry.connect("activate", lambda _: callback())
            if shortcut:
                key, modifiers = Gtk.accelerator_parse(shortcut)
                entry.add_accelerator("activate", accelerators, key, modifiers,
                                      Gtk.AccelFlags.VISIBLE)
            menu.append(entry)
            return entry

        file_menu = Gtk.Menu()
        view_menu = Gtk.Menu()
        for label, menu in (("File", file_menu), ("View", view_menu)):
            entry = Gtk.MenuItem(label=label)
            entry.set_submenu(menu)
            bar.append(entry)
        item(file_menu, "Open Folder…", self.open_folder)
        item(file_menu, "Save", lambda: self.editor.save_current(), "<Control>s")
        item(file_menu, "Save All", lambda: self.editor.save_all(), "<Control><Shift>s")
        item(file_menu, "Quit", self.close, "<Control>q")
        item(view_menu, "Terminal Below", self.toggle_terminal_bottom, "<Control>grave")
        item(view_menu, "Terminal on Right", self.toggle_terminal_right, "<Control><Alt>grave")
        item(view_menu, "Split Terminal", self.split_terminal, "<Control><Shift>5")
        item(view_menu, "Toggle Explorer Sidebar", self.toggle_explorer, "<Control>b")
        return bar

    def _size_terminal(self, dock: Gtk.Paned, allocation: Gdk.Rectangle,
                       panel: TerminalPanel) -> None:
        if not self._size_pending[panel] or not panel.get_visible():
            return
        right = panel is self.terminal_right
        extent = allocation.width if right else allocation.height
        if extent <= 1:
            return
        first, second = dock.get_child1().get_allocation(), panel.get_allocation()
        handle = max(0, extent - (first.width + second.width if right
                                  else first.height + second.height))
        available = extent - handle
        size = (min(max(round(extent * .4), 380), int(extent * .6), available - 300)
                if right else min(max(round(extent * .3), 150), available - 150))
        self._size_pending[panel] = False
        dock.set_position(max(0, available - max(0, size)))

    def _hide_terminal(self, panel: TerminalPanel) -> None:
        panel.set_no_show_all(True)
        panel.hide()

    def _show_terminal(self, panel: TerminalPanel) -> None:
        panel.set_no_show_all(False)
        panel.show_all()
        if panel.terminal_count() == 0:
            panel.new_terminal()
        panel.focus_terminal()

    def _toggle_terminal(self, panel: TerminalPanel) -> None:
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
        # Opening an empty dock already creates its initial terminal.
        empty = panel.terminal_count() == 0
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
        self._toggle(self.explorer)

    def _update_title(self) -> None:
        folder = Path(self.root)
        self.set_title(f"IDgent - {folder.name or str(folder)}")
        self.titlebar.set_folder(self.root)

    def set_root(self, path: str) -> None:
        root = Path(path).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"not a directory: {path}")
        self.root = str(root)
        self.explorer.set_root(self.root)
        self.terminal_bottom.set_cwd(self.root)
        self.terminal_right.set_cwd(self.root)
        self._update_title()

    def open_folder(self) -> None:
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

    def do_key_press_event(self, event: Gdk.EventKey) -> bool:
        focus = self.get_focus()
        modifiers = event.state & Gtk.accelerator_get_default_mod_mask()
        if (modifiers == Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK
                and event.keyval in (Gdk.KEY_5, Gdk.KEY_percent)):
            self.split_terminal()
            return True
        if (modifiers == Gdk.ModifierType.CONTROL_MASK
                and event.keyval == Gdk.KEY_s):
            if focus is not None and any(focus.is_ancestor(panel) for panel in
                                         (self.terminal_bottom, self.terminal_right)):
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
        return Gtk.ApplicationWindow.do_key_press_event(self, event)

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
