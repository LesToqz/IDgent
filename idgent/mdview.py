"""Small, inert Markdown subset and an incrementally painted GTK preview.

The parser has no GUI imports. It deliberately leaves HTML as text and never
loads image resources. Blocks are bounded so even giant paragraphs/code fences
can be consumed between main-loop iterations. This is not a full CommonMark
implementation (reference links and delimiter-flanking rules are omitted).
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import time
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
    lines = text.replace('\r\n', '\n').expandtabs(4).split('\n')
    i, count = 0, len(lines)
    while i < count:
        quote, line = _quote(lines[i])
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


class MarkdownView:
    """Own a selectable read-only view, tags, link handling and idle lifetime."""
    def __init__(self, open_file, source_path):
        import gi
        gi.require_version('Gtk', '3.0')
        from gi.repository import Gdk, Gtk, GLib, Pango
        self.Gtk, self.GLib, self.Pango, self.Gdk = Gtk, GLib, Pango, Gdk
        self.open_file, self.source_path = open_file, source_path
        self.view = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.readable_width = False
        self.view.connect('size-allocate', self._resize_readable)
        self.view.set_left_margin(32)
        self.view.set_right_margin(32)
        self.view.set_top_margin(44)
        self.view.set_bottom_margin(28)
        self.buffer = self.view.get_buffer()
        self.tags, self.links = {}, []
        self._widgets = []
        self._wide_widgets = []
        self._feedback = set()
        self._idle = 0
        self._work = None
        self._done = None
        self.destroyed = False
        self.provider = Gtk.CssProvider()
        self.view.get_style_context().add_provider(self.provider, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
        self.view.connect('button-release-event', self._release)
        self.view.connect('destroy', lambda *_: self.destroy())

    def set_readable_width(self, enabled: bool) -> None:
        self.readable_width = bool(enabled)
        self._resize_readable(self.view, self.view.get_allocation())

    def _resize_readable(self, _view, allocation):
        margin = max(32, (allocation.width - 700 + 1) // 2) if self.readable_width else 32
        if self.view.get_left_margin() != margin:
            self.view.set_left_margin(margin)
            self.view.set_right_margin(margin)
        # Paragraph tags override TextView margins. Move those too, including
        # containers already rendered before the option or viewport changed.
        for name, tag in self.tags.items():
            if name.startswith('container-'):
                _, quote, inset = name.split('-')
                tag.set_property('left-margin', margin + int(quote) * 22 + int(inset))
        for widget, quote, inset in self._wide_widgets:
            self._size_widget(widget, quote, inset)
        if 'codeblock' in self.tags:
            self.tags['codeblock'].set_property('right-margin', max(48, margin))

    def tag(self, name, **properties):
        tag = self.tags.get(name)
        if tag is None:
            tag = self.tags[name] = self.buffer.create_tag('md-' + name)
        for key, value in properties.items():
            tag.set_property(key.replace('_', '-'), value)
        return tag

    def set_style(self, syntax, chrome, size):
        from idgent import highlight
        self.syntax, self.chrome, self.size = syntax, chrome, size
        def valid(style):
            return style is not None and self.Gdk.RGBA().parse(style.foreground)
        def color(kind):
            style = syntax.get(kind)
            fallback = syntax.get('operator')
            return (style.foreground if valid(style) else fallback.foreground if valid(fallback)
                    else chrome['gutter_fg_active'])
        bg, fg = chrome['gutter_bg'], color('operator')
        self.foreground = fg
        self.provider.load_from_data(f'''
textview, textview text {{background-color:{bg}; color:{fg}; font-family:sans-serif; font-size:{size}pt;}}
.md-cell {{color:{fg}; font-family:sans-serif; font-size:{size}pt;
    border-right:1px solid {chrome['gutter_fg']}; border-bottom:1px solid {chrome['gutter_fg']}; padding:6px 8px;}}
.md-first-cell {{border-left:1px solid {chrome['gutter_fg']};}}
.md-header {{background-color:{chrome['current_line_bg']}; border-top:1px solid {chrome['gutter_fg']}; font-weight:bold;}}
.md-code-tools {{background-color:{chrome['current_line_bg']}; color:{fg};}}
.md-code-tools button {{background:transparent; color:{chrome['gutter_fg']}; border:0; box-shadow:none; padding:3px; min-width:20px; min-height:20px;}}
.md-code-tools button:hover {{background:{bg}; color:{fg};}}
'''.encode())
        p = self.Pango
        self.tag('body', pixels_above_lines=3, pixels_below_lines=8)
        for level, scale in enumerate((1.618, 1.462, 1.318, 1.188, 1.076, 1), 1):
            self.tag('h' + str(level), foreground=color('heading'), weight=700 if level <= 3 else 600,
                     scale=scale, pixels_above_lines=14, pixels_below_lines=8)
        self.tag('strong', foreground=color('strong'), weight=p.Weight.BOLD)
        self.tag('emphasis', foreground=color('emphasis'), style=p.Style.ITALIC)
        self.tag('strike', strikethrough=True)
        self.tag('code', family='monospace', background=chrome['current_line_bg'], foreground=color('string'))
        self.tag('codeblock', family='monospace', paragraph_background=chrome['current_line_bg'],
                 foreground=fg, right_margin=max(48, self.view.get_right_margin()), pixels_above_lines=5, pixels_below_lines=5)
        self.tag('table', pixels_above_lines=0, pixels_below_lines=0)
        self.tag('link', foreground=color('link'), underline=p.Underline.SINGLE)
        self.tag('image', foreground=chrome['gutter_fg'])
        self.tag('rule', foreground=chrome['gutter_fg'], scale=.65, pixels_above_lines=0, pixels_below_lines=8)
        for kind in highlight.KINDS:
            style = syntax.get(kind)
            self.tag('syntax-' + kind, foreground=color(kind), foreground_set=bool(valid(style)),
                     weight=p.Weight.BOLD if style and style.bold else p.Weight.NORMAL,
                     style=p.Style.ITALIC if style and style.italic else p.Style.NORMAL)
        for name in tuple(self.tags):
            if name.startswith('container-'):
                if name.split('-')[1] != '0':
                    self.tags[name].set_property('foreground', chrome['gutter_fg'])

    def cancel(self):
        if self._idle:
            self.GLib.source_remove(self._idle)
            self._idle = 0
        self._work = self._done = None

    def destroy(self):
        self.cancel()
        self._clear_widgets()
        self.destroyed = True

    def render(self, text, done=None):
        self.cancel()
        if self.destroyed:
            return
        self._clear_widgets()
        self.buffer.set_text('')
        self.links = []
        self._work, self._done = self._operations(text), done
        self._idle = self.GLib.idle_add(self._step)

    def _operations(self, text):
        from idgent import highlight
        code_parts = []
        for block in parse_blocks(text):
            inset = block.indent * 5 + (16 if block.kind == 'codeblock' else 12 if block.kind == 'list' else 0)
            container = f'container-{block.quote}-{inset}'
            if container not in self.tags:
                tag = self.tag(container, left_margin=self.view.get_left_margin() + block.quote * 22 + inset)
                if block.quote:
                    tag.set_property('foreground', self.chrome['gutter_fg'])
                # Container foreground must not override headings/links/inline styles.
                tag.set_priority(0)
            base = ('body', container)
            if block.kind == 'heading':
                base += ('h' + str(block.level),)
            elif block.kind == 'table':
                yield self._table_row(block), ('table', container), None
                yield '\n', ('table', container), None
                continue
            elif block.kind == 'codeblock':
                if not block.continuation:
                    code_parts = []
                    yield self._code_tools(code_parts, block), base, None
                    yield '\n', base + ('codeblock',), None
                code_parts.append(block.text)
                yield block.text, base + ('codeblock',), None
                if block.language and highlight.AVAILABLE:
                    # Bounded snippets avoid pathological long-line lexing in Pygments.
                    try:
                        from pygments.lexers import get_lexer_by_name
                        lexer = get_lexer_by_name(block.language, stripnl=False, stripall=False, ensurenl=False)
                        if lexer and len(block.text) <= CHUNK * 2:
                            for a, b, kind in highlight.classify(lexer, block.text):
                                yield (a, b, 'syntax-' + kind), (), None
                    except Exception:
                        pass  # Optional highlighting must never stop the preview.
                continue
            elif block.kind == 'rule':
                yield '─' * 64 + '\n', ('rule',), None
                continue
            if block.marker in ('☐', '☑'):
                checkbox = self.Gtk.CheckButton()
                checkbox.set_active(block.marker == '☑')
                checkbox.set_sensitive(False)
                checkbox.set_tooltip_text('Completed task' if checkbox.get_active() else 'Incomplete task')
                yield checkbox, base, None
                yield ' ', base, None
            elif block.marker:
                yield block.marker + '  ', base, None
            for start in range(0, len(block.text), CHUNK):
                for span in parse_inline(block.text[start:start + CHUNK]):
                    yield span.text, base + span.styles, span.link
            yield '\n', base, None
            if block.kind == 'heading' and block.level < 3:
                yield '─' * 64 + '\n', ('rule',), None

    def _clear_widgets(self):
        for source in self._feedback:
            self.GLib.source_remove(source)
        self._feedback.clear()
        for widget in self._widgets:
            widget.destroy()
        self._widgets.clear()
        self._wide_widgets.clear()

    def _style_widget(self, widget, *classes):
        context = widget.get_style_context()
        context.add_provider(self.provider, self.Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
        for name in classes:
            context.add_class(name)

    def _size_widget(self, widget, quote, inset):
        width = max(32, self.view.get_allocated_width() - self.view.get_left_margin() -
                    self.view.get_right_margin() - quote * 22 - inset - 2)
        widget.set_size_request(width, -1)
        if isinstance(widget, self.Gtk.Grid):
            columns = len(widget.get_children())
            for label in widget.get_children():
                label.set_size_request(max(1, width // columns - 18), -1)

    def _wide_widget(self, widget, block, inset=0):
        self._wide_widgets.append((widget, block.quote, inset))
        self._size_widget(widget, block.quote, inset)
        return widget

    def _table_row(self, block):
        grid = self.Gtk.Grid(column_homogeneous=True)
        for column, (cell, alignment) in enumerate(zip(block.cells, block.alignments)):
            label = self.Gtk.Label()
            label.set_selectable(True)
            label.set_line_wrap(True)
            label.set_line_wrap_mode(self.Pango.WrapMode.WORD_CHAR)
            label.set_width_chars(1)
            label.set_max_width_chars(1)
            label.set_xalign({'left': 0, 'center': .5, 'right': 1}[alignment])
            label.set_yalign(0)
            label.set_justify({'left': self.Gtk.Justification.LEFT, 'center': self.Gtk.Justification.CENTER,
                               'right': self.Gtk.Justification.RIGHT}[alignment])
            markup = []
            for span in parse_inline(cell):
                value = self.GLib.markup_escape_text(span.text)
                for name in span.styles:
                    if name in ('strong', 'emphasis', 'strike', 'code'):
                        tag = {'strong': 'b', 'emphasis': 'i', 'strike': 's', 'code': 'tt'}[name]
                        value = f'<{tag}>{value}</{tag}>'
                if span.link:
                    value = f'<a href="{self.GLib.markup_escape_text(span.link)}">{value}</a>'
                markup.append(value)
            label.set_markup(''.join(markup))
            label.connect('activate-link', lambda _label, uri: (self.activate_link(uri), True)[1])
            self._style_widget(label, 'md-cell', *(['md-header'] if block.header else []),
                               *(['md-first-cell'] if column == 0 else []))
            grid.attach(label, column, 0, 1, 1)
        return self._wide_widget(grid, block)

    def _code_tools(self, parts, block):
        box = self.Gtk.Box()
        self._style_widget(box, 'md-code-tools')
        button = self.Gtk.Button()
        button.set_image(mode_icon('copy'))
        button.set_tooltip_text('Copy code')
        button.get_accessible().set_name('Copy code')
        self._style_widget(button)
        box.pack_end(button, False, False, 0)
        button.connect('clicked', lambda *_: self._copy_code(button, parts))
        return self._wide_widget(box, block, 16)

    def _copy_code(self, button, parts):
        self.Gtk.Clipboard.get(self.Gdk.SELECTION_CLIPBOARD).set_text(''.join(parts), -1)
        button.set_image(mode_icon('copied'))
        button.set_tooltip_text('Copied!')
        button.get_accessible().set_name('Copied!')
        source = 0

        def reset():
            self._feedback.discard(source)
            if not self.destroyed:
                button.set_image(mode_icon('copy'))
                button.set_tooltip_text('Copy code')
                button.get_accessible().set_name('Copy code')
            return False

        source = self.GLib.timeout_add(1200, reset)
        self._feedback.add(source)

    def _step(self):
        deadline = time.perf_counter() + .008
        try:
            while time.perf_counter() < deadline:
                value, tags, link = next(self._work)
                if isinstance(value, self.Gtk.Widget):
                    anchor = self.buffer.create_child_anchor(self.buffer.get_end_iter())
                    self.view.add_child_at_anchor(value, anchor)
                    self._widgets.append(value)
                    value.show_all()
                    continue
                if isinstance(value, tuple):
                    a, b, name = value
                    self.buffer.apply_tag(self.tags[name], self.buffer.get_iter_at_offset(self._block_start + a),
                                          self.buffer.get_iter_at_offset(self._block_start + b))
                    continue
                start = self.buffer.get_char_count()
                self._block_start = start
                self.buffer.insert_with_tags(self.buffer.get_end_iter(), value, *(self.tags[name] for name in tags))
                if link:
                    self.links.append((start, start + len(value), link))
        except StopIteration:
            self._idle, self._work = 0, None
            done, self._done = self._done, None
            if done:
                done()
            return False
        return True

    def activate_link(self, target):
        resolved = link_target(target, self.source_path())
        if not resolved:
            return False
        kind, value = resolved
        if kind == 'file':
            self.open_file(value)
        else:
            top = self.view.get_toplevel()
            try:
                self.Gtk.show_uri_on_window(top if isinstance(top, self.Gtk.Window) else None, value, 0)
            except self.GLib.Error:
                return False
        return True

    def _release(self, view, event):
        if event.button != 1 or self.buffer.get_has_selection():
            return False
        x, y = view.window_to_buffer_coords(self.Gtk.TextWindowType.WIDGET, int(event.x), int(event.y))
        found, iterator = view.get_iter_at_location(x, y)
        if found:
            offset = iterator.get_offset()
            for a, b, target in self.links:
                if a <= offset < b:
                    return self.activate_link(target)
        return False


def mode_icon(mode):
    """Original eye/code strokes rasterized from the image's live CSS colour.

    BytesIcon SVGs are not classified as symbolic by GTK3, even with
    -gtk-icon-style: symbolic. Rebuild only when the inherited colour changes.
    """
    from gi.repository import Gtk, GdkPixbuf
    paths = {
        'copy': '<rect x="7" y="7" width="10" height="11" rx="1.5"/><path d="M13 7V3H3v11h4"/>',
        'copied': '<path d="m4 10 4 4 8-9"/>',
        'preview': '<path d="M2 10s3.5-6 8-6 8 6 8 6-3.5 6-8 6-8-6-8-6Z"/><circle cx="10" cy="10" r="2.7"/>',
        'source': '<path d="m6 5-5 5 5 5m8-10 5 5-5 5M12 3 8 17"/>',
    }
    image = Gtk.Image()
    previous = None

    def recolour(widget):
        nonlocal previous
        color = widget.get_style_context().get_color(widget.get_state_flags()).to_string()
        scale = widget.get_scale_factor()
        if (color, scale) == previous:
            return
        previous = color, scale
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20"><g fill="none" stroke="{color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">{paths[mode]}</g></svg>'
        loader = GdkPixbuf.PixbufLoader.new_with_type('svg')
        loader.write(svg.encode())
        loader.close()
        widget.set_from_pixbuf(loader.get_pixbuf())

    image.connect('style-updated', recolour)
    recolour(image)
    return image


def build_css(ui: dict[str, str]) -> str:
    """Theme the source/preview switch through the main application provider."""
    return f'''
.md-toggle {{ background: {ui['bg']}; border: 0; border-radius: 8px; padding: 3px; }}
.idgent-editor .md-toggle button {{ background: transparent; color: {ui['muted']};
    border: 0; border-radius: 4px; padding: 2px 4px; min-width: 20px; min-height: 20px;
    box-shadow: none; margin: 0; transition: none; }}
.idgent-editor .md-toggle button:hover {{ background: alpha({ui['overlay']}, .067); color: {ui['foreground']}; }}
.idgent-editor .md-toggle button:checked {{ background: alpha({ui['accent']}, .10); color: {ui['accent_text']}; }}
'''
