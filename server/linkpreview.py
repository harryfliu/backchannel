"""Best-effort link previews from URL balloon messages.

For `balloon_bundle_id = com.apple.messages.URLBalloonProvider`, payload_data
is an NSKeyedArchiver binary plist wrapping LPLinkMetadata (title, siteName,
summary, URL...). Anything we can't parse falls back to the first URL in the text.
"""

from __future__ import annotations

import plistlib
import re
from typing import Any

URL_BALLOON = "com.apple.messages.URLBalloonProvider"
_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def first_url(text: str | None) -> str | None:
    if not text:
        return None
    match = _URL.search(text)
    return match.group(0).rstrip(").,!?;:") if match else None


def _resolve(objects: list[Any], value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return None
    if isinstance(value, plistlib.UID):
        if 0 <= value.data < len(objects):
            target = objects[value.data]
            return None if target == "$null" else _resolve(objects, target, depth + 1)
        return None
    return value


def _url_string(objects: list[Any], value: Any) -> str | None:
    value = _resolve(objects, value)
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        relative = _resolve(objects, value.get("NS.relative"))
        base = _resolve(objects, value.get("NS.base"))
        rel = _url_string(objects, relative) if isinstance(relative, dict) else relative
        if isinstance(rel, str):
            if isinstance(base, (str, dict)) and not rel.startswith(("http://", "https://")):
                prefix = _url_string(objects, base)
                return (prefix or "") + rel
            return rel
    return None


def _text(objects: list[Any], value: Any) -> str | None:
    value = _resolve(objects, value)
    if isinstance(value, dict) and "NS.string" in value:
        value = _resolve(objects, value["NS.string"])
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def parse_payload(payload: bytes | None) -> dict[str, str | None] | None:
    """Pull url/title/siteName/summary out of an archived LPLinkMetadata."""
    if not payload:
        return None
    try:
        archive = plistlib.loads(bytes(payload))
    except Exception:
        return None
    if not isinstance(archive, dict) or not isinstance(archive.get("$objects"), list):
        return None
    objects: list[Any] = archive["$objects"]
    meta: dict[str, Any] | None = None
    for obj in objects:
        if isinstance(obj, dict) and "richLinkMetadata" in obj:
            candidate = _resolve(objects, obj["richLinkMetadata"])
            if isinstance(candidate, dict):
                meta = candidate
                break
    if meta is None:
        for obj in objects:
            if isinstance(obj, dict) and ("title" in obj or "originalURL" in obj or "URL" in obj):
                meta = obj
                break
    if meta is None:
        return None
    url = _url_string(objects, meta.get("URL")) or _url_string(objects, meta.get("originalURL"))
    if not url:
        for obj in objects:
            if isinstance(obj, str) and obj.startswith(("http://", "https://")):
                url = obj
                break
    return {
        "url": url,
        "title": _text(objects, meta.get("title")),
        "siteName": _text(objects, meta.get("siteName")),
        "summary": _text(objects, meta.get("summary")),
    }


def link_preview(
    balloon_bundle_id: str | None, payload: bytes | None, text: str | None, image_url: str | None = None
) -> dict[str, str | None] | None:
    """LinkPreview JSON for a URL balloon message, or None for anything else."""
    if balloon_bundle_id != URL_BALLOON:
        return None
    parsed = parse_payload(payload)
    url = (parsed or {}).get("url") or first_url(text)
    if not url:
        return None
    return {
        "url": url,
        "title": (parsed or {}).get("title"),
        "siteName": (parsed or {}).get("siteName"),
        "summary": (parsed or {}).get("summary"),
        "imageUrl": image_url,
    }
