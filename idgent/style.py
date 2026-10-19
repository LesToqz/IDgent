"""Theme-driven GTK CSS, with one shared provider for all application widgets."""
import dataclasses

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, GLib, Gtk

from idgent.explorer import build_css as build_explorer_css
from idgent.editor import build_css as build_editor_css
from idgent.terminal_bar import build_css as build_terminal_bar_css
from idgent.overlay import build_css as build_overlay_css
from idgent.palette import build_css as build_palette_css
from idgent.themes import Theme, _contrast, _legible, _mix_color, get_theme
from idgent.titlebar import build_titlebar_css
from idgent.settings_dialog import build_css as build_settings_css
from idgent.search import build_css as build_search_css


# Concrete values from docs/reference_app-tokens.json; no file IO at runtime.
# Spacing/radii are pixels, durations milliseconds, easings GTK CSS strings.
TOKENS = {
    'spacing': {
        'size-2-1': 2,
        'size-2-2': 4,
        'size-2-3': 6,
        'size-4-1': 4,
        'size-4-2': 8,
        'size-4-3': 12,
        'size-4-4': 16,
        'size-4-5': 20,
        'size-4-6': 24,
        'size-4-8': 32,
        'size-4-9': 36,
        'size-4-10': 40,
        'size-4-12': 48,
        'size-4-16': 64,
        'size-4-18': 72,
    },
    'radius': {
        's': 4,
        'm': 8,
        'l': 12,
        'xl': 24,
        'input': 5,
        'button': 5,
        'checkbox': 4,
        'clickable-icon': 4,
        'nav-item': 8,
        'menu': 8,
        'menu-item': 4,
        'modal': 12,
        'prompt': 12,
        'popover': 8,
        'tooltip': 12,
        'notice': 8,
        'setting-items-card': 12,
        'tab-inner': 4,
        'tab-active-top': '10px 10px 0 0',
        'status-bar': '8px 0 0 0',
        'status-bar-item': 4,
        'toggle': 18,
        'hotkey-chip': '2em (pill)',
        'scrollbar': 12,
        'suggestion-item': 4,
    },
    'durations': {
        'none': 0,
        'superfast': 70,
        'fast': 140,
        'moderate': 300,
        'slow': 560,
        'js-tween-default': 100,
        'tree-expand-collapse': 100,
        'chevron-rotate': 100,
        'toggle-thumb-slide': 150,
        'toggle-thumb-press-grow': 100,
        'toggle-thumb-press-delay': 50,
        'toggle-track': 150,
        'slider-thumb': 100,
        'input-hover-border': 140,
        'clickable-icon-opacity': 140,
        'settings-row-navigable': 150,
        'resize-handle-color': 200,
        'drop-overlay': 100,
        'tab-strip-padding': 75,
        'tab-close-reflow': 300,
        'notice-enter': 100,
        'notice-exit': 120,
        'notice-autohide': 4000,
        'notice-hover-grace': 1000,
        'tooltip-delay': 1000,
        'tooltip-warm-window': 100,
        'submenu-open-delay': 250,
        'flash-highlight': 250,
        'ribbon-collapsed-bg': 250,
        'ribbon-collapsed-bg-delay': 95,
        'workspace-padding': 100,
        'page-slide-mobile': 150,
        'sheet-mobile-min': 100,
        'sheet-mobile-max': 250,
        'window-geometry-save-debounce': 100,
        'modal-open-desktop': 0,
        'modal-close-desktop': 0,
        'menu-open-desktop': 0,
        'tooltip-fade': 0,
        'spinner-rotation': 1000,
    },
    'easings': {
        'smooth': 'cubic-bezier(0.45, 0.05, 0.55, 0.95)',
        'delay': 'cubic-bezier(0.65, 0.05, 0.36, 1)',
        'jumpy': 'cubic-bezier(0.68, -0.55, 0.27, 1.55)',
        'swing': 'cubic-bezier(0, 0.55, 0.45, 1)',
        'ease-in-out': 'cubic-bezier(0.42, 0, 0.58, 1)',
        'ease-out': 'cubic-bezier(0, 0, 0.58, 1)',
        'ease-in': 'cubic-bezier(0.42, 0, 1, 1)',
        'ease': 'cubic-bezier(0.25, 0.1, 0.25, 1)',
        'linear': 'linear',
        'collapse': 'cubic-bezier(0.02, 0.01, 0.47, 1)',
        'reflow': 'cubic-bezier(0.2, 0, 0, 1)',
        'slide-out': 'cubic-bezier(0.33, 1, 0.68, 1)',
        'sheet-dismiss': 'cubic-bezier(0.36, 0.66, 0.04, 1)',
        'backdrop-fade-in': 'cubic-bezier(0.5, 0, 0.75, 0)',
        'loading-icon': 'cubic-bezier(0.22, 1, 0.36, 1)',
    },
}


def build_css(theme: Theme) -> str:
    """Render all chrome rules from a complete theme."""
    ui = theme.ui
    radius = TOKENS['radius']
    duration = TOKENS['durations']
    easing = TOKENS['easings']['ease-in-out']
    selection_alpha = .33 if theme.dark else .2
    if theme.dark:
        input_shadow = ('inset 0 0.5px 0.5px 0.5px alpha(#ffffff, 0.09), '
                        '0 2px 4px alpha(#000000, 0.15), 0 1px 1.5px alpha(#000000, 0.1), '
                        '0 1px 2px alpha(#000000, 0.2)')
        popup_shadow = ('0 1px 2px alpha(#000000, 0.121), '
                        '0 3.4px 6.7px alpha(#000000, 0.179), 0 15px 30px alpha(#000000, 0.3)')
    else:
        input_shadow = ('inset 0 0 0 1px alpha(#000000, 0.12), '
                        '0 1px 2px alpha(#000000, 0.065)')
        popup_shadow = ('0 1px 2px alpha(#000000, 0.028), '
                        '0 3.4px 6.7px alpha(#000000, 0.042), 0 15px 30px alpha(#000000, 0.07)')
    return f'''
window, dialog, messagedialog {{ background-color: {ui['bg']}; color: {ui['foreground']}; }}
.background {{ background-color: {ui['bg']}; color: {ui['foreground']}; }}
/* GTK min-height is the content box: 22 + 2*4 padding = 30 pixels. */
button, combobox button {{ background: {ui['button_bg']}; color: {ui['foreground']};
    border: 0; border-radius: {radius['button']}px; box-shadow: {input_shadow};
    text-shadow: none; font-size: 13px; min-height: 22px; padding: 4px 12px; }}
button:hover, button:active, button:checked {{ background: alpha({ui['overlay']}, 0.067); }}
button:disabled, entry:disabled, menuitem:disabled {{ color: {ui['faint']}; }}
combobox:disabled {{ opacity: 0.5; }}
combobox arrow {{ color: {ui['muted']}; min-width: 13px; min-height: 13px; }}
button, menuitem, row, notebook > header.top > tabs > tab, treeview.view,
scrollbar, scrollbar slider, menu, popover, tooltip {{ transition: none; }}
*:focus {{ outline-color: {ui['border_focus']}; outline-style: solid;
    outline-width: 2px; outline-offset: -2px; -gtk-outline-radius: {radius['input']}px; }}
button:focus, combobox button:focus {{ outline-width: 3px; outline-offset: 0; }}
entry {{ background: {ui['input_bg']}; color: {ui['foreground']}; caret-color: {ui['foreground']};
    border: 1px solid {ui['border']}; border-radius: {radius['input']}px;
    box-shadow: none; text-shadow: none; font-size: 13px; min-height: 20px; padding: 4px 8px;
    transition: border-color {duration['input-hover-border']}ms {easing},
                box-shadow {duration['input-hover-border']}ms {easing}; }}
entry:hover {{ border-color: {ui['border_hover']}; }}
entry:focus {{ border-color: {ui['border_focus']}; box-shadow: 0 0 0 2px {ui['border_focus']}; }}
entry selection, textview text selection {{ background: alpha({ui['accent']}, {selection_alpha});
    color: {ui['foreground']}; }}
menu, popover {{ background: {ui['sidebar_bg']}; color: {ui['foreground']};
    border: 1px solid {ui['border_hover']}; border-radius: {radius['menu']}px;
    padding: 6px; box-shadow: {popup_shadow}; }}
popover {{ background: {ui['bg']}; }}
menuitem, popover modelbutton {{ padding: 4px 8px; border-radius: {radius['menu-item']}px;
    font-size: 13px; }}
menuitem:hover, menuitem:selected, popover modelbutton:hover, popover modelbutton:selected,
row:hover, row:selected {{ background: alpha({ui['overlay']}, 0.067); color: {ui['foreground']}; }}
menu separator {{ background: {ui['border']}; min-height: 1px; margin: 6px 0; }}
menuitem.destructive-action {{ color: {ui['danger']}; }}
{build_explorer_css(ui)}
{build_editor_css(ui)}
scrollbar, scrollbar trough {{ background: transparent; border: 0;
    box-shadow: none; padding: 0; margin: 0; min-width: 0; min-height: 0; }}
scrollbar slider {{ background: alpha({ui['overlay']}, 0.10); border: 0;
    border-radius: {radius['scrollbar']}px; box-shadow: none;
    min-width: 6px; min-height: 6px; padding: 0; margin: 1px; }}
scrollbar.vertical slider {{ min-height: 45px; }}
scrollbar.horizontal slider {{ min-width: 45px; }}
scrollbar slider:hover, scrollbar slider:active {{ background: alpha({ui['overlay']}, 0.20); }}
scrollbar button {{ min-width: 0; min-height: 0; padding: 0; border: 0;
    margin: 0; box-shadow: none; background: transparent; -gtk-icon-source: none; }}
paned > separator {{ background-color: {ui['border']}; background-image: none;
    -gtk-icon-source: none; min-width: 1px; min-height: 1px;
    border: 0; padding: 0; margin: 0;
    transition: background-color {duration['resize-handle-color']}ms {easing}; }}
paned > separator:hover, paned > separator:active {{ background-color: {ui['accent']}; }}
paned.horizontal > separator {{ border-left: 2px solid transparent; border-right: 2px solid transparent;
    background-clip: padding-box; }}
paned.vertical > separator {{ border-top: 2px solid transparent; border-bottom: 2px solid transparent;
    background-clip: padding-box; }}
{build_terminal_bar_css(ui)}

tooltip, tooltip.background {{ background: alpha(#000000, 0.9); color: #fafafa;
    border: 0; border-radius: {radius['tooltip']}px; padding: 8px 12px;
    font-size: 13px; box-shadow: {popup_shadow}; }}
tooltip label {{ color: #fafafa; }}
dialog .dialog-action-area, messagedialog .dialog-action-area {{ padding: 8px; }}
.idgent-resize-gate {{ background: {ui['panel_bg']}; }}
''' + (build_titlebar_css(ui) + build_settings_css(ui) + build_search_css(ui)
       # Prompts and notices override base entry/row/focus rules: keep them last.
       + build_palette_css(ui) + build_overlay_css(ui))


def _font_family(name: str | None) -> str:
    """A CSS-safe quoted family, or '' for unset/unusable names."""
    if not isinstance(name, str):
        return ''
    name = ''.join(c for c in name.strip() if c.isprintable() and c not in '"\\;{}')
    return f'"{name}"' if name else ''


def with_accent(theme: Theme, accent: str | None) -> Theme:
    """``theme`` with a user accent: accent roles derived contrast-safely.

    Roles that equalled the theme's own accent (status bar, active scrollbar
    in some themes) follow the new colour too. ``None`` returns ``theme``.
    """
    if not accent:
        return theme
    accent = accent.lower()
    old = theme.ui['accent']
    ui = {key: accent if value == old else value for key, value in theme.ui.items()}
    accent_fg = ('#ffffff' if _contrast('#ffffff', accent) >= _contrast('#111111', accent)
                 else '#111111')
    if theme.ui.get('status_bg') == old:
        ui['status_fg'] = accent_fg
    ui.update(accent=accent, accent_fg=accent_fg,
              accent_text=_legible(accent, ui['bg'], 3),
              accent_hover=_mix_color(accent, ui['foreground'], .2))
    return dataclasses.replace(theme, ui=ui)


def build_overrides(ui_font: str | None = None, editor_font: str | None = None) -> str:
    """Interface/editor font-family rules appended after the theme rules."""
    css = ''
    family = _font_family(ui_font)
    if family:
        # Every node: explicit families (editor/terminal monospace) are more specific.
        css += f'* {{ font-family: {family}; }}\n'
    family = _font_family(editor_font)
    if family:
        css += f'.idgent-editor textview, .idgent-editor textview text {{ font-family: {family}; }}\n'
    return css


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


def apply_theme(theme: Theme, accent: str | None = None, ui_font: str | None = None,
                editor_font: str | None = None) -> None:
    """Switch the shared CSS provider; editor syntax/chrome and VTE use their APIs.

    ``accent`` (``#rrggbb``) replaces the theme accent, ``ui_font`` the
    interface font family and ``editor_font`` the editor family; unset
    values (None/'') keep the theme defaults.
    """
    themed = with_accent(theme, accent)
    _apply(themed, build_css(themed) + build_overrides(ui_font, editor_font))


def apply_style(theme: Theme | None = None) -> None:
    """Install or update the shared provider, requiring an initialized display."""
    chosen = get_theme(None) if theme is None else theme
    _apply(chosen, CSS if theme is None else build_css(chosen))
