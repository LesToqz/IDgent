"""Project notes: the notes folder, note names and Markdown insert commands.

Notes are plain Markdown files in ``<project root>/.IDgent/Notes/``. The folder
is created on the first note, never on start-up. A note name maps directly to
its file name (``coffi shop`` -> ``coffi shop.md``, spaces kept); names that
contain ``/`` or are empty are refused, and an existing file is never
overwritten (the new note gets a numbered suffix instead).

The insert commands are pure text edits (``apply_command``), so they can be
tested without a display; ``NoteToolbar`` and the window apply them to a
``Gtk.TextBuffer`` as one undoable user action. The existing Markdown preview
renders the result.
"""

import os
from dataclasses import dataclass
from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GObject, Gtk

from . import fsops

NOTES_DIR = os.path.join(".IDgent", "Notes")
NOTE_SUFFIX = ".md"
TABLE = ("| Column 1 | Column 2 | Column 3 |\n"
         "| --- | --- | --- |\n"
         "|  |  |  |\n")


class NoteNameError(fsops.FsOpError):
    pass


def notes_dir(root: str) -> str:
    return os.path.join(os.path.abspath(root), NOTES_DIR)


def note_filename(name: str) -> str:
    """Validate a note name and return its file name ('coffi shop' -> 'coffi shop.md')."""
    if not isinstance(name, str):
        raise NoteNameError("Enter a note name.")
    name = name.strip()
    if name.lower().endswith(NOTE_SUFFIX):
        name = name[:-len(NOTE_SUFFIX)].rstrip()
    if not name:
        raise NoteNameError("Enter a note name.")
    if "/" in name or "\0" in name or name in (".", ".."):
        raise NoteNameError("Note names cannot contain '/'.")
    return name + NOTE_SUFFIX


def note_path(root: str, name: str) -> str:
    """The first free path for a note: '<name>.md', then '<name> 2.md', ..."""
    filename = note_filename(name)
    folder = notes_dir(root)
    stem = filename[:-len(NOTE_SUFFIX)]
    path, number = os.path.join(folder, filename), 2
    while os.path.lexists(path):
        path = os.path.join(folder, f"{stem} {number}{NOTE_SUFFIX}")
        number += 1
    return path


def create_note(root: str, name: str) -> str:
    """Create the notes folder if needed and a new note; return its path."""
    filename = note_filename(name)
    folder = notes_dir(root)
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as error:
        raise NoteNameError(f"Cannot create the notes folder: {error.strerror or error}") from error
    title = filename[:-len(NOTE_SUFFIX)]
    while True:
        path = note_path(root, title)
        try:
            # Exclusive creation: a file that appeared meanwhile is never replaced.
            with open(path, "x", encoding="utf-8") as handle:
                handle.write(f"# {title}\n\n")
            return path
        except FileExistsError:
            continue
        except OSError as error:
            raise NoteNameError(f"Cannot create {os.path.basename(path)}: "
                                f"{error.strerror or error}") from error


def list_notes(root: str) -> list[str]:
    """Paths of the Markdown files in the notes folder, sorted by name."""
    folder = notes_dir(root)
    try:
        with os.scandir(folder) as listing:
            paths = [entry.path for entry in listing
                     if entry.name.lower().endswith(NOTE_SUFFIX) and entry.is_file()]
    except OSError:
        return []
    return sorted(paths, key=lambda path: (os.path.basename(path)[:-len(NOTE_SUFFIX)].casefold(), path))


def is_note(root: str, path: str | None) -> bool:
    return bool(path) and path.lower().endswith(NOTE_SUFFIX) and (
        os.path.dirname(os.path.abspath(path)) == notes_dir(root))


# Insert commands ------------------------------------------------------------------


@dataclass(frozen=True)
class Edit:
    """Replace text[start:end] with ``text``; then select [select_start, select_end)
    (offsets in the edited document)."""
    start: int
    end: int
    text: str
    select_start: int
    select_end: int

    def apply(self, document: str) -> str:
        return document[:self.start] + self.text + document[self.end:]


def _line_bounds(document: str, start: int, end: int) -> tuple[int, int]:
    first = document.rfind("\n", 0, start) + 1
    if end > start and document[end - 1:end] == "\n":
        end -= 1
    last = document.find("\n", end)
    return first, len(document) if last < 0 else last


def _prefix_lines(document, start, end, prefix: Callable[[int, str], str]) -> Edit:
    first, last = _line_bounds(document, start, end)
    lines = document[first:last].split("\n")
    edited = [prefix(index, line) + line for index, line in enumerate(lines)]
    text = "\n".join(edited)
    if start == end:
        cursor = start + len(edited[0]) - len(lines[0])
        return Edit(first, last, text, cursor, cursor)
    return Edit(first, last, text, first, first + len(text))


def _heading(_index: int, line: str) -> str:
    level = len(line) - len(line.lstrip("#"))
    if 0 < level < 6 and line[level:level + 1] in (" ", ""):
        return "#"
    return "" if level else "# "


def _wrap(document, start, end, before, after, placeholder) -> Edit:
    selected = document[start:end] or placeholder
    text = before + selected + after
    return Edit(start, end, text, start + len(before), start + len(before) + len(selected))


def _block(document, start, end, body: str, select: tuple[int, int]) -> Edit:
    """Insert ``body`` (ending in a newline) as its own block, separated from
    surrounding paragraphs by blank lines so it never merges into them."""
    if start == 0 or document[start - 1:start] == "\n" and (
            start < 2 or document[start - 2] == "\n"):
        before = ""
    elif document[start - 1] == "\n":
        before = "\n"
    else:
        before = "\n\n"
    after = "\n" if end < len(document) and document[end] != "\n" else ""
    text = before + body + after
    offset = start + len(before)
    return Edit(start, end, text, offset + select[0], offset + select[1])


def _code(document, start, end) -> Edit:
    selected = document[start:end].rstrip("\n")
    body = "```\n" + selected + "\n```\n"
    return _block(document, start, end, body, (4, 4 + len(selected)))


def _insert_block(body: str, select: tuple[int, int]):
    """A block inserted after any selection, which it keeps."""
    return lambda document, start, end: _block(document, end, end, body, select)


def _link(document, start, end) -> Edit:
    selected = document[start:end]
    if selected:
        text = f"[{selected}](url)"
        url = start + len(selected) + 3
        return Edit(start, end, text, url, url + 3)
    return _wrap(document, start, end, "[", "](url)", "link text")


@dataclass(frozen=True)
class NoteCommand:
    id: str
    label: str
    icons: tuple[str, ...]
    glyph: str
    edit: Callable[[str, int, int], Edit]


NOTE_COMMANDS = (
    NoteCommand("heading", "Heading", (), "H",
                lambda d, s, e: _prefix_lines(d, s, e, _heading)),
    NoteCommand("bold", "Bold", ("format-text-bold-symbolic",), "B",
                lambda d, s, e: _wrap(d, s, e, "**", "**", "bold text")),
    NoteCommand("italic", "Italic", ("format-text-italic-symbolic",), "I",
                lambda d, s, e: _wrap(d, s, e, "*", "*", "italic text")),
    NoteCommand("bullet", "Bullet list", ("view-list-bullet-symbolic",), "•",
                lambda d, s, e: _prefix_lines(d, s, e, lambda i, line: "- ")),
    NoteCommand("numbered", "Numbered list", ("view-list-ordered-symbolic",), "1.",
                lambda d, s, e: _prefix_lines(d, s, e, lambda i, line: f"{i + 1}. ")),
    NoteCommand("checklist", "Checklist", ("checkbox-checked-symbolic",), "☐",
                lambda d, s, e: _prefix_lines(d, s, e, lambda i, line: "- [ ] ")),
    NoteCommand("table", "Table", ("x-office-spreadsheet-symbolic", "view-grid-symbolic"), "▦",
                _insert_block(TABLE, (2, 10))),
    NoteCommand("code", "Code block", (), "</>", _code),
    NoteCommand("link", "Link", ("insert-link-symbolic",), "Link", _link),
    NoteCommand("rule", "Horizontal rule", (), "―",
                _insert_block("---\n", (4, 4))),
)
COMMANDS_BY_ID = {command.id: command for command in NOTE_COMMANDS}


def apply_command(command_id: str, document: str, start: int, end: int | None = None) -> Edit:
    """The edit a note command makes for a selection [start, end) of ``document``."""
    end = start if end is None else end
    start, end = sorted((max(0, min(start, len(document))), max(0, min(end, len(document)))))
    return COMMANDS_BY_ID[command_id].edit(document, start, end)


def apply_to_buffer(command_id: str, buffer: Gtk.TextBuffer) -> Edit:
    """Apply a command to a buffer as one undoable user action and select its result."""
    bounds = buffer.get_selection_bounds()
    if bounds:
        start, end = (item.get_offset() for item in bounds)
    else:
        start = end = buffer.get_iter_at_mark(buffer.get_insert()).get_offset()
    document = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
    edit = apply_command(command_id, document, start, end)
    buffer.begin_user_action()
    try:
        first, last = buffer.get_iter_at_offset(edit.start), buffer.get_iter_at_offset(edit.end)
        buffer.delete(first, last)
        buffer.insert(buffer.get_iter_at_offset(edit.start), edit.text)
        buffer.select_range(buffer.get_iter_at_offset(edit.select_end),
                            buffer.get_iter_at_offset(edit.select_start))
    finally:
        buffer.end_user_action()
    return edit


class NoteToolbar(Gtk.Box):
    """Flat insert buttons shown above a note's editor."""

    __gsignals__ = {
        "command": (GObject.SignalFlags.RUN_LAST, None, (str,)),
    }

    def __init__(self) -> None:
        super().__init__(spacing=2)
        self.get_style_context().add_class("idgent-note-toolbar")
        self.set_valign(Gtk.Align.START)
        self.buttons: dict[str, Gtk.Button] = {}
        icon_theme = Gtk.IconTheme.get_default()
        for command in NOTE_COMMANDS:
            button = Gtk.Button()
            button.set_relief(Gtk.ReliefStyle.NONE)
            button.set_can_focus(False)
            icon = next((name for name in command.icons
                         if icon_theme is not None and icon_theme.has_icon(name)), None)
            if icon:
                button.add(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.MENU))
            else:
                label = Gtk.Label(label=command.glyph)
                label.get_style_context().add_class("idgent-note-glyph")
                button.add(label)
            button.set_tooltip_text(command.label)
            button.get_accessible().set_name(command.label)
            button.connect("clicked", lambda _, cid=command.id: self.emit("command", cid))
            self.buttons[command.id] = button
            self.pack_start(button, False, False, 0)


def build_css(ui: dict[str, str]) -> str:
    """Flat notes chrome: instant hovers and no transitions (they do not move)."""
    hover = f"alpha({ui['overlay']}, 0.067)"
    return f'''
.idgent-note-toolbar {{ background: {ui['bg']}; border-bottom: 1px solid {ui['border']};
    padding: 3px 8px; }}
.idgent-note-toolbar button, .idgent-notes-tab {{ background: transparent; border: 0;
    border-radius: 4px; box-shadow: none; color: {ui['muted']}; min-height: 16px; min-width: 16px;
    padding: 4px 6px; }}
.idgent-note-toolbar button:hover, .idgent-notes-tab:hover {{ background: {hover};
    color: {ui['foreground']}; }}
.idgent-notes-tab:checked {{ background: alpha({ui['overlay']}, 0.12); color: {ui['foreground']}; }}
.idgent-note-toolbar button image {{ opacity: 0.85; }}
.idgent-note-toolbar button:hover image {{ opacity: 1; }}
.idgent-note-glyph, .idgent-notes-tab label {{ font-size: 12px; font-weight: 600; }}
.idgent-notes-empty {{ color: {ui['muted']}; font-size: 12px; padding: 12px; }}
'''
