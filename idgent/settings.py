"""Small, failure-tolerant JSON settings store, resolved at call time."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from idgent.themes import get_theme


def _path() -> Path:
    root = os.environ.get('XDG_CONFIG_HOME') or str(Path.home() / '.config')
    return Path(root) / 'idgent' / 'settings.json'


def load() -> dict:
    """Read settings; missing, unreadable and malformed files behave as empty."""
    try:
        data = json.loads(_path().read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, UnicodeError):
        return {}


def save(data: dict) -> bool:
    """Merge keys and atomically replace settings with a private (0600) file.

    Unknown existing keys survive partial updates. On failure the old file stays
    intact; callers may continue using their in-memory preferences.
    """
    temporary = None
    try:
        target = _path()
        merged = load()
        merged.update(data)
        payload = json.dumps(merged, indent=2, ensure_ascii=False) + '\n'
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=target.parent,
                                         prefix='.settings-', delete=False) as stream:
            temporary = stream.name
            os.fchmod(stream.fileno(), 0o600)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        temporary = None
        return True
    except (OSError, TypeError, ValueError, UnicodeError):
        return False
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def get_theme_id() -> str:
    return get_theme(load().get('theme')).id


def set_theme_id(theme_id: str | None) -> bool:
    return save({'theme': get_theme(theme_id).id})
