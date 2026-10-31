// App state: one small store with plain functions as actions.
import { useRef, useSyncExternalStore } from 'react'
import { api, isMock } from '../api/client'
import { isNative, postNative } from '../native'
import type {
  ActivityItem,
  CatchUp,
  ChiefCard,
  CustomEmoji,
  ServerSettings,
  StickerItem,
  Conversation,
  ConversationPrefs,
  Health,
  LaterItem,
  MediaItem,
  Me,
  Message,
  Person,
  ScheduledMessage,
  SearchResult,
  ServerEvent,
} from '../api/types'
import { uid, whenLabel } from '../lib/format'

export type View =
  | { kind: 'conversation'; id: string }
  | { kind: 'catchup' }
  | { kind: 'threads' }
  | { kind: 'scheduled' }

export type Panel =
  | { kind: 'thread'; cid: string; mid: string }
  | { kind: 'profile'; handle: string; cid: string | null }
  | { kind: 'details'; cid: string }
  | null

export type RailTab = 'home' | 'dms' | 'activity' | 'later'

export interface Settings {
  theme: 'system' | 'light' | 'dark'
  sidebarTheme: 'aubergine' | 'ocean' | 'forest' | 'graphite'
  /** Slack style keeps inline replies in the thread pane only. */
  threadReplies: 'thread-only' | 'also-inline'
  compact: boolean
  /** Mac app only: show a notification for new texts. */
  notify: boolean
}

export interface Toast {
  id: string
  text: string
  tone?: 'default' | 'error'
}

interface ThreadState {
  root: Message | null
  replies: Message[]
  loading: boolean
  error: string | null
}

interface MessagesState {
  items: Message[]
  nextCursor: string | null
  loading: boolean
  loadingOlder: boolean
  error: string | null
}

export interface State {
  booted: boolean
  bootError: string | null
  me: Me | null
  health: Health | null
  connection: 'open' | 'reconnecting'
  conversations: Record<string, Conversation>
  people: Record<string, Person>
  messages: Record<string, MessagesState>
  threads: Record<string, ThreadState>
  media: Record<string, MediaItem[] | undefined>
  view: View | null
  history: View[]
  historyIndex: number
  rail: RailTab
  panel: Panel
  mobilePane: 'list' | 'main' | 'panel'
  drafts: Record<string, string>
  aiDrafts: Record<string, { loading: boolean; options: string[]; error: string | null }>
  newMarker: Record<string, string | null>
  highlight: { id: string; at: number } | null
  later: LaterItem[]
  scheduled: ScheduledMessage[]
  activity: ActivityItem[]
  activitySeenAt: string | null
  catchUp: { data: CatchUp | null; loading: boolean; error: string | null; done: string[] }
  search: { open: boolean; q: string; results: SearchResult[]; loading: boolean; scope: string | null }
  switcherOpen: boolean
  settingsOpen: boolean
  shortcutsOpen: boolean
  composeOpen: boolean
  tourOpen: boolean
  collapsed: Record<string, boolean>
  settings: Settings
  toasts: Toast[]
  /** Something to fix before Backchannel can work (Full Disk Access). */
  setup: 'fda' | null
  /** Open this conversation as soon as it shows up (after texting someone new). */
  pendingOpen: string | null
  chiefCards: Record<string, ChiefCard | null | 'loading' | undefined>
  /** The "Add to Chief loops" dialog. */
  chiefDraft: { cid: string; mid: string; title: string } | null
  /** Your own emoji (Slack-style), loaded at startup. */
  customEmoji: CustomEmoji[]
  /** Stickers and Genmoji from your iMessage history; null until first opened. */
  stickers: StickerItem[] | null
  /** The "Add emoji" dialog; a sticker to save, or blank for a new one. */
  emojiDialog: { sticker?: StickerItem } | null
  /** Preferences that change what the Mac does (null until loaded). */
  serverSettings: ServerSettings | null
}

// ------------------------------------------------------------------ storage

const LS_KEY = 'backchannel:v1'
function loadLocal(): Partial<Pick<State, 'settings' | 'collapsed' | 'drafts'>> {
  try {
    const raw = localStorage.getItem(LS_KEY)
    return raw ? JSON.parse(raw) : {}
  } catch {
    return {}
  }
}
function saveLocal(s: State) {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify({ settings: s.settings, collapsed: s.collapsed, drafts: s.drafts }))
  } catch {
    // storage blocked; settings just won't persist
  }
}

const local = loadLocal()

const defaultSettings: Settings = {
  theme: 'system',
  sidebarTheme: 'aubergine',
  threadReplies: 'thread-only',
  compact: false,
  notify: false,
}

let state: State = {
  booted: false,
  bootError: null,
  me: null,
  health: null,
  connection: 'open',
  conversations: {},
  people: {},
  messages: {},
  threads: {},
  media: {},
  view: null,
  history: [],
  historyIndex: -1,
  rail: 'home',
  panel: null,
  mobilePane: 'list',
  drafts: local.drafts ?? {},
  aiDrafts: {},
  newMarker: {},
  highlight: null,
  later: [],
  scheduled: [],
  activity: [],
  activitySeenAt: null,
  catchUp: { data: null, loading: false, error: null, done: [] },
  search: { open: false, q: '', results: [], loading: false, scope: null },
  switcherOpen: false,
  settingsOpen: false,
  shortcutsOpen: false,
  composeOpen: false,
  tourOpen: false,
  collapsed: local.collapsed ?? { automated: true },
  settings: { ...defaultSettings, ...(local.settings ?? {}) },
  toasts: [],
  setup: null,
  pendingOpen: null,
  chiefCards: {},
  chiefDraft: null,
  customEmoji: [],
  stickers: null,
  emojiDialog: null,
  serverSettings: null,
}

const listeners = new Set<() => void>()
export const getState = () => state
export function setState(patch: Partial<State> | ((s: State) => Partial<State>)) {
  const next = typeof patch === 'function' ? patch(state) : patch
  state = { ...state, ...next }
  if ('settings' in next || 'collapsed' in next || 'drafts' in next) saveLocal(state)
  listeners.forEach((l) => l())
}
const subscribe = (l: () => void) => {
  listeners.add(l)
  return () => listeners.delete(l)
}

/** Select a slice. Re-renders only when the slice changes (shallow compare for arrays/objects). */
export function useStore<T>(selector: (s: State) => T): T {
  const last = useRef<{ value: T } | null>(null)
  return useSyncExternalStore(subscribe, () => {
    const value = selector(state)
    if (last.current && shallowEqual(last.current.value, value)) return last.current.value
    last.current = { value }
    return value
  })
}

function shallowEqual(a: unknown, b: unknown) {
  if (Object.is(a, b)) return true
  if (typeof a !== 'object' || typeof b !== 'object' || !a || !b) return false
  if (Array.isArray(a) !== Array.isArray(b)) return false
  const ka = Object.keys(a)
  const kb = Object.keys(b)
  if (ka.length !== kb.length) return false
  for (const k of ka) if (!Object.is((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k])) return false
  return true
}

// ------------------------------------------------------------------ helpers

const threadKey = (cid: string, mid: string) => `${cid}|${mid}`
export const draftKey = (cid: string, threadRootId?: string | null) => (threadRootId ? `${cid}|${threadRootId}` : cid)

export function personFor(handle: string | null | undefined): Person | null {
  if (!handle) return null
  return state.people[handle] ?? null
}

export function displayName(handle: string | null | undefined, short = false): string {
  if (!handle || handle === 'me') return state.me ? (short ? state.me.shortName : state.me.name) : 'You'
  const p = state.people[handle]
  if (!p) return handle
  return short ? p.shortName : p.name
}

function indexPeople(convs: Record<string, Conversation>) {
  const people: Record<string, Person> = { ...state.people }
  for (const c of Object.values(convs)) {
    for (const p of c.participants) {
      people[p.handle] = p
      for (const h of p.handles) people[h] = p
    }
  }
  return people
}

function upsertConversation(c: Conversation) {
  setState((s) => {
    const conversations = { ...s.conversations, [c.id]: c }
    return { conversations, people: indexPeople({ [c.id]: c }) }
  })
}

function replaceMessage(list: Message[], m: Message): Message[] {
  const i = list.findIndex((x) => x.id === m.id || (m.clientId && x.id === `local:${m.clientId}`))
  if (i === -1) return [...list, m].sort((a, b) => a.sentAt.localeCompare(b.sentAt))
  const next = list.slice()
  next[i] = m
  return next
}

function patchMessage(cid: string, id: string, patch: Partial<Message>) {
  setState((s) => {
    const ms = s.messages[cid]
    const threads = { ...s.threads }
    for (const [k, t] of Object.entries(threads)) {
      if (!k.startsWith(cid + '|')) continue
      threads[k] = {
        ...t,
        root: t.root && t.root.id === id ? { ...t.root, ...patch } : t.root,
        replies: t.replies.map((r) => (r.id === id ? { ...r, ...patch } : r)),
      }
    }
    return {
      threads,
      messages: ms ? { ...s.messages, [cid]: { ...ms, items: ms.items.map((m) => (m.id === id ? { ...m, ...patch } : m)) } } : s.messages,
    }
  })
}

export function toast(text: string, tone: Toast['tone'] = 'default') {
  const t = { id: uid(), text, tone }
  setState((s) => ({ toasts: [...s.toasts, t] }))
  setTimeout(() => setState((s) => ({ toasts: s.toasts.filter((x) => x.id !== t.id) })), 4200)
}

const isNarrow = () => window.matchMedia('(max-width: 760px)').matches

// ------------------------------------------------------------------ events

let hellos = 0

/** After a dropped connection: reload the sidebar and the open conversation so nothing is missed. */
async function resync() {
  try {
    const list = await api.conversations()
    const convs: Record<string, Conversation> = {}
    for (const c of list) convs[c.id] = c
    const open = state.view?.kind === 'conversation' ? state.view.id : null
    setState({ conversations: convs, people: indexPeople(convs), messages: {}, threads: {}, media: {} })
    if (open) await loadMessages(open)
    const p = state.panel
    if (p?.kind === 'thread') void refreshThread(p.cid, p.mid)
  } catch {
    // the next hello will try again
  }
}

function onEvent(e: ServerEvent) {
  switch (e.type) {
    case 'hello':
      hellos += 1
      setState({ connection: 'open' })
      if (hellos > 1) void resync()
      break
    case 'conversation': {
      upsertConversation(e.conversation)
      if (state.pendingOpen === e.conversation.id) {
        setState({ pendingOpen: null })
        openConversation(e.conversation.id)
      }
      const v = state.view
      if (v?.kind === 'conversation' && v.id === e.conversation.id && e.conversation.unreadCount > 0 && document.visibilityState === 'visible') {
        scheduleMarkRead(e.conversation.id)
      }
      break
    }
    case 'message': {
      const m = e.message
      maybeNotify(m)
      setState((s) => {
        const ms = s.messages[m.conversationId]
        const out: Partial<State> = {}
        if (ms) out.messages = { ...s.messages, [m.conversationId]: { ...ms, items: replaceMessage(ms.items, m) } }
        if (m.threadRootId) {
          const k = threadKey(m.conversationId, m.threadRootId)
          const t = s.threads[k]
          if (t) out.threads = { ...s.threads, [k]: { ...t, replies: replaceMessage(t.replies, m) } }
        }
        return out
      })
      break
    }
    case 'message-updated': {
      const m = e.message
      setState((s) => {
        const ms = s.messages[m.conversationId]
        const out: Partial<State> = {}
        if (ms) out.messages = { ...s.messages, [m.conversationId]: { ...ms, items: ms.items.map((x) => (x.id === m.id ? m : x)) } }
        const threads = { ...s.threads }
        let touched = false
        for (const [k, t] of Object.entries(threads)) {
          if (!k.startsWith(m.conversationId + '|')) continue
          if (t.root?.id === m.id) {
            threads[k] = { ...t, root: m }
            touched = true
          } else if (t.replies.some((r) => r.id === m.id)) {
            threads[k] = { ...t, replies: t.replies.map((r) => (r.id === m.id ? m : r)) }
            touched = true
          }
        }
        if (touched) out.threads = threads
        return out
      })
      break
    }
    case 'send-status':
      if (e.status === 'failed') {
        patchMessage(e.conversationId, `local:${e.clientId}`, { status: 'failed' })
        toast(e.error ?? "Your message didn't send. Messages.app reported an error.", 'error')
      }
      break
    case 'scheduled':
      setState((s) => {
        const others = s.scheduled.filter((x) => x.id !== e.scheduled.id)
        return { scheduled: [...others, e.scheduled].sort((a, b) => a.sendAt.localeCompare(b.sendAt)) }
      })
      break
    case 'later': {
      const item = e.item
      const before = state.later.find((l) => l.messageId === item.messageId)
      setState((s) => ({ later: [item, ...s.later.filter((l) => l.messageId !== item.messageId)] }))
      if (item.firedAt && !before?.firedAt && !item.done && !item.resolvedByReply) {
        const who = item.message ? displayName(item.message.senderHandle) : 'Saved text'
        const text = item.message ? previewOf(item.message) : ''
        toast(`Reminder: ${who}${text ? `: “${text.slice(0, 80)}”` : ''}`)
        if (isNative) {
          postNative({ type: 'notify', title: 'Reminder', subtitle: who, body: text, conversationId: item.conversationId })
          postNative({ type: 'attention' })
        }
      }
      break
    }
  }
}

function previewOf(m: Message) {
  if (m.text) return m.text
  const a = m.attachments[0]
  if (!a) return ''
  return a.kind === 'image' ? 'Photo' : a.kind === 'video' ? 'Video' : a.kind === 'audio' ? 'Audio message' : a.name
}

/** Mac app: a notification for new texts while Backchannel isn't the app in front. */
function maybeNotify(m: Message) {
  if (!isNative || !state.settings.notify || m.fromMe || m.event) return
  if (document.hasFocus()) return
  const c = state.conversations[m.conversationId]
  if (c?.prefs.muted) return
  postNative({
    type: 'notify',
    title: displayName(m.senderHandle),
    subtitle: c && c.kind === 'group' ? c.title : undefined,
    body: previewOf(m) || 'New message',
    conversationId: m.conversationId,
  })
}

let readTimers: Record<string, number> = {}
function scheduleMarkRead(cid: string, delay = 700) {
  window.clearTimeout(readTimers[cid])
  readTimers[cid] = window.setTimeout(async () => {
    try {
      const c = await api.markRead(cid)
      upsertConversation(c)
    } catch {
      // not fatal
    }
  }, delay)
}

// ------------------------------------------------------------------ actions

export async function boot(initial?: string) {
  try {
    const firstHealth = await api.health().catch(() => null)
    if (firstHealth && !firstHealth.chatDb.readable) {
      // Without Full Disk Access nothing else can work; show the setup screen instead.
      setState({ health: firstHealth, setup: 'fda', booted: true })
      return
    }
    const [me, conversations, later, scheduled, activity, health] = await Promise.all([
      api.me(),
      api.conversations(),
      api.later().catch(() => []),
      api.scheduled().catch(() => []),
      api.activity().catch(() => []),
      api.health().catch(() => null),
    ])
    const convs: Record<string, Conversation> = {}
    for (const c of conversations) convs[c.id] = c
    setState({ me, conversations: convs, people: indexPeople(convs), later, scheduled, activity, health, booted: true })
    api.subscribe(onEvent, (st) => setState({ connection: st }))
    const first = initial && convs[initial] ? initial : conversations.find((c) => !c.isAutomated)?.id
    if (isNarrow()) {
      setState({ mobilePane: 'list' })
      if (first) void loadMessages(first)
      if (first) setState({ view: { kind: 'conversation', id: first }, history: [{ kind: 'conversation', id: first }], historyIndex: 0 })
    } else if (first) {
      openConversation(first)
    }
    void loadCustomEmoji()
    void api.serverSettings().then((ss) => setState({ serverSettings: ss })).catch(() => {})
    // Catch up needs Claude on the Mac; without it the view shows a plain fallback.
    if (health?.ai.enabled) void loadCatchUp(false)
  } catch (err) {
    setState({ bootError: err instanceof Error ? err.message : String(err), booted: true })
  }
}

function sameView(a: View | null, b: View | null) {
  if (!a || !b) return a === b
  if (a.kind !== b.kind) return false
  return a.kind !== 'conversation' || (b.kind === 'conversation' && a.id === b.id)
}

function pushView(v: View) {
  setState((s) => {
    if (sameView(s.view, v)) return { view: v }
    const history = [...s.history.slice(0, s.historyIndex + 1), v].slice(-50)
    return { view: v, history, historyIndex: history.length - 1 }
  })
}

export function goBack() {
  const s = state
  if (s.historyIndex <= 0) return
  const v = s.history[s.historyIndex - 1]
  setState({ historyIndex: s.historyIndex - 1 })
  showView(v)
}
export function goForward() {
  const s = state
  if (s.historyIndex >= s.history.length - 1) return
  const v = s.history[s.historyIndex + 1]
  setState({ historyIndex: s.historyIndex + 1 })
  showView(v)
}
function showView(v: View) {
  if (v.kind === 'conversation') enterConversation(v.id)
  setState({ view: v, mobilePane: 'main' })
}

function firstUnreadId(cid: string, items: Message[]) {
  const c = state.conversations[cid]
  if (!c || c.unreadCount <= 0) return null
  const incoming = items.filter((m) => !m.fromMe && !m.event && (state.settings.threadReplies === 'also-inline' || !m.threadRootId))
  const first = incoming[Math.max(0, incoming.length - c.unreadCount)]
  return first?.id ?? null
}

async function loadMessages(cid: string) {
  const existing = state.messages[cid]
  if (existing && (existing.loading || existing.items.length)) return
  setState((s) => ({ messages: { ...s.messages, [cid]: { items: [], nextCursor: null, loading: true, loadingOlder: false, error: null } } }))
  try {
    const page = await api.messages(cid)
    setState((s) => ({
      messages: { ...s.messages, [cid]: { items: page.messages, nextCursor: page.nextCursor, loading: false, loadingOlder: false, error: null } },
      newMarker: { ...s.newMarker, [cid]: s.newMarker[cid] ?? firstUnreadId(cid, page.messages) },
    }))
  } catch (err) {
    setState((s) => ({
      messages: {
        ...s.messages,
        [cid]: { items: [], nextCursor: null, loading: false, loadingOlder: false, error: err instanceof Error ? err.message : String(err) },
      },
    }))
  }
}

function enterConversation(cid: string) {
  const loaded = state.messages[cid]?.items.length
  setState((s) => ({
    newMarker: { ...s.newMarker, [cid]: loaded ? firstUnreadId(cid, s.messages[cid].items) : null },
  }))
  void loadMessages(cid)
  if ((state.conversations[cid]?.unreadCount ?? 0) > 0) scheduleMarkRead(cid, 1200)
}

export function openConversation(cid: string, highlightId?: string) {
  if (!state.conversations[cid]) return
  enterConversation(cid)
  pushView({ kind: 'conversation', id: cid })
  setState((s) => ({
    mobilePane: 'main',
    highlight: highlightId ? { id: highlightId, at: Date.now() } : null,
    panel: s.panel?.kind === 'thread' && s.panel.cid !== cid ? null : s.panel?.kind === 'details' ? null : s.panel,
    search: { ...s.search, open: false },
    switcherOpen: false,
  }))
}

export function openView(kind: 'catchup' | 'threads' | 'scheduled') {
  pushView({ kind })
  setState({ mobilePane: 'main', panel: null })
  if (kind === 'catchup' && !state.catchUp.data) void loadCatchUp(false)
}

export function setRail(tab: RailTab) {
  setState((s) => ({
    rail: tab,
    mobilePane: 'list',
    activitySeenAt: tab === 'activity' ? new Date().toISOString() : s.activitySeenAt,
  }))
}

export async function loadOlder(cid: string) {
  const ms = state.messages[cid]
  if (!ms || !ms.nextCursor || ms.loadingOlder) return
  setState((s) => ({ messages: { ...s.messages, [cid]: { ...ms, loadingOlder: true } } }))
  try {
    const page = await api.messages(cid, ms.nextCursor)
    setState((s) => {
      const cur = s.messages[cid]
      const ids = new Set(cur.items.map((m) => m.id))
      return {
        messages: {
          ...s.messages,
          [cid]: { ...cur, items: [...page.messages.filter((m) => !ids.has(m.id)), ...cur.items], nextCursor: page.nextCursor, loadingOlder: false },
        },
      }
    })
  } catch {
    setState((s) => ({ messages: { ...s.messages, [cid]: { ...s.messages[cid], loadingOlder: false } } }))
  }
}

export async function openThread(cid: string, mid: string) {
  setState({ panel: { kind: 'thread', cid, mid }, mobilePane: 'panel' })
  const k = threadKey(cid, mid)
  if (state.threads[k]?.root) {
    void refreshThread(cid, mid)
    return
  }
  setState((s) => ({ threads: { ...s.threads, [k]: { root: null, replies: [], loading: true, error: null } } }))
  await refreshThread(cid, mid)
}

async function refreshThread(cid: string, mid: string) {
  const k = threadKey(cid, mid)
  try {
    const t = await api.thread(cid, mid)
    setState((s) => ({ threads: { ...s.threads, [k]: { root: t.root, replies: t.replies, loading: false, error: null } } }))
  } catch (err) {
    setState((s) => ({
      threads: { ...s.threads, [k]: { root: null, replies: [], loading: false, error: err instanceof Error ? err.message : String(err) } },
    }))
  }
}

export function useThread(cid: string, mid: string) {
  return useStore((s) => s.threads[threadKey(cid, mid)])
}

export function openProfile(handle: string, cid: string | null = null) {
  setState({ panel: { kind: 'profile', handle, cid }, mobilePane: 'panel' })
}
export function openDetails(cid: string) {
  setState({ panel: { kind: 'details', cid }, mobilePane: 'panel' })
  void loadMedia(cid)
}
export function closePanel() {
  setState((s) => ({ panel: null, mobilePane: s.view ? 'main' : 'list' }))
}

export async function loadMedia(cid: string) {
  if (state.media[cid]) return
  try {
    const page = await api.media(cid)
    setState((s) => ({ media: { ...s.media, [cid]: page.items } }))
  } catch {
    setState((s) => ({ media: { ...s.media, [cid]: [] } }))
  }
}

export function setDraft(key: string, text: string) {
  setState((s) => {
    const drafts = { ...s.drafts }
    if (text) drafts[key] = text
    else delete drafts[key]
    return { drafts }
  })
}

export async function sendMessage(cid: string, text: string, opts: { threadRootId?: string | null; sendAt?: string | null } = {}) {
  const body = text.trim()
  if (!body) return
  const clientId = uid()
  const c = state.conversations[cid]
  const scheduledFor = opts.sendAt && new Date(opts.sendAt).getTime() > Date.now() + 30000 ? opts.sendAt : null
  if (!scheduledFor) {
    const optimistic: Message = {
      id: `local:${clientId}`,
      conversationId: cid,
      senderHandle: null,
      fromMe: true,
      text: body,
      sentAt: new Date().toISOString(),
      service: c?.service ?? 'iMessage',
      attachments: [],
      reactions: [],
      linkPreview: null,
      threadRootId: opts.threadRootId ?? null,
      thread: null,
      editedAt: null,
      unsent: false,
      status: 'sending',
      readAt: null,
      mentionsMe: false,
      effect: null,
      isAudioMessage: false,
      otpCode: null,
      event: null,
      clientId,
    }
    setState((s) => {
      const ms = s.messages[cid]
      const out: Partial<State> = {}
      if (ms) out.messages = { ...s.messages, [cid]: { ...ms, items: [...ms.items, optimistic] } }
      if (opts.threadRootId) {
        const k = threadKey(cid, opts.threadRootId)
        const t = s.threads[k]
        if (t) out.threads = { ...s.threads, [k]: { ...t, replies: [...t.replies, optimistic] } }
      }
      out.newMarker = { ...s.newMarker, [cid]: null }
      return out
    })
  }
  setDraft(draftKey(cid, opts.threadRootId), '')
  setState((s) => {
    const aiDrafts = { ...s.aiDrafts }
    delete aiDrafts[draftKey(cid, opts.threadRootId)]
    return { aiDrafts }
  })
  try {
    const res = await api.send(cid, { text: body, clientId, threadRootId: opts.threadRootId ?? null, sendAt: scheduledFor })
    if (res.status === 'scheduled' && scheduledFor) {
      toast(`Scheduled for ${whenLabel(scheduledFor)}`)
      if (!state.scheduled.some((x) => x.id === res.scheduledId)) {
        setState((s) => ({
          scheduled: [
            ...s.scheduled,
            {
              id: res.scheduledId ?? clientId,
              conversationId: cid,
              text: body,
              sendAt: scheduledFor,
              createdAt: new Date().toISOString(),
              status: 'pending' as const,
              error: null,
            },
          ].sort((a, b) => a.sendAt.localeCompare(b.sendAt)),
        }))
      }
    }
  } catch (err) {
    patchMessage(cid, `local:${clientId}`, { status: 'failed' })
    toast(err instanceof Error ? err.message : "Your message didn't send.", 'error')
  }
}

export async function retrySend(cid: string, localId: string) {
  const m = state.messages[cid]?.items.find((x) => x.id === localId)
  if (!m) return
  setState((s) => ({
    messages: { ...s.messages, [cid]: { ...s.messages[cid], items: s.messages[cid].items.filter((x) => x.id !== localId) } },
  }))
  const file = m.clientId ? pendingFiles.get(m.clientId) : undefined
  if (file) {
    pendingFiles.delete(m.clientId!)
    await sendFiles(cid, [file])
  } else {
    await sendMessage(cid, m.text, { threadRootId: m.threadRootId })
  }
}

/** Files waiting to be confirmed, kept so a failed send can be retried. */
const pendingFiles = new Map<string, File>()

export function attachmentKind(type: string): 'image' | 'video' | 'audio' | 'file' {
  return type.startsWith('image/') ? 'image' : type.startsWith('video/') ? 'video' : type.startsWith('audio/') ? 'audio' : 'file'
}

/** Send photos or files. Each shows up right away and gets swapped for the real message once Messages sends it. */
export async function sendFiles(cid: string, files: File[]) {
  const c = state.conversations[cid]
  for (const file of files) {
    const clientId = uid()
    const url = URL.createObjectURL(file)
    const kind = attachmentKind(file.type)
    pendingFiles.set(clientId, file)
    const optimistic: Message = {
      id: `local:${clientId}`,
      conversationId: cid,
      senderHandle: null,
      fromMe: true,
      text: '',
      sentAt: new Date().toISOString(),
      service: c?.service ?? 'iMessage',
      attachments: [
        {
          id: `local-${clientId}`,
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
      status: 'sending',
      readAt: null,
      mentionsMe: false,
      effect: null,
      isAudioMessage: false,
      otpCode: null,
      event: null,
      clientId,
    }
    setState((s) => {
      const ms = s.messages[cid]
      return ms ? { messages: { ...s.messages, [cid]: { ...ms, items: [...ms.items, optimistic] } } } : {}
    })
    try {
      await api.sendFile(cid, file, clientId)
    } catch (err) {
      patchMessage(cid, `local:${clientId}`, { status: 'failed' })
      toast(err instanceof Error ? err.message : `Couldn't send ${file.name}.`, 'error')
    }
  }
}

/** Text someone new (a phone number or email). Opens the conversation once Messages creates it. */
export async function startConversation(handle: string, text: string) {
  const clientId = uid()
  try {
    const { conversationId } = await api.startConversation(handle, text, clientId)
    if (state.conversations[conversationId]) {
      openConversation(conversationId)
      return
    }
    setState({ pendingOpen: conversationId })
    toast('Sent. The conversation opens as soon as Messages creates it.')
    // In case the update arrives before we start waiting, check again shortly.
    setTimeout(async () => {
      if (state.pendingOpen !== conversationId) return
      try {
        const list = await api.conversations()
        const found = list.find((c) => c.id === conversationId)
        if (found) {
          upsertConversation(found)
          setState({ pendingOpen: null })
          openConversation(conversationId)
        }
      } catch {
        // the live update will still open it
      }
    }, 4000)
  } catch (err) {
    toast(err instanceof Error ? err.message : "Couldn't start that conversation.", 'error')
  }
}

export async function requestDrafts(cid: string, threadRootId?: string | null) {
  const k = draftKey(cid, threadRootId)
  setState((s) => ({ aiDrafts: { ...s.aiDrafts, [k]: { loading: true, options: [], error: null } } }))
  try {
    const options = await api.draftReply(cid, threadRootId)
    setState((s) => ({ aiDrafts: { ...s.aiDrafts, [k]: { loading: false, options, error: null } } }))
  } catch (err) {
    setState((s) => ({
      aiDrafts: { ...s.aiDrafts, [k]: { loading: false, options: [], error: err instanceof Error ? err.message : String(err) } },
    }))
  }
}
export function clearDrafts(k: string) {
  setState((s) => {
    const aiDrafts = { ...s.aiDrafts }
    delete aiDrafts[k]
    return { aiDrafts }
  })
}

export async function setPrefs(cid: string, prefs: Partial<ConversationPrefs>) {
  const c = state.conversations[cid]
  if (c) upsertConversation({ ...c, prefs: { ...c.prefs, ...prefs } })
  try {
    upsertConversation(await api.setPrefs(cid, prefs))
  } catch (err) {
    if (c) upsertConversation(c)
    toast(err instanceof Error ? err.message : "Couldn't save that change.", 'error')
  }
}

export function toggleStar(cid: string) {
  const c = state.conversations[cid]
  if (!c) return
  void setPrefs(cid, { starred: !c.prefs.starred })
  toast(c.prefs.starred ? `Removed ${c.title} from Starred` : `Starred ${c.title}`)
}
export function toggleMute(cid: string) {
  const c = state.conversations[cid]
  if (!c) return
  void setPrefs(cid, { muted: !c.prefs.muted })
  toast(c.prefs.muted ? `Unmuted ${c.title}` : `Muted ${c.title}. You won't get notifications for it.`)
}
export function snooze(cid: string, until: string) {
  const c = state.conversations[cid]
  if (!c) return
  void setPrefs(cid, { snoozedUntil: until })
  toast(`Snoozed ${c.title} until ${whenLabel(until)}`)
}

export async function saveForLater(cid: string, mid: string, remindAt: string | null) {
  try {
    const item = await api.saveForLater(cid, mid, remindAt)
    setState((s) => ({ later: [item, ...s.later.filter((l) => l.messageId !== mid)] }))
    toast(remindAt ? `Saved. We'll remind you ${whenLabel(remindAt)}.` : 'Saved for later')
  } catch (err) {
    toast(err instanceof Error ? err.message : "Couldn't save that message.", 'error')
  }
}
export async function setLaterDone(mid: string, done: boolean) {
  setState((s) => ({ later: s.later.map((l) => (l.messageId === mid ? { ...l, done } : l)) }))
  try {
    const item = await api.setLaterDone(mid, done)
    setState((s) => ({ later: s.later.map((l) => (l.messageId === mid ? item : l)) }))
  } catch (err) {
    toast(err instanceof Error ? err.message : "Couldn't update that item.", 'error')
  }
}

export async function remindIfNoReply(cid: string, mid: string, at: string) {
  try {
    const item = await api.saveForLater(cid, mid, at, true)
    setState((s) => ({ later: [item, ...s.later.filter((l) => l.messageId !== mid)] }))
    toast(`If they don't reply by ${whenLabel(at)}, you'll get a nudge.`)
  } catch (err) {
    toast(err instanceof Error ? err.message : "Couldn't set that reminder.", 'error')
  }
}

export async function removeLater(mid: string) {
  setState((s) => ({ later: s.later.filter((l) => l.messageId !== mid) }))
  try {
    await api.removeLater(mid)
  } catch {
    // already removed locally
  }
}

export async function cancelScheduled(id: string) {
  setState((s) => ({ scheduled: s.scheduled.map((x) => (x.id === id ? { ...x, status: 'cancelled' as const } : x)) }))
  try {
    await api.cancelScheduled(id)
    toast('Scheduled message cancelled')
  } catch (err) {
    toast(err instanceof Error ? err.message : "Couldn't cancel that message.", 'error')
  }
}

export async function loadCatchUp(refresh: boolean) {
  setState((s) => ({ catchUp: { ...s.catchUp, loading: true, error: null } }))
  try {
    const data = await api.catchUp(refresh)
    setState((s) => ({ catchUp: { ...s.catchUp, data, loading: false, done: refresh ? [] : s.catchUp.done } }))
  } catch (err) {
    setState((s) => ({ catchUp: { ...s.catchUp, loading: false, error: err instanceof Error ? err.message : String(err) } }))
  }
}
export function markCatchUpDone(key: string) {
  setState((s) => ({ catchUp: { ...s.catchUp, done: [...s.catchUp.done, key] } }))
}

let searchSeq = 0
export async function runSearch(q: string, scope: string | null = state.search.scope) {
  const my = ++searchSeq
  setState((s) => ({ search: { ...s.search, q, scope, loading: !!q.trim() } }))
  if (!q.trim()) {
    setState((s) => ({ search: { ...s.search, results: [], loading: false } }))
    return
  }
  try {
    const results = await api.search(q, scope)
    if (my === searchSeq) setState((s) => ({ search: { ...s.search, results, loading: false } }))
  } catch {
    if (my === searchSeq) setState((s) => ({ search: { ...s.search, results: [], loading: false } }))
  }
}
export function openSearch(scope: string | null = null) {
  setState((s) => ({ search: { ...s.search, open: true, scope } }))
  if (state.search.q) void runSearch(state.search.q, scope)
}
export function closeSearch() {
  setState((s) => ({ search: { ...s.search, open: false } }))
}

export function chiefAvailable() {
  return isMock || !!state.health?.chief?.available
}

export async function loadChiefCard(handle: string, force = false) {
  if (!chiefAvailable()) return
  const cur = state.chiefCards[handle]
  if (!force && (cur === 'loading' || cur !== undefined)) return
  setState((s) => ({ chiefCards: { ...s.chiefCards, [handle]: 'loading' } }))
  try {
    const card = await api.chiefCard(handle)
    setState((s) => ({ chiefCards: { ...s.chiefCards, [handle]: card } }))
  } catch {
    setState((s) => ({ chiefCards: { ...s.chiefCards, [handle]: null } }))
  }
}

export function openChiefDraft(m: Message) {
  const who = m.fromMe ? null : displayName(m.senderHandle, true)
  const text = previewOf(m).replace(/\s+/g, ' ').trim()
  const short = text.length > 90 ? text.slice(0, 89) + '…' : text
  const title = who ? `Reply to ${who}: ${short}` : `Follow up: ${short}`
  setState({ chiefDraft: { cid: m.conversationId, mid: m.id, title } })
}

export async function addChiefLoop(title: string) {
  const d = state.chiefDraft
  if (!d || !title.trim()) return
  setState({ chiefDraft: null })
  try {
    const loop = await api.addChiefLoop(d.cid, d.mid, title.trim())
    toast(`Added to Chief as #${loop.id}`)
    const c = state.conversations[d.cid]
    if (c?.kind === 'dm' && c.participants[0]) void loadChiefCard(c.participants[0].handle, true)
  } catch (err) {
    toast(err instanceof Error ? err.message : "Couldn't add that to Chief.", 'error')
  }
}

export function setSettings(patch: Partial<Settings>) {
  if (patch.notify && !state.settings.notify && isNative) postNative({ type: 'requestNotifications' })
  setState((s) => ({ settings: { ...s.settings, ...patch } }))
}
export function toggleSection(key: string) {
  setState((s) => ({ collapsed: { ...s.collapsed, [key]: !s.collapsed[key] } }))
}

export function moveConversation(delta: 1 | -1, unreadOnly = false, order: string[]) {
  const v = state.view
  const cur = v?.kind === 'conversation' ? order.indexOf(v.id) : -1
  for (let i = 1; i <= order.length; i++) {
    const idx = (cur + delta * i + order.length) % order.length
    const c = state.conversations[order[idx]]
    if (!c) continue
    if (unreadOnly && c.unreadCount === 0) continue
    openConversation(c.id)
    return
  }
}

export { isNarrow }

// ------------------------------------------------------------------ custom emoji and stickers

export async function loadCustomEmoji() {
  try {
    const items = await api.customEmoji()
    setState({ customEmoji: items.sort((a, b) => a.name.localeCompare(b.name)) })
  } catch {
    // the picker just shows none
  }
}

export async function loadStickers(force = false) {
  if (state.stickers && !force) return
  try {
    setState({ stickers: await api.stickers() })
  } catch {
    setState({ stickers: [] })
  }
}

export async function addCustomEmoji(name: string, source: { file: File } | { url: string }) {
  const item = await api.addCustomEmoji(name, source)
  setState((s) => ({ customEmoji: [...s.customEmoji.filter((e) => e.name !== item.name), item].sort((a, b) => a.name.localeCompare(b.name)) }))
  toast(`Added :${item.name}:`)
  return item
}

export async function saveStickerAsEmoji(stickerId: string, name: string) {
  const item = await api.saveStickerAsEmoji(stickerId, name)
  setState((s) => ({ customEmoji: [...s.customEmoji.filter((e) => e.name !== item.name), item].sort((a, b) => a.name.localeCompare(b.name)) }))
  toast(`Saved as :${item.name}:`)
  return item
}

export async function removeCustomEmoji(name: string) {
  setState((s) => ({ customEmoji: s.customEmoji.filter((e) => e.name !== name) }))
  try {
    await api.removeCustomEmoji(name)
  } catch (err) {
    toast(err instanceof Error ? err.message : "Couldn't remove that emoji.", 'error')
    void loadCustomEmoji()
  }
}

/** data: URLs are decoded here (fetching them can be blocked); everything else is fetched with our session. */
async function urlToBlob(url: string): Promise<Blob> {
  if (url.startsWith('data:')) {
    const comma = url.indexOf(',')
    const meta = url.slice(5, comma)
    const body = url.slice(comma + 1)
    const type = meta.split(';')[0] || 'application/octet-stream'
    if (meta.includes(';base64')) {
      const bin = atob(body)
      const bytes = new Uint8Array(bin.length)
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
      return new Blob([bytes], { type })
    }
    return new Blob([decodeURIComponent(body)], { type })
  }
  const res = await fetch(url, { credentials: 'same-origin' })
  if (!res.ok) throw new Error(`Couldn't load that image (${res.status}).`)
  return res.blob()
}

const EXT: Record<string, string> = { 'image/png': 'png', 'image/gif': 'gif', 'image/jpeg': 'jpg', 'image/webp': 'webp', 'image/svg+xml': 'svg', 'image/heic': 'heic' }

/** Put an emoji or sticker in the composer as an attachment; iMessage carries it as a picture. */
export async function stageImageForSend(cid: string, url: string, baseName: string) {
  try {
    const blob = await urlToBlob(url)
    const ext = EXT[blob.type] ?? 'png'
    const safe = baseName.replace(/[^a-z0-9 _+-]+/gi, '').trim().slice(0, 60) || 'sticker'
    const file = new File([blob], `${safe}.${ext}`, { type: blob.type || 'image/png' })
    window.dispatchEvent(new CustomEvent('bc:attach', { detail: { cid, files: [file] } }))
  } catch (err) {
    toast(err instanceof Error ? err.message : "Couldn't add that.", 'error')
  }
}

// ------------------------------------------------------------------ server-side preferences

export async function refreshHealth() {
  try {
    setState({ health: await api.health() })
  } catch {
    // keep the last one
  }
}

export async function setServerSetting(patch: Partial<ServerSettings>) {
  // In the Mac app, turning on "mark read in Messages" asks macOS for Accessibility right away.
  if (patch.markReadInMessages && isNative) {
    window.__bcAccessibility = (trusted) => {
      if (!trusted) toast('Turn on Backchannel in System Settings › Privacy & Security › Accessibility, then try again.')
    }
    postNative({ type: 'requestAccessibility' })
  }
  const before = state.serverSettings
  if (before) setState({ serverSettings: { ...before, ...patch } })
  try {
    setState({ serverSettings: await api.setServerSettings(patch) })
    void refreshHealth()
  } catch (err) {
    setState({ serverSettings: before })
    toast(err instanceof Error ? err.message : "Couldn't save that setting.", 'error')
  }
}
