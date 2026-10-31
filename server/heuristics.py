"""Small text heuristics plus the tapback / effect lookup tables."""

from __future__ import annotations

import re
import unicodedata

# --------------------------------------------------------------------------- tapbacks

# associated_message_type -> (kind, emoji, verb used in previews)
TAPBACKS: dict[int, tuple[str, str, str]] = {
    2000: ("love", "❤️", "Loved"),
    2001: ("like", "👍", "Liked"),
    2002: ("dislike", "👎", "Disliked"),
    2003: ("laugh", "😂", "Laughed at"),
    2004: ("emphasize", "‼️", "Emphasized"),
    2005: ("question", "❓", "Questioned"),
    2006: ("emoji", "", "Reacted to"),
    2007: ("sticker", "🖼️", "Sent a sticker on"),
}
STICKER_EMOJI = "🖼️"


def is_reaction_type(assoc_type: int | None) -> bool:
    """Tapbacks (2000-2007), their removals (3000-3007) and placed stickers (1000)."""
    t = assoc_type or 0
    return t == 1000 or 2000 <= t <= 3999


# --------------------------------------------------------------------------- effects

_EFFECTS = {
    "com.apple.MobileSMS.expressivesend.impact": "Slam",
    "com.apple.MobileSMS.expressivesend.loud": "Loud",
    "com.apple.MobileSMS.expressivesend.gentle": "Gentle",
    "com.apple.MobileSMS.expressivesend.invisibleink": "Invisible Ink",
    "com.apple.messages.effect.CKConfettiEffect": "Confetti",
    "com.apple.messages.effect.CKFireworksEffect": "Fireworks",
    "com.apple.messages.effect.CKHappyBirthdayEffect": "Balloons",
    "com.apple.messages.effect.CKHeartEffect": "Love",
    "com.apple.messages.effect.CKLasersEffect": "Lasers",
    "com.apple.messages.effect.CKShootingStarEffect": "Shooting Star",
    "com.apple.messages.effect.CKSparklesEffect": "Sparkles",
    "com.apple.messages.effect.CKSpotlightEffect": "Spotlight",
    "com.apple.messages.effect.CKEchoEffect": "Echo",
}


def effect_name(style_id: str | None) -> str | None:
    if not style_id:
        return None
    if style_id in _EFFECTS:
        return _EFFECTS[style_id]
    tail = style_id.rsplit(".", 1)[-1]
    if tail.startswith("CK") and tail.endswith("Effect"):
        tail = tail[2:-6]
    words = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", tail).strip()
    return words.title() if words else None


# --------------------------------------------------------------------------- "waiting on you"

_URL = re.compile(r"https?://\S+", re.IGNORECASE)
# Phrases that ask something of the reader even without a "?" (docs/api.md, "Waiting on you").
_REQUEST = re.compile(
    r"\b(?:can|could|would|will|are|do|did|have) (?:you|u)\b|\blmk\b|\blet me know\b|\bcall me\b|"
    r"\btext me\b|\bsend me\b|\bwanna\b|\bwant to\b|\bdown to\b|\bthoughts\b|\bwhen you get a chance\b",
    re.IGNORECASE,
)
# Messages that open with one of these close the loop ("Perfect, you're on the schedule").
_CLOSER = re.compile(
    r"^\W*(?:(?:ok(?:ay)?|kk?|thanks|thank you|thank u|thx|ty|perfect|sounds good|great|cool|got it|will do|"
    r"see you|see ya|np|no worries|no problem|lol+|lo+l|(?:ha){2,}h?|hah|lmf?ao|nice|awesome|yup|yep)(?![a-z])"
    r"|👍|👌|🙏|❤️?|😂|🤣)",
    re.IGNORECASE,
)


def has_question_mark(text: str | None) -> bool:
    return bool(text) and "?" in _URL.sub("", text or "")


def asks_something(text: str | None) -> bool:
    """Contains a "?" (outside URLs) or a request phrase like "call me" / "lmk"."""
    if not text:
        return False
    stripped = _URL.sub("", text)
    return "?" in stripped or bool(_REQUEST.search(stripped))


def opens_with_closer(text: str | None) -> bool:
    """Starts with ok / thanks / perfect / sounds good / 👍 … (a loop-closing reply)."""
    return bool(text) and bool(_CLOSER.match(unicodedata.normalize("NFKC", text or "").strip()))


# --------------------------------------------------------------------------- one-time codes

_OTP_PATTERNS = (
    re.compile(r"\bG-(\d{6})\b"),
    re.compile(
        r"\b(?:code|passcode|pin|otp|password|verification|security code)\b[^0-9\n]{0,30}?(?<![\d$.,])(\d{4,8})(?![\d,]|\.\d)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<![\d$.,])(\d{4,8})(?![\d,]|\.\d)[^0-9\n]{0,15}\b(?:is|as)\s+(?:your|the)\b[^\n]{0,40}?\b(?:code|passcode|pin|otp|password)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:code|passcode|verification)\b[^0-9\n]{0,30}?(?<![\d$.,])(\d{3})[- ](\d{3})(?![\d-])",
        re.IGNORECASE,
    ),
)


def extract_otp(text: str | None) -> str | None:
    """'Your code is 482913', '482913 is your verification code', 'Code: 4829'."""
    if not text:
        return None
    for pattern in _OTP_PATTERNS:
        match = pattern.search(text)
        if match:
            return "".join(g for g in match.groups() if g)
    return None


# --------------------------------------------------------------------------- automated senders

_AUTOMATED = re.compile(
    r"(verification code|one[- ]time (?:code|passcode|password|pin)|\bcode is\b|is your\b[^\n]{0,30}\bcode\b|"
    r"\bcode:\s*\d|\botp\b|reply stop|text stop|stop to (?:end|opt|cancel|unsubscribe)|msg ?& ?data|"
    r"do not reply|no-?reply|out for delivery|was delivered|has been delivered|your (?:package|order|shipment)|"
    r"track(?:ing)?[: ]|appointment|reminder|your (?:card|account) ending|purchase of \$|transaction|"
    r"available balance|payment (?:of|due|received)|deposit|fraud alert|security alert|"
    r"reply (?:y|n|c|yes|no)\b)",
    re.IGNORECASE,
)
_NOREPLY_HANDLE = re.compile(r"(no-?reply|notifications?|alerts?|donotreply|mailer)@", re.IGNORECASE)


def looks_automated(text: str | None) -> bool:
    return bool(text) and bool(_AUTOMATED.search(text or ""))


def looks_automated_handle(handle: str) -> bool:
    return bool(_NOREPLY_HANDLE.search(handle or ""))
