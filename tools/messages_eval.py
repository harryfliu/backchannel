"""Time and check tapbacks and thread replies in the real Messages app, in your own chat only.

    .venv/bin/python tools/messages_eval.py --tapbacks 4 --replies 3 [--cold] [--depths 2,6,12,24]
    .venv/bin/python tools/messages_eval.py --sidebar

--sidebar touches nothing: it reads every row of Messages' sidebar once (scrolling it in the
background, then back to the top) and checks that each of your conversations matches one the way
the helpers look for it, so a name Messages writes differently shows up here, not as a failed
tapback. Chats Messages files apart (unknown senders, businesses) are listed separately.

Runs the same code the server runs (MessagesUI), on older messages in your note-to-self chat, and
prints how long each step took and whether chat.db shows the tapback or reply on the right message.
Every tapback it adds it removes again; replies stay (they're in your own chat). --cold first
switches Messages to another chat that has nothing unread, the way it usually is when you act from
Backchannel. Needs Accessibility for the terminal that runs it, an unlocked screen, and the
Backchannel server's settings (it copies state.db, so nothing of the server's changes).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server import messages_ui as UI  # noqa: E402
from server.app import Services  # noqa: E402
from server.config import load_settings  # noqa: E402
from server.messages_scripts import (  # noqa: E402
    MARK_READ_SCRIPT, REPLY_AIM_SCRIPT, REPLY_FINISH_SCRIPT, REPLY_SCRIPT, TAPBACK_SCRIPT,
)

NAMES = {id(TAPBACK_SCRIPT): "tapback", id(REPLY_AIM_SCRIPT): "aim", id(REPLY_FINISH_SCRIPT): "finish",
         id(REPLY_SCRIPT): "menubar", id(MARK_READ_SCRIPT): "open"}


class Clock:
    """Wraps the helper's runner, clicks and chat.db waits to time each step of one action."""

    def __init__(self, ui: UI.MessagesUI):
        self.steps: list[tuple[str, float, str]] = []
        real_runner, real_right, real_ctrl = ui.runner, ui.right_click, ui.control_click
        real_tap, real_row = ui.verify_tapback, ui.find_reply_row

        def runner(script, argv, timeout):
            t = time.perf_counter()
            out = real_runner(script, argv, timeout)
            self.steps.append((NAMES.get(id(script), "script"), time.perf_counter() - t,
                               out.stdout.splitlines()[0][:160] if out.stdout else f"rc{out.returncode}"))
            return out

        def timed(name, fn):
            def wrapper(*a, **k):
                t = time.perf_counter()
                out = fn(*a, **k)
                self.steps.append((name, time.perf_counter() - t, ""))
                return out
            return wrapper

        ui.runner = runner
        if ui.ax is not None:
            for name in ("tapback", "reply"):
                real = getattr(ui.ax, name)

                def timed_ax(job, real=real, name=name):
                    t = time.perf_counter()
                    out = real(job)
                    lines = out.stdout.splitlines()
                    marks = next((ln.split("\t", 1)[1] for ln in lines if ln.startswith("times\t")), "")
                    self.steps.append((f"ax-{name}", time.perf_counter() - t, f"{lines[0][:120]} ({marks})"))
                    return out
                setattr(ui.ax, name, timed_ax)
        ui.right_click = timed("right-click", real_right)
        ui.control_click = timed("ctrl-click", real_ctrl)
        ui.verify_tapback = timed("verify", real_tap)
        ui.find_reply_row = timed("verify", real_row)

    def take(self) -> list[tuple[str, float, str]]:
        steps, self.steps = self.steps, []
        return steps


def services() -> Services:
    settings = load_settings()
    work = Path(tempfile.mkdtemp(prefix="bc-eval-"))
    shutil.copy2(settings.state_dir / "state.db", work / "state.db")
    settings = dataclasses.replace(settings, state_dir=work, cache_dir=work / "cache")
    return Services(settings)


def own_chat(svc: Services):
    me = svc.convs.directory().me
    for conv in svc.convs.directory().convs.values():
        if conv.is_self:
            return conv
    raise SystemExit(f"No note-to-self chat found for {me}.")


def targets(svc: Services, conv, depths: list[int]) -> list[dict[str, Any]]:
    """Messages `depth` back from the newest (counting each self-chat pair once), with text."""
    recent = svc.builder.recent(conv, 400)
    seen, picked, uniq = set(), [], []
    for m in reversed(recent):  # newest first
        if m.get("event") or m.get("unsent") or not (m.get("text") or "").strip():
            continue
        key = (m["text"].strip()[:80])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(m)
    for d in depths:
        if d < len(uniq):
            picked.append({**uniq[d], "_depth": d})
    return picked


def other_quiet_chat(svc: Services, conv) -> Any | None:
    """A recent chat with nothing unread (opening it changes nothing), to start from."""
    for c in svc.convs.list():
        if (c["id"] != conv.id and c["id"].startswith("dm:") and c.get("unreadCount", 1) == 0
                and not c.get("isAutomated")):
            return svc.convs.get(c["id"])
    return None


def show(steps, total, result) -> str:
    parts = " ".join(f"{n}={d:.2f}s" + (f"[{c}]" if c and n.startswith("ax-") else "") for n, d, c in steps)
    return f"{total:5.2f}s  {result:<40} {parts}"


def sidebar_check(svc: Services) -> None:
    from server.messages_ax import _Run, row_index

    ui = svc.messages_ui
    pid = ui.ax.find_pid()
    if not pid:
        raise SystemExit("Messages isn't running.")
    run = _Run(pid, ui.ax.application(pid))
    if not ui.ax._look(run) or run.spots.get("ConversationList") is None:
        raise SystemExit("Couldn't find Messages' conversation list.")
    chats = run.spots["ConversationList"]
    labels: list[str] = []

    def grab(rows) -> bool:
        labels.extend(label for label, _top in rows if label and label not in labels)
        return False

    ui.ax._sweep(chats, "AXScrollUpByPage", lambda rows: False, 200)
    grab(ui.ax._snapshot(chats))
    pages = ui.ax._sweep(chats, "AXScrollDownByPage", grab, 400)
    ui.ax._sweep(chats, "AXScrollUpByPage", lambda rows: False, pages + 10)
    convs = [c for c in svc.convs.list() if not c["isAutomated"]]
    contacts, others = [], []
    for c in convs:
        conv = svc.convs.get(c["id"])
        _title, names, loose = ui._names(conv)
        if row_index(labels, [], names, loose, ui._members(conv)) < 0:
            known = any(p.is_contact for p in conv.persons)
            (contacts if known else others).append(f"{c['title']} ({c['kind']}, last {(c.get('lastMessage') or {}).get('sentAt', '')[:10]})")
    print(f"{len(labels)} rows in Messages' sidebar ({pages} pages); {len(convs)} conversations checked")
    print(f"not found, contacts and groups (these would fail): {len(contacts)}")
    for line in contacts:
        print("  ", line)
    print(f"not found, unknown senders (Messages files them apart; links still reach one-to-one chats): {len(others)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tapbacks", type=int, default=3)
    ap.add_argument("--replies", type=int, default=0)
    ap.add_argument("--depths", default="2,6,12,24")
    ap.add_argument("--kind", default="like")
    ap.add_argument("--cold", action="store_true", help="switch Messages to another chat before each action")
    ap.add_argument("--json", help="also write results here")
    ap.add_argument("--applescript", action="store_true", help="the old helpers only (to compare)")
    ap.add_argument("--sidebar", action="store_true", help="check every conversation can be found (read-only)")
    args = ap.parse_args()

    svc = services()
    ui = svc.messages_ui
    if ui.runner is None:
        raise SystemExit("The helper isn't using osascript (dry run or fake chat.db).")
    if args.sidebar:
        return sidebar_check(svc)
    if args.applescript:
        ui.ax = None
    clock = Clock(ui)
    conv = own_chat(svc)
    depths = [int(x) for x in args.depths.split(",")]
    picks = targets(svc, conv, depths)
    quiet = other_quiet_chat(svc, conv) if args.cold else None
    results: list[dict[str, Any]] = []

    def go_cold():
        link = UI.messages_link(quiet) if quiet is not None else None
        if link:  # opened in the background, like the helper does
            subprocess.run(["/usr/bin/open", "-g", link], capture_output=True, timeout=5)
            time.sleep(1.5)

    def record(kind, depth, total, steps, outcome, ok):
        results.append({"kind": kind, "depth": depth, "seconds": round(total, 2), "ok": ok, "outcome": outcome,
                        "steps": [(n, round(d, 2), c) for n, d, c in steps]})
        print(f"{kind:<8} d{depth:<3} {'OK ' if ok else 'BAD'} " + show(steps, total, outcome), flush=True)

    for i in range(args.tapbacks):
        m = picks[i % len(picks)]
        for remove in (False, True):
            go_cold()
            clock.take()
            job = ui.tapback_job(conv, m["id"], args.kind, remove)
            seen: list[dict] = []
            ui.publish = seen.append
            t = time.perf_counter()
            ui._run_tapback(job)
            total = time.perf_counter() - t
            done = [e for e in seen if e.get("type") == "ui-action"]
            outcome = (done[-1].get("result"), done[-1].get("detail")) if done else ("?", "")
            ok = outcome[0] == "done"
            if ui.ax is not None:
                ui.ax.tidy()
            record("untap" if remove else "tap", m["_depth"], total, clock.take(), f"{outcome[0]}: {outcome[1]}", ok)

    for i in range(args.replies):
        m = picks[i % len(picks)]
        go_cold()
        clock.take()
        text = f"eval reply {i + 1} ({time.strftime('%H:%M:%S')}) to d{m['_depth']}"
        job = ui.reply_job(conv, m["id"], text, f"eval-{uuid.uuid4().hex[:8]}")
        t = time.perf_counter()
        res = ui._run_reply(job)
        total = time.perf_counter() - t
        if ui.ax is not None:
            ui.ax.tidy()
        record("reply", m["_depth"], total, clock.take(), f"{res.kind}: {res.detail}", res.kind == "done")

    if results:
        for kind in ("tap", "untap", "reply"):
            rows = [r for r in results if r["kind"] == kind]
            if rows:
                secs = [r["seconds"] for r in rows]
                print(f"{kind}: {sum(r['ok'] for r in rows)}/{len(rows)} ok, median {statistics.median(secs):.1f}s, "
                      f"max {max(secs):.1f}s")
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
