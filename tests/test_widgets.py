"""Reusable settings widgets; run with GDK_BACKEND=x11 under xvfb."""
import time
import unittest

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('Atk', '1.0')
from gi.repository import Atk, Gdk, GLib, Gtk

from idgent import motion, style
from idgent.themes import THEMES, get_theme
from idgent.widgets import (THUMB_INSET, THUMB_TRAVEL, CenteredColumn, HotkeyChip, NavList,
                            SearchField, SettingCard, SettingRow, SwatchPicker, Toggle, WrapBox,
                            build_css)


def pump(ms=0):
    """Run the main loop for at least ms milliseconds (and until idle)."""
    end = time.monotonic() + ms / 1000
    while True:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        if time.monotonic() >= end:
            return
        GLib.usleep(2000)


def send_key(widget, keyval, state=0):
    window = widget.get_toplevel().get_window()
    event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
    event.window = window
    event.keyval = keyval
    event.state = Gdk.ModifierType(state)
    keys = Gdk.Keymap.get_for_display(window.get_display()).get_entries_for_keyval(keyval)[1]
    event.hardware_keycode = keys[0].keycode if keys else 0
    event.set_device(window.get_display().get_default_seat().get_keyboard())
    event.time = Gtk.get_current_event_time()
    Gtk.main_do_event(event)
    pump()


class WidgetTest(unittest.TestCase):
    def setUp(self):
        style.apply_theme(get_theme('idgent-dark'))
        self.window = Gtk.Window()
        self.window.set_default_size(500, 300)
        self.addCleanup(self.window.destroy)
        self.addCleanup(motion.set_reduce_motion, False)

    def show(self, child):
        self.window.add(child)
        self.window.show_all()
        pump(50)


class ToggleTest(WidgetTest):
    def test_geometry_role_and_animated_thumb(self):
        box = Gtk.Box()
        toggle = Toggle(label='Line numbers')
        box.pack_start(toggle, False, False, 0)
        self.show(box)
        alloc = toggle.get_allocation()
        self.assertEqual((alloc.width, alloc.height), (40, 22))
        thumb = toggle.thumb.get_allocation()
        self.assertEqual((thumb.width, thumb.height), (18, 18))
        self.assertEqual(thumb.x - alloc.x, THUMB_INSET)
        self.assertEqual(toggle.get_accessible().get_role(), Atk.Role.SWITCH)
        self.assertEqual(toggle.get_accessible().get_name(), 'Line numbers')
        if not motion.animations_enabled():
            self.skipTest('animations disabled in this environment')
        toggle.set_active(True)
        pump(60)
        middle = toggle.thumb_offset
        self.assertGreater(middle, THUMB_INSET)
        self.assertLess(middle, THUMB_INSET + THUMB_TRAVEL)
        pump(140)
        self.assertEqual(toggle.thumb_offset, THUMB_INSET + THUMB_TRAVEL)
        pump(20)
        self.assertEqual(toggle.thumb.get_allocation().x - toggle.get_allocation().x, 20)
        self.assertEqual(THUMB_TRAVEL, 18)  # Symmetric 2 px insets in a 40 px track.

    def test_reduce_motion_and_unmapped_toggles_jump(self):
        hidden = Toggle(active=True)
        self.assertEqual(hidden.thumb_offset, 20)
        hidden.set_active(False)
        self.assertEqual(hidden.thumb_offset, 2)
        box = Gtk.Box()
        toggle = Toggle()
        box.pack_start(toggle, False, False, 0)
        self.show(box)
        motion.set_reduce_motion(True)
        toggle.set_active(True)
        self.assertEqual(toggle.thumb_offset, 20)

    def test_space_and_enter_toggle_with_signal(self):
        box = Gtk.Box()
        toggle = Toggle()
        box.pack_start(toggle, False, False, 0)
        self.show(box)
        events = []
        toggle.connect('toggled', lambda t: events.append(t.get_active()))
        self.assertTrue(toggle.get_can_focus())
        toggle.grab_focus()
        send_key(toggle, Gdk.KEY_space)
        pump(250)
        send_key(toggle, Gdk.KEY_Return)
        pump(250)
        self.assertEqual(events, [True, False])
        self.assertFalse(toggle.props.active)


class RowsTest(WidgetTest):
    def test_row_search_reset_and_activation(self):
        resets = []
        toggle = Toggle()
        row = SettingRow('Accent color', 'Colour of toggles.', toggle, key='accent',
                         aliases=('highlight brand',), reset=lambda: resets.append(1))
        self.assertTrue(row.matches('ACCENT'))
        self.assertTrue(row.matches('brand col'))
        self.assertTrue(row.matches('toggles'))
        self.assertFalse(row.matches('terminal'))
        row.reset_button.clicked()
        self.assertEqual(resets, [1])
        self.assertTrue(row.activate_control())
        self.assertTrue(toggle.get_active())
        self.assertEqual(toggle.get_accessible().get_name(), 'Accent color')

    def test_card_dividers_filter_and_radius(self):
        card = SettingCard()
        rows = [SettingRow(name, 'Description') for name in ('Alpha', 'Beta', 'Gamma')]
        for row in rows:
            card.add(row)
        self.show(card)
        self.assertIsNone(rows[0].get_header())
        for row in rows[1:]:
            header = row.get_header()
            self.assertTrue(header.get_style_context().has_class('idgent-card-divider'))
            alloc = header.get_allocation()
            self.assertEqual(alloc.height, 1)
            self.assertEqual(alloc.x - card.get_allocation().x, 20)
            self.assertEqual(alloc.width, card.get_allocation().width - 40)
        card.set_query('beta')
        pump()
        self.assertEqual(card.visible_rows(), [rows[1]])
        self.assertIsNone(rows[1].get_header())
        card.set_query('')
        self.assertEqual(card.visible_rows(), rows)
        height = rows[0].get_allocated_height()
        self.assertTrue(70 <= height <= 82, height)

    def test_hotkey_chips_wrap(self):
        wrap = WrapBox()
        chips = [HotkeyChip(f'Ctrl+Shift+{n}') for n in range(12)]
        for chip in chips:
            wrap.add(chip)
        self.window.set_size_request(300, -1)
        self.show(wrap)
        widths = [c.get_allocated_width() for c in chips]
        self.assertEqual(widths, [c.get_preferred_width()[1] for c in chips])
        lines = {c.get_allocation().y for c in chips}
        self.assertGreater(len(lines), 1)
        self.assertTrue(all(c.get_allocation().x + c.get_allocated_width()
                            <= wrap.get_allocation().x + wrap.get_allocated_width() for c in chips))
        wrap.remove(chips[0])
        self.assertNotIn(chips[0], wrap.get_children())


class NavAndColumnTest(WidgetTest):
    def test_nav_rows_and_vim_keys(self):
        nav = NavList()
        for page in ('one', 'two', 'three'):
            nav.add_page(page, page.title(), 'folder-symbolic')
        self.show(nav)
        for row in nav.rows.values():
            # The allocation includes the 2 px CSS gap below each row.
            height = row.get_allocated_height() - 2
            self.assertTrue(24 <= height <= 26, height)
        self.assertEqual(nav.rows['two'].get_allocation().y - nav.rows['one'].get_allocation().y,
                         nav.rows['one'].get_allocated_height())
        nav.select_page('one')
        nav.rows['one'].grab_focus()
        send_key(nav, Gdk.KEY_j)
        self.assertEqual(nav.page_id, 'two')
        send_key(nav, Gdk.KEY_G)
        self.assertEqual(nav.page_id, 'three')
        send_key(nav, Gdk.KEY_k)
        self.assertEqual(nav.page_id, 'two')
        send_key(nav, Gdk.KEY_g)
        send_key(nav, Gdk.KEY_g)
        self.assertEqual(nav.page_id, 'one')
        send_key(nav, Gdk.KEY_Down)
        self.assertEqual(nav.page_id, 'two')
        nav.rows['three'].set_count(4)
        self.assertEqual(nav.rows['three'].count_label.get_text(), '4')

    def test_centered_column(self):
        column = CenteredColumn()
        child = Gtk.Label(label='x')
        column.add(child)
        self.window.resize(1000, 300)
        self.show(column)
        alloc = child.get_allocation()
        self.assertEqual(alloc.width, 700)
        self.assertEqual(alloc.x, 150)
        self.assertEqual(alloc.y, 48)
        self.window.resize(600, 300)
        pump(100)
        alloc = child.get_allocation()
        self.assertEqual((alloc.x, alloc.width), (32, 536))

    def test_swatches_and_search_field(self):
        picker = SwatchPicker(('#111111', '#222222'))
        events = []
        picker.connect('value-changed', lambda _, v: events.append(v))
        picker.buttons['#222222'].clicked()
        picker.buttons['#222222'].clicked()
        self.assertEqual((picker.value, events), ('#222222', ['#222222']))
        self.assertTrue(picker.buttons['#222222'].get_active())
        picker.set_value(None)
        self.assertEqual((picker.value, events), (None, ['#222222']))
        self.assertFalse(any(b.get_active() for b in picker.buttons.values()))
        field = SearchField()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.pack_start(field, False, False, 0)
        self.show(box)
        self.assertEqual(field.get_allocated_height(), 30)


class CssTest(unittest.TestCase):
    def test_css_parses_for_every_theme(self):
        for theme in THEMES:
            provider = Gtk.CssProvider()
            errors = []
            provider.connect('parsing-error', lambda _, section, error: errors.append(str(error)))
            provider.load_from_data(build_css(theme.ui).encode())
            self.assertEqual(errors, [], theme.id)
            self.assertNotIn('transition:', build_css(theme.ui))
