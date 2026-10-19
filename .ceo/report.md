STATUS: DONE

Task: window-buttons
Branch: ceo/codex/window-buttons

What changed
- idgent/titlebar.py: WINDOW_BUTTON_WIDTH is 52 px; HEADER_HEIGHT remains 40 px
  (39 px controls plus the 1 px divider). WINDOW_ICON_SIZE is 36 px. The installed
  symbolic assets have internal padding, so loading them natively at 36 px renders
  an 18 px visible mark. Removed the 0.75 CSS transform. Sizing GTK's internal
  titlebutton images before allocation also covers rebuilt decoration children.
  The settings cog, menu, title, theme roles, hover and pressed styles are unchanged.
- tests/test_titlebar.py: preserved contiguous-block, far-right, hit-area and
  title-centering checks; strengthened glyph pixel checks to 18–20 px and added
  centering, pressed-overlay, all-theme/rebuilt-decoration, restore-image, and
  minimum-width layout coverage. Three new tests.
- tests/test_style.py: deliberately unchanged. This checkout has no titlebar
  component CSS hash line: its baseline tuple contains only explorer, editor and
  terminal hashes. There was therefore no permitted titlebar line to re-baseline
  or annotate. No other hash or assertion was changed.

Verification
Every GUI invocation used GDK_BACKEND=x11 and xvfb-run -a, with a fresh
XDG_CONFIG_HOME from mktemp -d. Only one of this worker's xvfb runs ran at a time.

1. Targeted acceptance command:
   export XDG_CONFIG_HOME=$(mktemp -d)
   GDK_BACKEND=x11 xvfb-run -a python3 -W ignore::DeprecationWarning -m unittest tests.test_titlebar tests.test_style -v
   Result: 38 tests OK, final run 7.430 seconds.
2. Full suite, run exactly once:
   export XDG_CONFIG_HOME=$(mktemp -d)
   GDK_BACKEND=x11 xvfb-run -a python3 -W ignore::DeprecationWarning -m unittest discover -s tests
   Result: 601 tests OK in 180.211 seconds, exit 0.
   Log: .ceo/shots/full-suite.log. The run emitted one desktop-entry category hint
   and GTK/Gdk critical messages for gtk_widget_get_mapped/gdk_window_is_visible;
   no test failed. Their origin was not investigated or attributed to a baseline.
3. Smoke:
   export XDG_CONFIG_HOME=$(mktemp -d)
   GDK_BACKEND=x11 timeout 8 xvfb-run -a python3 -m idgent /tmp
   Result: exit 124 as required; .ceo/shots/smoke.log is empty, no traceback.
4. git diff --check passed.

Screenshot observations
- Captured the real MainWindow's GdkWindow under xvfb using Gdk pixbufs; inspected
  the 400 x 60 px top-right crops at 100% and nearest-neighbor 2x, and compared
  with .ceo/evidence/topright.png.
- .ceo/shots/idgent-dark.png and idgent-light.png, with corresponding -2x.png
  files: visibly larger, crisp minimize/maximize/close marks, with no clipped
  edges. Measured minimize 18 x 3 px; maximize and close 18 x 18 px in both themes.
  Each hit area is 52 x 39 px; the three form a contiguous 156 px block flush right.
  The cog remains 28 x 28 px, immediately left of minimize with its existing margin.
- Trial CSS scaling enlarged the icons but softened their edges; native image
  loading produced the sharper final result without depending on transform clipping.
- Additional *-close-hover* and *-restore* shots show danger-red/white close hover
  and the complete overlapping-square restore glyph in both themes.
- minimum-width.png and minimum-width-2x.png show the 336 px minimum window width:
  menus, title, cog and all controls fit without overlap or overflow. GTK retains
  its normal narrow-header title placement; the existing centered-title test at
  normal width still passes.
- Screenshots and logs are ignored and not committed.

Open issues / assumptions
- All validation ran on X11/xvfb. The user's real desktop is Wayland GNOME and was
  not opened or modified. No Reference app installation, configuration or vault was used.
- xvfb has no window manager here, so restore rendering was checked by selecting
  GTK's window-restore-symbolic asset on its maximize image. Decoration rebuilding
  was exercised separately by changing and restoring the decoration layout.
- Native icon padding can vary with the system icon theme; all application color
  themes were tested, and the supplied environment renders the requested 18 px mark.

Implementation commits
- 6446b55 fix(window-buttons): enlarge window controls and native glyphs
- 81197de test(window-buttons): verify larger glyphs and compact layout

This report is committed last; no push, branch deletion, or trunk operation occurred.
