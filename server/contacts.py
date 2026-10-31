"""Contacts from the macOS AddressBook databases (read-only).

Phones match on their last 10 digits (like Harry's digest), emails lowercase.
Names prefer the nickname ("Mom"), then "First Last", then the organisation
for business cards.

Photos use Core Data's binary storage: ZTHUMBNAILIMAGEDATA (or ZIMAGEDATA)
starts with 0x01 followed by the image bytes, or 0x02 followed by an ASCII
UUID naming a file in `<folder of the .abcddb>/.AddressBook-v22_SUPPORT/_EXTERNAL_DATA/`.
Older cards keep a file in `<folder>/Images/<unique id>`. Each source is
classified once at load time (the first 64 bytes, plus a stat/peek of an
external file), so `avatarUrl` is only offered for a photo we can really serve.
"""

from __future__ import annotations

import glob
import logging
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.request import pathname2url

from .handles import digits, normalize_handle, phone_key
from .imagetypes import AVATAR_IMAGES, sniff_format

log = logging.getLogger("backchannel.contacts")

RELOAD_INTERVAL = 30.0  # seconds between mtime checks
EXTERNAL_DIR = (".AddressBook-v22_SUPPORT", "_EXTERNAL_DATA")
_EXTERNAL_REF = re.compile(r"^[A-Za-z0-9._-]{8,80}$")


@dataclass(frozen=True)
class PhotoSource:
    """Where a contact photo lives and what format it is."""

    column: str | None   # ZTHUMBNAILIMAGEDATA / ZIMAGEDATA, or None for the Images/ file
    storage: str         # inline | external | images_dir
    fmt: str             # jpeg, png, heic, tiff…
    path: Path | None = None  # external or Images/ file


def classify_blob(head: bytes | None, db_dir: Path) -> tuple[str, str | None, Path | None]:
    """(status, format, external path) for the first bytes of a Core Data image blob.

    status is one of: none, inline, external, invalid, missing_external.
    """
    if not head:
        return "none", None, None
    head = bytes(head)
    if head[0] == 0x01:
        fmt = sniff_format(head[1:])
        return ("inline", fmt, None) if fmt else ("invalid", None, None)
    if head[0] == 0x02:
        ref = head[1:].split(b"\0", 1)[0].decode("ascii", "ignore").strip()
        if not _EXTERNAL_REF.match(ref):
            return "invalid", None, None
        path = db_dir.joinpath(*EXTERNAL_DIR, ref)
        try:
            with path.open("rb") as f:
                fmt = sniff_format(f.read(64))
        except OSError:
            return "missing_external", None, None
        return ("external", fmt, path) if fmt else ("invalid", None, None)
    fmt = sniff_format(head)  # an image stored without the tag byte
    return ("inline", fmt, None) if fmt else ("invalid", None, None)


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
    photo: PhotoSource | None = None
    photo_status: str = "none"  # for `backchannel doctor`: inline, external, images_dir, invalid, …

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
    def images_dir_path(self) -> Path | None:
        if not self.unique_id:
            return None
        return self.db_path.parent / "Images" / self.unique_id.split(":")[0]

    @property
    def has_photo(self) -> bool:
        return self.photo is not None


def _open_ro(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{pathname2url(path)}?mode=ro", uri=True, timeout=2)
    conn.row_factory = sqlite3.Row
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


class ContactBook:
    def __init__(self, globs: list[str], can_convert: bool = False):
        self.globs = globs
        self.can_convert = can_convert  # sips is available, so HEIC/TIFF photos can be served as JPEG
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
            head = lambda name: f"substr(r.{name}, 1, 64) AS {name}_head" if name in cols else f"NULL AS {name}_head"  # noqa: E731
            rows = conn.execute(
                f"""SELECT r.Z_PK, {pick('ZFIRSTNAME')}, {pick('ZLASTNAME')}, {pick('ZNICKNAME')},
                           {pick('ZORGANIZATION')}, {pick('ZUNIQUEID')},
                           {head('ZTHUMBNAILIMAGEDATA')}, {head('ZIMAGEDATA')}
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
                )
                self._choose_photo(contacts[row["Z_PK"]], row["ZTHUMBNAILIMAGEDATA_head"], row["ZIMAGEDATA_head"])
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

    def _servable(self, fmt: str | None) -> bool:
        return bool(fmt) and (fmt in AVATAR_IMAGES or self.can_convert)

    def _choose_photo(self, contact: Contact, thumb_head: bytes | None, image_head: bytes | None) -> None:
        """Pick the first photo source that is a real image we can serve; remember why if none is."""
        db_dir = contact.db_path.parent
        statuses: list[str] = []
        for column, head in (("ZTHUMBNAILIMAGEDATA", thumb_head), ("ZIMAGEDATA", image_head)):
            status, fmt, path = classify_blob(head, db_dir)
            statuses.append(status)
            if status in ("inline", "external") and self._servable(fmt):
                contact.photo = PhotoSource(column, status, fmt, path)  # type: ignore[arg-type]
                contact.photo_status = status
                return
            if status in ("inline", "external"):
                statuses[-1] = "unconvertible"
        images = contact.images_dir_path
        if images is not None and images.is_file():
            fmt = sniff_format(images.read_bytes()[:64]) if images.stat().st_size else None
            if self._servable(fmt):
                contact.photo = PhotoSource(None, "images_dir", fmt, images)  # type: ignore[arg-type]
                contact.photo_status = "images_dir"
                return
            statuses.append("invalid" if not fmt else "unconvertible")
        for status in ("missing_external", "unconvertible", "invalid"):
            if status in statuses:
                contact.photo_status = status
                return
        contact.photo_status = "none"

    def photo_stats(self) -> dict[str, int]:
        """How contact photos are stored (counts only, for `backchannel doctor`)."""
        out: dict[str, int] = {}
        with self._lock:
            for contact in self._contacts:
                out[contact.photo_status] = out.get(contact.photo_status, 0) + 1
        return out

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
        """(image bytes, format) of the contact's photo, or None if it can't be read as an image."""
        source = contact.photo
        if source is None:
            return None
        try:
            if source.path is not None:
                data = source.path.read_bytes()
            else:
                data = self._blob(contact, source.column or "ZTHUMBNAILIMAGEDATA")
                if data and data[0] == 0x01:
                    data = data[1:]
        except (OSError, sqlite3.Error):
            return None
        fmt = sniff_format(data[:64]) if data else None
        return (data, fmt) if fmt else None

    @staticmethod
    def _blob(contact: Contact, column: str) -> bytes | None:
        pk = int(contact.key.split(":")[1])
        conn = _open_ro(str(contact.db_path))
        try:
            row = conn.execute(f"SELECT {column} FROM ZABCDRECORD WHERE Z_PK = ?", (pk,)).fetchone()
        finally:
            conn.close()
        return bytes(row[0]) if row and row[0] else None
