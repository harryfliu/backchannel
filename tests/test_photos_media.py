"""Contact photos (Core Data storage), HEIF sizing, previews an <img> can draw, Genmoji."""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import quote

import pytest

import media_gen
from server.contacts import classify_blob
from server.imagetypes import sniff_format
from server.media import _heif_size, sniff_dimensions

JPEG = media_gen.jpeg(16, 16, lambda bx, by: (10, 200, 30))

FAKE_SIPS = r'''#!{python}
import sys
args = sys.argv[1:]
with open({log!r}, "a") as log:
    log.write(" ".join(args) + "\n")
out = args[args.index("--out") + 1]
open(out, "wb").write({jpeg!r})
'''


@pytest.fixture
def fake_sips(tmp_path) -> Path:
    script = tmp_path / "sips"
    script.write_text(FAKE_SIPS.format(python=sys.executable, log=str(tmp_path / "sips.log"), jpeg=JPEG))
    script.chmod(0o755)
    return script


def avatar_url(harness, conv_id):
    return harness.conversation(conv_id)["participants"][0]["avatarUrl"]


# --------------------------------------------------------------------------- contact photos


def test_blob_classification(tmp_path):
    png = media_gen.png(4, 4, lambda x, y: (1, 2, 3))
    assert classify_blob(b"\x01" + png[:63], tmp_path)[:2] == ("inline", "png")
    assert classify_blob(png[:64], tmp_path)[:2] == ("inline", "png")  # no tag byte
    external = tmp_path / ".AddressBook-v22_SUPPORT" / "_EXTERNAL_DATA"
    external.mkdir(parents=True)
    (external / "6E1B4C2A-0F7D-4E63-9B1A-2C3D4E5F6A7B").write_bytes(JPEG)
    status, fmt, path = classify_blob(b"\x026E1B4C2A-0F7D-4E63-9B1A-2C3D4E5F6A7B\x00", tmp_path)
    assert (status, fmt, path.name) == ("external", "jpeg", "6E1B4C2A-0F7D-4E63-9B1A-2C3D4E5F6A7B")
    assert classify_blob(b"\x02AAAAAAAA-0000-0000-0000-000000000000\x00", tmp_path)[0] == "missing_external"
    assert classify_blob(b"\x07junk \xff\xd8\xff\xe0 more", tmp_path)[0] == "invalid"  # no searching for \xff\xd8
    assert classify_blob(None, tmp_path)[0] == "none"


def test_only_real_photos_get_an_avatar_url(ro):
    assert avatar_url(ro, "dm:+14155550134")  # inline 0x01 JPEG thumbnail
    assert avatar_url(ro, "dm:+15105550122")  # 0x02 external-storage file
    assert avatar_url(ro, "dm:+14155550178")  # Images/<unique id> file
    assert avatar_url(ro, "dm:+16505550147")  # ZIMAGEDATA (no thumbnail)
    assert avatar_url(ro, "dm:+15105550123") is None  # external reference to a missing file
    assert avatar_url(ro, "dm:+16285550190") is None  # garbage blob with \xff\xd8 inside
    kevin = next(p for p in ro.conversation("g:chat100000000000000002")["participants"] if p["name"] == "Kevin Huang")
    assert kevin["avatarUrl"] is None  # a TIFF, and no sips here to convert it


def test_avatar_bytes_are_images(ro):
    for handle, kind in (("+14155550134", "image/jpeg"), ("+15105550122", "image/jpeg"), ("+14155550178", "image/jpeg"),
                         ("+16505550147", "image/png"), ("+15105550100", "image/png")):
        response = ro.client.get(f"/api/avatar/{quote(handle, safe='')}")
        assert response.status_code == 200 and response.headers["content-type"] == kind, handle
        assert sniff_format(response.content[:64]) == kind.split("/")[1], handle
    for handle in ("+15105550123", "+16285550190", "+14155550126"):
        assert ro.client.get(f"/api/avatar/{quote(handle, safe='')}").status_code == 404, handle


def test_photo_stats(ro):
    stats = ro.services.contacts.photo_stats()
    assert stats["inline"] == 3 and stats["external"] == 1 and stats["images_dir"] == 1
    assert stats["missing_external"] == 1 and stats["invalid"] == 1 and stats["unconvertible"] == 1


def test_tiff_photo_converted_with_sips(make_rw, fake_sips):
    harness = make_rw()
    harness.services.media.sips = str(fake_sips)
    harness.services.contacts.can_convert = True
    harness.services.contacts.reload_if_changed(force=True)
    harness.services.convs._list_cache = None
    kevin = next(p for p in harness.conversation("g:chat100000000000000002")["participants"] if p["name"] == "Kevin Huang")
    assert kevin["avatarUrl"] == "/api/avatar/%2B14155550126"
    response = harness.client.get(kevin["avatarUrl"])
    assert response.status_code == 200 and response.headers["content-type"] == "image/jpeg"
    assert response.content == JPEG


# --------------------------------------------------------------------------- HEIF sizing


def test_heif_uses_the_primary_item(tmp_path):
    assert _heif_size(media_gen.heic_iphone(4032, 3024, turns=1)) == (3024, 4032)
    assert _heif_size(media_gen.heic_iphone(4032, 3024, turns=0)) == (4032, 3024)
    assert _heif_size(media_gen.heic_iphone(4032, 3024, turns=2)) == (4032, 3024)
    assert _heif_size(media_gen.heic_without_pitm([(512, 512), (4032, 3024), (320, 240)])) == (4032, 3024)
    path = tmp_path / "IMG.HEIC"
    path.write_bytes(media_gen.heic_iphone(5712, 4284, turns=3))
    assert sniff_dimensions(path, "image") == (4284, 5712)


def test_heif_garbage_never_raises():
    good = media_gen.heic_iphone(4032, 3024)
    for cut in (20, 60, 120, 300, len(good) - 10):
        _heif_size(good[:cut])  # truncated files: no exception
    assert _heif_size(b"\x00\x00\x00\x10ftypheic" + b"\xff" * 40) is None


# --------------------------------------------------------------------------- previews


def test_thumbs_only_return_drawable_images(ro):
    jpeg = ro.client.get(f"/api/attachments/{ro.attachment('f_table')}/thumb")  # no sips: the JPEG itself
    assert jpeg.status_code == 200 and jpeg.headers["content-type"] == "image/jpeg"
    if not ro.services.media.sips:
        heic = ro.client.get(f"/api/attachments/{ro.attachment('j_place')}/thumb")
        assert heic.status_code == 404  # a HEIC isn't something an <img> can draw
    if not ro.services.media.qlmanage:
        video = next(m for m in ro.all_messages("dm:+16505550147") if m["attachments"]
                     and m["attachments"][0]["kind"] == "video")["attachments"][0]
        assert video["thumbUrl"] is None
        assert ro.client.get(video["url"] + "/thumb").status_code == 404  # never a .mov in an <img>
    pdf = ro.client.get(f"/api/attachments/{ro.attachment('p_menu')}/thumb")
    assert pdf.status_code == 404


def test_heic_thumbs_with_sips(make_rw, fake_sips):
    harness = make_rw()
    harness.services.media.sips = str(fake_sips)
    heic = harness.message("j_place")["attachments"][0]
    assert heic["thumbUrl"] == heic["url"] + "/thumb"
    thumb = harness.client.get(heic["thumbUrl"])
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg" and thumb.content == JPEG
    full = harness.client.get(heic["url"])
    assert full.headers["content-type"] == "image/jpeg" and full.content == JPEG
    assert "-Z 800" in (Path(fake_sips).parent / "sips.log").read_text()


def test_misnamed_heic_is_judged_by_its_bytes(rw):
    from server import fakedb

    conn = rw.db()
    chat = fakedb.chat_info(conn, "iMessage;-;+16505550147")
    hid = fakedb.ensure_handle(conn, "+16505550147", "iMessage")
    filename, size = fakedb.store_attachment_file(rw.settings.attachments_root, "at_0_MISNAMED", "IMG_0001.JPG",
                                                  media_gen.heic_iphone(4032, 3024))
    fakedb.insert_message(conn, chat_rowid=chat["ROWID"], text=None, is_from_me=False, handle_rowid=hid,
                          attachments=[{"guid": "at_0_MISNAMED", "filename": filename, "transfer_name": "IMG_0001.JPG",
                                        "mime_type": "image/jpeg", "uti": "public.jpeg", "total_bytes": size}])
    conn.close()
    response = rw.client.get("/api/attachments/at_0_MISNAMED")
    assert response.headers["content-type"] == "image/heic"  # not the image/jpeg the row claims
    if not rw.services.media.sips:
        assert rw.client.get("/api/attachments/at_0_MISNAMED/thumb").status_code == 404


def test_link_preview_and_sticker_images_are_drawable(ro):
    preview = ro.message("d_link")["linkPreview"]
    image = ro.client.get(preview["imageUrl"])
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    sticker = next(r for r in ro.message("soc_root")["reactions"] if r["kind"] == "sticker")
    assert ro.client.get(sticker["stickerUrl"]).headers["content-type"] == "image/png"


def test_genmoji(ro):
    genmoji = ro.message("e_genmoji")["attachments"][0]
    assert genmoji["isGenmoji"] is True and genmoji["emojiDescription"] == "dog wearing sunglasses"
    assert genmoji["kind"] == "image" and genmoji["thumbUrl"]
