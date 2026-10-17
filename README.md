# IDgent

IDgent is a VS Code-style desktop IDE for Linux, built with Python 3, GTK 3,
and VTE 2.91. Its resizable layout has an explorer sidebar, a central editor,
and a terminal dock that moves between the bottom and right side while keeping
running shells alive. The editor uses plain Gtk.TextView; GtkSource is not required.

Run from this directory with Python 3.10 or newer:

```sh
python3 -m idgent [folder]
```

The folder defaults to the current directory and must exist. GTK 3, PyGObject
(`gi`), and VTE 2.91 must be provided by the Linux system. No pip or npm
packages are required. The project also declares the `idgent` console entry point.

Use **File → Open Folder…** to change the explorer root and the working directory
for new terminals. Quitting prompts to save, discard, or cancel when files have
unsaved changes. Layout settings are not persisted yet.

| Shortcut | Command |
| --- | --- |
| Ctrl+` | Toggle terminal |
| Ctrl+B | Toggle explorer sidebar |
| Ctrl+S | Save current file |
| Ctrl+Shift+S | Save all files |
| Ctrl+Q | Quit |

The View menu also has **Move Terminal to Right**, which changes to
**Move Terminal to Bottom** after moving the dock.

## Project layout

```text
idgent/
  __init__.py       Empty package marker
  __main__.py       Module entry point
  app.py            CLI validation and Gtk.Application
  window.py         Layout, menus, shortcuts, and panel wiring
  _placeholder.py   Temporary contract-compatible stand-ins
  explorer.py       Explorer panel (developed separately)
  editor.py         Editor panel (developed separately)
  terminal.py       VTE terminal panel (developed separately)
tests/
  test_window_layout.py
packaging/          Reserved for packaging work
pyproject.toml      Project metadata and console entry point
```

The shell loads panels when creating a window and uses stand-ins when their
modules are unavailable. Integration will remove these fallbacks.

## Panel contract

All panels subclass `Gtk.Box` using Gtk 3.0. Paths are strings.

| Panel | Constructor | Methods | Signals |
| --- | --- | --- | --- |
| `ExplorerPanel` | `(root: str)` | `set_root(path: str) -> None`, `refresh() -> None` | `file-activated(path: str)`, `path-renamed(old: str, new: str)`, `path-deleted(path: str)` |
| `EditorPanel` | `()` | `open_file(path: str) -> None`, `save_current() -> bool`, `save_all() -> bool`, `has_unsaved() -> bool`, `on_path_renamed(old: str, new: str) -> None`, `on_path_deleted(path: str) -> None` | None |
| `TerminalPanel` | `(cwd: str)` | `set_cwd(path: str) -> None`, `new_terminal() -> None`, `close_current() -> None` | None |

The window forwards explorer signals to the editor. `TerminalPanel.set_cwd`
affects new terminals. The window owns quit confirmation via `has_unsaved()`
and keeps the same terminal panel instance when moving or hiding the dock.

Run the layout and wiring tests with system-provided GTK and Xvfb:

```sh
xvfb-run -a python3 -m unittest tests.test_window_layout -v
```
