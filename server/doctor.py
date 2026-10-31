"""`python -m server.doctor [--ai]` (or `./backchannel doctor`): a report Harry can paste back.

Counts, formats, timings and versions only: never message text, names or
handles. Paths under the home folder are shown as ~. Exits non-zero when
chat.db can't be read.
"""

from __future__ import annotations

import argparse
import os
import platform
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
        scratch = Media(services.settings.attachments_root, Path(tmp))
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


def check_messages_ui(report: Report, services: Any) -> None:
    """Accessibility structure of Messages, for tuning the mark-read script. No names or text."""
    from . import messages_ui

    report.section("Messages UI (Accessibility)")
    health = services.messages_ui.health()
    report.item("markReadInMessages setting", "on" if health["enabled"] else "off")
    report.item("last result", "(none)" if not health["lastAt"] else f"at {health['lastAt']}, permission {health['permission']}")
    start = time.perf_counter()
    for line in messages_ui.inspect(runner=messages_ui.osascript_runner):
        report.lines.append(line)
    report.item("inspection took", _ms(start))


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


def run(argv: list[str] | None = None, *, dotenv: bool = True) -> tuple[int, str]:
    parser = argparse.ArgumentParser(prog="backchannel doctor", description=__doc__)
    parser.add_argument("--ai", action="store_true", help="also make one tiny real Claude call")
    parser.add_argument("--messages-ui", action="store_true",
                        help="describe the Messages window's accessibility structure (roles and counts only)")
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
        if args.messages_ui:
            check_messages_ui(report, services)
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
