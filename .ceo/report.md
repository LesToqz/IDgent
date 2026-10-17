STATUS: DONE

Implemented a single client-side titlebar with File/View menus at the left,
the folder title centred, and minimize/maximize/close controls on the right.

Files changed:
- idgent/titlebar.py: new Gtk.HeaderBar subclass and scoped TITLEBAR_CSS.
- idgent/window.py: install the titlebar, remove the content menu row, and
  synchronize the header label alongside the existing window title.
- tests/test_titlebar.py: real GTK hierarchy, folder changes, root-path title,
  decoration controls, CSS parsing/height/centering, and all seven accelerators.
- tests/test_window_layout.py: verify sidebar-only content and synchronized title.
- tests/test_integration.py: locate File > Save through the titlebar menu bar.

Verification:
- xvfb-run -a python3 -m unittest tests.test_titlebar tests.test_window_layout tests.test_integration
  PASS: 26 tests, 2.998s.
- xvfb-run -a python3 -m unittest discover -s tests
  PASS: 93 tests, 5.257s.
- git diff --check: PASS.
- Visual check: GDK_BACKEND=x11 xvfb-run -a python3 with a throw-away
  Gtk.CssProvider applying TITLEBAR_CSS over the existing theme; collected
  parsing-error signals (none), iterated the main loop, and captured the window
  using Gdk.pixbuf_get_from_window. Viewed .ceo/titlebar-visual.png: menus are
  unclipped and vertically aligned, title centred, controls right aligned,
  dark palette correct, bar height 33px. PNG is ignored and uncommitted.

Final API:
- TitleBar(menu_bar: Gtk.MenuBar): packs the existing menu bar at the start,
  vertically centred; installs a custom title label and right-hand decorations.
- TitleBar.set_folder(root: str) -> None: sets IDgent - <basename>, preserving /.
- TitleBar.menu_bar: the supplied Gtk.MenuBar.
- TitleBar.title_label: the centred Gtk.Label with class idgent-title.
- TITLEBAR_CSS: module-level CSS string, ready for the later theme integration.
- MainWindow.titlebar: the installed TitleBar; _update_title updates both titles.

Open issues / assumptions:
- None blocking. As requested, style.py is unchanged; the later theme task must
  append TITLEBAR_CSS for the application to use this styling by default.
- Existing GTK/PyGObject deprecation warnings and a desktop category hint are
  emitted by the suite; all tests pass.
- HeaderBar's built-in dragging and double-click handling are preserved; no
  custom pointer-event handling was introduced. Xvfb screenshot has no window
  manager, so interactive window-manager gestures were not exercised.
