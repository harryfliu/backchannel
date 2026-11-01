"""A real right-click at a point on screen (macOS), for the one thing accessibility can't do on
macOS 26.3: open a message's context menu, whose "Reply…" is the only way to reply to a message
that isn't the newest ("Reply to Last Message" in the menu bar answers the newest one).

Quartz events through ctypes (no extra packages). The pointer is put back where it was. Posting
events needs the same Accessibility permission the Messages helpers already have.
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


class CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


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
        cf.CFRelease.restype = None
        cf.CFRelease.argtypes = [ctypes.c_void_p]
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
