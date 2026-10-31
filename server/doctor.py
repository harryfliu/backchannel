"""`python -m server.doctor [--ai]` (or `./backchannel doctor`): a report Harry can paste back.

Counts, formats, timings and versions only: never message text, names or
handles. Paths under the home folder are shown as ~. Exits non-zero when
chat.db can't be read.
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.request import pathname2url

from . import __version__
from .chatdb import ChatDBError
from .config import load_dotenv, load_settings
from .imagetypes import sniff_file
from .media import Media, kind_of, sniff_dimensions
from .timeutil import now_apple_ns

DAY_NS = 86400 * 1_000_000_000


def tilde(path: Any) -> str:
    text = str(path)
    home = str(Path.home())
    return "~" + text[len(home):] if text == home or text.startswith(home + os.sep) else text


class Report:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def section(self, title: str) -> None:
        if self.lines:
            self.lines.append("")
        self.lines.append(f"## {title}")

    def item(self, label: str, value: Any) -> None:
        self.lines.append(f"- {label}: {value}")

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


def macos_version() -> str:
    version = platform.mac_ver()[0]
    if version:
        return f"macOS {version}"
    return f"{platform.system()} {platform.release()} (not macOS)"


def _ms(start: float) -> str:
    return f"{(time.perf_counter() - start) * 1000:.0f} ms"


def check_chat_db(report: Report, services: Any) -> bool:
    report.section("chat.db")
    db = services.db
    report.item("path", tilde(db.path))
    readable, error = db.readable()
    report.item("readable", "yes" if readable else "NO")
    if not readable:
        report.item("error", (error or "").replace(str(Path.home()), "~"))
        return False
    counts = db.query(
        """SELECT (SELECT COUNT(*) FROM message) AS messages, (SELECT COUNT(*) FROM chat) AS chats,
                  (SELECT COUNT(*) FROM handle) AS handles, (SELECT COUNT(*) FROM attachment) AS attachments,
                  (SELECT MAX(date) FROM message) AS newest"""
    )[0]
    for key in ("messages", "chats", "handles", "attachments"):
        report.item(key, f"{counts[key]:,}")
    if counts["newest"]:
        from .timeutil import apple_to_ns

        age = (now_apple_ns() - apple_to_ns(counts["newest"])) / 3.6e12
        report.item("newest message", f"{age:.1f} hours ago")
    cols = db.columns("message")
    present = [c for c in ("date_edited", "date_retracted", "thread_originator_guid", "associated_message_emoji")
               if c in cols]
    report.item("newer message columns present", ", ".join(present) or "none")
    report.item("attachment Genmoji columns", "yes" if "emoji_image_content_identifier" in db.columns("attachment") else "no")
    return True


def check_attachments(report: Report, services: Any, days: int = 90) -> None:
    report.section(f"Attachments (last {days} days)")
    db, media = services.db, services.media
    since = now_apple_ns() - days * DAY_NS
    select = db.select_list("attachment", "a", ("ROWID", "guid", "filename", "mime_type", "uti", "transfer_name",
                                               "is_sticker", "hide_attachment", "emoji_image_content_identifier"))
    rows = db.query(
        f"""SELECT {select} FROM attachment a
            JOIN message_attachment_join maj ON maj.attachment_id = a.ROWID
            JOIN message m ON m.ROWID = maj.message_id
            WHERE m.date > ? ORDER BY m.date DESC""",
        (since,),
    )
    kinds: Counter[str] = Counter()
    exts: Counter[str] = Counter()
    where: Counter[str] = Counter()
    formats: Counter[str] = Counter()
    samples: dict[str, list[tuple[str, Path]]] = {"convert": [], "video": [], "heif": []}
    seen: set[int] = set()
    for row in rows:
        if row["ROWID"] in seen:
            continue
        seen.add(row["ROWID"])
        name = row["transfer_name"] or os.path.basename(row["filename"] or "")
        if name.endswith(".pluginPayloadAttachment"):
            where["link-preview payloads"] += 1
            continue
        kind = kind_of(row["mime_type"], row["uti"], name)
        kinds[kind] += 1
        exts[(Path(name).suffix.lower() or "(none)")] += 1
        if row["is_sticker"]:
            where["stickers"] += 1
        if row["emoji_image_content_identifier"]:
            where["Genmoji"] += 1
        if not row["filename"]:
            where["no file name"] += 1
            continue
        path = media.resolve(row["filename"])
        if path is None:
            where["outside the Messages folder"] += 1
            continue
        if not path.is_file():
            where["missing (in iCloud or deleted)"] += 1
            continue
        where["on disk"] += 1
        if kind == "image":
            fmt = sniff_file(path) or "unknown"
            formats[fmt] += 1
            if fmt in ("heic", "tiff") and len(samples["convert"]) < 3:
                samples["convert"].append((row["guid"], path))
            if fmt == "heic" and len(samples["heif"]) < 50:
                samples["heif"].append((row["guid"], path))
        elif kind == "video" and len(samples["video"]) < 2:
            samples["video"].append((row["guid"], path))
    report.item("attachments", f"{len(seen):,}")
    report.item("by kind", ", ".join(f"{k} {v:,}" for k, v in kinds.most_common()) or "none")
    report.item("by extension", ", ".join(f"{k} {v:,}" for k, v in exts.most_common(15)) or "none")
    report.item("files", ", ".join(f"{k} {v:,}" for k, v in where.most_common() if v) or "none")
    report.item("image formats on disk (by bytes)", ", ".join(f"{k} {v:,}" for k, v in formats.most_common()) or "none")
    if samples["heif"]:
        sized = sum(1 for _, p in samples["heif"] if sniff_dimensions(p, "image") != (None, None))
        report.item("HEIC sizes read", f"{sized} of {len(samples['heif'])} sampled")

    with tempfile.TemporaryDirectory(prefix="bc-doctor-") as tmp:
        scratch = Media(services.settings.attachments_root, Path(tmp),
                        sticker_cache_root=services.settings.sticker_cache_root,
                        messages_container_root=services.settings.messages_container_root)
        report.item("sips", tilde(scratch.sips) if scratch.sips else "not found")
        report.item("qlmanage", tilde(scratch.qlmanage) if scratch.qlmanage else "not found")
        report.item("afconvert", tilde(scratch.afconvert) if scratch.afconvert else "not found")
        if samples["convert"] and not scratch.sips:
            report.item("HEIC/TIFF → JPEG", f"skipped: sips not available ({len(samples['convert'])} file(s) to try)")
            samples["convert"] = []
        if samples["video"] and not (scratch.sips and scratch.qlmanage):
            report.item("video thumbnail", "skipped: qlmanage/sips not available")
            samples["video"] = []
        for index, (guid, path) in enumerate(samples["convert"], start=1):
            start = time.perf_counter()
            out, mime = scratch.full_file(path, f"doctor-{index}", "application/octet-stream")
            ok = mime == "image/jpeg" and out != path
            report.item(f"HEIC/TIFF → JPEG #{index} ({sniff_file(path)})", f"{'OK' if ok else 'FAILED'} in {_ms(start)}")
        if not samples["convert"] and scratch.sips:
            report.item("HEIC/TIFF → JPEG", "no HEIC/TIFF files on disk to try")
        for index, (guid, path) in enumerate(samples["video"], start=1):
            start = time.perf_counter()
            thumb = scratch.thumbnail(path, f"doctor-video-{index}", "video")
            report.item(f"video thumbnail #{index} ({path.suffix.lower()})", f"{'OK' if thumb else 'FAILED'} in {_ms(start)}")
        if not samples["video"] and scratch.qlmanage:
            report.item("video thumbnail", "no videos on disk to try")


def check_contacts(report: Report, services: Any) -> None:
    report.section("Contacts")
    contacts = services.contacts
    report.item("AddressBook databases", len(contacts.db_paths()))
    report.item("contacts with a phone or email", f"{contacts.count:,}")
    stats = contacts.photo_stats()
    order = ("inline", "external", "images_dir", "unconvertible", "invalid", "missing_external", "none")
    report.item("photos by storage", ", ".join(f"{k} {stats.get(k, 0):,}" for k in order))
    report.item("HEIC/TIFF photos convertible", "yes (sips)" if contacts.can_convert else "no")
    if contacts.error:
        report.item("errors", contacts.error.replace(str(Path.home()), "~"))
    try:
        me = services.convs.me()
        card = next((contacts.lookup(h) for h in me.handles if contacts.lookup(h)), None)
        report.item("your handles found", len(me.handles))
        report.item("your Contacts card", "found" if card else "not found")
        report.item("your photo", "yes" if me.avatar_handle else "no")
    except ChatDBError:
        report.item("your handles", "unknown (chat.db unreadable)")


def check_ai(report: Report, services: Any, live: bool) -> None:
    report.section("AI")
    ai = services.ai
    report.item("ai.backend setting", ai.mode)
    binary = ai.cli.locate(force=True)
    report.item("claude", tilde(binary) if binary else "not found")
    if binary:
        report.item("claude --version", ai.cli.version() or "failed")
    report.item("ANTHROPIC_API_KEY set", "yes" if ai.has_api_key() else "no")
    report.item("backend in use", ai.backend or "none (AI off)")
    report.item("models", f"drafts {ai.model_label}" if ai.backend else "-")
    if live:
        if ai.backend is None:
            report.item("test call", "skipped: no backend")
            return
        start = time.perf_counter()
        try:
            answer = ai.call_text(system="You are a connectivity check. Reply with exactly: OK", user="Reply with OK")
            report.item("test call", f"{'OK' if 'OK' in answer.upper() else 'unexpected answer'} in {_ms(start)}"
                                     f" ({answer.strip()[:20]!r})")
        except Exception as exc:  # report, don't crash
            report.item("test call", f"FAILED in {_ms(start)}: {exc}")


def check_chief(report: Report, services: Any) -> None:
    report.section("Chief")
    chief = services.chief
    report.item("chief_db", tilde(chief.path) if chief.path else "not configured")
    report.item("found", "yes" if chief.available else "no")
    if chief.available:
        try:
            conn = sqlite3.connect(f"file:{pathname2url(str(chief.path))}?mode=ro", uri=True, timeout=5)
            try:
                people = conn.execute("SELECT COUNT(*) FROM people").fetchone()[0]
                loops = conn.execute("SELECT COUNT(*) FROM open_loops WHERE status IN ('open', 'in_progress')").fetchone()[0]
            finally:
                conn.close()
            report.item("people", people)
            report.item("open loops", loops)
        except sqlite3.Error as exc:
            report.item("read error", str(exc))


def check_messages_ui(report: Report, services: Any, tree: bool = False) -> None:
    """Accessibility structure of Messages, for tuning the Messages helper scripts. No names or text.

    It opens the newest bubble's context menu, the Tapback picker and the Reply
    field (choosing and typing nothing), and closes each with Escape.
    """
    from . import messages_ui

    report.section("Messages UI (Accessibility)")
    settings = services.state.get_settings()
    for key in messages_ui.HELPER_SETTINGS:
        report.item(f"{key} setting", "on" if settings.get(key) else "off")
    health = services.messages_ui.health()
    report.item("last result", "(none)" if not health["lastAt"] else f"at {health['lastAt']}, permission {health['permission']}")
    start = time.perf_counter()
    private = services.contacts.private_names()
    try:
        me = services.convs.me().name.strip().lower()
        if len(me) >= 3:
            private.add(me)
    except ChatDBError:
        pass
    for line in messages_ui.inspect(runner=messages_ui.osascript_runner, private=private, tree=tree):
        report.lines.append(line)
    report.item("inspection took", _ms(start))


def check_messages_try(report: Report, services: Any) -> None:
    """Live check of the helpers, in your own chat only: press Heart on its newest message with Messages in
    the background (then in front if that didn't take), confirm it in chat.db and take it off again; press
    Reply both ways and see where the focus goes, then Escape. Nothing is typed or sent."""
    from . import messages_ui

    report.section("Messages helpers, live try (your own chat only)")
    try:
        me = services.convs.me()
    except ChatDBError:
        report.item("result", "skipped: chat.db isn't readable")
        return
    private = services.contacts.private_names()
    if len(me.name.strip()) >= 3:
        private.add(me.name.strip().lower())
    title = messages_ui.inspect_mode(["title"])
    open_title = ""
    for line in title.stdout.splitlines():
        if line.startswith("title_text\t"):
            open_title = line.split("\t", 1)[1].strip().lower()
    mine = {me.name.strip().lower(), me.short_name.strip().lower()} - {""}
    if not open_title or open_title not in mine:
        report.item("result", "skipped: open your own chat in Messages first (your name at the top), then run this again")
        return
    report.item("open chat", "yours")
    db = services.db

    def newest_rowid() -> int:
        rows = db.query("SELECT MAX(ROWID) AS m FROM message")
        return int(rows[0]["m"] or 0) if rows else 0

    def wait_for(after: int, kinds: tuple[int, ...], seconds: float = 5.0) -> int | None:
        marks = ",".join("?" * len(kinds))
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            rows = db.query(f"SELECT associated_message_type AS t FROM message WHERE ROWID > ? AND is_from_me = 1"
                            f" AND associated_message_type IN ({marks}) ORDER BY ROWID DESC LIMIT 1", (after, *kinds))
            if rows:
                return int(rows[0]["t"])
            time.sleep(0.4)
        return None

    def show(outcome: Any) -> None:
        for line in messages_ui.inspect(private=private, raw=outcome):
            if not line.startswith(("- Accessibility", "- Messages running", "- windows", "- menu bar", "- Conversation menu")):
                report.lines.append(line)

    landed_where = None
    for where in ("back", "front"):
        before = newest_rowid()
        outcome = messages_ui.inspect_mode(["try-tapback", where, "R:Heart"])
        if where == "back":
            show(outcome)
        landed = wait_for(before, (2000,))
        report.item(f"Heart with Messages in the {'background' if where == 'back' else 'front'}",
                    "landed in chat.db" if landed else "didn't land")
        if landed:
            landed_where = where
            break
    if landed_where:
        before = newest_rowid()
        messages_ui.inspect_mode(["try-tapback", landed_where, "R:Heart"])
        report.item("took the heart off again", "yes" if wait_for(before, (3000,)) else "no: remove it by hand")
    for where in ("back", "front"):
        report.lines.append(f"- Reply with Messages in the {'background' if where == 'back' else 'front'}:")
        show(messages_ui.inspect_mode(["try-reply", where]))


def check_system(report: Report, services: Any) -> None:
    settings = services.settings
    report.section("Backchannel")
    report.item("version", __version__)
    report.item("system", macos_version())
    report.item("python", f"{platform.python_version()} ({tilde(sys.executable)})")
    report.item("send mode", f"{services.sender.availability}" + (" + fake chat.db" if settings.fake_db else ""))
    report.item("osascript", tilde(shutil.which("osascript")) if shutil.which("osascript") else "not found")
    report.item("state dir", tilde(settings.state_dir))
    if (settings.dist_dir / "index.html").is_file():
        report.item("web UI", "built")
    else:
        report.item("web UI", "not built (web/dist missing)")


_SHOWABLE = re.compile(r"[A-Za-z][A-Za-z .'’…|_-]{0,40}")


def check_messages_reply_test(report: Report, services: Any) -> None:
    """In the user's own chat only: start a reply to the newest message each way Messages may allow
    (its Reply… action, its right-click menu's Reply, a Reply command in the menu bar). When one
    switches Messages into reply mode, send a short test reply there and check chat.db for the
    thread; stop at the first that threads (and let replies to others go through from then on)."""
    from . import messages_ui
    from .conversations import message_text

    report.section("Messages helpers, reply test (your own chat only)")
    try:
        me = services.convs.me()
    except ChatDBError:
        report.item("result", "skipped: chat.db isn't readable")
        return
    db, helpers = services.db, services.messages_ui
    private = services.contacts.private_names()
    names = [n for n in (me.name.strip(), me.short_name.strip()) if n]

    def newest_rowid() -> int:
        rows = db.query("SELECT MAX(ROWID) AS m FROM message")
        return int(rows[0]["m"] or 0) if rows else 0

    def sent_row(after: int, text: str, seconds: float = 6.0) -> Any | None:
        deadline = time.monotonic() + seconds
        fields = ("ROWID", "guid", "text", "attributedBody", "thread_originator_guid")
        while time.monotonic() < deadline:
            for row in db.query(f"SELECT {db.message_select(fields=fields)} FROM message m"
                                " WHERE m.ROWID > ? AND m.is_from_me = 1 ORDER BY m.ROWID", (after,)):
                if (message_text(row) or "").strip() == text:
                    return row
            time.sleep(0.4)
        return None

    def show(value: str) -> str:
        value = (value or "").strip()
        if value in ("", "-"):
            return "-"
        if not _SHOWABLE.fullmatch(value) or any(p in value.lower() for p in private):
            return "(hidden)"
        return value

    for number, method in enumerate(("action", "menu", "menubar"), 1):
        text = f"Backchannel reply test {number}"
        before = newest_rowid()
        outcome = helpers.reply_test(method, text, names)
        if outcome.timed_out:
            report.item(method, "timed out")
            continue
        got: dict[str, list[str]] = {}
        for line in outcome.stdout.splitlines():
            key, _, rest = line.partition("\t")
            got[key] = rest.split("\t")
        if "rt" in got:
            answer = got["rt"][0]
            if answer == "not-your-chat":
                report.item("result", "skipped: open your own chat in Messages first (your name at the top), then run this again")
                return
            report.item(method, re.sub(r"[^a-z-]", "", answer) or "no answer")
            continue
        if not got:
            report.item(method, f"the script failed: {(outcome.stderr.splitlines() or ['no answer'])[-1][:160]}")
            continue
        hint, focus, marks = got.get("rt_hint", ["", ""]), got.get("rt_focus", ["", ""]), got.get("rt_marks", ["", ""])
        report.item(method, f"started: {show(got.get('rt_did', ['?'])[0])}; placeholder {show(hint[0])} → "
                            f"{show(hint[-1])}; focused field {show(focus[0])} → {show(focus[-1])}; "
                            f"entry area {marks[0]} → {marks[-1]}; reply mode: {got.get('rt_engaged', ['?'])[0]}")
        if "rt_hint_with_text" in got:
            report.item(f"{method}, placeholder with the text in", show(got["rt_hint_with_text"][0]))
        if got.get("rt_sent", [""])[0] != "yes":
            if "rt_sent" in got:
                report.item(f"{method}, test reply", "not sent (the text didn't land in the field)")
            continue
        row = sent_row(before, text)
        if row is None:
            report.item(f"{method}, test reply", "sent, but it isn't in chat.db")
        elif row["thread_originator_guid"]:
            report.item(f"{method}, test reply", "THREADED")
            helpers.note_reply_proof(True)
            report.item("result", f"real replies work on this Mac ({method}); replies to others are on")
            return
        else:
            report.item(f"{method}, test reply", "went out as a regular text (not threaded)")
    report.item("result", "no way of starting a reply threaded")


def check_messages_open(report: Report, services: Any, name: str) -> None:
    """Find a conversation by the name Backchannel shows and bring it up in Messages, the way
    tapbacks and replies do (link for one-to-one chats, else its sidebar item, scrolling to it).
    Nothing is typed or sent. Prints the steps (names of steps and item positions only)."""
    from . import messages_ui

    report.section("Messages helpers, open a chat")
    try:
        summaries = services.convs.list()
    except ChatDBError:
        report.item("result", "skipped: chat.db isn't readable")
        return
    want = " ".join(name.split()).lower()
    exact = [c for c in summaries if (c.get("title") or "").lower() == want]
    loose = [c for c in summaries if want in (c.get("title") or "").lower()]
    found = exact or loose
    if not found:
        report.item("result", "no conversation in Backchannel has that name")
        return
    conv = services.convs.get(found[0]["id"])
    report.item("conversation", f"found ({conv.kind}{', more than one match: used the first' if len(found) > 1 else ''})")
    report.item("Messages link", "yes" if messages_ui.messages_link(conv) else "no (group or unusual handle)")
    started = time.monotonic()
    outcome = messages_ui.inspect_mode(services.messages_ui.open_argv(conv), timeout=90.0)
    took = int((time.monotonic() - started) * 1000)
    if outcome.timed_out:
        report.item("result", f"timed out after {took} ms")
        return
    answer, trail = "", ""
    for line in outcome.stdout.splitlines():
        if line.startswith("open\t"):
            parts = line.split("\t")
            answer = parts[1] if len(parts) > 1 else ""
            trail = re.sub(r"[^a-z0-9:,/ -]", "", (parts[2] if len(parts) > 2 else "").lower())
    if not answer:
        detail = (outcome.stderr.splitlines() or [""])[-1][:160]
        report.item("result", f"the script failed: {detail or 'no answer'}")
        return
    report.item("result", {"ok": "open in Messages", "row-not-found": "not found in Messages' sidebar",
                           "not-selected": "found, but couldn't open it", "no-list": "Messages' sidebar not found"}
                .get(answer, re.sub(r"[^a-z-]", "", answer) or "?"))
    report.item("steps", trail or "-")
    report.item("took", f"{took} ms")


def run(argv: list[str] | None = None, *, dotenv: bool = True) -> tuple[int, str]:
    parser = argparse.ArgumentParser(prog="backchannel doctor", description=__doc__)
    parser.add_argument("--ai", action="store_true", help="also make one tiny real Claude call")
    parser.add_argument("--messages-try", action="store_true",
                        help="live check in your own chat: press Heart (then take it off) and Reply (then Escape)")
    parser.add_argument("--messages-tree", action="store_true",
                        help="with --messages-ui: map the whole Messages window instead (slow, a minute or two)")
    parser.add_argument("--messages-reply-test", action="store_true",
                        help="in your own chat: try each way of starting a reply; for the first that puts "
                             "Messages in reply mode, send a short test reply to yourself and check it threaded")
    parser.add_argument("--messages-open", metavar="NAME",
                        help="bring a conversation up in Messages the way tapbacks and replies do (its name as "
                             "Backchannel shows it); nothing is sent. Prints how it got there")
    parser.add_argument("--messages-ui", action="store_true",
                        help="describe what the scripts can reach in Messages (spots, label shapes and action "
                             "names only); opens the newest bubble's menu and Tapback picker and closes them "
                             "again without choosing anything")
    args = parser.parse_args(argv)
    if dotenv:
        load_dotenv()
    from .app import Services  # imported late so `--help` stays fast

    settings = load_settings()
    services = Services(settings)
    report = Report()
    try:
        check_system(report, services)
        readable = check_chat_db(report, services)
        if readable:
            check_attachments(report, services)
        check_contacts(report, services)
        check_ai(report, services, live=args.ai)
        check_chief(report, services)
        if args.messages_ui or args.messages_tree:
            check_messages_ui(report, services, tree=args.messages_tree)
        if args.messages_try:
            check_messages_try(report, services)
        if args.messages_open:
            check_messages_open(report, services, args.messages_open)
        if args.messages_reply_test:
            check_messages_reply_test(report, services)
    finally:
        services.close()
    report.section("Result")
    report.item("chat.db", "readable" if readable else "NOT readable: give your terminal (or the Backchannel app) "
                "Full Disk Access in System Settings → Privacy & Security")
    return (0 if readable else 1), report.text()


def main() -> None:
    code, text = run()
    sys.stdout.write(text)
    sys.exit(code)


if __name__ == "__main__":
    main()
