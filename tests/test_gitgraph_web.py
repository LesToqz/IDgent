"""Git graph page (idgent/gitgraph_web): node unit tests + a WebKit2 harness under xvfb.

Run: GDK_BACKEND=x11 xvfb-run -a python3 -W ignore::DeprecationWarning -m unittest tests.test_gitgraph_web -v
Screenshots go to .ceo/shots/ when GITGRAPH_SHOTS=1.
"""
import json
import os
import shutil
import subprocess
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(ROOT, 'idgent', 'gitgraph_web', 'gitgraph.html')
FIXTURES = os.path.join(ROOT, 'tests', 'fixtures')
SHOTS = os.path.join(ROOT, '.ceo', 'shots')

DARK = {
    'background': '#1c1c1c', 'surface': '#282828', 'surface_alt': '#232323', 'border': '#333333',
    'text': '#dadada', 'text_muted': '#b3b3b3', 'accent': '#8a5cf5', 'accent_fg': '#000000', 'head': '#8a5cf5',
    'branch_colors': ['#4c8df6', '#a371f7', '#3fb950', '#f0883e', '#f85149', '#39c5cf', '#db61a2', '#d29922'],
    'mono_font': 'monospace', 'ui_font': 'sans-serif', 'dark': True,
}
LIGHT = {
    'background': '#ffffff', 'surface': '#f6f6f6', 'surface_alt': '#fafafa', 'border': '#e4e4e4',
    'text': '#222222', 'text_muted': '#5c5c5c', 'accent': '#9873f7', 'accent_fg': '#ffffff', 'head': '#9873f7',
    'branch_colors': ['#4c8df6', '#a371f7', '#3fb950', '#f0883e', '#f85149', '#39c5cf', '#db61a2', '#d29922'],
    'mono_font': 'monospace', 'ui_font': 'sans-serif', 'dark': False,
}


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding='utf-8') as fh:
        return json.load(fh)


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
    """A real WebKit2 view on gitgraph.html with JS eval helpers and (optionally) the 'idgent' message handler."""

    def __init__(self, width=1100, height=520, query='', bridge=True):
        (Gtk, GLib, WebKit2), _ = _webkit()
        self.Gtk, self.GLib, self.WebKit2 = Gtk, GLib, WebKit2
        self.messages = []
        self.ucm = WebKit2.UserContentManager()
        if bridge:
            self.ucm.connect('script-message-received::idgent', self._on_message)
            self.ucm.register_script_message_handler('idgent')
        self.console = []
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
        self.wait_for('!!(window.IDGit && window.IDGit.stats)', 15)
        # collect console errors / uncaught exceptions inside the page
        self.js("window.__errs = []; window.addEventListener('error', e => window.__errs.push(String(e.message)));"
                " (function(){ const o = console.error; console.error = function(){ window.__errs.push(Array.from(arguments).join(' ')); return o.apply(console, arguments); }; })(); true")

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
        return self.js('IDGit.%s(%s)' % (name, ', '.join(json.dumps(a) for a in args)))

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

    def stats(self):
        return self.js('IDGit.stats()')

    def settle(self, timeout=8):
        self.wait_for('!IDGit.stats().animating && !IDGit.stats().running', timeout)
        self.pump(0.1)

    def errors(self):
        return self.js('window.__errs || []') + [m for m in self.of_type('error')]

    def snapshot(self, name):
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
        self.wait_for('IDGit.stats().width === %d && IDGit.stats().height === %d' % (width, height), 5)

    def destroy(self):
        self.win.destroy()
        self.pump(0.05)

    # synthetic input -----------------------------------------------------
    def pointer(self, kind, x, y, button=0, buttons=1):
        return self.js(
            "(() => { const c = document.getElementById('gg'); const r = c.getBoundingClientRect();"
            " c.dispatchEvent(new PointerEvent(%s, {bubbles: true, cancelable: true, pointerId: 1, pointerType: 'mouse',"
            " isPrimary: true, clientX: r.left + %f, clientY: r.top + %f, button: %d, buttons: %d})); return true; })()"
            % (json.dumps(kind), x, y, button, buttons))

    def wheel(self, x, y, dy, dx=0, shift=False):
        return self.js(
            "(() => { const c = document.getElementById('gg'); const r = c.getBoundingClientRect();"
            " c.dispatchEvent(new WheelEvent('wheel', {bubbles: true, cancelable: true, clientX: r.left + %f,"
            " clientY: r.top + %f, deltaY: %f, deltaX: %f, deltaMode: 0, shiftKey: %s})); return true; })()"
            % (x, y, dy, dx, 'true' if shift else 'false'))

    def key(self, key):
        return self.js("(() => { const c = document.getElementById('gg'); c.focus();"
                       " c.dispatchEvent(new KeyboardEvent('keydown', {key: %s, bubbles: true, cancelable: true})); return true; })()"
                       % json.dumps(key))

    def pos(self, h):
        return self.js('IDGit._debug.screenPos(%s)' % json.dumps(h))

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
        return self.js("(() => { window.__stop = true; const f = window.__f; const d = [];"
                       " for (let i = 1; i < f.length; i++) d.push(f[i] - f[i-1]);"
                       " d.sort((a,b)=>a-b); const avg = d.reduce((a,b)=>a+b,0)/Math.max(1,d.length);"
                       " return {fps: 1000/avg, frames: d.length, p95: d[Math.floor(d.length*0.95)] || 0}; })()")


def web_process_cpu_seconds():
    """utime+stime (s) of WebKitWebProcess children of this python process (None when unknown)."""
    me = os.getpid()
    total, found = 0.0, False
    tck = os.sysconf('SC_CLK_TCK')
    for pid in os.listdir('/proc'):
        if not pid.isdigit():
            continue
        try:
            with open('/proc/%s/stat' % pid) as fh:
                data = fh.read()
        except OSError:
            continue
        name = data[data.find('(') + 1:data.rfind(')')]
        rest = data[data.rfind(')') + 2:].split()
        if int(rest[1]) != me or 'WebProces' not in name:
            continue
        total += (int(rest[11]) + int(rest[12])) / tck
        found = True
    return total if found else None


class NodeUnitTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'node is not on PATH')
    def test_gitgraph_core(self):
        proc = subprocess.run(['node', '--test', os.path.join(ROOT, 'tests', 'js', 'gitgraph_core.test.mjs')], cwd=ROOT,
                              capture_output=True, text=True, timeout=300)
        self.assertEqual(proc.returncode, 0, proc.stdout[-4000:] + proc.stderr[-2000:])


WK, WK_REASON = _webkit()
SHOW = fixture('gitgraph_showcase.json')


@unittest.skipIf(WK is None, 'WebKit2 4.1 unavailable: ' + WK_REASON)
class GitGraphPageTests(unittest.TestCase):
    perf = {}

    @classmethod
    def setUpClass(cls):
        cls.page = Page(1100, 520)
        cls.page.call('setTheme', DARK)

    @classmethod
    def tearDownClass(cls):
        cls.page.destroy()
        if cls.perf:
            os.makedirs(SHOTS, exist_ok=True)
            with open(os.path.join(SHOTS, 'gitgraph-perf.json'), 'w') as fh:
                json.dump(cls.perf, fh, indent=2)
            print('\nGITGRAPH PERF', json.dumps(cls.perf))

    def setUp(self):
        p = self.page
        p.call('resume')
        p.call('setSettings', {'reduceMotion': False, 'showRemote': True, 'showTags': True})
        p.call('setTheme', DARK)
        p.call('select', None)
        p.pointer('pointerleave', -5, -5)
        p.messages.clear()

    def show(self, data=None, settle=True):
        p = self.page
        p.call('setState', 'loading')
        p.call('setData', data or SHOW)
        if settle:
            p.settle()

    def hash_of(self, subject, data=None):
        for c in (data or SHOW)['commits']:
            if c['subject'] == subject:
                return c['hash']
        raise KeyError(subject)

    def test_01_api_ready_and_safe_before_data(self):
        p = Page(420, 240)
        try:
            p.pump(0.3)
            for cmd in ('setTheme', 'setSettings', 'select', 'focus', 'pause', 'resume'):
                self.assertIn(cmd, p.js('Object.keys(IDGit)'))
                p.call(cmd, None)
            for cmd in ('setData', 'setState', 'stats'):
                self.assertIn(cmd, p.js('Object.keys(IDGit)'))
            self.assertEqual(len(p.of_type('ready')), 1, p.messages)
            self.assertEqual(p.stats()['state'], 'loading')
            self.assertIn('Loading history', p.js("document.getElementById('state').innerText"))
            self.assertEqual(p.errors(), [])
        finally:
            p.destroy()

    def test_02_renders_showcase(self):
        p = self.page
        self.show()
        st = p.stats()
        self.assertEqual(st['state'], 'data')
        self.assertEqual(st['commits'], len(SHOW['commits']))
        self.assertGreaterEqual(st['lanes'], 6)
        self.assertFalse(st['running'], 'rAF loop must stop once settled')
        kinds = [lb['kind'] for lb in p.js('IDGit._debug.labels()')]
        self.assertIn('head', kinds)
        self.assertIn('pill', kinds)
        self.assertEqual(p.errors(), [])

    def test_03_hover_tooltip_hash_subject_time(self):
        p = self.page
        self.show()
        h = self.hash_of("Merge branch 'feature/ui'")
        x, y = p.pos(h)
        p.pointer('pointermove', x + 1, y + 1, buttons=0)
        p.wait_for('IDGit.stats().tip', 3)
        parts = p.js('IDGit._debug.tipParts()')
        self.assertTrue(parts[0]['cls'].startswith('t-hash') and h[:7] in parts[0]['text'], parts)
        self.assertEqual(parts[1]['cls'], 't-subj')
        self.assertIn("Merge branch 'feature/ui'", parts[1]['text'])
        self.assertEqual(parts[2]['cls'], 't-time')
        self.assertRegex(parts[2]['text'], r'[A-Z][a-z]{2} \d{1,2}, \d{4}, \d{1,2}:\d\d [AP]M')
        self.assertIn('ago', parts[2]['text'])
        r = p.js('IDGit._debug.tipRect()')
        st = p.stats()
        self.assertTrue(0 <= r['x'] and r['x'] + r['w'] <= st['width'] and 0 <= r['y'] and r['y'] + r['h'] <= st['height'], r)
        self.assertEqual(p.stats()['hover'], h)
        p.pointer('pointerleave', -5, -5)
        p.pump(0.2)
        self.assertFalse(p.stats()['tip'])

    def test_04_click_posts_open_commit_and_select_ring(self):
        p = self.page
        self.show()
        h = self.hash_of('Refactor config loader')
        x, y = p.pos(h)
        p.pointer('pointerdown', x, y)
        p.pointer('pointerup', x, y, buttons=0)
        p.pump(0.1)
        self.assertEqual(p.of_type('open-commit'), [{'type': 'open-commit', 'hash': h}])
        self.assertEqual(p.stats()['selected'], h)
        p.call('select', None)
        self.assertIsNone(p.stats()['selected'])
        p.call('select', SHOW['commits'][3]['hash'])
        self.assertEqual(p.stats()['selected'], SHOW['commits'][3]['hash'])
        # a drag is not a click
        p.messages.clear()
        p.pointer('pointerdown', x, y)
        p.pointer('pointermove', x + 40, y + 5)
        p.pointer('pointerup', x + 40, y + 5, buttons=0)
        p.pump(0.1)
        self.assertEqual(p.of_type('open-commit'), [])

    def test_05_head_pill_over_head_commit(self):
        p = self.page
        self.show()
        hx, hy = p.pos(SHOW['head']['hash'])
        heads = [lb for lb in p.js('IDGit._debug.labels()') if lb['kind'] == 'head']
        self.assertEqual(len(heads), 1)
        b = heads[0]
        self.assertAlmostEqual(b['x'] + b['w'] / 2, hx, delta=1.5)
        self.assertLess(b['y'] + b['h'], hy - 9, 'HEAD pill sits above the HEAD dot')
        self.assertGreater(b['y'] + b['h'], hy - 40)
        p.js("IDGit._debug.pulseNow(); true")
        self.assertTrue(p.stats()['pulse'])

    def test_06_theme_dark_and_light(self):
        p = self.page
        self.show()
        p.call('setTheme', LIGHT)
        p.pump(0.1)
        px = p.js("(() => { const c = document.getElementById('gg'); const d = c.getContext('2d').getImageData(3, 3, 1, 1).data; return Array.from(d); })()")
        self.assertGreater(px[0], 240)
        self.assertEqual(p.js("getComputedStyle(document.body).backgroundColor"), 'rgb(255, 255, 255)')
        self._check_contrast('light')
        p.call('setTheme', DARK)
        self._check_contrast('dark')
        px = p.js("(() => { const c = document.getElementById('gg'); const d = c.getContext('2d').getImageData(3, 3, 1, 1).data; return Array.from(d); })()")
        self.assertLess(px[0], 40)
        self.assertEqual(p.errors(), [])

    def _check_contrast(self, label):
        def lum(h):
            c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            c = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
            return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

        def ratio(a, b):
            la, lb = lum(a), lum(b)
            return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)
        pal = self.page.js('IDGit._debug.palette()')
        for lane in pal['lanes'] + [pal['main']]:
            self.assertGreaterEqual(ratio(lane['text'], lane['fill']), 4.5, (label, lane))
            self.assertGreaterEqual(ratio(lane['css'], pal['bg']), 2.9, (label, lane))
        self.assertGreaterEqual(ratio(pal['headFg'], pal['head']), 4.5, label)
        self.assertGreaterEqual(ratio(pal['muted'], pal['bg']), 4.5, label)

    def test_07_states(self):
        p = self.page
        texts = {'loading': 'Loading history', 'empty': 'No commits yet', 'not-a-repo': 'Not a Git repository',
                 'git-missing': 'Git is not installed', 'error': 'Could not read'}
        p.call('setData', dict(SHOW, commits=[]))
        for kind, text in texts.items():
            p.call('setState', kind, 'boom' if kind == 'error' else None)
            p.pump(0.05)
            self.assertEqual(p.stats()['state'], kind)
            self.assertIn(text, p.js("document.getElementById('state').innerText"))
            self.assertFalse(p.js("document.getElementById('state').hidden"))
        p.call('setData', dict(SHOW, commits=[]))
        self.assertEqual(p.stats()['state'], 'empty')
        p.call('setData', {'ok': False, 'error': 'not-a-repo'})
        self.assertEqual(p.stats()['state'], 'not-a-repo')
        self.show()
        self.assertTrue(p.js("document.getElementById('state').hidden"))
        p.call('setState', 'loading')            # refresh while data is shown keeps the graph
        self.assertEqual(p.stats()['state'], 'data')
        self.assertEqual(p.errors(), [])

    def test_08_pause_resume_and_idle(self):
        p = self.page
        self.show()
        f0 = p.stats()['frames']
        p.pump(1.0)
        f1 = p.stats()['frames']
        self.perf['idle_frames_per_s'] = f1 - f0
        self.assertLessEqual(f1 - f0, 1, 'no frames while nothing animates')
        p.call('pause')
        self.assertTrue(p.stats()['paused'])
        p.wheel(500, 260, -240)
        p.call('focus', SHOW['commits'][-1]['hash'])
        p.pump(0.4)
        self.assertEqual(p.stats()['frames'], f1, 'paused page must not render')
        self.assertFalse(p.stats()['pulse'])
        p.call('resume')
        p.pump(0.8)
        self.assertGreater(p.stats()['frames'], f1)
        p.settle()

    def test_09_idle_cpu(self):
        p = self.page
        self.show()
        p.js("IDGit._debug.pulseNow(); true")
        p.pump(0.5)
        c0 = web_process_cpu_seconds()
        if c0 is None:
            self.skipTest('web process not found in /proc')
        p.pump(2.0)
        self.perf['cpu_pct_pulse_awake'] = round((web_process_cpu_seconds() - c0) / 2.0 * 100, 1)
        # the pulse sleeps 9 s after the last interaction; pausing stops it at once
        p.call('pause')
        p.pump(0.3)
        c1 = web_process_cpu_seconds()
        f0 = p.stats()['frames']
        p.pump(2.0)
        cpu = (web_process_cpu_seconds() - c1) / 2.0 * 100
        self.perf['cpu_pct_paused'] = round(cpu, 1)
        self.assertEqual(p.stats()['frames'], f0)
        self.assertLess(cpu, 8.0)
        p.call('resume')
        p.js("IDGit.setSettings({reduceMotion: true}); true")        # no pulse at all
        p.pump(0.3)
        c2 = web_process_cpu_seconds()
        p.pump(2.0)
        self.perf['cpu_pct_idle_no_pulse'] = round((web_process_cpu_seconds() - c2) / 2.0 * 100, 1)
        self.assertLess(self.perf['cpu_pct_idle_no_pulse'], 8.0)

    def test_10_reduce_motion_is_instant(self):
        p = self.page
        p.call('setSettings', {'reduceMotion': True})
        p.call('setState', 'loading')
        p.call('setData', SHOW)
        st = p.stats()
        self.assertFalse(st['animating'])
        self.assertEqual(p.js('IDGit._debug.nodeAlpha(%s)' % json.dumps(SHOW['commits'][0]['hash'])), 1)
        p.call('focus', SHOW['commits'][-1]['hash'])
        self.assertFalse(p.stats()['animating'])
        p.js("IDGit._debug.pulseNow(); true")
        self.assertFalse(p.stats()['pulse'])

    def test_11_new_commit_animates_in_and_head_glides(self):
        p = self.page
        self.show()
        old_head = SHOW['head']['hash']
        new = dict(SHOW['commits'][0])
        new.update(hash='f' * 40, short='fffffff', parents=[old_head], subject='A brand new commit', ts=SHOW['commits'][0]['ts'] + 60,
                   refs=[{'name': 'master', 'kind': 'branch', 'current': True}])
        commits = [new] + [dict(c, refs=[r for r in c['refs'] if r['name'] != 'master']) for c in SHOW['commits']]
        data = dict(SHOW, commits=commits, head={'hash': new['hash'], 'branch': 'master', 'detached': False})
        before = p.pos(old_head)
        p.call('setData', data)
        p.pump(0.06)
        a = p.js('IDGit._debug.nodeAlpha(%s)' % json.dumps(new['hash']))
        self.assertLess(a, 0.95, 'new commit fades/slides in')
        self.assertTrue(p.stats()['animating'])
        p.settle()
        self.assertEqual(p.js('IDGit._debug.nodeAlpha(%s)' % json.dumps(new['hash'])), 1)
        hx, _ = p.pos(new['hash'])
        heads = [lb for lb in p.js('IDGit._debug.labels()') if lb['kind'] == 'head']
        self.assertAlmostEqual(heads[0]['x'] + heads[0]['w'] / 2, hx, delta=1.5)
        self.assertIsNotNone(before)
        self.assertEqual(p.stats()['commits'], len(commits))

    def test_12_keyboard_navigation(self):
        p = self.page
        self.show()
        head = SHOW['head']['hash']
        p.key('End')
        self.assertEqual(p.stats()['cursor'], head)
        p.key('ArrowLeft')
        self.assertEqual(p.stats()['cursor'], self.hash_of('Update docs for v0.2'))
        p.key('ArrowLeft')
        p.key('ArrowDown')                        # from the hotfix merge down to the hotfix lane
        self.assertEqual(p.stats()['cursor'], self.hash_of('Hotfix: update the changelog'))
        p.key('ArrowRight')
        self.assertEqual(p.stats()['cursor'], self.hash_of("Merge branch 'hotfix/critical'"))
        p.key('Home')
        self.assertEqual(p.stats()['cursor'], self.hash_of('Initial commit'))
        p.messages.clear()
        p.key('Enter')
        self.assertEqual(p.of_type('open-commit'), [{'type': 'open-commit', 'hash': self.hash_of('Initial commit')}])
        self.assertTrue(p.stats()['tip'])
        p.key('Escape')
        self.assertFalse(p.stats()['tip'])

    def test_13_camera_wheel_fit_toolbar(self):
        p = self.page
        self.show()
        s0 = p.stats()['scale']
        p.wheel(500, 260, -360)
        p.settle()
        self.assertGreater(p.stats()['scale'], s0 * 1.3)
        p.js("document.getElementById('b-fit').click(); true")
        p.settle()
        st = p.stats()
        self.assertLess(st['scale'], s0 * 1.3)
        for lb in p.js('IDGit._debug.labels()'):
            self.assertGreaterEqual(lb['x'], -2)
            self.assertLessEqual(lb['x'] + lb['w'], st['width'] + 2)
        p.messages.clear()
        p.js("document.getElementById('b-refresh').click(); true")
        self.assertEqual(p.of_type('refresh'), [{'type': 'refresh'}])
        n = len(p.js('IDGit._debug.labels()'))
        p.js("document.getElementById('c-tags').click(); true")
        self.assertLess(len(p.js('IDGit._debug.labels()')), n)
        p.js("document.getElementById('c-tags').click(); true")
        x0 = p.stats()['cam']['x']
        p.wheel(500, 260, 0, dx=200)
        self.assertNotEqual(p.stats()['cam']['x'], x0)
        p.js("document.getElementById('b-head').click(); true")
        p.settle()

    def test_14_small_and_large_panes(self):
        p = self.page
        try:
            for w, h in ((420, 220), (2500, 900), (420, 700), (1100, 300)):
                p.resize(w, h)
                self.show()
                st = p.stats()
                self.assertGreaterEqual(st['laneGap'], 34)
                hx, hy = p.pos(SHOW['head']['hash'])
                self.assertTrue(0 < hx < w and 0 < hy < h, (w, h, hx, hy))
                labs = [lb for lb in p.js('IDGit._debug.labels()')]
                for i, a in enumerate(labs):
                    for b in labs[i + 1:]:
                        overlap = a['x'] < b['x'] + b['w'] and b['x'] < a['x'] + a['w'] and a['y'] < b['y'] + b['h'] and b['y'] < a['y'] + a['h']
                        self.assertFalse(overlap, (w, h, a, b))
        finally:
            p.resize(1100, 520)
        self.assertEqual(p.errors(), [])

    def test_15_fps_pan_300_and_1500(self):
        p = self.page
        for n in (300, 1500):
            p.call('setState', 'loading')
            p.js('IDGit.setData(GitGraphCore.synth(%d, 3)); true' % n)
            p.settle(10)
            self.assertEqual(p.stats()['commits'], n)
            p.js('IDGit._debug.setView(IDGit._debug.view().x, IDGit._debug.view().y, 0.6); true')
            st = p.stats()
            cx, cy = st['width'] / 2, st['height'] / 2
            p.pointer('pointerdown', cx, cy)

            def drive(i, cx=cx, cy=cy):
                p.pointer('pointermove', cx - (i % 60) * 6, cy + ((i // 60) % 2) * 3)
            info = p.measure_fps(1.5, drive)
            p.pointer('pointerup', cx, cy, buttons=0)
            self.perf['pan_fps_%d' % n] = round(info['fps'], 1)
            self.perf['pan_p95_ms_%d' % n] = round(info['p95'], 1)
            self.perf['draw_ms_%d' % n] = round(p.stats()['drawMs'], 2)
            self.perf['layout_ms_%d' % n] = round(p.stats()['layoutMs'], 2)

            def zoom(i, cx=cx, cy=cy):
                p.wheel(cx, cy, -40 if (i // 15) % 2 else 40)
            info = p.measure_fps(1.5, zoom)
            self.perf['zoom_fps_%d' % n] = round(info['fps'], 1)
            p.settle(10)
            if n == 1500:                       # worst case: the whole width at minimum zoom
                p.js('IDGit._debug.setView(IDGit._debug.view().x, IDGit._debug.view().y, 0.25); true')
                p.pointer('pointerdown', cx, cy)
                info = p.measure_fps(1.5, drive)
                p.pointer('pointerup', cx, cy, buttons=0)
                self.perf['pan_fps_1500_min_zoom'] = round(info['fps'], 1)
                self.perf['draw_ms_1500_min_zoom'] = round(p.stats()['drawMs'], 2)
                p.settle(10)
        self.assertGreater(self.perf['pan_fps_300'], 30)
        self.assertGreater(self.perf['pan_fps_1500'], 15)
        self.assertEqual(p.errors(), [])

    def test_16_runs_without_bridge(self):
        p = Page(900, 400, query='?demo=80', bridge=False)
        try:
            p.wait_for("IDGit.stats().state === 'data'", 5)
            self.assertEqual(p.stats()['commits'], 80)
            self.assertEqual(p.js('IDGit.outbox[0].type'), 'ready')
            p.call('setData', fixture('gitgraph_deleted_branches.json'))
            p.pump(0.2)
            pills = [lb['text'] for lb in p.js('IDGit._debug.labels()') if lb['kind'] == 'pill']
            self.assertTrue(any(t.startswith('ceo/') for t in pills), pills)
            self.assertEqual(p.js('window.__errs'), [])
        finally:
            p.destroy()


@unittest.skipIf(WK is None or not os.environ.get('GITGRAPH_SHOTS'), 'screenshots only with GITGRAPH_SHOTS=1')
class GitGraphShots(unittest.TestCase):
    def test_shots(self):
        p = Page(1500, 700)
        try:
            def shot(name, theme, data, w, h, after=None):
                p.resize(w, h)
                p.call('setTheme', theme)
                p.call('setState', 'loading')
                p.call('setData', data)
                p.wait_for('!IDGit.stats().animating', 8)
                p.pump(0.2)
                if after:
                    after()
                p.snapshot(name)
            p.resize(1500, 700)
            p.call('setTheme', DARK)
            p.call('setState', 'loading')
            p.call('setData', SHOW)
            p.pump(0.3)
            p.snapshot('gg-dark-reveal-mid')
            shot('gg-dark-1500x700', DARK, SHOW, 1500, 700)
            shot('gg-light-1500x700', LIGHT, SHOW, 1500, 700)
            shot('gg-dark-bottom-1100x300', DARK, SHOW, 1100, 300)
            shot('gg-dark-right-420x700', DARK, SHOW, 420, 700)
            shot('gg-light-right-420x700', LIGHT, SHOW, 420, 700)

            def hover():
                h = [c['hash'] for c in SHOW['commits'] if c['subject'].startswith('Merge pull request')][0]
                x, y = p.pos(h)
                p.pointer('pointermove', x, y, buttons=0)
                p.pump(0.5)
            shot('gg-dark-hover', DARK, SHOW, 1500, 700, hover)
            shot('gg-light-hover', LIGHT, SHOW, 1100, 420, hover)

            def head_close():
                hx, hy = p.pos(SHOW['head']['hash'])
                p.js('IDGit._debug.setView(%f, %f, 2.4); true' % tuple(p.js('[IDGit._debug.view().x + (%f - IDGit.stats().width/2)/IDGit._debug.view().s, IDGit._debug.view().y + (%f - IDGit.stats().height/2)/IDGit._debug.view().s]' % (hx + 40, hy))))
                p.js('IDGit._debug.pulseNow(); true')
                p.pump(1.0)
            shot('gg-dark-head-closeup', DARK, SHOW, 900, 500, head_close)
            shot('gg-dark-deleted-branches', DARK, fixture('gitgraph_deleted_branches.json'), 1500, 700)
            shot('gg-dark-fit', DARK, SHOW, 1500, 700, lambda: (p.js("IDGit._debug.fit(); true"), p.pump(0.2)))
            shot('gg-dark-synth300-fit', DARK, p.js('GitGraphCore.synth(300, 3)'), 1500, 700, lambda: (p.js("IDGit._debug.fit(); true"), p.pump(0.2)))
            p.resize(900, 400)
            for kind in ('not-a-repo', 'empty', 'git-missing', 'loading'):
                p.call('setState', kind)
                p.pump(0.2)
                p.snapshot('gg-state-' + kind)
            p.call('setTheme', DARK)
            p.call('setData', SHOW)
            p.js("IDGit.setData(Object.assign({}, %s, {ok: false, error: 'git-missing'})); true" % json.dumps({'x': 1}))
        finally:
            p.destroy()


if __name__ == '__main__':
    unittest.main()
