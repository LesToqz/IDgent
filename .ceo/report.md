STATUS: DONE

## Root cause
`ResizeGate` (idgent/resizegate.py) is supposed to hold the next terminal grid
until the shell has answered the previous SIGWINCH, because readline computes
its redraw for one width. It took **any** `contents-changed` as that answer.
VTE emits `contents-changed` for its own resize about one update (~15 ms)
later, even when the child has written nothing. This was verified with bash
stopped by SIGSTOP: the gate released at about 15 ms. So the gate committed
the next width while bash's redraw was still in flight. When that redraw
(computed for a wider grid, where the prompt fits on one row) is parsed at a
narrower grid, VTE autowraps it onto two rows. Readline still believes it is
one row, so its next redraw clears only the lower row and leaves the upper one
behind. You get one stacked `lestoq@wall-e:~/projects/` row per race, exactly
as in the screenshots.

A second path made it worse on dock close/reopen: `TerminalPanel._commit_layout`
(after-paint on map/unmap) force-committed every gate in the window, including
gates still waiting for their shell.

Why it shows on the user's machine and not in earlier tests: on a fast host,
bash answers within VTE's update interval. In the user's VM the answer can land
after the gate's premature commit. The bottom dock's minimum width is fixed in
pixels, about 25 columns at the user's cell size (fewer than the 33-column
prompt), so squeezing the bottom dock (right dock divider far left) crosses the
prompt width. The right dock does the same when narrowed. In LC_ALL=C, readline
mostly recovers, which is another reason C-locale tests missed this.

Not the cause (checked): the deferred spawn (spawn grids were always the final
ones), panesplit close/reopen, a second spawn or re-feed (no extra spawns
logged), or a VTE reflow (instrumented: no grid change ever happened with
`rewrap-on-resize` on).

## Fix (no text is hidden or rewritten, nothing is sent to the shell)
- idgent/resizegate.py:
  - After a grid change, a one-shot high-priority GLib fd watch on the PTY
    master records when the program actually writes. VTE reads its PTY lazily
    from the main loop, so the watch sees the bytes first.
  - `contents-changed` counts as the answer only once the program has written
    and `FIONREAD` on the master is 0 (VTE parsed everything). VTE's own resize
    signal is ignored.
  - PTY-less terminals (fed output) keep the old behaviour.
  - If the `PACE_MS` timeout fires while an answer is in flight (written, not
    yet parsed), the gate waits one more period. A silent program is still
    released after `PACE_MS`.
  - New `commit_when_answered()`: commit now, or once the terminals have
    redrawn.
- idgent/terminal.py: `_commit_layout` uses `commit_when_answered()` instead
  of forcing `commit()` on gates that are waiting.

## Tests
- New tests/test_terminal_dup.py. Real MainWindow, real bash behind a small
  PTY proxy that delays the shell's output by 25 ms (like a loaded VM) and
  makes the race deterministic:
  - `test_right_dock_user_locale`, `test_bottom_dock_user_locale`: drag the
    dock below prompt width, widen, close (toggle), reopen, drag again; assert
    exactly one prompt row. The bottom case uses font 15 so its minimum is
    25 columns, like the user's.
  - `test_both_docks_c_locale`: same with LC_ALL=C, plus the exact cursor
    position and typing-wrap checks.
  - `test_gate_waits_for_shell_output_not_vte_resize_signal`: SIGSTOP the
    shell and assert the gate does not release on VTE's own signal.
- tests/test_resizegate.py: 3 unit tests (VTE signal ignored before output,
  in-flight answer extends the release once, `commit_when_answered` defers
  then commits).

## Verification
- On old code (2cbc26c sources, 3 runs): fails every time. Failures were
  right-dock user locale, C-locale bottom, and the gate-timing test, plus
  bottom user locale in 1 of 3 runs. Example failure (C, bottom, after
  reopen, 25 columns):
  `['lestoq@wall-e:~/projects/', 'lestoq@wall-e:~/projects/', 'lestoq@wall-e:~/projects/', 'IDgent$ ']`.
- Fixed code: test_terminal_dup passes 3/3 in the user locale and 4/4 with
  LC_ALL=C, and 5/5 under full CPU load (`yes` on every core).
- Targeted:
  `XDG_CONFIG_HOME=$(mktemp -d) GDK_BACKEND=x11 dbus-run-session -- xvfb-run -a python3 -W ignore::DeprecationWarning -m unittest tests.test_terminal_panel tests.test_terminal_bar tests.test_terminal_startup tests.test_terminal_resize tests.test_terminal_dup tests.test_panesplit tests.test_resizegate tests.test_settings_wiring`
  - en_US.UTF-8: Ran 186, OK.
  - LC_ALL=C: Ran 186, 2 failures, both `BashDragTest.test_drag_comparison`
    `assertGreater(baseline["history_copies"], ...)`. This is pre-existing: it
    fails identically on the old code under LC_ALL=C (checked twice). The
    ungated control does not corrupt in C, so the "baseline must be broken"
    check fails. The gated harness's own checks pass. Not touched.
- Full suite, run 1 (earlier fix version, before commit 10b273b): 913 tests,
  2 failures:
  - `test_settings_wiring...commits_gate`: it expects `commit()` after a font
    change. Fixed by `commit_when_answered` committing after the answer.
  - `test_bottom_dock_user_locale` under suite load: timeout released while
    an answer was in flight. Fixed by the one-time extension.
- Full suite, run 2 (final):
  `XDG_CONFIG_HOME=$(mktemp -d) GDK_BACKEND=x11 dbus-run-session -- xvfb-run -a python3 -X faulthandler -W ignore::DeprecationWarning -m unittest discover -s tests`
  gave Ran 916, OK (skipped=2). No test_search_wiring segfault in either run.

## Screenshots (.ceo/, not committed, checked by me)
All taken in the real MainWindow with the user's ~/.bashrc, font 15, after
right/bottom drags, then close and reopen of both docks.
- .ceo/terminal-dup-master-slow.png: old code with the delayed shell. The
  right terminal is full of stacked prompt fragments, as in the user report.
- .ceo/terminal-dup-fixed-slow.png: fixed code, user locale. One prompt per
  terminal. The cursor is 5 cells early (inside `IDgent`): that is the known
  upstream bash53-020 readline bug after narrow-then-wide in UTF-8. It is out
  of scope, and is not present in C.
- .ceo/terminal-dup-fixed-slow-C.png: fixed code, LC_ALL=C. One prompt per
  terminal, cursor right after the prompt.
- .ceo/terminal-dup-2.png: fixed code, real (undelayed) shells after
  resize/close/reopen. Clean.

## Open issues / assumptions
- The upstream bash 5.3.9 (< bash53-020) cursor offset remains in UTF-8 after
  shrinking below the prompt width and widening again. Out of scope per brief.
- A program that takes longer than 2 x `PACE_MS` (100 ms) to *start* writing
  its redraw can still race. That would need a longer `PACE_MS`, which slows
  drags for silent programs; I left it at 50 ms.
- The fd watch relies on VTE reading its PTY in the main loop (true for the
  installed VTE 0.84). If a future VTE reads on a thread, the gate falls back
  to the `PACE_MS` timeout.
- tests/test_terminal_dup.py imports DockWindow and the assertion helpers from
  tests/test_terminal_resize.py. That file is unchanged.
