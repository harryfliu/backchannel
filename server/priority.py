""""Needs you": what needs the user first (docs/api.md, "Needs you (priority)").

Pure: `prioritize(kind, items, asking_guid, vips, now, tz)` takes a
conversation's real messages from the last few days (oldest first) and returns
the conversation's `Priority` (or None) and each counting message's
`importance`. ConversationService feeds it from chat.db and caches it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any

from .timephrases import TimeHit, find_time

WINDOW = timedelta(days=3)
REPEAT_GAP = timedelta(minutes=30)
SOON = timedelta(hours=3)
GRACE = timedelta(hours=1)
ORDER = ("urgent", "repeat", "time", "asked", "mention", "reply", "vip")
WEIGHTS = {"urgent": 80, "repeat": 40, "time": 30, "time_soon": 80, "asked": 60, "mention": 50, "reply": 40, "vip": 25}
URGENT = re.compile(
    r"(?<![\w.])(urgent|asap|a\.s\.a\.p\.?|emergency|911|hospital|accident|call me|call asap|pick up|"
    r"answer your phone|need you now)(?![\w])|(?<![\w])(help!)",
    re.IGNORECASE,
)


@dataclass
class Item:
    """One real message (no tapbacks, events or unsent) in the window."""

    guid: str
    sent: float  # unix seconds
    from_me: bool
    sender: str | None = None  # the person's key (for repeats and VIPs)
    text: str = ""
    unread: bool = False
    mentions_me: bool = False
    reply_to_me: bool = False  # an inline reply to one of the user's messages


@dataclass
class Result:
    priority: dict[str, Any] | None
    importance: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


def urgent_word(text: str) -> str | None:
    """The urgent word or phrase in a text (lowercase), or None."""
    match = URGENT.search(text or "")
    if not match:
        return None
    word = (match.group(1) or match.group(2)).lower()
    return word.rstrip(".") if word.startswith("a.s.a.p") else word


def _soon(hit_at: datetime | None, now: datetime) -> bool:
    """A time whose moment is within the next 3 hours (or passed less than an hour ago)."""
    return hit_at is not None and now - GRACE <= hit_at <= now + SOON


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat(timespec="seconds") if moment is not None else None


def prioritize(kind: str, items: list[Item], asking_guid: str | None, vips: dict[str, str], now: datetime,
               tz: tzinfo) -> Result:
    """`kind` is "dm" or "group"; `asking_guid` is the DM's first asking message when it's awaitingReply."""
    now_ts = now.timestamp()
    items = [m for m in items if m.sent >= now_ts - WINDOW.total_seconds()]
    last_mine = max((i for i, m in enumerate(items) if m.from_me), default=-1)
    in_run = {m.guid for m in items[last_mine + 1:] if not m.from_me}
    reasons: dict[str, list[dict[str, Any]]] = {}
    hits: dict[str, TimeHit] = {}

    def add(item: Item, reason: dict[str, Any]) -> None:
        reasons.setdefault(item.guid, []).append(reason)

    for m in items:
        if m.from_me:
            continue
        counts = m.unread or m.guid in in_run
        asking = kind == "dm" and m.guid == asking_guid
        if asking:
            add(m, {"kind": "asked"})
        if counts and kind == "group" and m.mentions_me:
            add(m, {"kind": "mention"})
        if counts and m.reply_to_me:
            add(m, {"kind": "reply"})
        if m.unread or asking:
            found = find_time(m.text, datetime.fromtimestamp(m.sent, timezone.utc), now, tz) if m.text else None
            if found is not None:
                hits[m.guid] = found
                add(m, {"kind": "time", "label": found.label, "at": _iso(found.at)})
        if m.unread:
            word = urgent_word(m.text)
            if word:
                add(m, {"kind": "urgent", "word": word})

    # 3+ unread texts in a row from the same person, each within 30 min of the one before.
    streak: list[Item] = []
    for m in items:
        if m.from_me or not m.unread:
            streak = []
            continue
        if streak and streak[-1].sender == m.sender and m.sent - streak[-1].sent <= REPEAT_GAP.total_seconds():
            streak.append(m)
        else:
            streak = [m]
        if len(streak) >= 3:
            add(m, {"kind": "repeat", "count": len(streak)})

    for m in items:
        found_reasons = reasons.get(m.guid)
        if found_reasons and m.sender and m.sender in vips:
            found_reasons.append({"kind": "vip", "name": vips[m.sender]})
        if found_reasons:
            found_reasons.sort(key=lambda r: ORDER.index(r["kind"]))

    counted = [m for m in items if m.guid in reasons]
    if not counted:
        return Result(None, {})

    def weight(reason: dict[str, Any], hit: TimeHit | None) -> int:
        if reason["kind"] == "repeat":
            return WEIGHTS["repeat"] * max(0, reason["count"] - 2)
        if reason["kind"] == "time":
            return WEIGHTS["time_soon"] if hit is not None and _soon(hit.at, now) else WEIGHTS["time"]
        return WEIGHTS[reason["kind"]]

    union: list[dict[str, Any]] = []
    union_hit: TimeHit | None = None
    for reason_kind in ORDER:
        owners = [m for m in counted if any(r["kind"] == reason_kind for r in reasons[m.guid])]
        if not owners:
            continue
        if reason_kind == "time":
            owner = min(owners, key=lambda m: hits[m.guid].when)  # the soonest
            union_hit = hits[owner.guid]
        elif reason_kind == "repeat":
            owner = max(owners, key=lambda m: next(r["count"] for r in reasons[m.guid] if r["kind"] == "repeat"))
        else:
            owner = owners[-1]  # the newest
        union.append(next(r for r in reasons[owner.guid] if r["kind"] == reason_kind))

    urgent = any(r["kind"] in ("urgent", "repeat") for r in union) or (
        union_hit is not None and _soon(union_hit.at, now))
    newest = counted[-1]
    hours = min(72.0, max(0.0, (now_ts - newest.sent) / 3600))
    score = (300 if urgent else 100) + sum(weight(r, union_hit) for r in union) - hours

    def message_weight(m: Item) -> int:
        return sum(weight(r, hits.get(m.guid)) for r in reasons[m.guid])

    top = max(reversed(counted), key=message_weight)  # most weight, the newest on ties
    stops = [hits[m.guid].stops for m in counted if m.guid in hits]
    priority = {
        "level": "urgent" if urgent else "high",
        "score": round(score),
        "reasons": union,
        "messageId": top.guid,
        "until": _iso(min(stops)) if stops else None,
    }
    return Result(priority, reasons)
