# Task: terminal (branch ceo/codex/terminal)

Read AGENTS.md first. Work only in this worktree.

## Product
IDgent: a VS Code-style desktop IDE for Linux. Python 3 + GTK 3 (PyGObject) + VTE 2.91 (installed: Vte 0.84, `gi.require_version('Vte','2.91')`).
No npm, no compiler, no pip installs: use ONLY the stdlib and `gi` (Gtk 3.0, Gdk, GLib, Gio, Pango, Vte 2.91).
Siblings build the window shell, explorer and editor in PARALLEL on other branches. You build ONLY the terminal panel.
The window will dock this panel either BELOW or to the RIGHT of the editor and may re-parent the SAME widget between them: it must keep working (shells keep running) after being re-parented and must size sensibly in both positions.

## FIXED CONTRACT (do not change names/signatures)
idgent/terminal.py:
  class TerminalPanel(Gtk.Box):
    __init__(self, cwd: str)             # opens ONE terminal tab in cwd on construction
    set_cwd(self, path: str) -> None     # cwd for NEW terminals only; running shells untouched
    new_terminal(self) -> None
    close_current(self) -> None

## You own (create/edit ONLY these)
idgent/terminal.py
tests/test_terminal_panel.py
Create an EMPTY idgent/__init__.py ONLY if absent (other workers create the identical empty file). Leave it empty.

## Requirements
1. Panel = a slim header row ("TERMINAL" label, "+" new-terminal button, trash/"x" close-current button, tooltips) above a Gtk.Notebook
   (tabs at top, scrollable, tab label "bash 1", "bash 2"... derived from the shell basename + counter, with a close button per tab).
   Each tab hosts a Vte.Terminal inside a Gtk.ScrolledWindow-free layout (Vte scrolls itself; add a Gtk.Scrollbar bound to its vadjustment on the right).
2. Spawn the user's shell: `$SHELL` if executable else /bin/bash, as a login-less interactive shell, in the panel's cwd (fallback to $HOME if the cwd is invalid),
   with env inherited plus TERM=xterm-256color, COLORTERM=truecolor. Use `spawn_async` (the non-deprecated API; check its exact PyGObject signature for Vte 0.84).
3. When the shell exits (child-exited): close that tab automatically; if it was the last tab, open a fresh terminal so the panel is never empty.
   close_current on the last tab behaves the same (kill the shell, open a fresh one).
4. Keyboard (when a terminal has focus): Ctrl+Shift+C copy, Ctrl+Shift+V paste, Ctrl+Shift+T new terminal, Ctrl+Shift+W close current.
   Right-click context menu: Copy, Paste, Select All, Clear. Middle-click paste is VTE default; leave it.
5. Appearance: font "Monospace 11", dark background (#1e1e1e) / light foreground (#d4d4d4), a 16-colour palette similar to VS Code dark, cursor blink on,
   scrollback 10000 lines, scroll on output off / on keystroke on, bell off, mouse autohide on.
6. Ctrl+Shift+Plus / Ctrl+Shift+Minus / Ctrl+Shift+0 zoom the font (font_scale between 0.5 and3).
7. Clean shutdown: on the panel's "destroy", send SIGHUP/terminate the child shells so no orphans remain (use the pid reported by spawn_async).
8. `grab_focus` on the active terminal after creation and after tab switch; expose a method `focus_terminal()` (extra public helper is allowed).
9. Code: typed, small functions, no dead code. Factor pure helpers (`pick_shell()`, `safe_cwd(path)`, `tab_title(shell, n)`) as module-level functions for unit tests.

## Acceptance tests (run them; report results)
- `xvfb-run -a python3 -m unittest tests.test_terminal_panel -v` covering: pure helpers; panel starts with exactly one tab; new_terminal adds; close_current;
  closing the last tab leaves one fresh tab; set_cwd affects only NEW terminals (spawn a terminal, run `pwd` via feed_child, process the GLib main loop
  until the output appears in the terminal text (get_text), assert the cwd, with a timeout); shell exit (send `exit`) closes the tab and keeps >=1; re-parenting the panel
  between a vertical and a horizontal Gtk.Paned keeps the shell alive (type `echo ok` after re-parenting and see it); destroying the panel leaves no child process.
  Tests must be robust (poll the main loop up to ~5 s) and not flaky.
- Smoke: `timeout 5 xvfb-run -a python3 -c "..."` showing TerminalPanel in a Gtk.Window produces no traceback.

## Do NOT
Touch window/app/explorer/editor files, pyproject, README, AGENTS.md or CLAUDE*.md.

## Commit + report
Conventional commits `feat(terminal): ...`, `test(terminal): ...`. Finish with `.ceo/report.md` per AGENTS.md (commit it last; git status clean).
