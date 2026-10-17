STATUS: DONE

Task: resize-glitch. Branch: ceo/codex/resize-glitch. Application integration is deliberately left for the next task.

**Files changed**

- `idgent/resizegate.py`: standalone `ResizeGate(Gtk.Bin)`, 120 ms allocation debounce, immediate first allocation/realization, forced `commit()`, timeout cancellation, tiny minimum and child natural requests, own clipping window, CSS background and `set_background()`. Handles child visibility/removal/destruction, reparenting and collapse. Temporarily suspends VTE descendant reflow during allocation; restores the previous property afterward. `REFLOW_VTE = True` opts out of that mitigation.
- `tests/test_resizegate.py`: real X11 GTK/VTE tests, actual bash PTYs and Paned drags, WINCH logging, visible/history text metrics, single-resize control, allocation/lifecycle tests, screenshot pixel checks and a fatal-criticals subprocess. Imports `TerminalPanel._configure` read-only, so shell configuration matches the app.
- `.ceo/report.md`: this report, committed last. No application, dependency, lockfile or packaging changes.

**Reproduction and root cause**

Inspected `.ceo/user-screenshot.png`; reproduced the characteristic fragments (`stoq@wallestoq@wall-lestoq@wall...`) with three terminals in a homogeneous horizontal Box inside a horizontal Paned. A single prompt at the top of an otherwise empty screen did not reproduce accumulation reliably. The important additional condition was a prompt near the bottom, so wrapping/reflow moves text into scrollback. The final harness prints 15 blank lines before the first prompt. It does not inject damaged output or change the requested PS1.

The harness runs `/bin/bash --norc -i` in a temporary `projects/long-prompt-directory`, using the exact supplied Debian colour PS1 and the app's font, margins, initial 20x4 grid, colours and 10,000-line scrollback. PROMPT_COMMAND installs `trap 'printf "%s\n" "$COLUMNS" >> "$RESIZE_LOG"' WINCH` before the first prompt, then unsets itself. This avoids counting echoed setup commands or earlier legitimate prompts as duplicates. A separate uninstrumented run reproduced the same 2 visible / 3 history prefixes and garbage in all three terminals, so the trap is not the cause.

Each run moves the divider from 180 to 760 px and back, or the reverse, for three sweeps: 438 position updates at 4 px per step, every 8 ms (fast) or 24 ms (slow), then 650 ms of main-context settling. Every wait services GLib; shell startup and process exit use bounded polling. The tests use `get_text_format(Vte.Format.TEXT)` for the visible screen, and `get_text_range_format` over the adjustment's full range for scrollback. They independently construct the real user@host prefix, remove physical line breaks, and compare all remaining text with exactly one expected prompt. This detects partial garbage even if it does not contain a complete second prefix. All three terminals must pass separately.

Root cause: VTE reflows existing prompt cells when its grid width changes; bash/readline then redraws using cursor/wrap bookkeeping that can disagree with that reflow. Near the bottom this leaves pieces of the previous prompt behind or in history. Repetition accumulates those pieces. This is terminal content, not a GTK paint artifact: the fragments are present in VTE's text APIs. Repeated PTY resize signals amplify it, but **one large resize is sufficient**, so a pure debounce is not a complete fix.

The original every-pixel SIGWINCH hypothesis needs that qualification: 438 divider steps produced 66 WINCH trap invocations per terminal in the three-terminal layout, not 438. The grid changes only when enough pixels cross character-cell boundaries, and GTK coalesces frame allocations. For one wide terminal the exploratory baseline produced 195 invocations. Gating reduces the three-terminal count from 66 to 1 per shell (198 to 3 across all three).

Local versions: Bash 5.3.9(1)-release (package bash 5.3-2ubuntu1), libreadline8t64 8.3-4, VTE 0.84.0, GTK 3.24.52.

**BASELINE vs AFTER**

Numbers below are per terminal; all three agree. Each row uses 438 drag updates. Narrow final grid is 10 columns; wide final grid is 32 columns.

| Scenario | BASELINE WINCH | AFTER WINCH | BASELINE visible/history prefixes | AFTER visible/history prefixes | BASELINE/AFTER garbage | BASELINE/AFTER nonempty text lines |
| --- | ---: | ---: | --- | --- | --- | --- |
| 8 ms, right pane finishes shrinking | 66 | 1 | 2 / 3 | 1 / 1 | yes / no | 6 / 3 |
| 24 ms, right pane finishes growing | 66 | 1 | 2 / 2 | 1 / 1 | yes / no | 1 / 1 |
| 8 ms, right pane finishes growing | 66 | 1 | 2 / 2 | 1 / 1 | yes / no | 1 / 1 |
| 24 ms, right pane finishes shrinking | 66 | 1 | 2 / 3 | 1 / 1 | yes / no | 6 / 3 |

Nonempty TEXT lines are not a prompt-copy metric by themselves: clean wrapped output can span several lines, and corrupt fragments can concatenate on one line. The history/prefix counts and complete normalized-text comparison are the assertions.

Single manual shrink, 100 to 35 columns, same prompt near the bottom:

| Container | WINCH | Visible/history prefixes | Garbage |
| --- | ---: | --- | --- |
| Plain VTE | 1 | 2 / 2 | yes |
| Debounce-only gate (`REFLOW_VTE = True`) | 1 | 2 / 2 | yes |
| Default ResizeGate | 1 | 1 / 1 | no |

**Additional mitigation and tradeoff**

For each committed child allocation the gate finds VTE descendants by their `rewrap-on-resize` property, temporarily sets true values to false, allocates the child, and restores those values in `finally`. Existing false values remain false. No shell input, Ctrl-L, screen reset, output deletion, PTY replacement or synthetic signals are used. Generic GTK children have no such property and are unaffected. The test covers a VTE inside a full page with a scrollbar, not just a direct terminal child.

This keeps existing output in its original physical rows during a gated resize. Right-hand parts of old rows can be hidden at narrower widths and become visible when widened again; a long-output preservation test verifies that round trip. The 10,000-line history configuration and scrollbar adjustment object are retained. This intentionally sacrifices automatic reflow of existing output on gated allocations to prevent readline corruption. New shell output uses the committed PTY width normally. The VTE property is deprecated, but remains functional on the tested VTE; it is the available GTK 3 API for this behavior. See the [VTE API documentation](https://gnome.pages.gitlab.gnome.org/vte/gtk3/method.Terminal.set_rewrap_on_resize.html). `REFLOW_VTE = True` restores normal reflow behavior, with the demonstrated redraw risk. Retest this workaround when upgrading VTE or changing terminal backends.

The gate's Gdk window clips windowed and windowless children. An internal GTK Box paints the background in C, avoiding the unavailable `gi._gi_cairo` bridge; it does not appear as a second public child. During collapse the gate can become one pixel wide, while its child remains at least its minimum/margin-safe size. That avoids GTK negative-content-size warnings and clips the excess.

**Verification**

- `xvfb-run -a python3 -m unittest tests.test_resizegate`: **five consecutive final runs passed**, 14 tests each, about 80 seconds per run; **0 failures / 5 final runs**. All four speed/direction combinations require a strictly worse ungated history-prefix baseline. Across 120 terminal/scenario measurements, every ungated terminal had 66 WINCH events and corruption; every gated terminal had 1 WINCH event, exactly 1 visible/history prompt prefix and no garbage. These runs tested code revision `f6de2ab`.
- `xvfb-run -a python3 -m unittest discover -s tests`: **146 tests passed** in 97.595 seconds, after the five module runs. Only existing Python deprecations from `test_style.py` and `test_terminal_panel.py` appeared; no GTK warnings/criticals or callback errors.
- The module's subprocess runs allocation/lifecycle/pixel/VTE-page tests plus the real three-terminal drag with `G_DEBUG=fatal-criticals`, and checks stderr for GTK warnings, criticals and Python callback errors.
- `G_DEBUG=fatal-warnings xvfb-run -a python3 -m unittest tests.test_resizegate.ResizeGateTest`: 11 tests passed.
- Additional `G_DEBUG=fatal-warnings xvfb-run -a python3` smoke using `BashDrag`: collapsed an actual VTE gate to width 1 (child safely width 20), restored it, focused the terminal and sent a UTF-8 shell command successfully. No warnings/criticals. This tests UTF-8 input, not a full IME compose session.
- `git diff --check` and `git diff --cached --check`: clean before the final report commit. Only the two owned implementation/test files and this report are committed. The report is committed last, and git status is clean.
- Acceptance logs: `.ceo/resizegate-run-{1..5}.log`, `.ceo/full-suite.log` (ignored). The preceding timing trial passed three modules, then its fourth failed an overly strict `WINCH <= 1` assertion: two WINCH events were observed, with all prompt/text assertions still clean. A pause longer than the debounce interval legitimately permits an intermediate commit. The revised test records actual child allocation delays, requires every commit to follow a stable period (at least 100 ms with the 120 ms timer), bounds the commit count, and checks WINCH counts against those allocations. It retains the strict garbage/copy comparison. Earlier logs are preserved as `.ceo/resizegate-timing-trial-{1..4}.log`; final-revision flakiness was **0/5**.

**Integration notes / FOLLOW-UP**

- Use the default `SETTLE_MS = 120`. Both 8 ms and 24 ms steps stay unsettled until the drag ends. A user pause longer than 120 ms intentionally commits once; increase toward 150-180 ms only if interactive testing warrants a slower response. Avoid setting it below typical frame/drag gaps.
- Wrap each complete page (terminal, scrollbar, reserved gutter and separator) in its own gate, not the whole dock. Keep the bar outside. Preserve a stable gutter width when the scrollbar appears, since independent changes inside the page can still change the VTE's share of the page width.
- Keep the scrollbar attached to the existing `terminal.get_vadjustment()`. While dragging, page_size and geometry remain at the committed terminal grid; live output can still update history and scrolling. The commit updates geometry once. Existing idle visibility/queue_resize callbacks may continue; repeating the same outer allocation does not reset the gate timer. Wrapping only the VTE would let the scrollbar track live gate height while the terminal retains its old grid, so wrapping the page is preferable.
- Call `gate.set_background("#1e1e1e")` (or the current terminal background token), or style `.idgent-resize-gate` through the app stylesheet. Both `background` and `idgent-resize-gate` CSS classes are present. New space is painted; shrinking clips the last committed child at the top-left.
- The gate does not take focus or intercept keys. Keep all existing VTE focus/key/mouse handlers and focus `pane.terminal`, not the gate. Existing event coordinates continue through the Gdk child window. During dragging, the cursor and IME caret geometry stay at the committed size and can be clipped; input/output continue at that size. Actual IME composition and accessibility still deserve manual integration testing.
- For maximize/restore, fullscreen changes and dock shows, call `commit()` **after GTK has assigned the new gate allocation**, e.g. from the next size-allocate callback or an idle scheduled after layout. Calling it immediately before layout only commits the old allocation. First allocation and realization already commit immediately. Do not call `commit()` for each Paned position notification.
- The next task must update TerminalPanel's body child/index/reorder/maximize logic to use the gate wrapper where it currently assumes `pane.page` is a direct `_body` child. Keep the actual page reference for its contents and add/store a gate reference for allocation and body operations. No such wiring was made here.
- No missing credentials, dependencies or product decisions. The non-reflow history layout is a documented workaround, not a promise that arbitrary programs cannot overwrite hidden cells. The current work is a standalone widget with tested bash behavior, not an app-wide integration claim.
