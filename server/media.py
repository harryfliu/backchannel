"""Attachment files: path resolution, kinds, dimensions and conversions.

Files are looked up by attachment GUID only, and served only when the
resolved path is inside ~/Library/Messages/Attachments (or the configured
attachments root used for fixtures). On macOS, HEIC is converted to JPEG and
thumbnails are made with `sips`, video thumbnails with `qlmanage`, and `.caf`
voice memos become `.m4a` with `afconvert`; results are cached under
~/Library/Caches/Backchannel. Elsewhere the original file is served.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

log = logging.getLogger("backchannel.media")

MESSAGES_ATTACHMENTS_PREFIX = "~/Library/Messages/Attachments"
CONVERT_TIMEOUT = 30

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".heic", ".heif", ".webp", ".tif", ".tiff", ".bmp"}
VIDEO_EXTS = {".mov", ".mp4", ".m4v", ".3gp", ".avi"}
AUDIO_EXTS = {".caf", ".m4a", ".mp3", ".aac", ".wav", ".amr", ".aiff", ".opus"}
IMAGE_UTIS = {"public.jpeg", "public.png", "public.heic", "public.heif", "com.compuserve.gif", "public.tiff", "org.webmproject.webp"}
VIDEO_UTIS = {"com.apple.quicktime-movie", "public.mpeg-4", "public.movie", "public.3gpp"}
AUDIO_UTIS = {"com.apple.coreaudio-format", "public.audio", "com.apple.m4a-audio", "public.mp3", "org.3gpp.adaptive-multi-rate-audio"}
NEEDS_JPEG = {".heic", ".heif", ".tif", ".tiff"}

# Types a browser may render inline from our origin. Everything else downloads.
INLINE_TYPES = {
    "image/jpeg", "image/png", "image/gif", "image/webp", "image/heic", "image/heif", "image/bmp",
    "video/quicktime", "video/mp4", "video/x-m4v", "video/3gpp", "audio/x-caf", "audio/mp4",
    "audio/mpeg", "audio/aac", "audio/wav", "audio/x-wav", "audio/amr", "audio/aiff", "audio/x-aiff",
    "application/pdf", "text/plain",
}
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _which(tool: str) -> str | None:
    found = shutil.which(tool)
    if found:
        return found
    fallback = f"/usr/bin/{tool}"
    return fallback if os.path.exists(fallback) else None


def kind_of(mime: str | None, uti: str | None, name: str | None) -> str:
    m = (mime or "").lower()
    u = (uti or "").lower()
    ext = Path(name or "").suffix.lower()
    if m.startswith("image/") or u in IMAGE_UTIS or ext in IMAGE_EXTS:
        return "image"
    if m.startswith("video/") or u in VIDEO_UTIS or ext in VIDEO_EXTS:
        return "video"
    if m.startswith("audio/") or u in AUDIO_UTIS or ext in AUDIO_EXTS:
        return "audio"
    return "file"


def guess_mime(name: str, stored: str | None = None) -> str:
    if stored:
        return stored
    ext = Path(name).suffix.lower()
    known = {".heic": "image/heic", ".heif": "image/heif", ".caf": "audio/x-caf", ".mov": "video/quicktime",
             ".m4a": "audio/mp4", ".amr": "audio/amr"}
    return known.get(ext) or mimetypes.guess_type(name)[0] or "application/octet-stream"


# --------------------------------------------------------------------------- dimensions


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    i = 2
    orientation_swap = False
    size = None
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg_len = struct.unpack(">H", data[i + 2 : i + 4])[0]
        if marker == 0xE1 and data[i + 4 : i + 10] == b"Exif\x00\x00":
            orientation_swap = _exif_rotated(data[i + 10 : i + 2 + seg_len])
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            h, w = struct.unpack(">HH", data[i + 5 : i + 9])
            size = (w, h)
            break
        i += 2 + seg_len
    if size and orientation_swap:
        return size[1], size[0]
    return size


def _exif_rotated(tiff: bytes) -> bool:
    """True when the EXIF orientation is 5-8 (portrait photos stored sideways)."""
    if len(tiff) < 8:
        return False
    endian = "<" if tiff[:2] == b"II" else ">"
    try:
        offset = struct.unpack(endian + "I", tiff[4:8])[0]
        count = struct.unpack(endian + "H", tiff[offset : offset + 2])[0]
        for n in range(count):
            entry = tiff[offset + 2 + n * 12 : offset + 14 + n * 12]
            tag = struct.unpack(endian + "H", entry[:2])[0]
            if tag == 0x0112:
                value = struct.unpack(endian + "H", entry[8:10])[0]
                return value in (5, 6, 7, 8)
    except (struct.error, IndexError):
        return False
    return False


def _heif_size(data: bytes) -> tuple[int, int] | None:
    i = data.find(b"ispe")
    if i < 0 or i + 16 > len(data):
        return None
    w, h = struct.unpack(">II", data[i + 8 : i + 16])
    rot = data.find(b"irot")
    if rot >= 0 and rot + 5 <= len(data) and data[rot + 4] & 0x03 in (1, 3):
        w, h = h, w
    return (w, h) if w and h else None


def _movie_size(data: bytes) -> tuple[int, int] | None:
    pos = 0
    while True:
        i = data.find(b"tkhd", pos)
        if i < 0 or i + 4 > len(data):
            return None
        pos = i + 4
        version = data[i + 4] if i + 4 < len(data) else 0
        body = i + 8  # after version/flags
        # times + track id + reserved + duration (20 or 32 bytes), then 16 bytes of
        # reserved/layer/group/volume, then the 3x3 matrix, then width/height (16.16).
        matrix_at = body + (32 if version == 1 else 20) + 16
        dims_at = matrix_at + 36
        if dims_at + 8 > len(data):
            continue
        w = struct.unpack(">I", data[dims_at : dims_at + 4])[0] >> 16
        h = struct.unpack(">I", data[dims_at + 4 : dims_at + 8])[0] >> 16
        if w and h:
            b = struct.unpack(">i", data[matrix_at + 4 : matrix_at + 8])[0]
            return (h, w) if b != 0 else (w, h)


def sniff_dimensions(path: Path, kind: str) -> tuple[int | None, int | None]:
    try:
        with path.open("rb") as f:
            head = f.read(262144)
            tail = b""
            if kind == "video":
                size = path.stat().st_size
                if size > len(head):
                    f.seek(max(len(head), size - 262144))
                    tail = f.read()
    except OSError:
        return None, None
    found: tuple[int, int] | None = None
    if head.startswith(b"\x89PNG\r\n\x1a\n") and len(head) >= 24:
        found = struct.unpack(">II", head[16:24])
    elif head[:6] in (b"GIF87a", b"GIF89a"):
        found = struct.unpack("<HH", head[6:10])
    elif head.startswith(b"\xff\xd8"):
        found = _jpeg_size(head)
    elif head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"heic", b"heix", b"mif1", b"msf1", b"heim", b"heis", b"avif"):
            found = _heif_size(head)
        else:
            found = _movie_size(head) or _movie_size(tail)
    elif kind == "video":
        found = _movie_size(head) or _movie_size(tail)
    if not found:
        return None, None
    return int(found[0]), int(found[1])


# --------------------------------------------------------------------------- the service


class Media:
    def __init__(self, attachments_root: Path, cache_dir: Path):
        self.attachments_root = Path(attachments_root)
        self.cache_dir = Path(cache_dir)
        self.is_mac = sys.platform == "darwin"
        self.sips = _which("sips") if self.is_mac else None
        self.afconvert = _which("afconvert") if self.is_mac else None
        self.qlmanage = _which("qlmanage") if self.is_mac else None
        self._dims: dict[tuple[str, float], tuple[int | None, int | None]] = {}
        self._lock = threading.Lock()
        roots = {os.path.realpath(self.attachments_root),
                 os.path.realpath(os.path.expanduser(MESSAGES_ATTACHMENTS_PREFIX))}
        self._roots = [Path(r) for r in roots]

    @property
    def video_thumbnails(self) -> bool:
        return bool(self.qlmanage and self.sips)

    # ------------------------------------------------------------------ paths

    def resolve(self, filename: str | None) -> Path | None:
        """Real path for chat.db's `attachment.filename`, or None if it's outside the allowed roots."""
        if not filename:
            return None
        if filename == MESSAGES_ATTACHMENTS_PREFIX or filename.startswith(MESSAGES_ATTACHMENTS_PREFIX + "/"):
            path = self.attachments_root / filename[len(MESSAGES_ATTACHMENTS_PREFIX) + 1 :]
        else:
            path = Path(os.path.expanduser(filename))
        real = Path(os.path.realpath(path))
        if any(real == root or root in real.parents for root in self._roots):
            return real
        return None

    def exists(self, filename: str | None) -> bool:
        path = self.resolve(filename)
        return bool(path and path.is_file())

    def dimensions(self, path: Path, kind: str) -> tuple[int | None, int | None]:
        if kind not in ("image", "video"):
            return None, None
        try:
            key = (str(path), path.stat().st_mtime)
        except OSError:
            return None, None
        with self._lock:
            if key in self._dims:
                return self._dims[key]
        dims = sniff_dimensions(path, kind)
        with self._lock:
            if len(self._dims) > 20000:
                self._dims.clear()
            self._dims[key] = dims
        return dims

    # ------------------------------------------------------------------ conversions (macOS)

    def _cache_path(self, guid: str, suffix: str) -> Path:
        folder = self.cache_dir / "attachments"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{_SAFE_NAME.sub('_', guid)}{suffix}"

    @staticmethod
    def _fresh(out: Path, src: Path) -> bool:
        try:
            return out.exists() and out.stat().st_mtime >= src.stat().st_mtime and out.stat().st_size > 0
        except OSError:
            return False

    def _run(self, args: list[str], out: Path) -> bool:
        tmp = out.with_name(out.name + f".{os.getpid()}.{threading.get_ident()}.tmp{out.suffix}")
        try:
            result = subprocess.run(
                [a if a != "{out}" else str(tmp) for a in args],
                capture_output=True,
                timeout=CONVERT_TIMEOUT,
            )
            if result.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
                os.replace(tmp, out)
                return True
            log.info("Conversion failed (%s): %s", args[0], result.stderr.decode(errors="ignore")[:300])
        except (OSError, subprocess.SubprocessError) as exc:
            log.info("Conversion error (%s): %s", args[0], exc)
        finally:
            tmp.unlink(missing_ok=True)
        return False

    def full_file(self, src: Path, guid: str, mime: str) -> tuple[Path, str]:
        """The file to serve for /attachments/{id}: HEIC→JPEG and CAF→M4A on macOS."""
        ext = src.suffix.lower()
        if self.sips and ext in NEEDS_JPEG:
            out = self._cache_path(guid, "-full.jpg")
            if self._fresh(out, src) or self._run([self.sips, "-s", "format", "jpeg", str(src), "--out", "{out}"], out):
                return out, "image/jpeg"
        if self.afconvert and ext == ".caf":
            out = self._cache_path(guid, ".m4a")
            if self._fresh(out, src) or self._run([self.afconvert, "-f", "m4af", "-d", "aac", str(src), "{out}"], out):
                return out, "audio/mp4"
        return src, mime

    def thumbnail(self, src: Path, guid: str, kind: str) -> tuple[Path, str] | None:
        """~800px JPEG preview on macOS; None when we can't make one."""
        if not self.sips:
            return None
        out = self._cache_path(guid, "-thumb.jpg")
        if self._fresh(out, src):
            return out, "image/jpeg"
        if kind == "image":
            if self._run([self.sips, "-Z", "800", "-s", "format", "jpeg", str(src), "--out", "{out}"], out):
                return out, "image/jpeg"
            return None
        if kind == "video" and self.qlmanage:
            with tempfile.TemporaryDirectory(prefix="bc-thumb-") as tmpdir:
                try:
                    subprocess.run(
                        [self.qlmanage, "-t", "-s", "800", "-o", tmpdir, str(src)],
                        capture_output=True,
                        timeout=CONVERT_TIMEOUT,
                    )
                except (OSError, subprocess.SubprocessError):
                    return None
                pngs = list(Path(tmpdir).glob("*.png"))
                if pngs and self._run([self.sips, "-s", "format", "jpeg", str(pngs[0]), "--out", "{out}"], out):
                    return out, "image/jpeg"
        return None

    def resize_avatar(self, data: bytes, key: str) -> tuple[bytes, str] | None:
        """Shrink a full-size contact photo to 256px (macOS only)."""
        if not self.sips:
            return None
        src = self._cache_path("avatar-src-" + key, ".img")
        out = self._cache_path("avatar-" + key, ".jpg")
        if out.exists() and out.stat().st_size > 0 and src.exists() and src.read_bytes() == data:
            return out.read_bytes(), "image/jpeg"
        src.write_bytes(data)
        if self._run([self.sips, "-Z", "256", "-s", "format", "jpeg", str(src), "--out", "{out}"], out):
            return out.read_bytes(), "image/jpeg"
        return None


def serving_headers(mime: str, name: str) -> tuple[str, dict[str, str]]:
    """Content type + headers that keep attachments from running script on our origin."""
    safe_name = name.replace('"', "").replace("\r", "").replace("\n", "") or "attachment"
    headers = {
        "Cache-Control": "private, max-age=86400",
        "X-Content-Type-Options": "nosniff",
    }
    if mime in INLINE_TYPES:
        disposition = "inline"
        if mime != "application/pdf":
            headers["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src 'self' data:; media-src 'self'"
    else:
        disposition = "attachment"
        mime = "application/octet-stream"
        headers["Content-Security-Policy"] = "sandbox; default-src 'none'"
    from urllib.parse import quote

    headers["Content-Disposition"] = f"{disposition}; filename*=UTF-8''{quote(safe_name)}"
    return mime, headers
