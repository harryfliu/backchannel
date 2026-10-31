#!/usr/bin/env python3
"""Hit every read route of a running server and validate the JSON against types.ts.

    ./backchannel dev            # in one terminal
    python tests/check_live.py   # in another (default http://127.0.0.1:5556)

Read-only: it doesn't send messages or change prefs.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import quote

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ts_shapes import validate  # noqa: E402

EXTRAS = {"stickerUrl"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:5556")
    parser.add_argument("--ai", action="store_true", help="also check /api/ai/catchup (makes a real Claude call)")
    args = parser.parse_args()
    problems = 0
    checked = 0

    with httpx.Client(base_url=args.base, timeout=30) as client:
        if client.get("/api/session").status_code != 200:
            print("Couldn't start a session; is the server running?")
            return 2

        def check(path: str, type_name: str, *, key: str | None = None, each: bool = False, params=None):
            nonlocal problems, checked
            response = client.get(path, params=params)
            if response.status_code != 200:
                print(f"FAIL {path}: HTTP {response.status_code} {response.text[:200]}")
                problems += 1
                return None
            data = response.json()
            values = data[key] if key else data
            for value in (values if each else [values]):
                errors = validate(value, type_name, allow_extra=EXTRAS)
                checked += 1
                if errors:
                    problems += 1
                    print(f"FAIL {path} as {type_name}:\n  " + "\n  ".join(errors[:10]))
            return data

        check("/api/health", "Health")
        check("/api/me", "Me")
        convs = check("/api/conversations", "Conversation", key="conversations", each=True) or {"conversations": []}
        needs_you = [c for c in convs["conversations"] if c.get("priority")]
        for conv in needs_you:
            for reason in conv["priority"]["reasons"]:
                errors = validate(reason, "PriorityReason")
                checked += 1
                if errors:
                    problems += 1
                    print(f"FAIL priority reason in {conv['id']}: {errors[:3]}")
        levels = [c["priority"]["level"] for c in needs_you]
        print(f"Needs you: {len(needs_you)} conversations ({levels.count('urgent')} urgent, {levels.count('high')} high)")
        important = 0
        for conv in convs["conversations"]:
            cid = quote(conv["id"], safe="")
            check(f"/api/conversations/{cid}", "Conversation")
            page = check(f"/api/conversations/{cid}/messages", "MessagePage", params={"limit": 200})
            check(f"/api/conversations/{cid}/media", "MediaPage")
            for message in (page or {}).get("messages", []):
                if message.get("importance"):
                    important += 1
                if message.get("thread"):
                    check(f"/api/conversations/{cid}/threads/{quote(message['id'], safe='')}", "ThreadResponse")
        print(f"{important} messages with importance")
        if needs_you and not important:
            print("FAIL: conversations need you but no message says why")
            problems += 1
        handles = sorted({p["handle"] for c in convs["conversations"] for p in c["participants"]})
        cards = 0
        for handle in handles:
            response = client.get(f"/api/people/{quote(handle, safe='')}/chief")
            if response.status_code != 200:
                print(f"FAIL chief card for {handle}: HTTP {response.status_code}")
                problems += 1
            elif response.json() is not None:
                cards += 1
                errors = validate(response.json(), "ChiefCard")
                checked += 1
                problems += bool(errors)
                for error in errors[:10]:
                    print(f"FAIL chief card for {handle}:", error)
        print(f"{cards} Chief cards among {len(handles)} people")
        check("/api/threads", "Message", key="items", each=True)
        check("/api/activity", "ActivityItem", key="items", each=True)
        check("/api/later", "LaterItem", key="items", each=True)
        check("/api/scheduled", "ScheduledMessage", key="items", each=True)
        check("/api/search", "SearchResult", key="results", each=True, params={"q": "in"})
        check("/api/settings", "ServerSettings")
        emoji = check("/api/emoji", "CustomEmoji", key="items", each=True) or {"items": []}
        stickers = check("/api/stickers", "StickerItem", key="items", each=True) or {"items": []}
        images = [e["url"] for e in emoji["items"]] + [s["url"] for s in stickers["items"]]
        for url in images:
            response = client.get(url)
            if response.status_code != 200 or not response.headers.get("content-type", "").startswith("image/"):
                print(f"FAIL {url}: HTTP {response.status_code} {response.headers.get('content-type')}")
                problems += 1
        print(f"{len(emoji['items'])} custom emoji, {len(stickers['items'])} stickers; {len(images)} images fetched")
        catchup = client.get("/api/ai/catchup") if args.ai else None
        if catchup is None:
            print("skip /api/ai/catchup (pass --ai to include it)")
        elif catchup.status_code == 200:
            errors = validate(catchup.json(), "CatchUp")
            checked += 1
            problems += bool(errors)
            for error in errors[:10]:
                print("FAIL /api/ai/catchup:", error)
        else:
            print(f"skip /api/ai/catchup (HTTP {catchup.status_code}: AI not configured)")

    print(f"{checked} responses checked, {problems} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
