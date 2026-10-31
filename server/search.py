"""Full-text search over message text.

Most text lives only in attributedBody blobs, which SQL can't search, so
Backchannel keeps its own FTS5 index in the state dir (search.db). It indexes
new rows first and backfills history in small batches on a background
thread, so recent results are available right away. Delete search.db to
rebuild it.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
from pathlib import Path
from typing import Sequence

from . import heuristics as H
from .chatdb import ChatDB, ChatDBError
from .conversations import is_unsent, message_text

log = logging.getLogger("backchannel.search")

BATCH = 2000
_TOKEN = re.compile(r"[\w@.+'\-]+", re.UNICODE)


class SearchIndex:
    def __init__(self, path: Path, db: ChatDB):
        self.path = Path(path)
        self.db = db
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None, timeout=10)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self.fts = self._has_fts5()
        self._create()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _has_fts5(self) -> bool:
        try:
            self._conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp._probe USING fts5(x)")
            self._conn.execute("DROP TABLE temp._probe")
            return True
        except sqlite3.OperationalError:
            log.warning("SQLite has no FTS5; search falls back to LIKE")
            return False

    def _create(self) -> None:
        with self._lock:
            self._conn.executescript(
                """CREATE TABLE IF NOT EXISTS docs (rowid INTEGER PRIMARY KEY, chat_id INTEGER, date INTEGER, body TEXT);
                   CREATE INDEX IF NOT EXISTS docs_date ON docs(date);
                   CREATE INDEX IF NOT EXISTS docs_chat_date ON docs(chat_id, date);
                   CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);"""
            )
            if self.fts:
                self._conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(body, content='docs', content_rowid='rowid', "
                    "tokenize='unicode61 remove_diacritics 2')"
                )

    # ------------------------------------------------------------------ bookkeeping

    def _meta(self, key: str, default: str | None = None) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def _set_meta(self, key: str, value: str) -> None:
        self._conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))

    def _identity(self) -> str:
        rows = self.db.query("SELECT guid FROM message ORDER BY ROWID LIMIT 1")
        first = rows[0]["guid"] if rows else ""
        return f"{self.db.path}|{first}"

    def _check_identity(self) -> None:
        """Wipe the index if chat.db was replaced (e.g. regenerated fixtures)."""
        identity = self._identity()
        if self._meta("identity") != identity:
            self._conn.execute("DELETE FROM docs")
            if self.fts:
                self._conn.execute("INSERT INTO fts(fts) VALUES ('delete-all')")
            self._conn.execute("DELETE FROM meta")
            self._set_meta("identity", identity)

    @property
    def complete(self) -> bool:
        with self._lock:
            return self._meta("low") == "0"

    # ------------------------------------------------------------------ indexing

    def _docs_for(self, rows: Sequence) -> list[tuple[int, int | None, int, str]]:
        docs = []
        for row in rows:
            if H.is_reaction_type(row["associated_message_type"]) or row["item_type"]:
                continue
            text = message_text(row)
            if not text or is_unsent(row, text, False):
                continue
            docs.append((row["ROWID"], row["chat_id"], row["date"] or 0, text))
        return docs

    def _write(self, docs: Sequence[tuple[int, int | None, int, str]]) -> None:
        for rowid, chat_id, date, body in docs:
            self._delete(rowid)
            self._conn.execute("INSERT INTO docs (rowid, chat_id, date, body) VALUES (?, ?, ?, ?)", (rowid, chat_id, date, body))
            if self.fts:
                self._conn.execute("INSERT INTO fts (rowid, body) VALUES (?, ?)", (rowid, body))

    def _delete(self, rowid: int) -> None:
        old = self._conn.execute("SELECT body FROM docs WHERE rowid = ?", (rowid,)).fetchone()
        if old is None:
            return
        if self.fts:
            self._conn.execute("INSERT INTO fts (fts, rowid, body) VALUES ('delete', ?, ?)", (rowid, old[0]))
        self._conn.execute("DELETE FROM docs WHERE rowid = ?", (rowid,))

    def _fetch(self, where: str, params: Sequence, order: str) -> list:
        fields = ("ROWID", "text", "attributedBody", "date", "associated_message_type", "item_type",
                  "date_retracted", "date_edited", "message_summary_info")
        return self.db.query(
            f"""SELECT {self.db.message_select(fields=fields)}, cmj.chat_id AS chat_id
                FROM message m LEFT JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
                WHERE {where} ORDER BY m.ROWID {order} LIMIT {BATCH}""",
            params,
        )

    def sync(self, backfill_batches: int | None = None) -> int:
        """Index new rows, then up to `backfill_batches` older batches (None = all). Returns rows indexed."""
        indexed = 0
        with self._lock:
            self._check_identity()
            high = int(self._meta("high", "-1") or -1)
            if high < 0:
                # Start one batch below the top so the newest rows are searchable right away;
                # older history is backfilled newest-first.
                start = max(0, self.db.max_rowid() - BATCH)
                self._set_meta("high", str(start))
                self._set_meta("low", str(start + 1))
                high = start
            while True:  # new rows
                rows = self._fetch("m.ROWID > ?", (high,), "ASC")
                if not rows:
                    break
                self._conn.execute("BEGIN")
                self._write(self._docs_for(rows))
                high = rows[-1]["ROWID"]
                self._set_meta("high", str(high))
                self._conn.execute("COMMIT")
                indexed += len(rows)
        batches = 0
        while backfill_batches is None or batches < backfill_batches:
            with self._lock:
                low = int(self._meta("low", "0") or 0)
                if low <= 0:
                    break
                rows = self._fetch("m.ROWID < ?", (low,), "DESC")
                self._conn.execute("BEGIN")
                self._write(self._docs_for(rows))
                low = rows[-1]["ROWID"] if rows and len(rows) == BATCH else 0
                self._set_meta("low", str(low))
                self._conn.execute("COMMIT")
                indexed += len(rows)
            batches += 1
        return indexed

    def reindex(self, rowids: Sequence[int]) -> None:
        """Refresh rows whose text changed (edits) or disappeared (unsends)."""
        if not rowids:
            return
        with self._lock:
            marks = ",".join("?" * len(rowids))
            rows = self._fetch(f"m.ROWID IN ({marks})", list(rowids), "ASC")
            self._conn.execute("BEGIN")
            for rowid in rowids:
                self._delete(rowid)
            self._write(self._docs_for(rows))
            self._conn.execute("COMMIT")

    def start_background(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        def run() -> None:
            while not self._stop.is_set():
                try:
                    self.sync(backfill_batches=1)
                    if self.complete:
                        return
                except (ChatDBError, sqlite3.Error) as exc:
                    log.info("Search backfill paused: %s", exc)
                    self._stop.wait(30)
                self._stop.wait(0.05)

        self._thread = threading.Thread(target=run, name="search-backfill", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------ querying

    @staticmethod
    def _match_expr(query: str) -> str | None:
        tokens = [t.strip("'.-") for t in _TOKEN.findall(query)]
        tokens = [t for t in tokens if t]
        if not tokens:
            return None
        return " ".join('"' + t.replace('"', '""') + '"*' for t in tokens)

    def search(self, query: str, chat_ids: Sequence[int] | None, limit: int) -> list[int]:
        """Matching message ROWIDs, newest first."""
        query = query.strip()
        if not query:
            return []
        chat_filter = ""
        params: list = []
        if chat_ids is not None:
            if not chat_ids:
                return []
            chat_filter = f" AND d.chat_id IN ({','.join('?' * len(chat_ids))})"
            params = list(chat_ids)
        with self._lock:
            if self.fts:
                expr = self._match_expr(query)
                if not expr:
                    return []
                sql = (f"SELECT d.rowid FROM fts JOIN docs d ON d.rowid = fts.rowid "
                       f"WHERE fts MATCH ?{chat_filter} ORDER BY d.date DESC LIMIT ?")
                try:
                    rows = self._conn.execute(sql, (expr, *params, limit)).fetchall()
                except sqlite3.OperationalError:
                    rows = []
            else:
                like = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                sql = (f"SELECT d.rowid FROM docs d WHERE d.body LIKE ? ESCAPE '\\'{chat_filter} "
                       f"ORDER BY d.date DESC LIMIT ?")
                rows = self._conn.execute(sql, (like, *params, limit)).fetchall()
        return [r[0] for r in rows]
