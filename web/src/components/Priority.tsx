// Chips that say why something needs you: "In 16 min", "Asked you", "@ Mentioned you"…
import { useEffect, useState } from 'react'
import type { Conversation, PriorityReason } from '../api/types'
import { ageShort } from '../lib/format'
import { Icon, type IconName } from '../lib/icons'

/** Re-render every so often, so "In 16 min" counts down. */
export function useNow(everyMs = 60000) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), everyMs)
    return () => window.clearInterval(t)
  }, [everyMs])
  return now
}

const minutesUntil = (iso: string, now: number) => Math.round((new Date(iso).getTime() - now) / 60000)

/** A time reason within ~3 hours (or just past) makes things urgent. */
export function isSoon(r: PriorityReason, now: number) {
  if (r.kind !== 'time' || !r.at) return false
  const m = minutesUntil(r.at, now)
  return m <= 180 && m >= -60
}

function timeText(r: Extract<PriorityReason, { kind: 'time' }>, now: number, short: boolean) {
  if (r.at) {
    const m = minutesUntil(r.at, now)
    if (m >= -60 && m < 1) return 'Now'
    if (m >= 1 && m < 60) return `In ${m} min`
  }
  if (!short) return r.label
  return r.label.replace(/^Today /, '').replace(/^Tomorrow /, 'Tmrw ').replace(/^This (morning|afternoon)$/, (_, w: string) => w[0].toUpperCase() + w.slice(1))
}

const URGENT_LABEL: Record<string, string> = { asap: 'ASAP', 'a.s.a.p': 'ASAP', '911': '911' }

export function reasonView(r: PriorityReason, now: number, short = false): { icon: IconName; text: string; tone: 'urgent' | 'time' | 'plain' } {
  switch (r.kind) {
    case 'urgent':
      return { icon: 'alert', text: URGENT_LABEL[r.word] ?? r.word.charAt(0).toUpperCase() + r.word.slice(1), tone: 'urgent' }
    case 'repeat':
      return { icon: 'dms', text: `${r.count} texts`, tone: 'urgent' }
    case 'time':
      return { icon: 'clock', text: timeText(r, now, short), tone: isSoon(r, now) ? 'urgent' : 'time' }
    case 'asked':
      return { icon: 'help', text: 'Asked you', tone: 'plain' }
    case 'mention':
      return { icon: 'at', text: 'Mentioned you', tone: 'plain' }
    case 'reply':
      return { icon: 'reply', text: 'Replied to you', tone: 'plain' }
    case 'vip':
      return { icon: 'star', text: r.name, tone: 'plain' }
  }
}

/** All the reasons, as small chips (cards and messages). */
export function ReasonChips({ reasons, skip = [] }: { reasons: PriorityReason[]; skip?: PriorityReason['kind'][] }) {
  const now = useNow()
  const shown = reasons.filter((r) => !skip.includes(r.kind))
  if (!shown.length) return null
  return (
    <span className="reason-chips">
      {shown.map((r, i) => {
        const v = reasonView(r, now)
        return (
          <span key={i} className={`reason-chip is-${v.tone}`}>
            <Icon name={v.icon} size={12} strokeWidth={2.2} />
            {v.text}
          </span>
        )
      })}
    </span>
  )
}

/** The one chip a sidebar row has room for: the most pressing reason, or how long they've waited. */
export function SidebarChip({ c }: { c: Conversation }) {
  const now = useNow()
  const reasons = c.priority?.reasons ?? []
  // A clock time coming up soon says the most ("In 16 min"); otherwise the server's order.
  const top = reasons.find((r) => isSoon(r, now)) ?? reasons.find((r) => r.kind !== 'vip')
  if (!top || top.kind === 'asked') {
    return c.awaitingSince ? <span className="age-pill">{ageShort(c.awaitingSince)}</span> : null
  }
  const v = reasonView(top, now, true)
  const label = top.kind === 'mention' ? '@' : top.kind === 'reply' ? '' : v.text
  const urgent = v.tone === 'urgent' || c.priority?.level === 'urgent'
  return (
    <span className={`age-pill side-reason ${urgent ? 'is-urgent' : v.tone === 'time' ? 'is-time' : ''}`} title={c.priority!.reasons.map((r) => reasonView(r, now).text).join(' · ')}>
      {top.kind !== 'mention' && <Icon name={v.icon} size={11} strokeWidth={2.4} />}
      {label}
    </span>
  )
}

/** Order for the "Needs you" list: urgent, then high, then just-waiting; most pressing first. */
export function byNeed(a: Conversation, b: Conversation) {
  const rank = (c: Conversation) => (c.priority?.level === 'urgent' ? 0 : c.priority ? 1 : 2)
  return (
    rank(a) - rank(b) ||
    (b.priority?.score ?? 0) - (a.priority?.score ?? 0) ||
    (b.awaitingSince ?? b.lastMessage?.sentAt ?? '').localeCompare(a.awaitingSince ?? a.lastMessage?.sentAt ?? '')
  )
}
