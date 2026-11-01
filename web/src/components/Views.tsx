import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api/client'
import type { CatchUpItem, CatchUpSection, Conversation, Message } from '../api/types'
import { ageLong, ageShort, plural, whenLabel } from '../lib/format'
import { Icon } from '../lib/icons'
import {
  cancelScheduled,
  displayName,
  goBack,
  loadCatchUp,
  loadMessages,
  markAllRead,
  markCatchUpDone,
  markConversationRead,
  openConversation,
  openThread,
  openWithDraft,
  personFor,
  sendMessage,
  toast,
  unreadConversations,
  unreadMessages,
  useStore,
} from '../state/store'
import { Avatar, ConversationGlyph, Spinner } from './common'
import { Composer } from './Composer'
import { MessageContent, MessageRow } from './Message'
import { byNeed, ReasonChips } from './Priority'

function ViewHeader({ icon, title, sub, right }: { icon: 'sparkles' | 'thread' | 'send' | 'inbox'; title: string; sub?: string; right?: React.ReactNode }) {
  return (
    <header className="view-head">
      <button type="button" className="mobile-back icon-btn" aria-label="Back" onClick={() => goBack()}>
        <Icon name="arrowLeft" size={20} />
      </button>
      <span className="view-icon">
        <Icon name={icon} size={18} />
      </span>
      <div className="view-titles">
        <h1>{title}</h1>
        {sub && <p>{sub}</p>}
      </div>
      <div className="view-right">{right}</div>
    </header>
  )
}

// ------------------------------------------------------------------ Catch up

export function CatchUpView() {
  const cu = useStore((s) => s.catchUp)
  const waiting = useStore((s) => Object.values(s.conversations).filter((c) => c.awaitingReply && !c.isAutomated))
  const data = cu.data
  return (
    <section className="view catchup">
      <ViewHeader
        icon="sparkles"
        title="Catch up"
        sub={data ? `Summarized ${ageLong(data.generatedAt)} from your unread and unanswered texts` : 'A quick read of what needs you'}
        right={
          <button type="button" className="btn" onClick={() => loadCatchUp(true)} disabled={cu.loading}>
            <Icon name="refresh" size={15} /> {cu.loading ? 'Catching up…' : 'Refresh'}
          </button>
        }
      />
      <div className="view-scroll">
        <div className="catchup-inner">
          {!data && cu.loading && (
            <div className="panel-loading">
              <Spinner label="Catching up" />
            </div>
          )}
          {!data && cu.error && (
            <div className="notice">
              <p className="notice-title">Summaries are off</p>
              <p>{cu.error}</p>
              {waiting.length > 0 && (
                <>
                  <p className="notice-title">Waiting on you</p>
                  {waiting.map((c) => (
                    <button key={c.id} type="button" className="side-row is-plain" onClick={() => openConversation(c.id)}>
                      <span className="side-glyph">
                        <ConversationGlyph c={c} size={20} />
                      </span>
                      <span className="side-label">{c.title}</span>
                      <span className="age-pill">{ageShort(c.awaitingSince!)}</span>
                    </button>
                  ))}
                </>
              )}
            </div>
          )}
          {data && (
            <>
              <p className="vibe">{data.vibe}</p>
              {data.sections.map((s) => (
                <CatchUpBlock key={s.kind} s={s} done={cu.done} />
              ))}
            </>
          )}
        </div>
      </div>
    </section>
  )
}

function CatchUpBlock({ s, done }: { s: CatchUpSection; done: string[] }) {
  const items = s.items.filter((it) => !done.includes(itemKey(s.kind, it)))
  const finished = s.items.length - items.length
  if (!s.items.length) return null
  const tone = s.kind === 'reply_now' ? 'is-now' : s.kind === 'reply_soon' ? 'is-soon' : s.kind === 'plans' ? 'is-plans' : 'is-done'
  return (
    <section className={`cu-block ${tone}`}>
      <h2 className="cu-head">
        <span className="cu-dot" />
        {s.title}
        <span className="cu-count">{items.length}</span>
        {finished > 0 && <span className="cu-finished">{finished} handled</span>}
      </h2>
      {s.kind === 'caught_up' ? (
        <ul className="cu-list">
          {items.map((it) => (
            <li key={itemKey(s.kind, it)}>
              <button type="button" className="link-btn" onClick={() => openConversation(it.conversationId)}>
                {it.title}
              </button>{' '}
              {it.summary}
            </li>
          ))}
        </ul>
      ) : s.kind === 'plans' ? (
        <div className="cu-plans">
          {items.map((it) => (
            <PlanRow key={itemKey(s.kind, it)} it={it} k={itemKey(s.kind, it)} />
          ))}
        </div>
      ) : (
        <div className="cu-cards">
          {items.map((it) => (
            <ReplyCard key={itemKey(s.kind, it)} it={it} k={itemKey(s.kind, it)} />
          ))}
        </div>
      )}
    </section>
  )
}

const itemKey = (kind: string, it: CatchUpItem) => `${kind}:${it.conversationId}:${it.title}`

function ReplyCard({ it, k }: { it: CatchUpItem; k: string }) {
  const c = useStore((s) => s.conversations[it.conversationId])
  const [text, setText] = useState(it.draft ?? '')
  const [sent, setSent] = useState(false)
  if (!c) return null
  return (
    <article className="cu-card">
      <header className="cu-card-head">
        <ConversationGlyph c={c} size={28} />
        <button type="button" className="cu-card-title" onClick={() => openWithDraft(c.id, sent ? '' : text)}>
          {it.title}
        </button>
        {c.awaitingSince && <span className="age-pill is-light">waiting {ageShort(c.awaitingSince)}</span>}
      </header>
      <p className="cu-summary">{it.summary}</p>
      {sent ? (
        <p className="cu-sent">
          <Icon name="check" size={15} /> Sent
        </p>
      ) : (
        <form
          className="cu-reply"
          onSubmit={(e) => {
            e.preventDefault()
            if (!text.trim()) return
            void sendMessage(c.id, text)
            setSent(true)
            setTimeout(() => markCatchUpDone(k), 1800)
          }}
        >
          <label className="cu-reply-label" htmlFor={`cu-${k}`}>
            <Icon name="sparkles" size={13} /> Suggested reply
          </label>
          <textarea id={`cu-${k}`} value={text} rows={2} onChange={(e) => setText(e.target.value)} />
          <div className="cu-actions">
            <button type="button" className="btn is-quiet" onClick={() => markCatchUpDone(k)}>
              Done
            </button>
            <button type="button" className="btn" title="Open the conversation with this reply in the message box" onClick={() => openWithDraft(c.id, text)}>
              Open
            </button>
            <button type="submit" className="btn is-primary" disabled={!text.trim()}>
              <Icon name="send" size={14} /> Send
            </button>
          </div>
        </form>
      )}
    </article>
  )
}

function PlanRow({ it, k }: { it: CatchUpItem; k: string }) {
  return (
    <div className="cu-plan">
      <Icon name="calendar" size={18} />
      <div className="cu-plan-body">
        <div className="cu-plan-top">
          <button type="button" className="cu-card-title" onClick={() => openConversation(it.conversationId)}>
            {it.title}
          </button>
          {it.status && <span className={`status-chip ${it.status === 'locked_in' ? 'is-locked' : 'is-action'}`}>{it.status === 'locked_in' ? 'Locked in' : 'Needs action'}</span>}
        </div>
        <p className="cu-summary">{it.summary}</p>
      </div>
      <button type="button" className="icon-btn" aria-label="Mark handled" title="Mark handled" onClick={() => markCatchUpDone(k)}>
        <Icon name="check" size={16} />
      </button>
    </div>
  )
}

// ------------------------------------------------------------------ Threads

export function ThreadsView() {
  const [items, setItems] = useState<Message[] | null>(null)
  const convs = useStore((s) => s.conversations)
  useEffect(() => {
    let live = true
    api
      .threads()
      .then((x) => live && setItems(x))
      .catch(() => live && setItems([]))
    return () => {
      live = false
    }
  }, [])
  return (
    <section className="view">
      <ViewHeader icon="thread" title="Threads" sub="Inline replies from every conversation, newest first" />
      <div className="view-scroll">
        <div className="threads-inner">
          {!items && (
            <div className="panel-loading">
              <Spinner />
            </div>
          )}
          {items?.length === 0 && <p className="side-empty">No threads yet. When someone swipes to reply to a text, the thread shows up here.</p>}
          {items?.map((m) => {
            const c = convs[m.conversationId]
            const sender = m.fromMe ? null : personFor(m.senderHandle)
            return (
              <article key={m.id} className="thread-card">
                <header className="thread-card-head">
                  {c && <ConversationGlyph c={c} size={18} />}
                  <span className="thread-card-convo">{c?.title}</span>
                  <span className="thread-card-time">{m.thread ? `last reply ${ageLong(m.thread.lastReplyAt)}` : ''}</span>
                </header>
                <div className="thread-card-msg">
                  <Avatar person={sender} name={displayName(m.senderHandle)} size={32} />
                  <div className="msg-body">
                    <div className="msg-meta">
                      <span className="msg-name">{displayName(m.senderHandle)}</span>
                    </div>
                    <MessageContent m={m} />
                  </div>
                </div>
                <button type="button" className="thread-card-open" onClick={() => openThread(m.conversationId, m.id)}>
                  {m.thread?.replyCount} {m.thread?.replyCount === 1 ? 'reply' : 'replies'} <Icon name="chevronRight" size={14} />
                </button>
              </article>
            )
          })}
        </div>
      </div>
    </section>
  )
}

// ------------------------------------------------------------------ Unreads

/** Every conversation with something new in one place (Slack's "All unreads"). Nothing is marked read until you say so. */
export function UnreadsView() {
  const convs = useStore((s) => s.conversations)
  const list = useMemo(() => unreadConversations(convs), [convs])
  const people = list.filter((c) => !c.isAutomated)
  const needs = people.filter((c) => c.priority).sort(byNeed)
  const rest = people.filter((c) => !c.priority)
  const automated = list.filter((c) => c.isAutomated)
  const ids = list.map((c) => c.id).join('|')
  useEffect(() => {
    for (const id of ids ? ids.split('|') : []) void loadMessages(id)
  }, [ids])
  const total = list.reduce((n, c) => n + c.unreadCount, 0)
  return (
    <section className="view">
      <ViewHeader
        icon="inbox"
        title="Unreads"
        sub={list.length ? `${plural(total, 'new message')} in ${plural(list.length, 'conversation')}, newest first` : 'Nothing new'}
        right={
          list.length > 0 && (
            <button type="button" className="btn" onClick={() => markAllRead(list.map((c) => c.id))}>
              <Icon name="checkCheck" size={16} /> Mark all read
            </button>
          )
        }
      />
      <div className="view-scroll">
        <div className="unreads-inner">
          {list.length === 0 && (
            <div className="unreads-empty">
              <span className="unreads-empty-icon">
                <Icon name="checkCheck" size={26} />
              </span>
              <p className="empty-title">You’re all caught up</p>
              <p className="empty-sub">New texts from every conversation show up here.</p>
            </div>
          )}
          {needs.length > 0 && (
            <>
              <h2 className="unreads-group is-needs">
                <Icon name="alert" size={14} strokeWidth={2.2} /> Needs you first
              </h2>
              {needs.map((c) => (
                <UnreadCard key={c.id} c={c} />
              ))}
            </>
          )}
          {rest.length > 0 && needs.length > 0 && <h2 className="unreads-group">Everything else</h2>}
          {rest.map((c) => (
            <UnreadCard key={c.id} c={c} />
          ))}
          {automated.length > 0 && (
            <>
              <h2 className="unreads-group">Apps &amp; alerts</h2>
              {automated.map((c) => (
                <UnreadCard key={c.id} c={c} />
              ))}
            </>
          )}
        </div>
      </div>
    </section>
  )
}

function UnreadCard({ c }: { c: Conversation }) {
  const ms = useStore((s) => s.messages[c.id])
  const [replying, setReplying] = useState(false)
  const card = useRef<HTMLElement>(null)
  const unread = useMemo(() => (ms ? unreadMessages(c, ms.items) : []), [ms, c])
  const shown = unread.slice(-5)
  const earlier = unread.length - shown.length

  useEffect(() => {
    if (replying) card.current?.querySelector<HTMLTextAreaElement>('.unread-card-reply textarea')?.focus()
  }, [replying])

  return (
    <article
      ref={card}
      className={`unread-card ${c.priority?.level === 'urgent' ? 'is-urgent' : c.priority ? 'is-needs' : ''}`}
      aria-label={`${c.title}, ${plural(c.unreadCount, 'new message')}`}
    >
      <header className="unread-card-head">
        <button type="button" className="unread-card-title" onClick={() => openConversation(c.id)} title="Open the conversation">
          <ConversationGlyph c={c} size={20} />
          <span className="unread-card-name">{c.title}</span>
          <span className="unread-card-count">{c.unreadCount} new</span>
        </button>
        <span className="unread-card-time">{c.lastMessage ? ageShort(c.lastMessage.sentAt) : ''}</span>
        {c.priority && <ReasonChips reasons={c.priority.reasons} skip={c.kind === 'dm' ? ['vip'] : []} />}
        <div className="unread-card-actions">
          <button type="button" className={`btn is-quiet ${replying ? 'is-on' : ''}`} aria-expanded={replying} onClick={() => setReplying((r) => !r)}>
            <Icon name="reply" size={15} /> Reply
          </button>
          <button type="button" className="btn is-quiet" onClick={() => markConversationRead(c.id)}>
            <Icon name="check" size={15} /> Mark read
          </button>
        </div>
      </header>
      <div className="unread-card-msgs">
        {(!ms || ms.loading) && (
          <div className="panel-loading">
            <Spinner />
          </div>
        )}
        {ms?.error && <p className="side-empty">{ms.error}</p>}
        {earlier > 0 && (
          <button type="button" className="link-btn unread-earlier" onClick={() => openConversation(c.id)}>
            {plural(earlier, 'earlier new message')}: open the conversation
          </button>
        )}
        {shown.map((m, i) => (
          <MessageRow key={m.id} m={m} opts={{ head: i === 0 || shown[i - 1].senderHandle !== m.senderHandle, isGroup: c.kind === 'group' }} />
        ))}
      </div>
      {replying && (
        <div className="unread-card-reply">
          <Composer
            c={c}
            compact
            onSent={() => {
              // Replying here counts as reading it.
              toast(`Replied to ${c.title}`)
              void markConversationRead(c.id)
            }}
          />
        </div>
      )}
    </article>
  )
}

// ------------------------------------------------------------------ Scheduled

export function ScheduledView() {
  const items = useStore((s) => s.scheduled)
  const convs = useStore((s) => s.conversations)
  const pending = items.filter((x) => x.status === 'pending')
  const past = items.filter((x) => x.status !== 'pending')
  return (
    <section className="view">
      <ViewHeader icon="send" title="Scheduled" sub="Texts waiting to go out. Your Mac needs to be awake with Backchannel running." />
      <div className="view-scroll">
        <div className="threads-inner">
          {pending.length === 0 && (
            <p className="side-empty">Nothing scheduled. In any conversation, use the arrow next to Send to pick a time.</p>
          )}
          {pending.map((s) => {
            const c = convs[s.conversationId]
            return (
              <article key={s.id} className="sched-card">
                <div className="sched-when">
                  <Icon name="clock" size={15} /> Sends {whenLabel(s.sendAt)}
                </div>
                <div className="sched-body">
                  {c && <ConversationGlyph c={c} size={28} />}
                  <div>
                    <button type="button" className="cu-card-title" onClick={() => c && openConversation(c.id)}>
                      {c?.title ?? 'Conversation'}
                    </button>
                    <p className="sched-text">{s.text}</p>
                  </div>
                </div>
                <div className="cu-actions">
                  <button type="button" className="btn is-quiet" onClick={() => cancelScheduled(s.id)}>
                    Cancel
                  </button>
                </div>
              </article>
            )
          })}
          {past.length > 0 && (
            <>
              <h3 className="media-heading">Sent or cancelled</h3>
              {past.map((s) => (
                <p key={s.id} className="sched-past">
                  <span className={`status-chip ${s.status === 'sent' ? 'is-locked' : s.status === 'failed' ? 'is-fail' : ''}`}>{s.status}</span> {convs[s.conversationId]?.title}: {s.text}
                </p>
              ))}
            </>
          )}
        </div>
      </div>
    </section>
  )
}
