"""Apple <-> Unix time conversion and ISO 8601 formatting.

chat.db stores dates as nanoseconds since 2001-01-01 UTC. Very old rows use
seconds instead, so anything below 1e12 is treated as seconds. 0 means unset.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

APPLE_EPOCH = 978307200  # 2001-01-01T00:00:00Z as a Unix timestamp
_SECONDS_CUTOFF = 1_000_000_000_000


def apple_to_unix(value: int | float | None) -> float | None:
    """Convert a chat.db date (ns or s since 2001) to Unix seconds; None when unset."""
    if not value:
        return None
    if abs(value) < _SECONDS_CUTOFF:
        return float(value) + APPLE_EPOCH
    return value / 1e9 + APPLE_EPOCH


def apple_to_ns(value: int | None) -> int:
    """Normalise a chat.db date to nanoseconds (0 when unset), for comparisons."""
    if not value:
        return 0
    if abs(value) < _SECONDS_CUTOFF:
        return int(value) * 1_000_000_000
    return int(value)


def unix_to_apple_ns(ts: float) -> int:
    return int(round((ts - APPLE_EPOCH) * 1e9))


def now_apple_ns() -> int:
    return unix_to_apple_ns(time.time())


def iso(ts: float | None) -> str | None:
    """Unix seconds -> ISO 8601 in local time with offset, e.g. 2026-10-31T09:14:00-07:00."""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().isoformat(timespec="seconds")


def iso_apple(value: int | None) -> str | None:
    return iso(apple_to_unix(value))


def now_iso() -> str:
    return iso(time.time())  # type: ignore[return-value]


def parse_iso(value: str) -> float:
    """ISO 8601 -> Unix seconds. Accepts a trailing Z; naive times are local."""
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.astimezone()  # interpret as local time
    return dt.timestamp()
