STATUS: DONE

Changes
- Created empty idgent/__init__.py, module/console entry points, directory validation,
  and Gtk.Application with ID dev.idgent.IDgent.
- Added MainWindow with 1200x750 default size, resizable explorer/editor/terminal
  layout, terminal bottom/right move preserving panel identity, visibility toggles,
  menus/shortcuts, explorer signal wiring, folder switching, and save/discard/cancel
  confirmation for unsaved files on quit.
- Added temporary contract-compatible placeholder panels and integration TODO.
- Added dependency-free pyproject.toml metadata and README covering launch,
  shortcuts, project layout, and the complete fixed panel contract.
- Added tests/test_window_layout.py using unittest (pytest is not installed).
- Created packaging/ empty as requested; Git does not track empty directories.
- Included the supplied .ceo/task.md unchanged as task metadata so the final
  worktree is clean. No sibling panel files or dependency/lockfiles were changed.

Verification
- python3 -c "import idgent.window": PASS.
- xvfb-run -a python3 -m unittest tests.test_window_layout -v: PASS, 7 tests.
  Covers default bottom dock, same terminal widget moved right and back,
  hide/move/show, explorer toggle, default dimensions/title, folder propagation,
  explorer signal forwarding, and unsaved quit decisions including failed saves.
- timeout 5 xvfb-run -a python3 -m idgent /tmp: PASS, exit 124 as expected;
  no stderr output or traceback.
- python3 -m idgent /this-folder-does-not-exist: PASS, argparse error and exit 2.
- git diff --check: PASS.

Open issues / assumptions
- Real explorer/editor/terminal implementations are developed on sibling branches;
  this worktree launches with placeholders. Integration must remove fallbacks.
- Layout is intentionally not persisted. System GTK/PyGObject and VTE are required
  for the complete application; no package installation was performed.
