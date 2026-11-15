"""macOS Accessibility (AXUIElement) called directly through ctypes: no osascript, no System
Events, no extra packages.

Each call is a single request to the app, about a millisecond, where System Events plus AppleScript
take tens of milliseconds per element. The Messages helpers read a whole conversation this way in
about 0.1 s instead of 10–20 s (server/messages_ax.py). Needs the Accessibility permission the
helpers already have.
"""

from __future__ import annotations

import ctypes
import sys
from typing import Any

_APP_SERVICES = "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
_CORE_FOUNDATION = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
_UTF8 = 0x08000100  # kCFStringEncodingUTF8
_VP = ctypes.c_void_p

# AXValue types
_AX_POINT, _AX_SIZE, _AX_RECT, _AX_RANGE, _AX_ERROR = 1, 2, 3, 4, 5
_CF_NUMBER_DOUBLE = 13


class AXUnavailable(OSError):
    """Accessibility can't be used here (not macOS, or the frameworks didn't load)."""


class _CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class _CGSize(ctypes.Structure):
    _fields_ = [("w", ctypes.c_double), ("h", ctypes.c_double)]


class _CGRect(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double), ("w", ctypes.c_double), ("h", ctypes.c_double)]


class _CFRange(ctypes.Structure):
    _fields_ = [("location", ctypes.c_long), ("length", ctypes.c_long)]


class _Lib:
    def __init__(self) -> None:
        if sys.platform != "darwin":
            raise AXUnavailable("Accessibility is only on macOS.")
        try:
            ax = ctypes.cdll.LoadLibrary(_APP_SERVICES)
            cf = ctypes.cdll.LoadLibrary(_CORE_FOUNDATION)
        except OSError as exc:
            raise AXUnavailable(str(exc)) from exc
        for name, res, args in (
            ("AXIsProcessTrusted", ctypes.c_bool, []),
            ("AXUIElementCreateApplication", _VP, [ctypes.c_int32]),
            ("AXUIElementCopyAttributeValue", ctypes.c_int32, [_VP, _VP, ctypes.POINTER(_VP)]),
            ("AXUIElementCopyMultipleAttributeValues", ctypes.c_int32, [_VP, _VP, ctypes.c_uint32, ctypes.POINTER(_VP)]),
            ("AXUIElementCopyActionNames", ctypes.c_int32, [_VP, ctypes.POINTER(_VP)]),
            ("AXUIElementPerformAction", ctypes.c_int32, [_VP, _VP]),
            ("AXUIElementSetAttributeValue", ctypes.c_int32, [_VP, _VP, _VP]),
            ("AXUIElementSetMessagingTimeout", ctypes.c_int32, [_VP, ctypes.c_float]),
            ("AXUIElementGetTypeID", ctypes.c_ulong, []),
            ("AXValueGetTypeID", ctypes.c_ulong, []),
            ("AXValueGetType", ctypes.c_int32, [_VP]),
            ("AXValueGetValue", ctypes.c_bool, [_VP, ctypes.c_int32, _VP]),
            ("CGEventCreateKeyboardEvent", _VP, [_VP, ctypes.c_uint16, ctypes.c_bool]),
            ("CGEventPostToPid", None, [ctypes.c_int32, _VP]),
        ):
            fn = getattr(ax, name)
            fn.restype, fn.argtypes = res, args
        for name, res, args in (
            ("CFStringCreateWithCString", _VP, [_VP, ctypes.c_char_p, ctypes.c_uint32]),
            ("CFStringGetCString", ctypes.c_bool, [_VP, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]),
            ("CFStringGetLength", ctypes.c_long, [_VP]),
            ("CFAttributedStringGetString", _VP, [_VP]),
            ("CFGetTypeID", ctypes.c_ulong, [_VP]),
            ("CFStringGetTypeID", ctypes.c_ulong, []),
            ("CFAttributedStringGetTypeID", ctypes.c_ulong, []),
            ("CFArrayGetTypeID", ctypes.c_ulong, []),
            ("CFArrayGetCount", ctypes.c_long, [_VP]),
            ("CFArrayGetValueAtIndex", _VP, [_VP, ctypes.c_long]),
            ("CFArrayCreate", _VP, [_VP, ctypes.POINTER(_VP), ctypes.c_long, _VP]),
            ("CFBooleanGetTypeID", ctypes.c_ulong, []),
            ("CFBooleanGetValue", ctypes.c_bool, [_VP]),
            ("CFNumberGetTypeID", ctypes.c_ulong, []),
            ("CFNumberGetValue", ctypes.c_bool, [_VP, ctypes.c_int, _VP]),
            ("CFRetain", _VP, [_VP]),
            ("CFRelease", None, [_VP]),
        ):
            fn = getattr(cf, name)
            fn.restype, fn.argtypes = res, args
        self.ax, self.cf = ax, cf
        self.t_string = cf.CFStringGetTypeID()
        self.t_attributed = cf.CFAttributedStringGetTypeID()
        self.t_array = cf.CFArrayGetTypeID()
        self.t_bool = cf.CFBooleanGetTypeID()
        self.t_number = cf.CFNumberGetTypeID()
        self.t_element = ax.AXUIElementGetTypeID()
        self.t_value = ax.AXValueGetTypeID()
        self._names: dict[str, int] = {}

    def name(self, text: str) -> int:
        """A CFString for an attribute or action name, made once and kept."""
        ref = self._names.get(text)
        if ref is None:
            ref = self.cf.CFStringCreateWithCString(None, text.encode(), _UTF8)
            self._names[text] = ref
        return ref

    def string(self, ref: int) -> str:
        size = self.cf.CFStringGetLength(ref) * 4 + 1
        buf = ctypes.create_string_buffer(size)
        if not self.cf.CFStringGetCString(ref, buf, size, _UTF8):
            return ""
        return buf.value.decode("utf-8", "replace")

    def convert(self, ref: int | None) -> Any:
        """A CF value as Python (borrowed: the caller still owns `ref`)."""
        if not ref:
            return None
        kind = self.cf.CFGetTypeID(ref)
        if kind == self.t_string:
            return self.string(ref)
        if kind == self.t_attributed:
            inner = self.cf.CFAttributedStringGetString(ref)
            return self.string(inner) if inner else ""
        if kind == self.t_array:
            return [self.convert(self.cf.CFArrayGetValueAtIndex(ref, i)) for i in range(self.cf.CFArrayGetCount(ref))]
        if kind == self.t_element:
            return Element(self.cf.CFRetain(ref))
        if kind == self.t_bool:
            return bool(self.cf.CFBooleanGetValue(ref))
        if kind == self.t_number:
            out = ctypes.c_double()
            return out.value if self.cf.CFNumberGetValue(ref, _CF_NUMBER_DOUBLE, ctypes.byref(out)) else None
        if kind == self.t_value:
            value_type = self.ax.AXValueGetType(ref)
            struct = {_AX_POINT: _CGPoint, _AX_SIZE: _CGSize, _AX_RECT: _CGRect, _AX_RANGE: _CFRange}.get(value_type)
            if struct is None:  # kAXValueAXErrorType: the attribute has no value
                return None
            out = struct()
            if not self.ax.AXValueGetValue(ref, value_type, ctypes.byref(out)):
                return None
            return tuple(getattr(out, f) for f, _ in struct._fields_)
        return None


_lib: _Lib | None = None


def lib() -> _Lib:
    global _lib
    if _lib is None:
        _lib = _Lib()
    return _lib


def available() -> bool:
    try:
        lib()
    except AXUnavailable:
        return False
    return True


def trusted() -> bool:
    """This process may use Accessibility (System Settings › Privacy & Security › Accessibility)."""
    try:
        return bool(lib().ax.AXIsProcessTrusted())
    except AXUnavailable:
        return False


class Element:
    """One accessibility element (owns a reference; released when garbage-collected)."""

    __slots__ = ("ref",)

    def __init__(self, ref: int):
        self.ref = ref

    def __del__(self) -> None:
        if self.ref and _lib is not None:
            _lib.cf.CFRelease(self.ref)
            self.ref = None

    def get(self, attribute: str) -> Any:
        """An attribute's value, or None when the element doesn't have it (or didn't answer)."""
        L = lib()
        out = _VP()
        if L.ax.AXUIElementCopyAttributeValue(self.ref, L.name(attribute), ctypes.byref(out)) != 0 or not out.value:
            return None
        try:
            return L.convert(out.value)
        finally:
            L.cf.CFRelease(out.value)

    def get_many(self, attributes: list[str]) -> list[Any]:
        """Several attributes in one request; None for each one it lacks."""
        L = lib()
        names = (_VP * len(attributes))(*[L.name(a) for a in attributes])
        array = L.cf.CFArrayCreate(None, names, len(attributes), None)
        out = _VP()
        try:
            if L.ax.AXUIElementCopyMultipleAttributeValues(self.ref, array, 0, ctypes.byref(out)) != 0 or not out.value:
                return [None] * len(attributes)
        finally:
            L.cf.CFRelease(array)
        try:
            values = L.convert(out.value) or []
        finally:
            L.cf.CFRelease(out.value)
        return (list(values) + [None] * len(attributes))[: len(attributes)]

    def children(self) -> list["Element"]:
        return [k for k in (self.get("AXChildren") or []) if isinstance(k, Element)]

    def actions(self) -> list[str]:
        L = lib()
        out = _VP()
        if L.ax.AXUIElementCopyActionNames(self.ref, ctypes.byref(out)) != 0 or not out.value:
            return []
        try:
            return [a for a in (L.convert(out.value) or []) if isinstance(a, str)]
        finally:
            L.cf.CFRelease(out.value)

    def perform(self, action: str) -> bool:
        L = lib()
        return L.ax.AXUIElementPerformAction(self.ref, L.name(action)) == 0

    def set_text(self, attribute: str, text: str) -> bool:
        L = lib()
        value = L.cf.CFStringCreateWithCString(None, text.encode(), _UTF8)
        if not value:
            return False
        try:
            return L.ax.AXUIElementSetAttributeValue(self.ref, L.name(attribute), value) == 0
        finally:
            L.cf.CFRelease(value)


def application(pid: int, timeout: float = 3.0) -> Element:
    """The app's top element. Requests to it give up after `timeout` seconds (a busy app can't hang us)."""
    L = lib()
    ref = L.ax.AXUIElementCreateApplication(pid)
    if not ref:
        raise AXUnavailable("couldn't reach that app")
    L.ax.AXUIElementSetMessagingTimeout(ref, timeout)
    return Element(ref)


def post_key(pid: int, keycode: int) -> None:
    """Press and release one key in that app only (it doesn't have to be in front)."""
    L = lib()
    for down in (True, False):
        event = L.ax.CGEventCreateKeyboardEvent(None, keycode, down)
        if not event:
            raise OSError("couldn't make a key event")
        try:
            L.ax.CGEventPostToPid(pid, event)
        finally:
            L.cf.CFRelease(event)
