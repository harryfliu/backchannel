// "Needs you" for the mock: a compact version of the server's rules (docs/api.md, "Needs you").
import type { Conversation, Message, Priority, PriorityReason } from '../types'

/** Asks something of the user: a "?" or a request phrase (the "Waiting on you" rule). */
export const ASK_RE = /\?|\b(can you|could you|would you|will you|are you|do you|did you|have you|lmk|let me know|call me|text me|send me|wanna|want to|down to|thoughts|when you get a chance)\b/i

const HOUR = 3600000
const DAY = 24 * HOUR
// "call me" alone is just a request ("call me when you get a chance"); it's urgent next to now/asap/…
const URGENT_RE =
  /\b(urgent|asap|a\.s\.a\.p|emergency|911|hospital|accident|call asap|answer your phone|need you now|pick up (?:the|your) phone)\b|\bhelp!|\bpick up!|\bcall me\b(?=.*\b(?:asap|now|right away|immediately|urgent|emergency|when you see this)\b)|\b(?:asap|now|right away|immediately|urgent|emergency|when you see this)\b(?=.*\bcall me\b)/i
const WEEKDAYS = ['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday']
const ORDER: PriorityReason['kind'][] = ['urgent', 'repeat', 'time', 'asked', 'mention', 'reply', 'vip']
const WEIGHT: Record<PriorityReason['kind'], number> = { urgent: 80, repeat: 40, time: 30, asked: 60, mention: 50, reply: 40, vip: 25 }

interface TimeHit {
  label: string
  at: Date | null
  end: Date
}

const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate())
const atHour = (d: Date, h: number, m = 0) => new Date(d.getFullYear(), d.getMonth(), d.getDate(), h, m)
const endOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate(), 23, 59)
const clock = (d: Date) => d.toLocaleTimeString([], { hour: 'numeric', minute: d.getMinutes() ? '2-digit' : undefined })

function dayWord(d: Date, now: Date) {
  const diff = Math.round((startOfDay(d).getTime() - startOfDay(now).getTime()) / DAY)
  if (diff === 0) return 'Today'
  if (diff === 1) return 'Tomorrow'
  return d.toLocaleDateString([], { weekday: 'short' })
}

/** The soonest time a message names that still matters (see the server rules). */
export function findTime(text: string, sent: Date, now: Date): TimeHit | null {
  const t = text.toLowerCase()
  const hits: TimeHit[] = []
  // A named day that clock times attach to: "tomorrow at 9", "Friday 7pm".
  let base: Date | null = null
  if (/\b(tomorrow|tmrw|tmr)\b/.test(t)) base = new Date(startOfDay(sent).getTime() + DAY)
  const wd = t.match(/\b(sunday|monday|tuesday|wednesday|thursday|friday|saturday)\b/)
  if (!base && wd) {
    const ahead = (WEEKDAYS.indexOf(wd[1]) - sent.getDay() + 7) % 7
    base = new Date(startOfDay(sent).getTime() + ahead * DAY)
  }

  for (const m of t.matchAll(/\bin (\d{1,3}) ?(m|min|mins|minutes|h|hr|hrs|hour|hours)\b/g)) {
    const at = new Date(sent.getTime() + Number(m[1]) * (m[2].startsWith('h') ? HOUR : 60000))
    hits.push({ label: `${dayWord(at, now)} ${clock(at)}`, at, end: at })
  }
  for (const m of t.matchAll(/\b(at|by)?\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b/g)) {
    const [, pre, hs, ms, ap] = m
    if (!pre && !ap) continue
    const h0 = Number(hs)
    const min = ms ? Number(ms) : 0
    if (h0 < 1 || h0 > 12 || min > 59) continue
    const hours = ap ? [(h0 % 12) + (ap === 'pm' ? 12 : 0)] : [h0 % 12, (h0 % 12) + 12].filter((h) => h === 0 || h > 6)
    const options: Date[] = []
    for (const h of hours) {
      if (base) options.push(atHour(base, h, min))
      else {
        let at = atHour(sent, h, min)
        if (at <= sent) at = new Date(at.getTime() + DAY)
        options.push(at)
      }
    }
    const at = options.sort((a, b) => a.getTime() - b.getTime())[0]
    if (at) hits.push({ label: `${dayWord(at, now)} ${clock(at)}`, at, end: at })
  }
  // Day parts are vague: no clock time, they just count until the part of the day is over.
  const part = t.match(/\bthis (morning|afternoon|evening)\b|\btonight\b/)
  if (part) {
    const which = part[1] ?? 'evening'
    const end = which === 'morning' ? atHour(sent, 12) : which === 'afternoon' ? atHour(sent, 17) : endOfDay(sent)
    hits.push({ label: which === 'morning' ? 'This morning' : which === 'afternoon' ? 'This afternoon' : 'Tonight', at: null, end })
  }
  if (/\btoday\b|\beod\b|\bend of (the )?day\b/.test(t)) hits.push({ label: 'Today', at: null, end: endOfDay(sent) })
  if (base && !hits.some((h) => h.at && startOfDay(h.at).getTime() === base!.getTime())) {
    hits.push({ label: dayWord(base, now), at: null, end: endOfDay(base) })
  }

  const live = hits.filter((h) => {
    const when = h.at ?? h.end
    const stillOn = h.at ? now.getTime() <= h.at.getTime() + HOUR : now.getTime() <= h.end.getTime()
    return stillOn && when.getTime() - now.getTime() <= 36 * HOUR
  })
  live.sort((a, b) => (a.at ?? a.end).getTime() - (b.at ?? b.end).getTime())
  return live[0] ?? null
}

/** Work out `priority` for a conversation and `importance` for its messages (mutates both). */
export function prioritize(c: Conversation, items: Message[], awaitingSince: string | null, vips: Map<string, string>, now = new Date()) {
  for (const m of items) m.importance = null
  if (c.isAutomated || c.prefs.muted) {
    c.priority = null
    return
  }
  const recent = (m: Message) => now.getTime() - new Date(m.sentAt).getTime() <= 3 * DAY
  const real = items.filter((m) => !m.event && !m.unsent)
  const incoming = real.filter((m) => !m.fromMe)
  const unread = new Set(incoming.slice(-Math.max(0, c.unreadCount)).map((m) => m.id))
  let i = real.length - 1
  while (i >= 0 && !real[i].fromMe) i--
  const run = new Set(real.slice(i + 1).map((m) => m.id))
  const mine = new Set(real.filter((m) => m.fromMe).map((m) => m.id))

  const reasons = new Map<string, PriorityReason[]>()
  const add = (m: Message, r: PriorityReason) => reasons.set(m.id, [...(reasons.get(m.id) ?? []), r])
  // In groups a mention or reply is the ask, so "asked" is for DMs only.
  const asking = awaitingSince ? incoming.find((m) => m.sentAt === awaitingSince) : undefined
  if (asking && recent(asking) && c.kind === 'dm') add(asking, { kind: 'asked' })

  for (const m of incoming) {
    if (!recent(m) || !(unread.has(m.id) || run.has(m.id))) continue
    if (c.kind === 'group' && m.mentionsMe) add(m, { kind: 'mention' })
    if (m.threadRootId && mine.has(m.threadRootId)) add(m, { kind: 'reply' })
    // Time and urgency count while unread; in a DM, a question you haven't answered keeps them
    // even after you've read it ("Can you call me in 20 min?").
    const openAsk = c.kind === 'dm' && run.has(m.id) && ASK_RE.test(m.text)
    if (unread.has(m.id) || openAsk) {
      const hit = findTime(m.text, new Date(m.sentAt), now)
      if (hit) add(m, { kind: 'time', label: hit.label, at: hit.at ? hit.at.toISOString() : null })
      const u = m.text.match(URGENT_RE)
      if (u) add(m, { kind: 'urgent', word: u[0].toLowerCase() })
    }
  }
  // Three or more unread texts in a row from the same person, each within 30 minutes.
  const unreadList = incoming.filter((m) => unread.has(m.id) && recent(m))
  let streak: Message[] = []
  for (const m of unreadList) {
    const prev = streak[streak.length - 1]
    if (prev && prev.senderHandle === m.senderHandle && new Date(m.sentAt).getTime() - new Date(prev.sentAt).getTime() <= 30 * 60000) streak.push(m)
    else streak = [m]
    if (streak.length >= 3) add(m, { kind: 'repeat', count: streak.length })
  }

  for (const m of incoming) {
    const rs = reasons.get(m.id)
    if (!rs) continue
    const vip = m.senderHandle ? vips.get(m.senderHandle) : undefined
    if (vip) rs.push({ kind: 'vip', name: vip })
    rs.sort((a, b) => ORDER.indexOf(a.kind) - ORDER.indexOf(b.kind))
    m.importance = rs
  }
  const counted = incoming.filter((m) => m.importance)
  if (!counted.length) {
    c.priority = null
    return
  }

  const union: PriorityReason[] = []
  for (const kind of ORDER) {
    const all = counted.flatMap((m) => m.importance!.filter((r) => r.kind === kind))
    if (!all.length) continue
    if (kind === 'time') {
      all.sort((a, b) => ((a as { at: string | null }).at ?? '9').localeCompare((b as { at: string | null }).at ?? '9'))
    }
    union.push(all[0])
  }
  const soonMs = (r: PriorityReason) => (r.kind === 'time' && r.at ? new Date(r.at).getTime() - now.getTime() : Infinity)
  const urgent = union.some((r) => r.kind === 'urgent' || r.kind === 'repeat' || (soonMs(r) <= 3 * HOUR && soonMs(r) >= -HOUR))
  const weightOf = (m: Message) => m.importance!.reduce((n, r) => n + WEIGHT[r.kind] + (r.kind === 'time' && soonMs(r) <= 3 * HOUR ? 50 : 0), 0)
  const top = [...counted].sort((a, b) => weightOf(b) - weightOf(a) || b.sentAt.localeCompare(a.sentAt))[0]
  const newest = counted[counted.length - 1]
  const ageHours = Math.min(72, (now.getTime() - new Date(newest.sentAt).getTime()) / HOUR)
  const timeAt = union.find((r) => r.kind === 'time') as { at: string | null; label: string } | undefined
  const priority: Priority = {
    level: urgent ? 'urgent' : 'high',
    score: Math.round((urgent ? 300 : 100) + union.reduce((n, r) => n + WEIGHT[r.kind], 0) - ageHours),
    reasons: union,
    messageId: top.id,
    until: timeAt?.at ? new Date(new Date(timeAt.at).getTime() + HOUR).toISOString() : null,
  }
  c.priority = priority
}
