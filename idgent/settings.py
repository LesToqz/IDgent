"""Small, failure-tolerant JSON settings store, resolved at call time.

Graph defaults, bounds and steps follow docs/reference_app-graph-params.json, section
settings (spec section J). IDgent deviations: showCode defaults to True for source
files; reduceMotion defaults to False for the host's accessibility override;
localDepth/localIncoming/localOutgoing retain the brief's host names alongside
Reference app's localJumps/localBacklinks/localForelinks, with identical defaults and
bounds. These are independent settings, not synchronized aliases. Color groups
use #rrggbb strings instead of Reference app's {a: 1, rgb: int}, and are capped at 12
as required by the host bridge. Unknown keys are dropped. Numeric steps describe
the controls; persisted numbers are clamped, with integer controls rounded.
"""
from __future__ import annotations

import json
import math
import os
import re
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


def settings_path() -> Path:
    """Where settings are stored (resolved from XDG_CONFIG_HOME at call time)."""
    return _path()


# Settings window keys (spec 5.3). The theme and font sizes keep their own
# helpers above; get_value/set_value accept every key in DEFAULTS.
ACCENT_PRESETS = ('#8a5cf5', '#3b82f6', '#0ea5a4', '#22a55b',
                  '#d4a017', '#e8762d', '#e5484d', '#d6409f')
CHOICES = {
    'delete_mode': ('trash', 'permanent'),
    'terminal_cursor': ('block', 'ibeam', 'underline'),
}
LIMITS = {
    'indent_width': (2, 8),
    'terminal_scrollback': (1000, 100000),
}
NEW_DEFAULTS = {
    'accent': None,  # None follows the theme's own accent.
    'reduce_motion': False,
    'ui_font': '',  # Empty means the GTK/system interface font.
    'editor_font': '',  # Empty means the default monospace font.
    'line_numbers': True,
    'highlight_line': True,
    'md_default_preview': False,
    'md_readable_width': True,
    'indent_tabs': False,
    'indent_width': 4,
    'auto_pair': True,
    'show_hidden': True,
    'confirm_delete': True,
    'delete_mode': 'permanent',  # Current explorer behaviour: no trash.
    'terminal_cursor': 'block',
    'terminal_blink': True,
    'terminal_scrollback': 10000,
    'terminal_autostart': True,
}
NEW_KEYS = tuple(NEW_DEFAULTS)
DEFAULTS = {'theme': get_theme(None).id, **DEFAULT_FONT_SIZES, **NEW_DEFAULTS}
LIMITS.update(FONT_SIZE_LIMITS)
_FONT_NAME_MAX = 200


def normalize(key: str, value: object) -> object:
    """Validate one value: clamp numbers, otherwise invalid values use the default."""
    if key == 'graph':
        return normalize_graph(value)
    if key == 'theme':
        return get_theme(value if isinstance(value, str) else None).id
    if key in FONT_KEYS:
        return _font_size(key, value)
    if key not in NEW_DEFAULTS:
        raise ValueError(f'Unknown setting key: {key}')
    default = NEW_DEFAULTS[key]
    if key == 'accent':
        if (isinstance(value, str) and len(value) == 7 and value[0] == '#'
                and all(c in '0123456789abcdefABCDEF' for c in value[1:])):
            return value.lower()
        return None
    if key in CHOICES:
        return value if value in CHOICES[key] else default
    if isinstance(default, bool):
        return value if isinstance(value, bool) else default
    if key in LIMITS:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return default
        if isinstance(value, float) and not math.isfinite(value):
            return default
        low, high = LIMITS[key]
        return round(max(low, min(high, value)))
    # Font family names: printable text only, trimmed; '' means default.
    if not isinstance(value, str) or len(value) > _FONT_NAME_MAX or not value.isprintable():
        return default
    return value.strip()


def get_value(key: str) -> object:
    return normalize(key, load().get(key))


def get_values() -> dict:
    """Every known key, validated, from one read of the file."""
    data = load()
    return {key: normalize(key, data.get(key)) for key in DEFAULTS}


def set_value(key: str, value: object) -> bool:
    """Save one validated value; unknown keys raise ValueError."""
    return save({key: normalize(key, value)})


def set_values(values: dict) -> bool:
    """Save several validated values in one atomic write."""
    return save({key: normalize(key, value) for key, value in values.items()})


def reset_value(key: str) -> bool:
    return set_value(key, DEFAULTS[key])


# Reference app's initial graph preferences; user-tuned graph.json values are not defaults.
GRAPH_DEFAULTS = {
    'search': '', 'showTags': False, 'showAttachments': False,
    'hideUnresolved': False, 'showOrphans': True, 'showCode': True,
    'showArrow': False, 'textFadeMultiplier': 0, 'nodeSizeMultiplier': 1,
    'lineSizeMultiplier': 1, 'centerStrength': 0.518713248970312,
    'repelStrength': 10, 'linkStrength': 1, 'linkDistance': 250,
    'colorGroups': [], 'collapse-filter': True, 'collapse-display': True,
    'collapse-forces': True, 'collapse-color-groups': True, 'scale': 1,
    'localDepth': 1, 'localIncoming': True, 'localOutgoing': True,
    'localJumps': 1, 'localBacklinks': True, 'localForelinks': True,
    'localInterlinks': False, 'close': False, 'reduceMotion': False,
}
GRAPH_LIMITS = {
    'textFadeMultiplier': (-3, 3), 'nodeSizeMultiplier': (0.1, 5),
    'lineSizeMultiplier': (0.1, 5), 'centerStrength': (0, 1),
    'repelStrength': (0, 20), 'linkStrength': (0, 1),
    'linkDistance': (30, 500), 'scale': (1 / 128, 8),
    'localDepth': (1, 5), 'localJumps': (1, 5),
}
# None means no slider step is specified (persisted zoom).
GRAPH_STEPS = {
    'textFadeMultiplier': 0.1, 'nodeSizeMultiplier': 'any',
    'lineSizeMultiplier': 'any', 'centerStrength': 'any',
    'repelStrength': 'any', 'linkStrength': 'any', 'linkDistance': 1,
    'scale': None, 'localDepth': 1, 'localJumps': 1,
}
DEFAULTS['graph'] = GRAPH_DEFAULTS


def normalize_graph(value: object) -> dict:
    """Return independent, JSON-safe graph preferences, dropping unknown keys."""
    value = value if isinstance(value, dict) else {}
    result = {}
    for key, default in GRAPH_DEFAULTS.items():
        raw = value.get(key, default)
        if isinstance(default, bool):
            result[key] = raw if isinstance(raw, bool) else default
        elif key in GRAPH_LIMITS:
            if (isinstance(raw, bool) or not isinstance(raw, (int, float))
                    or isinstance(raw, float) and not math.isfinite(raw)):
                raw = default
            low, high = GRAPH_LIMITS[key]
            result[key] = max(low, min(high, raw))
            if GRAPH_STEPS[key] == 1:
                result[key] = round(result[key])
        elif key == 'colorGroups':
            groups = []
            for group in raw if isinstance(raw, list) else []:
                if (isinstance(group, dict) and isinstance(group.get('query'), str)
                        and isinstance(group.get('color'), str)
                        and re.fullmatch(r'#[0-9a-fA-F]{6}', group['color'])):
                    groups.append({'query': group['query'], 'color': group['color'].lower()})
                if len(groups) == 12:
                    break
            result[key] = groups
        else:
            result[key] = raw if isinstance(raw, str) else default
    return result


def get_graph() -> dict:
    return normalize_graph(load().get('graph'))


def set_graph(value: dict) -> bool:
    return save({'graph': normalize_graph(value)})
