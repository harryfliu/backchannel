import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { CatchUpItem, CatchUpSection, Message } from '../api/types'
import { ageLong, ageShort, whenLabel } from '../lib/format'
import { Icon } from '../lib/icons'
import {
  cancelScheduled,
  displayName,
  goBack,
  loadCatchUp,
  markCatchUpDone,
  openConversation,
  openThread,
  personFor,
  sendMessage,
  useStore,
} from '../state/store'
import { Avatar, ConversationGlyph, Spinner } from './common'
import { MessageContent } from './Message'

function ViewHeader({ icon, title, sub, right }: { icon: 'sparkles' | 'thread' | 'send'; title: string; sub?: string; right?: React.ReactNode }) {
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
        <button type="button" className="cu-card-title" onClick={() => openConversation(c.id)}>
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
            <button type="button" className="btn" onClick={() => openConversation(c.id)}>
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
