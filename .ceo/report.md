STATUS: DONE

Changes
- idgent/terminal_bar.py: replaced stable numbers with shell names plus live left-to-right positions. Renumbers on insertion, removal, and GTK child reordering. Closing chips retain their existing text during exit animation and do not consume positions. chip_labels() follows live widget order.
- idgent/style.py: add button uses #cccccc normally and white over HOVER on hover; preserved amber limit colours. Theme registration and parsing logic unchanged.
- README.md: documented positional labels and renumbering after closing a pane.
- tests/test_terminal_bar.py: updated stable-number expectations; covered delete/add, middle insertion after focusing the first pane, mixed-shell reordering, and fading chips.
- tests/test_style.py: checked realized add-button normal, hover/background, limit, and restored normal colours.

Verification
- xvfb-run -a python3 -m unittest discover -s tests -p 'test_terminal_bar.py': 29 tests passed.
- PYTHONWARNINGS=ignore xvfb-run -a python3 -m unittest discover -s tests -p 'test_style.py': 9 tests passed, including strict CSS parsing and apply_style() idempotence.
- xvfb-run -a python3 -m unittest discover -s tests: run 1, 132 tests passed in 17.760s; run 2, 132 tests passed in 16.279s. Existing deprecation warnings only.
- git diff --check and git diff HEAD --check: clean.
- GDK_BACKEND=x11 xvfb-run -a python3 (inline GTK capture script): realized a TerminalPanel with three bash panes, iterated the main loop for 1 second, captured using Gdk.pixbuf_get_from_window, and viewed both PNGs. Confirmed bash1/bash2/bash3 left to right, brighter normal plus, and amber limit plus. Normal add-button style colour was rgb(204,204,204). Captures are ignored, uncommitted .ceo/chip-numbering-visual.png and .ceo/chip-numbering-limit-visual.png.

Assumptions / open issues
- No open issues. The visual normal-state capture temporarily clears the limit CSS class after creating three panes, then restores it for the limit capture; production limit behaviour is unchanged.
- idgent/terminal.py and tests/test_terminal_panel.py needed no edits. Forbidden files were untouched, and their tests pass.
