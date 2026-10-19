"""Ratio-based pane layout and real, independently draggable GTK handles."""
from __future__ import annotations

import math

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk

HANDLE_SIZE = 6


def sizes_from_ratios(ratios, extent, minimum=0):
    """Apportion integer pixels, clamping small panes when space permits.

    Below the combined minimum, share the available space equally. Rounding
    preserves the total, including tiny/zero allocations during dock collapse.
    """
    count = len(ratios)
    if not count:
        return []
    extent = max(0, int(extent))
    floor = min(max(0, minimum), extent / count)
    weights = [max(0, float(r)) if math.isfinite(r) else 0 for r in ratios]
    pending, result, remaining = list(range(count)), [0.0] * count, float(extent)
    while pending:
        total = sum(weights[i] for i in pending)
        proposed = {i: remaining * weights[i] / total if total else remaining / len(pending)
                    for i in pending}
        small = [i for i in pending if proposed[i] < floor]
        if not small:
            for i in pending:
                result[i] = proposed[i]
            break
        for i in small:
            result[i] = floor
            remaining -= floor
            pending.remove(i)
    pixels = [math.floor(size) for size in result]
    order = sorted(range(count), key=lambda i: result[i] - pixels[i], reverse=True)
    for i in order[:extent - sum(pixels)]:
        pixels[i] += 1
    return pixels


def move_boundary(sizes, index, delta, minimum):
    """Move just one boundary, preserving its pair's total and every other size."""
    result = list(sizes)
    total = sizes[index] + sizes[index + 1]
    floor = min(minimum, total // 2)
    result[index] = max(floor, min(total - floor, round(sizes[index] + delta)))
    result[index + 1] = total - result[index]
    return result


class SplitLayout:
    """Session-only weights; independent of GTK visibility and allocations."""

    def __init__(self):
        self.ratios = []

    def equalize(self):
        self.ratios = [1.0] * len(self.ratios)

    def insert(self, index):
        self.ratios.insert(index, 1.0)
        self.equalize()

    def remove(self, index):
        removed = self.ratios.pop(index)
        neighbours = [i for i in (index - 1, index) if 0 <= i < len(self.ratios)]
        total = sum(self.ratios[i] for i in neighbours)
        for i in neighbours:
            self.ratios[i] += removed * (self.ratios[i] / total if total else 1 / len(neighbours))


class SplitHandle(Gtk.EventBox):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self._drag = None
        self.set_can_focus(True)
        self.set_no_show_all(True)
        self.get_style_context().add_class("idgent-pane-handle")
        orientation = (Gtk.Orientation.HORIZONTAL if owner.vertical else Gtk.Orientation.VERTICAL)
        self.line = Gtk.Separator(orientation=orientation)
        self.line.get_style_context().add_class("idgent-terminal-sep")
        if owner.vertical:
            self.line.set_valign(Gtk.Align.CENTER)
        else:
            self.line.set_halign(Gtk.Align.CENTER)
        self.add(self.line)
        self.line.show()
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK |
                        Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.ENTER_NOTIFY_MASK |
                        Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.connect("realize", self._realized)
        self.connect("button-press-event", self._press)
        self.connect("motion-notify-event", self._motion)
        self.connect("button-release-event", self._release)
        self.connect("key-press-event", self._key)
        self.connect("unmap", self._cancel)
        self.connect("destroy", self._cancel)
        self.set_tooltip_text("Drag to resize panes; double-click to equalize")
        self.get_accessible().set_name("Resize panes")

    def _realized(self, *_args):
        self.get_window().set_cursor(Gdk.Cursor.new_from_name(
            self.get_display(), "row-resize" if self.owner.vertical else "col-resize"))

    def _coordinate(self, event):
        return event.y_root if self.owner.vertical else event.x_root

    def _press(self, _widget, event):
        if event.button != 1:
            return False
        self.grab_focus()
        if event.type == Gdk.EventType.DOUBLE_BUTTON_PRESS:
            self._cancel()
            self.owner.equalize()
        else:
            self._drag = self._coordinate(event), self.owner.pane_sizes()
            self.set_state_flags(Gtk.StateFlags.ACTIVE, False)
            self.grab_add()
        return True

    def _motion(self, _widget, event):
        if self._drag is None:
            return False
        origin, sizes = self._drag
        self.owner.move_handle(self, self._coordinate(event) - origin, sizes)
        return True

    def _cancel(self, *_args):
        self._drag = None
        if self.has_grab():
            self.grab_remove()
        self.unset_state_flags(Gtk.StateFlags.ACTIVE)

    def _release(self, _widget, event):
        if event.button != 1 or self._drag is None:
            return False
        self._motion(self, event)
        self._cancel()
        return True

    def _key(self, _widget, event):
        keys = ((Gdk.KEY_Up, Gdk.KEY_Down) if self.owner.vertical else
                (Gdk.KEY_Left, Gdk.KEY_Right))
        if event.keyval not in keys:
            return False
        self.owner.move_handle(self, -16 if event.keyval == keys[0] else 16)
        return True


class PaneSplit(Gtk.Container):
    """Children are panes; handles are internal children outside ResizeGates."""

    def __init__(self, orientation=Gtk.Orientation.HORIZONTAL, minimum=160):
        super().__init__()
        self.set_has_window(False)
        self.orientation = orientation
        self.vertical = orientation == Gtk.Orientation.VERTICAL
        self.minimum = minimum
        self.layout = SplitLayout()
        self.panes = []
        self.handles = []

    def get_orientation(self):
        return self.orientation

    def do_forall(self, include_internals, callback, callback_data=None):
        for child in list(getattr(self, "panes", [])):
            callback(child)
        if include_internals:
            for child in list(getattr(self, "handles", [])):
                callback(child)

    def do_add(self, child):
        self.insert(child, len(self.panes))

    def insert(self, child, index):
        self.panes.insert(index, child)
        self.layout.insert(index)
        child.set_parent(self)
        self._rebuild_handles()
        self.queue_resize()

    def do_remove(self, child):
        if child in self.panes:
            index = self.panes.index(child)
            self.panes.pop(index)
            self.layout.remove(index)
            child.unparent()
            self._rebuild_handles()
            self.queue_resize()
        elif child in self.handles:
            self.handles.remove(child)
            child.unparent()

    def _rebuild_handles(self):
        for handle in list(self.handles):
            handle.destroy()
        self.handles = []
        for _ in range(max(0, len(self.panes) - 1)):
            handle = SplitHandle(self)
            handle.set_parent(self)
            self.handles.append(handle)
        self.sync_handles()

    def sync_handles(self):
        for i, handle in enumerate(self.handles):
            handle.set_visible(self.panes[i].get_visible() and self.panes[i + 1].get_visible())

    def _preferred(self, vertical):
        visible = [p for p in self.panes if p.get_visible()]
        requests = [p.get_preferred_height() if vertical else p.get_preferred_width()
                    for p in visible]
        if not requests:
            return 0, 0
        if vertical == self.vertical:
            # Permit outer dock collapse, as with the existing ResizeGate.
            return 0, sum(r[1] for r in requests) + HANDLE_SIZE * (len(visible) - 1)
        return max(r[0] for r in requests), max(r[1] for r in requests)

    def do_get_preferred_width(self):
        return self._preferred(False)

    def do_get_preferred_height(self):
        return self._preferred(True)

    def pane_sizes(self):
        allocation = self.get_allocation()
        extent = allocation.height if self.vertical else allocation.width
        return sizes_from_ratios(self.layout.ratios,
                                 extent - HANDLE_SIZE * len(self.handles), self.minimum)

    def equalize(self):
        self.layout.equalize()
        self.queue_resize()

    def move_handle(self, handle, delta, sizes=None):
        if handle not in self.handles or not handle.get_visible():
            return
        self.layout.ratios = move_boundary(self.pane_sizes() if sizes is None else sizes,
                                          self.handles.index(handle), delta, self.minimum)
        self.queue_resize()

    def do_size_allocate(self, allocation):
        self.set_allocation(allocation)
        self.sync_handles()
        visible = [p for p in self.panes if p.get_visible()]
        if not visible:
            return
        sizes = self.pane_sizes() if len(visible) == len(self.panes) else [
            allocation.height if self.vertical else allocation.width] * len(visible)
        offset = 0
        for pane, size in zip(visible, sizes):
            rect = Gdk.Rectangle()
            rect.x, rect.y = allocation.x, allocation.y
            rect.width, rect.height = max(1, allocation.width), max(1, allocation.height)
            if self.vertical:
                rect.y += offset
                rect.height = max(1, size)
            else:
                rect.x += offset
                rect.width = max(1, size)
            pane.size_allocate(rect)
            offset += size
            index = self.panes.index(pane)
            if index < len(self.handles) and self.handles[index].get_visible():
                if self.vertical:
                    rect.y = allocation.y + offset
                    rect.height = HANDLE_SIZE
                else:
                    rect.x = allocation.x + offset
                    rect.width = HANDLE_SIZE
                self.handles[index].size_allocate(rect)
                offset += HANDLE_SIZE
