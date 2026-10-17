"""Application-wide VS Code Dark+ palette and GTK 3 theme."""

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, GLib, Gtk

from idgent.titlebar import TITLEBAR_CSS

EDITOR_BG = '#1e1e1e'
SIDEBAR_BG = '#252526'
PANEL_BG = '#1e1e1e'
BORDER = '#2b2b2b'
MENUBAR_BG = '#333333'
FOREGROUND = '#cccccc'
MUTED = '#858585'
ACCENT = '#007acc'
SELECTION_ACTIVE = '#094771'
SELECTION_INACTIVE = '#37373d'
HOVER = '#2a2d2e'
TAB_INACTIVE_BG = '#2d2d2d'
EDITOR_FOREGROUND = '#d4d4d4'
EDITOR_SELECTION = '#264f78'
WARNING = '#cca700'
LIMIT_TINT = '#7d6f2a'

CSS = f'''
window, dialog, messagedialog {{ background-color: {EDITOR_BG}; color: {FOREGROUND}; }}
.background {{ background-color: {EDITOR_BG}; color: {FOREGROUND}; }}
menu {{ background: {SIDEBAR_BG}; color: {FOREGROUND}; border: 1px solid {BORDER}; padding: 4px; }}
menuitem {{ padding: 4px 8px; }}
menuitem:hover {{ background: {SELECTION_ACTIVE}; }}
button, entry {{ background: {MENUBAR_BG}; color: {FOREGROUND}; border: 1px solid {BORDER};
    border-radius: 2px; box-shadow: none; text-shadow: none; min-height: 22px; padding: 3px 8px; }}
button:hover {{ background: #505050; }}
button:active {{ background: {SELECTION_ACTIVE}; }}
button:focus, entry:focus {{ border-color: {ACCENT}; outline-color: {ACCENT}; outline-offset: -1px; }}
entry selection {{ background: {EDITOR_SELECTION}; color: white; }}
.idgent-explorer, .idgent-explorer viewport, .idgent-explorer scrolledwindow,
.idgent-explorer treeview {{ background: {SIDEBAR_BG}; color: {FOREGROUND}; }}
.idgent-explorer-header {{ min-height: 35px; padding: 0 6px; }}
.idgent-explorer-title {{ font-size: 11px; font-weight: bold; color: {MUTED}; }}
.idgent-explorer-header button {{ background: transparent; border: 0; border-radius: 2px;
    min-height: 22px; min-width: 22px; padding: 0; }}
.idgent-explorer-header button:hover {{ background: {HOVER}; }}
.idgent-explorer treeview {{ -GtkTreeView-vertical-separator: 2; -GtkTreeView-horizontal-separator: 4;
    -GtkTreeView-expander-size: 12; }}
.idgent-explorer treeview.view:hover {{ background: {HOVER}; }}
.idgent-explorer treeview.view:selected {{ background: {SELECTION_INACTIVE}; color: {FOREGROUND}; }}
.idgent-explorer treeview.view:selected:focus {{ background: {SELECTION_ACTIVE}; }}
.idgent-explorer treeview expander {{ color: {MUTED}; }}
.idgent-editor, .idgent-editor stack, .idgent-editor notebook,
.idgent-editor notebook > stack {{ background: {EDITOR_BG}; color: {FOREGROUND}; border: 0; }}
notebook > header.top {{ background: {SIDEBAR_BG}; border: 0; padding: 0; margin: 0; }}
notebook > header.top > tabs {{ padding: 0; margin: 0; border: 0; }}
notebook > header.top > tabs > tab {{ background: {TAB_INACTIVE_BG};
    color: {MUTED}; border: 0; border-top: 1px solid transparent; border-right: 1px solid {BORDER};
    border-radius: 0; box-shadow: none; min-height: 34px; padding: 0 10px; margin: 0; }}
notebook > header.top > tabs > tab:checked {{ background: {EDITOR_BG};
    color: {FOREGROUND}; border-top-color: {ACCENT}; }}
.idgent-tab-close {{ opacity: 0; min-width: 16px; min-height: 20px; padding: 0 2px;
    border: 0; background: transparent; }}
tab:hover .idgent-tab-close, tab:checked .idgent-tab-close,
.idgent-tab-close:focus {{ opacity: 1; }}
.idgent-tab-close:hover {{ background: #383838; }}
.idgent-editor textview, .idgent-editor textview text {{ background-color: {EDITOR_BG};
    color: {EDITOR_FOREGROUND}; caret-color: white; font-family: monospace; font-size: 11pt; }}
.idgent-editor textview text selection {{ background-color: {EDITOR_SELECTION}; color: white; }}
.idgent-empty {{ color: {MUTED}; }}
.idgent-find-bar > revealer > box {{ background: {SIDEBAR_BG}; border-top: 1px solid {BORDER};
    border-bottom: 0; box-shadow: none; padding: 6px; }}
.idgent-status {{ background: {ACCENT}; color: white; font-size: 12px; padding: 3px 10px; }}
scrollbar, scrollbar trough {{ background: transparent; border: 0;
    box-shadow: none; padding: 0; margin: 0; min-width: 0; min-height: 0; }}
scrollbar slider {{ background: #424242; border: 0; border-radius: 0;
    box-shadow: none; min-width: 8px; min-height: 8px; padding: 0; margin: 0; }}
scrollbar.vertical slider {{ min-height: 24px; }}
scrollbar.horizontal slider {{ min-width: 24px; }}
scrollbar slider:hover {{ background: #4f4f4f; }}
scrollbar slider:active {{ background: #686868; }}
scrollbar button {{ min-width: 0; min-height: 0; padding: 0; border: 0;
    margin: 0; box-shadow: none; background: transparent; -gtk-icon-source: none; }}
paned > separator {{ background-color: {BORDER}; background-image: none;
    -gtk-icon-source: none; min-width: 1px; min-height: 1px;
    border: 0; padding: 0; margin: 0; }}
paned.horizontal > separator {{ border-left: 2px solid transparent; border-right: 2px solid transparent;
    background-clip: padding-box; }}
paned.vertical > separator {{ border-top: 2px solid transparent; border-bottom: 2px solid transparent;
    background-clip: padding-box; }}
.idgent-terminal {{ background: {PANEL_BG}; color: {FOREGROUND}; }}
.idgent-terminal-sep {{ background: {BORDER}; min-width: 1px; min-height: 1px; border: 0; }}
.idgent-vte {{ padding: 6px; }}
.idgent-terminal-bar {{ background: {PANEL_BG}; color: {MUTED}; border: 0;
    border-bottom: 1px solid {BORDER}; box-shadow: none; min-height: 29px; padding: 0 4px; }}
.idgent-terminal-bar button {{ background: transparent; color: {MUTED}; border: 0; border-radius: 2px;
    box-shadow: none; text-shadow: none; min-height: 22px; min-width: 22px; padding: 0; }}
.idgent-terminal-bar button:hover {{ background: {HOVER}; color: {FOREGROUND}; }}
.idgent-terminal-chip {{ background: transparent; color: {MUTED}; border: 0;
    border-bottom: 2px solid transparent; margin: 0 2px 0 0; padding: 0 2px 0 4px;
    transition: background 120ms ease-in-out, color 120ms ease-in-out; }}
.idgent-terminal-chip:hover {{ background: {HOVER}; color: {FOREGROUND}; }}
.idgent-terminal-chip.active {{ color: {FOREGROUND}; border-bottom-color: {ACCENT}; }}
.idgent-terminal-chip.active button {{ color: {FOREGROUND}; }}
.idgent-terminal-chip button:hover {{ background: #3a3d3e; }}
.idgent-terminal-chip .idgent-terminal-chip-label {{ min-width: 0; padding: 0 6px 0 2px; font-size: 12px; }}
.idgent-terminal-chip .idgent-terminal-chip-label:hover {{ background: transparent; }}
.idgent-terminal-add {{ margin: 0 2px; }}
.idgent-terminal-add.limit {{ color: {LIMIT_TINT}; }}
.idgent-terminal-add.limit:hover {{ color: {WARNING}; }}
.idgent-terminal-notice {{ color: {WARNING}; font-size: 12px; padding: 0 8px; }}
'''

CSS += TITLEBAR_CSS

_provider = None
_screen = None


def _create_provider() -> Gtk.CssProvider:
    provider = Gtk.CssProvider()
    errors = []
    handler = provider.connect('parsing-error', lambda _, section, error: errors.append(str(error)))
    try:
        provider.load_from_data(CSS.encode('utf-8'))
    except GLib.Error as error:
        raise RuntimeError(f'IDgent theme CSS failed to parse: {error}') from error
    finally:
        provider.disconnect(handler)
    if errors:
        raise RuntimeError('IDgent theme CSS failed to parse: ' + '; '.join(errors))
    return provider


def apply_style() -> None:
    """Install one shared provider; reject invalid CSS before registering it."""
    global _provider, _screen
    settings = Gtk.Settings.get_default()
    screen = Gdk.Screen.get_default()
    if settings is None or screen is None:
        raise RuntimeError('IDgent theme requires an initialized GTK display')
    settings.set_property('gtk-application-prefer-dark-theme', True)
    if _provider is not None:
        return
    provider = _create_provider()
    Gtk.StyleContext.add_provider_for_screen(screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _provider, _screen = provider, screen
