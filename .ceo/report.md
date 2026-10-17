STATUS: DONE

Changes
- idgent/terminal.py: implemented the fixed TerminalPanel contract plus focus_terminal(); slim header, scrollable notebook, per-tab controls, native VTE scrollbar, interactive async shell spawning, inherited environment overrides, cwd fallback, automatic last-tab replacement, keyboard/context actions, bounded font zoom, dark palette, focus management, and PID-based SIGHUP cleanup (including late spawn callbacks).
- idgent/__init__.py: created empty as permitted.
- tests/test_terminal_panel.py: 13 helper/integration tests with real shells and GLib polling (5-second timeout), covering tabs, cwd isolation, environment, exit/replacement, focus/zoom, re-parenting, and destruction.
- .ceo/task.md: preserved supplied brief unchanged for a clean worktree.

Verification
- xvfb-run -a python3 -m unittest tests.test_terminal_panel -v: PASS, 13 tests in 1.841 seconds.
- timeout 5 xvfb-run -a python3 -c 'from idgent.terminal import TerminalPanel; from gi.repository import Gtk, GLib; w = Gtk.Window(); w.set_default_size(800, 400); w.add(TerminalPanel(".")); w.show_all(); GLib.timeout_add(3500, lambda: (w.destroy(), False)[1]); GLib.timeout_add(3800, lambda: (Gtk.main_quit(), False)[1]); Gtk.main()': PASS, exit 0, no traceback or diagnostic output.
- git diff --check: PASS.
- Confirmed installed VTE 0.84 PyGObject spawn_async signature locally; keyword arguments avoid its hidden child_setup_data positional slot.

Open issues / assumptions
- No implementation blockers. Shells receive SIGHUP on explicit close/destroy; real bash child processes were verified gone.
- VTE 0.84 rejects the attributes array automatically supplied by legacy PyGObject get_text(None, None), producing a VTE critical and empty output. Output assertions therefore use get_text_format(Vte.Format.TEXT), its supported replacement; feed_child and real-shell output polling remain as requested. See https://gnome.pages.gitlab.gnome.org/vte/gtk3/method.Terminal.get_text_format.html.
- Re-parenting test deliberately exercises Gtk.Widget.reparent, which emits one expected GTK deprecation warning. Python 3.14 deprecations originating in gi.events are filtered in tests.
