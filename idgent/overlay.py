"""In-window overlay layer: one prompt at a time plus a notice stack (spec 4.9, 4.14).

``OverlayHost(child)`` wraps the main content. ``show_prompt(prompt)``
places an ``idgent.palette.Prompt`` per spec 4.14 (no backdrop dim; a
transparent click catcher closes it), focuses its input and restores the
previous focus once the prompt closes. ``notify(text, timeout=4000,
level='info')`` stacks notices at the top right: the newest at the bottom,
at most ``MAX_NOTICES``; each slides in 350 px from the right (100 ms) and
leaves by fading while the stack below moves up (120 ms), both through
``idgent.motion`` (instant when reduced motion is on). Clicking a notice
dismisses it; hovering pauses its timer, leaving restarts a 1000 ms grace.
``timeout=0`` keeps a notice until it is clicked. Component CSS comes from
``build_css(ui)``.
"""
from __future__ import annotations

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, GLib, GObject, Gtk, Pango

from idgent import motion
from idgent.palette import SHADOW_MARGINS, Prompt, prompt_geometry


MAX_NOTICES = 5
NOTICE_PADDING = 10
NOTICE_GAP = 14
NOTICE_MAX_WIDTH = 300
NOTICE_SLIDE = 350
NOTICE_LEVELS = ('info', 'success', 'warning', 'error')
# Transparent room for the notice shadow: (top, right, bottom, left) pixels.
NOTICE_SHADOW = (0, 8, 12, 8)


class Notice(Gtk.EventBox):
    """One toast; created by ``OverlayHost.notify``."""

    def __init__(self, text: str, level: str = 'info'):
        super().__init__(visible_window=False)
        self.level = level
        self.box = Gtk.Box()
        context = self.box.get_style_context()
        context.add_class('idgent-notice')
        context.add_class(f'idgent-notice-{level}')
        top, right, bottom, left = NOTICE_SHADOW
        self.box.set_margin_top(top)
        self.box.set_margin_end(right)
        self.box.set_margin_bottom(bottom)
        self.box.set_margin_start(left)
        self.label = Gtk.Label(label=text, xalign=0, wrap=True,
                               wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.box.add(self.label)
        self.add(self.box)
        self.offset = 0.0      # horizontal slide offset in pixels
        self.collapse = 0.0    # 0 = full slot, 1 = slot removed (exit)
        self.leaving = False
        self.timer = 0
        self.timeout = 0
        self.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)

    @property
    def text(self) -> str:
        return self.label.get_text()

    def stop_timer(self):
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0


class OverlayHost(Gtk.Overlay):
    """Overlay wrapper around the main window content."""
    __gsignals__ = {
        'prompt-shown': (GObject.SignalFlags.RUN_LAST, None, (object,)),
        'prompt-hidden': (GObject.SignalFlags.RUN_LAST, None, (object,)),
    }

    def __init__(self, child: Gtk.Widget | None = None):
        super().__init__()
        self.get_style_context().add_class('idgent-overlay-host')
        if child is not None:
            self.add(child)
        self._prompt: Prompt | None = None
        self._prompt_handler = 0
        self._previous_focus = None
        self._notices: list[Notice] = []
        self._catcher = Gtk.EventBox(visible_window=False)
        self._catcher.get_style_context().add_class('idgent-overlay-catcher')
        self._catcher.connect('button-press-event', self._on_catcher_press)
        self._catcher.connect('scroll-event', lambda *_: True)
        self.connect('get-child-position', self._on_child_position)
        self.connect('destroy', self._on_destroy)

    # Prompt ---------------------------------------------------------------------

    @property
    def prompt(self) -> Prompt | None:
        return self._prompt

    def show_prompt(self, prompt: Prompt):
        """Show ``prompt`` (replacing any other) and focus its input."""
        if prompt is self._prompt:
            prompt.focus_input()
            return
        previous = self._previous_focus
        if self._prompt is not None:
            self._dismiss_prompt(restore=False)
        else:
            toplevel = self.get_toplevel()
            previous = toplevel.get_focus() if isinstance(toplevel, Gtk.Window) else None
        self._previous_focus = previous
        self._prompt = prompt
        if self._catcher.get_parent() is None:
            self.add_overlay(self._catcher)
        self._catcher.show()
        self.add_overlay(prompt)
        for notice in self._notices:
            self.reorder_overlay(notice, -1)
        prompt.prepare()
        prompt.show()
        self._prompt_handler = prompt.connect('closed', self._on_prompt_closed)
        prompt.focus_input()
        self.emit('prompt-shown', prompt)

    def hide_prompt(self):
        """Close the current prompt (emits its ``closed``) and restore focus."""
        if self._prompt is not None:
            self._prompt.close()

    def _on_prompt_closed(self, prompt):
        if prompt is self._prompt:
            self._dismiss_prompt(restore=True)

    def _dismiss_prompt(self, restore: bool):
        prompt = self._prompt
        if prompt is None:
            return
        self._prompt = None
        if self._prompt_handler:
            prompt.disconnect(self._prompt_handler)
            self._prompt_handler = 0
        if prompt.is_open:
            prompt.close()
        if prompt.get_parent() is self:
            self.remove(prompt)
        if restore:
            if self._catcher.get_parent() is self:
                self.remove(self._catcher)
            previous, self._previous_focus = self._previous_focus, None
            if (previous is not None and previous.get_toplevel() is self.get_toplevel()
                    and previous.get_mapped() and previous.get_sensitive()):
                if isinstance(previous, Gtk.Entry):
                    previous.grab_focus_without_selecting()
                else:
                    previous.grab_focus()
        self.emit('prompt-hidden', prompt)

    def _on_catcher_press(self, _widget, _event):
        self.hide_prompt()
        return True

    # Notices --------------------------------------------------------------------

    @property
    def notices(self) -> list[Notice]:
        """Visible notices (not leaving), oldest first."""
        return [notice for notice in self._notices if not notice.leaving]

    def notify(self, text: str, timeout: int = motion.DURATIONS['notice_autohide'],
               level: str = 'info') -> Notice:
        """Show a notice; ``timeout`` ms (0 = until clicked)."""
        if level not in NOTICE_LEVELS:
            raise ValueError(f'unknown notice level {level!r}')
        if timeout < 0:
            raise ValueError('timeout must be nonnegative')
        notice = Notice(text, level)
        notice.timeout = int(timeout)
        notice.connect('button-press-event', lambda widget, _event: self.dismiss(widget) or True)
        notice.connect('enter-notify-event', self._on_notice_enter)
        notice.connect('leave-notify-event', self._on_notice_leave)
        self._notices.append(notice)
        self.add_overlay(notice)
        notice.show_all()
        visible = self.notices
        for old in visible[:max(0, len(visible) - MAX_NOTICES)]:
            self.dismiss(old)
        self._start_timer(notice, notice.timeout)
        notice.offset = NOTICE_SLIDE

        def slide(t):
            notice.offset = NOTICE_SLIDE * (1 - t)
            self.queue_allocate()

        motion.animate(notice, 'notice_enter', slide, easing='ease_in_out', key='notice-slide')
        return notice

    def dismiss(self, notice: Notice):
        """Fade a notice out while the stack below closes the gap."""
        if notice.leaving or notice not in self._notices:
            return
        notice.leaving = True
        notice.stop_timer()

        def leave(t):
            notice.collapse = t
            notice.set_opacity(max(0.0, 1 - t))
            self.queue_allocate()

        motion.animate(notice, 'notice_exit', leave, easing='ease_in_out', key='notice-exit',
                       on_done=lambda: self._remove_notice(notice))

    def clear_notices(self):
        for notice in list(self._notices):
            notice.stop_timer()
            self._remove_notice(notice)

    def _remove_notice(self, notice):
        notice.stop_timer()
        if notice in self._notices:
            self._notices.remove(notice)
        if notice.get_parent() is self:
            self.remove(notice)
        notice.destroy()
        self.queue_allocate()

    def _start_timer(self, notice, delay):
        notice.stop_timer()
        if delay > 0 and not notice.leaving:
            def expire():
                notice.timer = 0
                self.dismiss(notice)
                return GLib.SOURCE_REMOVE
            notice.timer = GLib.timeout_add(delay, expire)

    def _on_notice_enter(self, notice, event):
        if event.detail != Gdk.NotifyType.INFERIOR:
            notice.stop_timer()
        return False

    def _on_notice_leave(self, notice, event):
        if event.detail != Gdk.NotifyType.INFERIOR and notice.timeout:
            self._start_timer(notice, min(notice.timeout, motion.DURATIONS['notice_hover_grace']))
        return False

    # Layout ---------------------------------------------------------------------

    def _on_child_position(self, _overlay, child, rect):
        width, height = self.get_allocated_width(), self.get_allocated_height()
        if child is self._catcher:
            rect.x, rect.y, rect.width, rect.height = 0, 0, width, height
            return True
        if child is self._prompt:
            x, y, box, max_height = prompt_geometry(width, height)
            top, right, bottom, left = SHADOW_MARGINS
            # Preferred sizes include the frame's shadow margins.
            minimum, natural = child.get_preferred_height_for_width(box + left + right)
            box_height = max(minimum - top - bottom, min(natural - top - bottom, max_height))
            rect.x, rect.y = x - left, y - top
            rect.width, rect.height = box + left + right, box_height + top + bottom
            return True
        if isinstance(child, Notice) and child in self._notices:
            top, right, bottom, left = NOTICE_SHADOW
            y = NOTICE_PADDING
            for notice in self._notices:
                box_width, box_height = self._notice_size(notice)
                if notice is child:
                    rect.x = round(width - NOTICE_PADDING - box_width - left + notice.offset)
                    rect.y = round(y) - top
                    rect.width = box_width + left + right
                    rect.height = box_height + top + bottom
                    return True
                y += (box_height + NOTICE_GAP) * (1 - notice.collapse)
        return False

    @staticmethod
    def _notice_size(notice):
        """Visible box size (without the shadow margins), max width 300."""
        minimum, natural = notice.box.get_preferred_width()
        top, right, bottom, left = NOTICE_SHADOW
        width = max(minimum, min(natural, NOTICE_MAX_WIDTH + left + right)) - left - right
        height = notice.box.get_preferred_height_for_width(width + left + right)[1]
        return width, height - top - bottom

    def _on_destroy(self, _widget):
        for notice in self._notices:
            notice.stop_timer()


def build_css(ui: dict[str, str]) -> str:
    """Notice rules (black 90% in every theme, spec 4.9); states are instant."""
    if ui['overlay'] == '#ffffff':
        shadow = '0 1px 2px alpha(#000000, 0.121), 0 3.4px 6.7px alpha(#000000, 0.179)'
    else:
        shadow = '0 1px 2px alpha(#000000, 0.028), 0 3.4px 6.7px alpha(#000000, 0.042)'
    bars = {'success': '#44cf6e', 'warning': '#e9973f', 'error': ui['danger']}
    levels = ''.join(f'.idgent-notice-{level} {{ box-shadow: inset 3px 0 0 {colour}, {shadow}; }}\n'
                     for level, colour in bars.items())
    return f"""
.idgent-notice {{ background: alpha(#000000, 0.9); color: #fafafa; border-radius: 8px;
    padding: 8px 12px; font-size: 13px; font-feature-settings: "tnum"; box-shadow: {shadow}; }}
.idgent-notice label {{ color: #fafafa; }}
""" + levels
