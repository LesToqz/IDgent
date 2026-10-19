"""Small, dependency-free Markdown and source-link parsers.

Offsets are character offsets and lines are one-based. Masking preserves both.
"""
from dataclasses import dataclass
import ast
import os
import re
from urllib.parse import unquote


@dataclass(frozen=True)
class Link:
    target: str
    kind: str
    alias: str | None = None
    heading: str | None = None
    block: str | None = None
    line: int = 1
    start: int = 0


@dataclass(frozen=True)
class CodeRef:
    spec: str
    kind: str
    line: int


def _scalar(value):
    value = value.strip()
    if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def parse_frontmatter(text):
    """Read the deliberately small YAML subset used by note properties."""
    opening = re.match(r'\A\ufeff?---[^\S\n]*\r?\n', text)
    if not opening:
        return {}, 0
    closing = re.search(r'(?m)^(?:---|\.\.\.)\s*$', text[opening.end():])
    if not closing:
        return {}, 0
    end = opening.end() + closing.end()
    if text[end:end + 1] == '\n':
        end += 1
    props, key = {}, None
    for line in text[opening.end():opening.end() + closing.start()].splitlines():
        item = re.match(r'^\s*-\s+(.*)$', line)
        if item and key:
            if not isinstance(props[key], list):
                props[key] = []
            props[key].append(_scalar(item[1]))
            continue
        match = re.match(r'^([\w-]+):\s*(.*)$', line)
        if match:
            key, value = match[1].lower(), match[2].strip()
            if value.startswith('[') and value.endswith(']'):
                props[key] = [_scalar(m[0]) for m in re.finditer(r'''"[^"]*"|'[^']*'|[^,]+''', value[1:-1]) if m[0].strip()]
            else:
                props[key] = _scalar(value)
    return props, end


def _blank(match):
    return re.sub(r'[^\n]', ' ', match[0])


def _mask(text):
    text = re.sub(r'<!--[\s\S]*?(?:-->|\Z)', _blank, text)
    lines, fence = [], None
    for line in text.splitlines(keepends=True):
        match = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)', line)
        if fence:
            lines.append(re.sub(r'[^\n]', ' ', line))
            if match and match[1][0] == fence[0] and len(match[1]) >= len(fence) and not match[2].strip():
                fence = None
        elif match:
            fence = match[1]
            lines.append(re.sub(r'[^\n]', ' ', line))
        else:
            lines.append(line)
    return re.sub(r'(`+)(?!`)([\s\S]*?)(?<!`)\1(?!`)', _blank, ''.join(lines))


def _local(target):
    return bool(target.strip()) and not re.match(r'^(?:[a-z][\w+.-]*:|//|#)', target.strip(), re.I)


def _target(raw):
    raw = unquote(raw.strip()).split('?', 1)[0]
    block = raw.split('^', 1)[1] if '^' in raw else None
    raw = raw.split('^', 1)[0]
    heading = raw.split('#', 1)[1] if '#' in raw else None
    return raw.split('#', 1)[0].strip(), heading, block


_LINKS = re.compile(r'(!?)\[\[([^\]\n]+)\]\]|(!?)\[([^\]\n]*)\]\(\s*(<[^>]+>|[^\s)]+)(?:\s+["\'][^\n]*?["\'])?\s*\)|<([^<>\s]+)>')


def extract_links(text):
    clean = _mask(text)
    result = []
    for match in _LINKS.finditer(clean):
        if match[2] is not None:
            raw = match[2].replace('\\|', '|')
            raw, separator, alias = raw.partition('|')
            kind = 'embed' if match[1] else 'wiki'
            alias = alias if separator else None
        elif match[5] is not None:
            raw, alias = match[5].strip('<>'), match[4]
            kind = 'embed' if match[3] else 'md'
        else:
            raw, alias, kind = match[6], None, 'md'
        if not _local(raw):
            continue
        target, heading, block = _target(raw)
        if target:
            result.append(Link(target, kind, alias, heading, block,
                               text.count('\n', 0, match.start()) + 1, match.start()))
    return result


def extract_tags(text):
    props, offset = parse_frontmatter(text)
    tags = props.get('tags', [])
    if isinstance(tags, str):
        tags = re.split(r'[,\s]+', tags)
    result = []

    def add(tag):
        tag = tag.lstrip('#')
        if re.fullmatch(r'[\w/-]+', tag) and any(c.isalpha() or c in '_-/' for c in tag) and tag not in result:
            result.append(tag)

    for tag in tags:
        add(tag)
    clean = _mask(text[offset:])
    clean = _LINKS.sub(_blank, clean)
    clean = re.sub(r'(?:[a-z]+://|mailto:)[^\s]+', _blank, clean, flags=re.I)
    for match in re.finditer(r'(?<![\w/#])#([\w/-]+)', clean):
        add(match[1])
    return result


def extract_code_links(path, text):
    ext = os.path.splitext(path)[1].lower()
    result = []
    if ext == '.py':
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                result.extend(CodeRef(n.name, 'python', node.lineno) for n in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = '.' * node.level + (node.module or '')
                if node.module:
                    result.append(CodeRef(base, 'python', node.lineno))
                else:
                    result.extend(CodeRef(base + n.name, 'python', node.lineno) for n in node.names if n.name != '*')
        return sorted(result, key=lambda ref: ref.line)
    patterns = []
    if ext in {'.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.vue'}:
        patterns = [r'''(?:\b(?:import|export)\s+(?:[^;\n]*?\s+from\s*)?|\brequire\s*\(\s*)["']([^"']+)["']''']
    elif ext in {'.css', '.scss', '.sass'}:
        patterns = [r'''@import\s+["']([^"']+)["']|url\(\s*["']?([^\s)'";]+)''']
    elif ext in {'.html', '.htm'}:
        patterns = [r'''\b(?:src|href)\s*=\s*["']([^"']+)["']''']
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.I):
            spec = next(g for g in match.groups() if g is not None)
            if _local(spec):
                result.append(CodeRef(spec, ext.lstrip('.'), text.count('\n', 0, match.start()) + 1))
    return result
