"""Phone/email handle normalisation and display formatting."""

from __future__ import annotations

import re

_NON_DIGITS = re.compile(r"\D")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_ACCOUNT_PREFIX = re.compile(r"^[ep]:", re.IGNORECASE)


def digits(value: str) -> str:
    return _NON_DIGITS.sub("", value or "")


def normalize_handle(raw: str | None) -> str:
    """Canonical form: E.164 for phone numbers, lowercase for emails, digits for short codes."""
    if not raw:
        return ""
    if raw[0] == "+" and raw[1:].isdigit():
        return raw  # already E.164: the common case in chat.db
    value = _ACCOUNT_PREFIX.sub("", raw.strip())
    for prefix in ("mailto:", "tel:"):
        if value.lower().startswith(prefix):
            value = value[len(prefix):]
    if "@" in value or value.lower().startswith("urn:"):
        return value.lower()  # emails and Apple Business Chat ids
    nums = digits(value)
    if not nums:
        return value.lower()
    has_plus = value.startswith("+")
    if has_plus:
        return "+" + nums
    if len(nums) == 10:
        return "+1" + nums  # US number written without a country code
    if len(nums) == 11 and nums.startswith("1"):
        return "+" + nums
    return nums  # short codes and local numbers stay as plain digits


def parse_handle(raw: str | None) -> str:
    """Validate what someone typed into "new conversation": E.164 phone, lowercase email or short code."""
    value = (raw or "").strip()
    if value.lower().startswith("mailto:"):
        value = value[len("mailto:"):]
    if not value:
        raise ValueError("Enter a phone number or email address.")
    if "@" in value:
        email = value.lower()
        if not _EMAIL.match(email):
            raise ValueError("That email address doesn't look right.")
        return email
    if re.search(r"[A-Za-z]", value) or not re.fullmatch(r"[\d\s().+\-]+", value):
        raise ValueError("That doesn't look like a phone number or email address.")
    handle = normalize_handle(value)
    if is_short_code(handle):
        return handle
    if handle.startswith("+") and 8 <= len(digits(handle)) <= 15:
        return handle
    raise ValueError("Include the area code, like (415) 555-0134 or +44 20 7946 0958.")


def is_email(handle: str) -> bool:
    return "@" in handle


def is_short_code(handle: str) -> bool:
    """3-6 digit senders such as 24273 (banks, deliveries, 2FA)."""
    return bool(handle) and handle.isdigit() and 3 <= len(handle) <= 6


def phone_key(handle: str) -> str | None:
    """Last 10 digits, the way Harry's digest matches Contacts numbers."""
    if not handle or "@" in handle:
        return None
    nums = digits(handle)
    if len(nums) < 7:
        return None
    return nums[-10:]


def format_handle(handle: str) -> str:
    """Human-readable form for people who aren't in Contacts."""
    if not handle:
        return "Unknown"
    if "@" in handle:
        return handle
    nums = digits(handle)
    if handle.startswith("+1") and len(nums) == 11:
        return f"({nums[1:4]}) {nums[4:7]}-{nums[7:]}"
    return handle
