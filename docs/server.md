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

6. **AI** (reply drafts, catch-up) works two ways; `"ai": {"backend": "auto"}`
   (the default) picks the first that's available:
   - **Claude Code** (`claude -p`, your Claude subscription, no API key) — like
     Chief. Install Claude Code and run `claude` once in Terminal to log in.
     Backchannel finds `claude` even when the app is launched from the Dock
     (where PATH is only /usr/bin:/bin:/usr/sbin:/sbin): `claude_bin` /
     `BACKCHANNEL_CLAUDE_BIN` if set, then PATH plus ~/.local/bin,
     ~/.claude/local, /opt/homebrew/bin, /usr/local/bin, ~/.npm-global/bin,
     ~/.bun/bin, ~/.volta/bin and ~/.nvm/versions/node/*/bin, then your login
     shell's PATH. It runs `claude -p --model <haiku|sonnet> --no-session-persistence
     --tools "" --disable-slash-commands --system-prompt …` with that PATH, in an
     empty folder under the state dir, and **without** ANTHROPIC_API_KEY (so the
     subscription is used); JSON is requested in the system prompt (not
     `--json-schema`). At most 2 run at once; timeouts are 90 s (drafts), 240 s
     (catch-up) and 120 s (style profile). Models: `"models_cli"` in config.json.
   - **API key**: `ANTHROPIC_API_KEY=sk-ant-…` in `.env` at the repo root (or the
     environment). Models: `"models"` in config.json.
   Set `"backend"` to `"claude-cli"` or `"api"` to force one. `/api/health`
   reports `ai.backend`. `python -m server` reads `.env` itself at startup (the
   Mac app needs no shell): `KEY=VALUE` lines, optional `export `, quotes
   stripped, `#` comments ignored, variables already set win. Drafts use Harry's
   style profile from `~/Documents/claude_projects/imessage-checker/.texting_style.json`
   when it exists; otherwise one is built from recent sent messages and cached
   for 72 h in the state dir.

7. **Diagnostics:** `./backchannel doctor` (or `.venv/bin/python -m server.doctor`)
   prints a report to paste back — counts, file formats, timings and versions
   only, never message text, names or handles: chat.db and attachment counts,
   attachments from the last 90 days by kind/extension/format and how many are
   on disk vs in iCloud, real HEIC/TIFF → JPEG (sips) and video thumbnail
   (qlmanage) attempts with timings, contact photos by storage, the AI backend
   and `claude --version`, Chief, and the macOS version. `--ai` adds one tiny
   real Claude call. `--messages-ui` describes the Messages window for tuning
   "mark read in Messages" (see below). It exits non-zero when chat.db can't be
   read.

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
| `BACKCHANNEL_STATE_DIR` | `~/Library/Application Support/Backchannel` | `state.db` (stars, mutes, snoozes, last-seen marks, later, scheduled sends, thread mapping, custom emoji, server settings, sticker file hashes), `search.db`, `emoji/` (custom emoji files), upload scratch |
| `BACKCHANNEL_CACHE_DIR` | `~/Library/Caches/Backchannel` | HEIC→JPEG, thumbnails, `.caf`→`.m4a`, sticker PNGs |
| `BACKCHANNEL_SEND_MODE` | `applescript` | `dry-run` logs instead of calling osascript |
| `BACKCHANNEL_FAKE_DB` | off | With dry-run: write sent messages into the fixture chat.db (refuses anything under `~/Library/Messages`) |
| `BACKCHANNEL_ME_NAME` | `id -F` | Your name |
| `BACKCHANNEL_ME_HANDLES` | from `config.json`, else read from your sent messages | Your phone/email handles, comma-separated |
| `BACKCHANNEL_STYLE_PROFILE` | Harry's imessage-checker profile | Texting-style profile JSON (`{"profile": "…"}`) |
| `BACKCHANNEL_CHIEF_DB` | `~/Documents/claude_projects/chief/db/chief.db` | Chief's database (`chief_db` in config.json) |
| `BACKCHANNEL_CLAUDE_BIN` | found automatically | Path to `claude` (`claude_bin` in config.json) |
| `BACKCHANNEL_AI_BACKEND` | `auto` | `auto`, `claude-cli` or `api` (`ai.backend` in config.json) |
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

## Photos and previews

- **Contact photos** come from Core Data binary storage in the AddressBook
  databases: `ZTHUMBNAILIMAGEDATA` (then `ZIMAGEDATA`) is `0x01` + the image
  bytes, or `0x02` + a UUID naming a file in
  `<folder of the .abcddb>/.AddressBook-v22_SUPPORT/_EXTERNAL_DATA/`; older
  cards use `Images/<unique id>`. Formats are judged by their first bytes only.
  JPEG/PNG/GIF are served as is; HEIC/TIFF/WebP become JPEG with sips; photos
  over 200 KB are shrunk to 256 px. `avatarUrl` (people and you) is only set when
  there is a photo that can really be served, and `/api/avatar` answers 404
  otherwise.
- **Attachments:** image sizes come from the bytes (for HEIC, the primary
  item's `ispe` with its `irot`, not the 512×512 tiles). Files are judged by
  their bytes, not their names (an iPhone may send HEIC named `.JPG`). `thumbUrl`
  is only offered when an `<img>` can draw the result (browser formats, or
  HEIC/TIFF with sips; videos only with qlmanage), and `/thumb` answers 404
  rather than handing an `<img>` a video, a document or an unconverted HEIC. The
  same applies to link-preview images and sticker tapbacks. Genmoji
  attachments carry `isGenmoji` and `emojiDescription`.

## Custom emoji, stickers and settings

- **Custom emoji** (`/api/emoji`): files live in `<state_dir>/emoji/` as
  `<random id>.<ext>`, metadata (name, type, size, animated, source, sha256) in
  state.db. A name is `:shortcode:`-style: colons and case are stripped, then it
  must match `^[a-z0-9_+-]{2,40}$` (400), and be free (409). The bytes decide the
  type: PNG, GIF, JPEG and WebP are kept as they are; HEIC becomes PNG with sips
  (415 without it, i.e. off macOS); SVG, TIFF, PDF and anything else are refused
  with 415 and a sentence. At most 5 MB (413). Width/height come from the file;
  `animated` means a GIF with more than one frame, an APNG (`acTL` with more than
  one frame) or an animated WebP. Images are served with the same headers as
  attachments (`nosniff`, a sandboxing CSP).
- **Emoji from a link** (`POST /api/emoji/link`): http(s) only, no
  `user:password@`; the host is resolved and every address must be public
  (no private, loopback, link-local, multicast, reserved or CGNAT ranges,
  including IPv4-mapped/6to4/Teredo forms of them), and the connection goes to
  that checked address (no second DNS lookup). Up to 3 redirects, each checked
  the same way; 15 s timeout; the download stops at 5 MB; Safari's User-Agent
  (some emoji sites refuse unknown clients). Errors: 400 for a bad or private
  link, 502 when the site answers with an error or too many redirects, 504 on
  timeout, 413/415 as for uploads.
- **Stickers & Genmoji** (`/api/stickers`): every attachment with
  `is_sticker = 1` or an `emoji_image_content_identifier` (macOS 15+), sticker
  tapbacks included. Copies are grouped by the Genmoji identifier, else by the
  file's SHA-256 (cached in state.db by attachment guid + mtime + size, so each
  file is read once); `id` is the newest copy, `timesUsed` counts copies,
  `sentByMe` is true if any copy is yours. Files that are missing (iCloud) or
  that an `<img>` couldn't draw are skipped. The list is cached for 5 minutes
  and rebuilt as soon as chat.db changes. `/api/stickers/{id}/image` serves
  PNG/GIF/JPEG/WebP as they are and HEIC/TIFF as a PNG made with
  `sips -s format png` (transparency kept, cached), else 404.
  `POST /api/stickers/{id}/save {name}` copies that image into custom emoji
  with `source: "imessage"`.
- **Server settings** (`GET/PATCH /api/settings`) are stored in state.db;
  unknown keys are ignored and missing ones keep their value.

## Mark read in Messages (experimental)

Off by default (`markReadInMessages`). When it's on, `POST /read` also queues a
job when chat.db still has unread incoming messages in that conversation. One
background worker runs jobs one at a time; a conversation already waiting in
the queue isn't queued twice (one that arrives while its own job runs is queued
once more). The request never waits for it. It never runs in dry-run or
fake-DB mode.

The job is one `osascript` (script lines as `-e`, names as arguments, never in
the source) that drives System Events against the running Messages app, with an
8 s timeout:

1. If Messages isn't running or has no window, stop.
2. Collect the labels (description, name, value) of rows, cells, buttons,
   groups and static texts in the front window (the first 2,500 elements),
   then look for the conversation:
   first a label line that is exactly one of its names (or `Name, …`), then one
   that starts with its title, full name, nickname, company or phone number
   (`(415) 555-0134` and `+1 (415) 555-0134`). The short name ("Jordan") only
   counts as an exact match, so it can't pick "Jordan Lee". Matches are climbed
   to their row; rows win over other elements.
3. Select the row (AXSelected, else AXPress — rows and cells only, never
   buttons) and, if the selection took, click the menu bar's
   Conversation › Mark as Read when it's enabled.
4. Otherwise open the element's context menu (AXShowMenu) and click
   Mark as Read there; if that isn't offered, press Escape.

Jobs still queued when the setting is turned off are dropped.
It never types text and never sends anything. Three seconds after a success it
checks chat.db again; if the messages that were unread are still unread,
`lastResult` says so. Results go to `Health.messagesUi`
(`permission` granted/denied/unknown, `lastResult`, `lastAt`, kept across
restarts) and a short code to the log (never the conversation's name).
"Not allowed assistive access" (-1719/-25211) means Accessibility is off for the
app that runs the server: System Settings › Privacy & Security › Accessibility.
-1743 means the "control System Events" prompt was declined:
System Settings › Privacy & Security › Automation.

`./backchannel doctor --messages-ui` shows whether Accessibility is allowed,
whether Messages is running, its window count, the menu bar titles, the
Conversation menu's items, the front window's roles and child counts to
depth 8 (at most 400 elements), and how many row-like elements expose a
description/name/value (counts and lengths only). It never prints labels or
values, never opens a menu, and hides any menu title with digits, `@` or `+`,
or a contact's name.

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
- **AI is off in the app** — `/api/health` → `ai.backend`. With Claude Code,
  run `./backchannel doctor` to see whether `claude` was found; set
  `claude_bin` if it lives somewhere unusual. "Claude Code isn't logged in" →
  run `claude` in Terminal once and log in. Or put `ANTHROPIC_API_KEY=…` in
  `.env` at the repo root.
- **Mark read in Messages doesn't work** — check `messagesUi.lastResult` in
  `/api/health`. "Couldn't find … in Messages' sidebar" or "didn't offer Mark as
  Read" usually means your macOS labels things differently: run
  `./backchannel doctor --messages-ui` with Messages open and paste the report.
  Permission errors: allow the app that runs the server (Backchannel, or your
  terminal) under Privacy & Security › Accessibility (and › Automation › System
  Events).
- **An emoji link fails** — 400 "private or local address" means the link
  pointed (or redirected) at your network; 502/504 are the site's answer or a
  timeout. Downloading the image and uploading it works too.
- **Broken contact photos or image previews** — `./backchannel doctor` shows
  contact photos by storage (inline / external / Images dir / invalid / missing
  external file) and whether sips converts HEIC/TIFF and qlmanage makes video
  thumbnails on this Mac.
