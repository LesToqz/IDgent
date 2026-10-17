"""Small, failure-tolerant JSON settings store, resolved at call time."""
from __future__ import annotations

import json
import math
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


FONT_KEYS = ('terminal_font_size', 'editor_font_size', 'explorer_font_size')
# Measured under X11: gtk_font_name == "Sans 10" and a Gtk.Label's Pango
# context reports Sans 10, size / Pango.SCALE == 10. No GUI imports here.
DEFAULT_FONT_SIZES = dict(zip(FONT_KEYS, (11, 11, 10)))
FONT_SIZE_LIMITS = dict(zip(FONT_KEYS, ((7, 32), (7, 32), (8, 24))))


def _font_size(key: str, value: object) -> int:
    if key not in FONT_KEYS:
        raise ValueError(f'Unknown font size key: {key}')
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return DEFAULT_FONT_SIZES[key]
    # Avoid float conversion of arbitrarily large integers from JSON.
    if isinstance(value, float) and not math.isfinite(value):
        return DEFAULT_FONT_SIZES[key]
    low, high = FONT_SIZE_LIMITS[key]
    return round(max(low, min(high, value)))


def get_font_size(key: str) -> int:
    """Read integer points; invalid values default, numeric values clamp/round."""
    return _font_size(key, load().get(key))


def get_font_sizes() -> dict[str, int]:
    data = load()
    return {key: _font_size(key, data.get(key)) for key in FONT_KEYS}


def set_font_size(key: str, size: int | float) -> bool:
    """Save clamped, rounded points (invalid input uses the default)."""
    return save({key: _font_size(key, size)})


def reset_font_size(key: str) -> bool:
    """Reset by explicitly writing the default; preserve unrelated settings."""
    return set_font_size(key, _font_size(key, None))
