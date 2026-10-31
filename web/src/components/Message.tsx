import { memo, useEffect, useMemo, useRef, useState } from 'react'
import type { Attachment, LinkPreview, Message } from '../api/types'
import { ageLong, fileSize, fullTimestamp, isEmojiOnly, segmentText, timeShort } from '../lib/format'
import { Icon } from '../lib/icons'
import { isClassic } from '../lib/tapbacks'
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
  sendTapback,
  shownReactions,
  toast,
  useStore,
} from '../state/store'
import { Avatar, Menu, myNames, remindPresets, useMenu } from './common'
import { Lightbox } from './Lightbox'
import { TapbackPicker } from './TapbackPicker'
import { ReasonChips } from './Priority'

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
  // Why it needs you. In a DM "asked you" goes without saying, so only time and urgency show there.
  const reasons = !m.fromMe && m.importance ? m.importance.filter((r) => r.kind !== 'vip' && (opts.isGroup || (r.kind !== 'asked' && r.kind !== 'mention'))) : []
  const urgent = reasons.some((r) => r.kind === 'urgent' || r.kind === 'repeat')
  return (
    <div
      ref={ref}
      className={`msg ${head ? 'is-head' : 'is-cont'} ${m.status === 'sending' ? 'is-sending' : ''} ${reasons.length ? 'is-important' : ''} ${urgent ? 'is-urgent' : ''}`}
      data-mid={m.id}
    >
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
        {reasons.length > 0 && <ReasonChips reasons={reasons} />}
        <Reactions m={m} />
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

const SHORTCODE_RE = /:([a-z0-9_+-]{2,40}):/g

/** Plain text with your custom :name: codes drawn as their images. */
function TextWithEmoji({ value, emoji }: { value: string; emoji: Map<string, string> }) {
  if (!emoji.size || !value.includes(':')) return <span>{value}</span>
  const parts: React.ReactNode[] = []
  let last = 0
  for (const m of value.matchAll(SHORTCODE_RE)) {
    const url = emoji.get(m[1].toLowerCase())
    if (!url) continue
    const at = m.index ?? 0
    if (at > last) parts.push(value.slice(last, at))
    parts.push(<img key={at} className="custom-emoji" src={url} alt={m[0]} title={m[0]} />)
    last = at + m[0].length
  }
  if (!parts.length) return <span>{value}</span>
  if (last < value.length) parts.push(value.slice(last))
  return <span>{parts}</span>
}

export function MessageContent({ m }: { m: Message }) {
  const customEmoji = useStore((s) => s.customEmoji)
  const emojiMap = useMemo(() => new Map(customEmoji.map((e) => [e.name, e.url])), [customEmoji])
  if (m.unsent) return <p className="msg-text is-unsent">This message was unsent.</p>
  const people = getState().people
  const names = Object.values(people).map((p) => p.shortName)
  // Genmoji are emoji, not photos: draw them inline at text size (big when sent alone).
  const genmoji = m.attachments.filter((a) => a.isGenmoji && !a.missing)
  const others = m.attachments.filter((a) => !(a.isGenmoji && !a.missing))
  const jumbo = others.length === 0 && (m.text ? isEmojiOnly(m.text) && genmoji.length === 0 : genmoji.length > 0)
  const genmojiImgs = genmoji.map((a) => (
    <img
      key={a.id}
      className="genmoji"
      src={a.thumbUrl ?? a.url}
      alt={a.emojiDescription ?? 'Genmoji'}
      title={a.emojiDescription ?? 'Genmoji'}
      onError={(e) => ((e.currentTarget as HTMLImageElement).style.display = 'none')}
    />
  ))
  return (
    <>
      {(m.text || genmoji.length > 0) && (
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
              <TextWithEmoji key={i} value={seg.value} emoji={emojiMap} />
            ),
          )}
          {m.text && genmoji.length > 0 ? ' ' : null}
          {genmojiImgs}
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
      {others.length > 0 && (
        <div className="attachments">
          {others.map((a) => (
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
  if (a.missing) {
    return (
      <div className="file-card is-missing" title="Messages keeps older attachments in iCloud and downloads them when you view them there.">
        <Icon name="cloud" size={20} />
        <span className="file-meta">
          <span className="file-name">{a.kind === 'image' ? 'Photo in iCloud' : a.kind === 'video' ? 'Video in iCloud' : a.name}</span>
          <span className="file-sub">Not on this Mac yet. Opening this conversation in Messages downloads it.</span>
        </span>
      </div>
    )
  }
  if (a.kind === 'image' || a.kind === 'video') return <MediaThumb a={a} />
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

/** Scale (w, h) down to fit a max box, never up. */
function fitBox(w: number, h: number, max: number) {
  const cap = Math.min(max, Math.round(window.innerWidth * 0.72))
  const scale = Math.min(1, cap / w, max / h)
  return { width: Math.max(1, Math.round(w * scale)), height: Math.max(1, Math.round(h * scale)) }
}

/** A photo or video preview. Its size comes from the loaded image itself (the server's numbers are
 *  only a first guess), so a wrong guess never crops a photo, and images without a built-in size
 *  (SVG) still show up. */
function MediaThumb({ a }: { a: Attachment }) {
  const [open, setOpen] = useState(false)
  const [broken, setBroken] = useState(false)
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null)
  const src = a.thumbUrl ?? (a.kind === 'image' ? a.url : null)
  const base = natural ?? (a.width && a.height ? { w: a.width, h: a.height } : { w: 4, h: 3 })
  const box = fitBox(base.w, base.h, a.isSticker ? 140 : 360)
  const lightbox = open && <Lightbox a={a} fallbackSrc={src} onClose={() => setOpen(false)} />
  if (a.kind === 'image' && (broken || !src)) {
    return (
      <a className="file-card" href={a.url} target="_blank" rel="noreferrer noopener">
        <span className="file-icon is-photo">
          <Icon name="image" size={20} />
        </span>
        <span className="file-meta">
          <span className="file-name">{a.name}</span>
          <span className="file-sub">Can’t preview this photo here. Click to open it.</span>
        </span>
        <Icon name="external" size={16} />
      </a>
    )
  }
  return (
    <>
      <button
        type="button"
        className={`media-thumb ${a.isSticker ? 'is-sticker' : ''} ${a.kind === 'video' ? 'is-video' : ''} ${broken || !src ? 'is-placeholder' : ''}`}
        onClick={() => setOpen(true)}
        aria-label={`Open ${a.name}`}
      >
        {src && !broken ? (
          <img
            src={src}
            alt={a.name}
            loading="lazy"
            draggable={false}
            style={box}
            onLoad={(e) => {
              const i = e.currentTarget
              if (i.naturalWidth && i.naturalHeight) setNatural({ w: i.naturalWidth, h: i.naturalHeight })
            }}
            onError={() => setBroken(true)}
          />
        ) : (
          <span className="media-placeholder">{a.name}</span>
        )}
        {a.kind === 'video' && (
          <span className="media-play">
            <Icon name="play" size={22} filled />
          </span>
        )}
      </button>
      {lightbox}
    </>
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
      {p.imageUrl && (
        <img className="unfurl-img" src={p.imageUrl} alt="" loading="lazy" onError={(e) => ((e.currentTarget as HTMLImageElement).style.display = 'none')} />
      )}
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

/** Reaction pills. Click one of the six tapbacks to add or remove yours (when tapbacks are on). */
function Reactions({ m }: { m: Message }) {
  const pending = useStore((s) => s.pendingTapbacks[m.id])
  const on = useStore((s) => !!s.serverSettings?.tapbacksViaMessages)
  const [picker, setPicker] = useState<HTMLElement | null>(null)
  const reactions = shownReactions(m, pending)
  if (!reactions.length) return null
  const canReact = !m.event && !m.unsent && !m.id.startsWith('local:')
  return (
    <div className="reactions">
      {reactions.map((r, i) => {
        const mine = r.from.includes('me')
        const who = reactorNames(r.from)
        const label = `${who.charAt(0).toUpperCase() + who.slice(1)} ${TAPBACK_VERB[r.kind] ?? 'reacted'}${r.kind === 'emoji' ? ` with ${r.emoji}` : ' this'}`
        const isPending = !!pending && mine && r.kind === pending.kind && !pending.remove
        const face =
          r.kind === 'sticker' && r.stickerUrl ? (
            <img className="reaction-sticker" src={r.stickerUrl} alt="" />
          ) : (
            <span className="reaction-emoji">{r.emoji}</span>
          )
        const count = <span className="reaction-count">{r.from.length}</span>
        const kind = r.kind
        if (!canReact || !isClassic(kind)) {
          return (
            <span key={i} className={`reaction ${mine ? 'is-mine' : ''}`} title={label} aria-label={label}>
              {face}
              {count}
            </span>
          )
        }
        const action = !on ? 'Turn on tapbacks to react' : mine ? 'Click to remove yours' : 'Click to react too'
        return (
          <button
            key={i}
            type="button"
            className={`reaction is-button ${mine ? 'is-mine' : ''} ${isPending ? 'is-pending' : ''}`}
            title={`${label}. ${action}`}
            aria-label={`${label}. ${action}`}
            aria-pressed={mine}
            onClick={(e) => (on ? void sendTapback(m, kind) : setPicker(e.currentTarget))}
          >
            {face}
            {count}
          </button>
        )
      })}
      {canReact && (
        <button type="button" className="reaction is-button is-add" title="Add a tapback" aria-label="Add a tapback" onClick={(e) => setPicker(picker ? null : e.currentTarget)}>
          <Icon name="smilePlus" size={15} />
        </button>
      )}
      {picker && <TapbackPicker m={m} anchor={picker} onClose={() => setPicker(null)} />}
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
  const realReplies = useStore((s) => !!s.serverSettings?.threadRepliesViaMessages)
  const reason = useStore((s) => s.failReasons[m.id])
  if (m.status === 'sending') return <p className="msg-status">Sending…</p>
  // A real reply that Messages couldn't take: nothing was sent. Retry, or send it as a regular text.
  if (m.status === 'failed' && m.threadRootId && realReplies)
    return (
      <p className="msg-status is-failed">
        <span className="msg-status-why">
          <Icon name="alert" size={14} /> {reason ?? 'Not sent as a reply.'}
        </span>
        <button type="button" className="link-btn" onClick={() => retrySend(m.conversationId, m.id)}>
          Try again
        </button>
        <span className="msg-status-sep">·</span>
        <button type="button" className="link-btn" onClick={() => retrySend(m.conversationId, m.id, { regularText: true })}>
          Send as a regular text
        </button>
      </p>
    )
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
  const react = useMenu()
  const tapbacks = useStore((s) => !!s.serverSettings?.tapbacksViaMessages)
  const open = !!remind.anchor || !!more.anchor || !!noReply.anchor || !!react.anchor
  return (
    <div className={`hover-bar ${open ? 'is-open' : ''}`} role="toolbar" aria-label="Message actions">
      <button
        type="button"
        className={`icon-btn ${react.anchor ? 'is-active' : ''}`}
        title={tapbacks ? 'Tapback' : 'Tapback (needs turning on)'}
        aria-label="Tapback"
        aria-expanded={!!react.anchor}
        onClick={react.open}
      >
        <Icon name="smilePlus" size={18} />
      </button>
      {react.anchor && <TapbackPicker m={m} anchor={react.anchor} onClose={react.close} />}
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
