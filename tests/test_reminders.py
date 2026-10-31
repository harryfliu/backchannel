"""Saved-for-later reminders: firing once, "if no reply" resolution, partial updates."""

from __future__ import annotations

import sqlite3
import time

from server import fakedb
from server.state import State
from ts_shapes import validate

JORDAN = "dm:+14155550134"
MARCUS = "dm:+14155550161"


def save(harness, label, conv, **body):
    response = harness.client.put(f"/api/later/{harness.guid(label)}", json={"conversationId": conv, **body})
    assert response.status_code == 200, response.text
    return response.json()


def incoming(harness, chat_guid, handle, text, when=None):
    conn = harness.db()
    chat = fakedb.chat_info(conn, chat_guid)
    hid = fakedb.ensure_handle(conn, handle, chat["service_name"])
    fakedb.insert_message(conn, chat_rowid=chat["ROWID"], text=text, is_from_me=False, handle_rowid=hid,
                          service=chat["service_name"], date=when or time.time())
    conn.close()


def later_events(events):
    return [e for e in events if e["type"] == "later"]


def test_due_reminder_fires_once(rw):
    item = save(rw, "mk_demo", MARCUS, remindAt="2026-01-01T09:00:00Z")
    assert item["firedAt"] is None and item["ifNoReply"] is False and item["resolvedByReply"] is False
    assert not validate(item, "LaterItem")
    events = rw.services.reminders.check()
    assert len(events) == 1 and events[0]["type"] == "later"
    fired = events[0]["item"]
    assert fired["messageId"] == rw.guid("mk_demo") and fired["firedAt"] and not fired["resolvedByReply"]
    assert fired["message"]["text"] == "Does Thursday at 9am work to start demo?"
    assert not validate(events[0], "ServerEvent")
    assert rw.services.hub.history[-1] == events[0]
    assert rw.services.reminders.check() == []  # only once
    assert rw.get("/api/later")["items"][0]["firedAt"] == fired["firedAt"]


def test_future_reminder_waits_for_its_time(rw):
    remind_at = time.time() + 3600
    save(rw, "mk_demo", MARCUS, remindAt=time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(remind_at)))
    assert rw.services.reminders.check(now=remind_at - 60) == []
    assert len(rw.services.reminders.check(now=remind_at + 1)) == 1


def test_done_items_never_fire(rw):
    save(rw, "mk_demo", MARCUS, remindAt="2026-01-01T09:00:00Z", done=True)
    assert rw.services.reminders.check() == []


def test_new_remind_time_rearms(rw):
    save(rw, "mk_demo", MARCUS, remindAt="2026-01-01T09:00:00Z")
    rw.services.reminders.check()
    again = save(rw, "mk_demo", MARCUS, remindAt="2026-01-02T09:00:00Z")
    assert again["firedAt"] is None
    assert len(rw.services.reminders.check()) == 1


def test_if_no_reply_on_my_message_resolves_when_they_write(rw):
    # Harry's own message: only replies after the item was saved count.
    item = save(rw, "j_drill", JORDAN, remindAt="2031-01-01T09:00:00Z", ifNoReply=True)
    assert item["ifNoReply"] and not item["resolvedByReply"]
    assert rw.services.reminders.check() == []  # Jordan's older messages don't count
    incoming(rw, "iMessage;-;+14155550134", "+14155550134", "oh yeah I'll bring it sunday")
    events = rw.events_until(lambda e: later_events(e))  # the watcher runs the reply check
    resolved = later_events(events)[0]["item"]
    assert resolved["resolvedByReply"] and resolved["firedAt"] is None
    assert rw.services.reminders.check(now=time.time() + 10**9) == []  # never fires afterwards


def test_if_no_reply_on_their_message(rw):
    save(rw, "mk_demo", MARCUS, remindAt="2031-01-01T09:00:00Z", ifNoReply=True)
    assert rw.services.reminders.check() == []  # nothing after Marcus's message yet
    incoming(rw, "SMS;-;+14155550161", "+14155550161", "Or Friday works too")
    resolved = later_events(rw.events_until(lambda e: later_events(e)))[0]["item"]
    assert resolved["resolvedByReply"]


def test_if_no_reply_already_answered(rw):
    # Jordan followed "are you down to hike Saturday?" with "can carpool from mine".
    save(rw, "j_hike", JORDAN, remindAt="2031-01-01T09:00:00Z", ifNoReply=True)
    events = rw.services.reminders.check()
    assert events and events[0]["item"]["resolvedByReply"]


def test_if_no_reply_fires_when_nobody_writes(rw):
    save(rw, "mk_demo", MARCUS, remindAt="2026-01-01T09:00:00Z", ifNoReply=True)
    events = rw.services.reminders.check()
    assert events[0]["item"]["firedAt"] and not events[0]["item"]["resolvedByReply"]


def test_tapbacks_and_my_own_messages_dont_resolve(rw):
    save(rw, "mk_demo", MARCUS, remindAt="2031-01-01T09:00:00Z", ifNoReply=True)
    conn = rw.db()
    chat = fakedb.chat_info(conn, "SMS;-;+14155550161")
    hid = fakedb.ensure_handle(conn, "+14155550161", "SMS")
    fakedb.insert_message(conn, chat_rowid=chat["ROWID"], text="Liked “looks great”", is_from_me=False,
                          handle_rowid=hid, service="SMS", associated_message_type=2001,
                          associated_message_guid=f"p:0/{rw.guid('mk_demo')}")
    fakedb.insert_message(conn, chat_rowid=chat["ROWID"], text="thursday works!", is_from_me=True,
                          handle_rowid=hid, service="SMS")
    conn.close()
    assert rw.services.reminders.check() == []


def test_set_done_without_conversation_id(rw):
    save(rw, "mk_demo", MARCUS, remindAt="2026-01-01T09:00:00Z")
    response = rw.client.put(f"/api/later/{rw.guid('mk_demo')}", json={"done": True})
    assert response.status_code == 200
    item = response.json()
    assert item["done"] and item["remindAt"] is not None  # untouched fields are kept
    assert rw.services.reminders.check() == []
    undone = rw.client.put(f"/api/later/{rw.guid('mk_demo')}", json={"done": False}).json()
    assert not undone["done"]
    assert rw.client.put(f"/api/later/{rw.guid('j_hike')}", json={"done": True}).status_code == 400  # not saved yet


def test_old_state_db_is_migrated(tmp_path):
    path = tmp_path / "state.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE later (message_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, saved_at REAL NOT NULL, "
        "remind_at REAL, done INTEGER NOT NULL DEFAULT 0);"
        "INSERT INTO later VALUES ('G1', 'dm:+1', 1.0, 2.0, 0);"
    )
    conn.commit()
    conn.close()
    state = State(path)
    row = state.get_later("G1")
    assert (row["if_no_reply"], row["resolved_by_reply"], row["fired_at"]) == (0, 0, None)
    assert state.fire_later("G1", 3.0) and not state.fire_later("G1", 4.0)
    state.close()


def test_later_list_shape(rw):
    save(rw, "mk_demo", MARCUS, remindAt="2031-01-01T09:00:00Z", ifNoReply=True)
    items = rw.get("/api/later")["items"]
    assert items and all(not validate(item, "LaterItem") for item in items)
