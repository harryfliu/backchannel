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

from .imagetypes import BROWSER_IMAGES, MIME, sniff_file, sniff_format

log = logging.getLogger("backchannel.media")

MESSAGES_ATTACHMENTS_PREFIX = "~/Library/Messages/Attachments"
# Messages also keeps stickers (and a few attachments) outside Attachments/. These are the only
# other folders served, read-only; temp folders like /var/folders/… are never served (they show
# as missing).
STICKER_CACHE_PREFIX = "~/Library/Messages/StickerCache"
MESSAGES_CONTAINER_PREFIX = "~/Library/Containers/com.apple.MobileSMS"
TRANSIENT_PREFIXES = ("/var/folders/", "/private/var/folders/")
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


def _orientation_at(data: bytes) -> tuple[int, int, str] | None:
    """A JPEG's EXIF orientation: (where its value sits in `data`, the value, byte order), or None."""
    if data[:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if marker == 0xDA:  # image data: no EXIF after this
            return None
        seg_len = struct.unpack(">H", data[i + 2 : i + 4])[0]
        if marker == 0xE1 and data[i + 4 : i + 10] == b"Exif\x00\x00":
            start = i + 10
            tiff = data[start : i + 2 + seg_len]
            endian = "<" if tiff[:2] == b"II" else ">"
            try:
                offset = struct.unpack(endian + "I", tiff[4:8])[0]
                count = struct.unpack(endian + "H", tiff[offset : offset + 2])[0]
                for n in range(count):
                    at = offset + 2 + n * 12
                    if struct.unpack(endian + "H", tiff[at : at + 2])[0] == 0x0112:
                        return start + at + 8, struct.unpack(endian + "H", tiff[at + 8 : at + 10])[0], endian
            except (struct.error, IndexError):
                return None
            return None
        i += 2 + seg_len
    return None


# EXIF orientation → how sips turns those pixels upright: degrees clockwise, then a flip.
UPRIGHT: dict[int, tuple[int, str | None]] = {
    2: (0, "horizontal"), 3: (180, None), 4: (0, "vertical"), 5: (90, "horizontal"),
    6: (90, None), 7: (270, "horizontal"), 8: (270, None),
}


def _boxes(data: bytes, start: int, end: int):
    """(type, payload start, box end) for each ISO-BMFF box in data[start:end]; stops at bad sizes."""
    pos = start
    while pos + 8 <= end:
        size, kind = struct.unpack(">I4s", data[pos : pos + 8])
        header = 8
        if size == 1:
            if pos + 16 > end:
                return
            size = struct.unpack(">Q", data[pos + 8 : pos + 16])[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            return
        yield kind, pos + header, pos + size
        pos += size


def _child(data: bytes, start: int, end: int, kind: bytes) -> tuple[int, int] | None:
    for k, s, e in _boxes(data, start, end):
        if k == kind:
            return s, e
    return None


def _heif_primary(data: bytes) -> tuple[int, int, int] | None:
    """(width, height, rotation quarter-turns) of the primary item: pitm → ipma → ipco."""
    meta = _child(data, 0, len(data), b"meta")
    if meta is None:
        return None
    body = meta[0] + 4  # FullBox version/flags
    pitm = _child(data, body, meta[1], b"pitm")
    iprp = _child(data, body, meta[1], b"iprp")
    if pitm is None or iprp is None:
        return None
    version = data[pitm[0]]
    primary = struct.unpack(">H" if version == 0 else ">I", data[pitm[0] + 4 : pitm[0] + (6 if version == 0 else 8)])[0]
    ipco = _child(data, iprp[0], iprp[1], b"ipco")
    ipma = _child(data, iprp[0], iprp[1], b"ipma")
    if ipco is None or ipma is None:
        return None
    properties = list(_boxes(data, ipco[0], ipco[1]))  # 1-based indexes in ipma
    version, flags = data[ipma[0]], int.from_bytes(data[ipma[0] + 1 : ipma[0] + 4], "big")
    pos = ipma[0] + 4
    (entries,) = struct.unpack(">I", data[pos : pos + 4])
    pos += 4
    for _ in range(entries):
        if version < 1:
            (item,) = struct.unpack(">H", data[pos : pos + 2])
            pos += 2
        else:
            (item,) = struct.unpack(">I", data[pos : pos + 4])
            pos += 4
        count = data[pos]
        pos += 1
        indexes = []
        for _ in range(count):
            if flags & 1:
                (value,) = struct.unpack(">H", data[pos : pos + 2])
                pos += 2
                indexes.append(value & 0x7FFF)
            else:
                indexes.append(data[pos] & 0x7F)
                pos += 1
        if item != primary:
            continue
        size = None
        turns = 0
        for index in indexes:
            if not 1 <= index <= len(properties):
                continue
            kind, start, _ = properties[index - 1]
            if kind == b"ispe":
                size = struct.unpack(">II", data[start + 4 : start + 12])
            elif kind == b"irot":
                turns = data[start] & 0x03
        return (size[0], size[1], turns) if size else None
    return None


def _heif_size(data: bytes) -> tuple[int, int] | None:
    """Displayed size of a HEIC/HEIF: the primary item's ispe, rotated by its irot.

    iPhone photos list 512x512 tile and thumbnail ispe boxes before the full-size
    grid's, so the first ispe is usually wrong. Without a usable pitm/ipma, fall
    back to the largest ispe.
    """
    try:
        primary = _heif_primary(data)
    except (struct.error, IndexError):
        primary = None
    if primary is not None:
        w, h, turns = primary
    else:
        best = None
        pos = 0
        while True:
            i = data.find(b"ispe", pos)
            if i < 0 or i + 16 > len(data):
                break
            pos = i + 4
            cw, ch = struct.unpack(">II", data[i + 8 : i + 16])
            if cw and ch and (best is None or cw * ch > best[0] * best[1]):
                best = (cw, ch)
        if best is None:
            return None
        w, h = best
        rot = data.find(b"irot")
        turns = data[rot + 4] & 0x03 if rot >= 0 and rot + 5 <= len(data) else 0
    if turns in (1, 3):
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
    def __init__(self, attachments_root: Path, cache_dir: Path, *, sticker_cache_root: Path | None = None,
                 messages_container_root: Path | None = None):
        self.attachments_root = Path(attachments_root)
        self.cache_dir = Path(cache_dir)
        # chat.db path prefix → where those files are on this machine (fixtures point them elsewhere).
        self.prefixes: list[tuple[str, Path]] = [
            (MESSAGES_ATTACHMENTS_PREFIX, self.attachments_root),
            (STICKER_CACHE_PREFIX, Path(sticker_cache_root or os.path.expanduser(STICKER_CACHE_PREFIX))),
            (MESSAGES_CONTAINER_PREFIX, Path(messages_container_root or os.path.expanduser(MESSAGES_CONTAINER_PREFIX))),
        ]
        self.is_mac = sys.platform == "darwin"
        self.sips = _which("sips") if self.is_mac else None
        self.afconvert = _which("afconvert") if self.is_mac else None
        self.qlmanage = _which("qlmanage") if self.is_mac else None
        self._dims: dict[tuple[str, float], tuple[int | None, int | None]] = {}
        self._formats: dict[tuple[str, float, str], str | None] = {}
        self._orients: dict[tuple[str, float], int] = {}
        self._lock = threading.Lock()
        self._upright_lock = threading.Lock()
        roots: set[str] = set()
        for prefix, root in self.prefixes:
            roots.add(os.path.realpath(root))
            roots.add(os.path.realpath(os.path.expanduser(prefix)))
        self._roots = [Path(r) for r in sorted(roots)]

    @property
    def video_thumbnails(self) -> bool:
        return bool(self.qlmanage and self.sips)

    # ------------------------------------------------------------------ paths

    def resolve(self, filename: str | None) -> Path | None:
        """Real path for chat.db's `attachment.filename`, or None if it's outside the allowed roots."""
        if not filename:
            return None
        for prefix, root in self.prefixes:
            if filename == prefix or filename.startswith(prefix + "/"):
                path = root / filename[len(prefix) + 1 :]
                break
        else:
            path = Path(os.path.expanduser(filename))
        real = Path(os.path.realpath(path))
        if any(real == root or root in real.parents for root in self._roots):
            return real
        return None

    @staticmethod
    def is_transient(filename: str | None) -> bool:
        """A temp-folder path (e.g. /var/folders/…): never served, and long gone anyway."""
        return bool(filename) and filename.startswith(TRANSIENT_PREFIXES)  # type: ignore[union-attr]

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

    def orientation(self, path: Path) -> int:
        """A JPEG's EXIF orientation (1 = upright; also for anything else), remembered per file version."""
        try:
            key = (str(path), path.stat().st_mtime)
        except OSError:
            return 1
        with self._lock:
            known = self._orients.get(key)
        if known is not None:
            return known
        try:
            with path.open("rb") as f:
                head = f.read(131072)
        except OSError:
            return 1
        found = _orientation_at(head)
        value = found[1] if found and found[1] in UPRIGHT else 1
        with self._lock:
            self._orients[key] = value
        return value

    def make_upright(self, path: Path) -> bool:
        """Turn a cached JPEG's pixels the way its EXIF orientation says, then mark it upright (orientation
        1), in place. sips converts and resizes without applying the tag, browsers apply it when they show
        the image, and the web view's Copy Image copies the pixels without it: Harry's portrait photos
        pasted into another chat came out sideways. False if it couldn't (the file is left as it was)."""
        if self.orientation(path) == 1:
            return True
        if not self.sips:
            return False
        with self._upright_lock:
            work = path.with_name(path.name + f".{os.getpid()}.{threading.get_ident()}.up{path.suffix}")
            try:
                data = path.read_bytes()
                found = _orientation_at(data[:131072])
                if found is None or found[1] not in UPRIGHT:
                    return True  # done meanwhile
                degrees, flip = UPRIGHT[found[1]]
                work.write_bytes(data)
                steps = ([["-r", str(degrees)]] if degrees else []) + ([["-f", flip]] if flip else [])
                for step in steps:
                    if not self._run([self.sips, *step, str(work), "--out", "{out}"], work):
                        return False
                turned = bytearray(work.read_bytes())
                tag = _orientation_at(bytes(turned[:131072]))
                if tag is not None:  # sips keeps the tag: now it would turn the pixels a second time
                    at, _, endian = tag
                    turned[at : at + 2] = struct.pack(endian + "H", 1)
                    work.write_bytes(bytes(turned))
                os.replace(work, path)
                return True
            except OSError as exc:
                log.info("Couldn't turn an image upright: %s", exc)
                return False
            finally:
                work.unlink(missing_ok=True)

    def _copy(self, src: Path, out: Path) -> bool:
        tmp = out.with_name(out.name + f".{os.getpid()}.{threading.get_ident()}.tmp{out.suffix}")
        try:
            shutil.copyfile(src, tmp)
            os.replace(tmp, out)
            return True
        except OSError:
            tmp.unlink(missing_ok=True)
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

    def image_format(self, path: Path) -> str | None:
        """The real format of an image file, judged by its bytes (cached by mtime)."""
        try:
            key = (str(path), path.stat().st_mtime, "fmt")
        except OSError:
            return None
        with self._lock:
            if key in self._formats:
                return self._formats[key]
        fmt = sniff_file(path)
        with self._lock:
            if len(self._formats) > 20000:
                self._formats.clear()
            self._formats[key] = fmt
        return fmt

    def drawable(self, path: Path | None) -> bool:
        """Can an <img> show this file (as is, or after we convert it)?"""
        if path is None:
            return False
        fmt = self.image_format(path)
        return fmt in BROWSER_IMAGES or (fmt is not None and self.sips is not None)

    def full_file(self, src: Path, guid: str, mime: str) -> tuple[Path, str]:
        """The file to serve for /attachments/{id}: HEIC/TIFF→JPEG and CAF→M4A on macOS.

        Images are judged by their bytes, not their name (iPhones send HEIC named .JPG).
        """
        fmt = self.image_format(src)
        if fmt in BROWSER_IMAGES:
            if fmt == "jpeg" and self.sips and self.orientation(src) != 1:
                # A phone photo stored sideways with an EXIF tag: an upright copy, so the web view's Copy
                # Image (which drops the tag) copies it the way it's shown.
                out = self._cache_path(guid, "-full.jpg")
                if (self._fresh(out, src) or self._copy(src, out)) and self.make_upright(out):
                    return out, "image/jpeg"
            return src, MIME[fmt]
        if fmt is not None or src.suffix.lower() in NEEDS_JPEG:
            if self.sips:
                out = self._cache_path(guid, "-full.jpg")
                if self._fresh(out, src) or self._run([self.sips, "-s", "format", "jpeg", str(src), "--out", "{out}"], out):
                    if sniff_file(out) == "jpeg":
                        self.make_upright(out)
                        return out, "image/jpeg"
            return src, MIME.get(fmt or "", mime)
        if self.afconvert and src.suffix.lower() == ".caf":
            out = self._cache_path(guid, ".m4a")
            if self._fresh(out, src) or self._run([self.afconvert, "-f", "m4af", "-d", "aac", str(src), "{out}"], out):
                return out, "audio/mp4"
        return src, mime

    def preview_file(self, src: Path, guid: str, kind: str) -> tuple[Path, str] | None:
        """Something an <img> can draw for /thumb: a thumbnail, else (images only) a browser-friendly full file."""
        thumb = self.thumbnail(src, guid, kind)
        if thumb is not None:
            return thumb
        if kind != "image" and self.image_format(src) is None:
            return None  # never hand a video or document to an <img>
        path, mime = self.full_file(src, guid, "application/octet-stream")
        fmt = self.image_format(path)
        return (path, MIME[fmt]) if fmt in BROWSER_IMAGES else None

    def thumbnail(self, src: Path, guid: str, kind: str) -> tuple[Path, str] | None:
        """~800px JPEG preview on macOS; None when we can't make one."""
        if not self.sips:
            return None
        out = self._cache_path(guid, "-thumb.jpg")
        if self._fresh(out, src):
            self.make_upright(out)  # thumbnails made before this was done are fixed the first time they're shown
            return out, "image/jpeg"
        if kind == "image" or self.image_format(src) is not None:
            if self._run([self.sips, "-Z", "800", "-s", "format", "jpeg", str(src), "--out", "{out}"], out):
                if sniff_file(out) == "jpeg":
                    self.make_upright(out)
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
                    if sniff_file(out) == "jpeg":
                        return out, "image/jpeg"
        return None

    def png_file(self, src: Path, key: str) -> Path | None:
        """A PNG copy made with sips (keeps transparency), cached; None without sips or on failure."""
        if not self.sips:
            return None
        out = self._cache_path(key, "-transparent.png")
        if self._fresh(out, src) and sniff_file(out) == "png":
            return out
        if self._run([self.sips, "-s", "format", "png", str(src), "--out", "{out}"], out) and sniff_file(out) == "png":
            return out
        return None

    def png_bytes(self, data: bytes, key: str) -> bytes | None:
        """PNG bytes for in-memory image data (e.g. an uploaded HEIC), via sips."""
        if not self.sips:
            return None
        src = self._cache_path("convert-src-" + key, ".img")
        src.write_bytes(data)
        out = None
        try:
            out = self.png_file(src, "convert-" + key)
            return out.read_bytes() if out else None
        finally:  # one-off conversions: keep nothing in the cache
            src.unlink(missing_ok=True)
            if out is not None:
                out.unlink(missing_ok=True)

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
            result = out.read_bytes()
            if sniff_format(result[:64]) == "jpeg":
                return result, "image/jpeg"
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
