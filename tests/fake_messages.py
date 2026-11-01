"""A stand-in for osascript + the Messages app, for the Messages helper tests (no osascript ever runs)."""

from __future__ import annotations

import threading
from contextlib import closing
from pathlib import Path
from typing import Callable

from server import fakedb
from server.messages_ui import Outcome


class FakeMessages:
    """Answers each helper script by its argv[0] ("mark-read", "mark-unread", "tapback", "reply", "probe";
    [] = inspect).

    `answers` holds the Outcome per script; `effects` run first (e.g. write the
    row Messages would have written). Non-probe calls are recorded in `calls`
    and can be held at `gate`; `max_active` shows whether two ever overlapped.
    """

    def __init__(self, **answers: Outcome):
        self.answers: dict[str, Outcome] = {
            "mark-read": Outcome(0, "ok-menu", ""),
            "mark-unread": Outcome(0, "ok-action", ""),
            "tapback": Outcome(0, "chosen-overlay", ""),
            "reply": Outcome(0, "sent-paste|shown t0 r:menu", ""),
            "compose": Outcome(0, "sent-paste|shown", ""),
            "reply-aim": Outcome(0, "aim:400,300|shown t0\nfront\tcom.example.backchannel\nhint\tiMessage", ""),
            "reply-finish": Outcome(0, "sent-paste|r:right-click", ""),
            "probe": Outcome(0, "ready", ""),
            "inspect": Outcome(0, "", ""),
        }
        self.answers.update({k.replace("_", "-"): v for k, v in answers.items()})
        self.effects: dict[str, Callable[[list[str]], None]] = {}
        self.calls: list[tuple[str, tuple[str, ...], list[str], float]] = []
        self.probes = 0
        self.gate: threading.Event | None = None
        self.started = threading.Event()
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def __call__(self, script: tuple[str, ...], argv: list[str], timeout: float) -> Outcome:
        name = argv[0] if argv else "inspect"
        if name == "probe":
            self.probes += 1
            return self.answers["probe"]
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            self.calls.append((name, script, list(argv), timeout))
            self.started.set()
            if self.gate is not None:
                assert self.gate.wait(10)
            effect = self.effects.get(name)
            if effect is None and name == "reply-finish":
                effect = self.effects.get("reply")  # the step that types and sends a reply
            if effect is not None:
                effect(list(argv))
            return self.answers[name]
        finally:
            with self._lock:
                self.active -= 1

    def argv(self, name: str) -> list[str]:
        return next(argv for n, _, argv, _ in self.calls if n == name)

    def names(self) -> list[str]:
        return [n for n, *_ in self.calls]


def tagged(argv: list[str], tag: str) -> list[str]:
    return [a[len(tag) + 1:] for a in argv[1:] if a.startswith(tag + ":")]


def chat_of(chat_db: Path, guid: str) -> int:
    with closing(fakedb.connect(chat_db)) as conn:
        return conn.execute("SELECT j.chat_id FROM message m JOIN chat_message_join j ON j.message_id = m.ROWID "
                            "WHERE m.guid = ?", (guid,)).fetchone()[0]


def write_tapback(chat_db: Path, target: str, kind_type: int, emoji: str | None = None) -> str:
    """What Messages writes when you pick a tapback: a row of yours pointing at the target (an emoji
    tapback, 2006/3006, names its emoji)."""
    extra = {"associated_message_emoji": emoji} if emoji is not None else {}
    _, guid = fakedb.write_as_messages_app(chat_db, chat_of(chat_db, target), text="Loved “…”",
                                           associated_message_guid=f"p:0/{target}",
                                           associated_message_type=kind_type, **extra)
    return guid


def write_reply(chat_db: Path, root: str, text: str, *, threaded: bool = True) -> str:
    """What Messages writes when you send from its Reply field."""
    columns = {"thread_originator_guid": root, "thread_originator_part": "0:0:10"} if threaded else {}
    _, guid = fakedb.write_as_messages_app(chat_db, chat_of(chat_db, root), text=text, **columns)
    return guid


def clear_unread(chat_db: Path) -> None:
    with closing(fakedb.connect(chat_db)) as conn:
        conn.execute("UPDATE message SET is_read = 1, date_read = 1 WHERE is_from_me = 0 AND is_read = 0")
