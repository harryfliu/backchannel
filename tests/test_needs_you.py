""""Needs you" end to end: priorities on conversations, importance on messages, events, VIPs."""

from __future__ import annotations

import time
from contextlib import closing
from urllib.parse import quote

from server import fakedb, vips as vips_module
from server.vips import VipBook
from ts_shapes import validate

DAD = "dm:+15105550123"
MOM = "dm:+15105550122"
NINA = "dm:+14155550163"
THEO = "dm:+14155550166"
PLUMBING = "dm:+14155550157"
CLIMBING = "g:chat100000000000000005"


def q(value: str) -> str:
    return quote(value, safe="")


def kinds(reasons) -> list[str]:
    return [r["kind"] for r in reasons or []]


def priorities(harness) -> dict[str, dict | None]:
    return {c["id"]: c["priority"] for c in harness.get("/api/conversations")["conversations"]}


def page(harness, conv_id: str) -> dict[str, dict]:
    messages = harness.get(f"/api/conversations/{q(conv_id)}/messages", params={"limit": 200})["messages"]
    return {m["id"]: m for m in messages}


def test_fixture_conversations(rw):
    rw.client.patch(f"/api/conversations/{q(DAD)}/prefs", json={"starred": True})
    found = priorities(rw)
    dad = found[DAD]
    assert dad["level"] == "urgent" and kinds(dad["reasons"]) == ["urgent", "time", "asked", "vip"]
    assert dad["reasons"][0] == {"kind": "urgent", "word": "call me"}
    assert dad["reasons"][3] == {"kind": "vip", "name": "Dad"}
    assert dad["messageId"] == rw.guid("d_call_soon") and dad["until"]
    nina = found[NINA]
    assert nina["level"] == "urgent" and nina["reasons"] == [{"kind": "repeat", "count": 3}, {"kind": "asked"}]
    climbing = found[CLIMBING]
    assert climbing["level"] == "high"
    assert kinds(climbing["reasons"])[:4] == ["time", "mention", "reply", "vip"]
    assert climbing["reasons"][0] == {"kind": "time", "label": "Tonight", "at": None}
    assert climbing["reasons"][3] == {"kind": "vip", "name": "Priya"}  # Chief knows Priya
    plumbing = found[PLUMBING]
    assert kinds(plumbing["reasons"])[:2] == ["time", "asked"] and plumbing["reasons"][0]["label"] == "Today"
    assert found[THEO] is None  # "be there in 15 min", three hours ago
    assert found[MOM] is None  # read, and "Ok 👍"
    assert found["dm:24273"] is None and found["dm:69525"] is None  # automated senders never count
    order = sorted((p for p in found.values() if p), key=lambda p: (p["level"] != "urgent", -p["score"]))
    assert order[0] is dad  # Dad first: urgent with the most weight
    for conversation in rw.get("/api/conversations")["conversations"]:
        assert not validate(conversation, "Conversation"), conversation["id"]


def test_messages_carry_their_importance(rw):
    dad = page(rw, DAD)
    soon, earlier = dad[rw.guid("d_call_soon")], dad[rw.guid("d_call")]
    assert kinds(soon["importance"]) == ["urgent", "time", "vip"]
    assert earlier["importance"] == [{"kind": "asked"}, {"kind": "vip", "name": "Dad"}]
    assert all(m["importance"] is None for m in dad.values() if m["fromMe"])
    climbing = page(rw, CLIMBING)
    assert kinds(climbing[rw.guid("cc_headcount")]["importance"]) == ["time", "mention"]
    assert kinds(climbing[rw.guid("cc_reply")]["importance"]) == ["reply", "vip"]
    plain = [m for m in climbing.values() if not m["fromMe"] and m["text"] in ("crag", "lol same", "I can take 3")]
    assert plain and all(m["importance"] is None for m in plain)  # unread, but nothing for Harry
    assert page(rw, THEO)[rw.guid("t_late")]["importance"] is None
    nina = page(rw, NINA)
    assert nina[rw.guid("n_3")]["importance"] == [{"kind": "repeat", "count": 3}]
    assert nina[rw.guid("n_2")]["importance"] == [{"kind": "asked"}]
    assert nina[rw.guid("n_1")]["importance"] is None
    results = rw.get("/api/search", params={"q": "insurance forms"})["results"]
    assert kinds(results[0]["message"]["importance"])[:2] == ["urgent", "time"]
    thread = rw.get(f"/api/conversations/{q(CLIMBING)}/threads/{q(rw.guid('cc_mine'))}")
    assert kinds(thread["replies"][0]["importance"]) == ["reply", "vip"]
    for message in [*dad.values(), *climbing.values(), *nina.values()]:
        assert not validate(message, "Message"), message["id"]


def test_mute_star_and_read_change_it(rw):
    rw.client.patch(f"/api/conversations/{q(CLIMBING)}/prefs", json={"muted": True})
    assert priorities(rw)[CLIMBING] is None
    rw.client.patch(f"/api/conversations/{q(CLIMBING)}/prefs", json={"muted": False})
    assert priorities(rw)[CLIMBING] is not None
    assert "vip" not in kinds(priorities(rw)[NINA]["reasons"])
    rw.client.patch(f"/api/conversations/{q(NINA)}/prefs", json={"starred": True})  # starred DM: Nina's a VIP now
    assert priorities(rw)[NINA]["reasons"][-1] == {"kind": "vip", "name": "Nina"}
    summary = rw.post(f"/api/conversations/{q(NINA)}/read")
    assert kinds(summary["priority"]["reasons"]) == ["asked", "vip"]  # repeat needs unread texts
    assert summary["priority"]["level"] == "high"
    events = [e for e in rw.services.hub.history if e["type"] == "conversation" and e["conversation"]["id"] == NINA]
    assert events and kinds(events[-1]["conversation"]["priority"]["reasons"]) == ["asked", "vip"]


def test_answering_ends_it_and_events_follow(rw):
    rw.post(f"/api/conversations/{q(NINA)}/read")
    rw.post(f"/api/conversations/{q(NINA)}/messages", {"text": "yep! what's up", "clientId": "ny-1"})
    events = rw.events_until(lambda e: any(x["type"] == "send-status" for x in e))
    nina = [e["conversation"] for e in events if e["type"] == "conversation" and e["conversation"]["id"] == NINA]
    assert nina and nina[-1]["priority"] is None  # answered and read: nothing needs Harry there


def test_new_messages_arrive_with_importance(rw):
    with closing(rw.db()) as conn:
        chat = fakedb.chat_info(conn, "iMessage;-;+14155550166")
        handle = fakedb.ensure_handle(conn, "+14155550166", "iMessage")
        fakedb.insert_message(conn, chat_rowid=chat["ROWID"], text="URGENT: can you pick up Leo at 5?",
                              is_from_me=False, handle_rowid=handle, date=time.time())
    events = rw.events_until(lambda e: any(x["type"] == "message" for x in e))
    message = next(e["message"] for e in events if e["type"] == "message")
    assert kinds(message["importance"])[:3] == ["urgent", "time", "asked"]
    assert message["importance"][0] == {"kind": "urgent", "word": "urgent"}
    theo = next(e["conversation"] for e in events if e["type"] == "conversation" and e["conversation"]["id"] == THEO)
    assert theo["priority"]["level"] == "urgent" and theo["priority"]["messageId"] == message["id"]
    for event in events:
        assert not validate(event, "ServerEvent", allow_extra={"stickerUrl"}), event["type"]


def test_time_passing_refreshes_priorities(rw):
    watcher = rw.services.watcher
    assert watcher.tick() == []  # nothing changed: no events
    real = time.time
    rw.services.convs.clock = lambda: real() + 2 * 3600  # two hours later: Dad's "in 20 min" is long gone
    events = watcher.tick()
    changed = {e["conversation"]["id"]: e["conversation"]["priority"] for e in events}
    assert DAD in changed and "time" not in kinds(changed[DAD]["reasons"])
    assert changed[DAD]["level"] == "urgent"  # "call me" is still urgent while unread
    assert watcher.tick() == []  # and only once


def test_vip_sources(rw, monkeypatch):
    services = rw.services
    directory = services.convs.directory()
    book = VipBook(services.db, services.state, services.contacts, chief=services.chief)
    found, _ = book.vips(directory)
    names = set(found.values())
    assert {"Jordan", "Mom", "Priya", "Ellie"} <= names  # Chief: full name, nickname, unique first name
    assert "Ben" not in names  # two Bens in Chief: no match
    assert "Nina" not in names  # Harry has never texted her, Chief doesn't know her
    assert "Theo" in names  # one of the 8 Harry texts most (the fixtures are small)
    monkeypatch.setattr(vips_module, "TOP_N", 2)
    no_chief = VipBook(services.db, services.state, services.contacts, chief=None)
    top, before = no_chief.vips(directory)
    assert len(top) == 2  # the two people Harry texted most in 90 days
    services.state.set_prefs(NINA, {"starred": True})
    starred, after = no_chief.vips(directory)
    assert "Nina" in starred.values() and len(starred) == 3 and after != before
