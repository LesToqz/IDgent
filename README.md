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
The explorer on the left supports searching (see Search in Files), creating
files/folders, renaming, deleting and refreshing. Renames retarget open files; deletions mark affected tabs deleted.
File → Open Folder changes the explorer root and the directory for new shells;
existing shells keep their directories. Deletion is permanent.

The editor has tabs, line numbers, current-line and matching-bracket highlights,
undo/redo, indentation, line comments and zoom via Ctrl+Plus/Minus or Ctrl+wheel;
Ctrl+0 resets to the editor text size chosen in Settings (11 pt by default).
Ctrl+F opens the in-file find bar (case-insensitive, Enter finds the next match).
Unsaved files prompt on close/quit.
Syntax highlighting detects filenames and shebangs: examples include Python,
JavaScript/TypeScript/JSX/TSX, HTML/XML, CSS, JSON, YAML, TOML, shell, C/C++, Rust,
Go, Java, SQL, Markdown, Dockerfile and Makefile (depending on Pygments).
Highlighting is skipped above 1.5 MB or for lines longer than 20,000 characters.
The editor accepts regular UTF-8 text files up to 5 MiB.

## Search in Files

The search box sits at the top of the explorer, directly under the EXPLORER header.
Press **Ctrl+Shift+F** (or View → Search in Files) to focus it: this shows the
explorer if it is hidden and leaves terminal fullscreen first. If the editor has
a non-empty, single-line selection of at most 200 characters, it is copied into the
box and searched at once; otherwise the previous query stays, selected.

Typing replaces the file tree with results after a short pause; the search runs in
the background, so the UI stays responsive. Clearing the box or pressing **Esc**
restores the tree and returns focus to the active editor tab (or to the tree when
no tab is open). Enter searches immediately and moves to the results; Down moves
from the box to the results.

- **Current file first.** Matches in the file of the active editor tab are listed
  first (marked "current file"), then the other files in path order, then a
  **File names** group with files whose name contains the query. Switching tabs
  re-sorts the list.
- **Unsaved edits are searched**, not just the saved text on disk.
- **Click or Enter** on a result opens (or focuses) the file, selects the match,
  scrolls it to roughly the middle and focuses the editor. A Markdown tab showing
  the preview is switched to its source first. Clicking a file header collapses it.
- **Aa** toggles match case (off: case-insensitive); **.\*** toggles Python regular
  expressions (an invalid pattern shows its error; empty matches are ignored). File
  name matching is always a plain, case-insensitive substring.
- **Limits.** At most 200 matches per file and 1000 in total (the summary then reads
  "Showing the first N results"). Only UTF-8 text files up to 1 MiB are searched;
  binary files (a NUL byte in the first 8 KB), undecodable files and symlinks are
  skipped. Previews of very long lines are cut to 200 characters around the match.
- **Ignored folders:** `.git .hg .svn __pycache__ node_modules .venv venv .mypy_cache
  .pytest_cache .tox .idea .ceo`. Other hidden files are searched.
- Results follow File → Open Folder, and are re-run after refresh, rename, delete
  and create, so they never point at files that are gone. While the box or results
  have focus, the explorer's F2 / Delete / F5 / Ctrl+Shift+N do nothing to the tree.

## Settings

File → Settings… (**Ctrl+,**) opens a non-modal dialog; changes apply immediately
and are saved.

- **Color theme** — the theme picker lives here (see Themes below).
- **Text size** — three independent spin buttons with per-row Reset: *Terminal*
  (7–32 pt, default 11), *Editor (file content)* (7–32 pt, default 11) and *Explorer
  (file names)* (8–24 pt, default 10; also sizes the search results).

Settings are stored as JSON in `~/.config/idgent/settings.json`
(`$XDG_CONFIG_HOME/idgent/settings.json` when set), written atomically with mode
0600, with the keys `theme`, `terminal_font_size`, `editor_font_size` and
`explorer_font_size`. A missing, unreadable or invalid file falls back to defaults;
out-of-range sizes are clamped. Zooming with Ctrl+Plus/Minus/wheel is temporary:
the saved size is what Ctrl+0 returns to and what new sessions start with.

## Markdown preview

Files ending in `.md` or `.markdown` (only those) get a small pill at the top right
of the tab with two buttons: the eye shows the **rendered preview**, `</>` shows the
**source**. **Ctrl+Shift+V** toggles between them (editor focus). The preview is
read-only and always renders the current buffer, unsaved edits included; switching
back to the source restores your cursor and scroll position. Ctrl+F switches to the source before opening find. Tabs for
other file types never show the pill.

The renderer is a small, dependency-free subset, not full CommonMark:

- Supported: ATX (`#`) and underlined headings, paragraphs, **bold**, *italic*,
  `~~strikethrough~~`, inline code, fenced and indented code blocks (highlighted by
  language when Pygments is installed), block quotes, bullet, numbered and task
  lists (nesting by indentation), tables (drawn as a monospace grid; column padding
  is capped at 120 characters, so longer cells overflow their column), horizontal rules, inline links and
  `<https://…>` autolinks.
- Links: `http(s)` and `mailto` links open in the system handler; relative links to
  existing files open them in a tab. Other schemes and absolute paths are ignored.
- Not supported: HTML (shown as text), images (shown as `[image: alt]`, never
  loaded), reference-style links, footnotes, and CommonMark's finer emphasis rules.

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

Pick a theme in File → Settings… for a live switch, or press **Ctrl+Alt+T** (no menu
entry) to cycle to the next one, wrapping after the last.
Editor syntax, chrome, the explorer and both terminal docks change without restarting
shells; new tabs and terminals inherit the theme. The selection is saved in the
settings file described above. Missing, invalid or unreadable settings fall back to Dark+.

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
| Ctrl+, | Open Settings (theme and text sizes) |
| Ctrl+B | Toggle explorer |
| Ctrl+Shift+F | Search in Files: focus the explorer search box, pre-filled from a one-line selection |
| Ctrl+` | Toggle bottom terminal dock and focus it when shown |
| Ctrl+Alt+` | Toggle right terminal dock and focus it when shown |
| Ctrl+Shift+5 | Split terminal in focused dock, otherwise bottom |
| Ctrl+Shift+F11 | Toggle whole-window terminal fullscreen |
| Ctrl+Alt+T | Next theme, wrapping after the last |
| Ctrl+W | Close tab (editor) |
| Ctrl+Z / Ctrl+Shift+Z | Undo / redo (editor) |
| Ctrl+F | Find in the current file (editor); Enter finds next match |
| Ctrl+Shift+V | Toggle Markdown rendered preview / source (`.md`, `.markdown` tabs) |
| Esc | In the explorer search box or results: clear the query, back to the editor |
| Ctrl+Tab / Ctrl+Shift+Tab | Next / previous editor tab |
| Ctrl+PageDown / Ctrl+PageUp | Next / previous editor tab |
| Ctrl+/ | Toggle line comments (Python, shell, TOML, YAML, conf) |
| Tab / Shift+Tab | Indent / unindent (editor) |
| Enter | Newline with automatic indentation (editor) |
| Ctrl+Plus (or Ctrl+=) / Ctrl+Minus / Ctrl+0 | Increase / decrease / reset editor font to the size set in Settings |
| Ctrl+wheel | Zoom editor |
| Ctrl+Shift+T | Split terminal (terminal focus) |
| Ctrl+Shift+W | Kill focused terminal (terminal focus) |
| Ctrl+Alt+Left / Right | Previous / next terminal, wrapping within its dock |
| Ctrl+Alt+Up / Down | Previous / next terminal in the stacked right dock |
| Ctrl+Shift+C / V | Copy / paste (terminal focus; in a Markdown editor tab Ctrl+Shift+V toggles the preview) |
| Ctrl+Shift+Plus (or Ctrl+Shift+=) / Minus / 0 | Increase / decrease / reset terminal font (hold Ctrl+Shift for each) |
| F2 / Delete / F5 | Rename / delete / refresh (explorer tree; ignored while the search box has focus) |
| Ctrl+Shift+N | New folder (explorer tree) |

## Tests and limitations

Install Xvfb and run with the system Python that has GTK/VTE available:

```sh
sudo apt install xvfb
GDK_BACKEND=x11 xvfb-run -a python3 -m unittest discover -s tests
```

No GtkSourceView, persisted layout/session, or Wayland-specific testing.
Ctrl+Q is the app quit key, including when a terminal has focus.
