"""AI through `claude -p` (Harry's Claude Code subscription), with a fake `claude` on disk."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from server.ai import CATCHUP_SCHEMA, DRAFT_SCHEMA
from server.claude_cli import (
    JSON_INSTRUCTION,
    ClaudeCLI,
    ClaudeCLIError,
    ClaudeCLIMissing,
    ClaudeCLINotLoggedIn,
    parse_json_text,
)
from test_ai import CATCHUP_REPLY
from ts_shapes import validate

FAKE = r'''#!{python}
import json, os, sys, time
here = os.path.dirname(os.path.abspath(__file__))
mode_file = os.path.join(here, "mode")
mode = open(mode_file).read().strip() if os.path.exists(mode_file) else "drafts"
stdin = "" if "--version" in sys.argv else sys.stdin.read()
started = time.time()
if mode == "slowish":
    time.sleep(0.6)
with open(os.path.join(here, "calls.jsonl"), "a") as log:
    log.write(json.dumps({{
        "argv": sys.argv[1:], "stdin": stdin, "cwd": os.getcwd(), "path": os.environ.get("PATH", ""),
        "has_key": "ANTHROPIC_API_KEY" in os.environ or "ANTHROPIC_AUTH_TOKEN" in os.environ,
        "start": started, "end": time.time(),
    }}) + "\n")
if "--version" in sys.argv:
    print("2.1.3 (Claude Code)")
elif mode in ("drafts", "slowish"):
    print("```json\n" + json.dumps({{"drafts": ["yes!! what time", "maybe, lmk the trail", "can't this weekend"]}}) + "\n```")
elif mode == "catchup":
    print("Here you go:\n" + json.dumps({catchup}, ensure_ascii=False) + "\nLet me know if you need more.")
elif mode == "text":
    print("Lowercase, short, lots of haha.")
elif mode == "fail":
    sys.stderr.write("Error: something unexpected happened\n")
    sys.exit(2)
elif mode == "nologin":
    print("Invalid API key · Please run /login")
    sys.exit(1)
elif mode == "slow":
    time.sleep(30)
elif mode == "prose":
    print("I can't help with that one.")
'''


@pytest.fixture
def fake_claude(tmp_path) -> Path:
    folder = tmp_path / "fakebin"
    folder.mkdir()
    script = folder / "claude"
    script.write_text(FAKE.format(python=sys.executable, catchup=repr(CATCHUP_REPLY)))
    script.chmod(0o755)
    return script


def mode(fake: Path, value: str) -> None:
    (fake.parent / "mode").write_text(value)


def calls(fake: Path) -> list[dict]:
    log = fake.parent / "calls.jsonl"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


@pytest.fixture
def no_real_claude(monkeypatch, tmp_path):
    """Make sure the search can't find a real `claude` on this machine."""
    shell = tmp_path / "fakeshell"
    shell.write_text("#!/bin/sh\necho 'Welcome to zsh'\necho __BACKCHANNEL_PATH_BEGIN__\necho /usr/bin:/bin\n"
                     "echo __BACKCHANNEL_PATH_END__\n")
    shell.chmod(0o755)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SHELL", str(shell))


# --------------------------------------------------------------------------- the runner


def test_argv_stdin_env_and_cwd(fake_claude, tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-leak")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "also-not")
    cli = ClaudeCLI(str(fake_claude), tmp_path / "state" / "claude-cwd")
    data = cli.run_json(user="the transcript", system="You write replies.", schema=DRAFT_SCHEMA, model="haiku",
                        timeout=30)
    assert data == {"drafts": ["yes!! what time", "maybe, lmk the trail", "can't this weekend"]}  # fences stripped
    call = calls(fake_claude)[-1]
    system = "You write replies.\n\n" + JSON_INSTRUCTION + json.dumps(DRAFT_SCHEMA, separators=(",", ":"))
    assert call["argv"] == ["-p", "--model", "haiku", "--no-session-persistence", "--tools", "",
                            "--disable-slash-commands", "--system-prompt", system]
    assert call["stdin"] == "the transcript"
    assert call["has_key"] is False  # the subscription is used, never the API key
    assert call["cwd"] == str(tmp_path / "state" / "claude-cwd") and not os.listdir(call["cwd"])
    assert str(fake_claude.parent) in call["path"].split(os.pathsep)
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-should-not-leak"  # our own env is untouched


def test_json_wrapped_in_prose(fake_claude, tmp_path):
    mode(fake_claude, "catchup")
    cli = ClaudeCLI(str(fake_claude), tmp_path / "cwd")
    assert cli.run_json(user="x", system="y", schema=CATCHUP_SCHEMA, model="sonnet", timeout=30) == CATCHUP_REPLY


def test_parse_json_text():
    assert parse_json_text('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_text('Sure! {"a": {"b": [1, 2]}} trailing } text') == {"a": {"b": [1, 2]}}
    with pytest.raises(json.JSONDecodeError):
        parse_json_text("no json here")


def test_errors_are_explained(fake_claude, tmp_path):
    cli = ClaudeCLI(str(fake_claude), tmp_path / "cwd")
    mode(fake_claude, "fail")
    with pytest.raises(ClaudeCLIError, match=r"exit 2\): Error: something unexpected happened"):
        cli.run(user="x", system="y", model="haiku", timeout=30)
    mode(fake_claude, "nologin")
    with pytest.raises(ClaudeCLINotLoggedIn, match="Run `claude` in Terminal once and log in"):
        cli.run(user="x", system="y", model="haiku", timeout=30)
    mode(fake_claude, "prose")
    with pytest.raises(ClaudeCLIError, match="wasn't valid JSON"):
        cli.run_json(user="x", system="y", schema=DRAFT_SCHEMA, model="haiku", timeout=30)


def test_slow_calls_time_out_and_are_killed(fake_claude, tmp_path):
    mode(fake_claude, "slow")
    cli = ClaudeCLI(str(fake_claude), tmp_path / "cwd")
    start = time.time()
    with pytest.raises(ClaudeCLIError, match="didn't answer within 1 seconds"):
        cli.run(user="x", system="y", model="haiku", timeout=1)
    assert time.time() - start < 8


def test_at_most_two_at_once(fake_claude, tmp_path):
    mode(fake_claude, "slowish")
    cli = ClaudeCLI(str(fake_claude), tmp_path / "cwd")
    threads = [threading.Thread(target=cli.run, kwargs=dict(user="x", system="y", model="haiku", timeout=30))
               for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    spans = [(c["start"], c["end"]) for c in calls(fake_claude)]
    assert len(spans) == 4
    most = max(sum(1 for s, e in spans if s <= t < e) for t, _ in spans)
    assert most <= 2


def test_found_through_the_login_shell(fake_claude, tmp_path, monkeypatch):
    shell = tmp_path / "loginshell"
    shell.write_text(f"#!/bin/sh\necho 'Last login: today'\necho __BACKCHANNEL_PATH_BEGIN__\n"
                     f"echo {fake_claude.parent}:/usr/bin:/bin\necho __BACKCHANNEL_PATH_END__\n")
    shell.chmod(0o755)
    monkeypatch.setenv("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")  # what an app launched from the Dock gets
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SHELL", str(shell))
    cli = ClaudeCLI(None, tmp_path / "cwd")
    assert cli.locate() == str(fake_claude)
    assert cli.search_path.split(os.pathsep)[0] in (str(fake_claude.parent), os.path.realpath(fake_claude.parent))
    assert cli.version() == "2.1.3 (Claude Code)"


def test_found_in_a_usual_install_folder(fake_claude, tmp_path, monkeypatch, no_real_claude):
    home = Path(os.environ["HOME"])
    local_bin = home / ".local" / "bin"
    local_bin.mkdir(parents=True)
    (local_bin / "claude").symlink_to(fake_claude)
    cli = ClaudeCLI(None, tmp_path / "cwd")
    assert cli.locate() == str(local_bin / "claude")


def test_missing_claude(tmp_path, no_real_claude):
    cli = ClaudeCLI(str(tmp_path / "not-here" / "claude"), tmp_path / "cwd")
    assert cli.locate() is None
    with pytest.raises(ClaudeCLIMissing):
        cli.run(user="x", system="y", model="haiku", timeout=5)


# --------------------------------------------------------------------------- through the app


@pytest.fixture
def cli_app(make_rw, fake_claude):
    harness = make_rw(BACKCHANNEL_AI_BACKEND="claude-cli", BACKCHANNEL_CLAUDE_BIN=str(fake_claude))
    return harness, fake_claude


def test_health_reports_the_cli_backend(cli_app):
    harness, _ = cli_app
    assert harness.get("/api/health")["ai"] == {"enabled": True, "model": "haiku", "backend": "claude-cli"}


def test_drafts_and_catchup_via_cli(cli_app):
    harness, fake = cli_app
    drafts = harness.post("/api/ai/draft", {"conversationId": "dm:+14155550134", "hint": "say yes"})
    assert drafts == {"drafts": ["yes!! what time", "maybe, lmk the trail", "can't this weekend"]}
    call = calls(fake)[-1]
    assert call["argv"][call["argv"].index("--model") + 1] == "haiku"
    system = call["argv"][call["argv"].index("--system-prompt") + 1]
    assert "mostly in lowercase" in system and JSON_INSTRUCTION in system and '"drafts"' in system
    assert "Jordan Park: are you down to hike Saturday?" in call["stdin"] and "Steer from Harry: say yes" in call["stdin"]
    mode(fake, "catchup")
    result = harness.get("/api/ai/catchup")
    assert not validate(result, "CatchUp")
    assert calls(fake)[-1]["argv"][calls(fake)[-1]["argv"].index("--model") + 1] == "sonnet"
    assert result["sections"][0]["items"][0]["conversationId"] == "dm:+14155550161"


def test_cli_errors_through_the_app(cli_app):
    harness, fake = cli_app
    mode(fake, "nologin")
    response = harness.client.post("/api/ai/draft", json={"conversationId": "dm:+14155550134"})
    assert response.status_code == 502 and "Run `claude` in Terminal once and log in" in response.json()["error"]


def test_style_profile_is_a_plain_text_call(cli_app, tmp_path):
    harness, fake = cli_app
    harness.services.settings.style_profile_path = tmp_path / "missing.json"
    mode(fake, "text")
    assert harness.services.ai.style_profile() == "Lowercase, short, lots of haha."
    call = calls(fake)[-1]
    assert JSON_INSTRUCTION not in call["argv"][call["argv"].index("--system-prompt") + 1]
    assert harness.services.state.get_kv("style_profile")["source"] == "claude"


def test_auto_prefers_claude_over_an_api_key(make_rw, fake_claude, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    harness = make_rw(BACKCHANNEL_AI_BACKEND="auto", BACKCHANNEL_CLAUDE_BIN=str(fake_claude))
    assert harness.get("/api/health")["ai"]["backend"] == "claude-cli"
    harness.post("/api/ai/draft", {"conversationId": "dm:+14155550134"})
    assert calls(fake_claude)[-1]["has_key"] is False


def test_auto_falls_back_to_the_api_key(make_rw, monkeypatch, no_real_claude):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    harness = make_rw(BACKCHANNEL_AI_BACKEND="auto")
    assert harness.get("/api/health")["ai"] == {"enabled": True, "model": "claude-haiku-5-5", "backend": "api"}


def test_nothing_available(make_rw, monkeypatch, no_real_claude):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    harness = make_rw(BACKCHANNEL_AI_BACKEND="auto")
    assert harness.get("/api/health")["ai"] == {"enabled": False, "model": None, "backend": None}
    response = harness.client.post("/api/ai/draft", json={"conversationId": "dm:+14155550134"})
    assert response.status_code == 503
    assert "Claude Code (the `claude` command) or an ANTHROPIC_API_KEY" in response.json()["error"]
    forced = make_rw(BACKCHANNEL_AI_BACKEND="claude-cli")
    response = forced.client.get("/api/ai/catchup")
    assert response.status_code == 503 and "claude_bin" in response.json()["error"]
