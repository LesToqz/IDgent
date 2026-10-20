"""Pure-Python project graphs with configurable IDgent project policies.

Queries remove non-matching files/tags; unresolved targets are unfiltered. No GUI modules are imported.
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass, field
from functools import lru_cache
import os
import posixpath as pp
import re
from urllib.parse import unquote

from .linkparse import extract_code_links, extract_links, extract_tags, parse_frontmatter

# IDgent file-resolution and filtering policies; see docs/graph-spec.md.
SEMANTICS = {
    'note_extensions': ('.md', '.markdown'),
    'code_extensions': frozenset('.py .pyi .js .ts .tsx .jsx .mjs .cjs .css .scss .sass .html .htm .json .sh .bash .rs .go .c .h .cpp .hpp .cc .java .kt .swift .rb .php .vue .svelte .sql .xml .yaml .yml .toml'.split()),
    'js_extensions': ('.ts', '.tsx', '.js', '.jsx', '.mjs', '.json', '.css', '.vue'),
    'case_sensitive': False,
    'wiki_path_order': ('root', 'source'),
    'markdown_path_order': ('source', 'root'),
    'python_path_order': ('root', 'source', 'src'),
    'duplicate_order': ('same_folder', 'shortest', 'lexicographic'),
    'alias_fallback': True,                 # filenames before aliases
    'attachments_require_reference': True,
    'hide_missing_attachments_with_attachments': True, # infer assets from suffix
    'query_primary_kinds': ('note', 'code'), # search notes and source files
    'tags_from_kinds': ('note',),
    'tag_prefix_boundary': '/',
    'keep_self_links': True,
    'degree_counts_duplicates': False,       # count collapsed edges
    'size_hint_base': 1,
    'unresolved_prefix': '?',
    'tag_prefix': '#',
    'local_min_depth': 1,
}
# Copied from search.py, whose widget definitions import GTK.
SKIP_DIRS = frozenset('.git .hg .svn __pycache__ node_modules .venv venv '
                      '.mypy_cache .pytest_cache .tox .idea .ceo'.split())
MAX_FILE_SIZE = 1024 * 1024


@dataclass
class GraphOptions:
    query: str = ''
    show_tags: bool = True
    show_attachments: bool = True
    existing_only: bool = True
    show_orphans: bool = True
    show_code: bool = True
    groups: list[dict] = field(default_factory=list)
    # Strict UI searches test each file/tag; data clients can retain neighbours.
    strict_filters: bool = False


@dataclass
class Node:
    id: str
    label: str
    kind: str
    path: str = ''
    folder: str = ''
    tags: list[str] = field(default_factory=list)
    in_degree: int = 0
    out_degree: int = 0
    size_hint: int = 1
    group: int | None = None
    ctime: float | None = None


@dataclass
class Link:
    source: str
    target: str
    kind: str
    resolved: bool = True
    count: int = 1
    self: bool = False


@dataclass
class Graph:
    nodes: list[Node]
    links: list[Link]
    truncated: bool = False
    stats: dict = field(default_factory=dict)

    def to_json(self):
        indices = {node.id: i for i, node in enumerate(self.nodes)}
        links = []
        for link in self.links:
            value = asdict(link)
            value['s'] = indices[value.pop('source')]
            value['t'] = indices[value.pop('target')]
            links.append(value)
        return {'nodes': [asdict(node) for node in self.nodes], 'links': links,
                'truncated': self.truncated, 'stats': self.stats}


@dataclass(eq=True)
class _File:
    path: str
    kind: str
    text: str
    tags: list[str]
    aliases: list[str]
    refs: list
    ctime: float | None = field(default=None, compare=False)

    @property
    def label(self):
        return pp.splitext(pp.basename(self.path))[0]


@lru_cache(maxsize=256)
def _query_tokens(query):
    return re.findall(r'-?(?:[\w]+:)?(?:"[^"]*"|/[^/]*(?:\\/[^/]*)*/|[^\s]+)', query)


def match_query(node_or_file, query):
    """AND terms, OR clauses, negation, quoted phrases and field searches.

    Malformed regular expressions simply do not match. Content searches require
    a file/dict carrying text; exported nodes intentionally omit file contents.
    """
    if not query.strip():
        return True

    def get(key, default=''):
        if isinstance(node_or_file, dict):
            return node_or_file.get(key, default)
        return getattr(node_or_file, key, default)

    path = get('path') or get('id')
    content = get('text', get('content'))
    label = get('label', pp.basename(path))
    haystack = f'{path}\n{label}\n{content}'

    def term(token):
        negative = token.startswith('-')
        if negative:
            token = token[1:]
        fieldname, sep, value = token.partition(':')
        if not sep or fieldname.lower() not in {'path', 'file', 'tag', 'content'}:
            fieldname, value = '', token
        fieldname = fieldname.lower()
        value = value.strip('"')
        if fieldname == 'tag':
            wanted = value.lstrip('#').casefold()
            found = any(t.casefold() == wanted or t.casefold().startswith(wanted + SEMANTICS['tag_prefix_boundary']) for t in get('tags', []))
        else:
            target = {'path': path, 'file': pp.basename(path), 'content': content}.get(fieldname, haystack)
            if value.startswith('/') and value.endswith('/'):
                try:
                    found = re.search(value[1:-1], target, re.I) is not None
                except re.error:
                    found = False
            else:
                found = value.casefold() in target.casefold()
        return not found if negative else found

    clauses = [[]]
    for token in _query_tokens(query):
        if token == 'OR':
            clauses.append([])
        else:
            clauses[-1].append(token)
    return any(clause and all(term(token) for token in clause) for clause in clauses)


class GraphIndex:
    def __init__(self, root, *, max_nodes=3000, ignore=SKIP_DIRS):
        self.root = os.path.abspath(root)
        self.max_nodes = max(0, max_nodes)
        self.ignore = frozenset(ignore)
        self._files = {}
        self._overrides = {}
        self._paths = {}
        self._names = defaultdict(set)
        self._aliases = defaultdict(set)
        self._last_candidates = []

    def _key(self, value):
        return value if SEMANTICS['case_sensitive'] else value.casefold()

    def _rel(self, path):
        path = os.fspath(path)
        absolute = os.path.abspath(path if os.path.isabs(path) else os.path.join(self.root, path))
        try:
            if os.path.commonpath([self.root, absolute]) != self.root:
                return None
        except ValueError:
            return None
        rel = os.path.relpath(absolute, self.root).replace(os.sep, '/')
        if any(part in self.ignore for part in rel.split('/')[:-1]):
            return None
        return rel

    def _read(self, rel):
        absolute = os.path.join(self.root, rel)
        if os.path.islink(absolute) or os.path.realpath(absolute) != absolute:
            return None
        binary = False
        if rel in self._overrides:
            text = self._overrides[rel]
        else:
            try:
                if os.path.getsize(absolute) > MAX_FILE_SIZE or not os.path.isfile(absolute):
                    return None
                with open(absolute, 'rb') as stream:
                    data = stream.read(MAX_FILE_SIZE + 1)
            except OSError:
                return None
            if len(data) > MAX_FILE_SIZE:
                return None
            try:
                binary = b'\0' in data
                text = data.decode('utf-8') if not binary else ''
            except UnicodeError:
                binary, text = True, ''
        ext = pp.splitext(rel)[1].lower()
        kind = 'note' if ext in SEMANTICS['note_extensions'] else 'code' if ext in SEMANTICS['code_extensions'] else 'attachment'
        # Binary files remain resolvable attachments, but never become text sources.
        if binary and kind != 'attachment':
            return None
        props = parse_frontmatter(text)[0] if kind == 'note' else {}
        aliases = props.get('aliases', props.get('alias', []))
        if isinstance(aliases, str):
            aliases = [aliases] if aliases else []
        refs = extract_links(text) if kind == 'note' else extract_code_links(rel, text) if kind == 'code' else []
        try:
            info = os.stat(os.path.join(self.root, rel))
            ctime = float(getattr(info, 'st_birthtime', min(info.st_ctime, info.st_mtime)))
        except OSError:
            ctime = None
        return _File(rel, kind, text, extract_tags(text) if kind in SEMANTICS['tags_from_kinds'] else [], aliases, refs, ctime)

    def _register(self, item, remove=False):
        key = self._key(item.path)
        if remove:
            self._paths.pop(key, None)
        else:
            self._paths[key] = item.path
        for mapping, values in ((self._names, [pp.basename(item.path)]), (self._aliases, item.aliases)):
            for value in values:
                bucket = mapping[self._key(value)]
                if remove:
                    bucket.discard(item.path)
                else:
                    bucket.add(item.path)

    def scan(self):
        self._files.clear()
        self._paths.clear()
        self._names.clear()
        self._aliases.clear()
        for directory, dirs, files in os.walk(self.root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in self.ignore and not os.path.islink(os.path.join(directory, d)))
            for name in sorted(files):
                rel = self._rel(os.path.join(directory, name))
                if rel is not None:
                    self.update_path(rel)
        for rel in self._overrides:
            if rel not in self._files:
                self.update_path(rel)

    def update_path(self, path):
        rel = self._rel(path)
        if rel is None:
            return False
        new, old = self._read(rel), self._files.get(rel)
        if new == old:
            return False
        if old:
            self._register(old, remove=True)
            del self._files[rel]
        if new:
            self._files[rel] = new
            self._register(new)
        return True

    def set_text(self, path, text):
        rel = self._rel(path)
        if rel is None:
            return
        if text is None:
            self._overrides.pop(rel, None)
        else:
            self._overrides[rel] = text
        self.update_path(rel)

    def _exact(self, candidate):
        candidate = pp.normpath(candidate)
        if candidate == '..' or candidate.startswith('../') or candidate.startswith('/'):
            return None
        return self._paths.get(self._key(candidate))

    def resolve(self, source_rel, target, kind):
        self._last_candidates = []
        target = unquote(target).split('#', 1)[0].split('^', 1)[0].split('?', 1)[0]
        folder = pp.dirname(source_rel)
        if not target or re.match(r'^(?:[a-z][\w+.-]*:|//)', target, re.I):
            return None
        if kind in {'python', 'import'} and pp.splitext(source_rel)[1].lower() == '.py':
            level = len(target) - len(target.lstrip('.'))
            module = target[level:].replace('.', '/')
            if level:
                base = folder
                for _ in range(level - 1):
                    base = pp.join(base, '..')
                candidates = [pp.join(base, module)]
            else:
                bases = {'root': '', 'source': folder, 'src': 'src'}
                candidates = [pp.join(bases[b], module) for b in SEMANTICS['python_path_order']]
            for candidate in candidates:
                for suffix in ('.py', '/__init__.py'):
                    found = self._exact(candidate + suffix)
                    if found:
                        return found
            return None
        if kind in {'import', 'js', 'jsx', 'ts', 'tsx', 'mjs', 'cjs', 'vue', 'css', 'scss', 'sass', 'html', 'htm'}:
            ext = pp.splitext(source_rel)[1].lower()
            if ext in {'.js', '.ts', '.tsx', '.jsx', '.mjs', '.cjs', '.vue'} and not target.startswith('.'):
                return None
            base = pp.join(folder, target)
            probes = [base]
            if ext in {'.js', '.ts', '.tsx', '.jsx', '.mjs', '.cjs', '.vue'}:
                probes += [base + e for e in SEMANTICS['js_extensions']]
                probes += [base + '/index' + e for e in SEMANTICS['js_extensions']]
            return next((found for candidate in probes if (found := self._exact(candidate))), None)
        if kind == 'md' or (kind == 'embed' and target.startswith(('.', '/'))):
            bases = {'source': folder, 'root': ''}
            return next((found for b in SEMANTICS['markdown_path_order'] if (found := self._exact(pp.join(bases[b], target.lstrip('/'))))), None)
        if '/' in target:
            candidates = [target] if pp.splitext(target)[1] else [target + SEMANTICS['note_extensions'][0]]
            bases = {'source': folder, 'root': ''}
            return next((found for b in SEMANTICS['wiki_path_order'] for c in candidates if (found := self._exact(pp.join(bases[b], c)))), None)
        candidates = set()
        if not pp.splitext(target)[1]:
            for ext in SEMANTICS['note_extensions']:
                candidates.update(self._names.get(self._key(target + ext), ()))
        if not candidates:
            candidates.update(self._names.get(self._key(target), ()))
        if not candidates and SEMANTICS['alias_fallback']:
            candidates.update(self._aliases.get(self._key(target), ()))
        order = {'same_folder': lambda p: pp.dirname(p) != folder,
                 'shortest': len, 'lexicographic': lambda p: (self._key(p), p)}
        candidates = sorted(candidates, key=lambda p: tuple(order[k](p) for k in SEMANTICS['duplicate_order']))
        self._last_candidates = candidates
        return candidates[0] if candidates else None

    def build(self, options=None, *, local_file=None):
        options = options or GraphOptions()
        nodes, edges, ambiguous = {}, {}, []
        for path, item in self._files.items():
            if item.kind == 'attachment' and SEMANTICS['attachments_require_reference']:
                continue
            if item.kind == 'code' and not options.show_code:
                continue
            nodes[path] = Node(path, item.label, item.kind, path, pp.dirname(path), list(item.tags), ctime=item.ctime)

        def edge(source, target, kind, resolved=True):
            if source == target and not SEMANTICS['keep_self_links']:
                return
            key = source, target, kind
            if key in edges:
                edges[key].count += 1
            else:
                edges[key] = Link(source, target, kind, resolved, self=source == target)

        for path, item in self._files.items():
            if path not in nodes:
                continue
            for ref in item.refs:
                code = item.kind == 'code'
                target = self.resolve(path, ref.spec if code else ref.target,
                                      ref.kind if code or ref.syntax == 'wiki' else 'md')
                if len(self._last_candidates) > 1:
                    ambiguous.append({'source': path, 'target': ref.target, 'candidates': list(self._last_candidates), 'resolved': target})
                if target:
                    dest = self._files[target]
                    if dest.kind == 'code' and not options.show_code or dest.kind == 'attachment' and not options.show_attachments:
                        continue
                    nodes.setdefault(target, Node(target, dest.label, dest.kind, target, pp.dirname(target), list(dest.tags), ctime=dest.ctime))
                elif code or options.existing_only:
                    continue
                else:
                    suffix = pp.splitext(ref.target)[1].lower()
                    if (SEMANTICS['hide_missing_attachments_with_attachments']
                            and not options.show_attachments and suffix
                            and suffix not in SEMANTICS['note_extensions']
                            and suffix not in SEMANTICS['code_extensions']):
                        continue
                    target = SEMANTICS['unresolved_prefix'] + ref.target
                    nodes.setdefault(target, Node(target, ref.target, 'unresolved'))
                edge(path, target, 'import' if code else 'embed' if ref.kind == 'embed' else 'link', not target.startswith(SEMANTICS['unresolved_prefix']))
            if options.show_tags:
                for tag in item.tags:
                    target = SEMANTICS['tag_prefix'] + tag
                    nodes.setdefault(target, Node(target, tag, 'tag', tags=[tag]))
                    edge(path, target, 'tag')
        links = list(edges.values())
        if options.query:
            if options.strict_filters:
                nodes = {key: node for key, node in nodes.items()
                         if key == local_file or node.kind == 'unresolved' or match_query(self._files.get(key, node), options.query)}
            else:
                selected = {key for key, node in nodes.items() if node.kind in SEMANTICS['query_primary_kinds'] and match_query(self._files[key], options.query)}
                secondary = {link.target for link in links if link.source in selected and nodes[link.target].kind not in SEMANTICS['query_primary_kinds']}
                secondary.update(link.source for link in links if link.target in selected and nodes[link.source].kind not in SEMANTICS['query_primary_kinds'])
                nodes = {key: node for key, node in nodes.items() if key in selected or key in secondary}
            links = [link for link in links if link.source in nodes and link.target in nodes]
        for node in nodes.values():
            item = self._files.get(node.id, node)
            node.group = next((i for i, group in enumerate(options.groups)
                               if (not options.strict_filters or
                                   node.kind not in ('tag', 'unresolved') and group.get('query', '').strip())
                               and match_query(item, group.get('query', ''))), None)
        if not options.show_orphans:
            linked = {end for link in links for end in (link.source, link.target)}
            nodes = {key: node for key, node in nodes.items() if key in linked}
        self._degrees(nodes, links)
        truncated = len(nodes) > self.max_nodes
        if truncated:
            keep = sorted(nodes, key=lambda key: (-(nodes[key].in_degree + nodes[key].out_degree), key))[:self.max_nodes]
            nodes = {key: nodes[key] for key in keep}
            links = [link for link in links if link.source in nodes and link.target in nodes]
        return self._finish(nodes, links, truncated, ambiguous)

    @staticmethod
    def _degrees(nodes, links):
        for node in nodes.values():
            node.in_degree = node.out_degree = 0
        for link in links:
            weight = link.count if SEMANTICS['degree_counts_duplicates'] else 1
            nodes[link.source].out_degree += weight
            nodes[link.target].in_degree += weight
        for node in nodes.values():
            node.size_hint = SEMANTICS['size_hint_base'] + node.in_degree + node.out_degree

    def _finish(self, nodes, links, truncated=False, ambiguous=()):
        self._degrees(nodes, links)
        neighbors = defaultdict(set)
        for link in links:
            neighbors[link.source].add(link.target)
            neighbors[link.target].add(link.source)
        unseen, components = set(nodes), 0
        while unseen:
            components += 1
            pending = [unseen.pop()]
            while pending:
                for neighbor in neighbors[pending.pop()]:
                    if neighbor in unseen:
                        unseen.remove(neighbor)
                        pending.append(neighbor)
        counts = {kind: 0 for kind in ('note', 'attachment', 'code', 'tag', 'unresolved')}
        counts.update(Counter(node.kind for node in nodes.values()))
        stats = {'counts': counts, 'orphans': sum(not (n.in_degree or n.out_degree) for n in nodes.values()),
                 'unresolved': sorted(n.id for n in nodes.values() if n.kind == 'unresolved'),
                 'ambiguous': sorted((a for a in ambiguous if a['source'] in nodes and a['resolved'] in nodes), key=lambda a: (a['source'], a['target'])),
                 'components': components}
        return Graph(sorted(nodes.values(), key=lambda n: n.id), sorted(links, key=lambda e: (e.source, e.target, e.kind)), truncated, stats)

    def local(self, path, depth=1, incoming=True, outgoing=True, options=None):
        focus = self._rel(path)
        if focus:
            focus = self._paths.get(self._key(focus), focus)
        graph = self.build(options, local_file=focus)
        nodes = {n.id: n for n in graph.nodes}
        neighbors = defaultdict(set)
        for link in graph.links:
            if outgoing:
                neighbors[link.source].add(link.target)
            if incoming:
                neighbors[link.target].add(link.source)
        seen = {focus} if focus in nodes else set()
        pending = deque((key, 0) for key in seen)
        while pending:
            key, level = pending.popleft()
            if level >= max(SEMANTICS['local_min_depth'], depth):
                continue
            for neighbor in neighbors[key] - seen:
                seen.add(neighbor)
                pending.append((neighbor, level + 1))
        result = self._finish({key: nodes[key] for key in seen}, [e for e in graph.links if e.source in seen and e.target in seen], graph.truncated, graph.stats['ambiguous'])
        result.stats['focus'] = focus
        return result

    def folder(self, prefix, options=None):
        """Only the files below ``prefix`` and the links between them."""
        graph = self.build(options)
        prefix = prefix.strip('/') + '/'
        nodes = {n.id: n for n in graph.nodes
                 if n.id.startswith(prefix) and n.kind not in ('tag', 'unresolved')}
        links = [e for e in graph.links if e.source in nodes and e.target in nodes]
        return self._finish(nodes, links, graph.truncated, graph.stats['ambiguous'])

    def empty(self):
        return self._finish({}, [])
