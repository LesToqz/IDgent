"""Theme catalogue integrity and independent WCAG contrast checks."""
import dataclasses
import os
import subprocess
import sys
import unittest

os.environ['GDK_BACKEND'] = 'x11'
from idgent import themes
from idgent.highlight import DARK_PLUS, KINDS
from idgent.gutter import DEFAULT_CHROME


def luminance(value):
    channels = [int(value[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    return sum(weight * (v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4)
               for weight, v in zip((.2126, .7152, .0722), channels))


def contrast(a, b):
    lo, hi = sorted((luminance(a), luminance(b)))
    return (hi + .05) / (lo + .05)


class ThemesTest(unittest.TestCase):
    def test_import_does_not_need_gtk(self):
        result = subprocess.run([sys.executable, '-c',
            "import sys; sys.modules['gi'] = None; import idgent.themes; "
            "assert 'idgent.highlight' not in sys.modules"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_catalogue_and_lookup(self):
        expected = ('dark-plus', 'light-plus', 'monokai', 'dracula', 'solarized-dark',
                    'solarized-light', 'nord', 'one-dark', 'github-dark',
                    'idgent-midnight', 'idgent-ember', 'idgent-paper')
        self.assertEqual(themes.theme_ids(), expected)
        self.assertEqual(len({t.name for t in themes.THEMES}), len(expected))
        self.assertEqual(len({(t.ui['bg'], t.ui['accent']) for t in themes.THEMES}), len(expected))
        for t in themes.THEMES:
            self.assertIs(themes.get_theme(t.id), t)
            self.assertRegex(t.id, r'^[a-z][a-z0-9-]+$')
        for unknown in (None, '', 'missing', [], {}):
            self.assertIs(themes.get_theme(unknown), themes.THEMES[0])
        with self.assertRaises(dataclasses.FrozenInstanceError):
            themes.THEMES[0].name = 'changed'

    def test_complete_and_valid_colors(self):
        roles = set(themes.THEMES[0].ui)
        self.assertTrue({'bg', 'editor_fg', 'accent', 'status_bg', 'input_bg'} <= roles)
        for t in themes.THEMES:
            with self.subTest(theme=t.id):
                self.assertEqual(set(t.ui), roles)
                self.assertEqual(set(t.syntax), set(KINDS))
                self.assertEqual(set(t.chrome), set(DEFAULT_CHROME))
                self.assertEqual(len(t.terminal.ansi), 16)
                for c in (*t.ui.values(), *t.chrome.values(), *t.terminal.ansi,
                          t.terminal.foreground, t.terminal.background, t.terminal.cursor,
                          *(s.foreground for s in t.syntax.values())):
                    self.assertRegex(c, r'^#[0-9a-fA-F]{6}$')
                for c in t.terminal.ansi:
                    self.assertRegex(c, r'^#[0-9a-f]{6}$')
                self.assertEqual(t.dark, luminance(t.ui['bg']) < .3)

    def test_contrast(self):
        for t in themes.THEMES:
            u = t.ui
            pairs = [('editor_fg', 'bg', 7), ('foreground', 'bg', 4.5),
                     ('foreground', 'sidebar_bg', 4.5), ('muted', 'sidebar_bg', 3),
                     ('accent_fg', 'accent', 4.5)]
            for a, b, minimum in pairs:
                with self.subTest(theme=t.id, role=a, background=b):
                    self.assertGreaterEqual(contrast(u[a], u[b]), minimum)
            for kind, value in t.syntax.items():
                with self.subTest(theme=t.id, kind=kind):
                    self.assertGreaterEqual(contrast(value.foreground, u['bg']),
                                            2.2 if kind == 'comment' else 3)
            self.assertGreaterEqual(contrast(t.terminal.foreground, t.terminal.background), 7)

    def test_terminal_palette_rejects_invalid_ansi(self):
        for palette in (('#ffffff',) * 15, ('#ffffff',) * 17,
                        ('#GGGGGG',) * 16, ('#fff',) * 16):
            with self.assertRaises(ValueError):
                themes.TerminalColors('#ffffff', '#000000', '#ffffff', palette)

    def test_dark_plus_matches_original(self):
        t = themes.get_theme('dark-plus')
        self.assertEqual(t.syntax, DARK_PLUS)
        self.assertEqual(t.chrome, DEFAULT_CHROME)
        self.assertEqual(t.ui, dict(
            bg='#1e1e1e', sidebar_bg='#252526', panel_bg='#1e1e1e', border='#2b2b2b',
            titlebar_bg='#333333', titlebar_fg='#cccccc', title_fg='#9d9d9d',
            foreground='#cccccc', muted='#858585', accent='#007acc', accent_fg='#ffffff',
            selection_active='#094771', selection_inactive='#37373d', hover='#2a2d2e',
            tab_inactive_bg='#2d2d2d', editor_fg='#d4d4d4', editor_selection='#264f78',
            selection_fg='#ffffff', warning='#cca700', limit_tint='#7d6f2a', toast_bg='#3a331a',
            status_bg='#007acc', status_fg='#ffffff', button_bg='#333333', button_hover='#505050',
            input_bg='#333333', scrollbar='#424242', scrollbar_hover='#4f4f4f',
            scrollbar_active='#686868', tab_close_hover='#383838', cursor='#ffffff',
            close_hover='#c42b1c', close_fg='#ffffff', hover_fg='#ffffff'))
