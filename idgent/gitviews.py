"""GTK hosts for the Git graph and commit pages; Git work stays off the UI thread."""
from __future__ import annotations

import colorsys
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import weakref

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, Gio, GLib, GObject, Gtk

from idgent import gitdiff, gitlog, motion, settings
from idgent.graphview import _load_webkit
from idgent.themes import _legible, _luminance, get_theme

_NO_PAYLOAD = object()
_BRANCHES = ('#5588ee', '#a477e8', '#48b86b', '#e79a39',
             '#e45c68', '#33b4af', '#d56fbd', '#c5ad35')


def _rgb(color):
    return tuple(int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))


def _fonts(ui):
    def family(key, fallback):
        value = ui.get(key) or settings.get_value(key)
        return json.dumps(value) + ', ' + fallback if value else fallback
    return family('editor_font', 'monospace'), family('ui_font', 'system-ui, sans-serif')


def git_colors(ui: dict, accent: str) -> dict:
    """Eight readable branch lanes, reserving the accent for the default branch."""
    bg = ui.get('bg', '#1e1e1e')
    branches = []
    for base in _BRANCHES:
        h, s, v = colorsys.rgb_to_hsv(*_rgb(base))
        for step in range(36):
            candidate = '#' + ''.join(f'{round(c * 255):02x}' for c in
                                      colorsys.hsv_to_rgb((h + step / 36) % 1, s, v))
            candidate = _legible(candidate, bg, 3)
            if all(sum((a - b) ** 2 for a, b in zip(_rgb(candidate), _rgb(other))) ** .5 >= .22
                   for other in [accent, *branches]):
                break
        branches.append(candidate)
    mono, font = _fonts(ui)
    return dict(background=bg, surface=ui.get('sidebar_bg', bg),
                surface_alt=ui.get('surface_alt', bg), border=ui.get('border', '#555555'),
                text=ui.get('foreground', '#cccccc'),
                text_muted=_legible(ui.get('muted', '#888888'), bg, 3),
                accent=accent, accent_fg=ui.get('accent_fg', '#ffffff'), head=accent,
                branch_colors=branches, mono_font=mono, ui_font=font, dark=_luminance(bg) < .3)


def commit_colors(ui: dict, accent: str, syntax_style: dict | None) -> dict:
    from idgent.highlight import DARK_PLUS, KINDS
    colors = git_colors(ui, accent)
    bg = colors['background']
    add = ui.get('git_new', '#48b86b')
    delete = ui.get('git_conflict', ui.get('danger', '#e45c68'))
    r, g, b = _rgb(delete)
    if r <= max(g, b):
        delete = ui.get('danger', '#e45c68')
    def tint(color):
        return 'rgba(' + ', '.join(str(round(c * 255)) for c in _rgb(color)) + ', 0.17)'
    styles = DARK_PLUS if syntax_style is None else syntax_style
    syntax = {kind: _legible(getattr(styles.get(kind), 'foreground', None) or
                            colors['text'], bg, 3) for kind in KINDS}
    # Explicit syntax colours match the editor exactly.
    if syntax_style is not None:
        syntax.update({k: v.foreground for k, v in syntax_style.items() if k in KINDS})
    return dict(bg=bg, fg=ui.get('editor_fg', colors['text']), muted=colors['text_muted'],
                surface=colors['surface'], surface2=colors['surface_alt'], border=colors['border'],
                hover=ui.get('hover', colors['surface']), accent=accent,
                accent_fg=colors['accent_fg'], add=add, **{'del': delete},
                add_bg=tint(add), del_bg=tint(delete), mono=colors['mono_font'],
                ui=colors['ui_font'], size=settings.get_font_size('editor_font_size'),
                dark=colors['dark'], syntax=syntax)


def _placeholder(api):
    return '''<!doctype html><meta charset="utf-8"><style>
body{margin:0;background:#1e1e1e;color:#ccc;font:14px system-ui;display:grid;
place-items:center;height:100vh} </style><body>Loading Git view…<script>
window.API={setState(k,m){document.body.firstChild.textContent=m||k},
setTheme(c){document.body.style.background=c.background||c.bg;
document.body.style.color=c.text||c.fg},setData(d){document.body.firstChild.textContent=
d.commits.length+' commits'},setCommit(d){document.body.firstChild.textContent=d.subject},
setSettings(){},select(){},focus(){},provideContext(){},pause(){},resume(){}};
webkit.messageHandlers.idgent.postMessage({type:'ready'});</script>'''.replace('window.API', 'window.' + api)


class _WebHost(Gtk.Box):
    __gsignals__ = {
        'open-commit': (GObject.SignalFlags.RUN_LAST, None, (str,)),
        'error': (GObject.SignalFlags.RUN_LAST, None, (str,)),
    }
    _shared_context = None
    _share_context = False

    def __init__(self, root, *, page=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.root = os.path.abspath(os.fspath(root))
        self.available = False
        self.webview = None
        self._closed = self._ready = False
        self._queue = []
        self._connections = []
        self._sources = set()
        self._threads = []
        self._lock = threading.Lock()
        self._generation = 0
        self._theme = None
        self._state = ('loading', '')
        self.last_data = None
        self._cancellable = Gio.Cancellable()
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.connect('destroy', lambda _w: self.shutdown())
        try:
            self._webkit = WebKit2 = _load_webkit()
        except (ImportError, ValueError):
            label = Gtk.Label(label='Git view is unavailable\nGit view needs WebKit2 '
                              '(sudo apt install gir1.2-webkit2-4.1)')
            label.set_line_wrap(True)
            self.pack_start(label, True, True, 0)
            label.show()
            return
        self.available = True
        self._manager = WebKit2.UserContentManager()
        self._manager.register_script_message_handler('idgent')
        self._connect(self._manager, 'script-message-received::idgent', self._message_received)
        if self._share_context:
            if CommitView._shared_context is None:
                CommitView._shared_context = WebKit2.WebContext.new_ephemeral()
            self._context = CommitView._shared_context
        else:
            self._context = WebKit2.WebContext.new_ephemeral()
        related = next((v.webview for v in CommitView._live_views
                        if not v._closed and v.webview is not None), None) if self._share_context else None
        if related is not None:
            # A context alone permits one process per tab. Related views share it;
            # pass the software-rendering settings at construction to avoid mixed
            # rendering policies in that process before our setters run.
            self.webview = WebKit2.WebView(related_view=related, settings=related.get_settings(),
                                         user_content_manager=self._manager)
        else:
            self.webview = WebKit2.WebView(web_context=self._context, user_content_manager=self._manager)
        if self._share_context:
            CommitView._live_views.add(self)
        prefs = self.webview.get_settings()
        debug = os.environ.get('IDGENT_GRAPH_DEBUG') == '1'
        prefs.set_enable_developer_extras(debug)
        prefs.set_enable_write_console_messages_to_stdout(debug)
        prefs.set_javascript_can_open_windows_automatically(False)
        if os.environ.get('IDGENT_GRAPH_GPU') != '1':
            prefs.set_hardware_acceleration_policy(WebKit2.HardwareAccelerationPolicy.NEVER)
        self.webview.set_hexpand(True)
        self.webview.set_vexpand(True)
        self._connect(self.webview, 'context-menu', lambda *_a: True)
        self._connect(self.webview, 'create', lambda *_a: None)
        self._connect(self.webview, 'decide-policy', self._decide_policy)
        self._connect(self.webview, 'load-changed', self._load_changed)
        self._connect(self.webview, 'load-failed', self._load_failed)
        self._connect(self.webview, 'web-process-terminated', self._process_terminated)
        self._connect(self, 'map', self._mapped)
        self._connect(self, 'unmap', lambda _w: self.post('pause'))
        self.pack_start(self.webview, True, True, 0)
        theme = get_theme(settings.get_theme_id())
        self.set_theme(theme.ui, settings.get_value('accent') or theme.ui['accent'])
        self._set_state('loading')
        self.post('pause')
        self.webview.show()
        path = Path(__file__).parent / self._page
        self._page_uri = path.resolve().as_uri()
        self._inline_page = page if page is not None else (
            None if path.is_file() else _placeholder(self._api))
        if self._inline_page is None:
            self.webview.load_uri(self._page_uri)
        else:
            self.webview.load_html(self._inline_page, self._page_uri)

    def _connect(self, obj, signal, callback):
        self._connections.append((obj, obj.connect(signal, callback)))

    def _launch(self, work, deliver):
        """Only Python in workers; idle delivery and shutdown share a lock."""
        def run():
            try:
                result = work()
            except Exception as exc:
                result = dict(ok=False, error=str(exc))
            with self._lock:
                if not self._closed:
                    self._sources.add(GLib.idle_add(finish, result))
        def finish(result):
            with self._lock:
                source = GLib.main_current_source()
                if source is not None:
                    self._sources.discard(source.get_id())
                if self._closed:
                    return GLib.SOURCE_REMOVE
            deliver(result)
            return GLib.SOURCE_REMOVE
        if self._closed:
            return
        self._threads = [t for t in self._threads if t.is_alive()]
        thread = threading.Thread(target=run, name='idgent-' + self._api, daemon=True)
        self._threads.append(thread)
        thread.start()

    def _set_state(self, kind, message=''):
        self._state = (kind, message)
        self._call('setState', kind, message)

    def post(self, cmd, payload=_NO_PAYLOAD):
        self._call(cmd, *(() if payload is _NO_PAYLOAD else (payload,)))

    def _call(self, cmd, *args):
        if self._closed or not self.available:
            return
        if cmd not in self._commands:
            raise ValueError(f'Unknown {self._api} command: {cmd}')
        script = f'window.{self._api}.{cmd}(' + ','.join(
            json.dumps(a, ensure_ascii=True, allow_nan=False) for a in args) + ');'
        if self._ready:
            self._evaluate(script)
        else:
            self._queue.append(script)

    def _evaluate(self, script):
        if not self._closed and self.webview is not None:
            self.webview.evaluate_javascript(script, -1, None, None, self._cancellable,
                                             self._javascript_done, None)

    def _javascript_done(self, view, result, _data):
        if not self._closed:
            try:
                view.evaluate_javascript_finish(result)
            except GLib.Error as exc:
                self.emit('error', exc.message)

    def _message_received(self, _manager, result):
        if self._closed:
            return
        try:
            message = json.loads(result.get_js_value().to_json(0))
        except (TypeError, ValueError, AttributeError):
            return
        if not isinstance(message, dict):
            return
        kind = message.get('type')
        if kind == 'ready' and not self._ready:
            self._ready = True
            queued, self._queue = self._queue, []
            for script in queued:
                self._evaluate(script)
            self.post('resume' if self.get_mapped() else 'pause')
        elif kind == 'open-commit':
            rev = message.get('hash')
            if isinstance(rev, str) and rev:
                self.emit('open-commit', rev)
        elif kind == 'error':
            self.emit('error', str(message.get('message', 'Git page error')))
        else:
            self._page_message(message)

    def _load_changed(self, _view, event):
        if not self._closed and event == self._webkit.LoadEvent.STARTED and self._ready:
            self._ready = False
            self._queue.clear()
            if self._theme is not None:
                self.post('setTheme', self._theme)
            self._settings()
            if self.last_data is not None:
                self._post_data()
            else:
                self._set_state(*self._state)

    def _decide_policy(self, _view, decision, kind):
        if kind == self._webkit.PolicyDecisionType.NEW_WINDOW_ACTION:
            decision.ignore()
            return True
        if kind == self._webkit.PolicyDecisionType.NAVIGATION_ACTION:
            action = decision.get_navigation_action()
            uri = action.get_request().get_uri()
            reload = action.get_navigation_type() == self._webkit.NavigationType.RELOAD
            if reload and uri == self._page_uri and self._inline_page is not None:
                # load_html has no backing file for WebKit's ordinary reload.
                decision.ignore()
                self._load_changed(self.webview, self._webkit.LoadEvent.STARTED)
                def reload_inline():
                    source = GLib.main_current_source()
                    self._sources.discard(source.get_id())
                    if not self._closed:
                        self.webview.load_html(self._inline_page, self._page_uri)
                    return GLib.SOURCE_REMOVE
                self._sources.add(GLib.idle_add(reload_inline))
                return True
            if uri not in (self._page_uri, 'about:blank') or (self._ready and not reload):
                decision.ignore()
                return True
        return False

    def _load_failed(self, _view, _event, _uri, error):
        if not self._closed:
            self.emit('error', error.message)
        return False

    def _process_terminated(self, _view, reason):
        if not self._closed:
            self.emit('error', f'Git web process stopped: {reason.value_nick}')

    def _settings(self):
        self.post('setSettings', dict(reduceMotion=not motion.animations_enabled()))

    def _apply_theme(self, colors):
        if self._closed or not self.available:
            return
        self._theme = colors
        color = Gdk.RGBA()
        color.parse(colors.get('background', colors.get('bg')))
        self.webview.set_background_color(color)
        self.post('setTheme', colors)
        self._settings()

    def _mapped(self, _widget):
        self.post('resume')

    def do_grab_focus(self):
        if not self._closed and self.webview is not None:
            self.webview.grab_focus()

    def shutdown(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            sources, self._sources = self._sources, set()
        for source in sources:
            GLib.source_remove(source)
        self._cancellable.cancel()
        self._queue.clear()
        for obj, handler in self._connections:
            if obj.handler_is_connected(handler):
                obj.disconnect(handler)
        self._connections.clear()
        # Git calls are bounded. Join ensures no worker survives tab destruction.
        for thread in self._threads:
            thread.join()
        self._threads.clear()
        if self.webview is not None:
            self._manager.unregister_script_message_handler('idgent')
            self.webview.stop_loading()
            self.webview.try_close()
            # A commit tab must never terminate its siblings' shared process.
            if self._share_context:
                CommitView._live_views.discard(self)
            if not self._share_context or not CommitView._live_views:
                self.webview.terminate_web_process()
                if self._share_context:
                    CommitView._shared_context = None
            self.webview.destroy()
            self.webview = None
            self._manager = self._context = None


class GitGraphView(_WebHost):
    _api = 'IDGit'
    _page = 'gitgraph_web/gitgraph.html'
    _commands = frozenset(('setData', 'setState', 'setTheme', 'setSettings', 'select',
                           'focus', 'pause', 'resume'))

    def __init__(self, root: str, *, page: str | None = None):
        self._worker_running = False
        self._dirty = False
        self._digest = None
        self._selection = None
        self._focus = None
        self._monitors = {}
        self._debounce = None
        self._focus_window = None
        self._focus_handler = None
        super().__init__(root, page=page)
        if self.available:
            self._connect(self, 'hierarchy-changed', self._hierarchy_changed)
            self._generation += 1
            self._start_collection()

    def set_theme(self, ui: dict, accent: str):
        self._apply_theme(git_colors(ui, accent))

    def select(self, hash: str | None):
        self._selection = hash
        self.post('select', hash)

    def focus_head(self):
        self._focus = 'HEAD'
        self.post('focus', 'HEAD')

    def _post_data(self):
        self.post('setData', self.last_data)
        self.post('select', self._selection)
        if self._focus is not None:
            self.post('focus', self._focus)

    def refresh(self):
        if self._closed or not self.available:
            return
        self._generation += 1
        self._dirty = True
        if self.get_mapped() and not self._worker_running:
            self._start_collection()

    def _start_collection(self):
        self._worker_running = True
        self._dirty = False
        generation = self._generation
        root = self.root
        def collect():
            data = gitlog.collect_graph(root)
            directories = set()
            # Linked worktrees keep HEAD locally and refs in the common Git dir.
            for arg in ('--absolute-git-dir', '--git-common-dir'):
                try:
                    raw, _ = gitlog._run(root, ['rev-parse', arg], time.monotonic() + 5, cap=65536)
                    gitdir = os.path.abspath(os.path.join(root, os.fsdecode(raw).strip()))
                except gitlog.GitError:
                    continue
                directories.add(gitdir)
            paths = set()
            for gitdir in directories:
                paths.add((gitdir, True))
                for name in ('HEAD', 'logs/HEAD', 'packed-refs'):
                    paths.add((os.path.join(gitdir, name), False))
                for name in ('refs/heads', 'refs/remotes', 'refs/tags'):
                    path = os.path.join(gitdir, name)
                    paths.add((path, True))
                    for directory, _dirs, _files in os.walk(path):
                        paths.add((directory, True))
                        if len(paths) >= 512:
                            break
            return data, paths
        def deliver(result):
            self._worker_running = False
            if isinstance(result, tuple):
                data, paths = result
                self._watch(paths)
            else:
                data = result
            if generation == self._generation:
                digest = hashlib.blake2s(json.dumps(data, sort_keys=True).encode()).digest()
                if digest != self._digest:
                    self._digest = digest
                    if data.get('ok'):
                        self.last_data = data
                        self._post_data()
                    else:
                        self.last_data = None
                        error = str(data.get('error', 'error'))
                        self._set_state(error if error in ('empty', 'not-a-repo', 'git-missing') else 'error', error)
            if self._dirty and self.get_mapped():
                self._start_collection()
        self._launch(collect, deliver)

    def _watch(self, paths):
        for key in self._monitors.keys() - paths:
            self._monitors.pop(key).cancel()
        for path, directory in paths - self._monitors.keys():
            try:
                file = Gio.File.new_for_path(path)
                monitor = (file.monitor_directory if directory else file.monitor_file)(
                    Gio.FileMonitorFlags.WATCH_MOVES, None)
                monitor.connect('changed', self._repo_changed)
                self._monitors[(path, directory)] = monitor
            except GLib.Error:
                pass

    def _repo_changed(self, *_args):
        if self._closed:
            return
        if self._debounce is not None:
            GLib.source_remove(self._debounce)
        def refresh():
            self._debounce = None
            self.refresh()
            return GLib.SOURCE_REMOVE
        self._debounce = GLib.timeout_add(300, refresh)

    def _hierarchy_changed(self, *_args):
        window = self.get_toplevel()
        if window is self._focus_window:
            return
        if self._focus_window is not None and self._focus_window.handler_is_connected(self._focus_handler):
            self._focus_window.disconnect(self._focus_handler)
        self._focus_window = window if isinstance(window, Gtk.Window) else None
        self._focus_handler = (window.connect('focus-in-event', self._focus_in)
                               if self._focus_window is not None else None)

    def _focus_in(self, *_args):
        self._repo_changed()
        return False

    def _mapped(self, widget):
        super()._mapped(widget)
        self._hierarchy_changed()
        if self._dirty and not self._worker_running:
            self._start_collection()

    def _page_message(self, message):
        if message.get('type') == 'refresh':
            self.refresh()

    def shutdown(self):
        if self._closed:
            return
        if self._debounce is not None:
            GLib.source_remove(self._debounce)
            self._debounce = None
        for monitor in self._monitors.values():
            monitor.cancel()
        self._monitors.clear()
        if self._focus_window is not None and self._focus_window.handler_is_connected(self._focus_handler):
            self._focus_window.disconnect(self._focus_handler)
        self._focus_window = None
        super().shutdown()


class CommitView(_WebHost):
    __gsignals__ = {'open-file': (GObject.SignalFlags.RUN_LAST, None, (str, int))}
    _api = 'IDCommit'
    _live_views = weakref.WeakSet()
    _page = 'gitcommit_web/commit.html'
    _share_context = True
    _commands = frozenset(('setCommit', 'setState', 'setTheme', 'setSettings',
                           'provideContext', 'pause', 'resume'))

    def __init__(self, root: str, rev: str, *, page: str | None = None):
        self.rev = rev
        self._worker_running = False
        self._reload_pending = False
        self._contexts = []
        self._context_running = False
        super().__init__(root, page=page)
        self.reload()

    def set_theme(self, ui: dict, accent: str, syntax_style: dict | None = None):
        self._apply_theme(commit_colors(ui, accent, syntax_style))

    def reload(self):
        if self._closed or not self.available:
            return
        self._generation += 1
        self.last_data = None
        self._contexts.clear()
        self._set_state('loading')
        self._reload_pending = True
        if not self._worker_running:
            self._load_detail()

    def _load_detail(self):
        self._worker_running = True
        self._reload_pending = False
        generation, root, rev = self._generation, self.root, self.rev
        def deliver(detail):
            self._worker_running = False
            if generation == self._generation:
                if detail.get('ok'):
                    self.last_data = detail
                    self._post_data()
                else:
                    error = str(detail.get('error', 'error'))
                    self._set_state('not-found' if error in ('unknown-revision', 'not-a-repo') else 'error', error)
            if self._reload_pending:
                self._load_detail()
        self._launch(lambda: gitdiff.commit_detail(root, rev), deliver)

    def _post_data(self):
        self.post('setCommit', self.last_data)

    def _page_message(self, message):
        if self._closed:
            return
        kind = message.get('type')
        if kind == 'copy' and isinstance(message.get('text'), str):
            Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(message['text'], -1)
        elif kind == 'open-file':
            path = message.get('path')
            if not isinstance(path, str) or not path or os.path.isabs(path) or '\0' in path:
                return
            absolute = os.path.abspath(os.path.join(self.root, path))
            real_root = os.path.realpath(self.root)
            if os.path.commonpath((real_root, os.path.realpath(absolute))) != real_root:
                return
            line = message.get('line', 1)
            line = max(1, min(2147483647, line)) if type(line) is int else 1
            self.emit('open-file', absolute, line)
        elif kind == 'expand' and self.last_data is not None:
            path, start, end = (message.get(k) for k in ('path', 'start', 'end'))
            if (not isinstance(path, str) or type(start) is not int or type(end) is not int
                    or start < 1 or end < start or len(self._contexts) >= 32):
                return
            self._contexts.append((self._generation, self.last_data['hash'], path, start, min(end, start + 199)))
            self._expand_next()

    def _expand_next(self):
        if self._closed or self._context_running or not self._contexts:
            return
        generation, rev, path, start, end = self._contexts.pop(0)
        root = self.root
        self._context_running = True
        def deliver(result):
            self._context_running = False
            if generation == self._generation:
                self._call('provideContext', path, start, end, result.get('lines', []))
            self._expand_next()
        self._launch(lambda: gitdiff.file_context(root, rev, path, start, end), deliver)

    def shutdown(self):
        self._contexts.clear()
        super().shutdown()
