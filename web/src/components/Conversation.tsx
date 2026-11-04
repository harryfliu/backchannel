import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { Conversation, Message } from '../api/types'
import { dayLabel, fileSize, formatHandle, isSameDay, plural, toDate, whenLabel } from '../lib/format'
import { Icon } from '../lib/icons'
import {
  goBack,
  loadMedia,
  loadOlder,
  markConversationRead,
  markUnread,
  openDetails,
  openProfile,
  openSearch,
  openThread,
  openView,
  setState,
  snooze,
  toggleMute,
  toggleStar,
  useStore,
} from '../state/store'
import { Avatar, ConversationGlyph, Menu, remindPresets, Spinner, useMenu } from './common'
import { Composer } from './Composer'
import { MessageRow, type RowOptions } from './Message'

type Row = { type: 'new'; key: string } | { type: 'msg'; key: string; m: Message; opts: RowOptions }

/** Messages grouped by day, so each day's date pill can stick while you scroll through that day. */
export interface DayGroup {
  key: string
  label: string
  rows: Row[]
}

const GROUP_GAP_MS = 5 * 60000

export function buildGroups(items: Message[], mode: 'thread-only' | 'also-inline', newMarkerId: string | null, isGroup: boolean): DayGroup[] {
  const byId = new Map(items.map((m) => [m.id, m]))
  const visible = mode === 'thread-only' ? items.filter((m) => !m.threadRootId || !byId.has(m.threadRootId)) : items
  const groups: DayGroup[] = []
  let rows: Row[] = []
  let prev: Message | null = null
  const lastMine = [...visible].reverse().find((m) => !m.event)
  for (const m of visible) {
    const d = toDate(m.sentAt)
    if (!prev || !isSameDay(toDate(prev.sentAt), d)) {
      rows = []
      groups.push({ key: `day-${m.id}`, label: dayLabel(m.sentAt), rows })
    }
    let fresh = false
    if (m.id === newMarkerId) {
      rows.push({ type: 'new', key: `new-${m.id}` })
      fresh = true
    }
    const sameSender = prev && !prev.event && !m.event && prev.fromMe === m.fromMe && prev.senderHandle === m.senderHandle
    const close = prev && d.getTime() - toDate(prev.sentAt).getTime() < GROUP_GAP_MS && isSameDay(toDate(prev.sentAt), d)
    const ctx = mode === 'also-inline' && m.threadRootId ? (byId.get(m.threadRootId) ?? null) : null
    const head = fresh || !sameSender || !close || !!ctx || !!(prev && prev.threadRootId && mode === 'also-inline')
    const showStatus = m.fromMe && (m === lastMine || m.status === 'failed' || m.status === 'sending')
    rows.push({ type: 'msg', key: m.id, m, opts: { head, inlineReplyContext: ctx, showStatus, isGroup } })
    prev = m
  }
  return groups
}

export function ConversationView({ c }: { c: Conversation }) {
  const [tab, setTab] = useState<'messages' | 'media'>('messages')
  const [dragging, setDragging] = useState(false)
  const depth = useRef(0)
  useEffect(() => setTab('messages'), [c.id])
  const hasFiles = (e: React.DragEvent) => Array.from(e.dataTransfer.types).includes('Files')
  return (
    <section
      className={`convo ${dragging ? 'is-dropping' : ''}`}
      aria-label={c.title}
      onDragEnter={(e) => {
        if (!hasFiles(e)) return
        depth.current += 1
        setDragging(true)
      }}
      onDragOver={(e) => {
        if (hasFiles(e)) e.preventDefault()
      }}
      onDragLeave={() => {
        depth.current = Math.max(0, depth.current - 1)
        if (depth.current === 0) setDragging(false)
      }}
      onDrop={(e) => {
        if (!hasFiles(e)) return
        e.preventDefault()
        depth.current = 0
        setDragging(false)
        setTab('messages')
        const files = Array.from(e.dataTransfer.files)
        if (files.length) window.dispatchEvent(new CustomEvent('bc:attach', { detail: { cid: c.id, files } }))
      }}
    >
      {dragging && (
        <div className="drop-overlay" aria-hidden="true">
          <Icon name="image" size={28} />
          <span>Drop to add to your message to {c.title}</span>
        </div>
      )}
      <ConversationHeader c={c} tab={tab} setTab={setTab} />
      {tab === 'messages' ? (
        <>
          <ScheduledBanner c={c} />
          <MessageList c={c} />
          <Composer c={c} />
        </>
      ) : (
        <MediaTab c={c} />
      )}
    </section>
  )
}

function ConversationHeader({ c, tab, setTab }: { c: Conversation; tab: string; setTab: (t: 'messages' | 'media') => void }) {
  const more = useMenu()
  const huddle = useMenu()
  const dm = c.kind === 'dm' ? c.participants[0] : null
  const subtitle = dm
    ? `${dm.isContact ? formatHandle(dm.handle) : 'Not in your contacts'} · ${c.service === 'SMS' ? 'Text message' : c.service}`
    : `${plural(c.participants.length + 1, 'person', 'people')} · ${c.service}`
  return (
    <header className="convo-head">
      <div className="convo-title-row">
        <button type="button" className="mobile-back icon-btn" aria-label="Back" onClick={() => goBack()}>
          <Icon name="arrowLeft" size={20} />
        </button>
        <button
          type="button"
          className="convo-title"
          onClick={() => (dm ? openProfile(dm.handle, c.id) : openDetails(c.id))}
          title={dm ? 'View profile' : 'Conversation details'}
        >
          <ConversationGlyph c={c} size={24} />
          <span className="convo-name">{c.title}</span>
          <Icon name="chevronDown" size={16} />
        </button>
        <span className={`service-pill ${c.service === 'SMS' ? 'is-sms' : ''}`}>{c.service === 'SMS' ? 'SMS' : 'iMessage'}</span>
        <div className="convo-actions">
          {c.kind === 'group' && (
            <button type="button" className="member-stack" onClick={() => openDetails(c.id)} title="Members">
              {c.participants.slice(0, 3).map((p) => (
                <Avatar key={p.handle} person={p} size={22} radius={5} />
              ))}
              <span>{c.participants.length + 1}</span>
            </button>
          )}
          {!c.isAutomated && (
          <button type="button" className="huddle-btn" onClick={huddle.open} title="Call">
            <Icon name="headphones" size={17} />
            <span>Huddle</span>
            <Icon name="chevronDown" size={13} strokeWidth={2.2} />
          </button>
          )}
          <button
            type="button"
            className={`icon-btn ${c.prefs.starred ? 'is-starred' : ''}`}
            aria-label={c.prefs.starred ? 'Unstar' : 'Star'}
            title={c.prefs.starred ? 'Unstar' : 'Star'}
            onClick={() => toggleStar(c.id)}
          >
            <Icon name="star" size={18} filled={c.prefs.starred} />
          </button>
          <button type="button" className="icon-btn" aria-label="More" title="More" onClick={more.open}>
            <Icon name="moreV" size={18} />
          </button>
        </div>
      </div>
      <p className="convo-sub">{subtitle}</p>
      <nav className="convo-tabs" aria-label="Conversation sections">
        <button type="button" className={tab === 'messages' ? 'is-active' : ''} onClick={() => setTab('messages')}>
          <Icon name="dms" size={15} /> Messages
        </button>
        <button type="button" className={tab === 'media' ? 'is-active' : ''} onClick={() => setTab('media')}>
          <Icon name="image" size={15} /> Photos & files
        </button>
      </nav>
      {huddle.anchor && (
        <Menu
          anchor={huddle.anchor}
          align="right"
          onClose={huddle.close}
          header={<div className="menu-header">{dm ? `Call ${dm.shortName}` : 'Group call'}</div>}
          items={
            dm
              ? [
                  { label: 'FaceTime Audio', icon: 'headphones', onSelect: () => (window.location.href = `facetime-audio:${dm.handle}`) },
                  { label: 'FaceTime Video', icon: 'video', onSelect: () => (window.location.href = `facetime:${dm.handle}`) },
                  { label: 'Call with iPhone', icon: 'phone', note: formatHandle(dm.handle), onSelect: () => (window.location.href = `tel:${dm.handle}`) },
                ]
              : [{ label: 'Group FaceTime', icon: 'video', note: 'Opens in the FaceTime app', disabled: true }]
          }
        />
      )}
      {more.anchor && (
        <Menu
          anchor={more.anchor}
          align="right"
          onClose={more.close}
          items={[
            c.unreadCount > 0
              ? { label: 'Mark as read', icon: 'check', onSelect: () => void markConversationRead(c.id) }
              : { label: 'Mark as unread', icon: 'dot', onSelect: () => void markUnread(c.id) },
            { divider: true, label: '' },
            { label: c.prefs.starred ? 'Unstar' : 'Star', icon: 'star', onSelect: () => toggleStar(c.id) },
            { label: c.prefs.muted ? 'Unmute' : 'Mute', icon: 'bellOff', onSelect: () => toggleMute(c.id) },
            { divider: true, label: '' },
            ...remindPresets().map((p) => ({
              label: `Snooze: ${p.label.toLowerCase()}`,
              icon: 'clock' as const,
              hint: undefined,
              onSelect: () => snooze(c.id, p.at.toISOString()),
            })),
            { divider: true, label: '' },
            { label: 'Search in this conversation', icon: 'search', onSelect: () => openSearch(c.id) },
            { label: dm ? 'View profile' : 'Conversation details', icon: 'users', onSelect: () => (dm ? openProfile(dm.handle, c.id) : openDetails(c.id)) },
          ]}
        />
      )}
    </header>
  )
}

function ScheduledBanner({ c }: { c: Conversation }) {
  const pending = useStore((s) => s.scheduled.filter((x) => x.conversationId === c.id && x.status === 'pending'))
  if (!pending.length) return null
  return (
    <button type="button" className="scheduled-banner" onClick={() => openView('scheduled')}>
      <Icon name="clock" size={15} />
      <span>
        {plural(pending.length, 'scheduled message')} · next sends {whenLabel(pending[0].sendAt)}
      </span>
      <span className="banner-link">View</span>
    </button>
  )
}

function MessageList({ c }: { c: Conversation }) {
  const ms = useStore((s) => s.messages[c.id])
  const mode = useStore((s) => s.settings.threadReplies)
  const marker = useStore((s) => s.newMarker[c.id] ?? null)
  const items = ms?.items ?? []
  const groups = useMemo(() => buildGroups(items, mode, marker, c.kind === 'group'), [items, mode, marker, c.kind])
  const scroller = useRef<HTMLDivElement>(null)
  const content = useRef<HTMLDivElement>(null)
  const atBottom = useRef(true)
  const prevCount = useRef(0)
  const prepend = useRef<{ height: number; top: number } | null>(null)
  const [jump, setJump] = useState(0)
  const loaded = !!ms && !ms.loading

  // In a busy conversation, the new messages that need you (asked, mentioned, time-sensitive…).
  const { forYou, freshCount } = useMemo(() => {
    const i = marker ? items.findIndex((m) => m.id === marker) : -1
    if (i < 0) return { forYou: [] as Message[], freshCount: 0 }
    const fresh = items.slice(i).filter((m) => !m.fromMe && !m.event)
    const important = fresh.filter((m) => m.importance?.some((r) => r.kind !== 'vip'))
    return { forYou: fresh.length >= 4 ? important : [], freshCount: fresh.length }
  }, [items, marker])
  const [forYouStep, setForYouStep] = useState(0)
  const [forYouHidden, setForYouHidden] = useState(false)
  useEffect(() => {
    setForYouStep(0)
    setForYouHidden(false)
  }, [c.id, marker])
  const showNextForYou = () => {
    const m = forYou[forYouStep % forYou.length]
    setForYouStep((n) => n + 1)
    if (m.threadRootId && mode === 'thread-only') void openThread(c.id, m.threadRootId)
    else setState({ highlight: { id: m.id, at: Date.now() } })
  }

  // Opening a conversation: land on the "New" line, or the bottom.
  useLayoutEffect(() => {
    const el = scroller.current
    if (!el || !loaded) return
    const newLine = el.querySelector<HTMLElement>('.new-divider')
    el.scrollTop = newLine ? Math.max(0, newLine.offsetTop - el.clientHeight / 3) : el.scrollHeight
    atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 90
    prevCount.current = items.length
    setJump(0)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [c.id, loaded])

  // New messages: follow along if already at the bottom, otherwise offer a jump button.
  useLayoutEffect(() => {
    const el = scroller.current
    if (!el || !loaded) return
    if (prepend.current) {
      el.scrollTop = el.scrollHeight - prepend.current.height + prepend.current.top
      prepend.current = null
      prevCount.current = items.length
      return
    }
    const added = items.length - prevCount.current
    prevCount.current = items.length
    if (added <= 0) return
    const last = items[items.length - 1]
    const fits = el.scrollHeight - el.clientHeight < 90
    if (fits || atBottom.current || last?.fromMe) {
      el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
      setJump(0)
    } else {
      setJump((n) => n + added)
    }
  }, [items, loaded])

  // Stay at the bottom when things change size in place: a new reaction, a photo finishing
  // loading, or the composer growing (which shrinks the list). When the list changes width (the
  // thread pane opening or closing, the window resizing), set the position again either way, now
  // and on the next frame: see settleScroll.
  useEffect(() => {
    const el = scroller.current
    const inner = content.current
    if (!el || !inner || !loaded || typeof ResizeObserver === 'undefined') return
    let width = el.clientWidth
    let frame = 0
    const settle = () => settleScroll(el, atBottom.current ? el.scrollHeight : el.scrollTop)
    const ro = new ResizeObserver(() => {
      const widthChanged = el.clientWidth !== width
      width = el.clientWidth
      if (prepend.current) return
      if (widthChanged) {
        settle()
        cancelAnimationFrame(frame)
        frame = requestAnimationFrame(settle)
      } else if (atBottom.current) {
        el.scrollTop = el.scrollHeight
      }
    })
    ro.observe(inner)
    ro.observe(el)
    return () => {
      ro.disconnect()
      cancelAnimationFrame(frame)
    }
  }, [c.id, loaded])

  const onScroll = () => {
    const el = scroller.current
    if (!el) return
    atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 90
    if (atBottom.current && jump) setJump(0)
    if (el.scrollTop < 240 && ms?.nextCursor && !ms.loadingOlder) {
      prepend.current = { height: el.scrollHeight, top: el.scrollTop }
      void loadOlder(c.id)
    }
  }

  if (!ms || ms.loading) {
    return (
      <div className="msg-list is-loading">
        <Spinner label="Loading messages" />
      </div>
    )
  }
  if (ms.error) {
    return (
      <div className="msg-list is-empty">
        <p className="empty-title">Couldn’t load this conversation</p>
        <p className="empty-sub">{ms.error}</p>
      </div>
    )
  }
  return (
    <div className="msg-list-wrap">
      <div className="msg-list" ref={scroller} onScroll={onScroll}>
        <div ref={content}>
          {ms.loadingOlder && (
            <div className="older-loading">
              <Spinner label="Loading older messages" />
            </div>
          )}
          {!ms.nextCursor && <ConversationIntro c={c} />}
          {groups.map((g) => (
            <section key={g.key} className="day-group" aria-label={g.label}>
              <div className="day-divider" role="separator">
                <span>{g.label}</span>
              </div>
              {g.rows.map((r) =>
                r.type === 'new' ? (
                  <div key={r.key} className="new-divider" role="separator">
                    <span>New</span>
                  </div>
                ) : (
                  <MessageRow key={r.key} m={r.m} opts={r.opts} />
                ),
              )}
            </section>
          ))}
          <div className="msg-list-end" />
        </div>
      </div>
      {forYou.length > 0 && !forYouHidden && (
        <div className="for-you" role="status">
          <button type="button" className="for-you-pill" onClick={showNextForYou} title="Jump to the messages that need you">
            <Icon name="at" size={14} strokeWidth={2.2} />
            {forYou.length} for you in {freshCount} new
            <span className="for-you-go">{forYouStep === 0 ? 'Show' : forYou.length > 1 ? 'Next' : 'Again'}</span>
          </button>
          <button type="button" className="for-you-x" aria-label="Hide" onClick={() => setForYouHidden(true)}>
            <Icon name="x" size={13} />
          </button>
        </div>
      )}
      {jump > 0 && (
        <button
          type="button"
          className="jump-latest"
          onClick={() => {
            scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: 'smooth' })
            setJump(0)
          }}
        >
          <Icon name="arrowDown" size={15} /> {plural(jump, 'new message')}
        </button>
      )}
    </div>
  )
}

/** Scroll the list to `top` so it's really drawn there. When the list gets wider (closing the
 *  thread pane), the chat gets shorter and the old spot can be past its end. The Mac app's web view
 *  can keep showing that spot, an empty list, until you scroll. Setting the spot the list already
 *  reports does nothing there, so step a pixel off it first. */
function settleScroll(el: HTMLElement, top: number) {
  const max = el.scrollHeight - el.clientHeight
  if (max <= 0) return
  const target = Math.max(0, Math.min(Math.round(top), max))
  el.scrollTop = target > 0 ? target - 1 : 1
  el.scrollTop = target
}

function ConversationIntro({ c }: { c: Conversation }) {
  const dm = c.kind === 'dm' ? c.participants[0] : null
  return (
    <div className="convo-intro">
      <ConversationGlyph c={c} size={dm ? 72 : 56} />
      <h2>{c.title}</h2>
      <p>
        {dm
          ? `This is the start of your conversation with ${dm.name}.`
          : c.isNamed
            ? `This is the start of ${c.title.startsWith('#') ? '' : '#'}${c.title}. ${plural(c.participants.length + 1, 'person', 'people')} are in this group.`
            : `This is the start of your group conversation with ${c.participants.map((p) => p.shortName).join(', ')}.`}
      </p>
    </div>
  )
}

function MediaTab({ c }: { c: Conversation }) {
  const items = useStore((s) => s.media[c.id])
  useEffect(() => {
    void loadMedia(c.id)
  }, [c.id])
  if (!items) {
    return (
      <div className="media-tab is-loading">
        <Spinner />
      </div>
    )
  }
  const visual = items.filter((i) => i.attachment.kind === 'image' || i.attachment.kind === 'video')
  const files = items.filter((i) => i.attachment.kind !== 'image' && i.attachment.kind !== 'video')
  return (
    <div className="media-tab">
      {items.length === 0 && (
        <div className="msg-list is-empty">
          <p className="empty-title">No photos or files yet</p>
          <p className="empty-sub">Anything shared in {c.title} shows up here.</p>
        </div>
      )}
      {visual.length > 0 && (
        <>
          <h3 className="media-heading">Photos & videos</h3>
          <div className="media-grid">
            {visual.map((i) => (
              <a key={i.attachment.id} className="media-cell" href={i.attachment.url} target="_blank" rel="noreferrer" title={i.attachment.name}>
                <img
                  src={i.attachment.thumbUrl ?? i.attachment.url}
                  alt={i.attachment.name}
                  loading="lazy"
                  onError={(e) => ((e.currentTarget as HTMLImageElement).style.visibility = 'hidden')}
                />
                {i.attachment.kind === 'video' && (
                  <span className="media-play is-small">
                    <Icon name="play" size={14} filled />
                  </span>
                )}
              </a>
            ))}
          </div>
        </>
      )}
      {files.length > 0 && (
        <>
          <h3 className="media-heading">Files</h3>
          <ul className="file-list">
            {files.map((i) => (
              <li key={i.attachment.id}>
                <Icon name={i.attachment.kind === 'audio' ? 'mic' : 'file'} size={18} />
                <span className="file-name">{i.attachment.name}</span>
                <span className="file-sub">{fileSize(i.attachment.size)}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}
