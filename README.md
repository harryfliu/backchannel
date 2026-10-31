# Backchannel

Your iMessages in a Slack-style app. A small Python server on your Mac reads
Messages' database. A web app with Slack's layout (sidebar sections, threads,
reactions, ⌘K) lets you read and reply. A native Mac app wraps both, so it lives
in your Dock like Slack. Replies go out through Messages.app, the same way
imessage-checker sends digests.

```
┌───────────────────────────────────────────────────────┐
│  Backchannel.app  (mac/Backchannel.swift)             │
│    window, Dock badge, notifications, menus           │
│    starts + stops ↓                                   │
│  server/  FastAPI on 127.0.0.1:5556                   │
│    ├─ reads ~/Library/Messages/chat.db (read-only)    │
│    ├─ names + photos from Contacts                    │
│    ├─ watches for new texts → pushes them live        │
│    ├─ sends with AppleScript → Messages.app           │
│    ├─ Chief: profile cards, “Add to Chief loops”      │
│    └─ optional: Claude drafts replies + catch-up      │
│    serves ↓                                           │
│  web/  React UI, prebuilt in web/dist                 │
└───────────────────────────────────────────────────────┘
```

## Quick start

```sh
./backchannel setup     # once: venv + Python packages
./backchannel app       # builds Backchannel.app, puts it in /Applications, opens it
```

1. Backchannel opens on a setup screen. Give **Backchannel** Full Disk Access
   (System Settings › Privacy & Security › Full Disk Access), then quit it and
   open it again.
2. On your first send, macOS asks whether Backchannel may control Messages. Click **Allow**.
3. Right-click its Dock icon › **Options › Keep in Dock**.
4. Optional: put `ANTHROPIC_API_KEY=sk-ant-…` in `.env` to turn on **Draft reply**
   and **Catch up**. Drafts use the texting-style profile imessage-checker already keeps.

Building the app needs Apple's command line tools (`xcode-select --install`).
The app runs the server from this folder, so leave the repo where it is. Pulling
new code doesn't need a rebuild; just quit and reopen. If you do rebuild the app,
macOS may ask for Full Disk Access again.

**In the app:** closing the window keeps Backchannel running, like Slack; ⌘Q quits
and stops the server. The Dock icon shows unread DMs and mentions. Turn on
notifications in Settings (⌘,). **Backchannel › Open at Login** keeps it
running, so scheduled texts and reminders go out on time.

**Without the app:** `./backchannel on` runs the server in tmux and
`./backchannel open` opens it in your browser. Then your terminal needs Full Disk
Access instead.

## How iMessage maps onto Slack

| iMessage | Backchannel |
|---|---|
| Named group chat | `#channel` under Groups |
| 1:1 and unnamed groups | Direct messages (phone, email and SMS chats with one person are merged) |
| Swipe-to-reply | A thread: “5 replies” under the message, opens on the side |
| Tapbacks | Reaction chips |
| Edited / unsent | “(edited)” / “This message was unsent.” |
| SMS | Green SMS tag and send button |

**Beyond Slack**
- **Catch up**: what needs you, with replies drafted in your voice.
- **Waiting on you**: people who asked you something you haven't answered.
- **Draft reply**: three suggestions in any conversation.
- **Apps & alerts**: codes, deliveries and reminders filed away, with a copy-code button.
- **Remind me later** on any text, **remind me if they don't reply** on yours, and **scheduled sends**.
- **Chief**: profile cards show what Chief knows (birthday, open loops, notes), and
  **Add to Chief loops** turns a text into a loop.
- Photos and files: the + button, paste, or drag onto a conversation.

**iMessage limits**
- Other apps can't send tapbacks or true threaded replies. Everyone else's show
  up here; your thread replies go out as normal texts.
- Reading here doesn't send read receipts or clear unread on your phone.
- Your Mac must be awake with Backchannel running for sends, scheduled sends and reminders.

## Commands

| Command | What it does |
|---|---|
| `backchannel app` | Build the Mac app and install it in /Applications |
| `backchannel setup` | Create `.venv` and install packages |
| `backchannel on` / `off` / `status` / `logs` | Run the server in tmux instead of the app |
| `backchannel open` | Open it in your browser |
| `backchannel dev [--fresh]` | Run against fake data with sends simulated (no Mac needed) |
| `backchannel simulate` | Make fake people text you while `dev` is running |

## Development

```sh
./backchannel dev                       # fake chat.db + chief.db on :5556
cd web && npm install && npm run dev    # UI on :5173, proxies /api to :5556
```

- `http://localhost:5173/?mock` runs the UI on built-in sample data, with no server needed.
  `npm run build:mock && node scripts/inline-mock.mjs` packs that into one HTML file.
- Server tests: `.venv/bin/pip install -r requirements-dev.txt && .venv/bin/python -m pytest`.
- `npm run build` refreshes `web/dist`, which is committed so the Mac doesn't need Node.
- `mac/make_icon.py` regenerates `mac/AppIcon.icns`.
- The API contract lives in [web/src/api/types.ts](web/src/api/types.ts) and [docs/api.md](docs/api.md).
  Server setup, config and troubleshooting: [docs/server.md](docs/server.md).

## Layout

```
backchannel/
├── backchannel        # control script (app/setup/on/off/status/logs/open/dev/simulate)
├── config.json        # defaults; personal overrides go in config.local.json
├── mac/               # the Mac app: Backchannel.swift, Info.plist, icon, build.sh
├── server/            # FastAPI app: chat.db reader, contacts, sender, watcher, Chief, AI
├── web/               # React + TypeScript UI (src/), built output (dist/)
├── tests/             # pytest suite, fake chat.db/chief.db generator, simulator
└── docs/              # api.md (routes), server.md (setup + troubleshooting)
```
