# Report: highlight

STATUS: DONE

## What changed
- `idgent/highlight.py` (new): optional Pygments backend, VS Code-like kinds, Dark+ style, `lexer_for`,
  `language_name`, `map_token`, `classify`, `Highlighter`.
- `idgent/editor.py`: each `_EditorTab` owns a `Highlighter`; `set_path()` re-detects the language;
  `on_path_renamed` uses it; `EditorPanel.set_syntax_style()`; status bar gains the language; closing a
  tab destroys its highlighter.
- `tests/test_highlight.py` (new, 46 tests) and `tests/test_editor_panel.py` (status string updated).

## Verification
- `xvfb-run -a python3 -m unittest discover -s tests` twice: `Ran 178 tests ... OK` both times.
- `git diff --check`: clean.
- `GDK_BACKEND=x11 timeout 8 xvfb-run -a python3 -m idgent /tmp`: rc=124, stderr empty.
- Visual check (not committed): `.ceo/shots/{py,json,md,html}.png`; colours looked right (HTML brackets grey,
  embedded CSS/JS coloured, Markdown fence content lexed as Python). One fix came out of it: the `.`/`#`
  of CSS selectors is now painted gold with the name.

## Timing (20 000-line / 630 KB synthetic Python file, real EditorPanel under Xvfb)
- Open to fully highlighted: 1.7-1.9 s (about 1.0 s of that is Pygments + classification).
- ~200-230 idle slices, **max slice 6-8 ms** (limit asserted in the test: 50 ms), mean ~5 ms.

## Public API
`idgent.highlight`:
- `AVAILABLE: bool`, `KINDS: tuple[str, ...]` (26 kinds), `DEFAULT_TEXT_COLOR = '#D4D4D4'`,
  `DEBOUNCE_MS=120`, `MAX_BYTES=1_500_000`, `MAX_LINE=20_000`.
- `SyntaxStyle(foreground, bold=False, italic=False, underline=False)`, `DARK_PLUS: dict[str, SyntaxStyle]`
  (keys == `KINDS`, pure data).
- `lexer_for(path, first_line='') -> Lexer | None` (cached; `.txt/.log/.gitignore`/unknown -> None),
  `language_name(lexer) -> str` (`'Plain Text'` for None).
- `map_token(token_type, lexer, value='') -> kind | None` (type walk plus lexer-aware tweaks; pass `value`
  for the text-based ones), `classify(lexer, text) -> Iterator[(start, end, kind)]` (full context-aware
  runs, code-point offsets; what the Highlighter paints).
- `Highlighter(buffer, styles=None, lexer=None)`: `.set_lexer(lexer|None)`, `.set_style(styles)`,
  `.destroy()`, plus read-only `.lexer`, `.skipped`, `.pending` (True while a pass is scheduled/running),
  `.tags` (`kind -> Gtk.TextTag` named `hl-<kind>`).

`EditorPanel`: `set_syntax_style(styles: dict[str, SyntaxStyle])` (stored in `.syntax_style`, applied to all
tabs and every later tab; initial `DARK_PLUS`). `_EditorTab`: `.highlighter`, `.language`,
`.detect_language()`, `.set_path(path)`. Status: `Ln 2, Col 7  |  UTF-8  |  LF  |  Python`.

## Assumptions / decisions
- `doc` is painted only for Python docstrings (Pygments `String.Doc` in Python) and is `#CE9178` in Dark+,
  as in VS Code; doc comments of other languages (`///`, `/** */`) map to `comment`.
- Pygments `Error` tokens are not painted (they fire on valid-but-unsupported syntax, would flash red while
  typing); `error` is only produced for `Generic.Error/Traceback`. `punctuation` is only produced for
  HTML/XML tag brackets (`#808080`).
- Builtin types (`int`, `str`, `Option`, `Vec`, exceptions) -> `class`; other builtins -> `builtin`.
  `Keyword.Type` -> `class` except in C-family lexers (-> `keyword`, as in VS Code). CamelCase identifiers
  -> `class`; identifier followed directly by `(` -> `function` (so `Foo()` is a function);
  `import/from/as` and flow keywords -> `control`; SQL/Docker/Makefile/INI keywords are never `control`.
- Pygments' Markdown lexer restarts offsets at 0 inside fenced code; `classify` guards with
  `max(index, running position)`. Lexers are built with `stripnl/stripall/ensurenl=False, tabsize=0`
  and driven through `get_tokens_unprocessed`, so offsets equal buffer offsets (editor text is already
  `\n`-normalised by `read_file`).
- Adjacent same-kind runs are merged across whitespace-only gaps (fewer tag applications; not for `link`).
- `.sql` -> generic SQL lexer, `.svg` -> XML, `.pl` -> Perl, `.jsonc` -> JSON, `.env*` -> Bash.
- "Save as"-like path changes: the editor has no save-as, only `on_path_renamed` (now via `set_path`);
  `_EditorTab.set_path` is the hook for any future one.
- Without Pygments no `hl-*` tags are created at all.

## FOLLOW-UP
- Hook `EditorPanel.set_syntax_style` to the theme task; `DARK_PLUS` shape is the contract.
- Highlighting restarts from the top on each edit (cheap enough at 6 ms slices); caching lexer state at
  line boundaries would make huge files edit-friendly but needs Pygments state capture.
- CSS inside `.scss`/`.sass` is only approximately tokenised by Pygments (nested properties look like selectors).
