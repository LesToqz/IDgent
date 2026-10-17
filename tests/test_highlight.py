"""Token-kind mapping, lexer detection and Highlighter/EditorPanel integration (run under xvfb)."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

# Keep GTK on the Xvfb display even when WAYLAND_DISPLAY is inherited.
os.environ["GDK_BACKEND"] = "x11"

from idgent import editor, highlight
from gi.repository import GLib, Gtk

needs_pygments = unittest.skipUnless(highlight.AVAILABLE, 'Pygments is not installed')


def kinds(name, source):
    """Return kind(needle, nth=0): the kind painted over the nth occurrence of needle."""
    lexer = highlight.lexer_for(name, source.split('\n')[0])
    runs = list(highlight.classify(lexer, source))

    def kind(needle, nth=0):
        start = -1
        for _ in range(nth + 1):
            start = source.index(needle, start + 1)
        return next((k for s, e, k in runs if s <= start < e), None)
    return kind


def settle(*highlighters, timeout=30):
    deadline = time.monotonic() + timeout
    context = GLib.MainContext.default()
    while any(h.pending for h in highlighters):
        if time.monotonic() > deadline:
            raise AssertionError('highlighting did not settle')
        if not context.iteration(False):
            time.sleep(0.001)


def painted(buffer):
    """Map each hl-tagged character offset to its tag name (small buffers only)."""
    result = {}
    for offset in range(buffer.get_char_count()):
        names = [tag.get_property('name') for tag in buffer.get_iter_at_offset(offset).get_tags()
                 if (tag.get_property('name') or '').startswith('hl-')]
        if names:
            result[offset] = names[0]
    return result


def tag_at(buffer, offset):
    names = [tag.get_property('name') for tag in buffer.get_iter_at_offset(offset).get_tags()]
    return next((name for name in names if name.startswith('hl-')), None)


PYTHON = '''#!/usr/bin/env python3
@app.route("/x")
class Foo(Base):
    """Docstring."""
    LIMIT = 0x1F + 2.5

    def method(self, value, flag=None):
        # a comment
        text = f"v={value!r}\\n" + 'plain'
        for item in range(3):
            if flag and not item:
                return len(text) + self.count(value)
        raise ValueError("bad")
'''


@needs_pygments
class KindMappingTest(unittest.TestCase):
    def test_python(self):
        kind = kinds('a.py', PYTHON)
        self.assertEqual(kind('#!/usr'), 'comment')
        self.assertEqual(kind('@app.route'), 'decorator')
        self.assertEqual(kind('.route'), 'decorator')
        self.assertEqual(kind('class'), 'keyword')
        self.assertEqual(kind('Foo'), 'class')
        self.assertEqual(kind('Base'), 'class')
        self.assertEqual(kind('"""Docstring."""'), 'doc')
        self.assertEqual(kind('0x1F'), 'number')
        self.assertEqual(kind('2.5'), 'number')
        self.assertEqual(kind('def'), 'keyword')
        self.assertEqual(kind('method'), 'function')
        self.assertEqual(kind('self'), 'variable')
        self.assertEqual(kind('value'), 'variable')
        self.assertEqual(kind('None'), 'constant')
        self.assertEqual(kind('# a comment'), 'comment')
        self.assertEqual(kind('f"v='), 'string')
        self.assertEqual(kind('{', 0), 'keyword')
        self.assertEqual(kind('value', 1), 'variable')
        self.assertEqual(kind('\\n'), 'escape')
        self.assertEqual(kind("'plain'"), 'string')
        self.assertEqual(kind('for'), 'control')
        self.assertEqual(kind('in range'), 'keyword')
        self.assertEqual(kind('range'), 'builtin')
        self.assertEqual(kind('and'), 'keyword')
        self.assertEqual(kind('return'), 'control')
        self.assertEqual(kind('len'), 'builtin')
        self.assertEqual(kind('count'), 'function')           # call, not a definition
        self.assertEqual(kind('ValueError'), 'class')
        self.assertEqual(kind('"bad"'), 'string')

    def test_python_call_versus_definition_and_types(self):
        kind = kinds('a.py', 'def helper(x: int) -> str:\n    return helper(x) + other.name\n')
        self.assertEqual(kind('helper'), 'function')
        self.assertEqual(kind('helper', 1), 'function')
        self.assertEqual(kind('int'), 'class')
        self.assertEqual(kind('str'), 'class')
        self.assertEqual(kind('name'), 'variable')

    def test_json(self):
        kind = kinds('a.json', '{"name": "x", "n": [1, 2.5e3, true, null], "o": {"k": "v"}}')
        self.assertEqual(kind('"name"'), 'property')
        self.assertEqual(kind('"x"'), 'string')
        self.assertEqual(kind('"k"'), 'property')
        self.assertEqual(kind('"v"'), 'string')
        self.assertEqual(kind('2.5e3'), 'number')
        self.assertEqual(kind('true'), 'constant')
        self.assertEqual(kind('null'), 'constant')

    def test_markdown(self):
        source = ('# Title\nSome **bold** and *em* and `code` and [text](http://x.y)\n'
                  '```python\ndef f(): pass\n```\n- item\n')
        kind = kinds('a.md', source)
        self.assertEqual(kind('# Title'), 'heading')
        self.assertEqual(kind('**bold**'), 'strong')
        self.assertEqual(kind('*em*'), 'emphasis')
        self.assertEqual(kind('`code`'), 'string')
        self.assertEqual(kind('http://x.y'), 'link')
        self.assertEqual(kind('```python'), 'string')
        self.assertEqual(kind('def'), 'keyword')              # fence content is lexed as Python
        self.assertEqual(kind('f()'), 'function')
        self.assertEqual(kind('- item'), 'keyword')

    def test_html(self):
        source = ('<!DOCTYPE html>\n<html lang="en"><head><style>a { color: red; }</style>'
                  '<script>var n = go(1);</script></head><p class="x">Hi &amp; bye</p><!-- c --></html>')
        kind = kinds('a.html', source)
        self.assertEqual(kind('html', 1), 'tag')
        self.assertEqual(kind('<head'), 'punctuation')
        self.assertEqual(kind('lang'), 'attribute')
        self.assertEqual(kind('"en"'), 'string')
        self.assertEqual(kind('&amp;'), 'constant')
        self.assertEqual(kind('<!-- c -->'), 'comment')
        self.assertEqual(kind('a {'), 'selector')             # embedded CSS
        self.assertEqual(kind('color'), 'property')
        self.assertEqual(kind('var'), 'keyword')              # embedded JavaScript
        self.assertEqual(kind('go'), 'function')

    def test_css(self):
        source = ('@media (max-width: 10px) { a:hover, .b > #c { color: red; margin: 10px 2em; '
                  '--x: 1; background: rgba(0, 0, 0, .5) !important; } }\n/* note */\n')
        kind = kinds('a.css', source)
        self.assertEqual(kind('media'), 'control')
        self.assertEqual(kind('max-width'), 'property')
        self.assertEqual(kind('a:hover'), 'selector')
        self.assertEqual(kind('hover'), 'selector')
        self.assertEqual(kind('b >'), 'selector')
        self.assertEqual(kind('color'), 'property')
        self.assertEqual(kind('red'), 'string')
        self.assertEqual(kind('10px'), 'number')
        self.assertEqual(kind('--x'), 'variable')
        self.assertEqual(kind('rgba'), 'function')
        self.assertEqual(kind('!important'), 'keyword')
        self.assertEqual(kind('/* note */'), 'comment')

    def test_javascript(self):
        source = ('import { a } from "x";\nconst obj = { key: 1 };\n'
                  'class Foo extends Bar { constructor() { super(); } }\n'
                  'function run(q) { if (q === 1) return `t ${q}`; throw new Error("e"); }\n'
                  'let re = /ab+c/gi.test(s) ? null : undefined; // tail\n'
                  'items.map(v => v * 2); console.log(go(1));\n')
        kind = kinds('a.js', source)
        self.assertEqual(kind('import'), 'control')
        self.assertEqual(kind('from'), 'control')
        self.assertEqual(kind('const'), 'keyword')
        self.assertEqual(kind('obj'), 'variable')
        self.assertEqual(kind('key'), 'property')
        self.assertEqual(kind('class'), 'keyword')
        self.assertEqual(kind('Foo'), 'class')
        self.assertEqual(kind('Bar'), 'class')
        self.assertEqual(kind('function'), 'keyword')
        self.assertEqual(kind('run'), 'function')
        self.assertEqual(kind('return'), 'control')
        self.assertEqual(kind('`t '), 'string')
        self.assertEqual(kind('Error'), 'class')
        self.assertEqual(kind('/ab+c/gi'), 'regex')
        self.assertEqual(kind('test'), 'function')
        self.assertEqual(kind('null'), 'constant')
        self.assertEqual(kind('// tail'), 'comment')
        self.assertEqual(kind('map'), 'function')
        self.assertEqual(kind('log'), 'function')
        self.assertEqual(kind('go'), 'function')

    def test_typescript(self):
        kind = kinds('a.ts', 'interface Foo { a: string }\n@Component({x: 1}) class D implements Foo {}\n'
                             'const n: number = 1;\n')
        self.assertEqual(kind('interface'), 'keyword')
        self.assertEqual(kind('Foo'), 'class')
        self.assertEqual(kind('string'), 'class')
        self.assertEqual(kind('@Component'), 'decorator')
        self.assertEqual(kind('number'), 'class')
        self.assertEqual(kind('D'), 'class')

    def test_shell(self):
        source = ('#!/bin/bash\nexport NAME="a $USER"\nif [ -f "$f" ]; then echo hi; fi\n'
                  'for i in 1 2; do cd /tmp; done  # tail\n')
        kind = kinds('run.sh', source)
        self.assertEqual(kind('#!/bin'), 'comment')
        self.assertEqual(kind('export'), 'builtin')
        self.assertEqual(kind('NAME'), 'variable')
        self.assertEqual(kind('"a '), 'string')
        self.assertEqual(kind('$USER'), 'variable')
        self.assertEqual(kind('if'), 'control')
        self.assertEqual(kind('then'), 'control')
        self.assertEqual(kind('echo'), 'builtin')
        self.assertEqual(kind('do'), 'control')
        self.assertEqual(kind('# tail'), 'comment')

    def test_yaml(self):
        kind = kinds('a.yml', '# c\nname: test\nenabled: true\ncount: 3\nitems:\n  - uses: "q"\n')
        self.assertEqual(kind('# c'), 'comment')
        self.assertEqual(kind('name'), 'property')
        self.assertEqual(kind('test'), 'string')
        self.assertEqual(kind('true'), 'constant')
        self.assertEqual(kind('3'), 'number')
        self.assertEqual(kind('uses'), 'property')
        self.assertEqual(kind('"q"'), 'string')

    def test_toml(self):
        kind = kinds('a.toml', '# c\n[tool.x]\nname = "x"\nn = 1.5\nok = true\n')
        self.assertEqual(kind('[tool.x]'), 'tag')
        self.assertEqual(kind('name'), 'property')
        self.assertEqual(kind('"x"'), 'string')
        self.assertEqual(kind('1.5'), 'number')
        self.assertEqual(kind('true'), 'constant')
        self.assertEqual(kind('# c'), 'comment')

    def test_c(self):
        kind = kinds('a.c', '#include <stdio.h>\nstatic int main(int argc) {\n  printf("hi\\n");\n'
                            '  if (argc) return NULL;\n  struct Foo *f = malloc(8);\n}\n')
        self.assertEqual(kind('#include'), 'preproc')
        self.assertEqual(kind('<stdio.h>'), 'string')
        self.assertEqual(kind('static'), 'keyword')
        self.assertEqual(kind('int'), 'keyword')
        self.assertEqual(kind('main'), 'function')
        self.assertEqual(kind('printf'), 'function')
        self.assertEqual(kind('\\n'), 'escape')
        self.assertEqual(kind('if'), 'control')
        self.assertEqual(kind('NULL'), 'constant')
        self.assertEqual(kind('Foo'), 'class')
        self.assertEqual(kind('malloc'), 'function')

    def test_rust(self):
        kind = kinds('a.rs', '#[derive(Debug)]\npub struct Foo { x: i32, y: Option<String> }\n'
                             'impl Foo { pub fn new() -> Self { let s = "a"; println!("{}", s); } }\n')
        self.assertEqual(kind('#[derive(Debug)]'), 'decorator')
        self.assertEqual(kind('struct'), 'keyword')
        self.assertEqual(kind('Foo'), 'class')
        self.assertEqual(kind('i32'), 'class')
        self.assertEqual(kind('Option'), 'class')
        self.assertEqual(kind('fn'), 'keyword')
        self.assertEqual(kind('new'), 'function')
        self.assertEqual(kind('let'), 'keyword')
        self.assertEqual(kind('println!'), 'function')

    def test_go(self):
        kind = kinds('a.go', 'package main\nimport "fmt"\ntype Foo struct { X int }\n'
                             'func (f *Foo) Bar(a string) error { return nil }\n'
                             'func main() { fmt.Println("hi") }\n')
        self.assertEqual(kind('package'), 'control')
        self.assertEqual(kind('"fmt"'), 'string')
        self.assertEqual(kind('type'), 'keyword')
        self.assertEqual(kind('Foo'), 'class')
        self.assertEqual(kind('func'), 'keyword')
        self.assertEqual(kind('Bar'), 'function')
        self.assertEqual(kind('string'), 'class')
        self.assertEqual(kind('nil'), 'constant')
        self.assertEqual(kind('Println'), 'function')

    def test_sql(self):
        kind = kinds('q.sql', "SELECT a, COUNT(*) FROM t WHERE b = 'x' AND c > 10; -- tail\n")
        self.assertEqual(kind('SELECT'), 'keyword')
        self.assertEqual(kind('FROM'), 'keyword')
        self.assertEqual(kind('WHERE'), 'keyword')
        self.assertEqual(kind("'x'"), 'string')
        self.assertEqual(kind('AND'), 'keyword')
        self.assertEqual(kind('10'), 'number')
        self.assertEqual(kind('-- tail'), 'comment')

    def test_map_token(self):
        from pygments.token import Comment, Keyword, Name, Number, String, Text
        python, json = highlight.lexer_for('a.py'), highlight.lexer_for('a.json')
        self.assertEqual(highlight.map_token(Comment.Single, python), 'comment')
        self.assertEqual(highlight.map_token(String.Double, python), 'string')
        self.assertEqual(highlight.map_token(String.Escape, python), 'escape')
        self.assertEqual(highlight.map_token(Number.Integer, python), 'number')
        self.assertEqual(highlight.map_token(Keyword.Constant, python), 'constant')
        self.assertEqual(highlight.map_token(Keyword, python, 'if'), 'control')
        self.assertEqual(highlight.map_token(Keyword, python, 'def'), 'keyword')
        self.assertEqual(highlight.map_token(Keyword.Namespace, python), 'control')
        self.assertEqual(highlight.map_token(Name.Function, python), 'function')
        self.assertEqual(highlight.map_token(Name.Builtin.Pseudo, python, 'self'), 'variable')
        self.assertEqual(highlight.map_token(Name.Tag, json), 'property')
        self.assertEqual(highlight.map_token(Name.Tag, highlight.lexer_for('a.html')), 'tag')
        self.assertIsNone(highlight.map_token(Text, python))
        self.assertIsNone(highlight.map_token(Name, None))
        for token_type in (Name.Builtin.Pseudo, Name.Other, String.Doc, Keyword.Type):
            self.assertIn(highlight.map_token(token_type, python), highlight.KINDS)

    def test_every_kind_has_a_dark_plus_style(self):
        self.assertEqual(set(highlight.DARK_PLUS), set(highlight.KINDS))
        for style in highlight.DARK_PLUS.values():
            self.assertRegex(style.foreground, r'^#[0-9A-Fa-f]{6}$')
        self.assertEqual(highlight.DARK_PLUS['comment'].foreground, '#6A9955')
        self.assertTrue(highlight.DARK_PLUS['heading'].bold)
        self.assertTrue(highlight.DARK_PLUS['link'].underline)

    def test_offsets_are_code_points_and_cover_the_text(self):
        source = 'x = "😀é"\ny = 1\n'
        runs = list(highlight.classify(highlight.lexer_for('a.py'), source))
        self.assertIn((source.index('"'), source.index('"') + 4, 'string'), runs)
        self.assertEqual(source[runs[-1][0]:runs[-1][1]], '1')
        for start, end, _ in runs:
            self.assertTrue(0 <= start < end <= len(source))


@needs_pygments
class LexerDetectionTest(unittest.TestCase):
    def name(self, path, first_line=''):
        return highlight.language_name(highlight.lexer_for(path, first_line))

    def test_by_file_name(self):
        expected = {
            'a.py': 'Python', 'stubs.pyi': 'Python', 'a.json': 'JSON', 'a.md': 'Markdown',
            'a.html': 'HTML', 'a.css': 'CSS', 'a.scss': 'SCSS', 'a.js': 'JavaScript',
            'a.ts': 'TypeScript', 'a.tsx': 'TypeScript React', 'a.jsx': 'JavaScript React',
            'run.sh': 'Shell Script', 'a.yml': 'YAML', 'a.yaml': 'YAML', 'a.toml': 'TOML',
            'a.c': 'C', 'a.h': 'C', 'a.cpp': 'C++', 'a.rs': 'Rust', 'a.go': 'Go', 'q.sql': 'SQL',
            'logo.svg': 'XML', 'a.xml': 'XML', 'Dockerfile': 'Dockerfile', 'Makefile': 'Makefile',
            '.bashrc': 'Shell Script', '.zshrc': 'Shell Script', '.profile': 'Shell Script',
            '.env': 'Shell Script', '/some/dir/Dockerfile.dev': 'Dockerfile',
        }
        for path, language in expected.items():
            self.assertEqual(self.name(path), language, path)

    def test_plain_and_unknown(self):
        for path in ('notes.txt', 'build.log', '.gitignore', '.dockerignore', 'LICENSE', 'data.unknownext',
                     'no_extension', ''):
            self.assertIsNone(highlight.lexer_for(path), path)
            self.assertEqual(self.name(path), 'Plain Text')
        self.assertIsNone(highlight.lexer_for('notes.txt', '#!/usr/bin/env python3'))
        self.assertIsNone(highlight.lexer_for('script', 'import os  # python-looking but no shebang'))
        self.assertEqual(highlight.language_name(None), 'Plain Text')

    def test_shebang(self):
        self.assertEqual(self.name('script', '#!/usr/bin/env python3'), 'Python')
        self.assertEqual(self.name('script', '#!/usr/bin/python3.12'), 'Python')
        self.assertEqual(self.name('script', '#!/usr/bin/env -S python3 -u'), 'Python')
        self.assertEqual(self.name('script', '#!/bin/bash'), 'Shell Script')
        self.assertEqual(self.name('script', '#!/bin/sh -e'), 'Shell Script')
        self.assertEqual(self.name('script', '#!/usr/bin/env node'), 'JavaScript')
        self.assertEqual(self.name('script', '#!/usr/bin/env perl'), 'Perl')
        self.assertIsNone(highlight.lexer_for('script', '#!/no/such/interpreter'))
        self.assertEqual(self.name('a.json', '#!/bin/bash'), 'JSON')    # a file name wins

    def test_instances_are_cached_without_stripping_text(self):
        self.assertIs(highlight.lexer_for('a.py'), highlight.lexer_for('/x/b.pyi'))
        lexer = highlight.lexer_for('a.py')
        self.assertFalse(lexer.stripnl or lexer.stripall or lexer.ensurenl)

    def test_unavailable_pygments(self):
        with patch.object(highlight, 'AVAILABLE', False):
            self.assertIsNone(highlight.lexer_for('a.py'))
            self.assertIsNone(highlight.map_token(None, object()))
            self.assertEqual(list(highlight.classify(object(), 'x = 1')), [])


class EditorHarness(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        patch.object(editor, '_message').start()
        patch.object(editor, '_ask', return_value='discard').start()
        self.addCleanup(patch.stopall)
        self.window = Gtk.Window()
        self.panel = editor.EditorPanel()
        self.window.add(self.panel)
        self.window.show_all()
        self.addCleanup(self.window.destroy)

    def open(self, name, text, binary=False):
        path = self.root / name
        path.write_bytes(text) if binary else path.write_text(text)
        self.panel.open_file(str(path))
        tab = self.panel._current()
        settle(tab.highlighter)
        return tab

    def replace_range(self, tab, start, end, text):
        buffer = tab.buffer
        buffer.begin_user_action()
        buffer.delete(buffer.get_iter_at_offset(start), buffer.get_iter_at_offset(end))
        buffer.insert(buffer.get_iter_at_offset(start), text)
        buffer.end_user_action()


@needs_pygments
class BufferIntegrationTest(EditorHarness):
    def test_tags_are_applied_to_an_opened_file(self):
        source = 'def greet(name):\n    return "hi " + name  # note\n'
        tab = self.open('a.py', source)
        self.assertEqual(self.panel.status.get_text(), 'Ln 3, Col 1  |  UTF-8  |  LF  |  Python')
        self.assertEqual(tag_at(tab.buffer, source.index('def')), 'hl-keyword')
        self.assertEqual(tag_at(tab.buffer, source.index('greet')), 'hl-function')
        self.assertEqual(tag_at(tab.buffer, source.index('"hi')), 'hl-string')
        self.assertEqual(tag_at(tab.buffer, source.index('return')), 'hl-control')
        self.assertEqual(tag_at(tab.buffer, source.index('# note')), 'hl-comment')
        self.assertIsNone(tag_at(tab.buffer, source.index('(')))
        self.assertIsNone(tag_at(tab.buffer, 3))              # the space after "def"
        for kind in highlight.KINDS:
            self.assertIsNotNone(tab.buffer.get_tag_table().lookup('hl-' + kind))

    def test_tag_properties_follow_dark_plus(self):
        tab = self.open('a.py', 'x = 1\n')
        table = tab.buffer.get_tag_table()
        self.assertEqual(table.lookup('hl-comment').get_property('foreground-rgba').to_string(),
                         'rgb(106,153,85)')
        self.assertTrue(table.lookup('hl-heading').get_property('weight-set'))
        self.assertTrue(table.lookup('hl-emphasis').get_property('style-set'))
        self.assertTrue(table.lookup('hl-link').get_property('underline-set'))
        self.assertFalse(table.lookup('hl-comment').get_property('weight-set'))

    def test_multiline_triple_quoted_string(self):
        source = 'x = 1\ny = """first\nsecond\nthird"""\nz = 2\n'
        tab = self.open('a.py', source)
        for needle in ('first', 'second', 'third'):
            self.assertEqual(tag_at(tab.buffer, source.index(needle)), 'hl-string', needle)
        self.assertEqual(tag_at(tab.buffer, source.index('z')), 'hl-variable')

    def test_typing_an_opening_quote_recolours_and_deleting_restores(self):
        source = 'a = 1\n"""\nb = 2\nc = 3\n'
        tab = self.open('a.py', source.replace('"""\n', ''))
        before = painted(tab.buffer)
        self.assertEqual(tag_at(tab.buffer, tab.buffer.get_char_count() - 2), 'hl-number')
        tab.buffer.insert(tab.buffer.get_iter_at_offset(6), '"""\n')
        settle(tab.highlighter)
        self.assertEqual(tag_at(tab.buffer, source.index('b = 2')), 'hl-string')
        self.assertEqual(tag_at(tab.buffer, source.index('c = 3') + 4), 'hl-string')
        tab.buffer.delete(tab.buffer.get_iter_at_offset(6), tab.buffer.get_iter_at_offset(10))
        settle(tab.highlighter)
        self.assertEqual(painted(tab.buffer), before)

    def test_editing_near_the_top_of_a_large_file(self):
        lines = ['value_%d = compute(%d, "text")  # c%d' % (i, i, i) for i in range(5000)]
        tab = self.open('big.py', '\n'.join(lines) + '\n')
        buffer = tab.buffer
        self.assertEqual(tag_at(buffer, buffer.get_char_count() - 5), 'hl-comment')
        buffer.insert(buffer.get_iter_at_offset(0), 'def top():\n    """open\n')
        settle(tab.highlighter)
        self.assertEqual(tag_at(buffer, 0), 'hl-keyword')
        self.assertEqual(tag_at(buffer, 6), 'hl-function')
        self.assertEqual(tag_at(buffer, 30), 'hl-string')       # inside the unterminated docstring
        self.assertEqual(tag_at(buffer, buffer.get_char_count() - 5), 'hl-string')
        buffer.delete(buffer.get_iter_at_offset(0), buffer.get_iter_at_offset(len('def top():\n    """open\n')))
        settle(tab.highlighter)
        self.assertEqual(tag_at(buffer, buffer.get_char_count() - 5), 'hl-comment')
        self.assertEqual(tag_at(buffer, 0), 'hl-variable')

    def test_edits_during_a_running_pass_converge(self):
        text = ''.join('def f%d(a):\n    return a + %d  # c\n' % (i, i) for i in range(400))
        tab = self.open('a.py', 'x = 1\n')
        tab.buffer.set_text(text)
        context = GLib.MainContext.default()
        deadline = time.monotonic() + 5
        while tab.highlighter._runs is None and time.monotonic() < deadline:
            context.iteration(False)
            time.sleep(0.002)
        self.assertIsNotNone(tab.highlighter._runs)           # a pass is under way
        context.iteration(False)
        tab.buffer.insert(tab.buffer.get_iter_at_offset(0), '"""\n')   # restart it
        tab.buffer.insert(tab.buffer.get_end_iter(), '"""\n')
        settle(tab.highlighter)
        fresh = Gtk.TextBuffer()
        fresh.set_text(editor._text(tab.buffer))
        reference = highlight.Highlighter(fresh, lexer=highlight.lexer_for('a.py'))
        settle(reference)
        self.assertEqual(painted(tab.buffer), painted(fresh))
        reference.destroy()

    def test_rename_switches_highlighting_on_and_off(self):
        tab = self.open('notes.txt', 'def f():\n    return 1\n')
        self.assertEqual(self.panel.status.get_text().split('  |  ')[-1], 'Plain Text')
        self.assertEqual(painted(tab.buffer), {})
        new = self.root / 'notes.py'
        (self.root / 'notes.txt').rename(new)
        self.panel.on_path_renamed(str(self.root / 'notes.txt'), str(new))
        settle(tab.highlighter)
        self.assertEqual(tag_at(tab.buffer, 0), 'hl-keyword')
        self.assertEqual(self.panel.status.get_text().split('  |  ')[-1], 'Python')
        back = self.root / 'notes.md'
        new.rename(back)
        self.panel.on_path_renamed(str(new), str(back))
        settle(tab.highlighter)
        self.assertEqual(self.panel.status.get_text().split('  |  ')[-1], 'Markdown')
        plain = self.root / 'notes.log'
        back.rename(plain)
        self.panel.on_path_renamed(str(back), str(plain))
        settle(tab.highlighter)
        self.assertEqual(painted(tab.buffer), {})
        self.assertEqual(self.panel.status.get_text().split('  |  ')[-1], 'Plain Text')

    def test_folder_rename_redetects_children(self):
        (self.root / 'pkg').mkdir()
        tab = self.open('pkg/mod.py', 'import os\n')
        (self.root / 'pkg').rename(self.root / 'pkg2')
        self.panel.on_path_renamed(str(self.root / 'pkg'), str(self.root / 'pkg2'))
        self.assertEqual(tab.language, 'Python')
        self.assertEqual(tag_at(tab.buffer, 0), 'hl-control')

    def test_shebang_detects_extensionless_file(self):
        tab = self.open('tool', '#!/usr/bin/env python3\nimport sys\n')
        self.assertEqual(tab.language, 'Python')
        self.assertEqual(tag_at(tab.buffer, 23), 'hl-control')

    def test_crlf_file_offsets(self):
        tab = self.open('crlf.py', 'x = 1\r\ndef f():\r\n    pass\r\n', binary=False)
        self.panel._close_tab(tab)
        path = self.root / 'win.py'
        path.write_bytes(b'x = 1\r\ndef f():\r\n    return "a"\r\n')
        self.panel.open_file(str(path))
        tab = self.panel._current()
        settle(tab.highlighter)
        text = editor._text(tab.buffer)
        self.assertNotIn('\r', text)
        self.assertEqual(tab.ending, 'CRLF')
        self.assertEqual(tag_at(tab.buffer, text.index('def')), 'hl-keyword')
        self.assertEqual(tag_at(tab.buffer, text.index('"a"')), 'hl-string')

    def test_astral_characters_keep_offsets_aligned(self):
        source = 's = "😀😀"\ndef g():\n    pass\n'
        tab = self.open('emoji.py', source)
        self.assertEqual(tag_at(tab.buffer, source.index('def')), 'hl-keyword')
        self.assertEqual(tag_at(tab.buffer, source.index('😀')), 'hl-string')
        self.assertEqual(tag_at(tab.buffer, source.index('pass')), 'hl-control')

    def test_buffer_stays_unmodified_and_history_untouched(self):
        tab = self.open('a.py', 'def f():\n    pass\n')
        history = list(tab.history)
        self.assertFalse(tab.buffer.get_modified())
        self.assertFalse(self.panel.has_unsaved())
        tab.highlighter.set_lexer(highlight.lexer_for('a.json'))
        settle(tab.highlighter)
        tab.highlighter.set_style({'keyword': highlight.SyntaxStyle('#FF0000')})
        tab.highlighter.set_lexer(highlight.lexer_for('a.py'))
        settle(tab.highlighter)
        self.assertFalse(tab.buffer.get_modified())
        self.assertEqual(tab.history, history)
        self.assertEqual(tab.history_index, 0)
        self.assertEqual(tab.label.get_text(), 'a.py')

    def test_edit_then_undo_returns_to_unmodified_with_colours(self):
        tab = self.open('a.py', 'def f():\n    pass\n')
        tab.buffer.begin_user_action()
        tab.buffer.insert(tab.buffer.get_start_iter(), '# ')
        tab.buffer.end_user_action()
        settle(tab.highlighter)
        self.assertEqual(tag_at(tab.buffer, 0), 'hl-comment')
        self.assertTrue(tab.buffer.get_modified())
        tab.undo()
        settle(tab.highlighter)
        self.assertFalse(tab.buffer.get_modified())
        self.assertEqual(tag_at(tab.buffer, 0), 'hl-keyword')

    def test_find_selection_and_cursor_survive_highlighting(self):
        tab = self.open('a.py', 'alpha = 1\nbeta = 2\n')
        self.panel.search_entry.set_text('beta')
        self.panel._find()
        selection = tuple(i.get_offset() for i in tab.buffer.get_selection_bounds())
        insert = tab.cursor()
        tab.highlighter.set_lexer(highlight.lexer_for('a.py'))
        settle(tab.highlighter)
        self.assertEqual(tuple(i.get_offset() for i in tab.buffer.get_selection_bounds()), selection)
        self.assertEqual(tab.cursor(), insert)
        self.assertEqual(tag_at(tab.buffer, 10), 'hl-variable')

    def test_size_limits_skip_but_keep_the_language(self):
        big = self.open('big.py', 'x = 1\n' * 260_000)
        self.assertGreater(big.buffer.get_char_count(), highlight.MAX_BYTES)
        self.assertTrue(big.highlighter.skipped)
        self.assertEqual(tag_at(big.buffer, 0), None)
        self.assertEqual(self.panel.status.get_text().split('  |  ')[-1], 'Python')
        wide = self.open('wide.py', 'y = "%s"\n' % ('a' * (highlight.MAX_LINE + 1)))
        self.assertTrue(wide.highlighter.skipped)
        self.assertIsNone(tag_at(wide.buffer, 0))
        edge = self.open('edge.py', 'def f(): pass\n' + 'z' * highlight.MAX_LINE + '\n')
        self.assertFalse(edge.highlighter.skipped)
        self.assertEqual(tag_at(edge.buffer, 0), 'hl-keyword')
        # editing a skipped file below the limit starts highlighting again
        wide.buffer.set_text('def g(): pass\n')
        settle(wide.highlighter)
        self.assertFalse(wide.highlighter.skipped)
        self.assertEqual(tag_at(wide.buffer, 0), 'hl-keyword')

    def test_closing_a_tab_destroys_its_highlighter(self):
        tab = self.open('a.py', 'x = 1\n')
        highlighter = tab.highlighter
        tab.buffer.insert(tab.buffer.get_start_iter(), 'y')      # leaves a debounce pending
        self.assertTrue(highlighter.pending)
        self.assertTrue(self.panel._close_tab(tab))
        self.assertFalse(highlighter.pending)
        self.assertTrue(highlighter._destroyed)
        context = GLib.MainContext.default()
        for _ in range(50):
            context.iteration(False)
            time.sleep(0.003)

    def test_destroyed_highlighter_is_inert(self):
        buffer = Gtk.TextBuffer()
        buffer.set_text('def f(): pass\n')
        highlighter = highlight.Highlighter(buffer, lexer=highlight.lexer_for('a.py'))
        highlighter.destroy()
        highlighter.destroy()
        highlighter.set_lexer(highlight.lexer_for('a.py'))
        buffer.insert(buffer.get_start_iter(), '# ')
        context = GLib.MainContext.default()
        for _ in range(60):
            context.iteration(False)
            time.sleep(0.003)
        self.assertFalse(highlighter.pending)
        self.assertIsNone(tag_at(buffer, 3))

    def test_set_syntax_style_recolours_existing_and_new_tabs(self):
        first = self.open('a.py', 'def f(): pass\n')
        table = first.buffer.get_tag_table()
        self.assertEqual(table.lookup('hl-keyword').get_property('foreground-rgba').to_string(), 'rgb(86,156,214)')
        theme = {kind: highlight.SyntaxStyle('#102030') for kind in highlight.KINDS}
        theme['keyword'] = highlight.SyntaxStyle('#ff0000', bold=True, italic=True, underline=True)
        del theme['comment']
        self.panel.set_syntax_style(theme)
        keyword = table.lookup('hl-keyword')
        self.assertEqual(keyword.get_property('foreground-rgba').to_string(), 'rgb(255,0,0)')
        self.assertTrue(keyword.get_property('weight-set'))
        self.assertTrue(keyword.get_property('style-set'))
        self.assertTrue(keyword.get_property('underline-set'))
        self.assertEqual(table.lookup('hl-string').get_property('foreground-rgba').to_string(), 'rgb(16,32,48)')
        self.assertFalse(table.lookup('hl-comment').get_property('foreground-set'))   # unknown kind: no colour
        self.assertEqual(tag_at(first.buffer, 0), 'hl-keyword')                          # tags stay in place
        second = self.open('b.py', 'class A: pass\n')
        keyword = second.buffer.get_tag_table().lookup('hl-keyword')
        self.assertEqual(keyword.get_property('foreground-rgba').to_string(), 'rgb(255,0,0)')
        self.panel.set_syntax_style(highlight.DARK_PLUS)
        self.assertEqual(keyword.get_property('foreground-rgba').to_string(), 'rgb(86,156,214)')
        self.assertFalse(keyword.get_property('weight-set'))
        self.assertFalse(keyword.get_property('underline-set'))
        self.assertTrue(table.lookup('hl-comment').get_property('foreground-set'))

    def test_invalid_colours_are_ignored(self):
        tab = self.open('a.py', 'x = 1\n')
        self.panel.set_syntax_style({'keyword': highlight.SyntaxStyle('not-a-colour')})
        self.assertFalse(tab.buffer.get_tag_table().lookup('hl-keyword').get_property('foreground-set'))

    def test_markdown_and_json_files(self):
        source = '# Title\n\nText with `code`.\n'
        tab = self.open('r.md', source)
        self.assertEqual(tag_at(tab.buffer, 2), 'hl-heading')
        self.assertEqual(tag_at(tab.buffer, source.index('`code`')), 'hl-string')
        tab = self.open('d.json', '{"k": 1}')
        self.assertEqual(tag_at(tab.buffer, 2), 'hl-property')
        self.assertEqual(tag_at(tab.buffer, 6), 'hl-number')


@needs_pygments
class PerformanceTest(EditorHarness):
    def test_twenty_thousand_lines_highlight_quickly_without_blocking(self):
        block = ('class Widget%d(Base):\n    """Doc."""\n    LIMIT = %d\n\n    @property\n'
                 '    def value(self, a, b=None):\n        # note\n        items = [x * 2 for x in range(10)]\n'
                 '        text = f"item {a!r}" + \'plain\'\n        return len(items) + self.count(a)\n\n')
        text = ''.join(block % (i, i) for i in range(2000))
        self.assertGreaterEqual(text.count('\n'), 20_000)
        durations = []
        original = highlight.Highlighter._step

        def timed(highlighter, generation):
            started = time.perf_counter()
            try:
                return original(highlighter, generation)
            finally:
                durations.append(time.perf_counter() - started)
        with patch.object(highlight.Highlighter, '_step', timed):
            path = self.root / 'big.py'
            path.write_text(text)
            started = time.perf_counter()
            self.panel.open_file(str(path))
            tab = self.panel._current()
            settle(tab.highlighter)
            elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 8, 'full highlight took %.2fs' % elapsed)
        self.assertGreater(len(durations), 10)
        self.assertLess(max(durations), 0.05, 'idle slice blocked for %.1f ms' % (max(durations) * 1000))
        self.assertFalse(tab.highlighter.skipped)
        self.assertEqual(tag_at(tab.buffer, 0), 'hl-keyword')
        self.assertEqual(tag_at(tab.buffer, text.rindex('items')), 'hl-variable')
        print('\n20k-line highlight: %.2fs in %d slices, max slice %.1f ms'
              % (elapsed, len(durations), max(durations) * 1000))


class WithoutPygmentsTest(EditorHarness):
    def test_editor_works_unhighlighted_and_silent(self):
        with patch.object(highlight, 'AVAILABLE', False):
            path = self.root / 'a.py'
            path.write_text('def f():\n    return "x"\n')
            self.panel.open_file(str(path))
            tab = self.panel._current()
            settle(tab.highlighter)
            self.assertIsNone(tab.highlighter.lexer)
            self.assertEqual(self.panel.status.get_text(), 'Ln 3, Col 1  |  UTF-8  |  LF  |  Plain Text')
            self.assertEqual(tab.buffer.get_tag_table().get_size(), 0)
            tab.buffer.insert(tab.buffer.get_end_iter(), '# more\n')
            self.assertFalse(tab.highlighter.pending)
            self.panel.set_syntax_style(highlight.DARK_PLUS)
            new = self.root / 'a.json'
            path.rename(new)
            self.panel.on_path_renamed(str(path), str(new))
            self.assertEqual(self.panel.status.get_text().split('  |  ')[-1], 'Plain Text')
            self.assertTrue(self.panel.save_current())
            self.assertTrue(self.panel._close_tab(tab))

    def test_import_failure_leaves_a_working_module(self):
        import builtins
        import importlib
        real_import = builtins.__import__

        def fake(name, *args, **kwargs):
            if name == 'pygments' or name.startswith('pygments.'):
                raise ImportError('no pygments')
            return real_import(name, *args, **kwargs)
        with patch.object(builtins, '__import__', fake):
            module = importlib.reload(highlight)
            try:
                self.assertFalse(module.AVAILABLE)
                self.assertIsNone(module.lexer_for('a.py'))
                buffer = Gtk.TextBuffer()
                buffer.set_text('x = 1')
                helper = module.Highlighter(buffer, lexer=None)
                self.assertEqual(helper.tags, {})
                helper.destroy()
            finally:
                pass
        importlib.reload(highlight)


if __name__ == '__main__':
    unittest.main()
