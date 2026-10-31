"""Chief: Harry's chief-of-staff app. Read-mostly access to its SQLite database.

Backchannel shows what Chief knows about a person (people, open_loops,
profile_facts) and can add a message as an open loop. Reads use a read-only
connection; the one write uses a short busy timeout because Chief's own
daemon writes the same file.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.request import pathname2url

log = logging.getLogger("backchannel.chief")

BUSY_TIMEOUT = 5.0
MAX_LOOPS = 5
MAX_FACTS = 3
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December")


class ChiefUnavailable(RuntimeError):
    """chief.db is missing or can't be written right now (HTTP 503)."""


@dataclass
class Who:
    """Names Backchannel knows for a person, most specific first."""

    full_names: list[str] = field(default_factory=list)  # "Jordan Park", "Mom"
    first_name: str | None = None                         # "Jordan"


def human_birthday(value: str | None) -> str | None:
    """'03-03' or '1990-03-03' -> 'March 3'; anything else is returned as written."""
    text = (value or "").strip()
    if not text:
        return None
    match = re.fullmatch(r"(?:\d{4}-)?(\d{1,2})-(\d{1,2})", text)
    if match:
        month, day = int(match.group(1)), int(match.group(2))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{MONTHS[month - 1]} {day}"
    return text


def match_name(names: list[str], who: Who) -> str | None:
    """Which of Chief's people names is this person: a full name or nickname, else a first name
    only one Chief person has."""
    by_name = {(n or "").strip().lower(): n for n in names if n}
    for full in who.full_names:
        found = by_name.get((full or "").strip().lower())
        if found is not None:
            return found
    first = (who.first_name or "").strip().lower()
    if first:
        matches = [n for n in names if (n or "").split() and n.split()[0].lower() == first]
        if len(matches) == 1:
            return matches[0]
    return None


def _mentions(text: str, names: list[str]) -> bool:
    for name in names:
        if re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text, re.IGNORECASE):
            return True
    return False


class Chief:
    def __init__(self, path: Path | None):
        self.path = Path(path) if path else None

    # ------------------------------------------------------------------ connections

    def _connect_ro(self) -> sqlite3.Connection:
        assert self.path is not None
        conn = sqlite3.connect(f"file:{pathname2url(str(self.path))}?mode=ro", uri=True, timeout=BUSY_TIMEOUT)
        conn.row_factory = sqlite3.Row
        return conn

    def _tables(self, conn: sqlite3.Connection) -> set[str]:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}

    @property
    def available(self) -> bool:
        if not self.path or not self.path.is_file():
            return False
        try:
            conn = self._connect_ro()
            try:
                return {"people", "open_loops"} <= self._tables(conn)
            finally:
                conn.close()
        except sqlite3.Error:
            return False

    # ------------------------------------------------------------------ reads

    def _match_person(self, conn: sqlite3.Connection, who: Who) -> sqlite3.Row | None:
        rows = conn.execute("SELECT id, name, relationship, birthday, notes FROM people").fetchall()
        name = match_name([r["name"] for r in rows if r["name"]], who)
        return next((r for r in rows if r["name"] == name), None) if name else None

    def people_names(self) -> list[str]:
        """Everyone in Chief's people table (names only), or [] without Chief."""
        if not self.available:
            return []
        try:
            conn = self._connect_ro()
        except sqlite3.Error:
            return []
        try:
            return [r["name"] for r in conn.execute("SELECT name FROM people") if r["name"]]
        except sqlite3.Error as exc:
            log.info("Couldn't read Chief's people: %s", exc)
            return []
        finally:
            conn.close()

    def card(self, who: Who) -> dict[str, Any] | None:
        """ChiefCard JSON for a person, or None when Chief doesn't know them."""
        if not self.available:
            return None
        try:
            conn = self._connect_ro()
        except sqlite3.Error:
            return None
        try:
            person = self._match_person(conn, who)
            if person is None:
                return None
            chief_name = person["name"].strip()
            names: list[str] = []
            for name in [chief_name, chief_name.split()[0], *who.full_names, who.first_name]:
                if name and len(name.strip()) >= 2 and name.strip().lower() not in [n.lower() for n in names]:
                    names.append(name.strip())
            loops = []
            for row in conn.execute(
                """SELECT id, title, status, due_date FROM open_loops
                   WHERE status IN ('open', 'in_progress') ORDER BY created_at DESC, id DESC"""
            ):
                if _mentions(row["title"] or "", names):
                    loops.append({"id": row["id"], "title": row["title"], "status": row["status"],
                                  "dueDate": row["due_date"] or None})
                    if len(loops) == MAX_LOOPS:
                        break
            facts: list[str] = []
            if "profile_facts" in self._tables(conn):
                for row in conn.execute(
                    """SELECT fact FROM profile_facts WHERE superseded_by IS NULL
                       ORDER BY captured_at DESC, id DESC"""
                ):
                    if _mentions(row["fact"] or "", names):
                        facts.append(row["fact"])
                        if len(facts) == MAX_FACTS:
                            break
            return {
                "name": chief_name,
                "relationship": person["relationship"] or None,
                "birthday": human_birthday(person["birthday"]),
                "notes": person["notes"] or None,
                "loops": loops,
                "facts": facts,
            }
        except sqlite3.Error as exc:
            log.info("Couldn't read Chief's database: %s", exc)
            return None
        finally:
            conn.close()

    # ------------------------------------------------------------------ the one write

    def add_loop(self, title: str, message_guid: str) -> dict[str, Any]:
        """Insert an undated open loop the way Chief's meeting tool does, tagged with the message."""
        if not self.path or not self.path.is_file():
            raise ChiefUnavailable(
                f"Chief's database isn't at {self.path or '(not configured)'}. Set \"chief_db\" in "
                "config.local.json (or BACKCHANNEL_CHIEF_DB) to where chief.db lives."
            )
        source_ref = f"backchannel:{message_guid}"
        try:
            conn = sqlite3.connect(str(self.path), timeout=BUSY_TIMEOUT, isolation_level=None)
        except sqlite3.Error as exc:
            raise ChiefUnavailable(f"Couldn't open Chief's database: {exc}") from exc
        try:
            conn.execute(f"PRAGMA busy_timeout = {int(BUSY_TIMEOUT * 1000)}")
            existing = conn.execute(
                """SELECT id, title FROM open_loops
                   WHERE source_ref = ? AND title = ? AND status IN ('open', 'in_progress')""",
                (source_ref, title),
            ).fetchone()
            if existing:  # a double click shouldn't make two loops
                return {"id": existing[0], "title": existing[1]}
            cursor = conn.execute(
                """INSERT INTO open_loops (title, owner, urgency, category, autonomy_tier, status, source_ref)
                   VALUES (?, 'harry', 'medium', 'imessage', 'ask', 'open', ?)""",
                (title, source_ref),
            )
            return {"id": int(cursor.lastrowid), "title": title}
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc) or "busy" in str(exc):
                raise ChiefUnavailable("Chief's database is busy right now. Try again in a moment.") from exc
            raise ChiefUnavailable(f"Couldn't add the loop to Chief: {exc}") from exc
        finally:
            conn.close()
