"""Decode (and encode) the `attributedBody` blobs in chat.db.

Since Ventura, `message.text` is often NULL and the text only lives in
`attributedBody`: an NSAttributedString archived as an Apple "typedstream".
Layout of the part we need:

    \\x04\\x0bstreamtyped ... NSAttributedString ... NSObject ...
    NSString \\x01 \\x94 \\x84 \\x01 +  <length> <UTF-8 bytes> \\x86 ...

<length> is a typedstream integer: one byte when < 0x80, 0x81 followed by a
2-byte little-endian value, or 0x82 followed by a 4-byte little-endian value.
(Harry's digest decoder skipped "high-bit" bytes instead, which corrupts
lengths such as 300 = 81 2C 01.) U+FFFC marks inline attachments.

The attribute dictionaries that follow carry extras such as mentions
(`__kIMMentionConfirmedMention` -> the mentioned handle).

The encoder is used by the fixture generator and the dry-run "fake
Messages.app"; it reproduces the reference tables Apple's archiver writes, so
`encode_attributed_body("Hello world")` matches a real blob byte for byte.
"""

from __future__ import annotations

import re
from typing import Iterable, Sequence

HEADER = b"\x04\x0bstreamtyped"
OBJECT_REPLACEMENT = "￼"
MENTION_KEY = b"__kIMMentionConfirmedMention"

_STRING_CLASSES = (b"NSString", b"NSMutableString")
_TEXT_TYPE = b"\x84\x01+"  # "new shared string '+'": the type of the text payload
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HANDLE = re.compile(r"^(\+?\d{3,15}|[^@\s]+@[^@\s]+\.[^@\s]+|urn:[\w:.\-]+)$")


# --------------------------------------------------------------------------- decoding


def read_int(buf: bytes, pos: int) -> tuple[int, int] | None:
    """Read a typedstream length at `pos`; returns (value, position after it)."""
    if pos >= len(buf):
        return None
    tag = buf[pos]
    if tag == 0x81:
        if pos + 3 > len(buf):
            return None
        return int.from_bytes(buf[pos + 1 : pos + 3], "little"), pos + 3
    if tag == 0x82:
        if pos + 5 > len(buf):
            return None
        return int.from_bytes(buf[pos + 1 : pos + 5], "little"), pos + 5
    if tag < 0x80:
        return tag, pos + 1
    return None


def _first_string_class(blob: bytes) -> int:
    """Index just past the first NSString/NSMutableString class name, or -1."""
    best = -1
    for name in _STRING_CLASSES:
        i = blob.find(name)
        if i >= 0 and (best < 0 or i < best):
            best = i + len(name)
    return best


def _decode_structured(blob: bytes) -> str | None:
    if not blob.startswith(HEADER):
        return None
    after = _first_string_class(blob)
    if after < 0:
        return None
    marker = blob.find(_TEXT_TYPE, after, after + 32)
    if marker < 0:
        return None
    parsed = read_int(blob, marker + len(_TEXT_TYPE))
    if parsed is None:
        return None
    length, start = parsed
    if start + length > len(blob):
        return None
    return blob[start : start + length].decode("utf-8", errors="replace")


def _decode_heuristic(blob: bytes) -> str | None:
    """Fallback for blobs that don't match the usual layout."""
    after = _first_string_class(blob)
    if after < 0:
        return None
    plus = blob.find(b"+", after, after + 40)
    if plus < 0:
        return None
    parsed = read_int(blob, plus + 1)
    if parsed and parsed[0] > 0 and parsed[1] + parsed[0] <= len(blob):
        return blob[parsed[1] : parsed[1] + parsed[0]].decode("utf-8", errors="replace")
    # Last resort: take bytes up to the end-of-object marker.
    start = plus + 2
    end = blob.find(b"\x86\x84", start)
    if end < 0:
        end = len(blob)
    return blob[start:end].decode("utf-8", errors="ignore") or None


def clean_text(text: str | None) -> str:
    """Strip attachment placeholders and stray control characters."""
    if not text:
        return ""
    text = text.replace(OBJECT_REPLACEMENT, "")
    text = _CONTROL.sub("", text)
    return text.strip()


def decode_text(blob: bytes | None) -> str | None:
    """The plain text stored in an attributedBody blob (None if there's none)."""
    if not blob or not isinstance(blob, (bytes, bytearray, memoryview)):
        return None
    data = bytes(blob)
    try:
        text = _decode_structured(data)
    except Exception:  # never let one odd blob break a page
        text = None
    if text is None:
        try:
            text = _decode_heuristic(data)
        except Exception:
            text = None
    return text


def _scan_handle(blob: bytes, start: int, stop: int) -> str | None:
    """Find the first length-prefixed string after `start` that looks like a handle."""
    for pos in range(start, min(stop, len(blob))):
        length = blob[pos]
        if not 3 <= length < 0x80:
            continue
        chunk = blob[pos + 1 : pos + 1 + length]
        if len(chunk) != length:
            continue
        try:
            value = chunk.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if _HANDLE.match(value):
            return value
    return None


def extract_mentions(blob: bytes | None) -> list[str]:
    """Handles mentioned with iMessage's @-mention feature (raw, not normalised)."""
    if not blob:
        return []
    data = bytes(blob)
    found: list[str] = []
    pos = 0
    while True:
        i = data.find(MENTION_KEY, pos)
        if i < 0:
            break
        pos = i + len(MENTION_KEY)
        handle = _scan_handle(data, pos, pos + 64)
        if handle and handle not in found:
            found.append(handle)
    return found


# --------------------------------------------------------------------------- encoding

_SUPERCLASS = {
    "NSAttributedString": "NSObject",
    "NSMutableAttributedString": "NSAttributedString",
    "NSString": "NSObject",
    "NSMutableString": "NSString",
    "NSDictionary": "NSObject",
    "NSNumber": "NSValue",
    "NSValue": "NSObject",
    "NSObject": None,
}
_CLASS_VERSION = {"NSString": 1, "NSMutableString": 1}


class _Writer:
    """Minimal typedstream writer that tracks shared strings and object references."""

    def __init__(self) -> None:
        self.out = bytearray(HEADER + b"\x81\xe8\x03")  # streamer version 1000
        self._strings: dict[bytes, int] = {}
        self._classes: dict[str, int] = {}
        self._objects = 0

    def int(self, n: int) -> None:
        if -0x6E <= n < 0x80:
            self.out.append(n & 0xFF)
        elif -0x8000 <= n < 0x8000:
            self.out.append(0x81)
            self.out += n.to_bytes(2, "little", signed=True)
        else:
            self.out.append(0x82)
            self.out += n.to_bytes(4, "little", signed=True)

    def _ref(self, index: int) -> None:
        self.out.append(0x92 + index)

    def shared(self, value: str) -> None:
        raw = value.encode("utf-8")
        if raw in self._strings:
            self._ref(self._strings[raw])
            return
        self._strings[raw] = len(self._strings)
        self.out.append(0x84)
        self.int(len(raw))
        self.out += raw

    def cls(self, name: str) -> None:
        if name in self._classes:
            self._ref(self._classes[name])
            return
        self.out.append(0x84)
        self._classes[name] = self._objects
        self._objects += 1
        self.shared(name)
        self.int(_CLASS_VERSION.get(name, 0))
        parent = _SUPERCLASS.get(name)
        if parent is None:
            self.out.append(0x85)  # nil: end of the class chain
        else:
            self.cls(parent)

    def begin(self, class_name: str) -> None:
        self.out.append(0x84)
        self._objects += 1
        self.cls(class_name)

    def end(self) -> None:
        self.out.append(0x86)

    def string(self, value: str, class_name: str = "NSString") -> None:
        self.shared("@")
        self.begin(class_name)
        self.shared("+")
        raw = value.encode("utf-8")
        self.int(len(raw))
        self.out += raw
        self.end()

    def number(self, value: int) -> None:
        self.shared("@")
        self.begin("NSNumber")
        self.shared("*")
        self.out.append(0x84)
        self.shared("i")
        self.shared("i")
        self.int(value)
        self.end()

    def dictionary(self, items: Sequence[tuple[str, str | int]]) -> None:
        self.shared("@")
        self.begin("NSDictionary")
        self.shared("i")
        self.int(len(items))
        for key, value in items:
            self.string(key)
            if isinstance(value, int):
                self.number(value)
            else:
                self.string(value)
        self.end()


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def encode_attributed_body(
    text: str,
    *,
    attachment_guids: Iterable[str] = (),
    mentions: Iterable[tuple[int, int, str]] = (),
) -> bytes:
    """Archive `text` the way Messages.app does.

    Each attachment becomes a leading U+FFFC run tagged with its transfer GUID
    (its own message part); `mentions` are (start, length, handle) ranges
    inside `text`.
    """
    guids = list(attachment_guids)
    full = OBJECT_REPLACEMENT * len(guids) + text
    runs: list[tuple[int, list[tuple[str, str | int]]]] = []
    for part, guid in enumerate(guids):
        runs.append((1, [("__kIMMessagePartAttributeName", part), ("__kIMFileTransferGUIDAttributeName", guid)]))

    text_part = len(guids)
    cursor = 0
    for start, length, handle in sorted(mentions):
        if start > cursor:
            runs.append((_utf16_len(text[cursor:start]), [("__kIMMessagePartAttributeName", text_part)]))
        runs.append(
            (
                _utf16_len(text[start : start + length]),
                [("__kIMMessagePartAttributeName", text_part), ("__kIMMentionConfirmedMention", handle)],
            )
        )
        cursor = start + length
    if cursor < len(text) or not runs:
        runs.append((_utf16_len(text[cursor:]), [("__kIMMessagePartAttributeName", text_part)]))

    w = _Writer()
    w.shared("@")
    w.begin("NSAttributedString")
    w.string(full)
    for index, (length, attrs) in enumerate(runs, start=1):
        w.shared("iI")
        w.int(index)
        w.int(length)
        w.dictionary(attrs)
    w.end()
    return bytes(w.out)
