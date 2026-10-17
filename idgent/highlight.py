"""VS Code-like syntax highlighting for Gtk.TextBuffer, backed by optional Pygments.

Pygments only provides the lexers: token types are folded into a small set of VS Code-like
*kinds* (``KINDS``), and each kind is painted by one ``hl-<kind>`` Gtk.TextTag whose look comes
from a plain ``dict[str, SyntaxStyle]`` such as ``DARK_PLUS``.  When Pygments cannot be imported
``AVAILABLE`` is False, ``lexer_for`` returns None and everything degrades to plain text.
"""
from __future__ import annotations

import os
import re
import time
from typing import Iterator, NamedTuple

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, GLib, Gtk, Pango

try:
    from pygments.lexers import find_lexer_class_by_name, find_lexer_class_for_filename
    from pygments.lexers.special import TextLexer
    from pygments.token import Token
    from pygments.util import ClassNotFound
    AVAILABLE = True
except Exception:  # ImportError, or a broken install: highlighting is optional
    AVAILABLE = False

KINDS = ('comment', 'doc', 'string', 'escape', 'regex', 'number', 'keyword', 'control', 'operator',
         'constant', 'builtin', 'function', 'class', 'decorator', 'variable', 'property', 'tag',
         'attribute', 'selector', 'heading', 'strong', 'emphasis', 'link', 'preproc', 'punctuation',
         'error')
DEFAULT_TEXT_COLOR = '#D4D4D4'

DEBOUNCE_MS = 120
TOKEN_SECONDS = 0.004   # lexing budget of one idle slice; applying its tags costs about as much again
MAX_BYTES = 1_500_000
MAX_LINE = 20_000


class SyntaxStyle(NamedTuple):
    foreground: str
    bold: bool = False
    italic: bool = False
    underline: bool = False


DARK_PLUS: dict[str, SyntaxStyle] = {
    'comment': SyntaxStyle('#6A9955'),
    'doc': SyntaxStyle('#CE9178'),
    'string': SyntaxStyle('#CE9178'),
    'escape': SyntaxStyle('#D7BA7D'),
    'regex': SyntaxStyle('#D16969'),
    'number': SyntaxStyle('#B5CEA8'),
    'keyword': SyntaxStyle('#569CD6'),
    'control': SyntaxStyle('#C586C0'),
    'operator': SyntaxStyle('#D4D4D4'),
    'constant': SyntaxStyle('#569CD6'),
    'builtin': SyntaxStyle('#DCDCAA'),
    'function': SyntaxStyle('#DCDCAA'),
    'class': SyntaxStyle('#4EC9B0'),
    'decorator': SyntaxStyle('#DCDCAA'),
    'variable': SyntaxStyle('#9CDCFE'),
    'property': SyntaxStyle('#9CDCFE'),
    'tag': SyntaxStyle('#569CD6'),
    'attribute': SyntaxStyle('#9CDCFE'),
    'selector': SyntaxStyle('#D7BA7D'),
    'heading': SyntaxStyle('#569CD6', bold=True),
    'strong': SyntaxStyle('#569CD6', bold=True),
    'emphasis': SyntaxStyle('#D4D4D4', italic=True),
    'link': SyntaxStyle('#3794FF', underline=True),
    'preproc': SyntaxStyle('#C586C0'),
    'punctuation': SyntaxStyle('#808080'),
    'error': SyntaxStyle('#F44747'),
}

# --------------------------------------------------------------------------- lexer detection

_LEXER_OPTIONS = {'stripnl': False, 'stripall': False, 'ensurenl': False, 'tabsize': 0}
_PLAIN = 'plain'
_BY_BASENAME = {
    'dockerfile': 'docker', 'containerfile': 'docker', 'makefile': 'make', 'gnumakefile': 'make',
    'cmakelists.txt': 'cmake', '.bashrc': 'bash', '.bash_profile': 'bash', '.bash_aliases': 'bash',
    '.bash_logout': 'bash', '.zshrc': 'bash', '.zprofile': 'bash', '.zshenv': 'bash',
    '.zlogin': 'bash', '.profile': 'bash', '.env': 'bash', '.gitignore': _PLAIN,
    '.dockerignore': _PLAIN, '.gitattributes': _PLAIN,
}
_BY_EXTENSION = {
    '.pyi': 'python', '.pyw': 'python', '.tsx': 'tsx', '.jsx': 'jsx', '.sql': 'sql',
    '.svg': 'xml', '.jsonc': 'json', '.pl': 'perl', '.pm': 'perl', '.txt': _PLAIN, '.log': _PLAIN,
}
_SHEBANG = re.compile(r'#!\s*(\S+)[ \t]*(.*)')
_INTERPRETERS = {
    'sh': 'bash', 'bash': 'bash', 'zsh': 'bash', 'dash': 'bash', 'ash': 'bash', 'ksh': 'bash',
    'node': 'javascript', 'nodejs': 'javascript', 'deno': 'typescript', 'bun': 'javascript',
}
_DISPLAY_NAMES = {'Bash': 'Shell Script', 'Docker': 'Dockerfile', 'TSX': 'TypeScript React',
                  'JSX': 'JavaScript React'}
_instances: dict[type, object] = {}


def _class_by_name(alias: str):
    try:
        return find_lexer_class_by_name(alias)
    except ClassNotFound:
        return None


def _interpreter(first_line: str) -> str:
    match = _SHEBANG.match(first_line)
    if not match:
        return ''
    program = os.path.basename(match.group(1))
    if program == 'env':
        rest = [word for word in match.group(2).split() if not word.startswith('-') and '=' not in word]
        program = os.path.basename(rest[0]) if rest else ''
    return re.sub(r'[\d.]+$', '', program).lower()


def lexer_for(path: str, first_line: str = ''):
    """Return a cached Pygments lexer for a path (and shebang line), or None for plain text."""
    if not AVAILABLE:
        return None
    name = os.path.basename(path).lower()
    extension = os.path.splitext(name)[1]
    lexer_class = None
    alias = _BY_BASENAME.get(name) or (_BY_BASENAME['.env'] if name.startswith('.env.') else None) \
        or (_BY_BASENAME['dockerfile'] if name.startswith('dockerfile.') else None) \
        or _BY_EXTENSION.get(extension)
    if alias == _PLAIN:
        return None
    if alias:
        lexer_class = _class_by_name(alias)
    if lexer_class is None and name:
        lexer_class = find_lexer_class_for_filename(name)
    if lexer_class is None and not extension:
        interpreter = _interpreter(first_line)
        alias = _INTERPRETERS.get(interpreter, interpreter)
        lexer_class = _class_by_name(alias) if alias else None
    if lexer_class is None or lexer_class is TextLexer:
        return None
    if lexer_class not in _instances:
        _instances[lexer_class] = lexer_class(**_LEXER_OPTIONS)
    return _instances[lexer_class]


def language_name(lexer) -> str:
    return _DISPLAY_NAMES.get(lexer.name, lexer.name) if lexer is not None else 'Plain Text'


# --------------------------------------------------------------------------- token mapping

_LETTER = re.compile(r'[^\W\d_]').search
_CAMEL = re.compile(r'_*[A-Z]\w*[a-z]\w*$').match
_NUMBER = re.compile(r'[-+]?(?:0x[0-9a-fA-F_]+|\d[\d_]*(?:\.\d*)?(?:[eE][-+]?\d+)?|\.\d+)$').match
_DOTTED = re.compile(r'@[\w.]+').match
_LIST_MARKER = re.compile(r'\s*(?:[-*+]|\d+[.)]|>)\s*$').match

CONTROL = frozenset(
    'if elif else elseif for foreach while do return try except catch finally throw raise yield await '
    'break continue pass switch case default goto with assert import from export as match when then '
    'fi done esac until unless rescue ensure defer loop select of end begin endif'.split())
_CLASS_INTRODUCERS = frozenset('class extends new implements struct enum interface trait impl '
                               'instanceof union namespace type'.split())
_FUNCTION_INTRODUCERS = frozenset('function def fn func fun sub proc defun'.split())
_TYPE_BUILTINS = frozenset('int str float bool bytes bytearray list dict set frozenset tuple object '
                           'type complex memoryview slice'.split())
_CONSTANT_BUILTINS = frozenset(['NULL', 'nil', 'null', 'Ellipsis', 'NotImplemented', '__debug__'])
_YAML_CONSTANTS = frozenset('true false null yes no on off ~ True False Null NULL Yes No On Off'.split())
_MARKUP_PUNCTUATION = frozenset(['<', '>', '/', '</', '/>', '<?', '?>'])
_NO_BRIDGE = frozenset(['link'])    # whitespace between two runs of these must not be painted

if AVAILABLE:
    _BASE = {
        Token.Comment: 'comment', Token.Comment.Preproc: 'preproc',
        Token.Comment.PreprocFile: 'string',
        Token.Literal.String: 'string', Token.Literal.String.Doc: 'sdoc',
        Token.Literal.String.Escape: 'escape', Token.Literal.String.Regex: 'regex',
        Token.Literal.String.Interpol: 'keyword', Token.Literal.Number: 'number',
        Token.Literal.Date: 'number',
        Token.Keyword: 'kw', Token.Keyword.Constant: 'constant', Token.Keyword.Namespace: 'control',
        Token.Keyword.Type: 'class', Token.Operator: 'operator', Token.Operator.Word: 'keyword',
        Token.Name: 'ident', Token.Name.Other: 'ident', Token.Name.Attribute: 'attribute',
        Token.Name.Builtin: 'builtin_name', Token.Name.Builtin.Pseudo: 'pseudo',
        Token.Name.Class: 'class', Token.Name.Constant: 'constant',
        Token.Name.Decorator: 'decorator', Token.Name.Entity: 'constant',
        Token.Name.Exception: 'class', Token.Name.Function: 'function',
        Token.Name.Label: 'variable', Token.Name.Namespace: 'variable',
        Token.Name.Property: 'property', Token.Name.Tag: 'tag', Token.Name.Variable: 'variable',
        Token.Generic.Heading: 'heading', Token.Generic.Subheading: 'heading',
        Token.Generic.Strong: 'strong', Token.Generic.Emph: 'emphasis',
        Token.Generic.Deleted: 'regex', Token.Generic.Inserted: 'number',
        Token.Generic.Error: 'error', Token.Generic.Traceback: 'error',
    }
    _OVERRIDES = {
        'json': {Token.Name.Tag: 'property'},
        'yaml': {Token.Name.Tag: 'property', Token.Name.Constant: 'string',
                 Token.Name.Variable: 'yamlval', Token.Literal.Scalar.Plain: 'yamlval',
                 Token.Name.Label: 'variable', Token.Punctuation.Indicator: None},
        'toml': {Token.Keyword: 'tag', Token.Keyword.Constant: 'constant', Token.Name: 'property'},
        'markdown': {Token.Keyword: 'mdkw', Token.Generic.Subheading: 'mdhead',
                     Token.Name.Tag: 'mdtag', Token.Name.Attribute: 'mdattr'},
        'markup': {Token.Comment.Preproc: 'tag', Token.Punctuation: 'mpunct'},
        'css': {Token.Name: 'cssname', Token.Name.Tag: 'cssname', Token.Name.Class: 'selector',
                Token.Name.Namespace: 'selector', Token.Name.Decorator: 'selector',
                Token.Name.Builtin: 'function', Token.Keyword: 'csskw', Token.Keyword.Constant: 'string',
                Token.Keyword.Type: 'number', Token.Comment.Preproc: 'keyword',
                Token.Error: None},
        'c': {Token.Keyword.Type: 'keyword'},
        'rust': {Token.Comment.Preproc: 'decorator'},
    }
    _tables: dict[str, tuple[dict, dict]] = {}


def _table(family: str) -> tuple[dict, dict]:
    """The token-type -> kind table of a family, plus its memo of resolved hierarchy walks."""
    if family not in _tables:
        _tables[family] = ({**_BASE, **_OVERRIDES.get(family, {})}, {})
    return _tables[family]


def _family(lexer) -> str:
    name = lexer.name
    if name.startswith('Python'):
        return 'python'
    if 'JSON' in name:
        return 'json'
    if name in ('YAML', 'TOML', 'Markdown', 'Rust'):
        return name.lower()
    if 'HTML' in name or 'XML' in name:
        return 'markup'
    if 'CSS' in name or name in ('Sass', 'SCSS'):
        return 'css'
    if name in ('JavaScript', 'TypeScript', 'TSX', 'JSX'):
        return 'js'
    if name in ('C', 'C++', 'Objective-C', 'Objective-C++', 'CUDA'):
        return 'c'
    return 'generic'


def _walk(table: dict, cache: dict, token_type):
    """Kind registered for the closest ancestor of token_type (None when unmapped or hidden)."""
    try:
        return cache[token_type]
    except KeyError:
        pass
    node, kind = token_type, None
    while node is not None:
        if node in table:
            kind = table[node]
            break
        node = node.parent
    cache[token_type] = kind
    return kind


_DEFAULTS = {'ident': 'variable', 'kw': 'keyword', 'builtin_name': 'builtin', 'pseudo': 'variable',
             'sdoc': 'doc', 'yamlval': 'string', 'mdkw': 'keyword', 'mdhead': 'heading',
             'mdtag': 'tag', 'mdattr': 'attribute', 'mpunct': None, 'cssname': 'selector',
             'csskw': 'property'}


class _Classifier:
    """Folds a Pygments token stream into kinds, using the neighbouring tokens for context."""

    def __init__(self, lexer, text: str):
        self.text, self.family = text, _family(lexer)
        self.no_control = 'SQL' in lexer.name or lexer.name in ('Docker', 'Makefile', 'INI')
        self.prev = ''
        self.embed = self.pending_embed = None
        self.decorator_end = 0

    def kind(self, token_type, value: str, start: int, end: int) -> str | None:
        if not value or value.isspace():
            return None
        family = self.family
        if family == 'markup':
            self._track_embed(token_type, value)
            family = self.embed or family
        elif family == 'python' and start < self.decorator_end:
            self.prev = value
            return 'decorator'
        table, cache = _table(family)
        base = _walk(table, cache, token_type)
        kind = base if base is None or base in _KIND_SET else self._resolve(family, base, value, start, end)
        if kind == 'decorator' and family == 'python':
            match = _DOTTED(self.text, start)
            self.decorator_end = match.end() if match else 0
        self.prev = value
        return kind

    def _track_embed(self, token_type, value: str) -> None:
        if token_type is Token.Name.Tag:
            word = value.lower()
            if word in ('style', 'script'):
                if self.prev in ('/', '</'):
                    self.embed = self.pending_embed = None
                elif self.prev == '<':
                    self.pending_embed = 'css' if word == 'style' else 'js'
        elif self.pending_embed and value == '>':
            self.embed, self.pending_embed = self.pending_embed, None

    def _resolve(self, family: str, base: str, value: str, start: int, end: int) -> str | None:
        if base == 'ident':
            return self._identifier(family, value, end)
        if base == 'kw':
            if value[0] == '@':
                return 'decorator'
            if not _LETTER(value):
                return 'operator'
            return 'control' if value in CONTROL and not self.no_control else 'keyword'
        if base == 'builtin_name':
            if value in _TYPE_BUILTINS or _CAMEL(value):
                return 'class'
            return 'constant' if value in _CONSTANT_BUILTINS else 'builtin'
        if base == 'pseudo':
            return 'class' if _CAMEL(value) else 'variable'
        if base == 'sdoc':
            return 'doc' if family == 'python' else 'comment'
        if base == 'cssname':
            following = self.text[end:end + 2]
            if value[0].isdigit() or value[0] == '.' and value[1:2].isdigit():
                return 'number'
            if self.prev == '$':
                return 'variable'
            if following[:1] == ':' and following[1:].isspace():
                return 'property'
            return 'function' if following[:1] == '(' else 'selector'
        if base == 'csskw':
            return 'control' if value[0] == '@' or self.prev == '@' else 'property'
        if base == 'yamlval':
            if value in _YAML_CONSTANTS:
                return 'constant'
            return 'number' if _NUMBER(value) else 'string'
        if base == 'mdkw':
            return 'keyword' if _LIST_MARKER(value) else self._resolve(family, 'kw', value, start, end)
        if base == 'mdhead':
            if value.startswith(' '):
                return 'string'
            return 'heading' if value.startswith('#') else None
        if base == 'mdtag':
            return 'string' if self.text[start - 1:start] == '[' and self.text[end:end + 1] == ']' else 'tag'
        if base == 'mdattr':
            return 'link' if self.text[start - 1:start] == '(' and self.text[end:end + 1] == ')' else 'attribute'
        if base == 'mpunct':
            return 'punctuation' if value in _MARKUP_PUNCTUATION else None
        return None

    def _identifier(self, family: str, value: str, end: int) -> str:
        previous = self.prev
        if previous in _CLASS_INTRODUCERS:
            return 'class'
        if previous in _FUNCTION_INTRODUCERS:
            return 'function'
        following = self.text[end:end + 1]
        if following == '(':
            return 'function'
        if previous in ('.', '::', '->') or value in ('self', 'cls'):
            return 'variable'
        if _CAMEL(value):
            return 'class'
        if family == 'js' and following == ':' and previous in ('{', ','):
            return 'property'
        return 'variable'


_KIND_SET = frozenset(KINDS)


def map_token(token_type, lexer, value: str = '') -> str | None:
    """Kind for one Pygments token type; ``value`` enables the text-based refinements."""
    if not AVAILABLE or lexer is None:
        return None
    table, cache = _table(_family(lexer))
    base = _walk(table, cache, token_type)
    if base is None or base in _KIND_SET:
        return base
    if not value:
        return _DEFAULTS.get(base)
    return _Classifier(lexer, value).kind(token_type, value, 0, len(value))


def classify(lexer, text: str) -> Iterator[tuple[int, int, str]]:
    """Yield (start, end, kind) runs with code-point offsets into ``text``, in order."""
    if not AVAILABLE or lexer is None:
        return
    classifier = _Classifier(lexer, text)
    size = len(text)
    source = text if text.endswith('\n') else text + '\n'
    position = 0
    run_start = run_end = 0
    run_kind = None
    for index, token_type, value in lexer.get_tokens_unprocessed(source):
        # Embedded sub-lexers (Markdown code fences) restart their offsets at zero.
        index = max(index, position)
        end = position = index + len(value)
        kind = classifier.kind(token_type, value, index, end)
        if kind is None:
            if run_kind and not value.isspace():
                yield run_start, run_end, run_kind
                run_kind = None
            continue
        if kind == run_kind and kind not in _NO_BRIDGE:
            run_end = min(end, size)
            continue
        if run_kind:
            yield run_start, run_end, run_kind
        if index >= size:
            run_kind = None
            continue
        run_start, run_end, run_kind = index, min(end, size), kind
    if run_kind:
        yield run_start, run_end, run_kind


# --------------------------------------------------------------------------- buffer binding

class Highlighter:
    """Keeps the ``hl-*`` tags of one Gtk.TextBuffer in sync with its text.

    Edits restart a debounced pass that lexes the buffer in short idle slices and recolours it
    progressively; a generation counter makes every stale callback a no-op.
    """

    def __init__(self, buffer: Gtk.TextBuffer, styles: dict[str, SyntaxStyle] | None = None, lexer=None):
        self.buffer = buffer
        self.lexer = None
        self.skipped = False
        self.tags: dict[str, Gtk.TextTag] = {}
        self._styles: dict[str, SyntaxStyle] = {}
        self._generation = 0
        self._timeout = self._idle = 0
        self._runs: Iterator[tuple[int, int, str]] | None = None
        self._painted = 0
        self._destroyed = False
        if AVAILABLE:
            table = buffer.get_tag_table()
            for kind in KINDS:
                self.tags[kind] = table.lookup('hl-' + kind) or buffer.create_tag('hl-' + kind)
        self.set_style(DARK_PLUS if styles is None else styles)
        self._changed_id = buffer.connect('changed', self._on_changed)
        self.set_lexer(lexer)

    @property
    def pending(self) -> bool:
        """True while a pass is scheduled or still running."""
        return bool(self._timeout or self._idle)

    def set_style(self, styles: dict[str, SyntaxStyle]) -> None:
        self._styles = dict(styles)
        for kind, tag in self.tags.items():
            style = self._styles.get(kind)
            color = Gdk.RGBA()
            if style is not None and color.parse(style.foreground):
                tag.set_property('foreground', style.foreground)
            else:
                tag.set_property('foreground-set', False)
            for name, enabled, value in (('weight', style and style.bold, Pango.Weight.BOLD),
                                         ('style', style and style.italic, Pango.Style.ITALIC),
                                         ('underline', style and style.underline, Pango.Underline.SINGLE)):
                if enabled:
                    tag.set_property(name, value)
                else:
                    tag.set_property(name + '-set', False)

    def set_lexer(self, lexer) -> None:
        self.lexer = lexer if AVAILABLE else None
        self._restart(0)

    def destroy(self) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        self._generation += 1
        self._cancel()
        self._runs = None
        try:
            self.buffer.disconnect(self._changed_id)
        except TypeError:
            pass

    def _cancel(self) -> None:
        for source in (self._timeout, self._idle):
            if source:
                GLib.source_remove(source)
        self._timeout = self._idle = 0

    def _on_changed(self, *_: object) -> None:
        if self._destroyed or self.lexer is None:
            return
        self._restart(DEBOUNCE_MS)

    def _restart(self, delay: int) -> None:
        if self._destroyed:
            return
        self._generation += 1
        self._cancel()
        self._runs = None
        if self.lexer is None:
            self.skipped = False
            self._clear(0, self.buffer.get_char_count())
        elif delay:
            self._timeout = GLib.timeout_add(delay, self._begin, self._generation)
        else:
            self._timeout = GLib.idle_add(self._begin, self._generation)

    def _clear(self, start: int, end: int) -> None:
        if end <= start or not self.tags:
            return
        first, last = self.buffer.get_iter_at_offset(start), self.buffer.get_iter_at_offset(end)
        for tag in self.tags.values():
            self.buffer.remove_tag(tag, first, last)

    def _begin(self, generation: int) -> bool:
        """Start a pass: snapshot the text, apply the size limits and arm the idle slices."""
        self._timeout = 0
        if self._destroyed or generation != self._generation:
            return False
        try:
            buffer = self.buffer
            text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
            self.skipped = len(text.encode('utf-8', 'surrogatepass')) > MAX_BYTES or \
                re.search(r'[^\n]{%d}' % (MAX_LINE + 1), text) is not None
            if self.skipped or self.lexer is None:
                self._clear(0, buffer.get_char_count())
                return False
            self._runs = classify(self.lexer, text)
            self._painted = 0
            self._idle = GLib.idle_add(self._step, generation)
        except Exception:
            self._runs = None
        return False

    def _step(self, generation: int) -> bool:
        """One idle slice: lex for a few milliseconds, then recolour the span that was covered."""
        if self._destroyed or generation != self._generation or self._runs is None:
            return False
        try:
            done, runs = False, []
            deadline = time.perf_counter() + TOKEN_SECONDS
            for run in self._runs:
                runs.append(run)
                if time.perf_counter() > deadline:
                    break
            else:
                done = True
            buffer = self.buffer
            limit = buffer.get_char_count()
            stop = limit if done else min(runs[-1][1], limit) if runs else self._painted
            if stop > self._painted:
                self._clear(self._painted, stop)
            cursor = buffer.get_iter_at_offset(0)
            tags, edge = self.tags, buffer.get_iter_at_offset(0)
            for start, end, kind in runs:
                if start >= limit:
                    break
                cursor.set_offset(start)
                edge.set_offset(min(end, limit))
                buffer.apply_tag(tags[kind], cursor, edge)
            self._painted = max(self._painted, stop)
            if done:
                self._runs = None
                self._idle = 0
                return False
            return True
        except Exception:
            self._runs = None
            self._idle = 0
            return False
