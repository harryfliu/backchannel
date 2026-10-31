"""Conversations: merging chat rows, people, titles and sidebar summaries.

A DM conversation is every 1:1 chat whose participant resolves to the same
contact (or the same normalised handle): Jordan's phone chat, SMS chat and
email chat become one `dm:+14155550134`. Group chats map 1:1 to chat rows.

Summaries are cached per conversation and keyed on the newest message in
each of its chats, so after a new message only that conversation is
recomputed. Unread counts come from one query over the is_read index.
"""

from __future__ import annotations

import hashlib
import logging
import plistlib
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import quote

from datetime import datetime, timezone

from . import heuristics as H
from .chatdb import ChatDB, ChatDBError, ChatInfo, ChangeTracker, HandleInfo, Meta
from .contacts import Contact, ContactBook
from .handles import format_handle, is_short_code, normalize_handle
from .priority import WINDOW as PRIORITY_WINDOW, Item, Result, prioritize
from .state import State
from .timephrases import local_tz
from .timeutil import apple_to_ns, apple_to_unix, iso, iso_apple, now_apple_ns, unix_to_apple_ns
from .typedstream import clean_text, decode_text, extract_mentions

log = logging.getLogger("backchannel.conversations")

DAY_NS = 86400 * 1_000_000_000
# Columns needed for previews, the unanswered run and automated detection (no payload blobs).
FACT_FIELDS = (
    "ROWID", "guid", "text", "attributedBody", "handle_id", "service", "date", "date_edited", "date_retracted",
    "is_from_me", "item_type", "group_title", "group_action_type", "other_handle", "associated_message_type",
    "associated_message_emoji", "balloon_bundle_id", "message_summary_info", "thread_originator_guid",
    "cache_has_attachments", "is_audio_message", "has_unseen_mention", "is_read",
)
AWAITING_WINDOW_NS = 7 * DAY_NS
_SAFE_ID = re.compile(r"^[A-Za-z0-9@._+\-]+$")


class NotFound(LookupError):
    """Raised for unknown conversation/message ids; the message is user-facing."""


def safe_key(value: str) -> str:
    """Conversation id fragment: the value itself when URL-friendly, else a short hash."""
    if value and _SAFE_ID.match(value):
        return value
    return "h" + hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def message_text(row: Any) -> str:
    """Plain text for a message row: the text column, else the decoded attributedBody."""
    raw = row["text"]
    if raw and raw.replace("￼", "").strip():
        return clean_text(raw)
    return clean_text(decode_text(row["attributedBody"]))


def summary_info(blob: bytes | None) -> dict:
    if not blob:
        return {}
    try:
        data = plistlib.loads(bytes(blob))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def is_unsent(row: Any, text: str, has_attachments: bool) -> bool:
    if row["date_retracted"]:
        return True
    if row["date_edited"] and not text and not has_attachments:
        info = summary_info(row["message_summary_info"])
        return bool(info.get("rp")) or "otr" in info
    return False


# --------------------------------------------------------------------------- people


@dataclass
class Person:
    key: str
    handle: str
    handles: list[str]
    name: str
    short_name: str
    is_contact: bool
    has_photo: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "handle": self.handle,
            "handles": list(self.handles),
            "name": self.name,
            "shortName": self.short_name,
            "isContact": self.is_contact,
            "avatarUrl": f"/api/avatar/{quote(self.handle, safe='')}" if self.has_photo else None,
        }


@dataclass
class Me:
    name: str
    short_name: str
    handles: list[str]
    avatar_handle: str | None = None

    def is_me(self, handle: str | None) -> bool:
        if not handle:
            return False
        return handle in self.handles or normalize_handle(handle) in self.handles

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "shortName": self.short_name,
            "handles": list(self.handles),
            "avatarUrl": f"/api/avatar/{quote(self.avatar_handle, safe='')}" if self.avatar_handle else None,
        }


@dataclass
class Conv:
    id: str
    kind: str  # "dm" | "group"
    chats: list[ChatInfo]
    persons: list[Person]
    display_name: str | None = None
    is_self: bool = False
    aliases: list[str] = field(default_factory=list)

    @property
    def chat_ids(self) -> list[int]:
        return [c.rowid for c in self.chats]


def _join_names(names: list[str]) -> str:
    if not names:
        return "Group"
    if len(names) == 1:
        return names[0]
    if len(names) <= 4:
        return ", ".join(names[:-1]) + " & " + names[-1]
    return ", ".join(names[:3]) + f" & {len(names) - 3} others"


def conv_title(conv: Conv, me: Me) -> str:
    if conv.kind == "group":
        return conv.display_name or _join_names([p.short_name for p in conv.persons])
    if conv.is_self:
        return f"{me.name} (you)"
    return conv.persons[0].name if conv.persons else "Unknown"


class Directory:
    """People and conversations derived from chat.db metadata + Contacts."""

    def __init__(self, meta: Meta, contacts: ContactBook, me: Me):
        self.meta = meta
        self.me = me
        self.persons: dict[str, Person] = {}
        self.convs: dict[str, Conv] = {}
        self.by_chat: dict[int, Conv] = {}
        self._build_people(contacts)
        self._build_conversations()

    # people ---------------------------------------------------------------

    def _build_people(self, contacts: ContactBook) -> None:
        groups: dict[str, list[str]] = {}
        cards: dict[str, Contact | None] = {}
        for info in sorted(self.meta.handles.values(), key=lambda h: h.rowid):
            norm = info.norm
            if not norm or norm in self.persons:
                continue
            contact = contacts.lookup(norm)
            key = f"c:{contact.key}" if contact else f"h:{norm}"
            if norm not in groups.setdefault(key, []):
                groups[key].append(norm)
            cards[key] = contact
        for key, handles in groups.items():
            contact = cards[key]
            phones = [h for h in handles if h.startswith("+")]
            canonical = phones[0] if phones else handles[0]
            ordered = [canonical] + [h for h in handles if h != canonical]
            if contact and contact.name:
                person = Person(key, canonical, ordered, contact.name, contact.short_name, True, contact.has_photo)
            else:
                label = format_handle(canonical)
                person = Person(key, canonical, ordered, label, label, False, False)
            for h in handles:
                self.persons[h] = person

    def person(self, handle: str | None) -> Person:
        if handle and handle in self.persons:  # already normalised (the common case)
            return self.persons[handle]
        norm = normalize_handle(handle or "")
        if norm in self.persons:
            return self.persons[norm]
        label = format_handle(norm) if norm else "Unknown"
        return Person(f"h:{norm}", norm, [norm] if norm else [], label, label, False, False)

    def handle(self, rowid: int | None) -> HandleInfo | None:
        return self.meta.handles.get(rowid or 0)

    def handle_norm(self, rowid: int | None) -> str | None:
        info = self.handle(rowid)
        return info.norm if info else None

    def short_name(self, handle: str | None) -> str:
        if handle and self.me.is_me(handle):
            return "You"
        return self.person(handle).short_name if handle else "Someone"

    # conversations ------------------------------------------------------------

    def _build_conversations(self) -> None:
        dm_chats: dict[str, list[ChatInfo]] = {}
        dm_person: dict[str, Person] = {}
        used_group_ids: set[str] = set()
        for chat in sorted(self.meta.chats.values(), key=lambda c: c.rowid):
            members = [self.handle_norm(h) for h in chat.handle_rowids]
            members = [m for m in members if m]
            if chat.is_group:
                persons: list[Person] = []
                for norm in members:
                    if self.me.is_me(norm):
                        continue
                    person = self.person(norm)
                    if person not in persons:
                        persons.append(person)
                cid = "g:" + safe_key(chat.identifier or chat.guid)
                if cid in used_group_ids:
                    cid = f"{cid}:{chat.rowid}"
                used_group_ids.add(cid)
                conv = Conv(cid, "group", [chat], persons, display_name=chat.display_name)
                self.convs[cid] = conv
                self.by_chat[chat.rowid] = conv
                continue
            other = members[0] if members else normalize_handle(chat.identifier)
            if not other:
                continue
            if self.me.is_me(other):
                key = "self"
            else:
                person = self.person(other)
                key = person.key
                dm_person[key] = person
            dm_chats.setdefault(key, []).append(chat)

        for key, chats in dm_chats.items():
            if key == "self":
                handle = self.me.handles[0] if self.me.handles else normalize_handle(chats[0].identifier)
                person = self.person(handle)
                person = Person(person.key, handle, list(self.me.handles) or [handle], self.me.name,
                                self.me.short_name, person.is_contact, person.has_photo)
                conv = Conv("dm:" + safe_key(handle), "dm", chats, [person], is_self=True)
                conv.aliases = ["dm:" + safe_key(h) for h in self.me.handles if h != handle]
            else:
                person = dm_person[key]
                conv = Conv("dm:" + safe_key(person.handle), "dm", chats, [person])
                conv.aliases = ["dm:" + safe_key(h) for h in person.handles if h != person.handle]
            self.convs[conv.id] = conv
            for chat in chats:
                self.by_chat[chat.rowid] = conv
        for conv in list(self.convs.values()):
            for alias in conv.aliases:
                self.convs.setdefault(alias, conv)

    def get(self, conv_id: str) -> Conv:
        conv = self.convs.get(conv_id)
        if conv is None:
            raise NotFound("That conversation isn't in Messages anymore.")
        return conv

    def unique_convs(self) -> list[Conv]:
        seen: set[str] = set()
        out = []
        for conv in self.convs.values():
            if conv.id not in seen:
                seen.add(conv.id)
                out.append(conv)
        return out


# --------------------------------------------------------------------------- summaries


@dataclass
class Facts:
    """Time-independent facts about a conversation's recent messages (cached)."""

    preview: dict[str, Any] | None
    service: str
    last_date: int
    run_newest: int | None = None   # newest message in the unanswered run (ns)
    awaiting_since: int | None = None  # first asking/qualifying message in the run (ns)
    asking_guid: str | None = None  # …and that message ("asked" goes on it)
    automated: bool = False
    rows: list[Any] = field(default_factory=list)  # the newest rows read for these facts (newest first)
    complete: bool = False  # `rows` holds every row of the conversation


def normalize_service(value: str | None, fallback: str = "iMessage") -> str:
    if value in ("iMessage", "SMS", "RCS"):
        return value
    low = (value or "").lower()
    if "sms" in low:
        return "SMS"
    if "rcs" in low:
        return "RCS"
    return fallback if fallback in ("iMessage", "SMS", "RCS") else "iMessage"


def attachment_label(kinds: list[str], names: list[str], is_audio: bool, sticker: bool) -> str:
    if is_audio:
        return "Voice message"
    if sticker:
        return "Sticker"
    if not kinds:
        return "Attachment"
    if all(k == "image" for k in kinds):
        return "Photo" if len(kinds) == 1 else f"{len(kinds)} Photos"
    if all(k == "video" for k in kinds):
        return "Video" if len(kinds) == 1 else f"{len(kinds)} Videos"
    if len(kinds) == 1:
        return "Audio" if kinds[0] == "audio" else (names[0] or "Attachment")
    return f"{len(kinds)} Attachments"


class ConversationService:
    def __init__(
        self,
        db: ChatDB,
        tracker: ChangeTracker,
        contacts: ContactBook,
        state: State,
        me_factory: Callable[[], Me],
        sidebar_days: int,
        sidebar_max: int,
        attachment_kinds: Callable[[list[int]], dict[int, list[tuple[str, str, bool, bool, bool]]]],
    ):
        self.db = db
        self.tracker = tracker
        self.contacts = contacts
        self.state = state
        self.me_factory = me_factory
        self.sidebar_days = sidebar_days
        self.sidebar_max = sidebar_max
        self.attachment_kinds = attachment_kinds
        self._lock = threading.RLock()
        self._generation = -1
        self._meta: Meta | None = None
        self._contacts_version = -1
        self._directory: Directory | None = None
        self._me: Me | None = None
        self._last_dates: dict[int, tuple[int, int]] = {}
        self._facts: dict[str, tuple[tuple, Facts]] = {}
        self._dirty: dict[str, int] = {}
        self._unread: dict[str, list[tuple[int, bool]]] | None = None
        self._list_cache: tuple[tuple, list[dict[str, Any]]] | None = None
        # "Needs you" (server/priority.py): each conversation's priority, cached until its inputs change
        # (or 5 minutes pass).
        self.clock: Callable[[], float] = time.time
        self.tz = local_tz()
        self.vip_book: Any = None  # vips.VipBook, set by the app
        self._priorities: dict[str, tuple[tuple, Result]] = {}
        self._texts: dict[tuple[int, int], str] = {}  # (ROWID, date_edited) → decoded text

    # ------------------------------------------------------------------ refresh

    def refresh(self) -> int:
        """Reload metadata if chat.db changed; returns the current generation."""
        with self._lock:
            generation = self.tracker.check()
            self.contacts.reload_if_changed()
            if generation == self._generation and self.contacts.version == self._contacts_version:
                return generation
            meta = self.db.load_meta()
            if (
                self._directory is None
                or not meta.same_as(self._meta)
                or self.contacts.version != self._contacts_version
            ):
                self._me = self.me_factory()
                self._directory = Directory(meta, self.contacts, self._me)
                self._meta = meta
                self._contacts_version = self.contacts.version
                self._facts.clear()
            self._last_dates = self.db.chat_last_dates()
            self._unread = None
            self._generation = generation
            return generation

    def directory(self) -> Directory:
        self.refresh()
        assert self._directory is not None
        return self._directory

    def me(self) -> Me:
        self.refresh()
        assert self._me is not None
        return self._me

    def get(self, conv_id: str) -> Conv:
        return self.directory().get(conv_id)

    def dm_for_handle(self, handle: str) -> Conv | None:
        """The existing 1:1 conversation with whoever owns `handle` (any of their numbers or emails)."""
        directory = self.directory()
        me = directory.me
        if me.is_me(handle):
            return next((c for c in directory.unique_convs() if c.is_self), None)
        person = directory.persons.get(handle)
        if person is not None:
            key = person.key
        else:
            contact = self.contacts.lookup(handle)
            key = f"c:{contact.key}" if contact else f"h:{handle}"
        for conv in directory.unique_convs():
            if conv.kind == "dm" and not conv.is_self and conv.persons and conv.persons[0].key == key:
                return conv
        return None

    def expected_dm_id(self, handle: str) -> str:
        """The id a new DM with `handle` will get once Messages writes its chat row."""
        person = self.directory().persons.get(handle)
        return "dm:" + safe_key(person.handle if person is not None else handle)

    def invalidate(self, conv_ids: set[str] | list[str]) -> None:
        """Called by the watcher when existing rows (edits, unsends, read state) change."""
        with self._lock:
            for cid in conv_ids:
                self._dirty[cid] = self._dirty.get(cid, 0) + 1
            self._unread = None
            self._list_cache = None

    def last_date(self, conv: Conv) -> int:
        dates = [self._last_dates.get(c, (0, 0))[0] for c in conv.chat_ids]
        return apple_to_ns(max(dates)) if dates else 0

    def newest_chat(self, conv: Conv) -> ChatInfo:
        """The most recently used chat row (where replies are sent)."""
        self.refresh()
        return max(conv.chats, key=lambda c: (self._last_dates.get(c.rowid, (0, 0)), c.rowid))

    # ------------------------------------------------------------------ list

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            generation = self.refresh()
            key = (generation, self.state.version, int(self.clock() // 60))
            if self._list_cache and self._list_cache[0] == key:
                return self._list_cache[1]
            directory = self._directory
            assert directory is not None
            prefs = self.state.all_prefs()
            cutoff = now_apple_ns() - self.sidebar_days * DAY_NS
            starred: list[Conv] = []
            recent: list[tuple[int, Conv]] = []
            for conv in directory.unique_convs():
                is_starred = any(prefs.get(k, {}).get("starred") for k in (conv.id, *conv.aliases))
                last = self.last_date(conv)
                if is_starred:
                    starred.append(conv)
                elif last and last >= cutoff:
                    recent.append((last, conv))
            recent.sort(key=lambda item: item[0], reverse=True)
            room = max(0, self.sidebar_max - len(starred))
            chosen = starred + [c for _, c in recent[:room]]
            out = [self.summary(conv, refreshed=True) for conv in chosen]
            out.sort(key=lambda s: s["_last"], reverse=True)
            for item in out:
                item.pop("_last", None)
            self._list_cache = (key, out)
            return out

    # ------------------------------------------------------------------ one conversation

    def summary(self, conv: Conv, *, public: bool = False, refreshed: bool = False) -> dict[str, Any]:
        with self._lock:
            if not refreshed:
                self.refresh()
            me = self._me
            assert me is not None
            facts = self._facts_for(conv)
            mark = self.state.get_mark(conv.id, conv.aliases) or 0
            unread, mentions = self._unread_counts(conv, mark)
            prefs = self.state.get_prefs(conv.id, conv.aliases)
            awaiting = self._awaiting(conv, facts)
            snoozed = prefs.get("snoozed_until")
            result = self._priority_result(conv, facts, awaiting, prefs, mark, unread)
            data = {
                "id": conv.id,
                "kind": conv.kind,
                "title": conv_title(conv, me),
                "isNamed": conv.kind == "group" and bool(conv.display_name),
                "participants": [p.to_json() for p in conv.persons],
                "service": facts.service,
                "lastMessage": facts.preview,
                "unreadCount": unread,
                "mentionCount": mentions,
                "isAutomated": facts.automated,
                "awaitingReply": awaiting,
                "awaitingSince": iso_apple(facts.awaiting_since) if awaiting else None,
                "priority": result.priority,
                "prefs": {
                    "starred": bool(prefs.get("starred")),
                    "muted": bool(prefs.get("muted")),
                    "snoozedUntil": iso(snoozed) if snoozed else None,
                    "section": prefs.get("section"),
                },
            }
            if not public:
                data["_last"] = facts.last_date
            return data

    def public_summary(self, conv: Conv) -> dict[str, Any]:
        data = self.summary(conv, public=True)
        data.pop("_last", None)
        return data

    def is_automated(self, conv: Conv) -> bool:
        with self._lock:
            return self._facts_for(conv).automated

    # ------------------------------------------------------------------ facts

    def _facts_for(self, conv: Conv) -> Facts:
        signature = (tuple(self._last_dates.get(c, (0, 0)) for c in conv.chat_ids), self._dirty.get(conv.id, 0))
        cached = self._facts.get(conv.id)
        if cached and cached[0] == signature:
            return cached[1]
        facts = self._compute_facts(conv)
        self._facts[conv.id] = (signature, facts)
        return facts

    def _compute_facts(self, conv: Conv) -> Facts:
        directory = self._directory
        me = self._me
        assert directory is not None and me is not None
        ordered = self._recent_rows(conv, 10)  # newest first
        newest_real = next((r for r in ordered if not H.is_reaction_type(r["associated_message_type"])
                            and not r["item_type"]), None)
        recent_incoming = newest_real is not None and not newest_real["is_from_me"] and (
            now_apple_ns() - apple_to_ns(newest_real["date"]) < AWAITING_WINDOW_NS)
        fetched = 10
        if len(ordered) == 10 and (newest_real is None or recent_incoming):
            ordered = self._recent_rows(conv, 40)  # the unanswered run may be longer than 10 rows
            fetched = 40
        attachments = self.attachment_kinds([r["ROWID"] for r in ordered if r["cache_has_attachments"]])
        fallback_service = normalize_service(conv.chats[0].service if conv.chats else None)
        facts = Facts(preview=None, service=fallback_service, last_date=self.last_date(conv))
        if ordered:
            facts.service = normalize_service(ordered[0]["service"], fallback_service)

        # Preview: the newest row that has something to show (tapbacks included).
        for row in ordered:
            text = self.preview_text(row, attachments.get(row["ROWID"], []))
            if text:
                sender = None if row["is_from_me"] else directory.handle_norm(row["handle_id"])
                if not row["is_from_me"] and not sender and conv.kind == "dm" and conv.persons:
                    sender = conv.persons[0].handle
                facts.preview = {
                    "id": row["guid"],
                    "text": text,
                    "senderHandle": sender,
                    "fromMe": bool(row["is_from_me"]),
                    "sentAt": iso_apple(row["date"]),
                }
                break

        # The unanswered run: real messages from others after the user's last real one.
        run: list[tuple[Any, str]] = []
        for row in ordered:
            if H.is_reaction_type(row["associated_message_type"]) or row["item_type"]:
                continue
            text = self._text(row)
            has_atts = bool(attachments.get(row["ROWID"]))
            if is_unsent(row, text, has_atts) or (not text and not has_atts and not row["balloon_bundle_id"]):
                continue
            if row["is_from_me"]:
                break
            run.append((row, text))
        run.reverse()  # oldest first
        if run:
            facts.run_newest = apple_to_ns(run[-1][0]["date"])
            asking = self._awaiting_row(conv, run)
            if asking is not None:
                facts.awaiting_since = apple_to_ns(asking["date"])
                facts.asking_guid = asking["guid"]

        facts.automated = self._automated(conv, [t for r, t in run] + [
            self._text(r) for r in ordered if not r["is_from_me"] and not r["item_type"]
        ][:8])
        facts.rows = ordered  # "Needs you" reuses these when they cover its 3-day window
        facts.complete = len(ordered) < fetched
        return facts

    def _text(self, row: Any) -> str:
        """message_text(), remembered: facts, previews and "Needs you" decode the same recent rows."""
        key = (row["ROWID"], row["date_edited"] or 0)
        text = self._texts.get(key)
        if text is None:
            if len(self._texts) > 20000:
                self._texts.clear()
            text = self._texts[key] = message_text(row)
        return text

    def _recent_rows(self, conv: Conv, count: int) -> list[Any]:
        ids = self.db.page_ids(conv.chat_ids, None, count, reactions=True)
        rows = {r["ROWID"]: r for r in self.db.messages_by_rowid([i for _, i in ids], FACT_FIELDS)}
        return [rows[i] for _, i in ids if i in rows]

    def _mentions_me(self, row: Any) -> bool:
        me = self._me
        assert me is not None
        if row["has_unseen_mention"]:
            return True
        return any(me.is_me(h) for h in extract_mentions(row["attributedBody"]))

    def _awaiting_row(self, conv: Conv, run: list[tuple[Any, str]]) -> Any | None:
        """docs/api.md "Waiting on you": the first asking message in the run, else None."""
        if conv.kind == "dm":
            asking = [r for r, t in run if H.asks_something(t) or self._mentions_me(r)]
            if not asking:
                return None
            newest_row, newest_text = run[-1]
            # "Perfect, see you then" closes the loop, but "Perfect. Can you bring his records" doesn't.
            newest_asks = H.asks_something(newest_text) or self._mentions_me(newest_row)
            if H.opens_with_closer(newest_text) and not newest_asks:
                return None
            return asking[0]
        roots = [r["thread_originator_guid"] for r, _ in run if r["thread_originator_guid"]]
        mine: set[str] = set()
        if roots:
            mine = {row["guid"] for row in self.db.messages_by_guid(list(set(roots))) if row["is_from_me"]}
        for row, _ in run:
            if self._mentions_me(row) or (row["thread_originator_guid"] in mine):
                return row
        return None

    def _automated(self, conv: Conv, incoming_texts: list[str]) -> bool:
        if conv.kind != "dm" or conv.is_self or not conv.persons:
            return False
        person = conv.persons[0]
        if any(is_short_code(h) for h in person.handles):
            return True
        if person.is_contact:
            return False
        if self.db.has_sent_to(conv.chat_ids):
            return False
        if any(H.looks_automated_handle(h) for h in person.handles):
            return True
        return any(H.looks_automated(t) for t in incoming_texts if t)

    def preview_text(self, row: Any, attachments: list[tuple[str, str, bool, bool, bool]]) -> str | None:
        """One-line preview: text, 'Photo', 'Loved “…”', an event line…"""
        directory = self._directory
        assert directory is not None
        text = self._text(row) if "ROWID" in row.keys() and "date_edited" in row.keys() else message_text(row)
        assoc = row["associated_message_type"] or 0
        if H.is_reaction_type(assoc):
            if text:
                return _one_line(text)
            kind = H.TAPBACKS.get(assoc if assoc < 3000 else assoc - 1000)
            if assoc == 2006 and row["associated_message_emoji"]:
                return f"Reacted {row['associated_message_emoji']} to a message"
            return f"{kind[2]} a message" if kind and assoc < 3000 else "Removed a reaction"
        if row["item_type"]:
            event = event_line(row, directory)
            return event[1] if event else None
        visible = [a for a in attachments if not a[2]]
        if is_unsent(row, text, bool(visible)):
            who = "You" if row["is_from_me"] else directory.short_name(directory.handle_norm(row["handle_id"]))
            return f"{who} unsent a message"
        if text:
            return _one_line(text)
        if visible or row["is_audio_message"]:
            kinds = [a[0] for a in visible]
            names = [a[1] for a in visible]
            if visible and all(a[4] for a in visible):
                return "Genmoji"
            stickers = bool(visible) and all(a[3] for a in visible)
            return attachment_label(kinds, names, bool(row["is_audio_message"]), stickers)
        if row["balloon_bundle_id"]:
            return "Link" if "URLBalloon" in (row["balloon_bundle_id"] or "") else "Message"
        return None

    # ------------------------------------------------------------------ needs you (priority)

    def _priority_result(self, conv: Conv, facts: Facts, awaiting: bool, prefs: dict[str, Any],
                         mark: int | None = None, unread: int | None = None) -> Result:
        """"Needs you" for one conversation. Cached per conversation: recomputed when its own messages,
        read mark, prefs or the VIPs change, and at least every 5 minutes. Conversations with nothing in
        the last 3 days cost nothing (no query)."""
        if facts.automated or prefs.get("muted") or conv.is_self:
            return Result(None, {})
        now = self.clock()
        since = now - PRIORITY_WINDOW.total_seconds()
        if self.last_date(conv) < unix_to_apple_ns(since):
            return Result(None, {})
        directory = self._directory
        assert directory is not None
        vips: dict[str, str] = {}
        vip_version: tuple = ()
        if self.vip_book is not None:
            vips, vip_version = self.vip_book.vips(directory)
        if mark is None:
            mark = self.state.get_mark(conv.id, conv.aliases) or 0
        if unread is None:
            unread, _ = self._unread_counts(conv, mark)
        if not unread and not (facts.run_newest and facts.run_newest >= unix_to_apple_ns(since)):
            return Result(None, {})  # every reason needs an unread message or an unanswered one
        signature = (tuple(self._last_dates.get(c, (0, 0)) for c in conv.chat_ids), self._dirty.get(conv.id, 0),
                     self._contacts_version)
        key = (signature, mark, awaiting, facts.asking_guid, vip_version, int(now // 300))
        cached = self._priorities.get(conv.id)
        if cached and cached[0] == key:
            return cached[1]
        start = unix_to_apple_ns(since - 3600)
        if facts.rows and (facts.complete or apple_to_ns(facts.rows[-1]["date"]) <= start):
            rows = list(reversed(facts.rows))  # the facts already read the whole window
        else:
            try:
                rows = self.db.window_rows(conv.chat_ids, start)
            except ChatDBError:
                rows = []
        result = prioritize(conv.kind, self._priority_items(conv, rows, mark, directory, start),
                            facts.asking_guid if awaiting else None, vips,
                            datetime.fromtimestamp(now, timezone.utc), self.tz)
        self._priorities[conv.id] = (key, result)
        return result

    def _priority_items(self, conv: Conv, rows: list[Any], mark: int, directory: Directory,
                        start: int = 0) -> list[Item]:
        """The conversation's real messages for priority.py; text is decoded only where it can matter."""
        real = [r for r in rows if (r["date"] or 0) > start and not r["item_type"]
                and not H.is_reaction_type(r["associated_message_type"])]
        last_mine = max((i for i, r in enumerate(real) if r["is_from_me"] and not r["date_retracted"]), default=-1)
        mine = {r["guid"] for r in real if r["is_from_me"]}
        unknown_roots = {r["thread_originator_guid"] for r in real
                         if not r["is_from_me"] and r["thread_originator_guid"] and r["thread_originator_guid"] not in mine}
        if unknown_roots:
            mine |= {r["guid"] for r in self.db.messages_by_guid(list(unknown_roots), ("ROWID", "guid", "is_from_me"))
                     if r["is_from_me"]}
        items: list[Item] = []
        for index, row in enumerate(real):
            sent = apple_to_unix(row["date"]) or 0.0
            if row["is_from_me"]:
                if not row["date_retracted"]:
                    items.append(Item(row["guid"], sent, True))
                continue
            unread = not row["is_read"] and apple_to_ns(row["date"]) > mark
            candidate = unread or index > last_mine
            text = self._text(row) if candidate else ""
            if candidate and (is_unsent(row, text, bool(row["cache_has_attachments"]))
                              or (not text and not row["cache_has_attachments"] and not row["balloon_bundle_id"])):
                continue
            sender = directory.handle_norm(row["handle_id"])
            if not sender and conv.kind == "dm" and conv.persons:
                sender = conv.persons[0].handle
            person = directory.persons.get(sender) if sender else None
            mentions_me = candidate and self._mentions_me(row)
            items.append(Item(
                row["guid"], sent, False, sender=person.key if person else sender, text=text, unread=unread,
                mentions_me=mentions_me and conv.kind == "group",
                reply_to_me=bool(row["thread_originator_guid"]) and row["thread_originator_guid"] in mine,
                asks=candidate and conv.kind == "dm" and (H.asks_something(text) or mentions_me),
            ))
        return items

    def importance_map(self, conv: Conv | None) -> dict[str, list[dict[str, Any]]]:
        """`Message.importance` for a conversation's counting messages (guid → reasons)."""
        if conv is None:
            return {}
        with self._lock:
            self.refresh()
            facts = self._facts_for(conv)
            prefs = self.state.get_prefs(conv.id, conv.aliases)
            return self._priority_result(conv, facts, self._awaiting(conv, facts), prefs).importance

    def _awaiting(self, conv: Conv, facts: Facts) -> bool:
        return bool(not facts.automated and not conv.is_self and facts.run_newest
                    and now_apple_ns() - facts.run_newest < AWAITING_WINDOW_NS and facts.awaiting_since)

    # ------------------------------------------------------------------ unread

    def _unread_counts(self, conv: Conv, mark: int | None = None) -> tuple[int, int]:
        if self._unread is None:
            self._unread = self._load_unread()
        if mark is None:
            mark = self.state.get_mark(conv.id, conv.aliases) or 0
        entries = []
        for cid in (conv.id, *conv.aliases):
            entries.extend(self._unread.get(cid, []))
        fresh = [m for d, m in entries if d > mark]
        return len(fresh), sum(1 for m in fresh if m)

    def _load_unread(self) -> dict[str, list[tuple[int, bool]]]:
        directory = self._directory
        assert directory is not None
        since = now_apple_ns() - self.sidebar_days * DAY_NS
        out: dict[str, list[tuple[int, bool]]] = {}
        seen: set[int] = set()
        for row in self.db.unread_incoming(since):
            if row["ROWID"] in seen or H.is_reaction_type(row["associated_message_type"]):
                continue
            seen.add(row["ROWID"])
            conv = directory.by_chat.get(row["chat_id"])
            if conv is None:
                continue
            text = message_text(row)
            if not text and not row["cache_has_attachments"] and not row["balloon_bundle_id"]:
                continue
            out.setdefault(conv.id, []).append((apple_to_ns(row["date"]), self._mentions_me(row)))
        return out

    # ------------------------------------------------------------------ actions

    def mark_read(self, conv: Conv) -> dict[str, Any]:
        self.refresh()
        ids = self.db.page_ids(conv.chat_ids, None, 1, reactions=True)
        newest = apple_to_ns(ids[0][0]) if ids else now_apple_ns()
        self.state.set_mark(conv.id, max(newest, 1))
        with self._lock:
            self._list_cache = None
        return self.public_summary(conv)

    def set_prefs(self, conv: Conv, patch: dict[str, Any]) -> dict[str, Any]:
        self.state.set_prefs(conv.id, patch, conv.aliases)
        with self._lock:
            self._list_cache = None
        return self.public_summary(conv)


def _one_line(text: str, limit: int = 240) -> str:
    line = " ".join(text.split())
    return line if len(line) <= limit else line[: limit - 1] + "…"


def event_line(row: Any, directory: Directory) -> tuple[str, str] | None:
    """(MessageEventType, text) for group events, with names resolved and "You" for the user."""
    me = directory.me
    item_type = row["item_type"] or 0
    action = row["group_action_type"] or 0
    actor_handle = None if row["is_from_me"] else directory.handle_norm(row["handle_id"])
    actor = "You" if row["is_from_me"] else directory.short_name(actor_handle)
    if item_type == 1:
        target_handle = directory.handle_norm(row["other_handle"])
        if target_handle and me.is_me(target_handle):
            target = "you"
        else:
            target = directory.person(target_handle).short_name if target_handle else "someone"
        if action == 0:
            return "added", f"{actor} added {target}"
        if action == 1:
            if target_handle and target_handle == actor_handle:
                return "left", f"{actor} left the conversation"
            return "removed", f"{actor} removed {target}"
        return "other", f"{actor} changed the members"
    if item_type == 2:
        title = (row["group_title"] or "").strip()
        if title:
            return "renamed", f"{actor} named the conversation “{title}”"
        return "renamed", f"{actor} removed the conversation name"
    if item_type == 3:
        if action == 0:
            return "left", f"{actor} left the conversation"
        if action == 1:
            return "photo", f"{actor} changed the group photo"
        if action == 2:
            return "photo", f"{actor} removed the group photo"
        return None
    if item_type == 5:
        return "other", f"{actor} kept an audio message"
    text = message_text(row)
    return ("other", _one_line(text)) if text else None


def my_handles(configured: list[str], discovered: list[str]) -> list[str]:
    """Configured handles first, then discovered ones in a fixed order (phone numbers, then emails).

    The note-to-self conversation's id is built from the first handle, so this order must not
    depend on which address the latest messages happened to go out from.
    """
    found = sorted({normalize_handle(h) or "" for h in discovered} - {""}, key=lambda h: ("@" in h, h))
    out: list[str] = []
    for raw in [*configured, *found]:
        norm = normalize_handle(raw)
        if norm and norm not in out:
            out.append(norm)
    return out


def build_me(name: str | None, handles: list[str], contacts: ContactBook) -> Me:
    full = (name or "").strip() or "Me"
    short = full.split()[0] if full.split() else full
    avatar = None
    for handle in handles:
        contact = contacts.lookup(handle)
        if contact and contact.has_photo:
            avatar = handle
            break
    return Me(name=full, short_name=short, handles=handles, avatar_handle=avatar)


__all__ = [
    "Conv", "ConversationService", "Directory", "Facts", "Me", "NotFound", "Person",
    "build_me", "event_line", "is_unsent", "message_text", "my_handles", "normalize_service",
]
