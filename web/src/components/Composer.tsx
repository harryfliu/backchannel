import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { Conversation } from '../api/types'
import { fileSize, timeShort, whenLabel } from '../lib/format'
import { Icon } from '../lib/icons'
import { attachmentKind, clearDrafts, draftKey, requestDrafts, sendFiles, sendMessage, setDraft, useStore } from '../state/store'
import { Menu, Modal, useMenu } from './common'

const EMOJI = ['😂', '❤️', '👍', '🙏', '😭', '🔥', '🎉', '😅', '🥹', '😍', '🫡', '🙌', '👀', '💯', '🤝', '😮', '🤔', '😴', '🥳', '🍕', '☕', '🏃', '⚽', '🦃', '🎃', '🍰', '🚗', '🏠', '✅', '❌']

export function Composer({ c, threadRootId = null, compact = false }: { c: Conversation; threadRootId?: string | null; compact?: boolean }) {
  const key = draftKey(c.id, threadRootId)
  const draft = useStore((s) => s.drafts[key] ?? '')
  const ai = useStore((s) => s.aiDrafts[key])
  const aiEnabled = useStore((s) => s.health?.ai.enabled ?? false)
  const [text, setText] = useState(draft)
  const ta = useRef<HTMLTextAreaElement>(null)
  const emoji = useMenu()
  const schedule = useMenu()
  const [customOpen, setCustomOpen] = useState(false)
  const [files, setFiles] = useState<File[]>([])
  const picker = useRef<HTMLInputElement>(null)
  const canAttach = !threadRootId
  const previews = useMemo(() => files.map((f) => (attachmentKind(f.type) === 'image' ? URL.createObjectURL(f) : null)), [files])
  useEffect(() => () => previews.forEach((u) => u && URL.revokeObjectURL(u)), [previews])

  // Files dropped anywhere on the conversation land here (see Conversation.tsx).
  useEffect(() => {
    if (!canAttach) return
    const onAttach = (e: Event) => {
      const detail = (e as CustomEvent<{ cid: string; files: File[] }>).detail
      if (detail.cid !== c.id) return
      setFiles((cur) => [...cur, ...detail.files])
      ta.current?.focus()
    }
    window.addEventListener('bc:attach', onAttach)
    return () => window.removeEventListener('bc:attach', onAttach)
  }, [c.id, canAttach])
  useEffect(() => setFiles([]), [key])

  // Switching conversations loads that conversation's draft.
  useEffect(() => {
    setText(draft)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  useEffect(() => {
    if (!compact) ta.current?.focus({ preventScroll: true })
  }, [key, compact])

  useLayoutEffect(() => {
    const el = ta.current
    if (!el) return
    el.style.height = '0px'
    el.style.height = Math.min(el.scrollHeight, 240) + 'px'
  }, [text])

  const update = (v: string) => {
    setText(v)
    setDraft(key, v)
  }

  const submit = (sendAt: string | null = null) => {
    if (!text.trim() && files.length === 0) return
    if (files.length) {
      void sendFiles(c.id, files)
      setFiles([])
    }
    if (text.trim()) void sendMessage(c.id, text, { threadRootId, sendAt })
    setText('')
  }
  const ready = !!text.trim() || files.length > 0

  const insert = (s: string) => {
    const el = ta.current
    if (!el) return update(text + s)
    const start = el.selectionStart ?? text.length
    const end = el.selectionEnd ?? text.length
    const next = text.slice(0, start) + s + text.slice(end)
    update(next)
    requestAnimationFrame(() => {
      el.focus()
      el.setSelectionRange(start + s.length, start + s.length)
    })
  }

  const who = c.kind === 'dm' ? c.participants[0]?.name : c.title
  const placeholder = threadRootId ? 'Reply…' : c.service === 'SMS' ? `Text message to ${who}` : `Message ${c.isNamed ? '#' : ''}${who}`

  const presets = schedulePresets()

  return (
    <div className={`composer-wrap ${compact ? 'is-compact' : ''}`}>
      {ai && (
        <div className="ai-drafts" aria-live="polite">
          <span className="ai-label">
            <Icon name="sparkles" size={14} /> {ai.loading ? 'Drafting in your voice…' : ai.error ? 'Couldn’t draft a reply' : 'Pick one to edit'}
          </span>
          {ai.loading && (
            <span className="ai-skeletons">
              <span />
              <span />
              <span />
            </span>
          )}
          {ai.error && <span className="ai-error">{ai.error}</span>}
          {ai.options.map((o, i) => (
            <button
              key={i}
              type="button"
              className="ai-chip"
              onClick={() => {
                update(o)
                clearDrafts(key)
                requestAnimationFrame(() => ta.current?.focus())
              }}
            >
              {o}
            </button>
          ))}
          <button type="button" className="icon-btn ai-close" aria-label="Dismiss suggestions" onClick={() => clearDrafts(key)}>
            <Icon name="x" size={14} />
          </button>
        </div>
      )}
      <div className={`composer ${c.service === 'SMS' ? 'is-sms' : ''}`}>
        {files.length > 0 && (
          <div className="pending-files">
            {files.map((f, i) => (
              <div key={`${f.name}-${i}`} className="pending-file">
                {previews[i] ? <img src={previews[i]!} alt="" /> : <Icon name="file" size={20} />}
                <span className="pending-meta">
                  <span className="pending-name">{f.name}</span>
                  <span className="pending-size">{fileSize(f.size)}</span>
                </span>
                <button
                  type="button"
                  className="icon-btn pending-remove"
                  aria-label={`Remove ${f.name}`}
                  onClick={() => setFiles((cur) => cur.filter((_, j) => j !== i))}
                >
                  <Icon name="x" size={14} />
                </button>
              </div>
            ))}
          </div>
        )}
        <textarea
          ref={ta}
          id={`composer-${key}`}
          value={text}
          rows={1}
          placeholder={placeholder}
          aria-label={placeholder}
          onChange={(e) => update(e.target.value)}
          onPaste={(e) => {
            if (!canAttach || e.clipboardData.files.length === 0) return
            e.preventDefault()
            setFiles((cur) => [...cur, ...Array.from(e.clipboardData.files)])
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault()
              submit()
            }
            if (e.key === 'Escape') (e.target as HTMLTextAreaElement).blur()
          }}
        />
        <div className="composer-bar">
          {canAttach && (
            <>
              <button type="button" className="icon-btn" title="Attach photos or files" aria-label="Attach photos or files" onClick={() => picker.current?.click()}>
                <Icon name="plus" size={18} strokeWidth={2} />
              </button>
              <input
                ref={picker}
                id={`attach-${key}`}
                type="file"
                multiple
                hidden
                onChange={(e) => {
                  const picked = Array.from(e.target.files ?? [])
                  if (picked.length) setFiles((cur) => [...cur, ...picked])
                  e.target.value = ''
                  ta.current?.focus()
                }}
              />
            </>
          )}
          <button type="button" className="icon-btn" title="Emoji" aria-label="Emoji" onClick={emoji.open}>
            <Icon name="smile" size={18} />
          </button>
          <span className="composer-sep" />
          <button
            type="button"
            className="ai-btn"
            onClick={() => requestDrafts(c.id, threadRootId)}
            disabled={ai?.loading}
            title={aiEnabled ? 'Draft a reply in your voice' : 'Needs ANTHROPIC_API_KEY on your Mac'}
          >
            <Icon name="sparkles" size={15} /> Draft reply
          </button>
          <span className="composer-hint">{c.service === 'SMS' ? 'Sends as a text message (SMS)' : ''}</span>
          <div className={`send-group ${ready ? 'is-ready' : ''}`}>
            <button type="button" className="send-btn" aria-label="Send now" title="Send now (Enter)" disabled={!ready} onClick={() => submit()}>
              <Icon name="send" size={16} />
            </button>
            <button
              type="button"
              className="send-more"
              aria-label="Schedule for later"
              title={files.length ? 'Photos and files send right away' : 'Schedule for later'}
              disabled={!text.trim() || files.length > 0}
              onClick={schedule.open}
            >
              <Icon name="chevronDown" size={14} strokeWidth={2.2} />
            </button>
          </div>
        </div>
      </div>
      {threadRootId && <p className="composer-note">Goes out as a regular text. iMessage doesn’t let other apps send threaded replies, so it stays threaded here only.</p>}
      {emoji.anchor && (
        <Menu
          anchor={emoji.anchor}
          onClose={emoji.close}
          items={[]}
          header={
            <div className="emoji-grid">
              {EMOJI.map((e) => (
                <button
                  key={e}
                  type="button"
                  onClick={() => {
                    insert(e)
                    emoji.close()
                  }}
                >
                  {e}
                </button>
              ))}
            </div>
          }
        />
      )}
      {schedule.anchor && (
        <Menu
          anchor={schedule.anchor}
          align="right"
          onClose={schedule.close}
          header={<div className="menu-header">Schedule message</div>}
          items={[
            ...presets.map((p) => ({ label: p.label, hint: timeShort(p.at.toISOString()), onSelect: () => submit(p.at.toISOString()) })),
            { divider: true, label: '' },
            { label: 'Custom time…', icon: 'calendar' as const, onSelect: () => setCustomOpen(true) },
          ]}
        />
      )}
      {customOpen && <CustomSchedule onClose={() => setCustomOpen(false)} onPick={(iso) => submit(iso)} />}
    </div>
  )
}

function schedulePresets(now = new Date()) {
  const tomorrow = new Date(now)
  tomorrow.setDate(now.getDate() + 1)
  tomorrow.setHours(9, 0, 0, 0)
  const monday = new Date(now)
  monday.setDate(now.getDate() + ((8 - now.getDay()) % 7 || 7))
  monday.setHours(9, 0, 0, 0)
  const out = [{ label: 'Tomorrow', at: tomorrow }]
  if (monday.toDateString() !== tomorrow.toDateString()) out.push({ label: 'Monday', at: monday })
  return out
}

function CustomSchedule({ onClose, onPick }: { onClose: () => void; onPick: (iso: string) => void }) {
  const d = new Date(Date.now() + 3 * 3600000)
  d.setMinutes(0, 0, 0)
  const pad = (n: number) => String(n).padStart(2, '0')
  const [value, setValue] = useState(`${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:00`)
  const when = new Date(value)
  const valid = !Number.isNaN(when.getTime()) && when.getTime() > Date.now() + 60000
  return (
    <Modal onClose={onClose} label="Schedule message" className="small-modal">
      <form
        onSubmit={(e) => {
          e.preventDefault()
          if (!valid) return
          onPick(when.toISOString())
          onClose()
        }}
      >
        <h2 className="modal-title">Schedule message</h2>
        <label className="field">
          <span>Send at</span>
          <input id="schedule-at" type="datetime-local" value={value} onChange={(e) => setValue(e.target.value)} />
        </label>
        <p className="field-help">{valid ? `Sends ${whenLabel(when.toISOString())}. Your Mac needs to be awake with Backchannel running.` : 'Pick a time in the future.'}</p>
        <div className="modal-actions">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn is-primary" disabled={!valid}>
            Schedule
          </button>
        </div>
      </form>
    </Modal>
  )
}
