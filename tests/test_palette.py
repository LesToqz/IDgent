"""Fuzzy matcher (pure) and prompt/switcher/palette widgets; run with GDK_BACKEND=x11 under xvfb."""
import os
import random
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ["GDK_BACKEND"] = "x11"

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, GLib, Gtk, Pango

from idgent import motion, overlay, palette, style
from idgent.overlay import OverlayHost
from idgent.palette import (MAX_RESULTS, Command, CommandPalette, FuzzyIndex, Prompt, PromptItem,
                            QuickSwitcher, highlight_markup, match, prompt_geometry, rank,
                            split_path_ranges)
from idgent.themes import THEMES, get_theme
from tests.test_widgets import pump, send_key


CTRL = Gdk.ModifierType.CONTROL_MASK
SHIFT = Gdk.ModifierType.SHIFT_MASK


def labels(results):
    return [item for item, _score, _ranges in results]


def parse_errors(css):
    provider = Gtk.CssProvider()
    errors = []
    provider.connect('parsing-error', lambda _, _section, error: errors.append(str(error)))
    provider.load_from_data(css.encode('utf-8'))
    return errors


def install_theme(test, theme_id='idgent-dark'):
    """Apply a theme plus the not-yet-composed component CSS for one test."""
    theme = get_theme(theme_id)
    style.apply_theme(theme)
    provider = Gtk.CssProvider()
    provider.load_from_data((palette.build_css(theme.ui) + overlay.build_css(theme.ui)).encode())
    screen = Gdk.Screen.get_default()
    Gtk.StyleContext.add_provider_for_screen(screen, provider,
                                             Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
    test.addCleanup(Gtk.StyleContext.remove_provider_for_screen, screen, provider)
    test.addCleanup(style.apply_style)


def click_at(toplevel, x, y, state=0):
    """Real pointer routing: warp, find the deepest GdkWindow, press and release."""
    window = toplevel.get_window()
    pointer = window.get_display().get_default_seat().get_pointer()
    _ok, origin_x, origin_y = window.get_origin()
    pointer.warp(window.get_screen(), origin_x + x, origin_y + y)
    pump(20)
    target, local_x, local_y = pointer.get_window_at_position()
    for kind in (Gdk.EventType.BUTTON_PRESS, Gdk.EventType.BUTTON_RELEASE):
        event = Gdk.Event.new(kind)
        event.window = target
        event.x, event.y = local_x, local_y
        event.x_root, event.y_root = origin_x + x, origin_y + y
        event.button = 1
        event.state = Gdk.ModifierType(state)
        event.set_device(pointer)
        event.time = Gtk.get_current_event_time()
        Gtk.main_do_event(event)
    pump(20)


def move_pointer(toplevel, x, y):
    window = toplevel.get_window()
    pointer = window.get_display().get_default_seat().get_pointer()
    _ok, origin_x, origin_y = window.get_origin()
    pointer.warp(window.get_screen(), origin_x + x, origin_y + y)
    pump(20)
    target, local_x, local_y = pointer.get_window_at_position()
    event = Gdk.Event.new(Gdk.EventType.MOTION_NOTIFY)
    event.window = target
    event.x, event.y = local_x, local_y
    event.x_root, event.y_root = origin_x + x, origin_y + y
    event.set_device(pointer)
    event.time = Gtk.get_current_event_time()
    Gtk.main_do_event(event)
    pump(20)


def make_paths(count, seed=7):
    rng = random.Random(seed)
    words = ['src', 'lib', 'test', 'docs', 'core', 'utils', 'model', 'view', 'plan', 'api',
             'server', 'client', 'main', 'config', 'index', 'helper', 'widget', 'palette',
             'overlay', 'theme', 'style', 'editor', 'terminal', 'search']
    paths = []
    for number in range(count):
        folder = '/'.join(rng.choice(words) for _ in range(rng.randint(1, 4)))
        name = f'{rng.choice(words)}_{rng.choice(words)}{number}'
        paths.append(f"{folder}/{name}.{rng.choice(['py', 'md', 'js', 'txt'])}")
    return paths


# Matcher (no display needed) ---------------------------------------------------

class MatcherTest(unittest.TestCase):
    def test_empty_query_matches_everything_without_ranges(self):
        self.assertEqual(match('', 'anything'), (0.0, ()))
        self.assertEqual(match('   ', 'anything'), (0.0, ()))
        self.assertEqual(labels(rank('', ['b', 'a'])), ['b', 'a'])

    def test_order_matters_and_missing_characters_fail(self):
        self.assertIsNone(match('lpa', 'Plan.md'))
        self.assertIsNone(match('plx', 'Plan.md'))
        self.assertIsNotNone(match('pmd', 'Plan.md'))

    def test_pla_ranks_plan_md_first_and_file_names_outrank_folders(self):
        candidates = ['notes/plan/todo.md', 'explain.md', 'apple.md', 'people/lamp.md',
                      'Planning/Plan.md', 'p_l_a.md', 'Plan.md']
        ranked = labels(rank('pla', candidates, path=True))
        self.assertEqual(ranked[0], 'Plan.md')
        self.assertNotIn('apple.md', ranked)
        self.assertLess(ranked.index('Planning/Plan.md'), ranked.index('p_l_a.md'))
        self.assertLess(ranked.index('explain.md'), ranked.index('notes/plan/todo.md'))
        self.assertEqual(match('pla', 'Planning/Plan.md', path=True)[1], ((9, 12),))
        self.assertEqual(match('pla', 'Plan.md', path=True)[1], ((0, 3),))

    def test_word_boundaries_win(self):
        self.assertEqual(labels(rank('fb', ['fabric.py', 'foo_bar.py'])), ['foo_bar.py', 'fabric.py'])
        self.assertEqual(match('fb', 'foo_bar.py')[1], ((0, 1), (4, 5)))
        self.assertEqual(labels(rank('gfn', ['gifnorm', 'getFileName'])), ['getFileName', 'gifnorm'])
        self.assertEqual(match('gfn', 'getFileName')[1], ((0, 1), (3, 4), (7, 8)))
        self.assertEqual(match('v2', 'view2')[1], ((0, 1), (4, 5)))

    def test_consecutive_and_compact_matches_win(self):
        self.assertEqual(labels(rank('main', ['m_a_i_n.py', 'mxaxixn.py', 'main.py'])),
                         ['main.py', 'm_a_i_n.py', 'mxaxixn.py'])
        self.assertLess(match('ab', 'a' + 'x' * 30 + 'b')[0], match('ab', 'axb')[0])

    def test_late_first_match_and_long_candidates_are_penalised(self):
        self.assertGreater(match('x', 'x' + 'a' * 20)[0], match('x', 'a' * 20 + 'x')[0])
        self.assertGreater(match('abc', 'abc')[0], match('abc', 'abc' + 'z' * 60)[0])

    def test_tokens_must_all_match_in_order(self):
        score, ranges = match('src main', 'src/app/main.py', path=True)
        self.assertEqual(ranges, ((0, 3), (8, 12)))
        self.assertIsNone(match('main zzz', 'src/app/main.py'))
        # Out-of-order tokens fall back to a character subsequence, which fails here.
        self.assertIsNone(match('main src', 'src/app/main.py'))

    def test_case_rules(self):
        self.assertIsNotNone(match('PLAN', 'plan.md'))
        self.assertIsNotNone(match('plan', 'PLAN.MD'))
        # A lower-case query is case-blind: equal candidates keep source order.
        self.assertEqual(labels(rank('readme', ['README.md', 'readme.md'])), ['README.md', 'readme.md'])
        self.assertEqual(labels(rank('readme', ['readme.md', 'README.md'])), ['readme.md', 'README.md'])
        # Upper case in the query prefers exact case (smart case).
        self.assertEqual(labels(rank('README', ['readme.md', 'README.md'])), ['README.md', 'readme.md'])

    def test_unicode(self):
        self.assertEqual(match('ä', 'Ärger.md')[1], ((0, 1),))
        self.assertEqual(match('ÄR', 'märz ärger')[1], ((5, 7),))
        self.assertEqual(match('日本', '資料/日本語.md', path=True)[1], ((3, 5),))
        self.assertEqual(match('🙂x', 'a🙂x')[1], ((1, 3),))
        # Lower-casing 'İ' yields two code points; ranges must still index the original.
        score, ranges = match('x', 'İİx')
        self.assertEqual(ranges, ((2, 3),))
        self.assertEqual(match('İ', 'xİy')[1], ((1, 2),))

    def test_ties_keep_source_order(self):
        items = [{'name': f'same{n}', 'id': n} for n in range(5)]
        for item in items:
            item['name'] = 'same'
        result = rank('sa', items, key=lambda item: item['name'])
        self.assertEqual([item['id'] for item, _s, _r in result], [0, 1, 2, 3, 4])

    def test_limit_and_key(self):
        items = [f'file{n}.txt' for n in range(500)]
        self.assertEqual(len(rank('file', items)), MAX_RESULTS)
        self.assertEqual(len(rank('file', items, limit=None)), 500)
        self.assertEqual(len(rank('file', items, limit=3)), 3)

    def test_incremental_index_matches_fresh_ranking(self):
        paths = make_paths(3000)
        index = FuzzyIndex(paths, path=True)
        for query in ['p', 'pl', 'pla', 'plan', 'plan ', 'plan m', 'plan md', 'plan', 'x', 'xy', '']:
            with self.subTest(query=query):
                self.assertEqual(index.search(query), rank(query, paths, path=True))

    def test_highlight_markup_escapes_and_bolds(self):
        markup = highlight_markup('a<b&c>', [(1, 3)])
        self.assertEqual(markup, 'a<b>&lt;b</b>&amp;c&gt;')
        ok, _attrs, text, _accel = Pango.parse_markup(markup, -1, '\0')
        self.assertTrue(ok)
        self.assertEqual(text, 'a<b&c>')
        self.assertEqual(highlight_markup('abc', [(2, 9), (0, 1)]), '<b>a</b>b<b>c</b>')
        self.assertEqual(highlight_markup('x & y'), 'x &amp; y')

    def test_split_path_ranges(self):
        path = 'src/plan/planner.py'
        _score, ranges = match('plan pla', path, path=True)
        folder, name = split_path_ranges(path, ranges)
        self.assertEqual([path[:8][b:e] for b, e in folder], ['plan'])
        self.assertEqual(['planner.py'[b:e] for b, e in name], ['pla'])
        self.assertEqual(split_path_ranges('a.py', [(0, 1)]), ([], [(0, 1)]))

    def test_prompt_geometry(self):
        self.assertEqual(prompt_geometry(1000, 800), (150, 120, 700, 560))
        x, y, width, max_height = prompt_geometry(600, 400)
        self.assertEqual((width, y, max_height), (480, 60, 280))
        self.assertEqual(x, 60)

    def test_twenty_thousand_items_filter_quickly(self):
        index = FuzzyIndex(make_paths(20000), path=True)
        worst = 0.0
        for query in ['e', 'p', 'pl', 'pla', 'plan', 'plan m', 'zzz', 'src/main', 'pal th']:
            start = time.perf_counter()
            index.search(query)
            worst = max(worst, time.perf_counter() - start)
        # Measured ~60 ms worst case (single common letter); generous for slow CI.
        self.assertLess(worst, 1.0)

    def test_css_parses_for_every_theme(self):
        self.assertEqual(len(THEMES), 14)
        for theme in THEMES:
            with self.subTest(theme=theme.id):
                css = palette.build_css(theme.ui)
                self.assertEqual(parse_errors(css), [])
                self.assertNotIn('transition: background', css)


# Widgets ------------------------------------------------------------------------

class PromptCase(unittest.TestCase):
    width, height = 1000, 800

    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        env = patch.dict(os.environ, XDG_CONFIG_HOME=root.name)
        env.start()
        self.addCleanup(env.stop)
        install_theme(self)
        motion.set_reduce_motion(True)
        self.addCleanup(motion.set_reduce_motion, False)
        self.window = Gtk.Window()
        self.window.set_default_size(self.width, self.height)
        self.addCleanup(self.window.destroy)
        self.text = Gtk.TextView()
        self.text.get_buffer().set_text('main content\n' * 5)
        self.host = OverlayHost(self.text)
        self.window.add(self.host)
        self.window.show_all()
        self.window.present()
        pump(120)
        self.text.grab_focus()
        pump()

    def prompt(self, count=5, **kwargs):
        def provider(query):
            items = [PromptItem(f'item {n}', data=n) for n in range(count)]
            return [item for item in items if not query or query in item.label]
        prompt = Prompt(provider, **kwargs)
        self.addCleanup(prompt.destroy)
        return prompt

    def show(self, prompt):
        self.host.show_prompt(prompt)
        pump(60)
        return prompt

    def record(self, prompt):
        events = []
        prompt.connect('chosen', lambda _p, item, mods: events.append(('chosen', item.data, mods)))
        prompt.connect('query-chosen', lambda _p, query, mods: events.append(('query', query, mods)))
        prompt.connect('closed', lambda _p: events.append(('closed',)))
        return events

    def frame_rect(self, prompt):
        x, y = prompt.frame.translate_coordinates(self.host, 0, 0)
        allocation = prompt.frame.get_allocation()
        return x, y, allocation.width, allocation.height

    def pixel(self, widget, x, y):
        tx, ty = widget.translate_coordinates(self.window, x, y)
        data = Gdk.pixbuf_get_from_window(self.window.get_window(), tx, ty, 1, 1).get_pixels()
        return tuple(data[:3])


class PromptTest(PromptCase):
    def test_geometry_width_top_and_max_height(self):
        prompt = self.show(self.prompt(count=60))
        x, y, width, height = self.frame_rect(prompt)
        self.assertEqual((x, y, width), (150, 120, 700))
        self.assertEqual(height, 560)
        self.assertTrue(prompt.entry.has_focus())

    def test_short_list_uses_natural_height(self):
        prompt = self.show(self.prompt(count=2))
        _x, _y, _w, height = self.frame_rect(prompt)
        self.assertLess(height, 200)

    def test_narrow_host_clamps_width_to_eighty_percent(self):
        self.window.resize(600, 500)
        pump(150)
        prompt = self.show(self.prompt(count=60))
        x, y, width, height = self.frame_rect(prompt)
        host_width, host_height = self.host.get_allocated_width(), self.host.get_allocated_height()
        self.assertEqual(width, int(host_width * .8))
        self.assertEqual(y, round(host_height * .15))
        self.assertEqual(height, int(host_height * .7))
        self.assertEqual(x, (host_width - width) // 2)

    def test_row_and_input_heights(self):
        prompt = self.show(self.prompt())
        self.assertIn(prompt._rows[0].get_allocated_height(), (31, 32))
        self.assertTrue(48 <= prompt.entry.get_allocated_height() <= 52)
        context = prompt.entry.get_style_context()
        self.assertEqual(context.get_property('font', Gtk.StateFlags.NORMAL).get_size() / Pango.SCALE,
                         11.25)  # 15 px

    def test_result_cap(self):
        prompt = self.show(self.prompt(count=500))
        self.assertEqual(len(prompt.items), MAX_RESULTS)
        self.assertEqual(sum(row.get_visible() for row in prompt._rows), MAX_RESULTS)

    def test_keyboard_navigation_wraps_and_pages_clamp(self):
        prompt = self.show(self.prompt(count=40))
        entry = prompt.entry
        self.assertEqual(prompt.selected_index, 0)
        send_key(entry, Gdk.KEY_Down)
        self.assertEqual(prompt.selected_index, 1)
        send_key(entry, Gdk.KEY_Up)
        send_key(entry, Gdk.KEY_Up)
        self.assertEqual(prompt.selected_index, 39)
        send_key(entry, Gdk.KEY_Down)
        self.assertEqual(prompt.selected_index, 0)
        send_key(entry, Gdk.KEY_Page_Down)
        page = prompt.selected_index
        self.assertGreater(page, 5)
        for _ in range(10):
            send_key(entry, Gdk.KEY_Page_Down)
        self.assertEqual(prompt.selected_index, 39)
        send_key(entry, Gdk.KEY_Page_Up)
        self.assertEqual(prompt.selected_index, 39 - page)
        send_key(entry, Gdk.KEY_Home)
        self.assertEqual(prompt.selected_index, 0)
        send_key(entry, Gdk.KEY_End)
        self.assertEqual(prompt.selected_index, 39)
        pump(50)
        # The selected row is scrolled into view.
        adjustment = prompt.scroller.get_vadjustment()
        self.assertGreater(adjustment.get_value(), 0)
        self.assertEqual(prompt.list.get_selected_row(), prompt._rows[39])
        send_key(entry, Gdk.KEY_Tab)
        self.assertTrue(entry.has_focus())

    def test_selection_is_instant_overlay_highlight(self):
        prompt = self.show(self.prompt())
        first, second = prompt._rows[0], prompt._rows[1]
        selected, plain = self.pixel(first, 3, 15), self.pixel(second, 3, 15)
        self.assertNotEqual(selected, plain)
        send_key(prompt.entry, Gdk.KEY_Down)
        pump(17)
        self.assertEqual(self.pixel(second, 3, 15), selected)
        self.assertEqual(self.pixel(first, 3, 15), plain)

    def test_enter_and_modifier_enter_choose_then_restore_focus(self):
        for state, expected in ((0, 0), (CTRL, int(CTRL)), (SHIFT, int(SHIFT))):
            with self.subTest(state=state):
                prompt = self.show(self.prompt())
                events = self.record(prompt)
                send_key(prompt.entry, Gdk.KEY_Down)
                send_key(prompt.entry, Gdk.KEY_Return, state)
                self.assertEqual(events, [('closed',), ('chosen', 1, expected)])
                self.assertIsNone(self.host.prompt)
                self.assertIsNone(prompt.get_parent())
                self.assertTrue(self.text.has_focus())

    def test_escape_closes_once_and_restores_focus(self):
        prompt = self.show(self.prompt())
        events = self.record(prompt)
        send_key(prompt.entry, Gdk.KEY_Escape)
        send_key(self.text, Gdk.KEY_Escape)
        self.host.hide_prompt()
        self.assertEqual(events, [('closed',)])
        self.assertTrue(self.text.has_focus())

    def test_focus_restore_keeps_entry_selection(self):
        entry = Gtk.Entry(text='keep me')
        self.host.remove(self.text)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.add(entry)
        self.host.add(box)
        box.show_all()
        pump(50)
        entry.grab_focus()
        entry.select_region(1, 3)
        prompt = self.show(self.prompt())
        send_key(prompt.entry, Gdk.KEY_Escape)
        self.assertTrue(entry.has_focus())
        self.assertEqual(entry.get_selection_bounds(), (1, 3))

    def test_mouse_click_chooses_row(self):
        prompt = self.show(self.prompt())
        events = self.record(prompt)
        x, y = prompt._rows[3].translate_coordinates(self.window, 30, 10)
        click_at(self.window, x, y, CTRL)
        self.assertEqual(events, [('closed',), ('chosen', 3, int(CTRL))])
        self.assertTrue(self.text.has_focus())

    def test_pointer_motion_moves_the_single_highlight(self):
        prompt = self.show(self.prompt())
        x, y = prompt._rows[2].translate_coordinates(self.window, 30, 10)
        move_pointer(self.window, x, y)
        self.assertEqual(prompt.selected_index, 2)
        send_key(prompt.entry, Gdk.KEY_Down)
        self.assertEqual(prompt.selected_index, 3)

    def test_outside_click_closes(self):
        for x, y in ((5, 5), (140, 300)):  # catcher, then the prompt's shadow margin
            with self.subTest(point=(x, y)):
                prompt = self.show(self.prompt())
                events = self.record(prompt)
                click_at(self.window, x, y)
                self.assertEqual(events, [('closed',)])
                self.assertIsNone(self.host.prompt)
                self.assertTrue(self.text.has_focus())
        # A click inside the box (footer) keeps it open.
        prompt = self.show(self.prompt())
        x, y = prompt.footer.translate_coordinates(self.window, 5, 5)
        click_at(self.window, x, y)
        self.assertIs(self.host.prompt, prompt)

    def test_markup_is_escaped_and_matches_are_bold(self):
        prompt = Prompt(lambda query: [PromptItem('a<b&c', detail='x>y',
                                                  label_ranges=match(query, 'a<b&c')[1])])
        self.addCleanup(prompt.destroy)
        self.show(prompt)
        prompt.set_query('b&')
        title = prompt._rows[0].title
        self.assertEqual(title.get_text(), 'a<b&c')
        self.assertEqual(title.get_label(), 'a&lt;<b>b&amp;</b>c')
        self.assertEqual(prompt._rows[0].detail.get_text(), 'x>y')

    def test_typing_filters_and_enter_flushes_pending_query(self):
        prompt = self.show(self.prompt(count=20))
        events = self.record(prompt)
        prompt.entry.set_text('item 1')
        pump()
        self.assertEqual([item.data for item in prompt.items], [1] + list(range(10, 20)))
        prompt.entry.set_text('item 13')
        send_key(prompt.entry, Gdk.KEY_Return)  # before the idle refresh ran
        self.assertEqual(events, [('closed',), ('chosen', 13, 0)])

    def test_empty_state_and_query_chosen(self):
        prompt = self.show(self.prompt(empty_text='Nothing here.'))
        events = self.record(prompt)
        prompt.set_query('zzz')
        self.assertEqual(prompt.items, [])
        self.assertTrue(prompt.empty.get_visible())
        self.assertFalse(prompt.scroller.get_visible())
        self.assertEqual(prompt.empty.get_text(), 'Nothing here.')
        send_key(prompt.entry, Gdk.KEY_Down)
        send_key(prompt.entry, Gdk.KEY_Return, SHIFT)
        self.assertEqual(events, [('closed',), ('query', 'zzz', int(SHIFT))])

    def test_footer_instructions(self):
        prompt = self.prompt()
        texts = [child.get_text() for child in prompt._instructions.get_children()]
        self.assertEqual(texts, ['↑↓ to navigate', '↵ to use', 'esc to dismiss'])
        self.assertTrue(prompt.footer.get_visible())
        hidden = self.prompt(instructions=None)
        self.assertFalse(hidden.footer.get_visible())
        custom = self.prompt(instructions=[('<x>', 'a & b')])
        self.assertEqual(custom._instructions.get_children()[0].get_text(), '<x> a & b')
        self.show(prompt)
        size = prompt._instructions.get_children()[0].get_style_context().get_property(
            'font', Gtk.StateFlags.NORMAL).get_size() / Pango.SCALE
        self.assertEqual(size, 9)  # 12 px

    def test_one_prompt_at_a_time_and_reopen_resets(self):
        first = self.show(self.prompt())
        first_events = self.record(first)
        first.set_query('item 3')
        second = self.show(self.prompt())
        self.assertEqual(first_events, [('closed',)])
        self.assertIs(self.host.prompt, second)
        self.assertIsNone(first.get_parent())
        self.assertTrue(second.entry.has_focus())
        send_key(second.entry, Gdk.KEY_Escape)
        self.assertTrue(self.text.has_focus())
        self.show(first)
        self.assertEqual(first.query, '')
        self.assertEqual(len(first.items), 5)
        self.assertEqual(first.selected_index, 0)

    def test_set_provider_and_refresh(self):
        prompt = self.show(self.prompt())
        prompt.set_provider(lambda query: [PromptItem('other', hotkey=('Ctrl+K', 'F2'))])
        self.assertEqual([item.label for item in prompt.items], ['other'])
        chips = [chip for chip in prompt._rows[0].chips.get_children() if chip.get_visible()]
        self.assertEqual([chip.get_text() for chip in chips], ['Ctrl+K', 'F2'])
        self.assertTrue(prompt._rows[0].chips.get_visible())
        self.assertFalse(prompt._rows[1].get_visible())


class SwitcherAndPaletteTest(PromptCase):
    def test_quick_switcher_lists_recent_first_then_alphabetical(self):
        files = ['/r/src/b.py', '/r/a.md', '/r/Zeta.md', '/r/src/plan/Plan.md', '/elsewhere/x.md']
        switcher = QuickSwitcher(lambda: files, root='/r', recent_provider=lambda: ['/r/Zeta.md'])
        self.addCleanup(switcher.destroy)
        self.show(switcher)
        self.assertEqual([(item.label, item.detail) for item in switcher.items],
                         [('Zeta.md', ''), ('x.md', '/elsewhere'), ('a.md', ''),
                          ('b.py', 'src'), ('Plan.md', 'src/plan')])
        events = self.record(switcher)
        switcher.set_query('pla')
        first = switcher.items[0]
        self.assertEqual((first.label, first.label_ranges), ('Plan.md', [(0, 3)]))
        self.assertEqual(switcher._rows[0].title.get_label(), '<b>Pla</b>n.md')
        send_key(switcher.entry, Gdk.KEY_Return, CTRL)
        self.assertEqual(events, [('closed',), ('chosen', '/r/src/plan/Plan.md', int(CTRL))])

    def test_quick_switcher_without_recent_is_alphabetical_and_reloads(self):
        files = ['b.md', 'A.md', 'c/a.md']
        switcher = QuickSwitcher(lambda: list(files))
        self.addCleanup(switcher.destroy)
        self.show(switcher)
        self.assertEqual([item.data for item in switcher.items], ['A.md', 'b.md', 'c/a.md'])
        send_key(switcher.entry, Gdk.KEY_Escape)
        files.append('0.md')
        self.show(switcher)
        self.assertEqual(switcher.items[0].data, '0.md')
        texts = [child.get_text() for child in switcher._instructions.get_children()]
        self.assertIn('ctrl ↵ to open in new tab', texts)

    def test_quick_switcher_twenty_thousand_files(self):
        paths = make_paths(20000)
        switcher = QuickSwitcher(lambda: paths)
        self.addCleanup(switcher.destroy)
        start = time.perf_counter()
        self.show(switcher)
        opened = time.perf_counter() - start
        worst = 0.0
        for query in ['p', 'pl', 'pla', 'plan', 'plan m', 'e']:
            start = time.perf_counter()
            switcher.set_query(query)
            worst = max(worst, time.perf_counter() - start)
        self.assertEqual(len(switcher.items), MAX_RESULTS)
        # Measured ~0.25 s to open (index build) and <0.1 s per keystroke.
        self.assertLess(opened, 3.0)
        self.assertLess(worst, 1.5)

    def test_command_palette_runs_callback_after_close_and_tracks_recent(self):
        calls = []
        commands = [Command('File: Save', lambda: calls.append(('save', self.text.has_focus())), 'Ctrl+S'),
                    Command('View: Toggle explorer', lambda: calls.append(('explorer', None)), 'Ctrl+B'),
                    Command('Editor: Toggle comment', lambda: calls.append(('comment', None)), 'Ctrl+/')]
        palette_widget = CommandPalette(lambda: commands)
        self.addCleanup(palette_widget.destroy)
        run = []
        palette_widget.connect('command-run', lambda _p, command: run.append(command.label))
        self.show(palette_widget)
        self.assertEqual([item.label for item in palette_widget.items],
                         ['File: Save', 'View: Toggle explorer', 'Editor: Toggle comment'])
        chips = palette_widget._rows[0].chips.get_children()
        self.assertEqual(chips[0].get_text(), 'Ctrl+S')
        palette_widget.set_query('tog com')
        self.assertEqual(palette_widget.items[0].label, 'Editor: Toggle comment')
        send_key(palette_widget.entry, Gdk.KEY_Return)
        self.assertEqual(calls, [('comment', None)])
        self.assertEqual(run, ['Editor: Toggle comment'])
        self.show(palette_widget)
        self.assertEqual(palette_widget.items[0].label, 'Editor: Toggle comment')
        palette_widget.set_query('save')
        send_key(palette_widget.entry, Gdk.KEY_Return)
        self.assertEqual(calls[-1], ('save', True))
        self.assertEqual(palette_widget.recent, ['File: Save', 'Editor: Toggle comment'])
        self.show(palette_widget)
        self.assertEqual([item.label for item in palette_widget.items][:2],
                         ['File: Save', 'Editor: Toggle comment'])
        palette_widget.set_query('qqq')
        self.assertEqual(palette_widget.empty.get_text(), 'No commands found.')


if __name__ == '__main__':
    unittest.main()
