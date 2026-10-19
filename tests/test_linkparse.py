import unittest

from idgent.linkparse import extract_code_links, extract_links, extract_tags, parse_frontmatter


class LinkParseTests(unittest.TestCase):
    def test_frontmatter(self):
        text = '---\ntags: [one, "#two"]\naliases:\n  - "First name"\n  - Second\nrelation: "[[Other]]"\n---\nBody'
        props, offset = parse_frontmatter(text)
        self.assertEqual(props['tags'], ['one', '#two'])
        self.assertEqual(props['aliases'], ['First name', 'Second'])
        self.assertEqual(text[offset:], 'Body')
        self.assertEqual(extract_links(text)[0].target, 'Other')
        crlf = '---\r\ntags: one\r\n---\r\n\r\nBody'
        self.assertEqual(crlf[parse_frontmatter(crlf)[1]:], '\r\nBody')
        self.assertEqual(parse_frontmatter('no YAML'), ({}, 0))
        self.assertEqual(parse_frontmatter('---\ntags: x'), ({}, 0))
        self.assertEqual(parse_frontmatter('---\nalias: \'Name\'\n---\n')[0]['alias'], 'Name')

    def test_links(self):
        text = '[[Target#Heading^block|Alias]] ![[pic.png]] [name](a%20b.md#h "Title") ![alt](p.png) <file.md> [[a\\|b]] [[ ]]'
        links = extract_links(text)
        self.assertEqual([r.target for r in links], ['Target', 'pic.png', 'a b.md', 'p.png', 'file.md', 'a'])
        self.assertEqual((links[0].heading, links[0].block, links[0].alias), ('Heading', 'block', 'Alias'))
        self.assertEqual([r.kind for r in links], ['wiki', 'embed', 'md', 'embed', 'md', 'wiki'])
        self.assertEqual(links[-1].alias, 'b')
        self.assertEqual(extract_links('[x](a.md?q=1)')[0].target, 'a.md')
        self.assertEqual(extract_links('[[Self]]')[0].target, 'Self')

    def test_ignored_regions_and_positions(self):
        text = '```md\n[[bad]] #bad\n```\n~~~\n[[bad]]\n~~~\n`[[bad]]` ``a ` [[bad]]`` <!-- [[bad]] -->\n[[Good]]'
        links = extract_links(text)
        self.assertEqual([r.target for r in links], ['Good'])
        self.assertEqual(links[0].start, text.index('[[Good]]'))
        self.assertEqual(links[0].line, 8)
        self.assertEqual(extract_links('[x](https://x) [x](http://x) [x](mailto:x) [x](data:x) [x](#anchor) <https://x>'), [])
        self.assertEqual(extract_tags(text), [])
        self.assertEqual(extract_links('```\n[[unclosed]]'), [])

    def test_tags(self):
        text = '---\ntags: "#front, second third"\n---\n# Heading\n#front #a/b/c #123 #a1 #_ #hy-phen #кириллица `#code` [#label](x#anchor) [[x#heading]] https://x/#url abc#inside #end'
        self.assertEqual(extract_tags(text), ['front', 'second', 'third', 'a/b/c', 'a1', '_', 'hy-phen', 'кириллица', 'end'])
        self.assertEqual(extract_tags('---\ntags:\n - "#one"\n - two\n---\n#two'), ['one', 'two'])

    def test_python(self):
        refs = extract_code_links('a.py', 'import a.b, c as d\nfrom a.b import c\nfrom . import x\nfrom ..m import y\n# import nope\ns="import nope"')
        self.assertEqual([r.spec for r in refs], ['a.b', 'c', 'a.b', '.x', '..m'])
        self.assertTrue(all(r.kind == 'python' for r in refs))
        self.assertEqual(refs[-1].line, 4)
        self.assertEqual(extract_code_links('broken.py', 'import ('), [])

    def test_other_code(self):
        refs = extract_code_links('a.ts', 'import x from "./x";\nimport "./x.css"; require("./r"); export {x} from "./e";')
        self.assertEqual([r.spec for r in refs], ['./x', './x.css', './r', './e'])
        self.assertEqual(refs[1].line, 2)
        self.assertEqual([r.spec for r in extract_code_links('a.scss', '@import "./x"; a {background:url(./img.png)} b {x:url("data:x")}')], ['./x', './img.png'])
        self.assertEqual([r.spec for r in extract_code_links('a.html', '<a href="page.html"><img src="img.png"><a href="https://x"><a href="#id">')], ['page.html', 'img.png'])
        self.assertEqual([r.spec for r in extract_code_links('a.html', '<img src=photo.png>')], ['photo.png'])
        self.assertEqual(extract_code_links('a.md', '[[Note]]'), [])
