"""Times named in a text ("in 20 min", "7pm", "tomorrow at 9", "tonight"…), for "Needs you".

Pure functions: `find_time(text, sent, now, tz)` returns the soonest time the
text names that still counts (docs/api.md, "Needs you (priority)"), or None.
`sent` and `now` are aware datetimes; everything is worked out in `tz` (the
Mac's time zone in production, `local_tz()`).

What counts as a time:
- "in 20 min", "in 2 hrs", "in an hour", "in half an hour" (also "20m", "2h"
  units) → that moment;
- a clock time: "7pm", "7:30 pm", "7.30pm", "at 7", "at 7:30", "by 5", "@ 7",
  "at 19:00", noon, midnight. Bare numbers count only with am/pm or after
  at/by (or "@"). Without am/pm: the next 7:00 or 19:00 after the message, skipping
  1–6 am (a day part in the same text — "tonight at 8", "this morning at 9" —
  picks the half of the day);
- this morning / this afternoon / this evening / tonight → until 12:00, 17:00,
  23:59, 23:59 (no clock time: they're vague);
- today, eod / end of (the) day → until 23:59;
- tomorrow / tmrw / tmr (+ morning/afternoon/night) → until 23:59 tomorrow;
- weekday names ("friday", "this fri", "on fri") up to 6 days ahead → until
  23:59 that day; an explicit date ("Nov 3", "on 11/3") likewise.
A clock time attaches to a day named in the same text ("tomorrow at 9",
"Friday 7pm", "Tue Nov 3 at 10am"). URLs, emails and phone numbers are ignored, and so are numbers
followed by a unit ("at 3 people", "by 5%").

A time counts while it (or its window's end) is still ahead and within 36 h,
and for up to 1 h after a named moment passes; of several, the soonest wins.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta, tzinfo

GRACE = timedelta(hours=1)
HORIZON = timedelta(hours=36)
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
SHORT_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_ABBREV = {"mon": 0, "tue": 1, "tues": 1, "wed": 2, "weds": 2, "thu": 3, "thur": 3, "thurs": 3, "fri": 4,
           "sat": 5, "sun": 6}

_URL = re.compile(r"\b(?:https?://|www\.)\S+|\b[\w.-]+\.(?:com|org|net|io|co|us|app|dev|gov|edu|me|ly)(?:/\S*)?\b")
_EMAIL = re.compile(r"\S+@\S+\.\w+")
_PHONE = re.compile(r"(?<![\w:])\+?\(?\d[\d\s().\-]{6,}\d(?![\w:])")
_IN = re.compile(r"\bin\s+(\d{1,3}|an?|half\s+an?)\s*(m|mins?|minutes?|h|hrs?|hours?)\b")
_CLOCK = re.compile(r"(?<![\w$#.:/°+-])(\d{1,2})(?:([:.])([0-5]\d))?(?![\d])")
_AMPM = re.compile(r"\s*(am|pm)\b")
_AT_BY = re.compile(r"(?:\b(?:at|by)|@)\s*$")
_UNITS = re.compile(
    r"\s*(?:%|°|x\b|k\b|th\b|st\b|nd\b|rd\b|-|/|"
    r"(?:people|ppl|persons|guys|kids|points|pts|percent|pct|degrees|deg|mph|miles|mi|km|kms|kg|lbs?|pounds|"
    r"dollars|bucks|usd|times|items|things|minutes|mins?|hours|hrs?|days|weeks|months|years|yrs|secs?|seconds|"
    r"inches|feet|ft|meters|gb|mb|of|out|more|left|games?|rounds?|laps|reps|sets)\b)"
)
_TOMORROW = re.compile(r"\b(?:tomorrow|tmrw|tmr|tomorow|tomm?orrow)\b")
_WEEKDAY = re.compile(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?\b")
_WEEKDAY_ABBREV = re.compile(
    r"\b(?:this|on|next|by|until|till|til)\s+(mon|tues?|weds?|thu|thurs?|fri|sat|sun)\b"
    r"|\b(mon|tues?|weds?|thu|thurs?|fri|sat|sun)\b(?=\s*(?:@|at\b|\d{1,2}(?:[:.][0-5]\d)?\s*(?:am|pm)\b))"
)
_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10,
           "nov": 11, "dec": 12}
# "Nov 3", "november 3rd", "on may 5" (bare "may 5" is too often the verb), "on 11/3".
_DATE = re.compile(r"\b(?:(on\s+)?(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(st|nd|rd|th)?"
                   r"(?![\d:])|on\s+(\d{1,2})/(\d{1,2})(?![\d/]))")
_PART = re.compile(r"\bthis\s+(morning|afternoon|evening|eve)\b|\b(tonight|tonite)\b")
_TODAY = re.compile(r"\btoday\b|\beod\b|\bend\s+of\s+(?:the\s+)?day\b")
_TOMORROW_PART = re.compile(r"\b(?:tomorrow|tmrw|tmr|tomorow)\s+(morning|afternoon|evening|night)\b")
_NOON = re.compile(r"\b(noon|midday|midnight)\b")


@dataclass(frozen=True)
class TimeHit:
    """A time a text names: `at` the moment (None for vague windows), `stops` when it stops counting."""

    label: str
    at: datetime | None
    stops: datetime
    when: datetime  # `at`, or the window's end: what "soonest" compares


@dataclass(frozen=True)
class _Found:
    at: datetime | None
    end: datetime  # window end (= at for moments)
    day: date | None  # the day for day-only windows; None for day parts
    part: str | None = None  # "morning" | "afternoon" | "tonight" for day parts


def local_tz() -> tzinfo:
    """The Mac's time zone (IANA, so DST is right): $TZ, else /etc/localtime, else the current offset."""
    from zoneinfo import ZoneInfo

    name = os.environ.get("TZ", "").lstrip(":")
    if not name:
        try:
            real = os.path.realpath("/etc/localtime")
            if "zoneinfo/" in real:
                name = real.split("zoneinfo/", 1)[1]
        except OSError:
            name = ""
    if name:
        try:
            return ZoneInfo(name)
        except (KeyError, ValueError, OSError):
            pass
    return datetime.now().astimezone().tzinfo  # type: ignore[return-value]


def _clean(text: str) -> str:
    t = (text or "").lower().replace("’", "'").replace(" ", " ").replace(" ", " ")
    t = re.sub(r"\b([ap])\.\s?m\.?", r"\1m", t)  # a.m. / p.m.
    for pattern in (_URL, _EMAIL, _PHONE):
        t = pattern.sub(" ", t)
    return t


def _at(day: date, hour: int, minute: int, tz: tzinfo) -> datetime:
    return datetime.combine(day, dtime(hour, minute), tz)


def _end_of(day: date, tz: tzinfo) -> datetime:
    return datetime.combine(day, dtime(23, 59), tz)


def _calendar_date(t: str, sent_day: date) -> date | None:
    """An explicit date ("Nov 3", "on 11/3"): this year, or next year if that's more than a month ago."""
    for match in _DATE.finditer(t):
        if match.group(2):
            if match.group(2) == "may" and not (match.group(1) or match.group(4)):
                continue
            month, day = _MONTHS[match.group(2)], int(match.group(3))
        else:
            month, day = int(match.group(5)), int(match.group(6))
        try:
            found = date(sent_day.year, month, day)
        except ValueError:
            continue
        if (sent_day - found).days > 31:
            found = found.replace(year=found.year + 1)
        return found
    return None


def _named_day(t: str, sent_day: date) -> date | None:
    """A day the text names that clock times attach to: a date, tomorrow, else a weekday (0–6 days ahead)."""
    explicit = _calendar_date(t, sent_day)
    if explicit is not None:
        return explicit
    if _TOMORROW.search(t):
        return sent_day + timedelta(days=1)
    full = _WEEKDAY.search(t)
    abbrev = _WEEKDAY_ABBREV.search(t)
    candidates = []
    if full:
        candidates.append((full.start(), WEEKDAYS.index(full.group(1))))
    if abbrev:
        word = abbrev.group(1) or abbrev.group(2)
        candidates.append((abbrev.start(), _ABBREV[word]))
    if not candidates:
        return None
    weekday = min(candidates)[1]
    return sent_day + timedelta(days=(weekday - sent_day.weekday()) % 7)


def _half_hint(t: str) -> str | None:
    """Which half of the day an ambiguous "at 8" means, from a day part in the same text."""
    if re.search(r"\b(?:this\s+)?morning\b", t):
        return "am"
    if re.search(r"\b(?:this\s+)?(?:afternoon|evening|eve)\b|\b(?:tonight|tonite|night)\b", t):
        return "pm"
    return None


def _clock_times(t: str, sent: datetime, base: date | None, tz: tzinfo) -> list[datetime]:
    """Clock times in the text, as moments (on the named day, else the next one after the message)."""
    out: list[datetime] = []
    hint = _half_hint(t)
    for match in _CLOCK.finditer(t):
        hour = int(match.group(1))
        sep, minute = match.group(2), int(match.group(3) or 0)
        rest = t[match.end():]
        ampm = _AMPM.match(rest)
        anchored = bool(_AT_BY.search(t[:match.start()]))
        if ampm:
            after = rest[ampm.end():]
            if after[:1].isalnum():
                continue
            if not 1 <= hour <= 12:
                continue
            hours = [hour % 12 + (12 if ampm.group(1) == "pm" else 0)]
        else:
            if not anchored:
                continue  # bare numbers only with am/pm or after at/by
            if sep == "." and not match.group(3):
                continue
            if _UNITS.match(rest) or (rest[:1] == "." and rest[1:2].isdigit()):
                continue
            if 13 <= hour <= 23 and sep:  # 24-hour clock: "at 19:00"
                hours = [hour]
            elif 1 <= hour <= 12:
                hours = [h for h in (hour % 12, hour % 12 + 12) if h == 0 or h > 6]
                if hint:
                    halved = [h for h in hours if (h < 12) == (hint == "am")]
                    hours = halved or hours
            else:
                continue
        out.extend(_resolve(hours, minute, sent, base, tz))
    for match in _NOON.finditer(t):
        word = match.group(1)
        if word == "midnight":
            day = (base or sent.date()) + timedelta(days=1)
            out.append(_at(day, 0, 0, tz))
        else:
            out.extend(_resolve([12], 0, sent, base, tz))
    return out


def _resolve(hours: list[int], minute: int, sent: datetime, base: date | None, tz: tzinfo) -> list[datetime]:
    if not hours:
        return []
    options = []
    for hour in hours:
        if base is not None:
            moment = _at(base, hour, minute, tz)
            if base <= sent.date() and moment <= sent and len(hours) > 1:
                continue  # "today at 7" sent at 3 pm means 7 pm
            options.append(moment)
        else:
            moment = _at(sent.date(), hour, minute, tz)
            if moment <= sent:
                moment = _at(sent.date() + timedelta(days=1), hour, minute, tz)
            options.append(moment)
    return [min(options)] if options else []


def find_times(text: str, sent: datetime, tz: tzinfo | None = None) -> list[_Found]:
    """Every time the text names, judged from when it was sent (not yet from "now")."""
    tz = tz or local_tz()
    sent = sent.astimezone(tz)
    t = _clean(text)
    if not re.search(r"\d|noon|midday|midnight|morning|afternoon|evening|eve\b|tonight|tonite|today|eod|end of|"
                     r"tomorrow|tmrw|tmr|tomorow|day\b|mon|tue|wed|thu|fri|sat|sun|hour|min|half", t):
        return []  # cheap check: most texts name no time at all
    found: list[_Found] = []
    for match in _IN.finditer(t):
        amount, unit = match.group(1), match.group(2)
        if amount.startswith("half"):
            minutes = 30.0 if unit.startswith("h") else 0.0
        elif amount in ("a", "an"):
            minutes = 60.0 if unit.startswith("h") else 1.0
        else:
            minutes = float(amount) * (60 if unit.startswith("h") else 1)
        if minutes <= 0:
            continue
        moment = sent + timedelta(minutes=minutes)
        found.append(_Found(moment, moment, None))
    sent_day = sent.date()
    base = _named_day(t, sent_day)
    clock_base = base
    if clock_base is None and (_PART.search(t) or _TODAY.search(t)):
        clock_base = sent_day  # "tonight at 8", "today at 5"
    clocks = _clock_times(t, sent, clock_base, tz)
    found.extend(_Found(m, m, None) for m in clocks)
    clock_days = {m.date() for m in clocks}

    part = _PART.search(t)
    if part:
        which = part.group(1) or "tonight"
        if which == "morning":
            end, label = _at(sent_day, 12, 0, tz), "morning"
        elif which == "afternoon":
            end, label = _at(sent_day, 17, 0, tz), "afternoon"
        else:
            end, label = _end_of(sent_day, tz), "tonight"
        if sent_day not in clock_days:
            found.append(_Found(None, end, None, label))
    if _TODAY.search(t) and sent_day not in clock_days and not part:
        found.append(_Found(None, _end_of(sent_day, tz), sent_day))
    if base is not None and base not in clock_days:
        found.append(_Found(None, _end_of(base, tz), base))
    return found


def day_word(day: date, today: date) -> str:
    diff = (day - today).days
    if diff == 0:
        return "Today"
    if diff == 1:
        return "Tomorrow"
    if diff == -1:
        return "Yesterday"
    return SHORT_DAYS[day.weekday()]


def clock_label(moment: datetime) -> str:
    hour = moment.hour % 12 or 12
    suffix = "AM" if moment.hour < 12 else "PM"
    return f"{hour}:{moment.minute:02d} {suffix}" if moment.minute else f"{hour} {suffix}"


def _label(found: _Found, now: datetime) -> str:
    if found.at is not None:
        return f"{day_word(found.at.date(), now.date())} {clock_label(found.at)}"
    if found.part is not None:
        return {"morning": "This morning", "afternoon": "This afternoon", "tonight": "Tonight"}[found.part]
    assert found.day is not None
    return day_word(found.day, now.date())


def find_time(text: str, sent: datetime, now: datetime, tz: tzinfo | None = None) -> TimeHit | None:
    """The soonest time the text names that still counts at `now`, or None."""
    tz = tz or local_tz()
    now = now.astimezone(tz)
    live: list[TimeHit] = []
    for found in find_times(text, sent, tz):
        if found.at is not None:
            stops, when = found.at + GRACE, found.at
        else:
            stops = when = found.end
        if now <= stops and when - now <= HORIZON:
            live.append(TimeHit(_label(found, now), found.at, stops, when))
    return min(live, key=lambda hit: hit.when) if live else None
