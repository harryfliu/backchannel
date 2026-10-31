"""Server settings and the experimental "mark read in Messages" (Accessibility) job.

osascript is never run here: every test injects a fake runner.
"""

from __future__ import annotations

import re
import threading
import time
from contextlib import closing
from urllib.parse import quote

import pytest

from server import messages_ui
from server.messages_ui import (INSPECT_SCRIPT, MARK_READ_SCRIPT, TIMEOUT, Job, Outcome, candidate_names, interpret,
                                outcome_code)
from server.state import State
from ts_shapes import validate

JORDAN = "dm:+14155550134"
MARCUS = "dm:+14155550161"
FAMILY = "g:chat100000000000000001"
DENIED_STDERR = "execution error: System Events got an error: osascript is not allowed assistive access. (-25211)"


class FakeRunner:
    """Stands in for osascript: records calls, can block, can clear unread rows like Messages would."""

    def __init__(self, stdout: str = "ok-menu", *, returncode: int = 0, stderr: str = "", timed_out: bool = False,
                 clears: str | None = None, gate: threading.Event | None = None):
        self.outcome = Outcome(returncode, stdout, stderr, timed_out)
        self.clears = clears  # chat.db path whose unread rows "Messages" marks read
        self.gate = gate
        self.calls: list[tuple[tuple[str, ...], list[str], float]] = []
        self.started = threading.Event()

    def __call__(self, script, argv, timeout):
        self.calls.append((script, list(argv), timeout))
        self.started.set()
        if self.gate is not None:
            assert self.gate.wait(10)
        if self.clears:
            from server import fakedb
            with closing(fakedb.connect(self.clears)) as conn:
                conn.execute("UPDATE message SET is_read = 1, date_read = 1 WHERE is_from_me = 0 AND is_read = 0")
        return self.outcome


def harness(make_rw, runner, *, enabled: bool = True):
    rw = make_rw(messages_ui_runner=runner)
    rw.services.messages_ui.check_after = 0.05
    if enabled:
        assert rw.client.patch("/api/settings", json={"markReadInMessages": True}).status_code == 200
    if isinstance(runner, FakeRunner) and runner.clears == "auto":
        runner.clears = str(rw.settings.chat_db)
    return rw


def read(rw, conv_id: str):
    return rw.post(f"/api/conversations/{quote(conv_id, safe='')}/read")


def ui_health(rw) -> dict:
    return rw.get("/api/health")["messagesUi"]


# --------------------------------------------------------------------------- settings


def test_settings_default_patch_and_persist(rw):
    assert rw.get("/api/settings") == {"markReadInMessages": False}
    # types.ts has since grown two settings for the next (not yet built) Messages automation round.
    not_built_yet = {"ServerSettings.tapbacksViaMessages: missing", "ServerSettings.threadRepliesViaMessages: missing"}
    assert set(validate(rw.get("/api/settings"), "ServerSettings")) <= not_built_yet
    assert ui_health(rw) == {"enabled": False, "permission": "unknown", "lastResult": None, "lastAt": None}
    response = rw.client.patch("/api/settings", json={"markReadInMessages": True, "somethingElse": 1})
    assert response.status_code == 200 and response.json() == {"markReadInMessages": True}
    assert rw.get("/api/settings") == {"markReadInMessages": True}
    assert ui_health(rw)["enabled"] is True
    assert rw.client.patch("/api/settings", json={}).json() == {"markReadInMessages": True}
    assert rw.client.patch("/api/settings", json={"markReadInMessages": "maybe"}).status_code == 400
    other = State(rw.settings.state_dir / "state.db")  # it's in state.db, not memory
    try:
        assert other.get_settings() == {"markReadInMessages": True}
    finally:
        other.close()
    assert rw.client.patch("/api/settings", json={"markReadInMessages": False}).json() == {"markReadInMessages": False}


def test_settings_need_a_session_and_json(rw):
    from fastapi.testclient import TestClient

    fresh = TestClient(rw.app, base_url="http://127.0.0.1:5556")  # no session cookie
    assert fresh.get("/api/settings").status_code == 401
    assert fresh.patch("/api/settings", json={"markReadInMessages": True}).status_code == 401
    cross = rw.client.patch("/api/settings", json={"markReadInMessages": True}, headers={"sec-fetch-site": "cross-site"})
    assert cross.status_code == 403
    form = rw.client.patch("/api/settings", content="markReadInMessages=true",
                           headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert form.status_code == 415


# --------------------------------------------------------------------------- candidate names


def test_candidate_names():
    names, loose = candidate_names("Jordan Park", full_name="Jordan Park", person_name="Jordan Park",
                                   handle="+14155550134", short_name="Jordan")
    assert names == ["Jordan Park", "(415) 555-0134", "+1 (415) 555-0134", "Jordan"] and loose == 3
    names, loose = candidate_names("Jojo", full_name="Joanna Lee", nickname="Jojo", organization="Acme Labs",
                                   person_name="Jojo", handle="jo@example.com", short_name="Jojo")
    assert names == ["Jojo", "Joanna Lee", "Acme Labs", "jo@example.com"] and loose == 4
    assert candidate_names("Liu Family 🏡") == (["Liu Family 🏡"], 1)
    job = Job("dm:x", "Jordan Park", ["Jordan Park", "Jordan"], [1], loose=1)
    assert job.argv() == ["names", "1", "Jordan Park", "Jordan"]


# --------------------------------------------------------------------------- the scripts


def _check_blocks(lines: tuple[str, ...]) -> None:
    stack: list[str] = []
    for number, line in enumerate(lines, 1):
        text = line.strip()
        if text.startswith(("end ", "end\t")) or text == "end":
            kind = text.split()[1] if len(text.split()) > 1 else ""
            assert stack, f"line {number}: {text!r} closes nothing"
            opened = stack.pop()
            if opened == "handler":
                assert kind not in ("tell", "repeat", "if", "try"), f"line {number}: {text!r} closes a handler"
            else:
                assert kind == opened, f"line {number}: {text!r} closes {opened!r}"
        elif text.startswith("on error"):
            assert stack and stack[-1] == "try", f"line {number}: 'on error' outside try"
        elif re.match(r"^on \w+", text):
            assert not stack, f"line {number}: handler inside a block"
            stack.append("handler")
        elif text == "try":
            stack.append("try")
        elif text.startswith("repeat"):
            stack.append("repeat")
        elif text.startswith("tell ") and " to " not in text:
            stack.append("tell")
        elif text.startswith("if ") and text.endswith(" then"):
            stack.append("if")
        elif text == "else" or (text.startswith("else if ") and text.endswith(" then")):
            assert stack and stack[-1] == "if", f"line {number}: else outside if"
    assert not stack, f"unclosed blocks: {stack}"


@pytest.mark.parametrize("script", [MARK_READ_SCRIPT, INSPECT_SCRIPT], ids=["mark-read", "inspect"])
def test_scripts_are_well_formed_and_harmless(script):
    _check_blocks(script)
    source = "\n".join(script).lower()
    for forbidden in ("keystroke", "do shell script", "send ", "set value of text", "set focused", "click button"):
        assert forbidden not in source, forbidden
    assert "key code" not in source or source.count("key code") == source.count("key code 53")  # Escape only
    assert "jordan" not in source  # names arrive as argv, never in the source


def test_inspect_script_never_prints_text():
    printed = [line for line in INSPECT_SCRIPT if "set end of outLines to" in line]
    for line in printed:
        assert not re.search(r"\b(description|value) of\b", line), line  # lengths only (textLength)
        assert "name of" not in line or "menu" in line, line  # menu titles only


# --------------------------------------------------------------------------- outcomes


@pytest.mark.parametrize("outcome, permission, ok, words", [
    (Outcome(0, "ok-menu", ""), "granted", True, "Marked Jordan Park read in Messages."),
    (Outcome(0, "ok-context", ""), "granted", True, "context menu"),
    (Outcome(0, "row-not-found", ""), "granted", False, "Couldn't find Jordan Park"),
    (Outcome(0, "no-mark-item", ""), "granted", False, "Mark as Read"),
    (Outcome(0, "no-window", ""), "granted", False, "no window"),
    (Outcome(0, "not-running", ""), None, False, "Messages isn't open"),
    (Outcome(1, "", DENIED_STDERR), "denied", False, "Privacy & Security › Accessibility"),
    (Outcome(1, "", "execution error: Not authorized to send Apple events to System Events. (-1743)"), "denied",
     False, "Privacy & Security › Automation"),
    (Outcome(1, "", "execution error: Can't get window 1. (-1728)"), None, False, "failed: execution error"),
    (Outcome(1, "", "System Events got an error: Messages isn't allowed (-1719)"), "denied", False, "Accessibility"),
    (Outcome(-1, "", "", timed_out=True), None, False, "didn't respond within 8 seconds"),
    (Outcome(0, "weird", ""), None, False, "unexpected"),
])
def test_interpret(outcome, permission, ok, words):
    got_permission, sentence, succeeded = interpret(outcome, "Jordan Park")
    assert (got_permission, succeeded) == (permission, ok)
    assert words in sentence


def test_outcome_codes_never_carry_text():
    assert outcome_code(Outcome(0, "ok-menu", "")) == "ok-menu"
    assert outcome_code(Outcome(0, "Jordan Park, hey", "")) == "unexpected answer"
    assert outcome_code(Outcome(1, "", DENIED_STDERR)) == "not allowed (Accessibility)"
    assert outcome_code(Outcome(1, "", "Not authorized to send Apple events to System Events. (-1743)")) == (
        "not allowed (Automation)")
    assert outcome_code(Outcome(1, "", 'Can\'t get row "Jordan Park". (-1728)')) == "failed (exit 1)"
    assert outcome_code(Outcome(-1, "", "", timed_out=True)) == "timed out"


# --------------------------------------------------------------------------- the job


def test_read_marks_it_read_in_messages(make_rw, caplog):
    runner = FakeRunner("ok-menu", clears="auto")
    rw = harness(make_rw, runner)
    with caplog.at_level("INFO", logger="backchannel.messages_ui"):
        read(rw, JORDAN)
        assert rw.services.messages_ui.wait_idle(5)
    assert len(runner.calls) == 1
    script, argv, timeout = runner.calls[0]
    assert script is MARK_READ_SCRIPT and timeout == TIMEOUT
    assert argv == ["names", "3", "Jordan Park", "(415) 555-0134", "+1 (415) 555-0134", "Jordan"]
    health = ui_health(rw)
    assert health["permission"] == "granted" and health["lastResult"] == "Marked Jordan Park read in Messages."
    assert health["lastAt"] and not validate(rw.get("/api/health"), "Health")
    assert "Jordan" not in caplog.text and "ok-menu" in caplog.text  # the log gets a code, not the name
    read(rw, JORDAN)  # Messages agrees now: nothing to do
    assert rw.services.messages_ui.wait_idle(5) and len(runner.calls) == 1


def test_reports_when_chat_db_still_shows_unread(make_rw):
    runner = FakeRunner("ok-context")  # "succeeds" but nothing changes in chat.db
    rw = harness(make_rw, runner)
    read(rw, JORDAN)
    assert rw.services.messages_ui.wait_idle(5)
    assert ui_health(rw)["lastResult"] == ("Marked Jordan Park read in Messages (from its context menu), "
                                           "but chat.db still shows unread messages there.")


def test_permission_denied_and_failures(make_rw):
    runner = FakeRunner("", returncode=1, stderr=DENIED_STDERR)
    rw = harness(make_rw, runner)
    read(rw, JORDAN)
    assert rw.services.messages_ui.wait_idle(5)
    health = ui_health(rw)
    assert health["permission"] == "denied" and "System Settings › Privacy & Security › Accessibility" in health["lastResult"]
    runner.outcome = Outcome(0, "not-running", "")
    read(rw, JORDAN)
    assert rw.services.messages_ui.wait_idle(5)
    health = ui_health(rw)
    assert health["permission"] == "denied"  # unchanged: Messages wasn't open, so we learned nothing
    assert health["lastResult"] == "Messages isn't open, so nothing was marked read."
    runner.outcome = Outcome(-1, "", "", timed_out=True)
    read(rw, JORDAN)
    assert rw.services.messages_ui.wait_idle(5)
    assert "didn't respond" in ui_health(rw)["lastResult"]
    status = State(rw.settings.state_dir / "state.db")  # remembered across restarts
    try:
        assert status.get_kv(messages_ui.STATUS_KEY)["permission"] == "denied"
    finally:
        status.close()


def test_only_when_on_and_unread(make_rw):
    off = FakeRunner()
    rw = harness(make_rw, off, enabled=False)
    read(rw, JORDAN)
    assert rw.services.messages_ui.wait_idle(5) and off.calls == []
    on = FakeRunner()
    rw = harness(make_rw, on)
    read(rw, MARCUS)  # nothing unread in chat.db
    assert rw.services.messages_ui.wait_idle(5) and on.calls == []
    assert ui_health(rw)["lastAt"] is None


def test_group_conversations_use_the_title(make_rw):
    runner = FakeRunner(clears="auto")
    rw = harness(make_rw, runner)
    read(rw, FAMILY)
    assert rw.services.messages_ui.wait_idle(5)
    assert runner.calls[0][1] == ["names", "1", "Liu Family 🏡"]


def test_never_blocks_and_coalesces(make_rw):
    gate = threading.Event()
    runner = FakeRunner(gate=gate)
    rw = harness(make_rw, runner)
    started = time.monotonic()
    read(rw, JORDAN)
    assert runner.started.wait(5)  # running, and stuck until the gate opens
    read(rw, JORDAN)  # queued once more (new messages may arrive while it runs)
    read(rw, JORDAN)  # dropped: already queued
    read(rw, FAMILY)
    read(rw, JORDAN)  # dropped
    assert time.monotonic() - started < 3  # none of these waited for Messages
    assert len(runner.calls) == 1
    gate.set()
    assert rw.services.messages_ui.wait_idle(10)
    assert [call[1][2] for call in runner.calls] == ["Jordan Park", "Jordan Park", "Liu Family 🏡"]


def test_dry_run_and_fake_db_never_get_osascript(fixture_src, tmp_path):
    from conftest import Harness

    for env in ({}, {"BACKCHANNEL_SEND_MODE": "applescript"}):
        with Harness(fixture_src, tmp_path / f"state-{len(env)}", **env) as h:
            assert h.services.messages_ui.runner is None
            h.client.patch("/api/settings", json={"markReadInMessages": True})
            assert h.services.messages_ui.request(Job(JORDAN, "Jordan Park", ["Jordan Park"], [1])) is False
    services = Harness(fixture_src, tmp_path / "state-real", BACKCHANNEL_SEND_MODE="applescript",
                       BACKCHANNEL_FAKE_DB="0").services  # the real thing: only with Messages' own chat.db
    try:
        assert services.messages_ui.runner is messages_ui.osascript_runner
    finally:
        services.close()


def test_menu_titles_hide_anything_personal():
    private = {"jordan park", "jordan", "park", "mark", "priya"}
    titles = "Delete Conversation… | Mark as Read | Mark as Unread | Call Jordan | Park Here | jo@x.example | +1 415 | Marky"
    assert messages_ui.safe_titles(titles, private) == (
        "Delete Conversation… | Mark as Read | Mark as Unread | (hidden) | (hidden) | (hidden) | (hidden) | Marky")


def test_turning_it_off_drops_queued_jobs(make_rw):
    gate = threading.Event()
    runner = FakeRunner(gate=gate)
    rw = harness(make_rw, runner)
    read(rw, JORDAN)
    assert runner.started.wait(5)
    read(rw, FAMILY)  # queued behind Jordan's
    rw.client.patch("/api/settings", json={"markReadInMessages": False})
    gate.set()
    assert rw.services.messages_ui.wait_idle(10)
    assert [call[1][2] for call in runner.calls] == ["Jordan Park"]
