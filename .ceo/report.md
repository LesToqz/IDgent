STATUS: DONE

Files changed
- idgent/window.py: MainWindow.set_theme resolves fallback themes, updates shared CSS,
  editor syntax/chrome and both terminal palettes without respawning shells, synchronizes
  radio selection and optionally persists. Construction loads settings before showing.
  View > Theme follows catalogue order; Next Theme cycles/wraps with Ctrl+Alt+T.
- idgent/app.py: startup applies the saved theme before constructing/showing a window;
  recreating a window reloads the saved preference.
- pyproject.toml: optional highlight = ["pygments"] extra; dependencies remains empty.
- tests/test_window_layout.py: menu order/radio selection, persistence, new-window
  restoration, fallback, persist=False, accelerator cycling/wrapping; temporary config.
- tests/test_integration.py: realized CSS colours, Python syntax and chrome tag colours,
  real VTE foreground/background/16 ANSI setter calls and drawn background, shell PIDs
  and commands, new tabs/terminals, actual Application startup and recreation, malformed,
  unknown and unreadable settings; temporary config and isolated startup subprocesses.
- README.md: rewritten against current implementation, including themes, layout,
  highlighting, installation, resize behaviour, limitations and complete shortcut table.

Verification
- GDK_BACKEND=x11 xvfb-run -a python3 -m unittest discover -s tests -p test_integration.py:
  16 tests passed after fixes.
- GDK_BACKEND=x11 xvfb-run -a python3 -m unittest discover -s tests -p test_window_layout.py:
  25 tests passed.
- xvfb-run -a python3 -m unittest discover -s tests, with GDK_BACKEND=x11 and a temporary
  XDG_CONFIG_HOME: 267 tests passed TWICE (87.711s and 87.511s).
  Logs: .ceo/acceptance-1.log and .ceo/acceptance-2.log (not committed).
- GDK_BACKEND=x11 timeout 8 xvfb-run -a python3 -m idgent /tmp, with temporary
  XDG_CONFIG_HOME: rc 124, stderr empty (0 bytes); .ceo/smoke.stderr not committed.
- git diff --check and git diff --check 259eb89..HEAD: clean.
- README reviewed against window.py, editor.py, explorer.py, terminal.py, themes.py,
  settings.py, highlight.py, resizegate.py and packaging/install.sh.
- Existing suite emits GI/asyncio deprecation warnings and desktop validator hints;
  acceptance remained green and the app smoke emitted no stderr.

Shortcuts verified against code
- Window/File/View: Ctrl+S, Ctrl+Shift+S, Ctrl+Q, Ctrl+B, Ctrl+grave,
  Ctrl+Alt+grave, Ctrl+Shift+5, Ctrl+Shift+F11, Ctrl+Alt+T.
  No Ctrl+Alt+T conflict existed; no fallback accelerator was needed.
- Editor: Ctrl+W, Ctrl+Z/Ctrl+Shift+Z, Ctrl+F (also Ctrl+Shift+F),
  Ctrl+Tab/Ctrl+Shift+Tab, Ctrl+PageDown/PageUp, Ctrl+slash,
  Ctrl+Plus/equal/Minus/0 and keypad equivalents, Ctrl+wheel, Tab/Shift+Tab, Enter.
  Search entry Enter advances the search; inherited GTK editing bindings remain.
- Terminal: Ctrl+Shift+T/W/C/V, Ctrl+Shift+5, Ctrl+Alt+Left/Right
  (also Up/Down in the stacked dock), Ctrl+Shift+Plus/equal/Minus/0 and
  supported shifted/keypad variants. Ctrl+S forwards to the shell when focused;
  File > Save still saves. Esc is not a terminal fullscreen binding.
- Explorer: F2, Delete/keypad Delete, F5, Ctrl+Shift+N.
- Menu accelerators, fullscreen key dispatch and theme cycle/wrap are exercised
  by the GTK integration/layout tests; the remaining bindings were source-reviewed.

Final visual review (not committed)
Rendered under GDK_BACKEND=x11 xvfb-run -a with a 1600x1000 Xvfb screen.
Every screenshot is 1410x893, from the real MainWindow after set_theme(id), with
idgent/app.py open, caret on the gi.require_version bracket line, both docks open
with two bash terminals each, printed shell prompts, and 1.1 seconds of main loop
per theme. Reviewed every theme in the contact sheet and inspected full-size
Light+ and IDgent Midnight samples. Chrome, gutter, brackets, syntax, status,
headers and terminal backgrounds follow each theme without layout problems.

Screenshots in .ceo/shots/:
- final-dark-plus.png
- final-light-plus.png
- final-monokai.png
- final-dracula.png
- final-solarized-dark.png
- final-solarized-light.png
- final-nord.png
- final-one-dark.png
- final-github-dark.png
- final-idgent-midnight.png
- final-idgent-ember.png
- final-idgent-paper.png
- final-contact.png (scaled contact sheet, 1400x2700)

Assumptions
- Existing settings API supplies failure-tolerant reads and best-effort writes.
- Existing style API intentionally shares one screen CSS provider; normal application
  operation has one window. Window theme changes use the existing editor/VTE APIs.
- No forbidden source files, packaging files, or other dependencies were changed.

FOLLOW-UP
- Visuals show fragments of the first bash prompt at the top of right terminals,
  apparently from the narrow initial allocation before layout settles. Later prompts
  are intact. Consider delaying initial shell spawning until its first stable geometry
  in idgent/terminal.py/resizegate.py. These files are outside this task's ownership;
  no changes made. Existing output deliberately does not re-flow during resize.
