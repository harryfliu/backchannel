"""Send through Messages.app with AppleScript (`osascript`).

The chat GUID, handle, text and file path are always passed as argv to an
`on run argv` script, never spliced into AppleScript source, so message text
can't change what the script does. Calls block (30 s timeout); callers run
them off the event loop.

Order of attempts for text:
 1. `send msgText to chat id chatGuid`
 2. DMs only: `send msgText to participant handle of (1st account whose service type = iMessage)`
    (or SMS for SMS chats), then the pre-Monterey `buddy … of 1st service` form.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("backchannel.sender")

TIMEOUT = 30

SEND_TO_CHAT = (
    "on run argv",
    "set chatGuid to item 1 of argv",
    "set msgText to item 2 of argv",
    'tell application "Messages"',
    "send msgText to chat id chatGuid",
    "end tell",
    "end run",
)
SEND_TO_PARTICIPANT = (
    "on run argv",
    "set targetHandle to item 1 of argv",
    "set msgText to item 2 of argv",
    "set serviceName to item 3 of argv",
    'tell application "Messages"',
    'if serviceName is "SMS" then',
    "set targetAccount to 1st account whose service type = SMS",
    "else",
    "set targetAccount to 1st account whose service type = iMessage",
    "end if",
    "send msgText to participant targetHandle of targetAccount",
    "end tell",
    "end run",
)
SEND_TO_BUDDY = (
    "on run argv",
    "set targetHandle to item 1 of argv",
    "set msgText to item 2 of argv",
    'tell application "Messages"',
    "set targetService to 1st service whose service type = iMessage",
    "send msgText to buddy targetHandle of targetService",
    "end tell",
    "end run",
)
SEND_FILE_TO_CHAT = (
    "on run argv",
    "set chatGuid to item 1 of argv",
    "set theFile to POSIX file (item 2 of argv)",
    'tell application "Messages"',
    "send theFile to chat id chatGuid",
    "end tell",
    "end run",
)
SEND_FILE_TO_PARTICIPANT = (
    "on run argv",
    "set targetHandle to item 1 of argv",
    "set theFile to POSIX file (item 2 of argv)",
    'tell application "Messages"',
    "set targetAccount to 1st account whose service type = iMessage",
    "send theFile to participant targetHandle of targetAccount",
    "end tell",
    "end run",
)

_SAFE_FILE = re.compile(r"[^\w.\- ()]+", re.UNICODE)


@dataclass
class SendResult:
    ok: bool
    error: str | None = None


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
            return SendResult(True)
        return SendResult(False, friendly_error(result.stderr))

    def send_text(self, chat_guid: str, text: str, *, dm_handle: str | None = None, service: str = "iMessage") -> SendResult:
        if self.mode == "dry-run":
            log.info("[dry-run] would send to %s: %r", chat_guid, text)
            return SendResult(True)
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

    def send_file(self, chat_guid: str, path: Path, *, dm_handle: str | None = None) -> SendResult:
        if self.mode == "dry-run":
            log.info("[dry-run] would send file %s to %s", path, chat_guid)
            return SendResult(True)
        result = self._run(SEND_FILE_TO_CHAT, chat_guid, str(path))
        if result.ok or not dm_handle:
            return result
        fallback = self._run(SEND_FILE_TO_PARTICIPANT, dm_handle, str(path))
        return fallback if fallback.ok else result
