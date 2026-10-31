// The composer's emoji picker: regular emoji, your custom emoji, and stickers/Genmoji from iMessage.
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { EMOJI } from '../lib/emoji'
import { Icon } from '../lib/icons'
import { loadStickers, setState, stageImageForSend, useStore } from '../state/store'

type Tab = 'emoji' | 'custom' | 'imessage'

export function EmojiPicker({
  anchor,
  cid,
  onUnicode,
  onClose,
  allowImages = true,
}: {
  anchor: HTMLElement
  cid: string
  onUnicode: (char: string) => void
  onClose: () => void
  /** Thread replies can't carry pictures yet, so they only get regular emoji. */
  allowImages?: boolean
}) {
  const [tab, setTab] = useState<Tab>('emoji')
  const [q, setQ] = useState('')
  const ref = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)
  const custom = useStore((s) => s.customEmoji)
  const stickers = useStore((s) => s.stickers)

  useLayoutEffect(() => {
    if (!ref.current) return
    const r = anchor.getBoundingClientRect()
    const m = ref.current.getBoundingClientRect()
    const left = Math.max(8, Math.min(r.left, window.innerWidth - m.width - 8))
    let top = r.top - m.height - 8
    if (top < 8) top = Math.min(r.bottom + 8, window.innerHeight - m.height - 8)
    setPos({ top, left })
  }, [anchor, tab])

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
    if (tab === 'imessage') void loadStickers()
  }, [tab])

  const needle = q.trim().toLowerCase().replace(/:/g, '')
  const emoji = useMemo(() => EMOJI.filter(([n]) => !needle || n.includes(needle)), [needle])
  const mine = useMemo(() => custom.filter((e) => !needle || e.name.includes(needle)), [custom, needle])
  const fromImessage = useMemo(
    () => (stickers ?? []).filter((st) => !needle || (st.description ?? '').toLowerCase().includes(needle) || st.kind.includes(needle)),
    [stickers, needle],
  )

  const pickImage = (url: string, name: string) => {
    onClose()
    void stageImageForSend(cid, url, name)
  }

  return createPortal(
    <div ref={ref} className="emoji-picker" role="dialog" aria-label="Emoji" style={pos ?? { top: -9999, left: -9999 }}>
      <div className="ep-top">
        <div className="ep-search">
          <Icon name="search" size={15} />
          <input id="emoji-search" autoFocus placeholder={tab === 'imessage' ? 'Search stickers' : 'Search emoji'} value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        {allowImages && (
          <nav className="ep-tabs" aria-label="Emoji sets">
            {(['emoji', 'custom', 'imessage'] as Tab[]).map((t) => (
              <button key={t} type="button" className={tab === t ? 'is-on' : ''} onClick={() => setTab(t)}>
                {t === 'emoji' ? 'Emoji' : t === 'custom' ? 'Custom' : 'From iMessage'}
              </button>
            ))}
          </nav>
        )}
      </div>
      <div className="ep-body">
        {tab === 'emoji' && (
          <div className="ep-grid">
            {emoji.map(([name, char]) => (
              <button
                key={name}
                type="button"
                className="ep-cell"
                title={`:${name}:`}
                onClick={() => {
                  onUnicode(char)
                  onClose()
                }}
              >
                {char}
              </button>
            ))}
            {emoji.length === 0 && <p className="ep-empty">No emoji named “{q}”.</p>}
          </div>
        )}
        {tab === 'custom' && (
          <>
            <div className="ep-grid is-images">
              {mine.map((e) => (
                <button key={e.name} type="button" className="ep-cell" title={`:${e.name}:`} onClick={() => pickImage(e.url, e.name)}>
                  <img src={e.url} alt={`:${e.name}:`} loading="lazy" />
                </button>
              ))}
              <button
                type="button"
                className="ep-cell ep-add"
                title="Add an emoji"
                onClick={() => {
                  onClose()
                  setState({ emojiDialog: {} })
                }}
              >
                <Icon name="plus" size={20} />
              </button>
            </div>
            <p className="ep-note">
              {custom.length === 0
                ? 'Add your own: upload an image or paste a link, like one from slackmojis.com.'
                : 'They go out as small pictures, since iMessage has no custom emoji. Type :name: to find one fast.'}
            </p>
          </>
        )}
        {tab === 'imessage' && (
          <>
            {!stickers && <p className="ep-empty">Looking through your stickers…</p>}
            {stickers && fromImessage.length === 0 && (
              <p className="ep-empty">{stickers.length ? `Nothing matches “${q}”.` : 'No stickers or Genmoji in your messages yet.'}</p>
            )}
            <div className="ep-grid is-stickers">
              {fromImessage.map((st) => (
                <div key={st.id} className="ep-sticker">
                  <button type="button" className="ep-cell" title={st.description ?? st.kind} onClick={() => pickImage(st.url, st.description ?? st.kind)}>
                    <img src={st.url} alt={st.description ?? st.kind} loading="lazy" />
                  </button>
                  <button
                    type="button"
                    className="ep-save"
                    title="Save as a custom emoji"
                    aria-label="Save as a custom emoji"
                    onClick={() => {
                      onClose()
                      setState({ emojiDialog: { sticker: st } })
                    }}
                  >
                    <Icon name="bookmark" size={12} />
                  </button>
                </div>
              ))}
            </div>
            {stickers && stickers.length > 0 && <p className="ep-note">Stickers and Genmoji you’ve sent or received, most used first.</p>}
          </>
        )}
      </div>
    </div>,
    document.body,
  )
}
