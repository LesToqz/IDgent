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
