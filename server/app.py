"""FastAPI app: routes from docs/api.md, wired to the services."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, File, Form, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .ai import AIFailed, AIService, AIUnavailable
from .chatdb import ChangeTracker, ChatDB, ChatDBError
from .chief import Chief, ChiefUnavailable, Who
from .config import Settings, load_dotenv, load_settings, system_full_name
from .contacts import ContactBook
from .conversations import ConversationService, NotFound, build_me, conv_title, my_handles
from .media import Media, guess_mime, kind_of, serving_headers
from .handles import parse_handle
from .imagetypes import AVATAR_IMAGES, MIME
from .messages import MessageBuilder
from .reminders import Reminders, later_json
from .search import SearchIndex
from .security import COOKIE_NAME, SecurityMiddleware, new_token
from .sender import Sender
from .sending import SendService, scheduled_json
from .state import UNSET, State
from .timeutil import now_iso, parse_iso
from .watcher import EventHub, Watcher

log = logging.getLogger("backchannel")

MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_TEXT = 20000
KEEPALIVE_SECONDS = 15.0

NOT_BUILT_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>Backchannel</title>
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="font: 16px/1.5 -apple-system, system-ui, sans-serif; max-width: 36rem; margin: 4rem auto; padding: 0 1rem">
<h1>Backchannel is running</h1>
<p>The web UI hasn't been built yet. Build it once with:</p>
<pre style="background:#f4f4f5;padding:1rem;border-radius:8px">cd web &amp;&amp; npm install &amp;&amp; npm run build</pre>
<p>Then reload this page. The API is up at <code>/api/health</code>.</p></body></html>"""


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# --------------------------------------------------------------------------- request bodies


class SendBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    text: str
    clientId: str
    threadRootId: str | None = None
    sendAt: str | None = None


class PrefsBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    starred: bool | None = None
    muted: bool | None = None
    snoozedUntil: str | None = None
    section: str | None = None


class LaterBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    conversationId: str | None = None
    remindAt: str | None = None
    ifNoReply: bool | None = None
    done: bool | None = None


class NewConversationBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    handle: str
    text: str
    clientId: str


class ChiefLoopBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    conversationId: str
    messageId: str
    title: str


class DraftBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    conversationId: str
    threadRootId: str | None = None
    hint: str | None = None


# --------------------------------------------------------------------------- services


class Services:
    def __init__(self, settings: Settings, *, ai_client_factory: Callable[[], Any] | None = None):
        self.settings = settings
        settings.state_dir.mkdir(parents=True, exist_ok=True)
        self.token = new_token()
        self.db = ChatDB(settings.chat_db)
        self.tracker = ChangeTracker(self.db)
        self.media = Media(settings.attachments_root, settings.cache_dir)
        self.contacts = ContactBook(settings.addressbook_globs, can_convert=self.media.sips is not None)
        self.state = State(settings.state_dir / "state.db")
        self.hub = EventHub()
        self._full_name: str | None = None
        self.builder: MessageBuilder
        self.convs = ConversationService(
            self.db, self.tracker, self.contacts, self.state, self._me,
            settings.sidebar_days, settings.sidebar_max,
            attachment_kinds=lambda ids: self.builder.attachment_kinds(ids),
        )
        self.builder = MessageBuilder(self.db, self.convs, self.state, self.media)
        self.sender = Sender(settings.send_mode, outbox_dir=settings.state_dir / "outbox")
        self.sending = SendService(settings, self.convs, self.state, self.sender, self.hub.publish, self.db.attachments_for)
        self.search = SearchIndex(settings.state_dir / "search.db", self.db)
        self.watcher = Watcher(self.db, self.tracker, self.convs, self.builder, self.sending, self.search, self.hub)
        self.reminders = Reminders(self.db, self.convs, self.state, self.builder, self.hub.publish)
        self.watcher.reminders = self.reminders
        self.chief = Chief(settings.chief_db)
        self.ai = AIService(settings, self.convs, self.builder, self.state, client_factory=ai_client_factory)

    def _me(self):
        if self.settings.me_name:
            name = self.settings.me_name
        else:
            if self._full_name is None:
                self._full_name = system_full_name() or ""
            name = self._full_name
        try:
            discovered = self.db.my_account_handles()
        except ChatDBError:
            discovered = []
        return build_me(name, my_handles(self.settings.me_handles, discovered), self.contacts)

    def close(self) -> None:
        self.search.stop()
        self.state.close()


def _validation_message(exc: RequestValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "The request wasn't valid."
    first = errors[0]
    where = ".".join(str(p) for p in first.get("loc", []) if p not in ("body", "query", "path"))
    return f"Invalid request: {where or 'body'} — {first.get('msg', 'invalid value')}."


def _parse_time(value: str | None, field: str) -> float | None:
    if value is None:
        return None
    try:
        return parse_iso(value)
    except ValueError:
        raise ApiError(400, f"{field} must be an ISO 8601 time, like 2026-10-31T09:14:00-07:00.") from None


def _sniff_mime(path: Path) -> str | None:
    try:
        head = path.read_bytes()[:16] if path.stat().st_size < 64 else path.open("rb").read(16)
    except OSError:
        return None
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if head[:4] == b"GIF8":
        return "image/gif"
    return None


def create_app(
    settings: Settings | None = None,
    *,
    start_background: bool = True,
    ai_client_factory: Callable[[], Any] | None = None,
) -> FastAPI:
    if settings is None:  # e.g. `uvicorn server.app:create_app --factory`
        load_dotenv()
        settings = load_settings()
    services = Services(settings, ai_client_factory=ai_client_factory)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        services.hub.bind(asyncio.get_running_loop())
        tasks: list[asyncio.Task] = []
        if start_background:
            try:
                await asyncio.to_thread(services.watcher.prime)
            except ChatDBError as exc:
                log.warning("%s", exc)
            tasks.append(asyncio.create_task(services.watcher.run()))
            tasks.append(asyncio.create_task(services.sending.scheduler_loop()))
            tasks.append(asyncio.create_task(services.reminders.loop()))
            if settings.ai.get("backend") in ("auto", "claude-cli"):
                # Finding `claude` may need the login shell (a few seconds): do it now, off the request path.
                tasks.append(asyncio.create_task(asyncio.to_thread(services.ai.cli.locate)))
            services.search.start_background()
        log.info("Backchannel %s on http://127.0.0.1:%s (send mode: %s%s)", __version__, settings.port,
                 services.sender.availability, ", fake chat.db" if settings.fake_db else "")
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await services.sending.shutdown()
            services.close()

    app = FastAPI(title="Backchannel", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.services = services
    app.add_middleware(SecurityMiddleware, port=settings.port, token=services.token, dev_origins=settings.dev_origins)

    # ------------------------------------------------------------------ errors

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        return JSONResponse({"error": exc.message}, status_code=exc.status)

    @app.exception_handler(NotFound)
    async def _not_found(request: Request, exc: NotFound):
        return JSONResponse({"error": str(exc)}, status_code=404)

    @app.exception_handler(ChatDBError)
    async def _chatdb(request: Request, exc: ChatDBError):
        return JSONResponse({"error": str(exc)}, status_code=503)

    @app.exception_handler(ChiefUnavailable)
    async def _chief_unavailable(request: Request, exc: ChiefUnavailable):
        return JSONResponse({"error": str(exc)}, status_code=503)

    @app.exception_handler(AIUnavailable)
    async def _ai_unavailable(request: Request, exc: AIUnavailable):
        return JSONResponse({"error": str(exc)}, status_code=503)

    @app.exception_handler(AIFailed)
    async def _ai_failed(request: Request, exc: AIFailed):
        return JSONResponse({"error": str(exc)}, status_code=502)

    @app.exception_handler(ValueError)
    async def _value_error(request: Request, exc: ValueError):
        return JSONResponse({"error": str(exc) or "That request wasn't valid."}, status_code=400)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        return JSONResponse({"error": _validation_message(exc)}, status_code=400)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        message = exc.detail if isinstance(exc.detail, str) and exc.detail not in ("Not Found",) else "Not found."
        if exc.status_code == 405:
            message = "That method isn't allowed here."
        return JSONResponse({"error": message}, status_code=exc.status_code)

    s = services

    # ------------------------------------------------------------------ session & status

    @app.get("/api/session")
    def session():
        response = JSONResponse({"ok": True})
        response.set_cookie(COOKIE_NAME, s.token, httponly=True, samesite="strict", path="/")
        return response

    @app.get("/api/health")
    def health():
        readable, error = s.db.readable()
        return {
            "ok": readable,
            "version": __version__,
            "chatDb": {"path": str(settings.chat_db), "readable": readable, "error": error},
            "contacts": {"count": s.contacts.count},
            "send": {"mode": s.sender.availability},
            "ai": {"enabled": s.ai.backend is not None, "model": s.ai.model_label, "backend": s.ai.backend},
            "chief": {"available": s.chief.available},
            # "Mark read in Messages" (api.md) isn't built yet: report it as off.
            "messagesUi": {"enabled": False, "permission": "unknown", "lastResult": None, "lastAt": None},
        }

    @app.get("/api/me")
    def me():
        return s.convs.me().to_json()

    # ------------------------------------------------------------------ conversations

    @app.get("/api/conversations")
    def conversations():
        return {"conversations": s.convs.list()}

    @app.get("/api/conversations/{conv_id}")
    def conversation(conv_id: str):
        return s.convs.public_summary(s.convs.get(conv_id))

    @app.get("/api/conversations/{conv_id}/messages")
    def messages(conv_id: str, before: str | None = None, limit: int = Query(60, ge=1, le=200)):
        return s.builder.page(s.convs.get(conv_id), before, limit)

    @app.get("/api/conversations/{conv_id}/threads/{message_id}")
    def thread(conv_id: str, message_id: str):
        return s.builder.thread(s.convs.get(conv_id), message_id)

    @app.get("/api/conversations/{conv_id}/media")
    def media(conv_id: str, before: str | None = None, limit: int = Query(60, ge=1, le=200)):
        return s.builder.media_page(s.convs.get(conv_id), before, limit)

    @app.post("/api/conversations/{conv_id}/read")
    def mark_read(conv_id: str):
        summary = s.convs.mark_read(s.convs.get(conv_id))
        s.watcher.note_state_change(summary["id"])
        return summary

    @app.patch("/api/conversations/{conv_id}/prefs")
    def set_prefs(conv_id: str, body: PrefsBody):
        conv = s.convs.get(conv_id)
        patch: dict[str, Any] = {}
        fields = body.model_fields_set
        if "starred" in fields and body.starred is not None:
            patch["starred"] = body.starred
        if "muted" in fields and body.muted is not None:
            patch["muted"] = body.muted
        if "snoozedUntil" in fields:
            patch["snoozed_until"] = _parse_time(body.snoozedUntil, "snoozedUntil")
        if "section" in fields:
            patch["section"] = (body.section or "").strip() or None
        summary = s.convs.set_prefs(conv, patch)
        s.watcher.note_state_change(summary["id"])
        return summary

    # ------------------------------------------------------------------ sending

    @app.post("/api/conversations/{conv_id}/messages")
    async def send(conv_id: str, body: SendBody):
        conv = await asyncio.to_thread(s.convs.get, conv_id)
        text = body.text.strip()
        client_id = body.clientId.strip()
        if not text:
            raise ApiError(400, "There's nothing to send.")
        if len(text) > MAX_TEXT:
            raise ApiError(413, f"That message is too long (over {MAX_TEXT} characters).")
        if not client_id or len(client_id) > 200:
            raise ApiError(400, "clientId is required (at most 200 characters).")
        send_at = _parse_time(body.sendAt, "sendAt")
        if send_at is not None and send_at > time.time() + 5:
            return await asyncio.to_thread(s.sending.schedule, conv, text, send_at, client_id, body.threadRootId)
        return s.sending.queue_text(conv, text, client_id, body.threadRootId)

    @app.post("/api/conversations")
    async def start_conversation(body: NewConversationBody):
        text = body.text.strip()
        client_id = body.clientId.strip()
        if not text:
            raise ApiError(400, "There's nothing to send.")
        if len(text) > MAX_TEXT:
            raise ApiError(413, f"That message is too long (over {MAX_TEXT} characters).")
        if not client_id or len(client_id) > 200:
            raise ApiError(400, "clientId is required (at most 200 characters).")
        handle = parse_handle(body.handle)  # ValueError -> 400 with a readable message
        await asyncio.to_thread(s.convs.refresh)
        return s.sending.start_conversation(handle, text, client_id)

    @app.post("/api/conversations/{conv_id}/files")
    async def send_file(conv_id: str, file: UploadFile = File(...), clientId: str = Form(...)):
        conv = await asyncio.to_thread(s.convs.get, conv_id)
        client_id = clientId.strip()
        if not client_id or len(client_id) > 200:
            raise ApiError(400, "clientId is required (at most 200 characters).")
        uploads = settings.state_dir / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=uploads)
        total = 0
        try:
            with os.fdopen(fd, "wb") as out:
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        raise ApiError(413, "That file is larger than 100 MB.")
                    out.write(chunk)
            if total == 0:
                raise ApiError(400, "That file is empty.")
            existing = s.state.get_pending(client_id)
            if existing and existing["status"] != "failed":
                return {"clientId": client_id, "status": "queued"}  # same clientId twice: don't double-send
            staged = await asyncio.to_thread(s.sender.stage_file, Path(tmp_name), file.filename or "file")
            return s.sending.queue_file(conv, staged, client_id, file.content_type)
        finally:
            Path(tmp_name).unlink(missing_ok=True)

    # ------------------------------------------------------------------ later & scheduled

    @app.get("/api/later")
    def later():
        return {"items": s.reminders.items()}

    @app.put("/api/later/{message_id}")
    def put_later(message_id: str, body: LaterBody):
        fields = body.model_fields_set
        existing = s.state.get_later(message_id)
        conv_id = None
        if body.conversationId:
            conv_id = s.convs.get(body.conversationId).id
        elif existing is None:
            raise ApiError(400, "conversationId is required to save a message for later.")
        message = s.builder.by_guid(message_id)
        if message is None and existing is None:
            raise ApiError(404, "That message isn't in Messages anymore.")
        row = s.state.put_later(
            message_id, conv_id,
            remind_at=_parse_time(body.remindAt, "remindAt") if "remindAt" in fields else UNSET,
            if_no_reply=bool(body.ifNoReply) if "ifNoReply" in fields and body.ifNoReply is not None else UNSET,
            done=bool(body.done) if "done" in fields and body.done is not None else UNSET,
        )
        return later_json(row, message)

    @app.delete("/api/later/{message_id}", status_code=204)
    def delete_later(message_id: str):
        s.state.delete_later(message_id)
        return Response(status_code=204)

    @app.get("/api/scheduled")
    def scheduled():
        return {"items": [scheduled_json(r) for r in s.state.list_scheduled()]}

    @app.delete("/api/scheduled/{sid}", status_code=204)
    def cancel_scheduled(sid: str):
        if not s.sending.cancel_scheduled(sid):
            raise ApiError(404, "That scheduled message doesn't exist.")
        return Response(status_code=204)

    # ------------------------------------------------------------------ activity, threads, search

    @app.get("/api/activity")
    def activity(limit: int = Query(50, ge=1, le=200)):
        return {"items": s.builder.activity(limit)}

    @app.get("/api/threads")
    def threads(limit: int = Query(40, ge=1, le=200)):
        return {"items": s.builder.thread_roots(limit)}

    @app.get("/api/search")
    def search(q: str = "", conversationId: str | None = None, limit: int = Query(40, ge=1, le=200)):
        if not q.strip():
            return {"results": []}
        directory = s.convs.directory()
        chat_ids = None
        if conversationId:
            chat_ids = directory.get(conversationId).chat_ids
        s.search.sync(backfill_batches=0)  # make just-arrived messages searchable
        rowids = s.search.search(q, chat_ids, limit)
        messages = s.builder.by_rowids(rowids)  # oldest first by chat.db date
        messages.reverse()
        me = directory.me
        results = []
        for m in messages:
            conv = directory.convs.get(m["conversationId"])
            if conv is None:
                continue
            results.append({"message": m, "conversation": {"id": conv.id, "title": conv_title(conv, me), "kind": conv.kind}})
        return {"results": results}

    # ------------------------------------------------------------------ AI

    @app.post("/api/ai/draft")
    def ai_draft(body: DraftBody):
        if not s.ai.enabled:
            raise AIUnavailable(s.ai.unavailable_message)
        conv = s.convs.get(body.conversationId)
        return {"drafts": s.ai.draft(conv, body.threadRootId, body.hint)}

    @app.get("/api/ai/catchup")
    def ai_catchup(refresh: int = 0):
        return s.ai.catchup(refresh=bool(refresh))

    # ------------------------------------------------------------------ files

    @app.get("/api/people/{handle}/chief")
    def chief_card(handle: str):
        directory = s.convs.directory()
        person = directory.person(handle)
        contact = s.contacts.lookup(handle)
        who = Who()
        if contact is not None:
            full = " ".join(p for p in (contact.first, contact.last) if p)
            who.full_names = [n for n in (full, contact.nickname, contact.organization) if n]
            who.first_name = contact.first
        if person.is_contact and person.name not in who.full_names:
            who.full_names.append(person.name)
        if not who.full_names:
            return JSONResponse(None)
        return JSONResponse(s.chief.card(who))

    @app.post("/api/chief/loops")
    def add_chief_loop(body: ChiefLoopBody):
        title = " ".join(body.title.split())
        if not title:
            raise ApiError(400, "Give the loop a title.")
        if len(title) > 500:
            raise ApiError(400, "That title is too long (500 characters at most).")
        if not body.messageId.strip():
            raise ApiError(400, "messageId is required.")
        s.convs.get(body.conversationId)  # 404 for unknown conversations
        return s.chief.add_loop(title, body.messageId.strip())

    @app.get("/api/avatar/{handle}")
    def avatar(handle: str):
        contact = s.contacts.lookup(handle)
        if contact is None or not contact.has_photo:
            raise ApiError(404, "No contact photo for that handle.")
        photo = s.contacts.photo(contact)
        if photo is None:
            raise ApiError(404, "No contact photo for that handle.")
        data, fmt = photo
        if fmt not in AVATAR_IMAGES or len(data) > 200_000:
            converted = s.media.resize_avatar(data, contact.key.replace(":", "-"))  # JPEG, 256px
            if converted:
                data, fmt = converted[0], "jpeg"
            elif fmt not in AVATAR_IMAGES:
                raise ApiError(404, "That contact photo isn't in a format Backchannel can show.")
        return Response(content=data, media_type=MIME[fmt], headers={
            "Cache-Control": "private, max-age=3600",
            "Content-Security-Policy": "sandbox; default-src 'none'",
        })

    def _attachment_file(att_id: str):
        row = s.db.attachment_by_guid(att_id)
        if row is None:
            raise ApiError(404, "No attachment with that id.")
        path = s.media.resolve(row["filename"])
        if path is None:
            raise ApiError(403, "That attachment is stored outside the Messages attachments folder, so it isn't served.")
        if not path.is_file():
            raise ApiError(404, "This attachment isn't on this Mac (it may be in iCloud). Open it in Messages to download it.")
        name = row["transfer_name"] or path.name
        mime = row["mime_type"] or _sniff_mime(path) or guess_mime(name)
        return row, path, name, mime

    @app.get("/api/attachments/{att_id}")
    def attachment(att_id: str, raw: int = 0):
        row, path, name, mime = _attachment_file(att_id)
        if not raw and not name.endswith(".pluginPayloadAttachment"):
            path, mime = s.media.full_file(path, row["guid"], mime)
            if mime == "image/jpeg" and not name.lower().endswith((".jpg", ".jpeg")):
                name = Path(name).with_suffix(".jpg").name
            elif mime == "audio/mp4" and name.lower().endswith(".caf"):
                name = Path(name).with_suffix(".m4a").name
        mime, headers = serving_headers(mime, name)
        return FileResponse(path, media_type=mime, headers=headers)

    @app.get("/api/attachments/{att_id}/thumb")
    def attachment_thumb(att_id: str):
        row, path, name, mime = _attachment_file(att_id)
        kind = kind_of(row["mime_type"], row["uti"], name)
        preview = s.media.preview_file(path, row["guid"], kind)
        if preview is None:  # never hand an <img> a video, a document or a HEIC it can't draw
            raise ApiError(404, "There's no preview for this attachment.")
        path, mime = preview
        if mime == "image/jpeg" and not name.lower().endswith((".jpg", ".jpeg")):
            name = Path(name).with_suffix(".jpg").name
        mime, headers = serving_headers(mime, name)
        return FileResponse(path, media_type=mime, headers=headers)

    # ------------------------------------------------------------------ live events

    @app.get("/api/events")
    async def events(request: Request):
        queue = s.hub.subscribe()

        async def stream():
            import json

            try:
                yield f"data: {json.dumps({'type': 'hello', 'serverTime': now_iso()})}\n\n"
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
                    except asyncio.TimeoutError:
                        if await request.is_disconnected():
                            break
                        yield ": keepalive\n\n"
                        continue
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            finally:
                s.hub.unsubscribe(queue)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={
            "Cache-Control": "no-store", "X-Accel-Buffering": "no", "Connection": "keep-alive",
        })

    # ------------------------------------------------------------------ the web UI

    def _index() -> Response:
        index = settings.dist_dir / "index.html"
        if index.is_file():
            response: Response = FileResponse(index, media_type="text/html", headers={"Cache-Control": "no-store"})
        else:
            response = HTMLResponse(NOT_BUILT_HTML, headers={"Cache-Control": "no-store"})
        response.set_cookie(COOKIE_NAME, s.token, httponly=True, samesite="strict", path="/")
        return response

    @app.get("/", include_in_schema=False)
    def root():
        return _index()

    app.mount("/assets", StaticFiles(directory=str(settings.dist_dir / "assets"), check_dir=False), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/") or path == "api":
            raise ApiError(404, "Not found.")
        dist = settings.dist_dir.resolve()
        candidate = (dist / path).resolve()
        if candidate.is_file() and (candidate == dist or dist in candidate.parents):
            return FileResponse(candidate)
        return _index()

    return app
