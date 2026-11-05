import { useMemo } from 'react'
import type { Conversation } from '../api/types'
import { ageShort, timeShort } from '../lib/format'
import { Icon, Logo } from '../lib/icons'
import {
  closeRowMenu,
  displayName,
  getState,
  markConversationRead,
  markUnread,
  openConversation,
  openRowMenu,
  openView,
  setLaterDone,
  setRail,
  setState,
  toggleMute,
  toggleSection,
  toggleStar,
  useStore,
  type RailTab,
  unreadConversations,
} from '../state/store'
import type { LaterItem } from '../api/types'
import { Avatar, ConversationGlyph, Menu } from './common'
import { byNeed, SidebarChip } from './Priority'

export interface SidebarSections {
  waiting: Conversation[]
  starred: Conversation[]
  groups: Conversation[]
  dms: Conversation[]
  automated: Conversation[]
  /** Flat order used by keyboard navigation. */
  order: string[]
}

const byRecent = (a: Conversation, b: Conversation) => (b.lastMessage?.sentAt ?? '').localeCompare(a.lastMessage?.sentAt ?? '')

/** Each conversation appears once: Waiting on you › Starred › Groups / Direct messages. */
export function buildSections(convs: Record<string, Conversation>, now = Date.now()): SidebarSections {
  const visible = Object.values(convs).filter((c) => !c.prefs.snoozedUntil || new Date(c.prefs.snoozedUntil).getTime() <= now)
  const automated = visible.filter((c) => c.isAutomated).sort(byRecent)
  const people = visible.filter((c) => !c.isAutomated)
  // "Needs you": they asked you something, mentioned you, or it's time-sensitive. Most pressing first.
  const waiting = people.filter((c) => (c.awaitingReply || c.priority) && !c.prefs.muted).sort(byNeed)
  const waitingIds = new Set(waiting.map((c) => c.id))
  const rest = people.filter((c) => !waitingIds.has(c.id))
  const starred = rest.filter((c) => c.prefs.starred).sort(byRecent)
  const unstarred = rest.filter((c) => !c.prefs.starred)
  const groups = unstarred.filter((c) => c.kind === 'group' && c.isNamed).sort(byRecent)
  const dms = unstarred.filter((c) => !(c.kind === 'group' && c.isNamed)).sort(byRecent)
  const order = [...waiting, ...starred, ...groups, ...dms, ...automated].map((c) => c.id)
  return { waiting, starred, groups, dms, automated, order }
}

export function useSections() {
  const convs = useStore((s) => s.conversations)
  return useMemo(() => buildSections(convs), [convs])
}

// ------------------------------------------------------------------ rail

export function Rail() {
  const rail = useStore((s) => s.rail)
  const me = useStore((s) => s.me)
  const unreadDms = useStore((s) => Object.values(s.conversations).filter((c) => !c.isAutomated && !c.prefs.muted && c.unreadCount > 0).length)
  // Compare instants, not strings: the server writes local times with an offset (…-07:00), the app UTC (…Z).
  const newActivity = useStore((s) => {
    // The server counts past the 50 items loaded; its count holds until the user looks again.
    if (s.activityNew && s.activityNew.since === s.activitySeenAt) return s.activityNew.count
    const seen = s.activitySeenAt ? Date.parse(s.activitySeenAt) : 0
    return s.activity.filter((a) => Date.parse(a.at) > seen).length
  })
  // Badge only what needs attention now: reminders that fired and aren't done.
  const laterCount = useStore((s) => s.later.filter((l) => isDue(l)).length)
  const tab = (id: RailTab, icon: 'home' | 'dms' | 'bell' | 'bookmark', label: string, badge?: number, dot?: boolean) => (
    <button type="button" className={`rail-tab ${rail === id ? 'is-active' : ''}`} onClick={() => setRail(id)} aria-label={label}>
      <span className="rail-icon">
        <Icon name={icon} size={20} filled={rail === id && icon !== 'dms'} strokeWidth={rail === id ? 1.6 : 1.8} />
        {badge ? <span className="rail-badge">{badge > 99 ? '99+' : badge}</span> : dot ? <span className="rail-dot" /> : null}
      </span>
      <span className="rail-label">{label}</span>
    </button>
  )
  return (
    <nav className="rail" aria-label="Main">
      <div className="rail-logo" title="Backchannel">
        <Logo size={36} />
      </div>
      {tab('home', 'home', 'Home')}
      {tab('dms', 'dms', 'DMs', undefined, unreadDms > 0)}
      {tab('activity', 'bell', 'Activity', newActivity || undefined)}
      {tab('later', 'bookmark', 'Later', laterCount || undefined)}
      <div className="rail-spacer" />
      <button type="button" className="rail-new" aria-label="New message" title="New message" onClick={() => setState({ composeOpen: true })}>
        <Icon name="plus" size={20} strokeWidth={2.2} />
      </button>
      <button type="button" className="rail-me" aria-label="Settings" title="Settings" onClick={() => setState({ settingsOpen: true })}>
        <Avatar name={me?.name ?? 'Me'} person={me ? { handle: 'me', handles: [], name: me.name, shortName: me.shortName, isContact: true, avatarUrl: me.avatarUrl } : null} size={32} />
        <span className="rail-presence" />
      </button>
    </nav>
  )
}

// ------------------------------------------------------------------ sidebar column

export function SidebarColumn() {
  const rail = useStore((s) => s.rail)
  const list = rail === 'dms' ? <DmList /> : rail === 'activity' ? <ActivityList /> : rail === 'later' ? <LaterList /> : <HomeSidebar />
  return (
    <>
      {list}
      <RowMenu />
    </>
  )
}

/** Right-click on a conversation in the sidebar. */
function onRowContextMenu(e: React.MouseEvent, cid: string) {
  e.preventDefault()
  openRowMenu(cid, e.clientX, e.clientY)
}

function RowMenu() {
  const menu = useStore((s) => s.rowMenu)
  const c = useStore((s) => (s.rowMenu ? s.conversations[s.rowMenu.cid] : undefined))
  if (!menu || !c) return null
  return (
    <Menu
      anchor={null}
      at={{ x: menu.x, y: menu.y }}
      onClose={closeRowMenu}
      items={[
        c.unreadCount > 0
          ? { label: 'Mark as read', icon: 'check', onSelect: () => void markConversationRead(c.id) }
          : { label: 'Mark as unread', icon: 'dot', onSelect: () => void markUnread(c.id) },
        { divider: true, label: '' },
        { label: c.prefs.starred ? 'Unstar' : 'Star', icon: 'star', onSelect: () => toggleStar(c.id) },
        { label: c.prefs.muted ? 'Unmute' : 'Mute', icon: 'bellOff', onSelect: () => toggleMute(c.id) },
      ]}
    />
  )
}

function HomeSidebar() {
  const s = useSections()
  const view = useStore((st) => st.view)
  const catchUpCount = useStore(
    (st) => st.catchUp.data?.sections.filter((x) => x.kind === 'reply_now' || x.kind === 'reply_soon').reduce((n, x) => n + x.items.length, 0) ?? 0,
  )
  const scheduledCount = useStore((st) => st.scheduled.filter((x) => x.status === 'pending').length)
  const convs = useStore((st) => st.conversations)
  const unreadCount = useMemo(() => unreadConversations(convs).length, [convs])
  const threadsUnread = 0
  const me = useStore((st) => st.me)
  return (
    <aside className="sidebar" aria-label="Conversations">
      <header className="sidebar-head">
        <button type="button" className="workspace" onClick={() => setState({ settingsOpen: true })}>
          <span className="workspace-name">{me ? `${me.shortName}’s Messages` : 'Messages'}</span>
          <Icon name="chevronDown" size={16} />
        </button>
        <button type="button" className="sidebar-compose" aria-label="New message" title="New message" onClick={() => setState({ composeOpen: true })}>
          <Icon name="compose" size={18} />
        </button>
      </header>
      <div className="sidebar-scroll">
        <div className="sidebar-nav">
          <NavItem icon="inbox" label="Unreads" active={view?.kind === 'unreads'} count={unreadCount || undefined} bold={unreadCount > 0} onClick={() => openView('unreads')} />
          <NavItem icon="sparkles" label="Catch up" active={view?.kind === 'catchup'} badge={catchUpCount || undefined} onClick={() => openView('catchup')} accent />
          <NavItem icon="thread" label="Threads" active={view?.kind === 'threads'} badge={threadsUnread || undefined} onClick={() => openView('threads')} />
          <NavItem icon="send" label="Scheduled" active={view?.kind === 'scheduled'} count={scheduledCount || undefined} onClick={() => openView('scheduled')} />
        </div>

        {s.waiting.length > 0 && (
          <Section id="waiting" title="Needs you" count={s.waiting.length} hint="They asked you something, mentioned you, or it's time-sensitive. Most pressing first.">
            {s.waiting.map((c) => (
              <Row key={c.id} c={c} trailing={<SidebarChip c={c} />} />
            ))}
          </Section>
        )}
        {s.starred.length > 0 && (
          <Section id="starred" title="Starred">
            {s.starred.map((c) => (
              <Row key={c.id} c={c} />
            ))}
          </Section>
        )}
        {s.groups.length > 0 && (
          <Section id="groups" title="Groups">
            {s.groups.map((c) => (
              <Row key={c.id} c={c} />
            ))}
          </Section>
        )}
        {s.dms.length > 0 && (
          <Section id="dms" title="Direct messages">
            {s.dms.map((c) => (
              <Row key={c.id} c={c} />
            ))}
          </Section>
        )}
        {s.automated.length > 0 && (
          <Section
            id="automated"
            title="Apps & alerts"
            hint="Codes, deliveries and reminders, filed out of the way"
            collapsedSummary={s.automated.reduce((n, c) => n + c.unreadCount, 0) || undefined}
          >
            {s.automated.map((c) => (
              <Row key={c.id} c={c} />
            ))}
          </Section>
        )}
      </div>
    </aside>
  )
}

function NavItem({
  icon,
  label,
  active,
  badge,
  count,
  onClick,
  accent,
  bold,
}: {
  icon: 'sparkles' | 'thread' | 'send' | 'inbox'
  label: string
  active: boolean
  badge?: number
  count?: number
  onClick: () => void
  accent?: boolean
  bold?: boolean
}) {
  return (
    <button type="button" className={`side-row nav-row ${active ? 'is-active' : ''} ${accent ? 'is-accent' : ''} ${bold ? 'is-unread' : ''}`} onClick={onClick}>
      <span className="side-glyph">
        <Icon name={icon} size={17} />
      </span>
      <span className="side-label">{label}</span>
      {badge ? <span className="side-badge is-soft">{badge}</span> : count ? <span className="side-count">{count}</span> : null}
    </button>
  )
}

function Section({
  id,
  title,
  count,
  hint,
  collapsedSummary,
  children,
}: {
  id: string
  title: string
  count?: number
  hint?: string
  collapsedSummary?: number
  children: React.ReactNode
}) {
  const collapsed = useStore((s) => !!s.collapsed[id])
  return (
    <section className={`side-section section-${id}`}>
      <button type="button" className="section-head" onClick={() => toggleSection(id)} aria-expanded={!collapsed} title={hint}>
        <Icon name={collapsed ? 'chevronRight' : 'chevronDown'} size={14} strokeWidth={2.2} />
        <span>{title}</span>
        {count ? <span className="section-count">{count}</span> : null}
        {collapsed && collapsedSummary ? <span className="side-badge is-soft">{collapsedSummary}</span> : null}
      </button>
      {!collapsed && <div className="section-body">{children}</div>}
    </section>
  )
}

function Row({ c, trailing }: { c: Conversation; trailing?: React.ReactNode }) {
  const active = useStore((s) => s.view?.kind === 'conversation' && s.view.id === c.id)
  const hasDraft = useStore((s) => !!s.drafts[c.id])
  const unread = c.unreadCount > 0 && !c.prefs.muted
  return (
    <button
      type="button"
      className={`side-row ${active ? 'is-active' : ''} ${unread ? 'is-unread' : ''} ${c.prefs.muted ? 'is-muted' : ''}`}
      onClick={() => openConversation(c.id)}
      onContextMenu={(e) => onRowContextMenu(e, c.id)}
      title={c.title}
    >
      <span className="side-glyph">
        <ConversationGlyph c={c} size={20} />
      </span>
      <span className="side-label">{c.title}</span>
      {hasDraft && !active && <Icon name="edit" size={14} className="side-draft" />}
      {c.prefs.muted && <Icon name="bellOff" size={14} className="side-muted" />}
      {c.mentionCount > 0 ? (
        <span className="side-badge">{c.mentionCount}</span>
      ) : c.kind === 'dm' && unread && !c.isAutomated ? (
        <span className="side-badge">{c.unreadCount}</span>
      ) : null}
      {trailing}
    </button>
  )
}

// ------------------------------------------------------------------ DMs tab

function DmList() {
  const convs = useStore((s) => s.conversations)
  const activeId = useStore((s) => (s.view?.kind === 'conversation' ? s.view.id : null))
  const list = useMemo(() => Object.values(convs).filter((c) => !c.isAutomated).sort(byRecent), [convs])
  return (
    <aside className="sidebar" aria-label="Direct messages">
      <header className="sidebar-head">
        <h2 className="sidebar-title">Direct messages</h2>
        <button type="button" className="sidebar-compose" aria-label="New message" title="New message" onClick={() => setState({ composeOpen: true })}>
          <Icon name="compose" size={18} />
        </button>
      </header>
      <div className="sidebar-scroll">
        {list.map((c) => {
          const lm = c.lastMessage
          const who = lm ? (lm.fromMe ? 'You' : c.kind === 'group' ? displayName(lm.senderHandle, true) : null) : null
          return (
            <button
              key={c.id}
              type="button"
              className={`dm-card ${activeId === c.id ? 'is-active' : ''} ${c.unreadCount > 0 ? 'is-unread' : ''}`}
              onClick={() => openConversation(c.id)}
              onContextMenu={(e) => onRowContextMenu(e, c.id)}
            >
              <ConversationGlyph c={c} size={36} />
              <span className="dm-card-body">
                <span className="dm-card-top">
                  <span className="dm-card-name">{c.title}</span>
                  {lm && <span className="dm-card-time">{ageShort(lm.sentAt)}</span>}
                </span>
                <span className="dm-card-preview">
                  {who && <span className="dm-card-who">{who}: </span>}
                  {lm?.text}
                </span>
              </span>
              {c.unreadCount > 0 && <span className="dm-card-dot" aria-label={`${c.unreadCount} unread`} />}
            </button>
          )
        })}
      </div>
    </aside>
  )
}

// ------------------------------------------------------------------ Activity tab

function ActivityList() {
  const items = useStore((s) => s.activity)
  const convs = useStore((s) => s.conversations)
  return (
    <aside className="sidebar" aria-label="Activity">
      <header className="sidebar-head">
        <h2 className="sidebar-title">Activity</h2>
      </header>
      <div className="sidebar-scroll">
        {items.length === 0 && <p className="side-empty">Mentions, replies to your threads and reactions to your texts show up here.</p>}
        {items.map((a) => {
          const c = convs[a.conversationId]
          const actor = getState().people[a.actorHandle]
          const verb =
            a.type === 'reaction' ? `reacted ${a.reaction?.emoji ?? ''} to your message` : a.type === 'mention' ? 'mentioned you' : 'replied in a thread'
          return (
            <button key={a.id} type="button" className="activity-card" onClick={() => openConversation(a.conversationId, a.message.id)}>
              <span className="activity-kind">
                <Icon name={a.type === 'reaction' ? 'smile' : a.type === 'mention' ? 'at' : 'thread'} size={14} />
                {a.type === 'reaction' ? 'Reaction' : a.type === 'mention' ? 'Mention' : 'Thread'} in {c?.title ?? 'a conversation'}
                <span className="activity-time">{ageShort(a.at)}</span>
              </span>
              <span className="activity-main">
                <Avatar person={actor} name={displayName(a.actorHandle)} size={32} />
                <span className="activity-text">
                  <strong>{displayName(a.actorHandle)}</strong> {verb}
                  <span className="activity-quote">{a.message.text || 'Photo'}</span>
                </span>
              </span>
            </button>
          )
        })}
      </div>
    </aside>
  )
}

// ------------------------------------------------------------------ Later tab

export function isDue(l: LaterItem, now = Date.now()) {
  if (l.done || l.resolvedByReply) return false
  return !!l.firedAt || (!!l.remindAt && new Date(l.remindAt).getTime() <= now && !l.ifNoReply)
}

function LaterList() {
  const all = useStore((s) => s.later)
  const convs = useStore((s) => s.conversations)
  const open = all.filter((l) => !l.done)
  const due = open.filter((l) => isDue(l))
  const waiting = open.filter((l) => !isDue(l)).sort((a, b) => (a.remindAt ?? '9').localeCompare(b.remindAt ?? '9'))
  const done = all.filter((l) => l.done).slice(0, 10)
  const card = (l: LaterItem, state: 'due' | 'waiting' | 'done') => {
    const c = convs[l.conversationId]
    const m = l.message
    const label =
      state === 'due'
        ? l.ifNoReply
          ? 'No reply yet'
          : 'Reminder'
        : l.resolvedByReply
          ? 'They replied'
          : l.ifNoReply && l.remindAt
            ? `Nudge if no reply by ${ageLabelFuture(l.remindAt)}`
            : l.remindAt
              ? `Due ${ageLabelFuture(l.remindAt)}`
              : 'Saved'
    return (
      <div key={l.messageId} className={`later-card is-${state} ${l.resolvedByReply ? 'is-resolved' : ''}`}>
        <button type="button" className="activity-card" onClick={() => openConversation(l.conversationId, l.messageId)}>
          <span className="activity-kind">
            <Icon name={state === 'due' ? 'bell' : l.resolvedByReply ? 'check' : 'clock'} size={14} />
            {label}
            <span className="activity-time">{c?.title}</span>
          </span>
          <span className="activity-main">
            <Avatar person={m && !m.fromMe ? getState().people[m.senderHandle ?? ''] : null} name={m ? displayName(m.senderHandle) : '?'} size={32} />
            <span className="activity-text">
              <strong>{m ? displayName(m.senderHandle) : 'Message'}</strong>
              <span className="activity-quote">{m?.text || (m?.attachments.length ? 'Photo' : '')}</span>
            </span>
          </span>
        </button>
        {state !== 'done' && (
          <button type="button" className="later-done" title="Mark done" aria-label="Mark done" onClick={() => setLaterDone(l.messageId, true)}>
            <Icon name="check" size={15} />
          </button>
        )}
      </div>
    )
  }
  return (
    <aside className="sidebar" aria-label="Later">
      <header className="sidebar-head">
        <h2 className="sidebar-title">Later</h2>
      </header>
      <div className="sidebar-scroll">
        {open.length === 0 && done.length === 0 && (
          <p className="side-empty">Hover any text and choose “Remind me later” to park it here. You’ll get a nudge when it’s time.</p>
        )}
        {due.length > 0 && <p className="later-heading">Now</p>}
        {due.map((l) => card(l, 'due'))}
        {waiting.length > 0 && <p className="later-heading">Coming up</p>}
        {waiting.map((l) => card(l, 'waiting'))}
        {done.length > 0 && <p className="later-heading">Done</p>}
        {done.map((l) => card(l, 'done'))}
      </div>
    </aside>
  )
}

function ageLabelFuture(iso: string) {
  const d = new Date(iso)
  const now = new Date()
  const days = Math.round((d.getTime() - now.getTime()) / 86400000)
  if (days <= 0) return `today at ${timeShort(iso)}`
  if (days === 1) return `tomorrow at ${timeShort(iso)}`
  return `${d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })} at ${timeShort(iso)}`
}
