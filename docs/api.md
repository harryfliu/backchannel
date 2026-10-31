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
  client, and only if the resolved file (realpath) lives under
  `~/Library/Messages/Attachments`, `~/Library/Messages/StickerCache` (where
  Messages keeps stickers) or `~/Library/Containers/com.apple.MobileSMS` (or
  the configured fixture dirs). Files in temp folders (`/var/folders/…`) are
  never served: they show as `missing` (404).

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
| POST | `/api/conversations/{id}/unread` | body `{ messageId? }` → `Conversation` — mark as unread from that message on (default: the newest incoming one); `POST /read` clears it |
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

**Mark as unread.** chat.db is Messages' own, so "Mark as unread" is kept in
state.db: the conversation and the moment it counts from (the given message's
date, or the newest incoming real message's). While it's set, `markedUnread` is
true and `unreadCount` is the incoming real messages from that moment on, read
in chat.db or not, plus chat.db-unread ones between the "last seen" mark and
that moment, at least 1 (mentions and "Needs you" still go by chat.db alone).
New messages keep counting. `POST /read` clears it. A `messageId` from another
conversation is a 404. The change goes out as a `conversation` event.

**Waiting on you.** Look at the *unanswered run*: the real messages (not
tapbacks, events or unsent) that others sent after the user's last real
message, where the newest is less than 7 days old. `awaitingReply` is true for
non-automated conversations when:
- in a DM: some message in the run asks something of the user — it contains
  `?` (outside links; a `?` mixed with `!`, like "seriously?!" or "11 pm!??!",
  is surprise and only counts when its sentence has a question word: who, what,
  when, where, why, how, can, could, would, will, do, did, are, is, you…) or a
  request phrase (can you, could you, would you, will you, are you,
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

**Needs you (priority).** What needs the user first, so important texts don't
get buried in busy chats. Computed on the Mac, no AI, for every non-automated,
non-muted conversation; `priority` is null when nothing applies. Only real
messages from others in the last 3 days count (no tapbacks, events, unsent).
Each message that counts gets `importance` (its reasons); the conversation's
`priority.reasons` is the union, most important first (urgent, repeat, time,
asked, mention, reply, vip), each kind once (for `time`, the soonest).
- `asked`: a DM that is `awaitingReply` (rule above). Goes on the first asking
  message. (In groups the `mention` / `reply` reasons already say it.)
- `mention` / `reply`: a group message that mentions the user, or an inline
  reply to one of the user's messages, that is unread or in the unanswered run.
- `time` and `urgent` look at *pending* messages: unread ones, and in a DM that
  is `awaitingReply` also every message of the unanswered run that asks
  something (the request test of "Waiting on you": `?`, a request phrase or a
  mention), read or not: reading "Can you call me in 20 min?" doesn't resolve it,
  answering does (and so does a closing "ok thanks"). In groups only unread
  messages are pending (a mention or reply already keeps it in Needs you until
  the user answers). Both still expire: a time an hour after it passes or when
  its window ends, everything after 3 days.
- `time`: a pending message names a time that is still ahead, judged from the
  message's own timestamp in the Mac's time zone:
  - in N min/mins/minutes/hr/hrs/hours → `at` = sent + N, label = that clock
    time ("Today 7:42 PM"; the UI shows a live "In 12 min" when it's close);
  - a clock time: "7pm", "7:30 pm", "at 7", "at 7:30", "by 5", "7:30", noon,
    midnight. Bare hours count only with am/pm or after at/by; a bare H:MM
    ("7:30?") counts on its own when the minutes are two digits 00–59 with no
    digit right after, and it isn't a ratio, score, pace or race time ("3:1",
    "won 2:10", "2:10 pace", "1:23:45"). Without am/pm: 12 means noon (unless
    "am" or "midnight" is said); a morning word in the text means am and
    tonight/evening/night pm; on a named day ("Friday at 7", "tomorrow at 8")
    7–11 means pm; otherwise take the next 7:00 or 19:00 after the message,
    skipping 1–6 am. A clock time attaches to a day named in the same message
    ("tomorrow at 9am", "Friday 7pm") → label "Today 7 PM", "Tomorrow 9 AM",
    "Fri 7 PM";
  - this morning / this afternoon / this evening / tonight → `at` null (they're
    vague), counting until 12:00, 17:00, 23:59, 23:59 that day; label "This
    morning", "This afternoon", "Tonight" (never urgent on their own);
  - today, eod / end of day → `at` null, label "Today", window ends 23:59;
  - tomorrow / tmrw (optionally + morning/afternoon/night or a clock time) →
    label "Tomorrow" or "Tomorrow 9 AM";
  - weekday names ("Friday", "this Fri", "on fri") up to 6 days ahead → "Fri";
    an explicit date ("Nov 3", "on 11/3") is a named day too, so "Tue Nov 3 at
    10am" isn't read as the next 10 AM.
  Also understood: "@ 7" like "at 7", "at 19:00", "in an hour", "in half an
  hour", "20m"/"2h", "tonite", "tmr". URLs, emails, phone numbers and numbers
  followed by a unit ("at 3 people", "by 5%") never count.
  It counts while the moment (or the day's 23:59 when `at` is null) is still
  ahead and within 36 hours, and for up to 1 hour after a named time passes.
  A message mentioning several times uses the soonest still ahead. A clock
  time the user named in one of their own last 10 texts in the 24 hours before
  doesn't count (same clock face, am/pm aside): it's a reaction ("11 pm!??"
  after "home at like 11 pm"), not a plan.
- `urgent`: a pending message with urgent, asap, a.s.a.p, emergency, 911,
  hospital, accident, "call asap", "pick up the phone" / "pick up your phone",
  "pick up!", "answer your phone", "need you now", or "help!", or "call me"
  together with an immediacy word in the same message (asap / a.s.a.p, now /
  right now, right away, immediately, urgent, emergency, "when you see this")
  (whole words, any case; `word` = the first match, lowercase). A plain
  "pick up" ("can you pick up milk") or "call me" ("call me when you get a
  chance", "call me later": that's `asked`) doesn't count; "Can you call me in
  20 min?" is urgent through its `time`, not the words.
- `repeat`: 3 or more unread messages in a row from the same person, each
  within 30 minutes of the one before (`count` = how many).
- `vip`: the sender is close: the other person in a starred DM, in Chief's
  people, or one of the 8 people the user sent the most texts to in the last
  90 days (`name` = short name). Added only to messages that already have
  another reason; never creates priority alone.
Level is `urgent` when there's an `urgent` or `repeat` reason, or a `time` whose
`at` is within the next 3 hours (or passed less than an hour ago); otherwise
`high`. `score` orders within a level: start at 300 (urgent) or 100 (high),
add urgent +80, repeat +40 per message past 2, time +80 when it's within 3
hours (else +30), asked +60, mention +50, reply +40, vip +25 (each reason in
`priority.reasons` once: for `repeat` the longest streak, for the others the
newest), then subtract the hours since the newest counting message (at most
72), rounded. `messageId` is the counting message with the most weight (the
same weights over its own reasons; newest on ties). `until` is when the earliest
time-based reason stops counting (a moment + 1 h, or the window's end), or null.
Priorities are recomputed whenever the conversation changes and at least every
5 minutes (the server re-checks every conversation each minute); a change emits
the usual `conversation` event, and new messages arrive with `importance` set.

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
`threadRootId`, so it stays in the thread in Backchannel. With
`threadRepliesViaMessages` on, it's sent as a real reply through the Messages
app instead, or not at all (see "Tapbacks and threaded replies via Messages"
below). `regularText: true` in the `SendRequest` skips Messages and sends it the
normal way, still kept in the thread in Backchannel: the UI's "Send as a regular
text" on a reply that couldn't be sent as a real one.

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
30 s. It finds the conversation's row in Messages' conversation list by its
display name (scrolling the list a page at a time when it isn't in view, then
back to the top) and uses the row's own "Mark as Read" accessibility action (no
selecting, no menus; works with Messages in the background). It never types
into Messages and never sends anything. Results go to the log and to
`Health.messagesUi` (permission granted/denied; denied = macOS error -1719/-25211,
"not allowed assistive access"). `./backchannel doctor --messages-ui` reports
what the scripts can reach in Messages (the list, the open chat, label shapes and
action names; no message text) so they can be tuned to the user's macOS version.
The same setting syncs "Mark as unread": `POST /unread` asks Messages to use the
row's "Mark as Unread" action (answers `ok-action`, `already-unread`,
`row-not-found`, `no-mark-item`), and `POST /read` on a conversation that was
marked unread asks Messages to mark it read even when chat.db shows nothing
unread (Messages' own unread dot may not show there). Mark read and mark unread
for one conversation share a queue slot: a newer one replaces one still waiting.

**Tapbacks and threaded replies via Messages (experimental, opt-in).** The same
Accessibility automation as read sync, for the two things AppleScript can't do.
Both act only inside the conversation's own Messages transcript, only on
messages Messages has loaded (recent ones), and are verified in chat.db. Each
attempt ends with one `ui-action` event: `result` is `done`, `fallback` (replies
only) or `failed`, and `detail` is a sentence the UI shows as a toast.
- Opening the chat (both): nothing happens unless Messages' main window shows
  that conversation, checked by the title at the top of the chat (or its list
  item being the only one selected). If it isn't on screen: for one-to-one chats
  Messages' own link (`imessage://<handle>`, or `sms:<handle>` for SMS-only
  chats; only plain numbers and addresses), which counts when the title then
  names the chat or at least changed (the message itself still has to be found
  by text and minute); else its item in the list, scrolling
  the list a page at a time (at most 12) when it isn't in view, pressed, then
  any other selected item pressed off, then clicked with Messages in front. The
  log line ends with the steps taken (step names and item positions only).
- Tapback (`tapbacksViaMessages`): open the chat as above, find the target
  bubble in the transcript (match its text and the minute it was sent, newest
  first, scrolling the chat up a page at a time when it isn't on screen;
  attachments by their placeholder) and use the
  bubble's own accessibility action for that tapback ("Heart", "Thumbs up",
  "Ha ha!"…; works with Messages in the background); to remove, use the same
  tapback again. Because
  choosing a tapback toggles it, chat.db is checked first: adding one you
  already have, or removing one you don't, is `done` without touching
  Messages. Verify a new row with `associated_message_guid` pointing at the
  target and the right `associated_message_type` within ~5 s, then emit
  `ui-action` (`done` or `failed`). The new reaction itself reaches the UI as
  the target's normal `message-updated`, the same as anyone else's tapback. One
  tapback per person per message, like iMessage: picking a different one
  replaces yours.
- Thread reply (`threadRepliesViaMessages`): when a send (immediate or
  scheduled) has `threadRootId` and not `regularText`, open the chat as above,
  bring Messages to the front (its Reply only works there), find the root
  bubble and start replying: focus and select the bubble's row, then the menu
  bar's Reply command (on macOS 26.3 the only way that switches Messages into
  reply mode: the placeholder turns "Reply"); else its "Reply…" action, else
  its right-click menu's Reply. Reply mode counts only when the text field's
  placeholder changes or another text field takes the focus (the action alone
  changed an inner group of the entry area while the placeholder stayed
  "iMessage", and the text went out unthreaded); otherwise nothing is typed
  (`no-reply-mode`). The log names the way and the menu command used.
  Replies to other people are only attempted once a real reply to an older
  message has threaded on this Mac, in the user's own chat (Messages' Reply
  answers the newest message by default, so a reply to the newest proves
  nothing); until then they fail with "Not sent as a reply: real replies haven't
  worked on this Mac yet…". A reply that comes back unthreaded, or threaded
  under a different message, turns them off again.
  Then paste the text, as a person would (setting the field's value dropped
  the reply on macOS 26.3 and sent a regular text; setting its selected text
  did nothing). Everything on the clipboard (text, images, files) is saved
  first and put back right after, through AppKit. Return is pressed only when the
  focused field holds exactly the reply and the reply preview is still there
  (if Messages let go of the message being answered, the text is taken back
  out: `reply-mode-lost`, nothing sent); then the app that was in front gets
  focus back. Verify the sent row has `thread_originator_guid` = root and
  reconcile it to the clientId as usual (`ui-action` `done`; if the row arrived
  without a thread, `fallback`). If nothing was typed (the chat isn't in
  Messages' list, the message isn't loaded there, the reply field didn't
  open…), nothing is sent: `ui-action` `failed` and `send-status` failed with
  an error that starts "Not sent as a reply:" and says why. The UI offers Try
  again and Send as a regular text (`regularText`). If text may have been typed
  but nothing was verified (including timeouts), never send a second copy:
  emit `failed` and a `send-status` failed whose error says to check Messages
  before retrying (the row is still reconciled if it shows up later).
- Read sync failures also emit `ui-action` (`mark-read` or `mark-unread`,
  `failed`); successes don't, and neither does "Messages isn't open / has no window" (it would come
  with every conversation you open).
- Turning any helper on (false → true in `PATCH /api/settings`) queues a
  harmless System Events call (~90 s timeout, never touches Messages) so macOS
  asks "Backchannel wants to control System Events" right then, not during the
  first real action. Its result goes to `Health.messagesUi`: -1743 → `denied`,
  success → `lastResult: "Ready"` (permission stays as it was: Accessibility
  isn't proven until a real action works).
- At most one Messages action at a time (queue: read sync, tapbacks, replies
  and the permission check share it); timeouts 30 s (read sync), 45 s (tapback),
  60 s (reply), since finding a chat may mean scrolling Messages' list. Never in
  dry-run/fake-DB mode: there, a tapback or reply is simulated by writing the
  row it would have produced into the fake chat.db (like simulated sends), so
  the whole flow can be tried without a Mac. Tests use a fake runner.
