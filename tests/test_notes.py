"""Project notes: folder, names, listing and the exact Markdown of each insert command."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

# Keep GTK on the Xvfb display even when WAYLAND_DISPLAY is inherited.
os.environ["GDK_BACKEND"] = "x11"

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from idgent import fsops, notes
from idgent.notes import NOTE_COMMANDS, TABLE, NoteNameError, apply_command
from idgent.themes import THEMES


def run(command_id, document, start, end=None):
    edit = apply_command(command_id, document, start, end)
    text = edit.apply(document)
    return text, text[edit.select_start:edit.select_end], (edit.select_start, edit.select_end)


class NoteFilesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / ".IDgent" / "Notes"

    def test_notes_folder_is_a_hidden_project_folder(self):
        self.assertEqual(notes.notes_dir(str(self.root)), str(self.folder))
        self.assertTrue(notes.NOTES_DIR.startswith("."))

    def test_name_maps_to_markdown_file_name_with_spaces_kept(self):
        self.assertEqual(notes.note_filename("coffi shop"), "coffi shop.md")
        self.assertEqual(notes.note_filename("  coffi shop  "), "coffi shop.md")
        self.assertEqual(notes.note_filename("coffi shop.md"), "coffi shop.md")
        self.assertEqual(notes.note_filename("Plan 2026"), "Plan 2026.md")

    def test_invalid_names_are_refused(self):
        for name in ("", "   ", ".md", "a/b", "/", "../x", ".", "..", "a\0b", None):
            with self.subTest(name=name), self.assertRaises(NoteNameError):
                notes.note_filename(name)
        # The explorer's name dialog keeps itself open on FsOpError.
        self.assertTrue(issubclass(NoteNameError, fsops.FsOpError))

    def test_folder_created_lazily_on_first_note(self):
        self.assertEqual(notes.list_notes(str(self.root)), [])
        self.assertFalse(self.folder.exists())
        with self.assertRaises(NoteNameError):
            notes.create_note(str(self.root), "a/b")
        self.assertFalse((self.root / ".IDgent").exists())
        path = notes.create_note(str(self.root), "coffi shop")
        self.assertEqual(path, str(self.folder / "coffi shop.md"))
        self.assertTrue(self.folder.is_dir())
        self.assertEqual(Path(path).read_text(encoding="utf-8"), "# coffi shop\n\n")

    def test_existing_notes_are_never_overwritten(self):
        self.folder.mkdir(parents=True)
        (self.folder / "coffi shop.md").write_text("keep")
        (self.folder / "coffi shop 2.md").write_text("keep too")
        path = notes.create_note(str(self.root), "coffi shop")
        self.assertEqual(path, str(self.folder / "coffi shop 3.md"))
        self.assertEqual((self.folder / "coffi shop.md").read_text(), "keep")
        self.assertEqual((self.folder / "coffi shop 2.md").read_text(), "keep too")

    def test_creation_race_takes_the_next_free_name(self):
        real_open = open

        def racing_open(path, mode="r", *args, **kwargs):
            if mode == "x" and path.endswith("race.md"):
                with real_open(path, "w") as other:
                    other.write("other writer")
            return real_open(path, mode, *args, **kwargs)

        with patch("builtins.open", racing_open):
            path = notes.create_note(str(self.root), "race")
        self.assertEqual(path, str(self.folder / "race 2.md"))
        self.assertEqual((self.folder / "race.md").read_text(), "other writer")

    def test_unwritable_project_reports_a_note_error(self):
        with patch("idgent.notes.os.makedirs", side_effect=PermissionError(13, "Permission denied")):
            with self.assertRaises(NoteNameError) as raised:
                notes.create_note(str(self.root), "x")
        self.assertIn("Permission denied", str(raised.exception))

    def test_list_shows_markdown_files_only_sorted_by_name(self):
        self.folder.mkdir(parents=True)
        for name in ("b.md", "A.MD", "coffi shop 2.md", "coffi shop.md", "skip.txt", ".md.swp"):
            (self.folder / name).write_text("")
        (self.folder / "folder.md").mkdir()
        self.assertEqual([os.path.basename(path) for path in notes.list_notes(str(self.root))],
                         ["A.MD", "b.md", "coffi shop.md", "coffi shop 2.md"])

    def test_is_note(self):
        root = str(self.root)
        self.assertTrue(notes.is_note(root, str(self.folder / "x.md")))
        for path in (None, "", str(self.folder / "x.txt"), str(self.root / "x.md"),
                     str(self.folder / "sub" / "x.md")):
            with self.subTest(path=path):
                self.assertFalse(notes.is_note(root, path))


class InsertCommandTest(unittest.TestCase):
    def test_every_requested_command_exists_in_order(self):
        self.assertEqual([command.label for command in NOTE_COMMANDS],
                         ["Heading", "Bold", "Italic", "Bullet list", "Numbered list", "Checklist",
                          "Table", "Code block", "Link", "Horizontal rule"])

    def test_exact_markdown_in_an_empty_note(self):
        expected = {
            "heading": ("# ", ""),
            "bold": ("**bold text**", "bold text"),
            "italic": ("*italic text*", "italic text"),
            "bullet": ("- ", ""),
            "numbered": ("1. ", ""),
            "checklist": ("- [ ] ", ""),
            "table": ("| Column 1 | Column 2 | Column 3 |\n"
                      "| --- | --- | --- |\n"
                      "|  |  |  |\n", "Column 1"),
            "code": ("```\n\n```\n", ""),
            "link": ("[link text](url)", "link text"),
            "rule": ("---\n", ""),
        }
        for command_id, (text, selected) in expected.items():
            with self.subTest(command=command_id):
                self.assertEqual(run(command_id, "", 0)[:2], (text, selected))

    def test_table_has_three_columns_header_and_separator_rows(self):
        header, separator, body = TABLE.splitlines()
        self.assertEqual(header.count("|"), 4)
        self.assertEqual(separator, "| --- | --- | --- |")
        self.assertEqual(body.count("|"), 4)

    def test_inline_commands_wrap_the_selection(self):
        self.assertEqual(run("bold", "hello world", 6, 11)[:2], ("hello **world**", "world"))
        self.assertEqual(run("italic", "hello world", 6, 11)[:2], ("hello *world*", "world"))
        # A linked selection keeps its text and selects the URL placeholder.
        self.assertEqual(run("link", "see docs", 4, 8)[:2], ("see [docs](url)", "url"))
        # Reversed bounds are the same selection.
        self.assertEqual(run("bold", "hello world", 11, 6)[0], "hello **world**")

    def test_line_commands_prefix_every_selected_line(self):
        self.assertEqual(run("bullet", "a\nb\nc", 0, 3)[0], "- a\n- b\nc")
        self.assertEqual(run("numbered", "a\nb\nc", 0, 5)[0], "1. a\n2. b\n3. c")
        self.assertEqual(run("checklist", "milk\neggs\n", 0, 10)[0], "- [ ] milk\n- [ ] eggs\n")
        self.assertEqual(run("heading", "intro", 2)[0], "# intro")

    def test_line_commands_keep_the_cursor_on_its_text(self):
        text, _selected, cursor = run("checklist", "x\nmilk", 6)
        self.assertEqual(text, "x\n- [ ] milk")
        self.assertEqual(cursor, (len(text), len(text)))

    def test_list_commands_replace_an_existing_marker_and_keep_indent(self):
        self.assertEqual(run("checklist", "- milk", 6)[0], "- [ ] milk")
        self.assertEqual(run("numbered", "- a\n- [ ] b", 0, 11)[0], "1. a\n2. b")
        self.assertEqual(run("bullet", "  3. x", 6)[0], "  - x")

    def test_heading_deepens_up_to_six_levels(self):
        self.assertEqual(run("heading", "## Title", 3)[0], "### Title")
        self.assertEqual(run("heading", "###### Title", 3)[0], "###### Title")

    def test_blocks_stand_apart_from_paragraphs(self):
        table = run("table", "para", 4)
        self.assertEqual(table[0], "para\n\n" + TABLE)
        self.assertEqual(table[1], "Column 1")
        self.assertEqual(run("rule", "para", 4)[0], "para\n\n---\n")
        self.assertEqual(run("rule", "para\n", 5)[0], "para\n\n---\n")
        self.assertEqual(run("rule", "para\n\n", 6)[0], "para\n\n---\n")
        # In the middle of a line the rest of it follows the block.
        self.assertEqual(run("rule", "ab", 1)[0], "a\n\n---\n\nb")
        # Tables and rules go after a selection and keep it.
        self.assertEqual(run("table", "a\nb\n", 0, 4)[0], "a\nb\n\n" + TABLE)

    def test_code_block_wraps_selection_on_its_own_lines(self):
        self.assertEqual(run("code", "print(1)\n", 0, 9)[:2], ("```\nprint(1)\n```\n", "print(1)"))
        self.assertEqual(run("code", "run x now", 4, 5)[:2], ("run \n\n```\nx\n```\n\n now", "x"))


class BufferAndToolbarTest(unittest.TestCase):
    def test_apply_to_buffer_is_one_user_action_and_selects_result(self):
        buffer = Gtk.TextBuffer()
        buffer.set_text("hello world")
        buffer.select_range(buffer.get_iter_at_offset(6), buffer.get_iter_at_offset(11))
        actions = []
        buffer.connect("begin-user-action", lambda *_: actions.append("begin"))
        buffer.connect("end-user-action", lambda *_: actions.append("end"))
        notes.apply_to_buffer("bold", buffer)
        self.assertEqual(buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True),
                         "hello **world**")
        self.assertEqual(actions, ["begin", "end"])
        start, end = buffer.get_selection_bounds()
        self.assertEqual((start.get_offset(), end.get_offset()), (8, 13))

    def test_toolbar_has_a_button_per_command_and_emits_its_id(self):
        toolbar = notes.NoteToolbar()
        self.addCleanup(toolbar.destroy)
        self.assertEqual(list(toolbar.buttons), [command.id for command in NOTE_COMMANDS])
        self.assertEqual([button.get_tooltip_text() for button in toolbar.buttons.values()],
                         [command.label for command in NOTE_COMMANDS])
        self.assertTrue(all(not button.get_can_focus() for button in toolbar.buttons.values()))
        emitted = []
        toolbar.connect("command", lambda _, command_id: emitted.append(command_id))
        for button in toolbar.buttons.values():
            button.clicked()
        self.assertEqual(emitted, [command.id for command in NOTE_COMMANDS])

    def test_toolbar_icon_fallback_glyphs(self):
        with patch.object(Gtk.IconTheme, "get_default") as theme:
            theme.return_value.has_icon.return_value = False
            toolbar = notes.NoteToolbar()
        self.addCleanup(toolbar.destroy)
        self.assertEqual([button.get_child().get_text() for button in toolbar.buttons.values()],
                         [command.glyph for command in NOTE_COMMANDS])

    def test_css_parses_without_transitions_for_every_theme(self):
        for theme in THEMES:
            with self.subTest(theme=theme.id):
                css = notes.build_css(theme.ui)
                provider = Gtk.CssProvider()
                errors = []
                provider.connect("parsing-error", lambda *args: errors.append(args[-1]))
                provider.load_from_data(css.encode())
                self.assertEqual(errors, [])
                self.assertNotIn("transition", css)


if __name__ == "__main__":
    unittest.main()
