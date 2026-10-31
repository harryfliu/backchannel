#!/usr/bin/env python3
"""Generate a fake chat.db, AddressBook and attachment files for Backchannel.

    python tests/make_fixtures.py                 # writes tests/fixtures/
    python tests/make_fixtures.py --out /tmp/fx   # somewhere else

Everything is fictional (555-01xx numbers, .example domains). Timestamps are
relative to when the script runs (the last ~3 weeks, a few unread today), and
GUIDs are deterministic so local state (stars, later items) survives a
regeneration. Most messages have text = NULL and only a typedstream
attributedBody, like real chat.db rows since Ventura.
"""

from __future__ import annotations

import argparse
import json
import plistlib
import shutil
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for path in (str(ROOT), str(HERE)):
    if path not in sys.path:
        sys.path.insert(0, path)

import media_gen  # noqa: E402
from chatdb_schema import ADDRESSBOOK_SCHEMA, CHAT_DB_SCHEMA, CHIEF_SCHEMA  # noqa: E402

from server import fakedb  # noqa: E402
from server.timeutil import APPLE_EPOCH, unix_to_apple_ns  # noqa: E402
from server.typedstream import encode_attributed_body  # noqa: E402

DEFAULT_OUT = HERE / "fixtures"
NAMESPACE = uuid.UUID("6b1c0f4e-5d7a-4c55-9a51-0b5ac3a11e5e")

ME_PHONE = "+15105550100"
ME_EMAIL = "harry.liu@icloud.example"
ME_NAME = "Harry Liu"

# key, first, last, nickname, organization, phones (as typed in Contacts), emails, photo, source
CONTACTS: list[dict[str, Any]] = [
    dict(key="jordan", first="Jordan", last="Park", phones=["(415) 555-0134"], emails=["Jordan.Park@icloud.example"], photo="thumb"),
    dict(key="priya", first="Priya", last="Natarajan", phones=["+1 415-555-0178"], photo="image"),
    dict(key="mom", first="Linda", last="Liu", nickname="Mom", phones=["+1 (510) 555-0122"], photo="thumb"),
    dict(key="dad", first="David", last="Liu", nickname="Dad", phones=["510.555.0123"]),
    dict(key="grace", first="Grace", last="Liu", phones=["(628) 555-0190"]),
    dict(key="marcus", first="Marcus", last="Webb", org="Webb Renovations", phones=["415-555-0161"]),
    dict(key="ellie", first="Ellie", last="Chen", phones=["(650) 555-0147"]),
    dict(key="sam", first="Sam", last="Rivera", phones=["(415) 555-0112"]),
    dict(key="tomas", first="Tomás", last="Ortega", phones=["(415) 555-0118"]),
    dict(key="kevin", first="Kevin", last="Huang", phones=["(415) 555-0126"]),
    dict(key="aisha", first="Aisha", last="Bello", phones=["(415) 555-0139"]),
    dict(key="ben", first="Ben", last="Carter", phones=["(415) 555-0143"], source="local"),
    dict(key="sunset", org="Sunset Auto Repair", phones=["(415) 555-0170"], source="local"),
    dict(key="me", first="Harry", last="Liu", phones=["(510) 555-0100"], emails=[ME_EMAIL], source="root"),
]

# chat.db handle ids (E.164 / lowercase email / short code) for everyone who texts
HANDLES = {
    "jordan": "+14155550134", "jordan_email": "jordan.park@icloud.example", "priya": "+14155550178",
    "mom": "+15105550122", "dad": "+15105550123", "grace": "+16285550190", "marcus": "+14155550161",
    "ellie": "+16505550147", "sam": "+14155550112", "tomas": "+14155550118", "kevin": "+14155550126",
    "aisha": "+14155550139", "ben": "+14155550143", "sunset": "+14155550170", "nate": "+14155550155",
    "vet": "+14155550199", "bank": "24273", "ups": "69525", "dental": "36320", "me": ME_PHONE,
}

TAPBACK_VERBS = {2000: "Loved", 2001: "Liked", 2002: "Disliked", 2003: "Laughed at", 2004: "Emphasized",
                 2005: "Questioned"}
REMOVED_NOUNS = {3000: "a heart", 3001: "a like", 3002: "a dislike", 3003: "a laugh", 3004: "an exclamation",
                 3005: "a question mark"}


def det_uuid(label: str) -> str:
    return str(uuid.uuid5(NAMESPACE, label)).upper()


def avatar_jpeg(seed: int) -> bytes:
    palette = [(66, 133, 244), (219, 68, 55), (244, 180, 0), (15, 157, 88), (171, 71, 188)]
    base = palette[seed % len(palette)]

    def block(bx: int, by: int) -> tuple[int, int, int]:
        centre = (bx - 3.5) ** 2 + (by - 3.5) ** 2
        if centre < 4:
            return (250, 235, 220)
        shade = 1 - 0.06 * by
        return tuple(int(c * shade) for c in base)  # type: ignore[return-value]

    return media_gen.jpeg(64, 64, block)


# --------------------------------------------------------------------------- AddressBook


def write_addressbook(out: Path) -> dict[str, str]:
    """Writes AddressBook DBs; returns contact key -> ZUNIQUEID."""
    root = out / "AddressBook"
    sources = {
        "icloud": root / "Sources" / det_uuid("source:icloud") / "AddressBook-v22.abcddb",
        "local": root / "Sources" / det_uuid("source:local") / "AddressBook-v22.abcddb",
        "root": root / "AddressBook-v22.abcddb",
    }
    conns: dict[str, sqlite3.Connection] = {}
    for name, path in sources.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        conn.executescript(ADDRESSBOOK_SCHEMA)
        conn.execute("INSERT INTO Z_PRIMARYKEY VALUES (22, 'ABCDContact', 19, 0)")
        conn.execute("INSERT INTO Z_METADATA VALUES (1, ?, NULL)", (det_uuid(f"meta:{name}"),))
        conns[name] = conn
    unique_ids: dict[str, str] = {}
    pk = {"icloud": 0, "local": 0, "root": 0}
    for index, contact in enumerate(CONTACTS):
        source = contact.get("source", "icloud")
        conn = conns[source]
        pk[source] += 1
        record = pk[source]
        unique = det_uuid(f"contact:{contact['key']}") + ":ABPerson"
        unique_ids[contact["key"]] = unique
        thumb = None
        if contact.get("photo") == "thumb":
            thumb = b"\x01" + avatar_jpeg(index)  # real thumbnails carry a leading format byte
        elif contact.get("photo") == "image":
            images = sources[source].parent / "Images"
            images.mkdir(parents=True, exist_ok=True)
            (images / unique.split(":")[0]).write_bytes(avatar_jpeg(index))
        conn.execute(
            """INSERT INTO ZABCDRECORD (Z_PK, Z_ENT, Z_OPT, ZFIRSTNAME, ZLASTNAME, ZNICKNAME, ZORGANIZATION,
                   ZDISPLAYFLAGS, ZUNIQUEID, ZTHUMBNAILIMAGEDATA, ZME, ZCREATIONDATE, ZMODIFICATIONDATE)
               VALUES (?, 22, 1, ?, ?, ?, ?, ?, ?, ?, ?, 700000000, 780000000)""",
            (record, contact.get("first"), contact.get("last"), contact.get("nickname"), contact.get("org"),
             1 if contact.get("org") and not contact.get("first") else 0, unique, thumb,
             1 if contact["key"] == "me" else 0),
        )
        for order, phone in enumerate(contact.get("phones", [])):
            conn.execute(
                "INSERT INTO ZABCDPHONENUMBER (Z_ENT, Z_OPT, ZISPRIMARY, ZORDERINGINDEX, ZOWNER, ZFULLNUMBER, ZLABEL, ZUNIQUEID) "
                "VALUES (17, 1, ?, ?, ?, ?, '_$!<Mobile>!$_', ?)",
                (1 if order == 0 else 0, order, record, phone, det_uuid(f"phone:{contact['key']}:{order}")),
            )
        for order, email in enumerate(contact.get("emails", [])):
            conn.execute(
                "INSERT INTO ZABCDEMAILADDRESS (Z_ENT, Z_OPT, ZISPRIMARY, ZORDERINGINDEX, ZOWNER, ZADDRESS, ZADDRESSNORMALIZED, ZLABEL, ZUNIQUEID) "
                "VALUES (13, 1, ?, ?, ?, ?, ?, '_$!<Home>!$_', ?)",
                (1 if order == 0 else 0, order, record, email, email.lower(), det_uuid(f"email:{contact['key']}:{order}")),
            )
    for conn in conns.values():
        conn.commit()
        conn.close()
    return unique_ids


# --------------------------------------------------------------------------- chat.db


class ChatBuilder:
    def __init__(self, out: Path, now: datetime):
        self.out = out
        self.now = now
        self.attachments_root = out / "Attachments"
        self.conn = sqlite3.connect(out / "chat.db", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(CHAT_DB_SCHEMA)
        self.conn.execute("BEGIN")
        self.conn.executemany(
            "INSERT INTO _SqliteDatabaseProperties (key, value) VALUES (?, ?)",
            [("_ClientVersion", "17003"), ("counter_in_all", "0"), ("counter_out_all", "0"),
             ("_UniqueIdentifier", det_uuid("chatdb"))],
        )
        self.handles: dict[tuple[str, str], int] = {}
        self.chats: dict[str, dict[str, Any]] = {}
        self.guids: dict[str, str] = {}
        self.texts: dict[str, str] = {}
        self.last_guid: dict[str, str] = {}
        self.counter = 0
        self.manifest: dict[str, Any] = {"chats": {}, "messages": {}, "attachments": {}}

    # time helpers ----------------------------------------------------------------

    def day(self, days_ago: int, hh: int, mm: int = 0) -> float:
        """A wall-clock time `days_ago` days ago (days_ago >= 2 so it's always in the past)."""
        d = (self.now - timedelta(days=days_ago)).replace(hour=hh, minute=mm, second=0, microsecond=0)
        return d.timestamp()

    def ago(self, hours: float = 0, minutes: float = 0) -> float:
        return (self.now - timedelta(hours=hours, minutes=minutes)).timestamp()

    # rows ------------------------------------------------------------------------

    def handle(self, key: str, service: str) -> int:
        hid = HANDLES[key]
        cache_key = (hid, service)
        if cache_key not in self.handles:
            self.handles[cache_key] = fakedb.ensure_handle(self.conn, hid, service)
        return self.handles[cache_key]

    def chat(self, key: str, guid: str, members: list[str], *, name: str | None = None, group: bool = False,
             identifier_key: str | None = None) -> None:
        service = guid.split(";")[0]
        service = "iMessage" if service == "any" else service
        identifier = guid.split(";")[2]
        me_handle = ME_EMAIL if service == "iMessage" and group else ME_PHONE
        props = plistlib.dumps({"shouldForceToSMS": False, "messageHandshakeState": 1,
                                "numberOfTimesRespondedtoThread": 0}, fmt=plistlib.FMT_BINARY)
        rowid = fakedb._insert(self.conn, "chat", {
            "guid": guid, "style": 43 if group else 45, "state": 3, "account_id": det_uuid(f"account:{service}"),
            "properties": props, "chat_identifier": identifier, "service_name": service,
            "room_name": identifier if group else None,
            "account_login": ("E:" + ME_EMAIL) if service == "iMessage" else ("P:" + ME_PHONE),
            "is_archived": 0, "last_addressed_handle": me_handle, "display_name": name or "",
            "group_id": det_uuid(f"group:{guid}"), "is_filtered": 0, "successful_query": 1,
        })
        member_ids = []
        for member in members:
            hid = self.handle(member, service)
            member_ids.append(hid)
            self.conn.execute("INSERT INTO chat_handle_join (chat_id, handle_id) VALUES (?, ?)", (rowid, hid))
        self.chats[key] = {"rowid": rowid, "guid": guid, "service": service, "group": group, "members": members,
                           "member_ids": member_ids}
        self.manifest["chats"][key] = {"rowid": rowid, "guid": guid}

    def _attachment(self, msg_guid: str, part: int, name: str, data: bytes | None, mime: str | None,
                    uti: str | None, *, sticker: bool = False, outgoing: bool = False, when: float) -> dict:
        guid = f"at_{part}_{msg_guid}"
        if data is None:  # offloaded to iCloud: the row exists, the file doesn't
            filename = f"~/Library/Messages/Attachments/{fakedb.attachment_relpath(guid, name)}"
            size = 2_481_337
        else:
            filename, size = fakedb.store_attachment_file(self.attachments_root, guid, name, data)
        self.manifest["attachments"][f"{msg_guid}:{part}"] = guid
        return {"guid": guid, "filename": filename, "mime_type": mime, "uti": uti, "transfer_name": name,
                "total_bytes": size, "is_sticker": int(sticker), "is_outgoing": int(outgoing),
                "transfer_state": 5 if data is not None else 0, "created_date": unix_to_apple_ns(when)}

    def msg(self, chat_key: str, sender: str, text: str | None, when: float, *, label: str | None = None,
            unread: bool = False, attachments: list[tuple] = (), mentions: list[tuple[int, int, str]] = (),
            thread: str | None = None, store_text: bool = False, effect: str | None = None,
            **columns: Any) -> str:
        chat = self.chats[chat_key]
        self.counter += 1
        label = label or f"{chat_key}:{self.counter}"
        guid = det_uuid(f"msg:{label}")
        from_me = sender == "me"
        if from_me:
            handle_rowid = chat["member_ids"][0] if not chat["group"] else 0
        else:
            handle_rowid = self.handle(sender, chat["service"])
        atts = []
        for part, spec in enumerate(attachments):
            name, data, mime, uti = spec[:4]
            extra = spec[4] if len(spec) > 4 else {}
            atts.append(self._attachment(guid, part, name, data, mime, uti, outgoing=from_me, when=when, **extra))
        my_handle = ME_EMAIL if chat["service"] == "iMessage" and chat["group"] else ME_PHONE
        cols: dict[str, Any] = {
            "reply_to_guid": self.last_guid.get(chat_key),
            "is_read": 1 if from_me or not unread else 0,
            "date_read": 0 if (from_me or unread) else unix_to_apple_ns(when + 120),
            "has_unseen_mention": 1 if (mentions and unread) else 0,
        }
        if from_me:
            cols["is_delivered"] = 1
            cols["date_delivered"] = unix_to_apple_ns(when + 2)
            if chat["service"] == "iMessage" and not chat["group"]:
                cols["date_read"] = unix_to_apple_ns(when + 300)
        if thread:
            cols["thread_originator_guid"] = self.guids[thread]
            cols["thread_originator_part"] = "0:0:" + str(len(self.texts.get(thread, "")))
        if effect:
            cols["expressive_send_style_id"] = effect
        cols.update(columns)
        _, guid = fakedb.insert_message(
            self.conn, chat_rowid=chat["rowid"], text=text, is_from_me=from_me, handle_rowid=handle_rowid,
            date=when, service=chat["service"], guid=guid, store_text_column=store_text, mentions=mentions,
            attachments=atts, my_handle=my_handle, **cols,
        )
        self.guids[label] = guid
        self.texts[label] = text or ""
        self.last_guid[chat_key] = guid
        self.manifest["messages"][label] = guid
        return guid

    def tapback(self, chat_key: str, sender: str, target: str, kind: int, when: float, *, emoji: str | None = None,
                part: int = 0, sticker: bytes | None = None, label: str | None = None) -> str:
        target_text = self.texts.get(target, "")
        if part == 0 and self._has_attachment(target):
            quoted = "an image"
        else:
            quoted = f"“{target_text}”" if target_text else "a message"
        if 2000 <= kind <= 2005:
            text = f"{TAPBACK_VERBS[kind]} {quoted}"
        elif kind == 2006:
            text = f"Reacted {emoji} to {quoted}"
        elif kind == 2007:
            text = f"Sent a sticker on {quoted}"
        elif 3000 <= kind <= 3005:
            text = f"Removed {REMOVED_NOUNS[kind]} from {quoted}"
        else:
            text = f"Removed a reaction from {quoted}"
        attachments = []
        if sticker is not None:
            attachments.append(("sticker.png", sticker, "image/png", "public.png", {"sticker": True}))
        cols = {
            "associated_message_guid": f"p:{part}/{self.guids[target]}",
            "associated_message_type": kind,
            "associated_message_range_location": 0,
            "associated_message_range_length": len(target_text),
        }
        if emoji:
            cols["associated_message_emoji"] = emoji
        return self.msg(chat_key, sender, text, when, label=label, attachments=attachments, **cols)

    def _has_attachment(self, label: str) -> bool:
        row = self.conn.execute("SELECT cache_has_attachments FROM message WHERE guid = ?", (self.guids[label],)).fetchone()
        return bool(row and row[0])

    def event(self, chat_key: str, actor: str, item_type: int, when: float, *, action: int = 0,
              other: str | None = None, title: str | None = None, label: str | None = None) -> str:
        chat = self.chats[chat_key]
        self.counter += 1
        label = label or f"{chat_key}:event:{self.counter}"
        guid = det_uuid(f"msg:{label}")
        _, guid = fakedb.insert_message(
            self.conn, chat_rowid=chat["rowid"], text=None, is_from_me=actor == "me",
            handle_rowid=0 if actor == "me" else self.handle(actor, chat["service"]), date=when,
            service=chat["service"], guid=guid, attributed_body=None, item_type=item_type,
            group_action_type=action, other_handle=self.handle(other, chat["service"]) if other else 0,
            group_title=title, is_read=1, part_count=0,
        )
        self.guids[label] = guid
        self.manifest["messages"][label] = guid
        return guid

    def finish(self) -> None:
        self.conn.execute("COMMIT")
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.conn.close()


def link_payload() -> bytes:
    UID = plistlib.UID
    objects = [
        "$null",
        {"richLinkMetadata": UID(2), "$class": UID(9)},
        {"title": UID(3), "siteName": UID(4), "summary": UID(5), "URL": UID(6), "originalURL": UID(6), "$class": UID(8)},
        "Hinodeya Ramen Bar — San Francisco",
        "hinodeya.example",
        "Dashi-based ramen from Tokyo, now on Clement Street.",
        {"NS.base": UID(0), "NS.relative": UID(7), "$class": UID(10)},
        "https://www.hinodeya.example/sf",
        {"$classname": "LPLinkMetadata", "$classes": ["LPLinkMetadata", "NSObject"]},
        {"$classname": "RichLink", "$classes": ["RichLink", "NSObject"]},
        {"$classname": "NSURL", "$classes": ["NSURL", "NSObject"]},
    ]
    archive = {"$version": 100000, "$archiver": "NSKeyedArchiver", "$top": {"root": UID(1)}, "$objects": objects}
    return plistlib.dumps(archive, fmt=plistlib.FMT_BINARY)


def edit_history(old: str, new: str, old_date: float, new_date: float) -> bytes:
    return plistlib.dumps({
        "ec": {"0": [
            {"d": unix_to_apple_ns(old_date), "t": encode_attributed_body(old)},
            {"d": unix_to_apple_ns(new_date), "t": encode_attributed_body(new)},
        ]},
        "ep": [0],
    }, fmt=plistlib.FMT_BINARY)


def retraction_info(length: int) -> bytes:
    return plistlib.dumps({"rp": [0], "otr": {"0": {"le": length, "lo": 0}}}, fmt=plistlib.FMT_BINARY)


def build_story(b: ChatBuilder) -> None:
    JPEG, HEIC, MOV = "image/jpeg", "image/heic", "video/quicktime"
    photo = lambda seed, w=96, h=72: media_gen.jpeg(w, h, lambda bx, by: ((seed * 40 + bx * 20) % 256, (by * 30 + 60) % 256, (seed * 70 + 90) % 256))  # noqa: E731
    sticker_png = media_gen.png(48, 48, lambda x, y: (255, 200, 40) if (x - 24) ** 2 + (y - 24) ** 2 < 400 else (255, 255, 255))
    preview_png = media_gen.png(120, 63, lambda x, y: (200 - y, 80 + x // 2, 60))

    # -- chats --------------------------------------------------------------------------
    b.chat("jordan", "iMessage;-;+14155550134", ["jordan"])
    b.chat("jordan_sms", "SMS;-;+14155550134", ["jordan"])
    b.chat("jordan_email", "iMessage;-;jordan.park@icloud.example", ["jordan_email"])
    b.chat("priya", "iMessage;-;+14155550178", ["priya"])
    b.chat("mom", "iMessage;-;+15105550122", ["mom"])
    b.chat("dad", "any;-;+15105550123", ["dad"])
    b.chat("grace", "iMessage;-;+16285550190", ["grace"])
    b.chat("marcus", "SMS;-;+14155550161", ["marcus"])
    b.chat("ellie", "iMessage;-;+16505550147", ["ellie"])
    b.chat("sam", "iMessage;-;+14155550112", ["sam"])
    b.chat("sunset", "SMS;-;+14155550170", ["sunset"])
    b.chat("vet", "SMS;-;+14155550199", ["vet"])
    b.chat("bank", "SMS;-;24273", ["bank"])
    b.chat("ups", "SMS;-;69525", ["ups"])
    b.chat("dental", "SMS;-;36320", ["dental"])
    b.chat("self", "iMessage;-;+15105550100", ["me"])
    b.chat("family", "iMessage;+;chat100000000000000001", ["mom", "dad", "grace"], name="Liu Family 🏡", group=True)
    b.chat("soccer", "iMessage;+;chat100000000000000002", ["jordan", "tomas", "kevin", "aisha", "ben", "nate"],
           name="Sunday Soccer ⚽", group=True)
    b.chat("dinner", "iMessage;+;chat100000000000000003", ["priya", "ellie", "sam", "jordan"], name="Dinner Club 🍜", group=True)
    b.chat("trio", "iMessage;+;chat100000000000000004", ["jordan", "sam", "ellie"], group=True)

    # -- Jordan: phone, SMS and email chats that merge into one DM ------------------------------
    old = b.msg("jordan_sms", "jordan", "it's jordan, new number!", time.time(), label="j_2015", store_text=True,
                attributedBody=None)
    seconds = int(datetime(2015, 6, 12, 18, 30).timestamp() - APPLE_EPOCH)  # very old rows store seconds
    b.conn.execute("UPDATE message SET date = ?, date_read = ? WHERE guid = ?", (seconds, seconds + 60, old))
    b.conn.execute("UPDATE chat_message_join SET message_date = ? WHERE message_id = (SELECT ROWID FROM message WHERE guid = ?)", (seconds, old))
    b.msg("jordan_sms", "jordan", "hey my imessage is being weird, getting this?", b.day(20, 10, 5), store_text=True)
    b.msg("jordan_sms", "me", "yep got it, sms though", b.day(20, 10, 7), store_text=True)
    b.msg("jordan_email", "jordan_email", "did you see the warriors game last night", b.day(14, 21, 40))
    b.msg("jordan_email", "me", "insane ending. curry from half court 🤯", b.day(14, 21, 52))
    b.msg("jordan", "me", "you still have my drill?", b.day(6, 18, 20), label="j_drill")
    sent = b.day(6, 18, 31)
    b.msg("jordan", "jordan", "yes!! bringing it sunday", sent, label="j_bring", date_edited=unix_to_apple_ns(sent + 95),
          message_summary_info=edit_history("yes!! bringing it saturday", "yes!! bringing it sunday", sent, sent + 95))
    b.tapback("jordan", "me", "j_bring", 2001, b.day(6, 18, 34))
    b.msg("jordan", "jordan", "the new place 🏠", b.day(2, 12, 10), label="j_place",
          attachments=[("IMG_4821.HEIC", media_gen.heic_placeholder(4032, 3024), HEIC, "public.heic")])
    b.tapback("jordan", "me", "j_place", 2000, b.day(2, 12, 14))
    unsent_at = b.day(2, 12, 15)
    b.msg("jordan", "me", None, unsent_at, label="j_unsent", attributedBody=None, date_edited=unix_to_apple_ns(unsent_at + 30),
          date_retracted=unix_to_apple_ns(unsent_at + 30), message_summary_info=retraction_info(15))
    b.msg("jordan", "me", "congrats!! it looks amazing", b.day(2, 12, 16))
    b.msg("jordan", "jordan", "are you down to hike Saturday?", b.ago(hours=2), label="j_hike", unread=True)
    b.msg("jordan", "jordan", "can carpool from mine", b.ago(hours=1, minutes=54), label="j_carpool", unread=True)

    # -- Priya: Harry has the last word -------------------------------------------------------
    b.msg("priya", "priya", "are you coming to dinner club next week?", b.day(10, 19, 0))
    b.msg("priya", "me", "yes! wouldn't miss it", b.day(10, 19, 20))
    b.msg("priya", "priya", "here's the menu draft", b.day(5, 9, 12), label="p_menu",
          attachments=[("Dinner Club Menu.pdf", media_gen.pdf(["Dinner Club - Nov 7", "Hinodeya, Clement St", "7:00 PM"]),
                        "application/pdf", "com.adobe.pdf")])
    b.msg("priya", "priya", "can you send me that ramen place?", b.day(3, 14, 2))
    b.msg("priya", "me", "hinodeya on clement! get the dashi ramen", b.day(3, 14, 30), label="p_ramen")
    b.tapback("priya", "priya", "p_ramen", 2000, b.day(3, 14, 31), label="p_ramen_love")

    # -- Mom: voice memo, then an acknowledgement ---------------------------------------------
    b.msg("mom", "mom", "Did you get my email about Thanksgiving?", b.day(9, 8, 30))
    b.msg("mom", "me", "yes! will reply tonight", b.day(9, 9, 2))
    b.msg("mom", "mom", None, b.ago(hours=30), label="m_voice", is_audio_message=1,
          attachments=[("Audio Message.caf", media_gen.caf_tone(), "audio/x-caf", "com.apple.coreaudio-format")])
    b.msg("mom", "me", "haha thanks mom, will call you tomorrow", b.ago(hours=29, minutes=30))
    b.msg("mom", "mom", "Ok 👍", b.ago(hours=29, minutes=24))

    # -- Dad: a request without a question mark (any;-; guid) ----------------------------------
    b.msg("dad", "dad", "Can you help me set up the new printer this weekend?", b.day(12, 17, 0))
    b.msg("dad", "me", "sure, sunday afternoon?", b.day(12, 17, 45))
    b.msg("dad", "dad", "Sunday works.", b.day(12, 17, 50))
    b.msg("dad", "dad", "call me when you get a chance", b.ago(hours=5), label="d_call")

    # -- Grace ----------------------------------------------------------------------------------
    b.msg("grace", "grace", "can you pick up the pie from Tartine on Wednesday?", b.day(5, 20, 0))
    b.msg("grace", "me", "yep, I'll grab it", b.day(5, 20, 15))
    b.msg("grace", "grace", "you're the best", b.day(5, 20, 16))

    # -- Marcus (SMS only): an unanswered question, already read ------------------------------
    b.msg("marcus", "me", "Hi Marcus, it's Harry — following up about the bathroom tile quote", b.day(9, 11, 0))
    b.msg("marcus", "marcus", "Hey Harry, I'll have it by Friday", b.day(9, 13, 20))
    b.msg("marcus", "marcus", "Here's the tile sample I mentioned", b.day(4, 16, 45),
          attachments=[("tile_sample.jpg", photo(3), JPEG, "public.jpeg")])
    b.msg("marcus", "me", "looks great, let's go with that one", b.day(4, 17, 2))
    b.msg("marcus", "marcus", "Does Thursday at 9am work to start demo?", b.ago(hours=3), label="mk_demo")

    # -- Ellie: video + custom emoji tapback --------------------------------------------------
    b.msg("ellie", "ellie", "omg did you see this", b.day(7, 22, 10),
          attachments=[("IMG_0937.MOV", media_gen.mov_placeholder(1920, 1080), MOV, "com.apple.quicktime-movie")])
    b.msg("ellie", "me", "LOL", b.day(7, 22, 15), label="e_lol")
    b.tapback("ellie", "ellie", "e_lol", 2006, b.day(7, 22, 16), emoji="🔥", label="e_fire")

    # -- Sam: removed tapback and a changed one ---------------------------------------------------
    b.msg("sam", "sam", "you still want my old monitor?", b.day(11, 15, 0), label="s_monitor")
    b.msg("sam", "me", "yes please!", b.day(11, 15, 20), label="s_yes")
    b.tapback("sam", "sam", "s_yes", 2001, b.day(11, 15, 21))
    b.tapback("sam", "sam", "s_yes", 3001, b.day(11, 15, 25))
    b.tapback("sam", "me", "s_monitor", 2000, b.day(11, 15, 30))
    b.tapback("sam", "me", "s_monitor", 2003, b.day(11, 15, 31))
    b.msg("sam", "sam", "cool I'll drop it off thursday", b.day(11, 15, 40))

    # -- Sunset Auto (business card) incl. a failed send --------------------------------------------
    b.msg("sunset", "sunset", "Hi Harry, your Civic is ready for pickup. Total is $412.18.", b.day(13, 10, 0), store_text=True)
    b.msg("sunset", "me", "thanks! be there by 5", b.day(13, 10, 20))
    b.msg("sunset", "me", "running 10 min late", b.day(13, 16, 50), label="sun_failed", error=4, is_delivered=0,
          date_delivered=0)

    # -- Vet (not in Contacts, but Harry replied): closer after a request ----------------------------
    b.msg("vet", "vet", "Hi Harry, this is Mission Pet Hospital. Biscuit is due for his vaccines — would Tue Nov 3 at "
          "10:00 AM work? Reply Y to book.", b.day(3, 10, 2))
    b.msg("vet", "me", "Y", b.day(3, 10, 15))
    b.msg("vet", "vet", "Perfect, you're on the schedule for Tue Nov 3 at 10:00 AM. Can you bring his records "
          "from his last clinic", b.day(3, 10, 16), label="vet_closer")

    # -- Short codes ------------------------------------------------------------------------------
    b.msg("bank", "bank", "Mission Credit Union: A purchase of $54.20 at TRADER JOE'S was made on card ending 4417. "
          "Reply STOP to opt out.", b.day(6, 13, 5))
    b.msg("bank", "bank", "Mission Credit Union: Your one-time code is 482913. It expires in 10 minutes. Don't share "
          "it with anyone.", b.ago(minutes=25), label="bank_otp", unread=True)
    b.msg("ups", "ups", "UPS: Your package is out for delivery today. Track: https://ups.example/t/1Z999AA10123456784",
          b.ago(hours=4), label="ups_today", unread=True)
    b.msg("dental", "dental", "Reminder: Your appointment with Bay Smiles Dental is Thu Nov 5 at 2:30 PM. Reply C to "
          "confirm or call 415-555-0108.", b.day(2, 9, 0))
    b.msg("dental", "dental", "Bay Smiles Dental: Code: 4829 — use it to sign in to the patient portal.", b.day(2, 9, 1),
          label="dental_code")

    # -- Notes to self ------------------------------------------------------------------------------
    b.msg("self", "me", "note to self: renew passport before march", b.day(4, 8, 0))

    # -- Liu Family 🏡 --------------------------------------------------------------------------------
    b.event("family", "mom", 2, b.day(21, 18, 0), title="Liu Family 🏡", label="f_renamed")
    b.msg("family", "mom", "Made us a family chat! 🏡", b.day(21, 18, 1))
    b.msg("family", "dad", "👍", b.day(21, 18, 5))
    b.msg("family", "dad", "Found this old one of you two", b.day(8, 10, 0), label="f_missing",
          attachments=[("IMG_2210.JPG", None, JPEG, "public.jpeg")])
    b.msg("family", "mom", "Thanksgiving planning thread! Who's hosting this year?", b.day(6, 19, 0), label="f_root")
    b.msg("family", "grace", "I can host! We finally have the big table", b.day(6, 19, 10), thread="f_root")
    b.msg("family", "dad", "Grace's it is. I'll bring the turkey 🦃", b.day(6, 19, 12), thread="f_root")
    b.msg("family", "me", "I'll do pies + wine", b.day(6, 19, 30), thread="f_root", label="f_pies")
    b.msg("family", "mom", "What time should we plan to eat?", b.day(5, 8, 0), thread="f_root")
    b.msg("family", "grace", "3pm?", b.day(5, 8, 20), thread="f_root")
    b.msg("family", "grace", "here's the table!", b.day(5, 8, 25), thread="f_root", label="f_table",
          attachments=[("IMG_5502.JPG", photo(7, 128, 96), JPEG, "public.jpeg")])
    b.msg("family", "mom", "Grandma's stuffing recipe", b.day(4, 21, 0), thread="f_root",
          attachments=[("recipe_card.png", media_gen.png(80, 60, lambda x, y: (250, 240 - y, 220 - x)), "image/png", "public.png")])
    b.msg("family", "dad", "Perfect", b.day(4, 21, 5), thread="f_root")
    b.msg("family", "grace", "I GOT THE JOB!!!", b.day(4, 12, 0), label="f_job",
          effect="com.apple.MobileSMS.expressivesend.impact")
    b.tapback("family", "mom", "f_job", 2004, b.day(4, 12, 1))
    b.tapback("family", "dad", "f_job", 2000, b.day(4, 12, 2))
    b.tapback("family", "me", "f_job", 2000, b.day(4, 12, 3))
    b.msg("family", "mom", "So proud of you sweetie!! ❤️", b.day(4, 12, 4))
    b.msg("family", "dad", "Does anyone know where the Costco card is?", b.ago(hours=26))
    b.msg("family", "mom", "In the kitchen drawer", b.ago(hours=25, minutes=30))
    chairs = "Harry can you bring your folding chairs too?"
    b.msg("family", "grace", chairs, b.ago(minutes=40), thread="f_root", label="f_chairs", unread=True,
          mentions=[(0, 5, ME_PHONE)])

    # -- Sunday Soccer ⚽ ------------------------------------------------------------------------------
    b.event("soccer", "jordan", 1, b.day(15, 9, 0), action=0, other="nate", label="soc_added")
    b.msg("soccer", "kevin", "great game today 🔥", b.day(14, 13, 30))
    b.msg("soccer", "aisha", "my legs are dead", b.day(14, 13, 35), label="soc_legs")
    b.tapback("soccer", "me", "soc_legs", 2003, b.day(14, 13, 40))
    b.msg("soccer", "nate", "thanks for having me guys", b.day(14, 13, 45))
    b.msg("soccer", "jordan", "who's in Sunday? 9am at Kezar", b.ago(hours=15), label="soc_root")
    b.msg("soccer", "tomas", "in", b.ago(hours=14, minutes=54), thread="soc_root", label="soc_tomas_in")
    b.msg("soccer", "kevin", "in 💪", b.ago(hours=14, minutes=48), thread="soc_root")
    b.msg("soccer", "aisha", "in but might be 10 min late", b.ago(hours=14, minutes=30), thread="soc_root")
    b.msg("soccer", "ben", "out this week, sorry 😩", b.ago(hours=14), thread="soc_root")
    b.tapback("soccer", "kevin", "soc_root", 2007, b.ago(hours=13, minutes=54), sticker=sticker_png, label="soc_sticker")
    b.tapback("soccer", "ben", "soc_root", 2002, b.ago(hours=13, minutes=48))
    b.tapback("soccer", "jordan", "soc_tomas_in", 2001, b.ago(hours=13, minutes=30))
    b.msg("soccer", "me", "I can bring the new ball + pump", b.ago(hours=12), label="soc_ball")
    b.msg("soccer", "nate", "bringing a friend if that's ok?", b.ago(hours=3), thread="soc_root", label="soc_friend")
    b.tapback("soccer", "tomas", "soc_friend", 2005, b.ago(hours=2, minutes=54))
    b.msg("soccer", "tomas", "yes pls, ours is flat", b.ago(hours=2, minutes=12), thread="soc_ball",
          label="soc_ball_reply", unread=True)

    # -- Dinner Club 🍜 ---------------------------------------------------------------------------------
    b.msg("dinner", "priya", "next dinner club: Nov 7 at 7. booked this place", b.day(9, 18, 0))
    b.msg("dinner", "priya", "https://www.hinodeya.example/sf", b.day(9, 18, 1), label="d_link",
          balloon_bundle_id="com.apple.messages.URLBalloonProvider", payload_data=link_payload(),
          attachments=[(f"{det_uuid('preview')}.pluginPayloadAttachment", preview_png, None, "dyn.age80q2pssvu1g8pftb4gn5xxrf106")])
    b.msg("dinner", "ellie", "yesss", b.day(9, 18, 10))
    b.msg("dinner", "sam", "I'm in", b.day(9, 18, 12))
    b.msg("dinner", "jordan", "🙌", b.day(9, 18, 15))
    b.msg("dinner", "me", "count me in", b.day(9, 18, 30))
    unsent = b.day(2, 20, 0)
    b.msg("dinner", "sam", None, unsent, label="d_unsent", attributedBody=None,
          date_edited=unix_to_apple_ns(unsent + 40), message_summary_info=retraction_info(18))
    b.msg("dinner", "sam", "nvm wrong chat lol", b.day(2, 20, 5))
    sake = "Harry are you bringing the sake again? 🍶"
    b.msg("dinner", "ellie", sake, b.ago(hours=1), label="d_sake", unread=True, mentions=[(0, 5, ME_PHONE)])

    # -- An unnamed group ---------------------------------------------------------------------------------
    b.msg("trio", "sam", "surprise party for priya — sat the 14th?", b.day(16, 19, 0))
    b.msg("trio", "jordan", "yes! I can do decorations", b.day(16, 19, 5))
    b.msg("trio", "ellie", "I'll handle the cake 🎂", b.day(16, 19, 10))
    b.msg("trio", "me", "I'll get her a gift from all of us", b.day(16, 19, 20))
    b.msg("trio", "sam", "perfect", b.day(16, 19, 22))
    b.msg("trio", "jordan", "reminder: don't tell priya 🤫", b.day(3, 11, 0))


def write_chief(out: Path) -> None:
    """A small chief.db (Harry's chief-of-staff app) with people, loops and facts."""
    path = out / "chief.db"
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(CHIEF_SCHEMA)
    conn.executemany(
        "INSERT INTO people (name, relationship, owner, birthday, notes) VALUES (?, ?, 'harry', ?, ?)",
        [
            ("Jordan Park", "friend", "03-03", "Climbing buddy. Lent Harry the camping stove."),
            ("Mom", "family", "1962-05-14", "Prefers a call over a text."),
            ("Priya Natarajan", "friend", None, None),
            ("Ellie", "friend", "11-21", None),          # first name only: matches Ellie Chen
            ("Ben", "colleague", None, None),             # two Bens: no first-name match for Ben Carter
            ("Ben Okafor", "friend", None, None),
        ],
    )
    conn.executemany(
        "INSERT INTO open_loops (title, owner, urgency, category, status, due_date, created_at) VALUES (?, 'harry', ?, ?, ?, ?, ?)",
        [
            ("Get the camping stove back from Jordan", "medium", "imessage", "open", None, "2026-10-20 09:00:00"),
            ("Plan Jordan's birthday dinner", "low", "social", "in_progress", "2027-03-01", "2026-10-25 18:30:00"),
            ("Return Jordan's drill", "low", "home", "done", None, "2026-10-01 12:00:00"),
            ("Jordanian visa form for the spring trip", "high", "admin", "open", None, "2026-10-26 08:00:00"),
            ("Book flights for Mom's visit", "high", "family", "open", "2026-11-15", "2026-10-28 20:00:00"),
            ("Renew car registration", "medium", "vehicle", "open", "2026-12-01", "2026-10-29 10:00:00"),
        ],
    )
    conn.executemany(
        "INSERT INTO profile_facts (id, owner, category, fact, source, superseded_by, captured_at) VALUES (?, 'external', 'people', ?, 'tell_cli', ?, ?)",
        [
            (1, "Mom moved to Oakland in 2019.", 2, "2026-08-01 09:00:00"),
            (2, "Mom lives in San Leandro now.", None, "2026-08-02 09:00:00"),
            (3, "Mom is allergic to shellfish.", None, "2026-09-01 09:00:00"),
            (4, "Mom's favorite flowers are peonies.", None, "2026-10-01 09:00:00"),
            (5, "Jordan Park is vegetarian.", None, "2026-10-05 09:00:00"),
            (6, "Harry's birthday is December 26.", None, "2026-07-01 09:00:00"),
        ],
    )
    conn.commit()
    conn.close()


def generate(out: Path = DEFAULT_OUT, now: datetime | None = None) -> dict[str, Any]:
    """(Re)create fixtures in `out`; returns the manifest."""
    out = Path(out)
    fakedb.guard_path(out / "chat.db")
    out.mkdir(parents=True, exist_ok=True)
    for name in ("chat.db", "chat.db-wal", "chat.db-shm", "manifest.json"):
        (out / name).unlink(missing_ok=True)
    for folder in ("AddressBook", "Attachments"):
        shutil.rmtree(out / folder, ignore_errors=True)
    for stale in (out / "state").glob("search.db*") if (out / "state").exists() else []:
        stale.unlink()  # row ids change between generations

    now = now or datetime.now()
    unique_ids = write_addressbook(out)
    write_chief(out)
    builder = ChatBuilder(out, now)
    build_story(builder)
    builder.finish()

    (out / "texting_style.json").write_text(json.dumps({
        "profile": ("Harry texts casually and mostly in lowercase, with short one-line messages and little "
                    "punctuation. He uses 'haha', 'lol' and the occasional 🤯 or 🙌, says 'pls' and 'tho', and "
                    "skips greetings. Excited replies get a double exclamation ('congrats!!')."),
        "last_updated": now.isoformat(),
        "sample_count": 42,
    }, indent=2))
    manifest = builder.manifest
    manifest.update({
        "generated_at": now.isoformat(),
        "me": {"name": ME_NAME, "handles": [ME_PHONE, ME_EMAIL]},
        "handles": HANDLES,
        "contacts": unique_ids,
    })
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"output folder (default {DEFAULT_OUT})")
    args = parser.parse_args()
    manifest = generate(args.out)
    print(f"Wrote fixtures to {args.out} ({len(manifest['messages'])} messages, {len(manifest['chats'])} chats)")


if __name__ == "__main__":
    main()
