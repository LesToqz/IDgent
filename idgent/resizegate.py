"""A clipped GTK container which paces allocation of its single child.

Wrap the complete terminal page so terminal and scrollbar keep their committed
geometry together.
"""
from __future__ import annotations

import array
import fcntl
import termios

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk


class ResizeGate(Gtk.Bin):
    """Follow our size live, but let a terminal's program answer each resize.

    Every new allocation is committed to the child at once, unless a VTE
    descendant changed its grid in the previous commit and its program has not
    redrawn yet: a shell's SIGWINCH redraw is computed for one width, and VTE
    would otherwise interpret it at the next one (overwritten prompts, erased
    history lines, stacked copies of a prompt's first row). Such a commit waits
    until the program has written to its PTY and VTE has read all of it, or
    ``PACE_MS`` for a program which does not redraw. VTE's own
    ``contents-changed`` for the resize is not an answer: it arrives a frame
    later even when the program has written nothing. Meanwhile the private Gdk
    window clips the child, or our background fills the extra space.

    ``commit()`` applies the latest allocation immediately; it never changes
    terminal contents or sends input to a child process.

    VTE descendants use non-reflow allocation: readline redraws its prompt
    relative to the old grid after a SIGWINCH, which overwrites or duplicates
    the prompt when VTE has also reflowed it. This preserves history bytes but
    leaves old lines at their original wrapping. ``REFLOW_VTE = True`` opts out.
    """

    PACE_MS = 50
    # Application layouts can retain the wrapped page's original size requests.
    CHILD_MINIMUM = False
    # Suppress VTE reflow for the committed allocation, then restore the caller's
    # setting. Set True on an instance to retain normal reflow instead.
    REFLOW_VTE = False

    def __init__(self):
        super().__init__()
        self.set_has_window(True)
        self._target = None
        self._committed = None
        self._allocated = False
        self._timer = None
        # Terminals whose program has not yet answered our last grid change.
        self._answers = []
        # Terminal -> PTY watch source until its program writes, then None.
        self._output = {}
        self._release = None
        self._destroyed = False
        self._background_provider = None
        # Let GTK paint CSS in C, so this works without python3-gi-cairo. This
        # internal widget is not the Bin's public child and receives no input.
        self._background = Gtk.Box()
        self._background.get_style_context().add_class("background")
        self._background.get_style_context().add_class("idgent-resize-gate")
        self._background.set_parent(self)
        self._background.show()
        context = self.get_style_context()
        context.add_class("background")
        context.add_class("idgent-resize-gate")
        self.connect("destroy", self._on_destroy)
        self.connect("add", self._child_changed)
        self.connect("remove", self._child_changed)

    def do_forall(self, include_internals, callback, callback_data=None):
        background = getattr(self, "_background", None)
        if include_internals and background is not None:
            callback(background)
        child = self.get_child()
        if child is not None:
            callback(child)

    def do_remove(self, child):
        if child is getattr(self, "_background", None):
            child.unparent()
            self._background = None
        else:
            Gtk.Bin.do_remove(self, child)

    def do_get_preferred_width(self):
        child = self.get_child()
        return (child.get_preferred_width() if self.CHILD_MINIMUM else
                (0, child.get_preferred_width()[1])) if child and child.get_visible() else (0, 0)

    def do_get_preferred_height(self):
        child = self.get_child()
        return (child.get_preferred_height() if self.CHILD_MINIMUM else
                (0, child.get_preferred_height()[1])) if child and child.get_visible() else (0, 0)

    def do_get_preferred_width_for_height(self, height):
        child = self.get_child()
        if not child or not child.get_visible():
            return 0, 0
        minimum, natural = child.get_preferred_width_for_height(max(1, height))
        return minimum if self.CHILD_MINIMUM else 0, natural

    def do_get_preferred_height_for_width(self, width):
        child = self.get_child()
        if not child or not child.get_visible():
            return 0, 0
        minimum, natural = child.get_preferred_height_for_width(max(1, width))
        return minimum if self.CHILD_MINIMUM else 0, natural

    def do_realize(self):
        self.set_realized(True)
        allocation = self.get_allocation()
        attributes = Gdk.WindowAttr()
        attributes.window_type = Gdk.WindowType.CHILD
        attributes.wclass = Gdk.WindowWindowClass.INPUT_OUTPUT
        attributes.x, attributes.y = allocation.x, allocation.y
        attributes.width, attributes.height = max(1, allocation.width), max(1, allocation.height)
        attributes.visual = self.get_visual()
        attributes.event_mask = self.get_events() | Gdk.EventMask.EXPOSURE_MASK
        window = Gdk.Window.new(self.get_parent_window(), attributes,
                                Gdk.WindowAttributesType.X | Gdk.WindowAttributesType.Y |
                                Gdk.WindowAttributesType.VISUAL)
        self.set_window(window)
        window.set_user_data(self)
        self.commit()

    def do_unrealize(self):
        self._cancel_timer()
        self._stop_waiting()
        Gtk.Bin.do_unrealize(self)

    def do_size_allocate(self, allocation):
        # GTK/GDK require positive window sizes, including when a paned collapses.
        safe = Gdk.Rectangle()
        safe.x, safe.y = allocation.x, allocation.y
        safe.width, safe.height = max(1, allocation.width), max(1, allocation.height)
        self.set_allocation(safe)
        if self.get_realized():
            self.get_window().move_resize(safe.x, safe.y, safe.width, safe.height)
        background = Gdk.Rectangle()
        background.x = background.y = 0
        background.width, background.height = safe.width, safe.height
        self._background.size_allocate(background)
        target = safe.width, safe.height
        self._target = target
        first = not self._allocated
        self._allocated = True
        if first or self._committed is None or (target != self._committed and not self._answers):
            self.commit()
        else:
            # Hold the committed geometry until the terminal has answered, or
            # honour a child's queue_resize()/show() without bypassing the gate.
            self._allocate_child()

    def _allocate_child(self):
        child = self.get_child()
        if child is None or not child.get_visible() or self._committed is None:
            return
        allocation = Gdk.Rectangle()
        allocation.x = allocation.y = 0
        # GtkWidget subtracts margins *after* size_allocate and warns on a
        # negative content size. Keep the child usable when the gate collapses;
        # our own window still clips it to one pixel.
        allocation.width = max(self._committed[0], child.get_preferred_width()[0],
                               child.get_margin_start() + child.get_margin_end() + 1)
        allocation.height = max(self._committed[1], child.get_preferred_height()[0],
                                child.get_margin_top() + child.get_margin_bottom() + 1)
        terminals = []

        def collect(widget):
            # Property access avoids importing VTE into this GTK container
            # and the Python deprecation warning on its equivalent methods.
            if widget.find_property("rewrap-on-resize") is not None:
                terminals.append(widget)
            if isinstance(widget, Gtk.Container):
                for descendant in widget.get_children():
                    collect(descendant)
        collect(child)
        grids = [(t.get_column_count(), t.get_row_count()) for t in terminals]
        reflowing = []
        if not self.REFLOW_VTE:
            for terminal in terminals:
                if terminal.get_property("rewrap-on-resize"):
                    reflowing.append(terminal)
                    terminal.set_property("rewrap-on-resize", False)
        try:
            child.size_allocate(allocation)
        finally:
            for terminal in reflowing:
                terminal.set_property("rewrap-on-resize", True)
        changed = [t for t, grid in zip(terminals, grids)
                   if (t.get_column_count(), t.get_row_count()) != grid and t.get_mapped()
                   and t.get_pty() is not None]
        # An empty terminal has no program to answer SIGWINCH. In particular,
        # do not pace startup allocations before its deferred shell is born.
        if changed:
            self._wait_for(changed)

    def _wait_for(self, terminals):
        """Hold further commits until each terminal's program has redrawn."""
        self._stop_waiting()
        for terminal in terminals:
            handler = terminal.connect("contents-changed", self._answered)
            self._answers.append((terminal, handler))
            # VTE reads its PTY from the main loop, after a high-priority watch
            # on the same descriptor has seen the program's first bytes. Without
            # a PTY, any parsed (fed) output is the answer.
            pty = terminal.get_pty()
            self._output[terminal] = None if pty is None else GLib.unix_fd_add_full(
                GLib.PRIORITY_HIGH, pty.get_fd(), GLib.IOCondition.IN, self._wrote, terminal)
        self._release = GLib.timeout_add(max(1, int(self.PACE_MS)), self._released)

    def _wrote(self, _fd, _condition, terminal):
        if terminal in self._output:
            self._output[terminal] = None
        return GLib.SOURCE_REMOVE

    def _stop_waiting(self):
        for terminal, handler in self._answers:
            if terminal.handler_is_connected(handler):
                terminal.disconnect(handler)
        self._answers = []
        for source in self._output.values():
            if source is not None:
                GLib.source_remove(source)
        self._output = {}
        if self._release is not None:
            GLib.source_remove(self._release)
            self._release = None

    @staticmethod
    def _unread(terminal):
        """Bytes the program wrote which VTE has not read yet."""
        pty = terminal.get_pty()
        if pty is None:
            return 0
        count = array.array("i", [0])
        try:
            fcntl.ioctl(pty.get_fd(), termios.FIONREAD, count)
        except OSError:
            return 0
        return count[0]

    def _answered(self, terminal):
        # Ignore VTE's signal for the resize itself, and an answer VTE has
        # only partly parsed: the rest would be interpreted at the next grid.
        if self._output.get(terminal, None) is not None or self._unread(terminal):
            return
        for index, (other, handler) in enumerate(self._answers):
            if other is terminal:
                terminal.disconnect(handler)
                del self._answers[index]
                break
        if not self._answers:
            self._stop_waiting()
            # VTE emits this while it parses child output; resize it afterwards,
            # still before the next frame is painted.
            self._timer = self._timer or GLib.idle_add(self._settled, priority=GLib.PRIORITY_HIGH_IDLE)

    def _released(self):
        self._release = None
        self._stop_waiting()
        self._settled()
        return GLib.SOURCE_REMOVE

    def _cancel_timer(self):
        if self._timer is not None:
            GLib.source_remove(self._timer)
            self._timer = None

    def _settled(self):
        self._timer = None
        if not self._answers and self._target is not None and self._target != self._committed:
            self.commit()
        return GLib.SOURCE_REMOVE

    def commit_when_answered(self):
        """Apply the latest size now, or once the terminals have redrawn."""
        if not self._answers:
            self.commit()

    def commit(self):
        """Apply the latest size now, cancelling any outstanding timeout."""
        self._cancel_timer()
        if self._destroyed:
            return
        if self._target is None:
            allocation = self.get_allocation()
            self._target = max(1, allocation.width), max(1, allocation.height)
        self._committed = self._target
        self._allocate_child()
        self.queue_draw()

    def _child_changed(self, _gate, _child):
        self._cancel_timer()
        self._stop_waiting()
        self._committed = None
        self.queue_resize()

    def _on_destroy(self, _widget):
        self._destroyed = True
        self._cancel_timer()
        self._stop_waiting()

    def set_background(self, rgba_string):
        """Set this gate's padding colour, accepting any Gdk.RGBA string."""
        color = Gdk.RGBA()
        if not color.parse(rgba_string):
            raise ValueError(f"Invalid background colour: {rgba_string!r}")
        provider = Gtk.CssProvider()
        provider.load_from_data(
            (".idgent-resize-gate { background-color: " + color.to_string() + "; }").encode())
        context = self.get_style_context()
        if self._background_provider is not None:
            context.remove_provider(self._background_provider)
            self._background.get_style_context().remove_provider(self._background_provider)
        context.add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._background.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._background_provider = provider
        self.queue_draw()
