"""Optional AI features: reply drafts and the catch-up digest.

Both use the Messages API with structured JSON output
(`output_config.format = json_schema`) and read the reply from the first
`text` block (adaptive-thinking models may put `thinking` blocks first).
Without ANTHROPIC_API_KEY every AI route answers 503 with an explanation.
The client is injectable so tests can stub it.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .claude_cli import ClaudeCLI, ClaudeCLIError, ClaudeCLIMissing
from .config import Settings
from .conversations import Conv, ConversationService, conv_title
from .messages import MessageBuilder
from .state import State
from .timeutil import now_iso, parse_iso

log = logging.getLogger("backchannel.ai")

FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models that accept server-side `fallbacks="default"` (Haiku has no fallback).
_FALLBACK_MODELS = ("claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1")
STYLE_KEY = "style_profile"
# `claude -p` timeouts (seconds) per kind of call.
CLI_TIMEOUTS = {"draft": 90.0, "catchup": 240.0, "style": 120.0}
CATCHUP_NEW_HOURS = 36  # a catch-up summarizes messages this recent…
CATCHUP_CONTEXT = 8  # …after at most this many older ones, marked as context
_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
_BOT_PREFIXES = ("📩", "📋", "👀", "📊", "✅ Added", "✅ Done", "📝")
_COMMANDS = ("todo:", "done ", "schedule:", "cancel:", "reschedule:", "explore ", "hangout", "ghosted",
             "digest", "status", "help")

DRAFT_SCHEMA = {
    "type": "object",
    "properties": {"drafts": {"type": "array", "items": {"type": "string"}}},
    "required": ["drafts"],
    "additionalProperties": False,
}
_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "conversation_id": {"type": "string"},
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "draft": {"type": "string"},
        "status": {"type": "string", "enum": ["needs_action", "locked_in", "none"]},
    },
    "required": ["conversation_id", "title", "summary", "draft", "status"],
    "additionalProperties": False,
}
CATCHUP_SCHEMA = {
    "type": "object",
    "properties": {
        "vibe": {"type": "string"},
        "reply_now": {"type": "array", "items": _ITEM_SCHEMA},
        "reply_soon": {"type": "array", "items": _ITEM_SCHEMA},
        "plans": {"type": "array", "items": _ITEM_SCHEMA},
        "caught_up": {"type": "array", "items": _ITEM_SCHEMA},
    },
    "required": ["vibe", "reply_now", "reply_soon", "plans", "caught_up"],
    "additionalProperties": False,
}
SECTIONS = (
    ("reply_now", "Reply now"),
    ("reply_soon", "Reply soon"),
    ("plans", "Plans"),
    ("caught_up", "Caught up"),
)


class AIUnavailable(RuntimeError):
    """No usable API key (HTTP 503)."""


class AIFailed(RuntimeError):
    """The model call failed or returned something unusable (HTTP 502)."""


# --------------------------------------------------------------------------- prompts

DRAFT_SYSTEM = """You write text-message replies for {name}, in his voice, for him to send as-is.

Write exactly three short reply options to the latest message(s) in the conversation.
- Sound like {short} actually texts (style notes below): casual, usually lowercase, short.
- Make the three options genuinely different (for example: a yes, a question back, a lighter or noncommittal take) unless his steer says otherwise.
- Use real details from the conversation. Don't invent plans, times or facts; put anything he'd need to fill in in [brackets].
- [photo], [video] and the like are things you can't see: don't describe what they show unless the texts say so.
- Don't guess anyone's gender from a name or nickname; use their name unless the conversation or the notes about {short} make it clear.
- No greetings or sign-offs unless he uses them, no hashtags, no quotation marks around the reply. One or two sentences each.

{short}'s texting style:
{profile}{about}"""

CATCHUP_SYSTEM = """You're {name}'s personal assistant catching him up on his iMessages from about the last day. Sort what needs his attention into four buckets and write a one-line vibe check. {short} is the user; lines from him are labelled "{short}".

Buckets:
- reply_now: truly time-sensitive — someone needs an answer for something happening soon.
- reply_soon: someone asked {short} a direct question or needs a response, but it can wait.
- plans: dates, reservations, meetups. status "needs_action" if he must confirm, decide or book something; "locked_in" if it's fully set.
- caught_up: everything else in a line or two — closed loops, banter, FYI.

Rules:
- Always use people's real names as written in the transcript, or the phone number if that's all there is. Never say "someone", "a friend" or "that person".
- Check who sent the LAST message in each conversation. If {short} sent it, the ball is in their court: don't put it in reply_now or reply_soon unless there's a separate unanswered question from them; note that he's waiting on them.
- If someone only acknowledged or confirmed something ("sounds good", "ok", "got it", "will do", "yup", 👍) and nothing is needed from him, it goes in caught_up, not reply_soon.
- Only use reply_soon when they asked him a direct question or need a response from him.
- A transcript may start with a few older messages marked (earlier, for context), then (new). Summarize the new ones; use the older ones only to understand them, never to recap settled threads.
- Bracketed notes ([photo], [video], [voice message], [file: …], [link: …]) stand for things you can't see or hear. Never say what a photo or video shows unless a caption in the same message, or the texts around it, say so; otherwise just say they sent a photo. A photo sent right after a question is usually the answer to it: after "did you do anything tonight?" it shows what they did.
- Keep straight who sent what, and what each message answers. A comment is often about something someone else sent earlier (their photo, their news), not about the sender's own message just before it. Don't merge separate messages, or separate sentences of one message, into one claim.
- Don't guess anyone's gender from a name or nickname. Use he or she only when the conversation or the notes about {short} make it clear; otherwise use their name.
- Skip verification codes, spam and automated messages entirely.
- Put each conversation in one bucket (the most urgent); a conversation may also appear in plans when it contains a plan.
- title: the person's name or a short label for the topic, e.g. "Marcus Webb" or "Thanksgiving at Grace's". summary: one or two plain sentences, no markdown, written to {short} ("you", never "{short}").
- draft: for reply_now and reply_soon, a reply he could send as-is, in his voice; use [brackets] for details he must fill in. Use an empty string elsewhere. status is "none" outside plans.
- vibe: one short line, e.g. "Nothing on fire. Two people are waiting on you."
- Copy conversation_id exactly from the conversation header.

{short}'s texting style:
{profile}{about}"""

# Appended to both prompts when the user wrote a note about themselves (ServerSettings.aboutMe).
ABOUT_BLOCK = """

About {short}, in his own words (who's who, nicknames; trust this over guesses):
{note}"""

STYLE_SYSTEM = (
    "You are analyzing someone's texting style from their real iMessage history. Produce a concise style "
    "guide that another AI can use to write draft replies in this person's voice. Cover capitalization, "
    "punctuation, filler words and phrases they repeat, emoji use, message length and tone, and any "
    "distinctive quirks, with short examples from the messages. Write it as one direct paragraph under "
    "150 words; it will be pasted into a system prompt."
)


def _stamp(sent_at: str | None) -> str:
    if not sent_at:
        return ""
    try:
        dt = datetime.fromtimestamp(parse_iso(sent_at))
    except ValueError:
        return ""
    return dt.strftime("%a %b %d, %I:%M %p").replace(" 0", " ")


def _attachment_note(message: dict[str, Any]) -> str:
    notes = []
    if message.get("isAudioMessage"):
        notes.append("[voice message]")
    for att in message.get("attachments") or []:
        notes.append({"image": "[photo]", "video": "[video]", "audio": "[audio]"}.get(att["kind"], f"[file: {att['name']}]"))
    link = message.get("linkPreview")
    if link:
        notes.append(f"[link: {link.get('title') or link.get('url')}]")
    return " ".join(notes)


def transcript(messages: list[dict[str, Any]], names: Callable[[str | None], str], me_short: str,
               roots: dict[str, str] | None = None) -> str:
    """Plain-text transcript with real names, oldest first."""
    lines = []
    roots = roots or {}
    for m in messages:
        if m.get("event"):
            lines.append(f"[{_stamp(m['sentAt'])}] ({m['event']['text']})")
            continue
        who = me_short if m["fromMe"] else names(m.get("senderHandle"))
        body = "(unsent a message)" if m.get("unsent") else (m.get("text") or "")
        extra = _attachment_note(m)
        content = " ".join(p for p in (body, extra) if p).strip() or "(empty)"
        prefix = ""
        if m.get("threadRootId"):
            root_text = roots.get(m["threadRootId"])
            prefix = f"(thread reply to “{root_text[:60]}”) " if root_text else "(thread reply) "
        reactions = ", ".join(
            f"{'/'.join(me_short if h == 'me' else names(h) for h in r['from'])} {r['emoji']}"
            for r in m.get("reactions") or []
        )
        tail = f"  [reactions: {reactions}]" if reactions else ""
        lines.append(f"[{_stamp(m['sentAt'])}] {who}: {prefix}{content}{tail}")
    return "\n".join(lines)


def heuristic_style(samples: list[str]) -> str:
    """A short style note computed locally (used when no profile file or API style call is available)."""
    if not samples:
        return "Casual and brief. Mostly lowercase, light punctuation, the occasional emoji."
    lower = sum(1 for s in samples if s == s.lower()) / len(samples)
    words = sum(len(s.split()) for s in samples) / len(samples)
    emoji = Counter(ch for s in samples for ch in _EMOJI.findall(s)).most_common(3)
    exclaim = sum(1 for s in samples if s.rstrip().endswith("!")) / len(samples)
    period = sum(1 for s in samples if s.rstrip().endswith(".")) / len(samples)
    parts = [
        f"Writes {'almost always in lowercase' if lower > 0.7 else 'mostly lowercase' if lower > 0.4 else 'with normal capitalization'}",
        f"messages average about {round(words)} words",
        "rarely ends with a period" if period < 0.2 else "often ends sentences with periods",
    ]
    if exclaim > 0.2:
        parts.append("uses exclamation points for enthusiasm")
    if emoji:
        parts.append("favourite emoji: " + " ".join(e for e, _ in emoji))
    examples = [s for s in samples if 2 <= len(s.split()) <= 12][:6]
    note = ", ".join(parts) + "."
    if examples:
        note += " Examples: " + " | ".join(f'"{e}"' for e in examples)
    return note[0].upper() + note[1:]


# --------------------------------------------------------------------------- service


class AIService:
    def __init__(
        self,
        settings: Settings,
        convs: ConversationService,
        builder: MessageBuilder,
        state: State,
        client_factory: Callable[[], Any] | None = None,
        cli: ClaudeCLI | None = None,
    ):
        self.settings = settings
        self.convs = convs
        self.builder = builder
        self.state = state
        self._client_factory = client_factory
        self._client: Any = None
        self._lock = threading.Lock()
        self._catchup: tuple[float, dict[str, Any]] | None = None
        self.cli = cli or ClaudeCLI(settings.claude_bin, settings.state_dir / "claude-cwd")

    # ------------------------------------------------------------------ backend choice

    @property
    def mode(self) -> str:
        return str(self.settings.ai.get("backend", "auto"))

    @staticmethod
    def has_api_key() -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))

    @property
    def backend(self) -> str | None:
        """'claude-cli' (`claude -p`, Harry's subscription), 'api' (ANTHROPIC_API_KEY) or None."""
        if self._client_factory is not None:
            return "api"
        if self.mode == "api":
            return "api" if self.has_api_key() else None
        if self.mode == "claude-cli":
            return "claude-cli" if self.cli.locate() else None
        if self.cli.locate():
            return "claude-cli"
        return "api" if self.has_api_key() else None

    @property
    def enabled(self) -> bool:
        return self.backend is not None

    @property
    def model_label(self) -> str | None:
        backend = self.backend
        if backend == "claude-cli":
            return self.settings.models_cli.get("draft")
        if backend == "api":
            return self.settings.models.get("draft")
        return None

    @property
    def unavailable_message(self) -> str:
        if self.mode == "api":
            return ("AI features need an Anthropic API key. Set ANTHROPIC_API_KEY in the environment "
                    "(or in .env at the repo root) and restart Backchannel.")
        if self.mode == "claude-cli":
            return ("Backchannel is set to use Claude Code but can't find the `claude` command. Install Claude Code, "
                    "or set \"claude_bin\" in config.local.json (or BACKCHANNEL_CLAUDE_BIN) to its path.")
        return ("AI features need Claude Code (the `claude` command) or an ANTHROPIC_API_KEY. Install Claude Code "
                "and run `claude` once in Terminal to log in, or put ANTHROPIC_API_KEY=… in .env at the repo root, "
                "then restart Backchannel.")

    def _require(self) -> str:
        backend = self.backend
        if backend is None:
            raise AIUnavailable(self.unavailable_message)
        return backend

    def _api_client(self) -> Any:
        with self._lock:
            if self._client is None:
                if self._client_factory is not None:
                    self._client = self._client_factory()
                else:
                    import anthropic

                    self._client = anthropic.Anthropic(
                        timeout=float(self.settings.ai.get("timeout_seconds", 90)), max_retries=2
                    )
            return self._client

    def _create(self, client: Any, kwargs: dict[str, Any]) -> Any:
        import anthropic

        model = kwargs["model"]
        use_fallbacks = bool(self.settings.ai.get("server_side_fallbacks", True)) and model.startswith(_FALLBACK_MODELS)
        try:
            if use_fallbacks:
                return client.beta.messages.create(**kwargs, betas=[FALLBACK_BETA], fallbacks="default")
            return client.messages.create(**kwargs)
        except anthropic.AuthenticationError as exc:
            raise AIUnavailable("Anthropic rejected the API key in ANTHROPIC_API_KEY. Check it and restart Backchannel.") from exc
        except anthropic.PermissionDeniedError as exc:
            raise AIFailed("This Anthropic API key isn't allowed to use that model.") from exc
        except anthropic.NotFoundError as exc:
            raise AIFailed(f"The model {model} isn't available to this API key. Change it under \"models\" in config.json.") from exc
        except anthropic.RateLimitError as exc:
            raise AIFailed("Claude is rate-limited right now. Try again in a minute.") from exc
        except anthropic.BadRequestError as exc:
            raise AIFailed(f"Claude rejected the request: {exc.message}") from exc
        except anthropic.APIStatusError as exc:
            raise AIFailed(f"Claude returned an error ({exc.status_code}). Try again shortly.") from exc
        except anthropic.APIConnectionError as exc:  # includes timeouts
            raise AIFailed("Couldn't reach Claude. Check the internet connection and try again.") from exc

    def _text(self, response: Any) -> str:
        reason = getattr(response, "stop_reason", None)
        if reason == "refusal":
            raise AIFailed("Claude declined to help with this conversation.")
        if reason == "max_tokens":
            raise AIFailed("Claude's answer was cut off. Try again.")
        for block in getattr(response, "content", None) or []:
            if getattr(block, "type", None) == "text":
                return block.text
        raise AIFailed("Claude returned no text.")

    def _cli(self, call: Callable[[], Any]) -> Any:
        try:
            return call()
        except ClaudeCLIMissing as exc:
            raise AIUnavailable(self.unavailable_message) from exc
        except ClaudeCLIError as exc:
            raise AIFailed(str(exc)) from exc

    def call_json(self, *, kind: str, system: str, user: str, schema: dict, max_tokens: int, effort: str) -> dict[str, Any]:
        """One JSON answer from whichever backend is available. `kind` is 'draft' or 'catchup'."""
        if self._require() == "claude-cli":
            data = self._cli(lambda: self.cli.run_json(
                user=user, system=system, schema=schema, model=self.settings.models_cli[kind],
                timeout=CLI_TIMEOUTS[kind]))
        else:
            response = self._create(self._api_client(), {
                "model": self.settings.models[kind],
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user}],
                "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            })
            text = self._text(response)
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise AIFailed("Claude's answer wasn't valid JSON. Try again.") from exc
        if not isinstance(data, dict):
            raise AIFailed("Claude's answer had an unexpected shape.")
        return data

    def call_text(self, *, system: str, user: str, max_tokens: int = 2000) -> str:
        """A plain-text answer (the style profile), using the draft model."""
        if self._require() == "claude-cli":
            return self._cli(lambda: self.cli.run(
                user=user, system=system, model=self.settings.models_cli["draft"], timeout=CLI_TIMEOUTS["style"]))
        response = self._create(self._api_client(), {
            "model": self.settings.models["draft"],
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": {"effort": "low"},
        })
        return self._text(response)

    # ------------------------------------------------------------------ style

    def my_samples(self, limit: int = 200) -> list[str]:
        directory = self.convs.directory()
        self_chats = {c for conv in directory.unique_convs() if conv.is_self for c in conv.chat_ids}
        rows = self.builder.db.query(
            f"""SELECT {self.builder.db.message_select(fields=('ROWID', 'text', 'attributedBody'))}, cmj.chat_id AS chat_id
                FROM message m JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
                WHERE m.ROWID > (SELECT COALESCE(MAX(ROWID), 0) FROM message) - 20000
                  AND m.is_from_me = 1 AND m.item_type = 0 AND COALESCE(m.associated_message_type, 0) = 0
                ORDER BY m.ROWID DESC LIMIT ?""",
            (limit * 3,),
        )
        from .conversations import message_text

        samples = []
        for row in rows:
            if row["chat_id"] in self_chats:
                continue
            text = message_text(row)
            if not text or len(text) <= 3 or text.startswith(_BOT_PREFIXES) or text.lower().startswith(_COMMANDS):
                continue
            samples.append(text)
            if len(samples) >= limit:
                break
        return samples

    def style_profile(self) -> str:
        path = self.settings.style_profile_path
        if path and Path(path).is_file():
            try:
                profile = json.loads(Path(path).read_text()).get("profile")
                if profile:
                    return str(profile)
            except (OSError, json.JSONDecodeError, AttributeError):
                log.info("Couldn't read the style profile at %s", path)
        cached = self.state.get_kv(STYLE_KEY)
        max_age = float(self.settings.ai.get("style_cache_hours", 72)) * 3600
        if cached and time.time() - cached.get("updated", 0) < max_age and cached.get("profile"):
            return cached["profile"]
        samples = self.my_samples()
        profile, source = heuristic_style(samples), "heuristic"
        if samples and self.enabled:
            try:
                answer = self.call_text(system=STYLE_SYSTEM, user="Here are their recent texts:\n" +
                                        "\n".join(f"- {s}" for s in samples[:150]))
                if answer.strip():
                    profile, source = answer.strip(), "claude"
            except (AIFailed, AIUnavailable) as exc:
                log.info("Style profile call failed, using the local one: %s", exc)
        self.state.set_kv(STYLE_KEY, {"profile": profile, "updated": time.time(), "source": source,
                                      "sample_count": len(samples)})
        return profile

    def about_block(self, short: str) -> str:
        """The user's note about themselves (Settings › AI), for the prompts; empty when there's none."""
        note = (self.state.get_settings().get("aboutMe") or "").strip()
        return ABOUT_BLOCK.format(short=short, note=note) if note else ""

    # ------------------------------------------------------------------ drafts

    def build_draft_prompt(self, conv: Conv, thread_root_id: str | None, hint: str | None) -> tuple[str, str]:
        directory = self.convs.directory()
        me = directory.me
        if thread_root_id:
            thread = self.builder.thread(conv, thread_root_id)
            messages = [thread["root"], *thread["replies"]][-40:]
        else:
            messages = self.builder.recent(conv, 40)
        names = lambda h: directory.person(h).name  # noqa: E731
        root_text = {m["id"]: m.get("text") or "" for m in messages}
        kind = "group chat" if conv.kind == "group" else "DM"
        people = ", ".join(p.name for p in conv.persons) or "nobody else"
        header = f'Conversation: "{conv_title(conv, me)}" ({kind} with {people}).'
        if thread_root_id:
            header += f" {me.short_name} is replying inside the thread that starts with: “{root_text.get(thread_root_id, '')[:120]}”."
        body = transcript(messages, names, me.short_name, root_text)
        steer = f"\n\nSteer from {me.short_name}: {hint.strip()}" if hint and hint.strip() else ""
        user = f"{header}\n\nTranscript (oldest first):\n{body}{steer}\n\nWrite three reply options for {me.short_name}."
        system = DRAFT_SYSTEM.format(name=me.name, short=me.short_name, profile=self.style_profile(),
                                     about=self.about_block(me.short_name))
        return system, user

    def draft(self, conv: Conv, thread_root_id: str | None = None, hint: str | None = None) -> list[str]:
        self._require()
        system, user = self.build_draft_prompt(conv, thread_root_id, hint)
        data = self.call_json(
            kind="draft", system=system, user=user, schema=DRAFT_SCHEMA,
            max_tokens=4000, effort=str(self.settings.ai.get("draft_effort", "low")),
        )
        return clean_drafts(data.get("drafts"))

    # ------------------------------------------------------------------ catch-up

    def pick_conversations(self, now: float | None = None) -> list[dict[str, Any]]:
        now = time.time() if now is None else now
        picked = []
        for conv in self.convs.list():
            if conv["isAutomated"] or conv["prefs"]["muted"] or not conv["lastMessage"]:
                continue
            if parse_iso(conv["lastMessage"]["sentAt"]) < now - 86400:
                continue
            if conv["unreadCount"] > 0 or conv["awaitingReply"]:
                picked.append(conv)
        return picked[:25]

    def build_catchup_prompt(self, picked: list[dict[str, Any]]) -> tuple[str, str]:
        directory = self.convs.directory()
        me = directory.me
        names = lambda h: directory.person(h).name  # noqa: E731
        blocks = []
        for summary in picked:
            conv = directory.get(summary["id"])
            messages = self.builder.recent(conv, 30)
            roots = {m["id"]: m.get("text") or "" for m in messages}
            # What's new (the last day and a half), after a few older messages for context: a long
            # window had the model recapping threads settled weeks ago.
            cutoff = time.time() - CATCHUP_NEW_HOURS * 3600
            new_from = next((i for i, m in enumerate(messages) if parse_iso(m["sentAt"]) >= cutoff), len(messages))
            older = messages[max(0, new_from - CATCHUP_CONTEXT):new_from]
            newer = messages[new_from:]
            last = messages[-1] if messages else None
            last_by = (me.short_name if last and last["fromMe"] else names(last.get("senderHandle"))) if last else "nobody"
            kind = "group" if conv.kind == "group" else "DM"
            details = [f"{summary['unreadCount']} unread"] if summary["unreadCount"] else []
            details.append(f"last message from {last_by}")
            members = ", ".join(p.name for p in conv.persons)
            header = (f"### {summary['title']} — {kind}" + (f" with {members}" if conv.kind == "group" else "") +
                      f" [conversation_id: {conv.id}] ({'; '.join(details)})")
            body = transcript(newer, names, me.short_name, roots)
            if older:
                body = ("(earlier, for context)\n" + transcript(older, names, me.short_name, roots) +
                        "\n(new)\n" + body)
            blocks.append(header + "\n" + body)
        now = datetime.now().strftime("%A, %b %d, %I:%M %p").replace(" 0", " ")
        user = f"Now: {now}.\n\n" + "\n\n".join(blocks)
        system = CATCHUP_SYSTEM.format(name=me.name, short=me.short_name, profile=self.style_profile(),
                                       about=self.about_block(me.short_name))
        return system, user

    def forget_catchup(self) -> None:
        """Drop the cached catch-up (the note about the user changed)."""
        self._catchup = None

    def catchup(self, refresh: bool = False) -> dict[str, Any]:
        self._require()
        ttl = float(self.settings.ai.get("catchup_cache_minutes", 10)) * 60
        if not refresh and self._catchup and time.time() - self._catchup[0] < ttl:
            return self._catchup[1]
        picked = self.pick_conversations()
        if not picked:
            result = {
                "generatedAt": now_iso(),
                "vibe": "You're all caught up — nothing new needs you from the last day.",
                "sections": [{"kind": k, "title": t, "items": []} for k, t in SECTIONS],
            }
        else:
            system, user = self.build_catchup_prompt(picked)
            data = self.call_json(
                kind="catchup", system=system, user=user, schema=CATCHUP_SCHEMA,
                max_tokens=16000, effort=str(self.settings.ai.get("catchup_effort", "medium")),
            )
            result = parse_catchup(data, picked)
        self._catchup = (time.time(), result)
        return result


def clean_drafts(raw: Any) -> list[str]:
    out: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, str):
            continue
        text = item.strip().strip('"“”').strip()
        if text and unicodedata.normalize("NFKC", text).lower() not in {unicodedata.normalize("NFKC", o).lower() for o in out}:
            out.append(text)
    if not out:
        raise AIFailed("Claude didn't return any drafts. Try again.")
    return out[:3]


def parse_catchup(data: dict[str, Any], picked: list[dict[str, Any]]) -> dict[str, Any]:
    """Model JSON (snake_case) -> CatchUp (camelCase), keeping only known conversations."""
    by_id = {c["id"]: c for c in picked}
    by_title = {c["title"].lower(): c["id"] for c in picked}
    sections = []
    for kind, title in SECTIONS:
        items = []
        for raw in data.get(kind) or []:
            if not isinstance(raw, dict):
                continue
            cid = str(raw.get("conversation_id") or "")
            if cid not in by_id:
                cid = by_title.get(str(raw.get("title") or "").lower(), "")
            if not cid:
                continue
            status = raw.get("status")
            draft = (raw.get("draft") or "").strip() or None
            items.append({
                "conversationId": cid,
                "title": (raw.get("title") or by_id[cid]["title"]).strip(),
                "summary": (raw.get("summary") or "").strip(),
                "draft": draft,
                "status": status if kind == "plans" and status in ("needs_action", "locked_in") else None,
            })
        sections.append({"kind": kind, "title": title, "items": items})
    vibe = (data.get("vibe") or "").strip() or "Here's what happened."
    return {"generatedAt": now_iso(), "vibe": vibe, "sections": sections}
