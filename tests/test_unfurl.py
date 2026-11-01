"""Link previews for links the user sent without one (server/unfurl.py)."""

from __future__ import annotations

import threading
import time
from urllib.parse import quote

import pytest

from server import emoji
from server.unfurl import Unfurler, parse_page

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PAGE = b"""<!doctype html><html><head>
<meta charset="utf-8"><title>Fallback title</title>
<meta property="og:title" content="Tartine Bakery &amp; Cafe">
<meta property="og:site_name" content="Tartine">
<meta property="og:description" content="Bread, pastries and   coffee in the Mission.">
<meta property="og:image" content="/img/hero.png">
</head><body><meta property="og:title" content="not this"></body></html>"""


def test_parse_page_reads_open_graph_and_falls_back():
    page = parse_page(PAGE, "text/html; charset=utf-8", "https://www.tartine.example/menu")
    assert page == {"title": "Tartine Bakery & Cafe", "siteName": "Tartine",
                    "summary": "Bread, pastries and coffee in the Mission.",
                    "image": "https://www.tartine.example/img/hero.png"}
    twitter = b'<head><meta name="twitter:title" content="T"><meta name="twitter:image" content="https://cdn.example/x.jpg">'
    assert parse_page(twitter, "text/html", "https://example.com/a") == {
        "title": "T", "siteName": "example.com", "summary": None, "image": "https://cdn.example/x.jpg"}
    plain = parse_page(b"<html><head><title> Just a\n title </title></head></html>", "text/html", "https://www.x.example/")
    assert plain == {"title": "Just a title", "siteName": "x.example", "summary": None, "image": None}
    assert parse_page(b"<html><body>nothing</body></html>", "text/html", "https://x.example/") is None
    # Only web images; a javascript: or data: "image" is dropped.
    sneaky = b'<head><title>t</title><meta property="og:image" content="javascript:alert(1)">'
    assert parse_page(sneaky, "text/html", "https://x.example/")["image"] is None
    latin = "<head><title>Caf\xe9</title>".encode("latin-1")
    assert parse_page(latin, "text/html; charset=iso-8859-1", "https://x.example/")["title"] == "Café"


class FakeWeb:
    """Stands in for emoji.fetch_url."""

    def __init__(self, pages: dict[str, tuple[bytes, str]]):
        self.pages = pages
        self.calls: list[str] = []
        self.gate = threading.Event()
        self.gate.set()

    def __call__(self, url: str, **kw):
        self.calls.append(url)
        self.gate.wait(5)
        if url not in self.pages:
            raise emoji.EmojiError(502, "The link answered with HTTP 404.")
        body, content_type = self.pages[url]
        return body, content_type, url


def wait_until(check, timeout: float = 5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if check():
            return True
        time.sleep(0.02)
    raise AssertionError("timed out")


def test_unfurler_fetches_once_caches_and_announces(tmp_path):
    from server.state import State

    state = State(tmp_path / "state.db")
    web = FakeWeb({"https://www.tartine.example/menu": (PAGE, "text/html; charset=utf-8"),
                   "https://www.tartine.example/img/hero.png": (PNG, "image/png")})
    ready: list[list[str]] = []
    unfurler = Unfurler(state, tmp_path / "unfurl", fetch=web, on_ready=ready.append)
    web.gate.clear()
    url = "https://www.tartine.example/menu"
    assert unfurler.preview(url, "g1") is None  # not known yet: fetched in the background
    assert unfurler.preview(url, "g2") is None  # already on its way: no second fetch
    web.gate.set()
    wait_until(lambda: ready)
    assert ready == [["g1", "g2"]]
    preview = unfurler.preview(url, "g3")
    assert preview["title"] == "Tartine Bakery & Cafe" and preview["siteName"] == "Tartine"
    assert preview["url"] == url and preview["imageUrl"].startswith("/api/unfurl/")
    image_id = preview["imageUrl"].rsplit("/", 1)[1]
    path, mime = unfurler.image_path(image_id)
    assert mime == "image/png" and path.read_bytes() == PNG
    assert web.calls == [url, "https://www.tartine.example/img/hero.png"]
    assert unfurler.image_path("../../etc/passwd") is None and unfurler.image_path("0" * 32) is None
    unfurler.close()


def test_unfurler_remembers_failures_and_takes_raster_images_only(tmp_path):
    from server.state import State

    state = State(tmp_path / "state.db")
    svg_page = b'<head><title>Logo</title><meta property="og:image" content="https://x.example/logo.svg">'
    web = FakeWeb({"https://x.example/": (svg_page, "text/html"),
                   "https://x.example/logo.svg": (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/svg+xml"),
                   "https://x.example/pic.png": (PNG, "image/png"),
                   "https://x.example/file.zip": (b"PK\x03\x04", "application/zip")})
    unfurler = Unfurler(state, tmp_path / "unfurl", fetch=web)
    for url in ("https://x.example/", "https://gone.example/", "https://x.example/pic.png", "https://x.example/file.zip"):
        unfurler.preview(url)
    wait_until(lambda: all(state.get_unfurl(u) for u in ("https://x.example/", "https://gone.example/",
                                                         "https://x.example/pic.png", "https://x.example/file.zip")))
    assert unfurler.preview("https://x.example/") == {"url": "https://x.example/", "title": "Logo",
                                                      "siteName": "x.example", "summary": None, "imageUrl": None}
    assert unfurler.preview("https://gone.example/") is None  # remembered: not fetched again today
    assert unfurler.preview("https://x.example/file.zip") is None
    direct = unfurler.preview("https://x.example/pic.png")
    assert direct["title"] is None and direct["imageUrl"].startswith("/api/unfurl/")
    assert web.calls.count("https://gone.example/") == 1
    unfurler.close()


@pytest.fixture
def unfurling(make_rw):
    """A harness whose sends are dry-run into the fake chat.db, with link previews on and a fake web."""
    rw = make_rw()
    web = FakeWeb({"https://www.tartine.example/menu": (PAGE, "text/html; charset=utf-8"),
                   "https://www.tartine.example/img/hero.png": (PNG, "image/png")})
    services = rw.services
    services.unfurler = Unfurler(services.state, rw.settings.state_dir / "unfurl", fetch=web,
                                 on_ready=services._previews_ready)
    services.builder.unfurler = services.unfurler
    rw.web = web
    yield rw
    services.unfurler.close()


def test_a_link_you_send_gets_a_preview_and_the_ui_hears_about_it(unfurling):
    rw = unfurling
    conv = "dm:+14155550134"
    rw.post(f"/api/conversations/{quote(conv, safe='')}/messages",
            {"text": "lunch here? https://www.tartine.example/menu", "clientId": "u-1"})

    def updated(history):
        return [e["message"] for e in history if e["type"] == "message-updated"
                and (e["message"].get("linkPreview") or {}).get("title")]

    deadline = time.time() + 8
    found = []
    while time.time() < deadline and not found:
        rw.services.watcher.check()
        found = updated(rw.services.hub.history)
        time.sleep(0.05)
    assert found, [e["type"] for e in rw.services.hub.history][-20:]
    message = found[-1]
    assert message["fromMe"] and message["linkPreview"]["title"] == "Tartine Bakery & Cafe"
    image = rw.client.get(message["linkPreview"]["imageUrl"])
    assert image.status_code == 200 and image.headers["content-type"] == "image/png" and image.content == PNG
    assert image.headers["x-content-type-options"] == "nosniff"
    assert rw.client.get("/api/unfurl/" + "f" * 32).status_code == 404
    # Links other people send are never fetched (their previews come from Messages, or not at all).
    assert rw.web.calls == ["https://www.tartine.example/menu", "https://www.tartine.example/img/hero.png"]
