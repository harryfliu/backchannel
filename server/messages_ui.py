"""Experimental: mark a conversation read in the Messages app through Accessibility.

Apple gives other apps no way to mark texts read. With `markReadInMessages`
on, `POST /read` queues a job (one worker, repeats coalesced, ~8 s timeout)
that runs one osascript driving System Events against process "Messages":
find the conversation's sidebar row by its display name, select it, click the
menu bar's Conversation › Mark as Read, else the row's context menu › Mark as
Read, else press Escape. It never types text and never sends anything.
Candidate names are passed as argv, never put into the script source.

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
from .state import State
from .timeutil import iso

log = logging.getLogger("backchannel.messages_ui")

TIMEOUT = 8.0
CHECK_AFTER = 3.0
STATUS_KEY = "messages_ui_status"
DENIED = re.compile(r"-1719|-25211|assistive access", re.IGNORECASE)
DENIED_TEXT = ("macOS hasn't let Backchannel control Messages. Allow Backchannel in System Settings › Privacy & "
               "Security › Accessibility, then try again.")

MARK_READ_SCRIPT = (
    "on run argv",
    "set candidates to rest of argv",
    'tell application "System Events"',
    'if not (exists process "Messages") then return "not-running"',
    'tell process "Messages"',
    'if (count of windows) is 0 then return "no-window"',
    "set win to window 1",
    "set els to {}",
    "set labels to {}",
    "repeat with el in (entire contents of win)",
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
    "set end of els to (contents of el)",
    "set end of labels to lbl",
    "end if",
    "end if",
    "end try",
    "end repeat",
    "set foundRow to missing value",
    "repeat with c in candidates",
    "set needle to c as text",
    "if (length of needle) > 0 then",
    "repeat with i from 1 to (count of els)",
    "repeat with part in paragraphs of (item i of labels)",
    "if (part as text) starts with needle then",
    "set foundRow to item i of els",
    "exit repeat",
    "end if",
    "end repeat",
    "if foundRow is not missing value then exit repeat",
    "end repeat",
    "end if",
    "if foundRow is not missing value then exit repeat",
    "end repeat",
    'if foundRow is missing value then return "row-not-found"',
    "set foundRow to my climbToRow(foundRow)",
    "try",
    'set value of attribute "AXSelected" of foundRow to true',
    "on error",
    "try",
    'perform action "AXPress" of foundRow',
    "end try",
    "end try",
    "delay 0.5",
    "try",
    'set markItem to menu item "Mark as Read" of menu 1 of menu bar item "Conversation" of menu bar 1',
    "if enabled of markItem then",
    "click markItem",
    'return "ok-menu"',
    "end if",
    "end try",
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
    "return el",
    "end climbToRow",
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
    candidates: list[str]
    chat_ids: list[int] = field(default_factory=list)


def interpret(outcome: Outcome, title: str) -> tuple[str | None, str, bool]:
    """(permission or None for unchanged, lastResult sentence, succeeded)."""
    if outcome.timed_out:
        return None, f"Messages didn't respond within {int(TIMEOUT)} seconds.", False
    if outcome.returncode != 0:
        if DENIED.search(outcome.stderr):
            return "denied", DENIED_TEXT, False
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

    def _record(self, permission: str | None, result: str) -> None:
        with self._lock:
            if permission:
                self._status["permission"] = permission
            self._status["lastResult"] = result
            self._status["lastAt"] = time.time()
            snapshot = dict(self._status)
        self.state.set_kv(STATUS_KEY, snapshot)
        log.info("Mark read in Messages: %s", result)

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
        try:
            newest = self.newest_unread(job.chat_ids)
        except ChatDBError:
            return
        if newest is None:
            return  # Messages already agrees: nothing to do
        assert self.runner is not None
        outcome = self.runner(MARK_READ_SCRIPT, ["names", *job.candidates], TIMEOUT)
        permission, result, succeeded = interpret(outcome, job.title)
        if succeeded:
            time.sleep(self.check_after)
            try:  # only the messages that were unread when we started (new ones may have arrived since)
                if self.newest_unread(job.chat_ids, upto=newest) is not None:
                    result = result[:-1] + ", but chat.db still shows unread messages there."
                    log.info("Messages accepted Mark as Read for %s but chat.db still has unread rows", job.conversation_id)
            except ChatDBError:
                pass
        self._record(permission, result)

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


def inspect(runner: Runner = osascript_runner, timeout: float = 30.0) -> list[str]:
    """Report lines about Messages' accessibility structure: roles, counts and menu titles only."""
    outcome = runner(INSPECT_SCRIPT, [], timeout)
    if outcome.timed_out:
        return ["- inspection: timed out"]
    if outcome.returncode != 0:
        if DENIED.search(outcome.stderr):
            return ["- Accessibility: NOT allowed for this app (System Settings › Privacy & Security › Accessibility)"]
        return [f"- inspection: failed ({(outcome.stderr.splitlines() or ['no output'])[-1][:120]})"]
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
        elif key in ("menu_bar", "conversation_menu") and len(parts) >= 2:
            label = "menu bar" if key == "menu_bar" else "Conversation menu"
            lines.append(f"- {label}: {parts[1][:400]}")  # app UI strings
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
