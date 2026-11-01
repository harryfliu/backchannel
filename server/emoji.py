"""Custom emoji: Slack-style images Harry adds by upload, link (slackmojis.com) or from a sticker.

Images are judged by their bytes: PNG, GIF, JPEG and WebP are kept as they
are; HEIC becomes PNG with sips; SVG and everything else is refused. Links
are fetched over http(s) only, at most 3 redirects, 15 s, 5 MB, and every
hop's host is resolved and refused if any address is private, loopback,
link-local, multicast or reserved. The connection then goes to that checked
address (so DNS can't change the answer in between).
"""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import logging
import os
import re
import socket
import ssl
import struct
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urljoin, urlsplit

from .imagetypes import sniff_format
from .media import _jpeg_size
from .state import State
from .timeutil import iso

log = logging.getLogger("backchannel.emoji")

NAME = re.compile(r"^[a-z0-9_+-]{2,40}$")
MAX_BYTES = 5 * 1024 * 1024
FETCH_TIMEOUT = 15.0
MAX_REDIRECTS = 3
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
              "Version/17.4 Safari/605.1.15")
EXTENSIONS = {"png": ".png", "gif": ".gif", "jpeg": ".jpg", "webp": ".webp"}
MIMES = {"png": "image/png", "gif": "image/gif", "jpeg": "image/jpeg", "webp": "image/webp"}


class EmojiError(Exception):
    """A user-facing problem with an emoji request; `status` is the HTTP status to answer with."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def clean_name(raw: str | None) -> str:
    """':Party-Parrot:' -> 'party-parrot', or EmojiError(400)."""
    name = (raw or "").strip().strip(":").strip().lower()
    if not NAME.match(name):
        raise EmojiError(400, "Emoji names are 2–40 characters: lowercase letters, digits, - _ or +.")
    return name


# --------------------------------------------------------------------------- image inspection


@dataclass
class ImageInfo:
    fmt: str
    width: int | None
    height: int | None
    animated: bool


def _png_info(data: bytes) -> tuple[int | None, int | None, bool]:
    width = height = None
    if len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
    animated = False
    pos = 8
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos : pos + 8])
        if kind == b"acTL" and pos + 12 <= len(data):
            animated = struct.unpack(">I", data[pos + 8 : pos + 12])[0] > 1
            break
        if kind in (b"IDAT", b"IEND"):
            break
        pos += 12 + length
    return width, height, animated


def _gif_info(data: bytes) -> tuple[int | None, int | None, bool]:
    if len(data) < 13:
        return None, None, False
    width, height = struct.unpack("<HH", data[6:10])
    pos = 13
    flags = data[10]
    if flags & 0x80:
        pos += 3 * (2 ** ((flags & 0x07) + 1))
    frames = 0
    while pos < len(data) and frames < 2:
        block = data[pos]
        if block == 0x3B:  # trailer
            break
        if block == 0x21:  # extension: introducer, label, sub-blocks
            pos += 2
        elif block == 0x2C:  # image descriptor
            frames += 1
            if pos + 10 > len(data):
                break
            local = data[pos + 9]
            pos += 10
            if local & 0x80:
                pos += 3 * (2 ** ((local & 0x07) + 1))
            pos += 1  # LZW minimum code size
        else:
            break
        while pos < len(data):  # skip data sub-blocks
            size = data[pos]
            pos += 1
            if size == 0:
                break
            pos += size
    return width, height, frames > 1


def _webp_info(data: bytes) -> tuple[int | None, int | None, bool]:
    width = height = None
    animated = False
    pos = 12
    while pos + 8 <= len(data):
        kind = data[pos : pos + 4]
        size = struct.unpack("<I", data[pos + 4 : pos + 8])[0]
        body = data[pos + 8 : pos + 8 + size]
        if kind == b"VP8X" and len(body) >= 10:
            animated = bool(body[0] & 0x02)
            width = int.from_bytes(body[4:7], "little") + 1
            height = int.from_bytes(body[7:10], "little") + 1
        elif kind == b"VP8 " and len(body) >= 10 and width is None:
            width = struct.unpack("<H", body[6:8])[0] & 0x3FFF
            height = struct.unpack("<H", body[8:10])[0] & 0x3FFF
        elif kind == b"VP8L" and len(body) >= 5 and width is None:
            bits = struct.unpack("<I", body[1:5])[0]
            width = (bits & 0x3FFF) + 1
            height = ((bits >> 14) & 0x3FFF) + 1
        elif kind in (b"ANIM", b"ANMF"):
            animated = True
        pos += 8 + size + (size & 1)
    return width, height, animated


def looks_like_svg(data: bytes) -> bool:
    head = data[:512].lstrip().lower()
    return head.startswith((b"<svg", b"<?xml")) or b"<svg" in head


def inspect_image(data: bytes) -> ImageInfo:
    """Format, size and animation of an image we accept as an emoji (EmojiError otherwise)."""
    fmt = sniff_format(data[:64])
    if fmt is None:
        if looks_like_svg(data):
            raise EmojiError(415, "SVG images can't be emoji (they can carry scripts). Use PNG, GIF, JPEG or WebP.")
        raise EmojiError(415, "That isn't a PNG, GIF, JPEG or WebP image.")
    try:
        if fmt == "png":
            width, height, animated = _png_info(data)
        elif fmt == "gif":
            width, height, animated = _gif_info(data)
        elif fmt == "webp":
            width, height, animated = _webp_info(data)
        elif fmt == "jpeg":
            size = _jpeg_size(data[:262144])
            width, height, animated = (size[0], size[1], False) if size else (None, None, False)
        else:
            raise EmojiError(415, f"{fmt.upper()} images can't be emoji. Use PNG, GIF, JPEG or WebP.")
    except (struct.error, IndexError):
        width = height = None
        animated = False
    return ImageInfo(fmt, width or None, height or None, animated)


# --------------------------------------------------------------------------- safe downloads


def public_address(ip: str) -> bool:
    """True only for globally routable unicast addresses (also unwrapping IPv4-in-IPv6 forms)."""
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address):
        for embedded in (addr.ipv4_mapped, addr.sixtofour, addr.teredo[1] if addr.teredo else None):
            if embedded is not None and not public_address(str(embedded)):
                return False
    if (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_reserved
            or addr.is_unspecified):
        return False
    return addr.is_global


def resolve(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(info[4][0] for info in infos))


def tls_context() -> ssl.SSLContext:
    """The OS trust store when truststore is around (it comes with anthropic), else certifi, else defaults."""
    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        pass
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _connect_any(ips: list[str], port: int, timeout: float) -> socket.socket:
    """Connect to the first of these (already checked) addresses that answers, e.g. IPv4 when IPv6 is down."""
    deadline = time.monotonic() + timeout
    last: OSError = TimeoutError("timed out")
    for ip in ips:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            return socket.create_connection((ip, port), min(remaining, 5.0) if len(ips) > 1 else remaining)
        except OSError as exc:
            last = exc
    raise last


class _PinnedHTTP(http.client.HTTPConnection):
    """Connects only to the addresses we checked (no second DNS lookup), still sending Host: <name>."""

    def __init__(self, host: str, port: int, ips: list[str], timeout: float):
        super().__init__(host, port, timeout=timeout)
        self._ips = ips

    def connect(self) -> None:
        self.sock = _connect_any(self._ips, self.port, self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, ips: list[str], timeout: float, context: ssl.SSLContext):
        super().__init__(host, port, timeout=timeout, context=context)
        self._ips = ips
        self._ctx = context

    def connect(self) -> None:
        sock = _connect_any(self._ips, self.port, self.timeout)
        self.sock = self._ctx.wrap_socket(sock, server_hostname=self.host)


def fetch_image(
    url: str,
    *,
    resolver: Callable[[str, int], list[str]] = resolve,
    allowed: Callable[[str], bool] = public_address,
    max_bytes: int = MAX_BYTES,
    timeout: float = FETCH_TIMEOUT,
) -> bytes:
    """Download an image link safely (see the module docstring). Raises EmojiError."""
    return fetch_url(url, resolver=resolver, allowed=allowed, max_bytes=max_bytes, timeout=timeout)[0]


def fetch_url(
    url: str,
    *,
    resolver: Callable[[str, int], list[str]] = resolve,
    allowed: Callable[[str], bool] = public_address,
    max_bytes: int = MAX_BYTES,
    timeout: float = FETCH_TIMEOUT,
    accept: str = "image/avif,image/webp,image/apng,image/png,image/gif,image/*;q=0.8",
    what: str = "image",
    truncate: bool = False,
) -> tuple[bytes, str, str]:
    """GET a link safely (see the module docstring): (body, content type, final URL). With
    `truncate`, a body over `max_bytes` is cut there instead of refused (enough of a page's <head>).
    Raises EmojiError."""
    deadline = time.monotonic() + timeout
    noun = what
    for _hop in range(MAX_REDIRECTS + 1):
        parts = urlsplit(url.strip())
        if parts.scheme not in ("http", "https"):
            raise EmojiError(400, f"Only http:// and https:// {noun} links work.")
        if not parts.hostname:
            raise EmojiError(400, "That link has no host name.")
        if parts.username or parts.password:
            raise EmojiError(400, "Links with a user name or password aren't supported.")
        port = parts.port or (443 if parts.scheme == "https" else 80)
        try:
            addresses = resolver(parts.hostname, port)
        except (OSError, UnicodeError):
            raise EmojiError(400, f"Couldn't find {parts.hostname}.") from None
        if not addresses:
            raise EmojiError(400, f"Couldn't find {parts.hostname}.")
        if not all(allowed(a) for a in addresses):
            raise EmojiError(400, "That link points at a private or local address, so Backchannel won't fetch it.")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise EmojiError(504, f"Downloading the {noun} took longer than {int(timeout)} seconds.")
        if parts.scheme == "https":
            conn: http.client.HTTPConnection = _PinnedHTTPS(parts.hostname, port, addresses, remaining, tls_context())
        else:
            conn = _PinnedHTTP(parts.hostname, port, addresses, remaining)
        target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        try:
            conn.request("GET", target, headers={
                "User-Agent": USER_AGENT,
                "Accept": accept,
                "Accept-Encoding": "identity",
            })
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                if not location:
                    raise EmojiError(502, "The link redirected without saying where to.")
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise EmojiError(502, f"The link answered with HTTP {response.status}.")
            declared = response.getheader("Content-Length")
            if not truncate and declared and declared.isdigit() and int(declared) > max_bytes:
                raise EmojiError(413, f"That {noun} is larger than {max_bytes // (1024 * 1024)} MB.")
            chunks: list[bytes] = []
            total = 0
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise EmojiError(504, f"Downloading the {noun} took longer than {int(timeout)} seconds.")
                if conn.sock is not None:
                    conn.sock.settimeout(left)  # a slow trickle can't stretch the timeout
                chunk = response.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    if truncate:
                        chunks.append(chunk[:max_bytes - (total - len(chunk))])
                        break
                    raise EmojiError(413, f"That {noun} is larger than {max_bytes // (1024 * 1024)} MB.")
                chunks.append(chunk)
            return b"".join(chunks), (response.getheader("Content-Type") or "").lower(), url
        except (socket.timeout, TimeoutError):
            raise EmojiError(504, f"Downloading the {noun} took longer than {int(timeout)} seconds.") from None
        except ssl.SSLError as exc:
            raise EmojiError(502, f"Couldn't make a secure connection to {parts.hostname} ({exc.reason}).") from None
        except (OSError, http.client.HTTPException) as exc:
            raise EmojiError(502, f"Couldn't download the {noun}: {exc}.") from None
        finally:
            conn.close()
    raise EmojiError(502, "That link redirected too many times (more than 3).")


# --------------------------------------------------------------------------- the store


class EmojiStore:
    def __init__(self, state: State, folder: Path, convert_heic: Callable[[bytes], bytes | None]):
        self.state = state
        self.folder = Path(folder)
        self.convert_heic = convert_heic  # HEIC bytes -> PNG bytes (sips), or None
        self.fetch = fetch_image  # replaceable in tests

    @staticmethod
    def to_json(row: Any) -> dict[str, Any]:
        return {
            "name": row["name"],
            "url": f"/api/emoji/{quote(row['name'], safe='')}/image",
            "mimeType": row["mime_type"],
            "width": row["width"],
            "height": row["height"],
            "animated": bool(row["animated"]),
            "source": row["source"],
            "createdAt": iso(row["created_at"]),
        }

    def list(self) -> list[dict[str, Any]]:
        return [self.to_json(r) for r in self.state.list_emoji()]

    def path(self, name: str) -> tuple[Path, str] | None:
        row = self.state.get_emoji(name)
        if row is None:
            return None
        path = self.folder / row["file"]
        return (path, row["mime_type"]) if path.is_file() else None

    def add(self, raw_name: str, data: bytes, source: str, source_ref: str | None = None) -> dict[str, Any]:
        name = clean_name(raw_name)
        if self.state.get_emoji(name) is not None:
            raise EmojiError(409, f"There's already an emoji called :{name}:.")
        if len(data) > MAX_BYTES:
            raise EmojiError(413, "That image is larger than 5 MB.")
        if not data:
            raise EmojiError(400, "That file is empty.")
        if sniff_format(data[:64]) == "heic":
            converted = self.convert_heic(data)
            if not converted or sniff_format(converted[:64]) != "png":
                raise EmojiError(415, "HEIC images need macOS's sips to convert. Use PNG, GIF, JPEG or WebP.")
            data = converted
        info = inspect_image(data)
        self.folder.mkdir(parents=True, exist_ok=True)
        file_name = f"{uuid.uuid4().hex}{EXTENSIONS[info.fmt]}"
        tmp = self.folder / f".{file_name}.tmp"
        tmp.write_bytes(data)
        os.replace(tmp, self.folder / file_name)
        row = {
            "name": name, "file": file_name, "mime_type": MIMES[info.fmt], "width": info.width,
            "height": info.height, "animated": int(info.animated), "source": source, "source_ref": source_ref,
            "sha256": hashlib.sha256(data).hexdigest(), "created_at": time.time(),
        }
        if not self.state.add_emoji(row):  # lost a race for the name
            (self.folder / file_name).unlink(missing_ok=True)
            raise EmojiError(409, f"There's already an emoji called :{name}:.")
        return self.to_json(self.state.get_emoji(name))

    def add_link(self, raw_name: str, url: str) -> dict[str, Any]:
        name = clean_name(raw_name)
        if self.state.get_emoji(name) is not None:
            raise EmojiError(409, f"There's already an emoji called :{name}:.")
        return self.add(name, self.fetch(url), "link", url.strip())

    def delete(self, raw_name: str) -> None:
        name = (raw_name or "").strip().strip(":").lower()
        row = self.state.delete_emoji(name)
        if row is not None:
            (self.folder / row["file"]).unlink(missing_ok=True)
