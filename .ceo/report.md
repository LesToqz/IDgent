STATUS: DONE

Task: glitch-integration. Branch: ceo/codex/resize-glitch. No trunk, forbidden source files, dependencies or lockfiles changed. The previous report was absent from disk; read it from commit f828884 before implementation.

Files changed
- idgent/terminal.py: each complete pane page (header, terminal, scrollbar, reserved gutter and divider) is inside its own ResizeGate, directly under the panel body. The header remains in its existing inner box, with its existing height and packing. pane.page still names the contents; pane.frame names the body child (gate, or page for the control). Body ordering, removal/destruction, maximize/restore visibility, divider/corner bookkeeping and fades use frame. Focus, native VTE input/mouse/menu handlers and scrollbar adjustment handling remain in place. RESIZE_GATE defaults to True and permits instance overrides. Existing/new/closing gates follow apply_palette background changes.
- idgent/resizegate.py: optional CHILD_MINIMUM preserves the original page's size requests for application integration. TerminalPanel opts in; standalone gate defaults and clipping/debounce/reflow behavior remain intact. This avoids an observed one-pixel dock sizing regression from replacing the original page minimum with zero.
- TerminalPanel commits discrete add/remove, maximize/restore, font zoom and dock map/unmap changes through a one-shot frame-clock after-paint callback, after GTK layout. Traversing the toplevel also commits the other affected dock when one dock is shown/hidden. Pending callbacks are disconnected on window destruction. Divider position notifications do not force commits. First allocation remains immediate.
- tests/test_resizegate.py: configure terminals through a temporary panel instance. Both independent control/gated harnesses now move together in one timing loop; all four speed/direction cases retain three sweeps and strict visible/history-prefix and complete garbage assertions. Use 8-pixel steps at 8/24 ms for this standalone matrix (222 updates per scenario), and 250 ms final settling. Allocation timing, WINCH, single-resize, history preservation, lifecycle, clipping and fatal-criticals checks remain.
- tests/test_terminal_panel.py: frame-aware geometry assertions; gate/control composition, focusability, preserved size requests, palette propagation and immediate discrete layout commits (with a 10-second debounce to exclude fallback).
- tests/test_terminal_bar.py: update existing visibility, lifecycle, geometry and animation assertions to inspect the body frame.
- tests/test_window_layout.py: update visibility assertions; new real MainWindow regression with six actual bash PTYs in both docks, exact colour PS1, blank lines before the first prompt, 1400x850 window, X11, three right-divider sweeps and three bottom-divider sweeps at 4 pixels every 8 ms. Shell spawning is deferred until all panes are allocated, then uses /bin/bash --norc -i for reproducible startup. No synthetic prompt text, clearing, terminal reset or injected redraw is used. Reuses BashDrag's independent prefix/history/garbage metric and requires a strictly worse ungated aggregate control. Saves both requested screenshots.

Verification
- xvfb-run -a python3 -m unittest discover -s tests: PASSED TWICE, 245 tests each, 83.411 s and 83.180 s. Logs: .ceo/suite-final-{1,2}.log. No GTK warnings/criticals, callback tracebacks or signal-cleanup warnings. Existing Python deprecations and desktop-file category hint remain.
- xvfb-run -a python3 -m unittest tests.test_resizegate: FIVE consecutive final passes, 14 tests each, 25.405 / 25.370 / 25.433 / 25.327 / 25.487 s; zero flakes. Logs: .ceo/gate-final-{3,4,5,6,7}.log. Down from approximately 80 s to approximately 25.4 s, below the 30 s target. Earlier final passes also green.
- G_DEBUG=fatal-warnings xvfb-run -a python3 -W ignore -m unittest tests.test_terminal_panel: 24 tests passed, 3.408 s (.ceo/panel-final.log).
- Additional real-window G_DEBUG=fatal-warnings check: show/hide right and bottom docks with all gates' SETTLE_MS=10000; final targets equalled committed allocations within 100 ms after each action. PASS.
- GDK_BACKEND=x11 timeout 8 xvfb-run -a python3 -m idgent /tmp: rc 124, empty stdout/stderr (.ceo/app-smoke.stdout and .ceo/app-smoke.stderr).
- git diff --check: clean.
- Screenshot capture rerun: xvfb-run -a -s '-screen 0 1600x1000x24' python3 -W ignore -m unittest tests.test_window_layout.WindowResizeRegressionTests. PASSED, 1 test in 15.258 s (.ceo/window-shots-final.log). Larger virtual screen accommodates the complete 1400x850 window; inspected both images. Screenshots are ignored and not committed.

Real-window BEFORE / AFTER
Order: bottom panes 1/2/3, right panes 1/2/3. Both divider orientations are exercised in each run.
- Ungated visible/history prefix counts: [1, 1, 1, 3, 3, 3], aggregate 12. Garbage: [false, false, false, true, true, true].
- Gated visible/history prefix counts: [1, 1, 1, 1, 1, 1], aggregate 6. Garbage: all false. Exactly one complete expected prompt per terminal, including scrollback.
- This particular control accumulates corruption in the right dock; the bottom control remains clean after the combined horizontal and vertical drags. The baseline guard compares the total across all six terminals; the gated assertions apply individually to all six.
- Screenshots: .ceo/shots/after-drag-gated.png and .ceo/shots/after-drag-control.png (full windows, not committed).

Assumptions / limitations / FOLLOW-UP
- Retains the prior gate's documented VTE workaround: temporarily suspend rewrap-on-resize for committed allocations, then restore the property. Historical physical rows retain their original wrapping; narrowing can hide right-hand cells until widened. This tradeoff is necessary because even one reflow can corrupt readline's prompt. No shell input or history deletion is used.
- Gate CSS styling remains the later task's responsibility; background colour is already applied explicitly through the panel palette.
- Existing keyboard, focus, selection/menu event wiring is preserved. Full manual IME composition was not performed; native VTE continues receiving input directly.
- No changes required in forbidden files. No missing credentials, dependencies or product decisions.

Implementation commits: 90e070a and 9e22bd4. Report is committed last; git status is clean after its commit. Exploratory failures (insufficient one-sweep growing control, initial minimum-size mismatch, and new-test cleanup ordering) were corrected before the final acceptance runs; their ignored logs remain for audit.
