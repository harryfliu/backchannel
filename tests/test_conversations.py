"""Conversation list: merging, titles, people, previews, sidebar window."""

from __future__ import annotations

from urllib.parse import quote

JORDAN = "dm:+14155550134"
FAMILY = "g:chat100000000000000001"
SOCCER = "g:chat100000000000000002"
DINNER = "g:chat100000000000000003"
TRIO = "g:chat100000000000000004"


def test_jordans_phone_sms_and_email_chats_merge_into_one_dm(ro):
    convs = ro.conversations()
    jordans = [c for c in convs.values() if c["title"] == "Jordan Park"]
    assert len(jordans) == 1
    jordan = jordans[0]
    assert jordan["id"] == JORDAN and jordan["kind"] == "dm"
    person = jordan["participants"][0]
    assert person["handle"] == "+14155550134"
    assert person["handles"] == ["+14155550134", "jordan.park@icloud.example"]
    assert person["isContact"] and person["shortName"] == "Jordan"
    assert len(ro.services.convs.get(JORDAN).chat_ids) == 3
    messages = ro.all_messages(JORDAN)
    senders = {m["senderHandle"] for m in messages if not m["fromMe"]}
    assert senders == {"+14155550134", "jordan.park@icloud.example"}
    assert {m["service"] for m in messages} == {"SMS", "iMessage"}
    stamps = [m["sentAt"] for m in messages]
    assert stamps == sorted(stamps)
    assert messages[0]["text"] == "it's jordan, new number!" and messages[0]["sentAt"].startswith("2015-06-12")


def test_email_alias_resolves_to_the_same_conversation(ro):
    assert ro.conversation("dm:jordan.park@icloud.example")["id"] == JORDAN


def test_titles(ro):
    convs = ro.conversations()
    assert convs[FAMILY]["title"] == "Liu Family 🏡" and convs[FAMILY]["isNamed"]
    assert convs[SOCCER]["title"] == "Sunday Soccer ⚽"
    assert convs[TRIO]["title"] == "Jordan, Sam & Ellie" and not convs[TRIO]["isNamed"]
    assert convs["dm:+15105550122"]["title"] == "Mom"  # nickname wins
    assert convs["dm:+14155550161"]["title"] == "Marcus Webb"  # person name beats the organization
    assert convs["dm:+14155550170"]["title"] == "Sunset Auto Repair"  # business card
    assert convs["dm:+14155550199"]["title"] == "(415) 555-0199"  # not a contact
    assert convs["dm:24273"]["title"] == "24273"
    assert convs["dm:+15105550100"]["title"] == "Harry Liu (you)"


def test_group_participants_exclude_me_and_resolve_names(ro):
    soccer = ro.conversation(SOCCER)
    names = [p["name"] for p in soccer["participants"]]
    assert names == ["Jordan Park", "Tomás Ortega", "Kevin Huang", "Aisha Bello", "Ben Carter", "(415) 555-0155"]
    nate = soccer["participants"][-1]
    assert nate["isContact"] is False and nate["avatarUrl"] is None
    jordan = soccer["participants"][0]
    assert jordan["avatarUrl"] == "/api/avatar/%2B14155550134"


def test_service_is_that_of_the_latest_message(ro):
    convs = ro.conversations()
    assert convs["dm:+14155550161"]["service"] == "SMS"
    assert convs[JORDAN]["service"] == "iMessage"


def test_previews(ro):
    convs = ro.conversations()
    priya = convs["dm:+14155550178"]["lastMessage"]
    assert priya["text"] == "Loved “hinodeya on clement! get the dashi ramen”"
    assert priya["senderHandle"] == "+14155550178" and not priya["fromMe"]
    ellie = convs["dm:+16505550147"]["lastMessage"]
    assert ellie["text"] == "Reacted 🔥 to “LOL”"
    sunset = convs["dm:+14155550170"]["lastMessage"]
    assert sunset["fromMe"] and sunset["senderHandle"] is None


def test_sorted_by_latest_activity(ro):
    convs = ro.get("/api/conversations")["conversations"]
    stamps = [c["lastMessage"]["sentAt"] for c in convs]
    assert stamps == sorted(stamps, reverse=True)


def test_sidebar_window_keeps_starred(make_rw):
    harness = make_rw()
    # Star Sam (last activity 11 days ago), then shrink the window to 7 days.
    harness.client.patch(f"/api/conversations/{quote('dm:+14155550112', safe='')}/prefs", json={"starred": True})
    harness.services.convs.sidebar_days = 7
    harness.services.convs._list_cache = None
    ids = {c["id"] for c in harness.get("/api/conversations")["conversations"]}
    assert "dm:+14155550112" in ids  # starred
    assert "dm:+14155550170" not in ids  # Sunset Auto: 13 days ago, not starred
    assert JORDAN in ids


def test_me(ro):
    me = ro.get("/api/me")
    assert me == {"name": "Harry Liu", "shortName": "Harry",
                  "handles": ["+15105550100", "harry.liu@icloud.example"], "avatarUrl": "/api/avatar/%2B15105550100"}
    avatar = ro.client.get(me["avatarUrl"])
    assert avatar.status_code == 200 and avatar.headers["content-type"] == "image/png"


def test_health(ro):
    health = ro.get("/api/health")
    assert health["ok"] and health["chatDb"]["readable"] and health["chatDb"]["error"] is None
    assert health["contacts"]["count"] == 14
    assert health["send"]["mode"] == "dry-run"


def test_list_is_fast(ro):
    import time

    ro.services.convs._list_cache = None
    ro.services.convs._facts.clear()
    start = time.perf_counter()
    ro.get("/api/conversations")
    assert time.perf_counter() - start < 0.3
