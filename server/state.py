"""Backchannel's own SQLite database (never chat.db).

Holds things Messages.app doesn't know about: stars/mutes/snoozes, local
"last seen" marks, saved-for-later items, scheduled sends, sends in flight,
and which sent messages belong to which thread.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Sequence

SCHEMA = """
CREATE TABLE IF NOT EXISTS prefs (
    conversation_id TEXT PRIMARY KEY,
    starred INTEGER NOT NULL DEFAULT 0,
    muted INTEGER NOT NULL DEFAULT 0,
    snoozed_until REAL,
    section TEXT,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS marks (
    conversation_id TEXT PRIMARY KEY,
    last_seen_date INTEGER NOT NULL,   -- chat.db date (ns since 2001) of the newest message seen
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS later (
    message_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    saved_at REAL NOT NULL,
    remind_at REAL,
    done INTEGER NOT NULL DEFAULT 0,
    if_no_reply INTEGER NOT NULL DEFAULT 0,      -- only remind if nobody else writes first
    resolved_by_reply INTEGER NOT NULL DEFAULT 0,
    fired_at REAL                                -- when the reminder fired
);
CREATE TABLE IF NOT EXISTS scheduled (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    text TEXT NOT NULL,
    send_at REAL NOT NULL,
    created_at REAL NOT NULL,
    status TEXT NOT NULL,              -- pending | sent | failed | cancelled
    error TEXT,
    client_id TEXT,
    thread_root_id TEXT
);
CREATE INDEX IF NOT EXISTS scheduled_status ON scheduled(status, send_at);
CREATE TABLE IF NOT EXISTS pending_sends (
    client_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    chat_ids TEXT NOT NULL,            -- JSON list of chat.db chat ROWIDs
    kind TEXT NOT NULL,                -- text | file
    text TEXT,
    file_name TEXT,
    thread_root_id TEXT,
    created_at REAL NOT NULL,
    dispatched_at REAL,
    status TEXT NOT NULL,              -- sending | dispatched | sent | failed
    error TEXT,
    message_guid TEXT,
    handle TEXT                        -- set for new conversations (no chat row yet)
);
CREATE INDEX IF NOT EXISTS pending_guid ON pending_sends(message_guid);
CREATE TABLE IF NOT EXISTS sent_threads (
    message_guid TEXT PRIMARY KEY,
    thread_root_id TEXT NOT NULL,
    conversation_id TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS sent_threads_root ON sent_threads(thread_root_id);
CREATE TABLE IF NOT EXISTS custom_emoji (
    name TEXT PRIMARY KEY,
    file TEXT NOT NULL,                -- file name inside <state_dir>/emoji/
    mime_type TEXT NOT NULL,
    width INTEGER,
    height INTEGER,
    animated INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL,              -- upload | link | imessage
    source_ref TEXT,                   -- the link, or the sticker's attachment guid
    sha256 TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS file_hashes (
    guid TEXT PRIMARY KEY,             -- chat.db attachment guid
    mtime REAL NOT NULL,
    size INTEGER NOT NULL,
    sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""

DEFAULT_PREFS = {"starred": False, "muted": False, "snoozed_until": None, "section": None}

# Columns added after the first release: (table, column, definition).
MIGRATIONS = (
    ("later", "if_no_reply", "INTEGER NOT NULL DEFAULT 0"),
    ("later", "resolved_by_reply", "INTEGER NOT NULL DEFAULT 0"),
    ("later", "fired_at", "REAL"),
    ("pending_sends", "handle", "TEXT"),
)
UNSET: Any = object()


# `ServerSettings` (web/src/api/types.ts) and their defaults.
SERVER_SETTINGS: dict[str, bool] = {"markReadInMessages": False}


class State:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None, timeout=10)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._migrate()
        self.version = 0  # bumped on changes that affect conversation summaries

    def _migrate(self) -> None:
        for table, column, definition in MIGRATIONS:
            existing = {row[1] for row in self._conn.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _exec(self, sql: str, params: Iterable = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, tuple(params))

    def _all(self, sql: str, params: Iterable = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    def _one(self, sql: str, params: Iterable = ()) -> sqlite3.Row | None:
        rows = self._all(sql, params)
        return rows[0] if rows else None

    # ------------------------------------------------------------------ prefs

    def _prefs_row(self, conversation_id: str, aliases: Sequence[str] = ()) -> sqlite3.Row | None:
        for key in (conversation_id, *aliases):
            row = self._one("SELECT * FROM prefs WHERE conversation_id = ?", (key,))
            if row:
                return row
        return None

    def get_prefs(self, conversation_id: str, aliases: Sequence[str] = ()) -> dict[str, Any]:
        row = self._prefs_row(conversation_id, aliases)
        if not row:
            return dict(DEFAULT_PREFS)
        return {
            "starred": bool(row["starred"]),
            "muted": bool(row["muted"]),
            "snoozed_until": row["snoozed_until"],
            "section": row["section"],
        }

    def all_prefs(self) -> dict[str, dict[str, Any]]:
        return {
            r["conversation_id"]: {
                "starred": bool(r["starred"]),
                "muted": bool(r["muted"]),
                "snoozed_until": r["snoozed_until"],
                "section": r["section"],
            }
            for r in self._all("SELECT * FROM prefs")
        }

    def set_prefs(self, conversation_id: str, patch: dict[str, Any], aliases: Sequence[str] = ()) -> dict[str, Any]:
        current = self.get_prefs(conversation_id, aliases)
        current.update({k: v for k, v in patch.items() if k in DEFAULT_PREFS})
        with self._lock:
            self._conn.execute(
                """INSERT INTO prefs (conversation_id, starred, muted, snoozed_until, section, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(conversation_id) DO UPDATE SET starred=excluded.starred, muted=excluded.muted,
                     snoozed_until=excluded.snoozed_until, section=excluded.section, updated_at=excluded.updated_at""",
                (
                    conversation_id,
                    int(bool(current["starred"])),
                    int(bool(current["muted"])),
                    current["snoozed_until"],
                    current["section"],
                    time.time(),
                ),
            )
            self.version += 1
        return current

    # ------------------------------------------------------------------ last-seen marks

    def get_mark(self, conversation_id: str, aliases: Sequence[str] = ()) -> int | None:
        best: int | None = None
        for key in (conversation_id, *aliases):
            row = self._one("SELECT last_seen_date FROM marks WHERE conversation_id = ?", (key,))
            if row and (best is None or row[0] > best):
                best = row[0]
        return best

    def all_marks(self) -> dict[str, int]:
        return {r[0]: r[1] for r in self._all("SELECT conversation_id, last_seen_date FROM marks")}

    def set_mark(self, conversation_id: str, last_seen_date: int) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO marks (conversation_id, last_seen_date, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(conversation_id) DO UPDATE SET
                     last_seen_date = MAX(marks.last_seen_date, excluded.last_seen_date),
                     updated_at = excluded.updated_at""",
                (conversation_id, int(last_seen_date), time.time()),
            )
            self.version += 1

    # ------------------------------------------------------------------ later

    def list_later(self) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM later ORDER BY done, COALESCE(remind_at, saved_at), saved_at DESC")

    def get_later(self, message_id: str) -> sqlite3.Row | None:
        return self._one("SELECT * FROM later WHERE message_id = ?", (message_id,))

    def put_later(
        self,
        message_id: str,
        conversation_id: str | None = None,
        *,
        remind_at: Any = UNSET,
        if_no_reply: Any = UNSET,
        done: Any = UNSET,
    ) -> sqlite3.Row:
        """Create or partially update a saved item; fields left UNSET keep their value.

        A new remind time re-arms the reminder (clears fired_at); turning on
        if_no_reply clears an earlier "resolved by reply".
        """
        with self._lock:
            existing = self.get_later(message_id)
            if existing is None:
                if not conversation_id:
                    raise ValueError("conversationId is required to save a message for later.")
                self._conn.execute(
                    """INSERT INTO later (message_id, conversation_id, saved_at, remind_at, done, if_no_reply)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (message_id, conversation_id, time.time(),
                     None if remind_at is UNSET else remind_at,
                     0 if done is UNSET else int(bool(done)),
                     0 if if_no_reply is UNSET else int(bool(if_no_reply))),
                )
                return self.get_later(message_id)  # type: ignore[return-value]
            updates: dict[str, Any] = {}
            if conversation_id:
                updates["conversation_id"] = conversation_id
            if remind_at is not UNSET and remind_at != existing["remind_at"]:
                updates["remind_at"] = remind_at
                updates["fired_at"] = None
            if if_no_reply is not UNSET and bool(if_no_reply) != bool(existing["if_no_reply"]):
                updates["if_no_reply"] = int(bool(if_no_reply))
                updates["resolved_by_reply"] = 0
            if done is not UNSET:
                updates["done"] = int(bool(done))
            if updates:
                cols = ", ".join(f"{k} = ?" for k in updates)
                self._conn.execute(f"UPDATE later SET {cols} WHERE message_id = ?", (*updates.values(), message_id))
            return self.get_later(message_id)  # type: ignore[return-value]

    def armed_later(self) -> list[sqlite3.Row]:
        """Items that may still fire or be resolved by a reply."""
        return self._all(
            """SELECT * FROM later WHERE done = 0 AND fired_at IS NULL AND resolved_by_reply = 0
               AND (remind_at IS NOT NULL OR if_no_reply = 1) ORDER BY COALESCE(remind_at, saved_at)"""
        )

    def fire_later(self, message_id: str, when: float) -> bool:
        """Mark a reminder fired; True only the first time."""
        return self._exec(
            "UPDATE later SET fired_at = ? WHERE message_id = ? AND fired_at IS NULL AND done = 0 AND resolved_by_reply = 0",
            (when, message_id),
        ).rowcount == 1

    def resolve_later(self, message_id: str) -> bool:
        """Mark an if-no-reply item resolved because someone wrote; True only the first time."""
        return self._exec(
            "UPDATE later SET resolved_by_reply = 1 WHERE message_id = ? AND resolved_by_reply = 0 AND fired_at IS NULL",
            (message_id,),
        ).rowcount == 1

    def delete_later(self, message_id: str) -> bool:
        return self._exec("DELETE FROM later WHERE message_id = ?", (message_id,)).rowcount > 0

    # ------------------------------------------------------------------ scheduled sends

    def add_scheduled(
        self, conversation_id: str, text: str, send_at: float, client_id: str | None, thread_root_id: str | None
    ) -> sqlite3.Row:
        sid = "sch_" + uuid.uuid4().hex[:16]
        self._exec(
            """INSERT INTO scheduled (id, conversation_id, text, send_at, created_at, status, client_id, thread_root_id)
               VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)""",
            (sid, conversation_id, text, send_at, time.time(), client_id, thread_root_id),
        )
        return self.get_scheduled(sid)  # type: ignore[return-value]

    def get_scheduled(self, sid: str) -> sqlite3.Row | None:
        return self._one("SELECT * FROM scheduled WHERE id = ?", (sid,))

    def list_scheduled(self, history_days: float = 7) -> list[sqlite3.Row]:
        cutoff = time.time() - history_days * 86400
        return self._all(
            """SELECT * FROM scheduled WHERE status = 'pending' OR send_at >= ?
               ORDER BY status != 'pending', send_at""",
            (cutoff,),
        )

    def due_scheduled(self, now: float) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM scheduled WHERE status = 'pending' AND send_at <= ? ORDER BY send_at", (now,))

    def set_scheduled_status(self, sid: str, status: str, error: str | None = None) -> sqlite3.Row | None:
        self._exec("UPDATE scheduled SET status = ?, error = ? WHERE id = ?", (status, error, sid))
        return self.get_scheduled(sid)

    def claim_scheduled(self, sid: str) -> bool:
        """Atomically move a pending item to 'sending' so it's only sent once."""
        return self._exec(
            "UPDATE scheduled SET status = 'sending' WHERE id = ? AND status = 'pending'", (sid,)
        ).rowcount == 1

    # ------------------------------------------------------------------ pending sends

    def get_pending(self, client_id: str) -> sqlite3.Row | None:
        return self._one("SELECT * FROM pending_sends WHERE client_id = ?", (client_id,))

    def add_pending(
        self,
        client_id: str,
        conversation_id: str,
        chat_ids: Sequence[int],
        kind: str,
        text: str | None,
        file_name: str | None,
        thread_root_id: str | None,
        handle: str | None = None,
    ) -> sqlite3.Row:
        self._exec(
            """INSERT OR REPLACE INTO pending_sends
               (client_id, conversation_id, chat_ids, kind, text, file_name, thread_root_id, created_at, status, handle)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'sending', ?)""",
            (client_id, conversation_id, json.dumps(list(chat_ids)), kind, text, file_name, thread_root_id,
             time.time(), handle),
        )
        return self.get_pending(client_id)  # type: ignore[return-value]

    def update_pending(self, client_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        self._exec(f"UPDATE pending_sends SET {cols} WHERE client_id = ?", (*fields.values(), client_id))

    def open_pending(self) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM pending_sends WHERE status IN ('sending', 'dispatched') ORDER BY created_at")

    def client_ids_for(self, guids: Sequence[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        guids = list(guids)
        for i in range(0, len(guids), 500):
            part = guids[i : i + 500]
            marks = ",".join("?" * len(part))
            for row in self._all(
                f"SELECT message_guid, client_id FROM pending_sends WHERE message_guid IN ({marks})", part
            ):
                out[row[0]] = row[1]
        return out

    # ------------------------------------------------------------------ local thread mapping

    def set_thread_root(self, message_guid: str, thread_root_id: str, conversation_id: str | None) -> None:
        self._exec(
            "INSERT OR REPLACE INTO sent_threads (message_guid, thread_root_id, conversation_id, created_at) VALUES (?, ?, ?, ?)",
            (message_guid, thread_root_id, conversation_id, time.time()),
        )

    def thread_roots_for(self, guids: Sequence[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        guids = list(guids)
        for i in range(0, len(guids), 500):
            part = guids[i : i + 500]
            marks = ",".join("?" * len(part))
            for row in self._all(
                f"SELECT message_guid, thread_root_id FROM sent_threads WHERE message_guid IN ({marks})", part
            ):
                out[row[0]] = row[1]
        return out

    def local_replies(self, roots: Sequence[str]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        roots = list(roots)
        for i in range(0, len(roots), 500):
            part = roots[i : i + 500]
            marks = ",".join("?" * len(part))
            for row in self._all(
                f"SELECT message_guid, thread_root_id FROM sent_threads WHERE thread_root_id IN ({marks})", part
            ):
                out.setdefault(row[1], []).append(row[0])
        return out

    def recent_local_threads(self, since: float) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM sent_threads WHERE created_at >= ?", (since,))

    # ------------------------------------------------------------------ custom emoji

    def list_emoji(self) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM custom_emoji ORDER BY name")

    def get_emoji(self, name: str) -> sqlite3.Row | None:
        return self._one("SELECT * FROM custom_emoji WHERE name = ?", (name,))

    def add_emoji(self, row: dict[str, Any]) -> bool:
        """Insert; False when the name is taken."""
        cols = ", ".join(row)
        marks = ", ".join("?" * len(row))
        try:
            self._exec(f"INSERT INTO custom_emoji ({cols}) VALUES ({marks})", tuple(row.values()))
        except sqlite3.IntegrityError:
            return False
        return True

    def delete_emoji(self, name: str) -> sqlite3.Row | None:
        with self._lock:
            row = self.get_emoji(name)
            if row is not None:
                self._conn.execute("DELETE FROM custom_emoji WHERE name = ?", (name,))
            return row

    # ------------------------------------------------------------------ attachment file hashes

    def cached_hash(self, guid: str, mtime: float, size: int) -> str | None:
        row = self._one("SELECT sha256 FROM file_hashes WHERE guid = ? AND mtime = ? AND size = ?", (guid, mtime, size))
        return row[0] if row else None

    def store_hash(self, guid: str, mtime: float, size: int, digest: str) -> None:
        self.store_hashes([(guid, mtime, size, digest)])

    def store_hashes(self, rows: Sequence[tuple[str, float, int, str]]) -> None:
        """(guid, mtime, size, sha256) rows, in one transaction."""
        if not rows:
            return
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.executemany(
                    "INSERT OR REPLACE INTO file_hashes (guid, mtime, size, sha256) VALUES (?, ?, ?, ?)", rows)
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def cached_hashes(self, guids: Sequence[str]) -> dict[str, tuple[float, int, str]]:
        """guid -> (mtime, size, sha256) for the guids we've hashed before."""
        out: dict[str, tuple[float, int, str]] = {}
        guids = list(guids)
        for start in range(0, len(guids), 500):
            chunk = guids[start:start + 500]
            marks = ",".join("?" * len(chunk))
            for row in self._all(f"SELECT guid, mtime, size, sha256 FROM file_hashes WHERE guid IN ({marks})", chunk):
                out[row["guid"]] = (row["mtime"], row["size"], row["sha256"])
        return out

    # ------------------------------------------------------------------ server settings

    def get_settings(self) -> dict[str, Any]:
        """`ServerSettings`: the known keys only, with their defaults."""
        stored = self.get_kv("server_settings") or {}
        return {key: bool(stored.get(key, default)) for key, default in SERVER_SETTINGS.items()}

    def update_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            settings = self.get_settings()
            settings.update(patch)
            self.set_kv("server_settings", settings)
            return settings

    # ------------------------------------------------------------------ key/value

    def get_kv(self, key: str) -> Any:
        row = self._one("SELECT value FROM kv WHERE key = ?", (key,))
        return json.loads(row[0]) if row else None

    def set_kv(self, key: str, value: Any) -> None:
        self._exec(
            "INSERT OR REPLACE INTO kv (key, value, updated_at) VALUES (?, ?, ?)",
            (key, json.dumps(value), time.time()),
        )
