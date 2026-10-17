STATUS: PARTIAL — all my work is done and tested, but 1 test in a forbidden file fails (see FOLLOW-UP #1).

## What changed
- `idgent/gutter.py` (new): `ViewChrome` per text view — gutter, current-line tag (`idgent-current-line`, paragraph-background, priority 0), bracket tag (`idgent-bracket`, top priority), per-view CSS provider (font size, gutter colours).
- `idgent/editor.py`: `_EditorTab` owns a `ViewChrome`; `EditorPanel.set_chrome/chrome/font_size/set_font_size/zoom`, Ctrl +/=/-/0, Ctrl+wheel; left margin 6.
- `tests/test_editor_polish.py` (new, 11 tests).

## Design note (important)
This machine has NO `gi._gi_cairo` (python3-gi-cairo): any Python `draw` handler fails with
"Couldn't find foreign struct converter for 'cairo.Context'". So the gutter is NOT cairo-painted. It lives in the
view's LEFT border window (`set_border_window_size`, coloured by CSS `textview border.left`) and holds a small pool of
`Gtk.Label` children (`add_child_in_window`/`move_child`), one per *visible* line, repositioned on vadjustment
value-changed/changed, size-allocate, buffer changed and caret line change. HiDPI/flicker-safe (pure GTK widgets).
Gutter click selects the line.

## Public API
- `EditorPanel.set_chrome(dict)` keys gutter_bg, gutter_fg, gutter_fg_active, gutter_border, current_line_bg, bracket_bg;
  unknown keys and unparsable colours ignored, missing keep previous; applies to open and future tabs.
- `EditorPanel.chrome` dict of current values (defaults VS Code Dark+).
- `EditorPanel.font_size` (11, clamp 7..32), `set_font_size(n)`, `zoom(step)`.

## Verification
- `xvfb-run -a python3 -m unittest tests.test_editor_polish` : 11 OK (width 9/10/99/100/1000, visible-lines-only labels,
  active colour, caret/edit/undo line tag, brackets incl. 20k limit, set_chrome, zoom limits, 100k-line update < 50 ms, modified stays False, hl tags + find).
- Full suite `xvfb-run -a python3 -m unittest discover -s tests` (twice): 227 pass, 1 fail (below).
- `git diff --check` clean. `GDK_BACKEND=x11 timeout 8 xvfb-run -a python3 -m idgent /tmp`: rc 124, no stderr noise.
- Screenshot `.ceo/shots/editor.png` (not committed) checked: gutter, bright active number, line bg, bracket pair OK.

## FOLLOW-UP
1. `tests/test_highlight.py:726` (`WithoutPygmentsTest.test_editor_works_unhighlighted_and_silent`) asserts
   `tab.buffer.get_tag_table().get_size() == 0`; the spec-mandated `idgent-current-line` / `idgent-bracket` tags make it 2.
   Fix (forbidden file for me): count only `hl-` tags, e.g. `sum(1 for n in names if n.startswith('hl-'))`. I did not
   degrade the feature (e.g. disabling highlights without Pygments) to satisfy it.
2. Caret width 2 px: not possible per view in GTK 3 without a cairo draw; skipped silently.
3. Ctrl+=/-/0 are handled in the editor key handler; window.py does not bind them, so no conflict observed (not tested in the full window).
4. Current-line highlight is hidden for multi-line selections; brackets skip when text is selected. Brackets inside strings/comments are matched by plain scan.
