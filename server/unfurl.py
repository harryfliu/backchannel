"""Link previews for links the user sent without one.

Messages keeps a rich preview (LPLinkMetadata, see linkpreview.py) with a link only when it was
sent from Messages itself or a phone; links Backchannel sends through AppleScript are plain text.
For those (from the user, no preview in chat.db) the server reads the page's Open Graph / Twitter
card tags and <title> once, safely (emoji.fetch_url: http(s), public addresses only, 3 redirects,
time and size caps; never links other people sent), keeps the result in state.db, keeps the
page's image in <state>/unfurl/, and sends the message again (`message-updated`) with
`linkPreview` filled in.
"""

from __future__ import annotations

import hashlib
import html
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

from . import emoji
from .imagetypes import sniff_format
from .state import State

log = logging.getLogger("backchannel.unfurl")

PAGE_BYTES = 768 * 1024  # enough for any <head>
PAGE_TIMEOUT = 8.0
IMAGE_BYTES = 5 * 1024 * 1024
IMAGE_TIMEOUT = 10.0
FRESH_FOR = 7 * 86400
RETRY_AFTER = 86400  # a page that gave nothing is tried again a day later
SUMMARY_CHARS = 300
IMAGE_TYPES = {"png": "image/png", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}
ACCEPT_PAGE = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"
_ID = re.compile(r"^[0-9a-f]{32}$")

Fetch = Callable[..., tuple[bytes, str, str]]


class _Head(HTMLParser):
    """<meta> tags and <title> from a page's head (stops at <body>)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title = ""
        self._in_title = False
        self.done = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.done:
            return
        if tag == "body":
            self.done = True
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            values = {k.lower(): (v or "") for k, v in attrs}
            key = (values.get("property") or values.get("name") or "").strip().lower()
            content = values.get("content", "").strip()
            if key and content and key not in self.meta:
                self.meta[key] = content

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "head":
            self.done = True

    def handle_data(self, data: str) -> None:
        if self._in_title and not self.done and len(self.title) < 500:
            self.title += data


def _clean(text: str | None, limit: int = 200) -> str | None:
    if not text:
        return None
    text = " ".join(html.unescape(text).split())
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _site(url: str) -> str | None:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else (host or None)


def _decode(body: bytes, content_type: str) -> str:
    charset = None
    match = re.search(r"charset=([\w-]+)", content_type)
    if match:
        charset = match.group(1)
    else:
        head = body[:2048].decode("ascii", "ignore")
        found = re.search(r"""<meta[^>]+charset=["']?([\w-]+)""", head, re.IGNORECASE)
        charset = found.group(1) if found else None
    try:
        return body.decode(charset or "utf-8", "replace")
    except LookupError:
        return body.decode("utf-8", "replace")


def parse_page(body: bytes, content_type: str, page_url: str) -> dict[str, str | None] | None:
    """title / siteName / summary / image (absolute) from a page, or None when it names nothing."""
    parser = _Head()
    try:
        parser.feed(_decode(body, content_type))
    except Exception:  # html.parser copes with almost anything; never let a page break a message
        pass
    meta = parser.meta
    title = _clean(meta.get("og:title") or meta.get("twitter:title") or parser.title)
    summary = _clean(meta.get("og:description") or meta.get("twitter:description") or meta.get("description"),
                     SUMMARY_CHARS)
    image = meta.get("og:image:secure_url") or meta.get("og:image") or meta.get("og:image:url") \
        or meta.get("twitter:image") or meta.get("twitter:image:src")
    image = urljoin(page_url, html.unescape(image).strip()) if image else None
    if image and urlsplit(image).scheme not in ("http", "https"):
        image = None
    if not (title or summary or image):
        return None
    return {"title": title, "siteName": _clean(meta.get("og:site_name"), 80) or _site(page_url),
            "summary": summary, "image": image}


class Unfurler:
    """Fetches, caches and serves previews; `preview()` never blocks (it schedules a fetch)."""

    def __init__(self, state: State, folder: Path, *, fetch: Fetch = emoji.fetch_url,
                 on_ready: Callable[[list[str]], None] | None = None, workers: int = 2):
        self.state = state
        self.folder = folder
        self.fetch = fetch
        self.on_ready = on_ready  # called with the message guids waiting on a link, once it's fetched
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="unfurl")
        self._lock = threading.Lock()
        self._waiting: dict[str, set[str]] = {}

    # ------------------------------------------------------------------ for the message builder

    def preview(self, url: str, message_guid: str | None = None) -> dict[str, Any] | None:
        """The LinkPreview for `url` if it's known; otherwise None, and it's fetched in the background
        (then `on_ready([message_guid, …])`)."""
        row = self.state.get_unfurl(url)
        now = time.time()
        if row is not None:
            data, ok, fetched = row
            if (ok and now - fetched < FRESH_FOR) or (not ok and now - fetched < RETRY_AFTER):
                return data if ok else None
        self._schedule(url, message_guid)
        return row[0] if row is not None and row[1] else None

    def image_path(self, image_id: str) -> tuple[Path, str] | None:
        if not _ID.match(image_id or ""):
            return None
        for ext, mime in IMAGE_TYPES.items():
            path = self.folder / f"{image_id}.{ext}"
            if path.is_file():
                return path, mime
        return None

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------------ fetching

    def _schedule(self, url: str, guid: str | None) -> None:
        with self._lock:
            waiting = self._waiting.get(url)
            if waiting is not None:
                if guid:
                    waiting.add(guid)
                return
            self._waiting[url] = {guid} if guid else set()
        try:
            self._pool.submit(self._run, url)
        except RuntimeError:  # shutting down
            with self._lock:
                self._waiting.pop(url, None)

    def _run(self, url: str) -> None:
        data: dict[str, Any] | None = None
        try:
            data = self.fetch_preview(url)
        except Exception as exc:  # anything a page or a network can throw ends as "no preview"
            log.info("No link preview (%s)", type(exc).__name__)
        self.state.set_unfurl(url, data, ok=data is not None)
        with self._lock:
            guids = self._waiting.pop(url, set())
        if data is not None and guids and self.on_ready is not None:
            try:
                self.on_ready(sorted(guids))
            except Exception:
                log.exception("Couldn't announce a link preview")

    def fetch_preview(self, url: str) -> dict[str, Any] | None:
        body, content_type, final = self.fetch(url, accept=ACCEPT_PAGE, max_bytes=PAGE_BYTES, timeout=PAGE_TIMEOUT,
                                               what="page", truncate=True)
        if content_type.startswith("image/"):  # a link straight to a picture
            image_id = self._keep_image_bytes(url, body)
            return {"url": url, "title": None, "siteName": _site(final), "summary": None,
                    "imageUrl": f"/api/unfurl/{image_id}" if image_id else None} if image_id else None
        if "html" not in content_type and content_type:
            return None
        page = parse_page(body, content_type, final)
        if page is None:
            return None
        image_id = self._keep_image(page["image"]) if page["image"] else None
        return {"url": url, "title": page["title"], "siteName": page["siteName"], "summary": page["summary"],
                "imageUrl": f"/api/unfurl/{image_id}" if image_id else None}

    def _keep_image(self, image_url: str) -> str | None:
        try:
            body, _type, _final = self.fetch(image_url, max_bytes=IMAGE_BYTES, timeout=IMAGE_TIMEOUT)
        except Exception:
            return None
        return self._keep_image_bytes(image_url, body)

    def _keep_image_bytes(self, image_url: str, body: bytes) -> str | None:
        kind = sniff_format(body[:64])
        if kind == "jpg":
            kind = "jpeg"
        if kind not in IMAGE_TYPES:  # raster images only (no SVG, nothing that could run)
            return None
        image_id = hashlib.sha256(image_url.encode("utf-8", "replace")).hexdigest()[:32]
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / f"{image_id}.{kind}"
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(body)
        tmp.replace(path)
        return image_id
