"""Window → worker → real WebKit page → editor integration (run under xvfb).

IDGENT_GRAPH_QA=1 also records window screenshots and software-rendering metrics
in the ignored .ceo directory. The demo vault is always copied before use.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
import threading
import time
import tempfile
import unittest
from unittest.mock import patch

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, Gio, GLib, Gtk

from idgent import fsops, graphview, settings
from idgent.graphdata import GraphIndex, GraphOptions
from idgent.graphview import graph_colors
from idgent.themes import THEMES
from idgent.window import MainWindow
from tests.test_graph_web import Page
from tests.test_graphview import poll, web_processes

DEMO = Path('/home/lestoq/projects/demo')
ARTIFACTS = Path(__file__).resolve().parents[1] / '.ceo'


class GraphDataParityTests(unittest.TestCase):
    def test_creation_time_files_and_synthetic_nodes(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, 'A.md')
            path.write_text('[[Missing]] ![[pic.png]] #topic')
            Path(root, 'pic.png').write_bytes(b'\0PNG')
            os.utime(path, (1000000000, 1000000000))
            index = GraphIndex(root)
            index.scan()
            nodes = {n['id']: n for n in index.build(GraphOptions(existing_only=False)).to_json()['nodes']}
            info = path.stat()
            self.assertEqual(nodes['A.md']['ctime'],
                             getattr(info, 'st_birthtime', min(info.st_ctime, info.st_mtime)))
            self.assertIsInstance(nodes['pic.png']['ctime'], float)
            self.assertIsNone(nodes['#topic']['ctime'])
            self.assertIsNone(nodes['?Missing']['ctime'])
            # Birth time wins on platforms which expose it.
            from types import SimpleNamespace
            original = os.stat
            def birth_stat(name, *args, **kwargs):
                stat = original(name, *args, **kwargs)
                if os.fspath(name) == str(path):
                    return SimpleNamespace(st_birthtime=42.0, st_ctime=100, st_mtime=99)
                return stat
            with patch('idgent.graphdata.os.stat', side_effect=birth_stat):
                # The override avoids isfile/getsize needing the mocked stat's other fields.
                index.set_text(path, path.read_text() + '\nchanged')
            self.assertEqual(next(n for n in index.build().nodes if n.id == 'A.md').ctime, 42)

    def test_ui_filters_remove_neighbors_and_first_nonempty_file_group_wins(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, 'Alpha.md').write_text('[[Beta]] [[Missing]] ![[pic.png]] #alpha #other')
            Path(root, 'Beta.md').write_text('beta')
            Path(root, 'pic.png').write_bytes(b'\0')
            index = GraphIndex(root)
            index.scan()
            options = GraphOptions(query='alpha', existing_only=False, reference_app_filters=True,
                                   groups=[{'query': '', 'color': '#ffffff'},
                                           {'query': 'alpha', 'color': '#ff0000'},
                                           {'query': 'file:Alpha', 'color': '#00ff00'}])
            graph = index.build(options)
            nodes = {n.id: n for n in graph.nodes}
            self.assertEqual(set(nodes), {'Alpha.md', '#alpha', '?Missing'})
            self.assertEqual(nodes['Alpha.md'].group, 1)
            self.assertIsNone(nodes['#alpha'].group)
            self.assertIsNone(nodes['?Missing'].group)
            self.assertTrue(all(graph.nodes[e['s']].id in nodes for e in graph.to_json()['links']))
            # The local centre always passes the search filter (spec B.2).
            local = index.local('Beta.md', options=options)
            self.assertIn('Beta.md', {n.id for n in local.nodes})


class WindowGraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
            raise unittest.SkipTest('a display is required; run with xvfb-run')
        cls.app = Gtk.Application.get_default() or Gtk.Application(
            application_id='dev.idgent.GraphWiringTests', flags=Gio.ApplicationFlags.NON_UNIQUE)
        if not cls.app.get_is_registered():
            cls.app.register(None)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='idgent-graph-wiring-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name, 'vault')
        self.root.mkdir()
        env = patch.dict(os.environ, XDG_CONFIG_HOME=str(Path(self.temp.name, 'config')))
        env.start()
        self.addCleanup(env.stop)
        settings.set_value('terminal_autostart', False)
        settings.set_graph({'close': True, 'scale': .5,
                            'colorGroups': [{'query': 'file:Orbit', 'color': {'a': 1, 'rgb': 16711680}}]})
        self.window = MainWindow(self.app, str(self.root))
        # GTK client-side titlebar/shadows add 10 x 50 to the content size.
        self.window.set_default_size(1270, 750)
        self.window.show_all()
        self.addCleanup(self.destroy_window)
        self.page = None

    def destroy_window(self):
        self.window.destroy()
        poll(lambda: not web_processes(), timeout=6)

    def test_graph_focus_shortcuts_and_hotkeys_without_webkit(self):
        settings.set_graph({'localJumps': 3, 'localBacklinks': False, 'localForelinks': False})
        with patch('idgent.graphview._load_webkit', side_effect=ValueError('missing')):
            view = self.window.open_graph()
        self.assertEqual(view._settings['localDepth'], 3)
        self.assertFalse(view._settings['localIncoming'])
        self.assertFalse(view._settings['localOutgoing'])
        child = Gtk.Entry()
        view.pack_start(child, False, False, 0)
        child.show()
        child.grab_focus()
        self.assertFalse(self.window._terminal_focused())
        for key, state, method in (
            (Gdk.KEY_p, Gdk.ModifierType.CONTROL_MASK, 'open_quick_switcher'),
            (Gdk.KEY_o, Gdk.ModifierType.CONTROL_MASK, 'open_quick_switcher'),
            (Gdk.KEY_P, Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK, 'open_command_palette'),
            (Gdk.KEY_comma, Gdk.ModifierType.CONTROL_MASK, 'open_settings'),
        ):
            event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
            event.keyval, event.state = key, state
            with patch.object(self.window, method) as callback:
                self.assertTrue(self.window.do_key_press_event(event))
                callback.assert_called_once()
        keys = [keys for _, keys in self.window.hotkeys()]
        self.assertEqual(keys.count('Ctrl+Alt+G'), 1)
        self.assertEqual(keys.count('Ctrl+Shift+Alt+G'), 1)
        labels = [c.label for c in self.window.commands()]
        for label in ('Open graph view', 'Open local graph', 'Rebuild graph', 'Reset camera'):
            self.assertIn('Graph view: ' + label, labels)
        self.window.toggle_terminal_right()
        self.window.toggle_terminal_right()
        self.assertEqual(self.window.terminal_right.pane_count(), 1)
        self.assertEqual(self.window.terminal_right.terminal_count(), 0)

    def test_closing_terminal_retains_shortcut_guard(self):
        panel = self.window.terminal_bottom
        self.window._show_terminal(panel)
        terminal = panel._panes[0].terminal
        terminal.grab_focus()
        self.assertIs(self.window.get_focus(), terminal)
        # Closing panes leave _panes before their fade and focus transfer finish.
        with patch.object(panel, '_panes', []):
            self.assertTrue(self.window._terminal_focused())

    def make_page(self, view):
        page = object.__new__(Page)
        page.Gtk, page.GLib = Gtk, GLib
        page.view, page.win = view.webview, self.window
        return page

    def menu(self, label):
        # Walk the real View menu.
        def find(widget):
            if isinstance(widget, Gtk.MenuItem) and widget.get_label() == label:
                return widget
            children = widget.get_children() if isinstance(widget, Gtk.Container) else []
            if isinstance(widget, Gtk.MenuItem) and widget.get_submenu():
                children += [widget.get_submenu()]
            for child in children:
                result = find(child)
                if result is not None:
                    return result
        result = find(self.window.titlebar)
        self.assertIsNotNone(result, label)
        result.activate()

    def test_demo_vault_end_to_end(self):
        try:
            graphview._load_webkit()
        except (ImportError, ValueError) as exc:
            self.skipTest('WebKit2 4.1 is unavailable: ' + str(exc))
        if not DEMO.is_dir():
            self.skipTest('demo vault is unavailable: ' + str(DEMO))
        shutil.copytree(DEMO, self.root, dirs_exist_ok=True)
        self.window.explorer.refresh()
        baseline = web_processes()
        scans, calls = [], []
        original_scan, original_eval = GraphIndex.scan, graphview.GraphView._evaluate
        def scan(index):
            scans.append(threading.get_ident())
            return original_scan(index)
        def evaluate(view, script):
            calls.append((view._ready, script))
            return original_eval(view, script)
        panel = self.window.terminal_right
        panel.set_no_show_all(False)
        panel.show_all()
        start = time.monotonic()
        with patch.object(GraphIndex, 'scan', scan), patch.object(graphview.GraphView, '_evaluate', evaluate):
            panel.add_button.clicked()
            panel.add_button.choices.emit('row-activated', panel.add_button.choices.get_row_at_index(1))
            view = panel.graph_views()[0]
            self.assertTrue(poll(lambda: view._ready and view.last_data is not None, timeout=15))
            page = self.page = self.make_page(view)
            page.wait_for('IDGraph.stats().nodes >= 200 && IDGraph._debug.nodeAlpha("index/Space Index.md") > 0', 15)
        first_frame = time.monotonic() - start
        self.assertTrue(scans)
        self.assertTrue(all(ident != threading.get_ident() for ident in scans))
        self.assertTrue(all(ready for ready, _ in calls))
        self.assertTrue(any('setData(' in script for _, script in calls))
        self.assertEqual(panel.pane_count(), 1)
        self.assertEqual(panel.terminal_count(), 0)
        self.assertEqual(page.js('IDGraph._debug.settings().colorGroups[0].color'), {'a': 1, 'rgb': 16711680})
        self.assertTrue(page.js('IDGraph._debug.settings().close'))
        page.wait_for('IDGraph._debug.nodeColor("concepts/Orbit.md") === "#ff0000"')
        kinds = {n['kind'] for n in view.last_data['nodes']}
        self.assertEqual(kinds, {'note', 'code', 'unresolved'})
        page.wait_for('Math.abs(IDGraph.stats().scale - .5) < .01')
        expected = graph_colors(self.window.theme.ui, self.window._values['accent'] or self.window.theme.ui['accent'])
        self.assertEqual(page.js('document.documentElement.style.getPropertyValue("--bg")').lower(), expected['background'])
        light = next(t for t in THEMES if not t.dark)
        self.window.set_theme(light.id, persist=False)
        page.wait_for('document.documentElement.style.getPropertyValue("--bg") === ' + json.dumps(light.ui['bg']))
        self.assertEqual(page.js('document.documentElement.style.colorScheme'), 'light')
        accent = '#e5484d'
        self.change_setting('accent', accent)
        page.wait_for('document.documentElement.style.getPropertyValue("--accent") === ' + json.dumps(accent))
        note = 'index/Space Index.md'
        self.window.editor.open_file(str(self.root / note))
        page.wait_for('IDGraph._debug.nodeColor(' + json.dumps(note) + ') === ' + json.dumps(accent))
        self.change_setting('reduce_motion', True)
        page.wait_for('IDGraph._debug.settings().reduceMotion')
        self.change_setting('reduce_motion', False)
        self.window.set_theme('dark-plus', persist=False)
        self.change_setting('accent', None)
        page.pump(.4)
        # Freeze positions while dispatching the page's actual node click handlers.
        view.post('pause')
        page.wait_for('IDGraph.stats().paused')
        click_id = 'planets/Mars.md'
        page.js('''(() => {
          const c=document.getElementById('graph'), r=c.getBoundingClientRect();
          const p=IDGraph._debug.screenPos(%s);
          for (const type of ['pointerdown','pointerup']) c.dispatchEvent(new PointerEvent(type,
            {clientX:r.left+p[0],clientY:r.top+p[1],pointerId:1,button:0,bubbles:true}));
          return null;
        })()''' % json.dumps(click_id))
        self.assertTrue(poll(lambda: self.window.editor.current_path() == str(self.root / click_id)))
        view.post('resume')
        page.wait_for('IDGraph._debug.nodeColor(' + json.dumps(click_id) + ') === ' + json.dumps(self.window.theme.ui['accent']))
        # Real buffer change → 400 ms window debounce → worker → setData.
        tab = self.window.editor._current()
        with patch.object(view, 'set_text', wraps=view.set_text) as set_text:
            tab.buffer.insert(tab.buffer.get_end_iter(), '\n[[Integration Live Target]]\n')
            self.assertEqual(set_text.call_count, 0)
            page.wait_for('IDGraph._debug.ids().includes("?Integration Live Target")')
            self.assertEqual(set_text.call_count, 1)
        self.assertTrue(self.window.editor.save_current())
        self.assertIn('[[Integration Live Target]]', Path(tab.path).read_text())
        # Create through fsops + Explorer refresh, as the create flow does.
        created = fsops.create_file(str(self.root), 'Integration Live Target.md')
        self.window.explorer.refresh()
        page.wait_for('IDGraph._debug.ids().includes("Integration Live Target.md") && !IDGraph._debug.ids().includes("?Integration Live Target")')
        renamed = self.window.explorer._rename(created, 'Integration Renamed.md')
        page.wait_for('IDGraph._debug.ids().includes("Integration Renamed.md") && !IDGraph._debug.ids().includes("Integration Live Target.md")')
        self.window.explorer._delete(renamed, permanent=True)
        page.wait_for('!IDGraph._debug.ids().includes("Integration Renamed.md")')
        # Real page options use query/groups/local.enabled, not settings key names.
        self.send({'type': 'options', 'options': {'query': 'file:Orbit', 'groups': [
            {'query': 'file:Orbit', 'color': '#ff0000'}], 'local': {'enabled': False}}})
        page.wait_for('IDGraph.stats().nodes < 30')
        self.assertEqual(view._settings['search'], 'file:Orbit')
        self.send({'type': 'options', 'options': {'query': '', 'groups': [], 'local': {'enabled': False}}})
        page.wait_for('IDGraph.stats().nodes >= 200')
        self.send({'type': 'options', 'options': {'showTags': True, 'showAttachments': True,
                   'showCode': False, 'hideUnresolved': True, 'showOrphans': False}})
        page.wait_for('IDGraph._debug.ids().includes("#space")')
        counts = view.last_data['stats']['counts']
        self.assertGreater(counts['tag'], 0)
        self.assertGreater(counts['attachment'], 0)
        self.assertEqual(counts['code'], 0)
        self.assertEqual(counts['unresolved'], 0)
        self.assertEqual(view.last_data['stats']['orphans'], 0)
        self.send({'type': 'options', 'options': {'showTags': False, 'showAttachments': False,
                   'showCode': True, 'hideUnresolved': False, 'showOrphans': True}})
        page.wait_for('IDGraph._debug.ids().includes("src/app.py") && !IDGraph._debug.ids().includes("#space")')
        global_count = page.js('IDGraph.stats().nodes')
        self.menu('Local graph')
        page.wait_for('IDGraph._debug.local().enabled && IDGraph.stats().nodes < ' + str(global_count))
        self.assertEqual(panel.graph_count(), 1)
        self.window.editor.open_file(str(self.root / 'concepts/Orbit.md'))
        page.wait_for('IDGraph._debug.local().path === "concepts/Orbit.md"')
        self.menu('Graph view')
        page.wait_for('!IDGraph._debug.local().enabled && IDGraph.stats().nodes >= 200')
        # Context/search/create/errors cross the real script message bridge.
        self.send({'type': 'search', 'query': 'tag:#space'})
        self.assertEqual(self.window.explorer.search.entry.get_text(), 'tag:#space')
        self.window.explorer.clear_search()
        self.send({'type': 'create', 'name': 'Integration Created'})
        self.assertTrue((self.root / 'Integration Created.md').is_file())
        self.assertEqual(self.window.editor.current_path(), str(self.root / 'Integration Created.md'))
        with patch.object(self.window, 'notify') as notice:
            self.send({'type': 'create', 'name': '../escape'})
            notice.assert_called_once()
        self.send({'type': 'context', 'action': 'copy-path', 'path': note})
        self.assertEqual(Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).wait_for_text(), str(self.root / note))
        for action in ('reveal', 'open-new-tab', 'open-right'):
            self.send({'type': 'context', 'action': action, 'path': note})
        self.assertEqual(self.window.editor.current_path(), str(self.root / note))
        with patch.object(self.window, 'notify') as notice, patch('sys.stderr', new_callable=io.StringIO) as log:
            for message in ('test error', 'test error', 'second error'):
                self.send({'type': 'error', 'message': message})
            self.assertEqual(notice.call_count, 1)
            self.assertEqual(log.getvalue().count('Graph view:'), 2)
        self.window.toggle_terminal_right()
        page.wait_for('IDGraph.stats().paused')
        self.window.toggle_terminal_right()
        page.wait_for('!IDGraph.stats().paused')
        self.assertEqual(panel.terminal_count(), 0)
        view.grab_focus()
        self.assertIs(self.window.get_focus(), view.webview)
        self.assertFalse(self.window._terminal_focused())
        for key, state, method in (
            (Gdk.KEY_p, Gdk.ModifierType.CONTROL_MASK, 'open_quick_switcher'),
            (Gdk.KEY_o, Gdk.ModifierType.CONTROL_MASK, 'open_quick_switcher'),
            (Gdk.KEY_P, Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK, 'open_command_palette'),
            (Gdk.KEY_comma, Gdk.ModifierType.CONTROL_MASK, 'open_settings'),
        ):
            event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
            event.keyval, event.state = key, state
            with patch.object(self.window, method) as callback:
                self.assertTrue(self.window.do_key_press_event(event))
                callback.assert_called_once()
        self.window.toggle_terminal_fullscreen()
        self.assertIs(self.window._fullscreen_panel, panel)
        self.window.toggle_terminal_fullscreen()
        if os.environ.get('IDGENT_GRAPH_QA') == '1':
            self.visual_qa(first_frame)
        # Open Folder replaces the root even if the old index was busy.
        other = Path(self.temp.name, 'other')
        other.mkdir()
        (other / 'Fresh.md').write_text('[[Second]]')
        (other / 'Second.md').write_text('')
        self.window.set_root(str(other))
        page.wait_for('IDGraph._debug.ids().length === 2 && IDGraph._debug.ids().includes("Fresh.md")')
        self.assertEqual(view.root, str(other))
        self.window.set_theme(light.id, persist=False)
        page.wait_for('IDGraph.stats().nodes === 2')
        processes = web_processes() - baseline
        self.assertTrue(processes)
        panel._header_focus(next(p for p in panel._panes if p.kind == 'graph'))
        panel.close_current()
        self.assertTrue(poll(lambda: not panel.get_visible()))
        self.assertTrue(poll(lambda: not (web_processes() & processes), timeout=10))
        self.assertIsNone(view.webview)

    def change_setting(self, key, value):
        self.assertTrue(settings.set_value(key, value))
        self.window._setting_changed(None, key, settings.get_value(key))

    def send(self, message):
        self.page.js('webkit.messageHandlers.idgent.postMessage(' + json.dumps(message) + '); null')
        self.page.pump(.03)

    def shot(self, name):
        self.page.pump(.35)
        shots = ARTIFACTS / 'shots'
        shots.mkdir(parents=True, exist_ok=True)
        window = Gdk.get_default_root_window()
        pixbuf = Gdk.pixbuf_get_from_window(window, 0, 0, 1280, 800)
        self.assertIsNotNone(pixbuf)
        pixbuf.savev(str(shots / ('wiring-' + name + '.png')), 'png', [], [])

    def visual_qa(self, first_frame):
        page, window = self.page, self.window
        panel = window.terminal_right
        view = panel.graph_views()[0]
        # CPU is measured from web-process ticks, normalized to one CPU core.
        pids = web_processes()
        ticks = os.sysconf('SC_CLK_TCK')
        def usage():
            total = 0
            for pid in pids:
                try:
                    fields = Path('/proc', str(pid), 'stat').read_text().rsplit(')', 1)[1].split()
                    total += int(fields[11]) + int(fields[12])
                except FileNotFoundError:
                    pass
            return total
        def sample(seconds=3):
            start_ticks, start = usage(), time.monotonic()
            page.pump(seconds)
            return round((usage() - start_ticks) / ticks / (time.monotonic() - start) * 100, 3)
        view.post('setSettings', {'close': True})
        window.reset_graph_camera()
        view.post('setSettings', {'scale': .25})
        page.pump(.1)
        running_start = page.js('IDGraph.stats()')
        self.assertTrue(running_start['simRunning'])
        # Measure display callbacks; stats.frames counts draw calls, which can
        # include more than one draw per compositor frame after host updates.
        frame_sample = page.measure_fps(.75)
        running = page.js('IDGraph.stats()')
        running_fps = frame_sample['fps']
        self.shot('a-right-graph-only')
        # Wait for natural idle, not a host pause.
        page.wait_for('!IDGraph.stats().running', timeout=90)
        before = page.js('IDGraph.stats()')
        visible_cpu = sample(5)
        after = page.js('IDGraph.stats()')
        rss = sum(int(Path('/proc', str(pid), 'statm').read_text().split()[1])
                  * os.sysconf('SC_PAGE_SIZE') for pid in pids) / 1024 / 1024
        window.toggle_terminal_right()
        page.wait_for('IDGraph.stats().paused')
        page.pump(3)
        hidden_cpu = sample(8)
        window.toggle_terminal_right()
        metrics = dict(first_frame_seconds=round(first_frame, 3), running_fps=round(running_fps, 2),
                       running_frame_ms=running['frameMs'], settled_fps_over_5s=round((after['frames'] - before['frames']) / 5, 3),
                       settled_stats=after, visible_cpu_percent=visible_cpu, hidden_cpu_percent=hidden_cpu,
                       web_process_rss_mib=round(rss, 2))
        ARTIFACTS.mkdir(exist_ok=True)
        (ARTIFACTS / 'graph-metrics.json').write_text(json.dumps(metrics, indent=2) + '\n')
        panel.new_terminal()
        self.shot('b-right-terminal-graph')
        bottom = window.terminal_bottom
        bottom.set_no_show_all(False)
        bottom.show_all()
        bottom.add_graph()
        bottom.new_terminal()
        bottom.new_terminal()
        bottom_page = self.make_page(bottom.graph_views()[0])
        bottom_page.wait_for('window.IDGraph && IDGraph.stats().nodes >= 200 && IDGraph._debug.nodeAlpha("index/Space Index.md") > 0')
        bottom.graph_views()[0].post('setSettings', {'scale': .12, 'close': True})
        window._hide_terminal(panel)
        self.shot('c-bottom-graph-two-terminals')
        window._hide_terminal(bottom)
        window._show_terminal(panel)
        for extra in list(panel._panes):
            if extra.kind == 'terminal':
                panel._close_pane(extra)
        page.pump(.3)
        light = next(t for t in THEMES if not t.dark)
        window.set_theme(light.id, persist=False)
        view.post('setSettings', {'close': False})
        self.shot('d-light')
        view.post('setSettings', {'close': True})
        window.set_theme('dark-plus', persist=False)
        self.change_setting('accent', '#e5484d')
        self.shot('e-accent')
        view.post('setSettings', {'close': False, 'collapse-filter': False})
        self.shot('f-controls')
        window.open_local_graph()
        view.post('setSettings', {'close': True, 'scale': .3})
        page.wait_for('IDGraph._debug.local().enabled && IDGraph.stats().nodes < 100')
        self.shot('g-local')
        window.open_graph()
        page.wait_for('IDGraph.stats().nodes >= 200')
        panel.add_button.clicked()
        self.shot('h-chooser')
        panel.add_button.popover.popdown()
        view.post('setSettings', {'close': True})
        page.wait_for('!IDGraph.stats().running', timeout=90)
        page.js('''(() => { const c=document.getElementById('graph'),r=c.getBoundingClientRect();
          const ids=IDGraph._debug.ids();
          const id=ids.find(id=>{const p=IDGraph._debug.screenPos(id);return p[0]>20&&p[0]<r.width-20&&p[1]>20&&p[1]<r.height-20});
          const p=IDGraph._debug.screenPos(id); c.dispatchEvent(new PointerEvent('pointermove',
          {clientX:r.left+p[0],clientY:r.top+p[1],pointerId:1,bubbles:true})); return null; })()''')
        page.wait_for('IDGraph.stats().hover !== null')
        self.shot('i-hover')
        # Close QA-only panes so the lifecycle assertion targets the right graph.
        for extra in list(bottom._panes):
            bottom._close_pane(extra)
        for extra in list(panel._panes):
            if extra.kind == 'terminal':
                panel._close_pane(extra)
        page.pump(.3)
