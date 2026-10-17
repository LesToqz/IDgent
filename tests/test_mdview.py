"""Pure subset parsing, inert resources, incremental GTK painting and links."""
import os
os.environ['GDK_BACKEND'] = 'x11'
from pathlib import Path
import tempfile
import time
import unittest
import warnings
from unittest.mock import patch

from idgent import mdview, highlight, gutter
from gi.repository import Gtk, GLib


def drain(predicate=lambda: False, timeout=3):
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', category=DeprecationWarning, module=r'gi\.events')
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if predicate():
                return
            if Gtk.events_pending():
                Gtk.main_iteration_do(False)
            else:
                time.sleep(.002)
    raise AssertionError('GTK operation did not complete')


class ParserTest(unittest.TestCase):
    def test_headings_and_rules(self):
        blocks = list(mdview.parse_blocks('# One\n\nTwo\n===\n\nThree\n---\n\n###### Six\n\n---'))
        self.assertEqual([(b.kind, b.level) for b in blocks],
                         [('heading', 1), ('heading', 1), ('heading', 2), ('heading', 6), ('rule', 0)])
        self.assertEqual(blocks[0].text, 'One')

    def test_inline_styles_and_nesting(self):
        spans = mdview.parse_inline('**bold _nested_** *italic* ~~gone~~ `a * b` ***both***')
        self.assertIn(mdview.Span('nested', ('strong', 'emphasis')), spans)
        self.assertIn(mdview.Span('a * b', ('code',)), spans)
        self.assertIn(mdview.Span('gone', ('strike',)), spans)
        self.assertIn(mdview.Span('both', ('strong', 'emphasis')), spans)

    def test_escapes_hard_and_soft_breaks(self):
        spans = mdview.parse_inline('\\*literal\\* soft\nline  \nhard\\\nbreak')
        self.assertEqual(''.join(s.text for s in spans), '*literal* soft line\nhard\nbreak')
        self.assertTrue(all(not s.styles for s in spans))

    def test_nested_quotes_lists_tasks(self):
        blocks = list(mdview.parse_blocks('> outer\n>> inner\n\n- parent\n  1. child\n    - [x] done\n- [ ] todo'))
        self.assertEqual([b.quote for b in blocks[:2]], [1, 2])
        self.assertEqual([(b.indent, b.marker) for b in blocks[2:]], [(0, '•'), (2, '1.'), (4, '☑'), (0, '☐')])

    def test_fenced_and_indented_code(self):
        blocks = list(mdview.parse_blocks('```python\ndef f():\n    pass\n```\n\n    *literal*\n\n~~~js\nlet x = 1;\n~~~'))
        self.assertEqual([b.kind for b in blocks], ['codeblock'] * 3)
        self.assertEqual(blocks[0].text, 'def f():\n    pass\n')
        self.assertEqual(blocks[0].language, 'python')
        self.assertEqual(blocks[1].text, '*literal*\n')
        self.assertEqual(blocks[2].language, 'js')

    def test_code_preserves_quote_characters_and_list_continuations(self):
        blocks = list(mdview.parse_blocks('```\n> literal\n```\n\n> ```\n> > literal\n> ```'))
        self.assertEqual([b.text for b in blocks], ['> literal\n', '> literal\n'])
        blocks = list(mdview.parse_blocks('- first\n  continuation\n  - child'))
        self.assertEqual(blocks[0].text, 'first\ncontinuation')
        self.assertEqual(blocks[1].indent, 2)

    def test_tables_are_aligned(self):
        blocks = list(mdview.parse_blocks('| A | Bee |\n| --- | :---: |\n| longer | x |'))
        self.assertTrue(all(b.kind == 'table' for b in blocks))
        rows = [b.text.rstrip('\n') for b in blocks]
        self.assertEqual(len(rows[0]), len(rows[-1]))
        self.assertIn('longer', rows[-1])
        self.assertIn('─', rows[1])
        long_table = list(mdview.parse_blocks('| A | B |\n| --- | --- |\n' + '| row | cell |\n' * 300))
        self.assertTrue(all(b.kind == 'table' for b in long_table))
        self.assertEqual(sum('row' in b.text for b in long_table), 300)
        wide_table = list(mdview.parse_blocks('| A | B |\n| --- | --- |\n| ' + 'x' * 1000 + ' | tail |'))
        self.assertEqual(sum(b.text.count('x') for b in wide_table), 1000)

    def test_raw_html_and_images_remain_inert(self):
        spans = mdview.parse_inline('<script>alert(1)</script> ![a cat](https://invalid/image.png)')
        self.assertEqual(''.join(s.text for s in spans), '<script>alert(1)</script> [image: a cat]')
        self.assertFalse(any(s.link for s in spans))

    def test_links_autolinks_and_safe_targets(self):
        spans = mdview.parse_inline('[web](https://example.org/a_(b)) <https://example.org> [mail](mailto:x@y.z)')
        self.assertEqual([s.link for s in spans if s.link], ['https://example.org/a_(b)', 'https://example.org', 'mailto:x@y.z'])
        for value in ('javascript:alert(1)', 'file:///etc/passwd', 'ftp://example.org', '//example.org/x', '/etc/passwd', '#part', 'https:\n//x', 'https://[bad'):
            self.assertIsNone(mdview.link_target(value, '/tmp/a.md'), value)
        self.assertEqual(mdview.link_target('https://example.org', '/tmp/a.md'), ('uri', 'https://example.org'))
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / 'some file.md'
            p.touch()
            self.assertEqual(mdview.link_target('some%20file.md#heading', str(Path(directory) / 'source.md')), ('file', str(p)))
            self.assertIsNone(mdview.link_target('missing.md', str(p)))

    def test_large_blocks_are_bounded(self):
        for text in ('a' * 200000, '```\n' + 'x' * 200000 + '\n```', '    ' + 'x' * 200000):
            blocks = list(mdview.parse_blocks(text))
            self.assertGreater(len(blocks), 10)
            self.assertLessEqual(max(len(b.text) for b in blocks), mdview.CHUNK * 2)


class RendererTest(unittest.TestCase):
    def setUp(self):
        self.opened = []
        self.preview = mdview.MarkdownView(self.opened.append, lambda: '/tmp/source.md')
        self.preview.set_style(highlight.DARK_PLUS, gutter.DEFAULT_CHROME, 11)
        self.addCleanup(self.preview.view.destroy)

    def render(self, text):
        self.preview.render(text)
        drain(lambda: self.preview._idle == 0)
        b = self.preview.buffer
        return b.get_text(b.get_start_iter(), b.get_end_iter(), True)

    def test_rendered_text_tags_and_readonly(self):
        text = self.render('# Title\n\n**strong** and `code`\n\n> quote\n\n---')
        self.assertIn('Title', text)
        self.assertIn('strong and code', text)
        self.assertNotIn('**', text)
        self.assertIn('─', text)
        self.assertFalse(self.preview.view.get_editable())
        at = self.preview.buffer.get_iter_at_offset(text.index('strong'))
        self.assertTrue(at.has_tag(self.preview.tags['strong']))
        self.assertFalse(self.preview.tags['rule'].get_property('strikethrough'))
        self.assertLess(self.preview.tags['container-0-0'].get_priority(), self.preview.tags['strong'].get_priority())

    def test_optional_code_highlighting(self):
        self.render('```python\ndef f():\n    return 123\n```')
        if highlight.AVAILABLE:
            b = self.preview.buffer
            self.assertTrue(b.get_iter_at_offset(0).has_tag(self.preview.tags['syntax-keyword']) or
                            b.get_iter_at_offset(0).has_tag(self.preview.tags['syntax-control']))
        with patch.object(highlight, 'AVAILABLE', False):
            self.assertIn('return 123', self.render('```python\nreturn 123\n```'))

    def test_links_open_only_allowed_destinations(self):
        with patch.object(Gtk, 'show_uri_on_window') as show:
            self.assertTrue(self.preview.activate_link('https://example.org'))
            self.assertTrue(self.preview.activate_link('mailto:a@b.c'))
            self.assertFalse(self.preview.activate_link('javascript:alert(1)'))
            self.assertEqual(show.call_count, 2)
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / 'target.md'
            p.touch()
            self.preview.source_path = lambda: str(Path(directory) / 'source.md')
            self.assertTrue(self.preview.activate_link('target.md'))
            self.assertEqual(self.opened, [str(p)])

    def test_single_click_opens_link_without_control(self):
        self.render('[web](https://example.org)')
        from types import SimpleNamespace
        event = SimpleNamespace(button=1, x=4, y=4)
        with patch.object(self.preview.view, 'window_to_buffer_coords', return_value=(0, 0)), \
             patch.object(self.preview.view, 'get_iter_at_location', return_value=(True, self.preview.buffer.get_start_iter())), \
             patch.object(Gtk, 'show_uri_on_window') as show:
            self.assertTrue(self.preview._release(self.preview.view, event))
            show.assert_called_once()
            self.preview.buffer.select_range(self.preview.buffer.get_start_iter(), self.preview.buffer.get_end_iter())
            self.assertFalse(self.preview._release(self.preview.view, event))

    def test_resources_never_fetched(self):
        with patch('urllib.request.urlopen', side_effect=AssertionError('network')), patch.object(Gtk, 'show_uri_on_window') as show:
            text = self.render('![secret](https://example.org/secret.png) <img src="https://example.org/a">')
            self.assertIn('[image: secret]', text)
            self.assertIn('<img src=', text)
            show.assert_not_called()

    def test_live_style_zoom_and_cancellation(self):
        self.render('# Heading\n\n`code`')
        styles = dict(highlight.DARK_PLUS, heading=highlight.SyntaxStyle('#abcdef', True))
        self.preview.set_style(styles, dict(gutter.DEFAULT_CHROME, current_line_bg='#123456'), 17)
        self.assertAlmostEqual(self.preview.tags['h1'].get_property('foreground-rgba').red, 0xab / 255)
        self.assertAlmostEqual(self.preview.tags['code'].get_property('background-rgba').red, 0x12 / 255)
        self.assertEqual(self.preview.size, 17)
        self.preview.render('word\n\n' * 10000)
        self.assertNotEqual(self.preview._idle, 0)
        self.preview.destroy()
        self.assertEqual(self.preview._idle, 0)
        self.assertIsNone(self.preview._work)

    def test_large_document_yields_and_completes(self):
        self.preview.render(('# heading\n\nparagraph *text*\n\n' * 5000))
        started = time.perf_counter()
        self.preview._step()
        self.assertLess(time.perf_counter() - started, .1)
        self.assertNotEqual(self.preview._idle, 0)
        drain(lambda: self.preview._idle == 0, timeout=15)
        self.assertGreater(self.preview.buffer.get_char_count(), 100000)
