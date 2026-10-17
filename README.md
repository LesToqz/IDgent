# IDgent

A small Linux desktop editor built with Python, GTK 3 and VTE 2.91.
It includes a file explorer (create, rename, delete and refresh), UTF-8 editor
tabs with undo/redo, search, indentation and comment toggling, and tabbed shells.
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

The resizable layout has an explorer on the left, editor in the center and
terminal below. View → Move Terminal to Right / Bottom moves the same running
terminal panel. View also toggles the terminal and explorer.

| Shortcut | Command |
| --- | --- |
| Ctrl+` | Toggle terminal |
| Ctrl+B | Toggle explorer sidebar |
| Ctrl+S | Save current file |
| Ctrl+Shift+S | Save all files |
| Ctrl+Q | Quit |
| Ctrl+W | Close editor tab (editor focus) |
| Ctrl+/ | Toggle line comments in supported text files (editor focus) |

Code lives in `idgent/`: `window.py` wires the explorer, editor and terminal
panels; `fsops.py` implements file operations. `packaging/` contains Linux desktop
assets. Run all tests with system GTK, VTE and Xvfb:

```sh
xvfb-run -a python3 -m unittest discover -s tests
```

Known limitations: no syntax highlighting, no persisted layout or session,
and only regular UTF-8 text files up to 5 MB are editable. Comment toggling
supports Python, shell, TOML, YAML and conf files. File deletion is permanent.
