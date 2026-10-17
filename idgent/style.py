"""Theme-driven GTK CSS, with one shared provider for all application widgets."""
import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, GLib, Gtk

from idgent.themes import Theme, get_theme
from idgent.titlebar import build_titlebar_css


def build_css(theme: Theme) -> str:
    """Render all chrome rules from a complete theme."""
    ui = theme.ui
    return f'''
window, dialog, messagedialog {{ background-color: {ui['bg']}; color: {ui['foreground']}; }}
.background {{ background-color: {ui['bg']}; color: {ui['foreground']}; }}
menu {{ background: {ui['sidebar_bg']}; color: {ui['foreground']}; border: 1px solid {ui['border']}; border-radius: 4px; padding: 4px; }}
menuitem {{ padding: 4px 8px; }}
menuitem:hover {{ background: {ui['selection_active']}; }}
button, entry {{ background: {ui['button_bg']}; color: {ui['foreground']}; border: 1px solid {ui['border']};
    border-radius: 4px; box-shadow: none; text-shadow: none; min-height: 22px; padding: 4px 8px; }}
button:hover {{ background: {ui['button_hover']}; }}
button:active {{ background: {ui['selection_active']}; }}
button:focus, entry:focus {{ border-color: {ui['accent']}; outline-color: {ui['accent']}; outline-offset: -1px; }}
entry selection {{ background: {ui['editor_selection']}; color: {ui['selection_fg']}; }}
.idgent-explorer, .idgent-explorer viewport, .idgent-explorer scrolledwindow,
.idgent-explorer treeview {{ background: {ui['sidebar_bg']}; color: {ui['foreground']}; }}
.idgent-explorer-header {{ min-height: 35px; padding: 0 8px; }}
.idgent-explorer-title {{ font-size: 11px; font-weight: bold; color: {ui['muted']}; }}
.idgent-explorer-header button {{ background: transparent; border: 0; border-radius: 4px;
    min-height: 22px; min-width: 22px; padding: 0; }}
.idgent-explorer-header button:hover {{ background: {ui['hover']}; }}
.idgent-explorer treeview {{ -GtkTreeView-vertical-separator: 2; -GtkTreeView-horizontal-separator: 4;
    -GtkTreeView-expander-size: 12; }}
.idgent-explorer treeview.view:hover {{ background: {ui['hover']}; }}
.idgent-explorer treeview.view:selected {{ background: {ui['selection_inactive']}; color: {ui['foreground']}; }}
.idgent-explorer treeview.view:selected:focus {{ background: {ui['selection_active']}; }}
.idgent-explorer treeview expander {{ color: {ui['muted']}; }}
.idgent-editor, .idgent-editor stack, .idgent-editor notebook,
.idgent-editor notebook > stack {{ background: {ui['bg']}; color: {ui['foreground']}; border: 0; }}
notebook > header.top {{ background: {ui['sidebar_bg']}; border: 0; padding: 0; margin: 0; }}
notebook > header.top > tabs {{ padding: 0; margin: 0; border: 0; }}
notebook > header.top > tabs > tab {{ background: {ui['tab_inactive_bg']};
    color: {ui['muted']}; border: 0; border-top: 2px solid transparent; border-right: 1px solid {ui['border']};
    border-radius: 0; box-shadow: none; min-height: 33px; padding: 0 12px; margin: 0; }}
notebook > header.top > tabs > tab:checked {{ background: {ui['bg']};
    color: {ui['foreground']}; border-top-color: {ui['accent']}; }}
.idgent-tab-close {{ opacity: 0; min-width: 16px; min-height: 20px; padding: 0 2px;
    border: 0; background: transparent; }}
tab:hover .idgent-tab-close, tab:checked .idgent-tab-close,
.idgent-tab-close:focus {{ opacity: 1; }}
.idgent-tab-close:hover {{ background: {ui['tab_close_hover']}; }}
.idgent-editor textview, .idgent-editor textview text {{ background-color: {ui['bg']};
    color: {ui['editor_fg']}; caret-color: {ui['cursor']}; font-family: monospace; font-size: 11pt; }}
.idgent-editor textview text selection {{ background-color: {ui['editor_selection']}; color: {ui['selection_fg']}; }}
.idgent-empty {{ color: {ui['muted']}; font-size: 16px; padding: 12px; }}
.idgent-find-bar > revealer > box {{ background: {ui['sidebar_bg']}; border-top: 1px solid {ui['border']};
    border-bottom: 0; box-shadow: none; padding: 8px; }}
.idgent-status {{ background: {ui['status_bg']}; color: {ui['status_fg']}; font-size: 12px; padding: 4px 12px; }}
scrollbar, scrollbar trough {{ background: transparent; border: 0;
    box-shadow: none; padding: 0; margin: 0; min-width: 0; min-height: 0; }}
scrollbar slider {{ background: {ui['scrollbar']}; border: 0; border-radius: 0;
    box-shadow: none; min-width: 8px; min-height: 8px; padding: 0; margin: 0; }}
scrollbar.vertical slider {{ min-height: 24px; }}
scrollbar.horizontal slider {{ min-width: 24px; }}
scrollbar slider:hover {{ background: {ui['scrollbar_hover']}; }}
scrollbar slider:active {{ background: {ui['scrollbar_active']}; }}
scrollbar button {{ min-width: 0; min-height: 0; padding: 0; border: 0;
    margin: 0; box-shadow: none; background: transparent; -gtk-icon-source: none; }}
paned > separator {{ background-color: {ui['border']}; background-image: none;
    -gtk-icon-source: none; min-width: 1px; min-height: 1px;
    border: 0; padding: 0; margin: 0; }}
paned.horizontal > separator {{ border-left: 2px solid transparent; border-right: 2px solid transparent;
    background-clip: padding-box; }}
paned.vertical > separator {{ border-top: 2px solid transparent; border-bottom: 2px solid transparent;
    background-clip: padding-box; }}
.idgent-terminal {{ background: {ui['panel_bg']}; color: {ui['foreground']}; }}
.idgent-terminal-sep {{ background: {ui['border']}; min-width: 1px; min-height: 1px; border: 0; }}
.idgent-vte {{ padding: 8px; }}
.idgent-terminal-header {{ background: {ui['panel_bg']}; color: {ui['muted']}; border: 0;
    border-bottom: 1px solid {ui['border']}; box-shadow: none; min-height: 29px; padding: 0 4px 0 0; }}
.idgent-terminal-header.corner {{ padding-right: 34px; }}
.idgent-terminal-header button {{ background: transparent; color: {ui['muted']}; border: 0; border-radius: 4px;
    box-shadow: none; text-shadow: none; min-height: 22px; min-width: 22px; padding: 0; margin: 3px 0 3px 2px; }}
.idgent-terminal-header button:hover {{ background: {ui['hover']}; color: {ui['foreground']}; }}
.idgent-terminal-header .idgent-terminal-header-label {{ min-width: 0; margin: 0; padding: 0 12px;
    border-radius: 0; font-size: 12px; }}
.idgent-terminal-header .idgent-terminal-header-label:hover {{ background: transparent; color: {ui['foreground']}; }}
.idgent-terminal-header.active {{ color: {ui['foreground']}; border-bottom-color: {ui['accent']}; }}
.idgent-terminal-header.active button {{ color: {ui['foreground']}; }}
.idgent-terminal-add {{ background: transparent; border: 0; border-radius: 4px; box-shadow: none;
    text-shadow: none; min-height: 22px; min-width: 24px; padding: 0; margin: 3px 4px 0 0;
    color: {ui['foreground']}; }}
.idgent-terminal-add:hover {{ background: {ui['hover']}; color: {ui['hover_fg']}; }}
.idgent-terminal-add.limit {{ color: {ui['limit_tint']}; }}
.idgent-terminal-add.limit:hover {{ color: {ui['warning']}; }}
.idgent-terminal-toast {{ background: {ui['toast_bg']}; color: {ui['warning']}; font-size: 12px;
    border: 1px solid {ui['limit_tint']}; border-radius: 4px; padding: 4px 12px; }}

entry {{ background: {ui['input_bg']}; }}
button, menuitem, notebook > header.top > tabs > tab, .idgent-explorer treeview.view {{
    transition: background 120ms ease, color 120ms ease;
}}
notebook > header.top > tabs > tab:hover {{ background: {ui['hover']}; color: {ui['foreground']}; }}
notebook > header.top > tabs > tab:checked:hover {{ background: {ui['bg']}; }}
*:focus {{ outline-color: {ui['accent']}; outline-style: solid; outline-width: 1px; outline-offset: -1px; }}
tooltip, tooltip.background {{ background: {ui['panel_bg']}; color: {ui['foreground']};
    border: 1px solid {ui['border']}; border-radius: 4px; padding: 8px; }}
tooltip label {{ color: {ui['foreground']}; }}
dialog .dialog-action-area, messagedialog .dialog-action-area {{ padding: 8px; }}
.idgent-resize-gate {{ background: {ui['panel_bg']}; }}
''' + build_titlebar_css(ui)


# Compatibility for existing consumers and strict CSS parser tests.
CSS = build_css(get_theme(None))
_provider = None
_screen = None
_current = None


def current_theme() -> Theme | None:
    """The successfully applied theme, or None before the first installation."""
    return _current


def _create_provider(css=None) -> Gtk.CssProvider:
    provider = Gtk.CssProvider()
    errors = []
    handler = provider.connect('parsing-error', lambda _, section, error: errors.append(str(error)))
    try:
        provider.load_from_data((CSS if css is None else css).encode('utf-8'))
    except GLib.Error as error:
        raise RuntimeError(f'IDgent theme CSS failed to parse: {error}') from error
    finally:
        provider.disconnect(handler)
    if errors:
        raise RuntimeError('IDgent theme CSS failed to parse: ' + '; '.join(errors))
    return provider


def _apply(theme, css):
    global _provider, _screen, _current
    settings = Gtk.Settings.get_default()
    screen = Gdk.Screen.get_default()
    if settings is None or screen is None:
        raise RuntimeError('IDgent theme requires an initialized GTK display')
    # A staging provider catches errors before either the active provider or the
    # GTK dark preference changes. Only the shared provider is ever registered.
    validated = _create_provider(css)
    if _provider is None:
        Gtk.StyleContext.add_provider_for_screen(screen, validated,
                                                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        _provider, _screen = validated, screen
    else:
        _provider.load_from_data(css.encode('utf-8'))
    settings.set_property('gtk-application-prefer-dark-theme', theme.dark)
    _current = theme


def apply_theme(theme: Theme) -> None:
    """Switch the shared CSS provider; editor syntax/chrome and VTE use their APIs."""
    _apply(theme, build_css(theme))


def apply_style(theme: Theme | None = None) -> None:
    """Install or update the shared provider (default Dark+), requiring a display."""
    chosen = get_theme(None) if theme is None else theme
    _apply(chosen, CSS if theme is None else build_css(chosen))
