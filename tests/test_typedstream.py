"""attributedBody (typedstream) decoding."""

from __future__ import annotations

import pytest

from server.conversations import message_text
from server.typedstream import (
    HEADER,
    clean_text,
    decode_text,
    encode_attributed_body,
    extract_mentions,
    read_int,
)

# A real attributedBody for "Hello world" as written by Messages.app.
REAL_HELLO = bytes.fromhex(
    "040b73747265616d747970656481e803840140848484124e5341747472696275746564537472696e67008484084e534f626a65637400"
    "8592848484084e53537472696e67019484012b0b48656c6c6f20776f726c648684026949010b928484840c4e5344696374696f6e61727900"
    "9484016901928496961d5f5f6b494d4d657373616765506172744174747269627574654e616d658692848484084e534e756d626572008484"
    "074e5356616c7565009484012a84999900868686"
)


def test_decodes_a_real_messages_blob():
    assert decode_text(REAL_HELLO) == "Hello world"


def test_encoder_matches_apples_bytes():
    assert encode_attributed_body("Hello world") == REAL_HELLO


@pytest.mark.parametrize("length, prefix", [(5, b"\x05"), (127, b"\x7f"), (128, b"\x81\x80\x00"),
                                            (300, b"\x81\x2c\x01"), (40000, b"\x82\x40\x9c\x00\x00")])
def test_length_prefixes(length, prefix):
    text = "x" * length
    blob = encode_attributed_body(text)
    assert b"NSString\x01\x94\x84\x01+" + prefix + text[:10].encode() in blob
    assert decode_text(blob) == text


def test_multibyte_lengths_that_broke_the_old_decoder():
    # 300 bytes encodes as 81 2C 01; the old high-bit-skipping decoder kept a stray \x01.
    text = "a" * 299 + "!"
    assert decode_text(encode_attributed_body(text)) == text
    # 'ↄ' is E2 86 84 in UTF-8, which contains the 86 84 end marker the old decoder stopped at.
    tricky = "turn ↄ around " * 30
    assert decode_text(encode_attributed_body(tricky)) == tricky


def test_utf8_and_emoji_lengths_are_bytes_not_chars():
    text = "Tomás says 🎉🎉 ok — “quoted”"
    assert decode_text(encode_attributed_body(text)) == text


def test_object_replacement_marks_attachments():
    blob = encode_attributed_body("look at this", attachment_guids=["at_0_A", "at_1_B"])
    assert decode_text(blob) == "￼￼look at this"
    assert clean_text(decode_text(blob)) == "look at this"
    assert clean_text(decode_text(encode_attributed_body("", attachment_guids=["at_0_A"]))) == ""


def test_mentions_are_extracted():
    text = "Harry are you bringing the sake again? 🍶"
    blob = encode_attributed_body(text, mentions=[(0, 5, "+15105550100")])
    assert decode_text(blob) == text
    assert extract_mentions(blob) == ["+15105550100"]
    email = encode_attributed_body("hey Harry", mentions=[(4, 5, "harry.liu@icloud.example")])
    assert extract_mentions(email) == ["harry.liu@icloud.example"]
    assert extract_mentions(encode_attributed_body("no mentions here")) == []


def test_read_int():
    assert read_int(b"\x05", 0) == (5, 1)
    assert read_int(b"\x81\x2c\x01", 0) == (300, 3)
    assert read_int(b"\x82\x40\x9c\x00\x00", 0) == (40000, 5)
    assert read_int(b"\x81\x2c", 0) is None  # truncated


def test_heuristic_fallback_when_the_layout_is_unusual():
    # Drop the "\x84\x01" before "+": the structured parse fails, the heuristic still finds the text.
    damaged = REAL_HELLO.replace(b"\x94\x84\x01+", b"\x94+", 1)
    assert decode_text(damaged) == "Hello world"
    # A blob without the streamtyped header but with the usual class markers.
    assert decode_text(REAL_HELLO[len(HEADER):]) == "Hello world"


def test_garbage_never_raises():
    for blob in (b"", b"\x00\x01\x02", b"NSString", HEADER, HEADER + b"NSString\x01\x94\x84\x01+\x81", None):
        decode_text(blob)  # no exception
    assert decode_text(None) is None


def test_text_column_wins_and_blob_is_the_fallback():
    row = {"text": "from the text column", "attributedBody": encode_attributed_body("from the blob")}
    assert message_text(row) == "from the text column"
    row = {"text": None, "attributedBody": encode_attributed_body("from the blob")}
    assert message_text(row) == "from the blob"
    row = {"text": "￼", "attributedBody": encode_attributed_body("caption", attachment_guids=["at_0_X"])}
    assert message_text(row) == "caption"


def test_fixture_rows_mostly_have_null_text(fixture_src):
    import sqlite3

    conn = sqlite3.connect(fixture_src / "chat.db")
    total, null_text, with_body = conn.execute(
        "SELECT COUNT(*), SUM(text IS NULL), SUM(attributedBody IS NOT NULL) FROM message"
    ).fetchone()
    conn.close()
    assert null_text / total > 0.8 and with_body / total > 0.8
