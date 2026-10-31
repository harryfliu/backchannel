"""Custom emoji: uploads, links (safe fetching), validation, serving."""

from __future__ import annotations

import http.server
import struct
import sys
import threading
import time
import zlib
from pathlib import Path

import pytest

import media_gen
from server.emoji import EmojiError, fetch_image, inspect_image, public_address
from ts_shapes import validate

PNG = media_gen.png(32, 24, lambda x, y: (255, x * 8, y * 10))
JPEG = media_gen.jpeg(16, 8, lambda bx, by: (200, 30, 30))


def gif(frames: int, width: int = 1, height: int = 1) -> bytes:
    out = bytearray(b"GIF89a" + struct.pack("<HH", width, height) + b"\x80\x00\x00" + b"\x00\x00\x00\xff\xff\xff")
    out += b"\x21\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00"  # loop forever
    for _ in range(frames):
        out += b"\x21\xf9\x04\x04\x0a\x00\x00\x00"  # graphic control extension
        out += b"\x2c" + struct.pack("<HHHH", 0, 0, width, height) + b"\x00" + b"\x02\x02\x44\x01\x00"
    return bytes(out + b"\x3b")


def apng(frames: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    ihdr_end = 8 + 25
    return PNG[:ihdr_end] + chunk(b"acTL", struct.pack(">II", frames, 0)) + PNG[ihdr_end:]


def webp_animated(width: int, height: int) -> bytes:
    vp8x = bytes([0x12, 0, 0, 0]) + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    body = b"WEBP" + b"VP8X" + struct.pack("<I", 10) + vp8x + b"ANIM" + struct.pack("<I", 6) + b"\x00" * 6
    return b"RIFF" + struct.pack("<I", len(body)) + body


def webp_lossless(width: int, height: int) -> bytes:
    bits = (width - 1) | ((height - 1) << 14)
    vp8l = b"\x2f" + struct.pack("<I", bits) + b"\x00" * 5
    body = b"WEBP" + b"VP8L" + struct.pack("<I", len(vp8l)) + vp8l
    return b"RIFF" + struct.pack("<I", len(body)) + body


def upload(harness, name, data, filename="x.png"):
    return harness.client.post("/api/emoji", data={"name": name}, files={"file": (filename, data, "application/octet-stream")})


# --------------------------------------------------------------------------- inspection


def test_inspect_formats():
    info = inspect_image(PNG)
    assert (info.fmt, info.width, info.height, info.animated) == ("png", 32, 24, False)
    assert inspect_image(JPEG).width == 16 and inspect_image(JPEG).height == 8
    assert inspect_image(gif(1, 5, 7)).animated is False and inspect_image(gif(1, 5, 7)).width == 5
    assert inspect_image(gif(3)).animated is True
    assert inspect_image(apng(2)).animated is True and inspect_image(apng(1)).animated is False
    animated = inspect_image(webp_animated(128, 64))
    assert (animated.fmt, animated.width, animated.height, animated.animated) == ("webp", 128, 64, True)
    lossless = inspect_image(webp_lossless(40, 30))
    assert (lossless.width, lossless.height, lossless.animated) == (40, 30, False)


@pytest.mark.parametrize("data, status", [
    (b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', 415),
    (b"  <svg viewBox='0 0 1 1'></svg>", 415),
    (media_gen.tiff(4, 4, (1, 2, 3)), 415),
    (b"%PDF-1.4 not an image", 415),
])
def test_rejected_images(data, status):
    with pytest.raises(EmojiError) as err:
        inspect_image(data)
    assert err.value.status == status


# --------------------------------------------------------------------------- uploads


def test_upload_list_serve_delete(rw):
    response = upload(rw, ":Party-Parrot:", gif(2, 16, 16), "party.gif")
    assert response.status_code == 200, response.text
    emoji = response.json()
    assert emoji == {"name": "party-parrot", "url": "/api/emoji/party-parrot/image", "mimeType": "image/gif",
                     "width": 16, "height": 16, "animated": True, "source": "upload", "createdAt": emoji["createdAt"]}
    assert not validate(emoji, "CustomEmoji")
    assert upload(rw, "a+b", PNG).status_code == 200
    names = [e["name"] for e in rw.get("/api/emoji")["items"]]
    assert names == ["a+b", "party-parrot"]  # sorted by name
    image = rw.client.get("/api/emoji/a%2Bb/image")
    assert image.status_code == 200 and image.content == PNG and image.headers["content-type"] == "image/png"
    assert "sandbox" in image.headers["content-security-policy"] and image.headers["x-content-type-options"] == "nosniff"
    assert rw.client.delete("/api/emoji/a%2Bb").status_code == 204
    assert rw.client.delete("/api/emoji/a%2Bb").status_code == 204  # idempotent
    assert rw.client.get("/api/emoji/a%2Bb/image").status_code == 404
    assert len(list((rw.settings.state_dir / "emoji").iterdir())) == 1


def test_upload_validation(rw):
    assert upload(rw, "ok", PNG).status_code == 200
    taken = upload(rw, "OK", PNG)
    assert taken.status_code == 409 and ":ok:" in taken.json()["error"]
    for bad in ("a", "has space", "bang!", "x" * 41, ""):
        assert upload(rw, bad, PNG).status_code in (400,), bad
    svg = upload(rw, "logo", b"<svg xmlns='http://www.w3.org/2000/svg'/>", "logo.svg")
    assert svg.status_code == 415 and "SVG" in svg.json()["error"]
    assert upload(rw, "tiff", media_gen.tiff(4, 4, (1, 2, 3))).status_code == 415
    assert upload(rw, "big", b"\x89PNG\r\n\x1a\n" + b"\x00" * (5 * 1024 * 1024 + 10)).status_code == 413
    assert upload(rw, "empty", b"").status_code == 400
    heic = upload(rw, "heic", media_gen.heic_iphone(64, 64))
    assert heic.status_code == 415 and "sips" in heic.json()["error"]  # no sips on Linux


FAKE_SIPS = r'''#!{python}
import sys
args = sys.argv[1:]
out = args[args.index("--out") + 1]
open(out, "wb").write({png!r} if "png" in args else b"")
'''


def test_heic_upload_becomes_png_with_sips(rw, tmp_path):
    sips = tmp_path / "sips"
    sips.write_text(FAKE_SIPS.format(python=sys.executable, png=PNG))
    sips.chmod(0o755)
    rw.services.media.sips = str(sips)
    response = upload(rw, "photo", media_gen.heic_iphone(64, 64), "photo.heic")
    assert response.status_code == 200, response.text
    assert response.json()["mimeType"] == "image/png" and response.json()["width"] == 32
    leftovers = [p.name for p in rw.settings.cache_dir.rglob("*convert*")]
    assert leftovers == []  # the one-off conversion leaves nothing in the cache


# --------------------------------------------------------------------------- links


def test_public_addresses():
    for ip in ("10.0.0.1", "192.168.1.1", "172.16.0.1", "127.0.0.1", "::1", "fe80::1", "169.254.1.1",
               "224.0.0.1", "0.0.0.0", "100.64.0.1", "::ffff:127.0.0.1", "2002:7f00:1::1", "240.0.0.1", "fc00::1"):
        assert not public_address(ip), ip
    for ip in ("8.8.8.8", "151.101.1.69", "2606:4700:4700::1111"):
        assert public_address(ip), ip


class Handler(http.server.BaseHTTPRequestHandler):
    seen_agents: list[str] = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        Handler.seen_agents.append(self.headers.get("User-Agent", ""))
        path = self.path
        if path == "/party.gif":
            self._send(200, gif(2, 8, 8), "image/gif")
        elif path.startswith("/hop"):
            n = int(path[4:])
            self.send_response(302)
            self.send_header("Location", f"/hop{n - 1}" if n > 1 else "/party.gif")
            self.end_headers()
        elif path == "/to-private":
            self.send_response(301)
            self.send_header("Location", "http://10.0.0.5/secret.png")
            self.end_headers()
        elif path == "/big":
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            try:
                self.wfile.write(b"\x89PNG\r\n\x1a\n")
                for _ in range(6 * 16):
                    self.wfile.write(b"\x00" * 65536)
            except (BrokenPipeError, ConnectionResetError):
                pass
        elif path == "/slow":
            time.sleep(3)
            self._send(200, PNG, "image/png")
        elif path == "/page.html":
            self._send(200, b"<html><body>not an image</body></html>", "text/html")
        else:
            self._send(404, b"nope", "text/plain")

    def _send(self, status, body, ctype):
        try:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the client gave up (the timeout test)


@pytest.fixture
def web():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def local_fetch(url, **kw):
    """fetch_image with loopback allowed (only for these tests) and everything else still checked."""
    return fetch_image(url, allowed=lambda ip: ip == "127.0.0.1" or public_address(ip), **kw)


def test_fetch_follows_up_to_three_redirects(web):
    Handler.seen_agents.clear()
    assert local_fetch(f"{web}/hop3") == gif(2, 8, 8)
    assert all("Mozilla/5.0" in agent for agent in Handler.seen_agents)
    with pytest.raises(EmojiError, match="more than 3"):
        local_fetch(f"{web}/hop4")


def test_fetch_refuses_private_hops_and_bad_links(web):
    with pytest.raises(EmojiError, match="private or local"):
        local_fetch(f"{web}/to-private")  # the redirect target is checked too
    with pytest.raises(EmojiError, match="private or local"):
        fetch_image(f"{web}/party.gif")  # the real check refuses loopback outright
    for bad in ("ftp://example.com/x.gif", "file:///etc/passwd", "javascript:alert(1)", "http://user:pw@example.com/x"):
        with pytest.raises(EmojiError):
            fetch_image(bad)


def test_fetch_limits(web):
    with pytest.raises(EmojiError) as big:
        local_fetch(f"{web}/big")
    assert big.value.status == 413
    with pytest.raises(EmojiError) as slow:
        local_fetch(f"{web}/slow", timeout=1)
    assert slow.value.status == 504
    with pytest.raises(EmojiError, match="HTTP 404"):
        local_fetch(f"{web}/missing.png")


def test_link_route(rw, web):
    rw.services.emoji.fetch = local_fetch
    response = rw.client.post("/api/emoji/link", json={"name": "parrot", "url": f"{web}/hop1"})
    assert response.status_code == 200, response.text
    assert response.json()["source"] == "link" and response.json()["animated"] is True
    html = rw.client.post("/api/emoji/link", json={"name": "page", "url": f"{web}/page.html"})
    assert html.status_code == 415
    taken = rw.client.post("/api/emoji/link", json={"name": "parrot", "url": f"{web}/party.gif"})
    assert taken.status_code == 409
    rw.services.emoji.fetch = fetch_image  # the real guard
    blocked = rw.client.post("/api/emoji/link", json={"name": "local", "url": f"{web}/party.gif"})
    assert blocked.status_code == 400 and "private or local" in blocked.json()["error"]


def test_emoji_files_live_in_the_state_dir(rw):
    upload(rw, "dot", PNG)
    files = list(Path(rw.settings.state_dir, "emoji").iterdir())
    assert len(files) == 1 and files[0].suffix == ".png" and files[0].read_bytes() == PNG


def test_emoji_writes_are_protected(rw):
    cross = rw.client.post("/api/emoji", data={"name": "evil"}, files={"file": ("x.png", PNG, "image/png")},
                           headers={"sec-fetch-site": "cross-site"})
    assert cross.status_code == 403
    form = rw.client.post("/api/emoji/link", data={"name": "evil", "url": "https://example.com/x.png"},
                          files={"file": ("x.png", PNG, "image/png")})
    assert form.status_code == 415  # multipart is for /api/emoji (and /files) only
    assert rw.client.delete("/api/emoji/ok", headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert rw.get("/api/emoji")["items"] == []


def test_fetch_tries_each_checked_address(web):
    port = int(web.rsplit(":", 1)[1])
    loopbacks = ("::1", "127.0.0.1")
    data = fetch_image(f"http://emoji.example:{port}/party.gif", resolver=lambda host, p: list(loopbacks),
                       allowed=lambda ip: ip in loopbacks)  # nothing listens on [::1]: falls back to IPv4
    assert data == gif(2, 8, 8)
    with pytest.raises(EmojiError, match="private or local"):  # every address has to pass, not just one
        fetch_image("http://mixed.example/x.png", resolver=lambda host, p: ["8.8.8.8", "10.0.0.1"])
