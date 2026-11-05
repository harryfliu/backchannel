"""Message building: reactions, threads, edits/unsends, events, attachments, pages."""

from __future__ import annotations

from urllib.parse import quote

from server.timeutil import parse_iso

SOCCER = "g:chat100000000000000002"
FAMILY = "g:chat100000000000000001"


def reactions(message):
    return {(r["kind"], r["emoji"]): r["from"] for r in message["reactions"]}


def test_tapback_rows_are_never_messages(ro):
    for conv_id in ro.conversations():
        for message in ro.all_messages(conv_id, limit=60):
            assert not message["text"].startswith(("Loved “", "Liked “", "Reacted ", "Removed ")), message


def test_every_kind_of_tapback(ro):
    assert reactions(ro.message("j_bring")) == {("like", "👍"): ["me"]}
    assert reactions(ro.message("f_job")) == {("emphasize", "‼️"): ["+15105550122"],
                                              ("love", "❤️"): ["+15105550123", "me"]}
    assert reactions(ro.message("soc_legs")) == {("laugh", "😂"): ["me"]}
    assert reactions(ro.message("soc_friend")) == {("question", "❓"): ["+14155550118"]}
    root = ro.message("soc_root")
    assert reactions(root) == {("sticker", "🖼️"): ["+14155550126"], ("dislike", "👎"): ["+14155550143"]}
    sticker = next(r for r in root["reactions"] if r["kind"] == "sticker")
    assert sticker["stickerUrl"].startswith("/api/attachments/")
    assert reactions(ro.message("e_lol")) == {("emoji", "🔥"): ["+16505550147"]}


def test_removed_and_replaced_tapbacks(ro):
    assert ro.message("s_yes")["reactions"] == []  # liked, then the like was removed
    assert reactions(ro.message("s_monitor")) == {("laugh", "😂"): ["me"]}  # love replaced by laugh


def test_threads(ro):
    root = ro.message("f_root")
    assert root["thread"]["replyCount"] == 9
    assert root["thread"]["participants"] == ["+16285550190", "+15105550123", "+15105550122", "me"]
    data = ro.get(f"/api/conversations/{quote(FAMILY, safe='')}/threads/{ro.guid('f_root')}")
    assert data["root"]["id"] == ro.guid("f_root")
    assert [r["text"] for r in data["replies"]][:3] == [
        "I can host! We finally have the big table", "Grace's it is. I'll bring the turkey 🦃", "I'll do pies + wine"]
    assert all(r["threadRootId"] == ro.guid("f_root") for r in data["replies"])
    timeline = ro.all_messages(FAMILY, limit=60)
    assert sum(1 for m in timeline if m["threadRootId"] == ro.guid("f_root")) == 9  # replies stay in the timeline
    # A reply to the user's own message makes it a root too.
    assert ro.message("soc_ball")["thread"]["participants"] == ["+14155550118"]


def test_thread_404s(ro):
    response = ro.client.get(f"/api/conversations/{quote(SOCCER, safe='')}/threads/{ro.guid('f_root')}")
    assert response.status_code == 404 and "error" in response.json()


def test_thread_roots_list(ro):
    items = ro.get("/api/threads")["items"]
    assert {m["id"] for m in items} == {ro.guid(label) for label in ("f_root", "cc_mine", "soc_ball", "soc_root")}
    assert [m["id"] for m in items][-2:] == [ro.guid("soc_ball"), ro.guid("soc_root")]
    stamps = [m["thread"]["lastReplyAt"] for m in items]
    assert stamps == sorted(stamps, reverse=True)


def test_edited_and_unsent(ro):
    bring = ro.message("j_bring")
    assert bring["text"] == "yes!! bringing it sunday" and bring["editedAt"] and not bring["unsent"]
    unsent = ro.message("j_unsent")  # date_retracted set
    assert unsent["unsent"] and unsent["text"] == "" and unsent["editedAt"] is None and unsent["fromMe"]
    sam = ro.message("d_unsent")  # only message_summary_info says it was retracted
    assert sam["unsent"] and sam["text"] == ""


def test_group_events(ro):
    renamed = ro.message("f_renamed")
    assert renamed["event"] == {"type": "renamed", "text": "Mom named the conversation “Liu Family 🏡”"}
    added = ro.message("soc_added")
    assert added["event"] == {"type": "added", "text": "Jordan added (415) 555-0155"}
    assert added["senderHandle"] == "+14155550134"


def test_attachments(ro):
    heic = ro.message("j_place")["attachments"][0]
    # iPhone layout: tile ispe boxes come first; the primary grid is 4032x3024 rotated a quarter turn.
    assert heic["kind"] == "image" and heic["name"] == "IMG_4821.HEIC" and (heic["width"], heic["height"]) == (3024, 4032)
    assert heic["url"] == f"/api/attachments/{heic['id']}"
    # Without sips (Linux) a HEIC can't become something an <img> draws, so no thumbnail is offered.
    assert heic["thumbUrl"] == (heic["url"] + "/thumb" if ro.services.media.sips else None)
    assert heic["isGenmoji"] is False and heic["emojiDescription"] is None
    table = ro.message("f_table")["attachments"][0]
    assert (table["kind"], table["width"], table["height"], table["missing"]) == ("image", 128, 96, False)
    missing = ro.message("f_missing")["attachments"][0]
    assert missing["missing"] and missing["thumbUrl"] is None
    voice = ro.message("m_voice")
    assert voice["isAudioMessage"] and voice["attachments"][0]["kind"] == "audio" and voice["text"] == ""
    pdf = ro.message("p_menu")["attachments"][0]
    assert pdf["kind"] == "file" and pdf["mimeType"] == "application/pdf" and pdf["thumbUrl"] is None
    sticker_files = [a for m in ro.all_messages(SOCCER, limit=60) for a in m["attachments"]]
    assert sticker_files == []  # the sticker belongs to the tapback, not a message


def test_video_dimensions_follow_rotation(ro):
    video = next(m for m in ro.all_messages("dm:+16505550147") if m["attachments"])["attachments"][0]
    assert video["kind"] == "video" and (video["width"], video["height"]) == (1080, 1920)


def test_link_preview(ro):
    link = ro.message("d_link")
    preview = link["linkPreview"]
    assert preview["url"] == "https://www.hinodeya.example/sf"
    assert preview["title"] == "Hinodeya Ramen Bar — San Francisco"
    assert preview["siteName"] == "hinodeya.example"
    assert preview["summary"].startswith("Dashi-based ramen")
    assert preview["imageUrl"].startswith("/api/attachments/")
    assert link["attachments"] == []  # the preview image isn't a separate attachment


def test_effects_status_and_mentions(ro):
    assert ro.message("f_job")["effect"] == "Slam"
    assert ro.message("p_ramen")["status"] == "read" and ro.message("p_ramen")["readAt"]
    assert ro.message("f_pies")["status"] == "delivered"
    assert ro.message("sun_failed")["status"] == "failed"
    assert ro.message("j_hike")["status"] is None
    assert ro.message("d_sake")["mentionsMe"] and ro.message("f_chairs")["mentionsMe"]
    assert not ro.message("j_hike")["mentionsMe"]


def test_otp_codes(ro):
    assert ro.message("bank_otp")["otpCode"] == "482913"
    assert ro.message("dental_code")["otpCode"] == "4829"
    assert ro.message("mk_demo")["otpCode"] is None


def test_paging_covers_everything_once(ro):
    small = ro.all_messages(FAMILY, limit=4)
    big = ro.get(f"/api/conversations/{quote(FAMILY, safe='')}/messages", params={"limit": 200})["messages"]
    assert [m["id"] for m in small] == [m["id"] for m in big]
    assert len(big) == 19


def test_media_page(ro):
    page = ro.get(f"/api/conversations/{quote(FAMILY, safe='')}/media", params={"limit": 2})
    assert [i["attachment"]["name"] for i in page["items"]] == ["recipe_card.png", "IMG_5502.JPG"]
    assert page["items"][0]["senderHandle"] == "+15105550122"
    older = ro.get(f"/api/conversations/{quote(FAMILY, safe='')}/media", params={"limit": 2, "before": page["nextCursor"]})
    assert [i["attachment"]["name"] for i in older["items"]] == ["IMG_2210.JPG"] and older["nextCursor"] is None


def test_activity(ro):
    items = ro.get("/api/activity")["items"]
    kinds = [(i["type"], i["actorHandle"]) for i in items]
    assert ("mention", "+16505550147") in kinds and ("mention", "+16285550190") in kinds
    assert ("thread_reply", "+14155550118") in kinds  # Tomás replied to Harry's message
    reaction = next(i for i in items if i["type"] == "reaction" and i["actorHandle"] == "+14155550178")
    assert reaction["message"]["id"] == ro.guid("p_ramen") and reaction["reaction"]["kind"] == "love"
    assert not any(i["type"] == "reaction" and i["actorHandle"] == "+14155550112" for i in items)  # removed like
    # Thread replies from before Harry joined the Thanksgiving thread don't count.
    early = {ro.guid("f_root")}
    assert not any(i["message"]["text"] == "I can host! We finally have the big table" for i in items), early
    stamps = [i["at"] for i in items]
    assert stamps == sorted(stamps, reverse=True)


def test_activity_new_count(ro):
    """newCount counts everything after `since`, not just the page that came back."""
    everything = ro.get("/api/activity", params={"limit": 200})["items"]
    assert len(everything) >= 3 and "newCount" not in ro.get("/api/activity")
    since = everything[len(everything) // 2]["at"]
    expected = sum(1 for i in everything if parse_iso(i["at"]) > parse_iso(since))
    feed = ro.get("/api/activity", params={"limit": 1, "since": since})
    assert len(feed["items"]) == 1 and feed["newCount"] == expected > 1
    assert ro.get("/api/activity", params={"since": "2001-01-01T00:00:00Z"})["newCount"] == len(everything)
    assert ro.get("/api/activity", params={"since": "2999-01-01T00:00:00Z"})["newCount"] == 0


def test_activity_bad_since(ro):
    assert "ISO 8601" in ro.get("/api/activity", status=400, params={"since": "yesterday"})["error"]
