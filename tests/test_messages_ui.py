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
OFF = {"markReadInMessages": False, "tapbacksViaMessages": False, "threadRepliesViaMessages": False, "linkPreviewsViaMessages": False}
DEFAULTS = {**OFF, "aboutMe": ""}  # every server setting: the switches, and the note about you the AI reads
SHOWN = {**DEFAULTS, "tapbackQuick": []}  # what /api/settings answers: the settings plus Messages' quick tapbacks


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
    assert rw.get("/api/settings") == SHOWN
    assert not validate(rw.get("/api/settings"), "ServerSettings")
    assert ui_health(rw) == {"enabled": False, "permission": "unknown", "lastResult": None, "lastAt": None}
    response = rw.client.patch("/api/settings", json={"tapbacksViaMessages": True, "somethingElse": 1})
    assert response.status_code == 200 and response.json() == {**SHOWN, "tapbacksViaMessages": True}
    assert ui_health(rw)["enabled"] is True  # any helper counts
    assert rw.client.patch("/api/settings", json={"threadRepliesViaMessages": True}).json() == {
        **SHOWN, "tapbacksViaMessages": True, "threadRepliesViaMessages": True}
    assert rw.client.patch("/api/settings", json={}).json()["tapbacksViaMessages"] is True
    assert rw.client.patch("/api/settings", json={"markReadInMessages": "maybe"}).status_code == 400
    other = State(rw.settings.state_dir / "state.db")  # it's in state.db, not memory
    try:
        assert other.get_settings() == {**DEFAULTS, "tapbacksViaMessages": True, "threadRepliesViaMessages": True}
    finally:
        other.close()
    assert rw.client.patch("/api/settings", json={"tapbacksViaMessages": False, "threadRepliesViaMessages": False,
                                                  "markReadInMessages": True}).json() == {**SHOWN, "markReadInMessages": True}
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
           "probe": PROBE_SCRIPT, "mark-unread": MARK_UNREAD_SCRIPT,
           "reply-test": messages_scripts.REPLY_TEST_SCRIPT, "compose": messages_scripts.COMPOSE_SCRIPT,
           "reply-aim": messages_scripts.REPLY_AIM_SCRIPT, "reply-finish": messages_scripts.REPLY_FINISH_SCRIPT}
KEYS = {"mark-read": {"key code 53"}, "tapback": {"key code 53"}, "inspect": {"key code 53"}, "probe": set(),
        "mark-unread": {"key code 53"},
        "reply": {"key code 53", "key code 36", "key code 9 using command down"},
        "reply-test": {"key code 53", "key code 36", "key code 9 using command down"},
        "compose": {"key code 53", "key code 36", "key code 9 using command down"},
        "reply-aim": {"key code 53"},
        "reply-finish": {"key code 53", "key code 36", "key code 9 using command down"}}


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
        if name in ("reply", "reply-finish"):
            assert writes == ["bcBox"] and clipboard and attribute_writes == []
        elif name in ("reply-test", "compose"):  # the doctor's reply test (own chat); a link with its preview
            assert writes == [] and clipboard and attribute_writes == []
        else:
            assert writes == [] and attribute_writes == [] and not clipboard, name


def test_return_is_only_pressed_on_our_own_text():
    act = messages_scripts.REPLY_TYPING
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
        assert 'property bcTrail : ""' in source and 'set bcLine to bcAnswer & "|" & bcTrail' in source
    mark = "\n".join(MARK_READ_SCRIPT)
    # Read sync runs whenever a chat is opened: at most 4 pages down (and back), not 30 s of scrolling.
    assert "my findRowScrolling(bcList, bcStrictNames, bcLooseNames, 4)" in mark and "my listHome(bcList, 6)" in mark
    assert "openChat" not in messages_scripts.MARK_READ_ACT  # read sync never changes which chat Messages shows


def test_reply_is_pasted_never_set_and_only_sent_while_still_replying():
    for act in (messages_scripts.REPLY_ACT, messages_scripts.REPLY_FINISH_ACT):
        assert act.rstrip().endswith("return my typeReply(bcReply, bcHintBefore, bcFocusBefore)\nend act")
    act = messages_scripts.REPLY_TYPING
    assert "set value of bcBox to bcReply" not in act  # that went out as a regular text on macOS 26.3
    assert "AXSelectedText" not in act  # did nothing on macOS 26.3
    assert act.count("set value of bcBox to") == 1 and 'set value of bcBox to ""' in act  # only to take it back out
    paste = act.index("key code 9 using command down")
    assert act.index("my clipSave()") < act.index("my setClip(bcReply)") < paste < act.index("my clipRestore(bcSaved)")
    before_return = act[:act.index("key code 36")]
    assert before_return.rindex("if not my stillReplying(bcHintBefore, bcFocusBefore) then") > paste
    assert "replyMarks" not in act  # an inner group changing came with a regular text on macOS 26.3


def test_the_clipboard_is_saved_whole_with_appkit():
    """Images and files too (a screenshot on the clipboard blocked replies), only in the reply script."""
    assert REPLY_SCRIPT[:3] == ('use AppleScript version "2.4"', 'use framework "AppKit"', "use scripting additions")
    clip = messages_scripts.CLIPBOARD
    assert "pasteboardItems()" in clip and "dataForType:" in clip and "writeObjects:bcSaved" in clip
    assert "clipboard info" not in clip  # nothing is refused for what kind it is any more
    for name, script in SCRIPTS.items():
        if name not in ("reply", "reply-test", "compose", "reply-finish"):
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


def test_reply_mode_counts_only_when_the_placeholder_or_focused_field_changes():
    """macOS 26.3: "Reply…" changed an inner group of the entry area but the placeholder stayed
    "iMessage", and what was sent came out unthreaded. Reply mode now needs a real change, and the
    message's right-click menu is the second way in."""
    menus = messages_scripts.MENUS
    start = menus[menus.index("on startReply("):menus.index("end startReply")]
    # Harry's Mac (doctor --messages-reply-test): only the menu bar's Reply turned the placeholder into
    # "Reply" and threaded; it goes first, aimed at the message, the other two stay as fallbacks.
    assert start.index("my aimAt(bcBubble)") < start.index("my replyFromMenuBar()") \
        < start.index('my customAction(bcBubble, {"Reply"})') < start.index("my replyFromMenu(bcBubble, bcWin)")
    assert start.count("my replyEngaged(bcHintBefore, bcFocusBefore)") == 3 and "replyMarks" not in start
    still = menus[menus.index("on stillReplying("):menus.index("end stillReplying")]
    assert 'my hintNow()' in still and 'my focusKey()' in still


def test_the_reply_test_only_runs_in_your_own_chat():
    main = messages_scripts.REPLY_TEST_MAIN
    assert main.index('if not my titleMatches(bcWin, my tagged(argv, "N"), {}) then return "rt" & tab & "not-your-chat"') \
        < main.index("key code 36")
    printed = [line for line in main.splitlines() if "set bcOut to bcOut & {" in line]
    assert printed and not any(re.search(r"\b(description|value) of\b|fieldText", line) for line in printed)


def test_tapbacks_and_replies_go_ahead_of_read_sync():
    """Harry's Mac: a tapback waited behind two mark-reads that each timed out after 30 s."""
    helpers = messages_ui.MessagesUI.__new__(messages_ui.MessagesUI)
    helpers._jobs = messages_ui.queue.PriorityQueue()
    helpers._order = messages_ui.itertools.count()
    helpers._queued, helpers._latest, helpers._lock = set(), {}, threading.Lock()
    helpers._thread = threading.current_thread()  # looks alive: nothing starts working
    read_a = Job("dm:a", "A", ["A"], [1])
    read_b = Job("dm:b", "B", ["B"], [2])
    tap = messages_ui.TapbackJob("dm:c", "C", ["C"], 1, [3], "m1", "love", False, messages_ui.Bubble(["hi"]))
    for job in (read_a, read_b, tap):
        assert helpers._enqueue(job)
    order = [helpers._jobs.get()[2] for _ in range(3)]
    assert order == [tap, read_a, read_b]


def test_unnamed_groups_are_matched_by_their_members_in_any_order():
    """Messages may list an unnamed group's people in another order than Backchannel
    ("Josiah, Wifey 4 Lifey & Christina")."""
    sidebar = messages_scripts.SIDEBAR
    index = sidebar[sidebar.index("on findRowIndex("):sidebar.index("end findRowIndex")]
    assert "if bcBestAt is 0 and bcMembers is not {} then set bcBestAt to my membersIndex(bcDescs, bcRoles)" in index
    title = sidebar[sidebar.index("on titleMatches("):sidebar.index("end titleMatches")]
    assert title.rstrip().endswith("return my namesAll(bcTitle)")
    for script in (MARK_READ_SCRIPT, TAPBACK_SCRIPT, REPLY_SCRIPT, INSPECT_SCRIPT):
        assert "property bcMembers : {}" in script
    assert 'set bcMembers to my tagged(argv, "G")' in "\n".join(TAPBACK_SCRIPT)


def test_the_answer_is_the_first_line_and_a_tapback_may_name_the_quick_emoji():
    outcome = Outcome(0, "chosen-action|shown t0\nquick\t😂 👀 lol ❤️ 😂", "")
    assert messages_ui.interpret_tapback(outcome, "Jordan").code == "chosen-action [shown t0]"
    assert messages_ui.quick_emoji(outcome) == ["😂", "👀", "❤️"]
    assert messages_ui.quick_emoji(Outcome(0, "chosen-action", "")) is None
    assert messages_ui.quick_emoji(Outcome(0, "chosen-action\nquick\t", "")) == []
    tapback = "\n".join(TAPBACK_SCRIPT)
    assert 'set bcExtra to "quick" & tab & my emojiActions(bcBubble)' in tapback
    assert "if bcExtra is not \"\" then set bcLine to bcLine & linefeed & bcExtra" in tapback


def test_emoji_titles_and_checks():
    assert messages_ui.emoji_titles("🔥") == ["🔥", "🔥\ufe0f"]
    assert messages_ui.emoji_titles("❤️") == ["❤️", "\u2764"]
    assert messages_ui.same_emoji("❤️", "\u2764") and not messages_ui.same_emoji("❤️", "🧡")
    assert messages_ui.clean_tapback_emoji(" 🔥 ") == "🔥"
    for bad in ("", "a", "ok", "🔥 🔥", "1", "#"):
        assert messages_ui.clean_tapback_emoji(bad) is None, bad


def test_emoji_not_offered_as_a_quick_tapback_goes_through_the_emoji_picker():
    """doctor --messages-emoji-picker on Harry's Mac: "Add Emoji as Tapback" opens an AXPopover whose
    search field takes the focus, with every emoji in AXGrids (CPKCharactersSectionStaticView)."""
    act = messages_scripts.TAPBACK_ACT
    assert act.index('if my customAction(bcBubble, bcSynonyms) then set bcAnswer to "chosen-action"') \
        < act.index('set bcAnswer to my tapbackFromPicker(bcBubble, bcPicked, my tagged(argv, "Y"))')
    picker = messages_scripts.EMOJI_PICKER
    assert 'my customAction(bcBubble, {"Add Emoji"})' in picker and 'is "AXPopover"' in picker
    assert "set value of" not in picker and "keystroke" not in picker  # found by label and pressed, nothing typed
    assert "tapbackFromPicker" in "\n".join(TAPBACK_SCRIPT)
    assert messages_ui.clean_emoji_names(["face_with_tears_of_joy", "joy", "<script>", "x" * 200, "joy"]) == \
        ["face with tears of joy", "joy"]
    job = messages_ui.TapbackJob("dm:a", "A", ["A"], 1, [1], "m1", "emoji", False, messages_ui.Bubble(["hi"]),
                                 emoji="🔥", emoji_names=["fire"])
    argv = job.argv()
    assert "E:🔥" in argv and "Y:fire" in argv and argv.index("R:🔥") < argv.index("E:🔥")
    classic = messages_ui.TapbackJob("dm:a", "A", ["A"], 1, [1], "m1", "love", False, messages_ui.Bubble(["hi"]))
    assert not [a for a in classic.argv() if a.startswith(("E:", "Y:"))]


def test_a_link_is_only_sent_from_an_empty_field_holding_it():
    act = messages_scripts.COMPOSE_ACT
    assert act.index('if (my fieldText(bcFocused)) is not "" then return "field-busy"') < act.index("my setClip(bcText)")
    assert 'if my attrText(bcFocused, "AXIdentifier") is not "messageBodyField" then return "no-field"' in act
    before_return = act[:act.index("key code 36")]
    assert before_return.rindex("if not bcOurs then return") > before_return.rindex("my focusedTextField()")
    assert "my clipRestore(bcSaved)" in before_return
    assert messages_ui.only_a_link(" https://www.target.com/p/thing ") and messages_ui.only_a_link("www.target.com")
    assert not messages_ui.only_a_link("lunch? https://x.com") and not messages_ui.only_a_link("target.com")


def test_older_messages_are_aimed_at_and_answered_from_their_own_menu():
    """macOS 26.3: the menu bar's Reply is "Reply to Last Message" (always the newest), and the message's
    "Reply…" action and AXShowMenu do nothing, so the script finds the message and the server
    right-clicks it (server/mouse.py); Messages stays in front in between."""
    aim = messages_scripts.REPLY_AIM_ACT
    assert "set bcKeepFront to true" in aim and 'return "aim:" & ' in aim
    assert aim.index("set bcKeepFront to true") > aim.index('return "bubble-not-found"')  # only when it found it
    assert "key code" not in aim and "set the clipboard" not in aim  # it only looks
    finish = messages_scripts.REPLY_FINISH_ACT
    assert "my findMenu(bcWin, bcWin)" in finish and 'starts with "Reply" then set bcAt to i' in finish
    assert 'my addTrail("top:" & ' in finish  # what was there when no menu showed up
    assert finish.index("my replyEngaged(bcHintBefore, bcFocusBefore)") < finish.index("return my typeReply(")
    run = messages_scripts.RUN
    assert "if not bcKeepFront then my restoreFront(bcPrevApp)" in run
    assert "if bcRestoreTo is not \"\" then set bcPrevApp to bcRestoreTo" in run


def test_the_menu_is_found_in_the_window_and_the_click_aims_where_nothing_covers_the_chat():
    """Harry's Mac (macOS 26.3): the right-click opened the message's menu, but as an AXMenu in the
    window's content group, not one of the app's elements, so it went unseen ("no-menu"). And the
    toolbar, title, entry area and sidebar float over the transcript, so the point is kept off them."""
    menus = messages_scripts.MENUS
    find = menus[menus.index("on findMenu("):menus.index("end findMenu")]
    assert "set bcMenu to menu 1 of UI element 1 of bcWin" in find
    assert find.index("menu 1 of UI element 1 of bcWin") < find.index('whose role is "AXMenu"')
    finish = messages_scripts.REPLY_FINISH_ACT
    no_menu = finish[finish.index("if bcMenu is missing value then\n"):finish.index('return "no-menu"')]
    assert "my pressEscape()" in no_menu and "my contentRoles(bcWin)" in no_menu  # a second click can't hit it
    aim = messages_scripts.REPLY_AIM_ACT
    assert "set bcArea to my freeArea(bcWin, bcTranscript, bcList, bcEntry)" in aim
    assert aim.index("set bcArea to my freeArea(") < aim.index("set bcPoint to my visiblePoint(bcBubble, bcArea)")
    free = aim[aim.index("on freeArea("):aim.index("end freeArea")]
    for cover in ("toolbar 1 of bcWin", "my spotTitle(bcWin)", "my frameOf(bcEntry)", "my rightEdge(bcList)"):
        assert cover in free, cover
    assert '"box" & tab & my joinWith(my frameOf(bcBubble), ",")' in aim and '"pid" & tab' in aim


def test_a_thread_left_open_is_closed_and_never_with_a_draft():
    """After a reply Messages keeps that thread open: only the thread shows (so nothing else can be
    found) and its field goes on replying in it (a link pasted next would go there). Closed with
    Escape, before looking for a message or pasting a link and right after a reply is sent."""
    sidebar = messages_scripts.SIDEBAR
    leave = sidebar[sidebar.index("on leaveThread("):sidebar.index("end leaveThread")]
    assert 'my attrText(bcField, "AXPlaceholderValue") starts with "Reply"' in leave
    assert leave.index('if my asText(bcDraft) is not "" then return') < leave.index("my pressEscape()")
    for act in (messages_scripts.TAPBACK_ACT, messages_scripts.REPLY_ACT, messages_scripts.REPLY_AIM_ACT):
        assert act.index("set bcTranscript to my leaveThread(bcWin, bcTranscript)") < act.index("my locateBubble(")
    compose = messages_scripts.COMPOSE_ACT
    assert compose.index("my leaveThread(bcWin, my spotTranscript(bcWin))") < compose.index("my setClip(")
    typing = messages_scripts.REPLY_TYPING
    assert typing.index("key code 36") < typing.index("my leaveThread(") < typing.index('return "sent-" & bcHow')


def test_a_bubble_is_checked_again_right_before_its_used_and_the_chat_pages_itself():
    """System Events finds elements by their place among the transcript's items, which shift while
    Messages scrolls or loads (Harry's Mac: a reply went to the message next to the one aimed at).
    And paging the first item stalled on a time header, so the transcript itself is paged."""
    bubbles = messages_scripts.BUBBLES
    check = bubbles[bubbles.index("on stillTheMessage("):bubbles.index("end stillTheMessage")]
    assert "my matchesBubble(bcLabel, bcNeedles, bcExacts)" in check and "my mentionsAny(bcLabel, bcTimes)" in check
    aim = messages_scripts.REPLY_AIM_ACT
    first = aim.index("set bcBubble to my checkedBubble(")
    assert first < aim.index('if not my hasAction(bcBubble, {"Reply"}) then return "no-reply-item"')
    assert aim.index("AXScrollToVisible") < aim.rindex("set bcBubble to my checkedBubble(") < \
        aim.index("set bcPoint to my visiblePoint(bcBubble, bcArea)")
    assert 'if bcBubble is missing value then return "bubble-moved"' in aim
    tapback = messages_scripts.TAPBACK_ACT
    assert tapback.index("set bcBubble to my checkedBubble(") < tapback.index("my customAction(bcBubble, bcSynonyms)")
    for handler in ("on locateBubble(", "on transcriptEnd("):
        body = bubbles[bubbles.index(handler):]
        body = body[:body.index("\nend ")]
        assert "my pageTranscript(bcTranscript," in body and "my pageList(" not in body, handler
    assert {"bubble-moved"} <= set(messages_ui.REPLY_ANSWERS) & set(messages_ui.TAPBACK_ANSWERS)


def test_a_replys_quote_of_its_message_is_never_taken_for_the_message():
    """macOS 26.3 shows each reply with a quote of the message it answers ("Harry Liu, Reply Preview,
    Todo: wash bed sheets, 8:09 AM"): same text and minute, no tapbacks. Harry's Mac: a tapback found
    the quote first and answered no-tapback-item."""
    bubbles = messages_scripts.BUBBLES
    match = bubbles[bubbles.index("on matchesBubble("):bubbles.index("end matchesBubble")]
    assert match.index('if bcLabel contains ", Reply Preview, " then return false') < match.index("repeat with bcN")
    for handler in ("on timedIndex(", "on findBubble(", "on stillTheMessage("):
        body = bubbles[bubbles.index(handler):]
        assert "my matchesBubble(bcLabel, bcNeedles, bcExacts)" in body[:body.index("\nend ")], handler


def test_photo_only_messages_are_found_by_how_messages_labels_them():
    """macOS 26.3 labels a photo-only bubble "Melinda Lem, Includes picture, 2:59 PM" (or "Name, Image
    attached, IMG_1.jpeg, Image · 2.5 MB, …"). Harry's tapback on a photo answered bubble-not-found."""
    needles, exacts = messages_ui.bubble_terms({"text": "", "attachments": [{"kind": "image", "name": "IMG_1.jpeg"}]})
    assert needles == [] and {"Includes picture", "Image attached"} <= set(exacts)
    _, video = messages_ui.bubble_terms({"text": None, "attachments": [{"kind": "video", "name": "IMG_2.mov"}]})
    assert "Video attached" in video
    match = messages_scripts.BUBBLES[messages_scripts.BUBBLES.index("on matchesBubble("):]
    assert 'bcLabel contains (", " & bcW & ",")' in match[:match.index("end matchesBubble")]  # a whole label part
