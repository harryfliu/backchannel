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

import itertools
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
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from . import fakedb, mouse
from . import heuristics as H
from .chatdb import ChatDB, ChatDBError, NOT_REACTION_SQL
from .conversations import Conv, NotFound, conv_title, message_text
from .handles import format_handle
from .linkpreview import URL_BALLOON
from .messages_scripts import (COMPOSE_SCRIPT, INSPECT_SCRIPT, MARK_READ_SCRIPT, MARK_UNREAD_SCRIPT, PROBE_SCRIPT,
                               REPLY_AIM_SCRIPT, REPLY_FINISH_SCRIPT, REPLY_SCRIPT,
                               REPLY_TEST_SCRIPT, TAPBACK_SCRIPT)
from .state import State
from .timeutil import iso

__all__ = [
    "INSPECT_SCRIPT", "MARK_READ_SCRIPT", "MARK_UNREAD_SCRIPT", "PROBE_SCRIPT", "REPLY_SCRIPT", "TAPBACK_SCRIPT",
    "Bubble", "Job", "UnreadJob",
    "MessagesUI", "Outcome", "ProbeJob", "ReplyJob", "ReplyResult", "Simulator", "TapbackJob", "candidate_names",
    "inspect", "interpret", "interpret_probe", "interpret_reply", "interpret_tapback", "osascript_runner",
    "outcome_code", "safe_titles",
]

log = logging.getLogger("backchannel.messages_ui")

# Each step into Messages costs about a tenth of a second on a busy macOS 26 Messages window.
# Finding a chat may mean scrolling Messages' sidebar a page at a time (and back, for read sync).
TIMEOUT = 30.0  # mark read
TAPBACK_TIMEOUT = 45.0
REPLY_TIMEOUT = 60.0
COMPOSE_TIMEOUT = 45.0
PROBE_TIMEOUT = 90.0
VERIFY_SECONDS = 5.0
CHECK_AFTER = 3.0
STATUS_KEY = "messages_ui_status"
HELPER_SETTINGS = ("markReadInMessages", "tapbacksViaMessages", "threadRepliesViaMessages", "linkPreviewsViaMessages")
READ_STATE = ("mark-read", "mark-unread")  # one queue slot per conversation: the newest of the two wins

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
# Real replies to other people wait until one has threaded on this Mac (in the user's own chat).
REPLY_PROOF_KEY = "reply_proof_v2"  # v1 came from a test reply to the newest message: proved nothing about aiming
REPLY_UNPROVEN = ("real replies haven't worked on this Mac yet. Reply in a thread to an older message in your own "
                  "chat first; once one threads there, replies to others go through too.")

# The six classic tapbacks, their chat.db offsets (2000 + k adds, 3000 + k removes) and what the
# Messages app may call them (button descriptions or menu titles).
TAPBACK_KINDS = ("love", "like", "dislike", "laugh", "emphasize", "question")
EMOJI_KIND = "emoji"  # any emoji as a tapback (iOS 18 / macOS 15): 2006 adds, 3006 removes
TAPBACK_QUICK_KEY = "tapback_quick"
_VS16 = "\ufe0f"
_KEYCAP = "\u20e3"


def clean_tapback_emoji(value: str | None) -> str | None:
    """The emoji for an emoji tapback, or None when it isn't one: 1–16 characters, at least one
    beyond ASCII, no letters or spaces (digits only in keycaps like 1️⃣)."""
    text = (value or "").strip()
    if not 1 <= len(text) <= 16 or all(ord(ch) < 128 for ch in text):
        return None
    if any(ch.isspace() or (ord(ch) < 128 and ch.isalpha()) for ch in text):
        return None
    if any(ord(ch) < 128 and ch.isdigit() for ch in text) and _KEYCAP not in text:
        return None
    return text


def same_emoji(a: str | None, b: str | None) -> bool:
    """Equal apart from the emoji presentation selector (U+FE0F), which Messages may add or drop."""
    return (a or "").replace(_VS16, "") == (b or "").replace(_VS16, "")


def emoji_titles(emoji: str) -> list[str]:
    """How Messages may title the tapback action for this emoji: as given, without U+FE0F, with it."""
    bare = emoji.replace(_VS16, "")
    return list(dict.fromkeys([emoji, bare, bare + _VS16 if len(bare) == 1 else emoji]))
SYNONYMS: dict[str, tuple[str, ...]] = {
    "love": ("Heart", "Love", "Loved"),
    "like": ("Thumbs up", "Like", "Liked"),
    "dislike": ("Thumbs down", "Dislike", "Disliked"),
    # macOS 26 names this one "Ha ha" with an unusual space, so plain "Ha" is the catch-all.
    "laugh": ("Ha ha", "Haha", "Ha-ha", "Laugh", "Ha"),
    "emphasize": ("Exclamation mark", "Exclamation marks", "Emphasize", "!!"),
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
    strict = [(short_name or "").strip()]
    parts = (full_name or person_name or "").split()
    if len(parts) >= 2 and parts[-1][:1].isalpha():
        strict.append(f"{parts[0]} {parts[-1][0]}.")  # Contacts' short name format "First L."
    for short in strict:
        if short and short.lower() not in (n.lower() for n in names):
            names.append(short)
    return names, count


_LINK_PHONE = re.compile(r"^\+?\d{3,15}$")
_LINK_EMAIL = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")


def messages_link(conv: Conv) -> str | None:
    """Messages' own link to a one-to-one chat (imessage: or sms:), which opens it directly, even
    when it's far down Messages' sidebar. None for groups and handles that aren't a plain number or
    address (nothing else may end up in the link)."""
    if conv.kind != "dm" or len(conv.persons) != 1:
        return None
    handle = conv.persons[0].handle or ""
    if not (_LINK_PHONE.match(handle) or _LINK_EMAIL.match(handle)):
        return None
    services = {(c.service or "").lower() for c in conv.chats}
    if "@" in handle or "imessage" in services:
        return f"imessage://{handle}"
    return f"sms:{handle}"


_EMOJI_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,:'’&()!#*+-]{0,79}$")


def clean_emoji_names(names: list[str] | None) -> list[str]:
    """An emoji's names as the app knows them ("face with tears of joy", "joy"), to find its cell in
    Messages' emoji picker: plain words only, at most 6."""
    out: list[str] = []
    for name in names or []:
        name = " ".join(str(name).replace("_", " ").split())
        if _EMOJI_NAME.match(name) and name.lower() not in (n.lower() for n in out):
            out.append(name)
    return out[:6]


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
    times: list[str] = field(default_factory=list)  # the minute it was sent, as Messages may show it
    skip_minute: int = 0  # newer look-alikes sent in that same minute (self-chats show every message twice)

    def argv(self) -> list[str]:
        return ([f"T:{n}" for n in self.needles] + [f"W:{w}" for w in self.exacts] + [f"S:{s}" for s in self.senders]
                + [f"K:{self.skip_sender}", f"J:{self.skip_any}"] + [f"H:{t}" for t in self.times]
                + ([f"Q:{self.skip_minute}"] if self.times else []))


def minute_marks(sent_at: str | None) -> tuple[list[str], tuple[int, ...] | None]:
    """How Messages may print the minute a message was sent in its bubble's label (" 1:37" for
    "1:37 PM", " 13:37" for 24-hour clocks; the leading space keeps " 1:37" from matching "11:37"),
    and that minute in local time."""
    if not sent_at:
        return [], None
    try:
        when = datetime.fromisoformat(sent_at).astimezone()
    except ValueError:
        return [], None
    marks = [f" {when.hour % 12 or 12}:{when.minute:02d}", f" {when.hour:02d}:{when.minute:02d}",
             f" {when.hour}:{when.minute:02d}"]
    return list(dict.fromkeys(marks)), (when.year, when.month, when.day, when.hour, when.minute)


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
    force: bool = False  # run even when chat.db shows nothing unread (it was marked unread)
    members: list[str] = field(default_factory=list)  # an unnamed group's people (G:, "nickname|first|full")
    action = "mark-read"

    def key(self) -> tuple | None:
        return ("read-state", self.conversation_id)  # shared with UnreadJob: the newest wish wins

    def argv(self) -> list[str]:
        return ["mark-read", *names_argv(self.candidates, self.loose), *[f"G:{m}" for m in self.members]]


@dataclass
class UnreadJob:
    """Mark a conversation unread in Messages (its row's "Mark as Unread")."""

    conversation_id: str
    title: str
    candidates: list[str]
    chat_ids: list[int] = field(default_factory=list)
    loose: int | None = None
    members: list[str] = field(default_factory=list)
    action = "mark-unread"

    def key(self) -> tuple | None:
        return ("read-state", self.conversation_id)

    def argv(self) -> list[str]:
        return ["mark-unread", *names_argv(self.candidates, self.loose), *[f"G:{m}" for m in self.members]]


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
    links: list[str] = field(default_factory=list)  # Messages' own link to the chat (one-to-one chats)
    members: list[str] = field(default_factory=list)
    emoji: str | None = None  # kind "emoji": which one
    emoji_names: list[str] = field(default_factory=list)  # its names, to find it in Messages' emoji picker
    action = "tapback"

    def key(self) -> tuple | None:
        return None

    def titles(self) -> list[str]:
        """The tapback action's title in Messages: its words for the classics, the emoji itself for
        an emoji (Messages offers its recent ones as actions titled with the emoji)."""
        return emoji_titles(self.emoji or "") if self.kind == EMOJI_KIND else list(SYNONYMS[self.kind])

    def argv(self) -> list[str]:
        picker = ([*[f"E:{e}" for e in self.titles()], *[f"Y:{n}" for n in self.emoji_names]]
                  if self.kind == EMOJI_KIND else [])
        return ["tapback", *names_argv(self.candidates, self.loose), *self.bubble.argv(),
                *[f"R:{s}" for s in self.titles()], *[f"U:{u}" for u in self.links],
                *[f"G:{m}" for m in self.members], *picker]


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
    root_is_newest: bool = False  # Messages' "Reply to Last Message" answers it; older ones need a right-click
    state: str = "queued"  # queued | running | done | cancelled
    ran_script: bool = False
    result: ReplyResult | None = None
    finished: threading.Event = field(default_factory=threading.Event)
    links: list[str] = field(default_factory=list)
    members: list[str] = field(default_factory=list)
    action = "reply"

    def key(self) -> tuple | None:
        return None

    def argv(self) -> list[str]:
        return ["reply", *names_argv(self.candidates, self.loose), *self.bubble.argv(), f"M:{self.text}",
                *[f"U:{u}" for u in self.links], *[f"G:{m}" for m in self.members]]


@dataclass
class ComposeJob:
    """A message that is only a link, sent from the chat's own text field so it carries a preview."""

    client_id: str
    conversation_id: str
    title: str
    candidates: list[str]
    loose: int
    chat_ids: list[int]
    text: str
    links: list[str] = field(default_factory=list)
    members: list[str] = field(default_factory=list)
    state: str = "queued"  # queued | running | done | cancelled
    ran_script: bool = False
    result: ReplyResult | None = None
    finished: threading.Event = field(default_factory=threading.Event)
    action = "compose"

    def key(self) -> tuple | None:
        return None

    def argv(self) -> list[str]:
        return ["compose", *names_argv(self.candidates, self.loose), f"M:{self.text}",
                *[f"U:{u}" for u in self.links], *[f"G:{m}" for m in self.members]]


_LINK_ONLY = re.compile(r"^(?:https?://|www\.)[^\s<>\"']+$", re.IGNORECASE)


def only_a_link(text: str | None) -> bool:
    """A message that is a single link and nothing else (the ones sent through Messages for a preview)."""
    return bool(text) and bool(_LINK_ONLY.match(text.strip())) and len(text.strip()) <= 2000


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
_TRAIL_JUNK = re.compile(r"[^a-z0-9:,/ -]")


def _split(outcome: Outcome) -> tuple[str, str]:
    """(answer, trail). Scripts may add "|<trail>": how they got to the chat, as step names and
    item positions ("link no row3/0 p:3,14 o:3"), never names or text."""
    first = (outcome.stdout.strip().splitlines() or [""])[0]  # later lines are data (see quick_emoji)
    answer, _, trail = first.partition("|")
    return answer.strip(), _TRAIL_JUNK.sub("", trail.lower()).strip()[:120]


def quick_emoji(outcome: Outcome) -> list[str] | None:
    """The emoji Messages offered as quick tapbacks on that message (the script's "quick" line), or
    None when the script didn't say."""
    for line in outcome.stdout.strip().splitlines()[1:]:
        key, _, rest = line.partition("\t")
        if key == "quick":
            found = [e for e in (clean_tapback_emoji(part) for part in rest.split()) if e]
            return list(dict.fromkeys(found))[:8]
    return None


def _answer(outcome: Outcome) -> tuple[str, int | None]:
    """(code, error number) from what a script printed ("error:-1719" → ("error", -1719))."""
    text, _ = _split(outcome)
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
    code, trail = _split(outcome)
    suffix = f" [{trail}]" if trail else ""
    if code in answers:
        permission, sentence, state = answers[code]
        return Interp(permission, sentence.format(title=title), state, code + suffix)
    return Interp(None, "The Messages script gave an unexpected answer.", unexpected_state, "unexpected answer" + suffix)


def outcome_code(outcome: Outcome) -> str:
    """A short code for the log: never names or UI text."""
    base = _common(outcome, TIMEOUT)
    if base is not None:
        return base.code
    answer, _ = _split(outcome)
    return answer if re.fullmatch(r"[a-z-]{1,24}", answer) else "unexpected answer"


MARK_READ_ANSWERS: dict[str, tuple[str | None, str, str]] = {
    "ok-action": ("granted", "Marked {title} read in Messages.", "done"),
    "already-read": ("granted", "{title} was already read in Messages.", "done"),
    "no-list": ("granted", "Couldn't find Messages' conversation list.", "nothing"),
    "ok-menu": ("granted", "Marked {title} read in Messages.", "done"),
    "ok-context": ("granted", "Marked {title} read in Messages (from its context menu).", "done"),
    "row-not-found": ("granted", "Couldn't find {title} in Messages' sidebar.", "nothing"),
    "no-mark-item": ("granted", "Messages didn't offer “Mark as Read” for {title}.", "nothing"),
    "no-window": ("granted", "Messages has no window open, so nothing was marked read.", "nothing"),
    "not-running": (None, "Messages isn't open, so nothing was marked read.", "nothing"),
}


MARK_UNREAD_ANSWERS: dict[str, tuple[str | None, str, str]] = {
    "ok-action": ("granted", "Marked {title} unread in Messages.", "done"),
    "already-unread": ("granted", "{title} was already unread in Messages.", "done"),
    "no-list": ("granted", "Couldn't find Messages' conversation list.", "nothing"),
    "row-not-found": ("granted", "Couldn't find {title} in Messages' sidebar, so it's unread only here.", "nothing"),
    "no-mark-item": ("granted", "Messages didn't offer “Mark as Unread” for {title}.", "nothing"),
    "no-window": ("granted", "Messages has no window open, so nothing was marked unread there.", "nothing"),
    "not-running": (None, "Messages isn't open, so nothing was marked unread there.", "nothing"),
}


def interpret(outcome: Outcome, title: str) -> tuple[str | None, str, bool]:
    """Mark read: (permission or None for unchanged, lastResult sentence, succeeded)."""
    base = _common(outcome, TIMEOUT)
    if base is not None:
        return base.permission, base.sentence, False
    result = _specific(outcome, MARK_READ_ANSWERS, title, "nothing")
    return result.permission, result.sentence, result.state == "done"


TAPBACK_ANSWERS: dict[str, tuple[str | None, str, str]] = {
    "chosen-action": ("granted", "Sent the tapback in Messages.", "done"),
    "chosen-picker": ("granted", "Sent the tapback in Messages.", "done"),
    "no-emoji-picker": ("granted", "Messages didn't open its emoji picker for that message.", "nothing"),
    "no-emoji-in-picker": ("granted", "Couldn't find that emoji in Messages' emoji picker.", "nothing"),
    "no-emoji-press": ("granted", "Messages' emoji picker didn't take that emoji.", "nothing"),
    "no-list": ("granted", "Couldn't find Messages' conversation list.", "nothing"),
    "no-transcript": ("granted", "Couldn't find the open conversation in Messages.", "nothing"),
    "chosen-menu": ("granted", "Sent the tapback in Messages.", "done"),
    "chosen-overlay": ("granted", "Sent the tapback in Messages.", "done"),
    "not-running": (None, "Messages isn't open, so the tapback wasn't sent.", "nothing"),
    "no-window": ("granted", "Messages has no window open, so the tapback wasn't sent.", "nothing"),
    "row-not-found": ("granted", "Couldn't find {title} in Messages' sidebar.", "nothing"),
    "not-selected": ("granted", "Couldn't open {title} in Messages.", "nothing"),
    "bubble-not-found": ("granted", "Couldn't find that message in Messages, even after scrolling up.", "nothing"),
    "no-menu": ("granted", "Messages didn't open a menu for that message.", "nothing"),
    "no-tapback-item": ("granted", "Messages didn't offer that tapback.", "nothing"),
    "no-tapback-button": ("granted", "Messages didn't offer that tapback.", "nothing"),
    "no-kind": (None, "That isn't one of Messages' tapbacks.", "nothing"),
}


def interpret_tapback(outcome: Outcome, title: str) -> Interp:
    return _common(outcome, TAPBACK_TIMEOUT) or _specific(outcome, TAPBACK_ANSWERS, title, "maybe")


REPLY_ANSWERS: dict[str, tuple[str | None, str, str]] = {
    "sent-axvalue": ("granted", "Sent as a reply in Messages.", "done"),
    "no-list": ("granted", "Couldn't find Messages' conversation list.", "nothing"),
    "no-transcript": ("granted", "Couldn't find the open conversation in Messages.", "nothing"),
    "sent-paste": ("granted", "Sent as a reply in Messages.", "done"),
    "sent-typed": ("granted", "Sent as a reply in Messages.", "done"),
    "reply-mode-lost": ("granted", "Messages stopped replying to that message before it was sent, so nothing "
                        "was sent.", "nothing"),
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
    "no-reply-mode": ("granted", "Messages didn't switch to replying to that message, so nothing was sent.",
                      "nothing"),
    "not-visible": ("granted", "The message you're replying to isn't in view in Messages.", "nothing"),
    "field-busy": ("granted", "Messages' reply field already had text in it.", "nothing"),
    "clipboard-busy": ("granted", "The clipboard holds an image or a file, so Backchannel didn't paste your "
                       "reply.", "nothing"),
    "nothing-typed": ("granted", "Couldn't paste your reply into Messages' reply field.", "nothing"),
    "no-text": (None, "There was no text to send.", "nothing"),
}


COMPOSE_ANSWERS: dict[str, tuple[str | None, str, str]] = {
    "sent-paste": ("granted", "Sent through Messages.", "done"),
    "returned-unconfirmed": ("granted", "Messages didn't confirm your link.", "maybe"),
    "typed-not-sent": ("granted", "Your link is in Messages' text field, but it wasn't sent.", "maybe"),
    "not-running": (None, "Messages isn't open.", "nothing"),
    "no-window": ("granted", "Messages has no window open.", "nothing"),
    "no-list": ("granted", "Couldn't find Messages' conversation list.", "nothing"),
    "row-not-found": ("granted", "Couldn't find {title} in Messages' sidebar.", "nothing"),
    "not-selected": ("granted", "Couldn't open {title} in Messages.", "nothing"),
    "no-field": ("granted", "Couldn't find Messages' text field.", "nothing"),
    "field-busy": ("granted", "Messages' text field already had text in it.", "nothing"),
    "nothing-typed": ("granted", "Couldn't paste the link into Messages.", "nothing"),
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
        quoted = f"“{job.target_text}”" if job.target_text else "a message"
        extra: dict[str, Any] = {}
        if job.kind == EMOJI_KIND:
            kind_type = 3006 if job.remove else 2006
            text = f"Removed {job.emoji} from {quoted}" if job.remove else f"Reacted {job.emoji} to {quoted}"
            extra["associated_message_emoji"] = job.emoji
        else:
            index = TAPBACK_KINDS.index(job.kind)
            if job.remove:
                kind_type, text = 3000 + index, f"Removed {H.REMOVED_NOUNS[3000 + index]} from {quoted}"
            else:
                kind_type, text = 2000 + index, f"{H.TAPBACKS[2000 + index][2]} {quoted}"
        _, guid = fakedb.write_as_messages_app(
            self.chat_db, job.target_chat_id, text=text, my_handle=self.my_handle(),
            associated_message_guid=f"p:0/{job.message_id}", associated_message_type=kind_type,
            associated_message_range_location=0, associated_message_range_length=len(job.target_text), **extra,
        )
        return guid

    def compose(self, job: "ComposeJob") -> str:
        """What Messages writes for a link sent from its text field: a URL balloon."""
        chat_id = job.chat_ids[0] if job.chat_ids else None
        _, guid = fakedb.write_as_messages_app(self.chat_db, chat_id, text=job.text, my_handle=self.my_handle(),
                                               balloon_bundle_id=URL_BALLOON)
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
        # A real right-click opens an older message's own menu (server/mouse.py); tests swap these.
        self.can_click: Callable[[], bool] = mouse.available
        self.right_click: Callable[[float, float], tuple[float, float] | None] = mouse.right_click
        self.move_pointer: Callable[[float, float], None] = mouse.move
        self._jobs: queue.PriorityQueue[tuple[int, int, Any]] = queue.PriorityQueue()
        self._order = itertools.count()
        self._queued: set[tuple] = set()
        self._latest: dict[tuple, Any] = {}  # read-state key → the newest mark-read/unread asked for
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
    def links_on(self) -> bool:
        return self._setting("linkPreviewsViaMessages")

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

    def _members(self, conv: Conv) -> list[str]:
        """An unnamed group's people as Messages may name them in its sidebar and title, in whatever
        order it lists them: "nickname|first name|full name" each (G:)."""
        if conv.kind != "group" or conv.display_name:
            return []
        out = []
        for person in conv.persons:
            contact = self.contacts.lookup(person.handle) if self.contacts is not None else None
            alts = [person.short_name, contact.nickname if contact else None, contact.first if contact else None,
                    person.name]
            alts = [a.replace("|", " ").strip() for a in dict.fromkeys(a for a in alts if a and a.strip())]
            if alts:
                out.append("|".join(alts))
        return out

    def mark_read_job(self, conv: Conv, title: str | None = None, *, force: bool = False) -> Job:
        conv_name, names, loose = self._names(conv)
        if title and title != conv_name:
            names, loose = [title, *[n for n in names if n != title]], loose + 1
        return Job(conv.id, title or conv_name, names, list(conv.chat_ids), loose, force, members=self._members(conv))

    def mark_unread_job(self, conv: Conv, title: str | None = None) -> UnreadJob:
        job = self.mark_read_job(conv, title)
        return UnreadJob(job.conversation_id, job.title, job.candidates, job.chat_ids, job.loose, members=job.members)

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
        times, minute = minute_marks(message.get("sentAt"))
        same_minute = sum(1 for m in hits if minute is not None and minute_marks(m.get("sentAt"))[1] == minute)
        return Bubble(needles, exacts, senders, skip_sender=sum(1 for m in hits if same_sender(m)),
                      skip_any=len(hits), times=times, skip_minute=same_minute)

    def tapback_job(self, conv: Conv, message_id: str, kind: str, remove: bool,
                    emoji: str | None = None, names: list[str] | None = None) -> TapbackJob:
        if kind == EMOJI_KIND:
            emoji = clean_tapback_emoji(emoji)
            if emoji is None:
                raise ValueError("That isn't an emoji Messages can use as a tapback.")
        elif kind in TAPBACK_KINDS:
            emoji = None
        else:
            raise ValueError("That isn't one of Messages' tapbacks.")
        row, message = self.target(conv, message_id)
        title, names, loose = self._names(conv)
        link = messages_link(conv)
        return TapbackJob(conv.id, title, names, loose, list(conv.chat_ids), message_id, kind, remove,
                          self.bubble_for(conv, message), target_chat_id=row["chat_id"],
                          target_text=message.get("text") or "", links=[link] if link else [],
                          members=self._members(conv), emoji=emoji,
                          emoji_names=clean_emoji_names(names) if kind == EMOJI_KIND else [])

    def tapback_quick(self) -> list[str]:
        """The emoji Messages offered as quick tapbacks the last time one was sent (its recent ones)."""
        stored = self.state.get_kv(TAPBACK_QUICK_KEY) or []
        return [e for e in (clean_tapback_emoji(x) for x in stored if isinstance(x, str)) if e][:8]

    def reply_job(self, conv: Conv, root_id: str, text: str, client_id: str) -> ReplyJob | None:
        """None when the root can't be found in Messages' terms (the reply is then not sent: "Not sent as a reply")."""
        try:
            row, message = self.target(conv, root_id)
        except (LookupError, ValueError):
            return None
        title, names, loose = self._names(conv)
        link = messages_link(conv)
        return ReplyJob(client_id, conv.id, title, names, loose, list(conv.chat_ids), root_id, text,
                        self.bubble_for(conv, message), root_chat_id=row["chat_id"],
                        root_text=message.get("text") or "", links=[link] if link else [],
                        members=self._members(conv),
                        root_is_newest=not self._newer_than_root(conv, root_id, message.get("text") or ""))

    def compose_job(self, conv: Conv, text: str, client_id: str) -> ComposeJob:
        title, names, loose = self._names(conv)
        link = messages_link(conv)
        return ComposeJob(client_id, conv.id, title, names, loose, list(conv.chat_ids), text.strip(),
                          links=[link] if link else [], members=self._members(conv))

    def open_argv(self, conv: Conv) -> list[str]:
        """The inspect script's "open" mode for this conversation (doctor --messages-open)."""
        _title, names, loose = self._names(conv)
        link = messages_link(conv)
        return ["open", *names_argv(names, loose), *([f"U:{link}"] if link else []),
                *[f"G:{m}" for m in self._members(conv)]]

    # ------------------------------------------------------------------ queueing

    def request(self, job: Any) -> bool:
        """Queue a job (never blocks). Repeats of a queued mark-read (or probe) are dropped; a
        mark-unread replaces a waiting mark-read for the same conversation and vice versa.

        Mark read/unread and the permission probe need a runner (never the simulator);
        a tapback that can't run at all is answered right away with a failed
        `ui-action`.
        """
        if job.action in READ_STATE:
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
                    if job.action not in READ_STATE:
                        return False
                    # Mark read/unread is waiting for this conversation: the newest wish replaces it
                    # (a repeat of the same wish is dropped, unless it has to run regardless).
                    waiting = self._latest.get(key)
                    if waiting is not None and waiting.action == job.action and not getattr(job, "force", False):
                        return False
                    self._latest[key] = job
                    return True
                self._queued.add(key)
                if job.action in READ_STATE:
                    self._latest[key] = job
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._work, name="messages-helpers", daemon=True)
                self._thread.start()
        # Tapbacks and replies go ahead of read sync and the permission check (someone's waiting on them).
        self._jobs.put((0 if job.action in ("tapback", "reply", "compose") else 1, next(self._order), job))
        return True

    def probe(self) -> bool:
        """Ask macOS for the Automation permission now (a helper was just turned on)."""
        return self.request(ProbeJob())

    def run_and_wait(self, job: ReplyJob | ComposeJob, timeout: float = 120.0) -> ReplyResult:
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
            _priority, _order, job = self._jobs.get()
            with self._lock:
                key = job.key()
                if key is not None:
                    self._queued.discard(key)
                    job = self._latest.pop(key, job)  # read/unread: whichever was asked for last
                cancelled = getattr(job, "state", None) == "cancelled"
                if isinstance(job, (ReplyJob, ComposeJob)) and not cancelled:
                    job.state = "running"
                self._busy += 1
            try:
                if not cancelled:
                    self._dispatch(job)
            except Exception:
                log.exception("Messages helper failed (%s)", job.action)
                if isinstance(job, (ReplyJob, ComposeJob)) and job.result is None:
                    job.result = (ReplyResult("unconfirmed", REPLY_UNCONFIRMED) if job.ran_script
                                  else ReplyResult("nothing", "The Messages helper failed before typing anything."))
            finally:
                if isinstance(job, (ReplyJob, ComposeJob)):
                    job.state = "done"
                    job.finished.set()
                with self._lock:
                    self._busy -= 1
                self._jobs.task_done()

    def _dispatch(self, job: Any) -> None:
        if job.action == "mark-read":
            self._run_mark_read(job)
        elif job.action == "mark-unread":
            self._run_mark_unread(job)
        elif job.action == "tapback":
            self._run_tapback(job)
        elif job.action == "reply":
            job.result = self._run_reply(job)
        elif job.action == "compose":
            job.result = self._run_compose(job)
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
        if newest is None and not job.force:
            return  # Messages already agrees: nothing to do
        assert self.runner is not None
        outcome = self.runner(MARK_READ_SCRIPT, job.argv(), TIMEOUT)
        base = _common(outcome, TIMEOUT)
        interp = base or _specific(outcome, MARK_READ_ANSWERS, job.title, "nothing")
        result = interp.sentence
        succeeded = interp.state == "done" and base is None
        if succeeded and newest is not None:
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

    def _run_mark_unread(self, job: UnreadJob) -> None:
        if not self.mark_read_on:
            return  # turned off while this was queued
        assert self.runner is not None
        outcome = self.runner(MARK_UNREAD_SCRIPT, job.argv(), TIMEOUT)
        base = _common(outcome, TIMEOUT)
        interp = base or _specific(outcome, MARK_UNREAD_ANSWERS, job.title, "nothing")
        succeeded = interp.state == "done" and base is None
        self._record(interp, interp.sentence, "Mark unread")
        if not succeeded and interp.code not in ("not-running", "no-window"):
            self._ui_action("mark-unread", job.conversation_id, "failed", interp.sentence)

    # ------------------------------------------------------------------ tapbacks

    def current_tapback(self, message_id: str) -> tuple[str, str | None] | None:
        """Your tapback on this message right now, from chat.db: (kind, emoji for an emoji one)."""
        message = self.builder.by_guid(message_id)
        for reaction in (message or {}).get("reactions", []):
            if "me" in reaction["from"] and (reaction["kind"] in TAPBACK_KINDS or reaction["kind"] == EMOJI_KIND):
                return reaction["kind"], (reaction.get("emoji") if reaction["kind"] == EMOJI_KIND else None)
        return None

    @staticmethod
    def _is_current(job: TapbackJob, current: tuple[str, str | None] | None) -> bool:
        if current is None or current[0] != job.kind:
            return False
        return job.kind != EMOJI_KIND or same_emoji(current[1], job.emoji)

    def verify_tapback(self, job: TapbackJob, start: int) -> bool:
        if job.kind == EMOJI_KIND:
            want = 3006 if job.remove else 2006
        else:
            want = (3000 if job.remove else 2000) + TAPBACK_KINDS.index(job.kind)
        fields = ("associated_message_type", "associated_message_emoji")
        deadline = time.monotonic() + self.verify_seconds
        while True:
            rows = self.db.query(
                f"""SELECT {self.db.message_select(fields=fields)} FROM message m
                   WHERE m.ROWID > ? AND m.is_from_me = 1
                     AND (m.associated_message_guid = ? OR m.associated_message_guid LIKE ?)""",
                (start, "bp:" + job.message_id, "p:%/" + job.message_id),
            )
            for r in rows:
                if r["associated_message_type"] != want:
                    continue
                # An emoji tapback must be the one asked for (a removal may leave the emoji out).
                if job.kind != EMOJI_KIND or same_emoji(r["associated_message_emoji"], job.emoji) \
                        or (job.remove and not r["associated_message_emoji"]):
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
        if not job.remove and self._is_current(job, current):
            return answer("done", "That tapback is already there.")
        if job.remove and not self._is_current(job, current):
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
        quick = quick_emoji(outcome)
        if quick:  # what Messages offers, for the app's tapback picker (kept in state, never logged)
            self.state.set_kv(TAPBACK_QUICK_KEY, quick)
        ok = interp.state != "nothing" and self.verify_tapback(job, start)
        if ok:
            sentence = done_text
            interp.code += ", verified"
        elif interp.state == "nothing" and job.kind == EMOJI_KIND and interp.code.startswith("no-tapback-item"):
            sentence = (f"Messages only offers its most recent emoji as quick tapbacks, and {job.emoji} isn't one of "
                        "them yet. React with it once in Messages and it will be.")
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
            # Every reply aims at its own message with a right-click (Harry: "i want it to pick the right
            # one"). Only if that couldn't start and the root is the newest message anyway, the menu
            # bar's "Reply to Last Message" (which always answers the newest).
            interp = self._reply_by_right_click(job)
            if interp.state == "nothing" and job.root_is_newest:
                log.info("Reply in Messages: right-click didn't start (%s); newest message, menu bar instead", interp.code)
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
        elif row["thread_originator_guid"]:
            result = ReplyResult("regular", "Messages threaded it under a different message.", row["guid"])
            interp.code += ", threaded under another message"
        else:
            result = ReplyResult("regular", "Messages sent it as a regular text instead of a reply.", row["guid"])
            interp.code += ", not threaded"
        if self.runner is not None:
            self._record(interp, result.detail, "Reply")
            if result.kind == "done":
                # Messages' Reply answers the newest message by default, so only a reply to an older
                # one shows that aiming works.
                if self._has_newer(job, row["guid"]):
                    self.note_reply_proof(True)
            elif result.kind == "regular":  # unthreaded, or threaded under the wrong message
                self.note_reply_proof(False)
        return result

    def _reply_by_right_click(self, job: ReplyJob) -> Interp:
        """Older messages: find it and get its place on screen (Messages stays in front), right-click
        there, then choose Reply… in its menu and send (REPLY_AIM / REPLY_FINISH)."""
        if not self.can_click():
            return Interp(None, "Backchannel can't click in Messages on this Mac.", "nothing", "no-click")
        aimed = self.runner(REPLY_AIM_SCRIPT, ["reply-aim", *job.argv()[1:]], REPLY_TIMEOUT)
        first, _, rest = aimed.stdout.strip().partition("\n")
        code, trail = _split(Outcome(aimed.returncode, first, aimed.stderr, aimed.timed_out))
        point = re.fullmatch(r"aim:(-?\d{1,6}),(-?\d{1,6})", code)
        if point is None:  # nothing happened in Messages yet: say why
            interp = interpret_reply(aimed, job.title)
            if interp.state != "nothing":  # a timeout or an odd answer while only looking: still nothing typed
                interp = Interp(interp.permission, interp.sentence, "nothing", interp.code)
            return interp
        extras = dict(line.split("\t", 1) for line in rest.splitlines() if "\t" in line)
        front = extras.get("front", "")
        hint = (extras.get("hint") or "iMessage").split("\t")[0] or "iMessage"
        x, y = float(point.group(1)), float(point.group(2))
        try:
            before = self.right_click(x, y)
        except OSError as exc:
            log.info("Right-click failed: %s", exc)
            return Interp(None, "Backchannel couldn't click in Messages.", "nothing", "click-failed")
        time.sleep(0.5)
        job.ran_script = True
        try:
            outcome = self.runner(REPLY_FINISH_SCRIPT, ["reply-finish", f"M:{job.text}", f"B:{hint}",
                                                        *([f"F:{front}"] if re.fullmatch(r"[\w.-]{1,120}", front) else [])],
                                  REPLY_TIMEOUT)
        finally:
            if before is not None:
                try:
                    self.move_pointer(*before)
                except OSError:
                    pass
        interp = interpret_reply(outcome, job.title)
        interp.code = (f"{interp.code} [aim {trail}]" if trail else interp.code)
        return interp

    def _has_newer(self, job: ReplyJob, reply_guid: str) -> bool:
        try:
            conv = self.convs.get(job.conversation_id)
        except LookupError:
            return False
        return self._newer_than_root(conv, job.root_id, job.root_text, ignore=reply_guid)

    def _newer_than_root(self, conv: Conv, root_id: str, root_text: str, ignore: str | None = None) -> bool:
        """Something is newer than the root (besides `ignore` and copies of the root, as a self-chat
        shows): then "Reply to Last Message" would answer the wrong message."""
        try:
            recent = self.builder.recent(conv, 30)
        except ChatDBError:
            return True
        ids = [m["id"] for m in recent]
        if root_id not in ids:
            return True  # the root is older than the last 30 messages
        text = _norm(root_text)
        newer = recent[ids.index(root_id) + 1:]
        return any(m["id"] != ignore and _norm(m.get("text") or "") != text for m in newer)

    # ------------------------------------------------------------------ links with their preview

    def find_compose_row(self, job: ComposeJob, start: int) -> Any | None:
        """The link's row in chat.db (from me, in this conversation), waiting up to ~8 s."""
        want = _norm(job.text)
        deadline = time.monotonic() + max(self.verify_seconds, 8.0) if self.runner is not None else \
            time.monotonic() + self.verify_seconds
        while True:
            for row in self.db.rows_after(start, 200):
                if (row["is_from_me"] and row["chat_id"] in job.chat_ids and (row["item_type"] or 0) == 0
                        and not H.is_reaction_type(row["associated_message_type"])):
                    text = _norm(message_text(row))
                    if text == want or (want and want in text):
                        return row
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.25)

    def _run_compose(self, job: ComposeJob) -> ReplyResult:
        if not self.links_on:
            return ReplyResult("nothing", "Sending links through Messages is off.")
        start = self.db.max_rowid()
        if self.runner is None:
            if self.simulator is None:
                return ReplyResult("nothing", DRY_RUN_TEXT)
            job.ran_script = True
            self.simulator.compose(job)
            interp = Interp(None, "Sent with its preview.", "done", "simulated")
        else:
            job.ran_script = True
            outcome = self.runner(COMPOSE_SCRIPT, job.argv(), COMPOSE_TIMEOUT)
            interp = _common(outcome, COMPOSE_TIMEOUT) or _specific(outcome, COMPOSE_ANSWERS, job.title, "maybe")
            if interp.state == "nothing":
                self._record(interp, interp.sentence, "Link")
                return ReplyResult("nothing", interp.sentence)
        row = self.find_compose_row(job, start)
        if row is None:
            result = ReplyResult("unconfirmed", REPLY_UNCONFIRMED.replace("your reply", "your link"))
            interp.code += ", not in chat.db"
        else:
            rich = row["balloon_bundle_id"] == URL_BALLOON
            result = ReplyResult("done", "Sent with its preview." if rich else "Sent, but without a preview.", row["guid"])
            interp.code += ", with preview" if rich else ", no preview"
        if self.runner is not None:
            self._record(interp, result.detail, "Link")
        return result

    # ------------------------------------------------------------------ has a real reply worked here?

    def replies_proven(self) -> bool:
        """A real reply threaded on this Mac (checked in chat.db), and none went out unthreaded since."""
        return bool((self.state.get_kv(REPLY_PROOF_KEY) or {}).get("ok"))

    def note_reply_proof(self, ok: bool) -> None:
        self.state.set_kv(REPLY_PROOF_KEY, {"ok": ok, "at": time.time()})

    def needs_reply_proof(self, conv: Conv) -> bool:
        """Real replies to other people wait until one has threaded in the user's own chat on this
        Mac, so a Messages that won't switch into reply mode can't send them regular texts."""
        return self.runner is not None and not conv.is_self and not self.replies_proven()

    def reply_test(self, method: str, text: str, names: list[str], timeout: float = 60.0) -> Outcome:
        """doctor --messages-reply-test: start replying to the newest message in the user's own chat one
        way (action / menu / menubar); if Messages switches into reply mode, paste `text` and send it."""
        if self.runner is None:
            return Outcome(127, "", "Messages can't be driven here.")
        argv = ["reply-test", f"W:{method}", f"M:{text}", *[f"N:{n}" for n in names if n]]
        return self.runner(REPLY_TEST_SCRIPT, argv, timeout)

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


_SHAPE_WORDS = {"Pinned", "Unread", "Read", "Delivered", "Sent", "Edited", "Muted", "Today", "Yesterday", "time"}


def _shape(raw: str) -> str:
    """A label's shape from the script: part lengths and a few known words; anything else is '?'."""
    return ",".join(t if (t.isdigit() or t in _SHAPE_WORDS) else "?" for t in raw.split(",") if t) or "-"


def _actions(raw: str, private: set[str] | frozenset[str] = frozenset()) -> str:
    """Accessibility action names. Custom actions arrive as "Name:Reply<newline>Target:…"; keep the name."""
    out = []
    for action in raw.split(","):
        action = action.strip()
        if re.fullmatch(r"AX[A-Za-z]+", action):
            out.append(action)
        elif action.startswith("Name:"):
            name = action[5:].split("Target:")[0].strip()
            shown = safe_titles(name, private) if name else "(hidden)"
            out.append(f"custom:{shown}" if shown not in ("---", "-") else "custom:(hidden)")
        elif action:
            out.append("(other)")
    return ", ".join(out) or "none"


# Accessibility identifiers worth showing: developer names like "ConversationList". Anything with
# digits, @, + or spaces could be a chat id, number or address, so it's hidden.
_SAFE_IDENT = re.compile(r"[A-Za-z][A-Za-z._-]{0,59}")


def inspect_mode(args: list[str], runner: Runner = osascript_runner, timeout: float = 60.0) -> Outcome:
    """Run one of the inspect script's modes ("title", "try-tapback", "try-reply") and return its raw output.
    The caller decides what is safe to print (the "title" mode returns the open chat's name)."""
    return runner(INSPECT_SCRIPT, args, timeout)


def inspect(runner: Runner = osascript_runner, timeout: float = 120.0,
            private: set[str] | frozenset[str] = frozenset(), tree: bool = False,
            raw: Outcome | None = None) -> list[str]:
    """Report lines about Messages' accessibility structure: roles, counts and menu/button titles only.

    Everything else the script prints is dropped; titles that look like they
    carry a name, number, address or quoted text (see `private`) are hidden.
    """
    outcome = raw if raw is not None else runner(INSPECT_SCRIPT, ["tree"] if tree else [], timeout)
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
        elif key == "tree" and len(parts) == 9 and parts[1].isdigit() and parts[4].isdigit():
            # depth, role, subrole, children, identifier, description length, value length, menu flag
            depth, role, sub, kids, ident, dlen, vlen, menu = parts[1:]
            role = role if _ROLE.match(role) else "(other)"
            sub = f" ‹{sub}›" if sub and _ROLE.match(sub) else ""
            ident = f" id={ident}" if _SAFE_IDENT.fullmatch(ident or "") else (" id=(hidden)" if ident else "")
            text = "".join(f" {k}{n}" for k, n in (("desc:", dlen), ("value:", vlen)) if n.isdigit() and n != "0")
            tree.append(f"    {'  ' * min(int(depth), 16)}{role}{sub} ({kids}){ident}{text}{' [menu]' if menu == 'menu' else ''}")
        elif key == "skip" and len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
            tree.append(f"    {'  ' * min(int(parts[1]), 16)}… {parts[2]} more")
        elif key == "spot" and len(parts) == 3:
            found = _SAFE_IDENT.fullmatch(parts[2] or "")
            lines.append(f"- {parts[1] if parts[1] in ('list', 'transcript', 'title') else '?'}: "
                         + (f"found (id {parts[2]})" if found else ("missing" if not parts[2] else "missing (got something else)")))
        elif key == "rows" and len(parts) == 5 and all(p.isdigit() for p in parts[1:]):
            lines.append(f"- conversation list: {parts[1]} items; labels read {parts[2]}, AXSelected read {parts[3]},"
                         f" selected children {parts[4]}")
        elif key == "row" and len(parts) == 6 and parts[1].isdigit() and parts[3].isdigit():
            role = parts[2] if _ROLE.match(parts[2]) else "(other)"
            selected = parts[4] if parts[4] in ("true", "false") else "?"
            lines.append(f"    item {parts[1]}: {role}, label {parts[3]} chars [{_shape(parts[5])}], selected {selected}")
        elif key == "row_actions" and len(parts) == 3:
            lines.append(f"- actions on a {parts[1] if parts[1] in ('short-label', 'long-label') else '?'} item: {_actions(parts[2], private)}")
        elif key == "row_selected_settable" and len(parts) == 2:
            lines.append(f"- can set AXSelected on a row: {parts[1] if parts[1] in ('true', 'false') else 'unknown'}")
        elif key == "bubbles" and len(parts) == 2 and parts[1].isdigit():
            lines.append(f"- messages on screen: {parts[1]}")
        elif key == "bubble_row" and len(parts) == 4 and parts[1].isdigit() and parts[2].isdigit():
            lines.append(f"    message {parts[1]}: label {parts[2]} chars [{_shape(parts[3])}]")
        elif key == "bubble_actions" and len(parts) == 4:
            ident = parts[3] if _SAFE_IDENT.fullmatch(parts[3] or "") else ("(hidden)" if parts[3] else "-")
            lines.append(f"- newest message, {parts[1] if parts[1] in ('row', 'balloon', 'text') else '?'}: id {ident};"
                         f" actions {_actions(parts[2], private)}")
        elif key in ("top_before", "top_after") and len(parts) == 2:
            roles = [r if _ROLE.match(r) else "(other)" for r in parts[1].split(",") if r]
            lines.append(f"- Messages' top-level elements {'before' if key == 'top_before' else 'after'} the right-click: "
                         + (", ".join(roles) or "none"))
        elif key == "bubble_menu_at" and len(parts) == 2:
            lines.append(f"- the right-click menu showed up: {parts[1] if parts[1] in ('element', 'app', 'window', 'none') else '?'}")
        elif key == "after_tapback" and len(parts) == 4:
            roles = [r if _ROLE.match(r) else "(other)" for r in parts[1].split(",") if r]
            lines.append(f"- after choosing Tapback: top-level {', '.join(roles) or 'none'}; windows {parts[2]} (were {parts[3]})")
        elif key == "window" and len(parts) == 4 and parts[1].isdigit():
            ident = parts[2] if _SAFE_IDENT.fullmatch(parts[2] or "") else ("(hidden)" if parts[2] else "-")
            sub = parts[3] if _ROLE.match(parts[3] or "") else "-"
            lines.append(f"    window {parts[1]}: id {ident}, subrole {sub}")
        elif key == "try_actions" and len(parts) == 2:
            lines.append(f"- newest message's actions: {_actions(parts[1], private)}")
        elif key == "try" and len(parts) == 2:
            lines.append(f"- pressed: {parts[1] if parts[1] in ('true', 'false', 'no-message') else '?'}")
        elif key == "focus" and len(parts) == 7 and parts[1] in ("before", "after"):
            role, ident, dlen, vlen, hint = parts[2:]
            role = role if _ROLE.match(role) else "(other)"
            ident = ident if _SAFE_IDENT.fullmatch(ident or "") else ("(hidden)" if ident else "-")
            hint = safe_titles(hint, private) if hint else "-"
            lines.append(f"- focus {parts[1]} Reply: {role}, id {ident}, label {dlen if dlen.isdigit() else '?'} chars,"
                         f" text {vlen if vlen.isdigit() else '?'} chars, placeholder {hint}")
        elif key == "focus" and len(parts) == 3 and parts[2] == "none":
            lines.append(f"- focus {parts[1] if parts[1] in ('before', 'after') else '?'} Reply: nothing focused")
        elif key == "section" and len(parts) == 2:
            tree.append(f"    [{parts[1] if re.fullmatch(r'[a-z0-9-]{1,20}', parts[1]) else '?'}]")
        elif key == "picker_buttons" and len(parts) == 3:
            lines.append(f"- tapback picker buttons ({parts[1] if parts[1] in ('window', 'popover') else '?'}): "
                         + (safe_titles(parts[2], private) if parts[2] else "none found"))
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
        lines.append("- front window (role ‹subrole› (children) id=…, text lengths only):")
        lines.extend(tree[:500])
    return lines or ["- inspection: no output"]
