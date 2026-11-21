"""Spam: marked in Backchannel, or filed as spam by Messages (chat.is_filtered = 2)."""

from __future__ import annotations

from contextlib import closing
from urllib.parse import quote

JORDAN = "dm:+14155550134"
MARCUS = "dm:+14155550161"


def prefs(harness, conv_id, **body):
    return harness.client.patch(f"/api/conversations/{quote(conv_id, safe='')}/prefs", json=body).json()


def listed(harness) -> set[str]:
    return set(harness.conversations())


def spam(harness) -> list[dict]:
    return harness.get("/api/spam")["conversations"]


def test_marking_a_conversation_as_spam_and_back(rw):
    assert JORDAN in listed(rw) and spam(rw) == []
    summary = prefs(rw, JORDAN, spam=True)
    assert summary["isSpam"] is True and summary["prefs"]["spam"] is True
    assert summary["priority"] is None  # never "Needs you"
    assert JORDAN not in listed(rw)  # out of the sidebar, Unreads, Needs you…
    assert [c["id"] for c in spam(rw)] == [JORDAN]
    assert rw.conversation(JORDAN)["isSpam"] is True  # still opens
    summary = prefs(rw, JORDAN, spam=False)
    assert summary["isSpam"] is False and summary["prefs"]["spam"] is False
    assert JORDAN in listed(rw) and spam(rw) == []
    starred = prefs(rw, JORDAN, starred=True)  # other prefs leave it alone
    assert starred["prefs"]["spam"] is False and starred["prefs"]["starred"] is True


def test_what_messages_filed_as_spam_is_spam_until_marked_not_spam(rw):
    conv = rw.services.convs.get(MARCUS)
    with closing(rw.db()) as conn:
        conn.executemany("UPDATE chat SET is_filtered = 2 WHERE ROWID = ?", [(c,) for c in conv.chat_ids])
        conn.commit()
    assert MARCUS not in listed(rw)  # Backchannel notices the change to chat.db
    [filed] = spam(rw)
    assert filed["id"] == MARCUS and filed["isSpam"] is True and filed["prefs"]["spam"] is None
    assert prefs(rw, MARCUS, spam=False)["isSpam"] is False  # "Not spam" here wins over Messages
    assert MARCUS in listed(rw) and spam(rw) == []
    assert prefs(rw, MARCUS, spam=None)["isSpam"] is True  # back to what Messages says
    assert MARCUS not in listed(rw)


def test_conversation_events_carry_the_spam_flag(rw):
    rw.services.hub.history.clear()
    prefs(rw, JORDAN, spam=True)
    pushed = [e["conversation"] for e in rw.services.hub.history if e.get("type") == "conversation"]
    assert any(c["id"] == JORDAN and c["isSpam"] for c in pushed)
