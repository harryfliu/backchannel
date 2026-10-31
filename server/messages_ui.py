"""Messages helpers (experimental, opt-in): drive the Messages app through Accessibility.

Apple gives other apps no way to mark texts read, send a tapback or send a real
inline reply, so with the matching setting on Backchannel asks the Messages app
to do it, through System Events:

- mark read (`markReadInMessages`): on `POST /read`, when chat.db still has
  unread incoming messages there;
- tapbacks (`tapbacksViaMessages`): `POST …/tapback`;
- thread replies (`threadRepliesViaMessages`): sends with a `threadRootId`.

One worker runs one action at a time (a queue), each with its own timeout, and
every result is checked in chat.db. Scripts live in messages_scripts.py; all
names and text are passed as argv, never put into the source. Nothing runs in
dry-run / fake-DB mode: there a Simulator writes the row each action would have
produced into the fake chat.db (tests inject a fake runner instead).
"""

from __future__ import annotations

import logging
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import fakedb
from . import heuristics as H
from .chatdb import ChatDB, ChatDBError, NOT_REACTION_SQL
from .conversations import Conv, NotFound, conv_title, message_text
from .handles import format_handle
from .messages_scripts import INSPECT_SCRIPT, MARK_READ_SCRIPT, PROBE_SCRIPT, REPLY_SCRIPT, TAPBACK_SCRIPT
from .state import State
from .timeutil import iso

__all__ = [
    "INSPECT_SCRIPT", "MARK_READ_SCRIPT", "PROBE_SCRIPT", "REPLY_SCRIPT", "TAPBACK_SCRIPT", "Bubble", "Job",
    "MessagesUI", "Outcome", "ProbeJob", "ReplyJob", "ReplyResult", "Simulator", "TapbackJob", "candidate_names",
    "inspect", "interpret", "interpret_probe", "interpret_reply", "interpret_tapback", "osascript_runner",
    "outcome_code", "safe_titles",
]

log = logging.getLogger("backchannel.messages_ui")

TIMEOUT = 8.0  # mark read
TAPBACK_TIMEOUT = 10.0
REPLY_TIMEOUT = 12.0
PROBE_TIMEOUT = 90.0
VERIFY_SECONDS = 5.0
CHECK_AFTER = 3.0
STATUS_KEY = "messages_ui_status"
HELPER_SETTINGS = ("markReadInMessages", "tapbacksViaMessages", "threadRepliesViaMessages")

DENIED = re.compile(r"-1719|-25211|assistive access", re.IGNORECASE)
DENIED_TEXT = ("macOS hasn't let Backchannel control Messages. Allow Backchannel in System Settings › Privacy & "
               "Security › Accessibility, then try again.")
# -1743: the other prompt, "Backchannel wants to control System Events" (Automation), was declined.
AUTOMATION_DENIED = re.compile(r"-1743|not authori[sz]ed to send apple events", re.IGNORECASE)
AUTOMATION_TEXT = ("macOS hasn't let Backchannel control System Events. Allow it in System Settings › Privacy & "
                   "Security › Automation, then try again.")
DRY_RUN_TEXT = "Backchannel is in dry-run mode, so it doesn't drive the Messages app."
TAPBACKS_OFF = "Sending tapbacks is off. Turn it on in Settings › Messages helpers."
TAPBACK_UNCONFIRMED = "Messages didn't confirm the tapback."
REPLY_UNCONFIRMED = "Messages didn't confirm your reply. Check Messages before you try again."

# The six classic tapbacks, their chat.db offsets (2000 + k adds, 3000 + k removes) and what the
# Messages app may call them (button descriptions or menu titles).
TAPBACK_KINDS = ("love", "like", "dislike", "laugh", "emphasize", "question")
SYNONYMS: dict[str, tuple[str, ...]] = {
    "love": ("Heart", "Love", "Loved"),
    "like": ("Thumbs up", "Like", "Liked"),
    "dislike": ("Thumbs down", "Dislike", "Disliked"),
    "laugh": ("Ha ha", "Haha", "Ha-ha", "Laugh"),
    "emphasize": ("Exclamation marks", "Exclamation mark", "Emphasize", "!!"),
    "question": ("Question mark", "Question", "?"),
}
ATTACHMENT_WORDS: dict[str, tuple[str, ...]] = {
    "image": ("Photo", "Image", "Picture"),
    "video": ("Video", "Movie"),
    "audio": ("Audio Message", "Audio"),
    "file": ("Attachment", "File", "Document"),
    "sticker": ("Sticker",),
}
NEEDLE_CHARS = 60
SHORT_NEEDLE = 8  # shorter text has to match a whole label line, not just appear in one
RECENT_FOR_SKIPS = 300


def _norm(text: str | None) -> str:
    return " ".join(unicodedata.normalize("NFC", text or "").split())


# --------------------------------------------------------------------------- running osascript


@dataclass
class Outcome:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


Runner = Callable[[tuple[str, ...], list[str], float], Outcome]


def osascript_runner(script: tuple[str, ...], argv: list[str], timeout: float) -> Outcome:
    """Run an AppleScript given as lines; `argv` follows the script (never interpolated)."""
    binary = shutil.which("osascript") if sys.platform == "darwin" else None
    if not binary:
        return Outcome(127, "", "osascript isn't available (this isn't macOS).")
    command = [binary]
    for line in script:
        command += ["-e", line]
    command += argv
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return Outcome(-1, "", "", timed_out=True)
    except OSError as exc:
        return Outcome(126, "", str(exc))
    return Outcome(result.returncode, result.stdout.strip(), result.stderr.strip())


# --------------------------------------------------------------------------- what to look for


def candidate_names(title: str, *, full_name: str | None = None, nickname: str | None = None,
                    organization: str | None = None, person_name: str | None = None,
                    handle: str | None = None, short_name: str | None = None) -> tuple[list[str], int]:
    """(names Messages might show for a conversation, how many of them may match as a prefix).

    Full names, nicknames, organizations and numbers can match the start of a
    label ("Jordan Park, can carpool…, 2:41 PM"); the short name has to match a
    whole label (or "Jordan, …") so it can't pick another Jordan.
    """
    loose: list[str] = [title, full_name or "", nickname or "", organization or "", person_name or ""]
    if handle:
        if "@" in handle:
            loose.append(handle)
        else:
            formatted = format_handle(handle)
            loose.append(formatted)
            if formatted.startswith("(") and handle.startswith("+1"):
                loose.append("+1 " + formatted)  # how Messages shows numbers that aren't in Contacts
    names: list[str] = []
    for name in loose:
        name = (name or "").strip()
        if name and name.lower() not in (n.lower() for n in names):
            names.append(name)
    count = len(names)
    short = (short_name or "").strip()
    if short and short.lower() not in (n.lower() for n in names):
        names.append(short)
    return names, count


def names_argv(names: list[str], loose: int | None) -> list[str]:
    loose = len(names) if loose is None else min(loose, len(names))
    return [f"L:{n}" for n in names[:loose]] + [f"N:{n}" for n in names[loose:]]


@dataclass
class Bubble:
    """How to recognise one message bubble in Messages' transcript (see messages_scripts)."""

    needles: list[str] = field(default_factory=list)  # a label contains one of these
    exacts: list[str] = field(default_factory=list)  # a label line is one of these
    senders: list[str] = field(default_factory=list)
    skip_sender: int = 0  # newer bubbles from the same sender that match too
    skip_any: int = 0  # newer bubbles from anyone that match too

    def argv(self) -> list[str]:
        return ([f"T:{n}" for n in self.needles] + [f"W:{w}" for w in self.exacts] + [f"S:{s}" for s in self.senders]
                + [f"K:{self.skip_sender}", f"J:{self.skip_any}"])


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        line = " ".join(line.split())
        if line:
            return line
    return ""


def _clip(text: str, limit: int = NEEDLE_CHARS) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    return cut[:space] if space >= limit // 2 else cut


def _attachment_kind(message: dict[str, Any]) -> str | None:
    atts = message.get("attachments") or []
    if not atts:
        return None
    first = atts[0]
    if first.get("isSticker") or first.get("isGenmoji"):
        return "sticker"
    return first.get("kind") or "file"


def bubble_terms(message: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(needles, exacts) for a message: its first line of text, else its attachment's placeholder words."""
    line = _first_line(message.get("text") or "")
    if line:
        needle = _clip(line)
        return ([needle], []) if len(needle) >= SHORT_NEEDLE else ([], [needle])
    kind = _attachment_kind(message)
    if kind is None:
        return [], []
    names = [a["name"] for a in message.get("attachments") or [] if a.get("name") and kind == "file"]
    return names, list(ATTACHMENT_WORDS.get(kind, ATTACHMENT_WORDS["file"]))


def _bubble_hit(message: dict[str, Any], needles: list[str], exacts: list[str], kind: str | None) -> bool:
    """Would a newer message's bubble match the same terms? (Mirrors matchesBubble in the script.)"""
    if message.get("unsent") or message.get("event"):
        return False
    text = (message.get("text") or "").lower()
    if any(n.lower() in text for n in needles if n):
        return True
    if kind is not None:  # placeholder words: any newer attachment of the same kind
        return not text.strip() and _attachment_kind(message) == kind
    for w in exacts:
        w = w.lower()
        for line in text.splitlines():
            line = " ".join(line.split())
            if line == w or line.startswith(w + ",") or line.startswith(w + " "):
                return True
    return False


# --------------------------------------------------------------------------- jobs


@dataclass
class Job:
    """Mark a conversation read in Messages."""

    conversation_id: str
    title: str
    candidates: list[str]  # names to look for in Messages' sidebar, most specific first
    chat_ids: list[int] = field(default_factory=list)
    loose: int | None = None  # how many of `candidates` may match a label's beginning (default: all)
    action = "mark-read"

    def key(self) -> tuple | None:
        return ("mark-read", self.conversation_id)

    def argv(self) -> list[str]:
        return ["mark-read", *names_argv(self.candidates, self.loose)]


@dataclass
class TapbackJob:
    conversation_id: str
    title: str
    candidates: list[str]
    loose: int
    chat_ids: list[int]
    message_id: str
    kind: str
    remove: bool
    bubble: Bubble
    target_chat_id: int | None = None
    target_text: str = ""
    action = "tapback"

    def key(self) -> tuple | None:
        return None

    def argv(self) -> list[str]:
        return ["tapback", *names_argv(self.candidates, self.loose), *self.bubble.argv(),
                *[f"R:{s}" for s in SYNONYMS[self.kind]]]


@dataclass
class ReplyResult:
    kind: str  # done | regular (sent, but not as a reply) | nothing (nothing typed) | unconfirmed
    detail: str
    message_guid: str | None = None


@dataclass
class ReplyJob:
    client_id: str
    conversation_id: str
    title: str
    candidates: list[str]
    loose: int
    chat_ids: list[int]
    root_id: str
    text: str
    bubble: Bubble
    root_chat_id: int | None = None
    root_text: str = ""
    state: str = "queued"  # queued | running | done | cancelled
    ran_script: bool = False
    result: ReplyResult | None = None
    finished: threading.Event = field(default_factory=threading.Event)
    action = "reply"

    def key(self) -> tuple | None:
        return None

    def argv(self) -> list[str]:
        return ["reply", *names_argv(self.candidates, self.loose), *self.bubble.argv(), f"M:{self.text}"]


@dataclass
class ProbeJob:
    """Ask for the System Events (Automation) permission up front; never touches Messages."""

    action = "probe"

    def key(self) -> tuple | None:
        return ("probe",)


# --------------------------------------------------------------------------- reading results


@dataclass
class Interp:
    permission: str | None  # granted | denied | None (unchanged)
    sentence: str
    state: str  # done | nothing (nothing changed in Messages) | maybe (something may have)
    code: str  # for the log: never names or text
    denied_by: str | None = None  # accessibility | automation


_STDERR_NUMBER = re.compile(r"\((-?\d{1,6})\)\s*$")


def _answer(outcome: Outcome) -> tuple[str, int | None]:
    """(code, error number) from what a script printed ("error:-1719" → ("error", -1719))."""
    text = outcome.stdout.strip()
    head, number = text, None
    for sep in (":", "\t"):
        before, found, after = text.partition(sep)
        if found and before in ("error", "error-late"):
            head = before
            try:
                number = int(after.strip())
            except ValueError:
                number = None
            break
    if number is None and outcome.returncode != 0:
        match = _STDERR_NUMBER.search((outcome.stderr.splitlines() or [""])[-1])
        number = int(match.group(1)) if match else None
    return head, number


def _denial(outcome: Outcome, number: int | None) -> str | None:
    if number in (-1719, -25211) or DENIED.search(outcome.stderr):
        return "accessibility"
    if number == -1743 or AUTOMATION_DENIED.search(outcome.stderr):
        return "automation"
    return None


def _common(outcome: Outcome, timeout: float) -> Interp | None:
    """Timeouts, permission errors and script failures, the same for every action."""
    if outcome.timed_out:
        return Interp(None, f"Messages didn't respond within {int(timeout)} seconds.", "maybe", "timed out")
    code, number = _answer(outcome)
    denial = _denial(outcome, number)
    if denial or outcome.returncode != 0 or code in ("error", "error-late"):
        if denial:  # macOS refused before anything happened (unless it was mid-way, which it never is)
            state = "maybe" if code == "error-late" else "nothing"
            if denial == "accessibility":
                return Interp("denied", DENIED_TEXT, state, "not allowed (Accessibility)", "accessibility")
            return Interp("denied", AUTOMATION_TEXT, state, "not allowed (Automation)", "automation")
        state = "nothing" if code == "error" else "maybe"
        if outcome.returncode != 0:
            detail = outcome.stderr.splitlines()[-1][:160] if outcome.stderr else f"exit {outcome.returncode}"
            return Interp(None, f"The Messages script failed: {detail}", "maybe", f"failed (exit {outcome.returncode})")
        return Interp(None, f"The Messages script failed (error {number}).", state, f"{code} {number}")
    return None


def _specific(outcome: Outcome, answers: dict[str, tuple[str | None, str, str]], title: str,
              unexpected_state: str) -> Interp:
    code = outcome.stdout.strip()
    if code in answers:
        permission, sentence, state = answers[code]
        return Interp(permission, sentence.format(title=title), state, code)
    return Interp(None, "The Messages script gave an unexpected answer.", unexpected_state, "unexpected answer")


def outcome_code(outcome: Outcome) -> str:
    """A short code for the log: never names or UI text."""
    base = _common(outcome, TIMEOUT)
    if base is not None:
        return base.code
    answer = outcome.stdout.strip()
    return answer if re.fullmatch(r"[a-z-]{1,24}", answer) else "unexpected answer"


MARK_READ_ANSWERS: dict[str, tuple[str | None, str, str]] = {
    "ok-menu": ("granted", "Marked {title} read in Messages.", "done"),
    "ok-context": ("granted", "Marked {title} read in Messages (from its context menu).", "done"),
    "row-not-found": ("granted", "Couldn't find {title} in Messages' sidebar.", "nothing"),
    "no-mark-item": ("granted", "Messages didn't offer “Mark as Read” for {title}.", "nothing"),
    "no-window": ("granted", "Messages has no window open, so nothing was marked read.", "nothing"),
    "not-running": (None, "Messages isn't open, so nothing was marked read.", "nothing"),
}


def interpret(outcome: Outcome, title: str) -> tuple[str | None, str, bool]:
    """Mark read: (permission or None for unchanged, lastResult sentence, succeeded)."""
    base = _common(outcome, TIMEOUT)
    if base is not None:
        return base.permission, base.sentence, False
    result = _specific(outcome, MARK_READ_ANSWERS, title, "nothing")
    return result.permission, result.sentence, result.state == "done"


TAPBACK_ANSWERS: dict[str, tuple[str | None, str, str]] = {
    "chosen-menu": ("granted", "Sent the tapback in Messages.", "done"),
    "chosen-overlay": ("granted", "Sent the tapback in Messages.", "done"),
    "not-running": (None, "Messages isn't open, so the tapback wasn't sent.", "nothing"),
    "no-window": ("granted", "Messages has no window open, so the tapback wasn't sent.", "nothing"),
    "row-not-found": ("granted", "Couldn't find {title} in Messages' sidebar.", "nothing"),
    "not-selected": ("granted", "Couldn't open {title} in Messages.", "nothing"),
    "bubble-not-found": ("granted", "Couldn't find that message in Messages. Tapbacks work on recent messages.",
                         "nothing"),
    "no-menu": ("granted", "Messages didn't open a menu for that message.", "nothing"),
    "no-tapback-item": ("granted", "Messages didn't offer that tapback.", "nothing"),
    "no-tapback-button": ("granted", "Messages didn't offer that tapback.", "nothing"),
    "no-kind": (None, "That isn't one of Messages' tapbacks.", "nothing"),
}


def interpret_tapback(outcome: Outcome, title: str) -> Interp:
    return _common(outcome, TAPBACK_TIMEOUT) or _specific(outcome, TAPBACK_ANSWERS, title, "maybe")


REPLY_ANSWERS: dict[str, tuple[str | None, str, str]] = {
    "sent-axvalue": ("granted", "Sent as a reply in Messages.", "done"),
    "sent-paste": ("granted", "Sent as a reply in Messages.", "done"),
    "returned-unconfirmed": ("granted", "Messages didn't confirm your reply.", "maybe"),
    "typed-not-sent": ("granted", "Your reply is in Messages' reply field, but it wasn't sent.", "maybe"),
    "typed-unknown": ("granted", "Messages didn't confirm your reply.", "maybe"),
    "not-running": (None, "Messages isn't open.", "nothing"),
    "no-window": ("granted", "Messages has no window open.", "nothing"),
    "row-not-found": ("granted", "Couldn't find {title} in Messages' sidebar.", "nothing"),
    "not-selected": ("granted", "Couldn't open {title} in Messages.", "nothing"),
    "bubble-not-found": ("granted", "Couldn't find the message you're replying to in Messages.", "nothing"),
    "no-menu": ("granted", "Messages didn't open a menu for that message.", "nothing"),
    "no-reply-item": ("granted", "Messages didn't offer Reply for that message.", "nothing"),
    "no-reply-field": ("granted", "Messages didn't open its reply field.", "nothing"),
    "field-busy": ("granted", "Messages' reply field already had text in it.", "nothing"),
    "clipboard-busy": ("granted", "The clipboard holds more than text, so Backchannel didn't paste.", "nothing"),
    "nothing-typed": ("granted", "Couldn't put the text in Messages' reply field.", "nothing"),
    "no-text": (None, "There was no text to send.", "nothing"),
}


def interpret_reply(outcome: Outcome, title: str) -> Interp:
    """Anything we can't account for counts as "maybe typed": a reply is never sent twice."""
    return _common(outcome, REPLY_TIMEOUT) or _specific(outcome, REPLY_ANSWERS, title, "maybe")


def interpret_probe(outcome: Outcome) -> Interp:
    if outcome.timed_out:
        return Interp(None, f"macOS didn't answer within {int(PROBE_TIMEOUT)} seconds. If it asks whether "
                            "Backchannel may control System Events, click OK.", "nothing", "timed out")
    base = _common(outcome, PROBE_TIMEOUT)
    if base is not None:
        return Interp(base.permission, base.sentence if base.permission else
                      base.sentence.replace("The Messages script failed", "The permission check failed"),
                      "nothing", base.code, base.denied_by)
    if outcome.stdout.strip() == "ready":
        return Interp(None, "Ready", "done", "ready")
    return Interp(None, "The permission check gave an unexpected answer.", "nothing", "unexpected answer")


# --------------------------------------------------------------------------- dry-run simulation


class Simulator:
    """Dry-run with the fake chat.db: write the row an action would have produced, like simulated sends."""

    def __init__(self, chat_db: Path, my_handle: Callable[[], str | None]):
        self.chat_db = Path(chat_db)
        self.my_handle = my_handle

    def tapback(self, job: TapbackJob) -> str:
        index = TAPBACK_KINDS.index(job.kind)
        quoted = f"“{job.target_text}”" if job.target_text else "a message"
        if job.remove:
            kind_type, text = 3000 + index, f"Removed {H.REMOVED_NOUNS[3000 + index]} from {quoted}"
        else:
            kind_type, text = 2000 + index, f"{H.TAPBACKS[2000 + index][2]} {quoted}"
        _, guid = fakedb.write_as_messages_app(
            self.chat_db, job.target_chat_id, text=text, my_handle=self.my_handle(),
            associated_message_guid=f"p:0/{job.message_id}", associated_message_type=kind_type,
            associated_message_range_location=0, associated_message_range_length=len(job.target_text),
        )
        return guid

    def reply(self, job: ReplyJob) -> str:
        _, guid = fakedb.write_as_messages_app(
            self.chat_db, job.root_chat_id, text=job.text, my_handle=self.my_handle(),
            thread_originator_guid=job.root_id, thread_originator_part=f"0:0:{len(job.root_text)}",
        )
        return guid


# --------------------------------------------------------------------------- the helper queue


class MessagesUI:
    def __init__(self, db: ChatDB, state: State, runner: Runner | None, *,
                 publish: Callable[[dict[str, Any]], None] | None = None, simulator: Simulator | None = None,
                 convs: Any = None, builder: Any = None, contacts: Any = None, check_after: float = CHECK_AFTER):
        self.db = db
        self.state = state
        self.runner = runner
        self.publish = publish
        self.simulator = simulator if runner is None else None
        self.convs = convs
        self.builder = builder
        self.contacts = contacts
        self.check_after = check_after
        self.verify_seconds = VERIFY_SECONDS
        self._jobs: queue.Queue[Any] = queue.Queue()
        self._queued: set[tuple] = set()
        self._lock = threading.Lock()
        self._busy = 0
        self._thread: threading.Thread | None = None
        self._status = {"permission": "unknown", "lastResult": None, "lastAt": None, "deniedBy": None,
                        **(state.get_kv(STATUS_KEY) or {})}

    # ------------------------------------------------------------------ settings and health

    def _setting(self, key: str) -> bool:
        return bool(self.state.get_settings().get(key))

    @property
    def mark_read_on(self) -> bool:
        return self._setting("markReadInMessages")

    @property
    def tapbacks_on(self) -> bool:
        return self._setting("tapbacksViaMessages")

    @property
    def replies_on(self) -> bool:
        return self._setting("threadRepliesViaMessages")

    @property
    def enabled(self) -> bool:
        settings = self.state.get_settings()
        return any(settings.get(key) for key in HELPER_SETTINGS)

    @property
    def can_act(self) -> bool:
        """Tapbacks and replies can run: the real (or a test) runner, or the fake-DB simulator."""
        return self.runner is not None or self.simulator is not None

    def health(self) -> dict[str, Any]:
        with self._lock:
            status = dict(self._status)
        return {
            "enabled": self.enabled,
            "permission": status["permission"],
            "lastResult": status["lastResult"],
            "lastAt": iso(status["lastAt"]) if status["lastAt"] else None,
        }

    def _record(self, interp: Interp, result: str, label: str, *, probe: bool = False) -> None:
        with self._lock:
            if interp.permission:
                self._status["permission"] = interp.permission
                self._status["deniedBy"] = interp.denied_by if interp.permission == "denied" else None
            elif probe and interp.state == "done" and self._status.get("deniedBy") == "automation":
                # Automation is allowed now; Accessibility isn't proven until a real action works.
                self._status["permission"] = "unknown"
                self._status["deniedBy"] = None
            self._status["lastResult"] = result
            self._status["lastAt"] = time.time()
            snapshot = dict(self._status)
        self.state.set_kv(STATUS_KEY, snapshot)
        log.info("%s in Messages: %s", label, interp.code)  # a code, never names or text

    def _ui_action(self, action: str, conversation_id: str, result: str, detail: str, *,
                   message_id: str | None = None, client_id: str | None = None) -> None:
        if self.publish is not None:
            self.publish({"type": "ui-action", "action": action, "conversationId": conversation_id,
                          "messageId": message_id, "clientId": client_id, "result": result, "detail": detail})

    # ------------------------------------------------------------------ building jobs

    def _names(self, conv: Conv) -> tuple[str, list[str], int]:
        title = conv_title(conv, self.convs.me())
        if conv.kind == "dm" and conv.persons:
            person = conv.persons[0]
            contact = self.contacts.lookup(person.handle) if self.contacts is not None else None
            names, loose = candidate_names(
                title,
                full_name=" ".join(p for p in (contact.first, contact.last) if p) if contact else None,
                nickname=contact.nickname if contact else None,
                organization=contact.organization if contact else None,
                person_name=person.name, handle=person.handle, short_name=person.short_name)
        else:
            names, loose = candidate_names(title)
        return title, names, loose

    def mark_read_job(self, conv: Conv, title: str | None = None) -> Job:
        conv_name, names, loose = self._names(conv)
        if title and title != conv_name:
            names, loose = [title, *[n for n in names if n != title]], loose + 1
        return Job(conv.id, title or conv_name, names, list(conv.chat_ids), loose)

    def target(self, conv: Conv, message_id: str) -> tuple[Any, dict[str, Any]]:
        """The chat.db row and Message for a tapback target or reply root; NotFound / ValueError otherwise."""
        rows = self.db.messages_by_guid([message_id])
        if not rows:
            raise NotFound("That message isn't in chat.db.")
        in_conv = [r for r in rows if r["chat_id"] in conv.chat_ids]
        row = in_conv[0] if in_conv else rows[0]
        if H.is_reaction_type(row["associated_message_type"]):
            raise ValueError("That's a tapback, not a message.")
        if (row["item_type"] or 0) != 0:
            raise ValueError("That's a conversation event, not a message.")
        message = self.builder.by_guid(message_id)
        if message is None or message.get("event"):
            raise ValueError("That's a conversation event, not a message.")
        if message.get("unsent"):
            raise ValueError("That message was unsent.")
        if not in_conv:
            raise ValueError("That message isn't in this conversation.")
        return row, message

    def bubble_for(self, conv: Conv, message: dict[str, Any]) -> Bubble:
        needles, exacts = bubble_terms(message)
        kind = _attachment_kind(message) if not _first_line(message.get("text") or "") else None
        if message.get("fromMe"):
            senders = ["Your ", "You,", "You:"]
            same_sender = lambda m: bool(m.get("fromMe"))  # noqa: E731
        else:
            handle = message.get("senderHandle")
            person = next((p for p in conv.persons if handle and (handle == p.handle or handle in p.handles)), None)
            contact = self.contacts.lookup(handle) if (self.contacts is not None and handle) else None
            full = " ".join(p for p in (contact.first, contact.last) if p) if contact else ""
            names = [person.name, person.short_name, full] if person else [format_handle(handle or ""), full]
            senders = [n for n in dict.fromkeys(names) if n and len(n) >= 3]
            same_sender = lambda m: not m.get("fromMe") and m.get("senderHandle") == handle  # noqa: E731
        recent = self.builder.recent(conv, RECENT_FOR_SKIPS)
        ids = [m["id"] for m in recent]
        newer = recent[ids.index(message["id"]) + 1:] if message["id"] in ids else recent
        hits = [m for m in newer if _bubble_hit(m, needles, exacts, kind)]
        return Bubble(needles, exacts, senders, skip_sender=sum(1 for m in hits if same_sender(m)),
                      skip_any=len(hits))

    def tapback_job(self, conv: Conv, message_id: str, kind: str, remove: bool) -> TapbackJob:
        if kind not in TAPBACK_KINDS:
            raise ValueError("That isn't one of Messages' tapbacks.")
        row, message = self.target(conv, message_id)
        title, names, loose = self._names(conv)
        return TapbackJob(conv.id, title, names, loose, list(conv.chat_ids), message_id, kind, remove,
                          self.bubble_for(conv, message), target_chat_id=row["chat_id"],
                          target_text=message.get("text") or "")

    def reply_job(self, conv: Conv, root_id: str, text: str, client_id: str) -> ReplyJob | None:
        """None when the root can't be found in Messages' terms (the reply then goes out the normal way)."""
        try:
            row, message = self.target(conv, root_id)
        except (LookupError, ValueError):
            return None
        title, names, loose = self._names(conv)
        return ReplyJob(client_id, conv.id, title, names, loose, list(conv.chat_ids), root_id, text,
                        self.bubble_for(conv, message), root_chat_id=row["chat_id"],
                        root_text=message.get("text") or "")

    # ------------------------------------------------------------------ queueing

    def request(self, job: Any) -> bool:
        """Queue a job (never blocks). Repeats of a queued mark-read (or probe) are dropped.

        Mark read and the permission probe need a runner (never the simulator);
        a tapback that can't run at all is answered right away with a failed
        `ui-action`.
        """
        if job.action == "mark-read":
            if self.runner is None or not self.mark_read_on:
                return False
        elif job.action == "probe":
            if self.runner is None:
                return False
        elif job.action == "tapback" and not self.can_act:
            self._ui_action("tapback", job.conversation_id, "failed", DRY_RUN_TEXT, message_id=job.message_id)
            return False
        return self._enqueue(job)

    def _enqueue(self, job: Any) -> bool:
        key = job.key()
        with self._lock:
            if key is not None:
                if key in self._queued:
                    return False
                self._queued.add(key)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._work, name="messages-helpers", daemon=True)
                self._thread.start()
        self._jobs.put(job)
        return True

    def probe(self) -> bool:
        """Ask macOS for the Automation permission now (a helper was just turned on)."""
        return self.request(ProbeJob())

    def run_and_wait(self, job: ReplyJob, timeout: float = 120.0) -> ReplyResult:
        """Queue a reply and wait for it. If it never got to run, it's cancelled (safe to send normally)."""
        if not self.can_act:
            return ReplyResult("nothing", DRY_RUN_TEXT)
        self._enqueue(job)
        if not job.finished.wait(timeout):
            with self._lock:
                if job.state == "queued":
                    job.state = "cancelled"
                    return ReplyResult("nothing", "Messages was busy, so the reply wasn't typed there.")
            job.finished.wait()  # it's running: its own timeouts bound this
        return job.result or ReplyResult("unconfirmed", REPLY_UNCONFIRMED)

    def _work(self) -> None:
        while True:
            job = self._jobs.get()
            with self._lock:
                key = job.key()
                if key is not None:
                    self._queued.discard(key)
                cancelled = getattr(job, "state", None) == "cancelled"
                if isinstance(job, ReplyJob) and not cancelled:
                    job.state = "running"
                self._busy += 1
            try:
                if not cancelled:
                    self._dispatch(job)
            except Exception:
                log.exception("Messages helper failed (%s)", job.action)
                if isinstance(job, ReplyJob) and job.result is None:
                    job.result = (ReplyResult("unconfirmed", REPLY_UNCONFIRMED) if job.ran_script
                                  else ReplyResult("nothing", "The Messages helper failed before typing anything."))
            finally:
                if isinstance(job, ReplyJob):
                    job.state = "done"
                    job.finished.set()
                with self._lock:
                    self._busy -= 1
                self._jobs.task_done()

    def _dispatch(self, job: Any) -> None:
        if job.action == "mark-read":
            self._run_mark_read(job)
        elif job.action == "tapback":
            self._run_tapback(job)
        elif job.action == "reply":
            job.result = self._run_reply(job)
        elif job.action == "probe":
            self._run_probe()

    def wait_idle(self, timeout: float = 15.0) -> bool:
        """For tests: wait until the queue is empty and nothing is running."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                idle = not self._queued and self._busy == 0
            if idle and self._jobs.unfinished_tasks == 0:
                return True
            time.sleep(0.02)
        return False

    # ------------------------------------------------------------------ mark read

    def newest_unread(self, chat_ids: list[int], upto: int | None = None) -> int | None:
        """ROWID of the newest unread incoming message in these chats (at or below `upto`), else None."""
        if not chat_ids:
            return None
        marks = ",".join("?" * len(chat_ids))
        bound = " AND m.ROWID <= ?" if upto is not None else ""
        rows = self.db.query(
            f"""SELECT MAX(m.ROWID) AS newest FROM message m JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
                WHERE m.is_read = 0 AND m.is_from_me = 0 AND m.item_type = 0 AND {NOT_REACTION_SQL}
                  AND cmj.chat_id IN ({marks}){bound}""",
            [*chat_ids, *([upto] if upto is not None else [])],
        )
        return rows[0]["newest"] if rows and rows[0]["newest"] is not None else None

    def _run_mark_read(self, job: Job) -> None:
        if not self.mark_read_on:
            return  # turned off while this was queued
        try:
            newest = self.newest_unread(job.chat_ids)
        except ChatDBError:
            return
        if newest is None:
            return  # Messages already agrees: nothing to do
        assert self.runner is not None
        outcome = self.runner(MARK_READ_SCRIPT, job.argv(), TIMEOUT)
        base = _common(outcome, TIMEOUT)
        interp = base or _specific(outcome, MARK_READ_ANSWERS, job.title, "nothing")
        result = interp.sentence
        succeeded = interp.state == "done" and base is None
        if succeeded:
            time.sleep(self.check_after)
            try:  # only the messages that were unread when we started (new ones may have arrived since)
                if self.newest_unread(job.chat_ids, upto=newest) is not None:
                    result = result[:-1] + ", but chat.db still shows unread messages there."
                    interp.code += ", still unread in chat.db"
                    succeeded = False
            except ChatDBError:
                pass
        self._record(interp, result, "Mark read")
        # Successes stay quiet, and so does "Messages isn't open" (nothing to tell on every open).
        if not succeeded and interp.code not in ("not-running", "no-window"):
            self._ui_action("mark-read", job.conversation_id, "failed", result)

    # ------------------------------------------------------------------ tapbacks

    def current_tapback(self, message_id: str) -> str | None:
        """Your classic tapback on this message right now, from chat.db."""
        message = self.builder.by_guid(message_id)
        for reaction in (message or {}).get("reactions", []):
            if "me" in reaction["from"] and reaction["kind"] in TAPBACK_KINDS:
                return reaction["kind"]
        return None

    def verify_tapback(self, job: TapbackJob, start: int) -> bool:
        want = (3000 if job.remove else 2000) + TAPBACK_KINDS.index(job.kind)
        deadline = time.monotonic() + self.verify_seconds
        while True:
            rows = self.db.query(
                """SELECT associated_message_type AS kind FROM message
                   WHERE ROWID > ? AND is_from_me = 1
                     AND (associated_message_guid = ? OR associated_message_guid LIKE ?)""",
                (start, "bp:" + job.message_id, "p:%/" + job.message_id),
            )
            if any(r["kind"] == want for r in rows):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.25)

    def _run_tapback(self, job: TapbackJob) -> None:
        def answer(result: str, detail: str) -> None:
            self._ui_action("tapback", job.conversation_id, result, detail, message_id=job.message_id)

        if not self.tapbacks_on:
            return answer("failed", TAPBACKS_OFF)
        try:
            current = self.current_tapback(job.message_id)
            start = self.db.max_rowid()
        except ChatDBError as exc:
            return answer("failed", str(exc))
        if not job.remove and current == job.kind:
            return answer("done", "That tapback is already there.")
        if job.remove and current != job.kind:
            return answer("done", "That tapback was already removed.")
        done_text = "Removed your tapback in Messages." if job.remove else "Sent the tapback in Messages."
        if self.runner is None:
            if self.simulator is None:
                return answer("failed", DRY_RUN_TEXT)
            try:
                self.simulator.tapback(job)
            except (fakedb.RefusedError, LookupError, OSError) as exc:
                return answer("failed", str(exc))
            ok = self.verify_tapback(job, start)
            return answer("done" if ok else "failed", done_text if ok else TAPBACK_UNCONFIRMED)
        outcome = self.runner(TAPBACK_SCRIPT, job.argv(), TAPBACK_TIMEOUT)
        interp = interpret_tapback(outcome, job.title)
        ok = interp.state != "nothing" and self.verify_tapback(job, start)
        if ok:
            sentence = done_text
            interp.code += ", verified"
        elif interp.state == "nothing":
            sentence = interp.sentence
        else:
            sentence = TAPBACK_UNCONFIRMED if interp.permission != "denied" else interp.sentence
            interp.code += ", not in chat.db"
        self._record(interp, sentence, "Tapback")
        answer("done" if ok else "failed", sentence)

    # ------------------------------------------------------------------ thread replies

    def find_reply_row(self, job: ReplyJob, start: int) -> Any | None:
        """Our reply's row in chat.db (from me, in this conversation, same text), waiting up to ~5 s."""
        want = _norm(job.text)
        deadline = time.monotonic() + self.verify_seconds
        while True:
            for row in self.db.rows_after(start, 200):
                if (row["is_from_me"] and row["chat_id"] in job.chat_ids and (row["item_type"] or 0) == 0
                        and not H.is_reaction_type(row["associated_message_type"])
                        and _norm(message_text(row)) == want):
                    return row
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.25)

    def _run_reply(self, job: ReplyJob) -> ReplyResult:
        if not self.replies_on:
            return ReplyResult("nothing", "Thread replies through Messages are off.")
        start = self.db.max_rowid()
        if self.runner is None:
            if self.simulator is None:
                return ReplyResult("nothing", DRY_RUN_TEXT)
            job.ran_script = True
            self.simulator.reply(job)
            interp = Interp(None, "Sent as a reply in Messages.", "done", "simulated")
        else:
            job.ran_script = True
            outcome = self.runner(REPLY_SCRIPT, job.argv(), REPLY_TIMEOUT)
            interp = interpret_reply(outcome, job.title)
            if interp.state == "nothing":
                self._record(interp, interp.sentence, "Reply")
                return ReplyResult("nothing", interp.sentence)
        row = self.find_reply_row(job, start)
        if row is None:
            result = ReplyResult("unconfirmed", REPLY_UNCONFIRMED)
            interp.code += ", not in chat.db"
        elif row["thread_originator_guid"] == job.root_id:
            result = ReplyResult("done", "Sent as a reply in Messages.", row["guid"])
            interp.code += ", verified"
        else:
            result = ReplyResult("regular", "Messages sent it as a regular text instead of a reply.", row["guid"])
            interp.code += ", not threaded"
        if self.runner is not None:
            self._record(interp, result.detail, "Reply")
        return result

    # ------------------------------------------------------------------ permission probe

    def _run_probe(self) -> None:
        assert self.runner is not None
        interp = interpret_probe(self.runner(PROBE_SCRIPT, ["probe"], PROBE_TIMEOUT))
        self._record(interp, interp.sentence, "Permission check", probe=True)


# --------------------------------------------------------------------------- doctor --messages-ui

_ROLE = re.compile(r"^(AX[A-Za-z]+|\?)$")
_ERROR_NUMBER = re.compile(r"\((-?\d{1,6})\)\s*$")
_LOOKS_PRIVATE = re.compile(r"[0-9@+\"“”‘’«»]|'.*'")
# Titles this module looks for itself: never hidden, even with a contact named Mark.
KNOWN_TITLES = frozenset({"mark as read", "mark as unread", "mark all as read", "conversation", "reply", "tapback",
                          "tapback…", "tapback...", "react", "react…"})


def _mentions(lowered: str, name: str) -> bool:
    if " " in name:
        return name in lowered
    return re.search(rf"(?<!\w){re.escape(name)}(?!\w)", lowered) is not None


def safe_titles(raw: str, private: set[str] | frozenset[str] = frozenset()) -> str:
    """Menu/button titles, each hidden if it has digits, "@", "+" or quotes, is long, or mentions a `private` name."""
    out = []
    for title in raw.split(" | ")[:60]:
        title = title.strip()
        if title in ("missing value", "-", ""):
            out.append("---")  # a separator
            continue
        lowered = title.lower()
        if lowered not in KNOWN_TITLES and (
                len(title) > 40 or _LOOKS_PRIVATE.search(title)
                or any(len(name) >= 3 and _mentions(lowered, name) for name in private)):
            title = "(hidden)"
        out.append(title or "-")
    return " | ".join(out)


def inspect(runner: Runner = osascript_runner, timeout: float = 60.0,
            private: set[str] | frozenset[str] = frozenset()) -> list[str]:
    """Report lines about Messages' accessibility structure: roles, counts and menu/button titles only.

    Everything else the script prints is dropped; titles that look like they
    carry a name, number, address or quoted text (see `private`) are hidden.
    """
    outcome = runner(INSPECT_SCRIPT, [], timeout)
    if outcome.timed_out:
        return ["- inspection: timed out"]
    if outcome.returncode == 127:
        return ["- inspection: skipped (osascript isn't available: this needs macOS)"]
    if outcome.returncode != 0:
        if DENIED.search(outcome.stderr):
            return ["- Accessibility: NOT allowed for this app (System Settings › Privacy & Security › Accessibility)"]
        if AUTOMATION_DENIED.search(outcome.stderr):
            return ["- Automation: NOT allowed to control System Events (System Settings › Privacy & Security › Automation)"]
        last = (outcome.stderr.splitlines() or [""])[-1]
        number = _ERROR_NUMBER.search(last)  # the error number only: AppleScript errors can quote UI text
        return [f"- inspection: failed (exit {outcome.returncode}" + (f", error {number.group(1)})" if number else ")")]
    lines: list[str] = []
    tree: list[str] = []
    for raw in outcome.stdout.splitlines():
        parts = raw.split("\t")
        key = parts[0]
        if key == "accessibility" and len(parts) >= 2:
            lines.append("- Accessibility: " + ("allowed" if parts[1] == "allowed" else
                                                 f"NOT allowed (error {parts[2] if len(parts) > 2 and parts[2].lstrip('-').isdigit() else '?'})"))
        elif key == "messages_running" and len(parts) >= 2:
            lines.append("- Messages running: " + ("yes" if parts[1] == "yes" else "no"))
        elif key == "windows" and len(parts) >= 2 and parts[1].isdigit():
            lines.append(f"- windows: {parts[1]}")
        elif key in ("menu_bar", "conversation_menu") and len(parts) == 2:
            label = "menu bar" if key == "menu_bar" else "Conversation menu"
            lines.append(f"- {label}: {safe_titles(parts[1], private)}")
        elif key == "tree" and len(parts) == 4 and parts[1].isdigit() and parts[3].isdigit():
            role = parts[2] if _ROLE.match(parts[2]) else "(other)"
            tree.append(f"    {'  ' * min(int(parts[1]), 12)}{role} ({parts[3]})")
        elif key == "rows" and len(parts) == 8 and all(p.isdigit() for p in parts[1:]):
            count, desc, name, value, max_desc, max_name, max_value = parts[1:]
            lines.append(f"- row-like elements (AXRow/AXCell): {count}; with description {desc} (longest {max_desc}),"
                         f" with name {name} (longest {max_name}), with value {value} (longest {max_value})")
        elif key == "bubble" and len(parts) == 2 and parts[1] == "none":
            lines.append("- newest bubble: none found right of the selected conversation")
        elif key == "bubble" and len(parts) == 3:
            role = parts[1] if _ROLE.match(parts[1]) else "(other)"
            actions = [a if re.fullmatch(r"AX[A-Za-z]+", a) else "(other)" for a in parts[2].split(",") if a]
            lines.append(f"- newest bubble: {role} (actions: {', '.join(actions) or 'none'})")
        elif key == "bubble_menu" and len(parts) == 2:
            lines.append("- its context menu: " + ("didn't open" if parts[1] == "(none)" else safe_titles(parts[1], private)))
        elif key in ("tapback_item", "reply_item") and len(parts) == 2 and parts[1] in ("yes", "no"):
            lines.append(f"- {'Tapback' if key == 'tapback_item' else 'Reply'} item: {parts[1]}")
        elif key == "tapback_buttons" and len(parts) == 2:
            lines.append("- Tapback picker buttons: " + (safe_titles(parts[1], private) if parts[1] else "none appeared"))
        elif key == "reply_field" and len(parts) == 2 and parts[1] == "none":
            lines.append("- reply field: no text field got focus")
        elif key == "reply_field" and len(parts) == 3 and parts[2].lower() in ("true", "false"):
            role = parts[1] if _ROLE.match(parts[1]) else "(other)"
            lines.append(f"- reply field: {role}, AXValue settable: {'yes' if parts[2].lower() == 'true' else 'no'}")
    if tree:
        lines.append("- front window (roles and child counts):")
        lines.extend(tree[:400])
    return lines or ["- inspection: no output"]
