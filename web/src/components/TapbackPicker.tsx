// Tapbacks, sent by operating the Messages app (Settings › Messages helpers): the six classics, the
// emoji Messages offers as quick tapbacks (its recent ones), and any other emoji from the full picker.
import { type ReactNode, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { Message } from '../api/types'
import { Icon } from '../lib/icons'
import { myTapback, sameChoice, TAPBACKS, type TapbackChoice } from '../lib/tapbacks'
import { sendTapback, setServerSetting, shownReactions, useStore } from '../state/store'
import { EmojiPicker } from './EmojiPicker'

export function TapbackPicker({ m, anchor, onClose }: { m: Message; anchor: HTMLElement; onClose: () => void }) {
  const on = useStore((s) => !!s.serverSettings?.tapbacksViaMessages)
  const loaded = useStore((s) => !!s.serverSettings)
  const quick = useStore((s) => s.serverSettings?.tapbackQuick ?? [])
  const pending = useStore((s) => s.pendingTapbacks[m.id])
  const mine = myTapback(shownReactions(m, pending))
  const ref = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)
  // "More…": the full emoji picker takes this one's place (same anchor), emoji only.
  const [more, setMore] = useState(false)

  useLayoutEffect(() => {
    if (!ref.current || more) return
    const r = anchor.getBoundingClientRect()
    const p = ref.current.getBoundingClientRect()
    const left = Math.max(8, Math.min(r.right - p.width, window.innerWidth - p.width - 8))
    let top = r.bottom + 6
    if (top + p.height > window.innerHeight - 8) top = Math.max(8, r.top - p.height - 6)
    setPos({ top, left })
  }, [anchor, on, more, quick.length])

  useEffect(() => {
    if (more) return // the emoji picker handles its own outside clicks and Escape
    const onDown = (e: MouseEvent) => {
      if (ref.current?.contains(e.target as Node) || anchor.contains(e.target as Node)) return
      onClose()
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onClose()
      }
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey, true)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey, true)
    }
  }, [anchor, onClose, more])

  useEffect(() => {
    if (!pos || more) return
    const first = ref.current?.querySelector<HTMLButtonElement>(on ? '.tb-btn' : '.tb-turn-on')
    first?.focus()
  }, [pos, on, more])

  const pick = (choice: TapbackChoice) => {
    void sendTapback(m, choice.kind, choice.emoji)
    onClose()
  }

  if (more) {
    return (
      <EmojiPicker
        anchor={anchor}
        cid={m.conversationId}
        allowImages={false}
        onUnicode={(char) => pick({ kind: 'emoji', emoji: char })}
        onClose={onClose}
      />
    )
  }

  const button = (choice: TapbackChoice, glyph: ReactNode, label: string) => {
    const isMine = sameChoice(mine, choice)
    const name = isMine ? `Remove ${label}` : label
    return (
      <button
        key={choice.kind === 'emoji' ? `e:${choice.emoji}` : choice.kind}
        type="button"
        className={`tb-btn ${isMine ? 'is-mine' : ''}`}
        disabled={!on}
        aria-pressed={isMine}
        title={name}
        aria-label={name}
        onClick={() => pick(choice)}
      >
        {glyph}
      </button>
    )
  }
  // An emoji tapback of yours that isn't in Messages' quick list still shows (highlighted) to remove it.
  const extra = mine?.kind === 'emoji' && !quick.some((e) => sameChoice(mine, { kind: 'emoji', emoji: e })) ? [mine.emoji] : []

  return createPortal(
    <div
      ref={ref}
      className={`tapback-picker ${on ? '' : 'is-off'}`}
      role="dialog"
      aria-label="Tapback"
      style={pos ?? { top: -9999, left: -9999 }}
      onKeyDown={(e) => {
        if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return
        e.preventDefault()
        const btns = [...(ref.current?.querySelectorAll<HTMLButtonElement>('.tb-btn:not([disabled])') ?? [])]
        const i = btns.indexOf(document.activeElement as HTMLButtonElement)
        btns[(i + (e.key === 'ArrowRight' ? 1 : -1) + btns.length) % btns.length]?.focus()
      }}
    >
      <div className="tb-row" role="group" aria-label="Tapbacks">
        {TAPBACKS.map((t) =>
          button({ kind: t.kind }, t.kind === 'laugh' ? <span className="tb-haha" aria-hidden="true">HA<br />HA</span> : t.emoji, t.label),
        )}
      </div>
      {on && (
        <div className="tb-row tb-row-emoji" role="group" aria-label="Emoji tapbacks">
          {[...quick, ...extra].map((e) => button({ kind: 'emoji', emoji: e }, e, e))}
          <button
            type="button"
            className="tb-btn tb-more"
            title="Any emoji"
            aria-label="Any emoji"
            onClick={() => setMore(true)}
          >
            <Icon name="smilePlus" size={18} />
          </button>
        </div>
      )}
      {on ? (
        <p className="tb-note">
          {mine
            ? 'Pick another to change yours, or the same one to remove it.'
            : quick.length
              ? 'Sent as a real tapback through Messages. The second row is the emoji Messages offers right now.'
              : 'Sent as a real tapback through Messages.'}
        </p>
      ) : (
        <div className="tb-off">
          <p>iMessage only takes tapbacks from the Messages app. Backchannel can send them by operating Messages for you.</p>
          <div className="tb-off-actions">
            <button type="button" className="btn is-primary tb-turn-on" disabled={!loaded} onClick={() => setServerSetting({ tapbacksViaMessages: true })}>
              Turn on tapbacks
            </button>
            <span className="beta-tag">Experimental</span>
          </div>
        </div>
      )}
    </div>,
    document.body,
  )
}
