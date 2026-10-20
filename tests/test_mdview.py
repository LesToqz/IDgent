"""Pure Markdown parsing, table cells and safe link resolution."""
from pathlib import Path
import tempfile
import unittest

from idgent import mdview


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

    def test_tables_preserve_cells_headers_and_alignment(self):
        blocks = list(mdview.parse_blocks('| A | Bee | C |\n| :--- | :---: | ---: |\n| longer | x | 1 |\n| end | y | 2 |'))
        self.assertTrue(all(b.kind == 'table' for b in blocks))
        self.assertEqual([b.header for b in blocks], [True, False, False])
        self.assertEqual(blocks[0].cells, ('A', 'Bee', 'C'))
        self.assertEqual(blocks[1].cells, ('longer', 'x', '1'))
        self.assertEqual(blocks[2].cells, ('end', 'y', '2'))
        self.assertTrue(all(b.alignments == ('left', 'center', 'right') for b in blocks))
        long_table = list(mdview.parse_blocks('| A | B |\n| --- | --- |\n' + '| row | cell |\n' * 300))
        self.assertEqual(len(long_table), 301)
        self.assertTrue(all(b.cells == ('row', 'cell') for b in long_table[1:]))
        wide_table = list(mdview.parse_blocks('| A | B |\n| --- | --- |\n| ' + 'x' * 1000 + ' | tail |'))
        self.assertEqual(wide_table[1].cells, ('x' * 1000, 'tail'))

    def test_table_escaped_pipes_and_optional_outer_separators(self):
        blocks = list(mdview.parse_blocks('A | B\n--- | ---:\nleft \\| middle | `right \\| end`\nonly |'))
        self.assertEqual(blocks[1].cells, (r'left \| middle', r'`right \| end`'))
        self.assertEqual(''.join(s.text for s in mdview.parse_inline(blocks[1].cells[0])), 'left | middle')
        self.assertEqual(blocks[2].cells, ('only', ''))
        self.assertEqual(mdview._cells(r'| a\\ | b |'), [r'a\\', 'b'])
        self.assertFalse(any(b.kind == 'table' for b in mdview.parse_blocks('| A | B | C |\n| --- | --- |')))

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

    def test_wiki_note_resolution_and_alias(self):
        spans = mdview.parse_inline('[[note]] [[note|alias]] https://example.org/a_b_c.')
        self.assertEqual([span.text for span in spans if span.link], ['note', 'alias', 'https://example.org/a_b_c'])
        self.assertEqual([span.link for span in spans if span.link], ['[[note]]', '[[note]]', 'https://example.org/a_b_c'])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            notes = root / '.IDgent' / 'Notes'
            notes.mkdir(parents=True)
            target = notes / 'other.md'
            target.touch()
            source = str(notes / 'source.md')
            self.assertEqual(mdview.link_target('[[other]]', source), ('file', str(target)))
            self.assertEqual(mdview.link_target('[[other|alias]]', source), ('file', str(target)))
            self.assertIsNone(mdview.link_target('[[missing]]', source))
            self.assertIsNone(mdview.link_target('[[/etc/passwd]]', source))
