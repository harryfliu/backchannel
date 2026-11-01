"""The send → confirm flow in dry-run mode (with the fake chat.db), and the AppleScript calls."""

from __future__ import annotations

import asyncio
import subprocess
import time
from pathlib import Path
from urllib.parse import quote

import pytest

from server import fakedb
from server.sender import SEND_TO_CHAT, SEND_TO_PARTICIPANT, Sender

JORDAN = "dm:+14155550134"
SOCCER = "g:chat100000000000000002"


def path(conv_id: str, suffix: str = "messages") -> str:
    return f"/api/conversations/{quote(conv_id, safe='')}/{suffix}"


def of_type(events, kind):
    return [e for e in events if e["type"] == kind]


def test_send_confirms_with_the_real_row(rw):
    response = rw.post(path(JORDAN), {"text": "  sounds fun, I'm in  ", "clientId": "c-1"})
    assert response == {"clientId": "c-1", "status": "queued"}
    events = rw.events_until(lambda e: of_type(e, "send-status"))
    status = of_type(events, "send-status")[0]
    assert status["status"] == "sent" and status["clientId"] == "c-1" and status["conversationId"] == JORDAN
    message = next(e["message"] for e in of_type(events, "message") if e["message"]["clientId"] == "c-1")
    assert message["id"] == status["messageId"] and message["fromMe"] and message["text"] == "sounds fun, I'm in"
    assert message["conversationId"] == JORDAN
    convo = next(e["conversation"] for e in of_type(events, "conversation") if e["conversation"]["id"] == JORDAN)
    assert convo["lastMessage"]["text"] == "sounds fun, I'm in" and not convo["awaitingReply"]
    # The row really is in the fixture chat.db, sent to the most recently used chat (the phone iMessage chat).
    row = rw.db().execute(
        "SELECT m.is_from_me, m.text, c.guid FROM message m JOIN chat_message_join j ON j.message_id = m.ROWID "
        "JOIN chat c ON c.ROWID = j.chat_id WHERE m.guid = ?", (status["messageId"],)).fetchone()
    assert row[0] == 1 and row[1] is None and row[2] == "iMessage;-;+14155550134"
    # Later fetches still carry the clientId; fake delivery/read receipts arrive as updates.
    updates = rw.events_until(lambda e: any(x["type"] == "message-updated" and x["message"]["status"] == "read" for x in e))
    assert any(u["message"]["id"] == status["messageId"] for u in of_type(updates, "message-updated"))
    assert rw.all_messages(JORDAN, limit=60)[-1]["clientId"] == "c-1"


def test_same_client_id_is_not_sent_twice(rw):
    rw.post(path(JORDAN), {"text": "only once", "clientId": "dup"})
    rw.post(path(JORDAN), {"text": "only once", "clientId": "dup"})
    rw.events_until(lambda e: of_type(e, "send-status"))
    time.sleep(1.0)
    rw.services.watcher.check()
    count = rw.db().execute("SELECT COUNT(*) FROM message WHERE is_from_me = 1 AND instr(attributedBody, ?) > 0",
                            ("only once".encode(),)).fetchone()[0]
    assert count == 1


def test_thread_reply_is_remembered_locally(rw):
    root = rw.guid("soc_root")
    rw.post(path(SOCCER), {"text": "in! bringing the pump", "clientId": "t-1", "threadRootId": root})
    events = rw.events_until(lambda e: of_type(e, "send-status"))
    message = next(e["message"] for e in of_type(events, "message") if e["message"]["clientId"] == "t-1")
    assert message["threadRootId"] == root
    updated = [e["message"] for e in of_type(events, "message-updated") if e["message"]["id"] == root]
    assert updated and updated[0]["thread"]["replyCount"] == 6 and updated[0]["thread"]["participants"][0] == "me"
    thread = rw.get(f"/api/conversations/{quote(SOCCER, safe='')}/threads/{root}")
    assert thread["replies"][-1]["text"] == "in! bringing the pump"


def test_file_send(rw, tmp_path):
    upload = tmp_path / "plan.txt"
    upload.write_text("the plan")
    with upload.open("rb") as f:
        response = rw.client.post(path("dm:+14155550178", "files"), files={"file": ("plan.txt", f, "text/plain")},
                                  data={"clientId": "f-1"})
    assert response.status_code == 200 and response.json() == {"clientId": "f-1", "status": "queued"}
    events = rw.events_until(lambda e: of_type(e, "send-status"))
    message = next(e["message"] for e in of_type(events, "message") if e["message"]["clientId"] == "f-1")
    assert [a["name"] for a in message["attachments"]] == ["plan.txt"]
    att = message["attachments"][0]
    assert not att["missing"]
    assert rw.client.get(att["url"]).content == b"the plan"


def test_rejects_empty_and_bad_requests(rw):
    assert rw.client.post(path(JORDAN), json={"text": "   ", "clientId": "x"}).status_code == 400
    assert rw.client.post(path(JORDAN), json={"text": "hi"}).status_code == 400
    assert rw.client.post(path(JORDAN), json={"text": "hi", "clientId": "x", "sendAt": "tomorrow"}).status_code == 400
    assert rw.client.post(path("dm:+19999999999"), json={"text": "hi", "clientId": "x"}).status_code == 404


def test_scheduled_send(rw):
    future = "2031-01-01T09:00:00-08:00"
    response = rw.post(path("dm:+16285550190"), {"text": "happy new year!!", "clientId": "s-1", "sendAt": future})
    assert response["status"] == "scheduled" and response["scheduledId"].startswith("sch_")
    items = rw.get("/api/scheduled")["items"]
    assert items[0]["status"] == "pending" and items[0]["text"] == "happy new year!!"
    assert rw.services.hub.history[-1]["type"] == "scheduled"
    # Make it due and run the scheduler once.
    rw.services.state._exec("UPDATE scheduled SET send_at = ? WHERE id = ?", (time.time() - 1, response["scheduledId"]))
    asyncio.run(rw.services.sending.run_due())
    item = rw.get("/api/scheduled")["items"][0]
    assert item["status"] == "sent"
    scheduled_events = [e for e in rw.services.hub.history if e["type"] == "scheduled"]
    assert scheduled_events[-1]["scheduled"]["status"] == "sent"


def test_cancel_scheduled(rw):
    response = rw.post(path("dm:+16285550190"), {"text": "later", "clientId": "s-2", "sendAt": "2031-01-01T09:00:00Z"})
    assert rw.client.delete(f"/api/scheduled/{response['scheduledId']}").status_code == 204
    assert rw.get("/api/scheduled")["items"][0]["status"] == "cancelled"
    assert rw.client.delete("/api/scheduled/sch_nope").status_code == 404


def test_overdue_scheduled_sends_fail_instead_of_sending_late(rw):
    row = rw.services.state.add_scheduled("dm:+16285550190", "old news", time.time() - 3600, "s-3", None)
    asyncio.run(rw.services.sending.run_due())
    assert rw.services.state.get_scheduled(row["id"])["status"] == "failed"


def test_plain_dry_run_reports_sent_without_an_id(make_rw):
    harness = make_rw(BACKCHANNEL_FAKE_DB="0")
    harness.post(path(JORDAN), {"text": "hello", "clientId": "p-1"})
    deadline = time.time() + 5
    while time.time() < deadline and not any(e["type"] == "send-status" for e in harness.services.hub.history):
        time.sleep(0.05)
    status = next(e for e in harness.services.hub.history if e["type"] == "send-status")
    assert status["status"] == "sent" and "messageId" not in status


def test_fake_db_refuses_the_real_messages_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    real = tmp_path / "Library" / "Messages"
    real.mkdir(parents=True)
    with pytest.raises(fakedb.RefusedError):
        fakedb.guard_path(real / "chat.db")
    with pytest.raises(fakedb.RefusedError):
        fakedb.connect(real / "sub" / ".." / "chat.db")
    fakedb.guard_path(tmp_path / "elsewhere" / "chat.db")  # fine


def test_applescript_gets_text_as_argv_never_in_the_script(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 1 if len(calls) == 1 else 0, "", "execution error: Can’t get chat id (-1728)")

    monkeypatch.setattr(subprocess, "run", fake_run)
    sender = Sender("applescript")
    sender.osascript = "/usr/bin/osascript"  # pretend; subprocess.run is mocked
    evil = 'hi" & (do shell script "rm -rf ~") & "'
    result = sender.send_text("iMessage;-;+14155550134", evil, dm_handle="+14155550134")
    assert result.ok
    first, second = calls
    script_lines = [first[i + 1] for i, part in enumerate(first) if part == "-e"]
    assert script_lines == list(SEND_TO_CHAT)
    assert first[-2:] == ["iMessage;-;+14155550134", evil]  # argv, after the script
    assert all(evil not in line for line in script_lines)
    assert [second[i + 1] for i, p in enumerate(second) if p == "-e"] == list(SEND_TO_PARTICIPANT)
    assert second[-3:] == ["+14155550134", evil, "iMessage"]


def test_applescript_errors_are_explained(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 1, "", "execution error: Not authorized to send Apple events to Messages. (-1743)"))
    sender = Sender("applescript")
    sender.osascript = "/usr/bin/osascript"
    result = sender.send_text("iMessage;+;chat1", "hi")
    assert not result.ok and "Automation" in result.error


def test_osascript_missing_means_unavailable():
    sender = Sender("applescript")
    sender.osascript = None
    assert sender.availability == "unavailable"
    assert not sender.send_text("iMessage;-;+1", "hi").ok


def test_files_are_staged_under_attachments_backchannel(tmp_path):
    sender = Sender("applescript", messages_attachments_dir=tmp_path / "Attachments")
    src = tmp_path / "upload.bin"
    src.write_bytes(b"x")
    staged = sender.stage_file(src, "../../evil name?.pdf")
    assert staged.parent.parent == tmp_path / "Attachments" / "Backchannel"
    assert staged.name == "evil name_.pdf" and staged.read_bytes() == b"x"
    assert isinstance(staged, Path)


def _fake_messages(monkeypatch, *, loads: bool):
    """subprocess.run as Messages would answer for a group it hasn't loaded (-1728) until its link is opened."""
    import server.sender as sender_mod

    calls, state = [], {"opened": False}

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[0] == "open":
            state["opened"] = True
            return subprocess.CompletedProcess(cmd, 0, "", "")
        lines = [cmd[i + 1] for i, p in enumerate(cmd) if p == "-e"]
        if lines == list(sender_mod.CHAT_LOADED):
            return subprocess.CompletedProcess(cmd, 0, "true\n" if state["opened"] and loads else "false\n", "")
        if state["opened"] and loads:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 1, "", "execution error: Messages got an error: Can’t get chat id \"x\". (-1728)")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(sender_mod.time, "sleep", lambda s: None)
    sender = Sender("applescript")
    sender.osascript = "/usr/bin/osascript"
    return sender, calls


def test_an_older_group_messages_hasnt_loaded_is_opened_with_its_link_then_sent(monkeypatch):
    """Harry's Mac: Messages keeps ~215 recent conversations ready for scripting; a group last active two
    months ago answered "can't get chat id" (-1728) until it was opened."""
    import server.sender as sender_mod

    sender, calls = _fake_messages(monkeypatch, loads=True)
    members = ["+14155550134", "grace@example.com"]
    result = sender.send_text("any;+;chat42", "congrats again", members=members)
    assert result.ok
    sends = [c for c in calls if c[0] != "open" and [c[i + 1] for i, p in enumerate(c) if p == "-e"] == list(SEND_TO_CHAT)]
    assert len(sends) == 2 and all(c[-2:] == ["any;+;chat42", "congrats again"] for c in sends)  # same chat both times
    opened = [c for c in calls if c[0] == "open"]
    assert opened == [["open", "-g", "imessage://open?addresses=%2B14155550134,grace@example.com"]]
    positions = [i for i, c in enumerate(calls) if c in sends]
    assert positions[0] < calls.index(opened[0]) < positions[1]  # opened between the two tries
    assert sender_mod.members_link(["+1 415 555", "a@b.co"]) is None  # only plain numbers and addresses
    assert sender_mod.members_link(["x@y.com&body=hi"]) is None and sender_mod.members_link([]) is None


def test_a_group_that_still_wont_load_fails_without_a_second_send(monkeypatch):
    sender, calls = _fake_messages(monkeypatch, loads=False)
    result = sender.send_text("any;+;chat42", "hi", members=["+14155550134"])
    assert not result.ok and "couldn't find that conversation" in result.error
    sends = [c for c in calls if c[0] != "open" and [c[i + 1] for i, p in enumerate(c) if p == "-e"] == list(SEND_TO_CHAT)]
    assert len(sends) == 1
    file_result = sender.send_file("any;+;chat42", Path("/tmp/x.jpg"), members=["+14155550134"])
    assert not file_result.ok
    no_members, calls2 = _fake_messages(monkeypatch, loads=True)
    assert not no_members.send_text("any;+;chat42", "hi").ok and not any(c[0] == "open" for c in calls2)  # DMs etc.
