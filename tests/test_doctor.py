"""`backchannel doctor`: counts only, honest exit code, real conversion checks."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

import media_gen
from server import doctor

FAKE_CLAUDE = """#!{python}
import sys
if "--version" in sys.argv:
    print("2.1.3 (Claude Code)")
else:
    sys.stdin.read()
    print("OK")
"""
FAKE_SIPS = """#!{python}
import sys
args = sys.argv[1:]
open(args[args.index("--out") + 1], "wb").write({jpeg!r})
"""
FAKE_QLMANAGE = """#!{python}
import os, sys
args = sys.argv[1:]
out_dir = args[args.index("-o") + 1]
open(os.path.join(out_dir, os.path.basename(args[-1]) + ".png"), "wb").write({png!r})
"""
SECRETS = ("Jordan", "Priya", "Linda", "Marcus", "Webb", "hike", "Thanksgiving", "482913", "+1415555",
           "icloud.example", "Hinodeya", "sunglasses", "camping stove")


def script(path: Path, body: str) -> Path:
    path.write_text(body)
    path.chmod(0o755)
    return path


@pytest.fixture
def doctor_env(fixture_src, tmp_path, monkeypatch):
    fx = tmp_path / "fx"
    shutil.copytree(fixture_src, fx)
    claude = script(tmp_path / "claude", FAKE_CLAUDE.format(python=sys.executable))
    env = {
        "BACKCHANNEL_CHAT_DB": str(fx / "chat.db"),
        "BACKCHANNEL_ADDRESSBOOK_GLOB": f"{fx}/AddressBook/Sources/*/AddressBook-v22.abcddb:{fx}/AddressBook/AddressBook-v22.abcddb",
        "BACKCHANNEL_ATTACHMENTS_ROOT": str(fx / "Attachments"),
        "BACKCHANNEL_STATE_DIR": str(tmp_path / "state"),
        "BACKCHANNEL_CACHE_DIR": str(tmp_path / "cache"),
        "BACKCHANNEL_CHIEF_DB": str(fx / "chief.db"),
        "BACKCHANNEL_SEND_MODE": "dry-run",
        "BACKCHANNEL_ME_NAME": "Harry Liu",
        "BACKCHANNEL_AI_BACKEND": "claude-cli",
        "BACKCHANNEL_CLAUDE_BIN": str(claude),
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return fx


def test_report_counts_only(doctor_env):
    code, text = doctor.run(["--ai"], dotenv=False)
    assert code == 0
    for heading in ("## Backchannel", "## chat.db", "## Attachments (last 90 days)", "## Contacts", "## AI",
                    "## Chief", "## Result"):
        assert heading in text
    assert "- messages: 113" in text and "- readable: yes" in text
    assert "photos by storage: inline 3, external 1, images_dir 1, unconvertible 1, invalid 1, missing_external 1" in text
    assert "- backend in use: claude-cli" in text and "claude --version: 2.1.3 (Claude Code)" in text
    assert "- test call: OK in" in text
    assert "- people: 6" in text
    for secret in SECRETS:
        assert secret not in text, secret


def test_unreadable_chat_db_exits_non_zero(doctor_env, monkeypatch, tmp_path):
    monkeypatch.setenv("BACKCHANNEL_CHAT_DB", str(tmp_path / "missing" / "chat.db"))
    code, text = doctor.run([], dotenv=False)
    assert code == 1
    assert "- readable: NO" in text and "NOT readable" in text and "Full Disk Access" in text


def test_conversion_checks_with_mac_tools(doctor_env, tmp_path, monkeypatch):
    jpeg = media_gen.jpeg(8, 8, lambda bx, by: (1, 2, 3))
    png = media_gen.png(4, 4, lambda x, y: (9, 9, 9))
    tools = {
        "sips": script(tmp_path / "sips", FAKE_SIPS.format(python=sys.executable, jpeg=jpeg)),
        "qlmanage": script(tmp_path / "qlmanage", FAKE_QLMANAGE.format(python=sys.executable, png=png)),
        "afconvert": None,
    }
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr("server.media._which", lambda tool: str(tools[tool]) if tools.get(tool) else None)
    code, text = doctor.run([], dotenv=False)
    assert code == 0
    assert "HEIC/TIFF → JPEG #1 (heic): OK in" in text
    assert "video thumbnail #1 (.mov): OK in" in text
