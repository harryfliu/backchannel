"""POST /api/conversations: texting a number or email."""

from __future__ import annotations

import subprocess
import time

import pytest

from server.handles import parse_handle
from server.sender import SEND_TO_BUDDY, SEND_TO_PARTICIPANT, Sender
from ts_shapes import validate


def start(harness, handle, text="hey! it's harry", client_id="n-1", status=200):
    response = harness.client.post("/api/conversations", json={"handle": handle, "text": text, "clientId": client_id})
    assert response.status_code == status, response.text
    return response.json()


def of_type(events, kind):
    return [e for e in events if e["type"] == kind]


def test_new_number_creates_a_conversation(rw):
    assert start(rw, "(415) 555-0177") == {"conversationId": "dm:+14155550177"}
    events = rw.events_until(lambda e: of_type(e, "send-status"))
    kinds = [e["type"] for e in events]
    assert kinds.index("conversation") < kinds.index("message") < kinds.index("send-status")
    convo = next(e["conversation"] for e in events if e["type"] == "conversation"
                 and e["conversation"]["id"] == "dm:+14155550177")
    assert convo["title"] == "(415) 555-0177" and convo["lastMessage"]["text"] == "hey! it's harry"
    message = of_type(events, "message")[0]["message"]
    assert message["clientId"] == "n-1" and message["conversationId"] == "dm:+14155550177" and message["fromMe"]
    status = of_type(events, "send-status")[0]
    assert status == {"type": "send-status", "clientId": "n-1", "conversationId": "dm:+14155550177",
                      "status": "sent", "messageId": message["id"]}
    for event in events:
        assert not validate(event, "ServerEvent", allow_extra={"stickerUrl"})
    # Messages (faked) created the handle and the 1:1 chat.
    chat = rw.db().execute("SELECT style, chat_identifier, service_name FROM chat WHERE guid = 'iMessage;-;+14155550177'").fetchone()
    assert tuple(chat) == (45, "+14155550177", "iMessage")
    assert "dm:+14155550177" in rw.conversations()


def test_new_email(rw):
    assert start(rw, "  New.Person@Example.COM ", client_id="n-2") == {"conversationId": "dm:new.person@example.com"}
    events = rw.events_until(lambda e: of_type(e, "send-status"))
    assert of_type(events, "send-status")[0]["status"] == "sent"


def test_existing_person_reuses_their_conversation(rw):
    assert start(rw, "+1 415 555 0134", client_id="n-3") == {"conversationId": "dm:+14155550134"}
    assert start(rw, "JORDAN.PARK@icloud.example", text="also", client_id="n-4") == {"conversationId": "dm:+14155550134"}
    events = rw.events_until(lambda e: len(of_type(e, "send-status")) == 2)
    assert {s["conversationId"] for s in of_type(events, "send-status")} == {"dm:+14155550134"}
    assert rw.db().execute("SELECT COUNT(*) FROM chat").fetchone()[0] == 24  # no new chat rows


def test_contact_without_a_chat_yet(rw):
    # Kevin is a contact who has only been in the soccer group, never in a 1:1 chat.
    assert start(rw, "(415) 555-0126", client_id="n-5") == {"conversationId": "dm:+14155550126"}
    events = rw.events_until(lambda e: of_type(e, "send-status"))
    convo = next(e["conversation"] for e in events if e["type"] == "conversation" and e["conversation"]["id"] == "dm:+14155550126")
    assert convo["title"] == "Kevin Huang"


@pytest.mark.parametrize("handle, error", [("555-0134", "area code"), ("hello", "phone number or email"),
                                           ("a@b", "email"), ("", "phone number or email")])
def test_bad_handles(rw, handle, error):
    response = rw.client.post("/api/conversations", json={"handle": handle, "text": "hi", "clientId": "x"})
    assert response.status_code == 400 and error in response.json()["error"]


def test_bad_requests(rw):
    assert rw.client.post("/api/conversations", json={"handle": "+14155550177", "text": "  ", "clientId": "x"}).status_code == 400
    assert rw.client.post("/api/conversations", json={"handle": "+14155550177", "text": "hi"}).status_code == 400


def test_same_client_id_is_sent_once(rw):
    start(rw, "+14155550177", client_id="dup")
    start(rw, "+14155550177", client_id="dup")
    rw.events_until(lambda e: of_type(e, "send-status"))
    time.sleep(0.8)
    rw.services.watcher.check()
    assert rw.db().execute("SELECT COUNT(*) FROM chat WHERE chat_identifier = '+14155550177'").fetchone()[0] == 1
    count = rw.db().execute("SELECT COUNT(*) FROM message WHERE is_from_me = 1 AND instr(attributedBody, ?) > 0",
                            ("hey! it's harry".encode(),)).fetchone()[0]
    assert count == 1


def test_plain_dry_run(make_rw):
    harness = make_rw(BACKCHANNEL_FAKE_DB="0")
    start(harness, "+14155550177", client_id="p-1")
    deadline = time.time() + 5
    while time.time() < deadline and not of_type(harness.services.hub.history, "send-status"):
        time.sleep(0.05)
    status = of_type(harness.services.hub.history, "send-status")[0]
    assert status["status"] == "sent" and "messageId" not in status


def test_parse_handle():
    assert parse_handle("(415) 555-0134") == "+14155550134"
    assert parse_handle("+44 20 7946 0958") == "+442079460958"
    assert parse_handle("24273") == "24273"
    assert parse_handle("mailto:Harry@Example.com") == "harry@example.com"


def fake_osascript(monkeypatch, fail_first: int):
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        code = 1 if len(calls) <= fail_first else 0
        return subprocess.CompletedProcess(cmd, code, "", "execution error: Can’t get participant (-1728)")

    monkeypatch.setattr(subprocess, "run", run)
    sender = Sender("applescript")
    sender.osascript = "/usr/bin/osascript"  # subprocess.run is mocked; nothing runs
    return sender, calls


def script(cmd):
    return [cmd[i + 1] for i, part in enumerate(cmd) if part == "-e"]


def test_participant_send_then_sms_fallback(monkeypatch):
    sender, calls = fake_osascript(monkeypatch, fail_first=1)
    evil = 'hi" & (do shell script "id") & "'
    assert sender.send_to_handle("+14155550177", evil).ok
    assert [script(c) for c in calls] == [list(SEND_TO_PARTICIPANT)] * 2
    assert calls[0][-3:] == ["+14155550177", evil, "iMessage"]
    assert calls[1][-3:] == ["+14155550177", evil, "SMS"]
    assert all(evil not in line for c in calls for line in script(c))


def test_email_never_falls_back_to_sms(monkeypatch):
    sender, calls = fake_osascript(monkeypatch, fail_first=1)
    assert sender.send_to_handle("new.person@example.com", "hi").ok
    assert [script(c) for c in calls] == [list(SEND_TO_PARTICIPANT), list(SEND_TO_BUDDY)]
    assert calls[1][-2:] == ["new.person@example.com", "hi"]
