"""Tapbacks and thread replies in Messages with Messages left in the background.

Everything goes through direct accessibility calls (server/ax.py): Messages is never brought to
the front, the pointer never moves, and nothing is typed into the app you're using. Found on
Harry's Mac (macOS 26.3), all with Messages in the background:

- a message's tapbacks are its own accessibility actions ("Name:Thumbs up", "Name:😂", …);
- AXShowMenu opens a message's real menu (an AXMenu in the window) and pressing its "Reply…"
  puts the field in reply mode for exactly that message;
- the field takes the reply as its AXValue and keeps replying (unlike the System Events way);
- Return posted to Messages' process alone (CGEventPostToPid) sends it; Edit › Send Message is
  greyed out unless Messages is in front;
- AXCancel on the transcript closes the thread view (the "Dismiss replies" button does nothing);
- the transcript and the sidebar page with AXScrollUpByPage / AXScrollDownByPage;
- pressing a sidebar row toggles it like a Command-click, so other selected rows are pressed off;
- `open -g` with Messages' own link opens a one-to-one chat without activating Messages.

Each step is checked before the next (the right chat's title, the message's label right before
acting, reply mode, the thread view's root, the field holding exactly the reply), and nothing is
sent unless all of them pass. Answers use the same codes as the AppleScript helpers
(messages_ui.TAPBACK_ANSWERS / REPLY_ANSWERS) plus "ax-unavailable", which tells the caller to use
those instead. The log trail only has step names and counts, never names or text.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from . import ax

log = logging.getLogger("backchannel.messages_ax")

RETURN_KEY = 36
PAGES = 25  # how far up a chat to look for a message (tall bubbles: Harry's digests)
LIST_PAGES = 12
POLL = 0.05
SWEEP_STEP = 0.15  # between page scrolls while sweeping: closer together and Messages drops some
IDS = ("TranscriptCollectionView", "MessageEntryView", "ConversationList", "ConversationTitle")


@dataclass
class Answer:
    """What a run printed, in the shape of an osascript Outcome (see messages_ui.Outcome)."""

    returncode: int
    stdout: str
    stderr: str = ""
    timed_out: bool = False


# --------------------------------------------------------------------------- matching (mirrors messages_scripts)


def names_all(label: str, members: list[str]) -> bool:
    """An unnamed group's label: it names every member ("nickname|first|full"), starting with one."""
    if not members or not label:
        return False
    first = False
    for member in members:
        found = False
        for name in member.split("|"):
            if name and name in label:
                found = True
                if label.startswith(name):
                    first = True
        if not found:
            return False
    return first


def title_matches(title: str, names: list[str], members: list[str], loose: int | None = None) -> bool:
    """The chat's title is one of its names. Messages may add to it ("Wifey 4 Lifey 🥰, San Francisco,
    CA": a shared location; Harry's Mac), so a loose name (full name, group name) may also start it
    as "Name, …"; a short name ("Jordan") has to be the whole title."""
    title = " ".join((title or "").split())  # "Coming In Hot 🥵 " ends in a space (Harry's Mac)
    if not title:
        return False
    loose = len(names) if loose is None else loose
    for k, name in enumerate(names):
        name = " ".join((name or "").split())
        if name and (title == name or (k < loose and title.startswith(name + ", "))):
            return True
    return names_all(title, members)


def _row_for_title(labels: list[str], title: str, skip: int = -1) -> int:
    """The sidebar row of the chat whose title this is ("Wifey 4 Lifey 🥰, San Francisco, CA" →
    "Wifey 4 Lifey 🥰, Pinned"): the longest "Name, " start of a label that the title is or starts
    with. -1 for none."""
    title = " ".join((title or "").split())
    best, best_len = -1, 0
    for i, label in enumerate(labels):
        if i == skip:
            continue
        flat = " ".join(label.split())
        cut = 0
        while True:
            cut = flat.find(", ", cut + 1)
            if cut < 0:
                break
            name = flat[:cut].strip()
            if name and (title == name or title.startswith(name + ", ")) and len(name) > best_len:
                best, best_len = i, len(name)
        if flat and title == flat and len(flat) > best_len:
            best, best_len = i, len(flat)
    return best


def row_index(labels: list[str], roles: list[str], names: list[str], loose: int, members: list[str]) -> int:
    """The sidebar row for the conversation, or -1: a label that is a name or starts "Name, " (the
    earliest name wins); else one starting with a loose name; else an unnamed group's shortest
    label naming all its members."""
    best, best_at, loose_best, loose_at = None, -1, None, -1
    for i, label in enumerate(labels):
        if i < len(roles) and roles[i] == "AXGroup":
            continue
        for k, name in enumerate(names):
            if not name:
                continue
            if label == name or label.startswith(name + ", "):
                if best is None or k < best:
                    best, best_at = k, i
            elif k < loose and label.startswith(name):
                if loose_best is None or k < loose_best:
                    loose_best, loose_at = k, i
    if best_at < 0:
        best_at = loose_at
    if best_at < 0 and members:
        pick_len = None
        for i, label in enumerate(labels):
            if i < len(roles) and roles[i] == "AXGroup":
                continue
            if names_all(label, members) and (pick_len is None or len(label) < pick_len):
                best_at, pick_len = i, len(label)
    return best_at


def matches_bubble(label: str, needles: list[str], exacts: list[str], whole: str = "") -> bool:
    """A reply's quote of its root ("Name, Reply Preview, <text>, 8:09 AM") never counts. With
    `whole` (more of the text, whitespace collapsed), the label has to hold that too."""
    if ", Reply Preview, " in label:
        return False
    if whole and whole not in " ".join(label.split()):
        return False
    if any(n and n in label for n in needles):
        return True
    for w in exacts:
        if w and (label == w or label.startswith(w + ",") or f", {w}," in label or label.endswith(", " + w)
                  or f", {w}\n" in label):  # the first line of a longer message
            return True
    return False


def timed_index(labels: list[str], needles: list[str], exacts: list[str], times: list[str], skip: int,
                whole: str = "") -> int:
    """The (skip + 1)-th row from the bottom showing the text at that minute; with fewer, the oldest
    one; -1 for none."""
    seen, oldest = 0, -1
    for i in range(len(labels) - 1, -1, -1):
        label = labels[i]
        if len(label) > 5 and matches_bubble(label, needles, exacts, whole) and any(t in label for t in times):
            seen += 1
            oldest = i
            if seen == skip + 1:
                return i
    return oldest


def find_index(labels: list[str], needles: list[str], exacts: list[str], senders: list[str],
               skip_sender: int, skip_any: int, whole: str = "") -> int:
    """Newest first: with senders, the (skip_sender + 1)-th match naming one; else the
    (skip_any + 1)-th match; -1 for none."""
    sender_hits = any_hits = 0
    any_pick = -1
    for i in range(len(labels) - 1, -1, -1):
        label = labels[i]
        if len(label) <= 5 or not matches_bubble(label, needles, exacts, whole):
            continue
        any_hits += 1
        if any_hits == skip_any + 1:
            any_pick = i
        if not senders and any_pick >= 0:
            break
        if senders and any(s and s in label for s in senders):
            sender_hits += 1
            if sender_hits == skip_sender + 1:
                return i
    return any_pick


def _whole(bubble: Any) -> str:
    return getattr(bubble, "whole", "") or ""


def _skips(bubble: Any) -> tuple[int, int, int]:
    """How many newer look-alikes to pass over: counted among those that match `whole` too, when set."""
    if _whole(bubble):
        return tuple(getattr(bubble, "whole_skips", (0, 0, 0)))  # type: ignore[return-value]
    return bubble.skip_sender, bubble.skip_any, bubble.skip_minute


def _action_name(action: str) -> str:
    """"Name:Thumbs up\\nTarget:0x0\\nSelector:(null)" → "Thumbs up"; standard actions as they are."""
    first = action.split("\n", 1)[0]
    return first[5:] if first.startswith("Name:") else first


def pick_action(actions: list[str], titles: Iterable[str]) -> str | None:
    """The first action whose title starts with one of `titles`, in the order of `titles`."""
    names = [(a, _action_name(a)) for a in actions]
    for title in titles:
        for action, name in names:
            if title and name.startswith(title):
                return action
    return None


def quick_emoji(actions: list[str]) -> list[str]:
    """The emoji Messages offers as quick tapbacks: actions titled with up to 8 characters, all of
    them beyond Latin-1."""
    out = []
    for action in actions:
        name = _action_name(action)
        if action.startswith("Name:") and 0 < len(name) <= 8 and all(ord(ch) >= 256 for ch in name):
            out.append(name)
    return out


# --------------------------------------------------------------------------- the driver


def _messages_pid() -> int | None:
    pgrep = shutil.which("pgrep") or "/usr/bin/pgrep"
    try:
        out = subprocess.run([pgrep, "-x", "Messages"], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    pids = [int(p) for p in out.split() if p.isdigit()]
    return pids[0] if pids else None


def _open_in_background(link: str) -> None:
    subprocess.run(["/usr/bin/open", "-g", link], capture_output=True, timeout=5)


class _Stop(Exception):
    """A run ends early with this answer code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass
class _Run:
    pid: int
    app: Any
    win: Any = None
    spots: dict[str, Any] = field(default_factory=dict)
    trail: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    up: int = 0  # pages the chat was scrolled up to find the message
    list_down: int = 0  # pages the sidebar was scrolled down to find the chat
    started: float = 0.0
    marks: list[str] = field(default_factory=list)  # step timings, for tools/messages_eval.py

    def mark(self, step: str, now: float) -> None:
        self.marks.append(f"{step}={now - self.started:.2f}")


class MessagesAX:
    """Runs tapbacks and replies; every outside effect is injectable for tests."""

    def __init__(self, *, find_pid: Callable[[], int | None] = _messages_pid,
                 application: Callable[[int], Any] = ax.application,
                 open_link: Callable[[str], None] = _open_in_background,
                 post_key: Callable[[int, int], None] = ax.post_key,
                 ready: Callable[[], bool] | None = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self.find_pid = find_pid
        self.application = application
        self.open_link = open_link
        self.post_key = post_key
        self._ready = ready or (lambda: ax.available() and ax.trusted())
        self.sleep = sleep
        self.clock = clock
        self._tidy: list[Callable[[], None]] = []  # scrolling back, done after the answer (see tidy())

    def ready(self) -> bool:
        try:
            return bool(self._ready())
        except Exception:  # noqa: BLE001 - any failure here just means "use the AppleScript helpers"
            return False

    # ------------------------------------------------------------------ public

    def tapback(self, job: Any) -> Answer:
        return self._run(job, self._tapback)

    def reply(self, job: Any) -> Answer:
        return self._run(job, self._reply)

    def tidy(self) -> None:
        """Scroll the chat and the sidebar back where they were. Called once the result is known, so
        the answer doesn't wait for it."""
        steps, self._tidy = self._tidy, []
        for step in steps:
            try:
                step()
            except (OSError, _Stop):
                pass

    def _run(self, job: Any, body: Callable[[_Run, Any], str]) -> Answer:
        if not self.ready():
            return Answer(0, "ax-unavailable")
        pid = self.find_pid()
        if not pid:
            return Answer(0, "not-running")
        try:
            run = _Run(pid, self.application(pid), started=self.clock())
        except OSError:
            return Answer(0, "ax-unavailable")
        try:
            code = body(run, job)
        except _Stop as stop:
            code = stop.code
        run.mark("end", self.clock())
        line = code + ("|" + " ".join(run.trail) if run.trail else "")
        return Answer(0, "\n".join([line, *run.extra, "times\t" + " ".join(run.marks)]))

    # ------------------------------------------------------------------ waiting

    def _wait(self, check: Callable[[], Any], seconds: float) -> Any:
        end = self.clock() + seconds
        while True:
            value = check()
            if value:
                return value
            if self.clock() >= end:
                return None
            self.sleep(POLL)

    # ------------------------------------------------------------------ finding things

    @staticmethod
    def _spots(win: Any) -> dict[str, Any]:
        """The window's landmarks by identifier, in one walk that doesn't go inside them."""
        found: dict[str, Any] = {}
        queue = [(win, 0)]
        while queue:
            el, depth = queue.pop(0)
            ident, kids = el.get_many(["AXIdentifier", "AXChildren"])
            if isinstance(ident, str) and ident in IDS:
                found.setdefault(ident, el)
                continue
            if depth < 9:
                queue.extend((k, depth + 1) for k in (kids or []) if not isinstance(k, (str, int, float)))
        return found

    def _look(self, run: _Run) -> bool:
        """Find Messages' main window (the one with the conversation list or a chat) and its landmarks."""
        for win in run.app.get("AXWindows") or []:
            spots = self._spots(win)
            if "ConversationList" in spots or "TranscriptCollectionView" in spots:
                run.win, run.spots = win, spots
                return True
        return False

    def _relook(self, run: _Run, need: str, seconds: float = 1.5) -> Any:
        return self._wait(lambda: self._look(run) and run.spots.get(need), seconds)

    @staticmethod
    def _find_in(root: Any, ident: str, depth: int = 6) -> Any:
        queue = [(root, 0)]
        while queue:
            el, d = queue.pop(0)
            found, kids = el.get_many(["AXIdentifier", "AXChildren"])
            if found == ident:
                return el
            if d < depth:
                queue.extend((k, d + 1) for k in (kids or []) if not isinstance(k, (str, int, float)))
        return None

    def _field(self, run: _Run) -> Any:
        entry = run.spots.get("MessageEntryView")
        return self._find_in(entry, "messageBodyField") if entry is not None else None

    @staticmethod
    def _text(el: Any) -> str:
        value = el.get("AXValue") if el is not None else None
        return value if isinstance(value, str) else ""

    def _title(self, run: _Run) -> str:
        title = run.spots.get("ConversationTitle")
        value = title.get("AXDescription") if title is not None else None
        return value if isinstance(value, str) else ""

    @staticmethod
    def _rows(container: Any) -> tuple[list[Any], list[str], list[str], list[bool]]:
        kids = container.children() if container is not None else []
        labels, roles, selected = [], [], []
        for kid in kids:
            role, desc, sel = kid.get_many(["AXRole", "AXDescription", "AXSelected"])
            roles.append(role if isinstance(role, str) else "")
            labels.append(desc if isinstance(desc, str) else "")
            selected.append(sel is True)
        return kids, labels, roles, selected

    @staticmethod
    def _snapshot(container: Any) -> tuple:
        """What's on screen: each row's label and top edge."""
        out = []
        for kid in (container.children() if container is not None else []):
            label, box = kid.get_many(["AXDescription", "AXFrame"])
            out.append((label if isinstance(label, str) else "", round(box[1]) if box else None))
        return tuple(out)

    def _page(self, container: Any, action: str, wait: float = 0.2) -> bool:
        """Scroll a page and wait for the animation (about half a second) to end; False when nothing
        moved within `wait` seconds (the end of the list). Reading or scrolling again mid-animation
        skips rows (Harry's Mac: messages a page up weren't found)."""
        before = self._snapshot(container)
        if not container.perform(action):
            return False
        if not self._wait(lambda: self._snapshot(container) != before, wait):
            return False
        self._settle(container)
        return True

    def _settle(self, container: Any, seconds: float = 1.5) -> None:
        """Wait until the rows stop moving (scroll animations, older messages loading in)."""
        last = [self._snapshot(container)]

        def still() -> bool:
            self.sleep(0.06)
            now = self._snapshot(container)
            same, last[0] = now == last[0], now
            return same

        self._wait(still, seconds)

    def _to_bottom(self, transcript: Any, pages: int = 40) -> int:
        """Scroll to the newest messages (sweeping, like _sweep_up); how many pages that took."""
        last = self._snapshot(transcript)
        if not transcript.perform("AXScrollDownByPage"):
            return 0
        if not self._wait(lambda: self._snapshot(transcript) != last, 0.2):
            return 0  # already there
        moved = 1
        while moved < pages:
            self.sleep(SWEEP_STEP)
            now = self._snapshot(transcript)
            if now == last:
                break
            last = now
            transcript.perform("AXScrollDownByPage")
            moved += 1
        self._settle(transcript)
        return moved

    # ------------------------------------------------------------------ the chat

    def _open_chat(self, run: _Run, job: Any) -> None:
        names, loose, members = list(job.candidates), int(job.loose), list(job.members)
        if not self._look(run):
            raise _Stop("no-window")
        if title_matches(self._title(run), names, members, loose):
            run.trail.append("shown")
            return
        for link in job.links[:1]:
            run.trail.append("link")
            try:
                self.open_link(link)
            except (OSError, subprocess.SubprocessError):
                run.trail.append("link-failed")
                break
            if self._wait(lambda: self._look(run) and title_matches(self._title(run), names, members, loose), 4.0):
                run.trail.append("title")
                return
            run.trail.append("no")
        chats = run.spots.get("ConversationList")
        if chats is None:
            raise _Stop("no-list")
        before = self._title(run)  # the chat on screen now, to press off if both end up selected
        at = self._find_row(run, chats, names, loose, members)
        if at < 0:
            raise _Stop("row-not-found")
        pressed_before = False

        def shown() -> bool:
            return self._look(run) and title_matches(self._title(run), names, members, loose)

        # Pressing a row toggles it, like a Command-click, and pinned rows never say whether they're
        # selected (Harry's Mac: pressing EastMeetEast while Wifey's chat showed selected both, and
        # Messages showed neither). So: press, look at the title, and fix what it shows.
        for step in range(5):
            kids, labels, roles, selected = self._rows(run.spots.get("ConversationList") or chats)
            at = row_index(labels, roles, names, loose, members)
            if at < 0:
                raise _Stop("row-not-found")
            title = self._title(run)
            if not title or (step and title != before and not shown()):
                # Two or more selected: press off the one that was showing, and any that say so.
                prev = _row_for_title(labels, before, skip=at) if before and not pressed_before else -1
                if prev >= 0:
                    kids[prev].perform("AXPress")
                    pressed_before = True
                    run.trail.append(f"off{prev + 1}")
                for i, on in enumerate(selected):
                    if on and i != at:
                        kids[i].perform("AXPress")
                if prev < 0 and not any(on and i != at for i, on in enumerate(selected)):
                    kids[at].perform("AXPress")  # nothing else to press off: start over from ours
            else:
                kids[at].perform("AXPress")
            if self._wait(shown, 1.2):
                run.trail.append(f"row{at + 1}/{run.list_down}" + (f" p{step + 1}" if step else ""))
                return
            self._wait(lambda: self._look(run) and self._title(run) != title, 0.4)
        raise _Stop("not-selected")

    def _find_row(self, run: _Run, chats: Any, names: list[str], loose: int, members: list[str]) -> int:
        """The chat's row, scrolling the sidebar (top first, then down a page at a time) if needed."""
        def look() -> int:
            _kids, labels, roles, _sel = self._rows(chats)
            return row_index(labels, roles, names, loose, members)

        at = look()
        if at >= 0:
            return at
        for _ in range(6):
            if not self._page(chats, "AXScrollUpByPage"):
                break
        at = look()
        while at < 0 and run.list_down < LIST_PAGES:
            if not self._page(chats, "AXScrollDownByPage"):
                break
            run.list_down += 1
            at = look()
        return at

    def _list_home(self, run: _Run) -> None:
        chats = run.spots.get("ConversationList")
        if run.list_down and chats is not None:
            for _ in range(run.list_down + 2):
                if not self._page(chats, "AXScrollUpByPage"):
                    break
            run.list_down = 0

    def _leave_thread(self, run: _Run) -> None:
        """After a reply Messages keeps the thread open; close it (only with nothing typed in it)."""
        box = self._field(run)
        if box is None or not str(box.get("AXPlaceholderValue") or "").startswith("Reply"):
            return
        if self._text(box).strip():
            return
        run.spots["TranscriptCollectionView"].perform("AXCancel")
        if self._wait(lambda: self._look(run) and not str((self._field(run) or box).get("AXPlaceholderValue")
                                                          or "").startswith("Reply"), 1.5):
            run.trail.append("thread-closed")

    def _prepare(self, run: _Run, job: Any) -> Any:
        """Open the chat, close a leftover thread view, wait for messages. The transcript."""
        self._open_chat(run, job)
        run.mark("chat", self.clock())
        if self._relook(run, "TranscriptCollectionView") is None:
            raise _Stop("no-transcript")
        self._leave_thread(run)
        transcript = self._relook(run, "TranscriptCollectionView")
        if transcript is None:
            raise _Stop("no-transcript")
        self._wait(lambda: transcript.children(), 2.0)
        return transcript

    # ------------------------------------------------------------------ the message

    @staticmethod
    def _balloon(row: Any) -> Any:
        """The part of a message row that has its Reply and tapback actions."""
        kids = row.children()
        for kid in kids:
            if pick_action(kid.actions(), ["Reply"]):
                return kid
        for kid in kids:
            for grand in kid.children():
                if pick_action(grand.actions(), ["Reply"]):
                    return grand
        if pick_action(row.actions(), ["Reply"]):
            return row
        return kids[0] if kids else row

    def _on_screen(self, transcript: Any, bubble: Any, timed: bool) -> int:
        _kids, labels, _roles, _sel = self._rows(transcript)
        if timed and bubble.times:
            return timed_index(labels, bubble.needles, bubble.exacts, bubble.times, _skips(bubble)[2], _whole(bubble))
        if timed:
            return -1
        skip_sender, skip_any, _minute = _skips(bubble)
        return find_index(labels, bubble.needles, bubble.exacts, bubble.senders, skip_sender, skip_any, _whole(bubble))

    def _locate(self, run: _Run, transcript: Any, bubble: Any) -> Any:
        """The message's balloon: by text and minute, paging up (at most PAGES); else by text, sender
        and skips on screen. None when it isn't found."""
        if self._to_bottom(transcript):
            run.trail.append("down")
        if bubble.times:
            at = self._on_screen(transcript, bubble, True)
            if at < 0:
                at = self._sweep_up(run, transcript, bubble)
            if at >= 0:
                run.trail.append(f"t{run.up}")
                return self._balloon(transcript.children()[at])
            run.trail.append(f"u{run.up}")
            self._to_end(run, transcript)
        at = self._on_screen(transcript, bubble, False)
        if at < 0:
            _kids, labels, _r, _s = self._rows(transcript)
            hits = [lb for lb in labels if len(lb) > 5 and matches_bubble(lb, bubble.needles, bubble.exacts, _whole(bubble))]
            timed = [lb for lb in hits if any(t in lb for t in bubble.times)]
            run.trail.append(f"b{len(labels)}/{len(hits)}/{len(timed)}")
            return None
        run.trail.append("s")
        return self._balloon(transcript.children()[at])

    def _sweep_up(self, run: _Run, transcript: Any, bubble: Any) -> int:
        """Page up while looking: a page every SWEEP_STEP seconds, the rows read in between, so the
        chat doesn't stop for each page (about 0.35 s a page instead of 0.7). A message seen on the
        way is found again once the chat has stopped. Its row, or -1 (the top, or PAGES pages)."""
        def seen(rows: tuple) -> bool:
            labels = [label for label, _top in rows]
            return timed_index(labels, bubble.needles, bubble.exacts, bubble.times, _skips(bubble)[2],
                               _whole(bubble)) >= 0

        last = self._snapshot(transcript)
        while run.up < PAGES:
            transcript.perform("AXScrollUpByPage")
            run.up += 1
            # Look twice a page: Messages sometimes runs queued pages together.
            self.sleep(SWEEP_STEP / 2)
            hit = seen(self._snapshot(transcript))
            self.sleep(SWEEP_STEP / 2)
            now = self._snapshot(transcript)
            if hit or seen(now):
                self._settle(transcript)
                at = self._on_screen(transcript, bubble, True)
                if at < 0 and self._page(transcript, "AXScrollDownByPage"):  # it moved past: a page back
                    at = self._on_screen(transcript, bubble, True)
                if at >= 0:
                    return at
            if now == last and not self._wait(lambda: self._snapshot(transcript) != last, 0.6):
                run.up -= 1  # nothing moved: the top of the chat
                break
            last = self._snapshot(transcript)
        self._settle(transcript)
        at = self._on_screen(transcript, bubble, True)
        if at >= 0:
            return at
        # Missed on the way up: back down a page at a time, each one settled (slower, but sure).
        run.trail.append("slow")
        for _ in range(run.up):
            if not self._page(transcript, "AXScrollDownByPage"):
                break
            run.up -= 1
            at = self._on_screen(transcript, bubble, True)
            if at >= 0:
                return at
        return -1

    @staticmethod
    def _still(el: Any, bubble: Any) -> bool:
        """The balloon still shows the message (rows are reused as the chat scrolls)."""
        label = el.get("AXDescription")
        if not isinstance(label, str) or not label:
            parent = el.get("AXParent")
            label = parent.get("AXDescription") if parent is not None else ""
        if not isinstance(label, str) or not matches_bubble(label, bubble.needles, bubble.exacts, _whole(bubble)):
            return False
        return not bubble.times or any(t in label for t in bubble.times)

    def _checked(self, run: _Run, transcript: Any, el: Any, bubble: Any) -> Any:
        if self._still(el, bubble):
            return el
        for _ in range(2):
            self.sleep(0.3)
            at = self._on_screen(transcript, bubble, bool(bubble.times))
            if at < 0:
                at = self._on_screen(transcript, bubble, False)
            if at >= 0:
                again = self._balloon(transcript.children()[at])
                if self._still(again, bubble):
                    run.trail.append("again")
                    return again
        raise _Stop("bubble-moved")

    def _to_end(self, run: _Run, transcript: Any) -> None:
        """Back down to the newest messages after paging up."""
        if run.up and transcript is not None:
            self._to_bottom(transcript, run.up + 4)
            run.up = 0

    def _find_message(self, run: _Run, job: Any) -> tuple[Any, Any]:
        transcript = self._prepare(run, job)
        run.mark("ready", self.clock())
        el = self._locate(run, transcript, job.bubble)
        run.mark("found", self.clock())
        if el is None:
            raise _Stop("bubble-not-found")
        # Rows are reused as the chat moves: act only once it has stopped, on a message that is in
        # view (a menu for one mostly off screen opens late), re-checked right before.
        self._settle(transcript)
        el = self._checked(run, transcript, el, job.bubble)
        if not self._in_view(el, transcript):
            el.perform("AXScrollToVisible")
            run.trail.append("into-view")
            self._settle(transcript)
            el = self._checked(run, transcript, el, job.bubble)
        run.mark("checked", self.clock())
        return transcript, el

    @staticmethod
    def _in_view(el: Any, transcript: Any) -> bool:
        box, area = el.get("AXFrame"), transcript.get("AXFrame")
        if not box or not area:
            return True
        top, bottom = area[1] + 60, area[1] + area[3] - 60  # the title and the entry float over the ends
        return box[1] + box[3] > top and box[1] < bottom and min(box[1] + box[3], bottom) - max(box[1], top) >= min(40, box[3])

    # ------------------------------------------------------------------ tapbacks

    def _later(self, run: _Run, transcript: Any) -> None:
        self._tidy.append(lambda: (self._to_end(run, transcript), self._list_home(run)))

    def _tapback(self, run: _Run, job: Any) -> str:
        transcript, el = self._find_message(run, job)
        self._later(run, transcript)
        actions = el.actions()
        run.extra.append("quick\t" + " ".join(quick_emoji(actions)))
        action = pick_action(actions, job.titles())
        if action is None:
            return "no-tapback-item"
        if not self._still(el, job.bubble):
            return "bubble-moved"
        return "chosen-action" if el.perform(action) else "no-tapback-item"

    # ------------------------------------------------------------------ replies

    def _menu_with(self, run: _Run, title: str) -> tuple[Any, Any] | None:
        """An open context menu in the window and its item starting with `title`."""
        queue = [(run.win, 0)]
        while queue:
            el, depth = queue.pop(0)
            role, ident, kids = el.get_many(["AXRole", "AXIdentifier", "AXChildren"])
            if role == "AXMenu":
                for item in kids or []:
                    name = item.get("AXTitle")
                    if isinstance(name, str) and name.startswith(title):
                        return el, item
                continue
            if ident in IDS:
                continue
            if depth < 5:
                queue.extend((k, depth + 1) for k in (kids or []) if not isinstance(k, (str, int, float)))
        return None

    def _thread_root(self, run: _Run, bubble: Any) -> bool:
        if not self._look(run):
            return False
        _kids, labels, roles, _s = self._rows(run.spots.get("TranscriptCollectionView"))
        if "AXButton" not in roles[:2]:
            return False  # still the whole chat
        return any(matches_bubble(lb, bubble.needles, bubble.exacts, _whole(bubble))
                   and (not bubble.times or any(t in lb for t in bubble.times))
                   for lb in labels)

    def _replying(self, run: _Run) -> bool:
        if not self._look(run):
            return False
        box = self._field(run)
        return box is not None and str(box.get("AXPlaceholderValue") or "").startswith("Reply")

    def _close_thread(self, run: _Run) -> None:
        if self._look(run) and run.spots.get("TranscriptCollectionView") is not None:
            run.spots["TranscriptCollectionView"].perform("AXCancel")
            if self._wait(lambda: not self._replying(run), 1.5):
                run.trail.append("thread-closed")

    def _reply(self, run: _Run, job: Any) -> str:
        if not (job.text or "").strip():
            return "no-text"
        transcript, el = self._find_message(run, job)
        self._later(run, transcript)
        box = self._field(run)
        if box is None:
            return "no-reply-field"
        if self._text(box).strip():
            return "field-busy"  # a draft in Messages: leave it alone
        if not pick_action(el.actions(), ["Reply"]):
            return "no-reply-item"
        if not self._still(el, job.bubble):
            return "bubble-moved"
        el.perform("AXShowMenu")
        found = self._wait(lambda: self._menu_with(run, "Reply"), 3.0)
        if not found:
            menu = self._wait(lambda: self._menu_with(run, ""), 0.2)
            if menu:
                menu[0].perform("AXCancel")
                return "no-reply-item"
            return "no-menu"
        run.mark("menu", self.clock())
        found[1].perform("AXPress")
        if not self._wait(lambda: self._replying(run), 2.0):
            return "no-reply-mode"
        run.mark("replying", self.clock())
        run.trail.append("menu")
        # The thread view ("Dismiss replies", then the message being answered and its replies)
        # has to show our message, at its minute.
        if not self._wait(lambda: self._thread_root(run, job.bubble), 2.0):
            self._close_thread(run)
            return "wrong-thread"
        box = self._field(run)
        if box is None:
            self._close_thread(run)
            return "no-reply-field"
        if self._text(box).strip():
            self._close_thread(run)
            return "field-busy"
        box.set_text("AXValue", job.text)
        if not self._wait(lambda: self._text(box) == job.text, 1.0):
            box.set_text("AXValue", "")
            self._close_thread(run)
            return "nothing-typed"
        if not self._replying(run):
            box.set_text("AXValue", "")
            self._close_thread(run)
            return "reply-mode-lost"
        run.mark("typed", self.clock())
        self.post_key(run.pid, RETURN_KEY)
        if not self._wait(lambda: not self._text(box).strip(), 4.0):
            if self._text(box) == job.text:  # Return didn't take: take the text back out
                box.set_text("AXValue", "")
                self._close_thread(run)
                return "not-sent"
            return "returned-unconfirmed"
        run.mark("sent", self.clock())
        self._close_thread(run)
        return "sent-ax"
