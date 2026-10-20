STATUS: DONE (implementation complete; two out-of-scope integration test incompatibilities reported below)

Changes:
- idgent/settings.py: all contract feature, explorer, terminal, graph-type, Git graph and notes keys; md_live_preview replaces md_default_preview. Existing legacy JSON keys survive but have no accessor/default. Terminal pane default 6, bounds 1..8; commit bounds 100..10000.
- idgent/settings_dialog.py: nine navigation pages in requested order; first Enable row per feature; disabled rows remain searchable and use sensitivity plus opacity 0.48. Moved explorer font and Markdown rows. Added shell entry with advisory hint, pane and commit spins, notes-folder info, eight graph controls with reset, full graph callbacks, silent initialization and copied values.
- README.md: settings pages and feature preferences documented.
- tests/test_settings.py, tests/test_settings_dialog.py: contract validation, persistence/reset, legacy key, graph preservation, page order, callbacks, keyboard navigation, search, sensitivity/opacity and silent initialization.

Verification:
- XDG_CONFIG_HOME=$(mktemp -d) GDK_BACKEND=x11 dbus-run-session -- xvfb-run -a python3 -X faulthandler -W ignore::DeprecationWarning -m unittest tests.test_settings tests.test_settings_dialog -v
  PASS: 44 tests, 4.447 seconds. Log: .ceo/targeted.log.
- Full suite launched ONCE using the same isolated X11 command with "discover -s tests"; 920 tests in 449.433 seconds; 916 passed, 2 skipped, 1 failure, 1 error (rc 1). Both issues are in unowned tests/test_window_wiring.py. Log: .ceo/full-suite.log.
- XDG_CONFIG_HOME=$(mktemp -d) GDK_BACKEND=x11 dbus-run-session -- timeout 8 xvfb-run -a python3 -m idgent .
  PASS: rc 124, no traceback. Log: .ceo/smoke.log.
- git diff HEAD~2 --check: PASS. Diff wording check: PASS.
- Real GTK screenshots captured using Gdk.pixbuf_get_from_window under xvfb with a 1280x1200 screen. LOOKED at all eight requested images, plus both disabled Git graph images, in .ceo/screenshots/.
  SAW: aligned end controls, readable labels and descriptions, rounded cards and separators, complete notes-folder row, shell hint and full terminal list, graph decimals and dropdowns. Both palettes preserve contrast. Disabled Git graph rows visibly fade while Enable stays active. Replaced an unavailable Git navigation icon with an available symbolic list icon.

Decisions / assumptions:
- Kept the existing "files" page ID; displayed title is "File explorer".
- Followed this task's explicit unknown-graph-key preservation acceptance requirement: normalize_graph now retains unknown graph keys; known keys retain existing validation/ranges. This differs from the older dropping policy in docs/graph-spec.md; that document is outside the owned files.
- Used existing reduceMotion graph setting, labelled "Reduce graph motion", rather than inventing an animation key.
- Notes path comes from the transient parent's root, with "<project>" placeholder when used standalone.
- Existing fonts/theme retain their dedicated change signals; all added scalar settings persist and use setting-changed. Graph rows emit key "graph" and the full copied dictionary.
- Concurrent persisted graph-panel changes are retained when editing an individual graph setting.
- No dependency, stylesheet, widgets.py, window.py or other unowned file changes.
- Contrary to .ceo/common.md, .ceo/report.md is already tracked in this checkout (from commit 76bd125). Committed the report last under the supplied AGENTS.md reporting rule to leave the worktree clean. Screenshots and verification logs remain ignored.

Commits:
- 3d2a076 feat(settings-core): add feature settings pages and storage keys
- 911c8cc fix(settings-core): dim disabled rows and retain concurrent graph edits
- de82c38 docs(settings-core): document graph change callbacks

Open issues:
- Full suite: test_window_wiring.WindowWiringTest.test_startup_applies_stored_values errors at line 348 because its stored fixture calls settings.set_values with removed md_default_preview.
- Full suite: test_window_wiring.WindowWiringTest.test_every_setting_changes_the_live_widgets_and_persists fails at line 326 because CHANGES enumerates the old NEW_KEYS, including md_default_preview and omitting the new contract keys.
- Left tests/test_window_wiring.py unchanged as specifically directed by the brief. The live-md / feature-wiring integration must update the old fixture/key enumeration and connect the new keys before these two tests can pass.
- Feature wiring is deliberately left to the assigned later task.
