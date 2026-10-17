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


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, application: Gtk.Application, root: str) -> None:
        super().__init__(application=application)
        self.set_default_size(1200, 750)
        self.root = str(Path(root).resolve())
        self.terminal_position = "bottom"

        self.explorer = ExplorerPanel(self.root)
        self.editor = EditorPanel()
        self.terminal = TerminalPanel(self.root)
        self.explorer.connect("file-activated", lambda _, path: self.editor.open_file(path))
        self.explorer.connect("path-renamed", lambda _, old, new: self.editor.on_path_renamed(old, new))
        self.explorer.connect("path-deleted", lambda _, path: self.editor.on_path_deleted(path))

        self.dock = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        self.dock.pack1(self.editor, resize=True, shrink=False)
        self.dock.pack2(self.terminal, resize=False, shrink=False)
        self.dock.set_position(470)
        self.sidebar = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.sidebar.pack1(self.explorer, resize=False, shrink=False)
        self.sidebar.pack2(self.dock, resize=True, shrink=False)
        self.sidebar.set_position(260)

        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        layout.pack_start(self._build_menu(), False, False, 0)
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
        item(view_menu, "Toggle Terminal", self.toggle_terminal, "<Control>grave")
        self.move_terminal_item = item(view_menu, "Move Terminal to Right", self.toggle_terminal_position)
        item(view_menu, "Toggle Explorer Sidebar", self.toggle_explorer, "<Control>b")
        return bar

    def toggle_terminal_position(self) -> None:
        # Keep the panel alive while reparenting, preserving running terminals.
        terminal = self.terminal
        self.dock.remove(terminal)
        self.terminal_position = "right" if self.terminal_position == "bottom" else "bottom"
        right = self.terminal_position == "right"
        self.dock.set_orientation(Gtk.Orientation.HORIZONTAL if right else Gtk.Orientation.VERTICAL)
        self.dock.pack2(terminal, resize=False, shrink=False)
        self.dock.set_position(600 if right else 470)
        self.move_terminal_item.set_label("Move Terminal to Bottom" if right else "Move Terminal to Right")

    @staticmethod
    def _toggle(widget: Gtk.Widget) -> None:
        visible = not widget.get_visible()
        widget.set_no_show_all(not visible)
        if visible:
            widget.show_all()
        else:
            widget.hide()

    def toggle_terminal(self) -> None:
        self._toggle(self.terminal)

    def toggle_explorer(self) -> None:
        self._toggle(self.explorer)

    def _update_title(self) -> None:
        folder = Path(self.root)
        self.set_title(f"IDgent - {folder.name or str(folder)}")

    def set_root(self, path: str) -> None:
        root = Path(path).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"not a directory: {path}")
        self.root = str(root)
        self.explorer.set_root(self.root)
        self.terminal.set_cwd(self.root)
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
        if (focus is not None and focus.is_ancestor(self.editor)
                and modifiers == Gdk.ModifierType.CONTROL_MASK
                and event.keyval in (Gdk.KEY_s, Gdk.KEY_w, Gdk.KEY_slash)):
            # Let the focused editor handle its commands before menu accelerators.
            if self.propagate_key_event(event):
                return True
            if event.keyval == Gdk.KEY_s:
                self.editor.save_current()
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
