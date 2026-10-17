# Task: skeleton (branch ceo/codex/skeleton)

Read AGENTS.md first. Work only in this worktree.

## Product
IDgent: a VS Code-style desktop IDE for Linux. Python 3 + GTK 3 (PyGObject) + VTE 2.91.
No npm, no compiler, no pip installs available: use ONLY the stdlib and
`gi` (Gtk 3.0, Gdk, GLib, Gio, Vte 2.91). GtkSource is NOT installed -> plain Gtk.TextView.
Run with `python3 -m idgent [folder]`.

## Your goal
Build the application shell: window, layout, terminal-dock toggle, app entry.
Three sibling workers build the panels in PARALLEL on other branches, so you must
code against the FIXED CONTRACT below and NOT create their files.

## FIXED CONTRACT (all workers rely on this)
All panels are Gtk.Box subclasses (Gtk 3.0).

idgent/explorer.py  (owned by explorer worker)
  class ExplorerPanel(Gtk.Box):
    __init__(self, root: str)
    set_root(self, path: str) -> None
    refresh(self) -> None
    signals: "file-activated"(path: str), "path-renamed"(old: str, new: str), "path-deleted"(path: str)
idgent/editor.py  (owned by editor worker)
  class EditorPanel(Gtk.Box):
    __init__(self)
    open_file(self, path: str) -> None
    save_current(self) -> bool
    save_all(self) -> bool
    has_unsaved(self) -> bool            # confirm-on-quit handled by window using this
    on_path_renamed(self, old: str, new: str) -> None
    on_path_deleted(self, path: str) -> None
idgent/terminal.py  (owned by terminal worker)
  class TerminalPanel(Gtk.Box):
    __init__(self, cwd: str)
    set_cwd(self, path: str) -> None     # cwd for NEW terminals
    new_terminal(self) -> None
    close_current(self) -> None

## You own (create/edit ONLY these)
idgent/__init__.py, idgent/__main__.py, idgent/app.py, idgent/window.py,
idgent/_placeholder.py, pyproject.toml, README.md, tests/test_window_layout.py, packaging/ (leave empty)

## Requirements
0. `idgent/__init__.py` MUST be an EMPTY file (other workers create the identical empty file; avoids merge conflicts).
1. `idgent/app.py`: Gtk.Application, id `dev.idgent.IDgent`, window title "IDgent - <folder name>".
   `__main__.py`: optional CLI arg = project folder; default = cwd. Validate it is a directory.
2. `idgent/window.py` `MainWindow(Gtk.ApplicationWindow)`, default size 1200x750.
   Layout like VS Code: header/menu bar; LEFT = ExplorerPanel (fixed-ish width ~260, resizable);
   CENTER = EditorPanel; TERMINAL = TerminalPanel docked either BELOW the editor or to the RIGHT of it.
   Use Gtk.Paned so users can drag sizes. Terminal position is a toggle: "bottom" (default) | "right".
   Moving the terminal must re-parent the SAME TerminalPanel widget (do not destroy running shells).
   Terminal can be hidden/shown.
3. Shortcuts + menu items (View menu): Ctrl+` toggle terminal; "Move Terminal to Right" / "Move Terminal to Bottom"
   (single menu item whose label flips); Ctrl+B toggle explorer sidebar; Ctrl+S save; Ctrl+Shift+S save all;
   Ctrl+Q quit (prompt if editor.has_unsaved()). File menu: Open Folder... (changes explorer root and terminal cwd), Save, Quit.
4. Wiring: explorer "file-activated" -> editor.open_file; "path-renamed" -> editor.on_path_renamed;
   "path-deleted" -> editor.on_path_deleted. Open Folder -> explorer.set_root + terminal.set_cwd + title update.
5. Panels are imported lazily with fallback: `try: from idgent.explorer import ExplorerPanel except ImportError: from idgent._placeholder import ExplorerPanel`
   (same for editor/terminal). `_placeholder.py` defines minimal stand-in classes honouring the contract
   (a labelled Gtk.Label inside the Box; signals declared on the explorer stand-in; methods are no-ops/return True/False).
   The integration worker will delete the fallback later; mark it with a `# TODO(integration): remove fallback` comment.
6. Persist nothing yet. Keep code small, typed, readable, no dead code.
7. README.md: what it is, how to run, shortcut table, project layout, the panel contract.
8. pyproject.toml: name idgent, python>=3.10, console script `idgent = idgent.app:main`, no dependencies (PyGObject is system-provided).

## Acceptance tests (run them; report results)
- `python3 -c "import idgent.window"` succeeds.
- `xvfb-run -a python3 -m pytest -q tests/test_window_layout.py` if pytest exists, else write the test with `unittest`
  and run `xvfb-run -a python3 -m unittest tests.test_window_layout -v`. Test must check (with placeholders):
  default terminal position is "bottom"; toggling moves the SAME terminal widget object to "right" and back;
  hide/show terminal; explorer toggle; window title contains folder name.
- Smoke: `timeout 5 xvfb-run -a python3 -m idgent /tmp` exits by timeout (rc 124) with no traceback on stderr.

## Do NOT
Touch idgent/explorer.py, editor.py, terminal.py, or any file not listed. Do not change AGENTS.md/CLAUDE*.md/CLAUDE-CEO.md.

## Commit + report
Conventional commits `feat(skeleton): ...`. Finish with `.ceo/report.md` per AGENTS.md (commit it last; git status clean).
