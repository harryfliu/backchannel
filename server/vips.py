"""Who's close to the user, for the "vip" reason in "Needs you".

A person is a VIP when they're the other person in a starred DM, in Chief's
people (matched by name like the Chief card: Chief has no phone numbers), or
one of the 8 people the user sent the most texts to in the last 90 days. VIP
only ever adds to a message that already counts (server/priority.py).
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from .chatdb import ChatDB, ChatDBError, NOT_REACTION_SQL
from .chief import Chief, Who, match_name
from .contacts import ContactBook
from .state import State
from .timeutil import unix_to_apple_ns

TOP_N = 8
TOP_DAYS = 90
TTL = 600.0  # Chief names and the top-8 are re-read every 10 minutes


def who_for(person: Any, contact: Any) -> Who:
    """Names to match a person against Chief's people (the same as the Chief card)."""
    who = Who()
    if contact is not None:
        full = " ".join(p for p in (contact.first, contact.last) if p)
        who.full_names = [n for n in (full, contact.nickname, contact.organization) if n]
        who.first_name = contact.first
    if person.is_contact and person.name not in who.full_names:
        who.full_names.append(person.name)
    return who


class VipBook:
    def __init__(self, db: ChatDB, state: State, contacts: ContactBook, chief: Chief | None = None,
                 clock: Callable[[], float] = time.time):
        self.db = db
        self.state = state
        self.contacts = contacts
        self.chief = chief
        self.clock = clock
        self._lock = threading.Lock()
        self._chief: tuple[float, int, dict[str, str]] | None = None  # (read at, directory id, key → name)
        self._top: tuple[float, int, dict[str, str]] | None = None
        self._combined: tuple[tuple, tuple[dict[str, str], tuple]] | None = None

    def vips(self, directory: Any) -> tuple[dict[str, str], tuple]:
        """(person key → short name, a version that changes when the set does). Cheap to call per
        conversation: recomputed only when prefs change or the 10-minute caches expire."""
        now = self.clock()
        with self._lock:
            key = (id(directory), self.state.version, int(now // TTL))
            if self._combined is not None and self._combined[0] == key:
                return self._combined[1]
            if self._chief is None or now - self._chief[0] > TTL or self._chief[1] != id(directory):
                self._chief = (now, id(directory), self._from_chief(directory))
            if self._top is None or now - self._top[0] > TTL or self._top[1] != id(directory):
                self._top = (now, id(directory), self._most_texted(directory))
            out = {**self._top[2], **self._chief[2], **self._starred(directory)}
            result = (out, (tuple(sorted(out.items())),))
            self._combined = (key, result)
            return result

    def _starred(self, directory: Any) -> dict[str, str]:
        out: dict[str, str] = {}
        for conv_id, prefs in self.state.all_prefs().items():
            if not prefs.get("starred"):
                continue
            conv = directory.convs.get(conv_id)
            if conv is not None and conv.kind == "dm" and not conv.is_self and conv.persons:
                person = conv.persons[0]
                out[person.key] = person.short_name
        return out

    def _from_chief(self, directory: Any) -> dict[str, str]:
        names = self.chief.people_names() if self.chief is not None else []
        if not names:
            return {}
        out: dict[str, str] = {}
        seen: set[str] = set()
        for person in directory.persons.values():
            if person.key in seen or not person.is_contact:
                continue
            seen.add(person.key)
            if match_name(names, who_for(person, self.contacts.lookup(person.handle))):
                out[person.key] = person.short_name
        return out

    def _most_texted(self, directory: Any) -> dict[str, str]:
        since = unix_to_apple_ns(self.clock() - TOP_DAYS * 86400)
        try:
            rows = self.db.query(
                f"""SELECT cmj.chat_id AS chat_id, COUNT(*) AS sent FROM message m
                    JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
                    WHERE m.date > ? AND m.is_from_me = 1 AND m.item_type = 0 AND {NOT_REACTION_SQL}
                    GROUP BY cmj.chat_id""",
                (since,),
            )
        except ChatDBError:
            return {}
        counts: dict[str, int] = {}
        names: dict[str, str] = {}
        for row in rows:
            conv = directory.by_chat.get(row["chat_id"])
            if conv is None or conv.kind != "dm" or conv.is_self or not conv.persons:
                continue
            person = conv.persons[0]
            counts[person.key] = counts.get(person.key, 0) + row["sent"]
            names[person.key] = person.short_name
        top = sorted(counts, key=lambda k: (-counts[k], k))[:TOP_N]
        return {k: names[k] for k in top}
