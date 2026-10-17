STATUS: DONE

## What changed
- `idgent/terminal_bar.py`: `TerminalBar`/chips removed. New `PaneHeader` (label button, fullscreen + delete icon buttons, signals `focus-requested`/`fullscreen-requested`/`delete-requested`, `set_number/set_active/set_corner/set_maximized`, `.text`), `AddButton` (`set_full`), `LimitToast` (`popup()`, `SHOW_MS`=2500, `FADE_MS`=300). `fade`/`cancel_fade`/`_IconButton` kept.
- `idgent/terminal.py`: `TerminalPanel(cwd, autostart=True, stacked=False)`. Layout = `Gtk.Overlay` over the body box (HORIZONTAL for columns, VERTICAL when stacked) with the "+" button and toast as overlay children. Each pane = `page[inner[header, content[terminal, scrollbar, gutter]], divider]`; divider is vertical (columns) or horizontal (stacked). Headers renumber by position (live panes only), `corner` class on the corner pane's header (last column / first row; the maximized pane while maximized), `active` class follows focus. Ctrl+Alt+Up/Down added for stacked panels. `apply_palette(fg, bg, ansi, cursor=None)`. Limit 3, `new_terminal() -> bool`, `is_full`, signals, animations (whole-pane `fade()`, no Revealer), `ANIMATION_MS`, maximize/restore unchanged in behaviour.
- `idgent/window.py`: right panel built with `stacked=True`; editor `set_size_request` removed; `dock_bottom.pack1(editor, shrink=True)`, `dock.pack1(dock_bottom, shrink=True)`; title is `IDgent`.
- `idgent/titlebar.py`: title label is `IDgent`; `set_folder` removed.
- `idgent/style.py`: header / add-button / toast CSS replaces the bar/chip CSS (`TOAST_BG` added).
- Tests: `test_terminal_bar.py` (rewritten: header/toast widgets + both orientations x behaviour + animations), `test_terminal_panel.py`, `test_window_layout.py`, `test_titlebar.py`, `test_integration.py`, `test_style.py`.

## Verification
- `xvfb-run -a python3 -m unittest discover -s tests` (run twice): 171 tests, OK both times.
- Divider tests (`test_window_layout.py`) set both paned positions to 0 and the maximum, measure allocations, restore, and run a full scenario in a subprocess with `G_DEBUG=fatal-criticals` (exit 0, no CRITICAL on stderr). The editor-collapse test fails when `shrink=True` is reverted.
- Screenshots (not committed, `.ceo/shots/`): `01-default`, `02-three-three`, `03-limit-toast` (+`03b-after-toast`), `04-collapsed-editor`, `05-collapsed-left`, `06-fullscreen-right`. Reviewed; no visible defects in my files.

## Final public API
`TerminalPanel(cwd, autostart=True, stacked=False)`: `new_terminal() -> bool`, `terminal_count()`, `is_full()`, `header_labels()`, `set_cwd`, `close_current`, `toggle_maximize(pane=None)`, `restore()`, `is_maximized()`, `focus_terminal()`, `apply_palette(...)`; attributes `stacked`, `overlay`, `add_button`, `toast`, `HEADER_HEIGHT`, `ANIMATION_MS`; signals `last-closed`, `limit-reached`, `maximize-toggled(bool)`. Removed: `panel.bar`.
Pane (`_Pane`): `page, terminal, header, content, divider, pid, ...`.

## Assumptions / notes
- "Header allocation bottom == terminal top, same x and width" is measured against the pane's content row (terminal + scrollbar + 8px gutter), because the VTE widget itself has 8px margins and excludes the scrollbar gutter; the header spans the full row.
- With both dividers collapsed to the left, two divider handles (explorer/dock and left part/right dock, ~5px each) remain visible next to each other; the right dock "touches" the explorer except for those handles. Likewise the bottom terminal sits one handle (5px) under the title bar when the editor is collapsed. Dragging the divider via `set_position` exercises the same clamp as the mouse.
- The `active` header is per dock, so each dock shows one active header even when only one has keyboard focus.
- `test_window_layout`'s subprocess test relies on the inherited DISPLAY (works under `xvfb-run`).

## FOLLOW-UP
- Theme task can call `apply_palette` on both panels; header/toast colours are still module constants in `style.py`.
- README still describes the old chip bar and "IDgent - folder" title.
