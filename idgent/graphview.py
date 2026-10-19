"""GTK host for the project graph, with an asynchronous index and a small JS bridge."""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, Gio, GLib, GObject, Gtk

from idgent import settings as preferences
from idgent.graphdata import GraphIndex, GraphOptions
from idgent.themes import _legible, _mix_color, get_theme


# Importing the dock must work on installations without WebKit's typelib.
def _load_webkit():
    gi.require_version('WebKit2', '4.1')
    from gi.repository import WebKit2
    return WebKit2


def graph_colors(ui: dict[str, str], accent: str) -> dict:
    """Resolve canvas roles from the current UI palette, keeping nodes legible."""
    background = ui.get('bg', '#1e1e1e')
    text = ui.get('foreground', '#cccccc')
    muted = ui.get('muted', text)
    groups = [_legible(c, background, 3) for c in preferences.ACCENT_PRESETS]
    return dict(background=background, node=_legible(muted, background, 3),
                node_focused=accent, node_tag=groups[3], node_attachment=groups[2],
                node_unresolved=_legible(muted, background, 3), node_code=groups[1],
                line=_mix_color(background, text, .25), line_highlight=accent,
                text=text, text_muted=_legible(muted, background, 3), groups=groups)


_PLACEHOLDER = '''<!doctype html><meta charset="utf-8"><style>
html,body{margin:0;width:100%;height:100%;overflow:hidden;font:14px sans-serif}
body{display:grid;place-items:center;background:#1e1e1e;color:#ccc}
</style><div id="stats">Loading graph…</div><script>
window.IDGraph = {
 setData(g) { document.getElementById('stats').textContent =
   g.nodes.length + ' nodes / ' + g.links.length + ' links' + (g.truncated ? ' (truncated)' : ''); },
 setTheme(c) { document.body.style.background=c.background; document.body.style.color=c.text; },
 setSettings(s) {}, setActive(id) {}, focus(id) {}, pause() {}, resume() {}, setLocal(o) {}
};
window.webkit.messageHandlers.idgent.postMessage({type:'ready'});
</script>'''
_COMMANDS = frozenset(('setData', 'setTheme', 'setSettings', 'setActive', 'focus',
                       'pause', 'resume', 'setLocal'))
_NO_PAYLOAD = object()


class GraphView(Gtk.Box):
    __gsignals__ = {
        'open-file': (GObject.SignalFlags.RUN_LAST, None, (str, int)),
        'settings-changed': (GObject.SignalFlags.RUN_LAST, None, (object,)),
        'ready': (GObject.SignalFlags.RUN_LAST, None, ()),
        'error': (GObject.SignalFlags.RUN_LAST, None, (str,)),
    }

    def __init__(self, root: str, *, page: str | None = None,
                 settings: dict | None = None, index: GraphIndex | None = None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.root = os.path.abspath(root)
        self.available = False
        self.webview = None
        self._closed = False
        self._ready = False
        self._queue = []
        self._connections = []
        self._cancellable = Gio.Cancellable()
        self._settings = preferences.normalize_graph(settings) if settings is not None else preferences.get_graph()
        self._index = index if index is not None else GraphIndex(self.root)
        self._local_path = None
        self._active_id = None
        self._options_timer = None
        self._lock = threading.Lock()
        self._sources = set()
        self._generation = 0
        self._worker_running = False
        self._dirty = False
        self._scan_needed = True
        self._updates = {}
        self.last_data = None
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.connect('destroy', lambda _w: self.shutdown())
        try:
            WebKit2 = _load_webkit()
        except (ImportError, ValueError):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            box.set_halign(Gtk.Align.CENTER)
            box.set_valign(Gtk.Align.CENTER)
            box.pack_start(Gtk.Label(label='Graph view is unavailable'), False, False, 0)
            hint = Gtk.Label(label='Graph view needs WebKit2 (sudo apt install gir1.2-webkit2-4.1)')
            hint.set_line_wrap(True)
            hint.get_style_context().add_class('dim-label')
            box.pack_start(hint, False, False, 0)
            self.pack_start(box, True, True, 0)
            box.show_all()
            return
        self.available = True
        self._webkit = WebKit2
        self._manager = WebKit2.UserContentManager()
        self._manager.register_script_message_handler('idgent')
        self._connect(self._manager, 'script-message-received::idgent', self._message_received)
        self._context = WebKit2.WebContext.new_ephemeral()
        self.webview = WebKit2.WebView(web_context=self._context, user_content_manager=self._manager)
        web_settings = self.webview.get_settings()
        debug = os.environ.get('IDGENT_GRAPH_DEBUG') == '1'
        web_settings.set_enable_developer_extras(debug)
        web_settings.set_enable_write_console_messages_to_stdout(debug)
        web_settings.set_javascript_can_open_windows_automatically(False)
        if os.environ.get('IDGENT_GRAPH_GPU') != '1':
            web_settings.set_hardware_acceleration_policy(WebKit2.HardwareAccelerationPolicy.NEVER)
        self.webview.set_hexpand(True)
        self.webview.set_vexpand(True)
        self._connect(self.webview, 'context-menu', lambda *_args: True)
        self._connect(self.webview, 'create', lambda *_args: None)
        self._connect(self.webview, 'decide-policy', self._decide_policy)
        self._connect(self.webview, 'load-failed', self._load_failed)
        self._connect(self.webview, 'web-process-terminated', self._process_terminated)
        self._connect(self, 'map', lambda _w: self.post('resume'))
        self._connect(self, 'unmap', lambda _w: self.post('pause'))
        self.pack_start(self.webview, True, True, 0)
        theme = get_theme(preferences.get_theme_id())
        self.set_theme(theme.ui, preferences.get_value('accent') or theme.ui['accent'])
        self.post('setSettings', self._settings)
        self.post('pause')
        self.webview.show()
        page_path = Path(__file__).parent / 'graph_web' / 'graph.html'
        self._page_uri = page_path.resolve().as_uri()
        if page is None and page_path.is_file():
            self.webview.load_uri(self._page_uri)
        else:
            self.webview.load_html(page if page is not None else _PLACEHOLDER, self._page_uri)
        self.rebuild()

    def _connect(self, widget, signal, callback):
        self._connections.append((widget, widget.connect(signal, callback)))

    def _decide_policy(self, _view, decision, kind):
        if kind == self._webkit.PolicyDecisionType.NEW_WINDOW_ACTION:
            decision.ignore()
            return True
        if kind == self._webkit.PolicyDecisionType.NAVIGATION_ACTION:
            # Only the initial local page may navigate. Links communicate via the bridge.
            uri = decision.get_navigation_action().get_request().get_uri()
            if self._ready or uri not in (self._page_uri, 'about:blank'):
                decision.ignore()
                return True
        return False

    def _load_failed(self, _view, _event, _uri, error):
        if not self._closed:
            self.emit('error', error.message)
        return False

    def _process_terminated(self, _view, reason):
        if not self._closed:
            self.emit('error', f'Graph web process stopped: {reason.value_nick}')

    def post(self, command: str, payload=_NO_PAYLOAD) -> None:
        """Queue one contract call until ready; JSON is always passed as data."""
        if self._closed or not self.available:
            return
        if command not in _COMMANDS:
            raise ValueError(f'Unknown graph command: {command}')
        # Serialise at enqueue time so a caller cannot mutate queued messages.
        argument = '' if payload is _NO_PAYLOAD else json.dumps(payload, ensure_ascii=True, allow_nan=False)
        script = f'IDGraph.{command}({argument});'
        if not self._ready:
            self._queue.append(script)
        else:
            self._evaluate(script)

    def _evaluate(self, script):
        if hasattr(self.webview, 'evaluate_javascript'):
            self.webview.evaluate_javascript(script, -1, None, None, self._cancellable,
                                             self._javascript_done, None)
        else:
            self.webview.run_javascript(script, self._cancellable, self._javascript_done, None)

    def _javascript_done(self, view, result, _data):
        if self._closed:
            return
        try:
            if hasattr(view, 'evaluate_javascript_finish'):
                view.evaluate_javascript_finish(result)
            else:
                view.run_javascript_finish(result)
        except GLib.Error as error:
            self.emit('error', error.message)

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
            self.emit('ready')
        elif kind == 'open':
            path = message.get('path')
            if isinstance(path, str) and path:
                absolute = os.path.abspath(os.path.join(self.root, path))
                if os.path.commonpath((absolute, self.root)) != self.root:
                    return
                line = message.get('line', 1)
                line = max(1, min(2147483647, line)) if type(line) is int else 1
                self.emit('open-file', absolute, line)
        elif kind in ('settings', 'options'):
            changes = message.get(kind)
            if not isinstance(changes, dict):
                return
            self._settings = preferences.normalize_graph({**self._settings, **changes})
            if kind == 'options':
                local = changes.get('local', _NO_PAYLOAD)
                if local is not _NO_PAYLOAD:
                    if isinstance(local, dict):
                        local_path = local.get('path', self._active_id)
                        self._local_path = self._node_id(local_path) if isinstance(local_path, str) else None
                        self._settings = preferences.normalize_graph({**self._settings,
                            'localDepth': local.get('depth', self._settings['localDepth']),
                            'localIncoming': local.get('incoming', self._settings['localIncoming']),
                            'localOutgoing': local.get('outgoing', self._settings['localOutgoing'])})
                    else:
                        self._local_path = self._active_id if local is True else None
                if self._options_timer is not None:
                    GLib.source_remove(self._options_timer)
                self._options_timer = GLib.timeout_add(80, self._options_changed)
            preferences.set_graph(self._settings)
            self.emit('settings-changed', dict(self._settings))
        elif kind == 'hover':
            label = message.get('label')
            self.set_tooltip_text(label if isinstance(label, str) else None)
        elif kind == 'error':
            self.emit('error', str(message.get('message', 'Graph error')))

    def _options_changed(self):
        self._options_timer = None
        self.rebuild()
        return GLib.SOURCE_REMOVE

    def set_theme(self, ui: dict, accent: str) -> None:
        if self._closed or not self.available:
            return
        colors = graph_colors(ui, accent)
        background = Gdk.RGBA()
        background.parse(colors['background'])
        self.webview.set_background_color(background)
        self.post('setTheme', colors)

    def _node_id(self, path):
        if path is None:
            return None
        absolute = os.path.abspath(os.path.join(self.root, path))
        if os.path.commonpath((self.root, absolute)) != self.root:
            return None
        return os.path.relpath(absolute, self.root).replace(os.sep, '/')

    def set_active_file(self, path) -> None:
        if self._closed or not self.available:
            return
        self._active_id = self._node_id(path)
        self.post('setActive', self._active_id)
        if self._local_path is not None:
            self.show_local(path, self._settings['localDepth'])

    def refresh_file(self, path) -> None:
        if self._closed or not self.available:
            return
        self._updates[os.fspath(path)] = ('update', None)
        self.rebuild()

    def set_text(self, path, text) -> None:
        if self._closed or not self.available:
            return
        self._updates[os.fspath(path)] = ('text', text)
        self.rebuild()

    def show_local(self, path, depth) -> None:
        if self._closed or not self.available:
            return
        self._local_path = self._node_id(path)
        self._settings = preferences.normalize_graph({**self._settings, 'localDepth': depth})
        self.post('setLocal', dict(path=self._local_path, depth=self._settings['localDepth'],
                                 incoming=self._settings['localIncoming'],
                                 outgoing=self._settings['localOutgoing']))
        self.rebuild()

    def rebuild(self) -> None:
        """Coalesce requests; the worker alone reads/mutates the index."""
        if self._closed or not self.available:
            return
        self._generation += 1
        self._dirty = True
        if not self._worker_running:
            self._start_worker()

    def _start_worker(self):
        self._worker_running = True
        self._dirty = False
        generation = self._generation
        scan, self._scan_needed = self._scan_needed, False
        updates, self._updates = self._updates, {}
        settings = dict(self._settings)
        local = self._local_path
        options = GraphOptions(query=settings['search'], show_tags=settings['showTags'],
                               show_attachments=settings['showAttachments'],
                               existing_only=settings['hideUnresolved'],
                               show_orphans=settings['showOrphans'], show_code=settings['showCode'],
                               groups=settings['colorGroups'])

        def work():
            data, error = None, None
            try:
                if scan:
                    self._index.scan()
                for path, (kind, text) in updates.items():
                    if kind == 'text':
                        self._index.set_text(path, text)
                    else:
                        self._index.update_path(path)
                graph = (self._index.local(local, depth=settings['localDepth'],
                                          incoming=settings['localIncoming'],
                                          outgoing=settings['localOutgoing'], options=options)
                         if local is not None else self._index.build(options))
                data = graph.to_json()
            except Exception as exc:
                error = str(exc)
            with self._lock:
                if self._closed:
                    return
                source = GLib.idle_add(self._deliver, generation, data, error)
                self._sources.add(source)
        threading.Thread(target=work, name='idgent-graph-index', daemon=True).start()

    def _deliver(self, generation, data, error):
        with self._lock:
            source = GLib.main_current_source()
            if source is not None:
                self._sources.discard(source.get_id())
            if self._closed:
                return GLib.SOURCE_REMOVE
        self._worker_running = False
        if generation == self._generation:
            if error is not None:
                self.emit('error', error)
            else:
                self.last_data = data
                self.post('setData', data)
        if self._dirty:
            self._start_worker()
        return GLib.SOURCE_REMOVE

    def do_grab_focus(self):
        if not self._closed and self.webview is not None:
            self.webview.grab_focus()

    def shutdown(self) -> None:
        """Idempotently cancel callbacks and release this view's ephemeral process."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            sources, self._sources = self._sources, set()
        for source in sources:
            GLib.source_remove(source)
        if self._options_timer is not None:
            GLib.source_remove(self._options_timer)
            self._options_timer = None
        self._cancellable.cancel()
        self._queue.clear()
        for widget, handler in self._connections:
            if widget.handler_is_connected(handler):
                widget.disconnect(handler)
        self._connections.clear()
        if self.webview is not None:
            self._manager.unregister_script_message_handler('idgent')
            self.webview.stop_loading()
            self.webview.try_close()
            if hasattr(self.webview, 'terminate_web_process'):
                self.webview.terminate_web_process()
            self.webview.destroy()
            self.webview = None
            self._manager = None
            self._context = None
