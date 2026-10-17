# IDgent

A small Linux desktop editor built with Python, GTK 3 and VTE 2.91.
It includes a file explorer (create, rename, delete and refresh), UTF-8 editor
tabs with undo/redo, search, indentation and comment toggling, and side-by-side terminal panes.
Renaming folders retargets open files; deleting them marks affected tabs deleted.

Requirements: Python 3.10+, `python3-gi`, `gir1.2-gtk-3.0`, and
`gir1.2-vte-2.91` (Debian/Ubuntu package names). No pip packages are needed.

Run from the repository:

```sh
python3 -m idgent [folder]
```

The folder defaults to the current directory. File → Open Folder changes the
explorer root and the working directory for **new** terminals. Existing shells
keep their directories. Unsaved files prompt on quit.

Install the desktop entry, icon and launcher for your user:

```sh
bash packaging/install.sh
# Ensure ~/.local/bin is on PATH. Keep this repository at its current location.
bash packaging/install.sh --uninstall
```

The top bar combines File/View menus, a centred folder title and window buttons.
The resizable layout keeps the explorer on the left, with independent terminal
docks below the editor and on its right. Both docks can be open together:

```text
+----------+--------------------------+---------------+
| Explorer | Editor                   | Right terminal|
|          +--------------------------+               |
|          | Bottom pane | Bottom pane|               |
+----------+--------------------------+---------------+
```

The bottom dock starts open; the right dock starts hidden. View → Terminal Below
and Terminal on Right toggle each dock. Hiding a dock preserves its running
shells. Showing an empty dock starts a shell. Closing the final pane hides its
dock after its exit animation. Each dock has equal-width horizontal panes and a
bar with chips named bash1, bash2, bash3 (or the selected shell's name). Each chip
has fullscreen and delete icons; the "+" button adds a terminal. Each dock allows
three terminals independently; a fourth shows "Cannot add terminal (limit 3)".
Adding and deleting panes animates the pane and chip.

A chip's fullscreen icon fills the whole content area, keeping the top bar visible.
Click it again, or use View → Toggle Terminal Fullscreen / Ctrl+Shift+F11, to
restore the previous layout. The command uses the focused dock, otherwise the
bottom dock, otherwise the right; if both are hidden it opens the bottom dock.
Layout commands restore fullscreen first. Esc stays available to terminal shells
and editors. Ctrl+Shift+F is already consumed by editor search, so fullscreen uses
Ctrl+Shift+F11.

Split Terminal inserts a pane immediately to the right of the focused pane,
using the focused dock or the bottom dock when focus is elsewhere. Right-click a
terminal for Copy, Paste, Select All, Clear, Split Terminal and Kill Terminal.

| Shortcut | Command |
| --- | --- |
| Ctrl+` | Toggle bottom terminal dock; focus it when shown |
| Ctrl+Alt+` | Toggle right terminal dock; focus it when shown |
| Ctrl+Shift+F11 | Toggle whole-window terminal fullscreen |
| Ctrl+Shift+5 | Split terminal in the focused dock (otherwise bottom) |
| Ctrl+Shift+T | Split terminal (terminal focus) |
| Ctrl+Shift+W | Kill focused terminal pane (terminal focus) |
| Ctrl+Alt+Left / Right | Focus previous / next terminal pane, wrapping within its dock |
| Ctrl+Shift+C / V | Copy selection / paste clipboard (terminal focus) |
| Ctrl+Shift+Plus / Minus / 0 | Increase / decrease / reset terminal font size |
| Ctrl+B | Toggle explorer sidebar |
| Ctrl+S | Save current file |
| Ctrl+Shift+S | Save all files |
| Ctrl+Q | Quit |
| Ctrl+W | Close editor tab (editor focus) |
| Ctrl+/ | Toggle line comments in supported text files (editor focus) |

Ctrl+Alt+Left/Right leaves the shell's Alt+Left/Right bindings available.

Code lives in `idgent/`: `window.py` wires the explorer, editor and terminal
panels; `fsops.py` implements file operations. `packaging/` contains Linux desktop
assets.

Tests (system GTK, VTE and Xvfb; test modules force the X11 backend):

```sh
xvfb-run -a python3 -m unittest discover -s tests
```

Manual smoke under Xvfb: `GDK_BACKEND=x11 xvfb-run -a python3 -m idgent /tmp`.
Set the backend explicitly so an inherited `WAYLAND_DISPLAY` cannot use the desktop.

Known limitations: no syntax highlighting, no persisted layout or session,
and only regular UTF-8 text files up to 5 MB are editable. Comment toggling
supports Python, shell, TOML, YAML and conf files. File deletion is permanent.
