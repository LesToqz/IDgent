"""MainWindow → real Git pages → editor, on disposable showcase copies.

Run under dbus-run-session / GDK_BACKEND=x11 / xvfb-run. IDGENT_GIT_QA=1
also captures read-only real-repository screenshots and measurements in .ceo/.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, Gio, GLib, Gtk

from idgent import gitlog, settings
from idgent.gitviews import CommitView, GitGraphView
from idgent.themes import THEMES
from idgent.window import MainWindow
from tests.gitfixtures import GitRepo, build_linear, build_showcase
from tests.test_gitgraph_web import Page
from tests.test_graphview import poll, web_processes

ARTIFACTS = Path(__file__).resolve().parents[1] / '.ceo'


class WindowGitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get('DISPLAY'):
            raise unittest.SkipTest('run under xvfb-run')
        cls.app = Gtk.Application.get_default() or Gtk.Application(
            application_id='dev.idgent.GitWiringTests', flags=Gio.ApplicationFlags.NON_UNIQUE)
        if not cls.app.get_is_registered():
            cls.app.register(None)
        cls.fixture = tempfile.TemporaryDirectory(prefix='idgent-git-showcase-')
        cls.addClassCleanup(cls.fixture.cleanup)
        cls.showcase = build_showcase(Path(cls.fixture.name, 'showcase'))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='idgent-git-wiring-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name, 'repo')
        shutil.copytree(self.showcase.path, self.root)
        self.repo = GitRepo(self.root, init=False)
        env = patch.dict(os.environ, XDG_CONFIG_HOME=str(Path(self.temp.name, 'config')))
        env.start()
        self.addCleanup(env.stop)
        settings.set_value('terminal_autostart', False)
        settings.set_value('reduce_motion', True)
        self.window = MainWindow(self.app, str(self.root))
        self.window.set_default_size(1440, 900)
        self.window.show_all()
        self.addCleanup(self.destroy_window)

    def destroy_window(self):
        self.window.destroy()
        self.assertTrue(poll(lambda: not web_processes(), 8))
        self.assertFalse([t for t in threading.enumerate()
                          if t.name.startswith(('idgent-IDGit', 'idgent-IDCommit', 'idgent-commit-resolve'))])

    def page(self, view):
        if not view.available:
            self.skipTest('WebKit2 4.1 is required')
        page = object.__new__(Page)
        page.Gtk, page.GLib = Gtk, GLib
        page.view, page.win = view.webview, self.window
        return page

    def graph(self, view=None):
        view = view or self.window.open_gitgraph()
        page = self.page(view)
        page.wait_for("window.IDGit && IDGit.stats().state === 'data'", 15)
        return view, page

    def commit(self, rev):
        view = self.window.open_commit(rev)
        if view is None:
            self.assertTrue(poll(lambda: bool(self.window._commit_views)))
            view = self.window.editor.view_tab_widget(self.window.editor.current_view_key())
        page = self.page(view)
        page.wait_for("window.IDCommit && document.querySelector('.commit-header')", 15)
        self.assertTrue(poll(lambda: view.last_data is not None))
        return view, page

    def menu(self, label):
        menu = self.window.titlebar.menu_bar.get_children()[1].get_submenu()
        return next(item for item in menu.get_children() if item.get_label() == label)

    def command(self, label):
        return next(c for c in self.window.commands() if c.label == label)

    def send(self, page, message):
        page.js('webkit.messageHandlers.idgent.postMessage(' + json.dumps(message) + '); true')

    def test_menu_palette_accelerator_and_bottom_reuse(self):
        w = self.window
        entry = self.menu('Git graph')
        entry.activate()
        view, page = self.graph(w.terminal_bottom.gitgraph_views()[0])
        self.assertEqual(page.stats()['commits'], len(view.last_data['commits']))
        self.assertEqual(page.stats()['commits'], int(self.repo.git('rev-list', '--count', '--all')))
        self.assertFalse(w.terminal_right.has_gitgraph())
        self.assertIs(self.command('Git graph: Open git graph').callback(), view)
        self.assertEqual(self.command('Git graph: Open git graph').hotkey, ('Ctrl+Alt+H',))
        self.assertTrue(Gtk.accel_groups_activate(w, *Gtk.accelerator_parse('<Control><Alt>h')))
        w.terminal_bottom.new_terminal()
        terminal = w.terminal_bottom._panes[-1].terminal
        self.assertTrue(poll(lambda: terminal.get_realized()))
        terminal.grab_focus()
        event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        event.keyval, event.state = Gdk.KEY_h, Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK
        self.assertTrue(w.do_key_press_event(event))
        self.assertEqual(w._gitgraph_views(), [view])
        self.assertTrue(poll(lambda: w.get_focus() == view.webview))
        self.command('Git graph: Jump to HEAD').callback()
        self.assertEqual(view._focus, 'HEAD')
        with patch.object(view, 'refresh', wraps=view.refresh) as refresh:
            self.command('Git graph: Refresh').callback()
            refresh.assert_called_once()
        for action in (self.command('Git graph: Open git graph').callback,
                       lambda: Gtk.accel_groups_activate(w, *Gtk.accelerator_parse('<Control><Alt>h'))):
            w.terminal_bottom._close_pane(next(p for p in w.terminal_bottom._panes if p.kind == 'gitgraph'))
            action()
            reopened, _ = self.graph(w.terminal_bottom.gitgraph_views()[0])
            self.assertIsNot(reopened, view)
            self.assertFalse(w.terminal_right.has_gitgraph())
            view = reopened

    def test_full_bottom_falls_back_right_and_full_docks_notice_once(self):
        w = self.window
        for panel in (w.terminal_bottom, w.terminal_right):
            panel.show_all()
            while not panel.is_full():
                panel.new_terminal()
        with patch.object(w, 'notify') as notice:
            self.assertIsNone(w.open_gitgraph())
            self.assertIsNone(w.open_gitgraph())
            notice.assert_called_once()
        right = w.terminal_right
        right._close_pane(right._panes[-1])
        view, _ = self.graph()
        self.assertEqual(right.gitgraph_views(), [view])
        self.assertFalse(w.terminal_bottom.has_gitgraph())
        right.toggle_maximize(right._panes[-1])
        self.assertIs(w._fullscreen_panel, right)
        self.assertIs(w.open_gitgraph(), view)
        self.assertIsNone(w._fullscreen_panel)

    def test_chooser_both_docks_three_kinds_and_empty_dock_rules(self):
        w = self.window
        for panel in (w.terminal_bottom, w.terminal_right):
            panel.set_no_show_all(False)
            panel.show_all()
            panel.add_button.clicked()
            row = panel.add_button.choices.get_row_at_index(2)
            self.assertEqual(row.kind, 'gitgraph')
            panel.add_button.choices.emit('row-activated', row)
            view, _ = self.graph(panel.gitgraph_views()[0])
            panel.add_button.choose('graph')
            panel.add_button.choose('terminal')
            self.assertCountEqual([p.kind for p in panel._panes], ['gitgraph', 'graph', 'terminal'])
            terminal_pane = next(p for p in panel._panes if p.kind == 'terminal')
            terminal = terminal_pane.terminal
            self.assertTrue(poll(lambda: terminal_pane.pid is not None))
            self.assertGreater(terminal.get_row_count(), 0)
            panel._close_pane(terminal_pane)
            panel._close_pane(next(p for p in panel._panes if p.kind == 'graph'))
            self.assertTrue(panel.get_visible())
            self.assertEqual(panel.pane_count(), 1)
            panel.toggle_maximize(panel._panes[0])
            panel.restore()
            w._toggle_terminal(panel)
            w._toggle_terminal(panel)
            self.assertTrue(panel.get_visible())
            self.assertEqual(panel.terminal_count(), 0)
            self.assertFalse(view._closed)
        self.assertEqual(len(w._gitgraph_views()), 2)

    def test_dot_commit_last_title_files_parent_reopen_and_close_signal(self):
        w = self.window
        w.editor.open_file(str(self.root / 'app.py'))
        w.editor.open_file(str(self.root / 'README.md'))
        view, page = self.graph()
        head = view.last_data['head']['hash']
        x, y = page.pos(head)
        page.pointer('pointerdown', x, y)
        page.pointer('pointerup', x, y, buttons=0)
        key = 'commit:' + head
        self.assertTrue(poll(lambda: w.editor.has_view_tab(key)))
        commit = w.editor.view_tab_widget(key)
        cp = self.page(commit)
        cp.wait_for("document.querySelector('.file-name')", 15)
        nb = w.editor.notebook
        self.assertIs(nb.get_nth_page(nb.get_n_pages() - 1), commit)
        self.assertEqual(w.editor.current_view_key(), key)
        self.assertEqual(w.editor._view_tabs[key].label.get_text(), head[:7])
        self.assertIn('Prepare the release', w.editor._view_tabs[key].header.get_tooltip_text())
        self.assertEqual(cp.js("Array.from(document.querySelectorAll('.file-name'), e=>e.textContent)"), ['README.md'])
        parent = commit.last_data['parents'][0]
        cp.js("document.querySelector('.parent').click(); true")
        self.assertTrue(poll(lambda: w.editor.current_view_key() == 'commit:' + parent))
        self.assertEqual(nb.get_n_pages(), 4)
        self.assertIs(w.open_commit(head[:7], view), commit)
        self.assertEqual(nb.get_n_pages(), 4)
        self.assertEqual(w.editor.current_view_key(), key)
        closed = []
        w.editor.connect('view-tab-closed', lambda _, k: closed.append(k))
        self.assertTrue(w.editor.close_view_tab(key))
        self.assertEqual(closed, [key])
        self.assertTrue(commit._closed)

    def test_selection_sync_files_commits_later_graph_and_graph_close(self):
        w = self.window
        w.editor.open_file(str(self.root / 'app.py'))
        view, page = self.graph()
        hashes = [c['hash'] for c in view.last_data['commits'][:2]]
        first, _ = self.commit(hashes[0])
        page.wait_for('IDGit.stats().selected === ' + json.dumps(hashes[0]))
        w.terminal_right.show_all()
        w.terminal_right.add_button.choose('gitgraph')
        other, op = self.graph(w.terminal_right.gitgraph_views()[0])
        op.wait_for('IDGit.stats().selected === ' + json.dumps(hashes[0]))
        self.send(op, {'type': 'open-commit', 'hash': hashes[1]})
        self.assertTrue(poll(lambda: w.editor.current_view_key() == 'commit:' + hashes[1]))
        for p in (page, op):
            p.wait_for('IDGit.stats().selected === ' + json.dumps(hashes[1]))
        w.editor.open_file(str(self.root / 'app.py'))
        for p in (page, op):
            p.wait_for('IDGit.stats().selected === null')
        w.open_commit(hashes[0])
        w.editor.close_view_tab('commit:' + hashes[0])
        for p in (page, op):
            p.wait_for('IDGit.stats().selected === ' + json.dumps(hashes[1]))
        remaining = w.editor.view_tab_widget('commit:' + hashes[1])
        w.terminal_bottom._close_pane(w.terminal_bottom._panes[0])
        w.terminal_right._close_pane(w.terminal_right._panes[0])
        self.assertTrue(view._closed and other._closed)
        self.assertFalse(remaining._closed)
        w.open_commit(hashes[1])
        later, lp = self.graph()
        lp.wait_for('IDGit.stats().selected === ' + json.dumps(hashes[1]))
        w.editor.close_view_tab('commit:' + hashes[1])
        lp.wait_for('IDGit.stats().selected === null')

    def test_commit_file_links_existing_and_deleted(self):
        w = self.window
        data = gitlog.collect_graph(self.root)
        root_hash = next(c['hash'] for c in data['commits'] if not c['parents'])
        view, page = self.commit(root_hash)
        page.js("Array.from(document.querySelectorAll('.file-name')).find(e=>e.textContent === 'app.py').click(); true")
        self.assertTrue(poll(lambda: w.editor.current_path() == str(self.root / 'app.py')))
        w.open_commit(root_hash)
        with patch.object(w, 'notify') as notice:
            page.js("Array.from(document.querySelectorAll('.file-name')).find(e=>e.textContent === 'obsolete.txt').click(); true")
            self.assertTrue(poll(lambda: notice.called))
            notice.assert_called_once_with('File not in the working tree')
        with patch.object(w.editor, 'open_file_at') as opened:
            view.emit('open-file', str(self.root / 'app.py'), 9)
            opened.assert_called_once_with(str(self.root / 'app.py'), 8)

    def test_theme_accent_fonts_motion_and_size(self):
        w = self.window
        view, page = self.graph()
        commit, cp = self.commit(view.last_data['head']['hash'])
        theme = next(t for t in THEMES if not t.dark)
        w.set_theme(theme.id, persist=False)
        self.assertEqual(view._theme['background'], theme.ui['bg'])
        self.assertEqual(commit._theme['syntax'], {k: v.foreground for k, v in w.editor.syntax_style.items()})
        self.assertFalse(commit._theme['dark'])
        page.wait_for('getComputedStyle(document.body).backgroundColor === ' +
                      json.dumps(cp.js('getComputedStyle(document.body).backgroundColor')))
        self.assertEqual(cp.js("getComputedStyle(document.documentElement).getPropertyValue('--bg').trim()"), theme.ui['bg'])
        w._setting_changed(None, 'accent', '#ff0088')
        w._setting_changed(None, 'ui_font', 'DejaVu Sans')
        w._setting_changed(None, 'editor_font', 'DejaVu Sans Mono')
        self.assertEqual(view._theme['accent'], '#ff0088')
        self.assertEqual(commit._theme['accent'], '#ff0088')
        self.assertIn('DejaVu Sans Mono', commit._theme['mono'])
        self.assertIn('DejaVu Sans', view._theme['ui_font'])
        w._font_size_changed(None, 'editor_font_size', 21)
        self.assertEqual(commit._theme['size'], 21)
        self.assertEqual(cp.js("getComputedStyle(document.documentElement).getPropertyValue('--size').trim()"), '21px')
        w._setting_changed(None, 'reduce_motion', False)
        self.assertEqual(page.js('IDGit.stats().reduceMotion'), False)
        self.assertFalse(cp.js("document.documentElement.classList.contains('reduce-motion')"))
        w._setting_changed(None, 'reduce_motion', True)
        self.assertEqual(page.js('IDGit.stats().reduceMotion'), True)
        self.assertTrue(cp.js("document.documentElement.classList.contains('reduce-motion')"))
        w.editor.open_file(str(self.root / 'app.py'))
        self.command('Editor: Zoom in').callback()
        w.open_commit(view.last_data['head']['hash'])
        self.assertEqual(commit._theme['size'], 22)
        self.command('Editor: Reset zoom').callback()
        self.assertEqual(commit._theme['size'], 21)

    def test_monitor_refresh_commit_survives_folder_change_closes_and_reroots(self):
        w = self.window
        view, page = self.graph()
        commit, _ = self.commit(view.last_data['head']['hash'])
        count = len(view.last_data['commits'])
        sha = self.repo.commit({'fresh.py': 'VALUE = 42\n'}, message='Monitor refresh integration')
        self.assertTrue(poll(lambda: len((view.last_data or {}).get('commits', [])) == count + 1, 10))
        page.wait_for('IDGit.stats().commits === ' + str(count + 1))
        self.assertEqual(view.last_data['head']['hash'], sha)
        self.assertFalse(commit._closed)
        self.assertTrue(w.editor.current_view_key().startswith('commit:'))
        other = build_linear(Path(self.temp.name, 'other'), 2)
        w.set_root(str(other.path))
        self.assertEqual(w._commit_views, {})
        self.assertTrue(commit._closed)
        self.assertIsNone(w.editor.current_view_key())
        page.wait_for('IDGit.stats().commits === 2')
        self.assertEqual(view.root, str(other.path))
        self.assertIsNone(page.stats()['selected'])
        w.terminal_bottom._close_pane(w.terminal_bottom._panes[0])
        self.assertTrue(view._closed)
        self.assertFalse(view._monitors)
        self.assertIsNone(view._debounce)
        self.assertFalse(view._sources)
        self.assertFalse(w.terminal_bottom.get_visible())

    def test_short_hash_worker_validation_dedup_and_stale_folder(self):
        w = self.window
        sha = self.repo.git('rev-parse', 'HEAD')
        original = gitlog._run
        workers = []
        def resolve(*args, **kwargs):
            if args[1][:3] == ['rev-parse', '--verify', '--end-of-options']:
                workers.append(threading.current_thread())
            return original(*args, **kwargs)
        with patch('idgent.window.gitlog._run', side_effect=resolve):
            w.open_commit(sha[:8])
            w.open_commit(sha[:8])
            self.assertTrue(poll(lambda: w.editor.has_view_tab('commit:' + sha)))
        self.assertEqual(len(w._commit_views), 1)
        self.assertEqual(len(workers), 1)
        self.assertIsNot(workers[0], threading.main_thread())
        with patch.object(w, 'notify') as notice, patch('idgent.window.gitlog._run') as run:
            for bad in ('HEAD', '--all', 'abcd~1', 'a' * 41, 'deadbeef;echo hi', 'deadbeef\n'):
                self.assertIsNone(w.open_commit(bad))
            self.assertEqual(notice.call_count, 6)
            run.assert_not_called()
        w.close_commit_tabs()
        entered, release = threading.Event(), threading.Event()
        def slow(*args, **kwargs):
            entered.set()
            release.wait(3)
            return original(*args, **kwargs)
        with patch('idgent.window.gitlog._run', side_effect=slow):
            w.open_commit(sha[:9])
            self.assertTrue(entered.wait(2))
            plain = Path(self.temp.name, 'plain')
            plain.mkdir()
            w.set_root(str(plain))
            release.set()
            w._commit_executor.submit(lambda: None).result(timeout=5)
            self.assertTrue(poll(lambda: not w._git_sources))
        self.assertEqual(w._commit_views, {})

    @staticmethod
    def page_for_pump():
        page = object.__new__(Page)
        page.GLib = GLib
        return page

    def test_empty_states_and_error_notice_once(self):
        w = self.window
        plain = Path(self.temp.name, 'plain')
        plain.mkdir()
        w.set_root(str(plain))
        view = w.open_gitgraph()
        page = self.page(view)
        page.wait_for("window.IDGit && IDGit.stats().state === 'not-a-repo'", 15)
        self.assertIn('Not a Git repository', page.js("document.getElementById('state').innerText"))
        self.assertIn('Open a folder containing a Git repository.', page.js("document.getElementById('state').innerText"))
        empty = GitRepo(Path(self.temp.name, 'empty'))
        w.set_root(str(empty.path))
        page.wait_for("IDGit.stats().state === 'empty'")
        self.assertIn('No commits yet', page.js("document.getElementById('state').innerText"))
        self.assertIn('Make your first commit', page.js("document.getElementById('state').innerText"))
        with patch.object(w, 'notify') as notice, patch('sys.stderr', new_callable=io.StringIO) as log:
            view.emit('error', 'same failure')
            view.emit('error', 'same failure')
            view.emit('error', 'another failure')
            notice.assert_called_once()
            self.assertEqual(log.getvalue().count('Git graph:'), 2)

    @unittest.skipUnless(os.environ.get('IDGENT_GIT_QA') == '1', 'opt-in read-only real-repository QA')
    def test_real_repository_qa(self):
        w = self.window
        shots = ARTIFACTS / 'shots'
        shots.mkdir(parents=True, exist_ok=True)
        measurements = {'backend': 'X11 / Xvfb, software WebKit rendering'}
        dark = next(t for t in THEMES if t.id == 'dark-plus')
        w.set_theme(dark.id, persist=False)

        def shot(name):
            Page.pump(self.page_for_pump(), .2)
            allocation = w.get_allocation()
            pixbuf = Gdk.pixbuf_get_from_window(w.get_window(), 0, 0, allocation.width, allocation.height)
            self.assertIsNotNone(pixbuf)
            pixbuf.savev(str(shots / (name + '.png')), 'png', [], [])

        def first_paint(page, selector):
            page.wait_for(selector, 20)
            page.js('window.__qaPaint = false; requestAnimationFrame(()=>requestAnimationFrame(()=>window.__qaPaint=true)); true')
            page.wait_for('window.__qaPaint', 5)

        # Only read-only Git collection and file opening touch these repositories.
        main = Path('/home/lestoq/projects/IDgent')
        demo = Path('/home/lestoq/projects/demo-git')
        self.assertTrue(main.is_dir() and demo.is_dir())
        w.set_root(str(main))
        w.editor.open_file(str(main / 'README.md'))
        w.editor.open_file(str(main / 'idgent/window.py'))
        started = time.monotonic()
        view, page = self.graph()
        first_paint(page, "IDGit.stats().frames > 0")
        measurements['main_graph_first_paint_ms'] = round((time.monotonic() - started) * 1000, 1)
        measurements['main_commits'] = len(view.last_data['commits'])
        w.dock_bottom.set_position(470)
        view.focus_head()
        page.pump(.2)
        head = view.last_data['head']['hash']
        x, y = page.pos(head)
        self.assertTrue(0 < x < page.stats()['width'] and 0 < y < page.stats()['height'])
        measurements['main_head_position'] = [round(x, 1), round(y, 1)]
        shot('git-integration-main-bottom')

        w.set_root(str(demo))
        page.wait_for("IDGit.stats().state === 'data' && IDGit.stats().commits < 100")
        w.editor.open_file(str(demo / 'app.py'))
        w.editor.open_file(str(demo / 'README.md'))
        view.focus_head()
        page.pump(.2)
        measurements['showcase_commits'] = len(view.last_data['commits'])
        shot('git-integration-showcase-bottom')
        head = view.last_data['head']['hash']
        x, y = page.pos(head)
        page.pointer('pointermove', x, y, buttons=0)
        page.wait_for('IDGit.stats().tip')
        shot('git-integration-hover')
        started = time.monotonic()
        page.pointer('pointerdown', x, y)
        page.pointer('pointerup', x, y, buttons=0)
        key = 'commit:' + head
        self.assertTrue(poll(lambda: w.editor.has_view_tab(key)))
        commit = w.editor.view_tab_widget(key)
        cp = self.page(commit)
        first_paint(cp, "document.querySelector('.file-name')")
        measurements['showcase_click_commit_first_paint_ms'] = round((time.monotonic() - started) * 1000, 1)
        page.pointer('pointerleave', -5, -5)
        shot('git-integration-click-commit-tab')

        data = view.last_data
        root_hash = next(c['hash'] for c in data['commits'] if not c['parents'])
        many, mp = self.commit(root_hash)
        measurements['many_files_commit_count'] = many.last_data['stats']['files']
        mp.js("document.getElementById('toggle-all').click(); true")
        shot('git-integration-many-files')
        another = next(c['hash'] for c in data['commits'] if len(c['parents']) > 1)
        self.commit(another)
        processes = web_processes()
        rss = {str(pid): next(int(line.split()[1]) for line in Path(f'/proc/{pid}/status').read_text().splitlines()
                             if line.startswith('VmRSS:')) for pid in processes}
        measurements['graph_plus_three_commit_tabs_web_rss_kib'] = rss
        measurements['graph_plus_three_commit_tabs_total_web_rss_kib'] = sum(rss.values())
        self.assertEqual(len(w._commit_views), 3)
        self.assertEqual(len(processes), 2)
        light = next(t for t in THEMES if not t.dark)
        w.set_theme(light.id, persist=False)
        self.commit(head)
        shot('git-integration-light')
        w.set_theme(dark.id, persist=False)

        # The chooser permits one per dock; global commands still reuse one.
        w.terminal_right.set_no_show_all(False)
        w.terminal_right.show_all()
        w.terminal_right.add_button.choose('gitgraph')
        right, rp = self.graph(w.terminal_right.gitgraph_views()[0])
        right.focus_head()
        rp.pump(.2)
        shot('git-integration-right-and-bottom')
        w.terminal_bottom._close_pane(w.terminal_bottom._panes[0])
        shot('git-integration-right')
        w.close_commit_tabs()
        plain = Path(self.temp.name, 'qa-plain')
        plain.mkdir()
        w.set_root(str(plain))
        rp.wait_for("IDGit.stats().state === 'not-a-repo'")
        shot('git-integration-not-a-repo')
        empty = GitRepo(Path(self.temp.name, 'qa-empty'))
        w.set_root(str(empty.path))
        rp.wait_for("IDGit.stats().state === 'empty'")
        shot('git-integration-empty-repo')
        (ARTIFACTS / 'git-integration-measurements.json').write_text(json.dumps(measurements, indent=2) + '\n')


if __name__ == '__main__':
    unittest.main()
