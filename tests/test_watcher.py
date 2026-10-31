"""Live updates from chat.db changes, and the SSE endpoint on a real server."""

from __future__ import annotations

import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from conftest import settings_for
from server import fakedb
from server.app import create_app
from server.timeutil import unix_to_apple_ns
from server.typedstream import encode_attributed_body

JORDAN = "dm:+14155550134"


def insert(harness, chat_guid, handle, text, **kw):
    conn = harness.db()
    chat = fakedb.chat_info(conn, chat_guid)
    hid = fakedb.ensure_handle(conn, handle, chat["service_name"])
    rowid, guid = fakedb.insert_message(conn, chat_rowid=chat["ROWID"], text=text, is_from_me=False, handle_rowid=hid,
                                        service=chat["service_name"], date=time.time(), **kw)
    conn.close()
    return guid


def test_new_message_emits_message_and_conversation(rw):
    guid = insert(rw, "iMessage;-;+14155550134", "+14155550134", "trail opens at 8")
    events = rw.events_until(lambda e: any(x["type"] == "conversation" for x in e))
    message = next(e["message"] for e in events if e["type"] == "message")
    assert message["id"] == guid and message["conversationId"] == JORDAN and message["text"] == "trail opens at 8"
    convo = next(e["conversation"] for e in events if e["type"] == "conversation")
    assert convo["id"] == JORDAN and convo["unreadCount"] == 3 and convo["lastMessage"]["id"] == guid
    assert rw.services.watcher.check() == []  # nothing new, nothing emitted


def test_tapback_updates_the_target_not_the_timeline(rw):
    target = rw.guid("j_drill")
    insert(rw, "iMessage;-;+14155550134", "+14155550134", "Loved “you still have my drill?”",
           associated_message_guid=f"p:0/{target}", associated_message_type=2000)
    events = rw.events_until(lambda e: any(x["type"] == "message-updated" for x in e))
    assert not [e for e in events if e["type"] == "message"]
    updated = next(e["message"] for e in events if e["type"] == "message-updated")
    assert updated["id"] == target and {"kind": "love", "emoji": "❤️", "from": ["+14155550134"]} in updated["reactions"]
    # ...and removing it
    insert(rw, "iMessage;-;+14155550134", "+14155550134", "Removed a heart from “you still have my drill?”",
           associated_message_guid=f"p:0/{target}", associated_message_type=3000)
    events = rw.events_until(lambda e: any(x["type"] == "message-updated" for x in e))
    updated = next(e["message"] for e in events if e["type"] == "message-updated")
    assert updated["reactions"] == []


def test_thread_reply_updates_the_root(rw):
    root = rw.guid("f_root")
    guid = insert(rw, "iMessage;+;chat100000000000000001", "+15105550122", "I'll make the gravy",
                  thread_originator_guid=root)
    events = rw.events_until(lambda e: any(x["type"] == "message-updated" for x in e))
    message = next(e["message"] for e in events if e["type"] == "message")
    assert message["id"] == guid and message["threadRootId"] == root
    updated = next(e["message"] for e in events if e["type"] == "message-updated")
    assert updated["id"] == root and updated["thread"]["replyCount"] == 10


def test_edits_unsends_and_receipts(rw):
    conn = rw.db()
    now = unix_to_apple_ns(time.time())
    conn.execute("UPDATE message SET attributedBody = ?, date_edited = ? WHERE guid = ?",
                 (encode_attributed_body("can carpool from mine, leaving 8:30"), now, rw.guid("j_carpool")))
    conn.close()
    events = rw.events_until(lambda e: any(x["type"] == "message-updated" for x in e))
    edited = next(e["message"] for e in events if e["type"] == "message-updated")
    assert edited["text"] == "can carpool from mine, leaving 8:30" and edited["editedAt"]

    conn = rw.db()
    conn.execute("UPDATE message SET date_retracted = ?, attributedBody = NULL WHERE guid = ?", (now, rw.guid("j_carpool")))
    conn.close()
    events = rw.events_until(lambda e: any(x["type"] == "message-updated" for x in e))
    assert next(e["message"] for e in events if e["type"] == "message-updated")["unsent"]

    conn = rw.db()
    conn.execute("UPDATE message SET error = 22 WHERE guid = ?", (rw.guid("p_ramen"),))
    conn.close()
    events = rw.events_until(lambda e: any(x["type"] == "message-updated" for x in e))
    assert next(e["message"] for e in events if e["type"] == "message-updated")["status"] == "failed"


def test_read_on_phone_updates_unread_counts(rw):
    conn = rw.db()
    conn.execute("UPDATE message SET is_read = 1, date_read = ? WHERE guid IN (?, ?)",
                 (unix_to_apple_ns(time.time()), rw.guid("j_hike"), rw.guid("j_carpool")))
    conn.close()
    events = rw.events_until(lambda e: any(x["type"] == "conversation" for x in e))
    convo = next(e["conversation"] for e in events if e["type"] == "conversation" and e["conversation"]["id"] == JORDAN)
    assert convo["unreadCount"] == 0 and convo["awaitingReply"]  # read isn't answered
    assert not [e for e in events if e["type"] == "message-updated"]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_server(fixture_src, tmp_path):
    import shutil

    fx = tmp_path / "fx"
    shutil.copytree(fixture_src, fx)
    port = free_port()
    settings = settings_for(fx, tmp_path / "state", BACKCHANNEL_PORT=str(port))
    app = create_app(settings)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", fx
    server.should_exit = True
    thread.join(timeout=10)


def test_sse_end_to_end(live_server):
    base, fx = live_server
    with httpx.Client(base_url=base, timeout=10) as client:
        assert client.get("/api/events").status_code == 401
        assert client.get("/api/session").status_code == 200
        received: list[dict] = []

        def listen():
            with client.stream("GET", "/api/events") as response:
                assert response.headers["content-type"].startswith("text/event-stream")
                for line in response.iter_lines():
                    if line.startswith("data: "):
                        received.append(json.loads(line[6:]))
                        if any(e["type"] == "message" for e in received):
                            return

        listener = threading.Thread(target=listen, daemon=True)
        listener.start()
        deadline = time.time() + 5
        while not received and time.time() < deadline:
            time.sleep(0.05)
        assert received and received[0]["type"] == "hello"
        conn = fakedb.connect(fx / "chat.db")
        chat = fakedb.chat_info(conn, "iMessage;+;chat100000000000000003")
        hid = fakedb.ensure_handle(conn, "+16505550147", "iMessage")
        fakedb.insert_message(conn, chat_rowid=chat["ROWID"], text="live from the watcher", is_from_me=False,
                              handle_rowid=hid, date=time.time())
        conn.close()
        listener.join(timeout=8)
        messages = [e["message"] for e in received if e["type"] == "message"]
        assert messages and messages[0]["text"] == "live from the watcher"
