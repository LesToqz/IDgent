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

IDgent follows the look and feel of the Reference app note app: flat neutral surfaces,
one accent colour, rounded tabs, instant hover/selection feedback and motion only
where something moves (sliding toggles, notices, terminal panes).

One top bar contains File/View menus, the title **IDgent**, a settings cog and the
window buttons. The explorer sidebar starts 300 px wide.
The explorer on the left supports searching (see Search in Files), creating
files/folders, renaming, deleting and refreshing. Renames retarget open files; deletions mark affected tabs deleted.
File → Open Folder changes the explorer root and the directory for new shells;
existing shells keep their directories. Deletion is permanent by default; Settings →
Files can move deleted items to the system trash instead.

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

## Graph view

Graph view draws the project's notes and source files as an interactive network.
Open it from either dock's **+ → Graph view** chooser, **View → Graph view**,
**Ctrl+Alt+G**, or the command palette's **Graph view: Open graph view** command.
The menu/shortcut/palette reuse the graph in the right dock. Each dock supports
one graph alongside terminals; pane focus, fullscreen and dock toggling also work
with graph panes. Ctrl+P/Ctrl+O, Ctrl+Shift+P and Ctrl+, remain available while the
graph has focus.

The index understands wikilinks, aliases, heading/block link targets, embeds,
relative Markdown links, inline/frontmatter tags, referenced attachments,
unresolved targets and supported code imports. Heading/block targets connect to
the containing file. Click a file node to open it in the editor, a tag to search
files, or an unresolved node to create a note with a simple name in the project
root. Context actions reveal files and copy absolute paths; both open actions
currently open or focus a normal editor tab.

The controls panel has **Filters / Groups / Display / Forces** sections. Filters
remove non-matching nodes; **Existing files only** hides unresolved targets.
Tags and attachments start hidden, orphans and source files start visible.
Groups use the first matching non-empty query. Display adjusts labels, node/line
sizes and arrows; Forces adjusts attraction, repulsion and link distance.
Panel state, zoom, groups and graph preferences persist separately from the
editor settings. The graph follows the current theme, accent and Reduce motion
setting, and the active editor file is highlighted. Saved files, unsaved note/code
edits (after 400 ms), explorer operations and Open Folder update the graph live.

**View → Local graph**, **Ctrl+Shift+Alt+G**, or **Graph view: Open local graph**
shows the active file and its neighbors one link away. It follows editor tab
changes; the panel adjusts depth and incoming/outgoing links. Open Graph view
again to return to the global graph. The palette also provides
**Graph view: Rebuild graph** and **Graph view: Reset camera**.

Install the optional WebKit2 dependency on Debian/Ubuntu:

```sh
sudo apt install gir1.2-webkit2-4.1
```

Without it, the pane shows an installation hint and the rest of IDgent works.
Indexing runs in a worker thread; the Canvas2D simulation stops drawing when
settled and pauses when hidden. Software rendering is the default; large graphs
are capped at 3,000 nodes and the page reports truncation. Performance varies with
project size, window dimensions and rendering drivers.

For a test vault, use a disposable copy of a folder containing hubs, chains,
rings, isolated pairs, orphan notes, nested tags, attachments, missing targets,
ambiguous basenames, source imports and a few hundred linked notes. The integration
tests copy the optional demo vault before editing it. Run them under Xvfb with
`python3 -m unittest tests.test_graph_wiring`; set `IDGENT_GRAPH_QA=1` to capture
screenshots and performance measurements in `.ceo/`.

Current limitations: group drag-reorder, the local **Neighbor links** toggle,
touch pinch and hover page-preview popovers are not implemented. Search supports
the index's query subset (text, quoted terms, AND/OR, negation, regex and
path/file/content/tag fields), rather than every Reference app search operator.

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

## Quick Switcher and Command Palette

Both open as a box near the top of the window (no dimming; click outside or press
**Esc** to close, which returns focus to where it was). Type to filter with fuzzy
matching (matched letters are bold); **Up/Down** move, **Enter** chooses.

- **Quick Switcher** — **Ctrl+P** or **Ctrl+O** (File → Quick Switcher…). Lists the
  files of the open folder (the Search in Files ignored folders are skipped; hidden
  files follow *Show hidden files*; at most 20,000 files). With an empty query the
  recently active and open files come first. Enter opens the file in the editor.
- **Command Palette** — **Ctrl+Shift+P** (View → Command Palette…). Every File and
  View menu action plus Open settings, Show hotkeys, Next theme, Toggle Markdown
  preview and editor zoom in/out/reset, each with its shortcut shown as a chip.
  Recently run commands are listed first.

Ctrl+P, Ctrl+O and F1 are left to a focused terminal (readline, vim, mc and htop
use them); Ctrl+Shift+P works everywhere. Notices (small toasts at the top right)
appear only for things with no other feedback, such as a settings file that could
not be written; click one to dismiss it.

## Settings

The header-bar cog or **Ctrl+,** opens the Settings window (there is no File menu
entry); **F1** opens it on the Hotkeys page. It is non-modal: changes apply
immediately to open tabs, the explorer and both terminal docks, and are saved at
once. Search the left pane to filter settings across pages; Esc clears the search,
then closes the window.

| Page | Setting | Default | Key |
| --- | --- | --- | --- |
| Appearance | Color theme | IDgent Dark | `theme` |
| | Accent color (swatches, Reset = theme accent) | theme accent | `accent` |
| | Reduce motion (turns off interface animations) | off | `reduce_motion` |
| | Interface font (Restore = system font) | system | `ui_font` |
| | Explorer text size, 8–24 pt (also search results) | 10 | `explorer_font_size` |
| Editor | Editor text size, 7–32 pt | 11 | `editor_font_size` |
| | Editor font (Restore = monospace; not the Markdown preview) | monospace | `editor_font` |
| | Line numbers | on | `line_numbers` |
| | Highlight current line | on | `highlight_line` |
| | Markdown: open in preview (newly opened tabs) | off | `md_default_preview` |
| | Markdown: readable line length (700 px preview column) | on | `md_readable_width` |
| | Indent using tabs | off | `indent_tabs` |
| | Indent width, 2–8 | 4 | `indent_width` |
| | Auto-pair brackets and quotes | on | `auto_pair` |
| Files | Show hidden files (explorer and Quick Switcher) | on | `show_hidden` |
| | Confirm before deleting | on | `confirm_delete` |
| | Deleted files: Move to system trash / Delete permanently | Delete permanently | `delete_mode` |
| | Ignored folders (read-only list) | — | — |
| Terminal | Terminal text size, 7–32 pt | 11 | `terminal_font_size` |
| | Cursor shape: block / I-beam / underline | block | `terminal_cursor` |
| | Blinking cursor | on | `terminal_blink` |
| | Scrollback lines, 1000–100000 | 10000 | `terminal_scrollback` |
| | Open bottom terminal at start-up (applies from the next start) | on | `terminal_autostart` |
| Hotkeys | Searchable list of every shortcut | — | — |
| About | Version, settings file path, licences | — | — |

Settings are stored as JSON in `~/.config/idgent/settings.json`
(`$XDG_CONFIG_HOME/idgent/settings.json` when set), written atomically with mode
0600; unknown keys are preserved. A missing, unreadable or invalid file falls back
to defaults; invalid values use their default and numbers are clamped. If the file
cannot be written, the change still applies for the session and an error notice
says so. Zooming with Ctrl+Plus/Minus/wheel is temporary: the saved size is what
Ctrl+0 returns to and what new sessions start with.

**Reduce motion** (or `IDGENT_REDUCE_MOTION=1`, or GTK's *enable animations* turned
off) makes toggles, notices and terminal pane changes instant.

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

Pick a theme in Settings → Appearance for a live switch, or press **Ctrl+Alt+T**
(also a Command Palette entry) to cycle to the next one, wrapping after the last.
The accent colour setting recolours the accent of any theme.
Editor syntax, chrome, the explorer and both terminal docks change without restarting
shells; new tabs and terminals inherit the theme. The selection is saved in the
settings file described above. Fresh installs, and missing, invalid or unreadable
settings, use **IDgent Dark**; a theme you saved before is kept.

- **IDgent Dark** — Reference app-like neutral greys with a purple accent; the default.
- **IDgent Light** — the light counterpart: white and soft grey with a purple accent.
- **Dark+** — charcoal with familiar blue and purple syntax accents.
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
| Ctrl+P / Ctrl+O | Quick Switcher (not while a terminal has focus) |
| Ctrl+Shift+P | Command Palette (everywhere, terminals included) |
| Ctrl+, | Open Settings |
| F1 | Open Settings on the Hotkeys page (not while a terminal has focus) |
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
| Esc | Close the Quick Switcher / Command Palette; in the explorer search box or results: clear the query, back to the editor |
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

No GtkSourceView, persisted window geometry/layout/session, or Wayland-specific testing.
GTK 3 CSS limits the Reference app look: no tree indent guides, no concave curves under
the active tab, and menus/tooltips are separate windows whose rounded corners and
shadows depend on the compositor. Ctrl+P inside the explorer tree opens the Quick
Switcher instead of GTK's emacs-style "move up".
Ctrl+Q is the app quit key, including when a terminal has focus.
