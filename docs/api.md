# Backchannel HTTP API

The web UI talks to a small Python server running on the Mac. The JSON shapes
are defined in `web/src/api/types.ts` (TypeScript is the source of truth;
field names are camelCase on the wire). This file lists the routes and the
behaviour behind them.

Server: FastAPI + uvicorn, bound to `127.0.0.1` only, default port `5556`.

## Security (required)

The server can send texts as the user, so any web page the user visits must
not be able to drive it.

- Bind to `127.0.0.1`, never `0.0.0.0`.
- Reject any request whose `Host` header is not `127.0.0.1:<port>` or
  `localhost:<port>` (blocks DNS rebinding).
- When `GET /` serves the UI, set a random per-process token as a cookie
  `bc_session` (`HttpOnly; SameSite=Strict; Path=/`). Every `/api/*` route
  requires that cookie. Cross-site requests never carry a SameSite=Strict
  cookie, so they get 401.
- State-changing routes (POST/PATCH/PUT/DELETE) also require an `Origin`
  header that is absent or equal to the server's own origin, and JSON
  bodies must be `Content-Type: application/json`.
- No CORS headers. In dev the Vite server proxies `/api`, so the browser
  only ever sees one origin.
- `chat.db` and the AddressBook databases are opened read-only
  (`file:...?mode=ro`, never `immutable=1`, so new writes in the WAL are seen).
- Attachments are served by GUID lookup only, never by a path from the
  client, and only if the resolved file lives under
  `~/Library/Messages/Attachments` (or the configured fixture dir).

## Routes

| Method | Path | Returns |
|---|---|---|
| GET | `/api/session` | sets the `bc_session` cookie, returns `{ ok: true }` (the UI calls it once at startup; refused when `Sec-Fetch-Site: cross-site`) |
| GET | `/api/health` | `Health` |
| GET | `/api/me` | `Me` |
| GET | `/api/conversations` | `{ conversations: Conversation[] }`, newest activity first |
| GET | `/api/conversations/{id}` | `Conversation` |
| GET | `/api/conversations/{id}/messages?before=<cursor>&limit=60` | `MessagePage` |
| GET | `/api/conversations/{id}/threads/{messageId}` | `ThreadResponse` |
| GET | `/api/conversations/{id}/media?before=<cursor>&limit=60` | `MediaPage` |
| POST | `/api/conversations/{id}/messages` | body `SendRequest` → `SendResponse` |
| POST | `/api/conversations/{id}/files` | multipart `file`, `clientId` → `SendResponse` |
| POST | `/api/conversations` | body `{ handle, text, clientId }` → `{ conversationId }` — start a conversation with a phone number or email |
| POST | `/api/conversations/{id}/read` | `Conversation` |
| PATCH | `/api/conversations/{id}/prefs` | body `Partial<ConversationPrefs>` → `Conversation` |
| GET | `/api/later` | `{ items: LaterItem[] }` |
| PUT | `/api/later/{messageId}` | body `{ conversationId, remindAt, ifNoReply?, done? }` → `LaterItem` |
| DELETE | `/api/later/{messageId}` | 204 |
| GET | `/api/scheduled` | `{ items: ScheduledMessage[] }` |
| DELETE | `/api/scheduled/{id}` | 204 |
| GET | `/api/activity?limit=50` | `{ items: ActivityItem[] }` |
| GET | `/api/threads?limit=40` | `{ items: Message[] }` — thread roots (messages with `thread` set) across conversations, newest reply first, last 30 days |
| GET | `/api/search?q=&conversationId=&limit=40` | `{ results: SearchResult[] }` |
| POST | `/api/ai/draft` | body `{ conversationId, threadRootId?, hint? }` → `{ drafts: string[] }` |
| GET | `/api/ai/catchup?refresh=0` | `CatchUp` |
| GET | `/api/people/{handle}/chief` | `ChiefCard`, or `null` when Chief doesn't know them or isn't installed |
| POST | `/api/chief/loops` | body `{ conversationId, messageId, title }` → `{ id, title }` |
| POST | `/api/conversations/{id}/messages/{messageId}/tapback` | body `{ kind, remove }` → 202 `{ status: 'queued' }` (needs `tapbacksViaMessages`, else 409) |
| GET | `/api/settings` | `ServerSettings` |
| PATCH | `/api/settings` | body `Partial<ServerSettings>` → `ServerSettings` |
| GET | `/api/emoji` | `{ items: CustomEmoji[] }` |
| POST | `/api/emoji` | multipart `name`, `file` → `CustomEmoji` |
| POST | `/api/emoji/link` | body `{ name, url }` → `CustomEmoji` (the server downloads the image) |
| DELETE | `/api/emoji/{name}` | 204 |
| GET | `/api/emoji/{name}/image` | the emoji image |
| GET | `/api/stickers?limit=300` | `{ items: StickerItem[] }` |
| GET | `/api/stickers/{id}/image` | browser-friendly sticker/Genmoji image (PNG for HEIC, transparency kept) |
| POST | `/api/stickers/{id}/save` | body `{ name }` → `CustomEmoji` |
| GET | `/api/avatar/{handle}` | contact photo (JPEG/PNG) or 404 |
| GET | `/api/attachments/{id}` | the file (HEIC converted to JPEG) |
| GET | `/api/attachments/{id}/thumb` | ~800px JPEG preview, falls back to the file |
| GET | `/api/events` | Server-sent events, `data: <ServerEvent JSON>` |
| GET | `/` and `/assets/*` | the built UI from `web/dist` |

Errors are `{ "error": "<sentence a person can act on>" }` with a 4xx/5xx status.
AI routes return 503 with a clear error when `ANTHROPIC_API_KEY` is not set.

## Behaviour notes

**Conversation ids.** Opaque and stable. Messages.app merges a person's
iMessage, SMS and email chats into one conversation; do the same for DMs:
group 1:1 chats whose participant resolves to the same contact (or the same
normalized handle) into one `dm` conversation. Groups map 1:1 to chat rows.
A conversation's messages are the union of its chat rows, ordered by date.

**Sidebar window.** `/api/conversations` returns conversations with activity
in the last 180 days, plus every starred one, at most 400.

**Timeline.** `/messages` returns every message in date order, including
inline replies (those carry `threadRootId`). The UI decides whether to show
replies in the main timeline or only in the thread pane. Tapback rows are
never returned as messages; they are folded into the target's `reactions`.

**Unread.** A message is unread when it is incoming, a real message (not a
tapback or event), `is_read = 0` in chat.db, and newer than the conversation's
local "last seen" mark. `POST /read` sets that mark to the newest message.
(Backchannel can't mark messages read in Messages.app or send read receipts.)

**Waiting on you.** Look at the *unanswered run*: the real messages (not
tapbacks, events or unsent) that others sent after the user's last real
message, where the newest is less than 7 days old. `awaitingReply` is true for
non-automated conversations when:
- in a DM: some message in the run asks something of the user — it contains
  `?` or a request phrase (can you, could you, would you, will you, are you,
  do you, did you, have you, lmk, let me know, call me, text me, send me,
  wanna, want to, down to, thoughts, when you get a chance) or mentions them —
  and the newest message isn't a bare closer: if it opens with ok, thanks,
  thank you, perfect, sounds good, great, cool, got it, will do, see you, np,
  no worries, lol, haha, 👍, ❤️… it only counts when it also asks something
  itself ("Perfect, see you then" closes the loop; "Perfect. Can you bring
  his records" doesn't);
- in a group: some message in the run mentions the user, or is an inline
  reply to one of the user's messages.
`awaitingSince` is the time of the first asking message in the run. The AI
catch-up does the finer judging.

**Automated.** `isAutomated` when the sender is a short code (3–6 digits) or a
non-contact who the user has never written to and whose texts look automated
(codes, deliveries, appointment reminders, bank alerts).

**Sending.** Text goes out through AppleScript (`osascript`), passing the chat
GUID and text as argv (never interpolated into the script). It is sent to
`chat id <guid>` of the conversation's most recently used chat row, falling
back to `participant <handle>` of the iMessage account for DMs. Files are
copied into `~/Library/Messages/Attachments/Backchannel/<uuid>/` first.
When the sent row shows up in chat.db (same conversation, from me, same text,
within 2 minutes) the server emits `send-status: sent` with the real
`messageId`, and the `message` event carries `clientId`. AppleScript errors,
or chat.db `error != 0`, emit `send-status: failed`.

**Thread replies.** AppleScript can't send a real inline reply. A reply sent
with `threadRootId` goes out as a normal text; the server stores
(sent message GUID → threadRootId) locally and reports the message with that
`threadRootId`, so it stays in the thread in Backchannel.

**Live updates.** A watcher polls `PRAGMA data_version` about once a second.
On change it emits `message` for new rows, `message-updated` for messages whose
reactions, edit/unsend state, delivery/read state or thread summary changed,
and `conversation` for conversations whose summary changed. It sends a
`hello` event on connect and a comment line every 15s as a keepalive.

**Dry-run mode.** `BACKCHANNEL_SEND_MODE=dry-run` logs instead of calling
`osascript`. With `BACKCHANNEL_FAKE_DB=1` it also inserts the sent message into
the fixture chat.db (refusing if the path is under `~/Library/Messages`) so the
whole send → confirm flow can be tested without a Mac.

**Reminders.** A saved item with `remindAt` fires once when that time passes:
the server sets `firedAt` and emits a `later` event, unless the item is done.
With `ifNoReply`, a message from anyone else in that conversation after the
saved message (or after the item was saved, for the user's own messages)
resolves it instead: `resolvedByReply = true`, a `later` event, and no reminder.

**New conversations.** `POST /api/conversations` normalizes the handle (E.164
for US numbers, lowercase email), sends through `participant <handle>` of the
iMessage account (falling back to SMS for phone numbers), and returns the id
the conversation will have: an existing conversation's id when this person
already has one, else `dm:<handle>`. The conversation appears through a
`conversation` event once Messages writes the chat row.

**Chief.** Reads `chief_db` (default
`~/Documents/claude_projects/chief/db/chief.db`). A person matches a Chief
`people` row by name (full name, then first name when unique). Loops match when
their title mentions the person's full or first name. New loops are inserted
as `owner='harry', urgency='medium', category='imessage', autonomy_tier='ask',
status='open'`, with `source_ref='backchannel:<message guid>'` and no calendar
event (the same shape Chief's own meeting tool uses for undated loops).

**Custom emoji.** Slack-style emoji Harry adds himself: an uploaded file or an
image link (often from slackmojis.com; nothing is bundled). Names match
`^[a-z0-9_+-]{2,40}$` and are unique (409 when taken). Images must really be
images by their bytes (PNG, GIF, JPEG, WebP; HEIC is converted to PNG), at
most 5 MB; link downloads accept http(s) only, follow at most 3 redirects, time
out after 15 s, and never reach private or loopback addresses. Files live in
`<state_dir>/emoji/`, metadata in state.db. iMessage can't carry custom emoji, so
the UI sends them as image attachments through the normal file route.

**Stickers and Genmoji.** `/api/stickers` lists attachments with
`is_sticker = 1` or a Genmoji identifier (`emoji_image_content_identifier`, with
`emoji_image_short_description` as the description), deduplicated by content
(identifier, else file hash), counted by how often they appear, most used first.
Files missing from disk are skipped. Message attachments carry `isGenmoji` and
`emojiDescription` so the UI can draw Genmoji at emoji size.

**Mark read in Messages (experimental, opt-in).** Apple gives other apps no way
to mark texts read, so with `markReadInMessages` on, `POST /read` also asks the
Messages app to do it through macOS Accessibility (System Events), but only when
chat.db still has unread incoming messages in that conversation. It runs in the
background, never blocks the request, at most one at a time, and gives up after
~8 s. Strategy, in order: (1) find the conversation's row in Messages' sidebar
by its display name, select it, and use the menu bar's Conversation › Mark as
Read when present; (2) the row's context menu (AXShowMenu) › Mark as Read. It
never types into Messages and never sends anything. Results go to the log and to
`Health.messagesUi` (permission granted/denied; denied = macOS error -1719/-25211,
"not allowed assistive access"). `./backchannel doctor --messages-ui` prints the
Messages window's accessibility structure (roles, counts and menu titles only; no
message text) so the script can be tuned to the user's macOS version.

**Tapbacks and threaded replies via Messages (experimental, opt-in).** The same
Accessibility automation as read sync, for the two things AppleScript can't do.
Both act only inside the conversation's own Messages transcript, only on
messages Messages has loaded (recent ones), and are verified in chat.db. Each
attempt ends with one `ui-action` event: `result` is `done`, `fallback` (replies
only) or `failed`, and `detail` is a sentence the UI shows as a toast.
- Tapback (`tapbacksViaMessages`): select the conversation in Messages' sidebar,
  find the target bubble in the transcript (match sender + text, newest match
  first; attachments by their placeholder), open its context menu (AXShowMenu)
  and choose the tapback (Tapback submenu or reaction buttons, whatever the
  user's macOS exposes); to remove, choose the same tapback again. Verify a new
  row with `associated_message_guid` pointing at the target and the right
  `associated_message_type` within ~5 s, then emit `ui-action` (`done` or
  `failed`). The new reaction itself reaches the UI as the target's normal
  `message-updated`, the same as anyone else's tapback. One tapback per person
  per message, like iMessage: picking a different one replaces yours.
- Thread reply (`threadRepliesViaMessages`): when a send has `threadRootId`,
  find the root bubble the same way, choose Reply in its context menu, then put
  the text in Messages' reply field via the clipboard (saved and restored) and
  press Return. Typing needs Messages in front for about a second, then focus
  returns to Backchannel. Verify the sent row has `thread_originator_guid` =
  root and reconcile it to the clientId as usual (`ui-action` `done`). If the
  reply field couldn't be opened (nothing typed), send it the normal AppleScript
  way and emit `fallback` ("went out as a regular text"). If text may have been
  typed but nothing was verified, never send a second copy: emit `failed` and a
  `send-status` failed whose error says to check Messages before retrying (the
  row is still reconciled if it shows up later).
- Read sync failures also emit `ui-action` (`mark-read`, `failed`); successes don't.
- At most one Messages action at a time (queue), ~10 s timeout each. Never in
  dry-run/fake-DB mode: there, a tapback or reply is simulated by writing the
  row it would have produced into the fake chat.db (like simulated sends), so
  the whole flow can be tried without a Mac. Tests use a fake runner.
