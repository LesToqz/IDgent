# IDgent

A small Linux IDE built with Python 3, GTK 3 and VTE 2.91.

## Install and run

Requires Python 3.10+, GTK 3, VTE 2.91 and PyGObject. On Debian/Ubuntu:

```sh
sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-vte-2.91
python3 -m idgent [folder]
```

The folder defaults to the current directory. No pip dependencies are required.
For optional syntax highlighting:

```sh
sudo apt install python3-pygments
```

The `highlight` Python extra also declares Pygments. The app works without it,
using plain text. Install the desktop launcher and icon for your user with:

```sh
bash packaging/install.sh
# Keep the repository here and ensure ~/.local/bin is on PATH.
bash packaging/install.sh --uninstall
```

## Layout and editing

One top bar contains File/View menus, the title **IDgent**, and window buttons.
The explorer on the left supports creating files/folders, renaming, deleting and
refreshing. Renames retarget open files; deletions mark affected tabs deleted.
File → Open Folder changes the explorer root and the directory for new shells;
existing shells keep their directories. Deletion is permanent.

The editor has tabs, line numbers, current-line and matching-bracket highlights,
undo/redo, case-insensitive search, indentation, line comments and zoom via
Ctrl+Plus/Minus/0 or Ctrl+wheel. Unsaved files prompt on close/quit.
Syntax highlighting detects filenames and shebangs: examples include Python,
JavaScript/TypeScript/JSX/TSX, HTML/XML, CSS, JSON, YAML, TOML, shell, C/C++, Rust,
Go, Java, SQL, Markdown, Dockerfile and Makefile (depending on Pygments).
Highlighting is skipped above 1.5 MB or for lines longer than 20,000 characters.
The editor accepts regular UTF-8 text files up to 5 MiB.

The bottom terminal dock starts open with side-by-side panes; the right dock
starts hidden and stacks panes vertically. Both can be open together. Drag the
bottom divider to the very top or the right divider to the far left to collapse
the editor area. Hiding a dock preserves its shells; showing an empty dock
starts a new shell.

Each terminal has its own header, labelled bash1…bash3 in order (or the selected
shell's name), with fullscreen and delete icons. The **+** in the dock's top-right
corner adds a terminal. Each dock independently allows three; adding a fourth
shows **Cannot add terminal (limit 3)**. Adding/deleting terminals animates them;
closing the last hides the dock. Split inserts after the focused pane.
Right-click offers Copy, Paste, Select All, Clear, Split Terminal and Kill Terminal.

A header's fullscreen icon, or View → Toggle Terminal Fullscreen, fills the whole
window content area with that terminal, keeping the top bar visible. Toggle again
to restore the layout. The menu command uses the focused visible dock, otherwise
the bottom, otherwise the right; if both are hidden it opens the bottom dock.
Layout commands restore fullscreen first. Esc is intentionally not bound to
terminal fullscreen. Resizing is applied once a drag settles. Output already on
screen is not re-flowed, preventing duplicated prompts.

## Themes

Choose View → Theme for a live switch, or View → Next Theme (Ctrl+Alt+T) to cycle.
Editor syntax, chrome and both terminal docks change without restarting shells;
new tabs and terminals inherit the theme. The selection is saved in
`~/.config/idgent/settings.json` (`$XDG_CONFIG_HOME/idgent/settings.json` when set).
Missing, invalid or unreadable settings fall back to Dark+.

- **Dark+** — charcoal with familiar blue and purple syntax accents; the default.
- **Light+** — white editor with blue and purple accents.
- **Monokai** — warm dark grey with bright pink, green and yellow syntax.
- **Dracula** — purple-grey with pastel pink, purple and green.
- **Solarized Dark** — deep teal with restrained Solarized accents.
- **Solarized Light** — cream with Solarized accents.
- **Nord** — cool blue-grey with icy blue accents.
- **One Dark** — charcoal-blue with muted colourful syntax.
- **GitHub Dark** — near-black with blue accents.
- **IDgent Midnight** — deep navy with cyan accents.
- **IDgent Ember** — warm brown with amber accents.
- **IDgent Paper** — warm paper with teal accents.

## Shortcuts

These are app bindings; GTK text editing and shell bindings also apply.

| Shortcut | Command / focus |
| --- | --- |
| Ctrl+S | Save current file; terminal focus forwards Ctrl+S to the shell (XOFF) |
| Ctrl+Shift+S | Save all files |
| Ctrl+Q | Quit the app |
| Ctrl+B | Toggle explorer |
| Ctrl+` | Toggle bottom terminal dock and focus it when shown |
| Ctrl+Alt+` | Toggle right terminal dock and focus it when shown |
| Ctrl+Shift+5 | Split terminal in focused dock, otherwise bottom |
| Ctrl+Shift+F11 | Toggle whole-window terminal fullscreen |
| Ctrl+Alt+T | Next theme, wrapping after the last |
| Ctrl+W | Close tab (editor) |
| Ctrl+Z / Ctrl+Shift+Z | Undo / redo (editor) |
| Ctrl+F (also Ctrl+Shift+F) | Open search (editor); Enter finds next match |
| Ctrl+Tab / Ctrl+Shift+Tab | Next / previous editor tab |
| Ctrl+PageDown / Ctrl+PageUp | Next / previous editor tab |
| Ctrl+/ | Toggle line comments (Python, shell, TOML, YAML, conf) |
| Tab / Shift+Tab | Indent / unindent (editor) |
| Enter | Newline with automatic indentation (editor) |
| Ctrl+Plus (or Ctrl+=) / Ctrl+Minus / Ctrl+0 | Increase / decrease / reset editor font |
| Ctrl+wheel | Zoom editor |
| Ctrl+Shift+T | Split terminal (terminal focus) |
| Ctrl+Shift+W | Kill focused terminal (terminal focus) |
| Ctrl+Alt+Left / Right | Previous / next terminal, wrapping within its dock |
| Ctrl+Alt+Up / Down | Previous / next terminal in the stacked right dock |
| Ctrl+Shift+C / V | Copy / paste (terminal focus) |
| Ctrl+Shift+Plus (or Ctrl+Shift+=) / Minus / 0 | Increase / decrease / reset terminal font (hold Ctrl+Shift for each) |
| F2 / Delete / F5 | Rename / delete / refresh (explorer) |
| Ctrl+Shift+N | New folder (explorer) |

## Tests and limitations

Install Xvfb and run with the system Python that has GTK/VTE available:

```sh
sudo apt install xvfb
GDK_BACKEND=x11 xvfb-run -a python3 -m unittest discover -s tests
```

No GtkSourceView, persisted layout/session, or Wayland-specific testing.
Ctrl+Q is the app quit key, including when a terminal has focus.
