"""Real window, filesystem signals, key dispatch, shells and user packaging."""
import configparser
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
import warnings
from unittest.mock import patch

# Keep GTK on the Xvfb display even when WAYLAND_DISPLAY is inherited.
os.environ["GDK_BACKEND"] = "x11"

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('Vte', '2.91')
from gi.repository import Gdk, Gio, GLib, Gtk, Vte

from idgent import editor
from idgent.window import MainWindow


def setUpModule():
    warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"gi\.events")


def poll(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    context = GLib.MainContext.default()
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        if predicate():
            return True
        time.sleep(.01)
    return bool(predicate())


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class WindowIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = Gtk.Application.get_default() or Gtk.Application(
            application_id="dev.idgent.Tests",
                                          flags=Gio.ApplicationFlags.NON_UNIQUE)
        if not cls.application.get_is_registered():
            assert cls.application.register(None)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='idgent-integration-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        # A predictable shell avoids user bash startup files and prompt hooks.
        self.environment = patch.dict(os.environ, {'SHELL': '/bin/sh', 'PS1': '$ '})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.window = MainWindow(self.application, str(self.root))
        self.pids = set()
        self.addCleanup(self.destroy_window)
        self.window.show_all()
        self.spawn(self.window.terminal_bottom._panes[0])

    def spawn(self, tab):
        self.assertTrue(poll(lambda: tab.pid is not None), 'shell did not spawn')
        self.pids.add(tab.pid)
        self.assertTrue(poll(lambda: "$" in self.text(tab)), "shell prompt did not appear")
        return tab

    def destroy_window(self):
        self.window.destroy()
        stopped = poll(lambda: all(not alive(pid) for pid in self.pids))
        for pid in self.pids:
            if alive(pid):
                os.kill(pid, signal.SIGKILL)
        self.assertTrue(stopped, 'shell survived window destruction')

    def open_file(self, name='file.py', content='original\n'):
        path = self.root / name
        path.write_text(content)
        self.window.explorer.refresh()
        self.window.explorer.tree.expand_row(Gtk.TreePath.new_from_string('0'), False)
        row = self.window.explorer._find(str(path))
        self.assertIsNotNone(row)
        tree = self.window.explorer.tree
        tree.row_activated(self.window.explorer.store.get_path(row), tree.get_column(0))
        return path, self.window.editor._current()

    def test_explorer_activation_edit_and_save(self):
        path, tab = self.open_file()
        self.assertEqual(tab.path, str(path))
        tab.buffer.set_text('edited\n')
        self.assertTrue(self.window.editor.save_current())
        self.assertEqual(path.read_text(), 'edited\n')
        self.assertFalse(self.window.editor.has_unsaved())

    def test_explorer_mutations_retarget_and_mark_deleted(self):
        explorer, editor = self.window.explorer, self.window.editor
        folder = explorer._create(str(self.root), 'folder', True)
        path = explorer._create(folder, 'created.py')
        self.assertTrue(Path(path).is_file())
        explorer.emit('file-activated', path)
        tab = editor._current()
        path = explorer._rename(path, 'renamed.py')
        self.assertEqual(tab.path, path)
        folder = explorer._rename(folder, 'renamed-folder')
        self.assertEqual(tab.path, str(Path(folder) / 'renamed.py'))
        tab.buffer.set_text('after rename\n')
        self.assertTrue(editor.save_current())
        self.assertEqual(Path(tab.path).read_text(), 'after rename\n')
        explorer._delete(tab.path)
        self.assertTrue(tab.deleted)
        self.assertTrue(tab.buffer.get_modified())
        other = explorer._create(folder, 'other.py')
        explorer.emit('file-activated', other)
        other_tab = editor._current()
        explorer._delete(folder)
        self.assertTrue(other_tab.deleted)
        self.assertTrue(other_tab.buffer.get_modified())
        self.assertIn('(deleted)', other_tab.label.get_text())
        self.assertFalse(Path(folder).exists())

    @staticmethod
    def text(tab):
        return tab.terminal.get_text_format(Vte.Format.TEXT) or ''

    def test_hide_show_both_docks_keeps_live_shells(self):
        self.window.toggle_terminal_right()
        self.spawn(self.window.terminal_right._panes[0])
        for panel, toggle in ((self.window.terminal_bottom, self.window.toggle_terminal_bottom),
                              (self.window.terminal_right, self.window.toggle_terminal_right)):
            pane = panel._panes[0]
            pid = pane.pid
            toggle()
            self.assertFalse(panel.get_visible())
            toggle()
            self.assertTrue(panel.get_visible())
            self.assertIs(panel._panes[0], pane)
            self.assertEqual(pane.pid, pid)
            pane.terminal.feed_child(b"printf '%s%s\\n' 'integ_' 'ok'\n")
            self.assertTrue(poll(lambda: 'integ_ok' in self.text(pane).splitlines()), repr(self.text(pane)))
            self.assertTrue(alive(pid))

    def test_split_targets_focused_dock_and_defaults_to_bottom(self):
        self.accelerate('<Control><Alt>grave')
        right = self.window.terminal_right
        self.spawn(right._panes[0])
        right.focus_terminal()
        self.assertTrue(self.key('<Control><Shift>5'))
        self.assertEqual(right.terminal_count(), 2)
        self.spawn(right._panes[-1])
        self.assertEqual(self.window.terminal_bottom.terminal_count(), 1)
        self.window.toggle_terminal_bottom()
        _, editor = self.open_file()
        editor.view.grab_focus()
        self.accelerate('<Control><Shift>5')
        self.assertTrue(self.window.terminal_bottom.get_visible())
        self.assertEqual(self.window.terminal_bottom.terminal_count(), 2)
        self.spawn(self.window.terminal_bottom._panes[-1])

    def test_background_exit_preserves_editor_focus(self):
        panel = self.window.terminal_bottom
        panel.new_terminal()
        exiting = self.spawn(panel._panes[-1])
        _, editor = self.open_file()
        editor.view.grab_focus()
        exiting.terminal.feed_child(b'exit\n')
        self.assertTrue(poll(lambda: panel.terminal_count() == 1))
        self.assertIs(self.window.get_focus(), editor.view)

    def test_destroy_cleans_both_docks(self):
        self.window.toggle_terminal_right()
        self.spawn(self.window.terminal_right._panes[0])
        for panel in (self.window.terminal_bottom, self.window.terminal_right):
            panel.new_terminal()
            self.spawn(panel._panes[-1])
        self.window.destroy()
        self.assertTrue(poll(lambda: all(not alive(pid) for pid in self.pids)))

    def test_folder_change_only_affects_new_shells(self):
        old = self.window.terminal_bottom._panes[0]
        new_root = self.root / 'new-project'
        new_root.mkdir()
        self.window.set_root(str(new_root))
        self.assertEqual(self.window.explorer.root, str(new_root))
        self.assertEqual(self.window.get_title(), 'IDgent')
        self.window.terminal_bottom.new_terminal()
        new = self.spawn(self.window.terminal_bottom._panes[-1])
        self.window.toggle_terminal_right()
        right = self.spawn(self.window.terminal_right._panes[0])
        for tab, expected in ((old, self.root), (new, new_root), (right, new_root)):
            tab.terminal.feed_child(b'pwd\n')
            self.assertTrue(poll(lambda: str(expected) in self.text(tab).splitlines()), repr(self.text(tab)))

    def test_unsaved_quit_decisions(self):
        path, tab = self.open_file()
        self.assertFalse(self.window._on_delete(self.window, None))
        tab.buffer.set_text('unsaved\n')
        with patch.object(self.window, '_ask_quit', return_value=Gtk.ResponseType.CANCEL):
            self.assertTrue(self.window._on_delete(self.window, None))
        with patch.object(self.window, '_ask_quit', return_value=Gtk.ResponseType.REJECT):
            self.assertFalse(self.window._on_delete(self.window, None))
            self.assertTrue(self.window.editor.has_unsaved())
        with patch.object(self.window, '_ask_quit', return_value=Gtk.ResponseType.ACCEPT):
            with patch.object(self.window.editor, 'save_all', return_value=False):
                self.assertTrue(self.window._on_delete(self.window, None))
            self.assertFalse(self.window._on_delete(self.window, None))
        self.assertEqual(path.read_text(), 'unsaved\n')

    def accelerate(self, shortcut):
        key, modifiers = Gtk.accelerator_parse(shortcut)
        self.assertTrue(Gtk.accel_groups_activate(self.window, key, modifiers), shortcut)

    def key(self, shortcut):
        key, modifiers = Gtk.accelerator_parse(shortcut)
        event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        event.window = self.window.get_window()
        event.set_device(Gdk.Display.get_default().get_default_seat().get_keyboard())
        event.send_event = True
        event.time = Gdk.CURRENT_TIME
        event.keyval, event.state = key, modifiers
        return self.window.emit('key-press-event', event)

    def test_readme_menu_actions_and_accelerators(self):
        self.accelerate('<Control>grave')
        self.assertFalse(self.window.terminal_bottom.get_visible())
        self.accelerate('<Control>grave')
        self.assertTrue(self.window.terminal_bottom.get_visible())
        self.accelerate('<Control>b')
        self.assertFalse(self.window.explorer.get_visible())
        self.accelerate('<Control>b')
        self.assertTrue(self.window.explorer.get_visible())
        self.accelerate('<Control><Alt>grave')
        self.assertTrue(self.window.terminal_right.get_visible())
        self.spawn(self.window.terminal_right._panes[0])
        self.accelerate('<Control><Alt>grave')
        self.assertFalse(self.window.terminal_right.get_visible())
        path, tab = self.open_file()
        tab.buffer.set_text('saved through menu\n')
        self.accelerate('<Control>s')
        self.assertEqual(path.read_text(), 'saved through menu\n')
        tab.buffer.set_text('saved all\n')
        self.accelerate('<Control><Shift>s')
        self.assertEqual(path.read_text(), 'saved all\n')
        tab.buffer.set_text('quit cancelled\n')
        with patch.object(self.window, '_ask_quit', return_value=Gtk.ResponseType.CANCEL) as ask:
            self.accelerate('<Control>q')
            self.assertTrue(poll(lambda: ask.called))
            self.assertTrue(self.window.get_visible())

    def test_fullscreen_menu_accelerator_and_key(self):
        self.accelerate('<Control><Shift>F11')
        self.assertTrue(self.window.terminal_bottom.is_maximized())
        self.accelerate('<Control><Shift>F11')
        self.assertFalse(self.window.terminal_bottom.is_maximized())
        _, tab = self.open_file()
        tab.view.grab_focus()
        self.assertTrue(self.key('<Control><Shift>F11'))
        self.assertTrue(self.window.terminal_bottom.is_maximized())
        self.assertTrue(self.key('<Control><Shift>F11'))
        self.assertFalse(self.window.terminal_bottom.is_maximized())

    def test_editor_focus_receives_commands_before_menu(self):
        path, tab = self.open_file(content='value = 1\n')
        # Finish initial terminal focus idles before assigning editor focus.
        self.assertTrue(poll(lambda: self.window.terminal_bottom._panes[0].pid is not None))
        tab.view.grab_focus()
        self.assertIs(self.window.get_focus(), tab.view)
        tab.buffer.set_text('value = 2\n')
        self.assertTrue(tab.buffer.get_modified())
        self.assertTrue(tab.label.get_text().startswith('● '))
        with patch.object(editor, 'atomic_write', wraps=editor.atomic_write) as write, \
             patch.object(self.window.editor, '_save', wraps=self.window.editor._save) as save:
            self.assertTrue(self.key('<Control>s'))
            # Intercept the actual save/write paths, including menu callbacks
            # that might have captured a bound save_current method earlier.
            save.assert_called_once_with(tab)
            write.assert_called_once_with(str(path), 'value = 2\n', 'LF')
        self.assertEqual(path.read_text(), 'value = 2\n')
        self.assertFalse(tab.buffer.get_modified())
        self.assertFalse(self.window.editor.has_unsaved())
        self.assertEqual(tab.label.get_text(), path.name)
        tab.buffer.place_cursor(tab.buffer.get_start_iter())
        self.assertTrue(self.key('<Control>slash'))
        self.assertEqual(tab.buffer.get_text(*tab.buffer.get_bounds(), True), '# value = 2\n')
        self.window.editor.save_current()
        self.assertTrue(self.key('<Control>w'))
        self.assertEqual(self.window.editor.tabs, [])

    def test_save_from_explorer_focus_writes_once_and_clears_dirty_marker(self):
        path, tab = self.open_file()
        self.window.explorer.tree.grab_focus()
        self.assertIs(self.window.get_focus(), self.window.explorer.tree)
        tab.buffer.set_text('saved from explorer\n')
        self.assertTrue(tab.buffer.get_modified())
        self.assertTrue(tab.label.get_text().startswith('● '))
        with patch.object(editor, 'atomic_write', wraps=editor.atomic_write) as write, \
             patch.object(self.window.editor, '_save', wraps=self.window.editor._save) as save:
            self.assertTrue(self.key('<Control>s'))
            save.assert_called_once_with(tab)
            write.assert_called_once_with(str(path), 'saved from explorer\n', 'LF')
        self.assertEqual(path.read_text(), 'saved from explorer\n')
        self.assertFalse(tab.buffer.get_modified())
        self.assertFalse(self.window.editor.has_unsaved())
        self.assertEqual(tab.label.get_text(), path.name)

    def test_terminal_save_key_is_forwarded_but_file_menu_still_saves(self):
        path, tab = self.open_file()
        self.window.toggle_terminal_right()
        self.spawn(self.window.terminal_right._panes[0])
        for panel in (self.window.terminal_bottom, self.window.terminal_right):
            with self.subTest(panel=panel):
                tab.buffer.set_text('leave Ctrl+S to the shell\n')
                panel.focus_terminal()
                terminal = panel._panes[0].terminal
                self.assertIs(self.window.get_focus(), terminal)
                seen = []
                handler = terminal.connect('key-press-event',
                                           lambda _, e: seen.append(e.keyval) or False)
                try:
                    with patch.object(editor, 'atomic_write', wraps=editor.atomic_write) as write:
                        self.key('<Control>s')
                        self.assertIn(Gdk.KEY_s, seen)
                        write.assert_not_called()
                        # Even an unhandled terminal event must bypass menu acceleration.
                        with patch.object(self.window, 'propagate_key_event', return_value=False) as propagate:
                            self.assertFalse(self.key('<Control>s'))
                            propagate.assert_called_once()
                        write.assert_not_called()
                finally:
                    terminal.disconnect(handler)
                self.assertEqual(path.read_text(), 'original\n')
                self.assertTrue(tab.buffer.get_modified())
                self.assertTrue(tab.label.get_text().startswith('● '))

        # Activate File > Save itself with the terminal still focused.
        menu_bar = self.window.titlebar.menu_bar
        file_menu = menu_bar.get_children()[0].get_submenu()
        with patch.object(editor, 'atomic_write', wraps=editor.atomic_write) as write, \
             patch.object(self.window.editor, '_save', wraps=self.window.editor._save) as save:
            file_menu.get_children()[1].activate()
            save.assert_called_once_with(tab)
            write.assert_called_once_with(str(path), 'leave Ctrl+S to the shell\n', 'LF')
        self.assertEqual(path.read_text(), 'leave Ctrl+S to the shell\n')
        self.assertFalse(tab.buffer.get_modified())
        self.assertFalse(self.window.editor.has_unsaved())
        self.assertEqual(tab.label.get_text(), path.name)


class PackagingTests(unittest.TestCase):
    def test_desktop_and_isolated_install_uninstall(self):
        repo = Path(__file__).resolve().parents[1]
        desktop = repo / 'packaging/idgent.desktop'
        validator = shutil.which('desktop-file-validate')
        if validator:
            subprocess.run([validator, str(desktop)], check=True)
        config = configparser.ConfigParser(interpolation=None)
        config.read(desktop)
        entry = config['Desktop Entry']
        for key, value in {'Name': 'IDgent', 'Exec': 'idgent', 'Icon': 'idgent',
                           'Terminal': 'false', 'Type': 'Application',
                           'Categories': 'Development;IDE;TextEditor;'}.items():
            self.assertEqual(entry[key], value)
        self.assertTrue(entry['Comment'])
        with tempfile.TemporaryDirectory(prefix='idgent-home-') as home:
            env = dict(os.environ, HOME=home)
            script = ['bash', str(repo / 'packaging/install.sh')]
            launcher = Path(home) / '.local/bin/idgent'
            assets = [launcher, Path(home) / '.local/share/applications/idgent.desktop',
                      Path(home) / '.local/share/icons/hicolor/scalable/apps/idgent.svg']
            for _ in range(2):
                subprocess.run(script, env=env, check=True)
                self.assertTrue(all(path.is_file() for path in assets))
            result = subprocess.run([str(launcher), '--help'], env=env, cwd=home,
                                    capture_output=True, text=True, check=True)
            self.assertIn('usage:', result.stdout)
            # A fake python records exact argv and PYTHONPATH without opening GTK.
            fakebin = Path(home) / 'fakebin'
            fakebin.mkdir()
            fake = fakebin / 'python3'
            fake.write_text('#!/bin/bash\nprintf "%s\\n" "$PYTHONPATH" "$@"\n')
            fake.chmod(0o755)
            result = subprocess.run([str(launcher), 'folder with spaces'],
                                    env=dict(env, PATH=f'{fakebin}:/usr/bin:/bin'),
                                    capture_output=True, text=True, check=True)
            lines = result.stdout.splitlines()
            self.assertEqual(lines[0].split(':')[0], str(repo))
            self.assertEqual(lines[1:], ['-m', 'idgent', 'folder with spaces'])
            for _ in range(2):
                subprocess.run(script + ['--uninstall'], env=env, check=True)
                self.assertTrue(all(not path.exists() for path in assets))
