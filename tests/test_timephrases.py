"""The time-phrase resolver behind "Needs you" (server/timephrases.py): table-driven, fixed clock and zone."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from server.timephrases import clock_label, day_word, find_time, find_times, local_tz

TZ = ZoneInfo("America/Los_Angeles")
WED_3PM = datetime(2026, 10, 28, 15, 0, tzinfo=TZ)  # a Wednesday afternoon
WED_9AM = datetime(2026, 10, 28, 9, 0, tzinfo=TZ)
WED_8PM = datetime(2026, 10, 28, 20, 0, tzinfo=TZ)
WED_11PM = datetime(2026, 10, 28, 23, 0, tzinfo=TZ)


def at(day: int, hour: int, minute: int = 0) -> datetime:
    """A moment in the test week: day 28 = Wednesday."""
    return datetime(2026, 10, day, hour, minute, tzinfo=TZ)


def hit(text: str, sent: datetime = WED_3PM, now: datetime | None = None):
    return find_time(text, sent, now or sent + timedelta(minutes=1), TZ)


# (text, sent, expected label, expected at or None)
CASES = [
    # in N minutes / hours: that moment, labelled with its clock time
    ("can you call me in 20 min?", WED_3PM, "Today 3:20 PM", at(28, 15, 20)),
    ("be there in 2 hrs", WED_3PM, "Today 5 PM", at(28, 17)),
    ("in 45 minutes", WED_3PM, "Today 3:45 PM", at(28, 15, 45)),
    ("back in 1 hour", WED_3PM, "Today 4 PM", at(28, 16)),
    ("in an hour?", WED_3PM, "Today 4 PM", at(28, 16)),
    ("in half an hour", WED_3PM, "Today 3:30 PM", at(28, 15, 30)),
    ("leaving in 10m", WED_3PM, "Today 3:10 PM", at(28, 15, 10)),
    ("in 2h", WED_11PM, "Tomorrow 1 AM", at(29, 1)),
    # clock times with am/pm
    ("7pm works", WED_3PM, "Today 7 PM", at(28, 19)),
    ("7:30 pm?", WED_3PM, "Today 7:30 PM", at(28, 19, 30)),
    ("7.30pm then", WED_3PM, "Today 7:30 PM", at(28, 19, 30)),
    ("9 a.m. sharp", WED_3PM, "Tomorrow 9 AM", at(29, 9)),
    ("10am", WED_9AM, "Today 10 AM", at(28, 10)),
    ("12pm", WED_9AM, "Today 12 PM", at(28, 12)),
    ("7pm-9pm", WED_3PM, "Today 7 PM", at(28, 19)),
    # at / by without am/pm: the next 7:00 or 19:00 after the message, never 1–6 am
    ("meet at 7", WED_3PM, "Today 7 PM", at(28, 19)),
    ("meet at 7", WED_9AM, "Today 7 PM", at(28, 19)),
    ("meet at 7", WED_8PM, "Tomorrow 7 AM", at(29, 7)),
    ("at 7:30", WED_3PM, "Today 7:30 PM", at(28, 19, 30)),
    ("by 5", WED_3PM, "Today 5 PM", at(28, 17)),
    ("by 5", WED_8PM, "Tomorrow 5 PM", at(29, 17)),  # 5 am is skipped
    ("at 6", WED_8PM, "Tomorrow 6 PM", at(29, 18)),
    ("at 11", WED_9AM, "Today 11 AM", at(28, 11)),
    ("at 12", WED_9AM, "Today 12 PM", at(28, 12)),
    ("at 12", WED_3PM, "Tomorrow 12 PM", at(29, 12)),  # 12 is noon…
    ("12am", WED_3PM, "Tomorrow 12 AM", at(29, 0)),  # …unless it says am
    ("by 12:30", WED_3PM, "Tomorrow 12:30 PM", at(29, 12, 30)),
    # a bare H:MM is a clock time too
    ("7:30?", WED_3PM, "Today 7:30 PM", at(28, 19, 30)),
    ("the 7:30 show", WED_3PM, "Today 7:30 PM", at(28, 19, 30)),
    ("9:30", WED_3PM, "Today 9:30 PM", at(28, 21, 30)),
    ("9:30", WED_8PM, "Today 9:30 PM", at(28, 21, 30)),
    ("10:30-11:30 works", WED_9AM, "Today 10:30 AM", at(28, 10, 30)),
    ("21:15", WED_3PM, "Today 9:15 PM", at(28, 21, 15)),
    ("dinner @ 7", WED_3PM, "Today 7 PM", at(28, 19)),
    ("at 19:00", WED_9AM, "Today 7 PM", at(28, 19)),
    ("lunch at noon", WED_9AM, "Today 12 PM", at(28, 12)),
    ("lunch at noon", WED_3PM, "Tomorrow 12 PM", at(29, 12)),
    ("home by midnight", WED_3PM, "Tomorrow 12 AM", at(29, 0)),
    # a day part picks the half of the day
    ("tonight at 8", WED_9AM, "Today 8 PM", at(28, 20)),
    ("this morning at 11", WED_9AM, "Today 11 AM", at(28, 11)),
    # a clock time attaches to a named day
    ("tomorrow at 9", WED_3PM, "Tomorrow 9 PM", at(29, 21)),  # on a named day, 7–11 means pm
    ("tomorrow at 8", WED_3PM, "Tomorrow 8 PM", at(29, 20)),
    ("tomorrow morning at 9", WED_3PM, "Tomorrow 9 AM", at(29, 9)),
    ("thursday at 11", WED_3PM, "Tomorrow 11 PM", at(29, 23)),
    ("tomorrow at 2", WED_3PM, "Tomorrow 2 PM", at(29, 14)),
    ("tomorrow at 12", WED_3PM, "Tomorrow 12 PM", at(29, 12)),
    ("tomorrow at 9am", WED_3PM, "Tomorrow 9 AM", at(29, 9)),
    ("tmrw 10am?", WED_3PM, "Tomorrow 10 AM", at(29, 10)),
    ("tomorrow night at 8", WED_3PM, "Tomorrow 8 PM", at(29, 20)),
    ("thursday 7pm", WED_3PM, "Tomorrow 7 PM", at(29, 19)),
    ("Friday at 7pm", at(29, 12), "Tomorrow 7 PM", at(30, 19)),  # labels are relative to now
    ("fri 8am", at(28, 22), "Fri 8 AM", at(30, 8)),  # two days out, 34 h away
    ("today at 5", WED_3PM, "Today 5 PM", at(28, 17)),
    # vague day parts: no moment, counted until the part of the day is over
    ("this morning", WED_9AM, "This morning", None),
    ("this afternoon", WED_9AM, "This afternoon", None),
    ("this evening", WED_3PM, "Tonight", None),
    ("need a head count by tonight", WED_3PM, "Tonight", None),
    ("tonite?", WED_3PM, "Tonight", None),
    # whole days
    ("can you confirm today to hold the slot?", WED_3PM, "Today", None),
    ("by eod", WED_3PM, "Today", None),
    ("by end of the day", WED_3PM, "Today", None),
    ("tomorrow", WED_3PM, "Tomorrow", None),
    ("tomorrow morning", WED_3PM, "Tomorrow", None),
    ("thursday?", WED_3PM, "Tomorrow", None),
    ("this thurs", WED_3PM, "Tomorrow", None),
    ("on thu", WED_3PM, "Tomorrow", None),
    ("wednesday", WED_3PM, "Today", None),  # the named day is today
    ("friday", at(29, 12), "Tomorrow", None),  # Fri 23:59 is within 36 h of Thu noon
    # several times: the soonest still ahead
    ("see you at 5 or 7pm", WED_3PM, "Today 5 PM", at(28, 17)),
    ("in 20 min, and dinner at 7", WED_3PM, "Today 3:20 PM", at(28, 15, 20)),
]


@pytest.mark.parametrize("text, sent, label, moment", CASES, ids=[c[0] for c in CASES])
def test_times(text, sent, label, moment):
    found = hit(text, sent)
    assert found is not None, text
    assert (found.label, found.at) == (label, moment)


NOT_TIMES = [
    "58° out today is crazy",  # a "today" window, but no clock time…
    "it was $40", "got 2 tickets", "top 5 albums", "iOS 18 is out", "room 7", "the 9-11 game", "at 9-11",
    "see https://example.com/at/7pm", "www.site.io/7pm", "call 415-555-0134", "text +1 (415) 555-0134 at 7pm? no",
    "at 7 people max", "by 5%", "at 3 points", "split by 4 of us", "7",
    "won 3:1", "a 3:1 ratio", "2:10 pace", "ran a 2:10/km", "score was 2:10", "finished in 1:23:45", "4:5",
    "I sat down", "the sun is out", "wed 3 times", "send me the doc",
]


@pytest.mark.parametrize("text", NOT_TIMES)
def test_not_times(text):
    found = hit(text, WED_3PM)
    if text.startswith("58°"):
        assert found is not None and found.label == "Today" and found.at is None  # only the word "today"
    elif text.startswith("text +1"):
        assert found is not None and found.at == at(28, 19)  # the phone number is ignored, "at 7pm" isn't
    else:
        assert found is None, (text, found)


def test_explicit_dates():
    sat = at(31, 9)  # Saturday Oct 31
    assert hit("Tue Nov 3 at 10:00 AM", sat) is None  # 3 days out, not "the next 10 am"
    found = hit("Tue Nov 3 at 10:00 AM", at(2 + 0, 12).replace(month=11))  # sent Mon Nov 2 at noon
    assert found.label == "Tomorrow 10 AM" and found.at == datetime(2026, 11, 3, 10, tzinfo=TZ)
    assert hit("dinner on 11/1 at 7pm", sat).at == datetime(2026, 11, 1, 19, tzinfo=TZ)
    assert hit("november 1st", sat) is None  # Nov 1 23:59 is 39 h away
    assert hit("november 1st", at(31, 13)).label == "Tomorrow"
    assert hit("may 5 people come?", at(31, 13)) is None  # "may" is a verb here
    assert hit("on may 5", datetime(2026, 5, 4, 13, tzinfo=TZ)).label == "Tomorrow"
    new_years_eve = datetime(2026, 12, 31, 23, tzinfo=TZ)  # "Jan 2" is next year's
    assert find_time("back on Jan 2", new_years_eve, datetime(2027, 1, 1, 13, tzinfo=TZ), TZ).label == "Tomorrow"
    assert hit("the 3/4 cup", sat) is None and hit("score was 2/3", sat) is None


def test_past_times_and_the_grace_hour():
    sent = WED_3PM
    assert hit("at 4pm", sent, at(28, 16, 30)).at == at(28, 16)  # passed 30 min ago: still counts
    assert hit("at 4pm", sent, at(28, 17)).at == at(28, 16)  # exactly an hour after
    assert hit("at 4pm", sent, at(28, 17, 1)) is None  # more than an hour after
    assert hit("this afternoon", WED_9AM, at(28, 16, 59)) is not None
    assert hit("this afternoon", WED_9AM, at(28, 17, 1)) is None  # windows end exactly, no grace
    assert hit("tonight", sent, at(28, 23, 58)) is not None and hit("tonight", sent, at(29, 0, 0)) is None
    assert hit("this morning", WED_3PM) is None  # already over when it was sent
    assert hit("in 15 min", at(28, 12), at(28, 13, 16)) is None  # 12:15 + 1 h passed


def test_the_36_hour_limit():
    sent = at(26, 9)  # Monday morning: "Friday" is 4 days out
    assert hit("see you friday", sent, at(26, 9, 5)) is None
    assert hit("see you friday", sent, at(29, 11, 58)) is None  # Fri 23:59 is 36 h 1 min away
    found = hit("see you friday", sent, at(29, 12))
    assert found is not None and found.label == "Tomorrow"
    assert hit("friday at 7pm", sent, at(29, 6, 59)) is None  # 7 pm Fri is 36 h 1 min away
    assert hit("friday at 7pm", sent, at(29, 7)).label == "Tomorrow 7 PM"
    assert hit("in 37 hours", WED_3PM) is None


def test_labels_follow_now():
    sent = at(28, 22)  # Wed 10 pm: "tomorrow at 9am"
    assert hit("tomorrow at 9am", sent, at(28, 22, 5)).label == "Tomorrow 9 AM"
    assert hit("tomorrow at 9am", sent, at(29, 8)).label == "Today 9 AM"  # it's Thursday now
    assert hit("tomorrow", sent, at(29, 8)).label == "Today"
    assert hit("at 11:30pm", at(28, 23), at(29, 0, 10)).label == "Yesterday 11:30 PM"  # in its grace hour


def test_stops_and_when():
    found = hit("at 7pm", WED_3PM)
    assert (found.when, found.stops) == (at(28, 19), at(28, 20))
    window = hit("tonight", WED_3PM)
    assert window.at is None and window.when == window.stops == at(28, 23, 59)


def test_weekdays_up_to_six_days_ahead():
    times = find_times("see you tuesday", WED_3PM, TZ)  # next week's Tuesday: 6 days out
    assert [f.day for f in times] == [at(3 + 0, 0).date().replace(month=11)]
    assert find_times("see you wednesday", WED_3PM, TZ)[0].day == WED_3PM.date()  # today, not next week


def test_dst_and_zone_handling():
    sent = datetime(2026, 10, 31, 22, 0, tzinfo=TZ)  # Sat night; DST ends Sun Nov 1 at 2 am
    found = find_time("tomorrow at 9am", sent, sent, TZ)
    assert found.at == datetime(2026, 11, 1, 9, 0, tzinfo=TZ) and found.at.utcoffset() == timedelta(hours=-8)
    utc_sent = sent.astimezone(ZoneInfo("UTC"))  # the zone decides the day, not the input's offset
    assert find_time("tomorrow at 9am", utc_sent, utc_sent, TZ).at == found.at
    tokyo = ZoneInfo("Asia/Tokyo")  # 22:00 Saturday in California is 2 pm Sunday in Tokyo
    assert find_time("this afternoon", sent, sent, tokyo).label == "This afternoon"
    assert find_time("tonight", sent, sent, tokyo).stops == datetime(2026, 11, 1, 23, 59, tzinfo=tokyo)
    assert find_time("this afternoon", sent, sent, TZ) is None  # …and the evening at home


def test_helpers():
    assert clock_label(at(28, 19)) == "7 PM" and clock_label(at(28, 0, 5)) == "12:05 AM"
    assert clock_label(at(28, 12)) == "12 PM"
    today = WED_3PM.date()
    assert [day_word(today + timedelta(days=d), today) for d in (-1, 0, 1, 2)] == ["Yesterday", "Today", "Tomorrow",
                                                                                     "Fri"]
    assert local_tz() is not None
