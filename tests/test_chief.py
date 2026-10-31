"""Chief cards and "Add to Chief loops"."""

from __future__ import annotations

import sqlite3
import threading
import time
from urllib.parse import quote

import pytest

from server.chief import human_birthday
from ts_shapes import validate

JORDAN = "dm:+14155550134"


def card(harness, handle):
    response = harness.client.get(f"/api/people/{quote(handle, safe='')}/chief")
    assert response.status_code == 200, response.text
    return response.json()


def test_jordan_card(ro):
    jordan = card(ro, "+14155550134")
    assert jordan == {
        "name": "Jordan Park",
        "relationship": "friend",
        "birthday": "March 3",
        "notes": "Climbing buddy. Lent Harry the camping stove.",
        "loops": [
            {"id": 2, "title": "Plan Jordan's birthday dinner", "status": "in_progress", "dueDate": "2027-03-01"},
            {"id": 1, "title": "Get the camping stove back from Jordan", "status": "open", "dueDate": None},
        ],  # newest first; the done loop and "Jordanian visa…" don't count
        "facts": ["Jordan Park is vegetarian."],
    }
    assert not validate(jordan, "ChiefCard")
    assert card(ro, "jordan.park@icloud.example") == jordan  # any of the person's handles


def test_nickname_card_with_facts(ro):
    mom = card(ro, "+15105550122")
    assert mom["name"] == "Mom" and mom["birthday"] == "May 14"
    assert mom["facts"] == ["Mom's favorite flowers are peonies.", "Mom is allergic to shellfish.",
                            "Mom lives in San Leandro now."]  # newest first, superseded fact left out
    assert [loop["title"] for loop in mom["loops"]] == ["Book flights for Mom's visit"]


def test_first_name_match_only_when_unique(ro):
    assert card(ro, "+16505550147")["name"] == "Ellie"  # Chief only knows "Ellie"
    assert card(ro, "+14155550143") is None  # Ben Carter: "Ben" and "Ben Okafor" are both in Chief
    assert card(ro, "+14155550155") is None  # Nate isn't a contact
    assert card(ro, "+16285550190") is None  # Grace isn't in Chief


def test_unavailable_chief(make_rw, tmp_path):
    harness = make_rw(BACKCHANNEL_CHIEF_DB=str(tmp_path / "nope" / "chief.db"))
    assert harness.get("/api/health")["chief"] == {"available": False}
    assert card(harness, "+14155550134") is None
    response = harness.client.post("/api/chief/loops", json={"conversationId": JORDAN, "messageId": "x", "title": "t"})
    assert response.status_code == 503 and "chief_db" in response.json()["error"]


def test_add_loop(rw):
    assert rw.get("/api/health")["chief"] == {"available": True}
    guid = rw.guid("j_hike")
    body = {"conversationId": JORDAN, "messageId": guid, "title": "  Hike with Jordan   on Saturday "}
    created = rw.post("/api/chief/loops", body)
    assert created == {"id": 7, "title": "Hike with Jordan on Saturday"}
    conn = sqlite3.connect(rw.fx / "chief.db")
    conn.row_factory = sqlite3.Row
    row = dict(conn.execute("SELECT * FROM open_loops WHERE id = 7").fetchone())
    conn.close()
    assert {k: row[k] for k in ("title", "owner", "urgency", "category", "autonomy_tier", "status", "source_ref",
                                "due_date", "gcal_event_id", "calendar_status")} == {
        "title": "Hike with Jordan on Saturday", "owner": "harry", "urgency": "medium", "category": "imessage",
        "autonomy_tier": "ask", "status": "open", "source_ref": f"backchannel:{guid}", "due_date": None,
        "gcal_event_id": None, "calendar_status": None}
    assert rw.post("/api/chief/loops", body) == created  # a double click doesn't add a second loop
    assert card(rw, "+14155550134")["loops"][0]["title"] == "Hike with Jordan on Saturday"


def test_add_loop_validation(rw):
    assert rw.client.post("/api/chief/loops", json={"conversationId": JORDAN, "messageId": "m", "title": "   "}).status_code == 400
    assert rw.client.post("/api/chief/loops", json={"conversationId": "dm:+19999999999", "messageId": "m",
                                                    "title": "x"}).status_code == 404
    assert rw.client.post("/api/chief/loops", json={"conversationId": JORDAN, "title": "x"}).status_code == 400


def test_insert_waits_for_chiefs_own_writes(rw):
    locker = sqlite3.connect(rw.fx / "chief.db", isolation_level=None, check_same_thread=False)
    locker.execute("BEGIN IMMEDIATE")  # Chief's daemon mid-write
    release = threading.Timer(0.8, lambda: locker.execute("COMMIT"))
    release.start()
    start = time.time()
    created = rw.post("/api/chief/loops", {"conversationId": JORDAN, "messageId": "m-1", "title": "Waited"})
    assert created["title"] == "Waited" and 0.5 < time.time() - start < 5
    release.join()
    locker.close()


def test_reads_never_write(ro, fixture_src):
    before = (fixture_src / "chief.db").stat().st_mtime_ns
    card(ro, "+14155550134")
    assert (fixture_src / "chief.db").stat().st_mtime_ns == before


@pytest.mark.parametrize("raw, shown", [("03-03", "March 3"), ("1990-12-26", "December 26"), ("3-9", "March 9"),
                                        ("", None), (None, None), ("sometime in May", "sometime in May"),
                                        ("13-40", "13-40")])
def test_human_birthday(raw, shown):
    assert human_birthday(raw) == shown
