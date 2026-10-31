#!/usr/bin/env python3
"""Build a large synthetic chat.db and time the hot paths (not part of pytest).

    python tests/perf_check.py --messages 500000 --chats 2500

Prints timings for the conversation list (cold, warm, after one new
message), a message page in the biggest chat, unread counting and the first
search sync.
"""

from __future__ import annotations

import argparse
import random
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE)]

from chatdb_schema import CHAT_DB_SCHEMA  # noqa: E402
from conftest import settings_for  # noqa: E402

from server import fakedb  # noqa: E402
from server.app import Services  # noqa: E402
from server.timeutil import unix_to_apple_ns  # noqa: E402
from server.typedstream import encode_attributed_body  # noqa: E402

WORDS = "ok sounds good see you at seven running late lol haha what time dinner tomorrow can you call me".split()


def build(path: Path, n_messages: int, n_chats: int, seed: int = 1) -> None:
    rng = random.Random(seed)
    conn = sqlite3.connect(path)
    tables = [s for s in CHAT_DB_SCHEMA.split(";") if s.strip() and "CREATE INDEX" not in s]
    indexes = [s for s in CHAT_DB_SCHEMA.split(";") if "CREATE INDEX" in s]
    conn.executescript(";".join(tables) + ";")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("BEGIN")
    handles = [f"+1415{rng.randrange(10**6, 10**7)}" for _ in range(int(n_chats * 1.2))]
    conn.executemany("INSERT INTO handle (ROWID, id, service) VALUES (?, ?, 'iMessage')",
                     [(i + 1, h) for i, h in enumerate(handles)])
    chats = []
    for c in range(1, n_chats + 1):
        group = c % 5 == 0
        members = rng.sample(range(1, len(handles) + 1), 4) if group else [c]
        guid = f"iMessage;+;chat{c:018d}" if group else f"iMessage;-;{handles[c - 1]}"
        conn.execute("INSERT INTO chat (ROWID, guid, style, chat_identifier, service_name, display_name) VALUES (?, ?, ?, ?, 'iMessage', ?)",
                     (c, guid, 43 if group else 45, guid.split(";")[2], f"Group {c}" if group and c % 2 else ""))
        conn.executemany("INSERT INTO chat_handle_join (chat_id, handle_id) VALUES (?, ?)", [(c, m) for m in members])
        chats.append((c, members))
    weights = [1 / (i + 1) ** 0.9 for i in range(n_chats)]  # a few huge chats, a long tail
    now = time.time()
    span = 8 * 365 * 86400
    msgs, joins = [], []
    for rowid in range(1, n_messages + 1):
        chat_id, members = rng.choices(chats, weights)[0]
        when = now - span * (1 - rowid / n_messages) ** 1.5
        date = unix_to_apple_ns(when)
        from_me = rng.random() < 0.4
        text = " ".join(rng.choices(WORDS, k=rng.randint(1, 12)))
        tapback = rng.random() < 0.05 and rowid > 10
        recent = now - when < 3 * 86400
        msgs.append((
            rowid, f"{rowid:08X}-0000-4000-8000-000000000000", None if rng.random() < 0.85 else text,
            encode_attributed_body(text), 0 if from_me else rng.choice(members), "iMessage", date, int(from_me),
            0 if (recent and not from_me and rng.random() < 0.3) else 1,
            f"p:0/{rowid - 5:08X}-0000-4000-8000-000000000000" if tapback else None, 2000 if tapback else 0,
        ))
        joins.append((chat_id, rowid, date))
        if len(msgs) == 20000:
            conn.executemany("INSERT INTO message (ROWID, guid, text, attributedBody, handle_id, service, date, is_from_me, is_read, associated_message_guid, associated_message_type) VALUES (?,?,?,?,?,?,?,?,?,?,?)", msgs)
            conn.executemany("INSERT INTO chat_message_join (chat_id, message_id, message_date) VALUES (?,?,?)", joins)
            msgs, joins = [], []
    if msgs:
        conn.executemany("INSERT INTO message (ROWID, guid, text, attributedBody, handle_id, service, date, is_from_me, is_read, associated_message_guid, associated_message_type) VALUES (?,?,?,?,?,?,?,?,?,?,?)", msgs)
        conn.executemany("INSERT INTO chat_message_join (chat_id, message_id, message_date) VALUES (?,?,?)", joins)
    conn.execute("COMMIT")
    for statement in indexes:
        conn.execute(statement)
    conn.execute("ANALYZE")
    conn.close()


def add_attachments(path: Path, attachments_root: Path, n_attachments: int, n_stickers: int, n_messages: int,
                    seed: int = 2) -> None:
    """Attachment rows on random messages; `n_stickers` of them are stickers with small files on disk
    (a quarter of them re-sent copies of the same image)."""
    import media_gen

    rng = random.Random(seed)
    conn = sqlite3.connect(path)
    conn.execute("BEGIN")
    rows, joins = [], []
    images = [media_gen.png(8, 8, lambda x, y, k=k: (k % 256, (k // 256) % 256, 7)) for k in range(max(1, n_stickers))]
    for rowid in range(1, n_attachments + 1):
        guid = f"AT{rowid:08X}-0000-4000-8000-000000000000"
        sticker = rowid <= n_stickers
        if sticker:
            data = images[rng.randrange(len(images) // 4 + 1)] if rng.random() < 0.25 else images[rowid - 1]
            filename, size = fakedb.store_attachment_file(attachments_root, guid, "sticker.png", data)
        else:
            filename, size = f"~/Library/Messages/Attachments/xx/{guid}/IMG_{rowid}.HEIC", 2_000_000
        rows.append((rowid, guid, guid, filename, "image/png" if sticker else "image/heic", "sticker.png" if sticker
                     else f"IMG_{rowid}.HEIC", size, int(sticker), 5))
        joins.append((rng.randrange(1, n_messages + 1), rowid))
    conn.executemany("INSERT INTO attachment (ROWID, guid, original_guid, filename, mime_type, transfer_name, "
                     "total_bytes, is_sticker, transfer_state) VALUES (?,?,?,?,?,?,?,?,?)", rows)
    conn.executemany("INSERT INTO message_attachment_join (message_id, attachment_id) VALUES (?,?)", joins)
    conn.execute("COMMIT")
    conn.close()


def timed(label: str, fn, repeat: int = 1):
    best = None
    result = None
    for _ in range(repeat):
        start = time.perf_counter()
        result = fn()
        elapsed = (time.perf_counter() - start) * 1000
        best = elapsed if best is None else min(best, elapsed)
    print(f"  {label:<48} {best:8.1f} ms")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--messages", type=int, default=500_000)
    parser.add_argument("--chats", type=int, default=2500)
    parser.add_argument("--attachments", type=int, default=60_000)
    parser.add_argument("--stickers", type=int, default=2000)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="bc-perf-") as tmp:
        tmp_path = Path(tmp)
        fx = tmp_path / "fx"
        fx.mkdir()
        (fx / "AddressBook").mkdir()
        start = time.time()
        build(fx / "chat.db", args.messages, args.chats)
        add_attachments(fx / "chat.db", fx / "Attachments", args.attachments, args.stickers, args.messages)
        size = (fx / "chat.db").stat().st_size / 1e6
        print(f"Built {args.messages:,} messages / {args.chats:,} chats / {args.attachments:,} attachments "
              f"({args.stickers:,} stickers) ({size:.0f} MB) in {time.time() - start:.0f} s")
        services = Services(settings_for(fx, tmp_path / "state"))
        convs = services.convs
        listed = timed("conversation list (cold)", convs.list)
        print(f"    -> {len(listed)} conversations in the sidebar")
        timed("conversation list (warm, cache dropped)", lambda: (setattr(convs, "_list_cache", None), convs.list())[1],
              repeat=3)
        timed("conversation list (cached)", convs.list, repeat=3)
        conn = fakedb.connect(fx / "chat.db")
        fakedb.insert_message(conn, chat_rowid=1, text="new one", is_from_me=False, handle_rowid=1, date=time.time())
        conn.close()
        timed("conversation list (after one new message)", convs.list)
        biggest = max(convs.directory().unique_convs(), key=lambda c: convs.last_date(c))
        timed("messages page (60) in a busy chat", lambda: services.builder.page(biggest, None, 60), repeat=3)
        timed("unread scan", services.convs._load_unread, repeat=3)
        timed("search: first sync (newest batch)", lambda: services.search.sync(backfill_batches=0))
        timed("search: one backfill batch", lambda: services.search.sync(backfill_batches=1))
        timed("search query", lambda: services.search.search("dinner tomorrow", None, 40), repeat=3)
        stickers = timed("stickers (cold: hashes every file)", lambda: services.stickers.list(10_000))
        print(f"    -> {len(stickers)} distinct stickers")
        timed("stickers (chat.db changed: hashes cached)",
              lambda: (services.stickers.invalidate(), services.stickers.list())[1], repeat=3)
        timed("stickers (cached)", services.stickers.list, repeat=3)
        services.close()


if __name__ == "__main__":
    main()
