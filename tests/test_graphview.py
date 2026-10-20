"""Real WebKit bridge/lifecycle checks, plus pure canvas-palette tests."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, GLib, Gtk

from idgent import graphview, settings
from idgent.graphdata import GraphIndex
from idgent.graphview import GraphView, graph_colors
from idgent.terminal import TerminalPanel
from idgent.themes import THEMES, _contrast


FAKE_PAGE = '''<!doctype html><meta charset="utf-8"><body><script>
window.calls=[];
window.IDGraph={};
for (const name of ['setData','setTheme','setSettings','setActive','focus','pause','resume','setLocal','setGraphType']) {
 IDGraph[name] = (...args) => { calls.push([name,...args]); };
}
window.send = (obj) => webkit.messageHandlers.idgent.postMessage(obj);
setTimeout(()=>send({type:'ready'}), 60);
</script>'''


def poll(predicate, timeout=6):
    deadline = time.monotonic() + timeout
    context = GLib.MainContext.default()
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        if predicate():
            return True
        time.sleep(.01)
    return bool(predicate())


def web_processes():
    rows = subprocess.check_output(['ps', '-eo', 'pid,ppid,comm'], text=True).splitlines()[1:]
    rows = [line.split(None, 2) for line in rows]
    descendants = {os.getpid()}
    while True:
        expanded = descendants | {int(pid) for pid, parent, _name in rows if int(parent) in descendants}
        if expanded == descendants:
            break
        descendants = expanded
    return {int(pid) for pid, _parent, name in rows
            if int(pid) in descendants and name.startswith('WebKitWebProces')}


class PaletteAndFallbackTest(unittest.TestCase):
    def test_canvas_roles_and_contrast_for_all_themes(self):
        for theme in THEMES:
            with self.subTest(theme=theme.id):
                colors = graph_colors(theme.ui, '#ff0088')
                self.assertEqual(set(colors), {'background', 'node', 'node_focused', 'node_tag',
                    'node_attachment', 'node_unresolved', 'node_code', 'line', 'line_highlight',
                    'text', 'text_muted', 'groups'})
                self.assertEqual(colors['node_focused'], '#ff0088')
                self.assertGreaterEqual(_contrast(colors['node'], colors['background']), 3)
                self.assertEqual(len(colors['groups']), 8)
                for color in colors['groups']:
                    self.assertGreaterEqual(_contrast(color, colors['background']), 3)

    def test_lazy_import_fallback_and_noop_api(self):
        for error in (ImportError('missing'), ValueError('typelib missing')):
            with patch.object(graphview, '_load_webkit', side_effect=error):
                view = GraphView('.')
            self.assertFalse(view.available)
            labels = view.get_children()[0].get_children()
            self.assertIn('sudo apt install gir1.2-webkit2-4.1', labels[1].get_text())
            view.post('setData', {})
            view.set_theme({}, '#ffffff')
            view.set_active_file('a.md')
            view.refresh_file('a.md')
            view.set_text('a.md', 'hi')
            view.show_local('a.md', 2)
            view.rebuild()
            view.shutdown()
            view.destroy()


class GraphViewTest(unittest.TestCase):
    def setUp(self):
        try:
            graphview._load_webkit()
        except (ImportError, ValueError):
            self.skipTest('WebKit2 4.1 is unavailable')
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self.config = tempfile.TemporaryDirectory()
        self.addCleanup(self.config.cleanup)
        environment = patch.dict(os.environ, XDG_CONFIG_HOME=self.config.name)
        environment.start()
        self.addCleanup(environment.stop)
        self.window = Gtk.Window()
        self.window.set_default_size(800, 400)
        self.addCleanup(self.window.destroy)
        self.errors = []

    def test_stored_default_type_and_dialog_options(self):
        preferences = graphview.preferences
        preferences.set_value('graph_default_type', 'note')
        view = self.make_view()
        self.ready()
        self.assertEqual(view.graph_type, 'note')
        view.set_graph_type('local')
        preferences.set_value('graph_default_type', 'global')
        self.assertEqual(view.graph_type, 'local')
        graph = preferences.normalize_graph({'showTags': False, 'search': 'example'})
        view.set_settings(graph)
        self.assertEqual(view._settings, graph)
        self.assertIsNotNone(view._options_timer)
        self.assertTrue(poll(lambda: view._options_timer is None))
        view.shutdown()
        view.set_settings(graph)
        self.assertIsNone(view._options_timer)

    def make_view(self, **kwargs):
        self.view = GraphView(self.root.name, **{'page': FAKE_PAGE, **kwargs})
        self.view.connect('error', lambda _v, message: self.errors.append(message))
        self.window.add(self.view)
        self.window.show_all()
        return self.view

    def ready(self):
        self.assertTrue(poll(lambda: self.view._ready and self.view.last_data is not None))
        self.assertEqual(self.errors, [])

    def js(self, code):
        results, failures = [], []
        def done(view, result, _data):
            try:
                value = view.evaluate_javascript_finish(result)
                results.append(json.loads(value.to_json(0)))
            except Exception as exc:
                failures.append(exc)
        self.view.webview.evaluate_javascript(code, -1, None, None, None, done, None)
        self.assertTrue(poll(lambda: bool(results or failures)))
        if failures:
            raise failures[0]
        return results[0]

    def send(self, message):
        self.js('send(' + json.dumps(message) + '); null')

    def test_ready_queue_json_roundtrip_and_all_commands(self):
        view = self.make_view()
        events = []
        view.connect('ready', lambda _v: events.append(True))
        payload = {'nodes': [{'id': "quote'\\\n雪", 'label': '</script>'}], 'links': [],
                   'truncated': True, 'stats': {'n': 7}}
        view.post('setData', payload)
        view.post('focus', 'a.md')
        view.set_active_file(Path(self.root.name) / 'active.md')
        payload['stats']['n'] = 99
        self.ready()
        calls = self.js('calls')
        graphs = [c[1] for c in calls if c[0] == 'setData']
        self.assertTrue(any(g['stats'].get('n') == 7 for g in graphs))
        self.assertIn(['focus', 'a.md'], calls)
        self.assertIn(['setActive', 'active.md'], calls)
        self.assertEqual(events, [True])
        self.assertEqual(self.js('calls[0][0]'), 'setTheme')
        self.assertTrue(view.webview.get_context().is_ephemeral())
        self.assertFalse(view.webview.get_settings().get_enable_developer_extras())
        self.assertFalse(view.webview.get_settings().get_javascript_can_open_windows_automatically())
        self.assertEqual(view.webview.get_settings().get_hardware_acceleration_policy(),
                         view._webkit.HardwareAccelerationPolicy.NEVER)
        with self.assertRaises(ValueError):
            view.post('alert(1)')
        self.send({'type': 'ready'})
        self.assertEqual(events, [True])

    def test_open_hover_error_and_malformed_messages(self):
        view = self.make_view()
        self.ready()
        opened = []
        view.connect('open-file', lambda _v, path, line: opened.append((path, line)))
        self.send({'type': 'open', 'id': 'a.md', 'path': 'a.md', 'line': 42, 'newTab': True})
        self.send({'type': 'open', 'path': 'a.md', 'line': 'bad'})
        self.send({'type': 'open', 'path': '../escape.md'})
        self.assertEqual(opened, [(os.path.join(self.root.name, 'a.md'), 42),
                                  (os.path.join(self.root.name, 'a.md'), 1)])
        self.send({'type': 'hover', 'id': 'a.md', 'label': 'Alpha', 'path': 'a.md'})
        self.assertEqual(view.get_tooltip_text(), 'Alpha')
        self.send({'type': 'error', 'message': 'broken graph'})
        self.assertEqual(self.errors, ['broken graph'])
        for value in (None, 'junk', [], {'type': 'settings', 'settings': []}, {'type': 'open'}):
            self.send(value)
        self.assertEqual(len(opened), 2)

    def test_settings_options_are_persisted_and_rebuild_debounced(self):
        Path(self.root.name, 'alpha.md').write_text('# alpha')
        Path(self.root.name, 'beta.md').write_text('# beta')
        view = self.make_view()
        self.ready()
        changes = []
        view.connect('settings-changed', lambda _v, value: changes.append(value))
        with patch.object(view, 'rebuild', wraps=view.rebuild) as rebuild:
            self.send({'type': 'settings', 'settings': {'repelStrength': 15}})
            self.assertEqual(settings.get_graph()['repelStrength'], 15)
            self.assertEqual(rebuild.call_count, 0)
            # One JS task ensures all messages arrive inside the debounce period.
            self.js("send({type:'options',options:{search:'beta'}});"
                    "send({type:'options',options:{search:'alpha'}}); null")
            self.assertTrue(poll(lambda: len(view.last_data['nodes']) == 1))
            self.assertEqual(rebuild.call_count, 1)
            self.assertEqual(view.last_data['nodes'][0]['id'], 'alpha.md')
        self.assertEqual(settings.get_graph()['search'], 'alpha')
        self.assertEqual(len(changes), 3)

    def test_incremental_updates_text_overrides_and_local_graph(self):
        for path, text in {'a.md': '[[b]]', 'b.md': '[[c]]', 'c.md': '', 'orphan.md': ''}.items():
            Path(self.root.name, path).write_text(text)
        view = self.make_view()
        self.ready()
        self.assertEqual(len(view.last_data['nodes']), 4)
        view.show_local('a.md', 1)
        self.assertTrue(poll(lambda: len(view.last_data['nodes']) == 2))
        self.assertEqual(view.last_data['stats']['focus'], 'a.md')
        self.assertIn(['setLocal', {'path': 'a.md', 'depth': 1, 'incoming': True, 'outgoing': True}],
                      self.js('calls'))
        view.show_local('a.md', 2)
        self.assertTrue(poll(lambda: len(view.last_data['nodes']) == 3))
        view.set_text('a.md', '[[orphan]]')
        self.assertTrue(poll(lambda: {n['id'] for n in view.last_data['nodes']} == {'a.md', 'orphan.md'}))
        Path(self.root.name, 'orphan.md').write_text('[[c]]')
        with patch.object(view._index, 'update_path', wraps=view._index.update_path) as update:
            view.refresh_file('orphan.md')
            self.assertTrue(poll(lambda: len(view.last_data['nodes']) == 3))
            update.assert_called_once_with('orphan.md')
        view.set_active_file('b.md')
        self.assertTrue(poll(lambda: view.last_data['stats']['focus'] == 'b.md'))
        view.show_local(None, 1)
        self.assertTrue(poll(lambda: len(view.last_data['nodes']) == 4))

    def test_graph_types_select_the_right_nodes(self):
        for path, text in {'a.md': '[[b]]', 'b.md': '', 'orphan.md': '',
                           '.IDgent/Notes/n1.md': '[[n2]] [[a]]',
                           '.IDgent/Notes/n2.md': ''}.items():
            Path(self.root.name, path).parent.mkdir(parents=True, exist_ok=True)
            Path(self.root.name, path).write_text(text)
        view = self.make_view()
        self.ready()
        ids = lambda: {n['id'] for n in view.last_data['nodes']}
        self.assertEqual(view.graph_type, 'global')
        self.assertEqual(len(ids()), 5)
        # Local without an active file: empty graph and a message.
        view.set_graph_type('local')
        self.assertTrue(poll(lambda: view.last_data['nodes'] == []))
        self.assertIn(['setGraphType', {'type': 'local', 'message': 'Open a file to see its local graph'}],
                      self.js('calls'))
        view.set_active_file(Path(self.root.name, 'a.md'))
        view.show_local(Path(self.root.name, 'a.md'), 1)
        self.assertTrue(poll(lambda: ids() == {'a.md', 'b.md', '.IDgent/Notes/n1.md'}))
        view.set_active_file(Path(self.root.name, 'orphan.md'))
        self.assertTrue(poll(lambda: ids() == {'orphan.md'}))
        view.set_graph_type('note')
        self.assertTrue(poll(lambda: ids() == {'.IDgent/Notes/n1.md', '.IDgent/Notes/n2.md'}))
        self.assertEqual(len(view.last_data['links']), 1)
        view.set_active_file(Path(self.root.name, 'a.md'))
        self.assertTrue(poll(lambda: view.graph_type == 'note'))
        view.set_graph_type('bogus')
        self.assertEqual(view.graph_type, 'note')
        self.send({'type': 'graphType', 'value': 'global'})
        self.assertTrue(poll(lambda: len(ids()) == 5))
        self.assertEqual(view.graph_type, 'global')

    def test_note_graph_without_notes_folder_is_empty(self):
        Path(self.root.name, 'a.md').write_text('')
        view = self.make_view()
        self.ready()
        view.set_graph_type('note')
        self.assertTrue(poll(lambda: view.last_data['nodes'] == []))
        self.assertTrue(poll(lambda: any(c[0] == 'setGraphType' and c[1]['type'] == 'note'
                                         and 'No notes' in c[1]['message'] for c in self.js('calls'))))

    def test_local_options_persist_depth_and_direction_and_cancel_timer(self):
        for path, text in {'a.md': '[[b]]', 'b.md': '[[c]]', 'c.md': ''}.items():
            Path(self.root.name, path).write_text(text)
        view = self.make_view()
        self.ready()
        view.set_active_file('a.md')
        self.send({'type': 'options', 'options': {'local': {
            'path': 'a.md', 'depth': 2, 'incoming': False, 'outgoing': True}}})
        self.assertTrue(poll(lambda: view.last_data['stats'].get('focus') == 'a.md'))
        self.assertEqual(len(view.last_data['nodes']), 3)
        self.assertEqual(settings.get_graph()['localDepth'], 2)
        self.assertFalse(settings.get_graph()['localIncoming'])
        self.send({'type': 'options', 'options': {'localOutgoing': False}})
        self.assertTrue(poll(lambda: len(view.last_data['nodes']) == 1))
        self.send({'type': 'options', 'options': {'search': 'c'}})
        self.assertIsNotNone(view._options_timer)
        view.shutdown()
        self.assertIsNone(view._options_timer)
        view.post('setData', {})
        view.rebuild()
        view.shutdown()

    def test_pause_resume_after_unmap_and_collapse(self):
        view = self.make_view()
        self.ready()
        view.hide()
        self.assertTrue(poll(lambda: not view.get_mapped()))
        self.assertEqual(self.js('calls[calls.length-1]'), ['pause'])
        view.show()
        self.assertTrue(poll(view.get_mapped))
        self.assertEqual(self.js('calls[calls.length-1]'), ['resume'])
        self.window.hide()
        self.assertEqual(self.js('calls[calls.length-1]'), ['pause'])

    def test_worker_coalesces_and_result_after_destroy_is_ignored(self):
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        main_thread = threading.get_ident()
        index = GraphIndex(self.root.name)
        scan = index.scan
        thread_ids = []
        def slow_scan():
            thread_ids.append(threading.get_ident())
            started.set()
            release.wait(5)
            scan()
            finished.set()
        self.addCleanup(release.set)
        with patch.object(index, 'scan', slow_scan):
            view = self.make_view(index=index)
            self.assertTrue(started.wait(1))
            for _ in range(10):
                view.rebuild()
            self.assertEqual(len(thread_ids), 1)
            self.assertNotEqual(thread_ids[0], main_thread)
            view.destroy()
            release.set()
            self.assertTrue(finished.wait(2))
            self.assertFalse(poll(lambda: bool(view._sources), .1))
            self.assertIsNone(view.last_data)
            self.assertEqual(view._queue, [])
            self.assertIsNone(view.webview)

    def test_latest_worker_request_wins_and_truncation_survives(self):
        for n in range(8):
            Path(self.root.name, f'{n}.md').write_text('')
        index = GraphIndex(self.root.name, max_nodes=2)
        view = self.make_view(index=index)
        for _ in range(20):
            view.rebuild()
        self.ready()
        self.assertTrue(view.last_data['truncated'])
        self.assertEqual(len(view.last_data['nodes']), 2)
        self.assertFalse(view._worker_running)
        self.assertFalse(view._dirty)
        self.assertEqual(self.js('calls.filter(c=>c[0]==="setData").length'), 1)

    def test_window_accelerator_with_webview_focused(self):
        view = self.make_view()
        self.ready()
        activated = []
        group = Gtk.AccelGroup()
        group.connect(Gdk.KEY_p, Gdk.ModifierType.CONTROL_MASK, Gtk.AccelFlags.VISIBLE,
                      lambda *_args: activated.append(True) or True)
        self.window.add_accel_group(group)
        view.grab_focus()
        self.assertTrue(poll(lambda: self.window.get_focus() == view.webview))
        event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        event.window = view.webview.get_window()
        event.send_event = True
        event.time = Gdk.CURRENT_TIME
        event.state = Gdk.ModifierType.CONTROL_MASK
        event.keyval = Gdk.KEY_p
        event.hardware_keycode = Gdk.Keymap.get_for_display(Gdk.Display.get_default()).get_entries_for_keyval(Gdk.KEY_p)[1][0].keycode
        event.set_device(Gdk.Display.get_default().get_default_seat().get_keyboard())
        Gtk.main_do_event(event)
        self.assertTrue(poll(lambda: bool(activated)), 'Ctrl+P did not reach the window accelerator')

    def test_closing_graph_pane_releases_web_process(self):
        baseline = len(web_processes())
        panel = TerminalPanel(self.root.name, autostart=False)
        panel.ANIMATION_MS = 0
        self.window.add(panel)
        self.view = GraphView(self.root.name, page=FAKE_PAGE)
        panel.add_graph(self.view)
        self.window.show_all()
        self.ready()
        self.assertTrue(poll(lambda: len(web_processes()) > baseline))
        panel.close_current()
        self.assertEqual(panel.graph_count(), 0)
        self.assertTrue(poll(lambda: len(web_processes()) <= baseline, 6),
                        f'WebKitWebProcess survived: {web_processes()}')

    def test_placeholder_default_page_and_navigation_guard(self):
        view = self.make_view(page=None)
        self.ready()
        if not (Path(graphview.__file__).parent / 'graph_web' / 'graph.html').exists():
            self.assertEqual(self.js("document.getElementById('stats').textContent"), '0 nodes / 0 links')
        original_uri = view.webview.get_uri()
        view.webview.load_uri('https://example.invalid/forbidden')
        poll(lambda: False, .15)
        self.assertEqual(view.webview.get_uri(), original_uri)
