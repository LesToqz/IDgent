"""Pure Markdown subset helpers shared by editable document views.

The parser has no GUI imports. It deliberately leaves HTML as text and never
loads image resources. Blocks are bounded so even giant paragraphs/code fences
can be consumed between main-loop iterations. This is not a full CommonMark
implementation (reference links and delimiter-flanking rules are omitted).
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urlsplit, unquote
from pathlib import Path

CHUNK = 8192


@dataclass(frozen=True)
class Span:
    text: str
    styles: tuple[str, ...] = ()
    link: str | None = None


@dataclass(frozen=True)
class Block:
    kind: str
    text: str
    level: int = 0
    indent: int = 0
    quote: int = 0
    marker: str = ''
    language: str = ''
    cells: tuple[str, ...] = ()
    alignments: tuple[str, ...] = ()
    header: bool = False
    continuation: bool = False


_INLINE = re.compile(r'\\([!"#$%&\'()*+,\-./:;<=>?@\[\]\\^_`{|}~]|\n)|'
                     r'(`+)([^`]*?)\2|'
                     r'(!?)\[([^\]\n]*)\]\((<[^>\n]*>|[^\s()]*(?:\([^()]*\)[^\s()]*)*)(?:\s+"[^"\n]*")?\)|'
                     r'<((?:https?://|mailto:)[^<>\s]+)>|'
                     r'(\*\*\*|___|\*\*|__|~~|\*|_)(?=\S)(.+?)\8| {2,}\n', re.DOTALL)


def parse_inline(text: str, styles: tuple[str, ...] = (), depth: int = 0) -> list[Span]:
    """Return literal text runs with semantic styles and optional link targets."""
    if depth > 12:
        return [Span(text, styles)]
    result, pos = [], 0
    for match in _INLINE.finditer(text):
        if match.start() > pos:
            result.append(Span(text[pos:match.start()].replace('\n', ' '), styles))
        escape, ticks, code, image, label, target, auto, delimiter, body = match.groups()
        if escape is not None:
            result.append(Span(escape, styles))
        elif ticks:
            result.append(Span(code.replace('\n', ' '), styles + ('code',)))
        elif label is not None:
            if image:
                result.append(Span('[image: ' + label + ']', styles + ('image',)))
            else:
                result.extend(Span(span.text, span.styles + ('link',), target.strip('<>'))
                              for span in parse_inline(label, styles, depth + 1))
        elif auto:
            result.append(Span(auto, styles + ('link',), auto))
        elif delimiter:
            added = ('strong', 'emphasis') if len(delimiter) == 3 else (
                {'**': 'strong', '__': 'strong', '*': 'emphasis', '_': 'emphasis', '~~': 'strike'}[delimiter],)
            result.extend(parse_inline(body, styles + added, depth + 1))
        else:
            result.append(Span('\n', styles))
        pos = match.end()
    if pos < len(text):
        result.append(Span(text[pos:].replace('\n', ' '), styles))
    return result


_LIST = re.compile(r'^( *)([-+*]|\d+[.)])\s+(.*)$')
_FENCE = re.compile(r'^ {0,3}(`{3,}|~{3,})(.*)$')
_HEADING = re.compile(r'^ {0,3}(#{1,6})(?:\s+|$)(.*)$')
_RULE = re.compile(r'^ {0,3}(?:(?:\*\s*){3,}|(?:-\s*){3,}|(?:_\s*){3,})$')
_TABLE_RULE = re.compile(r'^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$')


def _quote(line, limit=32):
    depth = 0
    while depth < limit:
        match = re.match(r'^ {0,3}> ?', line)
        if not match:
            break
        line = line[match.end():]
        depth += 1
    return depth, line


def _cells(line):
    # Split only unescaped separators; retain escapes for the inline parser.
    parts = re.split(r'(?<!\\)((?:\\\\)*)\|', line.strip())
    cells = [parts[0]]
    for index in range(1, len(parts), 2):
        cells[-1] += parts[index]
        if len(cells) < 32:
            cells.append(parts[index + 1])
        else:
            cells[-1] += '|' + parts[index + 1]
    if line.lstrip().startswith('|'):
        cells.pop(0)
    if len(cells) > 1 and not cells[-1].strip():
        cells.pop()
    return [cell.strip() for cell in cells]


def parse_blocks(text: str):
    """Yield flat structural blocks; indent and quote retain container nesting.

    At most CHUNK characters are accumulated per paragraph/code block. Tables
    are emitted one row at a time so painting can yield between rows.
    """
    lines = text.replace('\r\n', '\n').split('\n')
    i, count = 0, len(lines)
    while i < count:
        quote, line = _quote(lines[i].expandtabs(4))
        if not line.strip():
            i += 1
            continue
        fence = _FENCE.match(line)
        if fence:
            token, info = fence.groups()
            language = info.strip().split()[0] if info.strip() else ''
            i += 1
            content, size, continuation = [], 0, False
            while i < count:
                q, current = _quote(lines[i], quote)
                if re.fullmatch(r' {0,3}' + re.escape(token[0]) + '{' + str(len(token)) + r',}\s*', current):
                    i += 1
                    break
                for start in range(0, max(1, len(current)), CHUNK):
                    part = current[start:start + CHUNK]
                    content.append(part + ('\n' if start + CHUNK >= len(current) else ''))
                    size += len(part) + 1
                    if size >= CHUNK:
                        yield Block('codeblock', ''.join(content), quote=quote, language=language, continuation=continuation)
                        content, size, continuation = [], 0, True
                i += 1
            if content or not continuation:
                yield Block('codeblock', ''.join(content), quote=quote, language=language, continuation=continuation)
            continue
        heading = _HEADING.match(line)
        if heading:
            yield Block('heading', re.sub(r'\s+#+\s*$', '', heading[2]), len(heading[1]), quote=quote)
            i += 1
            continue
        if i + 1 < count and re.fullmatch(r' {0,3}(=+|-+)\s*', _quote(lines[i + 1])[1]):
            yield Block('heading', line.strip(), 1 if _quote(lines[i + 1])[1].lstrip().startswith('=') else 2, quote=quote)
            i += 2
            continue
        if _RULE.fullmatch(line):
            yield Block('rule', '', quote=quote)
            i += 1
            continue
        if i + 1 < count and '|' in line and _TABLE_RULE.fullmatch(_quote(lines[i + 1])[1]):
            header = _cells(line)
            rules = _cells(_quote(lines[i + 1])[1])
            if len(header) == len(rules):
                alignments = tuple('center' if cell.startswith(':') and cell.endswith(':') else
                                   'right' if cell.endswith(':') else 'left' for cell in rules)
                yield Block('table', line, quote=quote, cells=tuple(header), alignments=alignments, header=True)
                i += 2
                while i < count:
                    q, current = _quote(lines[i])
                    if q != quote or not current.strip() or '|' not in current:
                        break
                    cells = _cells(current)[:len(header)]
                    cells += [''] * (len(header) - len(cells))
                    yield Block('table', current, quote=quote, cells=tuple(cells), alignments=alignments)
                    i += 1
                continue
        item = _LIST.match(line)
        if item:
            spaces, marker, body = item.groups()
            task = re.match(r'^\[([ xX])\]\s*(.*)$', body)
            if task:
                marker, body = ('☐' if task[1] == ' ' else '☑'), task[2]
            elif marker in ('-', '+', '*'):
                marker = '•'
            i += 1
            content = [body]
            size = len(body)
            while i < count and size < CHUNK:
                q, continuation = _quote(lines[i])
                width = len(continuation) - len(continuation.lstrip(' '))
                if (q != quote or width < item.start(3) or not continuation.strip() or
                        _LIST.match(continuation) or _FENCE.match(continuation) or _HEADING.match(continuation)):
                    break
                content.append(continuation[item.start(3):])
                size += len(continuation)
                i += 1
            yield Block('list', '\n'.join(content), indent=min(len(spaces), 64), quote=quote, marker=marker)
            continue
        if line.startswith('    '):
            for start in range(4, max(5, len(line)), CHUNK):
                yield Block('codeblock', line[start:start + CHUNK] + '\n', quote=quote)
            i += 1
            continue
        content, size = [], 0
        while i < count:
            q, current = _quote(lines[i])
            if content and (not current.strip() or q != quote or _FENCE.match(current) or
                            _HEADING.match(current) or _LIST.match(current) or _RULE.fullmatch(current)):
                break
            if not current.strip():
                break
            if content and i + 1 < count and (_TABLE_RULE.fullmatch(_quote(lines[i + 1])[1]) or
                                               re.fullmatch(r' {0,3}(=+|-+)\s*', _quote(lines[i + 1])[1])):
                break
            for start in range(0, max(1, len(current)), CHUNK):
                part = current[start:start + CHUNK]
                content.append(part)
                size += len(part) + 1
                if size >= CHUNK:
                    yield Block('paragraph', '\n'.join(content), quote=quote)
                    content, size = [], 0
            i += 1
            if size >= CHUNK:
                break
        if content:
            yield Block('paragraph', '\n'.join(content), quote=quote)


def link_target(target: str, source_path: str):
    """Return ('uri'|'file', target), or None for unsafe/nonexistent targets."""
    if target.startswith('[[') and target.endswith(']]'):
        name = unquote(target[2:-2].split('|', 1)[0]).split('#', 1)[0]
        if not name or Path(name).is_absolute() or any(ord(c) < 32 for c in name):
            return None
        source = Path(source_path).absolute()
        root = next((parent for parent in source.parents
                     if (parent / '.IDgent').is_dir() or (parent / '.git').exists()), source.parent)
        variants = [name] if Path(name).suffix else [name + '.md', name + '.markdown', name]
        for folder in (root, source.parent, root / '.IDgent' / 'Notes'):
            for variant in variants:
                candidate = folder / variant
                if candidate.is_file():
                    return 'file', str(candidate.resolve())
        return None
    if any(ord(c) < 32 for c in target):
        return None
    try:
        parsed = urlsplit(target)
    except ValueError:
        return None
    if parsed.scheme.lower() in ('http', 'https') and parsed.netloc:
        return 'uri', target
    if parsed.scheme.lower() == 'mailto' and parsed.path:
        return 'uri', target
    if parsed.scheme or parsed.netloc or not parsed.path or Path(unquote(parsed.path)).is_absolute():
        return None
    path = Path(source_path).parent / unquote(parsed.path)
    try:
        return ('file', str(path.resolve())) if path.is_file() else None
    except OSError:
        return None
