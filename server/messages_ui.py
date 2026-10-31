"""Experimental: mark a conversation read in the Messages app through Accessibility.

Apple gives other apps no way to mark texts read. With `markReadInMessages`
on, `POST /read` queues a job (one worker, repeats coalesced, ~8 s timeout)
that runs one osascript driving System Events against process "Messages":
find the conversation's sidebar row by its display name, select it, click the
menu bar's Conversation › Mark as Read, else the row's context menu › Mark as
Read, else press Escape. It never types text and never sends anything, and it
only ever selects or presses rows (never buttons). Candidate names are passed
as argv, never put into the script source.

Never runs in dry-run / fake-DB mode unless a runner is injected (tests).
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
from dataclasses import dataclass, field
from typing import Any, Callable

from .chatdb import ChatDB, ChatDBError, NOT_REACTION_SQL
from .handles import format_handle
from .state import State
from .timeutil import iso

log = logging.getLogger("backchannel.messages_ui")

TIMEOUT = 8.0
CHECK_AFTER = 3.0
STATUS_KEY = "messages_ui_status"
DENIED = re.compile(r"-1719|-25211|assistive access", re.IGNORECASE)
DENIED_TEXT = ("macOS hasn't let Backchannel control Messages. Allow Backchannel in System Settings › Privacy & "
               "Security › Accessibility, then try again.")
# -1743: the other prompt, "Backchannel wants to control System Events" (Automation), was declined.
AUTOMATION_DENIED = re.compile(r"-1743|not authori[sz]ed to send apple events", re.IGNORECASE)
AUTOMATION_TEXT = ("macOS hasn't let Backchannel control System Events. Allow it in System Settings › Privacy & "
                   "Security › Automation, then try again.")

# argv: "names", how many of the names may match a label's beginning, then the names, most specific first.
# A name first has to match a whole label line (or "Name, preview, time"); only the first N (full names,
# numbers) may then match as a prefix, so a short name like "Jordan" never picks "Jordan Lee".
# Only rows/cells are ever selected or pressed; anything else just gets its context menu.
MARK_READ_SCRIPT = (
    "on run argv",
    "set looseCount to 0",
    "try",
    "set looseCount to (item 2 of argv) as integer",
    "end try",
    "set candidates to {}",
    "if (count of argv) > 2 then set candidates to items 3 thru -1 of argv",
    'tell application "System Events"',
    'if not (exists process "Messages") then return "not-running"',
    'tell process "Messages"',
    'if (count of windows) is 0 then return "no-window"',
    "set win to window 1",
    "set rowEls to {}",
    "set rowLabels to {}",
    "set seen to 0",
    "repeat with el in (entire contents of win)",
    "set seen to seen + 1",
    "if seen > 2500 then exit repeat",
    "try",
    "set r to role of el",
    'if r is in {"AXRow", "AXCell", "AXButton", "AXGroup", "AXStaticText"} then',
    'set lbl to ""',
    "try",
    "set lbl to lbl & ((description of el) as text) & linefeed",
    "end try",
    "try",
    "set lbl to lbl & ((name of el) as text) & linefeed",
    "end try",
    "try",
    "set lbl to lbl & ((value of el) as text) & linefeed",
    "end try",
    'if lbl is not "" then',
    "set end of rowEls to (contents of el)",
    "set end of rowLabels to lbl",
    "end if",
    "end if",
    "end try",
    "end repeat",
    "end tell",
    "end tell",
    "set foundRow to missing value",
    "repeat with ci from 1 to (count of candidates)",
    "set foundRow to my findRow(rowEls, rowLabels, (item ci of candidates) as text, true)",
    "if foundRow is not missing value then exit repeat",
    "end repeat",
    "if foundRow is missing value then",
    "repeat with ci from 1 to looseCount",
    "if ci > (count of candidates) then exit repeat",
    "set foundRow to my findRow(rowEls, rowLabels, (item ci of candidates) as text, false)",
    "if foundRow is not missing value then exit repeat",
    "end repeat",
    "end if",
    'if foundRow is missing value then return "row-not-found"',
    'tell application "System Events"',
    'tell process "Messages"',
    "if my selectRow(foundRow) then",
    "try",
    'set markItem to menu item "Mark as Read" of menu 1 of menu bar item "Conversation" of menu bar 1',
    "if enabled of markItem then",
    "click markItem",
    'return "ok-menu"',
    "end if",
    "end try",
    "end if",
    "try",
    'perform action "AXShowMenu" of foundRow',
    "delay 0.4",
    "on error",
    'return "no-mark-item"',
    "end try",
    "try",
    'click menu item "Mark as Read" of menu 1 of foundRow',
    'return "ok-context"',
    "end try",
    "try",
    'click menu item "Mark as Read" of menu 1 of win',
    'return "ok-context"',
    "end try",
    "key code 53",
    'return "no-mark-item"',
    "end tell",
    "end tell",
    "end run",
    "on findRow(rowEls, rowLabels, needle, strict)",
    "if (length of needle) is 0 then return missing value",
    "set fallback to missing value",
    "repeat with i from 1 to (count of rowEls)",
    "set hit to false",
    "repeat with aLine in paragraphs of (item i of rowLabels)",
    "set t to aLine as text",
    "if strict then",
    'if t is needle or t starts with (needle & ",") then set hit to true',
    "else",
    "if t starts with needle then set hit to true",
    "end if",
    "if hit then exit repeat",
    "end repeat",
    "if hit then",
    "set rowEl to my climbToRow(item i of rowEls)",
    "if rowEl is not missing value then return rowEl",
    "if fallback is missing value then set fallback to item i of rowEls",
    "end if",
    "end repeat",
    "return fallback",
    "end findRow",
    "on climbToRow(el)",
    'tell application "System Events"',
    "try",
    'if role of el is in {"AXRow", "AXCell"} then return el',
    "end try",
    "set p to el",
    "repeat 6 times",
    "try",
    'set p to value of attribute "AXParent" of p',
    'if role of p is in {"AXRow", "AXCell"} then return p',
    "on error",
    "exit repeat",
    "end try",
    "end repeat",
    "end tell",
    "return missing value",
    "end climbToRow",
    "on selectRow(rowEl)",
    'tell application "System Events"',
    "try",
    'if role of rowEl is not in {"AXRow", "AXCell"} then return false',
    "on error",
    "return false",
    "end try",
    "try",
    'set value of attribute "AXSelected" of rowEl to true',
    "delay 0.3",
    'if (value of attribute "AXSelected" of rowEl) is true then return true',
    "end try",
    "try",
    'perform action "AXPress" of rowEl',
    "delay 0.3",
    'if (value of attribute "AXSelected" of rowEl) is true then return true',
    "end try",
    "end tell",
    "return false",
    "end selectRow",
)

# Prints roles, counts, booleans/lengths and menu titles only: never names, descriptions or values.
INSPECT_SCRIPT = (
    "property budget : 400",
    "on run argv",
    "set outLines to {}",
    'tell application "System Events"',
    "try",
    'set probe to count of UI elements of process "Finder"',
    'set end of outLines to "accessibility" & tab & "allowed"',
    "on error errMsg number errNum",
    'set end of outLines to "accessibility" & tab & "denied" & tab & (errNum as text)',
    "return my joinWith(outLines, linefeed)",
    "end try",
    'if not (exists process "Messages") then',
    'set end of outLines to "messages_running" & tab & "no"',
    "return my joinWith(outLines, linefeed)",
    "end if",
    'set end of outLines to "messages_running" & tab & "yes"',
    'tell process "Messages"',
    'set end of outLines to "windows" & tab & ((count of windows) as text)',
    "try",
    'set end of outLines to "menu_bar" & tab & my joinWith(my cleanNames(name of every menu bar item of menu bar 1), " | ")',
    "end try",
    "try",
    'set end of outLines to "conversation_menu" & tab & my joinWith(my cleanNames(name of every menu item of menu 1 of menu bar item "Conversation" of menu bar 1), " | ")',
    "end try",
    "if (count of windows) > 0 then",
    "set win to window 1",
    "set end of outLines to my walk(win, 0, 8)",
    "set rowCount to 0",
    "set withDesc to 0",
    "set withName to 0",
    "set withValue to 0",
    "set maxDesc to 0",
    "set maxName to 0",
    "set maxValue to 0",
    "repeat with el in (entire contents of win)",
    "try",
    'if role of el is in {"AXRow", "AXCell"} then',
    "set rowCount to rowCount + 1",
    'set n to my textLength(el, "description")',
    "if n > 0 then set withDesc to withDesc + 1",
    "if n > maxDesc then set maxDesc to n",
    'set n to my textLength(el, "name")',
    "if n > 0 then set withName to withName + 1",
    "if n > maxName then set maxName to n",
    'set n to my textLength(el, "value")',
    "if n > 0 then set withValue to withValue + 1",
    "if n > maxValue then set maxValue to n",
    "end if",
    "end try",
    "end repeat",
    'set end of outLines to "rows" & tab & rowCount & tab & withDesc & tab & withName & tab & withValue & tab & maxDesc & tab & maxName & tab & maxValue',
    "end if",
    "end tell",
    "end tell",
    "return my joinWith(outLines, linefeed)",
    "end run",
    "on walk(el, depth, maxDepth)",
    "set outLines to {}",
    "if budget is less than or equal to 0 then return \"\"",
    "set budget to budget - 1",
    'tell application "System Events"',
    'set r to "?"',
    "try",
    "set r to role of el",
    "end try",
    "set kids to {}",
    "try",
    "set kids to UI elements of el",
    "end try",
    'set end of outLines to "tree" & tab & (depth as text) & tab & r & tab & ((count of kids) as text)',
    "if depth < maxDepth then",
    "set i to 0",
    "repeat with k in kids",
    "set i to i + 1",
    "if i > 12 or budget is less than or equal to 0 then exit repeat",
    "set end of outLines to my walk(k, depth + 1, maxDepth)",
    "end repeat",
    "end if",
    "end tell",
    "return my joinWith(outLines, linefeed)",
    "end walk",
    "on textLength(el, which)",
    'tell application "System Events"',
    "try",
    'if which is "description" then set t to description of el',
    'if which is "name" then set t to name of el',
    'if which is "value" then set t to value of el',
    "return length of (t as text)",
    "on error",
    "return 0",
    "end try",
    "end tell",
    "end textLength",
    "on cleanNames(lst)",
    "set out to {}",
    "repeat with x in lst",
    "try",
    "set end of out to (x as text)",
    "on error",
    'set end of out to "-"',
    "end try",
    "end repeat",
    "return out",
    "end cleanNames",
    "on joinWith(lst, sep)",
    "set saved to AppleScript's text item delimiters",
    "set AppleScript's text item delimiters to sep",
    "set s to lst as text",
    "set AppleScript's text item delimiters to saved",
    "return s",
    "end joinWith",
)


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


@dataclass
class Job:
    conversation_id: str
    title: str
    candidates: list[str]  # names to look for in Messages' sidebar, most specific first
    chat_ids: list[int] = field(default_factory=list)
    loose: int | None = None  # how many of `candidates` may match a label's beginning (default: all)

    def argv(self) -> list[str]:
        loose = len(self.candidates) if self.loose is None else min(self.loose, len(self.candidates))
        return ["names", str(loose), *self.candidates]


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


def outcome_code(outcome: Outcome) -> str:
    """A short code for the log: never names or UI text."""
    if outcome.timed_out:
        return "timed out"
    if outcome.returncode != 0:
        if DENIED.search(outcome.stderr):
            return "not allowed (Accessibility)"
        if AUTOMATION_DENIED.search(outcome.stderr):
            return "not allowed (Automation)"
        return f"failed (exit {outcome.returncode})"
    answer = outcome.stdout.strip()
    return answer if re.fullmatch(r"[a-z-]{1,20}", answer) else "unexpected answer"


def interpret(outcome: Outcome, title: str) -> tuple[str | None, str, bool]:
    """(permission or None for unchanged, lastResult sentence, succeeded)."""
    if outcome.timed_out:
        return None, f"Messages didn't respond within {int(TIMEOUT)} seconds.", False
    if outcome.returncode != 0:
        if DENIED.search(outcome.stderr):
            return "denied", DENIED_TEXT, False
        if AUTOMATION_DENIED.search(outcome.stderr):
            return "denied", AUTOMATION_TEXT, False
        detail = outcome.stderr.splitlines()[-1][:160] if outcome.stderr else f"exit {outcome.returncode}"
        return None, f"The Messages script failed: {detail}", False
    answers = {
        "ok-menu": ("granted", f"Marked {title} read in Messages.", True),
        "ok-context": ("granted", f"Marked {title} read in Messages (from its context menu).", True),
        "row-not-found": ("granted", f"Couldn't find {title} in Messages' sidebar.", False),
        "no-mark-item": ("granted", f"Messages didn't offer “Mark as Read” for {title}.", False),
        "no-window": ("granted", "Messages has no window open, so nothing was marked read.", False),
        "not-running": (None, "Messages isn't open, so nothing was marked read.", False),
    }
    return answers.get(outcome.stdout.strip(), (None, "The Messages script gave an unexpected answer.", False))


class MessagesUI:
    def __init__(self, db: ChatDB, state: State, runner: Runner | None, check_after: float = CHECK_AFTER):
        self.db = db
        self.state = state
        self.runner = runner
        self.check_after = check_after
        self._jobs: queue.Queue[Job] = queue.Queue()
        self._queued: set[str] = set()
        self._lock = threading.Lock()
        self._busy = 0
        self._thread: threading.Thread | None = None
        self._status = {"permission": "unknown", "lastResult": None, "lastAt": None,
                        **(state.get_kv(STATUS_KEY) or {})}

    @property
    def enabled(self) -> bool:
        return bool(self.state.get_settings().get("markReadInMessages"))

    def health(self) -> dict[str, Any]:
        with self._lock:
            status = dict(self._status)
        return {
            "enabled": self.enabled,
            "permission": status["permission"],
            "lastResult": status["lastResult"],
            "lastAt": iso(status["lastAt"]) if status["lastAt"] else None,
        }

    def _record(self, permission: str | None, result: str, code: str) -> None:
        with self._lock:
            if permission:
                self._status["permission"] = permission
            self._status["lastResult"] = result
            self._status["lastAt"] = time.time()
            snapshot = dict(self._status)
        self.state.set_kv(STATUS_KEY, snapshot)
        log.info("Mark read in Messages: %s", code)  # a code, not the sentence: that names the conversation

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

    # ------------------------------------------------------------------ queueing

    def request(self, job: Job) -> bool:
        """Queue a job if the setting is on (never blocks).

        A repeat for a conversation that's already waiting in the queue is
        dropped; one that arrives while its job is running is queued once more
        (new messages may have come in meanwhile).
        """
        if self.runner is None or not self.enabled:
            return False
        with self._lock:
            if job.conversation_id in self._queued:
                return False
            self._queued.add(job.conversation_id)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._work, name="messages-ui", daemon=True)
                self._thread.start()
        self._jobs.put(job)
        return True

    def _work(self) -> None:
        while True:
            job = self._jobs.get()
            with self._lock:
                self._queued.discard(job.conversation_id)
                self._busy += 1
            try:
                self._run(job)
            except Exception:
                log.exception("Mark read in Messages failed")
            finally:
                with self._lock:
                    self._busy -= 1
                self._jobs.task_done()

    def _run(self, job: Job) -> None:
        if not self.enabled:
            return  # turned off while this was queued
        try:
            newest = self.newest_unread(job.chat_ids)
        except ChatDBError:
            return
        if newest is None:
            return  # Messages already agrees: nothing to do
        assert self.runner is not None
        outcome = self.runner(MARK_READ_SCRIPT, job.argv(), TIMEOUT)
        permission, result, succeeded = interpret(outcome, job.title)
        code = outcome_code(outcome)
        if succeeded:
            time.sleep(self.check_after)
            try:  # only the messages that were unread when we started (new ones may have arrived since)
                if self.newest_unread(job.chat_ids, upto=newest) is not None:
                    result = result[:-1] + ", but chat.db still shows unread messages there."
                    code += ", still unread in chat.db"
            except ChatDBError:
                pass
        self._record(permission, result, code)

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


# --------------------------------------------------------------------------- doctor --messages-ui

_ROLE = re.compile(r"^(AX[A-Za-z]+|\?)$")
_ERROR_NUMBER = re.compile(r"\((-?\d{1,6})\)\s*$")
_LOOKS_PRIVATE = re.compile(r"[0-9@+]")
# Titles this module looks for itself: never hidden, even with a contact named Mark.
KNOWN_TITLES = frozenset({"mark as read", "mark as unread", "mark all as read", "conversation"})


def _mentions(lowered: str, name: str) -> bool:
    if " " in name:
        return name in lowered
    return re.search(rf"(?<!\w){re.escape(name)}(?!\w)", lowered) is not None


def safe_titles(raw: str, private: set[str] | frozenset[str] = frozenset()) -> str:
    """Menu titles, each hidden if it has digits, "@" or "+", or mentions one of the `private` names."""
    out = []
    for title in raw.split(" | ")[:60]:
        title = title.strip()[:60]
        if title in ("missing value", "-"):
            out.append("---")  # a separator
            continue
        lowered = title.lower()
        if lowered not in KNOWN_TITLES and (
                _LOOKS_PRIVATE.search(title) or any(len(name) >= 3 and _mentions(lowered, name) for name in private)):
            title = "(hidden)"
        out.append(title or "-")
    return " | ".join(out)


def inspect(runner: Runner = osascript_runner, timeout: float = 30.0,
            private: set[str] | frozenset[str] = frozenset()) -> list[str]:
    """Report lines about Messages' accessibility structure: roles, counts and menu titles only.

    Everything else the script prints is dropped; menu titles that look like
    they carry a name, number or address (see `private`) are hidden.
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
    if tree:
        lines.append("- front window (roles and child counts):")
        lines.extend(tree[:400])
    return lines or ["- inspection: no output"]
