"""The "Needs you" rules (server/priority.py), on hand-made conversations with a fixed clock."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from server.priority import Item, prioritize, urgent_word
from ts_shapes import validate

TZ = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 10, 28, 15, 0, tzinfo=TZ)  # Wednesday 3 PM


def ago(minutes: float) -> float:
    return (NOW - timedelta(minutes=minutes)).timestamp()


def them(guid: str, minutes: float, text: str = "ok", *, unread: bool = True, sender: str = "p:jordan", **kw) -> Item:
    return Item(guid, ago(minutes), False, sender=sender, text=text, unread=unread, **kw)


def mine(guid: str, minutes: float) -> Item:
    return Item(guid, ago(minutes), True)


def run(kind: str, items, asking: str | None = None, vips=None):
    return prioritize(kind, items, asking, vips or {}, NOW, TZ)


def kinds(reasons) -> list[str]:
    return [r["kind"] for r in reasons]


def test_nothing_needs_you():
    assert run("dm", []).priority is None
    assert run("dm", [them("a", 30, "lol", unread=False), mine("b", 20)]).priority is None
    assert run("group", [them("a", 30, "lol")]).priority is None  # unread, but nothing important
    result = run("dm", [them("a", 30, "running late, be there in 15 min")])  # that time passed 15 min ago…
    assert result.priority is not None  # …but within the grace hour
    late = run("dm", [them("a", 90, "running late, be there in 15 min")])
    assert late.priority is None and late.importance == {}  # passed more than an hour ago


def test_asked_goes_on_the_first_asking_message_in_dms_only():
    items = [them("a", 40, "are you free later?", unread=False), them("b", 30, "also, dinner?", unread=False)]
    result = run("dm", items, asking="a")
    assert result.importance == {"a": [{"kind": "asked"}]}
    assert result.priority["reasons"] == [{"kind": "asked"}] and result.priority["level"] == "high"
    assert run("group", items, asking="a").priority is None  # groups say it with mention/reply


def test_mention_and_reply_need_unread_or_the_unanswered_run():
    read_run = [mine("m", 60), them("a", 30, "@harry thoughts?", unread=False, mentions_me=True),
                them("b", 20, "+1", unread=False, reply_to_me=True)]
    result = run("group", read_run)
    assert result.importance == {"a": [{"kind": "mention"}], "b": [{"kind": "reply"}]}
    answered = [them("a", 30, "@harry thoughts?", unread=False, mentions_me=True), mine("m", 10)]
    assert run("group", answered).priority is None  # read, and Harry answered after it
    unread_before_answer = [them("a", 30, "@harry thoughts?", mentions_me=True), mine("m", 10)]
    assert kinds(run("group", unread_before_answer).importance["a"]) == ["mention"]
    assert run("dm", [them("a", 30, "you there?", mentions_me=True)]).priority is None  # mention is a group thing
    assert kinds(run("dm", [them("a", 30, "sure", reply_to_me=True)]).importance["a"]) == ["reply"]


def test_time_needs_an_unread_message_or_the_asking_one():
    assert run("dm", [them("a", 30, "dinner at 7?", unread=False)]).priority is None
    result = run("dm", [them("a", 30, "dinner at 7?", unread=False)], asking="a")
    assert kinds(result.importance["a"]) == ["time", "asked"]
    reason = result.importance["a"][0]
    assert reason == {"kind": "time", "label": "Today 7 PM", "at": "2026-10-28T19:00:00-07:00"}
    assert result.priority["level"] == "high"  # 4 hours away
    soon = run("group", [them("a", 30, "meet at 5")])
    assert soon.priority["level"] == "urgent"  # within 3 hours
    vague = run("group", [them("a", 30, "tonight?")])
    assert vague.priority["level"] == "high" and vague.importance["a"][0] == {"kind": "time", "label": "Tonight",
                                                                                "at": None}


def test_urgent_words():
    for text, word in [("URGENT pls", "urgent"), ("asap!", "asap"), ("a.s.a.p.", "a.s.a.p"), ("call me", "call me"),
                       ("Call ASAP", "call asap"), ("pick up the phone", "pick up the phone"),
                       ("PICK UP YOUR PHONE", "pick up your phone"), ("pick up!", "pick up!"),
                       ("answer your phone", "answer your phone"),
                       ("need you now", "need you now"), ("help!", "help!"), ("at the hospital", "hospital"),
                       ("911", "911"), ("there was an accident", "accident"), ("emergency", "emergency")]:
        assert urgent_word(text) == word, text
    for text in ("helpful", "the urgency", "callme", "911x", "4155550911", "help me with this", "pickup truck",
                 "can you pick up milk?", "I'll pick up the kids", "can you pick up?"):
        assert urgent_word(text) is None, text
    result = run("dm", [them("a", 5, "call me asap")])
    assert result.importance["a"] == [{"kind": "urgent", "word": "call me"}]
    assert result.priority["level"] == "urgent"
    assert run("dm", [them("a", 5, "call me asap", unread=False)]).priority is None  # read: not urgent any more


def test_repeat_three_in_a_row_within_30_minutes():
    burst = [them("a", 12, "hey"), them("b", 10, "hello?"), them("c", 8, "hellooo"), them("d", 7, "??")]
    result = run("dm", burst)
    assert "a" not in result.importance and "b" not in result.importance
    assert result.importance["c"] == [{"kind": "repeat", "count": 3}]
    assert result.importance["d"] == [{"kind": "repeat", "count": 4}]
    assert result.priority["reasons"] == [{"kind": "repeat", "count": 4}]  # the longest streak
    assert result.priority["level"] == "urgent"
    assert result.priority["score"] == 300 + 40 * 2 - 0  # 7 minutes ago rounds away
    gap = [them("a", 100, "hey"), them("b", 60, "hello?"), them("c", 20, "hellooo")]  # 40-minute gaps
    assert run("dm", gap).priority is None
    broken = [them("a", 12, "hey"), them("b", 10, "hey", sender="p:sam"), them("c", 8, "hey")]
    assert run("group", broken).priority is None  # someone else in between
    with_read = [them("a", 12, "hey", unread=False), them("b", 10, "hello?"), them("c", 8, "hellooo")]
    assert run("dm", with_read).priority is None
    after_reply = [them("a", 12, "hey"), mine("m", 11), them("b", 10, "hello?"), them("c", 8, "hellooo")]
    assert run("dm", after_reply).priority is None


def test_vip_only_alongside_another_reason():
    vips = {"p:mom": "Mom"}
    assert run("dm", [them("a", 5, "hi", sender="p:mom")], vips=vips).priority is None
    result = run("dm", [them("a", 5, "call me", sender="p:mom")], vips=vips)
    assert result.importance["a"] == [{"kind": "urgent", "word": "call me"}, {"kind": "vip", "name": "Mom"}]


def test_level_score_message_and_until():
    items = [
        them("a", 300, "can you call me in 20 min?", unread=False, sender="p:dad"),  # the asking one (read)
        them("b", 5, "Can you call me in 20 min? Need to go over the forms", sender="p:dad"),
    ]
    result = run("dm", items, asking="a", vips={"p:dad": "Dad"})
    priority = result.priority
    assert kinds(priority["reasons"]) == ["urgent", "time", "asked", "vip"]
    assert priority["level"] == "urgent"
    # 300 + urgent 80 + time within 3 h 80 + asked 60 + vip 25 − 5 min
    assert priority["score"] == round(300 + 80 + 80 + 60 + 25 - 5 / 60)
    assert priority["messageId"] == "b"  # the most weight
    assert priority["reasons"][1] == {"kind": "time", "label": "Today 3:15 PM", "at": "2026-10-28T15:15:00-07:00"}
    assert priority["until"] == "2026-10-28T16:15:00-07:00"  # an hour after 3:15 PM
    # The read asking message still says "call me" (it's unanswered); its own time (10:20 AM) passed.
    assert kinds(result.importance["a"]) == ["urgent", "asked", "vip"]
    assert not validate(priority, "Priority")
    for reasons in result.importance.values():
        for reason in reasons:
            assert not validate(reason, "PriorityReason"), reason


def test_reading_a_dm_doesnt_resolve_what_it_asks():
    """DMs: time and urgent words also count on read messages of the unanswered run that ask something."""
    asking = them("a", 5, "Can you call me in 20 min?", unread=False, asks=True)
    result = run("dm", [asking], asking="a")
    assert kinds(result.importance["a"]) == ["urgent", "time", "asked"] and result.priority["level"] == "urgent"
    remark = them("b", 4, "call me in 20 min, lol", unread=False)  # doesn't ask: read means seen
    assert run("dm", [asking, remark], asking="a").importance.get("b") is None
    answered = [asking, mine("m", 1)]
    assert run("dm", answered, asking=None).priority is None  # Harry answered
    expired = them("a", 120, "Can you call me in 20 min?", unread=False, asks=True)
    assert kinds(run("dm", [expired], asking="a").importance["a"]) == ["urgent", "asked"]  # the time passed
    group = [them("a", 5, "can you call me in 20 min?", unread=False, asks=True)]
    assert run("group", group).priority is None  # groups: unread only (a mention or reply would keep it)
    closed = [them("a", 5, "can you call me in 20 min?", unread=False, asks=True)]
    assert run("dm", closed, asking=None).priority is None  # not waiting on Harry (e.g. "nvm, thanks" after it)


def test_time_union_takes_the_soonest_and_until_the_earliest():
    items = [them("a", 30, "tomorrow at 9"), them("b", 20, "or tonight"), them("c", 10, "at 6?")]
    result = run("group", items)
    times = [r for r in result.priority["reasons"] if r["kind"] == "time"]
    assert times == [{"kind": "time", "label": "Today 6 PM", "at": "2026-10-28T18:00:00-07:00"}]
    assert result.priority["until"] == "2026-10-28T19:00:00-07:00"  # 6 PM + 1 h, the earliest to stop


def test_ties_pick_the_newest_message_and_age_lowers_the_score():
    items = [them("a", 600, "@harry ?", mentions_me=True), them("b", 120, "@harry ??", mentions_me=True)]
    result = run("group", items)
    assert result.priority["messageId"] == "b"
    assert result.priority["score"] == round(100 + 50 - 2)  # two hours since the newest counting message
    old = run("group", [them("a", 60 * 70, "@harry ?", mentions_me=True)])
    assert old.priority["score"] == round(100 + 50 - 70)


def test_only_the_last_three_days_count():
    old = [them("a", 60 * 24 * 3 + 1, "call me")]
    assert run("dm", old).priority is None
    assert run("dm", [them("a", 60 * 24 * 3 - 1, "call me")]).priority is not None
