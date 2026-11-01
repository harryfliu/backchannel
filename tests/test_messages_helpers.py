"""Tapbacks and real thread replies through the Messages app (Accessibility), and their dry-run simulation.

osascript never runs: FakeMessages (tests/fake_messages.py) answers the scripts and writes the rows
Messages would have written; without a runner the fake-DB Simulator does the same in the server.
"""

from __future__ import annotations

import re

import asyncio
import threading
import time
from contextlib import closing
from urllib.parse import quote

import pytest

from fake_messages import FakeMessages, chat_of, tagged, write_reply, write_tapback
from server import fakedb
from server.messages_scripts import REPLY_AIM_SCRIPT, REPLY_FINISH_SCRIPT
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
    rw.services.messages_ui.note_reply_proof(True)  # most tests reply to other people (see the gate test)
    rw.clicks = []  # right-clicks a reply asked for (server/mouse.py on a Mac)
    rw.services.messages_ui.can_click = lambda: True
    rw.services.messages_ui.right_click = lambda x, y: rw.clicks.append((x, y)) or (1.0, 2.0)
    rw.services.messages_ui.move_pointer = lambda x, y: None
    rw.services.messages_ui.control_click = lambda x, y: rw.clicks.append(("ctrl", x, y)) or (1.0, 2.0)
    rw.services.messages_ui.pointer_now = lambda: (400.0, 300.0)
    rw.services.messages_ui.element_at = lambda x, y: (1, None)  # what's under the point (accessibility)
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
    times = [a for a in argv if a.startswith("H:")]
    assert argv == ["tapback", "L:Jordan Park", "L:(415) 555-0134", "L:+1 (415) 555-0134", "N:Jordan", "N:Jordan P.",
                    "T:are you down to hike Saturday?", "S:Jordan Park", "S:Jordan", "K:0", "J:0", *times, "Q:0",
                    "R:Heart", "R:Love", "R:Loved", "U:imessage://+14155550134"]
    # The minute it was sent, as Messages may print it: " 2:41" and " 14:41" (12- and 24-hour clocks).
    assert times and all(re.fullmatch(r"H: \d{1,2}:\d{2}", t) for t in times)
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
    assert missing["detail"] == "Couldn't find that message in Messages, even after scrolling up."
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
    # Harry's Mac: "Reply to Last Message" answered the newest message, so every reply now aims at its
    # own: find it (reply-aim), right-click it, choose Reply… in its menu and send (reply-finish).
    (aim_name, aim_script, aim_argv, _), (name, script, argv, timeout) = fake.calls[-2:]
    assert (aim_name, aim_script) == ("reply-aim", REPLY_AIM_SCRIPT) and aim_argv[1] == "L:Sunday Soccer ⚽"
    assert (name, script, timeout) == ("reply-finish", REPLY_FINISH_SCRIPT, REPLY_TIMEOUT)
    assert argv == ["reply-finish", "M:in! bringing the pump", "B:iMessage", "F:com.example.backchannel"]
    assert rw.clicks == [(400.0, 300.0)]
    assert "pump" not in "\n".join(script) and "pump" not in "\n".join(aim_script)
    message = rw.services.builder.by_guid(sent[0]["messageId"])
    assert message["threadRootId"] == root and message["clientId"] == "r-1"


def test_reply_that_cant_be_typed_fails_instead_of_going_out_as_a_regular_text(fake_rw, sends):
    # Harry's Mac: two failed real replies quietly became regular texts in his wife's chat. Now nothing
    # is sent; the reply fails with the reason, and the UI offers "Send as a regular text" (regularText).
    rw, fake = fake_rw, fake_rw.fake
    root = rw.guid("soc_root")
    fake.answers["reply-finish"] = Outcome(0, "no-reply-item", "")
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
    assert fake.names() == ["reply-aim", "reply-finish"]  # Messages' Reply was only tried the first time


def test_typed_but_unconfirmed_reply_is_never_sent_twice(fake_rw, sends):
    rw, fake = fake_rw, fake_rw.fake
    root = rw.guid("soc_root")
    fake.answers["reply-finish"] = Outcome(0, "returned-unconfirmed", "")  # Return was pressed; no row (yet)
    rw.post(send_url(SOCCER), {"text": "on my way", "clientId": "r-3", "threadRootId": root})
    failed = wait_for(rw, lambda h: [e for e in h if e["type"] == "send-status" and e["clientId"] == "r-3"])[0]
    assert failed["status"] == "failed" and failed["error"] == REPLY_UNCONFIRMED
    action = wait_for(rw, ui_action("reply", clientId="r-3"))
    assert action["result"] == "failed" and action["detail"] == REPLY_UNCONFIRMED
    assert sends == []
    # Same clientId again (a retry): still never a second copy.
    assert rw.post(send_url(SOCCER), {"text": "on my way", "clientId": "r-3", "threadRootId": root})["status"] == "queued"
    assert rw.services.messages_ui.wait_idle(5) and fake.names() == ["reply-aim", "reply-finish"] and sends == []
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
        fake.answers["reply-finish"] = outcome
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
        fake.answers["reply-finish"] = Outcome(0, answer, "")
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
    assert fake.names() == ["reply-aim", "reply-finish"] and sends == []
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


SELF = "dm:+15105550100"


def test_replies_to_others_wait_until_one_threads_in_your_own_chat(fake_rw, sends):
    """Harry's Mac: Messages never really switched into reply mode, and his wife got plain texts. Until
    a real reply has threaded on this Mac (in his own chat), replies to others aren't attempted."""
    rw, fake = fake_rw, fake_rw.fake
    helpers = rw.services.messages_ui
    helpers.note_reply_proof(False)
    root = rw.guid("soc_root")
    rw.post(send_url(SOCCER), {"text": "in!", "clientId": "g-1", "threadRootId": root})
    failed = wait_for(rw, lambda h: [e for e in h if e["type"] == "send-status" and e["clientId"] == "g-1"])
    assert failed[0]["status"] == "failed"
    assert failed[0]["error"].startswith("Not sent as a reply: real replies haven't worked on this Mac yet.")
    assert not [c for c in fake.calls if c[0] == "reply"] and sends == []
    # In your own chat it's tried. A reply to the newest message threads but proves nothing (Messages'
    # Reply answers the newest one by default); one to an older message turns replies to others on.
    mine = rw.get(f"/api/conversations/{SELF}/messages")["messages"][-1]["id"]
    fake.effects["reply"] = lambda argv: write_reply(rw.settings.chat_db, mine, tagged(argv, "M")[0])
    rw.post(send_url(SELF), {"text": "noted", "clientId": "s-0", "threadRootId": mine})
    sent = wait_for(rw, lambda h: [e for e in h if e["type"] == "send-status" and e["clientId"] == "s-0"])
    assert sent[0]["status"] == "sent" and not helpers.replies_proven()
    fakedb.write_as_messages_app(rw.settings.chat_db, chat_of(rw.settings.chat_db, mine), text="another note")
    rw.post(send_url(SELF), {"text": "noted again", "clientId": "s-1", "threadRootId": mine})
    sent = wait_for(rw, lambda h: [e for e in h if e["type"] == "send-status" and e["clientId"] == "s-1"])
    assert sent[0]["status"] == "sent" and helpers.replies_proven()
    # One that goes out unthreaded turns them off again.
    fake.effects["reply"] = lambda argv: write_reply(rw.settings.chat_db, root, tagged(argv, "M")[0], threaded=False)
    rw.post(send_url(SOCCER), {"text": "ok then", "clientId": "g-2", "threadRootId": root})
    regular = wait_for(rw, ui_action("reply", clientId="g-2"))
    assert regular["result"] == "fallback" and not helpers.replies_proven()


def test_an_unnamed_groups_members_go_along(fake_rw):
    rw = fake_rw
    helpers = rw.services.messages_ui
    trio = next(c for c in rw.get("/api/conversations")["conversations"] if c["kind"] == "group"
                and c["title"].count(",") + c["title"].count("&") >= 2 and not c["title"].startswith(("Sunday", "Dinner",
                                                                                                         "Liu", "Climbing")))
    conv = rw.services.convs.get(trio["id"])
    members = helpers._members(conv)
    assert len(members) == len(conv.persons) >= 2
    assert all("|" not in m or m.split("|")[0] for m in members)
    assert [f"G:{m}" for m in members] == [a for a in helpers.mark_read_job(conv).argv() if a.startswith("G:")]
    named = rw.services.convs.get(SOCCER)
    assert helpers._members(named) == []  # a named group goes by its name


# --------------------------------------------------------------------------- emoji tapbacks


def mine(message: dict) -> tuple[str, str] | None:
    return next(((r["kind"], r["emoji"]) for r in message["reactions"] if "me" in r["from"]), None)


def test_emoji_tapback_route_checks_the_emoji(rw):
    turn_on(rw, tapbacksViaMessages=True)
    url = tapback_url(JORDAN, rw.guid("j_hike"))
    for bad in (None, "", "lol", "🔥 🔥", "1", "a🔥"):
        body = {"kind": "emoji", "remove": False, **({"emoji": bad} if bad is not None else {})}
        assert rw.client.post(url, json=body).status_code == 400, bad
    for good in ("🔥", "❤️", "1️⃣", "👍🏽", "🏳️‍🌈"):
        assert rw.client.post(url, json={"kind": "emoji", "emoji": good}).status_code == 202, good


def test_emoji_tapback_is_chosen_by_its_emoji_and_verified(fake_rw, caplog):
    rw, fake = fake_rw, fake_rw.fake
    target = rw.guid("j_hike")
    fake.effects["tapback"] = lambda argv: write_tapback(rw.settings.chat_db, target, 2006, "🔥")
    assert rw.client.post(tapback_url(JORDAN, target), json={"kind": "emoji", "emoji": "🔥"}).status_code == 202
    done = wait_for(rw, ui_action("tapback", messageId=target))
    assert done["result"] == "done" and done["detail"] == "Sent the tapback in Messages."
    assert tagged(fake.argv("tapback"), "R") == ["🔥", "🔥\ufe0f"]  # Messages titles its recent emoji with the emoji
    message = rw.services.builder.by_guid(target)
    assert mine(message) == ("emoji", "🔥")
    # The same one again is already there (no Messages); removing it chooses it again.
    calls = len(fake.calls)
    rw.client.post(tapback_url(JORDAN, target), json={"kind": "emoji", "emoji": "🔥"})
    assert wait_for(rw, ui_action("tapback", messageId=target, detail="That tapback is already there."))
    assert len(fake.calls) == calls
    fake.effects["tapback"] = lambda argv: write_tapback(rw.settings.chat_db, target, 3006, "🔥")
    rw.client.post(tapback_url(JORDAN, target), json={"kind": "emoji", "emoji": "🔥", "remove": True})
    assert wait_for(rw, ui_action("tapback", messageId=target, detail="Removed your tapback in Messages."))
    assert mine(rw.services.builder.by_guid(target)) is None
    assert "🔥" not in caplog.text  # the log gets codes, never the emoji


def test_emoji_tapback_ignores_the_presentation_selector(fake_rw):
    rw, fake = fake_rw, fake_rw.fake
    target = rw.guid("j_hike")
    fake.effects["tapback"] = lambda argv: write_tapback(rw.settings.chat_db, target, 2006, "\u2764")  # no FE0F
    rw.client.post(tapback_url(JORDAN, target), json={"kind": "emoji", "emoji": "❤️"})
    assert wait_for(rw, ui_action("tapback", messageId=target))["result"] == "done"
    assert tagged(fake.argv("tapback"), "R") == ["❤️", "\u2764"]


def test_an_emoji_messages_doesnt_offer_says_so_and_the_quick_ones_are_remembered(fake_rw, caplog):
    rw, fake = fake_rw, fake_rw.fake
    target = rw.guid("j_hike")
    assert rw.get("/api/settings")["tapbackQuick"] == []
    fake.answers["tapback"] = Outcome(0, "no-tapback-item|shown t0\nquick\t😂 👀 😭 🤔 😱", "")
    rw.client.post(tapback_url(JORDAN, target), json={"kind": "emoji", "emoji": "🔥"})
    failed = wait_for(rw, ui_action("tapback", messageId=target))
    assert failed["result"] == "failed"
    assert failed["detail"] == ("Messages only offers its most recent emoji as quick tapbacks, and 🔥 isn't one of "
                                "them yet. React with it once in Messages and it will be.")
    settings = rw.get("/api/settings")
    assert settings["tapbackQuick"] == ["😂", "👀", "😭", "🤔", "😱"] and not validate(settings, "ServerSettings")
    assert "😂" not in caplog.text and "🔥" not in caplog.text
    # A classic that Messages didn't offer keeps the usual sentence.
    rw.client.post(tapback_url(JORDAN, target), json={"kind": "like"})
    assert wait_for(rw, ui_action("tapback", messageId=target, detail="Messages didn't offer that tapback."))


def test_the_simulator_writes_emoji_tapbacks(make_rw):
    rw = make_rw(BACKCHANNEL_SEND_MODE="dry-run", BACKCHANNEL_FAKE_DB="1")
    turn_on(rw, tapbacksViaMessages=True)
    target = rw.guid("j_hike")
    rw.client.post(tapback_url(JORDAN, target), json={"kind": "emoji", "emoji": "🎉"})
    assert wait_for(rw, ui_action("tapback", messageId=target))["result"] == "done"
    assert mine(rw.services.builder.by_guid(target)) == ("emoji", "🎉")


LINK = "https://www.tartine.example/menu"


def test_a_message_that_is_only_a_link_goes_from_messages_text_field(fake_rw, sends):
    """AppleScript sends are plain text, so Harry's links arrived as bare "target.com" cards."""
    rw, fake = fake_rw, fake_rw.fake
    turn_on(rw, linkPreviewsViaMessages=True)
    chat = rw.services.convs.get(JORDAN).chat_ids[0]
    fake.effects["compose"] = lambda argv: fakedb.write_as_messages_app(
        rw.settings.chat_db, chat, text=tagged(argv, "M")[0], balloon_bundle_id="com.apple.messages.URLBalloonProvider")
    rw.post(send_url(JORDAN), {"text": LINK, "clientId": "l-1"})
    sent = wait_for(rw, lambda h: [e for e in h if e["type"] == "send-status" and e["clientId"] == "l-1"])
    assert sent[0]["status"] == "sent" and sends == []
    name, _script, argv, _timeout = fake.calls[-1]
    assert name == "compose" and f"M:{LINK}" in argv and "U:imessage://+14155550134" in argv
    # Text with a link in it, or the setting off: the normal way.
    rw.post(send_url(JORDAN), {"text": f"lunch? {LINK}", "clientId": "l-2"})
    wait_for(rw, lambda h: [e for e in h if e["type"] == "send-status" and e["clientId"] == "l-2"])
    assert len(sends) == 1
    turn_on(rw, linkPreviewsViaMessages=False)
    rw.post(send_url(JORDAN), {"text": LINK + "?again", "clientId": "l-3"})
    wait_for(rw, lambda h: [e for e in h if e["type"] == "send-status" and e["clientId"] == "l-3"])
    assert len(sends) == 2 and [c[0] for c in fake.calls].count("compose") == 1


def test_a_link_messages_couldnt_take_goes_the_normal_way(fake_rw, sends):
    rw, fake = fake_rw, fake_rw.fake
    turn_on(rw, linkPreviewsViaMessages=True)
    fake.answers["compose"] = Outcome(0, "field-busy|shown", "")
    rw.post(send_url(JORDAN), {"text": LINK, "clientId": "l-4"})
    sent = wait_for(rw, lambda h: [e for e in h if e["type"] == "send-status" and e["clientId"] == "l-4"])
    assert sent[0]["status"] == "sent" and len(sends) == 1  # the link still went, just without a preview


def test_a_reply_that_cant_be_aimed_types_nothing(fake_rw, sends):
    rw, fake = fake_rw, fake_rw.fake
    root = rw.guid("soc_root")
    fake.answers["reply-aim"] = Outcome(0, "bubble-not-found|shown b6/0/0", "")
    rw.post(send_url(SOCCER), {"text": "where?", "clientId": "a-1", "threadRootId": root})
    action = wait_for(rw, ui_action("reply", clientId="a-1"))
    assert action["result"] == "failed" and action["detail"].startswith("Not sent as a reply: Couldn't find")
    assert fake.names() == ["reply-aim"] and rw.clicks == [] and sends == []


def test_the_newest_message_falls_back_to_reply_to_last_message(fake_rw, sends):
    """When the right-click couldn't open a menu, the menu bar's Reply is still right for the newest."""
    rw, fake = fake_rw, fake_rw.fake
    mine = rw.get(f"/api/conversations/{SELF}/messages")["messages"][-1]["id"]  # the only (newest) one
    fake.answers["reply-finish"] = Outcome(0, "no-menu", "")
    fake.effects["reply-finish"] = lambda argv: None
    fake.effects["reply"] = lambda argv: write_reply(rw.settings.chat_db, mine, tagged(argv, "M")[0])
    rw.post(send_url(SELF), {"text": "noted", "clientId": "n-1", "threadRootId": mine})
    sent = wait_for(rw, lambda h: [e for e in h if e["type"] == "send-status" and e["clientId"] == "n-1"])
    assert sent[0]["status"] == "sent"
    assert fake.names() == ["reply-aim", "reply-finish", "reply-finish", "reply"]  # right-click, Control-click, menu bar


def test_a_chat_still_opening_from_its_link_is_tried_once_more(fake_rw):
    """Harry's Mac: "row-not-found [link w1/1 tl26/0 no row0/0]"; the same tapback worked a moment later."""
    rw, fake = fake_rw, fake_rw.fake
    target = rw.guid("j_hike")
    answers = iter([Outcome(0, "row-not-found|link w1/1 tl26/0 no tr0 row0/0", ""), Outcome(0, "chosen-action|shown t0", "")])
    fake.answers["tapback"] = Outcome(0, "chosen-action|shown t0", "")

    def effect(argv):
        fake.answers["tapback"] = next(answers)
        if fake.answers["tapback"].stdout.startswith("chosen"):
            write_tapback(rw.settings.chat_db, target, 2000)

    fake.effects["tapback"] = effect
    rw.services.messages_ui.verify_seconds = 1.0
    import server.messages_ui as mui
    original = mui.time.sleep
    mui.time.sleep = lambda s: None  # don't wait in the test
    try:
        assert rw.client.post(tapback_url(JORDAN, target), json={"kind": "love", "remove": False}).status_code == 202
        done = wait_for(rw, ui_action("tapback", messageId=target))
    finally:
        mui.time.sleep = original
    assert done["result"] == "done" and fake.names().count("tapback") == 2



def test_no_menu_after_a_right_click_tries_a_control_click(fake_rw, sends):
    rw, fake = fake_rw, fake_rw.fake
    root = rw.guid("soc_root")
    answers = iter([Outcome(0, "no-menu|top:AXWindow,AXMenuBar w1", ""), Outcome(0, "sent-paste|r:right-click", "")])
    fake.answers["reply-finish"] = Outcome(0, "no-menu", "")

    def effect(argv):
        fake.answers["reply-finish"] = next(answers)
        if fake.answers["reply-finish"].stdout.startswith("sent"):
            write_reply(rw.settings.chat_db, root, tagged(argv, "M")[0])

    fake.effects["reply-finish"] = effect
    rw.post(send_url(SOCCER), {"text": "second try", "clientId": "c-1", "threadRootId": root})
    sent = wait_for(rw, lambda h: [e for e in h if e["type"] == "send-status" and e["clientId"] == "c-1"])
    assert sent[0]["status"] == "sent"
    assert rw.clicks == [(400.0, 300.0), ("ctrl", 400.0, 300.0)]
    assert fake.names() == ["reply-aim", "reply-finish", "reply-finish"] and sends == []


AIMED = "aim:400,300|shown t0\nfront\tcom.example.backchannel\nhint\tiMessage\nbox\t350,250,100,100\npid\t42"


def test_a_click_only_goes_where_the_message_is(fake_rw, sends):
    """Harry's Mac: Messages sat behind Backchannel's own window, and on macOS 26 the toolbar, the title
    and the sidebar float over the chat (a click aimed at a tall bubble's middle hit the title). What's
    under the point has to be the message (inside its frame), in Messages, or nothing is clicked."""
    rw, fake = fake_rw, fake_rw.fake
    root = rw.guid("soc_root")
    fake.answers["reply-aim"] = Outcome(0, AIMED, "")
    for hit, why in (((7, (350.0, 250.0, 100.0, 100.0)), "covered"), ((42, (0.0, 30.0, 661.0, 811.0)), "off-bubble"),
                     (None, "hit-unknown")):
        rw.services.messages_ui.element_at = lambda x, y, hit=hit: hit
        rw.post(send_url(SOCCER), {"text": "where?", "clientId": f"h-{why}", "threadRootId": root})
        action = wait_for(rw, ui_action("reply", clientId=f"h-{why}"))
        assert action["result"] == "failed" and "in front of that message" in action["detail"], why
    assert rw.clicks == [] and "reply-finish" not in fake.names() and sends == []
    rw.services.messages_ui.element_at = lambda x, y: (42, (360.0, 260.0, 80.0, 40.0))  # inside the bubble
    fake.effects["reply"] = lambda argv: write_reply(rw.settings.chat_db, root, tagged(argv, "M")[0])
    rw.post(send_url(SOCCER), {"text": "there", "clientId": "h-ok", "threadRootId": root})
    sent = wait_for(rw, lambda h: [e for e in h if e["type"] == "send-status" and e["clientId"] == "h-ok"])
    assert sent[0]["status"] == "sent" and rw.clicks == [(400.0, 300.0)]


def test_in_your_own_chat_either_copy_of_the_root_counts(fake_rw, sends):
    """chat.db keeps each message of your own chat twice (sent and received: other guids, same text,
    same second), Messages shows both, and the reply threads under the copy that was clicked."""
    rw, fake = fake_rw, fake_rw.fake
    db = rw.settings.chat_db
    chat = chat_of(db, rw.get(f"/api/conversations/{SELF}/messages")["messages"][-1]["id"])
    _, sent_copy = fakedb.write_as_messages_app(db, chat, text="pick up the dry cleaning")
    _, received_copy = fakedb.write_as_messages_app(db, chat, text="pick up the dry cleaning")
    _, other = fakedb.write_as_messages_app(db, chat, text="buy stamps")
    with closing(fakedb.connect(db)) as conn:
        date = conn.execute("SELECT date FROM message WHERE guid = ?", (sent_copy,)).fetchone()[0]
        conn.execute("UPDATE message SET is_from_me = 0, date = ? WHERE guid = ?", (date + 1_000_000_000, received_copy))
    for root, threaded_under, client, result in ((sent_copy, received_copy, "t-1", "done"),
                                                 (sent_copy, other, "t-2", "fallback")):
        fake.effects["reply"] = lambda argv, under=threaded_under: write_reply(db, under, tagged(argv, "M")[0])
        rw.post(send_url(SELF), {"text": f"on it {client}", "clientId": client, "threadRootId": root})
        action = wait_for(rw, ui_action("reply", clientId=client))
        assert action["result"] == result, (client, action)
    assert sends == []


def test_in_your_own_chat_a_tapback_on_the_other_copy_counts(fake_rw):
    """Harry's Mac: a Heart aimed at one copy landed on the other, and once taken off only the
    received-side row was left. Both copies and both sides are you there."""
    rw, fake = fake_rw, fake_rw.fake
    db = rw.settings.chat_db
    chat = chat_of(db, rw.get(f"/api/conversations/{SELF}/messages")["messages"][-1]["id"])
    _, sent_copy = fakedb.write_as_messages_app(db, chat, text="water the plants")
    _, received_copy = fakedb.write_as_messages_app(db, chat, text="water the plants")
    with closing(fakedb.connect(db)) as conn:
        conn.execute("UPDATE message SET is_from_me = 0 WHERE guid = ?", (received_copy,))

    def effect(argv):
        guid = write_tapback(db, received_copy, 2000)
        with closing(fakedb.connect(db)) as conn:
            conn.execute("UPDATE message SET is_from_me = 0 WHERE guid = ?", (guid,))

    fake.effects["tapback"] = effect
    fake.answers["tapback"] = Outcome(0, "chosen-action|shown t0", "")
    assert rw.client.post(tapback_url(SELF, sent_copy), json={"kind": "love", "remove": False}).status_code == 202
    assert wait_for(rw, ui_action("tapback", messageId=sent_copy))["result"] == "done"
    assert rw.services.messages_ui.copies_of(SELF, sent_copy) == [sent_copy, received_copy]
    assert rw.services.messages_ui.copies_of(SOCCER, rw.guid("soc_root")) == [rw.guid("soc_root")]
