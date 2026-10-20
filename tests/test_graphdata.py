import json
import os
from pathlib import Path
import tempfile
import time
import unittest

from idgent.graphdata import GraphIndex, GraphOptions, match_query


class GraphTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.index = GraphIndex(str(self.root))

    def write(self, path, text=''):
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text)
        return file

    def ids(self, graph):
        return {n.id for n in graph.nodes}

    def test_resolution(self):
        for path in ['a/Source.md', 'a/Note.md', 'Note.md', 'long/path/Note.md', 'some/X.md', 'a/space name.md', 'plain', 'a/other.markdown']:
            self.write(path)
        self.write('alias.md', '---\naliases: [Nickname, "Other Name"]\n---\n')
        self.index.scan()
        for target, kind, expected in [('note', 'wiki', 'a/Note.md'), ('some/x', 'wiki', 'some/X.md'), ('space%20name.md', 'md', 'a/space name.md'), ('../Note.md', 'md', 'Note.md'), ('nickname', 'wiki', 'alias.md'), ('plain', 'wiki', 'plain'), ('other', 'wiki', 'a/other.markdown')]:
            self.assertEqual(self.index.resolve('a/Source.md', target, kind), expected)
        self.assertIsNone(self.index.resolve('a/Source.md', '../../../escape.md', 'md'))
        self.assertIsNone(self.index.resolve('a/Source.md', 'https://x', 'wiki'))
        self.assertEqual(self.index.resolve('else/Source.md', 'NOTE', 'wiki'), 'Note.md')
        self.write('a/Source.md', '[[Note]] [[Note]] [[Source]]')
        self.index.update_path('a/Source.md')
        graph = self.index.build()
        self.assertEqual(len(graph.stats['ambiguous']), 2)
        link = next(e for e in graph.links if e.target == 'a/Note.md')
        self.assertEqual(link.count, 2)
        self.assertTrue(next(e for e in graph.links if e.source == e.target).self)

    def test_toggles_and_stats(self):
        self.write('A.md', '[[B]] ![[pic.png]] [[Missing]] #a/b')
        self.write('B.md')
        self.write('orphan.md')
        self.write('code.py')
        self.write('pic.png').write_bytes(b'\x89PNG\0\xff')
        self.write('unused.txt', 'unused')
        self.index.scan()
        graph = self.index.build(GraphOptions(existing_only=False))
        self.assertEqual(self.ids(graph), {'A.md', 'B.md', 'orphan.md', 'code.py', 'pic.png', '?Missing', '#a/b'})
        self.assertEqual(graph.stats['counts']['note'], 3)
        self.assertEqual(graph.stats['unresolved'], ['?Missing'])
        self.assertEqual(graph.stats['orphans'], 2)
        self.assertEqual(graph.stats['components'], 3)
        graph = self.index.build(GraphOptions(show_tags=False, show_attachments=False, show_code=False, show_orphans=False))
        self.assertEqual(self.ids(graph), {'A.md', 'B.md'})
        self.assertEqual(next(n for n in graph.nodes if n.id == 'A.md').out_degree, 1)
        self.assertEqual(self.index.build(GraphOptions(query='nonsense')).nodes, [])

    def test_query_and_groups(self):
        self.write('folder/Alpha.md', '---\ntags: [planet/rocky]\n---\nbright red planet [[Beta]] [[pic.png]] [[Missing]]')
        self.write('Beta.md', 'blue planet')
        self.write('pic.png')
        self.index.scan()
        item = {'path': 'folder/Alpha.md', 'text': 'bright red planet', 'tags': ['planet/rocky']}
        for query in ['PATH:FOLDER file:Alpha', 'tag:#planet', '"red planet" -blue', 'nothing OR content:bright', '/RED.*planet/', 'content:"red planet"']:
            self.assertTrue(match_query(item, query), query)
        for query in ['tag:plan', 'bright blue', '-planet', '/[/', 'nothing OR absent']:
            self.assertFalse(match_query(item, query), query)
        graph = self.index.build(GraphOptions(query='file:alpha', existing_only=False, groups=[{'query': 'tag:planet', 'color': '#f00'}, {'query': '', 'color': '#fff'}]))
        self.assertEqual(self.ids(graph), {'folder/Alpha.md', '#planet/rocky', 'pic.png', '?Missing'})
        self.assertEqual(next(n for n in graph.nodes if n.id == 'folder/Alpha.md').group, 0)
        self.assertEqual(next(n for n in graph.nodes if n.id == 'pic.png').group, 1)

    def test_code(self):
        self.write('pkg/main.py', 'import root\nimport util\nimport module\nfrom . import sibling\nfrom .. import outer\nimport external')
        for path in ['root.py', 'pkg/util.py', 'src/module/__init__.py', 'pkg/sibling.py', 'outer.py']:
            self.write(path)
        self.write('web/main.ts', 'import x from "./mod"; import "external"; require("./sub"); import "./style.css"')
        self.write('web/mod.ts')
        self.write('web/sub/index.js')
        self.write('web/style.css', '@import "other.css"; a {x:url(img.png)}')
        self.write('web/other.css')
        self.write('web/img.png')
        self.write('web/page.html', '<img src="img.png"><a href="../A.md">')
        self.write('A.md')
        self.index.scan()
        graph = self.index.build()
        self.assertEqual({e.target for e in graph.links if e.source == 'pkg/main.py'}, {'root.py', 'pkg/util.py', 'src/module/__init__.py', 'pkg/sibling.py', 'outer.py'})
        self.assertEqual({e.target for e in graph.links if e.source == 'web/main.ts'}, {'web/mod.ts', 'web/sub/index.js', 'web/style.css'})
        self.assertTrue(all(e.kind == 'import' for e in graph.links))
        self.assertEqual({e.target for e in graph.links if e.source == 'web/style.css'}, {'web/other.css', 'web/img.png'})
        self.assertEqual(self.ids(self.index.build(GraphOptions(show_code=False))), {'A.md'})

    def test_markdown_embeds_are_relative(self):
        self.write('pic.png')
        self.write('notes/pic.png')
        self.write('notes/A.md', '![image](pic.png) ![[pic.png]] [B](../B.md)')
        self.write('B.md')
        self.index.scan()
        graph = self.index.build()
        self.assertEqual({e.target for e in graph.links}, {'notes/pic.png', 'B.md'})
        self.assertEqual(next(e.count for e in graph.links if e.kind == 'embed'), 2)

    def test_local(self):
        for path, text in [('A.md', '[[B]]'), ('B.md', '[[C]]'), ('C.md', ''), ('D.md', '[[A]]')]:
            self.write(path, text)
        self.index.scan()
        self.assertEqual(self.ids(self.index.local('A.md')), {'A.md', 'B.md', 'D.md'})
        self.assertEqual(self.ids(self.index.local('A.md', 2)), {'A.md', 'B.md', 'C.md', 'D.md'})
        self.assertEqual(self.ids(self.index.local('A.md', incoming=False)), {'A.md', 'B.md'})
        graph = self.index.local('A.md', outgoing=False)
        self.assertEqual(self.ids(graph), {'A.md', 'D.md'})
        self.assertEqual(graph.stats['focus'], 'A.md')
        self.assertEqual(self.index.local('missing.md').nodes, [])

    def test_folder_keeps_only_files_below_prefix(self):
        self.write('.IDgent/Notes/a.md', '[[b]] [[outside]] #tag')
        self.write('.IDgent/Notes/b.md', '')
        self.write('outside.md', '[[a]]')
        self.index.scan()
        graph = self.index.folder('.IDgent/Notes')
        self.assertEqual(self.ids(graph), {'.IDgent/Notes/a.md', '.IDgent/Notes/b.md'})
        self.assertEqual([(l.source, l.target) for l in graph.links],
                         [('.IDgent/Notes/a.md', '.IDgent/Notes/b.md')])
        self.assertEqual(len(self.index.build().nodes), 4)
        self.assertEqual(self.index.folder('missing').nodes, [])

    def test_empty_graph(self):
        self.write('a.md')
        self.index.scan()
        graph = self.index.empty()
        self.assertEqual((graph.nodes, graph.links), ([], []))
        self.assertEqual(graph.to_json()['nodes'], [])

    def test_incremental_and_buffers(self):
        file = self.write('A.md', '[[B]]')
        self.index.scan()
        self.assertFalse(self.index.update_path('A.md'))
        self.write('B.md')
        self.assertTrue(self.index.update_path('B.md'))
        self.assertEqual(len(self.index.build().links), 1)
        self.index.set_text('A.md', '')
        self.assertIn('A.md', self.ids(self.index.build()))
        self.assertEqual(self.index.build().links, [])
        self.index.set_text('A.md', '#tag [[C]]')
        self.assertIn('#tag', self.ids(self.index.build()))
        file.write_text('[[B]] #disk')
        self.assertFalse(self.index.update_path('A.md'))
        self.index.set_text('A.md', None)
        self.assertIn('#disk', self.ids(self.index.build()))
        file.unlink()
        self.assertTrue(self.index.update_path('A.md'))
        self.assertEqual(self.ids(self.index.build()), {'B.md'})
        self.index.set_text('New.md', '[[B]]')
        self.index.scan()
        self.assertIn('New.md', self.ids(self.index.build()))
        self.index.set_text('New.md', None)
        self.assertNotIn('New.md', self.ids(self.index.build()))
        self.assertFalse(self.index.update_path('../outside.md'))

    def test_alias_index_updates_and_missing_attachment(self):
        self.write('A.md', '[[Old]] [[absent.png]]')
        alias = self.write('B.md', '---\nalias: Old\n---\n')
        self.index.scan()
        self.assertEqual(self.index.resolve('A.md', 'Old', 'wiki'), 'B.md')
        alias.write_text('---\nalias: New\n---\n')
        self.index.update_path('B.md')
        self.assertIsNone(self.index.resolve('A.md', 'Old', 'wiki'))
        self.assertEqual(self.index.resolve('A.md', 'New', 'wiki'), 'B.md')
        self.assertIn('?absent.png', self.ids(self.index.build(GraphOptions(existing_only=False))))
        self.assertNotIn('?absent.png', self.ids(self.index.build(GraphOptions(existing_only=False, show_attachments=False))))

    def test_scan_safety(self):
        self.write('.git/ignored.md')
        self.write('binary.md').write_bytes(b'a\0b')
        self.write('large.md', 'a' * (1024 * 1024 + 1))
        self.write('Good.md')
        (self.root / 'loop').symlink_to(self.root, target_is_directory=True)
        (self.root / 'link.md').symlink_to(self.root / 'Good.md')
        self.index.scan()
        self.assertEqual(self.ids(self.index.build()), {'Good.md'})

    def test_truncation_and_json(self):
        self.write('A.md', '[[B]] [[C]] [[D]]')
        for name in 'BCD':
            self.write(name + '.md')
        self.index.scan()
        self.index.max_nodes = 2
        graph = self.index.build()
        self.assertTrue(graph.truncated)
        self.assertEqual(self.ids(graph), {'A.md', 'B.md'})
        self.assertEqual(graph.nodes[0].out_degree, 1)
        value = graph.to_json()
        self.assertEqual(value, self.index.build().to_json())
        json.dumps(value)
        for link in value['links']:
            self.assertLess(link['s'], len(value['nodes']))
            self.assertLess(link['t'], len(value['nodes']))
        self.assertEqual(value['nodes'][0]['id'], 'A.md')

    def test_performance(self):
        start = time.perf_counter()
        sum(i * i for i in range(100_000))
        calibration = time.perf_counter() - start
        if calibration > 8:
            self.skipTest('environment calibration exceeded 8 seconds')
        for i in range(5000):
            self.write(f'n{i}.md', f'[[n{(i + 1) % 5000}]] #tag')
        start = time.perf_counter()
        self.index.scan()
        self.assertLess(time.perf_counter() - start, 3)
        start = time.perf_counter()
        self.index.build()
        self.assertLess(time.perf_counter() - start, .2)
        self.write('n0.md', '[[n2]]')
        start = time.perf_counter()
        self.index.update_path('n0.md')
        self.assertLess(time.perf_counter() - start, .02)

    @unittest.skipUnless(os.path.isdir('/home/lestoq/projects/demo'), 'optional demo vault absent')
    def test_demo_smoke(self):
        index = GraphIndex('/home/lestoq/projects/demo')
        index.scan()
        graph = index.build(GraphOptions(existing_only=False))
        self.assertGreater(graph.stats['counts']['note'], 40)
        self.assertTrue(graph.stats['unresolved'])
        self.assertIn('#planet/rocky', self.ids(graph))
        self.assertGreaterEqual(next(n for n in graph.nodes if n.label == 'Space Index').out_degree, 20)
