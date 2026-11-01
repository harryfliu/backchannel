// In-memory implementation of the Api contract, used by the clickable mock.
// It behaves like the real server: sends get confirmed, people reply, new texts arrive.
import type {
  Api,
  CustomEmoji,
  StickerItem,
  ActivityItem,
  CatchUp,
  Conversation,
  Health,
  LaterItem,
  MediaPage,
  Message,
  ScheduledMessage,
  SearchResult,
  SendRequest,
  ServerEvent,
} from '../types'
import {
  activitySeed,
  autoReplies,
  awaiting,
  buildMockDb,
  catchUpSeed,
  chiefSeed,
  draftFallback,
  draftSeed,
  laterSeed,
  me,
  previewText,
  scheduledSeed,
  timedArrivals,
} from './data'
import { emojiArt, stickerArt } from './art'
import { applyTapback, choiceEmoji, type TapbackChoice } from '../../lib/tapbacks'
import { prioritize } from './priority'

const wait = (ms: number) => new Promise((res) => setTimeout(res, ms))
const clone = <T,>(v: T): T => (typeof structuredClone === 'function' ? structuredClone(v) : JSON.parse(JSON.stringify(v)))

export function createMockApi(): Api {
  const db = buildMockDb()
  let later: LaterItem[] = laterSeed()
  let scheduled: ScheduledMessage[] = scheduledSeed()
  const activity: ActivityItem[] = activitySeed(db.messages)
  let catchUp: CatchUp = catchUpSeed()
  const listeners = new Set<(e: ServerEvent) => void>()
  let started = false
  let nextId = 1000
  let serverSettings = {
    markReadInMessages: false,
    tapbacksViaMessages: false,
    threadRepliesViaMessages: false,
    // What Messages offers as quick tapbacks (its recent emoji), as on Harry's Mac.
    tapbackQuick: ['😂', '👀', '😭', '🤔', '😱'],
  }
  const seededAt = new Date(Date.now() - 20 * 86400000).toISOString()
  const emojiItem = (name: string, url: string, mimeType = 'image/svg+xml'): CustomEmoji => ({
    name,
    url,
    mimeType,
    width: 64,
    height: 64,
    animated: false,
    source: 'upload',
    createdAt: seededAt,
  })
  let emoji: CustomEmoji[] = [
    emojiItem('yay-blob', emojiArt.yayBlob),
    emojiItem('lgtm', emojiArt.lgtm),
    emojiItem('nice', emojiArt.nice),
    emojiItem('hype', emojiArt.hype),
  ]
  const stickerList: StickerItem[] = [
    { id: 'st-dog', kind: 'genmoji', description: 'dog wearing sunglasses', url: stickerArt.coolDog, timesUsed: 7, lastUsedAt: new Date(Date.now() - 2 * 86400000).toISOString(), sentByMe: true },
    { id: 'st-pizza', kind: 'sticker', description: 'pizza slice', url: stickerArt.pizza, timesUsed: 4, lastUsedAt: new Date(Date.now() - 5 * 86400000).toISOString(), sentByMe: false },
  ]
  let nextLoop = 81

  const emit = (e: ServerEvent) => listeners.forEach((fn) => fn(clone(e)))

  const convo = (id: string): Conversation => {
    const c = db.conversations.get(id)
    if (!c) throw new Error(`No conversation ${id}`)
    return c
  }

  function refreshSummary(cid: string) {
    const c = convo(cid)
    const list = db.messages.get(cid) ?? []
    const last = list[list.length - 1]
    if (last) {
      c.lastMessage = {
        id: last.id,
        text: previewText(last, db.people),
        senderHandle: last.senderHandle,
        fromMe: last.fromMe,
        sentAt: last.sentAt,
      }
      c.service = last.service
    }
    const since = awaiting(c.kind, list, c.isAutomated)
    c.awaitingReply = !!since
    c.awaitingSince = since
    prioritize(c, list, since, db.vips)
    emit({ type: 'conversation', conversation: c })
  }

  function addMessage(m: Message) {
    const list = db.messages.get(m.conversationId) ?? []
    list.push(m)
    db.messages.set(m.conversationId, list)
    if (m.threadRootId) {
      const root = list.find((x) => x.id === m.threadRootId)
      if (root) {
        const who = m.fromMe ? 'me' : (m.senderHandle as string)
        root.thread = {
          replyCount: (root.thread?.replyCount ?? 0) + 1,
          participants: [who, ...(root.thread?.participants ?? []).filter((p) => p !== who)].slice(0, 5),
          lastReplyAt: m.sentAt,
        }
        emit({ type: 'message-updated', message: root })
      }
    }
    emit({ type: 'message', message: m })
  }

  function incoming(cid: string, from: { handle: string }, text: string, threadRootId: string | null = null) {
    const c = convo(cid)
    const m: Message = {
      id: `m${nextId++}`,
      conversationId: cid,
      senderHandle: from.handle,
      fromMe: false,
      text,
      sentAt: new Date().toISOString(),
      service: c.service,
      attachments: [],
      reactions: [],
      linkPreview: null,
      threadRootId,
      thread: null,
      editedAt: null,
      unsent: false,
      status: null,
      readAt: null,
      mentionsMe: false,
      effect: null,
      isAudioMessage: false,
      otpCode: null,
      importance: null,
      event: null,
      clientId: null,
    }
    c.unreadCount += 1
    const list = db.messages.get(cid) ?? []
    prioritize(c, [...list, m], awaiting(c.kind, [...list, m], c.isAutomated), db.vips)
    addMessage(m)
    refreshSummary(cid)
  }

  function updateMessage(cid: string, id: string, patch: Partial<Message>) {
    const list = db.messages.get(cid) ?? []
    const m = list.find((x) => x.id === id)
    if (!m) return
    Object.assign(m, patch)
    emit({ type: 'message-updated', message: m })
  }

  async function deliver(cid: string, req: SendRequest) {
    await wait(650)
    const c = convo(cid)
    const m: Message = {
      id: `m${nextId++}`,
      conversationId: cid,
      senderHandle: null,
      fromMe: true,
      text: req.text,
      sentAt: new Date().toISOString(),
      service: c.service,
      attachments: [],
      reactions: [],
      linkPreview: null,
      threadRootId: req.threadRootId ?? null,
      thread: null,
      editedAt: null,
      unsent: false,
      status: 'sent',
      readAt: null,
      mentionsMe: false,
      effect: null,
      isAudioMessage: false,
      otpCode: null,
      importance: null,
      event: null,
      clientId: req.clientId,
    }
    addMessage(m)
    emit({ type: 'send-status', clientId: req.clientId, conversationId: cid, status: 'sent', messageId: m.id })
    if (req.threadRootId && serverSettings.threadRepliesViaMessages) {
      emit({ type: 'ui-action', action: 'reply', conversationId: cid, messageId: req.threadRootId, clientId: req.clientId, result: 'done', detail: 'Sent as a reply in Messages' })
    }
    refreshSummary(cid)
    await wait(900)
    if (c.service !== 'SMS') updateMessage(cid, m.id, { status: 'delivered' })
    const reply = autoReplies[cid]
    if (reply) {
      delete autoReplies[cid] // reply once per conversation
      await wait(reply.delay)
      if (c.kind === 'dm' && c.service !== 'SMS') updateMessage(cid, m.id, { status: 'read', readAt: new Date().toISOString() })
      incoming(cid, reply.from, reply.text, req.threadRootId ?? null)
    }
  }

  function start() {
    if (started) return
    started = true
    for (const t of timedArrivals) setTimeout(() => incoming(t.cid, t.from, t.text), t.after)
  }

  const api: Api = {
    async health(): Promise<Health> {
      return {
        ok: true,
        version: 'mock',
        chatDb: { path: '~/Library/Messages/chat.db', readable: true, error: null },
        contacts: { count: db.people.size },
        send: { mode: 'dry-run' },
        ai: { enabled: true, model: 'haiku', backend: 'claude-cli' },
        chief: { available: true },
        messagesUi: {
          enabled: serverSettings.markReadInMessages || serverSettings.tapbacksViaMessages || serverSettings.threadRepliesViaMessages,
          permission: 'unknown',
          lastResult: null,
          lastAt: null,
        },
      }
    },
    async me() {
      return clone(me)
    },
    async conversations() {
      return clone(
        [...db.conversations.values()].sort((a, b) => (b.lastMessage?.sentAt ?? '').localeCompare(a.lastMessage?.sentAt ?? '')),
      )
    },
    async messages(cid) {
      await wait(60)
      return { messages: clone(db.messages.get(cid) ?? []), nextCursor: null }
    },
    async thread(cid, mid) {
      const list = db.messages.get(cid) ?? []
      const root = list.find((m) => m.id === mid)
      if (!root) throw new Error('That message is no longer available.')
      return clone({ root, replies: list.filter((m) => m.threadRootId === mid) })
    },
    async media(cid): Promise<MediaPage> {
      const items = (db.messages.get(cid) ?? [])
        .flatMap((m) => m.attachments.map((a) => ({ attachment: a, messageId: m.id, senderHandle: m.senderHandle, sentAt: m.sentAt })))
        .reverse()
      return clone({ items, nextCursor: null })
    },
    async send(cid, req) {
      if (req.sendAt && new Date(req.sendAt).getTime() > Date.now() + 30000) {
        const s: ScheduledMessage = {
          id: `s${nextId++}`,
          conversationId: cid,
          text: req.text,
          sendAt: req.sendAt,
          createdAt: new Date().toISOString(),
          status: 'pending',
          error: null,
        }
        scheduled = [...scheduled, s]
        emit({ type: 'scheduled', scheduled: s })
        return { clientId: req.clientId, status: 'scheduled', scheduledId: s.id }
      }
      void deliver(cid, req)
      return { clientId: req.clientId, status: 'queued' }
    },
    async sendFile(cid, file, clientId) {
      const c = convo(cid)
      const url = URL.createObjectURL(file)
      const kind = file.type.startsWith('image/') ? 'image' : file.type.startsWith('video/') ? 'video' : file.type.startsWith('audio/') ? 'audio' : 'file'
      setTimeout(() => {
        const m: Message = {
          id: `m${nextId++}`,
          conversationId: cid,
          senderHandle: null,
          fromMe: true,
          text: '',
          sentAt: new Date().toISOString(),
          service: c.service,
          attachments: [
            {
              id: `a${nextId++}`,
              kind,
              mimeType: file.type || null,
              name: file.name,
              size: file.size,
              url,
              thumbUrl: kind === 'image' ? url : null,
              width: null,
              height: null,
              isSticker: false,
              isGenmoji: false,
              emojiDescription: null,
              missing: false,
            },
          ],
          reactions: [],
          linkPreview: null,
          threadRootId: null,
          thread: null,
          editedAt: null,
          unsent: false,
          status: 'delivered',
          readAt: null,
          mentionsMe: false,
          effect: null,
          isAudioMessage: false,
          otpCode: null,
          importance: null,
          event: null,
          clientId,
        }
        addMessage(m)
        emit({ type: 'send-status', clientId, conversationId: cid, status: 'sent', messageId: m.id })
        refreshSummary(cid)
      }, 900)
      return { clientId, status: 'queued' }
    },
    async startConversation(handle, text, clientId) {
      const digits = handle.replace(/\D/g, '')
      const existing = [...db.conversations.values()].find(
        (c) => c.kind === 'dm' && c.participants[0]?.handles.some((h) => h === handle || (digits.length >= 10 && h.replace(/\D/g, '').endsWith(digits.slice(-10)))),
      )
      if (existing) {
        void deliver(existing.id, { text, clientId })
        return { conversationId: existing.id }
      }
      const normalized = handle.includes('@') ? handle.toLowerCase() : `+${digits.length === 10 ? '1' + digits : digits}`
      const id = `dm-new-${normalized.replace(/[^a-z0-9]/gi, '')}`
      const name = normalized.includes('@') ? normalized : `(${digits.slice(-10, -7)}) ${digits.slice(-7, -4)}-${digits.slice(-4)}`
      const person = { handle: normalized, handles: [normalized], name, shortName: name, isContact: false, avatarUrl: null }
      const c: Conversation = {
        id,
        kind: 'dm',
        title: name,
        isNamed: false,
        participants: [person],
        service: 'iMessage',
        lastMessage: null,
        unreadCount: 0,
        markedUnread: false,
        mentionCount: 0,
        isAutomated: false,
        awaitingReply: false,
        awaitingSince: null,
        priority: null,
        prefs: { starred: false, muted: false, snoozedUntil: null, section: null },
      }
      db.conversations.set(id, c)
      db.messages.set(id, [])
      db.people.set(normalized, person)
      setTimeout(() => {
        emit({ type: 'conversation', conversation: c })
        void deliver(id, { text, clientId })
      }, 500)
      return { conversationId: id }
    },
    async markRead(cid) {
      const c = convo(cid)
      c.unreadCount = 0
      c.markedUnread = false
      c.mentionCount = 0
      const list = db.messages.get(cid) ?? []
      prioritize(c, list, c.awaitingSince, db.vips)
      return clone(c)
    },
    async markUnread(cid, messageId) {
      const c = convo(cid)
      const list = db.messages.get(cid) ?? []
      const from = messageId ? list.findIndex((m) => m.id === messageId) : -1
      if (messageId && from === -1) throw new Error("That message isn't in this conversation.")
      // Like the server: incoming messages from that one on (default: just the newest), at least 1.
      const counted = messageId ? list.slice(from).filter((m) => !m.fromMe && !m.event && !m.unsent).length : 1
      c.unreadCount = Math.max(1, counted)
      c.markedUnread = true
      emit({ type: 'conversation', conversation: c })
      return clone(c)
    },
    async setPrefs(cid, prefs) {
      const c = convo(cid)
      c.prefs = { ...c.prefs, ...prefs }
      emit({ type: 'conversation', conversation: c })
      return clone(c)
    },
    async later() {
      return clone(later)
    },
    async saveForLater(cid, mid, remindAt, ifNoReply) {
      const message = (db.messages.get(cid) ?? []).find((m) => m.id === mid) ?? null
      const item: LaterItem = {
        messageId: mid,
        conversationId: cid,
        savedAt: new Date().toISOString(),
        remindAt,
        done: false,
        ifNoReply: !!ifNoReply,
        resolvedByReply: false,
        firedAt: null,
        message,
      }
      later = [item, ...later.filter((l) => l.messageId !== mid)]
      return clone(item)
    },
    async setLaterDone(mid, done) {
      later = later.map((l) => (l.messageId === mid ? { ...l, done } : l))
      const item = later.find((l) => l.messageId === mid)
      if (!item) throw new Error('That saved item is gone.')
      return clone(item)
    },
    async removeLater(mid) {
      later = later.filter((l) => l.messageId !== mid)
    },
    async scheduled() {
      return clone(scheduled)
    },
    async cancelScheduled(id) {
      scheduled = scheduled.map((s) => (s.id === id ? { ...s, status: 'cancelled' } : s))
      const s = scheduled.find((x) => x.id === id)
      if (s) emit({ type: 'scheduled', scheduled: s })
    },
    async activity() {
      return clone(activity)
    },
    async threads() {
      const roots = [...db.messages.values()].flat().filter((m) => m.thread)
      return clone(roots.sort((a, b) => b.thread!.lastReplyAt.localeCompare(a.thread!.lastReplyAt)))
    },
    async search(q, cid): Promise<SearchResult[]> {
      const needle = q.trim().toLowerCase()
      if (!needle) return []
      const out: SearchResult[] = []
      for (const [id, list] of db.messages) {
        if (cid && id !== cid) continue
        const c = convo(id)
        for (const m of list) {
          if (m.text.toLowerCase().includes(needle)) out.push({ message: m, conversation: { id, title: c.title, kind: c.kind } })
        }
      }
      return clone(out.sort((a, b) => b.message.sentAt.localeCompare(a.message.sentAt)).slice(0, 40))
    },
    async draftReply(cid) {
      await wait(900)
      return [...(draftSeed[cid] ?? draftFallback)]
    },
    async chiefCard(handle) {
      return clone(chiefSeed[handle] ?? null)
    },
    async addChiefLoop(_cid, _mid, title) {
      return { id: nextLoop++, title }
    },
    async sendTapback(cid, mid, kind, remove, emoji) {
      if (!serverSettings.tapbacksViaMessages) throw new Error('Sending tapbacks is off. Turn it on in Settings.')
      const m = (db.messages.get(cid) ?? []).find((x) => x.id === mid)
      if (!m) throw new Error('That message is gone.')
      if (kind === 'emoji' && !emoji) throw new Error("That isn't an emoji Messages can use as a tapback.")
      const choice: TapbackChoice = kind === 'emoji' ? { kind, emoji: emoji! } : { kind }
      setTimeout(() => {
        updateMessage(cid, mid, { reactions: applyTapback(m.reactions, choice, remove) })
        const detail = remove ? 'Removed your tapback in Messages' : `Sent ${choiceEmoji(choice)} in Messages`
        emit({ type: 'ui-action', action: 'tapback', conversationId: cid, messageId: mid, clientId: null, result: 'done', detail })
      }, 700)
      return { status: 'queued' as const }
    },
    async serverSettings() {
      return { ...serverSettings }
    },
    async setServerSettings(patch) {
      serverSettings = { ...serverSettings, ...patch }
      return { ...serverSettings }
    },
    async customEmoji() {
      return clone(emoji)
    },
    async addCustomEmoji(name, source) {
      if (emoji.some((e) => e.name === name)) throw new Error(`:${name}: is already taken. Pick another name.`)
      const url = 'file' in source ? URL.createObjectURL(source.file) : source.url
      const mimeType = 'file' in source ? source.file.type || 'image/png' : 'image/gif'
      const item: CustomEmoji = { ...emojiItem(name, url, mimeType), source: 'file' in source ? 'upload' : 'link', createdAt: new Date().toISOString() }
      emoji = [...emoji, item]
      return clone(item)
    },
    async removeCustomEmoji(name) {
      emoji = emoji.filter((e) => e.name !== name)
    },
    async stickers() {
      await wait(250)
      return clone(stickerList)
    },
    async saveStickerAsEmoji(id, name) {
      const st = stickerList.find((x) => x.id === id)
      if (!st) throw new Error('That sticker is gone.')
      if (emoji.some((e) => e.name === name)) throw new Error(`:${name}: is already taken. Pick another name.`)
      const item: CustomEmoji = { ...emojiItem(name, st.url), source: 'imessage', createdAt: new Date().toISOString() }
      emoji = [...emoji, item]
      return clone(item)
    },
    async catchUp(refresh) {
      await wait(refresh ? 1400 : 300)
      if (refresh) catchUp = { ...catchUp, generatedAt: new Date().toISOString() }
      return clone(catchUp)
    },
    subscribe(fn) {
      listeners.add(fn)
      start()
      fn({ type: 'hello', serverTime: new Date().toISOString() })
      return () => listeners.delete(fn)
    },
  }
  return api
}
