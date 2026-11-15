"""Tapbacks and thread replies with Messages in the background (server/messages_ax.py), against a
small fake of Messages' accessibility tree (macOS 26.3's shape: a sidebar list, a transcript that
pages, a menu with Reply…, a thread view, the message field)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fake_messages import FakeMessages, chat_of, write_reply, write_tapback
from server import fakedb
from server.messages_ax import (Answer, MessagesAX, find_index, matches_bubble, names_all, pick_action, quick_emoji,
                                row_index, timed_index, title_matches)
from server.messages_ui import SYNONYMS, Bubble, TapbackJob

ACTION = "Name:{}\nTarget:0x0\nSelector:(null)"
VISIBLE = 6  # rows on screen
PAGE = 5  # rows a page scroll moves
LIST_VISIBLE, LIST_PAGE = 14, 12  # the sidebar


# --------------------------------------------------------------------------- matching


def test_row_matching_prefers_whole_names_then_loose_starts_then_members():
    labels = ["Jordan Parker, hey", "Jordan, Unread, see you", "Jordan Park, sure, 2:41 PM"]
    roles = ["AXStaticText"] * 3
    assert row_index(labels, roles, ["Jordan Park", "Jordan"], 1, []) == 2  # earliest name wins
    assert row_index(labels, roles, ["Jordan"], 0, []) == 1  # strict: "Jordan, …" only
    assert row_index(["Jordan Parkour, hi"], ["AXStaticText"], ["Jordan Park"], 1, []) == 0  # loose prefix
    assert row_index(["Jordan Parkour, hi"], ["AXStaticText"], ["Jordan Park"], 0, []) == -1
    group = ["Sam & Priya, Unread, hi", "Sam, Priya & Lee, see you all tomorrow"]
    assert row_index(group, ["AXStaticText"] * 2, ["Weekend"], 1, ["Sam|Sam Lee", "Priya"]) == 0  # shortest naming all
    assert row_index(["Weekend, hi"], ["AXGroup"], ["Weekend"], 1, []) == -1  # groups aren't rows
    assert names_all("Sam & Priya", ["Sam", "Priya"]) and not names_all("Priya & Sam", ["Sam", "Lee"])
    assert title_matches("Jordan", ["Jordan Park", "Jordan"], []) and not title_matches("", ["x"], [])


def test_bubble_matching():
    assert matches_bubble("Sam, see you at 7, 2:41 PM", ["see you at 7"], [])
    assert not matches_bubble("Sam, Reply Preview, see you at 7, 2:41 PM", ["see you at 7"], [])  # a reply's quote
    assert matches_bubble("Sam, Includes picture, 2:59 PM", [], ["Includes picture"])
    assert matches_bubble("You, ✅ Done:\n- distrokid\n- call, 4:47 PM", [], ["✅ Done:"])  # short first line
    assert not matches_bubble("Sam, Donezo, 1:00 PM", [], ["Done"])
    labels = ["Sam, ok, 1:05 PM", "Lee, ok, 1:05 PM", "Sam, ok, 1:07 PM"]
    assert timed_index(labels, [], ["ok"], [" 1:05"], 0) == 1  # newest at that minute first
    assert timed_index(labels, [], ["ok"], [" 1:05"], 1) == 0
    assert timed_index(labels, [], ["ok"], [" 1:05"], 5) == 0  # fewer than expected: the oldest
    assert timed_index(labels, [], ["ok"], [" 2:00"], 0) == -1
    assert find_index(labels, [], ["ok"], ["Lee"], 0, 0) == 1 and find_index(labels, [], ["ok"], [], 0, 1) == 1
    # Harry's daily digests share a first line and a minute: more of the text tells them apart.
    days = ["You, 📱 TEXT SOMEONE\n\nYaseen Ali (5 weeks): say hi, 10:00 AM",
            "You, 📱 TEXT SOMEONE\n\nMaya Chen (3 weeks): check in, 10:00 AM"]
    first = ["📱 TEXT SOMEONE"]
    assert timed_index(days, first, [], [" 10:00"], 0) == 1  # the newer day, wrongly
    assert timed_index(days, first, [], [" 10:00"], 0, whole="📱 TEXT SOMEONE Yaseen Ali (5 weeks)") == 0


def test_actions_by_title():
    actions = ["AXShowMenu", ACTION.format("Heart"), ACTION.format("Ha ha!"), ACTION.format("😂"),
               ACTION.format("👀"), ACTION.format("Reply…")]
    assert pick_action(actions, ["Haha", "Ha"]) == ACTION.format("Ha ha!")
    assert pick_action(actions, SYNONYMS["love"]) == ACTION.format("Heart")
    assert pick_action(actions, ["Thumbs up"]) is None
    assert quick_emoji(actions) == ["😂", "👀"]


# --------------------------------------------------------------------------- a fake Messages


class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


class El:
    """An accessibility element: fixed attributes, or computed ones from `dyn`."""

    def __init__(self, role="AXGroup", ident=None, desc=None, kids=(), actions=(), dyn=None, on=None, **attrs):
        self.attrs = {"AXRole": role, "AXIdentifier": ident, "AXDescription": desc, **attrs}
        self.kids = kids
        self.acts = actions if callable(actions) else list(actions)
        self.dyn = dyn or {}
        self.on = on or {}
        self.parent = None

    def get(self, name):
        if name == "AXChildren":
            kids = self.kids() if callable(self.kids) else list(self.kids)
            for k in kids:
                k.parent = self
            return kids
        if name == "AXParent":
            return self.parent
        if name in self.dyn:
            return self.dyn[name]()
        return self.attrs.get(name)

    def get_many(self, names):
        return [self.get(n) for n in names]

    def children(self):
        return self.get("AXChildren") or []

    def actions(self):
        return list(self.acts() if callable(self.acts) else self.acts)

    def perform(self, action):
        handler = self.on.get(action) or self.on.get(action.split("\n")[0])
        if handler is None:
            return action in self.acts
        handler()
        return True

    def set_text(self, name, text):
        self.on["set:" + name](text)
        return True


class FakeApp:
    """Two chats (a group and a DM); the transcript shows VISIBLE rows and pages PAGE at a time."""

    def __init__(self, open_chat="Jordan Park", others=0):
        self.chats = {
            "Sunday Soccer ⚽": [f"Sam, game {i} on sunday, 1:{i:02d} PM" for i in range(30)],
            "Jordan Park": [f"Jordan Park, hike {i}?, 2:{i:02d} PM" for i in range(10)],
            **{f"Friend {i}": [f"Friend {i}, hey, 9:00 AM"] for i in range(others)},
        }
        self.unread = {"Friend 50"}
        self.top = 0  # the sidebar's first row on screen
        self.selected = {open_chat}
        self.bottom = {name: len(msgs) for name, msgs in self.chats.items()}
        self.thread: str | None = None  # the label of the message whose thread is open
        self.field = ""
        self.menu: El | None = None
        self.sent: list[tuple[str, str]] = []  # (root label, text)
        self.tapbacks: list[tuple[str, str]] = []
        self.reply_opens = None  # the label a Reply… press opens (tests: the wrong one)
        self.return_works = True
        self.rows = [El("AXStaticText", desc=f"{name}, hi", dyn={"AXSelected": (lambda n=name: n in self.selected)},
                        on={"AXPress": (lambda n=name: self._toggle(n)),
                            "Name:Mark as Read": (lambda n=name: self.unread.discard(n)),
                            "Name:Mark as Unread": (lambda n=name: self.unread.add(n))})
                     for name in self.chats]
        for row, name in zip(self.rows, self.chats):
            row.acts = (lambda n=name: ["AXPress", ACTION.format("Mark as Read" if n in self.unread else "Mark as Unread")])
        self.list = El(ident="ConversationList", kids=lambda: self.rows[self.top:self.top + LIST_VISIBLE],
                       dyn={"AXSelectedChildren": lambda: [r for r, n in zip(self.rows, self.chats) if n in self.selected]},
                       on={"AXScrollDownByPage": lambda: self._list_scroll(LIST_PAGE),
                           "AXScrollUpByPage": lambda: self._list_scroll(-LIST_PAGE)})
        self.transcript = El(ident="TranscriptCollectionView", kids=self._rows,
                             on={"AXScrollUpByPage": lambda: self._scroll(-PAGE),
                                 "AXScrollDownByPage": lambda: self._scroll(PAGE), "AXCancel": self._cancel})
        self.box = El("AXTextField", ident="messageBodyField",
                      dyn={"AXValue": lambda: self.field,
                           "AXPlaceholderValue": lambda: "Reply" if self.thread else "iMessage"},
                      on={"set:AXValue": self._type})
        entry = El(ident="MessageEntryView", kids=[El("AXButton", desc="add"), self.box])
        title = El("AXButton", ident="ConversationTitle", dyn={"AXDescription": self._title})
        self.title_el = title
        self.content = El(kids=lambda: [El(kids=[El(kids=[self.transcript, entry])]), El(kids=[self.list]), title,
                                        *([self.menu] if self.menu else [])])
        self.app = El("AXApplication", AXWindows=[El("AXWindow", ident="SceneWindow", kids=[self.content])])

    # what's shown
    def shown(self) -> str | None:
        return next(iter(self.selected)) if len(self.selected) == 1 else None

    def _title(self):
        name = self.shown()
        return name.split(" ")[0] if name == "Jordan Park" else name

    def _toggle(self, name):
        self.selected ^= {name}

    def _list_scroll(self, rows):
        self.top = min(max(0, len(self.rows) - LIST_VISIBLE), max(0, self.top + rows))

    def _rows(self):
        name = self.shown()
        if name is None:
            return []
        if self.thread:
            return [El("AXButton", desc="Dismiss replies"), self._row(self.thread)]
        msgs = self.chats[name]
        bottom = self.bottom[name]
        return [self._row(label) for label in msgs[max(0, bottom - VISIBLE):bottom]]

    def _row(self, label):
        balloon = El(ident="Sticker", desc=label,
                     actions=["AXShowMenu", *(ACTION.format(t) for t in ("Heart", "Thumbs up", "😂", "Reply…"))],
                     on={"AXShowMenu": lambda: self._open_menu(label),
                         "Name:Heart": lambda: self.tapbacks.append((label, "Heart")),
                         "Name:Thumbs up": lambda: self.tapbacks.append((label, "Thumbs up"))})
        return El(desc=label, kids=[balloon], AXFrame=(0.0, 300.0, 600.0, 60.0))

    def _scroll(self, rows):
        name = self.shown()
        msgs = self.chats[name]
        self.bottom[name] = min(len(msgs), max(VISIBLE, self.bottom[name] + rows))

    def _open_menu(self, label):
        reply = El("AXMenuItem", AXTitle="Reply…", on={"AXPress": lambda: self._reply_to(label)})
        self.menu = El("AXMenu", kids=[El("AXMenuItem", AXTitle="Tapback"), reply],
                       on={"AXCancel": lambda: setattr(self, "menu", None)})

    def _reply_to(self, label):
        self.menu = None
        self.thread = self.reply_opens or label

    def _cancel(self):
        self.thread = None

    def _type(self, text):
        self.field = text

    def key(self, pid, keycode):
        assert keycode == 36
        if self.return_works and self.field:
            self.sent.append((self.thread, self.field))
            self.field = ""


def make(app: FakeApp, links=None) -> MessagesAX:
    clock = Clock()

    def open_link(link):
        app.selected = {"Jordan Park"}
        if links is not None:
            links.append(link)

    return MessagesAX(find_pid=lambda: 4242, application=lambda pid: app.app, open_link=open_link,
                      post_key=app.key, ready=lambda: True, sleep=clock.sleep, clock=clock.now)


def soccer_job(i: int, text: str = "") -> SimpleNamespace:
    bubble = Bubble(needles=[f"game {i} on sunday"], times=[f" 1:{i:02d}"])
    return SimpleNamespace(candidates=["Sunday Soccer ⚽"], loose=1, members=[], links=[], bubble=bubble, text=text,
                           titles=lambda: list(SYNONYMS["like"]))


def code(answer: Answer) -> tuple[str, str]:
    first = answer.stdout.splitlines()[0]
    head, _, trail = first.partition("|")
    return head, trail


# --------------------------------------------------------------------------- the driver


def test_tapback_on_an_older_message_in_another_chat():
    app = FakeApp(open_chat="Jordan Park")
    ax = make(app)
    answer = ax.tapback(soccer_job(12))
    head, trail = code(answer)
    assert head == "chosen-action"
    assert app.tapbacks == [("Sam, game 12 on sunday, 1:12 PM", "Thumbs up")]
    assert app.selected == {"Sunday Soccer ⚽"}  # opened from the sidebar, Jordan pressed off again
    assert "row1/0" in trail.split() and " t" in trail
    assert "quick\t😂" in answer.stdout  # Messages' quick emoji, for the app's picker
    assert app.bottom["Sunday Soccer ⚽"] < 30
    ax.tidy()  # after the answer: back to the newest messages
    assert app.bottom["Sunday Soccer ⚽"] == 30


def test_a_dm_opens_with_its_link_in_the_background():
    app = FakeApp(open_chat="Sunday Soccer ⚽")
    links: list[str] = []
    ax = make(app, links)
    bubble = Bubble(needles=["hike 8?"], times=[" 2:08"])
    job = SimpleNamespace(candidates=["Jordan Park", "Jordan"], loose=1, members=[], links=["imessage://+14155550134"],
                          bubble=bubble, text="", titles=lambda: list(SYNONYMS["love"]))
    head, trail = code(ax.tapback(job))
    assert head == "chosen-action" and trail.startswith("link title")
    assert links == ["imessage://+14155550134"] and app.tapbacks == [("Jordan Park, hike 8?, 2:08 PM", "Heart")]


def test_reply_goes_to_its_message_and_closes_the_thread():
    app = FakeApp(open_chat="Sunday Soccer ⚽")
    ax = make(app)
    head, trail = code(ax.reply(soccer_job(20, "in! bringing the pump")))
    assert head == "sent-ax" and "menu" in trail and trail.endswith("thread-closed")
    assert app.sent == [("Sam, game 20 on sunday, 1:20 PM", "in! bringing the pump")]
    assert app.thread is None and app.field == "" and app.menu is None


def test_reply_stops_before_typing_when_the_thread_is_another_message():
    app = FakeApp(open_chat="Sunday Soccer ⚽")
    app.reply_opens = "Sam, game 21 on sunday, 1:21 PM"
    head, _ = code(make(app).reply(soccer_job(20, "in!")))
    assert head == "wrong-thread"
    assert app.sent == [] and app.field == "" and app.thread is None


def test_reply_leaves_a_draft_alone():
    app = FakeApp(open_chat="Sunday Soccer ⚽")
    app.field = "half-written thought"
    head, _ = code(make(app).reply(soccer_job(20, "in!")))
    assert head == "field-busy"
    assert app.field == "half-written thought" and app.sent == [] and app.menu is None


def test_a_reply_return_didnt_take_is_taken_back_out():
    app = FakeApp(open_chat="Sunday Soccer ⚽")
    app.return_works = False
    head, _ = code(make(app).reply(soccer_job(20, "in!")))
    assert head == "not-sent"
    assert app.field == "" and app.thread is None and app.sent == []


def test_a_leftover_thread_view_is_closed_first():
    app = FakeApp(open_chat="Sunday Soccer ⚽")
    app.thread = "Sam, game 3 on sunday, 1:03 PM"
    head, trail = code(make(app).tapback(soccer_job(25)))
    assert head == "chosen-action" and "thread-closed" in trail


def test_answers_when_it_cant_run():
    app = FakeApp()
    assert code(MessagesAX(ready=lambda: False).reply(soccer_job(1, "x")))[0] == "ax-unavailable"
    ax = make(app)
    ax.find_pid = lambda: None
    assert code(ax.tapback(soccer_job(1)))[0] == "not-running"
    head, trail = code(make(app).tapback(soccer_job(99)))
    assert head == "bubble-not-found" and trail.endswith("b6/0/0")
    app.app.attrs["AXWindows"] = []
    assert code(make(app).tapback(soccer_job(1)))[0] == "no-window"


# --------------------------------------------------------------------------- in the server


class FakeAX:
    """Stands in for MessagesAX in the server: answers, and writes what Messages would have."""

    def __init__(self, answer: str, effect=None):
        self.answer, self.effect, self.calls, self.tidied = answer, effect, [], 0

    def tapback(self, job):
        self.calls.append(("tapback", job))
        if self.effect:
            self.effect(job)
        return Answer(0, self.answer)

    reply = tapback

    def tidy(self):
        self.tidied += 1


@pytest.fixture
def ax_rw(make_rw):
    fake = FakeMessages()
    rw = make_rw(messages_ui_runner=fake)
    rw.services.messages_ui.verify_seconds = 1.0
    assert rw.client.patch("/api/settings", json={"tapbacksViaMessages": True, "threadRepliesViaMessages": True}).status_code == 200
    rw.services.messages_ui.wait_idle(5)
    rw.services.messages_ui.note_reply_proof(True)
    rw.fake = fake
    return rw


def test_the_server_tapbacks_in_the_background_and_verifies(ax_rw):
    rw = ax_rw
    target = rw.guid("j_hike")
    ui = rw.services.messages_ui
    ui.ax = FakeAX("chosen-action", lambda job: write_tapback(rw.settings.chat_db, target, 2000))
    job = ui.tapback_job(rw.services.convs.get("dm:+14155550134"), target, "love", False)
    ui._run_tapback(job)
    assert rw.fake.calls == []  # no AppleScript
    done = [e for e in rw.services.hub.history if e["type"] == "ui-action"][-1]
    assert done["result"] == "done" and done["detail"] == "Sent the tapback in Messages."


def test_the_server_falls_back_when_the_background_helper_cant_run(ax_rw):
    rw = ax_rw
    ui = rw.services.messages_ui
    target = rw.guid("j_hike")
    ui.ax = FakeAX("ax-unavailable")
    rw.fake.effects["tapback"] = lambda argv: write_tapback(rw.settings.chat_db, target, 2000)
    ui._run_tapback(ui.tapback_job(rw.services.convs.get("dm:+14155550134"), target, "love", False))
    assert [c[0] for c in rw.fake.calls] == ["tapback"]  # the AppleScript helper took over
    # An emoji Messages doesn't offer as a quick one needs its picker: the AppleScript helper too.
    ui.ax = FakeAX("no-tapback-item")
    rw.fake.effects["tapback"] = lambda argv: write_tapback(rw.settings.chat_db, target, 2006, "🦖")
    ui._run_tapback(ui.tapback_job(rw.services.convs.get("dm:+14155550134"), target, "emoji", False, "🦖"))
    assert [c[0] for c in rw.fake.calls] == ["tapback", "tapback"]


def test_the_server_replies_in_the_background(ax_rw):
    rw = ax_rw
    ui = rw.services.messages_ui
    root = rw.guid("soc_root")
    ui.ax = FakeAX("sent-ax", lambda job: write_reply(rw.settings.chat_db, root, job.text))
    job = ui.reply_job(rw.services.convs.get("g:chat100000000000000002"), root, "in! bringing the pump", "r-ax")
    result = ui._run_reply(job)
    assert result.kind == "done" and rw.fake.calls == []
    # Nothing typed when it stops early: never the right-click path after the background one ran.
    ui.ax = FakeAX("wrong-thread")
    job = ui.reply_job(rw.services.convs.get("g:chat100000000000000002"), root, "again", "r-ax2")
    result = ui._run_reply(job)
    assert result.kind == "nothing" and "wrong message" in result.detail and rw.fake.calls == []


def test_tapback_job_titles_reach_the_driver(ax_rw):
    rw = ax_rw
    ui = rw.services.messages_ui
    job = ui.tapback_job(rw.services.convs.get("dm:+14155550134"), rw.guid("j_hike"), "laugh", False)
    assert isinstance(job, TapbackJob) and job.titles()[0] == "Ha ha"


def test_a_tapback_on_the_whole_message_counts(ax_rw):
    """Harry's Mac: a tapback chosen through accessibility on a long message came back as
    "p:a/GUID" (other people's devices write it too, since 2025); it was verified but never shown,
    and removing it answered "already removed"."""
    rw = ax_rw
    target = rw.guid("j_hike")
    fakedb.write_as_messages_app(rw.settings.chat_db, chat_of(rw.settings.chat_db, target), text="Liked “…”",
                                 associated_message_guid=f"p:a/{target}", associated_message_type=2001)
    message = rw.services.builder.by_guid(target)
    assert [(r["kind"], r["from"]) for r in message["reactions"]] == [("like", ["me"])]
    assert rw.services.messages_ui.current_tapback(target, "dm:+14155550134") == ("like", None)


def test_switching_presses_off_whatever_else_is_selected():
    """Harry's Mac: pinned rows never say they're selected, and pressing another chat's row while one
    (or a leftover draft) was selected selected both: "2 Conversations Selected". The list's
    AXSelectedChildren does say, pinned ones too: everything else selected is pressed off."""
    app = FakeApp(open_chat="Jordan Park")
    for row in app.rows:
        row.dyn["AXSelected"] = lambda: False  # pinned: never says
    head, trail = code(make(app).tapback(soccer_job(28)))
    assert head == "chosen-action" and app.selected == {"Sunday Soccer ⚽"}
    assert "off" in trail.split()


def test_a_failed_switch_puts_the_selection_back():
    app = FakeApp(open_chat="Jordan Park")
    app.title_el.dyn["AXDescription"] = lambda: "Something else"  # never shows ours
    head, _ = code(make(app).tapback(soccer_job(28)))
    assert head == "not-selected" and app.selected == {"Jordan Park"}


def test_titles_with_more_after_the_name():
    assert title_matches("Wifey 4 Lifey 🥰, San Francisco, CA", ["Wifey 4 Lifey 🥰", "Carmen"], [], 1)
    assert title_matches("Coming In Hot 🥵 ", ["Coming In Hot 🥵"], [], 1)  # a trailing space
    assert not title_matches("Jordan, Sam & Lee", ["Jordan Park", "Jordan"], [], 1)  # short names: whole title only


def test_mark_read_finds_a_chat_far_down_the_sidebar():
    """Harry: "it couldn't find a convo in the sidebar": the chat was 5 pages down and the helper
    only looked 4. It sweeps down (40 pages at most), uses the row's own action, and scrolls back up
    after the answer."""
    app = FakeApp(others=80)
    ax = make(app)
    job = SimpleNamespace(candidates=["Friend 50"], loose=None, members=[])
    head, trail = code(ax.mark(job, read=True))
    assert head == "ok-action" and app.unread == set()
    assert trail.startswith("row") and app.top > 0
    ax.tidy()
    assert app.top == 0
    assert code(make(app).mark(job, read=True))[0] == "already-read"
    assert code(make(app).mark(job, read=False))[0] == "ok-action" and app.unread == {"Friend 50"}
    gone = SimpleNamespace(candidates=["Nobody"], loose=None, members=[])
    assert code(make(app).mark(gone, read=True))[0] == "row-not-found"


def test_sidebar_labels_with_doubled_spaces():
    """Messages labels "Han Family (A&W, J&R, C&H, A&R)" as "Han Family (A&W,  J&R,  C&H,  A&R), …"."""
    labels = ["Hanyul Lee, Unread, hey", "Han Family (A&W,  J&R,  C&H,  A&R), the live chicken…, 5:50 PM"]
    assert row_index(labels, [], ["Han Family (A&W, J&R, C&H, A&R)"], 1, []) == 1
    assert title_matches("Han Family (A&W,  J&R,  C&H,  A&R)", ["Han Family (A&W, J&R, C&H, A&R)"], [], 1)
