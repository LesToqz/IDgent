"""Editable Markdown decoration without inserting presentation into the source.

Text marks attach widgets to source ranges. Window children are intentional:
TextChildAnchors insert U+FFFC into a buffer, changing offsets, search and undo.
All rendering here is tags and TextView children; only explicit user edits write.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
import time

import gi

gi.require_version('Gtk', '3.0')
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango

from idgent import highlight, mdview

MAX_BYTES = 1024 * 1024
SLICE_SECONDS = .006
LIST = re.compile(r'^(\s*)([-+*]|\d+[.)])\s+(?:\[([ xX])\]\s+)?')
QUOTE = re.compile(r'^(\s*(?:> ?)+)')
WIKI = re.compile(r'\[\[([^\]\n|]+)(?:\|([^\]\n]*))?\]\]')
URL = re.compile(r'https?://[^\s<>]+')


@dataclass(eq=False)
class Decoration:
    start: Gtk.TextMark
    end: Gtk.TextMark
    kind: str
    text: str
    widgets: list = field(default_factory=list)
    tags: list = field(default_factory=list)
    links: list = field(default_factory=list)


class LiveMarkdown:
    def __init__(self, view, open_file, source_path):
        self.view, self.buffer = view, view.get_buffer()
        self.open_file, self.source_path = open_file, source_path
        self.tags, self.blocks = {}, []
        self.enabled = self.destroyed = False
        self.readable_width = True
        self._idle = self._caret_idle = self._layout_idle = 0
        self._work = None
        self._dirty = None
        self._dirty_end = None
        self._active = set()
        self._feedback = set()
        self.max_slice_time = 0
        self.painted_blocks = 0
        self.provider = Gtk.CssProvider()
        self.accent_provider = Gtk.CssProvider()
        self._accent = None
        self._handlers = [(self.buffer, self.buffer.connect('insert-text', self._insert)),
                          (self.buffer, self.buffer.connect('delete-range', self._delete)),
                          (self.buffer, self.buffer.connect('changed', self._changed)),
                          (self.buffer, self.buffer.connect('mark-set', self._mark_set)),
                          (view, view.connect('size-allocate', self._resize)),
                          (view, view.connect('style-updated', self._sync_accent)),
                          (view, view.connect('button-press-event', self._press)),
                          (view, view.connect('motion-notify-event', self._motion)),
                          (view, view.connect('realize', self._realize)),
                          (view, view.connect('unrealize', self._unrealize)),
                          (view, view.connect('destroy', lambda *_: self.destroy()))]
        view.add_events(Gdk.EventMask.POINTER_MOTION_MASK)
        self._frame = self._frame_id = None
        self.syntax, self.chrome, self.size = {}, {}, 11
        self.tag('hidden', invisible=True)

    @property
    def pending(self):
        return bool(self._idle or self._caret_idle or self._layout_idle)

    def tag(self, name, **properties):
        if name not in self.tags:
            self.tags[name] = self.buffer.get_tag_table().lookup('live-md-' + name) or self.buffer.create_tag('live-md-' + name)
        tag = self.tags[name]
        for key, value in properties.items():
            tag.set_property(key.replace('_', '-'), value)
        return tag

    def set_style(self, syntax, chrome, size):
        self.syntax, self.chrome, self.size = syntax, dict(chrome), size
        def color(kind):
            style = syntax.get(kind) or syntax.get('operator')
            rgba = Gdk.RGBA()
            return style.foreground if style and rgba.parse(style.foreground) else chrome['gutter_fg_active']
        bg, fg, border = chrome['gutter_bg'], color('operator'), chrome['gutter_fg']
        self.provider.load_from_data(f'''
textview.live-markdown, textview.live-markdown text {{background:{bg}; color:{fg}; font-family:sans-serif; font-size:{size}pt;}}
.live-md-cell {{color:{fg}; background:{bg}; font-family:sans-serif; font-size:{size}pt;
 border-right:1px solid {border}; border-bottom:1px solid {border}; padding:6px 8px;}}
.live-md-first {{border-left:1px solid {border};}}
.live-md-header {{background:{chrome['current_line_bg']}; border-top:1px solid {border}; font-weight:bold;}}
.live-md-tools {{background:{chrome['current_line_bg']}; color:{border}; font-size:10pt;}}
.live-md-tools button {{background:transparent; color:{border}; border:0; box-shadow:none; padding:3px 6px; min-height:20px;}}
.live-md-tools button:hover {{background:{bg}; color:{fg};}}
.live-md-rule {{background:{border}; min-height:1px; min-width:1px;}}
.live-md-marker {{color:{border}; background:transparent; font-size:{size}pt;}}
'''.encode())
        self.tag('body', pixels_above_lines=3, pixels_below_lines=8)
        for level, scale in enumerate((1.618, 1.462, 1.318, 1.188, 1.076, 1), 1):
            self.tag('h' + str(level), foreground=color('heading'), weight=700 if level <= 3 else 600,
                     scale=scale, pixels_above_lines=14, pixels_below_lines=12 if level < 3 else 8)
        self.tag('strong', foreground=color('strong'), weight=Pango.Weight.BOLD)
        self.tag('emphasis', foreground=color('emphasis'), style=Pango.Style.ITALIC)
        self.tag('strike', strikethrough=True)
        self.tag('checked', foreground=border, strikethrough=True)
        self.tag('code', family='monospace', background=chrome['current_line_bg'], foreground=color('string'))
        self.tag('codeblock', family='monospace', paragraph_background=chrome['current_line_bg'],
                 foreground=fg, pixels_above_lines=5, pixels_below_lines=5)
        self.tag('link', foreground=color('link'), underline=Pango.Underline.SINGLE)
        self.tag('image', foreground=border, background=chrome['current_line_bg'])
        for kind in highlight.KINDS:
            style = syntax.get(kind)
            valid = bool(style and Gdk.RGBA().parse(style.foreground))
            self.tag('syntax-' + kind, foreground=color(kind), foreground_set=valid,
                     weight=Pango.Weight.BOLD if style and style.bold else Pango.Weight.NORMAL,
                     style=Pango.Style.ITALIC if style and style.italic else Pango.Style.NORMAL)
        self._sync_accent()
        self._resize()
        if self.enabled:
            for block in self.blocks:
                for tag in block.tags:
                    tag.set_property('foreground', bg)
            self._queue_layout()

    def _sync_accent(self, *_):
        from idgent import style
        theme = style.current_theme()
        if theme is None or 'link' not in self.tags:
            return
        accent = theme.ui['accent'], theme.ui['accent_text'], theme.ui['bg']
        self.tags['link'].set_property('foreground', accent[1])
        if accent != self._accent:
            self._accent = accent
            self.accent_provider.load_from_data(f'''
.live-md-task check:checked {{background:{accent[0]}; border-color:{accent[0]}; color:{accent[2]};}}
.live-md-cell link {{color:{accent[1]};}}
'''.encode())

    def set_readable_width(self, enabled):
        self.readable_width = bool(enabled)
        self._resize()

    def set_enabled(self, enabled):
        enabled = bool(enabled)
        if self.enabled == enabled:
            return
        self.enabled = enabled
        context = self.view.get_style_context()
        if enabled:
            context.add_class('live-markdown')
            context.add_provider(self.provider, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
            self.view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            self.view.set_top_margin(24)
            self.view.set_bottom_margin(28)
            self._resize()
            self._restart_all()
        else:
            self.cancel()
            self._clear()
            context.remove_class('live-markdown')
            context.remove_provider(self.provider)
            self.view.set_wrap_mode(Gtk.WrapMode.NONE)
            self.view.set_left_margin(0)
            self.view.set_right_margin(12)
            self.view.set_top_margin(0)
            self.view.set_bottom_margin(0)

    def _realize(self, *_):
        self._frame = self.view.get_frame_clock()
        self._frame_id = self._frame.connect('after-paint', self._position)

    def _unrealize(self, *_):
        if self._frame_id:
            self._frame.disconnect(self._frame_id)
        self._frame = self._frame_id = None

    def _resize(self, *_):
        if not self.enabled:
            return
        width = self.view.get_allocated_width()
        margin = max(32, (width - 700 + 1) // 2) if self.readable_width else 32
        changed = self.view.get_left_margin() != margin
        self.view.set_left_margin(margin)
        self.view.set_right_margin(margin)
        for name, tag in self.tags.items():
            if name.startswith('indent-'):
                inset = int(name.split('-')[1])
                tag.set_property('left-margin', margin + inset)
                tag.set_property('right-margin', margin)
        if changed or any(b.widgets for b in self.blocks):
            self._queue_layout()

    def _index(self, offset):
        lo, hi = 0, len(self.blocks)
        while lo < hi:
            mid = (lo + hi) // 2
            if self._offset(self.blocks[mid].start) <= offset:
                lo = mid + 1
            else:
                hi = mid
        return max(0, lo - 1)

    def _offset(self, mark):
        return self.buffer.get_iter_at_mark(mark).get_offset()

    def _mark(self, offset):
        return self.buffer.create_mark(None, self.buffer.get_iter_at_offset(offset), True)

    def _invalidate(self, start, end):
        if not self.enabled:
            return
        if self.blocks:
            index = max(0, self._index(start) - 1)
            start = min(start, self._offset(self.blocks[index].start))
            last = min(len(self.blocks) - 1, self._index(end) + 1)
            end = max(end, self._offset(self.blocks[last].end))
        if self._dirty:
            start = min(start, self._offset(self._dirty))
            end = max(end, self._offset(self._dirty_end))
            self.buffer.delete_mark(self._dirty)
            self.buffer.delete_mark(self._dirty_end)
        self._dirty = self._mark(start)
        self._dirty_end = self.buffer.create_mark(None, self.buffer.get_iter_at_offset(end), False)

    def _insert(self, _buffer, at, text, _length):
        self._invalidate(at.get_offset(), at.get_offset())

    def _delete(self, _buffer, first, last):
        self._invalidate(first.get_offset(), last.get_offset())

    def _changed(self, *_):
        if not self.enabled:
            return
        if not self._dirty:
            self._invalidate(0, self.buffer.get_char_count())
        self._work = None
        if not self._idle:
            self._idle = GLib.idle_add(self._step)

    def _restart_all(self):
        self.cancel()
        self._clear()
        self._invalidate(0, self.buffer.get_char_count())
        self._changed()

    def cancel(self):
        for name in ('_idle', '_caret_idle', '_layout_idle'):
            source = getattr(self, name)
            if source:
                GLib.source_remove(source)
                setattr(self, name, 0)
        self._work = None
        for mark in (self._dirty, self._dirty_end):
            if mark:
                self.buffer.delete_mark(mark)
        self._dirty = self._dirty_end = None

    def _clear(self):
        for block in self.blocks:
            self._dispose(block)
        self.blocks.clear()
        self._active.clear()
        for source in self._feedback:
            GLib.source_remove(source)
        self._feedback.clear()

    def destroy(self):
        if self.destroyed:
            return
        self.set_enabled(False)
        self.cancel()
        self._unrealize()
        for obj, handler in self._handlers:
            obj.disconnect(handler)
        self._handlers.clear()
        for tag in self.tags.values():
            self.buffer.get_tag_table().remove(tag)
        self.tags.clear()
        self.destroyed = True

    def _line(self, offset):
        first = self.buffer.get_iter_at_offset(offset)
        last = first.copy()
        last.forward_line()
        if last.get_offset() <= offset:
            last = self.buffer.get_end_iter()
        return self.buffer.get_text(first, last, True), last.get_offset()

    def _parse(self, offset):
        limit = self.buffer.get_char_count()
        while offset < limit:
            start = offset
            line, offset = self._line(start)
            fence = mdview._FENCE.match(line.rstrip('\n'))
            kind, parts = 'line', [line]
            if fence:
                kind = 'codeblock'
                closing = re.compile(r'^ {0,3}' + re.escape(fence[1][0]) + '{' + str(len(fence[1])) + r',}\s*$')
                while offset < limit:
                    current, offset = self._line(offset)
                    parts.append(current)
                    yield None
                    if closing.fullmatch(current.rstrip('\n')):
                        break
            elif '|' in line and offset < limit:
                rule, after = self._line(offset)
                if mdview._TABLE_RULE.fullmatch(rule.rstrip('\n')) and len(mdview._cells(line)) == len(mdview._cells(rule)):
                    kind, offset = 'table', after
                    parts.append(rule)
                    while offset < limit:
                        current, after = self._line(offset)
                        if '|' not in current or not current.strip():
                            break
                        parts.append(current)
                        offset = after
                        yield None
            yield start, offset, kind, ''.join(parts)

    def _rebuild(self):
        start = self._offset(self._dirty)
        stop = self._offset(self._dirty_end)
        index = self._index(start) if self.blocks else 0
        while index and self._offset(self.blocks[index - 1].start) == start:
            index -= 1
        if self.blocks and self._offset(self.blocks[index].start) < start and self._offset(self.blocks[index].end) <= start:
            index += 1
        for parsed in self._parse(start):
            if parsed is None:
                yield
                continue
            a, b, kind, text = parsed
            if index < len(self.blocks):
                old = self.blocks[index]
                if (self._offset(old.start), self._offset(old.end), old.kind, old.text) == (a, b, kind, text) and a >= stop:
                    return
            while index < len(self.blocks) and self._offset(self.blocks[index].start) < b:
                self._dispose(self.blocks.pop(index))
                yield
            block = Decoration(self._mark(a), self._mark(b), kind, text)
            self.blocks.insert(index, block)
            yield from self._paint(block)
            index += 1
            yield
        while index < len(self.blocks):
            self._dispose(self.blocks.pop(index))
            yield

    def _step(self):
        started = time.perf_counter()
        if self._work is None:
            self._work = self._rebuild()
        try:
            while time.perf_counter() - started < SLICE_SECONDS:
                next(self._work)
        except StopIteration:
            self._work = None
            self._idle = 0
            for mark in (self._dirty, self._dirty_end):
                if mark:
                    self.buffer.delete_mark(mark)
            self._dirty = self._dirty_end = None
            self._queue_caret()
            self._queue_layout()
        self.max_slice_time = max(self.max_slice_time, time.perf_counter() - started)
        return bool(self._idle)

    def _apply(self, block, name, a, b):
        if b > a:
            self.buffer.apply_tag(self.tags[name], self.buffer.get_iter_at_offset(a), self.buffer.get_iter_at_offset(b))

    def _clear_paint(self, block):
        a, b = self.buffer.get_iter_at_mark(block.start), self.buffer.get_iter_at_mark(block.end)
        for tag in self.tags.values():
            self.buffer.remove_tag(tag, a, b)
        for tag in block.tags:
            self.buffer.remove_tag(tag, a, b)
            self.buffer.get_tag_table().remove(tag)
        block.tags.clear()
        for widget, *_ in block.widgets:
            widget.destroy()
        block.widgets.clear()
        block.links.clear()
        self._active.discard(block)

    def _dispose(self, block):
        self._clear_paint(block)
        self.buffer.delete_mark(block.start)
        self.buffer.delete_mark(block.end)

    def _indent(self, block, a, b, inset, hanging=0):
        name = f'indent-{inset}-{hanging}'
        self.tag(name, left_margin=self.view.get_left_margin() + inset,
                 right_margin=self.view.get_right_margin(), indent=-hanging)
        self._apply(block, name, a, b)

    def _widget(self, block, widget, offset, role, data=None):
        widget.set_no_show_all(True)
        widget.get_style_context().add_provider(self.provider, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
        widget.get_style_context().add_provider(self.accent_provider, Gtk.STYLE_PROVIDER_PRIORITY_USER + 2)
        self.view.add_child_in_window(widget, Gtk.TextWindowType.WIDGET, 0, 0)
        block.widgets.append((widget, offset - self._offset(block.start), role, data))
        return widget

    def _paint(self, block):
        a, b = self._offset(block.start), self._offset(block.end)
        cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        active = a <= cursor.get_offset() < b or (b == cursor.get_offset() == self.buffer.get_char_count() and not block.text.endswith('\n'))
        if active:
            self._active.add(block)
        self.painted_blocks += 1
        if block.kind == 'codeblock':
            yield from self._code(block, active)
            return
        if block.kind == 'table':
            if not active:
                yield from self._table(block)
            return
        line = block.text.rstrip('\n')
        self._apply(block, 'body', a, b)
        heading = mdview._HEADING.match(line)
        quote, item = QUOTE.match(line), LIST.match(line)
        content = 0
        if heading:
            self._apply(block, 'h' + str(len(heading[1])), a, b)
            content = heading.start(2)
            if not active:
                self._apply(block, 'hidden', a, a + content)
                tail = re.search(r'\s+#+\s*$', line)
                if tail:
                    self._apply(block, 'hidden', a + tail.start(), a + len(line))
            if len(heading[1]) < 3:
                rule = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
                rule.get_style_context().add_class('live-md-rule')
                self._widget(block, rule, a, 'heading-rule')
        elif mdview._RULE.fullmatch(line):
            if not active:
                self._replacement(block, Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), 14, 'rule')
            return
        elif quote:
            content = quote.end()
            self._indent(block, a, b, 22 * line[:content].count('>'))
            if not active:
                self._apply(block, 'hidden', a, a + content)
            bar = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
            bar.get_style_context().add_class('live-md-rule')
            self._widget(block, bar, a, 'quote')
        elif item:
            content = item.end()
            depth = len(item[1].expandtabs(4))
            self._indent(block, a, b, depth * 5 + 24, 24 if active else 0)
            if item[3] and item[3].lower() == 'x':
                self._apply(block, 'checked', a + content, a + len(line))
            if not active:
                self._apply(block, 'hidden', a, a + content)
                if item[3] is not None:
                    check = Gtk.CheckButton()
                    check.get_style_context().add_class('live-md-task')
                    check.set_active(item[3].lower() == 'x')
                    check.set_can_focus(False)
                    check.set_tooltip_text('Toggle task')
                    check.get_accessible().set_name('Toggle task')
                    check.connect('toggled', lambda widget: self.toggle_task(block, item.start(3)))
                    self._widget(block, check, a, 'marker', depth * 5)
                else:
                    marker = Gtk.Label(label='•' if item[2] in '-+*' else item[2])
                    marker.get_style_context().add_class('live-md-marker')
                    self._widget(block, marker, a, 'marker', depth * 5)
        yield from self._inline(block, line[content:], a + content, active)

    def _inline(self, block, text, base, active, depth=0):
        if depth > 12:
            return
        covered = []
        urls = [(match.start(), match.end()) for match in URL.finditer(text)]
        for match in mdview._INLINE.finditer(text):
            a, b = base + match.start(), base + match.end()
            escape, ticks, code, image, label, target, auto, delimiter, body = match.groups()
            if delimiter and any(first <= match.start() < last for first, last in urls):
                continue
            covered.append((match.start(), match.end()))
            if escape is not None:
                if not active:
                    self._apply(block, 'hidden', a, a + 1)
            elif ticks:
                self._apply(block, 'code', a, b)
                if not active:
                    self._apply(block, 'hidden', a, a + len(ticks))
                    self._apply(block, 'hidden', b - len(ticks), b)
            elif label is not None:
                start = base + match.start(5)
                end = base + match.end(5)
                self._apply(block, 'image' if image else 'link', start, end)
                if not image:
                    block.links.append((a - self._offset(block.start), b - self._offset(block.start), target.strip('<>'), active))
                if not active:
                    self._apply(block, 'hidden', a, start)
                    self._apply(block, 'hidden', end, b)
                    if image:
                        self._image(block, a, b, target.strip('<>'))
                if not image:
                    yield from self._inline(block, label, start, active, depth + 1)
            elif auto:
                self._apply(block, 'link', a + 1, b - 1)
                block.links.append((a - self._offset(block.start), b - self._offset(block.start), auto, active))
                if not active:
                    self._apply(block, 'hidden', a, a + 1)
                    self._apply(block, 'hidden', b - 1, b)
            elif delimiter:
                kinds = ('strong', 'emphasis') if len(delimiter) == 3 else ({'**': 'strong', '__': 'strong', '*': 'emphasis', '_': 'emphasis', '~~': 'strike'}[delimiter],)
                for name in kinds:
                    self._apply(block, name, a, b)
                if not active:
                    self._apply(block, 'hidden', a, a + len(delimiter))
                    self._apply(block, 'hidden', b - len(delimiter), b)
                yield from self._inline(block, body, base + match.start(9), active, depth + 1)
            yield
        for regex, wiki in ((WIKI, True), (URL, False)):
            for match in regex.finditer(text):
                if any(start <= match.start() < end for start, end in covered):
                    continue
                a, b = base + match.start(), base + match.end()
                target = '[[' + match[1] + ']]' if wiki else match[0].rstrip('.,;!?)')
                self._apply(block, 'link', a, b)
                block.links.append((a - self._offset(block.start), b - self._offset(block.start), target, active))
                if wiki and not active:
                    start = base + (match.start(2) if match[2] is not None else match.start(1))
                    end = base + (match.end(2) if match[2] is not None else match.end(1))
                    self._apply(block, 'hidden', a, start)
                    self._apply(block, 'hidden', end, b)
                yield

    def _code(self, block, active):
        a, b = self._offset(block.start), self._offset(block.end)
        lines = block.text.splitlines(keepends=True)
        fence = mdview._FENCE.match(lines[0].rstrip('\n'))
        language = fence[2].strip().split()[0] if fence[2].strip() else ''
        closed = len(lines) > 1 and re.fullmatch(r' {0,3}' + re.escape(fence[1][0]) + '{' + str(len(fence[1])) + r',}\s*', lines[-1])
        start = a + len(lines[0])
        end = b - len(lines[-1]) if closed else b
        self._apply(block, 'codeblock', a, b)
        self._indent(block, a, b, 16)
        if not active:
            self._apply(block, 'hidden', a, start - 1)
            if closed:
                self._apply(block, 'hidden', end, b - (1 if block.text.endswith('\n') else 0))
        tools = Gtk.Box(spacing=8)
        tools.get_style_context().add_class('live-md-tools')
        tools.pack_start(Gtk.Label(label=language), False, False, 0)
        button = Gtk.Button(label='Copy')
        button.set_can_focus(False)
        button.set_tooltip_text('Copy code')
        button.get_accessible().set_name('Copy code')
        button.connect('clicked', lambda *_: self._copy_code(button, block, len(lines[0]), end - a))
        tools.pack_end(button, False, False, 0)
        tools.show_all()
        self._widget(block, tools, a, 'tools')
        lexer_class = highlight._class_by_name(language) if language and highlight.AVAILABLE else None
        lexer = lexer_class(**highlight._LEXER_OPTIONS) if lexer_class else None
        offset = start
        # Per-line lexing bounds tokenization work even for long fenced blocks.
        for line in lines[1:-1] if closed else lines[1:]:
            for chunk in range(0, len(line), 2048):
                part = line[chunk:chunk + 2048]
                for first, last, kind in highlight.classify(lexer, part):
                    self._apply(block, 'syntax-' + kind, offset + chunk + first, offset + chunk + last)
                    yield
                yield
            offset += len(line)

    def _copy_code(self, button, block, start, end):
        a = self._offset(block.start)
        text = self.buffer.get_text(self.buffer.get_iter_at_offset(a + start), self.buffer.get_iter_at_offset(a + end), True)
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(text, -1)
        button.set_label('Copied')
        button.set_tooltip_text('Copied!')
        source = 0
        def reset():
            self._feedback.discard(source)
            if not button.in_destruction():
                button.set_label('Copy')
                button.set_tooltip_text('Copy code')
            return False
        source = GLib.timeout_add(1200, reset)
        self._feedback.add(source)

    def _replacement(self, block, widget, height, role):
        a, b = self._offset(block.start), self._offset(block.end)
        self._apply(block, 'hidden', a + 1, b - (1 if block.text.endswith('\n') else 0))
        tag = self.buffer.create_tag(None, size_points=1, foreground=self.chrome['gutter_bg'],
                                     pixels_above_lines=height, pixels_below_lines=8)
        block.tags.append(tag)
        self.buffer.apply_tag(tag, self.buffer.get_iter_at_offset(a), self.buffer.get_iter_at_offset(a + 1))
        if role == 'rule':
            widget.get_style_context().add_class('live-md-rule')
        self._widget(block, widget, a, role, height)

    def _table(self, block):
        lines = block.text.splitlines()
        rules = mdview._cells(lines[1])
        count = len(rules)
        grid = Gtk.Grid(column_homogeneous=True)
        grid.get_style_context().add_class('live-md-table')
        offsets, offset = [0], len(lines[0]) + 1 + len(lines[1]) + 1
        for line in lines[2:]:
            offsets.append(offset)
            offset += len(line) + 1
        for row, line in enumerate([lines[0]] + lines[2:]):
            cells = mdview._cells(line)
            for column in range(count):
                cell = cells[column] if column < len(cells) else ''
                label = Gtk.Label()
                label.set_line_wrap(True)
                label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
                label.set_width_chars(1)
                label.set_max_width_chars(1)
                alignment = .5 if rules[column].startswith(':') and rules[column].endswith(':') else 1 if rules[column].endswith(':') else 0
                label.set_xalign(alignment)
                label.set_yalign(0)
                markup = []
                for span in mdview.parse_inline(cell):
                    value = GLib.markup_escape_text(span.text)
                    for name in span.styles:
                        if name in ('strong', 'emphasis', 'strike', 'code'):
                            tag = {'strong': 'b', 'emphasis': 'i', 'strike': 's', 'code': 'tt'}[name]
                            value = f'<{tag}>{value}</{tag}>'
                    if span.link:
                        value = f'<a href="{GLib.markup_escape_text(span.link)}">{value}</a>'
                    markup.append(value)
                label.set_markup(''.join(markup))
                label.connect('activate-link', lambda _label, uri: (self.activate_link(uri), True)[1])
                label.get_style_context().add_class('live-md-cell')
                if row == 0:
                    label.get_style_context().add_class('live-md-header')
                if column == 0:
                    label.get_style_context().add_class('live-md-first')
                label.get_style_context().add_provider(self.provider, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
                label.get_style_context().add_provider(self.accent_provider, Gtk.STYLE_PROVIDER_PRIORITY_USER + 2)
                box = Gtk.EventBox()
                box.add(label)
                cell_offset = offsets[row] + max(0, line.find(cell))
                box.connect('button-press-event', lambda _w, event, at=cell_offset: self._enter_table(block, at, event))
                grid.attach(box, column, row, 1, 1)
                yield
        grid.show_all()
        width = self._column_width()
        self._size_grid(grid, width)
        height = grid.get_preferred_height_for_width(width)[1]
        self._replacement(block, grid, height, 'table')

    def _enter_table(self, block, at, event):
        if event.button != 1:
            return False
        self.buffer.place_cursor(self.buffer.get_iter_at_offset(self._offset(block.start) + at))
        self.view.grab_focus()
        return True

    def _image(self, block, a, b, target):
        resolved = mdview.link_target(target, self.source_path())
        if not resolved or resolved[0] != 'file':
            return
        try:
            width = self._column_width()
            info, natural_width, _height = GdkPixbuf.Pixbuf.get_file_info(resolved[1])
            if info is None:
                return
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(resolved[1], min(width, natural_width), -1, True)
        except GLib.Error:
            return
        image = Gtk.Image.new_from_pixbuf(pixbuf)
        self._apply(block, 'hidden', a + 1, b)
        tag = self.buffer.create_tag(None, size_points=1, foreground=self.chrome['gutter_bg'], pixels_above_lines=pixbuf.get_height() + 8)
        block.tags.append(tag)
        image._md_image = resolved[1], natural_width, tag
        self.buffer.apply_tag(tag, self.buffer.get_iter_at_offset(a), self.buffer.get_iter_at_offset(a + 1))
        # Override the link delimiter's hidden tag for the reserved image line.
        self.buffer.remove_tag(self.tags['hidden'], self.buffer.get_iter_at_offset(a), self.buffer.get_iter_at_offset(a + 1))
        self._widget(block, image, a, 'image', pixbuf.get_height())

    def toggle_task(self, block, relative):
        at = self._offset(block.start) + relative
        first = self.buffer.get_iter_at_offset(at)
        value = 'x' if first.get_char() == ' ' else ' '
        last = first.copy()
        last.forward_char()
        self.buffer.begin_user_action()
        try:
            self.buffer.delete(first, last)
            self.buffer.insert(self.buffer.get_iter_at_offset(at), value)
        finally:
            self.buffer.end_user_action()

    def _mark_set(self, _buffer, _at, mark):
        if self.enabled and mark in (self.buffer.get_insert(), self.buffer.get_selection_bound()):
            self._queue_caret()

    def _queue_caret(self):
        if not self._caret_idle and self.enabled:
            self._caret_idle = GLib.idle_add(self._caret)

    def _caret(self):
        self._caret_idle = 0
        if self._idle or not self.blocks:
            return False
        offset = self._offset(self.buffer.get_insert())
        index = self._index(offset)
        block = self.blocks[index]
        active = {block} if self._offset(block.start) <= offset < self._offset(block.end) or (
            offset == self._offset(block.end) == self.buffer.get_char_count() and not block.text.endswith('\n')) else set()
        for current in self._active.symmetric_difference(active).copy():
            self._clear_paint(current)
            # Large code blocks need the same bounded work as initial painting.
            if current.kind == 'codeblock':
                self._invalidate(self._offset(current.start), self._offset(current.end))
                self._changed()
            else:
                for _ in self._paint(current):
                    pass
        self._active = active
        self._queue_layout()
        return False

    def _column_width(self):
        return max(32, self.view.get_allocated_width() - self.view.get_left_margin() - self.view.get_right_margin() - 2)

    @staticmethod
    def _size_grid(grid, width):
        columns = max((grid.child_get_property(child, 'left-attach') for child in grid.get_children()), default=0) + 1
        grid.set_size_request(width, -1)
        for child in grid.get_children():
            child.get_child().set_size_request(max(1, width // columns - 18), -1)

    def _queue_layout(self):
        if not self._layout_idle:
            self._layout_idle = GLib.idle_add(self._layout, priority=GLib.PRIORITY_LOW)

    def _layout(self):
        self._layout_idle = 0
        if not self.enabled:
            return False
        for block in self.blocks:
            for widget, _, role, _ in block.widgets:
                if role == 'table':
                    self._size_grid(widget, self._column_width())
                    height = widget.get_preferred_height_for_width(self._column_width())[1]
                    if block.tags:
                        block.tags[0].set_property('pixels-above-lines', height)
                elif role == 'image':
                    path, natural_width, tag = widget._md_image
                    width = min(natural_width, self._column_width())
                    if widget.get_pixbuf().get_width() != width:
                        try:
                            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, width, -1, True)
                        except GLib.Error:
                            continue
                        widget.set_from_pixbuf(pixbuf)
                        tag.set_property('pixels-above-lines', pixbuf.get_height() + 8)
        self._position()
        return False

    def _position(self, *_):
        if not self.enabled or not self.view.get_realized():
            return
        width = self._column_width()
        top, bottom = self.view.get_visible_rect().y, self.view.get_visible_rect().y + self.view.get_visible_rect().height
        for block in self.blocks:
            if not block.widgets:
                continue
            start = self._offset(block.start)
            for widget, relative, role, data in block.widgets:
                iterator = self.buffer.get_iter_at_offset(start + relative)
                rect = self.view.get_iter_location(iterator)
                y, x = rect.y, self.view.get_left_margin()
                height = widget.get_preferred_height()[1]
                if role == 'tools':
                    x += width - widget.get_preferred_width()[1]
                elif role == 'marker':
                    x += data
                elif role in ('table', 'image', 'rule'):
                    height = (block.tags[0].get_property('pixels-above-lines') if role == 'table' else
                              widget.get_pixbuf().get_height() if role == 'image' else data)
                    y -= height
                    widget.set_size_request(width if role != 'image' else -1, height)
                elif role == 'heading-rule':
                    y += rect.height + 5
                    widget.set_size_request(width, 1)
                elif role == 'quote':
                    widget.set_size_request(2, rect.height)
                    x += 4
                visible = y + height >= top and y <= bottom
                widget.set_visible(visible)
                if visible:
                    x, y = self.view.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, x, y)
                    allocation = widget.get_allocation()
                    if (allocation.x, allocation.y) != (x, y):
                        self.view.move_child(widget, x, y)

    def _link_at(self, event):
        x, y = self.view.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, int(event.x), int(event.y))
        found, iterator = self.view.get_iter_at_location(x, y)
        if not found or not self.blocks:
            return None
        offset = iterator.get_offset()
        block = self.blocks[self._index(offset)]
        relative = offset - self._offset(block.start)
        for a, b, target, revealed in block.links:
            if a <= relative < b:
                return target, revealed
        return None

    def _press(self, _view, event):
        if not self.enabled or event.button != 1:
            return False
        link = self._link_at(event)
        if link and (not link[1] or event.state & Gdk.ModifierType.CONTROL_MASK):
            return self.activate_link(link[0])
        return False

    def _motion(self, _view, event):
        if not self.enabled:
            return False
        link = self._link_at(event)
        window = self.view.get_window(Gtk.TextWindowType.TEXT)
        if window:
            window.set_cursor(Gdk.Cursor.new_from_name(window.get_display(), 'pointer' if link else 'text'))
        return False

    def activate_link(self, target):
        resolved = mdview.link_target(target, self.source_path())
        if not resolved:
            return False
        kind, value = resolved
        if kind == 'file':
            self.open_file(value)
        else:
            top = self.view.get_toplevel()
            try:
                Gtk.show_uri_on_window(top if isinstance(top, Gtk.Window) else None, value, 0)
            except GLib.Error:
                return False
        return True

    def edit_key(self, event, indent_tabs=False, indent_width=4):
        if not self.enabled:
            return False
        if event.state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK | Gdk.ModifierType.SUPER_MASK):
            return False
        if self.buffer.get_has_selection():
            return self._indent_selection(event, indent_tabs, indent_width)
        cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        block = self.blocks[self._index(cursor.get_offset())] if self.blocks else None
        if block and block.kind in ('codeblock', 'table'):
            return False
        first = cursor.copy()
        first.set_line_offset(0)
        last = first.copy()
        last.forward_to_line_end()
        line = self.buffer.get_text(first, last, True)
        match = LIST.match(line) or QUOTE.match(line)
        if not match:
            return False
        key, start = event.keyval, first.get_offset()
        item = LIST.match(line)
        a, b, replacement = cursor.get_offset(), cursor.get_offset(), None
        if key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if cursor.get_line_offset() < match.end():
                return False
            if not line[match.end():].strip():
                a, b, replacement = start, start + len(line), ''
            else:
                marker = match[0]
                if item:
                    token = item[2]
                    if token[0].isdigit():
                        token = str(int(token[:-1]) + 1) + token[-1]
                    marker = item[1] + token + (' [ ] ' if item[3] is not None else ' ')
                replacement = '\n' + marker
        elif key in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab) and item:
            a = b = start
            if key == Gdk.KEY_ISO_Left_Tab or event.state & Gdk.ModifierType.SHIFT_MASK:
                b += 1 if line.startswith('\t') else min(indent_width, len(line) - len(line.lstrip(' ')))
                replacement = ''
            else:
                replacement = '\t' if indent_tabs else ' ' * indent_width
        elif key == Gdk.KEY_BackSpace and cursor.get_line_offset() == match.end():
            a, b, replacement = start, cursor.get_offset(), ''
        if replacement is None:
            return False
        old_cursor = cursor.get_offset()
        self.buffer.begin_user_action()
        try:
            self.buffer.delete(self.buffer.get_iter_at_offset(a), self.buffer.get_iter_at_offset(b))
            self.buffer.insert(self.buffer.get_iter_at_offset(a), replacement)
            position = a + len(replacement) if key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_BackSpace) else old_cursor + len(replacement) - (b - a)
            self.buffer.place_cursor(self.buffer.get_iter_at_offset(max(a, position)))
        finally:
            self.buffer.end_user_action()
        return True

    def _indent_selection(self, event, tabs, width):
        if event.keyval not in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab):
            return False
        first, last = self.buffer.get_selection_bounds()
        first.set_line_offset(0)
        if last.get_line_offset() == 0:
            last.backward_char()
        last.forward_to_line_end()
        text = self.buffer.get_text(first, last, True)
        lines = text.split('\n')
        if not all(not line.strip() or LIST.match(line) for line in lines):
            return False
        outdent = event.keyval == Gdk.KEY_ISO_Left_Tab or event.state & Gdk.ModifierType.SHIFT_MASK
        def replace(line):
            if not line.strip():
                return line
            if outdent:
                amount = 1 if line.startswith('\t') else min(width, len(line) - len(line.lstrip(' ')))
                return line[amount:]
            return ('\t' if tabs else ' ' * width) + line
        replacement = '\n'.join(replace(line) for line in lines)
        offset = first.get_offset()
        self.buffer.begin_user_action()
        try:
            self.buffer.delete(first, last)
            self.buffer.insert(self.buffer.get_iter_at_offset(offset), replacement)
            self.buffer.select_range(self.buffer.get_iter_at_offset(offset + len(replacement)),
                                     self.buffer.get_iter_at_offset(offset))
        finally:
            self.buffer.end_user_action()
        return True
