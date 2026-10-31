"""Write rows into a *fixture* chat.db the way Messages.app would.

Used by the fixture generator, the simulator and dry-run sends with
BACKCHANNEL_FAKE_DB=1. It refuses to open anything under ~/Library/Messages,
so it can never touch Harry's real database.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Sequence

from .timeutil import unix_to_apple_ns
from .typedstream import encode_attributed_body


class RefusedError(RuntimeError):
    pass


def guard_path(path: Path) -> Path:
    real = Path(os.path.realpath(path))
    protected = Path(os.path.realpath(Path.home() / "Library" / "Messages"))
    if real == protected or protected in real.parents:
        raise RefusedError(f"Refusing to write to {real}: it is inside ~/Library/Messages (the real Messages data).")
    return real


def connect(path: Path) -> sqlite3.Connection:
    real = guard_path(path)
    conn = sqlite3.connect(str(real), timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def new_guid() -> str:
    return str(uuid.uuid4()).upper()


def _insert(conn: sqlite3.Connection, table: str, values: dict[str, Any]) -> int:
    cols = ", ".join(values)
    marks = ", ".join("?" * len(values))
    cur = conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(values.values()))
    return int(cur.lastrowid)


def ensure_handle(conn: sqlite3.Connection, handle_id: str, service: str, country: str = "us") -> int:
    row = conn.execute("SELECT ROWID FROM handle WHERE id = ? AND service = ?", (handle_id, service)).fetchone()
    if row:
        return int(row[0])
    return _insert(conn, "handle", {
        "id": handle_id, "country": country, "service": service,
        "uncanonicalized_id": None, "person_centric_id": None,
    })


def chat_info(conn: sqlite3.Connection, chat_guid: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM chat WHERE guid = ?", (chat_guid,)).fetchone()
    if row is None:
        raise LookupError(f"No chat with guid {chat_guid}")
    return row


def chat_handles(conn: sqlite3.Connection, chat_rowid: int) -> list[int]:
    return [r[0] for r in conn.execute("SELECT handle_id FROM chat_handle_join WHERE chat_id = ? ORDER BY rowid", (chat_rowid,))]


def insert_message(
    conn: sqlite3.Connection,
    *,
    chat_rowid: int,
    text: str | None,
    is_from_me: bool,
    handle_rowid: int = 0,
    date: float | None = None,
    service: str = "iMessage",
    guid: str | None = None,
    store_text_column: bool = False,
    attributed_body: bytes | None | str = "auto",
    mentions: Sequence[tuple[int, int, str]] = (),
    attachments: Sequence[dict[str, Any]] = (),
    my_handle: str | None = None,
    **columns: Any,
) -> tuple[int, str]:
    """Insert a message (and its chat/attachment joins). Returns (ROWID, guid).

    `attachments` items are dicts with attachment-table columns (guid,
    filename, mime_type, uti, transfer_name, total_bytes, ...).
    `columns` sets any other message column directly.
    """
    guid = guid or new_guid()
    when = time.time() if date is None else date
    apple_date = unix_to_apple_ns(when)
    att_guids = [a["guid"] for a in attachments]
    body: bytes | None
    if attributed_body == "auto":
        body = encode_attributed_body(text or "", attachment_guids=att_guids, mentions=mentions) if (text or att_guids) else None
    else:
        body = attributed_body  # type: ignore[assignment]
    stored_text = None
    if store_text_column and (text is not None or att_guids):
        stored_text = "￼" * len(att_guids) + (text or "")
    values: dict[str, Any] = {
        "guid": guid,
        "text": stored_text,
        "attributedBody": body,
        "handle_id": handle_rowid,
        "service": service,
        "account": (("e:" if "@" in my_handle else "p:") + my_handle) if (my_handle and is_from_me) else None,
        "account_guid": str(uuid.uuid5(uuid.NAMESPACE_URL, f"account:{service}")).upper(),
        "date": apple_date,
        "date_read": 0,
        "date_delivered": 0,
        "is_from_me": int(bool(is_from_me)),
        "is_read": 1 if is_from_me else 0,
        "is_sent": 1 if is_from_me else 0,
        "is_delivered": 1 if not is_from_me else 0,
        "is_finished": 1,
        "cache_has_attachments": 1 if att_guids else 0,
        "destination_caller_id": my_handle,
        "part_count": max(1, len(att_guids) + (1 if text else 0)),
        "version": 10,
    }
    values.update(columns)
    rowid = _insert(conn, "message", values)
    _insert(conn, "chat_message_join", {"chat_id": chat_rowid, "message_id": rowid, "message_date": apple_date})
    for att in attachments:
        data = {
            "created_date": apple_date,
            "start_date": 0,
            "transfer_state": 5,
            "is_outgoing": int(bool(is_from_me)),
            "is_sticker": 0,
            "hide_attachment": 0,
            "original_guid": att["guid"],
        }
        data.update(att)
        att_rowid = _insert(conn, "attachment", data)
        _insert(conn, "message_attachment_join", {"message_id": rowid, "attachment_id": att_rowid})
    return rowid, guid


def attachment_relpath(guid: str, name: str) -> str:
    """Where Messages keeps a file, relative to ~/Library/Messages/Attachments: ab/11/<GUID>/<name>."""
    digest = hashlib.sha1(guid.encode()).hexdigest()
    return f"{digest[:2]}/{int(digest[2:4], 16) % 100:02d}/{guid}/{name}"


def store_attachment_file(attachments_root: Path, guid: str, name: str, source: Path | bytes) -> tuple[str, int]:
    """Copy a file into the fixture attachments tree; returns (chat.db filename, size)."""
    rel = attachment_relpath(guid, name)
    dest = Path(attachments_root) / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(source, (bytes, bytearray)):
        dest.write_bytes(source)
    else:
        shutil.copyfile(source, dest)
    return f"~/Library/Messages/Attachments/{rel}", dest.stat().st_size


def send_as_messages_app(
    db_path: Path,
    chat_guid: str,
    *,
    text: str | None = None,
    file_path: Path | None = None,
    attachments_root: Path | None = None,
    my_handle: str | None = None,
    mime_type: str | None = None,
) -> tuple[int, str]:
    """Insert an outgoing message as if Messages.app had just sent it."""
    conn = connect(db_path)
    try:
        chat = chat_info(conn, chat_guid)
        handles = chat_handles(conn, chat["ROWID"])
        handle_rowid = handles[0] if chat["style"] == 45 and handles else 0
        service = chat["service_name"] or "iMessage"
        attachments: list[dict[str, Any]] = []
        if file_path is not None:
            if attachments_root is None:
                raise ValueError("attachments_root is required to fake a file send")
            att_guid = new_guid()
            name = file_path.name
            filename, size = store_attachment_file(attachments_root, att_guid, name, file_path)
            attachments.append({
                "guid": att_guid, "filename": filename, "transfer_name": name, "total_bytes": size,
                "mime_type": mime_type, "uti": None, "is_outgoing": 1,
            })
        conn.execute("BEGIN IMMEDIATE")
        try:
            result = insert_message(
                conn, chat_rowid=chat["ROWID"], text=text, is_from_me=True, handle_rowid=handle_rowid,
                service=service, attachments=attachments, my_handle=my_handle,
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return result
    finally:
        conn.close()


def start_chat_as_messages_app(db_path: Path, handle: str, *, text: str, my_handle: str | None = None) -> tuple[int, str]:
    """Messages.app sending to a new handle: create handle + 1:1 chat (if needed) and the message."""
    conn = connect(db_path)
    try:
        service = "iMessage"
        guid = f"{service};-;{handle}"
        conn.execute("BEGIN IMMEDIATE")
        try:
            chat = conn.execute("SELECT ROWID FROM chat WHERE guid = ?", (guid,)).fetchone()
            hid = ensure_handle(conn, handle, service)
            if chat is None:
                chat_rowid = _insert(conn, "chat", {
                    "guid": guid, "style": 45, "state": 3, "chat_identifier": handle, "service_name": service,
                    "account_login": ("E:" + my_handle) if my_handle and "@" in my_handle else (f"P:{my_handle}" if my_handle else None),
                    "last_addressed_handle": my_handle, "display_name": "", "is_archived": 0, "is_filtered": 0,
                })
                conn.execute("INSERT INTO chat_handle_join (chat_id, handle_id) VALUES (?, ?)", (chat_rowid, hid))
            else:
                chat_rowid = chat[0]
            result = insert_message(conn, chat_rowid=chat_rowid, text=text, is_from_me=True, handle_rowid=hid,
                                    service=service, my_handle=my_handle)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return result
    finally:
        conn.close()


def mark_delivered(db_path: Path, guid: str, *, read: bool = False) -> None:
    conn = connect(db_path)
    try:
        now = unix_to_apple_ns(time.time())
        if read:
            conn.execute("UPDATE message SET date_read = ?, is_read = 1 WHERE guid = ?", (now, guid))
        else:
            conn.execute("UPDATE message SET is_delivered = 1, date_delivered = ? WHERE guid = ?", (now, guid))
    finally:
        conn.close()

