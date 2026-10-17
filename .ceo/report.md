STATUS: DONE

Implemented on `ceo/codex/themes-core`, entirely within the assigned worktree.

Files changed:
- `idgent/themes.py`: display-independent frozen Theme/TerminalColors data, twelve complete palettes, lookup helpers, ANSI validation, contrast-adjusted syntax shades.
- `idgent/settings.py`: call-time XDG path resolution, tolerant JSON reads, merge-preserving atomic private writes and theme preference helpers.
- `idgent/style.py`: theme CSS builder, shared-provider runtime switching with preflight parsing, active-theme access, GTK dark preference, refined controls/menus/tooltips/dialogs/focus/scrollbars/tabs/status/spacing and resize-gate rule. Removed dead module colour constants after checking for importers; retained compatibility CSS.
- `idgent/titlebar.py`: theme-driven builder and preserved Dark+ TITLEBAR_CSS constant.
- `tests/test_themes.py`, `tests/test_settings.py`: catalogue/contrast/compatibility/import independence and filesystem/persistence/failure coverage.
- `tests/test_style.py`, `tests/test_titlebar.py`: all-theme strict GTK parsing, live editor/sidebar/status/titlebar restyling, shared registration, invalid-switch rollback and dark preference coverage.

Verification:
- `xvfb-run -a python3 -m unittest discover -s tests`: PASS twice, 245 tests each; 43.433s and 44.068s. Logs: `.ceo/acceptance-1.log`, `.ceo/acceptance-2.log`.
- `git diff --check`: PASS.
- `GDK_BACKEND=x11 timeout 8 xvfb-run -a python3 -m idgent /tmp`: expected rc 124; stdout and stderr both empty. Logs: `.ceo/smoke.stdout`, `.ceo/smoke.stderr`.
- Targeted theme/settings/style/titlebar tests passed during implementation.
- All requested contrast gates pass, including exact Dark+ syntax equality with highlight.DARK_PLUS and chrome equality with gutter.DEFAULT_CHROME.
- Real MainWindow screenshots: `GDK_BACKEND=x11 PYTHONWARNINGS=ignore xvfb-run -a -s '-screen 0 1600x1000x24' python3 .ceo/shots/render.py`. Rendered every theme with a Python file open, a 1480x900 requested window, two terminals in each of the open bottom/right docks, and about one second of GTK processing per theme. The script applies CSS, syntax, editor chrome, and both VTE palettes explicitly.
- Reviewed all twelve screenshots in the contact sheet, plus full-size Solarized Light, IDgent Midnight and IDgent Paper for readability. Themes have distinct surfaces/accents and coordinated syntax/terminal colours. Final screenshots regenerated after spacing refinements and contact sheet reviewed again.
- Existing suite output includes PyGObject/Python deprecation warnings, GTK deprecated background-colour getter warnings, and the desktop-file category hint; no application smoke stderr noise.

Theme catalogue (in order):
- dark-plus / Dark+: original neutral VS Code charcoal and blue, unchanged palette.
- light-plus / Light+: crisp white editor, pale grey chrome and blue accents.
- monokai / Monokai: olive charcoal, lime accent, pink keywords and yellow strings.
- dracula / Dracula: violet charcoal, lavender accent and pink/green/yellow syntax.
- solarized-dark / Solarized Dark: deep blue-teal surfaces and restrained Solarized hues.
- solarized-light / Solarized Light: cream parchment surfaces and Solarized blue accents.
- nord / Nord: arctic slate, icy cyan accent and soft Nordic syntax.
- one-dark / One Dark: cool graphite with blue accents and purple/green syntax.
- github-dark / GitHub Dark: near-black surfaces, vivid blue accent and coral/blue syntax.
- idgent-midnight / IDgent Midnight: blue-black surfaces and electric cyan with luminous cool syntax.
- idgent-ember / IDgent Ember: warm charcoal, amber accent and warm pastel syntax.
- idgent-paper / IDgent Paper: warm off-white, teal accent and botanical ink colours.

Public API:
- `themes.TerminalColors(foreground, background, cursor, ansi)`: frozen palette; exactly sixteen lowercase #rrggbb ANSI entries required.
- `themes.Theme(id, name, dark, ui, syntax, terminal, chrome)`: frozen theme record with complete UI/syntax/chrome dictionaries.
- `themes.THEMES`, `themes.DEFAULT_THEME_ID`, `themes.get_theme(theme_id)`, `themes.theme_ids()`; unknown/None IDs resolve to Dark+.
- `settings.load() -> dict`, `settings.save(data) -> bool`: partial saves preserve unknown keys; replace via a flushed/fsynced same-directory temporary file with mode 0600. Failure leaves the existing settings file intact.
- `settings.get_theme_id() -> str`, `settings.set_theme_id(theme_id) -> bool`: JSON key `theme`; unknown IDs normalize to Dark+.
- `style.build_css(theme) -> str`, `style.apply_theme(theme)`, `style.apply_style(theme=None)`, `style.current_theme() -> Theme | None`. apply_style defaults to Dark+; both apply functions require an initialized display, validate before changing live state, reuse one registered provider, and set the GTK dark preference. CSS remains the default-theme compatibility string.
- `titlebar.build_titlebar_css(ui) -> str`; `TITLEBAR_CSS` remains its Dark+ output.

Screenshot artifacts (ignored and not committed):
`.ceo/shots/theme-dark-plus.png`
`.ceo/shots/theme-light-plus.png`
`.ceo/shots/theme-monokai.png`
`.ceo/shots/theme-dracula.png`
`.ceo/shots/theme-solarized-dark.png`
`.ceo/shots/theme-solarized-light.png`
`.ceo/shots/theme-nord.png`
`.ceo/shots/theme-one-dark.png`
`.ceo/shots/theme-github-dark.png`
`.ceo/shots/theme-idgent-midnight.png`
`.ceo/shots/theme-idgent-ember.png`
`.ceo/shots/theme-idgent-paper.png`
`.ceo/shots/contact.png`
Reproduction script: `.ceo/shots/render.py` (also ignored and not committed).

Assumptions:
- `highlight.py` imports GTK, so the pure catalogue uses a private immutable named-tuple representation with the identical SyntaxStyle fields/protocol and value equality. Static annotations refer to highlight.SyntaxStyle; a runtime alias permits annotation introspection without importing GTK. Tests enforce exact kinds and Dark+ value equality, and real EditorPanel use is verified. No forbidden highlight changes were needed.
- Published palette hues are retained, with small syntax/foreground adjustments where needed for the requested WCAG thresholds. Solarized uses higher-contrast base foregrounds because its conventional defaults do not reach 7:1.
- Frozen dataclasses retain ordinary dictionaries as explicitly requested; callers should treat catalogue data as read-only.
- CSS switching intentionally does not discover editor/terminal instances or write preferences automatically; those explicit APIs remain available to the later window/startup task.

FOLLOW-UP:
- In the later owned window/app work: load `themes.get_theme(settings.get_theme_id())` at startup; apply style, editor syntax/chrome and both terminal palettes; on menu selection apply those same APIs and persist with `settings.set_theme_id(theme.id)`.
- Optional future cleanup: extract canonical syntax data types/kinds into a shared GTK-free module, then let both highlight and themes import them. Current structural compatibility is tested and works without changing forbidden files.
- No dependencies, packaging, app/window, editor/highlighter, terminal modules, or other tests were changed.

Implementation commits: d363a0d, 86e2d3f. This report is committed last.
