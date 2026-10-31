"""Mark as unread: kept in state.db (chat.db is Messages' own), cleared by reading, and synced to
Messages' own "Mark as Unread" when read sync is on."""

from __future__ import annotations

import threading
import time
from urllib.parse import quote

from fake_messages import FakeMessages
from server import fakedb
from server.messages_ui import MARK_READ_SCRIPT, MARK_UNREAD_SCRIPT, TIMEOUT, Outcome
from server.state import State
from ts_shapes import validate

JORDAN = "dm:+14155550134"
MARCUS = "dm:+14155550161"
FAMILY = "g:chat100000000000000001"
MARCUS_CHAT = "SMS;-;+14155550161"


def path(conv_id: str, action: str) -> str:
    return f"/api/conversations/{quote(conv_id, safe='')}/{action}"


def unread(rw, conv_id: str, message_id: str | None = None, status: int = 200):
    return rw.post(path(conv_id, "unread"), {"messageId": message_id} if message_id else {}, status=status)


def read(rw, conv_id: str):
    return rw.post(path(conv_id, "read"))


def incoming_ids(rw, conv_id: str) -> list[str]:
    """Incoming messages with something in them, oldest first (what "Mark as unread" counts)."""
    return [m["id"] for m in rw.all_messages(conv_id, limit=50)
            if not m["fromMe"] and not m["event"] and not m["unsent"] and (m["text"] or m["attachments"])]


def ui_actions(rw) -> list[dict]:
    return [e for e in rw.services.hub.history if e["type"] == "ui-action"]


def harness(make_rw, fake: FakeMessages):
    rw = make_rw(messages_ui_runner=fake)
    rw.services.messages_ui.check_after = 0.05
    assert rw.client.patch("/api/settings", json={"markReadInMessages": True}).status_code == 200
    assert rw.services.messages_ui.wait_idle(5)  # the permission probe
    return rw


# --------------------------------------------------------------------------- counting


def test_mark_unread_counts_from_the_newest_incoming_message(rw):
    before = rw.conversation(MARCUS)
    assert before["unreadCount"] == 0 and before["markedUnread"] is False
    summary = unread(rw, MARCUS)
    assert summary["unreadCount"] == 1 and summary["markedUnread"] is True
    assert not validate(summary, "Conversation")
    listed = rw.conversations()[MARCUS]
    assert listed["unreadCount"] == 1 and listed["markedUnread"] is True
    assert rw.conversation(MARCUS)["priority"] == before["priority"]  # "Needs you" goes by chat.db alone
    event = rw.services.hub.history[-1]
    assert event["type"] == "conversation" and event["conversation"]["markedUnread"] is True


def test_mark_unread_from_a_message_counts_incoming_messages_from_there(rw):
    ids = incoming_ids(rw, MARCUS)  # Friday, the tile sample, the demo question
    assert len(ids) == 3 and ids[-1] == rw.guid("mk_demo")
    assert unread(rw, MARCUS, ids[0])["unreadCount"] == 3
    assert unread(rw, MARCUS, ids[1])["unreadCount"] == 2  # marking again moves it
    mine = next(m["id"] for m in rw.all_messages(MARCUS, limit=50) if m["fromMe"])
    assert unread(rw, MARCUS, mine)["unreadCount"] == 3  # from one of yours: the incoming ones after it


def test_marked_unread_counts_never_go_below_one(rw):
    newest_mine = [m["id"] for m in rw.all_messages(MARCUS, limit=50) if m["fromMe"]][-1]
    conn = rw.db()
    try:  # move the demo question before Harry's last text, so nothing incoming follows it
        conn.execute("UPDATE message SET date = 1, date_read = 1 WHERE guid = ?", (rw.guid("mk_demo"),))
        conn.execute("UPDATE chat_message_join SET message_date = 1 WHERE message_id = "
                     "(SELECT ROWID FROM message WHERE guid = ?)", (rw.guid("mk_demo"),))
        conn.commit()
    finally:
        conn.close()
    assert unread(rw, MARCUS, newest_mine)["unreadCount"] == 1


def test_no_double_counting_with_chat_db_unread(rw):
    assert rw.conversation(JORDAN)["unreadCount"] == 2  # the hike + carpool texts, unread in chat.db
    assert unread(rw, JORDAN)["unreadCount"] == 2  # from the newest (carpool) + hike, unread before it
    assert unread(rw, JORDAN, rw.guid("j_place"))["unreadCount"] == 3  # the photo, hike, carpool
    assert unread(rw, JORDAN, rw.guid("j_carpool"))["unreadCount"] == 2


def test_reading_clears_it_and_new_messages_still_count(rw):
    unread(rw, MARCUS)
    summary = read(rw, MARCUS)
    assert summary["unreadCount"] == 0 and summary["markedUnread"] is False
    assert rw.services.state.get_unread_mark(MARCUS) is None
    assert unread(rw, MARCUS)["unreadCount"] == 1
    conn = rw.db()
    try:
        chat = fakedb.chat_info(conn, MARCUS_CHAT)
        hid = fakedb.ensure_handle(conn, "+14155550161", chat["service_name"])
        fakedb.insert_message(conn, chat_rowid=chat["ROWID"], text="also, 8am instead?", is_from_me=False,
                              handle_rowid=hid, service=chat["service_name"], date=time.time(), is_read=1)
        conn.commit()
    finally:
        conn.close()
    assert rw.conversation(MARCUS)["unreadCount"] == 2  # read on the phone or not, it's after the mark


def test_wrong_message_or_conversation_is_404(rw):
    unread(rw, JORDAN, rw.guid("mk_demo"), status=404)  # Marcus's message
    unread(rw, JORDAN, "nope", status=404)
    rw.post(path("dm:+19995550000", "unread"), {}, status=404)
    assert rw.conversation(JORDAN)["markedUnread"] is False


def test_unread_marks_follow_aliases(tmp_path):
    state = State(tmp_path / "state.db")
    try:
        state.set_unread_mark("dm:+14155550134", 500)
        assert state.get_unread_mark("c:jordan", ("dm:+14155550134",)) == 500  # the old id still counts
        state.set_unread_mark("c:jordan", 700, ("dm:+14155550134",))  # set under the new id: the old one goes
        assert state.get_unread_mark("dm:+14155550134") is None and state.get_unread_mark("c:jordan") == 700
        version = state.version
        assert state.clear_unread_mark("c:jordan", ("dm:+14155550134",)) is True
        assert state.version > version and state.get_unread_mark("c:jordan") is None
        assert state.clear_unread_mark("c:jordan") is False
    finally:
        state.close()


# --------------------------------------------------------------------------- Messages' own "Mark as Unread"


def test_mark_unread_in_messages(make_rw, caplog):
    fake = FakeMessages()
    rw = harness(make_rw, fake)
    with caplog.at_level("INFO", logger="backchannel.messages_ui"):
        unread(rw, MARCUS)
        assert rw.services.messages_ui.wait_idle(5)
    assert fake.names() == ["mark-unread"]
    _, script, argv, timeout = fake.calls[0]
    assert script is MARK_UNREAD_SCRIPT and timeout == TIMEOUT
    assert argv[0] == "mark-unread" and argv[1] == "L:Marcus Webb"
    health = rw.get("/api/health")["messagesUi"]
    assert health["lastResult"] == "Marked Marcus Webb unread in Messages." and health["permission"] == "granted"
    assert "Marcus" not in caplog.text and "Mark unread in Messages: ok-action" in caplog.text
    assert ui_actions(rw) == []  # successes stay quiet


def test_mark_unread_failures_are_reported(make_rw):
    fake = FakeMessages(mark_unread=Outcome(0, "row-not-found", ""))
    rw = harness(make_rw, fake)
    unread(rw, MARCUS)
    assert rw.services.messages_ui.wait_idle(5)
    sentence = "Couldn't find Marcus Webb in Messages' sidebar, so it's unread only here."
    assert ui_actions(rw) == [{"type": "ui-action", "action": "mark-unread", "conversationId": MARCUS,
                               "messageId": None, "clientId": None, "result": "failed", "detail": sentence}]
    assert not validate(ui_actions(rw)[0], "ServerEvent")
    assert rw.conversation(MARCUS)["markedUnread"] is True  # still unread here
    fake.answers["mark-unread"] = Outcome(0, "not-running", "")
    unread(rw, MARCUS)
    assert rw.services.messages_ui.wait_idle(5) and len(ui_actions(rw)) == 1  # "Messages isn't open": quiet


def test_reading_a_marked_conversation_marks_it_read_in_messages_too(make_rw):
    fake = FakeMessages()
    rw = harness(make_rw, fake)
    read(rw, MARCUS)  # nothing unread in chat.db, never marked: nothing to do
    assert rw.services.messages_ui.wait_idle(5) and fake.calls == []
    unread(rw, MARCUS)
    assert rw.services.messages_ui.wait_idle(5)
    read(rw, MARCUS)  # Messages' unread dot may not show in chat.db, so it asks anyway
    assert rw.services.messages_ui.wait_idle(5)
    assert fake.names() == ["mark-unread", "mark-read"]
    assert fake.calls[1][1] is MARK_READ_SCRIPT
    read(rw, MARCUS)  # not marked any more: back to "only when chat.db has unread"
    assert rw.services.messages_ui.wait_idle(5) and fake.names() == ["mark-unread", "mark-read"]


def test_the_newest_of_read_and_unread_wins_in_the_queue(make_rw):
    fake = FakeMessages()
    fake.gate = threading.Event()
    rw = harness(make_rw, fake)
    unread(rw, FAMILY)  # runs, held at the gate
    assert fake.started.wait(5)
    read(rw, JORDAN)  # waits (Jordan has unread texts in chat.db)
    unread(rw, JORDAN)  # replaces it
    read(rw, MARCUS)  # nothing to do for Marcus: never queued
    unread(rw, MARCUS)  # waits
    unread(rw, MARCUS)  # the same wish again: dropped
    read(rw, MARCUS)  # replaces it (and has to run: it was marked unread)
    fake.gate.set()
    assert rw.services.messages_ui.wait_idle(10)
    assert [(name, argv[1]) for name, _, argv, _ in fake.calls] == [
        ("mark-unread", "L:Liu Family 🏡"), ("mark-unread", "L:Jordan Park"), ("mark-read", "L:Marcus Webb")]


def test_dry_run_marks_unread_without_messages(rw):
    rw.client.patch("/api/settings", json={"markReadInMessages": True})
    assert unread(rw, MARCUS)["markedUnread"] is True
    assert rw.services.messages_ui.wait_idle(5)
    assert rw.get("/api/health")["messagesUi"]["lastAt"] is None  # nothing was asked of Messages
