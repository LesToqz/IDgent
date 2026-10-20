# Report: notes (slug: notes)

STATUS: DONE

## Follow-up
- `tests/test_titlebar_dock.py`: in test_button_accessibility_geometry_fade_and_keyboard_activation the right-dock
  button now expects graph_count()==1 and terminal_count()==0. Nothing else in that file changed. Commit
  `test(titlebar): right-dock button opens a graph by default`.
- Targeted set plus tests.test_titlebar_dock: 135 tests, OK.
- Full suite (`discover -s tests`): the first run died with a segmentation fault (core dumped) after repeated
  Gtk/Gdk CRITICAL messages, which matches the known flake. I didn't identify the test. The rerun gave
  898 tests, OK (skipped=2). The segfault is unfixed and I didn't investigate it.

## What changed
- `idgent/notes.py` (new):
  - The notes folder is `<root>/.IDgent/Notes/`, created on the first note.
  - `note_filename` maps `coffi shop` to `coffi shop.md`, keeping spaces and stripping a typed
    `.md`. It refuses empty names and names containing `/`, `.`, `..` or NUL by raising
    `NoteNameError`, which subclasses `fsops.FsOpError` so the name dialog stays open and shows it.
  - `create_note` uses exclusive creation and never overwrites; an existing name gets a suffix
    (`coffi shop 2.md`). A new note starts with `# <name>`.
  - `list_notes` and `is_note`.
  - Ten insert commands are pure text edits (`apply_command` returns an `Edit`): Heading, Bold,
    Italic, Bullet list, Numbered list, Checklist (`- [ ] `), Table (3 columns: header, separator,
    one empty row), Code block, Link, Horizontal rule.
  - `apply_to_buffer` applies a command as one undoable user action. `NoteToolbar` is the button
    row. `build_css` has flat, instant hovers and no transitions.
- `idgent/explorer.py`:
  - The header now reads `[Notes] [New File] [New Folder] [Refresh]`, matching the screenshot.
  - "Notes" is a toggle tab. Pressed shows the Notes list; released shows the Explorer.
  - In the Notes tab, New File becomes "New Note", New Folder is hidden, and Refresh and F5
    reload the list.
  - Clicking a note emits `file-activated`, which opens it in the editor.
  - The Notes page swaps in for the search panel under the header. I didn't use a Gtk.Stack because
    `tests/test_search_wiring.py` (not owned) requires the explorer's children to be exactly
    `[header, search]`. The detached page is destroyed together with the explorer.
  - Search, F2 and Delete never act on the hidden tree while Notes is shown, and `focus_search`
    switches back to the Explorer.
- `idgent/titlebar.py`:
  - New file explorer toggle beside the View menu (`sidebar-show-symbolic`, falling back to
    `panel-left-symbolic`, then the glyph ▯).
  - New below-terminal toggle immediately left of the right-dock toggle (`panel-bottom-symbolic`,
    then ▭).
  - All three use one `_toggle_icon` builder in the cog's clickable-icon style with its glyph fade.
  - Added `set_explorer_active`, `set_bottom_dock_active` and `set_dock_active`. These syncs never
    emit a toggle request.
- `idgent/window.py`:
  - All three toggles stay in sync with their panels through `notify::visible`, whichever way the
    panel is toggled (menu, hotkey, palette or icon).
  - `toggle_right_dock()` handles the titlebar click. An empty right dock opens with a graph view;
    if the graph cannot be created it falls back to a terminal.
  - Note tabs get a `NoteToolbar` above the editor. It is an overlay under the tab strip, and the
    existing Markdown pill sits at its right. It applies to source and preview modes and is removed
    when a note is renamed away or deleted.
  - Commands switch a previewed note back to source before inserting.
  - New palette entries: "Notes: New note", "Notes: Show notes", and "Notes: <Heading … Horizontal
    rule>". These work in any Markdown tab; other files get a notice.
- `idgent/style.py`: includes `notes.build_css`. `tests/test_style.py` has no hash baseline, so it
  needed no change.
- `idgent/palette.py`: unchanged. Commands are registered through the window's existing
  `_add_commands`.
- `idgent/mdview.py` and the graph files are untouched.
- Tests:
  - `tests/test_notes.py` (new, 23 tests).
  - `tests/test_explorer_panel.py`: 5 new tests, and 2 updated for the new Notes button.
  - `tests/test_titlebar.py`: 2 new tests. Updated the icon lookup chain, the geometry assertions,
    and the minimum-window-width bound (336 → 400), since the two new 28 px buttons add width.
  - `tests/test_window_wiring.py`: 4 new tests.

## Verification
- Targeted (acceptance 2):
  `XDG_CONFIG_HOME=$(mktemp -d) GDK_BACKEND=x11 dbus-run-session -- xvfb-run -a python3 -W ignore::DeprecationWarning -m unittest tests.test_notes tests.test_explorer_panel tests.test_titlebar tests.test_window_wiring tests.test_palette -v`
  → `Ran 132 tests ... OK`.
- Full suite (acceptance 3), `... -m unittest discover -s tests -v`:
  - Run 1 segfaulted in `test_search_wiring.test_activating_result_opens_tab_and_selects_exact_match`,
    after an earlier FAIL in `test_gitviews.HostTest.test_coalesced_refresh_stale_data_and_digest`.
  - Both modules pass alone (18 OK and 17 OK). The base commit 4ce9320's suite passed
    (864 OK, skipped=2).
  - Run 2 on this branch: `Ran 898 tests, FAILED (failures=1, skipped=2)`. The only failure is the
    blocked `test_titlebar_dock` assertion above; the segfault and gitviews failure did not recur.
    I treat run 1 as a one-off flake.
  - Logs: `.ceo/full-suite.log`, `.ceo/full-suite-2.log`, `.ceo/base-suite.log`.
- Every test run used a temp XDG_CONFIG_HOME; the window tests patch it per test as before.
- `git grep -i` for the forbidden product word: no matches in code, tests or commit messages.

## Screenshots (real MainWindow under xvfb, not committed)
- `.ceo/shots/notes-tab-idgent-dark.png`, `.ceo/shots/notes-tab-idgent-light.png`: Notes tab
  (pressed), the note list, the note toolbar, the titlebar explorer toggle beside View, and the
  below/right dock toggles. The right dock was opened with its toggle and shows the graph view.
- `.ceo/shots/explorer-tab-idgent-dark.png` / `-light.png`: Explorer tab with the
  `Notes | new file | new folder | refresh` header (as in the mockup); `.IDgent/Notes` is visible in the tree.
- `.ceo/shots/notes-preview-*.png`: the existing preview rendering the inserted Markdown. Table
  rendering is the mdview worker's job.
- Script: `.ceo/shot.py`. I looked at all of them myself.

## Assumptions / open issues
- "Notes" is a text tab button, as in the screenshot, not an icon. The Explorer tab is its released
  state, so the header layout stays exactly as in the mockup.
- The titlebar explorer icon toggles the whole sidebar (same as Ctrl+B), whichever tab is shown.
- A new note starts with `# <name>`. Duplicate names get " 2", " 3", … instead of asking.
- List commands replace an existing list marker (`- `, `1. `, `- [ ] `) rather than nesting.
  Heading deepens an existing heading, up to level 6.
- Table, Code block and Horizontal rule insert as their own block, separated from adjacent
  paragraphs by a blank line, so `---` never turns the line above into a setext heading. Table and
  rule are inserted after a selection and keep it.
