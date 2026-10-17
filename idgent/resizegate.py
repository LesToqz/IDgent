"""A clipped GTK container which debounces allocation of its single child.

Wrap the complete terminal page so terminal and scrollbar keep their committed
geometry together.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk


class ResizeGate(Gtk.Bin):
    """Hold child geometry until our width and height have settled.

    ``SETTLE_MS`` can be overridden on an instance. ``commit()`` applies the
    latest allocation immediately; it never changes terminal contents or sends
    input to a child process. The private Gdk window clips even windowed children
    while the gate is smaller than their last committed allocation.

    VTE descendants temporarily use non-reflow allocation to avoid a second
    readline redraw bug even with one SIGWINCH. This preserves history bytes but
    leaves old lines at their original wrapping. ``REFLOW_VTE = True`` opts out.
    """

    SETTLE_MS = 120
    # Application layouts can retain the wrapped page's original size requests.
    CHILD_MINIMUM = False
    # A single large reflow can also confuse readline's cursor bookkeeping.
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
        changed = target != self._target
        self._target = target
        first = not self._allocated
        self._allocated = True
        if first or self._committed is None:
            self.commit()
        elif changed:
            self._cancel_timer()
            if target != self._committed and not self._destroyed:
                self._timer = GLib.timeout_add(max(1, int(self.SETTLE_MS)), self._settled)
            self._allocate_child()
        else:
            # Honour a child's queue_resize()/show() without bypassing the gate.
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
        if not self.REFLOW_VTE:
            def suspend_reflow(widget):
                # Property access avoids importing VTE into this GTK container
                # and the Python deprecation warning on its equivalent methods.
                if widget.find_property("rewrap-on-resize") is not None:
                    previous = widget.get_property("rewrap-on-resize")
                    if previous:
                        terminals.append(widget)
                        widget.set_property("rewrap-on-resize", False)
                if isinstance(widget, Gtk.Container):
                    for descendant in widget.get_children():
                        suspend_reflow(descendant)
            suspend_reflow(child)
        try:
            child.size_allocate(allocation)
        finally:
            for terminal in terminals:
                terminal.set_property("rewrap-on-resize", True)

    def _cancel_timer(self):
        if self._timer is not None:
            GLib.source_remove(self._timer)
            self._timer = None

    def _settled(self):
        self._timer = None
        self.commit()
        return GLib.SOURCE_REMOVE

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
        self._committed = None
        self.queue_resize()

    def _on_destroy(self, _widget):
        self._destroyed = True
        self._cancel_timer()

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
