"""Host / cookie / Origin / Content-Type checks and safe attachment serving."""

from __future__ import annotations

import time
from urllib.parse import quote

from fastapi.testclient import TestClient

from conftest import BASE
from server import fakedb

JORDAN = quote("dm:+14155550134", safe="")


def fresh_client(harness) -> TestClient:
    return TestClient(harness.app, base_url=BASE)  # no cookie yet


def test_bad_host_is_rejected(ro):
    for host in ("evil.example", "evil.example:5556", "127.0.0.1:9999", "192.168.1.5:5556", "localhost"):
        response = ro.client.get("/api/health", headers={"host": host})
        assert response.status_code in (400, 403), host
        assert "error" in response.json()
    assert ro.client.get("/", headers={"host": "attacker.example"}).status_code == 403
    assert ro.client.get("/api/health", headers={"host": "localhost:5556"}).status_code == 200


def test_missing_or_wrong_cookie_is_401(ro):
    client = fresh_client(ro)
    response = client.get("/api/conversations")
    assert response.status_code == 401 and "error" in response.json()
    client.cookies.set("bc_session", "not-the-token")
    assert client.get("/api/conversations").status_code == 401
    assert client.get("/api/events").status_code == 401


def test_session_route_and_index_set_the_cookie(ro):
    client = fresh_client(ro)
    response = client.get("/api/session")
    assert response.status_code == 200 and response.json() == {"ok": True}
    cookie = response.headers["set-cookie"]
    assert "bc_session=" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/" in cookie
    assert client.get("/api/me").status_code == 200
    index = fresh_client(ro).get("/")
    assert index.status_code == 200 and "bc_session=" in index.headers["set-cookie"]
    assert "npm run build" in index.text  # no web/dist in tests
    assert fresh_client(ro).get("/api/session", headers={"sec-fetch-site": "cross-site"}).status_code == 403


def test_cross_origin_writes_are_403(ro):
    url = f"/api/conversations/{JORDAN}/prefs"
    for origin in ("https://evil.example", "http://127.0.0.1:5557", "null"):
        response = ro.client.patch(url, json={"muted": False}, headers={"origin": origin})
        assert response.status_code == 403, origin
    assert ro.client.patch(url, json={"muted": False}, headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert ro.client.patch(url, json={"muted": False}, headers={"origin": "http://127.0.0.1:5556"}).status_code == 200
    assert ro.client.patch(url, json={"muted": False}).status_code == 200  # no Origin header (curl, same-origin GETs)
    # The Vite dev origin is only allowed when configured.
    assert ro.client.patch(url, json={"muted": False}, headers={"origin": "http://localhost:5173"}).status_code == 403


def test_dev_origins(make_rw):
    harness = make_rw(BACKCHANNEL_DEV_ORIGINS="http://localhost:5173")
    url = f"/api/conversations/{JORDAN}/read"
    assert harness.client.post(url, headers={"origin": "http://localhost:5173"}).status_code == 200
    assert harness.client.post(url, headers={"origin": "http://localhost:3000"}).status_code == 403


def test_json_bodies_must_be_json(ro):
    url = f"/api/conversations/{JORDAN}/prefs"
    response = ro.client.patch(url, content=b"muted=1", headers={"content-type": "application/x-www-form-urlencoded"})
    assert response.status_code == 415
    response = ro.client.post(f"/api/conversations/{JORDAN}/files", json={"clientId": "x"})
    assert response.status_code == 415


def test_no_cors_and_no_framing(ro):
    response = ro.client.get("/api/health", headers={"origin": "https://evil.example"})
    assert not any(h.lower().startswith("access-control-") for h in response.headers)
    preflight = ro.client.options("/api/health", headers={"origin": "https://evil.example",
                                                          "access-control-request-method": "POST"})
    assert "access-control-allow-origin" not in preflight.headers
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cache-control"] == "no-store"


def test_attachments_are_served_by_guid_only(ro):
    good = ro.client.get(f"/api/attachments/{ro.attachment('f_table')}")
    assert good.status_code == 200 and good.headers["content-type"] == "image/jpeg"
    assert good.content[:2] == b"\xff\xd8"
    for bad in ("..%2F..%2Fetc%2Fpasswd", quote("/etc/passwd", safe=""), "at_0_NOPE"):
        assert ro.client.get(f"/api/attachments/{bad}").status_code == 404
    assert ro.client.get(f"/api/attachments/{ro.attachment('f_missing')}").status_code == 404


def test_files_outside_the_attachments_root_are_refused(rw):
    conn = rw.db()
    chat = fakedb.chat_info(conn, "iMessage;-;+16505550147")
    handle = fakedb.ensure_handle(conn, "+16505550147", "iMessage")
    secret = rw.fx / "secret.txt"
    secret.write_text("private")
    fakedb.insert_message(conn, chat_rowid=chat["ROWID"], text="look", is_from_me=False, handle_rowid=handle,
                          date=time.time(), attachments=[
                              {"guid": "at_0_ESCAPE1", "filename": str(secret), "transfer_name": "secret.txt"},
                              {"guid": "at_0_ESCAPE2", "filename": "~/Library/Messages/Attachments/../../../../etc/hosts",
                               "transfer_name": "hosts"},
                          ])
    conn.close()
    assert rw.client.get("/api/attachments/at_0_ESCAPE1").status_code == 403
    assert rw.client.get("/api/attachments/at_0_ESCAPE2").status_code == 403


def test_risky_file_types_download_instead_of_rendering(rw):
    conn = rw.db()
    chat = fakedb.chat_info(conn, "iMessage;-;+16505550147")
    handle = fakedb.ensure_handle(conn, "+16505550147", "iMessage")
    filename, size = fakedb.store_attachment_file(rw.settings.attachments_root, "at_0_HTML", "page.html",
                                                  b"<script>fetch('/api/conversations')</script>")
    fakedb.insert_message(conn, chat_rowid=chat["ROWID"], text=None, is_from_me=False, handle_rowid=handle,
                          date=time.time(), attachments=[{"guid": "at_0_HTML", "filename": filename,
                                                          "transfer_name": "page.html", "mime_type": "text/html",
                                                          "total_bytes": size}])
    conn.close()
    response = rw.client.get("/api/attachments/at_0_HTML")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert "sandbox" in response.headers["content-security-policy"]
    image = rw.client.get(f"/api/attachments/{rw.attachment('f_table')}")
    assert image.headers["content-disposition"].startswith("inline;")
    assert image.headers["x-content-type-options"] == "nosniff"


def test_avatars(ro):
    jordan = ro.client.get("/api/avatar/%2B14155550134")
    assert jordan.status_code == 200 and jordan.headers["content-type"] == "image/jpeg"
    assert jordan.content[:2] == b"\xff\xd8"  # the leading format byte was stripped
    assert ro.client.get("/api/avatar/jordan.park%40icloud.example").content == jordan.content
    priya = ro.client.get("/api/avatar/%2B14155550178")  # full image in Sources/<id>/Images/<uuid>
    assert priya.status_code == 200 and priya.content[:2] == b"\xff\xd8"
    assert ro.client.get("/api/avatar/%2B15105550123").status_code == 404  # Dad's photo file is missing
    assert ro.client.get("/api/avatar/%2B14155550155").status_code == 404  # not a contact


def test_serves_the_built_ui(make_rw, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Backchannel</title><div id=root></div>")
    (dist / "assets" / "app.js").write_text("console.log('hi')")
    (dist / "favicon.svg").write_text("<svg/>")
    harness = make_rw(BACKCHANNEL_DIST_DIR=str(dist))
    client = fresh_client(harness)
    index = client.get("/")
    assert index.status_code == 200 and "<div id=root>" in index.text and "bc_session=" in index.headers["set-cookie"]
    assert client.get("/assets/app.js").text == "console.log('hi')"
    assert client.get("/favicon.svg").status_code == 200
    deep = client.get("/c/dm:+14155550134")  # client-side route -> index.html
    assert deep.status_code == 200 and "<div id=root>" in deep.text
    (tmp_path / "secret.txt").write_text("TOP SECRET")
    for probe in ("/../secret.txt", "/..%2Fsecret.txt", "/assets/..%2F..%2Fsecret.txt"):
        assert "TOP SECRET" not in client.get(probe).text, probe
    missing = client.get("/api/not-a-route")  # API paths never fall back to the UI
    assert missing.status_code == 404 and missing.json() == {"error": "Not found."}
