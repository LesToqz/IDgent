"""Graph view page (idgent/graph_web): node unit tests + a WebKit2 harness under xvfb.

Run: GDK_BACKEND=x11 xvfb-run -a python3 -W ignore::DeprecationWarning -m unittest tests.test_graph_web -v
"""
import json
import os
import shutil
import subprocess
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(ROOT, 'idgent', 'graph_web', 'graph.html')
SHOTS = os.path.join(ROOT, '.ceo', 'shots')
DEMO_VAULT = '/home/lestoq/projects/demo'

LIGHT = {
    'background': '#ffffff', 'node': '#5c5c5c', 'node_focused': '#8a5cf5', 'node_tag': '#08b94e',
    'node_attachment': '#e0ac00', 'node_unresolved': '#ababab', 'node_code': '#1f7ae0',
    'line': '#dadada', 'line_highlight': '#9873f7', 'text': '#222222', 'text_muted': '#6e6e6e',
    'accent': '#9873f7', 'dark': False,
    'groups': ['#e5484d', '#f76b15', '#d9a400', '#46a758', '#12a594', '#00a2c7',
               '#3e63dd', '#8e4ec6', '#d6409f', '#ad7f58', '#8d8d8d', '#7cb518'],
    'font_ui': 'sans-serif', 'font_text': 'sans-serif',
}


class NodeUnitTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'node is not on PATH')
    def test_node_suite(self):
        proc = subprocess.run(['node', '--test', os.path.join(ROOT, 'tests', 'js')], cwd=ROOT,
                              capture_output=True, text=True, timeout=300)
        self.assertEqual(proc.returncode, 0, proc.stdout[-4000:] + proc.stderr[-2000:])


def _webkit():
    try:
        import gi
        gi.require_version('Gtk', '3.0')
        gi.require_version('WebKit2', '4.1')
        from gi.repository import Gtk, GLib, WebKit2
    except (ImportError, ValueError) as exc:
        return None, str(exc)
    if not os.environ.get('DISPLAY') and not os.environ.get('WAYLAND_DISPLAY'):
        return None, 'no display (run under xvfb-run)'
    return (Gtk, GLib, WebKit2), ''


class Page:
    """A real WebKit2 view on graph.html with JS eval helpers and the 'idgent' message handler."""

    def __init__(self, width=900, height=600, query=''):
        (Gtk, GLib, WebKit2), _ = _webkit()
        self.Gtk, self.GLib, self.WebKit2 = Gtk, GLib, WebKit2
        self.messages = []
        self.ucm = WebKit2.UserContentManager()
        self.ucm.connect('script-message-received::idgent', self._on_message)
        self.ucm.register_script_message_handler('idgent')
        self.view = WebKit2.WebView.new_with_user_content_manager(self.ucm)
        st = self.view.get_settings()
        st.set_hardware_acceleration_policy(WebKit2.HardwareAccelerationPolicy.NEVER)
        st.set_allow_file_access_from_file_urls(True)
        st.set_enable_write_console_messages_to_stdout(bool(os.environ.get('GRAPH_CONSOLE')))
        self.win = Gtk.Window()
        self.win.set_default_size(width, height)
        self.win.add(self.view)
        self.win.show_all()
        self.view.load_uri('file://' + PAGE + query)
        self.wait_for('!!(window.IDGraph && window.IDGraph.stats)', 15)

    def _on_message(self, _ucm, result):
        value = result.get_js_value() if hasattr(result, 'get_js_value') else result
        self.messages.append(json.loads(value.to_json(0)))

    def pump(self, seconds=0.0):
        ctx = self.GLib.MainContext.default()
        end = time.monotonic() + seconds
        while True:
            while ctx.pending():
                ctx.iteration(False)
            if time.monotonic() >= end:
                break
            ctx.iteration(False)
            time.sleep(0.002)

    def js(self, script, timeout=10):
        box = {}

        def done(view, res):
            try:
                value = view.evaluate_javascript_finish(res)
                box['v'] = json.loads(value.to_json(0) or 'null') if not value.is_undefined() else None
            except Exception as exc:  # noqa: BLE001 - surfaced below
                box['e'] = exc
        self.view.evaluate_javascript(script, -1, None, None, None, done)
        end = time.monotonic() + timeout
        ctx = self.GLib.MainContext.default()
        while 'v' not in box and 'e' not in box:
            if time.monotonic() > end:
                raise TimeoutError(script[:200])
            ctx.iteration(True)
        if 'e' in box:
            raise box['e']
        return box['v']

    def call(self, name, *args):
        return self.js('IDGraph.%s(%s)' % (name, ', '.join(json.dumps(a) for a in args)))

    def wait_for(self, expr, timeout=10):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                if self.js('!!(%s)' % expr, timeout=5):
                    return True
            except Exception:  # noqa: BLE001 - page may still be loading
                pass
            self.pump(0.03)
        raise AssertionError('timed out waiting for ' + expr)

    def of_type(self, kind):
        return [m for m in self.messages if m.get('type') == kind]

    def snapshot(self, name):
        """Save a PNG of the view to .ceo/shots (WebView.get_snapshot; falls back to the X window
        when PyGObject has no cairo foreign converter)."""
        os.makedirs(SHOTS, exist_ok=True)
        path = os.path.join(SHOTS, name + '.png')
        self.pump(0.05)
        box = {}

        def done(view, res):
            try:
                box['s'] = view.get_snapshot_finish(res)
            except Exception as exc:  # noqa: BLE001
                box['e'] = exc
        self.view.get_snapshot(self.WebKit2.SnapshotRegion.VISIBLE, self.WebKit2.SnapshotOptions.NONE, None, done)
        end = time.monotonic() + 10
        while not box and time.monotonic() < end:
            self.GLib.MainContext.default().iteration(True)
        if 's' in box:
            box['s'].write_to_png(path)
            return path
        from gi.repository import Gdk
        win = self.view.get_window()
        alloc = self.view.get_allocation()
        pix = Gdk.pixbuf_get_from_window(win, 0, 0, alloc.width, alloc.height) if win else None
        if pix is None:
            return None
        pix.savev(path, 'png', [], [])
        return path

    def resize(self, width, height):
        self.win.resize(width, height)
        self.view.set_size_request(width, height)
        self.pump(0.3)

    def destroy(self):
        self.win.destroy()
        self.pump(0.05)

    # synthetic input -----------------------------------------------------
    def pointer(self, kind, x, y, button=0, buttons=1, **mods):
        flags = ', '.join('%s: %s' % (k, json.dumps(v)) for k, v in mods.items())
        return self.js(
            "(() => { const c = document.getElementById('graph'); const r = c.getBoundingClientRect();"
            " c.dispatchEvent(new PointerEvent(%s, {bubbles: true, cancelable: true, pointerId: 1, pointerType: 'mouse',"
            " isPrimary: true, clientX: r.left + %f, clientY: r.top + %f, button: %d, buttons: %d%s})); return true; })()"
            % (json.dumps(kind), x, y, button, buttons, (', ' + flags) if flags else ''))

    def wheel(self, x, y, dy, mode=0, ctrl=False):
        return self.js(
            "(() => { const c = document.getElementById('graph'); const r = c.getBoundingClientRect();"
            " c.dispatchEvent(new WheelEvent('wheel', {bubbles: true, cancelable: true, clientX: r.left + %f,"
            " clientY: r.top + %f, deltaY: %f, deltaMode: %d, ctrlKey: %s})); return true; })()"
            % (x, y, dy, mode, 'true' if ctrl else 'false'))

    def stats(self):
        return self.js('IDGraph.stats()')

    def settle(self, timeout=20):
        self.wait_for('!IDGraph.stats().simRunning', timeout)
        self.pump(0.3)

    def measure_fps(self, seconds, driver=None):
        """Drive the page for `seconds` (optional driver(i) per step) and return fps from rAF timestamps."""
        self.js("window.__stop = false; window.__f = []; (function loop(t){ window.__f.push(t); if (window.__f.length < 100000 && !window.__stop) requestAnimationFrame(loop); })(performance.now()); true")
        end = time.monotonic() + seconds
        i = 0
        while time.monotonic() < end:
            if driver:
                driver(i)
            i += 1
            self.pump(0.016)
        info = self.js("(() => { window.__stop = true; const f = window.__f; const d = [];"
                       " for (let i = 1; i < f.length; i++) d.push(f[i] - f[i-1]);"
                       " d.sort((a,b)=>a-b); const avg = d.reduce((a,b)=>a+b,0)/Math.max(1,d.length);"
                       " return {fps: 1000/avg, frames: d.length, p95: d[Math.floor(d.length*0.95)] || 0, maxMs: d[d.length-1] || 0}; })()")
        return info


WK, WK_REASON = _webkit()


@unittest.skipIf(WK is None, 'WebKit2 4.1 unavailable: ' + WK_REASON)
class GraphPageTests(unittest.TestCase):
    perf = {}

    @classmethod
    def setUpClass(cls):
        cls.page = Page()
        cls.page.wait_for('IDGraph.stats().frames > 0', 10)

    @classmethod
    def tearDownClass(cls):
        cls.page.destroy()
        if cls.perf:
            os.makedirs(SHOTS, exist_ok=True)
            with open(os.path.join(SHOTS, 'perf.json'), 'w') as fh:
                json.dump(cls.perf, fh, indent=2)
            print('\nPERF', json.dumps(cls.perf))

    def setUp(self):
        p = self.page
        p.call('resume')
        p.call('setSettings', {'reduceMotion': False, 'close': True, 'showArrow': False, 'scale': 1,
                               'nodeSizeMultiplier': 1, 'lineSizeMultiplier': 1, 'textFadeMultiplier': 0})
        p.call('setLocal', {'enabled': False})
        p.call('setTheme', {})
        p.call('setActive', None)
        p.pump(0.3)
        p.messages.clear()

    def _demo(self, n=300, seed=1, settle=True):
        p = self.page
        p.call('demo', n, seed)
        if settle:
            p.settle(25)

    def _screen(self, node_id):
        return self.page.js('IDGraph._debug.screenPos(%s)' % json.dumps(node_id))

    def _visible_node(self, kind='note'):
        return self.page.js(
            "(() => { const ids = IDGraph._debug.ids(); const s = IDGraph.stats(); let best = null, bd = 1e9;"
            " for (const id of ids) { const p = IDGraph._debug.screenPos(id); if (!p) continue;"
            " if (%s && !(id.startsWith('notes/') || id.startsWith('projects/') || id.startsWith('daily/') || id.startsWith('people/'))) continue;"
            " const d = Math.hypot(p[0] - s.width/2, p[1] - s.height/2); if (d < bd) { bd = d; best = id; } } return best; })()"
            % ('true' if kind == 'note' else 'false'))

    # ------------------------------------------------------------------ basics
    def test_01_ready_once_and_safe_before_data(self):
        p = Page(400, 300)
        try:
            p.wait_for('IDGraph.stats().frames > 0', 10)
            p.pump(0.3)
            for cmd, arg in [('setTheme', {}), ('setSettings', {}), ('setActive', None), ('focus', 'x'),
                             ('setLocal', {}), ('stats', None), ('pause', None), ('resume', None)]:
                p.call(cmd, arg)
            p.pump(0.2)
            self.assertEqual(len(p.of_type('ready')), 1, p.messages)
            self.assertEqual(p.of_type('error'), [])
            self.assertIn('Building graph', p.js("document.getElementById('status').textContent"))
            p.call('setData', {'nodes': [], 'links': []})
            p.pump(0.1)
            self.assertIn('No linked files yet', p.js("document.getElementById('status').textContent"))
            p.call('setData', {'nodes': [{'id': 'a.md', 'label': 'a', 'kind': 'note'}], 'links': [], 'truncated': True})
            p.pump(0.1)
            self.assertIn('Showing the 1 most', p.js("document.getElementById('notice').textContent"))
        finally:
            p.destroy()

    def test_02_demo_renders_and_settles(self):
        p = self.page
        t0 = time.monotonic()
        info = p.call('demo', 300, 1)
        self.assertEqual(info['nodes'], 300)
        p.wait_for('IDGraph.stats().frames > 5', 5)
        p.pump(0.5)
        p.snapshot('01-demo300-dark-settling')
        p.wait_for('IDGraph.stats().motion < 0.5', 10)
        self.perf['visual_settle_300_s'] = round(time.monotonic() - t0, 2)
        p.settle(25)
        self.perf['settle_300_s'] = round(time.monotonic() - t0, 2)
        st = p.stats()
        self.assertFalse(st['simRunning'])
        p.pump(0.3)
        self.assertFalse(p.stats()['running'], 'rAF loop must stop when settled')
        self.assertEqual(p.of_type('error'), [])
        p.snapshot('02-demo300-dark')

    def test_03_idle_zero_cpu(self):
        p = self.page
        self._demo()
        f0 = p.stats()['frames']
        p.pump(1.0)
        f1 = p.stats()['frames']
        self.perf['idle_frames_per_s'] = f1 - f0
        self.assertLessEqual(f1 - f0, 2)
        p.call('pause')
        self.assertTrue(p.stats()['paused'])
        p.wheel(450, 300, -120)
        p.pump(0.3)
        self.assertEqual(p.stats()['frames'], f1, 'paused page must not render')
        p.call('resume')
        p.pump(0.6)
        self.assertGreater(p.stats()['frames'], f1)

    def test_04_diff_keeps_positions(self):
        p = self.page
        self._demo()
        before = p.js("(() => { const o = {}; for (const id of IDGraph._debug.ids()) o[id] = IDGraph._debug.worldPos(id); return o; })()")
        g = p.js("(() => { const g = GraphCore.demo(300, 1); g.nodes.push({id: 'new.md', label: 'new', kind: 'note', path: 'new.md'});"
                 " g.links.push({s: 300, t: 0, kind: 'link', resolved: true, count: 1, self: false});"
                 " g.nodes.splice(299, 1); g.links = g.links.filter(l => l.s !== 299 && l.t !== 299).map(l => ({...l, s: l.s === 300 ? 299 : l.s, t: l.t === 300 ? 299 : l.t})); return g; })()")
        p.call('setData', g)
        p.pump(0.05)
        after = p.js("(() => { const o = {}; for (const id of IDGraph._debug.ids()) o[id] = IDGraph._debug.worldPos(id); return o; })()")
        st = p.stats()
        self.assertGreaterEqual(st['alpha'], 0.25)
        moved = [abs(after[k][0] - before[k][0]) + abs(after[k][1] - before[k][1]) for k in after if k in before]
        self.assertLess(sorted(moved)[len(moved) // 2], 30, 'kept nodes must not restart the layout')
        anchor = after[g['nodes'][0]['id']]
        new = after['new.md']
        self.assertLess(((new[0] - anchor[0]) ** 2 + (new[1] - anchor[1]) ** 2) ** 0.5, 300)
        self.assertLess(p.js("IDGraph._debug.nodeAlpha('new.md')"), 0.9, 'new nodes fade in')

    def test_05_theme_and_settings_roundtrip(self):
        p = self.page
        self._demo()
        nid = self._visible_node()
        p.call('setTheme', LIGHT)
        p.pump(1.0)
        self.assertEqual(p.js("getComputedStyle(document.body).backgroundColor"), 'rgb(255, 255, 255)')
        self.assertEqual(p.js('IDGraph._debug.nodeColor(%s)' % json.dumps(nid)), '#5c5c5c')
        p.call('setSettings', {'close': False, 'collapse-filter': False, 'collapse-color-groups': False,
                               'collapse-display': False, 'collapse-forces': False})
        p.pump(0.5)
        p.snapshot('03-demo300-light-panel')
        p.call('setTheme', {})
        p.pump(0.6)
        p.snapshot('04-demo300-dark-panel-open')
        p.js("document.querySelector('.gc-panel').scrollTop = 10000")
        p.pump(0.2)
        p.snapshot('04b-panel-forces')
        p.js("document.querySelector('.gc-panel').scrollTop = 0")
        p.call('setSettings', {'repelStrength': 12, 'nodeSizeMultiplier': 2, 'showTags': True,
                               'colorGroups': [{'query': 'path:notes', 'color': {'a': 1, 'rgb': 0xff0000}}]})
        st = p.js('IDGraph._debug.settings()')
        self.assertEqual(st['repelStrength'], 12)
        self.assertEqual(st['nodeSizeMultiplier'], 2)
        self.assertEqual(p.js("document.querySelector('input[aria-label=\"Repel force\"]').value"), '12')
        self.assertEqual(p.js("document.querySelectorAll('.group').length"), 1)
        self.assertGreaterEqual(p.stats()['alpha'], 0.25, 'forces reheat')
        p.pump(0.4)
        self.assertEqual(p.of_type('settings'), [], 'setSettings must not echo')
        p.call('setSettings', {'repelStrength': 10, 'nodeSizeMultiplier': 1, 'showTags': False, 'colorGroups': [],
                               'collapse-filter': True, 'collapse-color-groups': True, 'collapse-display': True,
                               'collapse-forces': True})

    # ------------------------------------------------------------------ camera
    def test_06_wheel_zoom_and_anchor(self):
        p = self.page
        self._demo()
        s0 = p.stats()
        p.wheel(200, 150, -120)
        p.wait_for('Math.abs(IDGraph.stats().scale - 1.5) < 0.02', 3)
        st = p.stats()
        self.assertAlmostEqual(st['targetScale'], 1.5 * s0['targetScale'], places=4)
        p.pump(0.8)
        self.assertTrue(any('scale' in m['settings'] for m in p.of_type('settings')), 'zoom is persisted')
        # zoom-in keeps the world point under the pointer fixed
        wx = (200 - s0['pan'][0]) / s0['scale']
        self.assertAlmostEqual(st['pan'][0] + wx * st['scale'], 200, delta=1.0)
        p.wheel(200, 150, 120)
        p.wheel(200, 150, 120)
        p.wait_for('Math.abs(IDGraph.stats().scale - 1/1.5) < 0.01', 3)
        p.wheel(200, 150, 3, mode=1)   # line mode: 3 lines = 120 px
        p.wait_for('Math.abs(IDGraph.stats().scale - 1/2.25) < 0.01', 3)
        p.call('setSettings', {'scale': 1})
        p.wait_for('Math.abs(IDGraph.stats().scale - 1) < 0.01', 3)

    def test_07_pan_with_inertia(self):
        p = self.page
        self._demo()
        empty = p.js("(() => { const s = IDGraph.stats(); for (let y = 20; y < s.height; y += 20) for (let x = 20; x < s.width - 260; x += 20) {"
                     " let ok = true; for (const id of IDGraph._debug.ids()) { const q = IDGraph._debug.screenPos(id); if (Math.hypot(q[0]-x, q[1]-y) < q[2] + 6) { ok = false; break; } }"
                     " if (ok) return [x, y]; } return null; })()")
        self.assertIsNotNone(empty)
        x, y = empty
        pan0 = p.stats()['pan']
        p.pointer('pointerdown', x, y)
        for k in range(1, 6):
            p.pointer('pointermove', x + 12 * k, y)
            p.pump(0.016)
        pan1 = p.stats()['pan']
        self.assertAlmostEqual(pan1[0] - pan0[0], 60, delta=1)
        p.pointer('pointerup', x + 60, y, buttons=0)
        p.pump(0.8)
        pan2 = p.stats()['pan']
        self.assertGreater(pan2[0] - pan1[0], 5, 'inertial glide after a flick')
        self.assertEqual(p.of_type('open'), [])

    # ------------------------------------------------------------------ hover / click / drag
    def test_08_hover_dims_and_click_opens(self):
        p = self.page
        self._demo()
        nid = self._visible_node()
        x, y, r = self._screen(nid)
        p.pointer('pointermove', x, y, buttons=0)
        p.pump(0.6)
        self.assertEqual(p.stats()['hover'], nid)
        self.assertTrue(any(m.get('id') == nid for m in p.of_type('hover')))
        dims = p.js("(() => { const h = %s; const a = []; for (const id of IDGraph._debug.ids()) a.push(IDGraph._debug.nodeAlpha(id)); return [Math.min(...a), IDGraph._debug.nodeAlpha(h)]; })()" % json.dumps(nid))
        self.assertLess(dims[0], 0.3)
        self.assertGreater(dims[1], 0.95)
        p.snapshot('05-hover')
        p.pointer('pointerdown', x, y)
        p.pointer('pointerup', x, y, buttons=0)
        p.pump(0.05)
        opens = p.of_type('open')
        self.assertEqual(len(opens), 1, p.messages)
        self.assertEqual(opens[0]['id'], nid)
        self.assertFalse(opens[0]['newTab'])
        p.pointer('pointerdown', x, y, ctrlKey=True)
        p.pointer('pointerup', x, y, buttons=0, ctrlKey=True)
        p.pointer('pointerdown', x, y, button=1, buttons=4)
        p.pointer('pointerup', x, y, button=1, buttons=0)
        p.pump(0.05)
        self.assertEqual([m['newTab'] for m in p.of_type('open')], [False, True, True])
        p.pointer('pointerleave', -10, -10, buttons=0)
        p.pump(0.1)
        self.assertIsNone(p.stats()['hover'])

    def test_09_tag_and_unresolved_clicks(self):
        p = self.page
        g = {'nodes': [{'id': 'a.md', 'label': 'a', 'kind': 'note', 'path': 'a.md'},
                       {'id': '#topic', 'label': 'topic', 'kind': 'tag'},
                       {'id': 'Missing', 'label': 'Missing', 'kind': 'unresolved'}],
             'links': [{'s': 0, 't': 1, 'kind': 'tag'}, {'s': 0, 't': 2, 'kind': 'link', 'resolved': False}]}
        p.call('setData', g)
        p.settle()
        for nid in ('#topic', 'Missing'):
            x, y, _ = self._screen(nid)
            p.pointer('pointerdown', x, y)
            p.pointer('pointerup', x, y, buttons=0)
        p.pump(0.05)
        p.call('setSettings', {'showArrow': True, 'scale': 2})
        p.pump(1.0)
        p.snapshot('13-arrows-zoomed')
        p.call('setSettings', {'showArrow': False, 'scale': 1})
        self.assertEqual(p.of_type('search'), [{'type': 'search', 'query': 'tag:#topic'}])
        self.assertEqual(p.of_type('create'), [{'type': 'create', 'name': 'Missing'}])

    def test_10_drag_moves_node_and_reheats(self):
        p = self.page
        self._demo(1000, 3)
        nid = self._visible_node()
        x, y, _ = self._screen(nid)
        p.pointer('pointerdown', x, y)
        frames = []

        def drive(i):
            p.pointer('pointermove', x + 3 * i, y + 2 * i)
            if i == 20:
                p.snapshot('09-mid-drag')
        info = p.measure_fps(1.5, drive)
        st = p.stats()
        self.assertGreaterEqual(st['alpha'], 0.25)
        pos = self._screen(nid)
        p.pointer('pointerup', pos[0], pos[1], buttons=0)
        p.pump(0.05)
        self.assertEqual(p.of_type('open'), [], 'a drag is not a click')
        self.perf['drag_1000_fps'] = round(info['fps'], 1)
        self.perf['drag_1000_p95_ms'] = round(info['p95'], 1)
        self.perf['drag_1000_frameMs'] = st['frameMs']
        self.perf['tick_1000_ms'] = st['tickMs']
        frames.append(info)
        self.assertGreater(info['fps'], 10)

    def test_11_context_menu(self):
        p = self.page
        self._demo()
        nid = self._visible_node()
        x, y, _ = self._screen(nid)
        p.js("(() => { const c = document.getElementById('graph'); const r = c.getBoundingClientRect();"
             " c.dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, cancelable: true, clientX: r.left + %f, clientY: r.top + %f, button: 2})); })()" % (x, y))
        p.pump(0.1)
        self.assertTrue(p.js('IDGraph._debug.menuOpen()'))
        items = p.js("[...document.querySelectorAll('#menu .item')].map(e => e.textContent)")
        self.assertEqual(items, ['Open', 'Open in new tab', 'Open to the right', 'Reveal in explorer', 'Copy path'])
        self.assertEqual(p.js("document.querySelector('#menu .item').getBoundingClientRect().height"), 28)
        p.snapshot('06-context-menu')
        p.js("document.getElementById('menu').dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowDown', bubbles: true}));"
             "document.getElementById('menu').dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowDown', bubbles: true}));"
             "document.getElementById('menu').dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));")
        p.pump(0.05)
        ctx = p.of_type('context')
        self.assertEqual(len(ctx), 1)
        self.assertEqual(ctx[0]['action'], 'open-new-tab')
        self.assertEqual(ctx[0]['id'], nid)
        self.assertFalse(p.js('IDGraph._debug.menuOpen()'))

    # ------------------------------------------------------------------ panel
    def test_12_panel_controls_emit_messages(self):
        p = self.page
        self._demo()
        p.call('setSettings', {'close': False, 'collapse-filter': False, 'collapse-forces': False,
                               'collapse-display': False, 'collapse-color-groups': False})
        p.pump(0.3)
        p.js("document.querySelector('button[aria-label=\"Tags\"]').click()")
        p.js("{ const r = document.querySelector('input[aria-label=\"Repel force\"]'); r.value = '15'; r.dispatchEvent(new Event('input')); }")
        p.js("{ const s = document.querySelector('input[aria-label=\"Search files\"]'); s.value = 'tag:#x'; s.dispatchEvent(new Event('input')); }")
        p.pump(0.04)
        self.assertEqual(p.of_type('options'), [], 'options are debounced')
        p.pump(0.15)
        opts = p.of_type('options')
        self.assertEqual(len(opts), 1)
        self.assertTrue(opts[0]['options']['showTags'])
        self.assertEqual(opts[0]['options']['query'], 'tag:#x')
        self.assertEqual(p.of_type('settings'), [], 'settings are debounced 250 ms')
        p.pump(0.3)
        st = p.of_type('settings')
        self.assertEqual(len(st), 1)
        self.assertEqual(st[0]['settings'], {'showTags': True, 'repelStrength': 15, 'search': 'tag:#x'})
        p.messages.clear()
        p.js("document.querySelector('.groups-new').click()")
        p.pump(0.4)
        groups = p.of_type('options')[-1]['options']['groups']
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]['query'], '')
        p.js("document.querySelector('.group .swatch').click()")
        p.pump(0.1)
        self.assertFalse(p.js("document.getElementById('popup').hidden"))
        p.snapshot('07-panel-color-popup')
        p.js("document.querySelectorAll('#popup .swatch')[3].click()")
        p.pump(0.4)
        self.assertEqual(p.of_type('settings')[-1]['settings']['colorGroups'][0]['color']['a'], 1)
        p.messages.clear()
        p.js("document.querySelector('.sec-head').click()")
        p.pump(0.4)
        self.assertEqual(p.of_type('settings')[-1]['settings'], {'collapse-filter': True})
        p.js("document.querySelector('button[aria-label=\"Restore default settings\"]').click()")
        p.pump(0.4)
        last = p.of_type('settings')[-1]['settings']
        self.assertEqual(last['repelStrength'], 10)
        self.assertEqual(last['colorGroups'], [])
        self.assertFalse(last['showTags'])
        self.assertEqual(p.js('IDGraph._debug.settings().search'), 'tag:#x', 'restore leaves the search text')
        self.assertEqual(p.js("document.querySelectorAll('.group').length"), 0)
        p.js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))")
        p.pump(0.3)
        self.assertFalse(p.js('IDGraph._debug.panelOpen()'))
        p.call('setSettings', {'search': ''})

    def test_13_local_graph_mode(self):
        p = self.page
        g = p.js("GraphCore.demo(300, 1)")
        center = g['nodes'][0]['id']
        p.call('setLocal', {'enabled': True, 'path': center, 'depth': 2, 'incoming': True, 'outgoing': True})
        g['stats']['focus'] = center
        p.call('setData', g)
        p.call('setSettings', {'close': False, 'collapse-filter': False})
        p.settle()
        self.assertEqual(p.js('IDGraph._debug.weight(%s)' % json.dumps(center)), 30)
        rows = p.js("[...document.querySelectorAll('.sec .row:not(.is-hidden) .name')].map(e => e.textContent)")
        self.assertIn('Depth2', ''.join(rows).replace(' ', ''))
        self.assertNotIn('Orphans', rows)
        p.snapshot('08-local-graph')
        p.messages.clear()
        p.js("{ const r = document.querySelector('input[aria-label=\"Depth\"]'); r.value = '3'; r.dispatchEvent(new Event('input')); }")
        p.pump(0.15)
        self.assertEqual(p.of_type('options')[-1]['options']['local'],
                         {'enabled': True, 'depth': 3, 'incoming': True, 'outgoing': True})
        p.call('setLocal', {'enabled': False})

    def test_14_narrow_pane(self):
        p = Page(280, 200)
        try:
            p.wait_for('IDGraph.stats().frames > 0', 10)
            p.call('demo', 120, 2)
            p.call('setSettings', {'close': False, 'collapse-filter': False, 'collapse-display': False})
            p.pump(1.5)
            box = p.js("(() => { const r = document.querySelector('.gc-panel').getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom, innerWidth, innerHeight]; })()")
            self.assertGreaterEqual(box[0], 0)
            self.assertLessEqual(box[2], box[4])
            self.assertLessEqual(box[3], box[5])
            p.snapshot('10-narrow-280x200')
        finally:
            p.destroy()

    def test_15_focus_and_active(self):
        p = self.page
        self._demo()
        ids = p.js('IDGraph._debug.ids()')
        far = max(ids, key=lambda i: sum(abs(v) for v in p.js('IDGraph._debug.worldPos(%s)' % json.dumps(i))))
        p.call('setActive', far)
        self.assertTrue(p.call('focus', far))
        p.wait_for("(() => { const q = IDGraph._debug.screenPos(%s); const s = IDGraph.stats(); return Math.hypot(q[0]-s.width/2, q[1]-s.height/2) < 2; })()" % json.dumps(far), 5)
        p.pump(0.6)
        self.assertEqual(p.js('IDGraph._debug.nodeColor(%s)' % json.dumps(far)), '#a68af9')

    def test_16_keyboard_pan_zoom(self):
        p = self.page
        self._demo()
        p.js("document.getElementById('graph').focus()")
        pan0 = p.stats()['pan']
        p.js("document.getElementById('graph').dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowLeft', bubbles: true, cancelable: true}))")
        p.pump(0.4)
        p.js("window.dispatchEvent(new KeyboardEvent('keyup', {key: 'ArrowLeft', bubbles: true}))")
        self.assertGreater(p.stats()['pan'][0] - pan0[0], 50)
        prevented = p.js("(() => { const e = new KeyboardEvent('keydown', {key: 'p', ctrlKey: true, bubbles: true, cancelable: true});"
                         " document.getElementById('graph').dispatchEvent(e); return e.defaultPrevented; })()")
        self.assertFalse(prevented, 'Ctrl+P must reach the IDE')

    def test_17_reduce_motion_jumps(self):
        p = self.page
        self._demo()
        p.call('setSettings', {'reduceMotion': True})
        p.wheel(450, 300, -120)
        p.pump(0.05)
        self.assertAlmostEqual(p.stats()['scale'], 1.5, places=3)
        p.call('setSettings', {'reduceMotion': False, 'scale': 1})
        p.pump(0.5)

    def test_18_timelapse(self):
        p = self.page
        self._demo(80, 4)
        p.js("document.querySelector('button[aria-label=\"Start timelapse animation\"]').click()")
        p.pump(0.5)
        self.assertTrue(p.js('IDGraph._debug.timelapse()'))
        self.assertLess(p.stats()['nodes'], 80)
        p.call('setData', p.js('GraphCore.demo(80, 4)'))
        self.assertFalse(p.js('IDGraph._debug.timelapse()'), 'host data cancels the timelapse')
        self.assertEqual(p.stats()['nodes'], 80)

    # ------------------------------------------------------------------ perf
    def test_20_perf_300_idle_camera(self):
        p = self.page
        self._demo(300, 1)

        def drive(i):
            if i % 8 == 0:
                p.wheel(450, 300, -60 if (i // 8) % 2 == 0 else 60)
        info = p.measure_fps(2.0, drive)
        self.perf['camera_300_fps'] = round(info['fps'], 1)
        self.perf['camera_300_p95_ms'] = round(info['p95'], 1)
        self.perf['camera_300_frameMs'] = p.stats()['frameMs']
        self.assertGreaterEqual(info['fps'], 25)

    def test_21_perf_1000_running_and_settled(self):
        p = self.page
        p.call('demo', 1000, 5)
        p.pump(0.3)
        info = p.measure_fps(2.0)
        st = p.stats()
        self.perf['sim_1000_fps'] = round(info['fps'], 1)
        self.perf['sim_1000_p95_ms'] = round(info['p95'], 1)
        self.perf['sim_1000_tickMs'] = st['tickMs']
        self.perf['sim_1000_frameMs'] = st['frameMs']
        p.settle(30)

        def drive(i):
            if i % 8 == 0:
                p.wheel(450, 300, -60 if (i // 8) % 2 == 0 else 60)
        info2 = p.measure_fps(2.0, drive)
        self.perf['camera_1000_fps'] = round(info2['fps'], 1)
        self.perf['camera_1000_frameMs'] = p.stats()['frameMs']
        self.assertGreaterEqual(info['fps'], 12)

    def test_22_perf_1500_tick(self):
        p = self.page
        p.call('demo', 1500, 6)
        p.pump(1.0)
        st = p.stats()
        self.perf['sim_1500_tickMs'] = st['tickMs']
        self.perf['sim_1500_links'] = st['links']
        info = p.measure_fps(1.5)
        self.perf['sim_1500_fps'] = round(info['fps'], 1)
        self.perf['sim_1500_maxFrameMs'] = round(info['maxMs'], 1)
        self.assertLess(st['tickMs'], 40)

    # ------------------------------------------------------------------ real data
    @unittest.skipUnless(os.path.isdir(DEMO_VAULT), 'demo vault missing')
    def test_30_demo_vault(self):
        code = ("import json, sys; sys.path.insert(0, %r); from idgent.graphdata import GraphIndex, GraphOptions;"
                "index = GraphIndex(%r); index.scan();"
                "print(json.dumps(index.build(GraphOptions(existing_only=False)).to_json()))" % (ROOT, DEMO_VAULT))
        out = subprocess.run(['python3', '-c', code], capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        g = json.loads(out.stdout)
        self.assertGreater(len(g['nodes']), 100)
        p = self.page
        p.call('setData', g)
        p.settle(30)
        st = p.stats()
        self.assertEqual(st['nodes'], len(g['nodes']))
        self.assertEqual(p.of_type('error'), [])
        p.call('setSettings', {'scale': 0.6})
        p.pump(1.0)
        p.snapshot('11-demo-vault')
        p.call('setTheme', LIGHT)
        p.pump(0.6)
        p.snapshot('12-demo-vault-light')
        p.call('setSettings', {'scale': 1})


if __name__ == '__main__':
    unittest.main()
