// Small formatting helpers: times, names, phone numbers, text segments.

export const toDate = (iso: string) => new Date(iso)

export function isSameDay(a: Date, b: Date) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate()
}

const timeFmt = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' })
const weekdayFmt = new Intl.DateTimeFormat(undefined, { weekday: 'long' })
const monthDayFmt = new Intl.DateTimeFormat(undefined, { month: 'long', day: 'numeric' })
const shortMonthDayFmt = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' })
const fullFmt = new Intl.DateTimeFormat(undefined, {
  weekday: 'long',
  month: 'long',
  day: 'numeric',
  year: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
})

export function timeShort(iso: string) {
  return timeFmt.format(toDate(iso))
}

export function fullTimestamp(iso: string) {
  return fullFmt.format(toDate(iso))
}

function ordinal(n: number) {
  const s = ['th', 'st', 'nd', 'rd']
  const v = n % 100
  return n + (s[(v - 20) % 10] || s[v] || s[0])
}

/** Slack-style day divider label: Today, Yesterday, Monday, October 26th. */
export function dayLabel(iso: string, now = new Date()) {
  const d = toDate(iso)
  if (isSameDay(d, now)) return 'Today'
  const y = new Date(now)
  y.setDate(now.getDate() - 1)
  if (isSameDay(d, y)) return 'Yesterday'
  const month = new Intl.DateTimeFormat(undefined, { month: 'long' }).format(d)
  const base = `${weekdayFmt.format(d)}, ${month} ${ordinal(d.getDate())}`
  return d.getFullYear() === now.getFullYear() ? base : `${base}, ${d.getFullYear()}`
}

/** Compact age for lists: now, 5m, 2h, Yesterday, Tue, Oct 2. */
export function ageShort(iso: string, now = new Date()) {
  const d = toDate(iso)
  const mins = Math.round((now.getTime() - d.getTime()) / 60000)
  if (mins < 1) return 'now'
  if (mins < 60) return `${mins}m`
  const hours = Math.floor(mins / 60)
  if (hours < 24 && isSameDay(d, now)) return `${hours}h`
  const y = new Date(now)
  y.setDate(now.getDate() - 1)
  if (isSameDay(d, y)) return 'Yesterday'
  const days = Math.floor(hours / 24)
  if (days < 6) return new Intl.DateTimeFormat(undefined, { weekday: 'short' }).format(d)
  return shortMonthDayFmt.format(d)
}

/** "5 minutes ago", "3 hours ago", "2 days ago". */
export function ageLong(iso: string, now = new Date()) {
  const mins = Math.round((now.getTime() - toDate(iso).getTime()) / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins} minute${mins === 1 ? '' : 's'} ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`
  const days = Math.floor(hours / 24)
  return `${days} day${days === 1 ? '' : 's'} ago`
}

/** "today at 2:14 PM", "yesterday at 9:02 AM", "Oct 2 at 9:02 AM". */
export function whenLabel(iso: string, now = new Date()) {
  const d = toDate(iso)
  const t = timeShort(iso)
  if (isSameDay(d, now)) return `today at ${t}`
  const tm = new Date(now)
  tm.setDate(now.getDate() + 1)
  if (isSameDay(d, tm)) return `tomorrow at ${t}`
  const y = new Date(now)
  y.setDate(now.getDate() - 1)
  if (isSameDay(d, y)) return `yesterday at ${t}`
  const diffDays = Math.abs(d.getTime() - now.getTime()) / 86400000
  if (diffDays < 6) return `${weekdayFmt.format(d)} at ${t}`
  return `${monthDayFmt.format(d)} at ${t}`
}

/** +14155550134 → (415) 555-0134. Short codes and emails pass through. */
export function formatHandle(handle: string) {
  if (handle.includes('@')) return handle
  const digits = handle.replace(/\D/g, '')
  if (digits.length === 11 && digits.startsWith('1')) {
    return `(${digits.slice(1, 4)}) ${digits.slice(4, 7)}-${digits.slice(7)}`
  }
  if (digits.length === 10) return `(${digits.slice(0, 3)}) ${digits.slice(3, 6)}-${digits.slice(6)}`
  return handle
}

export function initials(name: string) {
  const clean = name.replace(/[^\p{L}\p{N}\s]/gu, ' ').trim()
  if (!clean) return '#'
  if (/^[\d\s()+-]+$/.test(name)) return '#'
  const parts = clean.split(/\s+/)
  const first = parts[0]?.[0] ?? ''
  const last = parts.length > 1 ? parts[parts.length - 1][0] : ''
  return (first + last).toUpperCase()
}

/** Stable index into the avatar palette for a name or handle. */
export function hashIndex(seed: string, buckets: number) {
  let h = 0
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) >>> 0
  return h % buckets
}

export type Segment =
  | { kind: 'text'; value: string }
  | { kind: 'link'; value: string; href: string }
  | { kind: 'mention'; value: string; me: boolean }

const URL_RE = /\b(https?:\/\/[^\s<>"')\]]+[^\s<>"')\].,!?;:])/gi

/** Split text into plain text, links, and mentions of the given names. */
export function segmentText(text: string, mentionNames: string[], myNames: string[]): Segment[] {
  const out: Segment[] = []
  const names = [...new Set([...myNames, ...mentionNames])].filter((n) => n.length > 1)
  const escaped = names.map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).sort((a, b) => b.length - a.length)
  const mentionRe = escaped.length ? new RegExp(`@?(?:${escaped.join('|')})\\b`, 'g') : null

  const pushText = (chunk: string) => {
    if (!chunk) return
    if (!mentionRe) {
      out.push({ kind: 'text', value: chunk })
      return
    }
    let last = 0
    for (const m of chunk.matchAll(mentionRe)) {
      const raw = m[0]
      const isAt = raw.startsWith('@')
      const bare = isAt ? raw.slice(1) : raw
      const me = myNames.includes(bare)
      // Only treat a bare name as a mention when it's the user's own name at the start
      // (iMessage mentions drop the @). Other names need an explicit @.
      if (!isAt && !(me && (m.index ?? 0) === 0)) continue
      const idx = m.index ?? 0
      if (idx > last) out.push({ kind: 'text', value: chunk.slice(last, idx) })
      out.push({ kind: 'mention', value: raw, me })
      last = idx + raw.length
    }
    if (last < chunk.length) out.push({ kind: 'text', value: chunk.slice(last) })
  }

  let last = 0
  for (const m of text.matchAll(URL_RE)) {
    const idx = m.index ?? 0
    pushText(text.slice(last, idx))
    out.push({ kind: 'link', value: m[0].replace(/^https?:\/\//, ''), href: m[0] })
    last = idx + m[0].length
  }
  pushText(text.slice(last))
  return out
}

const EMOJI_ONLY_RE = /^(?:\p{Extended_Pictographic}|\p{Emoji_Component}|‍|️|\s)+$/u

export function isEmojiOnly(text: string) {
  const t = text.trim()
  if (!t || t.length > 24) return false
  if (/^[\d#*\s]+$/.test(t)) return false
  return EMOJI_ONLY_RE.test(t)
}

export function fileSize(bytes: number | null) {
  if (bytes == null) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function uid() {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID()
  return 'id-' + Math.random().toString(36).slice(2) + Date.now().toString(36)
}

export function plural(n: number, one: string, many = one + 's') {
  return `${n} ${n === 1 ? one : many}`
}
