# IDgent design tokens

IDgent chooses its own GTK geometry, palettes and motion. These tokens support
compact workspaces with quiet surfaces, readable text and restrained movement.
The default is IDgent Dark; IDgent Light uses the same geometry and motion.
Existing catalogue themes continue to supply their own colors to these controls.

The authoritative values live in `idgent/style.py` (`TOKENS`),
`idgent/motion.py` (`DURATIONS` and easing control points), and
`idgent/themes.py` (palettes). Dimensions below are pixels and times are
milliseconds. Names describe their purpose within IDgent.

## Geometry and typography

A three-pixel spacing rhythm distinguishes close controls from sections and
workspace boundaries. Soft corners keep the interface calm without rounding
every surface into a pill.

| Group | Token | Value |
| --- | --- | --- |
| Spacing | tight | 3 |
| Spacing | compact | 6 |
| Spacing | control | 9 |
| Spacing | section | 15 |
| Spacing | panel | 21 |
| Spacing | canvas | 33 |
| Radius | button, input | 7 |
| Radius | menu | 11 |
| Radius | item | 5 |
| Radius | tooltip | 6 |
| Radius | scrollbar | 4 |
| Geometry | button_content | 22 |
| Geometry | entry_content | 20 |
| Geometry | border | 1 |
| Geometry | focus_ring | 2 |
| Geometry | scrollbar_thumb | 6 |
| Geometry | scrollbar_margin | 2 |
| Geometry | terminal_scrollbar_margin | 1 |
| Geometry | scrollbar_length | 34 |
| Geometry | separator | 1 |
| Geometry | separator_hit_padding | 3 |
| Typography | control | 14 |
| Typography | menu | 13 |
| Typography | tooltip | 12 |

General scrollbar thickness is 10: a 6-pixel thumb plus two 2-pixel margins.
The terminal already reserves an 8-pixel lane when scrollback is empty. Its
vertical slider uses 1-pixel side margins so showing scrollback preserves the
terminal's text width. The 34-pixel minimum length keeps the thumb discoverable.
The separator's transparent hit padding enlarges its target without a thick line.

## Compositing and shadows

| Token | Value |
| --- | --- |
| hover | 0.09 |
| disabled | 0.48 |
| selection_dark | 0.26 |
| selection_light | 0.19 |
| scrollbar | 0.21 |
| scrollbar_hover | 0.36 |
| Popup shadow offset | 0 / 5 |
| Popup shadow blur | 17 |
| Popup shadow color | #11191d |
| Popup shadow alpha, dark / light | 0.38 / 0.16 |

Hover overlays use white on dark surfaces and black on light surfaces.
Selection uses the accent; shadows separate transient surfaces from the workspace.
The lower selection alpha on light surfaces avoids muddying text backgrounds.

## Motion

Feedback is brief; entrances have more time to settle. Hover backgrounds switch
immediately. Entry borders and resize handles use the `state` duration with
the `smooth` curve. Terminal glyph hover uses `state` with `balanced` easing.

| Duration | Value | Intended use |
| --- | --- | --- |
| instant | 0 | Immediate changes |
| feedback | 96 | Brief feedback |
| state | 128 | State transitions and notice exit |
| travel | 224 | Entrances and layout movement |
| settle | 352 | Longer settling |
| pause | 640 | Deliberate delay |
| notice_hold | 3840 | Notice auto-hide timer |

The notice timer starts at popup, including entrance. Repeated popups restart
that timer. Entrance takes `travel`; exit takes `state`.

Existing callers can still resolve these duration aliases:

| Alias | IDgent duration |
| --- | --- |
| none | instant |
| superfast | feedback |
| fast, collapse, toggle, notice_out, notice_exit, resize_handle | state |
| moderate, notice_in, notice_enter, reflow | travel |
| slow | settle |
| notice_autohide | notice_hold |

Each curve is a CSS cubic Bézier `(x1, y1, x2, y2)`:

| Curve | Control points | Intent |
| --- | --- | --- |
| linear | (0, 0, 1, 1) | Constant progression |
| smooth | (0.24, 0.08, 0.28, 1) | Responsive start, gentle finish |
| arrive | (0.18, 0.66, 0.26, 1) | Early arrival, quiet settling |
| depart | (0.48, 0, 0.82, 0.42) | Gradual departure |
| balanced | (0.38, 0, 0.62, 1) | Symmetric state change |
| jumpy | (0.32, -0.18, 0.42, 1.16) | Optional overshoot; never the default |

Existing easing aliases resolve to IDgent curves: `ease` and `reflow` use
`smooth`; `ease_out`, `backdrop_fade_in` and `backdrop` use `arrive`;
`ease_in`, `delay`, `collapse`, `slide_out`, `sheet_dismiss` and `sheet` use
`depart`; `ease_in_out`, `swing` and `loading_icon` use `balanced`.

The application reduce-motion preference, `IDGENT_REDUCE_MOTION=1`, or GTK's
disabled animation setting makes new tweens finish immediately. Token changes
do not alter this behavior, cancellation, or completion callbacks.

## Mineral palettes

Blue-grey dark surfaces and a muted green accent support long work sessions.
Light surfaces use green-tinted paper with darker ink. The following are palette
inputs; syntax and derived text roles retain automatic contrast correction.

| Role | IDgent Dark | IDgent Light |
| --- | --- | --- |
| Background | #182126 | #f4f7f5 |
| Sidebar | #202c32 | #e9efec |
| Panel / alternate surface | #26343b | #dfe8e3 |
| Foreground | #e3eceb | #20352e |
| Muted | #a3b5b8 | #526a60 |
| Accent / focus border | #72b9ad | #356e60 |
| Border | #3b5059 | #b3c4bb |
| Hover border | #59737d | #8aab9b |
| Faint | #81989f | #6a8175 |
| Accent text | #91d0c4 | #356e60 |
| Accent hover | #8acabd | #417c6c |
| Danger | #ec9294 | #9b424c |
| Input background | #1c282e | #fcfdfb |
| Button background | #30434b | #d5e2da |
| Button hover | #3b5059 | #c8dcd2 |
| Warning | #dbca91 | #75622a |
| Syntax comment | #91a7aa | #60766c |
| Syntax red | #ec9294 | #9b424c |
| Syntax orange | #dcb183 | #865b32 |
| Syntax yellow | #dbca91 | #75622a |
| Syntax green | #9dca9a | #3e7050 |
| Syntax cyan | #87c8cd | #316c75 |
| Syntax blue | #91bce3 | #3d6293 |
| Syntax purple | #c4a6db | #77548c |

Titlebar and status surfaces use the sidebar. Active and editor selections mix
the accent at 0.26 in dark themes and 0.19 in light themes. Inactive selection,
hover and tab-close hover mix their overlay at 0.09. The terminal cursor uses
the foreground color.

For other catalogue themes, extended hover borders and accent hover mix their
base with foreground at 0.18; focus borders use 0.42. Git status colors start
at green/yellow/red from the mineral palettes and adjust for contrast against
both sidebar and its 0.09 selected-row overlay. User-supplied accent hover uses
a 0.20 foreground mix; accent text remains contrast-corrected.

Terminal ANSI colors, in index order:

| Index | Dark | Light |
| --- | --- | --- |
| 0 | #182126 | #20352e |
| 1 | #ec9294 | #9b424c |
| 2 | #9dca9a | #3e7050 |
| 3 | #dbca91 | #75622a |
| 4 | #91bce3 | #3d6293 |
| 5 | #c4a6db | #77548c |
| 6 | #87c8cd | #316c75 |
| 7 | #cbd9d8 | #dfe8e3 |
| 8 | #728b92 | #60766c |
| 9 | #f4b0ac | #b55761 |
| 10 | #b8deb0 | #54865f |
| 11 | #eddfab | #917c3d |
| 12 | #b1d4ee | #567db0 |
| 13 | #dec0ea | #936da6 |
| 14 | #abe0df | #4b8690 |
| 15 | #e3eceb | #f4f7f5 |

Component-specific geometry outside these shared tokens remains under those
components' ownership. This table defines IDgent's shared token system and
mineral palettes without requiring changes to their public styling functions.
