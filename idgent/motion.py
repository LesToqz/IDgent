'IDgent animation helpers and independently selected motion tokens.'
from __future__ import annotations

import math
import os
import re

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk


# Includes delays/holds as well as tween lengths; callers choose the right use.
# IDgent motion: immediate feedback, short state changes, unhurried entrances.
DURATIONS = {
    "instant": 0, "feedback": 96, "state": 128, "travel": 224,
    "settle": 352, "pause": 640,
}
# Established shorthand remains available to callers of the animation helpers.
DURATIONS.update({
    "none": DURATIONS["instant"], "superfast": DURATIONS["feedback"],
    "fast": DURATIONS["state"], "moderate": DURATIONS["travel"],
    "slow": DURATIONS["settle"], "collapse": DURATIONS["state"],
    "toggle": DURATIONS["state"], "notice_in": DURATIONS["travel"],
    "notice_out": DURATIONS["state"], "reflow": DURATIONS["travel"],
    "resize_handle": DURATIONS["state"],
})
# Short semantic names used by the motion alignment spec (WP-2).
# Curves chosen for IDgent. Default motion stays within the endpoints.
_EASING_POINTS = {
    "linear": (0.0, 0.0, 1.0, 1.0),
    "smooth": (0.24, 0.08, 0.28, 1.0),
    "arrive": (0.18, 0.66, 0.26, 1.0),
    "depart": (0.48, 0.0, 0.82, 0.42),
    "balanced": (0.38, 0.0, 0.62, 1.0),
    "jumpy": (0.32, -0.18, 0.42, 1.16),
}
# Compatibility names describe how a caller uses the IDgent curves.
_EASING_POINTS.update({
    "ease": _EASING_POINTS["smooth"],
    "ease_in": _EASING_POINTS["depart"],
    "ease_out": _EASING_POINTS["arrive"],
    "ease_in_out": _EASING_POINTS["balanced"],
    "delay": _EASING_POINTS["depart"],
    "swing": _EASING_POINTS["balanced"],
    "collapse": _EASING_POINTS["depart"],
    "reflow": _EASING_POINTS["smooth"],
    "slide_out": _EASING_POINTS["depart"],
    "sheet_dismiss": _EASING_POINTS["depart"],
    "backdrop_fade_in": _EASING_POINTS["arrive"],
    "loading_icon": _EASING_POINTS["balanced"],
})
_EASING_POINTS["sheet"] = _EASING_POINTS["sheet_dismiss"]
_EASING_POINTS["backdrop"] = _EASING_POINTS["backdrop_fade_in"]

__all__ = [
    "DURATIONS", "EASINGS", "duration_ms", "cubic_bezier", "animations_enabled",
    "set_reduce_motion",
    "Tween", "animate", "fade", "slide_in", "animate_size_request",
    "lerp_color", "StateFader",
]


def _finite(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("motion values must be finite")
    return value


def _clamp(value, lower=0.0, upper=1.0):
    return max(lower, min(upper, value))


def duration_ms(value):
    """Resolve a token name or finite numeric milliseconds (negative is immediate)."""
    return _finite(DURATIONS[value] if isinstance(value, str) else value)


def cubic_bezier(x1, y1, x2, y2):
    """Solve the CSS cubic's x coordinate with Newton, then bounded bisection."""
    x1, y1, x2, y2 = map(_finite, (x1, y1, x2, y2))
    if not (0 <= x1 <= 1 and 0 <= x2 <= 1):
        raise ValueError("CSS bezier x controls must be in [0, 1]")

    def sample(s, a, b):
        return ((1 - 3 * b + 3 * a) * s + 3 * b - 6 * a) * s * s + 3 * a * s

    def ease(t):
        t = _clamp(_finite(t))
        if t in (0.0, 1.0) or (x1 == y1 and x2 == y2):
            return t
        s = t
        for _ in range(8):
            error = sample(s, x1, x2) - t
            if abs(error) < 1e-9:
                return sample(s, y1, y2)
            slope = (3 * (1 - 3 * x2 + 3 * x1) * s + 6 * x2 - 12 * x1) * s + 3 * x1
            if abs(slope) < 1e-7:
                break
            candidate = s - error / slope
            if not 0 <= candidate <= 1:
                break
            s = candidate
        low, high = 0.0, 1.0
        for _ in range(40):
            s = (low + high) / 2
            x = sample(s, x1, x2)
            if abs(x - t) < 1e-10:
                break
            if x < t:
                low = s
            else:
                high = s
        return sample(s, y1, y2)

    return ease


EASINGS = {name: cubic_bezier(*points) for name, points in _EASING_POINTS.items()}
_OPACITY_KEY = object()
_SIZE_KEYS = {"width": object(), "height": object()}
_reduce_motion = False


def set_reduce_motion(flag: bool):
    """Set the application preference for subsequent tweens; no settings import.

    False permits animation only when the environment and GTK also permit it.
    Existing tweens keep their current lifecycle; this affects new tweens.
    """
    global _reduce_motion
    _reduce_motion = bool(flag)


def animations_enabled():
    """Check the application, environment and GTK accessibility settings lazily."""
    if _reduce_motion or os.environ.get("IDGENT_REDUCE_MOTION") == "1":
        return False
    settings = Gtk.Settings.get_default()
    return settings is None or bool(settings.get_property("gtk-enable-animations"))


class Tween:
    """Cancellation handle returned by animate; finished includes cancellation."""

    def __init__(self, widget, key, duration, delay, update, easing, on_done):
        self._widget = widget
        self._key = key
        self._duration = duration * 1000
        self._delay = delay * 1000
        self._update = update
        self._easing = easing
        self._on_done = on_done
        self._finished = False
        self._tick = None
        self._timeout = None
        self._signals = []
        self._origin = None
        self._last_time = None

    @property
    def finished(self):
        return self._finished

    def cancel(self):
        """Stop without completing or resetting the current value."""
        if self._finished:
            return
        self._finished = True
        widget = self._widget
        if self._tick is not None:
            widget.remove_tick_callback(self._tick)
            self._tick = None
        if self._timeout is not None:
            GLib.source_remove(self._timeout)
            self._timeout = None
        for signal in self._signals:
            widget.disconnect(signal)
        self._signals.clear()
        tweens = widget._motion_tweens
        if tweens.get(self._key) is self:
            del tweens[self._key]
        self._widget = self._update = self._on_done = None

    def _complete(self):
        done = self._on_done
        self.cancel()
        if done is not None:
            done()

    def _stop(self, _widget):
        self.cancel()

    def _mapped(self, widget):
        # Fallback and frame clocks share GLib's monotonic microsecond epoch.
        if self._timeout is not None:
            GLib.source_remove(self._timeout)
            self._timeout = None
        self._tick = widget.add_tick_callback(self._on_tick)

    def _on_tick(self, _widget, clock):
        return self._advance(clock.get_frame_time())

    def _on_timeout(self):
        return self._advance(GLib.get_monotonic_time())

    def _advance(self, now):
        if self._finished:
            return GLib.SOURCE_REMOVE
        # A newly mapped frame can predate the most recent fallback sample.
        now = max(now, self._last_time) if self._last_time is not None else now
        self._last_time = now
        if self._origin is None:
            self._origin = now
        elapsed = now - self._origin - self._delay
        if elapsed < 0:
            return GLib.SOURCE_CONTINUE
        progress = _clamp(elapsed / self._duration)
        try:
            self._update(1.0 if progress == 1 else self._easing(progress))
        except BaseException:
            self.cancel()
            raise
        # update() may destroy the widget, cancel us, or start a replacement.
        if not self._finished and progress == 1:
            self._complete()
        return not self._finished

    def _start(self):
        widget = self._widget
        self._signals = [widget.connect(signal, self._stop)
                         for signal in ("destroy", "unrealize", "unmap")]
        try:
            if self._duration <= 0 or not animations_enabled():
                self._update(1.0)
                if not self._finished:
                    self._complete()
                return
            self._update(0.0)
            if self._finished:
                return
            if widget.get_realized() and widget.get_mapped():
                self._tick = widget.add_tick_callback(self._on_tick)
            else:
                self._origin = GLib.get_monotonic_time()
                self._signals.append(widget.connect("map", self._mapped))
                self._timeout = GLib.timeout_add(16, self._on_timeout)
        except BaseException:
            self.cancel()
            raise


def animate(widget, duration, update, *, easing="smooth", on_done=None, key=None, delay=0):
    """Start/replace a tween; see the module docstring for lifecycle semantics."""
    duration = duration_ms(duration)
    delay = max(0.0, duration_ms(delay))
    easing = EASINGS[easing] if isinstance(easing, str) else easing
    if not callable(easing) or not callable(update):
        raise TypeError("easing and update must be callable")
    if on_done is not None and not callable(on_done):
        raise TypeError("on_done must be callable or None")
    tweens = getattr(widget, "_motion_tweens", None)
    if tweens is None:
        tweens = widget._motion_tweens = {}
    previous = tweens.get(key)
    if previous is not None:
        previous.cancel()
    tween = Tween(widget, key, duration, delay, update, easing, on_done)
    tweens[key] = tween
    tween._start()
    return tween


def _slide_origin(widget):
    return getattr(widget, "_motion_slide_margin", None)


def _restore_slide(widget):
    original = _slide_origin(widget)
    if original is not None:
        widget.set_margin_top(original)
        del widget._motion_slide_margin


def fade(widget, show, duration="fast", easing="smooth", on_done=None, hide_when_done=True):
    """Fade from current opacity; show before entrance and optionally hide on exit."""
    if show and not widget.get_visible():
        # Preserve a partially completed fade even if its widget was hidden.
        if widget.get_opacity() == 1:
            widget.set_opacity(0)
        widget.show()
    start, end = widget.get_opacity(), float(bool(show))
    margin, original = widget.get_margin_top(), _slide_origin(widget)

    def update(t):
        widget.set_opacity(_clamp(start + (end - start) * t))
        if original is not None:
            widget.set_margin_top(max(0, round(margin + (original - margin) * t)))

    def done():
        _restore_slide(widget)
        if not show and hide_when_done:
            widget.hide()
        if on_done is not None:
            on_done()

    return animate(widget, duration, update, easing=easing, on_done=done, key=_OPACITY_KEY)


def slide_in(widget, offset=12, duration="travel", easing="ease_in_out", on_done=None):
    """Show an opacity/margin entrance, preserving its baseline on interruption."""
    offset = _finite(offset)
    original = _slide_origin(widget)
    if original is None:
        original = widget._motion_slide_margin = widget.get_margin_top()
        widget.set_margin_top(max(0, round(original + offset)))
    if not widget.get_visible():
        if widget.get_opacity() == 1:
            widget.set_opacity(0)
        widget.show()
    margin, opacity = widget.get_margin_top(), widget.get_opacity()

    def update(t):
        widget.set_opacity(_clamp(opacity + (1 - opacity) * t))
        widget.set_margin_top(max(0, round(margin + (original - margin) * t)))

    def done():
        _restore_slide(widget)
        if on_done is not None:
            on_done()

    return animate(widget, duration, update, easing=easing, on_done=done, key=_OPACITY_KEY)


def animate_size_request(widget, axis, to, duration="moderate", easing="smooth",
                         on_done=None, *, restore=True):
    """Animate a minimum dimension, optionally retaining the target request."""
    if axis not in _SIZE_KEYS:
        raise ValueError("axis must be 'width' or 'height'")
    to = _finite(to)
    if to < 0:
        raise ValueError("target size must be nonnegative")
    prop = f"{axis}-request"
    originals = getattr(widget, "_motion_size_originals", None)
    if originals is None:
        originals = widget._motion_size_originals = {}
    current = widget.get_property(prop)
    originals.setdefault(axis, current)
    start = current if current >= 0 else getattr(widget, f"get_allocated_{axis}")()

    def update(t):
        widget.set_property(prop, max(0, round(start + (to - start) * t)))

    def done():
        original = originals.pop(axis)
        if restore:
            widget.set_property(prop, original)
        if on_done is not None:
            on_done()

    return animate(widget, duration, update, easing=easing, on_done=done, key=_SIZE_KEYS[axis])


def _color(value):
    value = value.strip()
    if re.fullmatch(r"#[0-9a-fA-F]{3,4}|#[0-9a-fA-F]{6}|#[0-9a-fA-F]{8}", value):
        digits = value[1:]
        if len(digits) <= 4:
            digits = "".join(c * 2 for c in digits)
        channels = [int(digits[i:i + 2], 16) / 255 for i in range(0, len(digits), 2)]
        return channels if len(channels) == 4 else channels + [1.0]
    match = re.fullmatch(r"(rgba?)\(([^()]*)\)", value, re.IGNORECASE)
    if match:
        parts = [part.strip() for part in match[2].split(",")]
        if len(parts) == (4 if match[1].lower() == "rgba" else 3):
            channels = [_clamp(_finite(part[:-1]) / 100 if part.endswith("%") else
                               _finite(part) / (255 if i < 3 else 1))
                        for i, part in enumerate(parts)]
            return channels if len(channels) == 4 else channels + [1.0]
    raise ValueError(f"unsupported colour: {value!r}")


def lerp_color(a, b, t):
    """Blend CSS hex/rgb(a) strings without requiring a display."""
    t = _finite(t)
    channels = [_clamp(x + (y - x) * t) for x, y in zip(_color(a), _color(b))]
    r, g, b = (round(c * 255) for c in channels[:3])
    return f"rgba({r}, {g}, {b}, {channels[3]:.6g})"


class StateFader:
    """Smooth a hover/active amount; set() reverses from the last emitted value."""

    def __init__(self, widget, update, duration="fast", easing="smooth", *, initial=0.0):
        self._widget = widget
        self._update = update
        self.duration = duration
        self.easing = easing
        self.value = _clamp(_finite(initial))
        self._key = object()
        widget.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self._signals = [widget.connect("enter-notify-event", self._enter),
                         widget.connect("leave-notify-event", self._leave),
                         widget.connect("destroy", self._destroy)]
        try:
            update(self.value)
        except BaseException:
            self.disconnect()
            raise

    def _enter(self, _widget, event):
        if event.detail != Gdk.NotifyType.INFERIOR:
            self.set(1)
        return False

    def _leave(self, _widget, event):
        if event.detail != Gdk.NotifyType.INFERIOR:
            self.set(0)
        return False

    def _destroy(self, _widget):
        self.disconnect()

    def set(self, target, on_done=None):
        """Animate to a clamped target; return the replacement Tween handle."""
        if self._widget is None:
            raise RuntimeError("StateFader is disconnected")
        start, target = self.value, _clamp(_finite(target))

        def update(t):
            self.value = _clamp(start + (target - start) * t)
            self._update(self.value)

        return animate(self._widget, self.duration, update, easing=self.easing,
                       on_done=on_done, key=self._key)

    def cancel(self):
        """Stop at the current value without removing the event handlers."""
        # Look up the registered tween: update() can cancel/disconnect before
        # animate() has returned its handle, including reduced-motion calls.
        if self._widget is not None:
            handle = getattr(self._widget, "_motion_tweens", {}).get(self._key)
            if handle is not None:
                handle.cancel()

    def disconnect(self):
        """Cancel, disconnect event handlers and release the widget/callback."""
        self.cancel()
        if self._widget is not None:
            try:
                for signal in self._signals:
                    self._widget.disconnect(signal)
            except RuntimeError:
                # A never-parented widget finalised by the GC emits "destroy"
                # from dispose, when its wrapper is no longer initialised.
                pass
            self._signals.clear()
            self._widget = self._update = None
