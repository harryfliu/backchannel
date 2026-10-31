"""Stickers and Genmoji from chat.db: the library, its images, and saving one as a custom emoji."""

from __future__ import annotations

import os
import sys
import time
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

import media_gen
from server import fakedb
from server.imagetypes import sniff_format
from ts_shapes import validate

PNG = media_gen.png(20, 20, lambda x, y: (30, 160, 220))


def items(harness, **params):
    return harness.get("/api/stickers", params=params)["items"]


def sticker_file(harness, label: str) -> Path:
    row = harness.services.db.attachment_by_guid(harness.attachment(label))
    return harness.services.media.resolve(row["filename"])


def add_sticker_message(harness, chat: str, *, from_me: bool, name: str, data: bytes, when: float,
                        **attachment_columns) -> str:
    """Insert a message carrying one sticker attachment into the writable chat.db copy; returns its guid."""
    guid = fakedb.new_guid()
    filename, size = fakedb.store_attachment_file(harness.settings.attachments_root, guid, name, data)
    info = harness.manifest["chats"][chat]
    with closing(harness.db()) as conn:
        handle = 0 if from_me else conn.execute(
            "SELECT handle_id FROM chat_handle_join WHERE chat_id = ? LIMIT 1", (info["rowid"],)).fetchone()[0]
        fakedb.insert_message(conn, chat_rowid=info["rowid"], text=None, is_from_me=from_me, handle_rowid=handle,
                              date=when, attachments=[{
                                  "guid": guid, "filename": filename, "mime_type": "image/png", "uti": "public.png",
                                  "transfer_name": name, "total_bytes": size, "is_sticker": 1,
                                  **attachment_columns}])
    return guid


# --------------------------------------------------------------------------- the library


def test_library_order_dedupe_and_shape(ro):
    stickers = items(ro)
    ids = [s["id"] for s in stickers]
    assert ids == [
        ro.attachment("j_my_sticker"),  # the party sticker: Harry sent it twice (same bytes), newest copy
        ro.attachment("t_my_genmoji"),  # the dog Genmoji: Ellie's and Harry's copies share an identifier
        ro.attachment("soc_sticker"),  # Kevin's sticker tapback
        ro.attachment("d_ellie_sticker"),
    ]
    party, genmoji, tapback, ramen = stickers
    assert (party["kind"], party["timesUsed"], party["sentByMe"], party["description"]) == ("sticker", 2, True, None)
    assert party["lastUsedAt"] == ro.message("j_my_sticker")["sentAt"]
    assert (genmoji["kind"], genmoji["timesUsed"], genmoji["sentByMe"]) == ("genmoji", 2, True)
    assert genmoji["description"] == "dog wearing sunglasses"
    assert (tapback["timesUsed"], tapback["sentByMe"]) == (1, False)
    assert (ramen["timesUsed"], ramen["sentByMe"]) == (1, False)
    assert ro.attachment("j_gone_sticker") not in ids  # offloaded to iCloud: no file on disk
    assert ro.attachment("j_place") not in ids  # an ordinary photo
    for sticker in stickers:
        assert not validate(sticker, "StickerItem"), sticker
        assert sticker["url"] == f"/api/stickers/{quote(sticker['id'], safe='')}/image"
    assert [s["id"] for s in items(ro, limit=2)] == ids[:2]
    assert ro.client.get("/api/stickers", params={"limit": 0}).status_code == 400


def test_file_hashes_are_cached_by_mtime(rw):
    library = rw.services.stickers
    items(rw)
    hashed = {row["guid"] for row in rw.services.state._all("SELECT guid FROM file_hashes")}
    expected = {rw.attachment(label) for label in ("j_my_sticker", "f_my_sticker", "soc_sticker", "d_ellie_sticker")}
    assert hashed == expected  # Genmoji group by identifier, so they're never hashed
    # A stale-looking cached hash is believed while the file's mtime and size match...
    copy = rw.attachment("f_my_sticker")
    rw.services.state._exec("UPDATE file_hashes SET sha256 = 'not-the-real-hash' WHERE guid = ?", (copy,))
    library.invalidate()
    assert len(items(rw)) == 5  # the party sticker's two copies no longer match
    # ...and recomputed once the file changes.
    path = sticker_file(rw, "f_my_sticker")
    stat = path.stat()
    os.utime(path, (stat.st_atime, stat.st_mtime + 5))
    library.invalidate()
    assert len(items(rw)) == 4
    assert rw.services.state.cached_hash(copy, path.stat().st_mtime, stat.st_size) != "not-the-real-hash"


def test_library_follows_new_messages(rw):
    before = items(rw)
    assert before[-1]["id"] == rw.attachment("d_ellie_sticker")
    ramen = sticker_file(rw, "d_ellie_sticker").read_bytes()
    new = add_sticker_message(rw, "dinner", from_me=True, name="ramen.png", data=ramen, when=time.time() - 60)
    after = items(rw)  # chat.db changed, so the 5-minute cache doesn't apply
    assert after[0]["id"] == new  # used twice now, most recently
    assert (after[0]["timesUsed"], after[0]["sentByMe"]) == (2, True)
    assert len(after) == len(before)


# --------------------------------------------------------------------------- images


def test_sticker_images(ro):
    sticker_id = ro.attachment("j_my_sticker")
    response = ro.client.get(f"/api/stickers/{quote(sticker_id, safe='')}/image")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == sticker_file(ro, "j_my_sticker").read_bytes()
    assert "sandbox" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    genmoji = ro.client.get(f"/api/stickers/{quote(ro.attachment('e_genmoji'), safe='')}/image")
    assert genmoji.status_code == 200 and sniff_format(genmoji.content) == "png"  # older copies stay reachable
    for missing in (ro.attachment("j_gone_sticker"), ro.attachment("j_place"), "no-such-guid", "../../etc/passwd"):
        assert ro.client.get(f"/api/stickers/{quote(missing, safe='')}/image").status_code == 404, missing


FAKE_SIPS = r'''#!{python}
import sys
args = sys.argv[1:]
with open({log!r}, "a") as log:
    log.write(" ".join(args) + "\n")
out = args[args.index("--out") + 1]
open(out, "wb").write({png!r} if args[args.index("format") + 1] == "png" else b"")
'''


def test_heic_stickers_become_png(rw, tmp_path):
    guid = add_sticker_message(rw, "ellie", from_me=False, name="sticker.heic", data=media_gen.heic_iphone(300, 300),
                               when=time.time() - 120)
    url = f"/api/stickers/{quote(guid, safe='')}/image"
    assert guid not in [s["id"] for s in items(rw)]  # no sips on Linux: nothing an <img> could draw
    assert rw.client.get(url).status_code == 404
    log = tmp_path / "sips.log"
    sips = tmp_path / "sips"
    sips.write_text(FAKE_SIPS.format(python=sys.executable, log=str(log), png=PNG))
    sips.chmod(0o755)
    rw.services.media.sips = str(sips)
    rw.services.stickers.invalidate()
    assert guid in [s["id"] for s in items(rw)]
    for _ in range(2):
        response = rw.client.get(url)
        assert response.status_code == 200 and response.headers["content-type"] == "image/png"
        assert response.content == PNG
    calls = log.read_text().splitlines()
    assert len(calls) == 1 and calls[0].startswith("-s format png")  # converted once, then cached
    saved = rw.post(f"/api/stickers/{quote(guid, safe='')}/save", {"name": "heic-sticker"})
    assert saved["mimeType"] == "image/png" and saved["width"] == 20


# --------------------------------------------------------------------------- saving as a custom emoji


def test_save_sticker_as_emoji(rw):
    sticker_id = rw.attachment("j_my_sticker")
    saved = rw.post(f"/api/stickers/{quote(sticker_id, safe='')}/save", {"name": ":Party:"})
    assert not validate(saved, "CustomEmoji"), saved
    assert (saved["name"], saved["source"], saved["mimeType"], saved["width"], saved["height"], saved["animated"]) == (
        "party", "imessage", "image/png", 40, 40, False)
    assert rw.services.state.get_emoji("party")["source_ref"] == sticker_id
    assert [e["name"] for e in rw.get("/api/emoji")["items"]] == ["party"]
    image = rw.client.get(saved["url"])
    assert image.status_code == 200 and image.content == sticker_file(rw, "j_my_sticker").read_bytes()
    again = rw.client.post(f"/api/stickers/{quote(sticker_id, safe='')}/save", json={"name": "party"})
    assert again.status_code == 409
    assert rw.client.post(f"/api/stickers/{quote(sticker_id, safe='')}/save", json={"name": "x"}).status_code == 400
    assert rw.client.post(f"/api/stickers/{quote(sticker_id, safe='')}/save", json={}).status_code == 400
    gone = rw.client.post(f"/api/stickers/{quote(rw.attachment('j_gone_sticker'), safe='')}/save", json={"name": "gone"})
    assert gone.status_code == 404
    genmoji = rw.post(f"/api/stickers/{quote(rw.attachment('t_my_genmoji'), safe='')}/save", {"name": "cool-dog"})
    assert genmoji["source"] == "imessage" and (genmoji["width"], genmoji["height"]) == (64, 64)


def test_library_without_genmoji_columns(rw):
    """Before macOS 15 there are no Genmoji columns: plain stickers still work."""
    with closing(rw.db()) as conn:
        conn.execute("ALTER TABLE attachment DROP COLUMN emoji_image_content_identifier")
        conn.execute("ALTER TABLE attachment DROP COLUMN emoji_image_short_description")
    rw.services.db._columns.clear()  # chat.db's schema only changes across macOS updates (and restarts)
    stickers = items(rw)
    assert [s["id"] for s in stickers] == [rw.attachment(label) for label in ("j_my_sticker", "soc_sticker",
                                                                              "d_ellie_sticker")]
    assert all(s["kind"] == "sticker" and s["description"] is None for s in stickers)
