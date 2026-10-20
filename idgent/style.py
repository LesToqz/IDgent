'IDgent GTK styling with application-owned design tokens.'
import dataclasses

from idgent.motion import DURATIONS

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, GLib, Gtk

from idgent.explorer import build_css as build_explorer_css
from idgent.notes import build_css as build_notes_css
from idgent.editor import build_css as build_editor_css
from idgent.terminal_bar import build_css as build_terminal_bar_css
from idgent.overlay import build_css as build_overlay_css
from idgent.palette import build_css as build_palette_css
from idgent.themes import Theme, _contrast, _legible, _mix_color, get_theme
from idgent.titlebar import build_titlebar_css
from idgent.settings_dialog import build_css as build_settings_css
from idgent.search import build_css as build_search_css


# Spacing/radii are pixels, durations milliseconds, easings GTK CSS strings.
# IDgent geometry is based on a compact three-pixel spacing rhythm.
TOKENS = {
    "spacing": {"tight": 3, "compact": 6, "control": 9,
                "section": 15, "panel": 21, "canvas": 33},
    "radius": {"button": 7, "input": 7, "menu": 11,
               "item": 5, "tooltip": 6, "scrollbar": 4},
    "geometry": {"button_content": 22, "entry_content": 20,
                 "border": 1, "focus_ring": 2, "scrollbar_thumb": 6,
                 "scrollbar_margin": 2, "scrollbar_length": 34,
                 "terminal_scrollbar_margin": 1,
                 "separator": 1, "separator_hit_padding": 3},
    "type": {"control": 14, "menu": 13, "tooltip": 12},
    "opacity": {"hover": 0.09, "disabled": 0.48,
                "selection_dark": 0.26, "selection_light": 0.19,
                "scrollbar": 0.21, "scrollbar_hover": 0.36},
    "durations": {"input_border": DURATIONS["state"],
                  "resize_handle": DURATIONS["state"]},
    "easings": {"state": "cubic-bezier(0.24, 0.08, 0.28, 1)"},
    "shadows": {"dark": "0 5px 17px alpha(#11191d, 0.38)",
                "light": "0 5px 17px alpha(#11191d, 0.16)"},
}


def build_css(theme: Theme) -> str:
    """Compose the IDgent controls with each existing component stylesheet."""
    ui = theme.ui
    radius, space = TOKENS["radius"], TOKENS["spacing"]
    geometry, font = TOKENS["geometry"], TOKENS["type"]
    opacity, duration = TOKENS["opacity"], TOKENS["durations"]
    easing = TOKENS["easings"]["state"]
    selection_alpha = opacity["selection_dark" if theme.dark else "selection_light"]
    popup_shadow = TOKENS["shadows"]["dark" if theme.dark else "light"]
    return f"""
window, dialog, messagedialog {{ background-color: {ui['bg']}; color: {ui['foreground']}; }}
.background {{ background-color: {ui['bg']}; color: {ui['foreground']}; }}
button, combobox button {{ background: {ui['button_bg']}; color: {ui['foreground']};
    border: 0; border-radius: {radius['button']}px; box-shadow: none;
    text-shadow: none; font-size: {font['control']}px;
    min-height: {geometry['button_content']}px; padding: {space['compact']}px {space['control']}px; }}
button:hover, button:active, button:checked {{ background: alpha({ui['overlay']}, {opacity['hover']}); }}
button:disabled, entry:disabled, menuitem:disabled {{ color: {ui['faint']}; }}
combobox:disabled {{ opacity: {opacity['disabled']}; }}
combobox arrow {{ color: {ui['muted']}; min-width: {space['control']}px; min-height: {space['compact']}px; }}
button, menuitem, row, notebook > header.top > tabs > tab, treeview.view,
scrollbar, scrollbar slider, menu, popover, tooltip {{ transition: none; }}
*:focus {{ outline-color: {ui['border_focus']}; outline-style: solid;
    outline-width: {geometry['focus_ring']}px; outline-offset: -{geometry['border']}px;
    -gtk-outline-radius: {radius['input']}px; }}
button:focus, combobox button:focus {{ outline-width: {geometry['focus_ring']}px; outline-offset: 0; }}
entry {{ background: {ui['input_bg']}; color: {ui['foreground']}; caret-color: {ui['foreground']};
    border: {geometry['border']}px solid {ui['border']}; border-radius: {radius['input']}px;
    box-shadow: none; text-shadow: none; font-size: {font['control']}px;
    min-height: {geometry['entry_content']}px; padding: {space['compact']}px {space['control']}px;
    transition: border-color {duration['input_border']}ms {easing},
                box-shadow {duration['input_border']}ms {easing}; }}
entry:hover {{ border-color: {ui['border_hover']}; }}
entry:focus {{ border-color: {ui['border_focus']};
    box-shadow: 0 0 0 {geometry['focus_ring']}px {ui['border_focus']}; }}
entry selection, textview text selection {{ background: alpha({ui['accent']}, {selection_alpha});
    color: {ui['foreground']}; }}
menu, popover {{ background: {ui['sidebar_bg']}; color: {ui['foreground']};
    border: {geometry['border']}px solid {ui['border_hover']}; border-radius: {radius['menu']}px;
    padding: {space['compact']}px; box-shadow: {popup_shadow}; }}
popover {{ background: {ui['bg']}; }}
menuitem, popover modelbutton {{ padding: {space['compact']}px {space['control']}px;
    border-radius: {radius['item']}px; font-size: {font['menu']}px; }}
menuitem:hover, menuitem:selected, popover modelbutton:hover, popover modelbutton:selected,
row:hover, row:selected {{ background: alpha({ui['overlay']}, {opacity['hover']}); color: {ui['foreground']}; }}
menu separator {{ background: {ui['border']}; min-height: {geometry['border']}px; margin: {space['tight']}px 0; }}
menuitem.destructive-action {{ color: {ui['danger']}; }}
{build_explorer_css(ui)}
{build_notes_css(ui)}
{build_editor_css(ui)}
scrollbar, scrollbar trough {{ background: transparent; border: 0;
    box-shadow: none; padding: 0; margin: 0; min-width: 0; min-height: 0; }}
scrollbar slider {{ background: alpha({ui['overlay']}, {opacity['scrollbar']}); border: 0;
    border-radius: {radius['scrollbar']}px; box-shadow: none;
    min-width: {geometry['scrollbar_thumb']}px; min-height: {geometry['scrollbar_thumb']}px;
    padding: 0; margin: {geometry['scrollbar_margin']}px; }}
scrollbar.vertical slider {{ min-height: {geometry['scrollbar_length']}px; }}
scrollbar.horizontal slider {{ min-width: {geometry['scrollbar_length']}px; }}
/* The terminal reserves an eight-pixel lane even without scrollback. */
.idgent-terminal scrollbar.vertical slider {{
    margin-left: {geometry['terminal_scrollbar_margin']}px;
    margin-right: {geometry['terminal_scrollbar_margin']}px; }}
scrollbar slider:hover, scrollbar slider:active {{ background: alpha({ui['overlay']}, {opacity['scrollbar_hover']}); }}
scrollbar button {{ min-width: 0; min-height: 0; padding: 0; border: 0;
    margin: 0; box-shadow: none; background: transparent; -gtk-icon-source: none; }}
paned > separator {{ background-color: {ui['border']}; background-image: none;
    -gtk-icon-source: none; min-width: {geometry['separator']}px; min-height: {geometry['separator']}px;
    border: 0; padding: 0; margin: 0;
    transition: background-color {duration['resize_handle']}ms {easing}; }}
paned > separator:hover, paned > separator:active {{ background-color: {ui['accent']}; }}
paned.horizontal > separator {{ border-left: {geometry['separator_hit_padding']}px solid transparent;
    border-right: {geometry['separator_hit_padding']}px solid transparent; background-clip: padding-box; }}
paned.vertical > separator {{ border-top: {geometry['separator_hit_padding']}px solid transparent;
    border-bottom: {geometry['separator_hit_padding']}px solid transparent; background-clip: padding-box; }}
{build_terminal_bar_css(ui)}
tooltip, tooltip.background {{ background: {ui['panel_bg']}; color: {ui['foreground']};
    border: 0; border-radius: {radius['tooltip']}px; padding: {space['compact']}px {space['control']}px;
    font-size: {font['tooltip']}px; box-shadow: {popup_shadow}; }}
tooltip label {{ color: {ui['foreground']}; }}
dialog .dialog-action-area, messagedialog .dialog-action-area {{ padding: {space['section']}px; }}
.idgent-resize-gate {{ background: {ui['panel_bg']}; }}
""" + (build_titlebar_css(ui) + build_settings_css(ui) + build_search_css(ui)
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
