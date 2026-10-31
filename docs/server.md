# Backchannel server (Mac)

A small Python server that reads Messages' `chat.db` (read-only), serves the
web UI on <http://127.0.0.1:5556>, and sends replies through Messages.app with
AppleScript. Routes and JSON shapes: `docs/api.md` and `web/src/api/types.ts`.

## Setup

The easiest way to run Backchannel is the Mac app: `./backchannel setup`, then
`./backchannel app` (see the README). The app starts this server itself and
holds the macOS permissions, so Full Disk Access goes to **Backchannel**. The
steps below are for running the server yourself in a terminal.

1. **Python 3.10+ and a venv** (from the repo root):

   ```sh
   ./backchannel setup        # = python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
   ```

2. **Full Disk Access** for the terminal you start Backchannel from (Terminal,
   iTerm, Ghostty…): System Settings → Privacy & Security → Full Disk Access.
   Quit and reopen the terminal afterwards, and run `tmux kill-server` if tmux
   was already running (the tmux server keeps the old permissions).

3. **The UI ships prebuilt** in `web/dist`, so no Node is needed to run it. After
   changing the UI, rebuild with `cd web && npm install && npm run build`.
   Without `web/dist`, `/` shows a short "build the UI" page.

4. **Start it:** `./backchannel on`, then `./backchannel open`.
   `./backchannel status | logs | off` do what they say (tmux session
   `backchannel`, log in `logs/backchannel.log`).

5. **First send:** macOS asks whether your terminal may control Messages —
   click Allow. If you missed it: System Settings → Privacy & Security →
   Automation → (your terminal) → Messages.

6. **Optional AI** (reply drafts, catch-up): put `ANTHROPIC_API_KEY=sk-ant-…`
   in `.env` at the repo root (gitignored), or export it, then restart.
   `python -m server` reads `.env` itself at startup (so the Mac app needs no
   shell): `KEY=VALUE` lines, optional `export `, quotes stripped, `#`
   comments ignored, and variables already in the environment win. Without it the AI routes answer 503 and everything else
   works. Models are set under `"models"` in `config.json` (drafts
   `claude-haiku-5-5`, catch-up `claude-sonnet-5-5`). Drafts use Harry's
   style profile from `~/Documents/claude_projects/imessage-checker/.texting_style.json`
   when it exists; otherwise one is built from recent sent messages and cached
   for 72 h in the state dir.

## Configuration

`config.json` holds the defaults; put personal overrides in
`config.local.json` (gitignored, same keys, merged on top). Environment
variables override both — mostly for dev and tests:

| Variable | Default | Meaning |
|---|---|---|
| `BACKCHANNEL_PORT` | `5556` | Port (always bound to 127.0.0.1) |
| `BACKCHANNEL_CHAT_DB` | `~/Library/Messages/chat.db` | Messages database (opened read-only) |
| `BACKCHANNEL_ADDRESSBOOK_GLOB` | `…/AddressBook/Sources/*/AddressBook-v22.abcddb` and the root `AddressBook-v22.abcddb` | Contacts DBs; several globs separated by `:` |
| `BACKCHANNEL_ATTACHMENTS_ROOT` | `~/Library/Messages/Attachments` | Where `~/Library/Messages/Attachments/…` paths in chat.db point |
| `BACKCHANNEL_STATE_DIR` | `~/Library/Application Support/Backchannel` | `state.db` (stars, mutes, snoozes, last-seen marks, later, scheduled sends, thread mapping), `search.db`, upload scratch |
| `BACKCHANNEL_CACHE_DIR` | `~/Library/Caches/Backchannel` | HEIC→JPEG, thumbnails, `.caf`→`.m4a` |
| `BACKCHANNEL_SEND_MODE` | `applescript` | `dry-run` logs instead of calling osascript |
| `BACKCHANNEL_FAKE_DB` | off | With dry-run: write sent messages into the fixture chat.db (refuses anything under `~/Library/Messages`) |
| `BACKCHANNEL_ME_NAME` | `id -F` | Your name |
| `BACKCHANNEL_ME_HANDLES` | from `config.json`, else read from your sent messages | Your phone/email handles, comma-separated |
| `BACKCHANNEL_STYLE_PROFILE` | Harry's imessage-checker profile | Texting-style profile JSON (`{"profile": "…"}`) |
| `BACKCHANNEL_CHIEF_DB` | `~/Documents/claude_projects/chief/db/chief.db` | Chief's database (`chief_db` in config.json) |
| `BACKCHANNEL_DEV_ORIGINS` | empty | Extra origins allowed on POST/PATCH/PUT/DELETE (dev: `http://localhost:5173`) |
| `BACKCHANNEL_DIST_DIR` | `web/dist` | Built UI |
| `BACKCHANNEL_CONFIG` | `config.json` | Alternate config file |
| `ANTHROPIC_API_KEY` | unset | Enables the AI routes |

## Chief, reminders and new conversations

- **Chief** (`chief_db`): `GET /api/people/{handle}/chief` matches the person to
  a Chief `people` row (full name or nickname, then first name when exactly one
  row has it) and returns their relationship, birthday ("March 3"), notes, up
  to 5 open/in-progress loops whose title names them, and up to 3 current
  profile facts that mention them — or `null`. Reads are read-only.
  `POST /api/chief/loops` inserts one undated loop (`owner='harry',
  urgency='medium', category='imessage', autonomy_tier='ask', status='open',
  source_ref='backchannel:<message guid>'`, no calendar event), waiting up to
  5 s if Chief's daemon is writing; the same title for the same message isn't
  added twice. Without chief.db it answers 503 and `/api/health` shows
  `chief.available: false`.
- **Reminders**: saved items with a remind time fire once (every ~15 s, a
  `later` event with `firedAt`) unless done. "If no reply" items are resolved
  instead when someone else writes in that conversation after the saved
  message (after the save time, for your own messages); this is also checked
  as soon as new messages arrive. `PUT /api/later/{id}` updates only the fields
  it's given (`{"done": true}` works without `conversationId` for a saved
  item); a new `remindAt` re-arms a fired reminder.
- **New conversations**: `POST /api/conversations {handle, text, clientId}`
  accepts a phone number (normalised to E.164; US numbers without +1 are
  fine), an email or a short code. If that person already has a conversation
  (by any of their handles or their Contacts card) the text goes there;
  otherwise it's sent with `participant <handle>` of the iMessage account
  (SMS account for phone numbers if that fails) and the reply is the future
  `dm:<handle>` id. When Messages writes the new chat, the watcher emits
  `conversation`, then `message` (with `clientId`), then `send-status`.

## Development (no Mac needed)

```sh
./backchannel dev          # fixtures + dry-run sends on 127.0.0.1:5556, foreground
./backchannel dev --fresh  # regenerate fixtures first (they also refresh when > 6 h old)
./backchannel simulate     # another terminal: an incoming message every 5 s (--interval, --count)
```

`tests/make_fixtures.py` writes a fake chat.db (full macOS 15 schema, mostly
`text = NULL` with typedstream bodies), two AddressBook sources plus the root
one, a small `chief.db`, and attachment files into `tests/fixtures/` (gitignored). Dev state lives
in `tests/fixtures/state`. Sends in dev appear in the fixture DB after ~0.5 s,
then get fake "delivered" and "read" receipts, so the whole
send → `send-status` → `message-updated` flow runs.

Vite: proxy `/api` to `http://127.0.0.1:5556` with `changeOrigin: true` (the
server rejects other Host headers). The UI calls `GET /api/session` at startup,
which sets the `bc_session` cookie; `./backchannel dev` allows the
`http://localhost:5173` origin for writes.

Tests: `.venv/bin/pip install -r requirements-dev.txt && .venv/bin/python -m pytest`.
`tests/ts_shapes.py` validates every route against `web/src/api/types.ts`.
`python tests/perf_check.py` builds a 500k-message DB and prints timings.

## Details not spelled out in api.md

- `GET /api/attachments/{id}?raw=1` serves the original file (no HEIC/CAF conversion).
- Media pages are newest first. Snoozed conversations are still listed (with `prefs.snoozedUntil`); the UI hides them.
- Sticker tapbacks are `kind: 'sticker'` with emoji 🖼️ and `stickerUrl` set.

## Troubleshooting

- **`/api/health` says chat.db isn't readable** — Full Disk Access is missing
  for the app that started the server. Grant it, quit the terminal, `tmux kill-server`, `backchannel on`.
- **Sends fail with "macOS blocked Backchannel from controlling Messages"** — allow
  Automation (step 5). "Couldn't find that conversation" usually means the chat
  isn't open in Messages on this Mac; open it once there and retry.
- **A send shows as failed after ~2 minutes** — Messages accepted it but no row
  appeared in chat.db; check Messages itself.
- **People show as phone numbers** — check `contacts.count` in `/api/health`.
  If it's 0, Contacts lives somewhere else; set `addressbook_glob` in `config.local.json`.
- **My own messages look like someone else's / "waiting on you" is wrong** — set
  `"me": {"handles": ["+1…", "you@icloud.com"]}` in `config.local.json`.
- **Search misses old messages** — history is indexed in the background after
  start (newest first). Delete `search.db` in the state dir to rebuild.
- **401 in the browser** — reload the page (the session cookie is per server
  run). **403** — the request didn't come from 127.0.0.1/localhost on the server's port.
- **Port in use** — `BACKCHANNEL_PORT=5557 backchannel on` (or `"port"` in `config.local.json`).
- **No Chief card / "Chief's database isn't at …"** — set `"chief_db"` in
  `config.local.json` to where chief.db lives; cards only appear for people
  whose Chief name matches their Contacts name or nickname.
- **AI says there's no API key in the Mac app** — put `ANTHROPIC_API_KEY=…` in
  `.env` at the repo root; the server reads it at startup.
