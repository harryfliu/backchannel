"""A real right-click at a point on screen (macOS), for the one thing accessibility can't do on
macOS 26.3: open a message's context menu, whose "Reply…" is the only way to reply to a message
that isn't the newest ("Reply to Last Message" in the menu bar answers the newest one).

Quartz events through ctypes (no extra packages). The pointer is put back where it was. Posting
events needs the same Accessibility permission the Messages helpers already have. Before clicking,
element_at() asks accessibility what is under the point (which app, which element), so a click
never lands on a window that happens to be in front of Messages.
"""

from __future__ import annotations

import ctypes
import sys
import time

_APP_SERVICES = "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
_CORE_FOUNDATION = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"

MOUSE_MOVED = 5
LEFT_DOWN = 1
LEFT_UP = 2
RIGHT_DOWN = 3
RIGHT_UP = 4
LEFT_BUTTON = 0
RIGHT_BUTTON = 1
HID_TAP = 0
CONTROL_FLAG = 1 << 18  # kCGEventFlagMaskControl


AX_VALUE_CGRECT = 3  # kAXValueCGRectType


class CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class CGRect(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double), ("w", ctypes.c_double), ("h", ctypes.c_double)]


_lib: tuple[ctypes.CDLL, ctypes.CDLL] | None = None


def _quartz() -> tuple[ctypes.CDLL, ctypes.CDLL]:
    global _lib
    if _lib is None:
        cg = ctypes.cdll.LoadLibrary(_APP_SERVICES)
        cf = ctypes.cdll.LoadLibrary(_CORE_FOUNDATION)
        cg.CGEventCreate.restype = ctypes.c_void_p
        cg.CGEventCreate.argtypes = [ctypes.c_void_p]
        cg.CGEventGetLocation.restype = CGPoint
        cg.CGEventGetLocation.argtypes = [ctypes.c_void_p]
        cg.CGEventCreateMouseEvent.restype = ctypes.c_void_p
        cg.CGEventCreateMouseEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint32, CGPoint, ctypes.c_uint32]
        cg.CGEventPost.restype = None
        cg.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
        cg.CGEventSetFlags.restype = None
        cg.CGEventSetFlags.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
        cg.AXUIElementCreateSystemWide.restype = ctypes.c_void_p
        cg.AXUIElementCreateSystemWide.argtypes = []
        cg.AXUIElementCopyElementAtPosition.restype = ctypes.c_int32
        cg.AXUIElementCopyElementAtPosition.argtypes = [ctypes.c_void_p, ctypes.c_float, ctypes.c_float,
                                                        ctypes.POINTER(ctypes.c_void_p)]
        cg.AXUIElementGetPid.restype = ctypes.c_int32
        cg.AXUIElementGetPid.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int32)]
        cg.AXUIElementCopyAttributeValue.restype = ctypes.c_int32
        cg.AXUIElementCopyAttributeValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        cg.AXValueGetValue.restype = ctypes.c_bool
        cg.AXValueGetValue.argtypes = [ctypes.c_void_p, ctypes.c_int32, ctypes.c_void_p]
        cf.CFRelease.restype = None
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        _lib = (cg, cf)
    return _lib


def available() -> bool:
    if sys.platform != "darwin":
        return False
    try:
        _quartz()
    except OSError:
        return False
    return True


def pointer() -> tuple[float, float] | None:
    """Where the pointer is now."""
    try:
        cg, cf = _quartz()
    except OSError:
        return None
    event = cg.CGEventCreate(None)
    if not event:
        return None
    try:
        point = cg.CGEventGetLocation(event)
        return point.x, point.y
    finally:
        cf.CFRelease(event)


def _post(kind: int, x: float, y: float, button: int, flags: int = 0) -> None:
    cg, cf = _quartz()
    event = cg.CGEventCreateMouseEvent(None, kind, CGPoint(x, y), button)
    if not event:
        raise OSError("couldn't make a mouse event")
    try:
        if flags:
            cg.CGEventSetFlags(event, flags)
        cg.CGEventPost(HID_TAP, event)
    finally:
        cf.CFRelease(event)


def move(x: float, y: float) -> None:
    _post(MOUSE_MOVED, x, y, 0)


def element_at(x: float, y: float) -> tuple[int, tuple[float, float, float, float] | None] | None:
    """What a click at (x, y) would hit: the owning app's pid and the element's frame (left, top,
    width, height), or None if accessibility couldn't say."""
    try:
        cg, cf = _quartz()
    except OSError:
        return None
    wide = cg.AXUIElementCreateSystemWide()
    if not wide:
        return None
    hit = ctypes.c_void_p()
    try:
        if cg.AXUIElementCopyElementAtPosition(wide, x, y, ctypes.byref(hit)) != 0 or not hit.value:
            return None
    finally:
        cf.CFRelease(wide)
    try:
        pid = ctypes.c_int32()
        if cg.AXUIElementGetPid(hit, ctypes.byref(pid)) != 0:
            return None
        return pid.value, _frame(cg, cf, hit)
    finally:
        cf.CFRelease(hit)


def _frame(cg: ctypes.CDLL, cf: ctypes.CDLL, element: ctypes.c_void_p) -> tuple[float, float, float, float] | None:
    name = cf.CFStringCreateWithCString(None, b"AXFrame", 0x08000100)  # kCFStringEncodingUTF8
    if not name:
        return None
    value = ctypes.c_void_p()
    try:
        if cg.AXUIElementCopyAttributeValue(element, name, ctypes.byref(value)) != 0 or not value.value:
            return None
    finally:
        cf.CFRelease(name)
    try:
        rect = CGRect()
        if not cg.AXValueGetValue(value, AX_VALUE_CGRECT, ctypes.byref(rect)):
            return None
        return rect.x, rect.y, rect.w, rect.h
    finally:
        cf.CFRelease(value)


def right_click(x: float, y: float) -> tuple[float, float] | None:
    """Right-click at (x, y) in global screen points (top-left origin, as accessibility reports
    positions). Returns where the pointer was, to put it back with move() afterwards."""
    before = pointer()
    move(x, y)
    time.sleep(0.08)
    _post(RIGHT_DOWN, x, y, RIGHT_BUTTON)
    time.sleep(0.05)
    _post(RIGHT_UP, x, y, RIGHT_BUTTON)
    return before


def control_click(x: float, y: float) -> tuple[float, float] | None:
    """A Control-click at (x, y), the other way macOS opens a context menu."""
    before = pointer()
    move(x, y)
    time.sleep(0.08)
    _post(LEFT_DOWN, x, y, LEFT_BUTTON, CONTROL_FLAG)
    time.sleep(0.05)
    _post(LEFT_UP, x, y, LEFT_BUTTON, CONTROL_FLAG)
    return before
