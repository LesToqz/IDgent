"""The real commit page, exercised on software WebKit under X11/xvfb.

Fixtures are commit_detail outputs from deterministic repositories made with
GitRepo (tests/gitfixtures.py); reference panesplit.py is the real 294-line file.
Run only this module while iterating. Set COMMIT_SHOTS=1 to refresh visual proof.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import unittest

from tests.test_graph_web import Page as GraphHarness, _webkit

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / 'idgent/gitcommit_web/commit.html'
SHOTS = ROOT / '.ceo/shots'


def fixture(name):
    return json.loads((ROOT / ('tests/fixtures/gitcommit_' + name + '.json')).read_text())


def palette(dark=True):
    from idgent.themes import get_theme
    theme = get_theme('idgent-dark' if dark else 'idgent-light')
    ui = theme.ui
    def tint(color):
        return 'rgba(%d, %d, %d, 0.12)' % tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
    return dict(bg=ui['bg'], fg=ui['editor_fg'], muted=ui['muted'], surface=ui['sidebar_bg'],
                surface2=ui['tab_inactive_bg'], border=ui['border'], hover=ui['hover'],
                accent='#8a5cf5', accent_fg=ui['accent_fg'], add=ui['git_new'],
                **{'del': ui['git_conflict']}, add_bg=tint(ui['git_new']), del_bg=tint(ui['git_conflict']),
                mono='"DejaVu Sans Mono", monospace', ui='sans-serif', size=13, dark=dark,
                syntax={k: v.foreground for k, v in theme.syntax.items()})


def generated_commit(n=300, lines=100):
    data = fixture('reference')
    source = copy.deepcopy(data['files'][1])
    source['hunks'][0]['lines'] = source['hunks'][0]['lines'][:lines]
    source['hunks'][0]['new_lines'] = lines
    source['additions'] = lines
    data['files'] = [dict(copy.deepcopy(source), path='src/module_%03d.py' % i) for i in range(n)]
    data['stats'] = dict(files=n, additions=n * lines, deletions=0)
    return data


WK, WK_REASON = _webkit()


class Page(GraphHarness):
    """Reuse the existing eval/pump/snapshot utilities, loading our own page only."""
    def __init__(self, width=900, height=1000, bridge=True):
        (Gtk, GLib, WebKit2), _ = _webkit()
        self.Gtk, self.GLib, self.WebKit2 = Gtk, GLib, WebKit2
        self.messages = []
        self.ucm = WebKit2.UserContentManager()
        if bridge:
            self.ucm.connect('script-message-received::idgent', self._on_message)
            self.ucm.register_script_message_handler('idgent')
        self.context = WebKit2.WebContext.new_ephemeral()
        self.view = WebKit2.WebView(web_context=self.context, user_content_manager=self.ucm)
        self.view.get_settings().set_hardware_acceleration_policy(WebKit2.HardwareAccelerationPolicy.NEVER)
        self.view.get_settings().set_allow_file_access_from_file_urls(True)
        self.view.get_settings().set_enable_write_console_messages_to_stdout(True)
        self.win = Gtk.Window()
        self.win.set_default_size(width, height)
        self.win.add(self.view)
        self.win.show_all()
        self.view.load_uri(PAGE.as_uri())
        self.wait_for('!!window.IDCommit', 20)

    def call(self, name, *args):
        return self.js('IDCommit.%s(%s)' % (name, ','.join(json.dumps(a) for a in args)))

    def shot(self, name):
        if os.environ.get('COMMIT_SHOTS'):
            self.pump(.3)
            return self.snapshot('commit-' + name)

    def load(self, name):
        data = fixture(name)
        self.call('setCommit', data)
        self.pump(.25)
        return data


class CommitCoreTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'node is not on PATH')
    def test_pure_helpers(self):
        proc = subprocess.run(['node', '--test', str(ROOT / 'tests/js/gitcommit_core.test.mjs')],
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_fixtures_are_real_contract_data(self):
        for name in ('reference', 'gaps', 'edges', 'merge', 'root', 'empty'):
            data = fixture(name)
            self.assertTrue(data['ok'])
            self.assertRegex(data['hash'], r'^[0-9a-f]{40}$')
            self.assertEqual(len(data['files']), data['stats']['files'])
        data = fixture('reference')
        self.assertEqual([f['additions'] for f in data['files']], [3, 294])
        self.assertTrue(fixture('merge')['merge'])
        self.assertEqual(fixture('root')['parents'], [])
        self.assertEqual(fixture('empty')['files'], [])
        self.assertTrue(any(f['too_large'] for f in fixture('edges')['files']))


@unittest.skipIf(WK is None, 'WebKit2 4.1 unavailable: ' + WK_REASON)
class CommitPageTests(unittest.TestCase):
    perf = {}

    @classmethod
    def setUpClass(cls):
        cls.page = Page()

    @classmethod
    def tearDownClass(cls):
        cls.page.destroy()
        SHOTS.mkdir(parents=True, exist_ok=True)
        (SHOTS / 'commit-perf.json').write_text(json.dumps(cls.perf, indent=2) + '\n')
        print('\nCOMMIT PERF', json.dumps(cls.perf))

    def setUp(self):
        self.page.resize(900, 1000)
        self.page.call('setTheme', palette())
        self.page.call('setSettings', {'reduceMotion': False})
        self.page.call('setState', 'loading')
        self.page.messages.clear()

    def tearDown(self):
        self.assertEqual(self.page.of_type('error'), [], self.page.messages)

    def test_01_ready_and_header_actions(self):
        p = self.page
        data = p.load('reference')
        self.assertEqual(p.js("document.querySelector('.author').textContent"), data['author']['name'])
        self.assertIn(data['author']['email'], p.js("document.querySelector('.date-line').textContent"))
        self.assertIn('+05:30', p.js("document.querySelector('.date-line').title"))
        self.assertEqual(p.js("document.querySelector('.subject').textContent"), data['subject'])
        self.assertEqual(p.js("document.querySelectorAll('.committer').length"), 0)
        self.assertIn('+297', p.js("document.querySelector('.totals').textContent"))
        self.assertEqual(p.js("document.querySelector('.file-total').textContent"), '2 files changed')
        p.js("document.querySelector('#copy-sha').click()")
        self.assertEqual(p.of_type('copy')[-1], {'type': 'copy', 'text': data['hash']})
        self.assertIn('Copied', p.js("document.querySelector('#copy-sha').textContent"))
        p.pump(1.25)
        self.assertIn('Commit SHA', p.js("document.querySelector('#copy-sha').textContent"))
        p.js("document.querySelector('.parent').click(); document.querySelector('.ref').click(); document.querySelector('.file-name').click()")
        self.assertEqual(p.of_type('open-commit'), [{'type': 'open-commit', 'hash': data['parents'][0]}, {'type': 'open-commit', 'hash': data['hash']}])
        self.assertEqual(p.of_type('open-file')[-1], {'type': 'open-file', 'path': 'README.md', 'line': 249})
        p.shot('01-reference-dark-900')

    def test_02_rows_syntax_wrap_and_selection(self):
        p = self.page
        data = p.load('reference')
        self.assertEqual(p.js("[...document.querySelectorAll('.file')].map(n => n.dataset.path)"), [f['path'] for f in data['files']])
        p.wait_for("document.querySelectorAll('.file[data-rendered=true]').length === 2")
        self.assertGreater(p.js("document.querySelectorAll('.syntax').length"), 10)
        self.assertEqual(p.js("document.querySelector('.added').dataset.new"), '249')
        self.assertEqual(p.js("document.querySelector('.added .marker').textContent"), '+')
        self.assertEqual(p.js("getComputedStyle(document.querySelector('.file-header')).position"), 'sticky')
        self.assertEqual(p.js("getComputedStyle(document.querySelector('.gutter')).webkitUserSelect"), 'none')
        self.assertEqual(p.js("getComputedStyle(document.querySelector('.code')).whiteSpace"), 'pre-wrap')
        # Copy a selection spanning entire rows; the clipboard payload is code only.
        copied = p.js("""(() => {
          const nodes = [...document.querySelectorAll('.added')].slice(0,3);
          const r = document.createRange(); r.setStartBefore(nodes[0]); r.setEndAfter(nodes[2]);
          const s = getSelection(); s.removeAllRanges(); s.addRange(r);
          const transfer = new DataTransfer();
          document.dispatchEvent(new ClipboardEvent('copy', {clipboardData: transfer, bubbles:true, cancelable:true}));
          const result = transfer.getData('text/plain'); s.removeAllRanges(); return result;
        })()""")
        self.assertIn('Drag between panes', copied)
        self.assertNotIn('249', copied)
        p.resize(520, 900)
        p.shot('02-reference-dark-520')
        self.assertLessEqual(p.js('document.documentElement.scrollWidth'), 520)
        self.assertTrue(p.js("[...document.querySelectorAll('.code')].every(n => n.scrollWidth <= n.clientWidth + 1)"))
        p.call('setTheme', palette(False))
        self.assertEqual(p.js('getComputedStyle(document.body).backgroundColor'), 'rgb(255, 255, 255)')
        p.shot('03-reference-light-520')
        p.resize(900, 1000)
        p.shot('04-reference-light-900')

    def test_03_collapse_keyboard_and_split(self):
        p = self.page
        p.load('reference')
        p.js("document.querySelector('.file-header').dispatchEvent(new KeyboardEvent('keydown', {key:' ',bubbles:true,cancelable:true}))")
        self.assertEqual(p.js("document.querySelector('.file-header').getAttribute('aria-expanded')"), 'false')
        p.js("document.querySelector('#toggle-all').click()")
        self.assertEqual(p.js("document.querySelectorAll('.collapsed').length"), 2)
        p.shot('05-collapsed')
        p.js("document.querySelector('#toggle-all').click()")
        self.assertEqual(p.js("document.querySelectorAll('.collapsed').length"), 0)
        p.js("document.querySelectorAll('.segment button')[1].click()")
        self.assertGreater(p.js("document.querySelectorAll('.split-pair').length"), 0)
        self.assertEqual(p.js("document.querySelectorAll('.segment button')[1].getAttribute('aria-pressed')"), 'true')
        p.shot('06-split')
        p.js("document.querySelectorAll('.segment button')[0].click(); document.querySelector('.file-header').focus(); document.dispatchEvent(new KeyboardEvent('keydown', {key:'j',bubbles:true}))")
        self.assertEqual(p.js('document.activeElement.getAttribute("aria-label")'), 'Toggle changes in idgent/panesplit.py')
        p.js("document.dispatchEvent(new KeyboardEvent('keydown', {key:'k',bubbles:true})); document.dispatchEvent(new KeyboardEvent('keydown', {key:'c',bubbles:true}))")
        self.assertEqual(len(p.of_type('copy')), 1)
        self.assertIn('README', p.js('document.activeElement.getAttribute("aria-label")'))

    def test_04_context_roundtrip_and_offsets(self):
        p = self.page
        data = p.load('gaps')
        self.assertEqual(p.js("document.querySelector('.body').textContent"), data['body'])
        self.assertTrue(p.js("!!document.querySelector('.committer')"))
        # First gap <=40: retrieve the entire gap, then remove the bar.
        p.js("document.querySelector('.gap-label').click()")
        request = p.of_type('expand')[-1]
        self.assertEqual((request['start'], request['end']), (1, 36))
        def answer(req):
            p.call('provideContext', req['path'], req['start'], req['end'],
                   [dict(n=i, text='value_%03d = %d' % (i, i), k=[[0, 5, 'variable']]) for i in range(req['start'], req['end'] + 1)])
        answer(request)
        self.assertEqual(p.js("document.querySelectorAll('.gap-bar').length"), 2)
        self.assertEqual(p.js("document.querySelector('.context-in').dataset.old"), '1')
        # Middle gap follows a net insertion, so old = new - 1.
        p.js("document.querySelector('.gap-bar button[aria-label=\"Expand context below\"]').click()")
        request = p.of_type('expand')[-1]
        self.assertEqual(request['end'] - request['start'], 19)
        p.js("document.querySelector('.gap-bar').scrollIntoView({block:'center'})")
        p.shot('07-context-loading')
        answer(request)
        self.assertEqual(p.js("document.querySelector('.context-in[data-new=\"%d\"]').dataset.old" % request['start']), str(request['start'] - 1))
        p.js("document.querySelector('.gap-bar').scrollIntoView({block:'center'})")
        p.shot('08-context-expanded')
        self.assertIn('Mono', p.js("getComputedStyle(document.querySelector('.context-in .code')).fontFamily"))
        self.assertEqual(p.js("getComputedStyle(document.querySelector('.context-in .code')).fontSize"), '13px')
        p.js("document.querySelector('.gap-bar button[aria-label=\"Expand context above\"]').click()")
        req = p.of_type('expand')[-1]
        self.assertEqual(req['end'] - req['start'], 19)
        # Switch the layout while the host is working; its answer must use the new DOM.
        p.js("document.querySelectorAll('.segment button')[1].click()")
        answer(req)
        self.assertEqual(p.js("document.querySelector('.context-in[data-new=\"%d\"]').dataset.old" % req['start']), str(req['start'] - 1))
        p.js("document.querySelectorAll('.segment button')[0].click()")
        # Repeated top and bottom requests consume rather than duplicate the gap.
        for _ in range(10):
            p.js("document.querySelector('.gap-bar .gap-label').click()")
            req = p.of_type('expand')[-1]
            if req['start'] > 200:
                p.call('provideContext', req['path'], req['start'], req['end'], [])
                break
            answer(req)
        self.assertEqual(p.js("document.querySelectorAll('.gap-bar').length"), 0)
        nums = p.js("[...document.querySelectorAll('.diff-row[data-new]')].map(n=>n.dataset.new).filter(Boolean)")
        self.assertEqual(len(nums), len(set(nums)))
        p.js("document.querySelectorAll('.segment button')[1].click()")
        self.assertEqual(p.js("document.querySelectorAll('.gap-bar').length"), 0)
        self.assertGreater(p.js("document.querySelectorAll('.split-pair').length"), 100)
        p.js("document.querySelectorAll('.segment button')[0].click()")

    def test_05_edge_files_and_long_unicode_lines(self):
        p = self.page
        data = p.load('edges')
        # Open each section explicitly to exercise lazy rendering below the viewport.
        p.js("document.querySelectorAll('.file-header').forEach(n=>{if(n.getAttribute('aria-expanded')==='false')n.click(); else {n.click();n.click();}})")
        self.assertEqual(p.js("document.querySelectorAll('.file').length"), 7)
        text = p.js('document.body.textContent')
        for phrase in ('Binary file not shown', 'Large diff not shown', 'mode 100644 → 100755', 'old.py → renamed.py', 'No newline at end of file'):
            self.assertIn(phrase, text)
        self.assertEqual(p.js("document.querySelector('.file[data-status=deleted] .marker').textContent"), '-')
        self.assertEqual(p.js("document.querySelector('.file[data-status=deleted]').querySelectorAll('.gap').length"), 0)
        self.assertEqual(p.js("document.querySelector('.file[data-path=\"image.dat\"]').querySelectorAll('.gap').length"), 0)
        self.assertGreater(p.js("document.querySelectorAll('.emphasis').length"), 0)
        p.shot('09-edge-files')
        p.resize(520, 900)
        p.js("document.querySelector('.file:last-child').scrollIntoView()")
        p.pump(.3)
        self.assertLessEqual(p.js('document.documentElement.scrollWidth'), 520)
        self.assertTrue(p.js("[...document.querySelectorAll('.code')].every(n=>n.scrollWidth<=n.clientWidth+1)"))
        p.shot('10-unicode-wrap-520')
        self.assertIn('😀 café', p.js("document.querySelector('.file:last-child .code').textContent"))

    def test_06_merge_root_and_empty(self):
        p = self.page
        data = p.load('merge')
        self.assertEqual(p.js("document.querySelectorAll('.parent').length"), 2)
        self.assertIn(data['diff_base'][:7], p.js("document.querySelector('.banner').textContent"))
        p.shot('11-merge')
        p.load('root')
        self.assertIn('Root commit - showing the whole tree', p.js('document.body.textContent'))
        p.load('empty')
        self.assertIn('This commit has no file changes.', p.js('document.body.textContent'))

    def test_07_states_and_reduced_motion(self):
        p = self.page
        p.call('setState', 'loading')
        self.assertEqual(p.js("document.querySelectorAll('.skeleton-file').length"), 2)
        p.shot('12-loading')
        p.resize(520, 900)
        p.call('setSettings', {'reduceMotion': True})
        self.assertEqual(p.js("getComputedStyle(document.querySelector('.skeleton-line'), '::after').animationName"), 'none')
        p.call('setState', 'error', 'Git could not read this repository.')
        self.assertIn('Git could not read', p.js('document.body.textContent'))
        self.assertIn('Refresh', p.js('document.body.textContent'))
        p.shot('13-error-520')
        p.call('setState', 'not-found')
        self.assertIn('Commit not found', p.js('document.body.textContent'))
        p.load('reference')
        self.assertEqual(p.js("getComputedStyle(document.querySelector('.file-body')).transitionDuration"), '0s')
        self.assertEqual(p.js("getComputedStyle(document.querySelector('.file')).animationName"), 'none')

    def test_08_standalone_and_ready_once(self):
        p = Page(520, 900, bridge=False)
        try:
            p.call('setTheme', palette(False))
            p.load('reference')
            p.js("document.querySelector('#copy-sha').click(); document.querySelector('.gap-label').click()")
            self.assertIn('Open in IDgent', p.js('document.body.textContent'))
            self.assertEqual(p.messages, [])
            self.assertEqual(p.js('document.querySelectorAll("script[src]").length'), 2)
        finally:
            p.destroy()
        p = Page(400, 400)
        try:
            self.assertEqual(len(p.of_type('ready')), 1)
            p.call('setTheme', {})
            p.call('setSettings', {})
            self.assertEqual(p.of_type('error'), [])
        finally:
            p.destroy()

    def test_09_lazy_render_and_performance(self):
        p = self.page
        p.call('setSettings', {'reduceMotion': True})
        data = generated_commit(50, 100)
        # Two rAF boundaries include the first paint opportunity, after setCommit.
        p.js('window.__start=performance.now()')
        p.call('setCommit', data)
        p.js('requestAnimationFrame(()=>requestAnimationFrame(()=>window.__paint=performance.now()-window.__start))')
        p.wait_for('window.__paint > 0')
        paint = p.js('window.__paint')
        self.perf['50_files_5000_lines_first_paint_ms'] = round(paint, 2)
        self.assertLess(paint, 1000, 'Generous CI budget; target is 400ms')
        self.assertEqual(p.js("document.querySelectorAll('.collapsed').length"), 50)
        self.assertEqual(p.js("document.querySelectorAll('[data-rendered=true]').length"), 0)
        p.js("document.querySelector('.file-header').click()")
        self.assertEqual(p.js("document.querySelectorAll('[data-rendered=true]').length"), 1)
        p.call('setCommit', generated_commit(300, 100))
        self.assertEqual(p.js("document.querySelectorAll('.file').length"), 300)
        p.js("""window.__scrollPerf=null; (()=>{
          let frames=[], i=0;
          function frame(t) {
            frames.push(t); scrollTo(0,(i%100)*110); i++;
            if(i<120) requestAnimationFrame(frame);
            else {const d=frames.slice(1).map((v,i)=>v-frames[i]).sort((a,b)=>a-b);
              window.__scrollPerf={fps:1000/((frames.at(-1)-frames[0])/(frames.length-1)),p95:d[Math.floor(d.length*.95)]};}
          } requestAnimationFrame(frame);
        })()""")
        p.wait_for('window.__scrollPerf !== null', 15)
        result = p.js('window.__scrollPerf')
        self.perf['300_files_scroll_fps'] = round(result['fps'], 2)
        self.perf['300_files_scroll_p95_ms'] = round(result['p95'], 2)
        self.assertGreater(result['fps'], 40, 'Software-rendered CI budget')
        # Small commits reserve body space but render only nearby file bodies.
        p.call('setCommit', generated_commit(30, 100))
        p.pump(.3)
        count = p.js("document.querySelectorAll('[data-rendered=true]').length")
        self.assertGreater(count, 0)
        self.assertLess(count, 8)
        p.js("document.querySelector('.file:last-child').scrollIntoView()")
        p.wait_for("document.querySelector('.file:last-child').dataset.rendered === 'true'")

    def test_10_theme_contrast_and_syntax_offsets(self):
        import re
        from idgent.themes import _contrast
        p = self.page
        data = fixture('reference')
        data['files'] = [data['files'][1]]
        data['files'][0]['hunks'][0]['lines'] = [
            dict(t='+', o=None, n=1, text='😀 keyword café', k=[[2, 9, 'keyword']]),
            dict(t='-', o=1, n=None, text='😀 comment café', k=[[2, 9, 'comment']]),
        ]
        p.call('setCommit', data)
        self.assertEqual(p.js("document.querySelector('.syntax').textContent"), 'keyword')
        for dark in (True, False):
            p.call('setTheme', palette(dark))
            colors = p.js("""[...document.querySelectorAll('.syntax')].map(n=>[
              getComputedStyle(n).color, getComputedStyle(n.closest('.diff-row')).backgroundColor,
              getComputedStyle(document.body).backgroundColor])""")
            def rgb(value):
                return [float(v) for v in re.findall(r'[\d.]+', value)]
            def hexcolor(values):
                return '#' + ''.join('%02x' % round(v) for v in values)
            for ink, tint, background in colors:
                t, b = rgb(tint), rgb(background)
                blended = [t[i] * t[3] + b[i] * (1 - t[3]) for i in range(3)]
                self.assertGreaterEqual(_contrast(hexcolor(rgb(ink)), hexcolor(blended)), 4.49)
        p.call('setTheme', dict(palette(), syntax={}))
        self.assertEqual(p.js("getComputedStyle(document.querySelector('.syntax')).color"),
                         p.js("getComputedStyle(document.querySelector('.code')).color"))


if __name__ == '__main__':
    unittest.main()
