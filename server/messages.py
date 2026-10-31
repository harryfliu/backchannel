"""Build `Message` JSON (web/src/api/types.ts) from chat.db rows.

Rows are built in batches so attachments, reactions, thread summaries and
local state are each one query per page. Tapback rows never become messages;
they are folded into their target's `reactions` in date order per reactor
(an add replaces that person's previous tapback; a 300x removes it).
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence
from urllib.parse import quote

from . import heuristics as H
from .chatdb import ChatDB
from .conversations import (
    Conv,
    ConversationService,
    Directory,
    NotFound,
    event_line,
    is_unsent,
    message_text,
    normalize_service,
)
from .linkpreview import link_preview
from .media import Media, kind_of
from .state import State
from .timeutil import apple_to_ns, iso_apple, now_apple_ns, parse_iso
from .typedstream import extract_mentions

log = logging.getLogger("backchannel.messages")

DAY_NS = 86400 * 1_000_000_000
_CURSOR = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?$")
_REACTED = re.compile(r"^Reacted (.+?) to ")


def parse_cursor(value: str | None, parts: int = 2) -> tuple[int, ...] | None:
    if not value:
        return None
    match = _CURSOR.match(value.strip())
    if not match or (parts == 3) != bool(match.group(3)):
        raise ValueError("That page cursor isn't valid. Reload the conversation.")
    return tuple(int(g) for g in match.groups() if g is not None)


def target_guid(assoc: str | None) -> str | None:
    """'p:0/GUID' or 'bp:GUID' -> GUID."""
    if not assoc:
        return None
    if "/" in assoc:
        return assoc.split("/", 1)[1]
    if assoc.startswith("bp:"):
        return assoc[3:]
    return assoc


def attachment_url(guid: str) -> str:
    return f"/api/attachments/{quote(guid, safe='')}"


@dataclass
class _Context:
    attachments: dict[int, list[Any]] = field(default_factory=dict)
    reactions: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    threads: dict[str, dict[str, Any]] = field(default_factory=dict)
    local_roots: dict[str, str] = field(default_factory=dict)
    client_ids: dict[str, str] = field(default_factory=dict)
    automated: dict[str, bool] = field(default_factory=dict)


class MessageBuilder:
    def __init__(self, db: ChatDB, convs: ConversationService, state: State, media: Media):
        self.db = db
        self.convs = convs
        self.state = state
        self.media = media

    # ------------------------------------------------------------------ attachments

    def is_plugin_payload(self, att: Any) -> bool:
        name = att["transfer_name"] or os.path.basename(att["filename"] or "")
        return name.endswith(".pluginPayloadAttachment")

    def attachment_json(self, att: Any) -> dict[str, Any]:
        name = att["transfer_name"] or os.path.basename(att["filename"] or "") or "Attachment"
        kind = kind_of(att["mime_type"], att["uti"], name)
        path = self.media.resolve(att["filename"])
        exists = bool(path and path.is_file())
        width, height = self.media.dimensions(path, kind) if exists and path else (None, None)
        url = attachment_url(att["guid"])
        thumb = None
        if exists and kind == "image" and self.media.drawable(path):
            thumb = url + "/thumb"  # a browser image, or one we can convert (HEIC/TIFF on macOS)
        elif exists and kind == "video" and self.media.video_thumbnails:
            thumb = url + "/thumb"
        return {
            "id": att["guid"],
            "kind": kind,
            "mimeType": att["mime_type"],
            "name": name,
            "size": att["total_bytes"] or None,
            "url": url,
            "thumbUrl": thumb,
            "width": width,
            "height": height,
            "isSticker": bool(att["is_sticker"]),
            # iOS 18+ Genmoji carry a content identifier; stickers and Genmoji may carry a description.
            "isGenmoji": bool(att["emoji_image_content_identifier"]),
            "emojiDescription": (att["emoji_image_short_description"] or "").strip() or None,
            "missing": not exists,
        }

    def attachment_kinds(self, message_ids: list[int]) -> dict[int, list[tuple[str, str, bool, bool, bool]]]:
        """(kind, name, hidden, is_sticker, is_genmoji) per message: what previews need."""
        out: dict[int, list[tuple[str, str, bool, bool, bool]]] = {}
        if not message_ids:
            return out
        for mid, atts in self.db.attachments_for(message_ids).items():
            for att in atts:
                name = att["transfer_name"] or os.path.basename(att["filename"] or "")
                hidden = bool(att["hide_attachment"]) or self.is_plugin_payload(att)
                out.setdefault(mid, []).append(
                    (kind_of(att["mime_type"], att["uti"], name), name, hidden, bool(att["is_sticker"]),
                     bool(att["emoji_image_content_identifier"]))
                )
        return out

    # ------------------------------------------------------------------ reactions

    def _reactions(self, rows: Sequence[Any], attachments: dict[int, list[Any]], directory: Directory) -> dict[str, list[dict]]:
        candidates: dict[str, str] = {}
        for row in rows:
            guid = row["guid"]
            parts = max(1, row["part_count"] or 0, len(attachments.get(row["ROWID"], [])) + 1)
            for i in range(parts + 1):
                candidates[f"p:{i}/{guid}"] = guid
            candidates[f"bp:{guid}"] = guid
        if not candidates:
            return {}
        tapbacks = self.db.reaction_rows(list(candidates))
        stickers = [t["ROWID"] for t in tapbacks if (t["associated_message_type"] or 0) in (1000, 2007)]
        sticker_files = self.db.attachments_for(stickers) if stickers else {}
        sticker_files = {rowid: [a for a in atts if self.media.drawable(self.media.resolve(a["filename"]))]
                         for rowid, atts in sticker_files.items()}
        return fold_reactions(tapbacks, candidates, directory, sticker_files)

    # ------------------------------------------------------------------ threads

    def _threads(self, guids: Sequence[str], directory: Directory) -> dict[str, dict[str, Any]]:
        if not guids:
            return {}
        replies: dict[str, list[tuple[int, str]]] = {}
        native: set[str] = set()
        for row in self.db.thread_reply_rows(list(guids)):
            who = "me" if row["is_from_me"] else (directory.handle_norm(row["handle_id"]) or "unknown")
            replies.setdefault(row["thread_originator_guid"], []).append((apple_to_ns(row["date"]), who))
            native.add(row["guid"])
        local = self.state.local_replies(list(guids))
        local_guids = [g for gs in local.values() for g in gs if g not in native]
        if local_guids:
            by_guid = {r["guid"]: r for r in self.db.messages_by_guid(local_guids)}
            for root, sent in local.items():
                for g in sent:
                    row = by_guid.get(g)
                    if row is not None and not row["thread_originator_guid"]:
                        replies.setdefault(root, []).append((apple_to_ns(row["date"]), "me"))
        out: dict[str, dict[str, Any]] = {}
        for root, items in replies.items():
            items.sort(reverse=True)
            people: list[str] = []
            for _, who in items:
                if who not in people:
                    people.append(who)
            out[root] = {
                "replyCount": len(items),
                "participants": people[:5],
                "lastReplyAt": iso_apple(items[0][0]),
            }
        return out

    # ------------------------------------------------------------------ building

    def build(self, rows: Iterable[Any]) -> list[dict[str, Any]]:
        rows = [r for r in rows if not H.is_reaction_type(r["associated_message_type"])]
        if not rows:
            return []
        directory = self.convs.directory()
        ctx = _Context()
        ctx.attachments = self.db.attachments_for([r["ROWID"] for r in rows if r["cache_has_attachments"]])
        ctx.reactions = self._reactions(rows, ctx.attachments, directory)
        guids = [r["guid"] for r in rows]
        ctx.threads = self._threads(guids, directory)
        ctx.local_roots = self.state.thread_roots_for(guids)
        ctx.client_ids = self.state.client_ids_for(guids)
        out = []
        for row in rows:
            try:
                message = self._message(row, ctx, directory)
            except Exception:  # one odd row shouldn't break the page
                log.exception("Couldn't build message %s", row["guid"])
                continue
            if message is not None:
                out.append(message)
        return out

    def _automated(self, conv: Conv | None, ctx: _Context) -> bool:
        if conv is None:
            return False
        if conv.id not in ctx.automated:
            ctx.automated[conv.id] = self.convs.is_automated(conv)
        return ctx.automated[conv.id]

    def _message(self, row: Any, ctx: _Context, directory: Directory) -> dict[str, Any] | None:
        me = directory.me
        conv = directory.by_chat.get(row["chat_id"]) if row["chat_id"] is not None else None
        from_me = bool(row["is_from_me"])
        sender = None
        if not from_me:
            sender = directory.handle_norm(row["handle_id"])
            if not sender and conv is not None and conv.kind == "dm" and conv.persons:
                sender = conv.persons[0].handle
        text = message_text(row)

        event = None
        if row["item_type"]:
            line = event_line(row, directory)
            if line is None:
                return None
            event = {"type": line[0], "text": line[1]}
            text = line[1]

        visible: list[dict[str, Any]] = []
        preview_image = None
        for att in ctx.attachments.get(row["ROWID"], []):
            if self.is_plugin_payload(att):
                if preview_image is None and self.media.drawable(self.media.resolve(att["filename"])):
                    preview_image = attachment_url(att["guid"])
                continue
            if att["hide_attachment"]:
                continue
            visible.append(self.attachment_json(att))

        unsent = event is None and is_unsent(row, text, bool(visible))
        if unsent:
            text = ""
            visible = []
        link = None if unsent else link_preview(row["balloon_bundle_id"], row["payload_data"], text, preview_image)
        if event is None and not unsent and not text and not visible and not link:
            return None  # nothing to show (typing artefacts, empty plugin rows…)

        status = None
        if from_me:
            if row["error"]:
                status = "failed"
            elif row["date_read"]:
                status = "read"
            elif row["is_delivered"] or row["date_delivered"]:
                status = "delivered"
            else:
                status = "sent"

        mentions = extract_mentions(row["attributedBody"])
        mentions_me = not from_me and (any(me.is_me(h) for h in mentions) or bool(row["has_unseen_mention"]))

        otp = None
        if not from_me and text and (self._automated(conv, ctx) or not directory.person(sender).is_contact):
            otp = H.extract_otp(text)

        guid = row["guid"]
        return {
            "id": guid,
            "conversationId": conv.id if conv else f"chat:{row['chat_id']}",
            "senderHandle": sender,
            "fromMe": from_me,
            "text": text,
            "sentAt": iso_apple(row["date"]),
            "service": normalize_service(row["service"], conv.chats[0].service if conv and conv.chats else "iMessage"),
            "attachments": visible,
            "reactions": ctx.reactions.get(guid, []),
            "linkPreview": link,
            "threadRootId": row["thread_originator_guid"] or ctx.local_roots.get(guid) or None,
            "thread": ctx.threads.get(guid),
            "editedAt": iso_apple(row["date_edited"]) if row["date_edited"] and not unsent and event is None else None,
            "unsent": unsent,
            "status": status,
            "readAt": iso_apple(row["date_read"]) if from_me and row["date_read"] else None,
            "mentionsMe": mentions_me,
            "effect": H.effect_name(row["expressive_send_style_id"]),
            "isAudioMessage": bool(row["is_audio_message"]),
            "otpCode": otp,
            "event": event,
            "clientId": ctx.client_ids.get(guid),
        }

    # ------------------------------------------------------------------ lookups

    def by_guid(self, guid: str) -> dict[str, Any] | None:
        built = self.build(self.db.messages_by_guid([guid]))
        return built[0] if built else None

    def by_guids(self, guids: Sequence[str]) -> dict[str, dict[str, Any]]:
        return {m["id"]: m for m in self.build(self.db.messages_by_guid(list(guids)))}

    def by_rowids(self, rowids: Sequence[int]) -> list[dict[str, Any]]:
        rows = self.db.messages_by_rowid(list(rowids))
        rows.sort(key=lambda r: (apple_to_ns(r["date"]), r["ROWID"]))
        return self.build(rows)

    # ------------------------------------------------------------------ pages

    def page(self, conv: Conv, before: str | None, limit: int) -> dict[str, Any]:
        cursor = parse_cursor(before)
        ids = self.db.page_ids(conv.chat_ids, cursor, limit + 1)  # type: ignore[arg-type]
        more = len(ids) > limit
        ids = ids[:limit]
        messages = self.by_rowids([i for _, i in ids])
        next_cursor = f"{ids[-1][0]}.{ids[-1][1]}" if more and ids else None
        return {"messages": messages, "nextCursor": next_cursor}

    def recent(self, conv: Conv, count: int) -> list[dict[str, Any]]:
        ids = self.db.page_ids(conv.chat_ids, None, count)
        return self.by_rowids([i for _, i in ids])

    def thread(self, conv: Conv, root_guid: str) -> dict[str, Any]:
        roots = self.db.messages_by_guid([root_guid])
        if not roots or roots[0]["chat_id"] not in conv.chat_ids:
            raise NotFound("That message isn't in this conversation.")
        reply_ids = [r["ROWID"] for r in self.db.thread_reply_rows([root_guid])]
        local = self.state.local_replies([root_guid]).get(root_guid, [])
        if local:
            reply_ids += [r["ROWID"] for r in self.db.messages_by_guid(local)]
        built = self.by_rowids(sorted(set(reply_ids)) + [roots[0]["ROWID"]])
        root = next((m for m in built if m["id"] == root_guid), None)
        if root is None:
            raise NotFound("That message can't be shown.")
        replies = [m for m in built if m["id"] != root_guid]
        return {"root": root, "replies": replies}

    def media_page(self, conv: Conv, before: str | None, limit: int) -> dict[str, Any]:
        cursor = parse_cursor(before, parts=3)
        rows = self.db.media_rows(conv.chat_ids, cursor, limit * 2 + 1)  # type: ignore[arg-type]
        directory = self.convs.directory()
        items: list[dict[str, Any]] = []
        last: tuple[int, int, int] | None = None
        more = False
        for row in rows:
            if len(items) == limit:
                more = True
                break
            last = (row["mdate"] or 0, row["mid"], row["ROWID"])
            if self.is_plugin_payload(row):
                continue
            sender = None if row["from_me"] else directory.handle_norm(row["handle_id"])
            if not row["from_me"] and not sender and conv.kind == "dm" and conv.persons:
                sender = conv.persons[0].handle
            items.append({
                "attachment": self.attachment_json(row),
                "messageId": row["mguid"],
                "senderHandle": sender,
                "sentAt": iso_apple(row["date"]),
            })
        if not more and len(rows) > limit * 2:
            more = True
        next_cursor = f"{last[0]}.{last[1]}.{last[2]}" if more and last else None
        return {"items": items, "nextCursor": next_cursor}

    # ------------------------------------------------------------------ activity & threads

    def activity(self, limit: int, days: int = 30) -> list[dict[str, Any]]:
        directory = self.convs.directory()
        me = directory.me
        since = now_apple_ns() - days * DAY_NS
        events: list[dict[str, Any]] = []

        # Reactions to the user's messages that are still in place.
        tapbacks = self.db.recent_rows(
            since, "m.is_from_me = 0 AND m.associated_message_type BETWEEN 2000 AND 2007"
        )
        target_ids = {t["guid"]: target_guid(t["associated_message_guid"]) for t in tapbacks}
        targets = {r["guid"]: r for r in self.db.messages_by_guid([g for g in set(target_ids.values()) if g])}
        mine = {g: r for g, r in targets.items() if r["is_from_me"]}
        if mine:
            final = self._reactions(list(mine.values()), self.db.attachments_for(
                [r["ROWID"] for r in mine.values() if r["cache_has_attachments"]]), directory)
            for tb in tapbacks:
                tg = target_ids.get(tb["guid"])
                if tg not in mine:
                    continue
                actor = directory.handle_norm(tb["handle_id"]) or "unknown"
                kind, emoji, _ = H.TAPBACKS[tb["associated_message_type"]]
                if tb["associated_message_type"] == 2006:
                    emoji = tb["associated_message_emoji"] or emoji
                still = any(r["kind"] == kind and actor in r["from"] and (kind != "emoji" or r["emoji"] == emoji)
                            for r in final.get(tg, []))
                if not still:
                    continue
                events.append({
                    "id": tb["guid"], "type": "reaction", "target": tg, "actorHandle": actor,
                    "date": apple_to_ns(tb["date"]),
                    "reaction": {"kind": kind, "emoji": emoji, "from": [actor]},
                })

        # Mentions of the user.
        mention_rows = self.db.recent_rows(
            since, "m.is_from_me = 0 AND (m.has_unseen_mention = 1 OR instr(m.attributedBody, ?) > 0)",
            (b"__kIMMentionConfirmedMention",),
        ) if "has_unseen_mention" in self.db.columns("message") else self.db.recent_rows(
            since, "m.is_from_me = 0 AND instr(m.attributedBody, ?) > 0", (b"__kIMMentionConfirmedMention",)
        )
        for row in mention_rows:
            if H.is_reaction_type(row["associated_message_type"]):
                continue
            handles = extract_mentions(row["attributedBody"])
            if row["has_unseen_mention"] or any(me.is_me(h) for h in handles):
                events.append({
                    "id": row["guid"], "type": "mention", "target": row["guid"],
                    "actorHandle": directory.handle_norm(row["handle_id"]) or "unknown",
                    "date": apple_to_ns(row["date"]),
                })

        # Replies in threads the user started or replied in.
        replies = [r for r in self.db.recent_rows(
            since, "m.is_from_me = 0 AND m.thread_originator_guid IS NOT NULL AND m.thread_originator_guid != ''"
        ) if not H.is_reaction_type(r["associated_message_type"])]
        roots = list({r["thread_originator_guid"] for r in replies})
        # When the user joined each thread: the root's date if they started it, else their first reply.
        joined: dict[str, int] = {}
        if roots:
            for r in self.db.messages_by_guid(roots):
                if r["is_from_me"]:
                    joined[r["guid"]] = apple_to_ns(r["date"])
            for r in self.db.thread_reply_rows(roots):
                if r["is_from_me"]:
                    when = apple_to_ns(r["date"])
                    joined[r["thread_originator_guid"]] = min(joined.get(r["thread_originator_guid"], when), when)
            local = self.state.local_replies(roots)
            if local:
                for r in self.db.messages_by_guid([g for gs in local.values() for g in gs]):
                    root = next((k for k, gs in local.items() if r["guid"] in gs), None)
                    if root:
                        when = apple_to_ns(r["date"])
                        joined[root] = min(joined.get(root, when), when)
        mentioned = {e["target"] for e in events if e["type"] == "mention"}
        for row in replies:
            root = row["thread_originator_guid"]
            if root in joined and apple_to_ns(row["date"]) > joined[root] and row["guid"] not in mentioned:
                events.append({
                    "id": row["guid"], "type": "thread_reply", "target": row["guid"],
                    "actorHandle": directory.handle_norm(row["handle_id"]) or "unknown",
                    "date": apple_to_ns(row["date"]),
                })

        events.sort(key=lambda e: e["date"], reverse=True)
        events = events[:limit]
        built = self.by_guids([e["target"] for e in events])
        out = []
        for e in events:
            message = built.get(e["target"])
            if message is None:
                continue
            item = {
                "id": e["id"],
                "type": e["type"],
                "conversationId": message["conversationId"],
                "actorHandle": e["actorHandle"],
                "at": iso_apple(e["date"]),
                "message": message,
            }
            if "reaction" in e:
                item["reaction"] = e["reaction"]
            out.append(item)
        return out

    def thread_roots(self, limit: int, days: int = 30) -> list[dict[str, Any]]:
        since = now_apple_ns() - days * DAY_NS
        roots = [r["root"] for r in self.db.recent_thread_roots(since, limit * 2)]
        for row in self.state.recent_local_threads(time.time() - days * 86400):
            if row["thread_root_id"] not in roots:
                roots.append(row["thread_root_id"])
        built = [m for m in self.by_guids(roots).values() if m.get("thread")]
        built.sort(key=lambda m: parse_iso(m["thread"]["lastReplyAt"]), reverse=True)
        return built[:limit]


def fold_reactions(
    tapbacks: Sequence[Any],
    target_of: dict[str, str],
    directory: Directory,
    sticker_files: dict[int, list[Any]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Apply tapback adds/removes in date order and group the survivors by emoji."""
    current: dict[str, dict[str, tuple[str, str, int]]] = {}
    stickers: dict[str, list[tuple[str, int, int]]] = {}
    for tb in tapbacks:
        target = target_of.get(tb["associated_message_guid"]) or target_guid(tb["associated_message_guid"])
        if not target:
            continue
        kind_type = tb["associated_message_type"] or 0
        reactor = "me" if tb["is_from_me"] else (directory.handle_norm(tb["handle_id"]) or "unknown")
        when = apple_to_ns(tb["date"])
        per_target = current.setdefault(target, {})
        if 2000 <= kind_type <= 2006:
            kind, emoji, _ = H.TAPBACKS[kind_type]
            if kind_type == 2006:
                emoji = tb["associated_message_emoji"] or _emoji_from_text(tb) or "❔"
            per_target[reactor] = (kind, emoji, when)
        elif 3000 <= kind_type <= 3006:
            kind = H.TAPBACKS[kind_type - 1000][0]
            existing = per_target.get(reactor)
            if existing and existing[0] == kind:
                emoji = tb["associated_message_emoji"]
                if kind != "emoji" or not emoji or emoji == existing[1]:
                    per_target.pop(reactor, None)
        elif kind_type in (1000, 2007):
            stickers.setdefault(target, []).append((reactor, tb["ROWID"], when))
        elif kind_type == 3007:
            mine = [s for s in stickers.get(target, []) if s[0] == reactor]
            if mine:
                stickers[target].remove(mine[-1])

    out: dict[str, list[dict[str, Any]]] = {}
    for target in set(current) | set(stickers):
        groups: dict[tuple[str, str], dict[str, Any]] = {}
        order: list[tuple[int, tuple[str, str]]] = []
        for reactor, (kind, emoji, when) in sorted(current.get(target, {}).items(), key=lambda kv: kv[1][2]):
            key = (kind, emoji)
            if key not in groups:
                groups[key] = {"kind": kind, "emoji": emoji, "from": []}
                order.append((when, key))
            groups[key]["from"].append(reactor)
        placed = stickers.get(target, [])
        if placed:
            key = ("sticker", H.STICKER_EMOJI)
            group = {"kind": "sticker", "emoji": H.STICKER_EMOJI, "from": []}
            for reactor, rowid, _ in placed:
                if reactor not in group["from"]:
                    group["from"].append(reactor)
                files = (sticker_files or {}).get(rowid)
                if files and "stickerUrl" not in group:
                    group["stickerUrl"] = attachment_url(files[0]["guid"])
            groups[key] = group
            order.append((placed[0][2], key))
        reactions = [groups[key] for _, key in sorted(order)]
        if reactions:
            out[target] = reactions
    return out


def _emoji_from_text(row: Any) -> str | None:
    text = message_text(row)
    match = _REACTED.match(text or "")
    return match.group(1) if match else None
