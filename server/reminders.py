"""Saved-for-later reminders.

An item with `remindAt` fires once when that time passes (sets `fired_at`,
emits a `later` event) unless it's done. With "if no reply", a real message
from anyone else in the conversation after the saved message (or after the
item was saved, when the saved message is the user's own) resolves it
instead: `resolved_by_reply`, a `later` event, and no reminder.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from .chatdb import ChatDB, ChatDBError
from .conversations import ConversationService
from .messages import MessageBuilder
from .state import State
from .timeutil import apple_to_ns, iso, unix_to_apple_ns

log = logging.getLogger("backchannel.reminders")

CHECK_INTERVAL = 15.0


def later_json(row: Any, message: dict[str, Any] | None) -> dict[str, Any]:
    """A `later` table row -> LaterItem JSON."""
    return {
        "messageId": row["message_id"],
        "conversationId": row["conversation_id"],
        "savedAt": iso(row["saved_at"]),
        "remindAt": iso(row["remind_at"]) if row["remind_at"] else None,
        "done": bool(row["done"]),
        "ifNoReply": bool(row["if_no_reply"]),
        "resolvedByReply": bool(row["resolved_by_reply"]),
        "firedAt": iso(row["fired_at"]) if row["fired_at"] else None,
        "message": message,
    }


class Reminders:
    def __init__(self, db: ChatDB, convs: ConversationService, state: State, builder: MessageBuilder,
                 publish: Callable[[dict[str, Any]], None]):
        self.db = db
        self.convs = convs
        self.state = state
        self.builder = builder
        self.publish = publish

    def items(self) -> list[dict[str, Any]]:
        rows = self.state.list_later()
        built = self.builder.by_guids([r["message_id"] for r in rows]) if rows else {}
        return [later_json(r, built.get(r["message_id"])) for r in rows]

    def item(self, message_id: str) -> dict[str, Any] | None:
        row = self.state.get_later(message_id)
        if row is None:
            return None
        return later_json(row, self.builder.by_guid(message_id))

    def _replied(self, row: Any, saved_message: Any) -> bool:
        try:
            conv = self.convs.get(row["conversation_id"])
        except LookupError:
            return False
        if saved_message is None or saved_message["is_from_me"]:
            after = unix_to_apple_ns(row["saved_at"])
        else:
            after = apple_to_ns(saved_message["date"])
        return self.db.someone_wrote_after(conv.chat_ids, after)

    def check(self, now: float | None = None) -> list[dict[str, Any]]:
        """Fire due reminders and resolve answered ones. Returns (and publishes) `later` events."""
        now = time.time() if now is None else now
        rows = self.state.armed_later()
        if not rows:
            return []
        saved = {r["guid"]: r for r in self.db.messages_by_guid([r["message_id"] for r in rows],
                                                                fields=("ROWID", "guid", "date", "is_from_me"))}
        changed: list[str] = []
        for row in rows:
            if row["if_no_reply"] and self._replied(row, saved.get(row["message_id"])):
                if self.state.resolve_later(row["message_id"]):
                    changed.append(row["message_id"])
                continue
            if row["remind_at"] is not None and row["remind_at"] <= now:
                if self.state.fire_later(row["message_id"], now):
                    changed.append(row["message_id"])
        if not changed:
            return []
        built = self.builder.by_guids(changed)
        events = []
        for message_id in changed:
            row = self.state.get_later(message_id)
            if row is not None:
                events.append({"type": "later", "item": later_json(row, built.get(message_id))})
        for event in events:
            self.publish(event)
        return events

    async def loop(self, interval: float = CHECK_INTERVAL) -> None:
        while True:
            try:
                await asyncio.to_thread(self.check)
            except ChatDBError as exc:
                log.debug("Reminder check skipped: %s", exc)
            except Exception:
                log.exception("Reminder check failed")
            await asyncio.sleep(interval)
