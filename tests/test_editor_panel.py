"""File invariants and GTK editor interaction tests (run under xvfb)."""
import os
from pathlib import Path
import stat
import tempfile
import unittest
import warnings
from unittest.mock import patch

# Keep GTK on the Xvfb display even when WAYLAND_DISPLAY is inherited.
os.environ["GDK_BACKEND"] = "x11"

from idgent import editor
from gi.repository import Gdk, Gtk


class FileHelpersTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'file.py'

    def test_reject_binary_and_invalid_utf8(self):
        for content in (b'a\0b', b'\xff'):
            self.path.write_bytes(content)
            with self.assertRaisesRegex(ValueError, 'binary or non-UTF-8'):
                editor.read_file(str(self.path))

    def test_size_limit(self):
        with self.path.open('wb') as stream:
            stream.truncate(editor.MAX_FILE_SIZE + 1)
        with self.assertRaisesRegex(ValueError, '5 MB'):
            editor.read_file(str(self.path))

    def test_crlf_round_trip_and_mode(self):
        original = b'first\r\nsecond\r\n'
        self.path.write_bytes(original)
        self.path.chmod(0o751)
        text, ending, _ = editor.read_file(str(self.path))
        self.assertEqual(text, 'first\nsecond\n')
        self.assertEqual(ending, 'CRLF')
        editor.atomic_write(str(self.path), text, ending)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o751)

    def test_reject_nonregular_without_blocking(self):
        os.mkfifo(self.path)
        with self.assertRaisesRegex(ValueError, 'non-regular'):
            editor.read_file(str(self.path))

    def test_no_added_newline(self):
        editor.atomic_write(str(self.path), 'no newline', 'LF')
        self.assertEqual(self.path.read_bytes(), b'no newline')

    def test_atomic_failure_preserves_original_and_removes_temp(self):
        self.path.write_text('original')
        with patch.object(editor.os, 'replace', side_effect=OSError('denied')):
            with self.assertRaises(OSError):
                editor.atomic_write(str(self.path), 'replacement', 'LF')
        self.assertEqual(self.path.read_text(), 'original')
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_indent_helpers(self):
        self.assertEqual(editor.leading_whitespace(' \t code'), ' \t ')
        self.assertEqual(editor.unindent('  x'), 'x')
        self.assertEqual(editor.unindent('\tx'), 'x')
        original = '  a\n\n    b'
        self.assertEqual(editor.toggle_comments(editor.toggle_comments(original)), original)


class EditorPanelTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / 'file.py'
        self.path.write_text('first\nsecond')
        self.message = patch.object(editor, '_message').start()
        self.ask = patch.object(editor, '_ask', return_value='cancel').start()
        self.addCleanup(patch.stopall)
        self.window = Gtk.Window()
        self.panel = editor.EditorPanel()
        self.window.add(self.panel)
        self.window.show_all()
        self.addCleanup(self.window.destroy)

    def open(self, path=None):
        self.panel.open_file(str(path or self.path))
        return self.panel._current()

    def replace(self, tab, text):
        tab.buffer.begin_user_action()
        tab.buffer.set_text(text)
        tab.buffer.end_user_action()

    def key(self, tab, key, ctrl=False, shift=False):
        event = Gdk.EventKey()
        event.keyval = key
        event.state = ((Gdk.ModifierType.CONTROL_MASK if ctrl else 0) |
                       (Gdk.ModifierType.SHIFT_MASK if shift else 0))
        return self.panel._key_press(tab.view, event, tab)

    def test_empty_open_switch_duplicate(self):
        self.assertEqual(self.panel.stack.get_visible_child_name(), 'empty')
        first = self.open()
        other = self.root / 'other.py'
        other.write_text('other')
        self.open(other)
        self.assertEqual(len(self.panel.tabs), 2)
        self.assertIs(self.open(), first)
        self.assertEqual(len(self.panel.tabs), 2)
        self.assertEqual(first.header.get_tooltip_text(), str(self.path))
        self.assertTrue(self.panel.notebook.get_scrollable())

    def test_invalid_missing_directory_binary_size(self):
        self.open(self.root / 'missing')
        self.open(self.root)
        self.path.write_bytes(b'\0')
        self.open()
        self.path.write_bytes(b'\xff')
        self.open()
        with self.path.open('wb') as stream:
            stream.truncate(editor.MAX_FILE_SIZE + 1)
        self.open()
        self.assertEqual(len(self.panel.tabs), 0)
        self.assertEqual(self.message.call_count, 5)

    def test_status_follows_tab_switch(self):
        first = self.open()
        first.buffer.place_cursor(first.buffer.get_end_iter())
        other = self.root / 'other.py'
        other.write_bytes(b'one\r\ntwo')
        self.open(other)
        self.panel.notebook.set_current_page(0)
        self.assertEqual(self.panel.status.get_text(), 'Ln 2, Col 7  |  UTF-8  |  LF  |  Python')
        self.panel.notebook.set_current_page(1)
        self.assertIn('CRLF', self.panel.status.get_text())

    def test_grouped_undo_and_redo_branch(self):
        tab = self.open()
        tab.buffer.begin_user_action()
        tab.buffer.insert_at_cursor('one')
        tab.buffer.insert_at_cursor('two')
        tab.buffer.end_user_action()
        self.assertEqual(editor._text(tab.buffer), 'first\nsecondonetwo')
        tab.undo()
        self.assertEqual(editor._text(tab.buffer), 'first\nsecond')
        self.assertFalse(self.panel.has_unsaved())
        self.replace(tab, 'new branch')
        tab.undo(True)
        self.assertEqual(editor._text(tab.buffer), 'new branch')

    def test_dirty_save_and_undo_to_saved_state(self):
        tab = self.open()
        self.replace(tab, 'modified')
        self.assertTrue(self.panel.has_unsaved())
        self.assertTrue(tab.label.get_text().startswith('● '))
        self.assertTrue(self.panel.save_current())
        self.assertEqual(self.path.read_text(), 'modified')
        self.assertFalse(self.panel.has_unsaved())
        tab.undo()
        self.assertTrue(self.panel.has_unsaved())
        tab.undo(True)
        self.assertFalse(self.panel.has_unsaved())

    def test_save_all_and_nothing_to_save(self):
        self.assertTrue(self.panel.save_current())
        self.assertTrue(self.panel.save_all())
        first = self.open()
        other = self.root / 'other.py'
        other.write_text('other')
        second = self.open(other)
        self.replace(first, 'one')
        self.replace(second, 'two')
        self.assertTrue(self.panel.save_all())
        self.assertEqual(self.path.read_text(), 'one')
        self.assertEqual(other.read_text(), 'two')
        self.assertFalse(self.panel.has_unsaved())

    def test_save_all_continues_after_failure(self):
        first = self.open()
        other = self.root / 'other.py'
        other.write_text('other')
        second = self.open(other)
        self.replace(first, 'one')
        self.replace(second, 'two')
        real_write = editor.atomic_write
        def write(path, text, ending):
            if path == str(self.path):
                raise OSError('permission denied')
            return real_write(path, text, ending)
        with patch.object(editor, 'atomic_write', side_effect=write):
            self.assertFalse(self.panel.save_all())
        self.assertEqual(other.read_text(), 'two')
        self.assertTrue(first.buffer.get_modified())
        self.message.assert_called_once()

    def test_crlf_status_and_save(self):
        self.path.write_bytes(b'one\r\ntwo')
        tab = self.open()
        self.assertIn('CRLF', self.panel.status.get_text())
        self.replace(tab, 'one\ntwo\nthree')
        self.panel.save_current()
        self.assertEqual(self.path.read_bytes(), b'one\r\ntwo\r\nthree')
        tab.buffer.place_cursor(tab.buffer.get_iter_at_offset(5))
        self.assertIn('Ln 2, Col 2', self.panel.status.get_text())

    def test_rename_file_and_folder_boundary(self):
        tab = self.open()
        new = self.root / 'renamed.py'
        self.path.rename(new)
        self.panel.on_path_renamed(str(self.path), str(new))
        self.assertEqual(tab.path, str(new))
        folder = self.root / 'folder'
        folder.mkdir()
        nested = folder / 'nested.py'
        nested.write_text('nested')
        child = self.open(nested)
        sibling = self.root / 'folder-other.py'
        sibling.write_text('sibling')
        unrelated = self.open(sibling)
        moved = self.root / 'moved'
        folder.rename(moved)
        self.panel.on_path_renamed(str(folder), str(moved))
        self.assertEqual(child.path, str(moved / 'nested.py'))
        self.assertEqual(unrelated.path, str(sibling))
        self.assertTrue(self.panel.save_all())

    def test_delete_file_recreate_and_folder(self):
        tab = self.open()
        self.path.unlink()
        self.panel.on_path_deleted(str(self.path))
        self.assertIn('(deleted)', tab.label.get_text())
        self.assertEqual(editor._text(tab.buffer), 'first\nsecond')
        self.assertTrue(self.panel.has_unsaved())
        self.assertTrue(self.panel.save_current())
        self.assertNotIn('(deleted)', tab.label.get_text())
        self.assertEqual(self.path.read_text(), 'first\nsecond')
        folder = self.root / 'dir'
        folder.mkdir()
        nested = folder / 'nested.py'
        nested.write_text('nested')
        child = self.open(nested)
        self.panel.on_path_deleted(str(folder))
        self.assertTrue(child.deleted)
        self.assertFalse(tab.deleted)

    def test_close_cancel_discard_save(self):
        tab = self.open()
        self.replace(tab, 'edited')
        self.assertFalse(self.panel._close_tab(tab))
        self.ask.return_value = 'save'
        with patch.object(editor, 'atomic_write', side_effect=OSError('denied')):
            self.assertFalse(self.panel._close_tab(tab))
        self.assertTrue(self.panel._close_tab(tab))
        self.assertEqual(self.path.read_text(), 'edited')
        self.assertEqual(self.panel.stack.get_visible_child_name(), 'empty')
        tab = self.open()
        self.replace(tab, 'discarded')
        self.ask.return_value = 'discard'
        self.assertTrue(self.panel._close_tab(tab))
        self.assertEqual(self.path.read_text(), 'edited')

    def test_external_modification_prompt(self):
        tab = self.open()
        self.replace(tab, 'mine')
        self.path.write_text('theirs')
        os.utime(self.path, ns=(tab.mtime + 1000000, tab.mtime + 1000000))
        self.assertFalse(self.panel.save_current())
        self.assertEqual(self.path.read_text(), 'theirs')
        self.ask.assert_called_with(self.panel, 'File changed on disk. Overwrite?', ('overwrite', 'cancel'))
        self.ask.return_value = 'overwrite'
        self.assertTrue(self.panel.save_current())
        self.assertEqual(self.path.read_text(), 'mine')

    def test_edit_keys_undo_redo(self):
        tab = self.open()
        self.replace(tab, '  hello')
        tab.buffer.place_cursor(tab.buffer.get_end_iter())
        self.key(tab, Gdk.KEY_Return)
        self.assertEqual(editor._text(tab.buffer), '  hello\n  ')
        self.key(tab, Gdk.KEY_Tab)
        self.assertEqual(editor._text(tab.buffer), '  hello\n      ')
        self.key(tab, Gdk.KEY_ISO_Left_Tab, shift=True)
        self.assertEqual(editor._text(tab.buffer), '  hello\n  ')
        self.key(tab, Gdk.KEY_z, ctrl=True)
        self.assertEqual(editor._text(tab.buffer), '  hello\n      ')
        self.key(tab, Gdk.KEY_Z, ctrl=True, shift=True)
        self.assertEqual(editor._text(tab.buffer), '  hello\n  ')

    def test_comment_selection_and_unknown_extension(self):
        tab = self.open()
        tab.buffer.select_range(tab.buffer.get_start_iter(), tab.buffer.get_end_iter())
        self.key(tab, Gdk.KEY_slash, ctrl=True)
        self.assertEqual(editor._text(tab.buffer), '# first\n# second')
        tab.buffer.select_range(tab.buffer.get_start_iter(), tab.buffer.get_end_iter())
        self.key(tab, Gdk.KEY_slash, ctrl=True)
        self.assertEqual(editor._text(tab.buffer), 'first\nsecond')
        tab.path += '.txt'
        self.key(tab, Gdk.KEY_slash, ctrl=True)
        self.assertEqual(editor._text(tab.buffer), 'first\nsecond')

    def test_tab_switch_close_find_and_search(self):
        first = self.open()
        other = self.root / 'other.py'
        other.write_text('other')
        second = self.open(other)
        self.key(second, Gdk.KEY_Tab, ctrl=True)
        self.assertIs(self.panel._current(), first)
        self.key(first, Gdk.KEY_Tab, ctrl=True, shift=True)
        self.assertIs(self.panel._current(), second)
        self.key(second, Gdk.KEY_Page_Up, ctrl=True)
        self.assertIs(self.panel._current(), first)
        self.key(first, Gdk.KEY_f, ctrl=True)
        self.assertTrue(self.panel.search_bar.get_search_mode())
        self.panel.search_entry.set_text('FIRST')
        self.panel._find()
        self.assertEqual(first.buffer.get_text(*first.buffer.get_selection_bounds(), True), 'first')
        self.panel._find(True)
        self.assertTrue(first.buffer.get_has_selection())
        self.panel.search_entry.set_text('absent')
        self.panel._find()
        self.assertTrue(self.panel.search_entry.get_style_context().has_class('error'))
        self.key(first, Gdk.KEY_w, ctrl=True)
        self.assertEqual(len(self.panel.tabs), 1)


    def test_open_file_at_selects_clamps_and_reports(self):
        self.panel.open_file_at(str(self.path), 1, 2, 3)
        tab = self.panel._current()
        self.assertEqual(tab.buffer.get_text(*tab.buffer.get_selection_bounds(), True), 'con')
        self.assertTrue(self.panel.status.get_text().startswith('Ln 2, Col 3'))
        self.panel.open_file_at(str(self.path), 0, 3, 50)
        self.assertEqual(tab.buffer.get_text(*tab.buffer.get_selection_bounds(), True), 'st')
        self.panel.open_file_at(str(self.path), 99, 99, 99)
        self.assertFalse(tab.buffer.get_has_selection())
        self.assertEqual(len(self.panel.tabs), 1)
        self.panel.open_file_at(str(self.root / 'missing.py'), 0)
        self.message.assert_called_once()
        self.assertEqual(len(self.panel.tabs), 1)

    def test_current_file_signal_buffer_text_and_selected_text(self):
        seen = []
        self.panel.connect('current-file-changed', lambda _, path: seen.append(path))
        self.assertIsNone(self.panel.current_path())
        first = self.open()
        other = self.root / 'other.py'
        other.write_text('other')
        second = self.open(other)
        self.panel.notebook.set_current_page(self.panel.notebook.page_num(first.page))
        self.panel._close_tab(first)
        self.panel._close_tab(second)
        self.assertEqual(seen, [str(self.path), str(other), str(self.path), str(other), ''])
        self.assertIsNone(self.panel.current_path())
        self.assertIsNone(self.panel.buffer_text(str(self.path)))
        tab = self.open()
        self.replace(tab, 'edited text')
        self.assertEqual(self.panel.buffer_text(str(self.path)), 'edited text')
        self.assertEqual(self.panel.selected_text(), '')
        tab.buffer.select_range(tab.buffer.get_start_iter(), tab.buffer.get_iter_at_offset(6))
        self.assertEqual(self.panel.selected_text(), 'edited')
        self.panel.on_path_deleted(str(self.path))
        self.assertIsNone(self.panel.buffer_text(str(self.path)))

    def test_ctrl_zero_uses_default_size_and_ctrl_shift_f_is_not_in_file_find(self):
        tab = self.open()
        self.assertEqual(self.panel.default_font_size, editor.DEFAULT_FONT_SIZE)
        self.panel.set_default_font_size(15)
        self.panel.set_default_font_size(15)
        self.key(tab, Gdk.KEY_0, ctrl=True)
        self.assertEqual(self.panel.font_size, 15)
        self.key(tab, Gdk.KEY_plus, ctrl=True)
        self.key(tab, Gdk.KEY_0, ctrl=True)
        self.assertEqual(self.panel.font_size, 15)
        self.panel.set_default_font_size(1000)
        self.assertEqual(self.panel.default_font_size, editor.MAX_FONT_SIZE)
        self.assertFalse(self.key(tab, Gdk.KEY_F, ctrl=True, shift=True))
        self.assertFalse(self.panel.search_bar.get_search_mode())


class MarkdownEditorPanelTest(unittest.TestCase):
    setUp = EditorPanelTest.setUp
    open = EditorPanelTest.open
    replace = EditorPanelTest.replace
    key = EditorPanelTest.key

    def markdown(self, name='notes.md', text='# Title\n\n**body**'):
        path = self.root / name
        path.write_text(text)
        return self.open(path)

    def wait(self, predicate, timeout=3):
        import time
        with warnings.catch_warnings():
            warnings.filterwarnings('ignore', category=DeprecationWarning, module=r'gi\.events')
            end = time.monotonic() + timeout
            while not predicate() and time.monotonic() < end:
                if Gtk.events_pending():
                    Gtk.main_iteration_do(False)
                else:
                    time.sleep(.002)
        self.assertTrue(predicate())

    def ready(self, tab):
        self.wait(lambda: not tab._md_timeout and not tab.md_renderer._idle and not tab._md_restore_idle)

    def test_only_markdown_and_default_source(self):
        plain = self.open()
        self.assertIsNone(plain.md_toggle)
        self.assertIsNone(self.panel.markdown_mode())
        for name in ('notes.md', 'README.MD', 'doc.MarkDown'):
            tab = self.markdown(name)
            self.assertIsInstance(tab.page, Gtk.Overlay)
            self.assertIsNotNone(tab.md_toggle)
            self.assertEqual(tab.md_mode, 'source')
            self.assertEqual(self.panel.markdown_mode(), 'source')
            self.assertEqual(tab.md_stack.get_visible_child_name(), 'source')
            self.assertEqual(tab.md_buttons['preview'].get_tooltip_text(), 'Preview Markdown')
            self.assertEqual(tab.md_buttons['source'].get_tooltip_text(), 'Show source')
            self.assertFalse(tab.md_preview_view.get_editable())
        with self.assertRaises(ValueError):
            self.panel.set_markdown_mode('invalid')

    def test_buttons_switch_both_ways_and_remain_pressed(self):
        tab = self.markdown()
        tab.md_buttons['preview'].clicked()
        self.ready(tab)
        self.assertEqual(tab.md_mode, 'preview')
        self.assertTrue(tab.md_buttons['preview'].get_active())
        self.assertFalse(tab.md_buttons['source'].get_active())
        tab.md_buttons['preview'].clicked()
        self.assertTrue(tab.md_buttons['preview'].get_active())
        tab.md_buttons['source'].clicked()
        self.assertEqual(tab.md_mode, 'source')
        self.assertTrue(tab.md_buttons['source'].get_active())
        self.assertFalse(tab.md_buttons['preview'].get_active())

    def test_per_tab_mode_and_named_path(self):
        first = self.markdown()
        second = self.markdown('second.md')
        self.panel.set_markdown_mode('preview', first.path)
        self.assertEqual(self.panel.markdown_mode(first.path), 'preview')
        self.assertEqual(self.panel.markdown_mode(second.path), 'source')
        self.assertIs(self.panel._current(), second)
        self.assertIsNone(self.panel.markdown_mode(self.root / 'missing.md'))
        self.panel.open_file(first.path)
        self.ready(first)
        self.assertIs(self.panel._current(), first)
        self.assertTrue(first.md_preview_view.is_focus())

    def test_unsaved_buffer_and_debounced_live_update(self):
        tab = self.markdown()
        self.replace(tab, '# Unsaved\n\nhello')
        self.panel.set_markdown_mode('preview')
        self.ready(tab)
        self.assertIn('Unsaved', editor._text(tab.md_renderer.buffer))
        self.assertNotIn('Unsaved', Path(tab.path).read_text())
        self.replace(tab, '# Latest')
        self.assertNotEqual(tab._md_timeout, 0)
        self.replace(tab, '# Final')
        self.ready(tab)
        self.assertIn('Final', editor._text(tab.md_renderer.buffer))
        self.assertNotIn('Latest', editor._text(tab.md_renderer.buffer))
        self.assertTrue(tab.buffer.get_modified())

    def test_cursor_focus_and_status_survive_switch(self):
        tab = self.markdown()
        tab.buffer.place_cursor(tab.buffer.get_iter_at_offset(4))
        before = self.panel.status.get_text()
        self.panel.set_markdown_mode('preview')
        self.ready(tab)
        self.assertTrue(tab.md_preview_view.is_focus())
        self.assertEqual(self.panel.status.get_text(), before + ' (Preview)')
        self.panel.set_markdown_mode('source')
        self.assertEqual(tab.cursor(), 4)
        self.assertTrue(tab.view.is_focus())
        self.assertEqual(self.panel.status.get_text(), before)

    def test_scroll_context_and_rerender_offset(self):
        tab = self.markdown(text=('paragraph line\n\n' * 300))
        self.wait(lambda: tab.source_scroll.get_vadjustment().get_upper() > 1000)
        a = tab.source_scroll.get_vadjustment()
        a.set_value((a.get_upper() - a.get_page_size()) * .4)
        original = a.get_value()
        self.panel.set_markdown_mode('preview')
        self.ready(tab)
        preview = tab.md_scroll.get_vadjustment()
        self.assertGreater(preview.get_value(), 100)
        preview.set_value(200)
        tab.buffer.insert(tab.buffer.get_end_iter(), '\nextra')
        self.ready(tab)
        self.assertAlmostEqual(preview.get_value(), 200, delta=2)
        self.panel.set_markdown_mode('source')
        self.assertAlmostEqual(a.get_value(), original, delta=2)

    def test_theme_and_zoom_update_open_and_future_previews(self):
        from idgent import themes
        tab = self.markdown()
        self.panel.set_markdown_mode('preview')
        self.ready(tab)
        for name in ('light-plus', 'nord', 'dark-plus'):
            theme = themes.get_theme(name)
            self.panel.set_syntax_style(theme.syntax)
            self.panel.set_chrome(theme.chrome)
            self.panel.set_font_size(16)
            tag = tab.md_renderer.tags['h1']
            from gi.repository import Gdk
            expected = Gdk.RGBA()
            expected.parse(theme.syntax['heading'].foreground)
            self.assertEqual(tag.get_property('foreground-rgba'), expected)
            self.assertEqual(tab.md_renderer.size, 16)
            self.assertEqual(tab.md_renderer.chrome, self.panel.chrome)
        later = self.markdown('later.markdown')
        self.assertEqual(later.md_renderer.size, 16)
        self.assertEqual(later.md_renderer.chrome, self.panel.chrome)

    def test_rename_adds_removes_and_readds_toggle(self):
        tab = self.open()
        tab.set_path(str(self.root / 'renamed.MD'))
        self.assertIsNotNone(tab.md_toggle)
        self.panel.set_markdown_mode('preview')
        renderer = tab.md_renderer
        tab.set_path(str(self.root / 'renamed.txt'))
        self.assertIsNone(tab.md_toggle)
        self.assertIsNone(tab.md_preview_view)
        self.assertTrue(renderer.destroyed)
        self.assertEqual(tab.md_mode, 'source')
        self.assertEqual(renderer._idle, 0)
        tab.set_path(str(self.root / 'again.markdown'))
        self.assertIsNotNone(tab.md_toggle)
        self.panel.set_markdown_mode('preview')
        self.ready(tab)

    def test_partial_and_invalid_syntax_styles_remain_supported(self):
        tab = self.markdown(text='# Heading\n\n```python\nreturn 123\n```')
        self.panel.set_syntax_style({'keyword': editor.highlight.SyntaxStyle('not-a-color')})
        self.panel.set_markdown_mode('preview')
        self.ready(tab)
        self.assertIn('return 123', editor._text(tab.md_renderer.buffer))
        self.assertFalse(tab.md_renderer.tags['syntax-keyword'].get_property('foreground-set'))

    def test_shortcut_find_readonly_and_shared_zoom(self):
        tab = self.markdown()
        self.assertTrue(self.key(tab, Gdk.KEY_V, ctrl=True, shift=True))
        self.ready(tab)
        self.assertEqual(tab.md_mode, 'preview')
        before = editor._text(tab.buffer)
        self.key(tab, Gdk.KEY_Return)
        self.key(tab, Gdk.KEY_Tab)
        self.key(tab, Gdk.KEY_slash, ctrl=True)
        self.assertEqual(editor._text(tab.buffer), before)
        self.key(tab, Gdk.KEY_plus, ctrl=True)
        self.assertEqual(tab.md_renderer.size, 12)
        self.key(tab, Gdk.KEY_minus, ctrl=True)
        self.assertEqual(tab.md_renderer.size, 11)
        self.key(tab, Gdk.KEY_0, ctrl=True)
        self.key(tab, Gdk.KEY_f, ctrl=True)
        self.assertEqual(tab.md_mode, 'source')
        self.assertTrue(self.panel.search_bar.get_search_mode())
        self.key(tab, Gdk.KEY_v, ctrl=True, shift=True)
        self.assertFalse(self.panel.search_bar.get_search_mode())
        self.key(tab, Gdk.KEY_v, ctrl=True, shift=True)
        self.assertEqual(tab.md_mode, 'source')

    def test_preview_key_event_scroll_switch_and_close(self):
        tab = self.markdown()
        self.panel.set_markdown_mode('preview')
        event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        event.keyval, event.state = Gdk.KEY_plus, Gdk.ModifierType.CONTROL_MASK
        self.assertTrue(tab.md_preview_view.emit('key-press-event', event))
        self.assertEqual(self.panel.font_size, 12)
        scroll = Gdk.Event.new(Gdk.EventType.SCROLL)
        scroll.state, scroll.direction = Gdk.ModifierType.CONTROL_MASK, Gdk.ScrollDirection.UP
        tab.md_preview_view.emit('scroll-event', scroll)
        self.assertEqual(self.panel.font_size, 13)
        other = self.markdown('other.md')
        self.key(other, Gdk.KEY_Tab, ctrl=True)
        self.assertIs(self.panel._current(), tab)
        event.keyval = Gdk.KEY_w
        self.assertTrue(tab.md_preview_view.emit('key-press-event', event))
        self.assertNotIn(tab, self.panel.tabs)

    def test_close_and_destroy_cancel_pending_work(self):
        tab = self.markdown()
        self.panel.set_markdown_mode('preview')
        self.replace(tab, '# pending')
        renderer = tab.md_renderer
        self.assertNotEqual(tab._md_timeout, 0)
        self.ask.return_value = 'discard'
        self.assertTrue(self.panel._close_tab(tab))
        self.assertEqual(tab._md_timeout, 0)
        self.assertEqual(renderer._idle, 0)
        self.assertTrue(renderer.destroyed)
        tab = self.markdown('again.md')
        self.panel.set_markdown_mode('preview')
        renderer = tab.md_renderer
        self.window.destroy()
        self.assertTrue(renderer.destroyed)
        self.assertEqual(renderer._idle, 0)
        self.assertEqual(tab._md_restore_idle, 0)


if __name__ == '__main__':
    unittest.main()
