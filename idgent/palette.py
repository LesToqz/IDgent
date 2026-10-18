"""Fuzzy matcher, prompt widget, quick switcher and command palette (spec 4.14, 6.2).

The matcher half of this module (``match``, ``rank``, ``FuzzyIndex``,
``highlight_markup``, ``split_path_ranges``, ``prompt_geometry``) is pure
Python and never touches GTK, so it can be unit-tested without a display.

Matching (case-insensitive, per character, so ranges index the original text):

* The query is split on whitespace. First every token must occur as a
  substring, in order (occurrences on a word boundary are preferred).
* Otherwise the tokens are joined and matched character by character
  (a subsequence), keeping the more compact or more boundary-aligned of two
  candidate alignments.
* With ``path=True`` the file name (after the last ``/`` or ``\\``) is tried
  as well as the full path; a match inside the file name earns a bonus, so
  file-name matches outrank directory matches.

The score is higher-is-better: bonuses for boundary starts (separators,
camelCase, letter/digit changes), consecutive characters, file-name and
prefix matches, exact names and exact case; penalties for extra fragments,
the gap between the first and last match, a late first match and long
candidates. Ties keep source order. Ranges are half-open ``(start, end)``.

GTK half: ``Prompt`` (a self-contained Reference app-like prompt box, placed by
``idgent.overlay.OverlayHost``), ``QuickSwitcher`` and ``CommandPalette``.
Component CSS comes from ``build_css(ui)``.
"""
from __future__ import annotations

import heapq
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, GLib, GObject, Gtk, Pango


MAX_RESULTS = 100
PROMPT_WIDTH = 700
PROMPT_WIDTH_FRACTION = .8
PROMPT_HEIGHT_FRACTION = .7
# Transparent room around the visible box so its large shadow is not clipped
# by the overlay child window: (top, right, bottom, left) pixels.
SHADOW_MARGINS = (24, 48, 72, 48)
DEFAULT_INSTRUCTIONS = (('↑↓', 'to navigate'), ('↵', 'to use'), ('esc', 'to dismiss'))

# Score weights (see the module docstring).
_BOUNDARY = 8.0
_CONSECUTIVE = 4.0
_FRAGMENT = 4.0
_GAP = .5
_LATE = .2
_LENGTH = .03
_NAME = 10.0
_PREFIX = 3.0
_TOKEN = 2.0
_EXACT_NAME = 15.0
_CASE = .1
_CAP = 40

Range = tuple[int, int]


# Matcher ---------------------------------------------------------------------

def fold(text: str) -> str:
    """Lower-case per character so that indexes keep meaning in ``text``."""
    low = text.lower()
    if len(low) == len(text):
        return low
    return ''.join(lower if len(lower := char.lower()) == 1 else char for char in text)


def _is_boundary(text: str, index: int) -> bool:
    if index <= 0:
        return True
    previous, current = text[index - 1], text[index]
    if not previous.isalnum() or not current.isalnum():
        return True
    if current.isupper() and previous.islower():
        return True
    return current.isdigit() != previous.isdigit()


def name_start(text: str) -> int:
    """Index of the file name in a path (0 when there is no directory)."""
    return max(text.rfind('/'), text.rfind('\\')) + 1


def _merge(indexes: Sequence[int]) -> list[Range]:
    ranges = []
    for index in indexes:
        if ranges and ranges[-1][1] == index:
            ranges[-1] = (ranges[-1][0], index + 1)
        else:
            ranges.append((index, index + 1))
    return ranges


def _tokens(folded: str, text: str, tokens: Sequence[str], start: int, boundary: bool):
    ranges, position = [], start
    for token in tokens:
        index = folded.find(token, position)
        if index < 0:
            return None
        if boundary:
            candidate = index
            while candidate >= 0 and not _is_boundary(text, candidate):
                candidate = folded.find(token, candidate + 1)
            if candidate >= 0:
                index = candidate
        ranges.append((index, index + len(token)))
        position = index + len(token)
    return ranges


def _token_ranges(folded, text, tokens, start):
    return (_tokens(folded, text, tokens, start, True)
            or _tokens(folded, text, tokens, start, False))


def _compact(folded: str, chars: str, start: int):
    """Leftmost subsequence end, then tightened backwards (shortest window)."""
    position = start
    for char in chars:
        position = folded.find(char, position)
        if position < 0:
            return None
        position += 1
    indexes, position = [], position - 1
    for char in reversed(chars):
        position = folded.rfind(char, start, position + 1)
        indexes.append(position)
        position -= 1
    indexes.reverse()
    return indexes


def _aligned(folded: str, text: str, chars: str, start: int):
    """Greedy alignment preferring consecutive characters, then boundaries."""
    indexes, position, size = [], start, len(folded)
    for char in chars:
        if indexes and position < size and folded[position] == char:
            index = position
        else:
            index = folded.find(char, position)
            if index < 0:
                return None
            candidate = index
            while candidate >= 0 and not _is_boundary(text, candidate):
                candidate = folded.find(char, candidate + 1)
            if candidate >= 0:
                index = candidate
        indexes.append(index)
        position = index + 1
    return indexes


def _score(text: str, ranges: Sequence[Range], query: str, start: int, base: float) -> float:
    score = base
    first, last = ranges[0][0], ranges[-1][1]
    matched = 0
    for begin, end in ranges:
        if _is_boundary(text, begin) or begin == start:
            score += _BOUNDARY
        score += _CONSECUTIVE * (end - begin - 1)
        matched += end - begin
    score -= _FRAGMENT * (len(ranges) - 1)
    score -= _GAP * min(last - first - matched, _CAP)
    score -= _LATE * min(first - start, _CAP)
    score -= _LENGTH * min(len(text), 4 * _CAP)
    if first == start:
        score += _PREFIX
    if query.islower() or not any(char.isupper() for char in query):
        return score
    # Smart case: an upper-case query rewards exact-case characters a little.
    position = 0
    for begin, end in ranges:
        for index in range(begin, end):
            if position < len(query) and text[index] == query[position]:
                score += _CASE
            position += 1
    return score


def _best(text, folded, tokens, chars, query, starts, path):
    best, name = None, starts[0]

    def bonus(ranges):
        return _NAME if path and ranges[0][0] >= name else 0.0

    for start in starts:
        ranges = _token_ranges(folded, text, tokens, start)
        if ranges is None:
            continue
        score = _score(text, ranges, query, start, _TOKEN + bonus(ranges))
        if best is None or score > best[0]:
            best = (score, ranges)
    if best is not None:
        return best
    for start in starts:
        for indexes in (_compact(folded, chars, start), _aligned(folded, text, chars, start)):
            if indexes is None:
                continue
            ranges = _merge(indexes)
            score = _score(text, ranges, query, start, bonus(ranges))
            if best is None or score > best[0]:
                best = (score, ranges)
        if best is not None and path:
            return best
    return best


def _prepare(query: str):
    tokens = fold(query).split()
    original = ''.join(query.split())
    return tokens, ''.join(tokens), original


def _match_prepared(text, folded, start, tokens, chars, original, path):
    if not tokens:
        return 0.0, ()
    # Cheap early exit: the joined query must be a subsequence of the text.
    position = 0
    for char in chars:
        position = folded.find(char, position)
        if position < 0:
            return None
        position += 1
    starts = (start, 0) if path and start else (0,)
    found = _best(text, folded, tokens, chars, original, starts, path)
    if found is None:
        return None
    score, ranges = found
    if path:
        name = folded[start:]
        stem = name.rsplit('.', 1)[0] if '.' in name[1:] else name
        if chars in (name, stem):
            score += _EXACT_NAME
    return score, tuple(ranges)


def match(query: str, text: str, *, path: bool = False):
    """Return ``(score, ranges)`` when every query character matches, else None.

    An empty/blank query matches everything with score 0 and no ranges.
    """
    tokens, chars, original = _prepare(query)
    start = name_start(text) if path else 0
    return _match_prepared(text, fold(text), start, tokens, chars, original, path)


def rank(query: str, candidates: Iterable[Any], *, key: Callable[[Any], str] | None = None,
         path: bool = False, limit: int | None = MAX_RESULTS):
    """Best matches first as ``(candidate, score, ranges)``; ties keep source order."""
    return FuzzyIndex(candidates, key=key, path=path).search(query, limit)


class FuzzyIndex:
    """Pre-folded candidates with incremental narrowing while a query grows.

    When a query extends the previous one, only the previous matches are
    rescanned (a longer query can only match a subset).
    """

    def __init__(self, candidates: Iterable[Any], *, key: Callable[[Any], str] | None = None,
                 path: bool = False):
        self.candidates = list(candidates)
        self.path = path
        self._texts = [key(item) if key else item for item in self.candidates]
        self._folded = [fold(text) for text in self._texts]
        self._starts = [name_start(text) if path else 0 for text in self._texts]
        self._last_query = None
        self._last_hits = None

    def __len__(self):
        return len(self.candidates)

    def search(self, query: str, limit: int | None = MAX_RESULTS):
        tokens, chars, original = _prepare(query)
        if not tokens:
            self._last_query = self._last_hits = None
            items = self.candidates if limit is None else self.candidates[:limit]
            return [(item, 0.0, ()) for item in items]
        if (self._last_query is not None and query.startswith(self._last_query)
                and _prepare(self._last_query)[0]):
            pool = self._last_hits
        else:
            pool = range(len(self.candidates))
        texts, folded, starts, path = self._texts, self._folded, self._starts, self.path
        hits, scored = [], []
        for index in pool:
            found = _match_prepared(texts[index], folded[index], starts[index],
                                    tokens, chars, original, path)
            if found is not None:
                hits.append(index)
                scored.append((found[0], -index, found[1]))
        self._last_query, self._last_hits = query, hits
        if limit is None:
            best = sorted(scored, key=lambda entry: (entry[0], entry[1]), reverse=True)
        else:
            best = heapq.nlargest(limit, scored, key=lambda entry: (entry[0], entry[1]))
        return [(self.candidates[-index], score, ranges) for score, index, ranges in best]


def highlight_markup(text: str, ranges: Iterable[Range] = ()) -> str:
    """Pango markup with matched ranges in bold and everything escaped."""
    def escape(part):
        return part.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    parts, position = [], 0
    for begin, end in sorted(ranges):
        begin, end = max(begin, position), min(end, len(text))
        if begin >= end:
            continue
        parts.append(escape(text[position:begin]))
        parts.append('<b>' + escape(text[begin:end]) + '</b>')
        position = end
    parts.append(escape(text[position:]))
    return ''.join(parts)


def split_path_ranges(path: str, ranges: Iterable[Range]):
    """Split full-path ranges into (directory ranges, file-name ranges).

    Directory ranges index ``path[:start - 1]`` (no trailing separator),
    file-name ranges are relative to the file name.
    """
    start = name_start(path)
    folder_end = max(start - 1, 0)
    folder, name = [], []
    for begin, end in ranges:
        if begin < folder_end:
            folder.append((begin, min(end, folder_end)))
        if end > start:
            name.append((max(begin, start) - start, end - start))
    return folder, name


def prompt_geometry(width: int, height: int):
    """Visible prompt box (x, y, width, max_height) inside a host (spec 4.14)."""
    box = min(PROMPT_WIDTH, int(width * PROMPT_WIDTH_FRACTION))
    max_height = int(height * PROMPT_HEIGHT_FRACTION)
    return (width - box) // 2, round((height - height * PROMPT_HEIGHT_FRACTION) / 2), box, max_height


# GTK --------------------------------------------------------------------------

@dataclass
class PromptItem:
    """One result row; ranges are half-open indexes into label/detail."""
    label: str
    detail: str = ''
    hotkey: str | Sequence[str] = ()
    data: Any = None
    label_ranges: Sequence[Range] = ()
    detail_ranges: Sequence[Range] = ()
    muted: bool = False

    @property
    def hotkeys(self) -> tuple[str, ...]:
        if isinstance(self.hotkey, str):
            return (self.hotkey,) if self.hotkey else ()
        return tuple(key for key in self.hotkey if key)


@dataclass
class Command:
    """A palette entry; ``id`` defaults to the label (used for recent ordering)."""
    label: str
    callback: Callable[[], Any]
    hotkey: str | Sequence[str] = ()
    id: str = ''

    def __post_init__(self):
        self.id = self.id or self.label


_CHOOSE_MODIFIERS = (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK
                     | Gdk.ModifierType.MOD1_MASK)
_RETURN_KEYS = (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter)


class _ResultRow(Gtk.ListBoxRow):
    def __init__(self):
        super().__init__()
        self.set_can_focus(False)
        self.get_style_context().add_class('idgent-prompt-item')
        box = Gtk.Box(spacing=10)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.title.get_style_context().add_class('idgent-prompt-title')
        self.detail = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.START, no_show_all=True)
        self.detail.get_style_context().add_class('idgent-prompt-detail')
        text.pack_start(self.title, False, False, 0)
        text.pack_start(self.detail, False, False, 0)
        self.chips = Gtk.Box(spacing=10, valign=Gtk.Align.CENTER, no_show_all=True)
        box.pack_start(text, True, True, 0)
        box.pack_end(self.chips, False, False, 0)
        self.add(box)
        self.item = None
        box.show_all()

    def update(self, item: PromptItem):
        self.item = item
        self.title.set_markup(highlight_markup(item.label, item.label_ranges))
        context = self.get_style_context()
        (context.add_class if item.muted else context.remove_class)('idgent-prompt-muted')
        if item.detail:
            self.detail.set_markup(highlight_markup(item.detail, item.detail_ranges))
            self.detail.show()
        else:
            self.detail.hide()
        keys = item.hotkeys
        chips = self.chips.get_children()
        for index, key in enumerate(keys):
            if index < len(chips):
                chip = chips[index]
            else:
                chip = Gtk.Label()
                chip.get_style_context().add_class('idgent-prompt-hotkey')
                self.chips.pack_start(chip, False, False, 0)
            chip.set_text(key)
            chip.show()
        for chip in chips[len(keys):]:
            chip.hide()
        self.chips.set_visible(bool(keys))


class Prompt(Gtk.EventBox):
    """Reference app-style prompt: input row, result list and instructions footer.

    ``provider(query) -> list[PromptItem]`` is called on every edit (coalesced
    into one idle per main-loop turn, so the typed character paints first)
    and at most ``MAX_RESULTS`` items are shown. Keys: Up/Down (wrap),
    PageUp/PageDown, Home/End (first/last result), Enter chooses,
    Ctrl/Shift/Alt+Enter choose with those modifiers, Esc closes, Tab stays.
    Signals: ``chosen(item, modifiers)`` after the prompt closed itself,
    ``query-chosen(query, modifiers)`` for Enter with no results, ``closed()``
    once per open. Place it with ``OverlayHost.show_prompt`` (or call
    ``prepare()`` yourself). ``instructions=()`` shows the default footer,
    ``None`` hides it. The widget's outer area is transparent shadow room;
    clicks there close the prompt.
    """
    __gsignals__ = {
        'chosen': (GObject.SignalFlags.RUN_LAST, None, (object, int)),
        'query-chosen': (GObject.SignalFlags.RUN_LAST, None, (str, int)),
        'closed': (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(self, provider: Callable[[str], Sequence[PromptItem]], placeholder: str = '',
                 instructions: Sequence[tuple[str, str]] | None = (), *,
                 empty_text: str = 'No results found.'):
        super().__init__(visible_window=False)
        self._provider = provider
        self._items: list[PromptItem] = []
        self._rows: list[_ResultRow] = []
        self._selected = -1
        self._pending = 0
        self._open = False
        self._pointer = None
        self.get_style_context().add_class('idgent-prompt-host')

        self.frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.frame.get_style_context().add_class('idgent-prompt')
        top, right, bottom, left = SHADOW_MARGINS
        self.frame.set_margin_top(top)
        self.frame.set_margin_end(right)
        self.frame.set_margin_bottom(bottom)
        self.frame.set_margin_start(left)
        self.add(self.frame)

        self.entry = Gtk.Entry(placeholder_text=placeholder, hexpand=True)
        self.entry.get_style_context().add_class('idgent-prompt-input')
        self.entry.connect('changed', self._on_changed)
        self.entry.connect('key-press-event', self._on_key)
        self.entry.connect('icon-press', lambda entry, *_: entry.set_text(''))
        self.frame.pack_start(self.entry, False, False, 0)

        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                           propagate_natural_height=True)
        self.scroller.get_style_context().add_class('idgent-prompt-scroller')
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE,
                                activate_on_single_click=True, can_focus=False)
        self.list.get_style_context().add_class('idgent-prompt-results')
        for side in ('top', 'bottom', 'start', 'end'):
            getattr(self.list, f'set_margin_{side}')(12)
        self.list.connect('row-activated', self._on_row_activated)
        self.list.add_events(Gdk.EventMask.POINTER_MOTION_MASK)
        self.list.connect('motion-notify-event', self._on_motion)
        self.scroller.add(self.list)
        self.frame.pack_start(self.scroller, True, True, 0)

        self.empty = Gtk.Label(label=empty_text, xalign=.5, no_show_all=True)
        self.empty.get_style_context().add_class('idgent-prompt-empty')
        self.frame.pack_start(self.empty, False, False, 0)

        self.footer = Gtk.Box(no_show_all=True)
        self.footer.get_style_context().add_class('idgent-prompt-instructions')
        self._instructions = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER, hexpand=True)
        self.footer.add(self._instructions)
        self._instructions.show()
        self.frame.pack_end(self.footer, False, False, 0)
        self.set_instructions(DEFAULT_INSTRUCTIONS if instructions == () else instructions)
        self.connect('button-press-event', self._on_button_press)
        self.connect('destroy', self._on_destroy)
        self.frame.show_all()

    # Public API --------------------------------------------------------------

    @property
    def query(self) -> str:
        return self.entry.get_text()

    @property
    def items(self) -> list[PromptItem]:
        return list(self._items)

    @property
    def selected_index(self) -> int:
        return self._selected

    @property
    def selected_item(self) -> PromptItem | None:
        return self._items[self._selected] if 0 <= self._selected < len(self._items) else None

    @property
    def is_open(self) -> bool:
        return self._open

    def set_provider(self, provider: Callable[[str], Sequence[PromptItem]]):
        self._provider = provider
        self.refresh()

    def set_placeholder(self, text: str):
        self.entry.set_placeholder_text(text)

    def set_empty_text(self, text: str):
        self.empty.set_text(text)

    def set_instructions(self, instructions: Sequence[tuple[str, str]] | None):
        for child in self._instructions.get_children():
            child.destroy()
        for key, text in instructions or ():
            label = Gtk.Label()
            label.set_markup(f'<b>{highlight_markup(key)}</b> {highlight_markup(text)}')
            label.show()
            self._instructions.pack_start(label, False, False, 0)
        self.footer.set_visible(bool(instructions))

    def set_query(self, text: str):
        """Replace the input text and filter synchronously."""
        self.entry.set_text(text)
        self.entry.set_position(-1)
        self.refresh()

    def prepare(self):
        """Reset for a fresh open: empty input, reloaded results, first row selected."""
        self._open = True
        self._load()
        if self.entry.get_text():
            self.entry.set_text('')
        self.refresh()

    def focus_input(self):
        self.entry.grab_focus_without_selecting()

    def refresh(self):
        """Run the provider for the current query now and rebuild the rows."""
        self._cancel_pending()
        items = list(self._provider(self.entry.get_text()))[:MAX_RESULTS]
        self._items = items
        while len(self._rows) < len(items):
            row = _ResultRow()
            self._rows.append(row)
            self.list.add(row)
        for row, item in zip(self._rows, items):
            row.update(item)
            row.show()
        for row in self._rows[len(items):]:
            row.hide()
        self.scroller.set_visible(bool(items))
        self.empty.set_visible(not items)
        self._selected = -1
        self.select(0 if items else -1)
        self.scroller.get_vadjustment().set_value(0)

    def select(self, index: int):
        """Select a result (clamped); the highlight moves instantly."""
        if not self._items:
            self._selected = -1
            self.list.unselect_all()
            return
        index = max(0, min(index, len(self._items) - 1))
        self._selected = index
        row = self._rows[index]
        self.list.select_row(row)
        self._scroll_to(row)

    def move(self, delta: int, *, wrap: bool = True):
        count = len(self._items)
        if not count:
            return
        if wrap:
            self.select((self._selected + delta) % count)
        else:
            self.select(self._selected + delta)

    def choose(self, modifiers: int = 0):
        """Choose the selected item (or the query when nothing matched)."""
        self._flush()
        modifiers = int(modifiers) & int(_CHOOSE_MODIFIERS)
        item = self.selected_item
        query = self.entry.get_text()
        if item is None and not query.strip():
            return
        self.close()
        if item is not None:
            self.emit('chosen', item, modifiers)
        else:
            self.emit('query-chosen', query, modifiers)

    def close(self):
        """Close once: emits ``closed`` (the host then removes the prompt)."""
        self._cancel_pending()
        if not self._open:
            return
        self._open = False
        self.emit('closed')

    # Subclass hooks -----------------------------------------------------------

    def _load(self):
        """Reload provider data before an open (subclasses)."""

    # Internals ------------------------------------------------------------------

    def _page(self) -> int:
        adjustment = self.scroller.get_vadjustment()
        row = self._rows[max(self._selected, 0)] if self._rows else None
        height = row.get_allocated_height() if row is not None else 0
        if height <= 1 or adjustment.get_page_size() <= 0:
            return 10
        return max(1, int(adjustment.get_page_size() // height) - 1)

    def _scroll_to(self, row):
        allocation = row.get_allocation()
        if allocation.height <= 1:
            return
        adjustment = self.scroller.get_vadjustment()
        margin = self.list.get_margin_top()
        top = allocation.y + margin
        bottom = top + allocation.height
        value, page = adjustment.get_value(), adjustment.get_page_size()
        if row is self._rows[0]:
            adjustment.set_value(0)
        elif top < value:
            adjustment.set_value(top)
        elif bottom > value + page:
            adjustment.set_value(bottom - page)

    def _on_changed(self, _entry):
        self.entry.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY,
                                           'edit-clear-symbolic' if self.entry.get_text() else None)
        if not self._pending:
            self._pending = GLib.idle_add(self._run_pending)

    def _run_pending(self):
        self._pending = 0
        self.refresh()
        return GLib.SOURCE_REMOVE

    def _cancel_pending(self):
        if self._pending:
            GLib.source_remove(self._pending)
            self._pending = 0

    def _flush(self):
        if self._pending:
            self.refresh()

    def _on_key(self, _entry, event):
        key = event.keyval
        state = event.state & Gtk.accelerator_get_default_mod_mask()
        if key == Gdk.KEY_Escape:
            self.close()
            return True
        if key in _RETURN_KEYS:
            self.choose(state)
            return True
        if key in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab):
            return True
        if state & ~Gdk.ModifierType.SHIFT_MASK:
            return False
        moves = {
            Gdk.KEY_Up: lambda: self.move(-1), Gdk.KEY_KP_Up: lambda: self.move(-1),
            Gdk.KEY_Down: lambda: self.move(1), Gdk.KEY_KP_Down: lambda: self.move(1),
            Gdk.KEY_Page_Up: lambda: self.move(-self._page(), wrap=False),
            Gdk.KEY_Page_Down: lambda: self.move(self._page(), wrap=False),
            Gdk.KEY_Home: lambda: self.select(0),
            Gdk.KEY_End: lambda: self.select(len(self._items) - 1),
        }
        action = moves.get(key)
        if action is None:
            return False
        self._flush()
        action()
        return True

    def _on_row_activated(self, _list, row):
        if row in self._rows:
            self.select(self._rows.index(row))
            ok, state = Gtk.get_current_event_state()
            self.choose(state if ok else 0)

    def _on_motion(self, _list, event):
        # Only real pointer movement selects (not rows scrolling under it).
        pointer = (round(event.x_root), round(event.y_root))
        if pointer == self._pointer:
            return False
        self._pointer = pointer
        _x, y = _local(self.list, event)
        row = self.list.get_row_at_y(int(y))
        if row is not None and row in self._rows:
            index = self._rows.index(row)
            if index != self._selected and index < len(self._items):
                self._selected = index
                self.list.select_row(row)
        return False

    def _on_button_press(self, _widget, event):
        x, y = _local(self.frame, event)
        allocation = self.frame.get_allocation()
        if not (0 <= x < allocation.width and 0 <= y < allocation.height):
            self.close()
            return True
        return False

    def _on_destroy(self, _widget):
        self._cancel_pending()


def _local(widget, event):
    """Event position relative to ``widget``'s allocation (via root coordinates)."""
    window = widget.get_window()
    _ok, origin_x, origin_y = window.get_origin()
    allocation = widget.get_allocation()
    if widget.get_has_window():
        return event.x_root - origin_x, event.y_root - origin_y
    return event.x_root - origin_x - allocation.x, event.y_root - origin_y - allocation.y


class CommandPalette(Prompt):
    """Prompt over ``commands_provider() -> list[Command]``.

    Empty query: recently used commands first (most recent first), then the
    provider order. Choosing records the command as recent and calls its
    callback after the palette closed (focus is back where it was).
    """
    __gsignals__ = {'command-run': (GObject.SignalFlags.RUN_LAST, None, (object,))}

    def __init__(self, commands_provider: Callable[[], Sequence[Command]],
                 placeholder: str = 'Select a command...', instructions=(), *,
                 recent: Sequence[str] = (), recent_limit: int = 20):
        self._commands_provider = commands_provider
        self._index = FuzzyIndex((), key=lambda command: command.label)
        self.recent = list(recent)
        self.recent_limit = recent_limit
        super().__init__(self._provide, placeholder, instructions,
                         empty_text='No commands found.')
        self.connect('chosen', self._on_chosen)

    def _load(self):
        self._index = FuzzyIndex(self._commands_provider(), key=lambda command: command.label)

    def _provide(self, query):
        if not query.strip():
            commands = self._index.candidates
            order = {name: position for position, name in enumerate(self.recent)}
            commands = sorted(commands, key=lambda command: order.get(command.id, len(order)))
            return [PromptItem(command.label, hotkey=command.hotkey, data=command)
                    for command in commands[:MAX_RESULTS]]
        return [PromptItem(command.label, hotkey=command.hotkey, data=command, label_ranges=ranges)
                for command, _score, ranges in self._index.search(query)]

    def _on_chosen(self, _prompt, item, _modifiers):
        command = item.data
        if command.id in self.recent:
            self.recent.remove(command.id)
        self.recent.insert(0, command.id)
        del self.recent[self.recent_limit:]
        command.callback()
        self.emit('command-run', command)


class QuickSwitcher(Prompt):
    """Prompt over ``files_provider() -> iterable of paths``.

    Rows show the file name with its folder below. ``root`` makes displayed
    and matched paths relative to it; ``recent_provider() -> paths`` (e.g.
    open/recent files) are listed first for an empty query, otherwise files
    are alphabetical. ``chosen`` carries the original path in ``item.data``.
    Enter with no match emits ``query-chosen`` (e.g. create a file).
    """
    INSTRUCTIONS = (('↑↓', 'to navigate'), ('↵', 'to open'), ('ctrl ↵', 'to open in new tab'),
                    ('esc', 'to dismiss'))

    def __init__(self, files_provider: Callable[[], Iterable[str]],
                 placeholder: str = 'Find or open a file...', instructions=None, *,
                 root: str = '', recent_provider: Callable[[], Iterable[str]] | None = None):
        self._files_provider = files_provider
        self._recent_provider = recent_provider
        self.root = root
        self._index = FuzzyIndex((), path=True)
        self._paths: dict[str, str] = {}
        self._sorted: list[str] = []
        super().__init__(self._provide, placeholder,
                         self.INSTRUCTIONS if instructions is None else instructions,
                         empty_text='No files found.')

    def display_path(self, path: str) -> str:
        if self.root:
            root = os.path.abspath(self.root)
            absolute = os.path.abspath(path)
            if absolute == root or absolute.startswith(root.rstrip(os.sep) + os.sep):
                return os.path.relpath(absolute, root)
        return path

    def _load(self):
        self._paths = {}
        for path in self._files_provider():
            self._paths.setdefault(self.display_path(path), path)
        shown = list(self._paths)
        self._index = FuzzyIndex(shown, path=True)
        self._sorted = sorted(shown, key=lambda text: (text.casefold(), text))

    def _item(self, shown, ranges=()):
        folder_ranges, name_ranges = split_path_ranges(shown, ranges)
        start = name_start(shown)
        return PromptItem(shown[start:], shown[:max(start - 1, 0)], data=self._paths[shown],
                          label_ranges=name_ranges, detail_ranges=folder_ranges)

    def _provide(self, query):
        if not query.strip():
            ordered, seen = [], set()
            for path in self._recent_provider() if self._recent_provider else ():
                shown = self.display_path(path)
                if shown in self._paths and shown not in seen:
                    seen.add(shown)
                    ordered.append(shown)
                    if len(ordered) >= MAX_RESULTS:
                        break
            for shown in self._sorted:
                if len(ordered) >= MAX_RESULTS:
                    break
                if shown not in seen:
                    ordered.append(shown)
            return [self._item(shown) for shown in ordered]
        return [self._item(shown, ranges) for shown, _score, ranges in self._index.search(query)]


def build_css(ui: dict[str, str]) -> str:
    """Prompt/switcher/palette rules; hover and selection are instant."""
    # --shadow-l, with the widest layer shortened to fit SHADOW_MARGINS.
    if ui['overlay'] == '#ffffff':
        shadow = ('0 1.8px 7.3px alpha(#000000, 0.071), 0 6.3px 24.7px alpha(#000000, 0.112), '
                  '0 16px 40px alpha(#000000, 0.2)')
    else:
        shadow = ('0 1.8px 7.3px alpha(#000000, 0.028), 0 6.3px 24.7px alpha(#000000, 0.042), '
                  '0 15px 30px alpha(#000000, 0.1)')
    return f"""
.idgent-prompt-host {{ background: transparent; }}
.idgent-prompt {{ background: {ui['bg']}; color: {ui['foreground']};
    border: 1px solid {ui['border_hover']}; border-radius: 12px; box-shadow: {shadow}; }}
.idgent-prompt entry.idgent-prompt-input {{ background: transparent; color: {ui['foreground']};
    border: 0; border-bottom: 1px solid {ui['sidebar_bg']}; border-radius: 12px 12px 0 0;
    box-shadow: none; outline: none; font-size: 15px; min-height: 25px; padding: 12px 24px; }}
.idgent-prompt entry.idgent-prompt-input:focus {{ border-color: {ui['sidebar_bg']};
    box-shadow: none; outline: none; }}
.idgent-prompt entry.idgent-prompt-input image {{ color: {ui['muted']}; }}
.idgent-prompt scrolledwindow, .idgent-prompt viewport,
.idgent-prompt list.idgent-prompt-results {{ background: transparent; border: 0; }}
.idgent-prompt list.idgent-prompt-results row.idgent-prompt-item {{ padding: 5px 12px;
    border-radius: 4px; background: transparent; color: {ui['foreground']};
    font-size: 15px; outline: none; transition: none; }}
.idgent-prompt list.idgent-prompt-results row.idgent-prompt-item:hover {{ background: transparent; }}
.idgent-prompt list.idgent-prompt-results row.idgent-prompt-item:selected,
.idgent-prompt list.idgent-prompt-results row.idgent-prompt-item:selected:hover {{
    background: alpha({ui['overlay']}, 0.067); color: {ui['foreground']}; }}
.idgent-prompt row.idgent-prompt-muted label.idgent-prompt-title {{ color: {ui['muted']}; }}
.idgent-prompt label.idgent-prompt-detail {{ color: {ui['muted']}; font-size: 12px; }}
.idgent-prompt label.idgent-prompt-hotkey {{ background: alpha({ui['overlay']}, 0.067);
    color: {ui['muted']}; font-size: 12px; padding: 2px 6px; border-radius: 999px; }}
.idgent-prompt label.idgent-prompt-empty {{ color: {ui['muted']}; font-size: 15px;
    padding: 18px 12px; }}
.idgent-prompt .idgent-prompt-instructions {{ color: {ui['muted']}; font-size: 12px;
    padding: 8px; border-top: 1px solid {ui['sidebar_bg']}; }}
.idgent-prompt .idgent-prompt-instructions label {{ color: {ui['muted']}; }}
"""
