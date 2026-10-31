"""AI drafts and catch-up with a stubbed Anthropic client (no network)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from server.ai import CATCHUP_SCHEMA, DRAFT_SCHEMA, FALLBACK_BETA, heuristic_style, parse_catchup


class StubClient:
    """Records calls; answers with canned JSON (or a refusal)."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.messages = SimpleNamespace(create=lambda **kw: self._answer("messages", kw))
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: self._answer("beta", kw)))

    def _answer(self, surface, kwargs):
        self.calls.append((surface, kwargs))
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if reply == "REFUSE":
            return SimpleNamespace(stop_reason="refusal", content=[], stop_details=None)
        text = reply if isinstance(reply, str) else json.dumps(reply)
        return SimpleNamespace(stop_reason="end_turn", content=[
            SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)])


@pytest.fixture
def ai(make_rw):
    def make(*replies):
        stub = StubClient(replies or [{"drafts": ["a", "b", "c"]}])
        harness = make_rw(ai_client_factory=lambda: stub)
        return harness, stub
    return make


def test_503_without_an_api_key(rw, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    draft = rw.client.post("/api/ai/draft", json={"conversationId": "dm:+14155550134"})
    assert draft.status_code == 503 and "ANTHROPIC_API_KEY" in draft.json()["error"]
    catchup = rw.client.get("/api/ai/catchup")
    assert catchup.status_code == 503 and "ANTHROPIC_API_KEY" in catchup.json()["error"]
    assert rw.get("/api/health")["ai"] == {"enabled": False, "model": None, "backend": None}


def test_draft_prompt_and_request(ai):
    harness, stub = ai({"drafts": ["yes!! what time", "  maybe, lmk the trail  ", "yes!! what time", "can't this weekend"]})
    response = harness.post("/api/ai/draft", {"conversationId": "dm:+14155550134", "hint": "say yes"})
    assert response == {"drafts": ["yes!! what time", "maybe, lmk the trail", "can't this weekend"]}
    surface, kwargs = stub.calls[-1]
    assert surface == "messages"  # Haiku: plain create, no server-side fallbacks
    assert kwargs["model"] == "claude-haiku-5-5"
    assert kwargs["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": DRAFT_SCHEMA}}
    assert "temperature" not in kwargs and "fallbacks" not in kwargs
    user = kwargs["messages"][0]["content"]
    assert "Jordan Park: are you down to hike Saturday?" in user
    assert "Harry: congrats!! it looks amazing" in user
    assert "Steer from Harry: say yes" in user
    assert "mostly in lowercase" in kwargs["system"]  # the style profile file
    assert "Harry Liu" in kwargs["system"]


def test_thread_draft_uses_the_thread(ai):
    harness, stub = ai({"drafts": ["yep I'll bring them", "how many do you need?", "on it"]})
    harness.post("/api/ai/draft", {"conversationId": "g:chat100000000000000001", "threadRootId": harness.guid("f_root")})
    user = stub.calls[-1][1]["messages"][0]["content"]
    assert "replying inside the thread" in user
    assert "Grace Liu: (thread reply to" in user and "folding chairs" in user
    assert "Costco card" not in user  # main-timeline chatter stays out of a thread draft


CATCHUP_REPLY = {
    "vibe": "Nothing on fire. Jordan and Marcus are waiting on you.",
    "reply_now": [{"conversation_id": "dm:+14155550161", "title": "Marcus Webb", "summary": "Wants to start demo Thursday 9am.",
                   "draft": "thursday at 9 works!", "status": "none"}],
    "reply_soon": [{"conversation_id": "dm:+14155550134", "title": "Jordan Park", "summary": "Hike Saturday? Can carpool.",
                    "draft": "down! what time", "status": "none"},
                   {"conversation_id": "dm:bogus", "title": "Nobody", "summary": "x", "draft": "", "status": "none"}],
    "plans": [{"conversation_id": "g:chat100000000000000003", "title": "Dinner Club", "summary": "Nov 7 at 7, Hinodeya.",
               "draft": "", "status": "locked_in"}],
    "caught_up": [{"conversation_id": "x", "title": "Liu Family 🏡", "summary": "Grace wants folding chairs.", "draft": "",
                   "status": "needs_action"}],
}


def test_catchup(ai):
    harness, stub = ai(CATCHUP_REPLY)
    result = harness.get("/api/ai/catchup")
    surface, kwargs = stub.calls[-1]
    assert surface == "beta" and kwargs["model"] == "claude-sonnet-5-5"
    assert kwargs["fallbacks"] == "default" and kwargs["betas"] == [FALLBACK_BETA]
    assert kwargs["output_config"]["format"] == {"type": "json_schema", "schema": CATCHUP_SCHEMA}
    user = kwargs["messages"][0]["content"]
    for conv_id in ("dm:+14155550134", "dm:+14155550161", "g:chat100000000000000003"):
        assert f"[conversation_id: {conv_id}]" in user
    assert "24273" not in user and "482913" not in user  # automated senders are skipped
    assert "Priya Natarajan — DM" not in user  # Harry had the last word, nothing unread
    assert "Jordan Park: are you down to hike Saturday?" in user
    sections = {s["kind"]: s for s in result["sections"]}
    assert [s["kind"] for s in result["sections"]] == ["reply_now", "reply_soon", "plans", "caught_up"]
    assert sections["reply_now"]["items"][0] == {
        "conversationId": "dm:+14155550161", "title": "Marcus Webb", "summary": "Wants to start demo Thursday 9am.",
        "draft": "thursday at 9 works!", "status": None}
    assert [i["conversationId"] for i in sections["reply_soon"]["items"]] == ["dm:+14155550134"]  # bogus id dropped
    assert sections["plans"]["items"][0]["status"] == "locked_in"
    assert sections["caught_up"]["items"][0]["conversationId"] == "g:chat100000000000000001"  # matched by title
    assert sections["caught_up"]["items"][0]["status"] is None and sections["caught_up"]["items"][0]["draft"] is None
    assert result["vibe"].startswith("Nothing on fire")
    # Cached for 10 minutes unless refresh=1.
    harness.get("/api/ai/catchup")
    assert len(stub.calls) == 1
    harness.get("/api/ai/catchup", params={"refresh": 1})
    assert len(stub.calls) == 2


def test_catchup_with_nothing_to_report_skips_the_model(ai):
    harness, stub = ai(CATCHUP_REPLY)
    harness.services.ai.pick_conversations = lambda now=None: []
    result = harness.get("/api/ai/catchup")
    assert stub.calls == [] and all(not s["items"] for s in result["sections"])


def test_refusal_and_bad_json_are_502(ai):
    harness, stub = ai("REFUSE")
    response = harness.client.post("/api/ai/draft", json={"conversationId": "dm:+14155550134"})
    assert response.status_code == 502 and "declined" in response.json()["error"]
    harness2, _ = ai("not json at all")
    response = harness2.client.post("/api/ai/draft", json={"conversationId": "dm:+14155550134"})
    assert response.status_code == 502 and "JSON" in response.json()["error"]


def test_style_profile_is_built_and_cached_without_the_file(ai, tmp_path):
    harness, stub = ai("Lowercase, short, lots of 'haha'.", {"drafts": ["ok", "sure", "nah"]})
    harness.services.settings.style_profile_path = tmp_path / "missing.json"
    harness.post("/api/ai/draft", {"conversationId": "dm:+14155550134"})
    style_call, draft_call = stub.calls[0][1], stub.calls[1][1]
    assert "texting style" in style_call["system"]
    assert "- hinodeya on clement! get the dashi ramen" in style_call["messages"][0]["content"]
    assert "note to self" not in style_call["messages"][0]["content"]  # self-chat excluded
    assert "Lowercase, short, lots of 'haha'." in draft_call["system"]
    cached = harness.services.state.get_kv("style_profile")
    assert cached["source"] == "claude"
    harness.post("/api/ai/draft", {"conversationId": "dm:+14155550134"})
    assert len(stub.calls) == 3  # no second style call within 72 h


def test_heuristic_style():
    note = heuristic_style(["haha yes", "omw now", "lol same 😂", "see you at 7", "Sounds good!"])
    assert "lowercase" in note and "😂" in note


def test_parse_catchup_ignores_garbage():
    picked = [{"id": "dm:+1", "title": "A"}]
    result = parse_catchup({"vibe": "", "reply_now": [None, "x", {"conversation_id": "dm:+1"}]}, picked)
    assert result["vibe"] and result["sections"][0]["items"][0]["title"] == "A"
