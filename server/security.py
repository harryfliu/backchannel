"""Request checks that keep other web pages from driving Backchannel.

- Host must be 127.0.0.1:<port> or localhost:<port> (blocks DNS rebinding).
- /api/* needs the per-process `bc_session` cookie (SameSite=Strict, so
  cross-site requests never carry it). /api/session hands it out.
- POST/PUT/PATCH/DELETE need an Origin that is absent or our own (plus any
  BACKCHANNEL_DEV_ORIGINS), and JSON bodies must be application/json.
- No CORS headers, and pages can't be framed (clickjacking).
"""

from __future__ import annotations

import hmac
import json
import secrets
from http.cookies import SimpleCookie
from typing import Awaitable, Callable

COOKIE_NAME = "bc_session"
STATE_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}
COOKIE_EXEMPT = {"/api/session"}
MULTIPART_PATHS_SUFFIX = "/files"
MULTIPART_PATHS = {"/api/emoji"}  # emoji uploads

ASGIApp = Callable[[dict, Callable[[], Awaitable[dict]], Callable[[dict], Awaitable[None]]], Awaitable[None]]


def new_token() -> str:
    return secrets.token_urlsafe(32)


def session_cookie(token: str) -> str:
    return f"{COOKIE_NAME}={token}; HttpOnly; SameSite=Strict; Path=/"


class SecurityMiddleware:
    def __init__(self, app: ASGIApp, *, port: int, token: str, dev_origins: list[str] | None = None):
        self.app = app
        self.token = token
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}", *(dev_origins or [])}

    async def _reject(self, send, status: int, message: str) -> None:
        body = json.dumps({"error": message}).encode()
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"cache-control", b"no-store"),
                (b"x-content-type-options", b"nosniff"),
            ],
        })
        await send({"type": "http.response.body", "body": body})

    def _cookie_ok(self, header: str | None) -> bool:
        if not header:
            return False
        jar = SimpleCookie()
        try:
            jar.load(header)
        except Exception:
            return False
        morsel = jar.get(COOKIE_NAME)
        return bool(morsel) and hmac.compare_digest(morsel.value.encode(), self.token.encode())

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers: dict[str, str] = {}
        for key, value in scope.get("headers") or []:
            headers[key.decode("latin-1").lower()] = value.decode("latin-1")
        path: str = scope.get("path", "")
        method: str = scope.get("method", "GET").upper()

        host = headers.get("host", "").strip().lower()
        if host not in self.hosts:
            await self._reject(send, 403, "Backchannel only answers requests for 127.0.0.1 or localhost on its own port.")
            return
        if scope["type"] == "websocket":
            await self._reject(send, 403, "WebSockets aren't supported.")
            return

        fetch_site = headers.get("sec-fetch-site", "")
        if method in STATE_CHANGING:
            origin = headers.get("origin")
            if origin is not None and origin not in self.origins:
                await self._reject(send, 403, "Cross-origin requests can't change anything in Backchannel.")
                return
            if fetch_site == "cross-site":
                await self._reject(send, 403, "Cross-site requests can't change anything in Backchannel.")
                return

        if path.startswith("/api/") or path == "/api":
            if path in COOKIE_EXEMPT:
                if fetch_site == "cross-site":
                    await self._reject(send, 403, "Open Backchannel directly to start a session.")
                    return
            elif not self._cookie_ok(headers.get("cookie")):
                await self._reject(send, 401, "Your Backchannel session expired. Reload the page to start a new one.")
                return
            if method in ("POST", "PUT", "PATCH"):
                has_body = headers.get("content-length", "0") not in ("", "0") or "transfer-encoding" in headers
                ctype = headers.get("content-type", "").split(";")[0].strip().lower()
                if path.endswith(MULTIPART_PATHS_SUFFIX) or path in MULTIPART_PATHS:
                    if ctype != "multipart/form-data":
                        await self._reject(send, 415, "File uploads must be sent as multipart/form-data.")
                        return
                elif has_body and ctype != "application/json":
                    await self._reject(send, 415, "Request bodies must be JSON (Content-Type: application/json).")
                    return

        async def send_with_headers(message: dict) -> None:
            if message["type"] == "http.response.start":
                extra = [
                    (b"x-frame-options", b"DENY"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                ]
                existing = {k.lower() for k, _ in message.get("headers", [])}
                if b"content-security-policy" not in existing:
                    extra.append((b"content-security-policy", b"frame-ancestors 'none'"))
                if path.startswith("/api/") and b"cache-control" not in existing:
                    extra.append((b"cache-control", b"no-store"))
                message = {**message, "headers": [*message.get("headers", []), *[h for h in extra if h[0] not in existing]]}
            await send(message)

        await self.app(scope, receive, send_with_headers)
