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
| `BACKCHANNEL_STICKER_CACHE_ROOT` | `~/Library/Messages/StickerCache` | Where `~/Library/Messages/StickerCache/…` paths point (most stickers live there) |
| `BACKCHANNEL_MESSAGES_CONTAINER_ROOT` | `~/Library/Containers/com.apple.MobileSMS` | Where paths into Messages' container point |
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
- **Where files are served from:** only `~/Library/Messages/Attachments`,
  `~/Library/Messages/StickerCache` (stickers) and
  `~/Library/Containers/com.apple.MobileSMS`, checked on the real path (no
  symlinks or `..` out). chat.db rows pointing elsewhere are refused (403);
  ones in temp folders (`/var/folders/…`) are long gone and show as missing (404).
- **Attachments:** image sizes come from the bytes (for HEIC, the primary
  item's `ispe` with its `irot`, not the 512×512 tiles). Files are judged by
  their bytes, not their names (an iPhone may send HEIC named `.JPG`). `thumbUrl`
  is only offered when an `<img>` can draw the result (browser formats, or
  HEIC/TIFF with sips; videos only with qlmanage), and `/thumb` answers 404
  rather than handing an `<img>` a video, a document or an unconverted HEIC. The
  same applies to link-preview images and sticker tapbacks. Portrait phone
  photos are stored sideways with an EXIF orientation (or a HEIC `irot`) that
  browsers apply when they show them, but the web view's right-click › Copy
  Image copies the bare pixels (a pasted copy came out sideways). So every JPEG
  Backchannel serves is upright: thumbnails and HEIC/TIFF conversions are turned
  with sips (`-r`/`-f`) and their orientation tag set to 1, and a sideways JPEG
  gets an upright cached copy for `/attachments/{id}`. Thumbnails cached before
  this are fixed the first time they're shown; `?raw=1` stays the original. Genmoji
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

## Needs you (priority)

What needs Harry first, worked out on the Mac with no AI (rules: docs/api.md,
"Needs you"). Code: `server/timephrases.py` (times named in a text),
`server/priority.py` (the rules), `server/vips.py` (who's close), wired into
`ConversationService` (`Conversation.priority`, `Message.importance`).

- **What counts:** real messages from others in the last 3 days, in
  conversations that aren't muted or automated. A message counts when it asks
  Harry something (DMs: the first asking message of the unanswered run),
  mentions him or replies inline to him (unread or unanswered), names a time
  that's coming up or uses urgent words (unread, or in a DM waiting on him any
  unanswered message that asks something — reading "call me in 20 min" doesn't
  resolve it), or is the third+ unread text in a row from one person within 30
  minutes. Answered messages stop counting; times stop counting an hour after
  they pass.
- **VIPs** (only ever added to a message that already counts): the other person
  in a starred DM; anyone in Chief's people (matched by name like the Chief
  card: full name or nickname, else a first name only one Chief person has —
  Chief has no phone numbers); and the 8 people Harry sent the most texts to in
  the last 90 days (DMs; one query, cached 10 minutes).
- **When it's recomputed:** a conversation's priority is cached until its own
  messages, read mark, mute/star, or the VIP set change, and at most 5 minutes;
  the watcher re-checks every summary once a minute and emits `conversation`
  only when something actually changed. Conversations with nothing in the last
  3 days, or with no unread or unanswered message, cost nothing.
- **Seeing why:** each message's `importance` lists its reasons, and the
  conversation's `priority.reasons` is their union (with `messageId` to jump to).
  `curl -s localhost:5556/api/conversations | jq '.conversations[] |
  select(.priority) | {title, priority}'` shows them (after `GET /api/session`
  for the cookie); `python tests/check_live.py` prints how many there are.
- **Time zone:** times like "7pm" use the Mac's zone (`/etc/localtime`, or `TZ`).
- **Dev fixtures** (`./backchannel dev`) have one of each: Dad ("Can you call me in
  20 min?", starred: urgent), Nina Shah (three texts in a row: urgent), Climbing
  Crew 🧗 (7 unread, an @mention with "by tonight", an inline reply), Bay
  Plumbing ("confirm today"), and Theo Grant (a time that already passed:
  nothing). "In 20 min" stops counting about 75 minutes after the fixtures are
  made: `./backchannel dev --fresh` regenerates them.

## Messages helpers (experimental)

Three things Apple gives other apps no way to do, done by driving the Messages
app through macOS Accessibility (System Events). Each is off by default and
turned on in Settings › Messages helpers (`PATCH /api/settings`):

| Setting | What Messages is asked to do | Checked in chat.db |
|---|---|---|
| `markReadInMessages` | on `POST /read`, when chat.db still has unread incoming messages there: find the conversation's row in Messages' list (scrolling to it if needed, then back) and use its "Mark as Read" action | 3 s later the messages that were unread are read |
| `tapbacksViaMessages` | `POST …/messages/{id}/tapback`: open the chat (see below), find the bubble, use its action for the tapback ("Heart", "Thumbs up"…) | within ~5 s a new row of yours with `associated_message_guid` → the message and type 2000+k (add) / 3000+k (remove) |
| `threadRepliesViaMessages` | a send with `threadRootId` (now or scheduled, not `regularText`): open the chat, bring Messages forward, find the root bubble, right-click it (a real click, `server/mouse.py`), choose "Reply…" in its menu, check the reply field opened, paste the text, press Return, close the thread Messages leaves open | within ~5 s your row with the same text and `thread_originator_guid` = the root (in your own chat, either copy of it) |

**Permissions.** Two macOS prompts, both for the app that runs the server
(Backchannel.app, or your terminal): *Accessibility* (System Settings › Privacy
& Security › Accessibility; the Mac app asks when a helper is turned on) and
*Automation › System Events*. Turning a helper on queues a harmless System
Events call (90 s timeout, never touches Messages) so the Automation prompt
appears right then instead of during the first real action. `Health.messagesUi`
says how it went: `permission` is `denied` for -1719/-25211 ("not allowed
assistive access": Accessibility) and -1743 (Automation), `granted` once a real
action got through, and the permission check alone only reports `lastResult:
"Ready"`. Status survives restarts.

**How it runs.** One worker, one action at a time (read sync, tapbacks, replies
and the permission check share the queue), never blocking a request. Each
action is one `osascript`: the script lines as `-e`, every name and text as
argv (tagged values, see `server/messages_scripts.py`), never in the source.
Timeouts: 30 s (mark read), 45 s (tapback), 60 s (reply); finding a chat may
mean scrolling Messages' list. Each accessibility
request costs ~0.1 s, so the scripts reach the list, the open chat and the
entry area with one literal reference each (child-index paths found on macOS
26.3, checked by accessibility id; a capped search by id if the layout
differs), read labels for a whole list in one request, and never use
`entire contents` (over a minute on a big window). When editing the
scripts: AppleScript names are case-insensitive and System Events' terms win
inside its tell, so every script variable is `bc`-prefixed (a list called
`outLines` *was* its `outlines` class: -10006 on every write), each tell is one
line that gets a value or does one thing, and `on error` branches only record
the error; tests enforce all three. A repeated mark-read
for a conversation already in the queue is dropped; jobs whose setting was
turned off while they waited are dropped. Nothing runs in dry-run or fake-DB
mode (see below).

**Finding things in Messages.**
- *The conversation*: the descriptions of every item in the conversation list
  (`ConversationList`), read in one request. They look like `Name, [Unread, ]preview,
  time` (pinned: `Name, Pinned`). A label that is exactly one of its names or
  starts `Name, ` wins, most specific name first; the title, full name,
  nickname, company and number (`(415) 555-0134`, `+1 (415) 555-0134`) may
  also match a label's start, the short name ("Jordan") only exactly. Only
  chats in the list's loaded part (pinned plus the most recent ones) can be
  found without scrolling: when it isn't there, the list is scrolled to the top
  and then down a page at a time (AXScrollDownByPage through its items, at most
  12 pages, stopping when the list stops moving; the list ends with section
  groups that can't scroll, so the next few items from that end are tried).
  Names also include Contacts' short format "First L.". An unnamed group's item
  (and its title) is matched by its members in any order (argv `G:`, each
  "nickname|first|full name"): every member named, starting with one of them.
  Read sync scrolls at most 4 pages. Read sync scrolls back to the
  top afterwards.
- *Opening the chat* (tapbacks, replies): nothing happens unless Messages' main
  window shows that conversation, checked by the title at the top of the chat
  (ConversationTitle's description is one of its names) or its item being the
  only one selected. Steps, each checked: already on screen; for one-to-one
  chats Messages' own link (`imessage://<handle>`, or `sms:<handle>` when the
  chat is SMS-only; the server only builds links from plain numbers and
  addresses); its list item (scrolled to) pressed, since pressing toggles like
  ⌘-click any other selected item is pressed off, then a click on it with
  Messages in front. Otherwise `not-selected`. The script appends a trail of the
  steps to its answer (`not-selected|link no row3/0 p:3,14 o:3,14 c:14`: step
  names and item positions only), which goes to the log after the code.
- *The bubble* (tapbacks, replies): the transcript right of the sidebar row,
  newest first (the window walked backwards). Text: the first line (60 chars);
  8+ characters must appear in a label, shorter ones must be a whole label line.
  Attachment-only messages: their placeholder as a whole part of the label
  ("Photo", "Audio Message", "Sticker"…; on macOS 26.3 a photo reads `Name,
  Includes picture, 2:59 PM` or `Name, Image attached, IMG_1.jpeg, Image ·
  2.5 MB, …`, a video `Video attached`). The label must also show the minute it was sent (" 1:37" or
  " 13:37", 12- and 24-hour clocks; the leading space keeps " 1:37" from
  matching "11:37"); newer look-alikes from the same minute are counted from
  chat.db and skipped (a self-chat shows every message twice). A reply's quote
  of the message it answers (`Name, Reply Preview, <text>, 8:09 AM` on macOS
  26.3: same text and minute, no tapbacks) never counts. When it isn't on
  screen, the chat is scrolled up a page at a time (at most 12; the transcript
  itself is paged, since its first item may be a time header that takes the
  action without moving) until it shows up, and back down after a tapback. If
  fewer same-minute look-alikes show than chat.db counts (a self-chat may show a
  message once though chat.db keeps a sent and a received copy), the oldest one
  showing is used. If no label shows that minute, the older rule applies on
  screen only: labels that also name the sender are preferred, and newer
  messages with the same text are counted and skipped, so "ok" finds the right
  "ok". System Events finds an element by its place among the transcript's
  items, and those shift while Messages scrolls or loads older messages, so the
  bubble's own label (text and minute) is read again right before it's used;
  if it changed, the message is looked for once more on screen, else
  `bubble-moved`. The log line ends with what the search saw
  (`b<items>/<text matches>/<text and minute matches>`, `t<pages up>` when found,
  `u<pages up>` when not, `again` when it was found a second time).
- *A thread left open*: after a reply Messages keeps showing just that thread,
  and its text field goes on replying in it (placeholder "Reply" instead of
  "iMessage"; the "Dismiss replies" button isn't among System Events' elements
  and pressing it through accessibility does nothing). Before looking for a
  message (tapbacks, replies) or pasting a link, and right after a reply is
  sent, Escape closes it, only while the field is empty so a draft is never
  lost (`thread-closed` in the log).
- *Tapbacks*: the bubble's own accessibility action whose title is one of:
  Heart/Love, Thumbs up/Like, Thumbs down/Dislike, Ha ha/Haha/Laugh, Exclamation
  mark/Emphasize/!!, Question mark/Question/? (works with Messages in the
  background). Choosing a tapback toggles it, so chat.db is checked first:
  adding one you have, or removing one you don't, is answered `done` without
  touching Messages.
- *Replies*: on macOS 26.3 the bubble's "Reply…" accessibility action and
  AXShowMenu do nothing, and the menu bar's Reply is "Reply to Last Message"
  (always the newest), so a reply takes a real right-click. `REPLY_AIM` finds
  the bubble and brings Messages forward. On macOS 26 the transcript fills the
  window and the toolbar, the chat's title, the entry area and the sidebar
  float over it, so the point is the middle of the part of the bubble none of
  them covers (scrolled into view only when too little shows). It answers
  `aim:x,y` plus the bubble's frame and Messages' pid. The server asks
  accessibility what's under the point (`mouse.element_at`) and clicks only
  when it's that bubble (inside its frame) in Messages, never a window in
  front (`covered`, `off-bubble`, `hit-unknown`: nothing clicked). Then a
  right-click (Quartz, the pointer put back), and `REPLY_FINISH` finds the menu:
  an AXMenu in the window's content group (`menu 1 of UI element 1 of window
  1`), not one of the app's elements. It chooses "Reply…". With no menu it
  presses Escape (a menu it can't see may still be open) and the server tries a
  Control-click the same way. Reply mode counts only when the text field's
  placeholder changes or another field takes the focus, else Escape and
  `no-reply-mode`. The focused text field must be empty (so a draft is never
  overwritten). The text is pasted with ⌘V (never set as the field's value:
  that dropped the reply on macOS 26.3 and sent a regular text; setting its
  AXSelectedText did nothing). The reply script uses AppKit (AppleScriptObjC,
  `use framework "AppKit"`) to save every item and kind on the clipboard
  (text, images, files) and put them back once the field shows the reply.
  Return is pressed only when the focused field holds exactly the reply and the
  reply preview is still there; if it's gone, the text is taken back out
  (`reply-mode-lost`). After sending, the thread Messages leaves open is
  closed (see above), and the app that was in front gets focus back.
- *Your own chat*: chat.db keeps every message there twice (sent and
  received copies: other guids, same text, same second or the next) and
  Messages shows both. A reply threads under, and a tapback lands on, whichever
  copy was used, and a removed tapback may leave only the received-side row. So
  there either copy (and either side) counts when checking chat.db, and "you
  already have that tapback" looks at both copies.

**Outcomes.** Every tapback and reply ends with one `ui-action` event
(`done` / `fallback` / `failed` + a sentence); read sync only reports failures
(not "Messages isn't open"). A reply that couldn't be typed (chat or bubble not
found, no reply mode, no permission…) is not sent at all: `send-status` failed
with "Not sent as a reply: <why>", and the UI offers Try again and Send as a
regular text (`regularText`, the normal AppleScript way, still threaded
locally). `fallback` now only means Messages sent the typed reply without a
thread. Once text may be in Messages (typed,
Return pressed, timed out, unexplained errors) it is never sent again:
`send-status` failed with "Messages didn't confirm your reply. Check Messages
before you try again." — and if the row shows up later it's still matched to the
send. The log gets a short code per action, never names or text.

**Dry-run and dev.** With the fake chat.db (`./backchannel dev`) nothing drives
Messages: a tapback or reply writes the row it would have produced into the
fake chat.db (like simulated sends), so the whole flow — `message-updated`,
`send-status`, `ui-action` — can be tried without a Mac. Plain dry-run against a
real chat.db never writes it: tapbacks answer `failed`, replies go out the
normal (dry-run) way. Mark read and the permission check need the real app.

**`./backchannel doctor --messages-ui`** prints, with no message text or names:
whether Accessibility is allowed and Messages is running, the window count, the
menu bar titles and the Conversation menu's items, whether the list, the open
chat and its title are where the scripts expect them (by accessibility id), the
list items' roles, selection and label shapes (the length of each part; a few
status words and clock times named) and the first items' action names, and for
the newest bubble: its row, balloon and text actions, its context-menu titles
and the Tapback picker's buttons (it opens the menu and picker, chooses
nothing, presses Escape). `--messages-tree` maps the whole window instead (a
bounded walk; a minute or two). `--messages-try` is a live check that runs only
when your own chat is the one open in Messages: it presses Heart (Messages in
the back, then in front), checks chat.db and takes it off again, and opens Reply
both ways without typing anything. Titles with digits, `@`, `+` or quotes,
longer than 40 characters, or naming a contact are hidden. This is what to look
at when a helper says it couldn't find something.

**`./backchannel doctor --messages-reply-test`** runs in your own chat only (it
checks the title): for each way of starting a reply (the newest message's
"Reply…" action, its right-click menu, a Reply command in the menu bar) it
prints whether Messages started, the text field's placeholder and the focused
field before → after, and whether that counts as reply mode. For the first way
that does, it pastes "Backchannel reply test N", sends it to you and checks
chat.db for the thread. It replies to the newest message, so it doesn't turn
real replies to others on: a reply to an older message in your own chat does.

**`./backchannel doctor --messages-open "Name"`** brings a conversation (by the
name Backchannel shows) up in Messages the way tapbacks and replies do, without
typing or sending anything, and prints whether it worked, the steps (the same
trail as the log) and how long it took.

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
They never find the machine's `sips`, `afconvert` or `qlmanage` (`tests/conftest.py`
hides them; tests that need one install a fake), so they behave the same on macOS
and Linux.
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
- **A Messages helper doesn't work** (read sync, tapbacks, replies) — check
  `messagesUi.lastResult` in `/api/health`. "Couldn't find … in Messages'
  sidebar", "Couldn't find that message", "didn't offer that tapback" or "didn't
  offer Reply" usually means your macOS labels things differently: open the
  conversation in Messages, run `./backchannel doctor --messages-ui` and paste
  the report. `./backchannel doctor --messages-open "Name"` tries opening one
  chat in Messages the way the helpers do (nothing is sent) and shows the steps. Permission errors: allow the app that runs the server
  (Backchannel, or your terminal) under Privacy & Security › Accessibility and ›
  Automation › System Events. A reply reported as "didn't confirm" may be
  sitting in Messages' reply field: check Messages before sending it again.
- **Stickers don't show** — they live in `~/Library/Messages/StickerCache`,
  which needs the same Full Disk Access as chat.db. Files chat.db points into
  `/var/folders/…` (temporary) are never served and show as missing.
- **An emoji link fails** — 400 "private or local address" means the link
  pointed (or redirected) at your network; 502/504 are the site's answer or a
  timeout. Downloading the image and uploading it works too.
- **Broken contact photos or image previews** — `./backchannel doctor` shows
  contact photos by storage (inline / external / Images dir / invalid / missing
  external file) and whether sips converts HEIC/TIFF and qlmanage makes video
  thumbnails on this Mac.
