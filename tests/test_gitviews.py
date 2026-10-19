"""Real WebKit hosts with contract stub pages and deterministic Git repositories."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, GLib, Gtk

from idgent import gitviews, motion, settings
from idgent.gitviews import CommitView, GitGraphView, commit_colors, git_colors
from idgent.themes import THEMES, _contrast
from tests.gitfixtures import GitRepo, build_linear
from tests.test_graphview import poll, web_processes


def stub(api):
    return '''<!doctype html><meta charset="utf-8"><body><script>
window.calls=[];window.API={};
for(const name of ['setData','setCommit','setState','setTheme','setSettings',
'select','focus','pause','resume','provideContext'])
API[name]=(...args)=>calls.push([name,...args]);
window.send=obj=>webkit.messageHandlers.idgent.postMessage(obj);
setTimeout(()=>send({type:'ready'}),120);
</script>'''.replace('API', api)


class PaletteTest(unittest.TestCase):
    def test_graph_palette_and_commit_roles(self):
        from idgent.highlight import KINDS
        for theme in THEMES:
            with self.subTest(theme=theme.id):
                for accent in (theme.ui['accent'], '#5588ee'):
                    colors = git_colors(theme.ui, accent)
                    self.assertEqual(set(colors), {'background', 'surface', 'surface_alt', 'border',
                        'text', 'text_muted', 'accent', 'accent_fg', 'head', 'branch_colors',
                        'mono_font', 'ui_font', 'dark'})
                    self.assertEqual(len(set(colors['branch_colors'])), 8)
                    for color in colors['branch_colors']:
                        self.assertGreaterEqual(_contrast(color, colors['background']), 3)
                        self.assertGreaterEqual(sum((a-b)**2 for a,b in
                            zip(gitviews._rgb(color), gitviews._rgb(accent)))**.5, .22)
                colors = commit_colors(theme.ui, theme.ui['accent'], theme.syntax)
                self.assertEqual(colors['syntax'], {k: v.foreground for k, v in theme.syntax.items()})
                self.assertEqual(set(colors['syntax']), set(KINDS))
                self.assertEqual(colors['add'], theme.ui['git_new'])
                self.assertTrue(colors['add_bg'].startswith('rgba('))
                self.assertGreater(colors['size'], 8)
                self.assertEqual(colors['dark'], theme.dark)

    def test_fonts_and_missing_webkit(self):
        with patch.object(settings, 'get_value', side_effect=lambda key: {'ui_font': 'Test UI', 'editor_font': 'Code Mono'}[key]):
            colors = git_colors({}, '#007acc')
            self.assertIn('Test UI', colors['ui_font'])
            self.assertIn('Code Mono', colors['mono_font'])
        for cls, args in ((GitGraphView, ('.',)), (CommitView, ('.', 'HEAD'))):
            for error in (ImportError('missing'), ValueError('missing')):
                with patch.object(gitviews, '_load_webkit', side_effect=error):
                    view = cls(*args)
                self.assertFalse(view.available)
                self.assertIn('WebKit2', view.get_children()[0].get_text())
                view.set_theme({}, '#ffffff')
                view.post('setTheme', {})
                view.shutdown()
                view.shutdown()
                view.destroy()


class HostTest(unittest.TestCase):
    def setUp(self):
        try:
            gitviews._load_webkit()
        except (ImportError, ValueError):
            self.skipTest('WebKit2 not installed')
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = build_linear(self.root / 'repo', 2)
        self.windows = []
        self.views = []
        self.errors = []

    def tearDown(self):
        for view in self.views:
            view.shutdown()
            view.shutdown()
        for window in self.windows:
            window.destroy()
        self.assertFalse([t for t in threading.enumerate() if t.name in ('idgent-IDGit', 'idgent-IDCommit')])
        self.temp.cleanup()

    def make(self, cls=GitGraphView, root=None, rev='HEAD', show=True):
        args = (str(root or self.repo.path),) + ((rev,) if cls is CommitView else ())
        view = cls(*args, page=stub(cls._api))
        self.views.append(view)
        view.connect('error', lambda _v, message: self.errors.append(message))
        window = Gtk.Window()
        window.set_default_size(700, 400)
        window.add(view)
        self.windows.append(window)
        if show:
            window.show_all()
        self.view = view
        return view

    def js(self, code, view=None):
        view = view or self.view
        results, errors = [], []
        def done(web, result, _data):
            try:
                results.append(json.loads(web.evaluate_javascript_finish(result).to_json(0)))
            except Exception as exc:
                errors.append(exc)
        view.webview.evaluate_javascript(code, -1, None, None, None, done, None)
        self.assertTrue(poll(lambda: bool(results or errors)))
        if errors:
            raise errors[0]
        return results[0]

    def send(self, message, view=None):
        self.js('send(' + json.dumps(message) + ');null', view)

    def ready(self, view=None):
        view = view or self.view
        self.assertTrue(poll(lambda: view._ready and view.last_data is not None))
        self.assertEqual(self.errors, [])

    def count(self, cmd, view=None):
        return self.js('calls.filter(c=>c[0]===' + json.dumps(cmd) + ').length', view)

    def test_handshake_loading_data_signals_and_json_queue(self):
        view = self.make()
        self.assertFalse(view._ready)
        payload = {'quote': "'\\\n雪</script>"}
        view.post('setSettings', payload)
        payload['quote'] = 'mutated'
        events = []
        view.connect('open-commit', lambda _v, rev: events.append(rev))
        self.ready()
        calls = self.js('calls')
        self.assertIn(['setState', 'loading', ''], calls)
        self.assertIn(['setSettings', {'quote': "'\\\n雪</script>"}], calls)
        self.assertEqual(len(view.last_data['commits']), 2)
        self.assertEqual(self.count('setData'), 1)
        self.assertTrue(view.webview.get_context().is_ephemeral())
        self.assertFalse(view.webview.get_settings().get_enable_developer_extras())
        self.assertEqual(view.webview.get_settings().get_hardware_acceleration_policy(),
                         view._webkit.HardwareAccelerationPolicy.NEVER)
        self.send({'type': 'open-commit', 'hash': view.last_data['head']['hash']})
        self.assertEqual(events, [view.last_data['head']['hash']])
        self.send({'type': 'error', 'message': 'test error'})
        self.assertEqual(self.errors, ['test error'])
        with self.assertRaises(ValueError):
            view.post('eval', 'bad')

    def test_error_and_empty_states(self):
        GitRepo(self.root / 'empty')
        for root, state in ((self.root / 'empty', 'empty'), (self.root, 'not-a-repo')):
            view = self.make(root=root)
            self.assertTrue(poll(lambda: view._ready and not view._worker_running))
            self.assertEqual(self.js('calls.filter(c=>c[0]==="setState").at(-1)[1]'), state)
            self.assertEqual(self.count('setData'), 0)
        for error, state in (('git-missing', 'git-missing'), ('timeout', 'error')):
            with patch.object(gitviews.gitlog, 'collect_graph', return_value={'ok': False, 'error': error}):
                view = self.make()
                self.assertTrue(poll(lambda: view._ready and not view._worker_running))
                self.assertEqual(self.js('calls.filter(c=>c[0]==="setState").at(-1)[1]'), state)

    def test_coalesced_refresh_stale_data_and_digest(self):
        view = self.make()
        self.ready()
        original = gitviews.gitlog.collect_graph
        entered, release = threading.Event(), threading.Event()
        calls = []
        active = []
        def collect(root):
            self.assertFalse(active)
            active.append(True)
            calls.append(True)
            if len(calls) == 1:
                entered.set()
                release.wait(3)
                data = dict(original(root), total=999)
            else:
                data = original(root)
            active.pop()
            return data
        with patch.object(gitviews.gitlog, 'collect_graph', side_effect=collect):
            view.refresh()
            self.assertTrue(entered.wait(2))
            for _ in range(10):
                view.refresh()
            release.set()
            self.assertTrue(poll(lambda: len(calls) == 2 and not view._worker_running))
        self.assertEqual(self.count('setData'), 1)
        self.assertNotEqual(view.last_data['total'], 999)
        self.send({'type': 'refresh'})
        self.assertTrue(poll(lambda: not view._worker_running))
        self.assertEqual(self.count('setData'), 1)

    def test_monitor_focus_and_hidden_dirty(self):
        view = self.make()
        self.ready()
        sha = self.repo.commit({'new.txt': 'next\n'}, message='New commit')
        self.assertTrue(poll(lambda: view.last_data['head']['hash'] == sha, 1.5))
        self.assertEqual(self.count('setData'), 2)
        original = gitviews.gitlog.collect_graph
        with patch.object(gitviews.gitlog, 'collect_graph', wraps=original) as collect:
            self.windows[-1].emit('focus-in-event', Gdk.Event.new(Gdk.EventType.FOCUS_CHANGE))
            self.assertTrue(poll(lambda: collect.call_count > 0, 1))
            self.assertTrue(poll(lambda: not view._worker_running))
            self.assertEqual(self.count('setData'), 2)
            self.windows[-1].hide()
            before = collect.call_count
            sha = self.repo.commit({'new.txt': 'hidden\n'}, message='While hidden')
            self.assertTrue(poll(lambda: view._dirty, 1.5))
            self.assertEqual(collect.call_count, before)
            self.assertEqual(self.js('calls.at(-1)[0]'), 'pause')
            self.windows[-1].show_all()
            self.assertTrue(poll(lambda: view.last_data['head']['hash'] == sha))
            self.assertIn(['resume'], self.js('calls'))

    def test_linked_worktree_watches_common_refs(self):
        linked = self.root / 'linked'
        self.repo.git('worktree', 'add', '-b', 'linked', str(linked))
        view = self.make(root=linked)
        self.ready()
        common_heads = (self.repo.path / '.git/refs/heads').resolve()
        self.assertIn((str(common_heads), True), view._monitors)
        self.repo.branch('new-branch')
        self.assertTrue(poll(lambda: any(r['name'] == 'new-branch'
            for c in view.last_data['commits'] for r in c['refs']), 1.5))

    def test_selection_focus_theme_and_page_reload(self):
        view = self.make()
        view.select('abc')
        view.focus_head()
        self.ready()
        with patch.object(motion, 'animations_enabled', return_value=False):
            view.set_theme(THEMES[0].ui, '#ff0088')
        self.assertIn(['setSettings', {'reduceMotion': True}], self.js('calls'))
        self.repo.commit({'a': 'new'}, message='Next')
        self.assertTrue(poll(lambda: len(view.last_data['commits']) == 3))
        calls = self.js('calls')
        self.assertEqual(calls[-2:], [['select', 'abc'], ['focus', 'HEAD']])
        view.webview.reload()
        self.assertTrue(poll(lambda: not view._ready))
        self.assertTrue(poll(lambda: view._ready))
        self.assertEqual(self.count('setData'), 1)
        self.assertIn(['select', 'abc'], self.js('calls'))
        self.assertIn(['focus', 'HEAD'], self.js('calls'))

    def test_commit_detail_context_copy_and_navigation(self):
        view = self.make(CommitView)
        self.ready()
        self.assertEqual(self.count('setCommit'), 1)
        self.assertEqual(view.last_data['hash'], self.repo.git('rev-parse', 'HEAD'))
        self.send({'type': 'expand', 'path': 'main.py', 'start': 1, 'end': 2})
        self.assertTrue(poll(lambda: not view._context_running))
        result = self.js('calls.filter(c=>c[0]==="provideContext").at(-1)')
        self.assertEqual(result[:4], ['provideContext', 'main.py', 1, 2])
        self.assertEqual([l['text'] for l in result[4]], ['def value():', '    return 1'])
        self.send({'type': 'copy', 'text': 'sha 雪'})
        self.assertEqual(Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).wait_for_text(), 'sha 雪')
        opened, commits = [], []
        view.connect('open-file', lambda _v, path, line: opened.append((path, line)))
        view.connect('open-commit', lambda _v, rev: commits.append(rev))
        for path in ('../escape', '/etc/passwd', 'missing/../../escape'):
            self.send({'type': 'open-file', 'path': path, 'line': 3})
        (self.repo.path / 'escape-link').symlink_to(self.root)
        self.send({'type': 'open-file', 'path': 'escape-link/outside', 'line': 3})
        self.assertEqual(opened, [])
        self.send({'type': 'open-file', 'path': 'main.py', 'line': 2})
        self.assertEqual(opened, [(str(self.repo.path / 'main.py'), 2)])
        self.send({'type': 'open-commit', 'hash': 'parent'})
        self.assertEqual(commits, ['parent'])
        view.reload()
        self.assertTrue(poll(lambda: not view._worker_running))
        self.assertEqual(self.count('setCommit'), 2)

    def test_commit_not_found_error_and_shared_context_survives_close(self):
        before = web_processes()
        views = [self.make(CommitView) for _ in range(3)]
        for view in views:
            self.ready(view)
        self.assertTrue(all(v._context is views[0]._context for v in views))
        self.assertEqual(len(web_processes() - before), 1)
        views[0].shutdown()
        self.assertEqual(self.js('1+1', views[1]), 2)
        view = self.make(CommitView, rev='unknown-revision')
        self.assertTrue(poll(lambda: view._ready and not view._worker_running))
        self.assertEqual(self.js('calls.filter(c=>c[0]==="setState").at(-1)[1]'), 'not-found')
        with patch.object(gitviews.gitdiff, 'commit_detail', return_value={'ok': False, 'error': 'timeout'}):
            view.reload()
            self.assertTrue(poll(lambda: not view._worker_running))
        self.assertEqual(self.js('calls.filter(c=>c[0]==="setState").at(-1)[1]'), 'error')

    def test_destroy_joins_worker_and_late_callbacks_are_harmless(self):
        started, release = threading.Event(), threading.Event()
        original = gitviews.gitdiff.commit_detail
        def slow(*args):
            started.set()
            release.wait(2)
            return original(*args)
        with patch.object(gitviews.gitdiff, 'commit_detail', side_effect=slow):
            view = self.make(CommitView)
            self.assertTrue(started.wait(2))
            timer = threading.Timer(.05, release.set)
            timer.start()
            view.destroy()
            timer.join()
        self.assertTrue(view._closed)
        self.assertEqual(view._threads, [])
        view._javascript_done(None, None, None)
        view._message_received(None, None)
        view._page_message({'type': 'copy', 'text': 'late'})
        view.reload()
        view.shutdown()
        self.assertEqual(view._sources, set())

    def test_graph_shutdown_cancels_monitors_focus_and_debounce(self):
        view = self.make()
        self.ready()
        monitors = list(view._monitors.values())
        self.assertTrue(monitors)
        self.assertIsNotNone(view._focus_window)
        view._repo_changed()
        self.assertIsNotNone(view._debounce)
        view.refresh()
        view.shutdown()
        self.assertTrue(all(m.is_cancelled() for m in monitors))
        self.assertEqual(view._monitors, {})
        self.assertIsNone(view._focus_window)
        self.assertIsNone(view._debounce)
        self.assertEqual(view._sources, set())
        view._repo_changed()
        view._message_received(None, None)
        view._javascript_done(None, None, None)
        self.windows[-1].destroy()

    def test_context_results_from_before_reload_are_discarded(self):
        view = self.make(CommitView)
        self.ready()
        entered, release = threading.Event(), threading.Event()
        original = gitviews.gitdiff.file_context
        def slow(*args):
            entered.set()
            release.wait(2)
            return original(*args)
        with patch.object(gitviews.gitdiff, 'file_context', side_effect=slow):
            self.send({'type': 'expand', 'path': 'main.py', 'start': 1, 'end': 2})
            self.assertTrue(entered.wait(2))
            view.reload()
            release.set()
            self.assertTrue(poll(lambda: not view._context_running and not view._worker_running))
        self.assertEqual(self.count('provideContext'), 0)
        self.assertEqual(self.count('setCommit'), 2)

    def test_commit_lifecycle_hooks_are_optional_in_contract_c(self):
        view = self.make(CommitView)
        self.ready()
        self.js('delete IDCommit.pause; delete IDCommit.resume; null')
        self.windows[-1].hide()
        self.windows[-1].show_all()
        self.assertEqual(self.js('1+1'), 2)
        self.assertEqual(self.errors, [])

    def test_set_root_clears_selection_monitors_and_data(self):
        view = self.make()
        self.ready(view)
        old_monitors = list(view._monitors.values())
        view.select(self.repo.git('rev-parse', 'HEAD'))
        other = build_linear(self.root / 'other', 4)
        view.set_root(str(other.path))
        self.assertIsNone(view.last_data)
        self.assertIsNone(view._selection)
        self.assertTrue(all(m.is_cancelled() for m in old_monitors))
        self.assertTrue(poll(lambda: view.last_data is not None and len(view.last_data['commits']) == 4))
        self.assertEqual(view.last_data['repo']['root'], str(other.path))
        self.assertEqual(view.root, str(other.path))
        plain = self.root / 'plain'
        plain.mkdir()
        view.set_root(str(plain))
        self.assertTrue(poll(lambda: view._state[0] == 'not-a-repo'))
        self.assertEqual(view._state[1], 'Open a folder containing a Git repository.')
        self.assertIsNone(view.last_data)
        self.assertEqual(self.errors, [])

    def test_set_root_discards_collection_from_previous_repo(self):
        view = self.make()
        self.ready(view)
        other = build_linear(self.root / 'other', 4)
        original = gitviews.gitlog.collect_graph
        entered, release = threading.Event(), threading.Event()
        def collect(root):
            if root == str(self.repo.path):
                entered.set()
                release.wait(3)
            return original(root)
        with patch.object(gitviews.gitlog, 'collect_graph', side_effect=collect):
            view.refresh()
            self.assertTrue(entered.wait(2))
            view.set_root(str(other.path))
            release.set()
            self.assertTrue(poll(lambda: view.last_data is not None and not view._worker_running))
        self.assertEqual(view.last_data['repo']['root'], str(other.path))
        self.assertEqual(len(view.last_data['commits']), 4)
        self.assertEqual(self.js("calls.filter(c=>c[0]==='setData').at(-1)[1].repo.root"), str(other.path))
        self.assertEqual(self.errors, [])

    def test_commit_font_size_uses_live_editor_size(self):
        view = self.make(CommitView)
        self.ready(view)
        view.set_font_size(23)
        self.assertEqual(view._theme['size'], 23)
        self.assertEqual(self.js("calls.filter(c=>c[0]==='setTheme').at(-1)[1].size", view), 23)
