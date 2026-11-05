// Shared contract between the web UI and the Mac-side server.
// docs/api.md describes the same shapes for the Python side. Keep them in sync.
//
// Conventions
// - Times are ISO 8601 strings with an offset, e.g. "2026-10-31T09:14:00-07:00".
// - Ids are opaque strings. Always encodeURIComponent() them in URLs.
// - A "handle" is a phone number in E.164 form (+15551234567) or a lowercase email.
//   The user's own messages use senderHandle = null and fromMe = true.
//   In reaction / thread-participant lists the user is the literal string "me".

export type Service = 'iMessage' | 'SMS' | 'RCS'

export interface Person {
  /** Canonical handle for this person. */
  handle: string
  /** Every handle merged into this person (phone + emails). */
  handles: string[]
  /** Contact nickname or "First Last"; formatted number/email when unknown. */
  name: string
  /** First name for compact spots ("Jordan"). Same as name when unknown. */
  shortName: string
  /** True when the handle matched a Contacts card. */
  isContact: boolean
  /** Contact photo URL, or null to draw initials. */
  avatarUrl: string | null
}

export interface Me {
  name: string
  shortName: string
  handles: string[]
  avatarUrl: string | null
}

export type ConversationKind = 'dm' | 'group'

export interface ConversationPrefs {
  starred: boolean
  muted: boolean
  /** While in the future, the conversation is hidden from the sidebar. */
  snoozedUntil: string | null
  /** Custom sidebar section name (reserved for later). */
  section: string | null
}

export interface MessagePreview {
  id: string
  /** Flattened one-liner: "Photo", "Loved “see you at 7”", etc. */
  text: string
  senderHandle: string | null
  fromMe: boolean
  sentAt: string
}

export interface Conversation {
  id: string
  kind: ConversationKind
  /** Group name if set; the person's name for DMs; "Jordan, Sam & Ellie" for unnamed groups. */
  title: string
  /** Group with an explicit name. Shown like a #channel. */
  isNamed: boolean
  /** Everyone except the user. */
  participants: Person[]
  /** Service of the most recent message. */
  service: Service
  lastMessage: MessagePreview | null
  /** Unread incoming messages; at least 1 while `markedUnread`. */
  unreadCount: number
  /** "Mark as unread" was used here (kept by Backchannel until the conversation is read). */
  markedUnread: boolean
  /** Unread messages that mention the user. */
  mentionCount: number
  /** Short codes, no-reply senders, verification codes and the like. */
  isAutomated: boolean
  /** The ball is in the user's court. */
  awaitingReply: boolean
  /** When the message the user hasn't answered arrived. */
  awaitingSince: string | null
  /** Why this needs the user, and how soon (docs/api.md, "Needs you"). Null when nothing does. */
  priority: Priority | null
  prefs: ConversationPrefs
}

/** Why something needs the user. Worked out on the Mac from the text, who sent it and when. */
export type PriorityReason =
  /** A question or request for the user (the "Waiting on you" rule). */
  | { kind: 'asked' }
  /** Their name, in a group. */
  | { kind: 'mention' }
  /** An inline reply to one of their messages. */
  | { kind: 'reply' }
  /** A time that's coming up. label: "In 20 min", "Today 7 PM", "Tonight", "Tomorrow 9 AM", "Fri".
   *  at: when, if a clock time or "in N min" was named (ISO); null for a whole day. */
  | { kind: 'time'; label: string; at: string | null }
  /** Urgent wording, e.g. "urgent", "asap", "emergency", "call me" (the matched words, lowercase). */
  | { kind: 'urgent'; word: string }
  /** Several unread texts in a row from the same person. */
  | { kind: 'repeat'; count: number }
  /** Someone close (starred DM, in Chief, or among the people the user texts most). Only alongside another reason. */
  | { kind: 'vip'; name: string }

export interface Priority {
  /** urgent: now (urgent words, repeated texts, a time within ~3 hours). high: soon. */
  level: 'urgent' | 'high'
  /** Higher first, within a level. */
  score: number
  /** Most important first. */
  reasons: PriorityReason[]
  /** The message that matters most (jump target). */
  messageId: string | null
  /** When the time-sensitive part stops mattering (ISO), or null. */
  until: string | null
}

export type TapbackKind =
  | 'love'
  | 'like'
  | 'dislike'
  | 'laugh'
  | 'emphasize'
  | 'question'
  | 'emoji'
  | 'sticker'

export interface Reaction {
  kind: TapbackKind
  /** What to draw: ❤️ 👍 👎 😂 ‼️ ❓ or the custom emoji. */
  emoji: string
  /** Handles of the people who reacted; "me" for the user. */
  from: string[]
  /** For sticker tapbacks: the sticker image. */
  stickerUrl?: string | null
}

export type AttachmentKind = 'image' | 'video' | 'audio' | 'file'

export interface Attachment {
  id: string
  kind: AttachmentKind
  mimeType: string | null
  /** Original file name, e.g. IMG_4821.HEIC */
  name: string
  size: number | null
  /** Full file. HEIC is served as JPEG. */
  url: string
  /** Smaller preview for images and videos, or null. */
  thumbUrl: string | null
  width: number | null
  height: number | null
  isSticker: boolean
  /** An iOS 18+ Genmoji (custom emoji made on device). Rendered at emoji size. */
  isGenmoji: boolean
  /** iMessage's description of a Genmoji or sticker, e.g. "dog wearing sunglasses". */
  emojiDescription: string | null
  /** The file isn't on this Mac (offloaded to iCloud). */
  missing: boolean
}

export interface LinkPreview {
  url: string
  title: string | null
  siteName: string | null
  summary: string | null
  imageUrl: string | null
}

export interface ThreadSummary {
  replyCount: number
  /** Handles of recent repliers, newest first, at most 5; "me" for the user. */
  participants: string[]
  lastReplyAt: string
}

export type DeliveryStatus = 'sending' | 'sent' | 'delivered' | 'read' | 'failed'

export type MessageEventType = 'added' | 'removed' | 'renamed' | 'left' | 'photo' | 'other'

export interface Message {
  /** Message GUID from chat.db, or "local:<clientId>" for an optimistic send. */
  id: string
  conversationId: string
  senderHandle: string | null
  fromMe: boolean
  /** Plain text. Empty for attachment-only or unsent messages. */
  text: string
  sentAt: string
  service: Service
  attachments: Attachment[]
  reactions: Reaction[]
  linkPreview: LinkPreview | null
  /** Set when this is an inline reply: the id of the message it replies to (thread root). */
  threadRootId: string | null
  /** Set on thread roots that have replies. */
  thread: ThreadSummary | null
  editedAt: string | null
  unsent: boolean
  /** Only for the user's own messages. */
  status: DeliveryStatus | null
  readAt: string | null
  mentionsMe: boolean
  /** Bubble or screen effect name: "Slam", "Invisible Ink", "Confetti"… */
  effect: string | null
  isAudioMessage: boolean
  /** One-time passcode found in the text, for the copy button. */
  otpCode: string | null
  /** Why this message needs the user (only on messages that count toward the conversation's priority). */
  importance: PriorityReason[] | null
  /** Group events (someone added, renamed the chat…). Render as a centered event line. */
  event: { type: MessageEventType; text: string } | null
  /** Echo of the client id when this message was sent from Backchannel. */
  clientId: string | null
}

export interface MessagePage {
  /** Oldest first. */
  messages: Message[]
  /** Pass as `before` to load older messages. Null when there are no more. */
  nextCursor: string | null
}

export interface ThreadResponse {
  root: Message
  /** Oldest first. */
  replies: Message[]
}

export interface MediaItem {
  attachment: Attachment
  messageId: string
  senderHandle: string | null
  sentAt: string
}

export interface MediaPage {
  items: MediaItem[]
  nextCursor: string | null
}

export interface SendRequest {
  text: string
  /** Random id from the client, echoed back so the optimistic message can be swapped. */
  clientId: string
  /** Replying from a thread. With threadRepliesViaMessages on it goes out as a real reply through
   *  Messages (or fails with the reason, never quietly as a regular text); otherwise as a regular
   *  text. Either way the server keeps it in the thread here. */
  threadRootId?: string | null
  /** Future time = scheduled send. */
  sendAt?: string | null
  /** A thread reply sent as a regular text on purpose, skipping Messages' Reply (after a failed real reply). */
  regularText?: boolean
}

export interface SendResponse {
  clientId: string
  status: 'queued' | 'scheduled'
  scheduledId?: string
}

export interface ScheduledMessage {
  id: string
  conversationId: string
  text: string
  sendAt: string
  createdAt: string
  status: 'pending' | 'sent' | 'failed' | 'cancelled'
  error: string | null
}

export interface LaterItem {
  messageId: string
  conversationId: string
  savedAt: string
  remindAt: string | null
  done: boolean
  /** "Nudge me if they don't reply": only remind if nobody else has written in the conversation since. */
  ifNoReply: boolean
  /** They replied before remindAt, so this nudge isn't needed any more. */
  resolvedByReply: boolean
  /** When the reminder fired (remindAt passed and it still applied). Null until then. */
  firedAt: string | null
  message: Message | null
}

export interface ActivityItem {
  id: string
  type: 'reaction' | 'mention' | 'thread_reply'
  conversationId: string
  actorHandle: string
  at: string
  /** The reply or mention itself; for reactions, the user's message that got the reaction. */
  message: Message
  reaction?: Reaction
}

export interface ActivityFeed {
  /** Newest first, at most 50. */
  items: ActivityItem[]
  /** With `since`: how many events came after it, including ones past the 50 in `items`. */
  newCount?: number
}

export interface SearchResult {
  message: Message
  conversation: { id: string; title: string; kind: ConversationKind }
}

export interface Health {
  ok: boolean
  version: string
  chatDb: { path: string; readable: boolean; error: string | null }
  contacts: { count: number }
  send: { mode: 'applescript' | 'dry-run' | 'unavailable' }
  /** backend: 'claude-cli' = `claude -p` on your Claude subscription; 'api' = ANTHROPIC_API_KEY. */
  ai: { enabled: boolean; model: string | null; backend: 'claude-cli' | 'api' | null }
  /** Chief's database was found (profile cards + "Add to Chief loops"). */
  chief: { available: boolean }
  /** "Also mark read in Messages" (drives the Messages app through Accessibility). */
  messagesUi: {
    enabled: boolean
    /** Whether macOS lets Backchannel use Accessibility: unknown until the first try. */
    permission: 'granted' | 'denied' | 'unknown'
    /** The last attempt, e.g. "Marked Jordan Park read" or why it failed. */
    lastResult: string | null
    lastAt: string | null
  }
}

/** Server-side preferences (they change what the Mac does, so they live on the server). */
export interface ServerSettings {
  /** When you open a conversation here that Messages still shows as unread, mark it read in Messages too
   *  (and "Mark as unread" here marks it unread there). */
  markReadInMessages: boolean
  /** Send tapbacks by driving the Messages app (right-click the message › Tapback). Experimental. */
  tapbacksViaMessages: boolean
  /** Send thread replies as real inline replies by driving the Messages app (right-click › Reply). Experimental. */
  threadRepliesViaMessages: boolean
  /** Send a message that is only a link from Messages' own text field, so it carries a preview
   *  (AppleScript sends are plain text). Experimental. */
  linkPreviewsViaMessages: boolean
  /** A short note about you that catch-up and suggested replies read (who's who, nicknames). At most
   *  1500 characters; empty by default. */
  aboutMe: string
  /** The emoji Messages offers as quick tapbacks (its recent ones), as seen the last time a tapback was sent.
   *  Read-only; empty until then. Other emoji may not be available as tapbacks yet. */
  tapbackQuick: string[]
}

/** The six classic tapbacks, the ones the Messages app can send from a right-click. */
export type ClassicTapback = 'love' | 'like' | 'dislike' | 'laugh' | 'emphasize' | 'question'

/** An emoji you added (Slack-style custom emoji). Sent through iMessage as an image. */
export interface CustomEmoji {
  /** Shortcode without colons: lowercase letters, digits, "-", "_", "+". */
  name: string
  /** Image URL (served by Backchannel). */
  url: string
  mimeType: string
  width: number | null
  height: number | null
  animated: boolean
  source: 'upload' | 'link' | 'imessage'
  createdAt: string
}

/** A sticker or Genmoji found in your iMessage history. */
export interface StickerItem {
  /** Attachment GUID of the newest copy in chat.db. */
  id: string
  kind: 'sticker' | 'genmoji'
  description: string | null
  /** Browser-friendly image (PNG with transparency for HEIC stickers). */
  url: string
  timesUsed: number
  lastUsedAt: string
  /** You've sent it yourself at least once. */
  sentByMe: boolean
}

/** What Chief knows about a person, shown on their profile. */
export interface ChiefCard {
  /** The name Chief uses for them. */
  name: string
  relationship: string | null
  /** Human-readable, e.g. "March 3". */
  birthday: string | null
  notes: string | null
  /** Open loops that mention them, newest first (at most 5). */
  loops: { id: number; title: string; status: 'open' | 'in_progress'; dueDate: string | null }[]
  /** Profile facts that mention them (at most 3). */
  facts: string[]
}

export type CatchUpKind = 'reply_now' | 'reply_soon' | 'plans' | 'caught_up'

export interface CatchUpItem {
  conversationId: string
  /** Who or what, e.g. "Marcus Webb" or "Thanksgiving at Grace's". */
  title: string
  summary: string
  /** Suggested reply in the user's voice. */
  draft: string | null
  /** For plans. */
  status: 'needs_action' | 'locked_in' | null
}

export interface CatchUpSection {
  kind: CatchUpKind
  title: string
  items: CatchUpItem[]
}

export interface CatchUp {
  generatedAt: string
  /** One-line summary, e.g. "Nothing on fire. Two people are waiting on you." */
  vibe: string
  sections: CatchUpSection[]
}

/** Server-sent events on GET /api/events. Each SSE `data:` line is one of these as JSON. */
export type ServerEvent =
  | { type: 'hello'; serverTime: string }
  | { type: 'message'; message: Message }
  | { type: 'message-updated'; message: Message }
  | { type: 'conversation'; conversation: Conversation }
  | {
      type: 'send-status'
      clientId: string
      conversationId: string
      status: 'sent' | 'failed'
      messageId?: string
      error?: string
    }
  | { type: 'scheduled'; scheduled: ScheduledMessage }
  /** A saved item changed: its reminder fired (firedAt set), or a reply resolved it. */
  | { type: 'later'; item: LaterItem }
  /** How a Messages-app action went (tapback, thread reply, mark read/unread). Tapbacks and replies
   *  themselves arrive as normal message updates once they're in chat.db. */
  | {
      type: 'ui-action'
      action: 'tapback' | 'reply' | 'mark-read' | 'mark-unread'
      conversationId: string
      /** The tapback's target, or the thread root for a reply. */
      messageId: string | null
      /** The send's clientId, for replies. */
      clientId: string | null
      /** done: it worked. fallback: the reply couldn't be made a real reply, so it went out as a
       *  regular text. failed: nothing was done, or Messages didn't confirm it. */
      result: 'done' | 'fallback' | 'failed'
      /** A sentence for a toast, e.g. "Couldn't find that message in Messages; it may be too old." */
      detail: string
    }

export interface Api {
  health(): Promise<Health>
  me(): Promise<Me>
  conversations(): Promise<Conversation[]>
  messages(conversationId: string, before?: string | null): Promise<MessagePage>
  thread(conversationId: string, messageId: string): Promise<ThreadResponse>
  media(conversationId: string, before?: string | null): Promise<MediaPage>
  send(conversationId: string, req: SendRequest): Promise<SendResponse>
  /** Send a photo or file. The optimistic message is swapped when chat.db shows the real one. */
  sendFile(conversationId: string, file: File, clientId: string): Promise<SendResponse>
  /** Text someone you haven't texted from this Mac yet (or by number/email). */
  startConversation(handle: string, text: string, clientId: string): Promise<{ conversationId: string }>
  markRead(conversationId: string): Promise<Conversation>
  /** Mark as unread from `messageId` on (default: the newest incoming message). Reading clears it. */
  markUnread(conversationId: string, messageId?: string | null): Promise<Conversation>
  setPrefs(conversationId: string, prefs: Partial<ConversationPrefs>): Promise<Conversation>
  later(): Promise<LaterItem[]>
  saveForLater(conversationId: string, messageId: string, remindAt: string | null, ifNoReply?: boolean): Promise<LaterItem>
  /** Mark a saved item done (or not done). */
  setLaterDone(messageId: string, done: boolean): Promise<LaterItem>
  removeLater(messageId: string): Promise<void>
  scheduled(): Promise<ScheduledMessage[]>
  cancelScheduled(id: string): Promise<void>
  /** Pass when the user last looked at Activity to get newCount. */
  activity(since?: string | null): Promise<ActivityFeed>
  /** Thread roots across all conversations, newest reply first (last 30 days). */
  threads(): Promise<Message[]>
  search(q: string, conversationId?: string | null): Promise<SearchResult[]>
  draftReply(conversationId: string, threadRootId?: string | null, hint?: string | null): Promise<string[]>
  catchUp(refresh?: boolean): Promise<CatchUp>
  /** Chief's card for a person; null when Chief doesn't know them (or Chief isn't installed). */
  chiefCard(handle: string): Promise<ChiefCard | null>
  /** Track a message as an open loop in Chief. */
  addChiefLoop(conversationId: string, messageId: string, title: string): Promise<{ id: number; title: string }>
  /** Add or remove one of your tapbacks by driving the Messages app (needs tapbacksViaMessages). */
  /** A classic tapback, or kind 'emoji' with the emoji (Messages offers its recent ones; see ServerSettings.tapbackQuick). */
  sendTapback(conversationId: string, messageId: string, kind: ClassicTapback | 'emoji', remove: boolean,
              emoji?: string, names?: string[]): Promise<{ status: 'queued' }>
  serverSettings(): Promise<ServerSettings>
  setServerSettings(patch: Partial<ServerSettings>): Promise<ServerSettings>
  customEmoji(): Promise<CustomEmoji[]>
  /** Add an emoji from a file or from an image link (e.g. slackmojis.com). */
  addCustomEmoji(name: string, source: { file: File } | { url: string }): Promise<CustomEmoji>
  removeCustomEmoji(name: string): Promise<void>
  /** Stickers and Genmoji from your iMessage history, most used first. */
  stickers(): Promise<StickerItem[]>
  /** Copy a sticker or Genmoji into your custom emoji under a name. */
  saveStickerAsEmoji(stickerId: string, name: string): Promise<CustomEmoji>
  /** Live updates. onStatus reports the connection so the UI can show "Reconnecting…". */
  subscribe(onEvent: (e: ServerEvent) => void, onStatus?: (s: 'open' | 'reconnecting') => void): () => void
}
