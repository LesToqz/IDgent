STATUS: DONE
Changed: tests/test_highlight.py (WithoutPygmentsTest asserts no tags named hl-*, instead of empty table).
Verified: `xvfb-run -a python3 -m unittest discover -s tests` run twice: OK, OK. `git diff --check` clean.
