"""Tiny, dependency-free writers for fixture media files.

- PNG: real RGB images (zlib + struct).
- JPEG: a real baseline JPEG made of flat 8x8 blocks, so only DC
  coefficients are coded (enough for photos, thumbnails and contact cards).
- HEIC / MOV: ISO-BMFF placeholders with correct `ispe` / `tkhd` dimensions
  (not decodable images, like an iCloud-only stub).
- CAF: an LPCM voice memo with a short tone.
- PDF: a one-page document.
"""

from __future__ import annotations

import math
import struct
import zlib
from typing import Callable

RGB = tuple[int, int, int]


# --------------------------------------------------------------------------- PNG


def png(width: int, height: int, pixel: Callable[[int, int], RGB]) -> bytes:
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter: none
        for x in range(width):
            raw += bytes(pixel(x, y))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b"")


# --------------------------------------------------------------------------- JPEG

_DC_BITS = [0, 1, 5, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0]
_DC_VALS = list(range(12))
_AC_BITS = [0, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
_AC_VALS = [0x00, 0x01]  # EOB is all we ever emit


def _codes(bits: list[int], vals: list[int]) -> dict[int, tuple[int, int]]:
    table: dict[int, tuple[int, int]] = {}
    code = 0
    k = 0
    for length in range(1, 17):
        for _ in range(bits[length - 1]):
            table[vals[k]] = (code, length)
            code += 1
            k += 1
        code <<= 1
    return table


class _Bits:
    def __init__(self) -> None:
        self.out = bytearray()
        self.acc = 0
        self.n = 0

    def write(self, value: int, length: int) -> None:
        for i in range(length - 1, -1, -1):
            self.acc = (self.acc << 1) | ((value >> i) & 1)
            self.n += 1
            if self.n == 8:
                self.out.append(self.acc)
                if self.acc == 0xFF:
                    self.out.append(0x00)  # byte stuffing
                self.acc = 0
                self.n = 0

    def flush(self) -> bytes:
        if self.n:
            self.write((1 << (8 - self.n)) - 1, 8 - self.n)  # pad with 1s
        return bytes(self.out)


def jpeg(width: int, height: int, block: Callable[[int, int], RGB]) -> bytes:
    """Baseline JPEG where each 8x8 block (bx, by) is one flat colour."""
    assert width % 8 == 0 and height % 8 == 0
    dc = _codes(_DC_BITS, _DC_VALS)
    ac = _codes(_AC_BITS, _AC_VALS)
    bits = _Bits()
    prev = [0, 0, 0]
    for by in range(height // 8):
        for bx in range(width // 8):
            r, g, b = block(bx, by)
            ycc = (
                0.299 * r + 0.587 * g + 0.114 * b,
                -0.168736 * r - 0.331264 * g + 0.5 * b + 128,
                0.5 * r - 0.418688 * g - 0.081312 * b + 128,
            )
            for comp, value in enumerate(ycc):
                coef = int(round(8 * (max(0.0, min(255.0, value)) - 128)))  # DC of a flat block, quant = 1
                diff = coef - prev[comp]
                prev[comp] = coef
                cat = 0 if diff == 0 else abs(diff).bit_length()
                code, length = dc[cat]
                bits.write(code, length)
                if cat:
                    bits.write(diff if diff > 0 else diff + (1 << cat) - 1, cat)
                code, length = ac[0x00]
                bits.write(code, length)
    scan = bits.flush()

    def segment(marker: int, payload: bytes) -> bytes:
        return struct.pack(">BBH", 0xFF, marker, len(payload) + 2) + payload

    app0 = segment(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
    dqt = segment(0xDB, b"\x00" + bytes([1] * 64))
    sof = segment(0xC0, struct.pack(">BHHB", 8, height, width, 3) + b"\x01\x11\x00\x02\x11\x00\x03\x11\x00")
    dht = segment(0xC4, b"\x00" + bytes(_DC_BITS) + bytes(_DC_VALS) + b"\x10" + bytes(_AC_BITS) + bytes(_AC_VALS))
    sos = segment(0xDA, b"\x03\x01\x00\x02\x00\x03\x00\x00\x3f\x00")
    return b"\xff\xd8" + app0 + dqt + sof + dht + sos + scan + b"\xff\xd9"


# --------------------------------------------------------------------------- ISO-BMFF placeholders


def _box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload) + 8) + kind + payload


def _fullbox(kind: bytes, version: int, flags: int, payload: bytes) -> bytes:
    return _box(kind, struct.pack(">I", (version << 24) | flags) + payload)


def heic_placeholder(width: int, height: int) -> bytes:
    ftyp = _box(b"ftyp", b"heic" + struct.pack(">I", 0) + b"mif1heic")
    hdlr = _fullbox(b"hdlr", 0, 0, b"\x00\x00\x00\x00pict" + b"\x00" * 12 + b"\x00")
    ispe = _fullbox(b"ispe", 0, 0, struct.pack(">II", width, height))
    meta = _fullbox(b"meta", 0, 0, hdlr + _box(b"iprp", _box(b"ipco", ispe)))
    return ftyp + meta + _box(b"mdat", bytes(range(256)) * 16)


def mov_placeholder(width: int, height: int, portrait: bool = True) -> bytes:
    ftyp = _box(b"ftyp", b"qt  " + struct.pack(">I", 0x20050300) + b"qt  ")
    identity = (0x00010000, 0, 0, 0, 0x00010000, 0, 0, 0, 0x40000000)
    rotated = (0, 0x00010000, 0, -0x00010000, 0, 0, 0, 0, 0x40000000)
    matrix = struct.pack(">9i", *(rotated if portrait else identity))
    mvhd = _fullbox(b"mvhd", 0, 0, struct.pack(">IIIIIH", 0, 0, 600, 1800, 0x00010000, 0x0100) + b"\x00" * 10
                    + struct.pack(">9i", *identity) + b"\x00" * 24 + struct.pack(">I", 2))
    tkhd = _fullbox(b"tkhd", 0, 3, struct.pack(">IIIII", 0, 0, 1, 0, 1800) + b"\x00" * 8
                    + struct.pack(">hhhH", 0, 0, 0, 0) + matrix + struct.pack(">II", width << 16, height << 16))
    moov = _box(b"moov", mvhd + _box(b"trak", tkhd))
    return ftyp + moov + _box(b"mdat", bytes(range(256)) * 32)


# --------------------------------------------------------------------------- CAF voice memo


def caf_tone(seconds: float = 1.2, rate: int = 16000, freq: float = 330.0) -> bytes:
    frames = int(seconds * rate)
    samples = bytearray()
    for i in range(frames):
        envelope = min(1.0, i / 800, (frames - i) / 800)
        samples += struct.pack(">h", int(9000 * envelope * math.sin(2 * math.pi * freq * i / rate)))
    header = b"caff" + struct.pack(">HH", 1, 0)
    desc = b"desc" + struct.pack(">q", 32) + struct.pack(">d4sIIIII", float(rate), b"lpcm", 0, 2, 1, 1, 16)
    data = b"data" + struct.pack(">q", len(samples) + 4) + struct.pack(">I", 0) + bytes(samples)
    return header + desc + data


# --------------------------------------------------------------------------- PDF


def pdf(lines: list[str]) -> bytes:
    text_ops = ["BT", "/F1 20 Tf", "72 720 Td"]
    for i, line in enumerate(lines):
        safe = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        if i:
            text_ops.append("0 -30 Td")
        text_ops.append(f"({safe}) Tj")
    text_ops.append("ET")
    stream = "\n".join(text_ops).encode("latin-1", "replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
