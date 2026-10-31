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
  unreadCount: number
  /** Unread messages that mention the user. */
  mentionCount: number
  /** Short codes, no-reply senders, verification codes and the like. */
  isAutomated: boolean
  /** The ball is in the user's court. */
  awaitingReply: boolean
  /** When the message the user hasn't answered arrived. */
  awaitingSince: string | null
  prefs: ConversationPrefs
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
  /** Replying from a thread. iMessage can't receive threaded replies from other apps,
   *  so this goes out as a normal text; the server remembers the thread locally. */
  threadRootId?: string | null
  /** Future time = scheduled send. */
  sendAt?: string | null
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
  ai: { enabled: boolean; model: string | null }
  /** Chief's database was found (profile cards + "Add to Chief loops"). */
  chief: { available: boolean }
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
  setPrefs(conversationId: string, prefs: Partial<ConversationPrefs>): Promise<Conversation>
  later(): Promise<LaterItem[]>
  saveForLater(conversationId: string, messageId: string, remindAt: string | null, ifNoReply?: boolean): Promise<LaterItem>
  /** Mark a saved item done (or not done). */
  setLaterDone(messageId: string, done: boolean): Promise<LaterItem>
  removeLater(messageId: string): Promise<void>
  scheduled(): Promise<ScheduledMessage[]>
  cancelScheduled(id: string): Promise<void>
  activity(): Promise<ActivityItem[]>
  /** Thread roots across all conversations, newest reply first (last 30 days). */
  threads(): Promise<Message[]>
  search(q: string, conversationId?: string | null): Promise<SearchResult[]>
  draftReply(conversationId: string, threadRootId?: string | null, hint?: string | null): Promise<string[]>
  catchUp(refresh?: boolean): Promise<CatchUp>
  /** Chief's card for a person; null when Chief doesn't know them (or Chief isn't installed). */
  chiefCard(handle: string): Promise<ChiefCard | null>
  /** Track a message as an open loop in Chief. */
  addChiefLoop(conversationId: string, messageId: string, title: string): Promise<{ id: number; title: string }>
  /** Live updates. onStatus reports the connection so the UI can show "Reconnecting…". */
  subscribe(onEvent: (e: ServerEvent) => void, onStatus?: (s: 'open' | 'reconnecting') => void): () => void
}
