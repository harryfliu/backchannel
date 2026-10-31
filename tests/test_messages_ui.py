"""Server settings, the permission probe, the helper scripts and "mark read in Messages".

osascript is never run here: every test injects a fake runner (tests/fake_messages.py).
"""

from __future__ import annotations

import re
import threading
import time
from urllib.parse import quote

import pytest

from fake_messages import FakeMessages, clear_unread
from server import messages_scripts, messages_ui, sender
from server.messages_ui import (AUTOMATION_TEXT, DENIED_TEXT, INSPECT_SCRIPT, MARK_READ_SCRIPT, MARK_UNREAD_SCRIPT,
                                PROBE_SCRIPT, REPLY_SCRIPT, TAPBACK_SCRIPT, TIMEOUT, Job, Outcome, candidate_names,
                                interpret, outcome_code)
from server.state import State
from ts_shapes import validate

JORDAN = "dm:+14155550134"
MARCUS = "dm:+14155550161"
FAMILY = "g:chat100000000000000001"
DENIED_STDERR = "execution error: System Events got an error: osascript is not allowed assistive access. (-25211)"
AUTOMATION_STDERR = "execution error: Not authorized to send Apple events to System Events. (-1743)"
OFF = {"markReadInMessages": False, "tapbacksViaMessages": False, "threadRepliesViaMessages": False}


def harness(make_rw, fake: FakeMessages, *, enabled: bool = True, clears: bool = False):
    rw = make_rw(messages_ui_runner=fake)
    rw.services.messages_ui.check_after = 0.05
    if clears:
        fake.effects["mark-read"] = lambda argv: clear_unread(rw.settings.chat_db)
    if enabled:
        assert rw.client.patch("/api/settings", json={"markReadInMessages": True}).status_code == 200
        assert rw.services.messages_ui.wait_idle(5)  # the permission probe
    return rw


def read(rw, conv_id: str):
    return rw.post(f"/api/conversations/{quote(conv_id, safe='')}/read")


def ui_health(rw) -> dict:
    return rw.get("/api/health")["messagesUi"]


def ui_actions(rw) -> list[dict]:
    return [e for e in rw.services.hub.history if e["type"] == "ui-action"]


# --------------------------------------------------------------------------- settings


def test_settings_default_patch_and_persist(rw):
    assert rw.get("/api/settings") == OFF
    assert not validate(rw.get("/api/settings"), "ServerSettings")
    assert ui_health(rw) == {"enabled": False, "permission": "unknown", "lastResult": None, "lastAt": None}
    response = rw.client.patch("/api/settings", json={"tapbacksViaMessages": True, "somethingElse": 1})
    assert response.status_code == 200 and response.json() == {**OFF, "tapbacksViaMessages": True}
    assert ui_health(rw)["enabled"] is True  # any helper counts
    assert rw.client.patch("/api/settings", json={"threadRepliesViaMessages": True}).json() == {
        **OFF, "tapbacksViaMessages": True, "threadRepliesViaMessages": True}
    assert rw.client.patch("/api/settings", json={}).json()["tapbacksViaMessages"] is True
    assert rw.client.patch("/api/settings", json={"markReadInMessages": "maybe"}).status_code == 400
    other = State(rw.settings.state_dir / "state.db")  # it's in state.db, not memory
    try:
        assert other.get_settings() == {**OFF, "tapbacksViaMessages": True, "threadRepliesViaMessages": True}
    finally:
        other.close()
    assert rw.client.patch("/api/settings", json={"tapbacksViaMessages": False, "threadRepliesViaMessages": False,
                                                  "markReadInMessages": True}).json() == {**OFF, "markReadInMessages": True}
    assert ui_health(rw)["enabled"] is True
    rw.client.patch("/api/settings", json={"markReadInMessages": False})
    assert ui_health(rw)["enabled"] is False


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


# --------------------------------------------------------------------------- the permission probe


def test_probe_runs_when_a_helper_is_turned_on(make_rw):
    fake = FakeMessages()
    rw = make_rw(messages_ui_runner=fake)
    ui = rw.services.messages_ui
    rw.client.patch("/api/settings", json={"tapbacksViaMessages": True})
    assert ui.wait_idle(5) and fake.probes == 1
    rw.client.patch("/api/settings", json={"tapbacksViaMessages": True})  # on → on
    rw.client.patch("/api/settings", json={"tapbacksViaMessages": False})  # turning off
    assert ui.wait_idle(5) and fake.probes == 1
    rw.client.patch("/api/settings", json={"threadRepliesViaMessages": True})  # another one off → on
    assert ui.wait_idle(5) and fake.probes == 2
    assert fake.calls == []  # it never touched Messages
    health = ui_health(rw)
    assert health["lastResult"] == "Ready" and health["permission"] == "unknown"  # Automation isn't Accessibility


@pytest.mark.parametrize("outcome, permission, result", [
    (Outcome(1, "", AUTOMATION_STDERR), "denied", AUTOMATION_TEXT),
    (Outcome(1, "", DENIED_STDERR), "denied", DENIED_TEXT),
    (Outcome(0, "ready", ""), "unknown", "Ready"),
    (Outcome(-1, "", "", timed_out=True), "unknown", "macOS didn't answer within 90 seconds. If it asks whether "
                                                      "Backchannel may control System Events, click OK."),
])
def test_probe_results(make_rw, outcome, permission, result):
    fake = FakeMessages(probe=outcome)
    rw = make_rw(messages_ui_runner=fake)
    rw.client.patch("/api/settings", json={"markReadInMessages": True})
    assert rw.services.messages_ui.wait_idle(5)
    health = ui_health(rw)
    assert (health["permission"], health["lastResult"]) == (permission, result)


def test_probe_clears_an_automation_denial_but_never_grants(make_rw):
    fake = FakeMessages(probe=Outcome(1, "", AUTOMATION_STDERR))
    rw = make_rw(messages_ui_runner=fake)
    rw.client.patch("/api/settings", json={"markReadInMessages": True})
    assert rw.services.messages_ui.wait_idle(5) and ui_health(rw)["permission"] == "denied"
    fake.answers["probe"] = Outcome(0, "ready", "")  # Harry allowed it in System Settings
    rw.client.patch("/api/settings", json={"markReadInMessages": False})
    rw.client.patch("/api/settings", json={"markReadInMessages": True})
    assert rw.services.messages_ui.wait_idle(5)
    assert ui_health(rw)["permission"] == "unknown" and ui_health(rw)["lastResult"] == "Ready"


def test_no_probe_without_the_real_runner(rw):
    rw.client.patch("/api/settings", json={"tapbacksViaMessages": True, "markReadInMessages": True})
    assert rw.services.messages_ui.wait_idle(5)
    assert ui_health(rw)["lastAt"] is None  # dry-run + fake DB: nothing asked of macOS


# --------------------------------------------------------------------------- candidate names


def test_candidate_names():
    names, loose = candidate_names("Jordan Park", full_name="Jordan Park", person_name="Jordan Park",
                                   handle="+14155550134", short_name="Jordan")
    assert names == ["Jordan Park", "(415) 555-0134", "+1 (415) 555-0134", "Jordan", "Jordan P."] and loose == 3
    names, loose = candidate_names("Jojo", full_name="Joanna Lee", nickname="Jojo", organization="Acme Labs",
                                   person_name="Jojo", handle="jo@example.com", short_name="Jojo")
    assert names == ["Jojo", "Joanna Lee", "Acme Labs", "jo@example.com", "Joanna L."] and loose == 4
    assert candidate_names("Liu Family 🏡") == (["Liu Family 🏡"], 1)
    job = Job("dm:x", "Jordan Park", ["Jordan Park", "Jordan"], [1], loose=1)
    assert job.argv() == ["mark-read", "L:Jordan Park", "N:Jordan"]


# --------------------------------------------------------------------------- the scripts

SCRIPTS = {"mark-read": MARK_READ_SCRIPT, "tapback": TAPBACK_SCRIPT, "reply": REPLY_SCRIPT, "inspect": INSPECT_SCRIPT,
           "probe": PROBE_SCRIPT, "mark-unread": MARK_UNREAD_SCRIPT}
KEYS = {"mark-read": {"key code 53"}, "tapback": {"key code 53"}, "inspect": {"key code 53"}, "probe": set(),
        "mark-unread": {"key code 53"},
        "reply": {"key code 53", "key code 36", "key code 9 using command down"}}


def _check_blocks(lines: tuple[str, ...]) -> None:
    stack: list[str] = []
    for number, line in enumerate(lines, 1):
        text = line.strip()
        if text.startswith(("end ", "end\t")) or text == "end":
            kind = text.split()[1] if len(text.split()) > 1 else ""
            assert stack, f"line {number}: {text!r} closes nothing"
            opened = stack.pop()
            if opened == "handler":
                assert kind not in ("tell", "repeat", "if", "try", "considering"), f"line {number}: {text!r} closes a handler"
            else:
                assert kind == opened, f"line {number}: {text!r} closes {opened!r}"
        elif text.startswith("on error"):
            assert stack and stack[-1] == "try", f"line {number}: 'on error' outside try"
        elif re.match(r"^on \w+", text):
            assert not stack, f"line {number}: handler inside a block"
            stack.append("handler")
        elif text == "try":
            stack.append("try")
        elif text.startswith("considering "):
            stack.append("considering")
        elif text.startswith("repeat"):
            stack.append("repeat")
        elif text.startswith("tell ") and " to " not in text:
            stack.append("tell")
        elif text.startswith("if ") and text.endswith(" then"):
            stack.append("if")
        elif text == "else" or (text.startswith("else if ") and text.endswith(" then")):
            assert stack and stack[-1] == "if", f"line {number}: else outside if"
    assert not stack, f"unclosed blocks: {stack}"


@pytest.mark.parametrize("name", sorted(SCRIPTS))
def test_scripts_are_well_formed_and_harmless(name):
    script = SCRIPTS[name]
    _check_blocks(script)
    source = "\n".join(script)
    defined = set(re.findall(r"^on (\w+)\(", source, re.M))
    assert set(re.findall(r"\bmy (\w+)\(", source)) <= defined  # every handler it calls is there
    lowered = source.lower()
    for forbidden in ("keystroke", "do shell script", "send ", "set value of text", "click button", "delete ",
                      "activate"):
        assert forbidden not in lowered, (name, forbidden)
    assert set(re.findall(r"key code \d+(?: using command down)?", lowered)) <= KEYS[name]
    for user_text in ("jordan", "hike", "harry", "+1415"):
        assert user_text not in lowered  # names and text arrive as argv, never in the source


SENDER_SCRIPTS = {name: getattr(sender, name) for name in
                  ("SEND_TO_CHAT", "SEND_TO_PARTICIPANT", "SEND_TO_BUDDY", "SEND_FILE_TO_CHAT", "SEND_FILE_TO_PARTICIPANT")}
LOCAL_NAMES = (re.compile(r"\bset (\w+) to\b"), re.compile(r"\brepeat with (\w+) (?:in|from)\b"),
               re.compile(r"\bon error (\w+) number (\w+)"), re.compile(r"^property (\w+) :"))


def _locals(script: tuple[str, ...]) -> set[str]:
    names: set[str] = set()
    for line in script:
        for pattern in LOCAL_NAMES:
            for match in pattern.finditer(line):
                names.update(match.groups())
        params = re.match(r"^on (?!error\b)\w+\(([^)]*)\)", line)
        if params:
            names.update(p.strip() for p in params.group(1).split(",") if p.strip())
        run = re.match(r"^on run (\w+)$", line)
        if run:
            names.add(run.group(1))
    return names


@pytest.mark.parametrize("name", sorted(SCRIPTS) + sorted(SENDER_SCRIPTS))
def test_script_locals_are_bc_prefixed(name):
    """Harry's Mac: a local called `outLines` was System Events' `outlines` class (names are
    case-insensitive and the app's terms win inside its tell), so every write failed with -10006.
    Every multi-letter variable and parameter is `bc…`; single letters and argv are fine."""
    script = SCRIPTS.get(name) or SENDER_SCRIPTS[name]
    names = _locals(script)
    assert names, name
    bad = sorted(n for n in names if len(n) > 1 and n != "argv" and not re.match(r"^bc[A-Z]", n))
    assert not bad, (name, bad)


@pytest.mark.parametrize("name", sorted(SCRIPTS))
def test_handlers_are_called_with_my(name):
    """Handler names may collide with System Events terms too; `my` always means ours."""
    lines = SCRIPTS[name]
    defined = set(re.findall(r"^on (\w+)\(", "\n".join(lines), re.M))
    for line in lines:
        for match in re.finditer(r"(\w+)\(", line):
            if match.group(1) in defined and not line.startswith(f"on {match.group(1)}("):
                assert line[:match.start()].endswith("my "), (name, line)


@pytest.mark.parametrize("name", sorted(SCRIPTS))
def test_tells_are_one_line_gets_or_actions(name):
    """No multi-line tell blocks; inside a tell only a plain get into a local or one action:
    strings, lists and coercions are built outside, where System Events' terms can't interfere."""
    for line in SCRIPTS[name]:
        assert not (line.startswith("tell ") and " to " not in line), (name, line)
        assert line != "end tell", (name, line)
        if line.startswith("tell "):
            inside = line.split(" to ", 1)[1]
            assert not re.search(r"&|\bas (text|number|integer)\b|\btab\b|\blinefeed\b|\{", inside), (name, line)


def test_on_error_branches_cant_fail_again():
    """An `on error` branch only records: returns a tagged string, sets a flag or the error number,
    or leaves the loop. Nothing in it talks to System Events."""
    simple = re.compile(r'^(return ("[\w?-]*"|"[\w-]+:" & \(bcErrNum as text\)|-?\d+|true|false|missing value)'
                        r'|exit repeat'
                        r'|set bc\w+ to ("[^"]*"|-?\d+|true|false|bcErrNum|"[\w-]+:" & \(bcErrNum as text\)))$')
    for name, script in SCRIPTS.items():
        lines = list(script)
        for i, line in enumerate(lines):
            if not line.startswith("on error"):
                continue
            for follow in lines[i + 1:]:
                if follow == "end try":
                    break
                assert simple.match(follow), (name, follow)


def test_only_the_reply_script_puts_text_into_messages():
    for name, script in SCRIPTS.items():
        source = "\n".join(script)
        writes = re.findall(r"set value of (\w+) to", source)
        attribute_writes = re.findall(r'set value of attribute "(\w+)" of (\w+) to', source)
        clipboard = "set the clipboard to" in source
        if name == "reply":
            assert writes == ["bcBox"] and clipboard and attribute_writes == []
        else:
            assert writes == [] and attribute_writes == [] and not clipboard, name


def test_return_is_only_pressed_on_our_own_text():
    act = messages_scripts.REPLY_ACT
    before_return = act[:act.index("key code 36")]
    assert "if not bcReady then return" in before_return  # the focused field must hold exactly the reply
    assert before_return.rindex("considering case") > before_return.rindex("set bcFocused to my focusedTextField()")


def test_inspect_script_never_prints_text():
    printed = [line for line in INSPECT_SCRIPT
               if re.search(r"set (bcReport|bcOut) to (bcReport & |bcOut & )?\{", line) or line.startswith('return "')
               or re.search(r"joinWith\(bcReport", line)]
    assert len(printed) >= 10  # sanity: we really found the report lines
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
    (Outcome(0, "error-late:-25211", ""), "denied", False, "Privacy & Security › Accessibility"),
    (Outcome(0, "error-late\t-25211", ""), "denied", False, "Privacy & Security › Accessibility"),
    (Outcome(1, "", AUTOMATION_STDERR), "denied", False, "Privacy & Security › Automation"),
    (Outcome(0, "error-late:-1743", ""), "denied", False, "Privacy & Security › Automation"),
    (Outcome(1, "", "execution error: Can't get window 1. (-1728)"), None, False, "failed: execution error"),
    (Outcome(0, "error-late:-1728", ""), None, False, "failed (error -1728)"),
    (Outcome(0, "error-late:unknown", ""), None, False, "failed (error None)"),
    (Outcome(1, "", "System Events got an error: Messages isn't allowed (-1719)"), "denied", False, "Accessibility"),
    (Outcome(-1, "", "", timed_out=True), None, False, "didn't respond within 30 seconds"),
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
    assert outcome_code(Outcome(1, "", AUTOMATION_STDERR)) == "not allowed (Automation)"
    assert outcome_code(Outcome(1, "", 'Can\'t get row "Jordan Park". (-1728)')) == "failed (exit 1)"
    assert outcome_code(Outcome(0, "error:-1728", "")) == "error -1728"
    assert outcome_code(Outcome(-1, "", "", timed_out=True)) == "timed out"


# --------------------------------------------------------------------------- mark read


def test_read_marks_it_read_in_messages(make_rw, caplog):
    fake = FakeMessages()
    rw = harness(make_rw, fake, clears=True)
    with caplog.at_level("INFO", logger="backchannel.messages_ui"):
        read(rw, JORDAN)
        assert rw.services.messages_ui.wait_idle(5)
    assert fake.names() == ["mark-read"]
    _, script, argv, timeout = fake.calls[0]
    assert script is MARK_READ_SCRIPT and timeout == TIMEOUT
    assert argv == ["mark-read", "L:Jordan Park", "L:(415) 555-0134", "L:+1 (415) 555-0134", "N:Jordan", "N:Jordan P."]
    health = ui_health(rw)
    assert health["permission"] == "granted" and health["lastResult"] == "Marked Jordan Park read in Messages."
    assert health["lastAt"] and not validate(rw.get("/api/health"), "Health")
    assert "Jordan" not in caplog.text and "ok-menu" in caplog.text  # the log gets a code, not the name
    assert ui_actions(rw) == []  # successes stay quiet
    read(rw, JORDAN)  # Messages agrees now: nothing to do
    assert rw.services.messages_ui.wait_idle(5) and len(fake.calls) == 1


def test_reports_when_chat_db_still_shows_unread(make_rw):
    fake = FakeMessages(mark_read=Outcome(0, "ok-context", ""))  # "succeeds" but nothing changes in chat.db
    rw = harness(make_rw, fake)
    read(rw, JORDAN)
    assert rw.services.messages_ui.wait_idle(5)
    sentence = ("Marked Jordan Park read in Messages (from its context menu), "
                "but chat.db still shows unread messages there.")
    assert ui_health(rw)["lastResult"] == sentence
    assert ui_actions(rw) == [{"type": "ui-action", "action": "mark-read", "conversationId": JORDAN, "messageId": None,
                               "clientId": None, "result": "failed", "detail": sentence}]


def test_permission_denied_and_failures(make_rw):
    fake = FakeMessages(mark_read=Outcome(1, "", DENIED_STDERR))
    rw = harness(make_rw, fake)
    read(rw, JORDAN)
    assert rw.services.messages_ui.wait_idle(5)
    health = ui_health(rw)
    assert health["permission"] == "denied" and health["lastResult"] == DENIED_TEXT
    assert ui_actions(rw)[-1]["detail"] == DENIED_TEXT and ui_actions(rw)[-1]["result"] == "failed"
    status = State(rw.settings.state_dir / "state.db")  # remembered across restarts
    try:
        assert status.get_kv(messages_ui.STATUS_KEY)["permission"] == "denied"
    finally:
        status.close()
    fake.answers["mark-read"] = Outcome(0, "not-running", "")
    read(rw, JORDAN)
    assert rw.services.messages_ui.wait_idle(5)
    health = ui_health(rw)
    assert health["permission"] == "denied"  # unchanged: Messages wasn't open, so we learned nothing
    assert health["lastResult"] == "Messages isn't open, so nothing was marked read."
    assert len(ui_actions(rw)) == 1  # "Messages isn't open" isn't worth a toast every time
    fake.answers["mark-read"] = Outcome(0, "row-not-found", "")
    read(rw, JORDAN)
    assert rw.services.messages_ui.wait_idle(5)
    assert ui_actions(rw)[-1]["detail"] == "Couldn't find Jordan Park in Messages' sidebar."
    assert ui_health(rw)["permission"] == "granted"  # the script got that far, so Accessibility works now
    fake.answers["mark-read"] = Outcome(-1, "", "", timed_out=True)
    read(rw, JORDAN)
    assert rw.services.messages_ui.wait_idle(5)
    assert "didn't respond" in ui_health(rw)["lastResult"] and ui_health(rw)["permission"] == "granted"
    for event in ui_actions(rw):
        assert not validate(event, "ServerEvent"), event


def test_only_when_on_and_unread(make_rw):
    off = FakeMessages()
    rw = harness(make_rw, off, enabled=False)
    read(rw, JORDAN)
    assert rw.services.messages_ui.wait_idle(5) and off.calls == []
    on = FakeMessages()
    rw = harness(make_rw, on)
    read(rw, MARCUS)  # nothing unread in chat.db
    assert rw.services.messages_ui.wait_idle(5) and on.calls == []
    assert ui_health(rw)["lastResult"] == "Ready"  # only the probe ran


def test_group_conversations_use_the_title(make_rw):
    fake = FakeMessages()
    rw = harness(make_rw, fake, clears=True)
    read(rw, FAMILY)
    assert rw.services.messages_ui.wait_idle(5)
    assert fake.argv("mark-read") == ["mark-read", "L:Liu Family 🏡"]


def test_never_blocks_and_coalesces(make_rw):
    fake = FakeMessages()
    fake.gate = threading.Event()
    rw = harness(make_rw, fake)
    started = time.monotonic()
    read(rw, JORDAN)
    assert fake.started.wait(5)  # running, and stuck until the gate opens
    read(rw, JORDAN)  # queued once more (new messages may arrive while it runs)
    read(rw, JORDAN)  # dropped: already queued
    read(rw, FAMILY)
    read(rw, JORDAN)  # dropped
    assert time.monotonic() - started < 3  # none of these waited for Messages
    assert len(fake.calls) == 1
    fake.gate.set()
    assert rw.services.messages_ui.wait_idle(10)
    assert [argv[1] for _, _, argv, _ in fake.calls] == ["L:Jordan Park", "L:Jordan Park", "L:Liu Family 🏡"]


def test_dry_run_and_fake_db_never_get_osascript(fixture_src, tmp_path):
    from conftest import Harness

    for env in ({}, {"BACKCHANNEL_SEND_MODE": "applescript"}):
        with Harness(fixture_src, tmp_path / f"state-{len(env)}", **env) as h:
            assert h.services.messages_ui.runner is None
            assert h.services.messages_ui.simulator is not None  # the fake DB stands in for Messages
            h.client.patch("/api/settings", json={"markReadInMessages": True})
            assert h.services.messages_ui.request(Job(JORDAN, "Jordan Park", ["Jordan Park"], [1])) is False
    plain = Harness(fixture_src, tmp_path / "state-plain", BACKCHANNEL_FAKE_DB="0").services  # dry-run, real DB
    try:
        assert plain.messages_ui.runner is None and plain.messages_ui.simulator is None
    finally:
        plain.close()
    services = Harness(fixture_src, tmp_path / "state-real", BACKCHANNEL_SEND_MODE="applescript",
                       BACKCHANNEL_FAKE_DB="0").services  # the real thing: only with Messages' own chat.db
    try:
        assert services.messages_ui.runner is messages_ui.osascript_runner
        assert services.messages_ui.simulator is None
    finally:
        services.close()


def test_menu_titles_hide_anything_personal():
    private = {"jordan park", "jordan", "park", "mark", "priya"}
    titles = "Delete Conversation… | Mark as Read | Mark as Unread | Call Jordan | Park Here | jo@x.example | +1 415 | Marky"
    assert messages_ui.safe_titles(titles, private) == (
        "Delete Conversation… | Mark as Read | Mark as Unread | (hidden) | (hidden) | (hidden) | (hidden) | Marky")
    assert messages_ui.safe_titles("Reply | Tapback… | Copy | Look Up “hike” | Translate 'ok' | missing value") == (
        "Reply | Tapback… | Copy | (hidden) | (hidden) | ---")


def test_turning_it_off_drops_queued_jobs(make_rw):
    fake = FakeMessages()
    fake.gate = threading.Event()
    rw = harness(make_rw, fake)
    read(rw, JORDAN)
    assert fake.started.wait(5)
    read(rw, FAMILY)  # queued behind Jordan's
    rw.client.patch("/api/settings", json={"markReadInMessages": False})
    fake.gate.set()
    assert rw.services.messages_ui.wait_idle(10)
    assert [argv[1] for _, _, argv, _ in fake.calls] == ["L:Jordan Park"]


def test_inspect_walks_without_entire_contents():
    # "entire contents" of a big Messages window took over a minute on macOS 26 (Harry's Mac).
    assert not any("entire contents" in line for line in INSPECT_SCRIPT if not line.lstrip().startswith("--"))


def test_inspect_tree_lines_hide_text_and_ids_that_could_be_personal():
    from server.messages_ui import inspect
    out = "\n".join([
        "accessibility\tallowed",
        "messages_running\tyes",
        "windows\t1",
        "tree\t0\tAXWindow\tAXStandardWindow\t2\t\t0\t0\t-",
        "tree\t1\tAXGroup\t\t3\tConversationList\t12\t0\t-",
        "tree\t2\tAXCell\t\t1\tiMessage;-;+15105550100\t31\t0\tmenu",
        "skip\t2\t40",
        "tree\t2\tAXStaticText\t\t0\tjordan@example.com\t0\t57\t-",
    ])
    lines = inspect(runner=lambda script, argv, timeout: Outcome(0, out, ""))
    text = "\n".join(lines)
    assert "AXWindow ‹AXStandardWindow› (2)" in text
    assert "id=ConversationList desc:12" in text
    assert "AXCell (1) id=(hidden) desc:31 [menu]" in text
    assert "… 40 more" in text
    assert "value:57" in text
    assert "+1510" not in text and "jordan@" not in text and "iMessage;" not in text



def test_inspect_probe_lines_report_shapes_actions_and_titles_without_text():
    from server.messages_ui import inspect
    out = "\n".join([
        "accessibility\tallowed", "messages_running\tyes", "windows\t1",
        "spot\tlist\tConversationList", "spot\ttranscript\tTranscriptCollectionView", "spot\ttitle\t",
        "rows\t25\t25\t0\t1",
        "row\t1\tAXGenericElement\t20\t\t12,Pinned",
        "row\t7\tAXGenericElement\t115\tfalse\t15,88,time",
        "row\t8\tAXGenericElement\t40\tfalse\tJordan Park,Hi",
        "row_actions\tlong-label\tAXPress,AXShowMenu,Name:Pin Target:0x0 Selector:(null)",
        "row_selected_settable\ttrue",
        "bubbles\t9", "bubble_row\t9\t343\t9,322,time",
        "bubble_actions\tballoon\tAXShowMenu,Name:Reply Target:0x1,Name:+14155550100\tSticker",
        "top_before\tAXWindow,AXMenuBar", "top_after\tAXWindow,AXMenuBar,AXMenu",
        "bubble_menu_at\tapp", "bubble_menu\tReply | Tapback… | Copy | Jordan Park",
        "tapback_item\tyes", "after_tapback\tAXWindow,AXMenuBar\t2\t1",
        "window\t2\t\tAXSystemDialog",
        "picker_buttons\twindow\tHeart | Thumbs Up | +14155550100",
    ])
    text = "\n".join(inspect(runner=lambda script, argv, timeout: Outcome(0, out, ""), private={"jordan park"}))
    assert "list: found (id ConversationList)" in text and "title: missing" in text
    assert "item 1: AXGenericElement, label 20 chars [12,Pinned]" in text
    assert "[15,88,time]" in text and "[?,?]" in text  # words that aren't known shapes never print
    assert "custom:Pin" in text and "custom:Reply" in text and "custom:(hidden)" in text
    assert "can set AXSelected on a row: true" in text
    assert "showed up: app" in text and "Tapback item: yes" in text and "windows 2 (were 1)" in text
    assert "Heart | Thumbs Up" in text
    assert "Jordan" not in text and "4155550100" not in text and "[?,?]" in text


def test_spots_are_reached_in_one_request():
    # Each step into Messages costs ~0.1 s on macOS 26; walking 13 levels one request at a time made
    # replies time out. The spot handlers use one nested reference instead.
    from server.messages_scripts import SPOTS, spot_ref
    assert spot_ref((1, 2, 3)) == "UI element 3 of UI element 2 of UI element 1 of bcWin"
    source = "\n".join(REPLY_SCRIPT)
    for name in ("list", "transcript", "entry"):
        assert f"set bcEl to {spot_ref(SPOTS[name])}" in source, name
    assert "atPath(bcWin, {1, 1, 1" not in source


# --------------------------------------------------------------------------- opening a chat (macOS 26)


def _conv(kind: str, handle: str, service: str, persons: int = 1):
    from server.chatdb import ChatInfo
    from server.conversations import Conv, Person
    people = [Person(f"p{i}", handle, [handle], "Someone", "Some", True, False) for i in range(persons)]
    chat = ChatInfo(1, "chat-guid", 45 if kind == "dm" else 43, handle, service, None, (1,))
    return Conv("c1", kind, [chat], people)


@pytest.mark.parametrize("kind, handle, service, persons, link", [
    ("dm", "+14155550134", "iMessage", 1, "imessage://+14155550134"),
    ("dm", "+14155550134", "SMS", 1, "sms:+14155550134"),
    ("dm", "jordan@example.com", "iMessage", 1, "imessage://jordan@example.com"),
    ("dm", "+14155550134&body=hi", "iMessage", 1, None),  # nothing but a number or an address goes in a link
    ("dm", "jordan@example.com?x", "iMessage", 1, None),
    ("group", "+14155550134", "iMessage", 3, None),
])
def test_messages_link_opens_one_to_one_chats_directly(kind, handle, service, persons, link):
    assert messages_ui.messages_link(_conv(kind, handle, service, persons)) == link


def test_the_trail_goes_to_the_log_but_not_into_the_sentence():
    outcome = Outcome(0, "not-selected|link no row3/0 p:3,14 o:3,14 c:14", "")
    tap = messages_ui.interpret_tapback(outcome, "Jordan Park")
    assert tap.sentence == "Couldn't open Jordan Park in Messages." and tap.state == "nothing"
    assert tap.code == "not-selected [link no row3/0 p:3,14 o:3,14 c:14]"
    assert messages_ui.interpret_reply(Outcome(0, "sent-paste|link title", ""), "Jordan").code == "sent-paste [link title]"
    # Only step names and positions survive, whatever a script printed.
    assert messages_ui.interpret_reply(Outcome(0, "no-list|Jordan's (iPhone)!", ""), "J").code == "no-list [jordans iphone]"
    assert outcome_code(Outcome(0, "ok-action|row3/0", "")) == "ok-action"
    got_permission, sentence, ok = interpret(Outcome(0, "error-late:-1743|link", ""), "Jordan")
    assert got_permission == "denied" and not ok


def test_scripts_open_the_chat_by_title_link_or_sidebar():
    for script in (TAPBACK_SCRIPT, REPLY_SCRIPT):
        source = "\n".join(script)
        assert "my openChat(bcWin, bcList, bcStrictNames, bcLooseNames, my tagged(argv, \"U\"))" in source
        assert "selectOnly" not in source
        assert "open location (item 1 of bcLinks)" in source  # the link comes from argv only
        assert 'property bcTrail : ""' in source and 'return bcAnswer & "|" & bcTrail' in source
    mark = "\n".join(MARK_READ_SCRIPT)
    assert "my findRowScrolling(bcList, bcStrictNames, bcLooseNames, 12)" in mark and "my listHome(bcList, 12)" in mark
    assert "openChat" not in messages_scripts.MARK_READ_ACT  # read sync never changes which chat Messages shows


def test_reply_is_pasted_never_set_and_only_sent_while_still_replying():
    act = messages_scripts.REPLY_ACT
    assert "set value of bcBox to bcReply" not in act  # that went out as a regular text on macOS 26.3
    assert "AXSelectedText" not in act  # did nothing on macOS 26.3
    assert act.count("set value of bcBox to") == 1 and 'set value of bcBox to ""' in act  # only to take it back out
    paste = act.index("key code 9 using command down")
    assert act.index("my clipSave()") < act.index("my setClip(bcReply)") < paste < act.index("my clipRestore(bcSaved)")
    before_return = act[:act.index("key code 36")]
    assert before_return.rindex("my replyMarks(bcEntry) is not greater than bcShapeBefore") > paste


def test_the_clipboard_is_saved_whole_with_appkit():
    """Images and files too (a screenshot on the clipboard blocked replies), only in the reply script."""
    assert REPLY_SCRIPT[:3] == ('use AppleScript version "2.4"', 'use framework "AppKit"', "use scripting additions")
    clip = messages_scripts.CLIPBOARD
    assert "pasteboardItems()" in clip and "dataForType:" in clip and "writeObjects:bcSaved" in clip
    assert "clipboard info" not in clip  # nothing is refused for what kind it is any more
    for name, script in SCRIPTS.items():
        if name != "reply":
            assert not any(line.startswith("use ") for line in script), name


def test_sidebar_scrolling_tries_the_next_items_with_literal_actions():
    """The sidebar ends with section groups that can't scroll: row0/0 on Harry's Mac."""
    sidebar = messages_scripts.SIDEBAR
    body = sidebar[sidebar.index("on pageList("):sidebar.index("end pageList")]
    assert "repeat with bcStep from 0 to 4" in body
    assert 'perform action "AXScrollUpByPage" of bcItem' in body and 'perform action "AXScrollDownByPage" of bcItem' in body
    assert "perform action bcAction" not in body


def test_after_the_link_a_changed_title_counts_and_fewer_copies_take_the_oldest():
    sidebar, bubbles = messages_scripts.SIDEBAR, messages_scripts.BUBBLES
    assert 'if bcTitleAfter is not "" and bcTitleAfter is not bcTitleBefore then return my traced("ok", bcTrace & {"changed"})' in sidebar
    timed = bubbles[bubbles.index("on timedIndex("):bubbles.index("end timedIndex")]
    assert "set bcOldest to i" in timed and timed.rstrip().endswith("return bcOldest")


def test_open_mode_is_in_the_inspect_script():
    source = "\n".join(INSPECT_SCRIPT)
    assert 'else if bcMode is "open" then' in source
    assert 'set bcReport to bcReport & {"open" & tab & bcOpened & tab & bcTrail}' in source
