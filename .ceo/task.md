# Task: editor (branch ceo/codex/editor)

Read AGENTS.md first. Work only in this worktree.

## Product
IDgent: a VS Code-style desktop IDE for Linux. Python 3 + GTK 3 (PyGObject) + VTE.
No npm, no compiler, no pip installs: use ONLY the stdlib and `gi` (Gtk 3.0, Gdk, GLib, Gio, Pango).
GtkSource is NOT installed -> plain Gtk.TextView. Siblings build the window shell, explorer and terminal in PARALLEL on other branches.
You build ONLY the tabbed text editor panel.

## FIXED CONTRACT (do not change names/signatures)
idgent/editor.py:
  class EditorPanel(Gtk.Box):
    __init__(self)
    open_file(self, path: str) -> None
    save_current(self) -> bool            # True on success / nothing to save
    save_all(self) -> bool
    has_unsaved(self) -> bool
    on_path_renamed(self, old: str, new: str) -> None   # old may be a FOLDER: retarget every open tab whose path is under it
    on_path_deleted(self, path: str) -> None            # path may be a FOLDER: tabs under it are kept open but marked as deleted ("(deleted)" in title), content preserved so user can Save As / re-save

## You own (create/edit ONLY these)
idgent/editor.py
tests/test_editor_panel.py
Create an EMPTY idgent/__init__.py ONLY if absent (other workers create the identical empty file). Leave it empty.

## Requirements
1. Gtk.Notebook with scrollable tabs; each tab = Gtk.ScrolledWindow + Gtk.TextView (monospace font via Pango "Monospace 11", line wrap off,
   left/right margins, tabs-as-4-spaces display) and a tab label with file name, close button (x), and a "●" dirty marker prefix when modified.
   Tooltip on tab = full path.
2. open_file: if already open, switch to it. Else read as UTF-8 (errors="replace" is NOT acceptable silently: on UnicodeDecodeError or binary
   content (NUL bytes) show an info bar/dialog "cannot open binary or non-UTF-8 file" and do not open). Refuse files > 5 MB with a message.
   Missing file -> message, no tab. Non-regular files rejected.
3. Empty state: when no tabs, show a centered placeholder label "IDgent  -  open a file from the explorer".
4. Saving: save_current writes atomically (write temp file in same dir + os.replace), preserves file mode, keeps trailing content exactly (do NOT add newline).
   On failure show an error dialog and return False. Clears dirty marker. Detect external modification (mtime changed since load/last save):
   ask "File changed on disk. Overwrite?" before saving. save_all returns False if any save failed/cancelled.
5. Closing a dirty tab (x button, Ctrl+W when editor has focus) asks Save / Discard / Cancel. Ctrl+Tab / Ctrl+Shift+Tab (or Ctrl+PageDown/Up) switch tabs.
6. Editing niceties: Ctrl+Z / Ctrl+Shift+Z undo/redo (implement a simple undo stack on the TextBuffer via begin/end user action; or use
   TextBuffer signals — GtkTextView has no built-in undo), auto-indent on Enter (copy previous line leading whitespace), Tab inserts 4 spaces,
   Shift+Tab unindents the line, Ctrl+/ toggles a '#'-style comment ONLY for known extensions (.py .sh .toml .yaml .yml .conf) else no-op.
   Status line under the notebook: "Ln X, Col Y  |  UTF-8  |  <LF|CRLF>" (detect line endings on load and PRESERVE them on save).
7. Find bar (Ctrl+F when editor focused): small Gtk.SearchBar with entry, next/prev, "not found" styling; plain-text, case-insensitive. Enough, no replace.
8. Dirty tracking via buffer "modified-changed"; has_unsaved checks all tabs.
9. Code: typed, small functions/classes (e.g. a private `_EditorTab` class), no dead code. Factor logic (file read/validate, atomic write,
   line-ending detection, indent helpers) into module-level pure functions so they can be unit tested without Gtk.
   Dialogs must be factored so tests can monkeypatch a single `_ask`/`_message` helper instead of showing UI.

## Acceptance tests (run them; report results)
- `xvfb-run -a python3 -m unittest tests.test_editor_panel -v` covering: pure helpers (binary detection, size limit, CRLF round-trip,
  atomic write keeps mode, no added trailing newline), open/switch/duplicate open, dirty marker + has_unsaved, save_current/save_all,
  on_path_renamed for file AND folder prefix, on_path_deleted for file and folder, closing dirty tab (monkeypatched answers), external-modification prompt,
  auto-indent / Tab / Shift+Tab / comment toggle, undo/redo.
- Smoke: `timeout 5 xvfb-run -a python3 -c "..."` showing EditorPanel in a Gtk.Window with one opened file produces no traceback.

## Do NOT
Touch window/app/explorer/terminal files, pyproject, README, AGENTS.md or CLAUDE*.md.

## Commit + report
Conventional commits `feat(editor): ...`, `test(editor): ...`. Finish with `.ceo/report.md` per AGENTS.md (commit it last; git status clean).
