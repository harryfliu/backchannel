"""Identify image formats from their first bytes (offset 0 only, never by searching)."""

from __future__ import annotations

from pathlib import Path

HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"hevm", b"hevs", b"mif1", b"msf1",
               b"avif", b"avis"}
MIME = {
    "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp",
    "tiff": "image/tiff", "heic": "image/heic", "bmp": "image/bmp",
}
# What an <img> in WebKit/Chrome can always draw.
BROWSER_IMAGES = {"jpeg", "png", "gif", "webp"}
# Contact photos: JPEG/PNG/GIF pass through, everything else becomes JPEG.
AVATAR_IMAGES = {"jpeg", "png", "gif"}


def sniff_format(head: bytes | None) -> str | None:
    """'jpeg', 'png', 'gif', 'webp', 'tiff', 'heic', 'bmp' or None."""
    if not head:
        return None
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if head[4:8] == b"ftyp" and head[8:12] in HEIF_BRANDS:
        return "heic"
    if head[:2] == b"BM" and len(head) >= 14:
        return "bmp"
    return None


def sniff_file(path: Path) -> str | None:
    try:
        with Path(path).open("rb") as f:
            return sniff_format(f.read(64))
    except OSError:
        return None
