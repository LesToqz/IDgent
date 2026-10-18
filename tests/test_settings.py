"""Settings IO, atomic replacement, and failure recovery."""
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from idgent import settings
from idgent.themes import DEFAULT_THEME_ID


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        env = patch.dict(os.environ, XDG_CONFIG_HOME=self.root.name)
        env.start()
        self.addCleanup(env.stop)
        self.path = Path(self.root.name) / 'idgent' / 'settings.json'

    def test_round_trip_private_and_unknown_keys(self):
        self.assertEqual(settings.load(), {})
        self.assertEqual(settings.get_theme_id(), DEFAULT_THEME_ID)
        self.assertTrue(settings.save({'unrelated': {'size': 12}, 'unicode': 'λ'}))
        self.assertTrue(settings.set_theme_id('nord'))
        self.assertEqual(settings.get_theme_id(), 'nord')
        self.assertEqual(settings.load()['unrelated'], {'size': 12})
        self.assertEqual(settings.load()['unicode'], 'λ')
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertTrue(settings.set_theme_id('unknown'))
        self.assertEqual(settings.get_theme_id(), DEFAULT_THEME_ID)
        self.assertEqual(settings.load()['unrelated'], {'size': 12})

    def test_invalid_files(self):
        self.path.parent.mkdir()
        for content in ('', '{broken', '[]', 'null', '42', '"text"'):
            self.path.write_text(content)
            self.assertEqual(settings.load(), {})
        self.path.write_bytes(b'\xff')
        self.assertEqual(settings.load(), {})
        self.path.unlink()
        self.path.mkdir()
        self.assertEqual(settings.load(), {})
        self.assertFalse(settings.save({'theme': 'nord'}))

    def test_default_lookup_does_not_create_or_rewrite_settings(self):
        self.assertEqual(settings.get_theme_id(), DEFAULT_THEME_ID)
        self.assertFalse(self.path.exists())
        self.path.parent.mkdir()
        payload = b'{ "theme": "dracula", "unrelated": 42 }\n'
        self.path.write_bytes(payload)
        timestamp = self.path.stat().st_mtime_ns
        self.assertEqual(settings.get_theme_id(), 'dracula')
        self.assertEqual(self.path.read_bytes(), payload)
        self.assertEqual(self.path.stat().st_mtime_ns, timestamp)

    def test_unreadable(self):
        with patch.object(Path, 'read_text', side_effect=PermissionError):
            self.assertEqual(settings.load(), {})

    def test_atomic_replace_and_cleanup_on_failure(self):
        self.assertTrue(settings.save({'original': True}))
        original = self.path.read_bytes()
        replace = os.replace

        def check(source, target):
            self.assertEqual(Path(target), self.path)
            self.assertEqual(Path(source).parent, self.path.parent)
            self.assertEqual(self.path.read_bytes(), original)
            self.assertEqual(json.loads(Path(source).read_text()), {'original': True, 'new': 7})
            self.assertEqual(stat.S_IMODE(Path(source).stat().st_mode), 0o600)
            replace(source, target)

        with patch.object(settings.os, 'replace', side_effect=check) as mocked:
            self.assertTrue(settings.save({'new': 7}))
            mocked.assert_called_once()
        original = self.path.read_bytes()
        with patch.object(settings.os, 'replace', side_effect=OSError('disk failure')):
            self.assertFalse(settings.save({'new': 8}))
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_read_only_directory(self):
        self.path.parent.mkdir()
        self.path.parent.chmod(0o500)
        self.addCleanup(self.path.parent.chmod, 0o700)
        # Root bypasses mode bits; inject the same EACCES in privileged CI.
        if os.geteuid() == 0:
            with patch.object(settings.tempfile, 'NamedTemporaryFile', side_effect=PermissionError):
                self.assertFalse(settings.save({'theme': 'nord'}))
        else:
            self.assertFalse(settings.save({'theme': 'nord'}))

    def test_call_time_path_and_home_fallback(self):
        with tempfile.TemporaryDirectory() as other:
            with patch.dict(os.environ, XDG_CONFIG_HOME=other):
                self.assertTrue(settings.set_theme_id('dracula'))
                self.assertTrue((Path(other) / 'idgent/settings.json').exists())
        with patch.dict(os.environ, XDG_CONFIG_HOME=''), patch.object(Path, 'home', return_value=Path(self.root.name)):
            self.assertTrue(settings.set_theme_id('nord'))
            self.assertTrue((Path(self.root.name) / '.config/idgent/settings.json').exists())

    def test_font_defaults_and_unknown_keys(self):
        self.assertEqual(settings.get_font_sizes(), dict(zip(settings.FONT_KEYS, (11, 11, 10))))
        for method, args in ((settings.get_font_size, ('other',)),
                             (settings.set_font_size, ('other', 12)),
                             (settings.reset_font_size, ('other',))):
            with self.assertRaises(ValueError):
                method(*args)

    def test_font_clamping_rounding_and_reset_preserve_keys(self):
        settings.save({'future': {'enabled': True}, 'theme': 'nord'})
        for key in settings.FONT_KEYS:
            low, high = settings.FONT_SIZE_LIMITS[key]
            for value, expected in ((-100, low), (100, high), (12.7, 13), (12.2, 12)):
                self.assertTrue(settings.set_font_size(key, value))
                self.assertEqual(settings.get_font_size(key), expected)
            self.assertTrue(settings.reset_font_size(key))
            self.assertEqual(settings.load()[key], settings.DEFAULT_FONT_SIZES[key])
        self.assertEqual(settings.load()['future'], {'enabled': True})
        self.assertEqual(settings.get_theme_id(), 'nord')

    def test_junk_font_values(self):
        for key in settings.FONT_KEYS:
            for value in (None, True, False, '14', 'junk', [], {}, float('nan'), float('inf'), -float('inf')):
                settings.save({key: value})
                self.assertEqual(settings.get_font_size(key), settings.DEFAULT_FONT_SIZES[key])
            for value, expected in ((-10, settings.FONT_SIZE_LIMITS[key][0]),
                                    (10 ** 1000, settings.FONT_SIZE_LIMITS[key][1]), (14.8, 15)):
                settings.save({key: value})
                self.assertEqual(settings.get_font_size(key), expected)

    def test_font_atomic_failure_preserves_old_file(self):
        settings.set_font_size('editor_font_size', 15)
        original = self.path.read_bytes()
        with patch.object(settings.os, 'replace', side_effect=OSError('disk failure')):
            self.assertFalse(settings.set_font_size('editor_font_size', 18))
            self.assertFalse(settings.reset_font_size('editor_font_size'))
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])


class SettingsWindowKeysTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        env = patch.dict(os.environ, XDG_CONFIG_HOME=self.root.name)
        env.start()
        self.addCleanup(env.stop)
        self.path = Path(self.root.name) / 'idgent' / 'settings.json'

    def test_defaults_cover_spec_keys(self):
        self.assertEqual(settings.NEW_KEYS, (
            'accent', 'reduce_motion', 'ui_font', 'editor_font', 'line_numbers', 'highlight_line',
            'md_default_preview', 'md_readable_width', 'indent_tabs', 'indent_width', 'auto_pair',
            'show_hidden', 'confirm_delete', 'delete_mode', 'terminal_cursor', 'terminal_blink',
            'terminal_scrollback', 'terminal_autostart'))
        values = settings.get_values()
        self.assertEqual(values, settings.DEFAULTS)
        self.assertIsNone(values['accent'])
        self.assertEqual(values['delete_mode'], 'permanent')
        self.assertEqual((values['indent_width'], values['terminal_scrollback']), (4, 10000))
        self.assertEqual(values['theme'], DEFAULT_THEME_ID)
        self.assertFalse(self.path.exists())
        self.assertEqual(settings.settings_path(), self.path)

    def test_round_trip_preserves_unknown_keys_and_privacy(self):
        self.assertTrue(settings.save({'future': [1, 2]}))
        changes = dict(accent='#3B82F6', reduce_motion=True, ui_font=' Inter ', editor_font='Fira Code',
                       line_numbers=False, indent_tabs=True, indent_width=2, delete_mode='trash',
                       terminal_cursor='ibeam', terminal_scrollback=50000, terminal_autostart=False)
        for key, value in changes.items():
            self.assertTrue(settings.set_value(key, value))
        values = settings.get_values()
        self.assertEqual(values['accent'], '#3b82f6')
        self.assertEqual(values['ui_font'], 'Inter')
        for key in ('reduce_motion', 'editor_font', 'line_numbers', 'indent_tabs', 'indent_width',
                    'delete_mode', 'terminal_cursor', 'terminal_scrollback', 'terminal_autostart'):
            self.assertEqual(values[key], changes[key], key)
        self.assertEqual(settings.load()['future'], [1, 2])
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertTrue(settings.set_values({'accent': None, 'indent_width': 6}))
        self.assertEqual((settings.get_value('accent'), settings.get_value('indent_width')), (None, 6))
        self.assertTrue(settings.reset_value('indent_width'))
        self.assertEqual(settings.get_value('indent_width'), 4)
        with self.assertRaises(ValueError):
            settings.set_value('nope', 1)

    def test_clamping_and_invalid_values_fall_back(self):
        cases = {
            'indent_width': ((1, 2), (99, 8), (5.6, 6), ('4', 4), (True, 4), (float('nan'), 4)),
            'terminal_scrollback': ((10, 1000), (10 ** 30, 100000), (None, 10000)),
            'accent': (('red', None), ('#12345', None), ('#gggggg', None), (7, None)),
            'line_numbers': ((0, True), ('false', True), (False, False)),
            'delete_mode': (('shred', 'permanent'), ('trash', 'trash')),
            'terminal_cursor': (('BLOCK', 'block'), ('underline', 'underline')),
            'ui_font': ((3, ''), ('x' * 500, ''), ('bad\nname', ''), ('Ubuntu Sans', 'Ubuntu Sans')),
            'theme': (('nope', DEFAULT_THEME_ID), (5, DEFAULT_THEME_ID), ('nord', 'nord')),
            'editor_font_size': ((100, 32), ('x', 11)),
        }
        for key, pairs in cases.items():
            for raw, expected in pairs:
                with self.subTest(key=key, raw=raw):
                    self.assertEqual(settings.normalize(key, raw), expected)
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({'indent_width': 'wide', 'show_hidden': 'yes',
                                         'terminal_scrollback': -5, 'keep': True}))
        values = settings.get_values()
        self.assertEqual((values['indent_width'], values['show_hidden'], values['terminal_scrollback']),
                         (4, True, 1000))
        self.assertTrue(settings.set_value('auto_pair', False))
        self.assertTrue(settings.load()['keep'])

    def test_corrupt_json_behaves_as_defaults(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{not json')
        self.assertEqual(settings.get_values(), settings.DEFAULTS)
        self.assertTrue(settings.set_value('terminal_blink', False))
        self.assertEqual(json.loads(self.path.read_text()), {'terminal_blink': False})
