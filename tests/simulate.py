#!/usr/bin/env python3
"""Append incoming messages to the fixture chat.db every few seconds.

    python tests/simulate.py                      # tests/fixtures/chat.db, every 5 s
    python tests/simulate.py --interval 2 --count 10
    ./backchannel simulate                        # same, via the control script

Mostly new texts (some thread replies), sometimes a tapback on Harry's latest
message, so the UI's live `message`, `message-updated` and `conversation`
events can be watched. Refuses to touch anything under ~/Library/Messages.
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from server import fakedb  # noqa: E402

SCRIPTS = [
    ("iMessage;-;+14155550134", ["+14155550134"],
     ["also bringing snacks", "you around later?", "lmk about saturday!", "lol did you see that", "trailhead at 8?"]),
    ("iMessage;+;chat100000000000000002", ["+14155550118", "+14155550126", "+14155550139", "+14155550155"],
     ["who's bringing cones?", "weather looks good for sunday", "I'm in", "anyone need a ride?", "bringing water"]),
    ("iMessage;+;chat100000000000000003", ["+14155550178", "+16505550147", "+14155550112"],
     ["so excited for this", "should we do omakase after?", "I can drive", "reservation is under Priya"]),
    ("iMessage;-;+15105550122", ["+15105550122"],
     ["Call me when you're free", "Did you eat?", "Love you!", "Dad says hi"]),
    ("SMS;-;+14155550161", ["+14155550161"],
     ["Running a few minutes late tomorrow", "Can you send me a photo of the vanity?", "Materials arrive Wednesday"]),
]
TAPBACKS = [(2000, "Loved"), (2001, "Liked"), (2003, "Laughed at"), (2004, "Emphasized")]


def handle_rowid(conn, handle: str, service: str) -> int:
    return fakedb.ensure_handle(conn, handle, service)


def tick(db_path: Path, rng: random.Random) -> str:
    conn = fakedb.connect(db_path)
    try:
        chat_guid, senders, lines = rng.choice(SCRIPTS)
        chat = fakedb.chat_info(conn, chat_guid)
        service = chat["service_name"] or "iMessage"
        sender = rng.choice(senders)
        hid = handle_rowid(conn, sender, service)
        conn.execute("BEGIN IMMEDIATE")
        roll = rng.random()
        mine = conn.execute(
            """SELECT m.guid, m.text, m.attributedBody FROM message m JOIN chat_message_join j ON j.message_id = m.ROWID
               WHERE j.chat_id = ? AND m.is_from_me = 1 AND m.associated_message_type = 0 AND m.item_type = 0
               ORDER BY m.date DESC LIMIT 1""",
            (chat["ROWID"],),
        ).fetchone()
        if roll < 0.2 and mine:
            kind, verb = rng.choice(TAPBACKS)
            from server.conversations import message_text

            target_text = message_text(mine) or "a message"
            fakedb.insert_message(
                conn, chat_rowid=chat["ROWID"], text=f"{verb} “{target_text}”", is_from_me=False, handle_rowid=hid,
                service=service, associated_message_guid=f"p:0/{mine['guid']}", associated_message_type=kind,
            )
            what = f"{sender} {verb.lower()} Harry's “{target_text[:30]}”"
        else:
            thread = None
            if chat["style"] == 43 and roll > 0.75:
                root = conn.execute(
                    """SELECT m.guid FROM message m JOIN chat_message_join j ON j.message_id = m.ROWID
                       WHERE j.chat_id = ? AND COALESCE(m.thread_originator_guid, '') = ''
                         AND m.associated_message_type = 0 AND m.item_type = 0
                       ORDER BY m.date DESC LIMIT 1""",
                    (chat["ROWID"],),
                ).fetchone()
                thread = root["guid"] if root else None
            text = rng.choice(lines)
            extra = {"thread_originator_guid": thread} if thread else {}
            fakedb.insert_message(conn, chat_rowid=chat["ROWID"], text=text, is_from_me=False, handle_rowid=hid,
                                  service=service, **extra)
            what = f"{sender} → {chat_guid}: {text}" + (" (thread reply)" if thread else "")
        conn.execute("COMMIT")
        return what
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=HERE / "fixtures" / "chat.db")
    parser.add_argument("--interval", type=float, default=5.0, help="seconds between messages (default 5)")
    parser.add_argument("--count", type=int, default=0, help="stop after N messages (default: run forever)")
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()
    if not args.db.exists():
        sys.exit(f"{args.db} doesn't exist. Run: python tests/make_fixtures.py")
    rng = random.Random(args.seed)
    sent = 0
    try:
        while True:
            print(time.strftime("%H:%M:%S"), tick(args.db, rng), flush=True)
            sent += 1
            if args.count and sent >= args.count:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
