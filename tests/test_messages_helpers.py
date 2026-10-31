"""Tapbacks and real thread replies through the Messages app (Accessibility), and their dry-run simulation.

osascript never runs: FakeMessages (tests/fake_messages.py) answers the scripts and writes the rows
Messages would have written; without a runner the fake-DB Simulator does the same in the server.
"""

from __future__ import annotations

import asyncio
import threading
import time
from contextlib import closing
from urllib.parse import quote

import pytest

from fake_messages import FakeMessages, tagged, write_reply, write_tapback
from server import fakedb
from server.messages_ui import (DENIED_TEXT, REPLY_SCRIPT, REPLY_TIMEOUT, TAPBACK_SCRIPT, TAPBACK_TIMEOUT, TAPBACKS_OFF,
                                Outcome)
from server.sending import REPLY_DONE, REPLY_UNCONFIRMED
from ts_shapes import validate

JORDAN = "dm:+14155550134"
FAMILY = "g:chat100000000000000001"
SOCCER = "g:chat100000000000000002"
MOM = "dm:+15105550122"


def q(value: str) -> str:
    return quote(value, safe="")


def tapback_url(conv_id: str, message_id: str) -> str:
    return f"/api/conversations/{q(conv_id)}/messages/{q(message_id)}/tapback"


def send_url(conv_id: str) -> str:
    return f"/api/conversations/{q(conv_id)}/messages"


def events(rw, kind: str, **match) -> list[dict]:
    return [e for e in rw.services.hub.history if e["type"] == kind and all(e.get(k) == v for k, v in match.items())]


def wait_for(rw, predicate, timeout: float = 8.0):
    """Run watcher passes until predicate(hub history) is truthy; returns its value."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        rw.services.watcher.check()
        found = predicate(rw.services.hub.history)
        if found:
            return found
        time.sleep(0.05)
    raise AssertionError(f"timed out; events: {[e['type'] for e in rw.services.hub.history][-30:]}")


def ui_action(action: str, **match):
    def find(history):
        hits = [e for e in history if e["type"] == "ui-action" and e["action"] == action
                and all(e.get(k) == v for k, v in match.items())]
        return hits[-1] if hits else None
    return find


def turn_on(rw, **settings: bool) -> None:
    assert rw.client.patch("/api/settings", json=settings).status_code == 200
    assert rw.services.messages_ui.wait_idle(5)  # the permission probe, if any


def my_reaction(message: dict) -> str | None:
    return next((r["kind"] for r in message["reactions"] if "me" in r["from"]), None)


@pytest.fixture
def fake_rw(make_rw):
    """A harness whose Messages helpers talk to FakeMessages; tapbacks and replies on."""
    fake = FakeMessages()
    rw = make_rw(messages_ui_runner=fake)
    rw.services.messages_ui.verify_seconds = 1.0
    turn_on(rw, tapbacksViaMessages=True, threadRepliesViaMessages=True)
    rw.fake = fake
    rw.prime = rw.services.watcher.prime
    rw.services.watcher.prime()
    return rw


# --------------------------------------------------------------------------- the tapback route


def test_tapback_route_answers(rw):
    url = tapback_url(JORDAN, rw.guid("j_hike"))
    off = rw.client.post(url, json={"kind": "love", "remove": False})
    assert off.status_code == 409 and off.json() == {"error": TAPBACKS_OFF}
    turn_on(rw, tapbacksViaMessages=True)
    assert rw.client.post(url, json={"kind": "wow", "remove": False}).status_code == 400
    assert rw.client.post(url, json={"remove": False}).status_code == 400
    assert rw.client.post(tapback_url("dm:+19995550100", rw.guid("j_hike")), json={"kind": "love"}).status_code == 404
    assert rw.client.post(tapback_url(JORDAN, "NOT-A-GUID"), json={"kind": "love"}).status_code == 404
    for conv, label, why in ((JORDAN, "j_unsent", "unsent"), (FAMILY, "f_renamed", "event"),
                             ("dm:+16505550147", "e_fire", "tapback"), (FAMILY, "j_hike", "conversation")):
        response = rw.client.post(tapback_url(conv, rw.guid(label)), json={"kind": "like"})
        assert response.status_code == 400, (label, response.text)
        assert why in response.json()["error"].lower(), (label, response.json())
    response = rw.client.post(url, json={"kind": "love", "remove": False})
    assert response.status_code == 202 and response.json() == {"status": "queued"}
    assert rw.services.messages_ui.wait_idle(5)


def test_tapback_route_security(rw):
    from fastapi.testclient import TestClient

    turn_on(rw, tapbacksViaMessages=True)
    url = tapback_url(JORDAN, rw.guid("j_hike"))
    fresh = TestClient(rw.app, base_url="http://127.0.0.1:5556")
    assert fresh.post(url, json={"kind": "love"}).status_code == 401
    assert rw.client.post(url, json={"kind": "love"}, headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert rw.client.post(url, json={"kind": "love"}, headers={"origin": "https://evil.example"}).status_code == 403
    form = rw.client.post(url, content="kind=love", headers={"content-type": "application/x-www-form-urlencoded"})
    assert form.status_code == 415
    assert rw.services.messages_ui.wait_idle(5)
    assert events(rw, "ui-action") == []


# --------------------------------------------------------------------------- tapbacks through Messages


def test_tapback_is_chosen_in_messages_and_verified(fake_rw):
    rw, fake = fake_rw, fake_rw.fake
    target = rw.guid("j_hike")
    fake.effects["tapback"] = lambda argv: write_tapback(rw.settings.chat_db, target, 2000)
    assert rw.client.post(tapback_url(JORDAN, target), json={"kind": "love", "remove": False}).status_code == 202
    done = wait_for(rw, ui_action("tapback", messageId=target))
    assert done == {"type": "ui-action", "action": "tapback", "conversationId": JORDAN, "messageId": target,
                    "clientId": None, "result": "done", "detail": "Sent the tapback in Messages."}
    assert not validate(done, "ServerEvent")
    # The reaction itself reaches the UI as the target's normal message-updated.
    updated = wait_for(rw, lambda h: [e["message"] for e in h if e["type"] == "message-updated"
                                      and e["message"]["id"] == target and my_reaction(e["message"]) == "love"])
    assert updated
    name, script, argv, timeout = fake.calls[-1]
    assert (name, script, timeout) == ("tapback", TAPBACK_SCRIPT, TAPBACK_TIMEOUT)
    assert argv == ["tapback", "L:Jordan Park", "L:(415) 555-0134", "L:+1 (415) 555-0134", "N:Jordan",
                    "T:are you down to hike Saturday?", "S:Jordan Park", "S:Jordan", "K:0", "J:0",
                    "R:Heart", "R:Love", "R:Loved"]
    source = "\n".join(script)
    assert "hike" not in source and "Jordan" not in source  # all of it came as argv
    assert rw.get("/api/health")["messagesUi"]["permission"] == "granted"


def test_tapback_state_is_checked_first(fake_rw):
    rw, fake = fake_rw, fake_rw.fake
    place, bring = rw.guid("j_place"), rw.guid("j_bring")  # fixtures: Harry loved j_place and liked j_bring
    rw.client.post(tapback_url(JORDAN, place), json={"kind": "love", "remove": False})
    assert wait_for(rw, ui_action("tapback", messageId=place))["detail"] == "That tapback is already there."
    rw.client.post(tapback_url(JORDAN, bring), json={"kind": "love", "remove": True})
    assert wait_for(rw, ui_action("tapback", messageId=bring))["detail"] == "That tapback was already removed."
    assert fake.calls == []  # neither needed Messages: choosing it again would have toggled it
    fake.effects["tapback"] = lambda argv: write_tapback(rw.settings.chat_db, place, 3000)
    rw.client.post(tapback_url(JORDAN, place), json={"kind": "love", "remove": True})
    removed = wait_for(rw, ui_action("tapback", messageId=place, detail="Removed your tapback in Messages."))
    assert removed["result"] == "done" and fake.names() == ["tapback"]
    assert tagged(fake.argv("tapback"), "R") == ["Heart", "Love", "Loved"]  # removing = choosing it again
    message = rw.message("j_place")
    assert my_reaction(message) is None


def test_tapback_failures(fake_rw):
    rw, fake = fake_rw, fake_rw.fake
    target = rw.guid("j_hike")
    started = time.monotonic()
    rw.client.post(tapback_url(JORDAN, target), json={"kind": "laugh"})  # Messages "chose" it, but no row came
    failed = wait_for(rw, ui_action("tapback", messageId=target))
    assert failed["result"] == "failed" and failed["detail"] == "Messages didn't confirm the tapback."
    assert time.monotonic() - started >= 0.9  # it waited for chat.db
    fake.answers["tapback"] = Outcome(0, "bubble-not-found", "")
    rw.client.post(tapback_url(JORDAN, rw.guid("j_carpool")), json={"kind": "like"})
    missing = wait_for(rw, ui_action("tapback", messageId=rw.guid("j_carpool")))
    assert missing["detail"] == "Couldn't find that message in Messages. Tapbacks work on recent messages."
    fake.answers["tapback"] = Outcome(0, "error:-25211", "")
    rw.client.post(tapback_url(JORDAN, rw.guid("j_drill")), json={"kind": "question"})
    denied = wait_for(rw, ui_action("tapback", messageId=rw.guid("j_drill")))
    assert denied["result"] == "failed" and denied["detail"] == DENIED_TEXT
    assert rw.get("/api/health")["messagesUi"]["permission"] == "denied"
    fake.answers["tapback"] = Outcome(-1, "", "", timed_out=True)
    rw.services.hub.history.clear()
    rw.client.post(tapback_url(JORDAN, target), json={"kind": "dislike"})
    timed_out = wait_for(rw, ui_action("tapback", messageId=target, result="failed",
                                       detail="Messages didn't confirm the tapback."))
    assert timed_out  # a timeout may still have worked, so chat.db is checked before giving up


def test_bubble_terms_for_repeats_and_attachments(fake_rw):
    rw = fake_rw
    ui = rw.services.messages_ui
    conv = rw.services.convs.get(JORDAN)
    with closing(rw.db()) as conn:  # "lol" from Jordan, then "lol" from Harry: target Jordan's
        chat = fakedb.chat_info(conn, "iMessage;-;+14155550134")
        handle = fakedb.ensure_handle(conn, "+14155550134", "iMessage")
        _, theirs = fakedb.insert_message(conn, chat_rowid=chat["ROWID"], text="lol", is_from_me=False,
                                          handle_rowid=handle, date=time.time() - 30)
        fakedb.insert_message(conn, chat_rowid=chat["ROWID"], text="LOL", is_from_me=True, handle_rowid=handle,
                              date=time.time() - 20)
    rw.services.convs.invalidate([JORDAN])
    job = ui.tapback_job(conv, theirs, "laugh", False)
    assert (job.bubble.needles, job.bubble.exacts) == ([], ["lol"])  # short: must be a whole label line
    assert (job.bubble.skip_sender, job.bubble.skip_any) == (0, 1)  # Harry's newer "LOL" matches too
    voice = ui.tapback_job(rw.services.convs.get(MOM), rw.guid("m_voice"), "love", False)
    assert voice.bubble.exacts == ["Audio Message", "Audio"] and voice.bubble.senders == ["Mom", "Linda Liu"]
    sticker = ui.tapback_job(conv, rw.guid("j_my_sticker"), "love", False)
    assert sticker.bubble.exacts == ["Sticker"] and sticker.bubble.senders == ["Your ", "You,", "You:"]
    long_text = ui.tapback_job(conv, rw.guid("j_place"), "love", False)
    assert long_text.bubble.needles == ["the new place 🏠"]


# --------------------------------------------------------------------------- real thread replies


@pytest.fixture
def sends(monkeypatch, fake_rw):
    """Count the normal AppleScript-style sends (dry-run Sender.send_text)."""
    calls: list[tuple] = []
    original = fake_rw.services.sender.send_text

    def counting(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(fake_rw.services.sender, "send_text", counting)
    return calls


def test_thread_reply_goes_through_messages(fake_rw, sends):
    rw, fake = fake_rw, fake_rw.fake
    root = rw.guid("soc_root")
    fake.effects["reply"] = lambda argv: write_reply(rw.settings.chat_db, root, tagged(argv, "M")[0])
    rw.post(send_url(SOCCER), {"text": "in! bringing the pump", "clientId": "r-1", "threadRootId": root})
    sent = wait_for(rw, lambda h: [e for e in h if e["type"] == "send-status" and e["clientId"] == "r-1"])
    assert sent[0]["status"] == "sent"
    done = wait_for(rw, ui_action("reply", clientId="r-1"))
    assert done == {"type": "ui-action", "action": "reply", "conversationId": SOCCER, "messageId": root,
                    "clientId": "r-1", "result": "done", "detail": REPLY_DONE}
    assert sends == []  # never sent the AppleScript way too
    name, script, argv, timeout = fake.calls[-1]
    assert (name, script, timeout) == ("reply", REPLY_SCRIPT, REPLY_TIMEOUT)
    assert argv[-1] == "M:in! bringing the pump" and argv[1] == "L:Sunday Soccer ⚽"
    assert "pump" not in "\n".join(script)
    message = rw.services.builder.by_guid(sent[0]["messageId"])
    assert message["threadRootId"] == root and message["clientId"] == "r-1"


def test_reply_that_cant_be_typed_fails_instead_of_going_out_as_a_regular_text(fake_rw, sends):
    # Harry's Mac: two failed real replies quietly became regular texts in his wife's chat. Now nothing
    # is sent; the reply fails with the reason, and the UI offers "Send as a regular text" (regularText).
    rw, fake = fake_rw, fake_rw.fake
    root = rw.guid("soc_root")
    fake.answers["reply"] = Outcome(0, "no-reply-item", "")
    rw.post(send_url(SOCCER), {"text": "count me in", "clientId": "r-2", "threadRootId": root})
    action = wait_for(rw, ui_action("reply", clientId="r-2"))
    assert action["result"] == "failed" and action["detail"].startswith("Not sent as a reply: ")
    status = wait_for(rw, lambda h: [e for e in h if e["type"] == "send-status" and e["clientId"] == "r-2"])[0]
    assert status["status"] == "failed" and status["error"].startswith("Not sent as a reply")
    assert sends == []
    # The person chooses a regular text: a new send with regularText skips Messages' Reply.
    rw.post(send_url(SOCCER), {"text": "count me in", "clientId": "r-2b", "threadRootId": root, "regularText": True})
    sent = wait_for(rw, lambda h: [e for e in h if e["type"] == "send-status" and e["clientId"] == "r-2b"])[0]
    assert sent["status"] == "sent" and len(sends) == 1
    assert rw.services.builder.by_guid(sent["messageId"])["threadRootId"] == root  # still threaded locally
    assert fake.names() == ["reply"]  # Messages' Reply was only tried the first time


def test_typed_but_unconfirmed_reply_is_never_sent_twice(fake_rw, sends):
    rw, fake = fake_rw, fake_rw.fake
    root = rw.guid("soc_root")
    fake.answers["reply"] = Outcome(0, "returned-unconfirmed", "")  # Return was pressed; no row (yet)
    rw.post(send_url(SOCCER), {"text": "on my way", "clientId": "r-3", "threadRootId": root})
    failed = wait_for(rw, lambda h: [e for e in h if e["type"] == "send-status" and e["clientId"] == "r-3"])[0]
    assert failed["status"] == "failed" and failed["error"] == REPLY_UNCONFIRMED
    action = wait_for(rw, ui_action("reply", clientId="r-3"))
    assert action["result"] == "failed" and action["detail"] == REPLY_UNCONFIRMED
    assert sends == []
    # Same clientId again (a retry): still never a second copy.
    assert rw.post(send_url(SOCCER), {"text": "on my way", "clientId": "r-3", "threadRootId": root})["status"] == "queued"
    assert rw.services.messages_ui.wait_idle(5) and fake.names() == ["reply"] and sends == []
    # Messages sends it after all: the row is still matched to the clientId.
    write_reply(rw.settings.chat_db, root, "on my way")
    later = wait_for(rw, lambda h: [e for e in h if e["type"] == "send-status" and e["clientId"] == "r-3"
                                    and e["status"] == "sent"])
    assert later[0]["messageId"]


def test_typing_errors_count_as_maybe_sent(fake_rw, sends):
    rw, fake = fake_rw, fake_rw.fake
    root = rw.guid("soc_root")
    for i, outcome in enumerate([Outcome(-1, "", "", timed_out=True), Outcome(0, "error-late:-1728", ""),
                                 Outcome(1, "", "osascript: something odd"), Outcome(0, "typed-unknown", "")]):
        fake.answers["reply"] = outcome
        rw.post(send_url(SOCCER), {"text": f"maybe {i}", "clientId": f"m-{i}", "threadRootId": root})
        status = wait_for(rw, lambda h: [e for e in h if e["type"] == "send-status" and e["clientId"] == f"m-{i}"])[0]
        assert status["status"] == "failed" and status["error"] == REPLY_UNCONFIRMED, outcome
    assert sends == []


def test_nothing_typed_answers_all_fail_without_sending(fake_rw, sends):
    rw, fake = fake_rw, fake_rw.fake
    root = rw.guid("soc_root")
    answers = ["not-running", "row-not-found", "not-selected", "bubble-not-found", "no-menu", "no-reply-field",
               "no-reply-mode", "field-busy", "clipboard-busy", "nothing-typed", "error:-1719"]
    for i, answer in enumerate(answers):
        fake.answers["reply"] = Outcome(0, answer, "")
        rw.post(send_url(SOCCER), {"text": f"plain {i}", "clientId": f"p-{i}", "threadRootId": root})
        action = wait_for(rw, ui_action("reply", clientId=f"p-{i}"))
        assert action["result"] == "failed", answer
    assert sends == []


def test_scheduled_thread_reply_goes_through_messages(fake_rw, sends):
    rw, fake = fake_rw, fake_rw.fake
    root = rw.guid("soc_root")
    fake.effects["reply"] = lambda argv: write_reply(rw.settings.chat_db, root, tagged(argv, "M")[0])
    response = rw.post(send_url(SOCCER), {"text": "see you at 9", "clientId": "s-r", "threadRootId": root,
                                          "sendAt": "2031-01-01T09:00:00Z"})
    rw.services.state._exec("UPDATE scheduled SET send_at = ? WHERE id = ?", (time.time() - 1, response["scheduledId"]))
    asyncio.run(rw.services.sending.run_due())
    assert fake.names() == ["reply"] and sends == []
    assert rw.get("/api/scheduled")["items"][0]["status"] == "sent"
    assert wait_for(rw, ui_action("reply", clientId="s-r"))["result"] == "done"


def test_replies_off_means_the_normal_send(fake_rw, sends):
    rw, fake = fake_rw, fake_rw.fake
    turn_on(rw, threadRepliesViaMessages=False)
    rw.post(send_url(SOCCER), {"text": "normal", "clientId": "n-1", "threadRootId": rw.guid("soc_root")})
    wait_for(rw, lambda h: [e for e in h if e["type"] == "send-status" and e["clientId"] == "n-1"])
    assert len(sends) == 1 and fake.calls == [] and events(rw, "ui-action") == []


# --------------------------------------------------------------------------- one at a time


def test_helpers_run_one_at_a_time(make_rw):
    fake = FakeMessages()
    rw = make_rw(messages_ui_runner=fake)
    rw.services.messages_ui.verify_seconds = 0.3
    turn_on(rw, markReadInMessages=True, tapbacksViaMessages=True)
    fake.gate = threading.Event()
    rw.post(f"/api/conversations/{q(JORDAN)}/read")
    assert fake.started.wait(5)
    rw.client.post(tapback_url(JORDAN, rw.guid("j_hike")), json={"kind": "like"})
    rw.client.post(tapback_url(JORDAN, rw.guid("j_carpool")), json={"kind": "love"})
    time.sleep(0.2)
    assert fake.names() == ["mark-read"]  # the tapbacks wait for read sync to finish
    fake.gate.set()
    assert rw.services.messages_ui.wait_idle(10)
    assert fake.names() == ["mark-read", "tapback", "tapback"] and fake.max_active == 1


# --------------------------------------------------------------------------- dry-run simulation


def test_dry_run_simulates_tapbacks(rw):
    turn_on(rw, tapbacksViaMessages=True)
    rw.services.watcher.prime()
    target = rw.guid("j_carpool")
    assert rw.client.post(tapback_url(JORDAN, target), json={"kind": "emphasize"}).status_code == 202
    done = wait_for(rw, ui_action("tapback", messageId=target))
    assert done["result"] == "done"
    with closing(rw.db()) as conn:
        row = conn.execute("SELECT is_from_me, associated_message_type, associated_message_guid FROM message "
                           "ORDER BY ROWID DESC LIMIT 1").fetchone()
    assert tuple(row) == (1, 2004, f"p:0/{target}")
    updated = wait_for(rw, lambda h: [e for e in h if e["type"] == "message-updated" and e["message"]["id"] == target])
    assert my_reaction(updated[-1]["message"]) == "emphasize"
    rw.client.post(tapback_url(JORDAN, target), json={"kind": "emphasize", "remove": True})
    wait_for(rw, ui_action("tapback", messageId=target, detail="Removed your tapback in Messages."))
    assert my_reaction(rw.message("j_carpool")) is None


def test_dry_run_simulates_real_replies(rw):
    turn_on(rw, threadRepliesViaMessages=True)
    rw.services.watcher.prime()
    root = rw.guid("j_hike")
    rw.post(send_url(JORDAN), {"text": "yes! what time?", "clientId": "d-1", "threadRootId": root})
    sent = wait_for(rw, lambda h: [e for e in h if e["type"] == "send-status" and e["clientId"] == "d-1"])[0]
    assert sent["status"] == "sent"
    assert wait_for(rw, ui_action("reply", clientId="d-1"))["result"] == "done"
    with closing(rw.db()) as conn:
        row = conn.execute("SELECT thread_originator_guid, is_from_me FROM message WHERE guid = ?",
                           (sent["messageId"],)).fetchone()
    assert tuple(row) == (root, 1)  # a real reply row, not just the local mapping
    assert rw.services.state.thread_roots_for([sent["messageId"]]) == {sent["messageId"]: root}
    updated = wait_for(rw, lambda h: [e for e in h if e["type"] == "message-updated" and e["message"]["id"] == root])
    assert updated[-1]["message"]["thread"]["replyCount"] >= 1


def test_plain_dry_run_never_drives_messages(make_rw):
    rw = make_rw(BACKCHANNEL_FAKE_DB="0")  # dry-run against a chat.db we must not write
    turn_on(rw, tapbacksViaMessages=True, threadRepliesViaMessages=True)
    target = rw.guid("j_hike")
    assert rw.client.post(tapback_url(JORDAN, target), json={"kind": "love"}).status_code == 202
    failed = wait_for(rw, ui_action("tapback", messageId=target))
    assert failed["result"] == "failed" and "dry-run" in failed["detail"]
    rw.post(send_url(JORDAN), {"text": "plain", "clientId": "pd-1", "threadRootId": target})
    wait_for(rw, lambda h: [e for e in h if e["type"] == "send-status" and e["clientId"] == "pd-1"])
    assert [e for e in events(rw, "ui-action") if e["action"] == "reply"] == []  # replies just go out normally


# --------------------------------------------------------------------------- the watcher


def test_my_tapback_rows_update_their_target(rw):
    rw.services.watcher.prime()
    target = rw.guid("j_carpool")
    write_tapback(rw.settings.chat_db, target, 2003)
    updated = wait_for(rw, lambda h: [e for e in h if e["type"] == "message-updated" and e["message"]["id"] == target])
    assert my_reaction(updated[-1]["message"]) == "laugh"
    assert not [e for e in events(rw, "message") if e["message"]["id"] == target]  # not a new message


# --------------------------------------------------------------------------- stickers outside Attachments/


def test_files_in_sticker_cache_and_container_are_served(ro):
    ramen, kevin = ro.attachment("d_ellie_sticker"), ro.attachment("soc_sticker")
    rows = {r["guid"]: r["filename"] for r in (ro.services.db.attachment_by_guid(g) for g in (ramen, kevin))}
    assert rows[ramen].startswith("~/Library/Messages/StickerCache/")
    assert rows[kevin].startswith("~/Library/Containers/com.apple.MobileSMS/")
    for guid in (ramen, kevin):
        response = ro.client.get(f"/api/attachments/{q(guid)}")
        assert response.status_code == 200 and response.headers["content-type"] == "image/png", guid
        assert ro.client.get(f"/api/stickers/{q(guid)}/image").status_code == 200
    assert {s["id"] for s in ro.get("/api/stickers")["items"]} >= {ramen, kevin}
    sticker = next(a for a in ro.message("d_ellie_sticker")["attachments"])
    assert not sticker["missing"] and sticker["thumbUrl"]


def test_temp_folder_files_show_as_missing(ro):
    row = ro.services.db.attachment_by_guid(ro.attachment("f_missing"))
    assert row["filename"].startswith("/var/folders/")
    attachment = ro.message("f_missing")["attachments"][0]
    assert attachment["missing"] and attachment["thumbUrl"] is None
    response = ro.client.get(f"/api/attachments/{q(ro.attachment('f_missing'))}")
    assert response.status_code == 404 and "temporary" in response.json()["error"]


def test_media_roots_keep_the_containment_check(tmp_path):
    from server.media import Media

    stickers = tmp_path / "StickerCache"
    (stickers / "AB").mkdir(parents=True)
    (stickers / "AB" / "s.png").write_bytes(b"\x89PNG")
    (tmp_path / "secret.txt").write_text("no")
    media = Media(tmp_path / "Attachments", tmp_path / "cache", sticker_cache_root=stickers,
                  messages_container_root=tmp_path / "Container")
    assert media.resolve("~/Library/Messages/StickerCache/AB/s.png") == (stickers / "AB" / "s.png").resolve()
    for bad in ("~/Library/Messages/StickerCache/../../secret.txt", str(tmp_path / "secret.txt"),
                "/var/folders/h2/x/T/a.jpg", "~/Library/Containers/com.apple.MobileSMS/../../../secret.txt"):
        assert media.resolve(bad) is None, bad
    (stickers / "AB" / "link.png").symlink_to(tmp_path / "secret.txt")
    assert media.resolve("~/Library/Messages/StickerCache/AB/link.png") is None  # realpath, not the name
