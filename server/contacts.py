"""Contacts from the macOS AddressBook databases (read-only).

Phones match on their last 10 digits (like Harry's digest), emails lowercase.
Names prefer the nickname ("Mom"), then "First Last", then the organisation
for business cards. Photos come from ZTHUMBNAILIMAGEDATA (which often has a
leading byte before the JPEG) or from `Sources/<id>/Images/<unique id>`.
"""

from __future__ import annotations

import glob
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.request import pathname2url

from .handles import digits, normalize_handle, phone_key

log = logging.getLogger("backchannel.contacts")

RELOAD_INTERVAL = 30.0  # seconds between mtime checks


@dataclass
class Contact:
    key: str
    unique_id: str | None
    first: str | None
    last: str | None
    nickname: str | None
    organization: str | None
    db_path: Path
    phones: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    has_thumbnail: bool = False

    @property
    def name(self) -> str:
        if self.nickname:
            return self.nickname
        full = " ".join(p for p in (self.first, self.last) if p)
        return full or self.organization or ""

    @property
    def short_name(self) -> str:
        return self.nickname or self.first or self.last or self.organization or self.name

    @property
    def image_path(self) -> Path | None:
        if not self.unique_id:
            return None
        path = self.db_path.parent / "Images" / self.unique_id.split(":")[0]
        return path if path.is_file() else None

    @property
    def has_photo(self) -> bool:
        return self.has_thumbnail or self.image_path is not None


def _open_ro(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{pathname2url(path)}?mode=ro", uri=True, timeout=2)
    conn.row_factory = sqlite3.Row
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


class ContactBook:
    def __init__(self, globs: list[str]):
        self.globs = globs
        self._lock = threading.Lock()
        self._by_phone: dict[str, Contact] = {}
        self._by_exact: dict[str, Contact] = {}
        self._by_email: dict[str, Contact] = {}
        self._contacts: list[Contact] = []
        self._signature: tuple = ()
        self._checked = 0.0
        self.version = 0
        self.error: str | None = None
        self.reload_if_changed(force=True)

    # ------------------------------------------------------------------ loading

    def db_paths(self) -> list[str]:
        paths: list[str] = []
        for pattern in self.globs:
            for path in sorted(glob.glob(pattern)):
                if path not in paths and os.path.isfile(path):
                    paths.append(path)
        return paths

    def reload_if_changed(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._checked < RELOAD_INTERVAL:
            return
        self._checked = now
        paths = self.db_paths()
        signature = tuple((p, self._mtime(p)) for p in paths)
        if not force and signature == self._signature:
            return
        contacts: list[Contact] = []
        errors: list[str] = []
        for index, path in enumerate(paths):
            try:
                contacts.extend(self._load_db(index, path))
            except sqlite3.Error as exc:
                errors.append(f"{path}: {exc}")
                log.warning("Couldn't read AddressBook %s: %s", path, exc)
        by_phone: dict[str, Contact] = {}
        by_exact: dict[str, Contact] = {}
        by_email: dict[str, Contact] = {}
        # Later sources don't overwrite cards that have a real person name.
        for contact in contacts:
            for phone in contact.phones:
                key = phone_key(phone)
                target = by_phone if key else by_exact
                key = key or digits(phone)
                if key and (key not in target or not target[key].name):
                    target[key] = contact
            for email in contact.emails:
                if email not in by_email or not by_email[email].name:
                    by_email[email] = contact
        with self._lock:
            self._contacts = contacts
            self._by_phone, self._by_exact, self._by_email = by_phone, by_exact, by_email
            self._signature = signature
            self.error = "; ".join(errors) or None
            self.version += 1

    @staticmethod
    def _mtime(path: str) -> float:
        best = 0.0
        for suffix in ("", "-wal"):
            try:
                best = max(best, os.path.getmtime(path + suffix))
            except OSError:
                pass
        return best

    def _load_db(self, index: int, path: str) -> list[Contact]:
        conn = _open_ro(path)
        try:
            cols = _columns(conn, "ZABCDRECORD")
            pick = lambda name: f"r.{name}" if name in cols else f"NULL AS {name}"  # noqa: E731
            rows = conn.execute(
                f"""SELECT r.Z_PK, {pick('ZFIRSTNAME')}, {pick('ZLASTNAME')}, {pick('ZNICKNAME')},
                           {pick('ZORGANIZATION')}, {pick('ZUNIQUEID')},
                           {"r.ZTHUMBNAILIMAGEDATA IS NOT NULL AND length(r.ZTHUMBNAILIMAGEDATA) > 0"
                            if 'ZTHUMBNAILIMAGEDATA' in cols else "0"} AS has_thumb
                    FROM ZABCDRECORD r"""
            ).fetchall()
            contacts: dict[int, Contact] = {}
            for row in rows:
                names = (row["ZFIRSTNAME"], row["ZLASTNAME"], row["ZNICKNAME"], row["ZORGANIZATION"])
                if not any(n and str(n).strip() for n in names):
                    continue
                contacts[row["Z_PK"]] = Contact(
                    key=f"{index}:{row['Z_PK']}",
                    unique_id=row["ZUNIQUEID"],
                    first=(row["ZFIRSTNAME"] or "").strip() or None,
                    last=(row["ZLASTNAME"] or "").strip() or None,
                    nickname=(row["ZNICKNAME"] or "").strip() or None,
                    organization=(row["ZORGANIZATION"] or "").strip() or None,
                    db_path=Path(path),
                    has_thumbnail=bool(row["has_thumb"]),
                )
            for owner, number in conn.execute("SELECT ZOWNER, ZFULLNUMBER FROM ZABCDPHONENUMBER"):
                if owner in contacts and number:
                    contacts[owner].phones.append(number)
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "ZABCDEMAILADDRESS" in tables:
                ecols = _columns(conn, "ZABCDEMAILADDRESS")
                norm = "ZADDRESSNORMALIZED" if "ZADDRESSNORMALIZED" in ecols else "NULL"
                for owner, address, normalized in conn.execute(
                    f"SELECT ZOWNER, ZADDRESS, {norm} FROM ZABCDEMAILADDRESS"
                ):
                    value = (normalized or address or "").strip().lower()
                    if owner in contacts and value:
                        contacts[owner].emails.append(value)
            return [c for c in contacts.values() if c.phones or c.emails]
        finally:
            conn.close()

    # ------------------------------------------------------------------ lookups

    @property
    def count(self) -> int:
        return len(self._contacts)

    def lookup(self, handle: str) -> Contact | None:
        if not handle:
            return None
        value = normalize_handle(handle)
        with self._lock:
            if "@" in value:
                return self._by_email.get(value)
            key = phone_key(value)
            if key:
                return self._by_phone.get(key)
            return self._by_exact.get(digits(value))

    def photo(self, contact: Contact) -> tuple[bytes, str] | None:
        """(image bytes, mime type) for a contact, thumbnail first."""
        if contact.has_thumbnail:
            data = self._thumbnail(contact)
            if data:
                return data
        path = contact.image_path
        if path:
            try:
                return _sniff_image(path.read_bytes())
            except OSError:
                return None
        return None

    def _thumbnail(self, contact: Contact) -> tuple[bytes, str] | None:
        pk = int(contact.key.split(":")[1])
        try:
            conn = _open_ro(str(contact.db_path))
            try:
                row = conn.execute("SELECT ZTHUMBNAILIMAGEDATA FROM ZABCDRECORD WHERE Z_PK = ?", (pk,)).fetchone()
            finally:
                conn.close()
        except sqlite3.Error:
            return None
        if not row or not row[0]:
            return None
        return _sniff_image(bytes(row[0]))


def _sniff_image(blob: bytes) -> tuple[bytes, str] | None:
    """Find the real image inside a blob (thumbnails often start with a format byte)."""
    if not blob:
        return None
    jpeg = blob.find(b"\xff\xd8")
    png = blob.find(b"\x89PNG\r\n\x1a\n")
    if png >= 0 and (jpeg < 0 or png < jpeg):
        return blob[png:], "image/png"
    if jpeg >= 0:
        return blob[jpeg:], "image/jpeg"
    if blob[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1"):
        return blob, "image/heic"
    return None
