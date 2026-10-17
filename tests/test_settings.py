"""Settings IO, atomic replacement, and failure recovery."""
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from idgent import settings


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
        self.assertEqual(settings.get_theme_id(), 'dark-plus')
        self.assertTrue(settings.save({'unrelated': {'size': 12}, 'unicode': 'λ'}))
        self.assertTrue(settings.set_theme_id('nord'))
        self.assertEqual(settings.get_theme_id(), 'nord')
        self.assertEqual(settings.load()['unrelated'], {'size': 12})
        self.assertEqual(settings.load()['unicode'], 'λ')
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertTrue(settings.set_theme_id('unknown'))
        self.assertEqual(settings.get_theme_id(), 'dark-plus')
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
