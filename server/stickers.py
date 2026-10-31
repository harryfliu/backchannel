"""Stickers and Genmoji from Harry's iMessage history.

Attachments with `is_sticker = 1` or a Genmoji identifier
(`emoji_image_content_identifier`, macOS 15+), including sticker tapbacks,
grouped by the identifier or else by a hash of the file (hashes are cached in
state.db by guid + mtime), counted by use, most used first. Files that are
missing, or that can't be made into something an <img> can draw, are skipped.
"""

from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .chatdb import ChangeTracker, ChatDB
from .imagetypes import BROWSER_IMAGES, MIME
from .media import Media
from .state import State
from .timeutil import apple_to_ns, iso_apple

CACHE_SECONDS = 300.0


def image_url(guid: str) -> str:
    return f"/api/stickers/{quote(guid, safe='')}/image"


class StickerLibrary:
    def __init__(self, db: ChatDB, tracker: ChangeTracker, state: State, media: Media):
        self.db = db
        self.tracker = tracker
        self.state = state
        self.media = media
        self._lock = threading.Lock()
        self._cache: tuple[int, float, list[dict[str, Any]]] | None = None

    # ------------------------------------------------------------------ helpers

    def _rows(self, guid: str | None = None) -> list[Any]:
        cols = self.db.columns("attachment")
        genmoji = "emoji_image_content_identifier" in cols
        select = self.db.select_list("attachment", "a", (
            "ROWID", "guid", "filename", "transfer_name", "is_sticker", "emoji_image_content_identifier",
            "emoji_image_short_description"))
        where = "(a.is_sticker = 1" + (" OR a.emoji_image_content_identifier IS NOT NULL)" if genmoji else ")")
        params: tuple = ()
        if guid is not None:
            where += " AND a.guid = ?"
            params = (guid,)
        return self.db.query(
            f"""SELECT {select}, m.date AS mdate, m.is_from_me AS from_me
                FROM attachment a
                JOIN message_attachment_join maj ON maj.attachment_id = a.ROWID
                JOIN message m ON m.ROWID = maj.message_id
                WHERE {where}""",
            params,
        )

    @staticmethod
    def _sha256(path: Path) -> str | None:
        digest = hashlib.sha256()
        try:
            with path.open("rb") as f:
                for block in iter(lambda: f.read(1 << 16), b""):
                    digest.update(block)
        except OSError:
            return None
        return digest.hexdigest()

    def _hashes(self, files: dict[str, Path]) -> dict[str, str]:
        """guid -> SHA-256 of its file; cached in state.db by guid + mtime + size, new ones written in one go."""
        cached = self.state.cached_hashes(list(files))
        out: dict[str, str] = {}
        fresh: list[tuple[str, float, int, str]] = []
        for guid, path in files.items():
            try:
                stat = path.stat()
            except OSError:
                continue
            known = cached.get(guid)
            if known and known[0] == stat.st_mtime and known[1] == stat.st_size:
                out[guid] = known[2]
                continue
            value = self._sha256(path)
            if value is not None:
                out[guid] = value
                fresh.append((guid, stat.st_mtime, stat.st_size, value))
        self.state.store_hashes(fresh)
        return out

    # ------------------------------------------------------------------ the list

    def list(self, limit: int = 300) -> list[dict[str, Any]]:
        generation = self.tracker.check()
        with self._lock:
            if self._cache and self._cache[0] == generation and time.monotonic() - self._cache[1] < CACHE_SECONDS:
                return self._cache[2][:limit]
        rows = self._rows()
        paths: dict[str, Path] = {}
        for row in rows:  # files that exist and that an <img> can draw (as is, or converted)
            guid = row["guid"]
            if guid not in paths:
                resolved = self.media.resolve(row["filename"])
                if resolved is not None and resolved.is_file() and self.media.drawable(resolved):
                    paths[guid] = resolved
        hashes = self._hashes({row["guid"]: paths[row["guid"]] for row in rows
                               if row["guid"] in paths and not row["emoji_image_content_identifier"]})
        groups: dict[str, dict[str, Any]] = {}
        for row in rows:
            guid = row["guid"]
            if guid not in paths:
                continue
            identifier = row["emoji_image_content_identifier"]
            if identifier:
                key = f"g:{identifier}"
            elif guid in hashes:
                key = f"h:{hashes[guid]}"
            else:
                continue
            when = apple_to_ns(row["mdate"])
            group = groups.setdefault(key, {
                "kind": "genmoji" if identifier else "sticker", "uses": 0, "last": 0, "guid": guid,
                "sent": False, "description": None,
            })
            group["uses"] += 1
            group["sent"] = group["sent"] or bool(row["from_me"])
            description = (row["emoji_image_short_description"] or "").strip() or None
            if when >= group["last"]:
                group["last"] = when
                group["guid"] = guid
                group["description"] = description or group["description"]
            elif group["description"] is None:
                group["description"] = description
        ranked = sorted(groups.values(), key=lambda g: (g["uses"], g["last"]), reverse=True)
        items = [{
            "id": g["guid"],
            "kind": g["kind"],
            "description": g["description"],
            "url": image_url(g["guid"]),
            "timesUsed": g["uses"],
            "lastUsedAt": iso_apple(g["last"]),
            "sentByMe": g["sent"],
        } for g in ranked]
        with self._lock:
            self._cache = (generation, time.monotonic(), items)
        return items[:limit]

    def invalidate(self) -> None:
        with self._lock:
            self._cache = None

    # ------------------------------------------------------------------ images

    def image(self, guid: str) -> tuple[Path, str] | None:
        """A file an <img> can draw for this sticker: PNG (transparency kept) for HEIC/TIFF."""
        rows = self._rows(guid)
        if not rows:
            return None
        path = self.media.resolve(rows[0]["filename"])
        if path is None or not path.is_file():
            return None
        fmt = self.media.image_format(path)
        if fmt in BROWSER_IMAGES:
            return path, MIME[fmt]
        converted = self.media.png_file(path, f"sticker-{guid}") if fmt else None
        return (converted, "image/png") if converted is not None else None

    def image_bytes(self, guid: str) -> bytes | None:
        found = self.image(guid)
        return found[0].read_bytes() if found else None
