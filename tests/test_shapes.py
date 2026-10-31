"""Every route's real response validates against web/src/api/types.ts."""

from __future__ import annotations

from urllib.parse import quote

import pytest

from test_ai import CATCHUP_REPLY, StubClient
from ts_shapes import validate

# Fields the server adds beyond types.ts (documented in docs/server.md).
EXTRAS = {"stickerUrl"}


def check(value, type_name):
    errors = validate(value, type_name, allow_extra=EXTRAS)
    assert not errors, "\n".join(errors[:20])


def q(value: str) -> str:
    return quote(value, safe="")


def test_conversations_and_messages(ro):
    convs = ro.get("/api/conversations")["conversations"]
    assert convs
    for conv in convs:
        check(conv, "Conversation")
        check(ro.conversation(conv["id"]), "Conversation")
        page = ro.get(f"/api/conversations/{q(conv['id'])}/messages", params={"limit": 200})
        check(page, "MessagePage")
        media = ro.get(f"/api/conversations/{q(conv['id'])}/media")
        check(media, "MediaPage")


def test_thread_activity_threads_search(ro):
    check(ro.get(f"/api/conversations/{q('g:chat100000000000000001')}/threads/{ro.guid('f_root')}"), "ThreadResponse")
    for item in ro.get("/api/activity")["items"]:
        check(item, "ActivityItem")
    for message in ro.get("/api/threads")["items"]:
        check(message, "Message")
    results = ro.get("/api/search", params={"q": "in"})["results"]
    assert results
    for result in results:
        check(result, "SearchResult")


def test_me_health(ro):
    check(ro.get("/api/me"), "Me")
    check(ro.get("/api/health"), "Health")


def test_later_scheduled_and_events(rw):
    later = rw.client.put(f"/api/later/{rw.guid('mk_demo')}",
                          json={"conversationId": "dm:+14155550161", "remindAt": "2026-11-02T09:00:00-08:00"})
    check(later.json(), "LaterItem")
    for item in rw.get("/api/later")["items"]:
        check(item, "LaterItem")
    sent = rw.post(f"/api/conversations/{q('dm:+14155550134')}/messages", {"text": "shape check", "clientId": "sh-1"})
    check(sent, "SendResponse")
    scheduled = rw.post(f"/api/conversations/{q('dm:+14155550134')}/messages",
                        {"text": "later", "clientId": "sh-2", "sendAt": "2031-01-01T00:00:00Z"})
    check(scheduled, "SendResponse")
    for item in rw.get("/api/scheduled")["items"]:
        check(item, "ScheduledMessage")
    root = rw.guid("soc_root")
    rw.post(f"/api/conversations/{q('g:chat100000000000000002')}/messages",
            {"text": "thread shape", "clientId": "sh-3", "threadRootId": root})
    rw.events_until(lambda e: sum(1 for x in e if x["type"] == "send-status") >= 2)
    rw.events_until(lambda e: any(x["type"] == "message-updated" and x["message"]["status"] == "read" for x in e))
    events = rw.services.hub.history
    assert {e["type"] for e in events} >= {"message", "message-updated", "conversation", "send-status", "scheduled"}
    for event in events:
        check(event, "ServerEvent")
    check({"type": "hello", "serverTime": "2026-10-31T09:14:00-07:00"}, "ServerEvent")
    check(rw.post(f"/api/conversations/{q('dm:+14155550134')}/read"), "Conversation")
    patched = rw.client.patch(f"/api/conversations/{q('dm:+14155550134')}/prefs", json={"muted": True, "snoozedUntil": None})
    check(patched.json(), "Conversation")


@pytest.fixture
def stubbed(make_rw):
    stub = StubClient([CATCHUP_REPLY])
    return make_rw(ai_client_factory=lambda: stub), stub


def test_ai_shapes(stubbed):
    harness, stub = stubbed
    check(harness.get("/api/ai/catchup"), "CatchUp")
    stub.replies = [{"drafts": ["one", "two", "three"]}]
    drafts = harness.post("/api/ai/draft", {"conversationId": "dm:+14155550134"})
    assert isinstance(drafts["drafts"], list) and all(isinstance(d, str) for d in drafts["drafts"])
