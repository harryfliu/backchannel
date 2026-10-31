"""The send flow: queue → AppleScript (or dry-run) → wait for the row in chat.db.

A send is "pending" until the watcher sees a matching row (same
conversation, from me, same text or file name, within 2 minutes); then it
emits `send-status: sent` with the real message id, and the `message` event
carries the clientId. Thread replies are remembered locally (sent GUID →
thread root) because AppleScript can't send real inline replies; with
`threadRepliesViaMessages` on, they're typed into Messages' own Reply field
instead (messages_ui), falling back to the normal send only when nothing was
typed there.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import unicodedata
from pathlib import Path
from typing import Any, Sequence

from . import fakedb
from .chatdb import ChatInfo
from .config import Settings
from .conversations import Conv, ConversationService, NotFound, message_text
from .messages_ui import REPLY_UNPROVEN
from .sender import Sender, SendResult
from .state import State
from .timeutil import apple_to_unix, iso

log = logging.getLogger("backchannel.sending")

MATCH_WINDOW = 120  # seconds after dispatch during which a chat.db row can confirm a send
OVERDUE_GRACE = 15 * 60  # scheduled sends older than this when we wake up are not sent
REPLY_DONE = "Sent as a reply in Messages."
REPLY_REGULAR = "Messages sent it as a regular text instead of a reply."
REPLY_UNCONFIRMED = "Messages didn't confirm your reply. Check Messages before you try again."


def _norm(text: str | None) -> str:
    return " ".join(unicodedata.normalize("NFC", text or "").split())


def scheduled_json(row: Any) -> dict[str, Any]:
    status = row["status"]
    return {
        "id": row["id"],
        "conversationId": row["conversation_id"],
        "text": row["text"],
        "sendAt": iso(row["send_at"]),
        "createdAt": iso(row["created_at"]),
        "status": "pending" if status == "sending" else status,
        "error": row["error"],
    }


class SendService:
    def __init__(
        self,
        settings: Settings,
        convs: ConversationService,
        state: State,
        sender: Sender,
        publish,
        attachments_for,
    ):
        self.settings = settings
        self.convs = convs
        self.state = state
        self.sender = sender
        self.publish = publish
        self.attachments_for = attachments_for
        self.helpers: Any = None  # messages_ui.MessagesUI, set by the app: real thread replies
        self._tasks: set[asyncio.Task] = set()

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def drain(self, timeout: float = 10.0) -> None:
        """Wait for in-flight sends (tests), ignoring the long confirmation timers."""
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            busy = [t for t in self._tasks if not getattr(t, "_bc_timer", False)]
            if not busy or asyncio.get_running_loop().time() > deadline:
                return
            await asyncio.wait(busy, timeout=0.1)

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # ------------------------------------------------------------------ helpers

    def _fallback_handle(self, conv: Conv, chat: ChatInfo) -> str | None:
        if conv.kind != "dm":
            return None
        directory = self.convs.directory()
        if chat.handle_rowids:
            info = directory.handle(chat.handle_rowids[0])
            if info and info.raw:
                return info.raw
        return chat.identifier or (conv.persons[0].handle if conv.persons else None)

    def _my_handle(self) -> str | None:
        handles = self.convs.me().handles
        return handles[0] if handles else None

    def _fail(self, client_id: str, conversation_id: str, error: str, message_id: str | None = None) -> None:
        self.state.update_pending(client_id, status="failed", error=error)
        event: dict[str, Any] = {
            "type": "send-status", "clientId": client_id, "conversationId": conversation_id,
            "status": "failed", "error": error,
        }
        if message_id:
            event["messageId"] = message_id
        self.publish(event)

    # ------------------------------------------------------------------ public API

    def queue_text(self, conv: Conv, text: str, client_id: str, thread_root_id: str | None,
                   regular_text: bool = False) -> dict[str, Any]:
        existing = self.state.get_pending(client_id)
        if existing and existing["status"] != "failed":
            return {"clientId": client_id, "status": "queued"}  # same clientId twice: don't double-send
        chat = self.convs.newest_chat(conv)
        self.state.add_pending(client_id, conv.id, conv.chat_ids, "text", text, None, thread_root_id)
        self._spawn(self.dispatch_text(conv, chat, text, client_id, thread_root_id, regular_text=regular_text))
        return {"clientId": client_id, "status": "queued"}

    def start_conversation(self, handle: str, text: str, client_id: str) -> dict[str, Any]:
        """Text a handle. Reuses the person's conversation when there is one, else starts a new chat."""
        conv = self.convs.dm_for_handle(handle)
        if conv is not None:
            self.queue_text(conv, text, client_id, None)
            return {"conversationId": conv.id}
        conversation_id = self.convs.expected_dm_id(handle)
        existing = self.state.get_pending(client_id)
        if existing and existing["status"] != "failed":
            return {"conversationId": existing["conversation_id"]}
        self.state.add_pending(client_id, conversation_id, [], "text", text, None, None, handle=handle)
        self._spawn(self.dispatch_new(handle, text, client_id, conversation_id))
        return {"conversationId": conversation_id}

    def queue_file(self, conv: Conv, staged: Path, client_id: str, mime: str | None) -> dict[str, Any]:
        """Send a file already copied where Messages can read it (Sender.stage_file)."""
        chat = self.convs.newest_chat(conv)
        self.state.add_pending(client_id, conv.id, conv.chat_ids, "file", None, staged.name, None)
        self._spawn(self.dispatch_file(conv, chat, staged, client_id, mime))
        return {"clientId": client_id, "status": "queued"}

    def schedule(self, conv: Conv, text: str, send_at: float, client_id: str, thread_root_id: str | None) -> dict[str, Any]:
        row = self.state.add_scheduled(conv.id, text, send_at, client_id, thread_root_id)
        self.publish({"type": "scheduled", "scheduled": scheduled_json(row)})
        return {"clientId": client_id, "status": "scheduled", "scheduledId": row["id"]}

    def cancel_scheduled(self, sid: str) -> bool:
        row = self.state.get_scheduled(sid)
        if row is None:
            return False
        if row["status"] == "pending":
            row = self.state.set_scheduled_status(sid, "cancelled")
            self.publish({"type": "scheduled", "scheduled": scheduled_json(row)})
        return True

    # ------------------------------------------------------------------ dispatch

    async def dispatch_text(self, conv: Conv, chat: ChatInfo, text: str, client_id: str,
                            thread_root_id: str | None = None, regular_text: bool = False) -> SendResult:
        via_messages = bool(thread_root_id) and not regular_text and self._replies_via_messages()
        if via_messages:
            handled, why = await self._reply_in_messages(conv, chat, text, client_id, thread_root_id)  # type: ignore[arg-type]
            if handled is not None:
                return handled
            # Nothing was typed into Messages. Don't quietly send it as a regular text: say why, and
            # let the person retry or choose "send as a regular text" (regularText) themselves.
            error = f"Not sent as a reply: {why}" if why else "Not sent as a reply."
            self._fail(client_id, conv.id, error)
            self._reply_action(conv.id, thread_root_id, client_id, "failed", error)
            return SendResult(False, error)
        result = await asyncio.to_thread(
            self.sender.send_text, chat.guid, text,
            dm_handle=self._fallback_handle(conv, chat), service=chat.service,
        )
        if not result.ok:
            self._fail(client_id, conv.id, result.error or "Couldn't send the message.")
            return result
        self.state.update_pending(client_id, status="dispatched", dispatched_at=time.time())
        await self._after_dispatch(conv, chat, client_id, text=text)
        return result

    # ------------------------------------------------------------------ real thread replies (Messages app)

    def _replies_via_messages(self) -> bool:
        helpers = self.helpers
        return helpers is not None and helpers.replies_on and helpers.can_act

    def _reply_action(self, conversation_id: str, root_id: str | None, client_id: str, result: str, detail: str) -> None:
        self.publish({"type": "ui-action", "action": "reply", "conversationId": conversation_id, "messageId": root_id,
                      "clientId": client_id, "result": result, "detail": detail})

    async def _reply_in_messages(self, conv: Conv, chat: ChatInfo, text: str, client_id: str,
                                 root_id: str) -> tuple[SendResult | None, str]:
        """Type the reply into Messages' Reply field. (None, why) = nothing was typed."""
        if self.helpers.needs_reply_proof(conv):
            return None, REPLY_UNPROVEN
        job = await asyncio.to_thread(self.helpers.reply_job, conv, root_id, text, client_id)
        if job is None:  # the root isn't a message Messages can show (unknown, an event, unsent…)
            return None, "Messages can't reply to that message."
        outcome = await asyncio.to_thread(self.helpers.run_and_wait, job)
        if outcome.kind == "nothing":
            return None, outcome.detail
        timer = self._spawn(self._expire(client_id, conv.id))
        timer._bc_timer = True  # type: ignore[attr-defined]
        if outcome.kind in ("done", "regular"):
            self.state.update_pending_if(client_id, ("sending",), status="dispatched", dispatched_at=time.time())
            if outcome.kind == "done":
                self._reply_action(conv.id, root_id, client_id, "done", REPLY_DONE)
            else:
                self._reply_action(conv.id, root_id, client_id, "fallback", REPLY_REGULAR)
            if self.settings.send_mode == "dry-run" and self.settings.fake_db and outcome.message_guid:
                self._spawn(self._fake_receipts(outcome.message_guid, read=chat.service == "iMessage" and not chat.is_group))
            return SendResult(True), ""
        # Text may be in Messages but nothing confirmed it: never send a second copy. If the row
        # shows up later the watcher still matches it (and reports it sent).
        if self.state.update_pending_if(client_id, ("sending", "dispatched"), status="unconfirmed",
                                        error=REPLY_UNCONFIRMED, dispatched_at=time.time()):
            self.publish({"type": "send-status", "clientId": client_id, "conversationId": conv.id,
                          "status": "failed", "error": REPLY_UNCONFIRMED})
            self._reply_action(conv.id, root_id, client_id, "failed", REPLY_UNCONFIRMED)
            return SendResult(False, REPLY_UNCONFIRMED), ""
        self._reply_action(conv.id, root_id, client_id, "done", REPLY_DONE)  # the watcher matched it meanwhile
        return SendResult(True), ""

    async def dispatch_new(self, handle: str, text: str, client_id: str, conversation_id: str) -> SendResult:
        result = await asyncio.to_thread(self.sender.send_to_handle, handle, text)
        if not result.ok:
            self._fail(client_id, conversation_id, result.error or "Couldn't start the conversation.")
            return result
        self.state.update_pending(client_id, status="dispatched", dispatched_at=time.time())
        if self.settings.send_mode == "dry-run":
            if not self.settings.fake_db:
                self.state.update_pending(client_id, status="sent")
                self.publish({"type": "send-status", "clientId": client_id, "conversationId": conversation_id,
                              "status": "sent"})
                return result
            await asyncio.sleep(0.5)  # Messages creates the handle, the chat and the message
            try:
                _, guid = await asyncio.to_thread(
                    fakedb.start_chat_as_messages_app, self.settings.chat_db, handle, text=text,
                    my_handle=self._my_handle(),
                )
            except (fakedb.RefusedError, OSError) as exc:
                self._fail(client_id, conversation_id, str(exc))
                return result
            self._spawn(self._fake_receipts(guid, read=True))
        timer = self._spawn(self._expire(client_id, conversation_id))
        timer._bc_timer = True  # type: ignore[attr-defined]
        return result

    async def dispatch_file(self, conv: Conv, chat: ChatInfo, staged: Path, client_id: str, mime: str | None) -> SendResult:
        result = await asyncio.to_thread(
            self.sender.send_file, chat.guid, staged, dm_handle=self._fallback_handle(conv, chat)
        )
        if not result.ok:
            self._fail(client_id, conv.id, result.error or "Couldn't send the file.")
            return result
        self.state.update_pending(client_id, status="dispatched", dispatched_at=time.time())
        await self._after_dispatch(conv, chat, client_id, file_path=staged, mime=mime)
        return result

    async def _after_dispatch(
        self, conv: Conv, chat: ChatInfo, client_id: str, *, text: str | None = None,
        file_path: Path | None = None, mime: str | None = None,
    ) -> None:
        if self.settings.send_mode == "dry-run":
            if not self.settings.fake_db:
                # Nothing will ever show up in chat.db, so report success without an id.
                self.state.update_pending(client_id, status="sent")
                self.publish({"type": "send-status", "clientId": client_id, "conversationId": conv.id, "status": "sent"})
                return
            await asyncio.sleep(0.5)  # roughly how long Messages.app takes to write the row
            try:
                _, guid = await asyncio.to_thread(
                    fakedb.send_as_messages_app, self.settings.chat_db, chat.guid, text=text,
                    file_path=file_path, attachments_root=self.settings.attachments_root,
                    my_handle=self._my_handle(), mime_type=mime,
                )
            except (fakedb.RefusedError, LookupError, OSError) as exc:
                self._fail(client_id, conv.id, str(exc))
                return
            self._spawn(self._fake_receipts(guid, read=chat.service == "iMessage" and not chat.is_group))
        timer = self._spawn(self._expire(client_id, conv.id))
        timer._bc_timer = True  # type: ignore[attr-defined]

    async def _fake_receipts(self, guid: str, read: bool) -> None:
        await asyncio.sleep(1.5)
        await asyncio.to_thread(fakedb.mark_delivered, self.settings.chat_db, guid)
        if read:
            await asyncio.sleep(2.5)
            await asyncio.to_thread(fakedb.mark_delivered, self.settings.chat_db, guid, read=True)

    async def _expire(self, client_id: str, conversation_id: str) -> None:
        await asyncio.sleep(MATCH_WINDOW + 5)
        row = self.state.get_pending(client_id)
        if row and row["status"] == "dispatched":
            self._fail(client_id, conversation_id,
                       "Messages accepted the message but it never appeared in chat.db. Check Messages to see if it went out.")
        elif row and row["status"] == "unconfirmed":  # already reported as failed; stop matching it
            self.state.update_pending_if(client_id, ("unconfirmed",), status="failed")

    # ------------------------------------------------------------------ reconciliation (called by the watcher)

    def reconcile(self, rows: Sequence[Any]) -> list[dict[str, Any]]:
        """Match new outgoing chat.db rows with pending sends. Returns match records."""
        mine = [r for r in rows if r["is_from_me"]]
        if not mine:
            return []
        pending = [p for p in self.state.open_pending()]
        if not pending:
            return []
        attachments = self.attachments_for([r["ROWID"] for r in mine if r["cache_has_attachments"]])
        directory = self.convs.directory()
        matches: list[dict[str, Any]] = []
        used: set[str] = set()
        for row in mine:
            sent_at = apple_to_unix(row["date"]) or time.time()
            conv = directory.by_chat.get(row["chat_id"]) if row["chat_id"] is not None else None
            for p in pending:
                if p["client_id"] in used:
                    continue
                if not self._same_conversation(row, conv, p):
                    continue
                if not (p["created_at"] - 10 <= sent_at <= p["created_at"] + MATCH_WINDOW):
                    continue
                if p["kind"] == "text":
                    if _norm(message_text(row)) != _norm(p["text"]):
                        continue
                else:
                    names = {a["transfer_name"] for a in attachments.get(row["ROWID"], [])}
                    if p["file_name"] not in names:
                        continue
                used.add(p["client_id"])
                failed = bool(row["error"])
                self.state.update_pending(p["client_id"], status="failed" if failed else "sent", message_guid=row["guid"],
                                          error="Messages couldn't deliver this message." if failed else None)
                if p["thread_root_id"]:
                    self.state.set_thread_root(row["guid"], p["thread_root_id"], p["conversation_id"])
                matches.append({
                    "clientId": p["client_id"], "conversationId": p["conversation_id"], "messageId": row["guid"],
                    "failed": failed, "threadRootId": p["thread_root_id"],
                })
                break
        return matches

    @staticmethod
    def _same_conversation(row: Any, conv: Conv | None, pending: Any) -> bool:
        if row["chat_id"] in json.loads(pending["chat_ids"]):
            return True
        if conv is None:
            return False
        if pending["conversation_id"] in (conv.id, *conv.aliases):
            return True
        # A brand-new chat for a handle we just started a conversation with.
        return bool(pending["handle"]) and conv.kind == "dm" and any(
            pending["handle"] in person.handles for person in conv.persons)

    # ------------------------------------------------------------------ scheduled sends

    async def run_due(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        for item in self.state.due_scheduled(now):
            if not self.state.claim_scheduled(item["id"]):
                continue
            if now - item["send_at"] > OVERDUE_GRACE:
                row = self.state.set_scheduled_status(
                    item["id"], "failed", "Backchannel wasn't running at the scheduled time, so this wasn't sent."
                )
                self.publish({"type": "scheduled", "scheduled": scheduled_json(row)})
                continue
            try:
                conv = self.convs.get(item["conversation_id"])
            except NotFound as exc:
                row = self.state.set_scheduled_status(item["id"], "failed", str(exc))
                self.publish({"type": "scheduled", "scheduled": scheduled_json(row)})
                continue
            client_id = item["client_id"] or f"scheduled-{item['id']}"
            chat = self.convs.newest_chat(conv)
            self.state.add_pending(client_id, conv.id, conv.chat_ids, "text", item["text"], None, item["thread_root_id"])
            result = await self.dispatch_text(conv, chat, item["text"], client_id, item["thread_root_id"])
            row = self.state.set_scheduled_status(item["id"], "sent" if result.ok else "failed", result.error)
            self.publish({"type": "scheduled", "scheduled": scheduled_json(row)})

    async def scheduler_loop(self, interval: float = 5.0) -> None:
        while True:
            try:
                await self.run_due()
            except Exception:
                log.exception("Scheduled-send loop error")
            await asyncio.sleep(interval)
