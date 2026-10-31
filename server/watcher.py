"""Live updates: poll `PRAGMA data_version`, diff, and fan events out over SSE.

On every change to chat.db the watcher emits
- `message` for new rows (tapbacks are folded, never sent as messages),
- `message-updated` for targets of new tapbacks, roots of new thread replies
  and recent rows whose edit/unsend/delivery/read state changed,
- `send-status` when a pending send is confirmed by its chat.db row,
- `conversation` for every conversation whose summary changed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any

from . import heuristics as H
from .chatdb import ChatDB, ChatDBError, ChangeTracker
from .conversations import ConversationService
from .messages import MessageBuilder, target_guid
from .search import SearchIndex
from .sending import SendService

log = logging.getLogger("backchannel.watcher")

WINDOW = 2000  # recent rows whose mutable fields are tracked
MAX_MESSAGE_EVENTS = 200


class EventHub:
    """Fan-out of ServerEvent dicts to SSE subscribers (thread-safe publish)."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self.history: list[dict[str, Any]] = []  # recent events, handy for tests and debugging

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def publish(self, event: dict[str, Any]) -> None:
        self.history.append(event)
        del self.history[:-500]
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._deliver(event)
        else:
            loop.call_soon_threadsafe(self._deliver, event)

    def _deliver(self, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                log.warning("Dropping a slow event subscriber")
                self._subscribers.discard(queue)


class Watcher:
    def __init__(
        self,
        db: ChatDB,
        tracker: ChangeTracker,
        convs: ConversationService,
        builder: MessageBuilder,
        sending: SendService,
        search: SearchIndex | None,
        hub: EventHub,
    ):
        self.db = db
        self.tracker = tracker
        self.convs = convs
        self.builder = builder
        self.sending = sending
        self.search = search
        self.hub = hub
        self.reminders = None  # set by the app (Reminders); checked when new messages arrive
        self._lock = threading.Lock()
        self._generation = -1
        self._max_rowid = 0
        self._window: dict[int, tuple] = {}
        self._snapshots: dict[str, str] = {}
        self._primed = False

    # ------------------------------------------------------------------ setup

    def prime(self) -> None:
        with self._lock:
            self._generation = self.tracker.check()
            self._max_rowid = self.db.max_rowid()
            self._window = self._read_window()
            self._snapshots = {c["id"]: json.dumps(c, sort_keys=True) for c in self.convs.list()}
            self._primed = True

    def _read_window(self) -> dict[int, tuple]:
        out: dict[int, tuple] = {}
        for row in self.db.mutable_fields_since(max(0, self._max_rowid - WINDOW)):
            out[row["ROWID"]] = (
                row["guid"], row["date_edited"] or 0, row["date_retracted"] or 0, row["is_delivered"] or 0,
                row["date_delivered"] or 0, row["date_read"] or 0, row["error"] or 0, row["body_len"] or 0,
                row["text_len"] or 0, row["is_read"] or 0, row["associated_message_type"] or 0,
                row["is_from_me"] or 0,
            )
        return out

    # ------------------------------------------------------------------ one pass

    def check(self) -> list[dict[str, Any]]:
        """Look for changes and return the events they produce (also publishes them)."""
        if not self._primed:
            self.prime()  # first pass only records where we are
            return []
        with self._lock:
            generation = self.tracker.check()
            if generation == self._generation:
                return []
            self._generation = generation
            events = self._collect()
        for event in events:
            self.hub.publish(event)
        if self.reminders is not None and any(e["type"] == "message" for e in events):
            try:
                events = events + self.reminders.check()  # a reply may resolve an "if no reply" item
            except Exception:
                log.exception("Reminder check after new messages failed")
        return events

    def _collect(self) -> list[dict[str, Any]]:
        self.convs.refresh()
        directory = self.convs.directory()
        events: list[dict[str, Any]] = []

        # 1. New rows.
        new_rows: list[Any] = []
        while True:
            batch = self.db.rows_after(self._max_rowid, 1000)
            if not batch:
                break
            new_rows.extend(batch)
            self._max_rowid = max(r["ROWID"] for r in batch)
        new_guids = {r["guid"] for r in new_rows}
        touched_convs: set[str] = set()
        reaction_targets: set[str] = set()
        thread_roots: set[str] = set()
        real_rows = []
        for row in new_rows:
            conv = directory.by_chat.get(row["chat_id"]) if row["chat_id"] is not None else None
            if conv:
                touched_convs.add(conv.id)
            if H.is_reaction_type(row["associated_message_type"]):
                target = target_guid(row["associated_message_guid"])
                if target:
                    reaction_targets.add(target)
                continue
            real_rows.append(row)
            if row["thread_originator_guid"]:
                thread_roots.add(row["thread_originator_guid"])

        matches = self.sending.reconcile(real_rows)
        for match in matches:
            if match["threadRootId"]:
                thread_roots.add(match["threadRootId"])
        for message in self.builder.build(real_rows[-MAX_MESSAGE_EVENTS:]):
            events.append({"type": "message", "message": message})
        for match in matches:
            event = {
                "type": "send-status", "clientId": match["clientId"], "conversationId": match["conversationId"],
                "status": "failed" if match["failed"] else "sent", "messageId": match["messageId"],
            }
            if match["failed"]:
                event["error"] = "Messages couldn't deliver this message."
            events.append(event)

        # 2. Recent rows whose mutable fields changed.
        window = self._read_window()
        changed: set[str] = set()
        reindex: list[int] = []
        invalidate: set[str] = set()
        for rowid, fields in window.items():
            old = self._window.get(rowid)
            if old is None or old == fields:
                continue
            guid = fields[0]
            if H.is_reaction_type(fields[10]):
                continue
            # Read state only shows on the user's own messages; for incoming rows it just
            # changes unread counts (handled by the conversation diff below).
            ignore = {9} if fields[11] else {5, 9}
            if any(old[i] != fields[i] for i in range(1, 10) if i not in ignore):
                changed.add(guid)
            if old[1:3] != fields[1:3] or old[7:9] != fields[7:9]:
                reindex.append(rowid)
            rows = self.db.messages_by_rowid([rowid])
            if rows and rows[0]["chat_id"] is not None:
                conv = directory.by_chat.get(rows[0]["chat_id"])
                if conv:
                    invalidate.add(conv.id)
                    touched_convs.add(conv.id)
        self._window = window
        if invalidate:
            self.convs.invalidate(invalidate)

        updates = (reaction_targets | thread_roots | changed) - new_guids
        if updates:
            for message in self.builder.by_guids(sorted(updates)).values():
                events.append({"type": "message-updated", "message": message})

        # 3. Search index.
        if self.search is not None:
            try:
                self.search.sync(backfill_batches=0)
                self.search.reindex(reindex)
            except Exception:
                log.exception("Search index update failed")

        # 4. Conversation summaries go out first, so a brand-new conversation exists in the UI
        #    before its first message and send-status arrive.
        return self._conversation_events(touched_convs) + events

    def _conversation_events(self, touched: set[str]) -> list[dict[str, Any]]:
        events = []
        current = {c["id"]: c for c in self.convs.list()}
        for cid in touched:
            if cid not in current:
                try:
                    current[cid] = self.convs.public_summary(self.convs.get(cid))
                except LookupError:
                    pass
        for cid, summary in current.items():
            encoded = json.dumps(summary, sort_keys=True)
            if self._snapshots.get(cid) != encoded:
                self._snapshots[cid] = encoded
                events.append({"type": "conversation", "conversation": summary})
        return events

    def note_state_change(self, conv_id: str) -> None:
        """Prefs/read marks changed through the API: push the new summary to other tabs."""
        try:
            summary = self.convs.public_summary(self.convs.get(conv_id))
        except LookupError:
            return
        with self._lock:
            self._snapshots[summary["id"]] = json.dumps(summary, sort_keys=True)
        self.hub.publish({"type": "conversation", "conversation": summary})

    # ------------------------------------------------------------------ loop

    async def run(self, interval: float = 1.0) -> None:
        failures = 0
        while True:
            try:
                await asyncio.to_thread(self.check)
                failures = 0
            except ChatDBError as exc:
                failures += 1
                if failures in (1, 30):
                    log.warning("Watcher can't read chat.db: %s", exc)
            except Exception:
                log.exception("Watcher error")
            await asyncio.sleep(interval if failures == 0 else min(30.0, interval * (failures + 1)))
