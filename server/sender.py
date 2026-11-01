"""Send through Messages.app with AppleScript (`osascript`).

The chat GUID, handle, text and file path are always passed as argv to an
`on run argv` script, never spliced into AppleScript source, so message text
can't change what the script does. Calls block (30 s timeout); callers run
them off the event loop.

Order of attempts for text:
 1. `send bcText to chat id bcChatGuid`
 2. Groups, when Messages answers "can't get chat id" (-1728): Messages only keeps a couple of hundred
    recent conversations ready for scripting (215 on Harry's Mac), so an older group can't be sent to
    until it's opened. Open Messages' link to its members in the background
    (`imessage://open?addresses=…`, which brings up that unnamed group), wait until
    `exists chat id bcChatGuid`, and send to that same chat id once more. Nothing ever goes to another
    chat: if the link opened a different conversation, the retry fails like the first try.
 3. DMs only: `send bcText to participant handle of (1st account whose service type = iMessage)`
    (or SMS for SMS chats), then the pre-Monterey `buddy … of 1st service` form.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import sys
import time
import uuid
from urllib.parse import quote
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("backchannel.sender")

TIMEOUT = 30

# Variables are `bc`-prefixed: AppleScript names are case-insensitive and an app's terms win inside
# its tell block, so a plain name can silently become one of Messages' classes or properties.
SEND_TO_CHAT = (
    "on run argv",
    "set bcChatGuid to item 1 of argv",
    "set bcText to item 2 of argv",
    'tell application "Messages"',
    "send bcText to chat id bcChatGuid",
    "end tell",
    "end run",
)
SEND_TO_PARTICIPANT = (
    "on run argv",
    "set bcHandle to item 1 of argv",
    "set bcText to item 2 of argv",
    "set bcServiceName to item 3 of argv",
    'tell application "Messages"',
    'if bcServiceName is "SMS" then',
    "set bcAccount to 1st account whose service type = SMS",
    "else",
    "set bcAccount to 1st account whose service type = iMessage",
    "end if",
    "send bcText to participant bcHandle of bcAccount",
    "end tell",
    "end run",
)
SEND_TO_BUDDY = (
    "on run argv",
    "set bcHandle to item 1 of argv",
    "set bcText to item 2 of argv",
    'tell application "Messages"',
    "set bcService to 1st service whose service type = iMessage",
    "send bcText to buddy bcHandle of bcService",
    "end tell",
    "end run",
)
SEND_FILE_TO_CHAT = (
    "on run argv",
    "set bcChatGuid to item 1 of argv",
    "set bcFile to POSIX file (item 2 of argv)",
    'tell application "Messages"',
    "send bcFile to chat id bcChatGuid",
    "end tell",
    "end run",
)
CHAT_LOADED = (
    "on run argv",
    "set bcChatGuid to item 1 of argv",
    'tell application "Messages"',
    "set bcFound to exists chat id bcChatGuid",
    "end tell",
    "return bcFound",
    "end run",
)

SEND_FILE_TO_PARTICIPANT = (
    "on run argv",
    "set bcHandle to item 1 of argv",
    "set bcFile to POSIX file (item 2 of argv)",
    'tell application "Messages"',
    "set bcAccount to 1st account whose service type = iMessage",
    "send bcFile to participant bcHandle of bcAccount",
    "end tell",
    "end run",
)

_SAFE_FILE = re.compile(r"[^\w.\- ()]+", re.UNICODE)
_LINK_PHONE = re.compile(r"^\+?\d{3,15}$")
_LINK_EMAIL = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
LOAD_SECONDS = 8.0  # how long to wait for Messages to load a chat after opening its link


def members_link(handles: list[str]) -> str | None:
    """Messages' link to a conversation with these people (imessage://open?addresses=…), or None unless
    every handle is a plain phone number or address (nothing else may end up in the link)."""
    if not handles or not all(_LINK_PHONE.match(h) or _LINK_EMAIL.match(h) for h in handles):
        return None
    return "imessage://open?addresses=" + ",".join(quote(h, safe="@.") for h in handles)


@dataclass
class SendResult:
    ok: bool
    error: str | None = None
    output: str = ""
    chat_missing: bool = False  # Messages said it can't get that chat id (-1728)


def friendly_error(stderr: str) -> str:
    text = (stderr or "").strip()
    if "-1743" in text or "Not authorized" in text:
        return ("macOS blocked Backchannel from controlling Messages. Open System Settings → Privacy & Security → "
                "Automation and allow your terminal to control Messages, then try again.")
    if "-1728" in text or "Can’t get" in text or "Can't get" in text:
        return "Messages couldn't find that conversation or person. Open it once in Messages and try again."
    if "-600" in text or "isn’t running" in text or "isn't running" in text:
        return "Messages isn't running. Open Messages and try again."
    return f"Messages reported an error: {text[:300] or 'unknown error'}"


def safe_file_name(name: str) -> str:
    base = Path(name or "file").name
    cleaned = _SAFE_FILE.sub("_", base).strip(" .") or "file"
    return cleaned[:120]


class Sender:
    def __init__(self, mode: str, messages_attachments_dir: Path | None = None, outbox_dir: Path | None = None):
        self.mode = mode
        self.osascript = shutil.which("osascript") if sys.platform == "darwin" else None
        self.messages_attachments_dir = messages_attachments_dir or Path.home() / "Library" / "Messages" / "Attachments"
        self.outbox_dir = outbox_dir

    @property
    def availability(self) -> str:
        if self.mode == "dry-run":
            return "dry-run"
        return "applescript" if self.osascript else "unavailable"

    def _run(self, script: tuple[str, ...], *args: str) -> SendResult:
        if self.mode != "applescript":
            raise RuntimeError("AppleScript sending is disabled in dry-run mode")
        if not self.osascript:
            return SendResult(False, "Sending needs macOS (osascript wasn't found).")
        command = [self.osascript]
        for line in script:
            command += ["-e", line]
        command += list(args)
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            return SendResult(False, "Messages didn't respond within 30 seconds.")
        except OSError as exc:
            return SendResult(False, f"Couldn't run osascript: {exc}")
        if result.returncode == 0:
            return SendResult(True, output=(result.stdout or "").strip())
        stderr = result.stderr or ""
        missing = "-1728" in stderr or "Can’t get" in stderr or "Can't get" in stderr
        return SendResult(False, friendly_error(stderr), chat_missing=missing)

    def _load_chat(self, chat_guid: str, members: list[str] | None) -> bool:
        """Get Messages to load a group it isn't keeping ready (see the module doc): open its members' link
        in the background and wait until the chat id answers. True when it does."""
        url = members_link(members or [])
        if url is None:
            return False
        try:
            subprocess.run(["open", "-g", url], capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as exc:
            log.info("Couldn't open Messages' link: %s", exc)
            return False
        deadline = time.monotonic() + LOAD_SECONDS
        while time.monotonic() < deadline:
            time.sleep(0.5)
            if self._run(CHAT_LOADED, chat_guid).output == "true":
                return True
        return False

    def send_text(self, chat_guid: str, text: str, *, dm_handle: str | None = None, service: str = "iMessage",
                  members: list[str] | None = None) -> SendResult:
        if self.mode == "dry-run":
            log.info("[dry-run] would send to %s: %r", chat_guid, text)
            return SendResult(True)
        result = self._run(SEND_TO_CHAT, chat_guid, text)
        if not result.ok and result.chat_missing and members and self._load_chat(chat_guid, members):
            log.info("Messages hadn't loaded that group; opened it with its link and sent again")
            result = self._run(SEND_TO_CHAT, chat_guid, text)
        if result.ok or not dm_handle:
            return result
        log.info("chat id send failed (%s); trying participant %s", result.error, dm_handle)
        fallback = self._run(SEND_TO_PARTICIPANT, dm_handle, text, "SMS" if service == "SMS" else "iMessage")
        if fallback.ok:
            return fallback
        legacy = self._run(SEND_TO_BUDDY, dm_handle, text)
        return legacy if legacy.ok else result

    def send_to_handle(self, handle: str, text: str) -> SendResult:
        """Start a conversation: `participant` of the iMessage account, then SMS for phone numbers."""
        if self.mode == "dry-run":
            log.info("[dry-run] would start a conversation with %s: %r", handle, text)
            return SendResult(True)
        result = self._run(SEND_TO_PARTICIPANT, handle, text, "iMessage")
        if result.ok:
            return result
        if "@" not in handle:
            log.info("iMessage send to %s failed (%s); trying SMS", handle, result.error)
            sms = self._run(SEND_TO_PARTICIPANT, handle, text, "SMS")
            if sms.ok:
                return sms
        legacy = self._run(SEND_TO_BUDDY, handle, text)
        return legacy if legacy.ok else result

    def stage_file(self, data_path: Path, name: str) -> Path:
        """Copy an upload where Messages is allowed to read it: Attachments/Backchannel/<uuid>/<name>."""
        base = self.outbox_dir if self.mode == "dry-run" and self.outbox_dir else self.messages_attachments_dir / "Backchannel"
        folder = Path(base) / uuid.uuid4().hex
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / safe_file_name(name)
        shutil.copyfile(data_path, dest)
        return dest

    def send_file(self, chat_guid: str, path: Path, *, dm_handle: str | None = None,
                  members: list[str] | None = None) -> SendResult:
        if self.mode == "dry-run":
            log.info("[dry-run] would send file %s to %s", path, chat_guid)
            return SendResult(True)
        result = self._run(SEND_FILE_TO_CHAT, chat_guid, str(path))
        if not result.ok and result.chat_missing and members and self._load_chat(chat_guid, members):
            log.info("Messages hadn't loaded that group; opened it with its link and sent the file again")
            result = self._run(SEND_FILE_TO_CHAT, chat_guid, str(path))
        if result.ok or not dm_handle:
            return result
        fallback = self._run(SEND_FILE_TO_PARTICIPANT, dm_handle, str(path))
        return fallback if fallback.ok else result
