import { memo, useEffect, useRef, useState } from 'react'
import type { Attachment, LinkPreview, Message, Reaction } from '../api/types'
import { ageLong, fileSize, fullTimestamp, isEmojiOnly, segmentText, timeShort } from '../lib/format'
import { Icon } from '../lib/icons'
import {
  chiefAvailable,
  displayName,
  getState,
  openChiefDraft,
  openProfile,
  openThread,
  personFor,
  remindIfNoReply,
  retrySend,
  saveForLater,
  toast,
  useStore,
} from '../state/store'
import { Avatar, Menu, Modal, myNames, remindPresets, useMenu } from './common'

export interface RowOptions {
  /** First message of a run from the same sender: show avatar + name. */
  head: boolean
  /** Show "replied to a thread" context above an inline reply. */
  inlineReplyContext?: Message | null
  /** In the thread pane: no "reply in thread" action, no thread summary. */
  inThread?: boolean
  /** Status line under the user's latest message. */
  showStatus?: boolean
  isGroup?: boolean
}

export const MessageRow = memo(function MessageRow({ m, opts }: { m: Message; opts: RowOptions }) {
  const highlight = useStore((s) => (s.highlight?.id === m.id ? s.highlight.at : 0))
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!highlight || !ref.current) return
    ref.current.scrollIntoView({ block: 'center', behavior: 'smooth' })
    ref.current.classList.remove('is-flash')
    void ref.current.offsetWidth
    ref.current.classList.add('is-flash')
  }, [highlight])

  if (m.event) return <EventRow m={m} />

  const sender = m.fromMe ? null : personFor(m.senderHandle)
  const name = displayName(m.senderHandle)
  const head = opts.head || !!opts.inlineReplyContext
  return (
    <div ref={ref} className={`msg ${head ? 'is-head' : 'is-cont'} ${m.status === 'sending' ? 'is-sending' : ''}`} data-mid={m.id}>
      <div className="msg-gutter">
        {head ? (
          m.fromMe ? (
            <Avatar name={getState().me?.name ?? 'You'} size={36} />
          ) : (
            <Avatar person={sender} name={name} size={36} onClick={() => m.senderHandle && openProfile(m.senderHandle, m.conversationId)} />
          )
        ) : (
          <time className="msg-hover-time" dateTime={m.sentAt} title={fullTimestamp(m.sentAt)}>
            {timeShort(m.sentAt).replace(/\s?[AP]M$/i, '')}
          </time>
        )}
      </div>
      <div className="msg-body">
        {opts.inlineReplyContext && (
          <button type="button" className="msg-context" onClick={() => openThread(m.conversationId, opts.inlineReplyContext!.id)}>
            <Icon name="reply" size={13} /> replied to a thread: <span>{excerpt(opts.inlineReplyContext.text || 'a photo', 70)}</span>
          </button>
        )}
        {head && (
          <div className="msg-meta">
            {m.fromMe ? (
              <span className="msg-name">{getState().me?.name ?? 'You'}</span>
            ) : (
              <button type="button" className="msg-name" onClick={() => m.senderHandle && openProfile(m.senderHandle, m.conversationId)}>
                {name}
              </button>
            )}
            {!m.fromMe && sender && !sender.isContact && opts.isGroup && <span className="msg-tag">Not in contacts</span>}
            <time className="msg-time" dateTime={m.sentAt} title={fullTimestamp(m.sentAt)}>
              {timeShort(m.sentAt)}
            </time>
            {m.service === 'SMS' && <span className="msg-tag is-sms">SMS</span>}
          </div>
        )}
        <MessageContent m={m} />
        {m.reactions.length > 0 && <Reactions reactions={m.reactions} />}
        {!opts.inThread && m.thread && <ThreadSummary m={m} />}
        {opts.showStatus && m.fromMe && <StatusLine m={m} />}
      </div>
      {m.status !== 'sending' && !m.unsent && <HoverBar m={m} inThread={!!opts.inThread} />}
    </div>
  )
})

function excerpt(t: string, n: number) {
  return t.length > n ? t.slice(0, n - 1) + '…' : t
}

function EventRow({ m }: { m: Message }) {
  return (
    <div className="msg msg-event" data-mid={m.id}>
      <div className="msg-gutter">
        <span className="event-icon">
          <Icon name={m.event?.type === 'renamed' ? 'edit' : m.event?.type === 'added' ? 'users' : 'dot'} size={14} />
        </span>
      </div>
      <div className="msg-body">
        <span className="event-text">{m.event?.text}</span>
        <time className="msg-time" dateTime={m.sentAt} title={fullTimestamp(m.sentAt)}>
          {timeShort(m.sentAt)}
        </time>
      </div>
    </div>
  )
}

export function MessageContent({ m }: { m: Message }) {
  if (m.unsent) return <p className="msg-text is-unsent">This message was unsent.</p>
  const people = getState().people
  const names = Object.values(people).map((p) => p.shortName)
  const jumbo = m.attachments.length === 0 && isEmojiOnly(m.text)
  return (
    <>
      {m.text && (
        <p className={`msg-text ${jumbo ? 'is-jumbo' : ''} ${m.mentionsMe ? 'mentions-me' : ''}`}>
          {segmentText(m.text, names, myNames()).map((seg, i) =>
            seg.kind === 'link' ? (
              <a key={i} href={seg.href} target="_blank" rel="noreferrer noopener">
                {seg.value}
              </a>
            ) : seg.kind === 'mention' ? (
              <span key={i} className={`mention ${seg.me ? 'is-me' : ''}`}>
                {seg.value}
              </span>
            ) : (
              <span key={i}>{seg.value}</span>
            ),
          )}
          {m.editedAt && (
            <span className="msg-edited" title={`Edited ${fullTimestamp(m.editedAt)}`}>
              {' '}
              (edited)
            </span>
          )}
        </p>
      )}
      {m.effect && <p className="msg-effect">sent with {m.effect}</p>}
      {m.otpCode && <OtpChip code={m.otpCode} />}
      {m.attachments.length > 0 && (
        <div className="attachments">
          {m.attachments.map((a) => (
            <AttachmentView key={a.id} a={a} isAudioMessage={m.isAudioMessage} />
          ))}
        </div>
      )}
      {m.linkPreview && <Unfurl p={m.linkPreview} />}
    </>
  )
}

function OtpChip({ code }: { code: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      type="button"
      className="otp-chip"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(code)
          setCopied(true)
          setTimeout(() => setCopied(false), 1600)
        } catch {
          toast(`Copy didn't work here. The code is ${code}.`)
        }
      }}
    >
      <Icon name={copied ? 'check' : 'key'} size={15} />
      {copied ? 'Copied' : `Copy code ${code}`}
    </button>
  )
}

function AttachmentView({ a, isAudioMessage }: { a: Attachment; isAudioMessage: boolean }) {
  const [open, setOpen] = useState(false)
  if (a.missing) {
    return (
      <div className="file-card is-missing">
        <Icon name="alert" size={20} />
        <span className="file-meta">
          <span className="file-name">{a.name}</span>
          <span className="file-sub">Not downloaded on this Mac. Open it on your phone, or turn on “Download attachments” in Messages.</span>
        </span>
      </div>
    )
  }
  if (a.kind === 'image' || a.kind === 'video') {
    const src = a.thumbUrl ?? a.url
    const ratio = a.width && a.height ? a.width / a.height : 4 / 3
    const w = ratio >= 1 ? 360 : Math.round(300 * ratio)
    return (
      <>
        <button type="button" className={`media-thumb ${a.isSticker ? 'is-sticker' : ''}`} onClick={() => setOpen(true)} style={{ width: w, aspectRatio: String(ratio) }} aria-label={`Open ${a.name}`}>
          <img src={src} alt={a.name} loading="lazy" draggable={false} />
          {a.kind === 'video' && (
            <span className="media-play">
              <Icon name="play" size={22} filled />
            </span>
          )}
        </button>
        {open && (
          <Modal onClose={() => setOpen(false)} label={a.name} className="lightbox">
            <div className="lightbox-bar">
              <span>{a.name}</span>
              <span className="lightbox-size">{fileSize(a.size)}</span>
              <a className="icon-btn" href={a.url} target="_blank" rel="noreferrer" aria-label="Open original" title="Open original">
                <Icon name="external" size={18} />
              </a>
              <button type="button" className="icon-btn" onClick={() => setOpen(false)} aria-label="Close">
                <Icon name="x" size={18} />
              </button>
            </div>
            {a.kind === 'video' && !a.url.startsWith('data:') && a.url !== '#' ? (
              <video src={a.url} controls autoPlay className="lightbox-media" />
            ) : (
              <img src={a.url !== '#' ? a.url : src} alt={a.name} className="lightbox-media" />
            )}
          </Modal>
        )}
      </>
    )
  }
  if (a.kind === 'audio') return <AudioRow a={a} voice={isAudioMessage} />
  return (
    <a className="file-card" href={a.url} target="_blank" rel="noreferrer noopener">
      <span className="file-icon">
        <Icon name="file" size={20} />
        <span>{(a.name.split('.').pop() ?? 'file').slice(0, 4).toUpperCase()}</span>
      </span>
      <span className="file-meta">
        <span className="file-name">{a.name}</span>
        <span className="file-sub">{fileSize(a.size)}</span>
      </span>
      <Icon name="download" size={16} />
    </a>
  )
}

const BARS = [5, 9, 14, 8, 12, 18, 10, 6, 14, 20, 12, 7, 11, 16, 9, 5, 12, 17, 10, 6, 9, 13, 8, 5]

function AudioRow({ a, voice }: { a: Attachment; voice: boolean }) {
  const [playing, setPlaying] = useState(false)
  const [progress, setProgress] = useState(0)
  const audio = useRef<HTMLAudioElement | null>(null)
  const timer = useRef<number>(0)
  const playable = a.url !== '#' && !a.url.startsWith('data:')
  const toggle = () => {
    if (playable) {
      if (!audio.current) {
        audio.current = new Audio(a.url.includes('?') ? a.url : `${a.url}?format=m4a`)
        audio.current.ontimeupdate = () => setProgress(audio.current!.currentTime / (audio.current!.duration || 1))
        audio.current.onended = () => setPlaying(false)
      }
      if (playing) audio.current.pause()
      else void audio.current.play().catch(() => toast("This browser can't play that audio file. Safari usually can."))
      setPlaying(!playing)
      return
    }
    // Mock: animate a 14 second voice memo.
    if (playing) {
      window.clearInterval(timer.current)
      setPlaying(false)
      return
    }
    setPlaying(true)
    timer.current = window.setInterval(() => {
      setProgress((p) => {
        if (p >= 1) {
          window.clearInterval(timer.current)
          setPlaying(false)
          return 0
        }
        return p + 1 / 70
      })
    }, 200)
  }
  useEffect(() => () => window.clearInterval(timer.current), [])
  return (
    <div className="audio-row">
      <button type="button" className="audio-play" onClick={toggle} aria-label={playing ? 'Pause' : 'Play'}>
        <Icon name={playing ? 'pause' : 'play'} size={16} filled={!playing} strokeWidth={playing ? 2.4 : 1.8} />
      </button>
      <span className="audio-wave" aria-hidden="true">
        {BARS.map((h, i) => (
          <span key={i} style={{ height: h }} className={i / BARS.length <= progress ? 'is-played' : ''} />
        ))}
      </span>
      <span className="audio-meta">{voice ? 'Voice memo' : a.name} · 0:14</span>
    </div>
  )
}

function Unfurl({ p }: { p: LinkPreview }) {
  return (
    <div className="unfurl">
      <div className="unfurl-text">
        {p.siteName && <span className="unfurl-site">{p.siteName}</span>}
        <a className="unfurl-title" href={p.url} target="_blank" rel="noreferrer noopener">
          {p.title ?? p.url}
        </a>
        {p.summary && <span className="unfurl-summary">{p.summary}</span>}
      </div>
      {p.imageUrl && <img className="unfurl-img" src={p.imageUrl} alt="" loading="lazy" />}
    </div>
  )
}

function reactorNames(from: string[]) {
  const names = from.map((h) => (h === 'me' ? 'you' : displayName(h, true)))
  if (names.length <= 1) return names[0] ?? ''
  return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`
}

const TAPBACK_VERB: Record<string, string> = {
  love: 'loved',
  like: 'liked',
  dislike: 'disliked',
  laugh: 'laughed at',
  emphasize: 'emphasized',
  question: 'questioned',
  emoji: 'reacted',
  sticker: 'stickered',
}

function Reactions({ reactions }: { reactions: Reaction[] }) {
  return (
    <div className="reactions">
      {reactions.map((r, i) => {
        const mine = r.from.includes('me')
        const who = reactorNames(r.from)
        const label = `${who.charAt(0).toUpperCase() + who.slice(1)} ${TAPBACK_VERB[r.kind] ?? 'reacted'}${r.kind === 'emoji' ? ` with ${r.emoji}` : ' this'}`
        return (
          <span key={i} className={`reaction ${mine ? 'is-mine' : ''}`} title={label} aria-label={label}>
            {r.kind === 'sticker' && r.stickerUrl ? (
              <img className="reaction-sticker" src={r.stickerUrl} alt="" />
            ) : (
              <span className="reaction-emoji">{r.emoji}</span>
            )}
            <span className="reaction-count">{r.from.length}</span>
          </span>
        )
      })}
    </div>
  )
}

function ThreadSummary({ m }: { m: Message }) {
  const t = m.thread!
  return (
    <button type="button" className="thread-summary" onClick={() => openThread(m.conversationId, m.id)}>
      <span className="thread-avatars">
        {t.participants.slice(0, 4).map((h) => (
          <Avatar key={h} person={h === 'me' ? null : personFor(h)} name={displayName(h)} size={24} radius={5} />
        ))}
      </span>
      <span className="thread-count">
        {t.replyCount} {t.replyCount === 1 ? 'reply' : 'replies'}
      </span>
      <span className="thread-last">Last reply {ageLong(t.lastReplyAt)}</span>
      <span className="thread-view">
        View thread <Icon name="chevronRight" size={14} />
      </span>
    </button>
  )
}

function StatusLine({ m }: { m: Message }) {
  if (m.status === 'sending') return <p className="msg-status">Sending…</p>
  if (m.status === 'failed')
    return (
      <p className="msg-status is-failed">
        <Icon name="alert" size={14} /> Not delivered.{' '}
        <button type="button" className="link-btn" onClick={() => retrySend(m.conversationId, m.id)}>
          Try again
        </button>
      </p>
    )
  if (m.status === 'read') return <p className="msg-status">Read {m.readAt ? timeShort(m.readAt) : ''}</p>
  if (m.status === 'delivered') return <p className="msg-status">Delivered</p>
  if (m.status === 'sent') return <p className="msg-status">{m.service === 'SMS' ? 'Sent as text message' : 'Sent'}</p>
  return null
}

/** "Remind me if they don't reply" choices. */
function noReplyPresets(now = new Date()) {
  const at = (days: number) => {
    const d = new Date(now)
    d.setDate(now.getDate() + days)
    d.setHours(9, 0, 0, 0)
    return d
  }
  return [
    { label: 'Tomorrow morning', at: at(1) },
    { label: 'In 2 days', at: at(2) },
    { label: 'In a week', at: at(7) },
  ]
}

function HoverBar({ m, inThread }: { m: Message; inThread: boolean }) {
  const remind = useMenu()
  const more = useMenu()
  const noReply = useMenu()
  const open = !!remind.anchor || !!more.anchor || !!noReply.anchor
  return (
    <div className={`hover-bar ${open ? 'is-open' : ''}`} role="toolbar" aria-label="Message actions">
      <button
        type="button"
        className="icon-btn"
        title="Add reaction"
        aria-label="Add reaction"
        onClick={() => toast('Tapbacks can’t be sent from Backchannel yet. iMessage only accepts them from Messages.app.')}
      >
        <Icon name="smilePlus" size={18} />
      </button>
      {!inThread && (
        <button type="button" className="icon-btn" title="Reply in thread" aria-label="Reply in thread" onClick={() => openThread(m.conversationId, m.threadRootId ?? m.id)}>
          <Icon name="thread" size={18} />
        </button>
      )}
      <button type="button" className="icon-btn" title="Remind me later" aria-label="Remind me later" onClick={remind.open}>
        <Icon name="clock" size={18} />
      </button>
      <button
        type="button"
        className="icon-btn"
        title="Copy text"
        aria-label="Copy text"
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(m.text)
            toast('Copied')
          } catch {
            toast("Copy didn't work here. Select the text instead.")
          }
        }}
      >
        <Icon name="copy" size={17} />
      </button>
      <button type="button" className="icon-btn" title="More actions" aria-label="More actions" onClick={more.open}>
        <Icon name="moreV" size={18} />
      </button>
      {noReply.anchor && (
        <Menu
          anchor={noReply.anchor}
          align="right"
          onClose={noReply.close}
          header={<div className="menu-header">Nudge me if they don’t reply by</div>}
          items={noReplyPresets().map((p) => ({
            label: p.label,
            hint: p.at.toLocaleDateString(undefined, { weekday: 'short' }) + ' ' + timeShort(p.at.toISOString()),
            onSelect: () => remindIfNoReply(m.conversationId, m.id, p.at.toISOString()),
          }))}
        />
      )}
      {remind.anchor && (
        <Menu
          anchor={remind.anchor}
          align="right"
          onClose={remind.close}
          header={<div className="menu-header">Remind me about this</div>}
          items={[
            ...remindPresets().map((p) => ({
              label: p.label,
              hint: timeShort(p.at.toISOString()),
              onSelect: () => saveForLater(m.conversationId, m.id, p.at.toISOString()),
            })),
            { divider: true, label: '' },
            { label: 'Save without a reminder', icon: 'bookmark' as const, onSelect: () => saveForLater(m.conversationId, m.id, null) },
          ]}
        />
      )}
      {more.anchor && (
        <Menu
          anchor={more.anchor}
          align="right"
          onClose={more.close}
          items={[
            ...(chiefAvailable()
              ? [{ label: 'Add to Chief loops', icon: 'listChecks' as const, note: 'Track it as an open loop in Chief', onSelect: () => openChiefDraft(m) }]
              : []),
            ...(m.fromMe
              ? [
                  {
                    label: 'Remind me if no reply…',
                    icon: 'bell' as const,
                    note: 'A nudge only if they go quiet',
                    onSelect: () => {
                      // Reopen as the time picker, anchored where the More menu was.
                      const anchor = more.anchor
                      setTimeout(() => anchor && noReply.open({ currentTarget: anchor } as React.MouseEvent<HTMLElement>), 0)
                    },
                  },
                ]
              : []),
            ...(m.senderHandle
              ? [
                  { divider: true, label: '' },
                  { label: `View ${displayName(m.senderHandle, true)}’s profile`, icon: 'users' as const, onSelect: () => openProfile(m.senderHandle!, m.conversationId) },
                ]
              : []),
          ]}
        />
      )}
    </div>
  )
}
