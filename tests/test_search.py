"""Search over attributedBody-only text."""

from __future__ import annotations

import time

from server import fakedb
from server.timeutil import unix_to_apple_ns
from server.typedstream import encode_attributed_body


def results(harness, q, **params):
    return harness.get("/api/search", params={"q": q, **params})["results"]


def test_finds_text_that_only_lives_in_attributed_body(ro):
    found = results(ro, "dashi")
    assert [r["message"]["text"] for r in found] == ["hinodeya on clement! get the dashi ramen"]
    assert found[0]["conversation"] == {"id": "dm:+14155550178", "title": "Priya Natarajan", "kind": "dm"}


def test_prefix_and_multiple_words(ro):
    assert {r["message"]["text"] for r in results(ro, "thanksg")} == {
        "Thanksgiving planning thread! Who's hosting this year?", "Did you get my email about Thanksgiving?"}
    assert [r["message"]["text"] for r in results(ro, "folding chairs")] == ["Harry can you bring your folding chairs too?"]


def test_newest_first_and_limit(ro):
    found = results(ro, "in", limit=3)
    assert len(found) == 3
    stamps = [r["message"]["sentAt"] for r in found]
    assert stamps == sorted(stamps, reverse=True)


def test_conversation_filter(ro):
    found = results(ro, "ramen", conversationId="g:chat100000000000000003")
    assert found == []
    found = results(ro, "ramen", conversationId="dm:+14155550178")
    assert len(found) == 2


def test_no_tapbacks_or_unsent_in_results(ro):
    assert results(ro, "Loved") == []
    assert all(r["message"]["text"] for r in results(ro, "wrong"))


def test_empty_query(ro):
    assert results(ro, "   ") == []


def test_new_and_edited_messages_are_indexed(rw):
    conn = rw.db()
    chat = fakedb.chat_info(conn, "iMessage;-;+16505550147")
    handle = fakedb.ensure_handle(conn, "+16505550147", "iMessage")
    _, guid = fakedb.insert_message(conn, chat_rowid=chat["ROWID"], text="want to try the zanzibar place?",
                                    is_from_me=False, handle_rowid=handle, date=time.time())
    conn.close()
    assert [r["message"]["id"] for r in results(rw, "zanzibar")] == [guid]
    rw.events_until(lambda e: any(x["type"] == "message" for x in e))
    conn = rw.db()
    conn.execute("UPDATE message SET attributedBody = ?, date_edited = ? WHERE guid = ?",
                 (encode_attributed_body("want to try the kokkari place?"), unix_to_apple_ns(time.time()), guid))
    conn.close()
    rw.events_until(lambda e: any(x["type"] == "message-updated" for x in e))
    assert results(rw, "zanzibar") == []
    assert [r["message"]["text"] for r in results(rw, "kokkari")] == ["want to try the kokkari place?"]
