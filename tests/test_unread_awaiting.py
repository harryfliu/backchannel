"""Unread counts, "waiting on you", automated senders."""

from __future__ import annotations

import time
from urllib.parse import quote

import pytest

from server import fakedb
from server.heuristics import asks_something, opens_with_closer

JORDAN = "dm:+14155550134"
FAMILY = "g:chat100000000000000001"
SOCCER = "g:chat100000000000000002"
DINNER = "g:chat100000000000000003"


def test_unread_and_mention_counts(ro):
    convs = ro.conversations()
    counts = {cid: (c["unreadCount"], c["mentionCount"]) for cid, c in convs.items()}
    assert counts[JORDAN] == (2, 0)
    assert counts[FAMILY] == (1, 1)
    assert counts[DINNER] == (1, 1)
    assert counts[SOCCER] == (1, 0)
    assert counts["dm:24273"] == (1, 0)
    assert counts["dm:69525"] == (1, 0)
    assert counts["dm:+14155550161"] == (0, 0)  # Marcus: read on the phone, not answered
    assert counts["dm:+15105550123"] == (1, 0)  # Dad: "Can you call me in 20 min?"
    assert counts["g:chat100000000000000005"] == (7, 1)  # the busy climbing group
    assert counts["dm:+14155550163"] == (3, 0)  # Nina, three in a row


def test_mark_read_uses_a_local_mark(rw):
    assert rw.conversation(JORDAN)["unreadCount"] == 2
    summary = rw.post(f"/api/conversations/{quote(JORDAN, safe='')}/read")
    assert summary["unreadCount"] == 0
    assert rw.conversation(JORDAN)["unreadCount"] == 0
    # chat.db still says is_read = 0: we never write to it.
    row = rw.db().execute("SELECT is_read FROM message WHERE guid = ?", (rw.guid("j_hike"),)).fetchone()
    assert row[0] == 0
    # A new incoming message after the mark is unread again.
    conn = rw.db()
    chat = fakedb.chat_info(conn, "iMessage;-;+14155550134")
    handle = fakedb.ensure_handle(conn, "+14155550134", "iMessage")
    fakedb.insert_message(conn, chat_rowid=chat["ROWID"], text="also is 8am ok?", is_from_me=False,
                          handle_rowid=handle, date=time.time() + 1)
    conn.close()
    assert rw.conversation(JORDAN)["unreadCount"] == 1


EXPECTED_AWAITING = {
    JORDAN: "j_hike",               # "are you down to hike Saturday?" then "can carpool from mine"
    "dm:+14155550161": "mk_demo",   # Marcus, SMS, read but unanswered
    "dm:+15105550123": "d_call",    # Dad: "call me when you get a chance" (no "?")
    FAMILY: "f_chairs",             # group: mentions Harry
    DINNER: "d_sake",               # group: mentions Harry
    SOCCER: "soc_ball_reply",       # group: inline reply to Harry's message (Nate's plain question doesn't count)
    "dm:+14155550199": "vet_closer",  # vet: opens with "Perfect," but still asks "Can you bring his records"
    "g:chat100000000000000005": "cc_reply",  # climbing: an inline reply to Harry, before the @mention
    "dm:+14155550157": "bp_confirm",  # Bay Plumbing: "Can you confirm today to hold the slot?"
    "dm:+14155550163": "n_2",       # Nina: "are you around?"
}


def test_awaiting_reply(ro):
    convs = ro.conversations()
    awaiting = {cid for cid, c in convs.items() if c["awaitingReply"]}
    assert awaiting == set(EXPECTED_AWAITING)
    for cid, label in EXPECTED_AWAITING.items():
        assert convs[cid]["awaitingSince"] == ro.message(label)["sentAt"], cid
    for cid, c in convs.items():
        if not c["awaitingReply"]:
            assert c["awaitingSince"] is None


def test_not_awaiting_cases(ro):
    convs = ro.conversations()
    assert not convs["dm:+14155550178"]["awaitingReply"]  # Priya: Harry sent the last real message
    assert not convs["dm:+15105550122"]["awaitingReply"]  # Mom: "Ok 👍" asks nothing
    assert convs["dm:+14155550199"]["awaitingReply"]  # vet: "Perfect, … Can you bring his records" still asks
    assert not convs["g:chat100000000000000004"]["awaitingReply"]  # group chatter, no mention
    assert not convs["dm:24273"]["awaitingReply"]  # automated


@pytest.mark.parametrize("text, asks", [
    ("are you down to hike Saturday?", True),
    ("call me when you get a chance", True),
    ("lmk", True),
    ("can carpool from mine", False),
    ("Ok 👍", False),
    ("check out https://example.com/?q=1", False),
    ("11 pm!??! Dude what the heck!", False),  # surprise, not a question
    ("seriously?!", False),
    ("you coming tonight?!", True),  # mixed with "!", but it asks
    ("really?", True),
])
def test_asks_something(text, asks):
    assert asks_something(text) is asks


@pytest.mark.parametrize("text, closer", [
    ("Perfect, you're on the schedule for Tue Nov 3 at 10:00 AM. Can you bring his records", True),
    ("thanks! when are you free?", True),
    ("sounds good", True),
    ("👍", True),
    ("haha nice", True),
    ("cooler weather this weekend", False),
    ("are you down to hike Saturday?", False),
])
def test_opens_with_closer(text, closer):
    assert opens_with_closer(text) is closer


def test_closer_with_question_mark_still_awaits(rw):
    conn = rw.db()
    chat = fakedb.chat_info(conn, "SMS;-;+14155550199")
    handle = fakedb.ensure_handle(conn, "+14155550199", "SMS")
    fakedb.insert_message(conn, chat_rowid=chat["ROWID"], text="Thanks! Could you also confirm his weight?",
                          is_from_me=False, handle_rowid=handle, service="SMS", date=time.time())
    conn.close()
    vet = rw.conversation("dm:+14155550199")
    assert vet["awaitingReply"]


def test_automated(ro):
    convs = ro.conversations()
    automated = {cid for cid, c in convs.items() if c["isAutomated"]}
    assert automated == {"dm:24273", "dm:69525", "dm:36320"}


def test_non_contact_automated_only_if_never_written_to(rw):
    conn = rw.db()
    rowid = fakedb._insert(conn, "chat", {"guid": "SMS;-;+14155550188", "style": 45, "chat_identifier": "+14155550188",
                                          "service_name": "SMS"})
    bot = fakedb.ensure_handle(conn, "+14155550188", "SMS")
    conn.execute("INSERT INTO chat_handle_join (chat_id, handle_id) VALUES (?, ?)", (rowid, bot))
    fakedb.insert_message(conn, chat_rowid=rowid, text="Your Lyft driver is arriving. Reply STOP to opt out.",
                          is_from_me=False, handle_rowid=bot, service="SMS", date=time.time() - 60)
    rowid2 = fakedb._insert(conn, "chat", {"guid": "SMS;-;+14155550187", "style": 45, "chat_identifier": "+14155550187",
                                           "service_name": "SMS"})
    human = fakedb.ensure_handle(conn, "+14155550187", "SMS")
    conn.execute("INSERT INTO chat_handle_join (chat_id, handle_id) VALUES (?, ?)", (rowid2, human))
    fakedb.insert_message(conn, chat_rowid=rowid2, text="hey it's Nate's friend Cam, see you sunday",
                          is_from_me=False, handle_rowid=human, service="SMS", date=time.time() - 30)
    conn.close()
    convs = rw.conversations()
    assert convs["dm:+14155550188"]["isAutomated"]
    assert not convs["dm:+14155550187"]["isAutomated"]
    assert not convs["dm:+14155550199"]["isAutomated"]  # the vet: Harry has written to them


def test_closer_without_ask_closes_the_loop(rw):
    conn = rw.db()
    chat = fakedb.chat_info(conn, "SMS;-;+14155550199")
    handle = fakedb.ensure_handle(conn, "+14155550199", "SMS")
    fakedb.insert_message(conn, chat_rowid=chat["ROWID"], text="Perfect, you're all set. We'll text when he's ready.",
                          is_from_me=False, handle_rowid=handle, service="SMS", date=time.time())
    conn.close()
    assert not rw.conversation("dm:+14155550199")["awaitingReply"]
