// Api implementation that talks to the Python server on the Mac.
import type {
  ActivityItem,
  Api,
  CatchUp,
  ChiefCard,
  CustomEmoji,
  Conversation,
  ConversationPrefs,
  Health,
  LaterItem,
  MediaPage,
  Me,
  Message,
  MessagePage,
  ScheduledMessage,
  SearchResult,
  SendRequest,
  SendResponse,
  ServerEvent,
  ServerSettings,
  StickerItem,
  ThreadResponse,
} from './types'

export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

const enc = encodeURIComponent

const refreshSession = () => fetch('/api/session', { credentials: 'same-origin' }).catch(() => null)

async function request<T>(method: string, path: string, body?: unknown, retry = true): Promise<T> {
  const res = await fetch(path, {
    method,
    credentials: 'same-origin',
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  // The session cookie is per server run. After a restart, get a new one and try once more.
  if (res.status === 401 && retry) {
    await refreshSession()
    return request<T>(method, path, body, false)
  }
  if (res.status === 204) return undefined as T
  let data: unknown = null
  try {
    data = await res.json()
  } catch {
    // fall through to the status check
  }
  if (!res.ok) {
    const msg =
      (data && typeof data === 'object' && 'error' in data && typeof (data as { error: unknown }).error === 'string'
        ? (data as { error: string }).error
        : null) ?? `The Backchannel server answered ${res.status}.`
    throw new ApiError(msg, res.status)
  }
  return data as T
}

export function createHttpApi(): Api {
  // Sets the session cookie. Every other route needs it.
  const ready = request<{ ok: boolean }>('GET', '/api/session').catch(() => ({ ok: false }))
  const get = async <T,>(path: string) => {
    await ready
    return request<T>('GET', path)
  }
  const send = async <T,>(method: string, path: string, body?: unknown) => {
    await ready
    return request<T>(method, path, body)
  }

  return {
    health: () => get<Health>('/api/health'),
    me: () => get<Me>('/api/me'),
    conversations: async () => (await get<{ conversations: Conversation[] }>('/api/conversations')).conversations,
    messages: (cid, before) =>
      get<MessagePage>(`/api/conversations/${enc(cid)}/messages?limit=80${before ? `&before=${enc(before)}` : ''}`),
    thread: (cid, mid) => get<ThreadResponse>(`/api/conversations/${enc(cid)}/threads/${enc(mid)}`),
    media: (cid, before) => get<MediaPage>(`/api/conversations/${enc(cid)}/media?limit=60${before ? `&before=${enc(before)}` : ''}`),
    send: (cid, req: SendRequest) => send<SendResponse>('POST', `/api/conversations/${enc(cid)}/messages`, req),
    sendFile: async (cid, file, clientId) => {
      await ready
      const form = new FormData()
      form.append('file', file, file.name)
      form.append('clientId', clientId)
      const post = () => fetch(`/api/conversations/${enc(cid)}/files`, { method: 'POST', credentials: 'same-origin', body: form })
      let res = await post()
      if (res.status === 401) {
        await refreshSession()
        res = await post()
      }
      const data = await res.json().catch(() => null)
      if (!res.ok) throw new ApiError((data && data.error) || `Sending the file failed (${res.status}).`, res.status)
      return data as SendResponse
    },
    startConversation: (handle, text, clientId) =>
      send<{ conversationId: string }>('POST', '/api/conversations', { handle, text, clientId }),
    markRead: (cid) => send<Conversation>('POST', `/api/conversations/${enc(cid)}/read`, {}),
    markUnread: (cid, messageId) =>
      send<Conversation>('POST', `/api/conversations/${enc(cid)}/unread`, messageId ? { messageId } : {}),
    setPrefs: (cid, prefs: Partial<ConversationPrefs>) => send<Conversation>('PATCH', `/api/conversations/${enc(cid)}/prefs`, prefs),
    later: async () => (await get<{ items: LaterItem[] }>('/api/later')).items,
    saveForLater: (cid, mid, remindAt, ifNoReply) =>
      send<LaterItem>('PUT', `/api/later/${enc(mid)}`, { conversationId: cid, remindAt, ifNoReply: !!ifNoReply }),
    setLaterDone: async (mid, done) => {
      const current = (await get<{ items: LaterItem[] }>('/api/later')).items.find((l) => l.messageId === mid)
      return send<LaterItem>('PUT', `/api/later/${enc(mid)}`, {
        conversationId: current?.conversationId,
        remindAt: current?.remindAt ?? null,
        ifNoReply: current?.ifNoReply ?? false,
        done,
      })
    },
    removeLater: (mid) => send<void>('DELETE', `/api/later/${enc(mid)}`),
    scheduled: async () => (await get<{ items: ScheduledMessage[] }>('/api/scheduled')).items,
    cancelScheduled: (id) => send<void>('DELETE', `/api/scheduled/${enc(id)}`),
    activity: async () => (await get<{ items: ActivityItem[] }>('/api/activity?limit=50')).items,
    threads: async () => (await get<{ items: Message[] }>('/api/threads?limit=40')).items,
    search: async (q, cid) =>
      (await get<{ results: SearchResult[] }>(`/api/search?limit=40&q=${enc(q)}${cid ? `&conversationId=${enc(cid)}` : ''}`)).results,
    draftReply: async (cid, threadRootId, hint) =>
      (await send<{ drafts: string[] }>('POST', '/api/ai/draft', { conversationId: cid, threadRootId: threadRootId ?? null, hint: hint ?? null }))
        .drafts,
    catchUp: (refresh) => get<CatchUp>(`/api/ai/catchup?refresh=${refresh ? 1 : 0}`),
    chiefCard: (handle) => get<ChiefCard | null>(`/api/people/${enc(handle)}/chief`),
    addChiefLoop: (cid, mid, title) => send<{ id: number; title: string }>('POST', '/api/chief/loops', { conversationId: cid, messageId: mid, title }),
    sendTapback: (cid, mid, kind, remove) =>
      send<{ status: 'queued' }>('POST', `/api/conversations/${enc(cid)}/messages/${enc(mid)}/tapback`, { kind, remove }),
    serverSettings: () => get<ServerSettings>('/api/settings'),
    setServerSettings: (patch) => send<ServerSettings>('PATCH', '/api/settings', patch),
    customEmoji: async () => (await get<{ items: CustomEmoji[] }>('/api/emoji')).items,
    addCustomEmoji: async (name, source) => {
      await ready
      if ('url' in source) return request<CustomEmoji>('POST', '/api/emoji/link', { name, url: source.url })
      const form = new FormData()
      form.append('name', name)
      form.append('file', source.file, source.file.name)
      const post = () => fetch('/api/emoji', { method: 'POST', credentials: 'same-origin', body: form })
      let res = await post()
      if (res.status === 401) {
        await refreshSession()
        res = await post()
      }
      const data = await res.json().catch(() => null)
      if (!res.ok) throw new ApiError((data && data.error) || `Adding the emoji failed (${res.status}).`, res.status)
      return data as CustomEmoji
    },
    removeCustomEmoji: (name) => send<void>('DELETE', `/api/emoji/${enc(name)}`),
    stickers: async () => (await get<{ items: StickerItem[] }>('/api/stickers?limit=300')).items,
    saveStickerAsEmoji: (id, name) => send<CustomEmoji>('POST', `/api/stickers/${enc(id)}/save`, { name }),
    subscribe(onEvent, onStatus) {
      let es: EventSource | null = null
      let closed = false
      let timer = 0
      // EventSource retries dropped connections by itself, but gives up for good on an
      // HTTP error (e.g. 401 after the server restarted). Then: new session, new stream.
      const connect = async () => {
        if (closed) return
        await refreshSession()
        if (closed) return
        es = new EventSource('/api/events', { withCredentials: true })
        es.onopen = () => onStatus?.('open')
        es.onerror = () => {
          onStatus?.('reconnecting')
          if (es?.readyState === EventSource.CLOSED) {
            es = null
            timer = window.setTimeout(() => void connect(), 3000)
          }
        }
        es.onmessage = (ev) => {
          try {
            onEvent(JSON.parse(ev.data) as ServerEvent)
          } catch {
            // ignore malformed lines
          }
        }
      }
      void ready.then(connect)
      return () => {
        closed = true
        window.clearTimeout(timer)
        es?.close()
      }
    },
  }
}
