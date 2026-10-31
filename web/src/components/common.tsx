// Small shared pieces: avatars, menus, tooltips-by-title, buttons.
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import type { Conversation, Person } from '../api/types'
import { hashIndex, initials } from '../lib/format'
import { Icon, type IconName } from '../lib/icons'
import { getState } from '../state/store'

const AVATAR_TONES = 8

export function Avatar({
  person,
  name,
  size = 36,
  radius,
  onClick,
  title,
}: {
  person?: Person | null
  name?: string
  size?: number
  radius?: number
  onClick?: () => void
  title?: string
}) {
  const label = person?.name ?? name ?? '?'
  const tone = hashIndex(person?.handle ?? label, AVATAR_TONES)
  const style = { width: size, height: size, borderRadius: radius ?? Math.round(size * 0.22), fontSize: Math.max(10, Math.round(size * 0.4)) }
  const body = person?.avatarUrl ? (
    <img src={person.avatarUrl} alt="" draggable={false} />
  ) : (
    <span className="avatar-initials">{initials(label)}</span>
  )
  if (onClick) {
    return (
      <button type="button" className={`avatar tone-${tone}`} style={style} onClick={onClick} title={title ?? label} aria-label={title ?? label}>
        {body}
      </button>
    )
  }
  return (
    <span className={`avatar tone-${tone}`} style={style} aria-hidden="true">
      {body}
    </span>
  )
}

/** The avatar for a sidebar row or header: a person, two stacked people, a # for groups, or a bot. */
export function ConversationGlyph({ c, size = 20 }: { c: Conversation; size?: number }) {
  if (c.isAutomated) {
    return (
      <span className="glyph glyph-bot" style={{ width: size, height: size }}>
        <Icon name={c.lastMessage?.text.match(/code/i) ? 'key' : 'bot'} size={Math.round(size * 0.72)} />
      </span>
    )
  }
  if (c.kind === 'group' && c.isNamed) {
    return (
      <span className="glyph glyph-hash" style={{ width: size, height: size }}>
        <Icon name="hash" size={Math.round(size * 0.8)} strokeWidth={2} />
      </span>
    )
  }
  if (c.kind === 'group') {
    const [a, b] = c.participants
    return (
      <span className="glyph glyph-stack" style={{ width: size, height: size }}>
        <Avatar person={a} size={Math.round(size * 0.72)} />
        <Avatar person={b} size={Math.round(size * 0.72)} />
        <span className="glyph-count">{c.participants.length}</span>
      </span>
    )
  }
  return <Avatar person={c.participants[0]} size={size} />
}

export function IconButton({
  icon,
  label,
  onClick,
  active,
  disabled,
  size = 18,
  className = '',
  badge,
}: {
  icon: IconName
  label: string
  onClick?: (e: React.MouseEvent<HTMLButtonElement>) => void
  active?: boolean
  disabled?: boolean
  size?: number
  className?: string
  badge?: ReactNode
}) {
  return (
    <button
      type="button"
      className={`icon-btn ${active ? 'is-active' : ''} ${className}`}
      onClick={onClick}
      aria-label={label}
      title={label}
      disabled={disabled}
    >
      <Icon name={icon} size={size} />
      {badge}
    </button>
  )
}

export interface MenuItem {
  label: string
  icon?: IconName
  hint?: string
  onSelect?: () => void
  danger?: boolean
  disabled?: boolean
  note?: string
  divider?: boolean
}

/** A popover menu anchored to an element. Closes on outside click or Escape. */
export function Menu({
  anchor,
  items,
  onClose,
  align = 'left',
  header,
}: {
  anchor: HTMLElement | null
  items: MenuItem[]
  onClose: () => void
  align?: 'left' | 'right'
  header?: ReactNode
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)

  useLayoutEffect(() => {
    if (!anchor || !ref.current) return
    const r = anchor.getBoundingClientRect()
    const m = ref.current.getBoundingClientRect()
    let left = align === 'right' ? r.right - m.width : r.left
    left = Math.max(8, Math.min(left, window.innerWidth - m.width - 8))
    let top = r.bottom + 6
    if (top + m.height > window.innerHeight - 8) top = Math.max(8, r.top - m.height - 6)
    setPos({ top, left })
  }, [anchor, align])

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (ref.current?.contains(e.target as Node) || anchor?.contains(e.target as Node)) return
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
    ref.current?.querySelector<HTMLButtonElement>('button:not([disabled])')?.focus()
  }, [pos])

  return createPortal(
    <div
      ref={ref}
      className="menu"
      role="menu"
      style={pos ? { top: pos.top, left: pos.left } : { top: -9999, left: -9999 }}
      onKeyDown={(e) => {
        if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
        e.preventDefault()
        const btns = [...(ref.current?.querySelectorAll<HTMLButtonElement>('button:not([disabled])') ?? [])]
        const i = btns.indexOf(document.activeElement as HTMLButtonElement)
        btns[(i + (e.key === 'ArrowDown' ? 1 : -1) + btns.length) % btns.length]?.focus()
      }}
    >
      {header}
      {items
        .filter((it, i, all) => !(it.divider && (i === 0 || i === all.length - 1 || all[i - 1].divider)))
        .map((it, i) =>
        it.divider ? (
          <div key={i} className="menu-divider" role="separator" />
        ) : (
          <button
            key={i}
            type="button"
            role="menuitem"
            className={`menu-item ${it.danger ? 'is-danger' : ''}`}
            disabled={it.disabled}
            onClick={() => {
              it.onSelect?.()
              onClose()
            }}
          >
            {it.icon && <Icon name={it.icon} size={16} />}
            <span className="menu-label">
              {it.label}
              {it.note && <span className="menu-note">{it.note}</span>}
            </span>
            {it.hint && <span className="menu-hint">{it.hint}</span>}
          </button>
        ),
      )}
    </div>,
    document.body,
  )
}

/** Hook for a menu toggled by a button. */
export function useMenu() {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null)
  return {
    anchor,
    open: (e: React.MouseEvent<HTMLElement>) => setAnchor((a) => (a ? null : e.currentTarget)),
    close: () => setAnchor(null),
  }
}

export function Modal({
  onClose,
  children,
  label,
  className = '',
}: {
  onClose: () => void
  children: ReactNode
  label: string
  className?: string
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])
  return createPortal(
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${className}`} role="dialog" aria-modal="true" aria-label={label}>
        {children}
      </div>
    </div>,
    document.body,
  )
}

/** Remind-me presets used by "Remind me later" and snooze. */
export function remindPresets(now = new Date()) {
  const inHour = new Date(now.getTime() + 60 * 60000)
  const tonight = new Date(now)
  tonight.setHours(19, 0, 0, 0)
  const tomorrow = new Date(now)
  tomorrow.setDate(now.getDate() + 1)
  tomorrow.setHours(9, 0, 0, 0)
  const nextWeek = new Date(now)
  nextWeek.setDate(now.getDate() + ((8 - now.getDay()) % 7 || 7))
  nextWeek.setHours(9, 0, 0, 0)
  const list = [{ label: 'In 1 hour', at: inHour }]
  if (tonight.getTime() - now.getTime() > 90 * 60000) list.push({ label: 'This evening', at: tonight })
  list.push({ label: 'Tomorrow', at: tomorrow }, { label: 'Next week', at: nextWeek })
  return list
}

export function myNames(): string[] {
  const me = getState().me
  return me ? [me.shortName, me.name] : []
}

export function Spinner({ label = 'Loading' }: { label?: string }) {
  return <span className="spinner" role="status" aria-label={label} />
}
