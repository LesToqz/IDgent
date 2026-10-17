STATUS: DONE

Files changed
- idgent/window.py: whole-window terminal fullscreen for either dock, exact visibility/no-show-all and divider-position restoration, layout-command restoration, View menu and Ctrl+Shift+F11 dispatch.
- idgent/style.py: append TITLEBAR_CSS and remove redundant global menubar rules; keep strict parsing and single-provider registration.
- tests/test_window_layout.py: poll final animated close; exercise real chip buttons, both fullscreen docks, previous visibility/dividers, parent show_all(), commands, last-close restoration, respawn during exit animation, and independent 3+3 limits/notices/split refusal.
- tests/test_integration.py: verify menu accelerator plus keyboard events from editor and terminal focus.
- tests/test_style.py: verify TITLEBAR_CSS belongs to the registered theme.
- README.md: merged top bar, dock chips/buttons/limits, animations, fullscreen behavior and fallback shortcut.

Verification
- xvfb-run -a python3 -m unittest discover -s tests -p test_window_layout.py: 15 tests passed.
- xvfb-run -a python3 -m unittest discover -s tests: final two runs passed all 128 tests (16.557 s and 15.465 s). An earlier run exposed editor-focused key consumption; fixed in the window key dispatcher before both passing runs.
- GDK_BACKEND=x11 timeout 8 xvfb-run -a python3 -m idgent /tmp: rc 124, empty stdout/stderr.
- git diff --check: clean.
- Screenshot capture: GDK_BACKEND=x11 xvfb-run -a -s '-screen 0 1600x1000x24' python3 with real MainWindow, apply_style(), 1400x850 default size, unchanged TerminalPanel.ANIMATION_MS, ~1 second main-loop iteration before every Gdk.pixbuf_get_from_window capture. All five PNGs visually inspected (1410x893 including window decoration).

Screenshots (git-ignored, uncommitted)
- .ceo/shots/01-default.png
- .ceo/shots/02-three-bottom-three-right.png
- .ceo/shots/03-limit-notice.png
- .ceo/shots/04-fullscreen-bottom.png
- .ceo/shots/05-fullscreen-right.png

Open issues / assumptions
- Ctrl+Shift+F conflicts: idgent/editor.py:377 handles Ctrl+F/F regardless of Shift. Used the explicitly allowed Ctrl+Shift+F11 fallback and documented it. Esc has no fullscreen binding.
- Resolved the earlier show_all() concern: a real window-level test calls window.show_all() while each dock is maximized and verifies hidden layout widgets and sibling terminal panes stay hidden, then confirms previous layout restoration.
- Existing TerminalPanel.new_terminal() already enforces the per-dock limit and ignores closing panes in its live count; retained this behavior and verified split refuses a fourth terminal and reopening during final exit animation stays visible.
- Existing deprecation warnings and the desktop validator category hint remain in test output.

FOLLOW-UP
- None. No visible defects requiring changes outside owned files were found.
