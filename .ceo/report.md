# Report: terminal-bar

STATUS: DONE

## What changed
- `idgent/terminal_bar.py` (new): `TerminalBar(Gtk.Box)`, `fade()`/`cancel_fade()` opacity helpers.
- `idgent/terminal.py`: `MAX_TERMINALS = 3`; `TerminalPanel` is now a VERTICAL box (bar + homogeneous horizontal body);
  limit, `limit-reached`, maximize (`maximize-toggled`), enter/exit animations, `ANIMATION_MS = 220`.
- `idgent/style.py`: CSS for bar, chips, icon buttons, `+` (`.limit`), notice; `WARNING`, `LIMIT_TINT` constants.
- Tests: new `tests/test_terminal_bar.py` (26 tests); updated `tests/test_terminal_panel.py` (new structure,
  `ANIMATION_MS = 0` where a synchronous `last-closed` is asserted); `tests/test_style.py` (+1 bar style test).

## Verification
- `GDK_BACKEND=x11 xvfb-run -a python3 -m unittest tests.test_terminal_panel tests.test_terminal_bar tests.test_style`
  -> Ran 51 tests, OK (run 3x, stable; stderr has no GTK/GLib warnings).
- `xvfb-run -a python3 -m unittest discover -s tests` -> Ran 116 tests, 1 failure (not mine, see FOLLOW-UP), rest OK.
- `python3 -c "import idgent.terminal_bar"` OK; `apply_style()` idempotent (test_style).
- Visual check: rendered 3 terminals at 900 px and 320 px (PNG kept in /tmp, outside git), maximize state too.
  Chips, icons, underline and the notice are readable; at 320 px chip labels ellipsize ("h1") and the notice stays
  fully visible. Found and fixed one visual bug (see Assumptions: no pane Revealer).

## FOLLOW-UP (tests I may not edit)
- `tests/test_window_layout.py::WindowLayoutTests::test_last_closed_hides_each_dock_and_show_respawns`
  fails: `panel.close_current()` now emits `last-closed` only after the ~220 ms exit animation, so the dock is still
  visible right after the call. Fix: after `close_current()` do `poll(lambda: not panel.get_visible())` (or set
  `TerminalPanel.ANIMATION_MS = 0` in that test's setUp).
- `tests/test_integration.py` passes unchanged.
- Window work: `window._show_terminal()` calls `new_terminal()` when count is 0; if called while the last pane is still
  closing, a new pane appears and `last-closed` is then NOT emitted (by design: it fires only if no live and no closing
  pane remain). Window should connect `maximize-toggled` and `limit-reached` as needed; `panel.bar` is public.

## Assumptions / notes
- Pane enter/exit animation is an opacity fade only (via `Widget.set_opacity` on the pane page). I first wrapped each
  pane in a `Gtk.Revealer` for a slide, but a revealer in the homogeneous box kept the page at its old (wider)
  allocation after the slot shrank, clipping the terminals. Chips do use a `Gtk.Revealer` (slide) plus a fade.
- Clicking a chip label while another pane is maximized switches the maximized pane to it (a hidden pane could not be
  focused otherwise). Ctrl+Alt+Left/Right likewise moves the maximize.
- Refused adds (limit) while maximized do not restore; only a successful add does.
- Hidden (maximized-away) panes use `set_no_show_all(True)` so a window-wide `show_all()` does not resurrect them.
- The maximized pane's divider is hidden while maximized.
- Dividers/`_panes` exclude closing panes; the homogeneous body keeps the closing pane's share until it is destroyed.
- Chip numbering: lowest unused among live chips (a chip still fading out does not hold its number).
- Chip clicking does not take keyboard focus (`focus_on_click = False`); the label click focuses the terminal.
- Notice timing: `TerminalBar.NOTICE_MS = 2500`, `NOTICE_FADE_MS = 300` (class attributes; tests shorten them).

## Final public API
`idgent.terminal`: `MAX_TERMINALS`, `pick_shell`, `safe_cwd`, `TerminalPanel(cwd, autostart=True)`:
- signals: `last-closed()`, `limit-reached()`, `maximize-toggled(bool)`
- `ANIMATION_MS` (class/instance attr, 0 disables), `bar` (TerminalBar)
- `new_terminal() -> bool`, `is_full() -> bool`, `terminal_count()` (excludes closing), `close_current()`,
  `focus_terminal()`, `set_cwd(path)`
- `toggle_maximize(pane=None)`, `is_maximized() -> bool`, `restore()`

`idgent.terminal_bar`: `fade(widget, start, end, duration_ms, done=None)`, `cancel_fade(widget)`,
`TerminalBar(limit=3)`:
- signals: `add-requested()`, `focus-requested(int)`, `fullscreen-requested(int)`, `delete-requested(int)`
- `add_chip(pane_id, index=None, shell_name="bash", duration=0) -> label`, `remove_chip(pane_id, duration=0)`,
  `chip(pane_id)`, `chip_labels()`, `set_active(pane_id|None)`, `set_maximized(pane_id|None)`,
  `set_full(bool)`, `show_limit_notice()`
- widgets: `add_button`, `notice`; chip: `box`, `label`, `label_button`, `fullscreen_button`, `delete_button`, `revealer`
