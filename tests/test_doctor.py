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
        "BACKCHANNEL_STICKER_CACHE_ROOT": str(fx / "StickerCache"),
        "BACKCHANNEL_MESSAGES_CONTAINER_ROOT": str(fx / "Containers" / "com.apple.MobileSMS"),
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
    assert "- messages: 135" in text and "- readable: yes" in text
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


# --------------------------------------------------------------------------- --messages-ui

INSPECTION = "\n".join([
    "accessibility\tallowed",
    "messages_running\tyes",
    "windows\t1",
    "menu_bar\tApple | Messages | File | Edit | View | Conversation | Format | Window | Help",
    "conversation_menu\tDelete Conversation… | Mark as Read | Mark as Unread | Call Jordan Park | "
    "Jordan.Park@icloud.example | +14155550134 | Priya's Birthday",
    "tree\t0\tAXWindow\t2",
    "tree\t1\tAXSplitGroup\t3",
    "tree\t2\tJordan Park\t1",  # a role is never a name: shown as (other)
    "tree\t2\tAXStaticText\t0",
    "tree\t3\tAXRow\tare you down to hike Saturday?",  # dropped: counts must be numbers
    "rows\t12\t12\t0\t12\t48\t0\t60",
    "rows\t12\tJordan\t0\t12\t48\t0\t60",  # dropped
    "description\tJordan Park, are you down to hike Saturday?, 2:41 PM",  # unknown keys are dropped
    "Marcus Webb",
    "bubble\tAXGroup\tAXShowMenu,AXPress,Jordan",
    "bubble_menu\tReply | Tapback… | Copy | Look Up “hike” | Translate | Forward… | missing value | Delete…",
    "tapback_item\tyes",
    "tapback_buttons\tHeart | Thumbs up | Thumbs down | Ha ha | Exclamation marks | Question mark | Jordan Park",
    "reply_item\tyes",
    "reply_field\tAXTextArea\ttrue",
    "reply_field\tAXTextArea\tare you down to hike",  # dropped: not a boolean
])


def test_messages_ui_inspection_never_prints_names(doctor_env, monkeypatch):
    from server import messages_ui

    calls = []

    def fake_runner(script, argv, timeout):
        calls.append((script, argv))
        return messages_ui.Outcome(0, INSPECTION, "")

    monkeypatch.setattr(messages_ui, "osascript_runner", fake_runner)
    code, text = doctor.run(["--messages-ui"], dotenv=False)
    assert code == 0
    assert calls == [(messages_ui.INSPECT_SCRIPT, [])]
    section = text[text.index("## Messages UI (Accessibility)"):]
    assert "- markReadInMessages setting: off" in section and "- last result: (none)" in section
    assert "- tapbacksViaMessages setting: off" in section and "- threadRepliesViaMessages setting: off" in section
    assert "- Accessibility: allowed" in section and "- Messages running: yes" in section and "- windows: 1" in section
    assert "- menu bar: Apple | Messages | File | Edit | View | Conversation | Format | Window | Help" in section
    assert ("- Conversation menu: Delete Conversation… | Mark as Read | Mark as Unread | (hidden) | (hidden) | "
            "(hidden) | (hidden)") in section
    assert "AXSplitGroup (3)" in section and "(other) (1)" in section and "AXStaticText (0)" in section
    assert "row-like elements (AXRow/AXCell): 12; with description 12 (longest 48)" in section
    assert "- newest bubble: AXGroup (actions: AXShowMenu, AXPress, (other))" in section
    assert ("- its context menu: Reply | Tapback… | Copy | (hidden) | Translate | Forward… | --- | Delete…"
            in section)
    assert "- Tapback item: yes" in section and "- Reply item: yes" in section
    assert ("- Tapback picker buttons: Heart | Thumbs up | Thumbs down | Ha ha | Exclamation marks | Question mark | "
            "(hidden)") in section
    assert "- reply field: AXTextArea, AXValue settable: yes" in section
    assert section.count("- reply field:") == 1
    for secret in SECRETS + ("Saturday", "2:41"):
        assert secret not in text, secret


@pytest.mark.parametrize("outcome, expected", [
    (("", "execution error: System Events got an error: osascript is not allowed assistive access. (-25211)"),
     "- Accessibility: NOT allowed for this app"),
    (("", "execution error: Not authorized to send Apple events to System Events. (-1743)"),
     "- Automation: NOT allowed to control System Events"),
    (("", 'execution error: Can\'t get row "Jordan Park" of window 1. (-1728)'),
     "- inspection: failed (exit 1, error -1728)"),
])
def test_messages_ui_inspection_failures(doctor_env, monkeypatch, outcome, expected):
    from server import messages_ui

    monkeypatch.setattr(messages_ui, "osascript_runner", lambda script, argv, timeout: messages_ui.Outcome(1, *outcome))
    code, text = doctor.run(["--messages-ui"], dotenv=False)
    assert code == 0 and expected in text
    assert "Jordan" not in text


@pytest.mark.skipif(sys.platform == "darwin", reason="would really drive System Events")
def test_messages_ui_needs_macos(doctor_env):
    code, text = doctor.run(["--messages-ui"], dotenv=False)
    assert code == 0 and "- inspection: skipped (osascript isn't available: this needs macOS)" in text
