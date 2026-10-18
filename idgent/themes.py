"""Display-independent theme catalogue.

Syntax entries use the same immutable tuple protocol as highlight.SyntaxStyle.
Keeping that small data representation here avoids importing GTK through highlight.
Published palettes: draculatheme.com/spec, nordtheme.com/docs/colors-and-palettes,
ethanschoonover.com/solarized, github.com/atom/one-dark-syntax,
github.com/microsoft/vscode/tree/main/extensions/theme-defaults,
github.com/primer/github-vscode-theme. Contrast-adjusted shades retain palette hues.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import NamedTuple, TYPE_CHECKING

if TYPE_CHECKING:
    from idgent.highlight import SyntaxStyle

KINDS = ('comment', 'doc', 'string', 'escape', 'regex', 'number', 'keyword', 'control', 'operator',
         'constant', 'builtin', 'function', 'class', 'decorator', 'variable', 'property', 'tag',
         'attribute', 'selector', 'heading', 'strong', 'emphasis', 'link', 'preproc', 'punctuation',
         'error')


class _Style(NamedTuple):
    foreground: str
    bold: bool = False
    italic: bool = False
    underline: bool = False


# Runtime alias keeps annotations introspectable without importing the GUI.
if not TYPE_CHECKING:
    SyntaxStyle = _Style


_DARK_PLUS = {
    'comment': _Style('#6A9955'),
    'doc': _Style('#CE9178'),
    'string': _Style('#CE9178'),
    'escape': _Style('#D7BA7D'),
    'regex': _Style('#D16969'),
    'number': _Style('#B5CEA8'),
    'keyword': _Style('#569CD6'),
    'control': _Style('#C586C0'),
    'operator': _Style('#D4D4D4'),
    'constant': _Style('#569CD6'),
    'builtin': _Style('#DCDCAA'),
    'function': _Style('#DCDCAA'),
    'class': _Style('#4EC9B0'),
    'decorator': _Style('#DCDCAA'),
    'variable': _Style('#9CDCFE'),
    'property': _Style('#9CDCFE'),
    'tag': _Style('#569CD6'),
    'attribute': _Style('#9CDCFE'),
    'selector': _Style('#D7BA7D'),
    'heading': _Style('#569CD6', bold=True),
    'strong': _Style('#569CD6', bold=True),
    'emphasis': _Style('#D4D4D4', italic=True),
    'link': _Style('#3794FF', underline=True),
    'preproc': _Style('#C586C0'),
    'punctuation': _Style('#808080'),
    'error': _Style('#F44747'),
}


@dataclass(frozen=True)
class TerminalColors:
    foreground: str
    background: str
    cursor: str
    ansi: tuple[str, ...]

    def __post_init__(self):
        if len(self.ansi) != 16 or any(re.fullmatch(r'#[0-9a-f]{6}', c) is None
                                       for c in self.ansi):
            raise ValueError('A terminal palette requires exactly 16 #rrggbb colors')


@dataclass(frozen=True)
class Theme:
    id: str
    name: str
    dark: bool
    ui: dict[str, str]
    syntax: dict[str, SyntaxStyle]
    terminal: TerminalColors
    chrome: dict[str, str]


def _luminance(color):
    rgb = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in rgb]
    return sum(a * b for a, b in zip(linear, (.2126, .7152, .0722)))


def _contrast(a, b):
    x, y = sorted((_luminance(a), _luminance(b)))
    return (y + .05) / (x + .05)


def _legible(color, background, minimum):
    """Move a palette shade toward ink/paper only as far as contrast requires."""
    target = 255 if _luminance(background) < .3 else 0
    rgb = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
    for step in range(101):
        result = '#' + ''.join(f'{round(v + (target - v) * step / 100):02x}' for v in rgb)
        if _contrast(result, background) >= minimum:
            return result
    return result


_DARK_UI = dict(
    bg='#1e1e1e', sidebar_bg='#252526', panel_bg='#1e1e1e', border='#2b2b2b',
    titlebar_bg='#333333', titlebar_fg='#cccccc', title_fg='#9d9d9d',
    foreground='#cccccc', muted='#858585', accent='#007acc', accent_fg='#ffffff',
    selection_active='#094771', selection_inactive='#37373d', hover='#2a2d2e',
    tab_inactive_bg='#2d2d2d', editor_fg='#d4d4d4', editor_selection='#264f78',
    selection_fg='#ffffff', warning='#cca700', limit_tint='#7d6f2a', toast_bg='#3a331a',
    status_bg='#007acc', status_fg='#ffffff', button_bg='#333333', button_hover='#505050',
    input_bg='#333333', scrollbar='#424242', scrollbar_hover='#4f4f4f',
    scrollbar_active='#686868', tab_close_hover='#383838', cursor='#ffffff',
    close_hover='#c42b1c', close_fg='#ffffff', hover_fg='#ffffff',
)


def _mix_color(background, foreground, amount):
    """Resolve an opaque compatibility role; CSS overlays use alpha() instead."""
    return '#' + ''.join(f'{round(int(background[i:i + 2], 16) * (1 - amount) + int(foreground[i:i + 2], 16) * amount):02x}'
                         for i in (1, 3, 5))


def _extend_ui(ui, dark):
    """Add design-system roles without changing any legacy component colours."""
    ui.update(surface_alt=ui['panel_bg'],
              border_hover=_mix_color(ui['border'], ui['foreground'], .15),
              border_focus=_mix_color(ui['border'], ui['foreground'], .35),
              faint=ui['muted'], accent_text=_legible(ui['accent'], ui['bg'], 3),
              accent_hover=_mix_color(ui['accent'], ui['foreground'], .2),
              overlay='#ffffff' if dark else '#000000', danger=ui['close_hover'])


_extend_ui(_DARK_UI, True)

_DARK_ANSI = ('#000000', '#cd3131', '#0dbc79', '#e5e510', '#2472c8', '#bc3fbc',
              '#11a8cd', '#e5e5e5', '#666666', '#f14c4c', '#23d18b', '#f5f543',
              '#3b8eea', '#d670d6', '#29b8db', '#ffffff')
_DARK_CHROME = dict(gutter_bg='#1e1e1e', gutter_fg='#858585', gutter_fg_active='#c6c6c6',
                    gutter_border='#1e1e1e', current_line_bg='#282828', bracket_bg='#3a3d41')


def _theme(slug, name, dark, bg, sidebar, panel, fg, muted, accent, border,
           colors, *, selection, ansi=None, syntax_colors=None):
    # colors = comment, red, orange, yellow, green, cyan, blue, purple.
    comment, red, orange, yellow, green, cyan, blue, purple = colors
    foreground = _legible(fg, bg, 7)
    ui = dict(_DARK_UI)
    ui.update(bg=bg, sidebar_bg=sidebar, panel_bg=panel, border=border,
              titlebar_bg=panel, titlebar_fg=foreground, title_fg=muted,
              foreground=foreground, muted=_legible(muted, sidebar, 3),
              accent=accent, accent_fg='#ffffff' if _contrast('#ffffff', accent) >= 4.5 else '#111111',
              selection_active=selection, selection_inactive=border, hover=border,
              tab_inactive_bg=sidebar, editor_fg=foreground, editor_selection=selection,
              selection_fg=foreground, warning=_legible(orange, panel, 3), limit_tint=muted,
              toast_bg=panel, status_bg=accent, button_bg=panel, button_hover=border,
              input_bg=bg, scrollbar=border, scrollbar_hover=muted, scrollbar_active=accent,
              tab_close_hover=border, cursor=foreground, hover_fg=foreground)
    ui['status_fg'] = ui['accent_fg']
    _extend_ui(ui, dark)
    mapping = dict(comment=comment, doc=green, string=green, escape=orange, regex=cyan,
                   number=orange, keyword=purple, control=purple, operator=foreground,
                   constant=orange, builtin=cyan, function=yellow, **{'class': blue},
                   decorator=yellow, variable=foreground, property=red, tag=red,
                   attribute=orange, selector=green, heading=blue, strong=blue,
                   emphasis=foreground, link=cyan, preproc=purple, punctuation=muted, error=red)
    mapping.update(syntax_colors or {})
    syntax = {kind: _Style(_legible(mapping[kind], bg, 2.2 if kind == 'comment' else 3),
                          bold=kind in ('heading', 'strong'), italic=kind == 'emphasis',
                          underline=kind == 'link') for kind in KINDS}
    if ansi is None:
        ansi = (bg, red, green, yellow, blue, purple, cyan, foreground,
                muted, red, green, yellow, blue, purple, cyan, foreground)
    chrome = dict(gutter_bg=bg, gutter_fg=ui['muted'], gutter_fg_active=foreground,
                  gutter_border=bg, current_line_bg=panel, bracket_bg=selection)
    return Theme(slug, name, dark, ui, syntax,
                 TerminalColors(foreground, bg, foreground, tuple(ansi)), chrome)



def _idgent_theme(dark):
    """Measured UI neutrals with original, subdued syntax and ANSI palettes."""
    if dark:
        theme = _theme('idgent-dark', 'IDgent Dark', True,
                       '#1c1c1c', '#282828', '#232323', '#dadada', '#b3b3b3',
                       '#8a5cf5', '#333333',
                       ('#85827e', '#df898d', '#d4a477', '#c8ba84',
                        '#9dbb95', '#8cbdba', '#93afd3', '#b29adb'),
                       selection='#403152',
                       ansi=('#29272d', '#ce7b81', '#91ad89', '#beae79',
                             '#849fc4', '#a68acb', '#80adaa', '#c7c4cb',
                             '#77727f', '#e69b9f', '#b1caa9', '#d9cb9e',
                             '#a8c0e0', '#c5ace7', '#a1cfcb', '#eeebf2'))
        theme.ui.update(border_hover='#3f3f3f', border_focus='#555555', faint='#666666',
                        accent_text='#a68af9', accent_hover='#a68af9', danger='#fb464c',
                        input_bg='#2e2e2e', button_bg='#333333', button_hover='#3f3f3f',
                        warning='#e9973f', accent_fg='#000000')
    else:
        theme = _theme('idgent-light', 'IDgent Light', False,
                       '#ffffff', '#f6f6f6', '#fafafa', '#222222', '#5c5c5c',
                       '#9873f7', '#e4e4e4',
                       ('#77736d', '#a34e59', '#956534', '#81702d',
                        '#55734b', '#377b78', '#4b6e9b', '#7956aa'),
                       selection='#eae3fd',
                       ansi=('#302c36', '#a34855', '#4d7247', '#826b28',
                             '#486c9c', '#7852aa', '#347974', '#b9b5bf',
                             '#706a79', '#b55865', '#61845a', '#917a37',
                             '#5d7eae', '#8a64bb', '#488a85', '#f0edf5'))
        theme.ui.update(border_hover='#dadada', border_focus='#bdbdbd', faint='#ababab',
                        accent_text='#8a5cf5', accent_hover='#a68af9', danger='#e93147',
                        input_bg='#ffffff', button_bg='#ffffff', button_hover='#fafafa',
                        warning='#ec7500')
    ui = theme.ui
    # Keep all UI roles opaque for legacy components. Shared base rules use
    # alpha() so overlays also work on surfaces other than the primary one.
    ui.update(titlebar_bg=ui['sidebar_bg'], titlebar_fg=ui['foreground'], title_fg=ui['muted'],
              status_bg=ui['sidebar_bg'], status_fg=ui['muted'],
              selection_active=_mix_color(ui['sidebar_bg'], '#8a5cf5', .15),
              selection_inactive=_mix_color(ui['sidebar_bg'], ui['overlay'], .067),
              hover=_mix_color(ui['sidebar_bg'], ui['overlay'], .067),
              tab_close_hover=_mix_color(ui['bg'], ui['overlay'], .067),
              editor_selection=_mix_color(ui['bg'], ui['accent'], .33 if dark else .2),
              close_hover=ui['danger'], toast_bg=ui['surface_alt'])
    return theme


THEMES: tuple[Theme, ...] = (
    Theme('dark-plus', 'Dark+', True, _DARK_UI, _DARK_PLUS,
          TerminalColors('#d4d4d4', '#1e1e1e', '#ffffff', _DARK_ANSI), _DARK_CHROME),
    _theme('light-plus', 'Light+', False, '#ffffff', '#f3f3f3', '#eeeeee', '#333333', '#616161',
           '#007acc', '#d4d4d4',
           ('#008000', '#a31515', '#b06000', '#795e26', '#a31515', '#267f99', '#0000ff', '#af00db'),
           selection='#add6ff',
           syntax_colors=dict(keyword='#0000ff', control='#af00db', number='#098658'),
           ansi=('#000000', '#cd3131', '#008000', '#795e26', '#0451a5',
           '#bc05bc', '#0598bc', '#555555', '#666666', '#cd3131', '#008000', '#795e26',
           '#0451a5', '#bc05bc', '#0598bc', '#333333')),
    _theme('monokai', 'Monokai', True, '#272822', '#22231e', '#30312a', '#f8f8f2', '#909084',
           '#a6e22e', '#49483e',
           ('#75715e', '#f92672', '#fd971f', '#e6db74', '#a6e22e', '#66d9ef', '#66d9ef', '#ae81ff'),
           selection='#49483e', syntax_colors=dict(keyword='#f92672', control='#f92672',
               function='#a6e22e', string='#e6db74', doc='#e6db74', number='#ae81ff')),
    _theme('dracula', 'Dracula', True, '#282a36', '#21222c', '#343746', '#f8f8f2', '#8b93b6',
           '#bd93f9', '#44475a',
           ('#6272a4', '#ff5555', '#ffb86c', '#f1fa8c', '#50fa7b', '#8be9fd', '#8be9fd', '#ff79c6'),
           selection='#44475a', syntax_colors=dict(function='#50fa7b', string='#f1fa8c',
               doc='#f1fa8c', number='#bd93f9', property='#f8f8f2')),
    _theme('solarized-dark', 'Solarized Dark', True, '#002b36', '#073642', '#073642', '#eee8d5', '#839496',
           '#268bd2', '#164956',
           ('#586e75', '#dc322f', '#cb4b16', '#b58900', '#859900', '#2aa198', '#268bd2', '#6c71c4'),
           selection='#164956', ansi=('#073642', '#dc322f', '#859900', '#b58900', '#268bd2',
           '#d33682', '#2aa198', '#eee8d5', '#586e75', '#cb4b16', '#93a1a1', '#839496',
           '#657b83', '#6c71c4', '#93a1a1', '#fdf6e3')),
    _theme('solarized-light', 'Solarized Light', False, '#fdf6e3', '#eee8d5', '#eee8d5', '#073642', '#657b83',
           '#268bd2', '#d5cdb6',
           ('#93a1a1', '#dc322f', '#cb4b16', '#b58900', '#859900', '#2aa198', '#268bd2', '#6c71c4'),
           selection='#d5e5dc', ansi=('#073642', '#dc322f', '#859900', '#b58900', '#268bd2',
           '#d33682', '#2aa198', '#657b83', '#586e75', '#cb4b16', '#657b83', '#839496',
           '#268bd2', '#6c71c4', '#2aa198', '#fdf6e3')),
    _theme('nord', 'Nord', True, '#2e3440', '#272d38', '#3b4252', '#eceff4', '#8793a8',
           '#88c0d0', '#434c5e',
           ('#616e88', '#bf616a', '#d08770', '#ebcb8b', '#a3be8c', '#8fbcbb', '#81a1c1', '#b48ead'),
           selection='#434c5e', syntax_colors=dict(keyword='#81a1c1', control='#81a1c1',
               function='#88c0d0', number='#b48ead', property='#d8dee9'),
           ansi=('#3b4252', '#bf616a', '#a3be8c', '#ebcb8b', '#81a1c1',
           '#b48ead', '#88c0d0', '#e5e9f0', '#4c566a', '#bf616a', '#a3be8c', '#ebcb8b',
           '#81a1c1', '#b48ead', '#8fbcbb', '#eceff4')),
    _theme('one-dark', 'One Dark', True, '#282c34', '#21252b', '#2c313a', '#c8ccd4', '#828997',
           '#61afef', '#3e4451',
           ('#5c6370', '#e06c75', '#d19a66', '#e5c07b', '#98c379', '#56b6c2', '#61afef', '#c678dd'),
           selection='#3e4451', syntax_colors={'class': '#e5c07b', 'function': '#61afef'}),
    _theme('github-dark', 'GitHub Dark', True, '#0d1117', '#010409', '#161b22', '#e6edf3', '#8b949e',
           '#2f81f7', '#30363d',
           ('#8b949e', '#ff7b72', '#ffa657', '#d2a8ff', '#a5d6ff', '#79c0ff', '#79c0ff', '#ff7b72'),
           selection='#264f78', syntax_colors=dict(function='#d2a8ff')),
    _theme('idgent-midnight', 'IDgent Midnight', True, '#081521', '#0b1c2b', '#102638', '#d5f1fa', '#7299ab',
           '#25d9e8', '#1b3c50',
           ('#587e91', '#ff7597', '#ffbb7b', '#f6df8e', '#7ce3b2', '#5fe4e9', '#78b8ff', '#c5a0ff'),
           selection='#16495e'),
    _theme('idgent-ember', 'IDgent Ember', True, '#241b18', '#2d211c', '#382820', '#f7e5cf', '#b09887',
           '#ef9b45', '#513a2d',
           ('#987c69', '#f47b67', '#ef9b45', '#efcc7d', '#becb83', '#8ac5bb', '#9bbbd4', '#d7a5bf'),
           selection='#63402c'),
    _theme('idgent-paper', 'IDgent Paper', False, '#f8f4ea', '#eee8da', '#e7e0cf', '#283e3b', '#6b766a',
           '#176b62', '#cfc9b8',
           ('#8a9380', '#aa413c', '#a55d28', '#856017', '#4e702e', '#23736b', '#396b93', '#875b88'),
           selection='#c5ded1'),
    _idgent_theme(True),
    _idgent_theme(False),
)

DEFAULT_THEME_ID = 'dark-plus'


def get_theme(theme_id: str | None) -> Theme:
    """Return the named theme, falling back to the default for unknown IDs."""
    default = next(theme for theme in THEMES if theme.id == DEFAULT_THEME_ID)
    return next((theme for theme in THEMES if theme.id == theme_id), default)


def theme_ids() -> tuple[str, ...]:
    return tuple(theme.id for theme in THEMES)
