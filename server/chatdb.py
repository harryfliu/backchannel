"""Read-only access to ~/Library/Messages/chat.db.

Opened with `file:...?mode=ro` (never immutable=1) so writes Messages.app
makes in the WAL are visible. Columns differ between macOS versions
(e.g. `associated_message_emoji` is new in macOS 15), so every SELECT is built
from the columns that actually exist. Queries lean on the real indexes:
chat_message_join(chat_id, message_date, message_id), message(guid),
message(associated_message_guid), message(thread_originator_guid),
message(is_read, is_from_me, item_type) and message(date).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence
from urllib.request import pathname2url

from .handles import normalize_handle

log = logging.getLogger("backchannel.chatdb")

GROUP_STYLE = 43
DM_STYLE = 45
CHUNK = 500  # max values per IN (...) list

MESSAGE_FIELDS = (
    "ROWID", "guid", "text", "attributedBody", "handle_id", "service", "account", "date",
    "date_read", "date_delivered", "date_edited", "date_retracted", "is_from_me", "is_read",
    "is_delivered", "is_sent", "is_finished", "error", "item_type", "group_title",
    "group_action_type", "other_handle", "associated_message_guid", "associated_message_type",
    "associated_message_emoji", "balloon_bundle_id", "payload_data", "expressive_send_style_id",
    "message_summary_info", "thread_originator_guid", "cache_has_attachments",
    "is_audio_message", "destination_caller_id", "part_count", "has_unseen_mention", "is_spam",
)
ATTACHMENT_FIELDS = (
    "ROWID", "guid", "created_date", "filename", "uti", "mime_type", "transfer_state",
    "is_outgoing", "transfer_name", "total_bytes", "is_sticker", "hide_attachment",
    "emoji_image_content_identifier", "emoji_image_short_description",
)
# Tapbacks, tapback removals and placed stickers never appear in timelines.
NOT_REACTION_SQL = (
    "NOT (COALESCE(m.associated_message_type, 0) = 1000 "
    "OR COALESCE(m.associated_message_type, 0) BETWEEN 2000 AND 3999)"
)


class ChatDBError(RuntimeError):
    """chat.db is missing or unreadable; the message is meant for a person."""


@dataclass(frozen=True)
class HandleInfo:
    rowid: int
    raw: str
    service: str
    norm: str


@dataclass(frozen=True)
class ChatInfo:
    rowid: int
    guid: str
    style: int
    identifier: str
    service: str
    display_name: str | None
    handle_rowids: tuple[int, ...]

    @property
    def is_group(self) -> bool:
        if self.style == GROUP_STYLE:
            return True
        if self.style == DM_STYLE:
            return False
        return len(self.handle_rowids) > 1 or self.identifier.startswith("chat")


@dataclass(frozen=True)
class Meta:
    handles: dict[int, HandleInfo]
    chats: dict[int, ChatInfo]

    def same_as(self, other: "Meta | None") -> bool:
        return other is not None and self.handles == other.handles and self.chats == other.chats


def chunks(values: Sequence, size: int = CHUNK) -> Iterator[Sequence]:
    for i in range(0, len(values), size):
        yield values[i : i + size]


class ChatDB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._local = threading.local()
        self._columns: dict[str, set[str]] = {}
        self._columns_lock = threading.Lock()

    # ------------------------------------------------------------------ connections

    def _explain(self, exc: Exception) -> str:
        return (
            f"Backchannel can't read {self.path} ({exc}). On your Mac, open System Settings → "
            "Privacy & Security → Full Disk Access, turn it on for the terminal app you start "
            "Backchannel from, then restart Backchannel."
        )

    def connect(self) -> sqlite3.Connection:
        if not self.path.exists():
            raise ChatDBError(self._explain(FileNotFoundError("file not found or not permitted")))
        try:
            conn = sqlite3.connect(
                f"file:{pathname2url(str(self.path))}?mode=ro",
                uri=True,
                timeout=5,
                isolation_level=None,
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = 1")
            conn.execute("SELECT 1 FROM message LIMIT 1").fetchall()
        except sqlite3.Error as exc:
            raise ChatDBError(self._explain(exc)) from exc
        return conn

    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self.connect()
            self._local.conn = conn
        return conn

    def query(self, sql: str, params: Iterable = ()) -> list[sqlite3.Row]:
        params = tuple(params)
        try:
            return self.conn().execute(sql, params).fetchall()
        except sqlite3.OperationalError as exc:
            # A connection can go stale (e.g. the file was swapped); retry once on a fresh one.
            if "no such column" in str(exc) or "syntax error" in str(exc):
                raise
            log.info("Reconnecting to chat.db after: %s", exc)
            self._local.conn = None
            try:
                return self.conn().execute(sql, params).fetchall()
            except sqlite3.Error as exc2:
                raise ChatDBError(self._explain(exc2)) from exc2

    def readable(self) -> tuple[bool, str | None]:
        try:
            self.query("SELECT 1 FROM message LIMIT 1")
            return True, None
        except ChatDBError as exc:
            return False, str(exc)

    # ------------------------------------------------------------------ schema helpers

    def columns(self, table: str) -> set[str]:
        with self._columns_lock:
            if table not in self._columns:
                rows = self.conn().execute(f"PRAGMA table_info({table})").fetchall()
                self._columns[table] = {row[1] for row in rows}
            return self._columns[table]

    def select_list(self, table: str, alias: str, fields: Sequence[str]) -> str:
        cols = self.columns(table)
        return ", ".join(f"{alias}.{f}" if f in cols else f"NULL AS {f}" for f in fields)

    def message_select(self, alias: str = "m", fields: Sequence[str] = MESSAGE_FIELDS) -> str:
        return self.select_list("message", alias, fields)

    def index_hint(self, table: str, index: str) -> str:
        """`INDEXED BY` when the index exists. With ANALYZE stats SQLite may skip an index whose
        column is mostly NULL (thread_originator_guid), turning a lookup into a full scan."""
        with self._columns_lock:
            key = f"index:{table}"
            if key not in self._columns:
                rows = self.conn().execute(f"PRAGMA index_list({table})").fetchall()
                self._columns[key] = {row[1] for row in rows}
            return f"INDEXED BY {index}" if index in self._columns[key] else ""

    # ------------------------------------------------------------------ metadata

    def load_meta(self) -> Meta:
        handles: dict[int, HandleInfo] = {}
        for row in self.query("SELECT ROWID, id, service FROM handle"):
            raw = row["id"] or ""
            handles[row["ROWID"]] = HandleInfo(row["ROWID"], raw, row["service"] or "", normalize_handle(raw))
        members: dict[int, list[int]] = {}
        for row in self.query("SELECT chat_id, handle_id FROM chat_handle_join ORDER BY rowid"):
            members.setdefault(row["chat_id"], []).append(row["handle_id"])
        chat_cols = self.columns("chat")
        display = "display_name" if "display_name" in chat_cols else "NULL AS display_name"
        chats: dict[int, ChatInfo] = {}
        for row in self.query(
            f"SELECT ROWID, guid, style, chat_identifier, service_name, {display} FROM chat"
        ):
            chats[row["ROWID"]] = ChatInfo(
                rowid=row["ROWID"],
                guid=row["guid"] or "",
                style=row["style"] or 0,
                identifier=row["chat_identifier"] or "",
                service=row["service_name"] or "",
                display_name=(row["display_name"] or "").strip() or None,
                handle_rowids=tuple(members.get(row["ROWID"], ())),
            )
        return Meta(handles=handles, chats=chats)

    def chat_last_dates(self) -> dict[int, tuple[int, int]]:
        """chat ROWID -> (newest message_date, newest message ROWID); both index lookups."""
        rows = self.query(
            """SELECT c.ROWID AS chat_id,
                      (SELECT MAX(message_date) FROM chat_message_join WHERE chat_id = c.ROWID) AS last_date,
                      (SELECT MAX(message_id) FROM chat_message_join WHERE chat_id = c.ROWID) AS last_id
               FROM chat c"""
        )
        return {r["chat_id"]: (r["last_date"] or 0, r["last_id"] or 0) for r in rows if r["last_id"]}

    def my_account_handles(self, scan: int = 5000) -> list[str]:
        """Handles the user sends from, read from recent outgoing messages."""
        cols = self.columns("message")
        fields = [f for f in ("account", "destination_caller_id") if f in cols]
        if not fields:
            return []
        rows = self.query(
            f"""SELECT DISTINCT {', '.join(fields)} FROM message
                WHERE ROWID > (SELECT COALESCE(MAX(ROWID), 0) FROM message) - ? AND is_from_me = 1""",
            (scan,),
        )
        found: list[str] = []
        for row in rows:
            for field in fields:
                value = normalize_handle(row[field] or "")
                if value and value not in found and ("@" in value or value.startswith("+")):
                    found.append(value)
        return found

    # ------------------------------------------------------------------ message queries

    def messages_by_rowid(self, rowids: Sequence[int], fields: Sequence[str] = MESSAGE_FIELDS) -> list[sqlite3.Row]:
        return self._messages_where("m.ROWID", list(rowids), fields)

    def messages_by_guid(self, guids: Sequence[str], fields: Sequence[str] = MESSAGE_FIELDS) -> list[sqlite3.Row]:
        return self._messages_where("m.guid", list(guids), fields)

    def _messages_where(self, column: str, values: list, fields: Sequence[str] = MESSAGE_FIELDS) -> list[sqlite3.Row]:
        out: list[sqlite3.Row] = []
        seen: set[int] = set()
        select = self.message_select(fields=fields)
        for part in chunks(values):
            marks = ",".join("?" * len(part))
            rows = self.query(
                f"""SELECT {select}, cmj.chat_id AS chat_id
                    FROM message m LEFT JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
                    WHERE {column} IN ({marks})""",
                part,
            )
            for row in rows:
                if row["ROWID"] not in seen:
                    seen.add(row["ROWID"])
                    out.append(row)
        return out

    def page_ids(
        self, chat_ids: Sequence[int], before: tuple[int, int] | None, limit: int, *, reactions: bool = False
    ) -> list[tuple[int, int]]:
        """Newest-first (message_date, ROWID) pairs older than `before`, merged across chats."""
        max_date, max_id = before if before else (2**62, 2**62)
        where = "" if reactions else f"AND {NOT_REACTION_SQL}"
        merged: list[tuple[int, int]] = []
        for chat_id in chat_ids:
            rows = self.query(
                f"""SELECT cmj.message_date AS d, cmj.message_id AS id
                    FROM chat_message_join cmj JOIN message m ON m.ROWID = cmj.message_id
                    WHERE cmj.chat_id = ? AND cmj.message_date <= ?
                      AND NOT (cmj.message_date = ? AND cmj.message_id >= ?) {where}
                    ORDER BY cmj.message_date DESC, cmj.message_id DESC LIMIT ?""",
                (chat_id, max_date, max_date, max_id, limit),
            )
            merged.extend((r["d"] or 0, r["id"]) for r in rows)
        merged.sort(reverse=True)
        return merged[:limit]

    def unread_incoming(self, since_ns: int) -> list[sqlite3.Row]:
        """Incoming, unread, non-event rows (uses the is_read/is_from_me/item_type index)."""
        fields = (
            "ROWID", "guid", "date", "handle_id", "text", "attributedBody", "associated_message_type",
            "cache_has_attachments", "balloon_bundle_id", "thread_originator_guid", "has_unseen_mention",
        )
        return self.query(
            f"""SELECT {self.message_select(fields=fields)}, cmj.chat_id AS chat_id
                FROM message m JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
                WHERE m.is_read = 0 AND m.is_from_me = 0 AND m.item_type = 0 AND +m.date > ?""",
            (since_ns,),
        )

    def reaction_rows(self, assoc_guids: Sequence[str]) -> list[sqlite3.Row]:
        fields = (
            "ROWID", "guid", "associated_message_guid", "associated_message_type",
            "associated_message_emoji", "handle_id", "is_from_me", "date", "text", "attributedBody",
        )
        out: list[sqlite3.Row] = []
        for part in chunks(list(assoc_guids)):
            marks = ",".join("?" * len(part))
            out.extend(
                self.query(
                    f"""SELECT {self.message_select(fields=fields)} FROM message m
                        WHERE m.associated_message_guid IN ({marks})""",
                    part,
                )
            )
        out.sort(key=lambda r: ((r["date"] or 0), r["ROWID"]))
        return out

    def thread_reply_rows(self, root_guids: Sequence[str]) -> list[sqlite3.Row]:
        fields = ("ROWID", "guid", "thread_originator_guid", "date", "is_from_me", "handle_id",
                  "associated_message_type", "item_type")
        out: list[sqlite3.Row] = []
        hint = self.index_hint("message", "message_idx_thread_originator_guid")
        for part in chunks(list(root_guids)):
            marks = ",".join("?" * len(part))
            out.extend(
                self.query(
                    f"""SELECT {self.message_select(fields=fields)} FROM message m {hint}
                        WHERE m.thread_originator_guid IN ({marks}) AND {NOT_REACTION_SQL}""",
                    part,
                )
            )
        return out

    def attachments_for(self, message_ids: Sequence[int]) -> dict[int, list[sqlite3.Row]]:
        result: dict[int, list[sqlite3.Row]] = {}
        select = self.select_list("attachment", "a", ATTACHMENT_FIELDS)
        for part in chunks(list(message_ids)):
            marks = ",".join("?" * len(part))
            rows = self.query(
                f"""SELECT maj.message_id AS message_id, {select}
                    FROM message_attachment_join maj JOIN attachment a ON a.ROWID = maj.attachment_id
                    WHERE maj.message_id IN ({marks}) ORDER BY maj.message_id, a.ROWID""",
                part,
            )
            for row in rows:
                result.setdefault(row["message_id"], []).append(row)
        return result

    def attachment_by_guid(self, guid: str) -> sqlite3.Row | None:
        select = self.select_list("attachment", "a", ATTACHMENT_FIELDS)
        rows = self.query(f"SELECT {select} FROM attachment a WHERE a.guid = ?", (guid,))
        return rows[0] if rows else None

    def message_ids_for_attachment(self, attachment_rowid: int) -> list[int]:
        rows = self.query(
            "SELECT message_id FROM message_attachment_join WHERE attachment_id = ?", (attachment_rowid,)
        )
        return [r["message_id"] for r in rows]

    def max_rowid(self) -> int:
        rows = self.query("SELECT COALESCE(MAX(ROWID), 0) AS m FROM message")
        return rows[0]["m"]

    def has_sent_to(self, chat_ids: Sequence[int]) -> bool:
        """Has the user ever sent a real message in these chats?"""
        for chat_id in chat_ids:
            rows = self.query(
                f"""SELECT 1 FROM chat_message_join cmj JOIN message m ON m.ROWID = cmj.message_id
                    WHERE cmj.chat_id = ? AND m.is_from_me = 1 AND m.item_type = 0 AND {NOT_REACTION_SQL}
                    LIMIT 1""",
                (chat_id,),
            )
            if rows:
                return True
        return False

    def someone_wrote_after(self, chat_ids: Sequence[int], after_ns: int) -> bool:
        """Did anyone but the user send a real message in these chats after `after_ns`?"""
        for chat_id in chat_ids:
            rows = self.query(
                f"""SELECT 1 FROM chat_message_join cmj JOIN message m ON m.ROWID = cmj.message_id
                    WHERE cmj.chat_id = ? AND cmj.message_date > ? AND m.is_from_me = 0 AND m.item_type = 0
                      AND {NOT_REACTION_SQL} LIMIT 1""",
                (chat_id, after_ns),
            )
            if rows:
                return True
        return False

    def media_rows(
        self, chat_ids: Sequence[int], before: tuple[int, int, int] | None, limit: int
    ) -> list[sqlite3.Row]:
        """Attachments in these chats, newest first, older than (message_date, message id, attachment id)."""
        max_date, max_mid, max_aid = before if before else (2**62, 2**62, 2**62)
        select = self.select_list("attachment", "a", ATTACHMENT_FIELDS)
        out: list[sqlite3.Row] = []
        retracted = "COALESCE(m.date_retracted, 0) = 0" if "date_retracted" in self.columns("message") else "1"
        # Stickers and Genmoji aren't media (they have their own library, /api/stickers).
        not_sticker = "COALESCE(a.is_sticker, 0) = 0"
        if "emoji_image_content_identifier" in self.columns("attachment"):
            not_sticker += " AND a.emoji_image_content_identifier IS NULL"
        for chat_id in chat_ids:
            out.extend(
                self.query(
                    f"""SELECT cmj.message_date AS mdate, m.ROWID AS mid, m.guid AS mguid, m.is_from_me AS from_me,
                               m.handle_id AS handle_id, m.date AS date, m.balloon_bundle_id AS balloon, {select}
                        FROM chat_message_join cmj
                        JOIN message m ON m.ROWID = cmj.message_id
                        JOIN message_attachment_join maj ON maj.message_id = m.ROWID
                        JOIN attachment a ON a.ROWID = maj.attachment_id
                        WHERE cmj.chat_id = ? AND cmj.message_date <= ? AND {NOT_REACTION_SQL} AND {retracted}
                          AND COALESCE(a.hide_attachment, 0) = 0 AND {not_sticker}
                          AND NOT (cmj.message_date = ? AND (m.ROWID > ? OR (m.ROWID = ? AND a.ROWID >= ?)))
                        ORDER BY cmj.message_date DESC, m.ROWID DESC, a.ROWID DESC LIMIT ?""",
                    (chat_id, max_date, max_date, max_mid, max_mid, max_aid, limit),
                )
            )
        out.sort(key=lambda r: (r["mdate"] or 0, r["mid"], r["ROWID"]), reverse=True)
        return out[:limit]

    def recent_rows(self, since_ns: int, where: str, params: Sequence = (), fields: Sequence[str] = MESSAGE_FIELDS) -> list[sqlite3.Row]:
        """Rows newer than `since_ns` (message.date index) matching an extra WHERE clause."""
        return self.query(
            f"""SELECT {self.message_select(fields=fields)}, cmj.chat_id AS chat_id
                FROM message m LEFT JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
                WHERE m.date > ? AND ({where})""",
            (since_ns, *params),
        )

    def recent_thread_roots(self, since_ns: int, limit: int) -> list[sqlite3.Row]:
        return self.query(
            f"""SELECT m.thread_originator_guid AS root, MAX(m.date) AS last
                FROM message m
                WHERE m.date > ? AND m.thread_originator_guid IS NOT NULL AND m.thread_originator_guid != ''
                  AND {NOT_REACTION_SQL}
                GROUP BY m.thread_originator_guid ORDER BY last DESC LIMIT ?""",
            (since_ns, limit),
        )

    def rows_after(self, rowid: int, limit: int = 1000) -> list[sqlite3.Row]:
        return self.query(
            f"""SELECT {self.message_select()}, cmj.chat_id AS chat_id
                FROM message m LEFT JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
                WHERE m.ROWID > ? ORDER BY m.ROWID LIMIT ?""",
            (rowid, limit),
        )

    def mutable_fields_since(self, rowid: int) -> list[sqlite3.Row]:
        """Fields that change after a row is written (edits, unsends, delivery, read state)."""
        fields = ("ROWID", "guid", "date_edited", "date_retracted", "is_delivered", "date_delivered",
                  "date_read", "error", "is_read", "is_from_me", "associated_message_type", "is_played")
        return self.query(
            f"SELECT {self.message_select(fields=fields)}, length(m.attributedBody) AS body_len, "
            f"length(m.text) AS text_len FROM message m WHERE m.ROWID > ?",
            (rowid,),
        )


class ChangeTracker:
    """Bumps `generation` whenever another connection commits to chat.db."""

    def __init__(self, db: ChatDB):
        self.db = db
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self._last: int | None = None
        self.generation = 0

    def check(self) -> int:
        with self._lock:
            try:
                if self._conn is None:
                    self._conn = self.db.connect()
                version = self._conn.execute("PRAGMA data_version").fetchone()[0]
            except (sqlite3.Error, ChatDBError):
                self._conn = None
                raise
            if version != self._last:
                self._last = version
                self.generation += 1
            return self.generation
