"""Pane layout maths, real handle events, and terminal/graph integration."""
from __future__ import annotations

import unittest
from unittest.mock import patch

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk

from idgent.panesplit import HANDLE_SIZE, PaneSplit, SplitLayout, move_boundary, sizes_from_ratios
from idgent.terminal import TerminalPanel
from tests.test_resizegate import poll, spin, setUpModule  # noqa: F401


def pointer(handle, kind, position):
    event = Gdk.Event.new(kind)
    event.window = handle.get_window()
    event.send_event = True
    event.x_root = event.y_root = position
    if kind != Gdk.EventType.MOTION_NOTIFY:
        event.button = 1
    else:
        event.state = Gdk.ModifierType.BUTTON1_MASK
    return handle.event(event)


def drag(handle, delta):
    pointer(handle, Gdk.EventType.BUTTON_PRESS, 500)
    pointer(handle, Gdk.EventType.MOTION_NOTIFY, 500 + delta)
    pointer(handle, Gdk.EventType.BUTTON_RELEASE, 500 + delta)
    spin(80)


class LayoutMathTest(unittest.TestCase):
    def test_apportion_and_scale(self):
        self.assertEqual(sizes_from_ratios([1, 2, 1], 800, 160), [200, 400, 200])
        self.assertEqual(sizes_from_ratios([1, 2, 1], 1600, 160), [400, 800, 400])
        self.assertEqual(sizes_from_ratios([1, 1, 1], 1000), [334, 333, 333])
        self.assertEqual(sizes_from_ratios([], 1000), [])

    def test_minima_and_collapsed_layouts(self):
        self.assertEqual(sizes_from_ratios([0, 1, 4], 800, 160), [160, 160, 480])
        self.assertEqual(sizes_from_ratios([1, 10, 100], 101, 160), [34, 34, 33])
        self.assertEqual(sizes_from_ratios([1, 1], -5, 160), [0, 0])
        self.assertEqual(sizes_from_ratios([0, 0], 321, 160), [161, 160])
        for extent in range(600):
            sizes = sizes_from_ratios([.01, .3, .69], extent, 160)
            self.assertEqual(sum(sizes), extent)
            self.assertGreaterEqual(min(sizes), min(160, extent // 3))

    def test_move_only_pair_and_clamp(self):
        self.assertEqual(move_boundary([200, 300, 400], 0, 50, 160), [250, 250, 400])
        self.assertEqual(move_boundary([200, 300, 400], 0, -10000, 160), [160, 340, 400])
        self.assertEqual(move_boundary([200, 300, 400], 1, 10000, 160), [200, 540, 160])
        self.assertEqual(move_boundary([20, 30], 0, 10000, 160), [25, 25])

    def test_add_resets_and_remove_redistributes_to_neighbours(self):
        layout = SplitLayout()
        for i in range(3):
            layout.insert(i)
        self.assertEqual(layout.ratios, [1, 1, 1])
        layout.ratios = [200, 300, 400]
        layout.remove(1)
        self.assertEqual(layout.ratios, [300, 600])
        layout.remove(0)
        self.assertEqual(layout.ratios, [900])
        layout.insert(0)
        self.assertEqual(layout.ratios, [1, 1])
        layout.remove(1)
        layout.remove(0)
        self.assertEqual(layout.ratios, [])
        layout.ratios = [1, 2, 3, 4]
        layout.remove(1)
        self.assertEqual(layout.ratios, [1.5, 4.5, 4])


class HandleTest(unittest.TestCase):
    def open(self, vertical=False, count=3):
        window = Gtk.Window()
        window.set_default_size(900, 900)
        self.addCleanup(window.destroy)
        split = PaneSplit(Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL,
                          minimum=120 if vertical else 160)
        window.add(split)
        for _ in range(count):
            split.add(Gtk.Entry())
        window.show_all()
        spin(80)
        extent = split.get_allocated_height() if vertical else split.get_allocated_width()
        self.assertEqual(sum(self.sizes(split)) + HANDLE_SIZE * len(split.handles), extent)
        return window, split

    @staticmethod
    def sizes(split):
        return [p.get_allocated_height() if split.vertical else p.get_allocated_width()
                for p in split.panes]

    def test_pointer_keyboard_reset_both_axes_two_and_three_panes(self):
        for vertical in (False, True):
            for count in (2, 3):
                with self.subTest(vertical=vertical, count=count):
                    window, split = self.open(vertical, count)
                    original = self.sizes(split)
                    handle = split.handles[0]
                    self.assertTrue(handle.get_mapped())
                    self.assertTrue(pointer(handle, Gdk.EventType.BUTTON_PRESS, 500))
                    self.assertTrue(handle.has_grab())
                    pointer(handle, Gdk.EventType.MOTION_NOTIFY, 580)
                    spin(50)
                    moved = self.sizes(split)
                    self.assertEqual(moved, [original[0] + 80, original[1] - 80] + original[2:])
                    pointer(handle, Gdk.EventType.BUTTON_RELEASE, 580)
                    self.assertFalse(handle.has_grab())
                    event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
                    event.window = handle.get_window()
                    event.send_event = True
                    event.keyval = Gdk.KEY_Up if vertical else Gdk.KEY_Left
                    handle.event(event)
                    spin(50)
                    self.assertEqual(self.sizes(split), [moved[0] - 16, moved[1] + 16] + moved[2:])
                    drag(handle, -10000)
                    self.assertEqual(self.sizes(split)[0], split.minimum)
                    pointer(handle, Gdk.EventType.DOUBLE_BUTTON_PRESS, 500)
                    spin(50)
                    self.assertEqual(self.sizes(split), original)
                    self.assertFalse(handle.has_grab())
                    window.destroy()

    def test_hover_and_drag_states_are_instant(self):
        window, split = self.open()
        handle = split.handles[0]
        for kind, hovered in ((Gdk.EventType.ENTER_NOTIFY, True),
                              (Gdk.EventType.LEAVE_NOTIFY, False)):
            event = Gdk.Event.new(kind)
            event.window = handle.get_window()
            event.send_event = True
            event.mode = Gdk.CrossingMode.NORMAL
            event.detail = Gdk.NotifyType.ANCESTOR
            handle.event(event)
            self.assertEqual(bool(handle.get_state_flags() & Gtk.StateFlags.PRELIGHT), hovered)
        pointer(handle, Gdk.EventType.BUTTON_PRESS, 500)
        self.assertTrue(handle.get_state_flags() & Gtk.StateFlags.ACTIVE)
        pointer(handle, Gdk.EventType.BUTTON_RELEASE, 500)
        self.assertFalse(handle.get_state_flags() & Gtk.StateFlags.ACTIVE)

    def test_proportional_resize_maximise_restore_and_handle_lifetime(self):
        window, split = self.open()
        drag(split.handles[0], 70)
        ratios = list(split.layout.ratios)
        before = self.sizes(split)
        window.resize(1400, 900)
        spin(80)
        after = self.sizes(split)
        self.assertEqual(split.layout.ratios, ratios)
        for old, new in zip(before, after):
            self.assertAlmostEqual(new / sum(after), old / sum(before), delta=.002)
        for pane in split.panes[1:]:
            pane.set_no_show_all(True)
            pane.hide()
        split.sync_handles()
        window.show_all()
        spin(50)
        self.assertFalse(any(h.get_visible() for h in split.handles))
        self.assertEqual(split.panes[0].get_allocated_width(), split.get_allocated_width())
        for pane in split.panes:
            pane.set_no_show_all(False)
            pane.show()
        split.sync_handles()
        spin(50)
        self.assertEqual(self.sizes(split), after)
        self.assertEqual(split.layout.ratios, ratios)
        old_handles = list(split.handles)
        destroyed = []
        for handle in old_handles:
            handle.connect('destroy', lambda h: destroyed.append(h))
        split.remove(split.panes[1])
        self.assertEqual(destroyed, old_handles)
        self.assertEqual(len(split.get_children()), 2)
        self.assertEqual(len(split.handles), 1)


class PanelSplitTest(unittest.TestCase):
    def open(self, stacked=False):
        window = Gtk.Window()
        window.set_default_size(1000, 900)
        self.addCleanup(window.destroy)
        panel = TerminalPanel('/tmp', autostart=False, stacked=stacked)
        panel.ANIMATION_MS = 0
        window.add(panel)
        window.show_all()
        # Geometry integration without starting unnecessary interactive shells.
        with patch.object(panel, '_defer_spawn'):
            panel.new_terminal()
            panel.new_terminal()
        spin(80)
        return window, panel

    def test_graph_live_resize_state_maximise_close_add_and_fade(self):
        for stacked in (False, True):
            with self.subTest(stacked=stacked):
                window, panel = self.open(stacked)
                panel.graph_factory = Gtk.Entry
                panel._header_focus(panel._panes[0])
                panel.add_graph()
                graph = panel.graph_views()[0]
                graph.set_text('graph state survives resizing')
                spin(80)
                body = panel._body
                self.assertEqual(len(body.handles), 2)
                sizes = body.pane_sizes()
                drag(body.handles[0], 40)
                self.assertEqual(body.pane_sizes(), [sizes[0] + 40, sizes[1] - 40] + sizes[2:])
                sizes = body.pane_sizes()
                drag(body.handles[1], -60)
                self.assertEqual(body.pane_sizes(), sizes[:1] + [sizes[1] - 60, sizes[2] + 60])
                graph_pane = next(p for p in panel._panes if p.kind == 'graph')
                self.assertEqual(graph.get_text(), 'graph state survives resizing')
                self.assertIs(panel.graph_views()[0], graph)
                before = list(body.layout.ratios)
                panel.toggle_maximize(graph_pane)
                spin(80)
                self.assertFalse(any(h.get_visible() for h in body.handles))
                panel.restore()
                spin(80)
                self.assertEqual(body.layout.ratios, before)
                self.assertEqual(graph.get_text(), 'graph state survives resizing')
                # Keep the old slot through fade-out, including add during close.
                panel.ANIMATION_MS = 100
                closing = panel._panes[0]
                with patch.object(panel, '_animated', return_value=True):
                    panel._close_pane(closing)
                self.assertIn(closing, panel._closing)
                self.assertEqual(body.layout.ratios, before)
                self.assertEqual(len(body.panes), 3)
                with patch.object(panel, '_defer_spawn'):
                    panel.new_terminal()
                self.assertEqual(body.layout.ratios, [1] * 4)
                self.assertTrue(poll(lambda: not panel._closing))
                self.assertEqual(len(body.panes), 3)
                self.assertIsNone(closing.frame.get_parent())
                self.assertEqual(len(body.handles), 2)
                panel.ANIMATION_MS = 0
                panel._close_pane(graph_pane)
                spin(80)
                self.assertEqual(panel.pane_count(), 2)
                self.assertEqual(len(body.get_children()), 2)
                self.assertEqual(len(body.handles), 1)
                self.assertGreater(min(body.layout.ratios), 0)
                window.destroy()

    def test_real_webkit_resize_keeps_page_state_and_pause_resume(self):
        import tempfile
        from idgent.graphview import GraphView, _load_webkit
        from tests.test_graphview import FAKE_PAGE, GraphViewTest, web_processes
        try:
            _load_webkit()
        except (ImportError, ValueError):
            self.skipTest('WebKit2 4.1 is unavailable')
        with tempfile.TemporaryDirectory() as directory:
            for stacked in (False, True):
                window, panel = self.open(stacked)
                view = GraphView(directory, page=FAKE_PAGE)
                self.view = view  # Reuse the existing asynchronous JS assertion helper.
                panel.add_graph(view)
                self.assertTrue(poll(lambda: view._ready and view.last_data is not None, 10))
                js = lambda code: GraphViewTest.js(self, code)
                js('window.paneState = {camera: 123, selection: "kept"}; null')
                loads = []
                view.webview.connect('load-changed', lambda *args: loads.append(True))
                before = view.get_allocated_height() if stacked else view.get_allocated_width()
                for index in range(8):
                    panel._body.move_handle(panel._body.handles[1], -8)
                    spin(20)
                after = view.get_allocated_height() if stacked else view.get_allocated_width()
                self.assertGreater(after, before)
                self.assertEqual(js('paneState'), dict(camera=123, selection='kept'))
                panel.toggle_maximize(panel._panes[0])
                spin(80)
                self.assertFalse(view.get_mapped())
                panel.restore()
                spin(80)
                self.assertTrue(view.get_mapped())
                self.assertEqual(js('paneState'), dict(camera=123, selection='kept'))
                self.assertIn('pause', js('calls.map(c => c[0])'))
                self.assertIn('resume', js('calls.map(c => c[0])'))
                self.assertEqual(loads, [])
                # Outer collapse must not reload the WebView either.
                allocation = panel._body.get_allocation()
                allocation.width = allocation.height = 1
                panel._body.size_allocate(allocation)
                panel.queue_resize()
                spin(80)
                self.assertEqual(js('paneState.camera'), 123)
                self.assertEqual(loads, [])
                window.destroy()
                self.assertTrue(poll(lambda: not web_processes(), 6))

    def test_shell_born_after_uneven_layout_uses_final_grid(self):
        # Use the real UTF-8 startup harness and its spawn geometry observation.
        from tests.test_terminal_startup import StartupWindow
        from tests.test_terminal_resize import PROMPT, DockWindow
        dock = StartupWindow()
        self.addCleanup(dock.close)
        self.assertTrue(poll(dock.ready, 10), dock.measurements())
        panel = dock.window.terminal_bottom
        panel.ANIMATION_MS = 0
        panel.new_terminal()
        pane = panel._panes[-1]
        spin(20)
        panel._body.move_handle(panel._body.handles[0], 100)
        self.assertTrue(poll(lambda: pane.pid and PROMPT.strip() in DockWindow.text(pane.terminal), 10))
        spawn = next(s for s in dock.spawns if s['terminal'] is pane.terminal)
        self.assertEqual(spawn['grid'], (pane.terminal.get_column_count(), pane.terminal.get_row_count()),
                         dock.measurements())
        self.assertGreater(panel._body.pane_sizes()[0], panel._body.pane_sizes()[1])


class RealShellSplitTest(unittest.TestCase):
    def test_two_real_terminals_in_each_dock(self):
        from tests.test_terminal_resize import DockWindow, TerminalResizeTest
        dock = DockWindow(bottom=2, right=2, size=(2200, 1200))
        self.addCleanup(dock.close)
        for panel in (dock.window.terminal_bottom, dock.window.terminal_right):
            handle = panel._body.handles[0]
            original = panel._body.pane_sizes()
            drag(handle, -100)
            self.assertEqual(panel._body.pane_sizes(), [original[0] - 100, original[1] + 100])
            for pane in panel._panes:
                TerminalResizeTest.assert_prompt(self, dock, pane, 'two panes narrow')
            drag(handle, 180)
            for pane in panel._panes:
                TerminalResizeTest.assert_prompt(self, dock, pane, 'two panes wide')
                TerminalResizeTest.assert_typing_wraps(self, dock, pane, 'two panes wide')

    def test_real_bash_boundary_cycles_both_docks(self):
        import time
        from tests.test_terminal_resize import DockWindow, TerminalResizeTest
        dock = DockWindow(bottom=3, right=3, size=(2200, 1200))
        self.addCleanup(dock.close)
        started = time.monotonic()
        measurements = []
        for panel in (dock.window.terminal_bottom, dock.window.terminal_right):
            body = panel._body
            for boundary in (0, 1):
                handle = body.handles[boundary]
                initial = body.pane_sizes()
                # Reach the minimum then return; repeat with different motion pacing.
                distance = initial[boundary] - body.minimum
                for step_ms in (4, 16):
                    for end in (-distance, distance):
                        before = body.pane_sizes()
                        pointer(handle, Gdk.EventType.BUTTON_PRESS, 1000)
                        for step in range(1, 21):
                            pointer(handle, Gdk.EventType.MOTION_NOTIFY, 1000 + end * step / 20)
                            if step < 20:
                                spin(step_ms)
                            for pane in dock.panes:
                                state = dock.geometry(pane)
                                self.assertEqual(state['grid'], state['pty'], state)
                        pointer(handle, Gdk.EventType.BUTTON_RELEASE, 1000 + end)
                        moved = time.monotonic()
                        settle = dock.settle(moved, 120)
                        measurements.append(round(settle * 1000, 2))
                        self.assertLessEqual(settle, .1)  # 1–2 frames normally; tolerate CI load.
                        after = body.pane_sizes()
                        for index in range(3):
                            if index not in (boundary, boundary + 1):
                                self.assertEqual(after[index], before[index])
                        self.assertEqual(sum(after), sum(before))
                        for pane in dock.panes:
                            TerminalResizeTest.assert_prompt(self, dock, pane, 'internal boundary')
        for pane in dock.panes:
            TerminalResizeTest.assert_typing_wraps(self, dock, pane, 'internal boundary')
        stale = dock.longest_stale(started)
        print('PANE SPLIT settle_ms', measurements, 'longest_stale_ms', round(stale * 1000, 2),
              flush=True)
        self.assertLess(stale, .15)
        for pane in dock.panes:
            self.assertEqual(pane.frame._target, pane.frame._committed)
            self.assertEqual(pane.frame._answers, [])
