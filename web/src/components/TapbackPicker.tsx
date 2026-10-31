// The six tapbacks, sent by operating the Messages app (Settings › Messages helpers).
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { Message } from '../api/types'
import { myTapback, TAPBACKS } from '../lib/tapbacks'
import { sendTapback, setServerSetting, shownReactions, useStore } from '../state/store'

export function TapbackPicker({ m, anchor, onClose }: { m: Message; anchor: HTMLElement; onClose: () => void }) {
  const on = useStore((s) => !!s.serverSettings?.tapbacksViaMessages)
  const loaded = useStore((s) => !!s.serverSettings)
  const pending = useStore((s) => s.pendingTapbacks[m.id])
  const mine = myTapback(shownReactions(m, pending))
  const ref = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)

  useLayoutEffect(() => {
    if (!ref.current) return
    const r = anchor.getBoundingClientRect()
    const p = ref.current.getBoundingClientRect()
    const left = Math.max(8, Math.min(r.right - p.width, window.innerWidth - p.width - 8))
    let top = r.bottom + 6
    if (top + p.height > window.innerHeight - 8) top = Math.max(8, r.top - p.height - 6)
    setPos({ top, left })
  }, [anchor, on])

  useEffect(() => {
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
  }, [anchor, onClose])

  useEffect(() => {
    if (!pos) return
    const first = ref.current?.querySelector<HTMLButtonElement>(on ? '.tb-btn' : '.tb-turn-on')
    first?.focus()
  }, [pos, on])

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
        {TAPBACKS.map((t) => {
          const isMine = mine === t.kind
          return (
            <button
              key={t.kind}
              type="button"
              className={`tb-btn ${isMine ? 'is-mine' : ''}`}
              disabled={!on}
              aria-pressed={isMine}
              title={isMine ? `Remove ${t.label}` : t.label}
              aria-label={isMine ? `Remove ${t.label}` : t.label}
              onClick={() => {
                void sendTapback(m, t.kind)
                onClose()
              }}
            >
              {t.emoji}
            </button>
          )
        })}
      </div>
      {on ? (
        <p className="tb-note">
          {mine ? 'Pick another to change yours, or the same one to remove it.' : 'Sent as a real tapback through Messages.'}
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
