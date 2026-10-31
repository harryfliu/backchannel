// The composer's emoji picker: every emoji (by category, with search by name or meaning), your
// custom emoji, and stickers/Genmoji from iMessage.
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { emojiName, noteEmojiUsed, POPULAR, recentEmoji, searchEmoji, useEmojiData } from '../lib/emoji'
import { Icon } from '../lib/icons'
import { loadStickers, setState, stageImageForSend, useStore } from '../state/store'

type Tab = 'emoji' | 'custom' | 'imessage'

const WIDTH = 440
const HEIGHT = 470
const MARGIN = 8
// One glyph per category for the jump bar, in the full set's group order.
const GROUP_ICONS = ['😀', '👋', '🐻', '🍔', '✈️', '⚽', '💡', '🔣', '🏳️']

type Place = { left: number; width: number; height: number; top?: number; bottom?: number }

/** Where the picker fits: above or below the button, whichever has more room, never off-screen. */
function placeFor(anchor: HTMLElement): Place {
  const r = anchor.getBoundingClientRect()
  const vw = window.innerWidth
  const vh = window.innerHeight
  const width = Math.min(WIDTH, vw - MARGIN * 2)
  const left = Math.max(MARGIN, Math.min(r.left, vw - width - MARGIN))
  const above = r.top - MARGIN * 2
  const below = vh - r.bottom - MARGIN * 2
  if (above >= Math.min(HEIGHT, below)) {
    return { left, width, height: Math.max(160, Math.min(HEIGHT, above)), bottom: vh - r.top + MARGIN }
  }
  return { left, width, height: Math.max(160, Math.min(HEIGHT, below)), top: r.bottom + MARGIN }
}

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
  const [hover, setHover] = useState<{ char: string; name: string } | null>(null)
  const ref = useRef<HTMLDivElement>(null)
  const body = useRef<HTMLDivElement>(null)
  const [place, setPlace] = useState<Place>(() => placeFor(anchor))
  const custom = useStore((s) => s.customEmoji)
  const stickers = useStore((s) => s.stickers)
  const set = useEmojiData()

  // Sized to the window, not to what's inside: stickers loading later can't push it off-screen.
  useLayoutEffect(() => {
    const update = () => setPlace(placeFor(anchor))
    update()
    window.addEventListener('resize', update)
    return () => window.removeEventListener('resize', update)
  }, [anchor])

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
    body.current?.scrollTo({ top: 0 })
  }, [tab])

  const needle = q.trim().toLowerCase().replace(/:/g, '')
  const found = useMemo(() => (needle ? searchEmoji(needle, 300, set) : []), [needle, set])
  const recents = useMemo(() => {
    const mine = recentEmoji()
    const chars = mine.length >= 8 ? mine : [...mine, ...POPULAR.map(([, c]) => c).filter((c) => !mine.includes(c))].slice(0, 24)
    return chars.map((char) => ({ char, name: emojiName(char) }))
  }, [])
  const sections = useMemo(() => {
    if (!set.ready) return []
    return set.groups.map((title, i) => ({ title, items: set.entries.filter((e) => e.group === i) }))
  }, [set])
  const mine = useMemo(() => custom.filter((e) => !needle || e.name.includes(needle)), [custom, needle])
  const fromImessage = useMemo(
    () => (stickers ?? []).filter((st) => !needle || (st.description ?? '').toLowerCase().includes(needle) || st.kind.includes(needle)),
    [stickers, needle],
  )

  const pick = (char: string) => {
    noteEmojiUsed(char)
    onUnicode(char)
    onClose()
  }
  const pickImage = (url: string, name: string) => {
    onClose()
    void stageImageForSend(cid, url, name)
  }
  const jump = (i: number) => {
    const el = body.current?.querySelector<HTMLElement>(`[data-section="${i}"]`)
    if (el && body.current) body.current.scrollTo({ top: el.offsetTop - 4 })
  }
  const cell = (char: string, name: string, key: string) => (
    <button
      key={key}
      type="button"
      className="ep-cell"
      title={name ? `:${name}:` : undefined}
      onMouseEnter={() => setHover({ char, name })}
      onFocus={() => setHover({ char, name })}
      onClick={() => pick(char)}
    >
      {char}
    </button>
  )

  return createPortal(
    <div
      ref={ref}
      className="emoji-picker"
      role="dialog"
      aria-label="Emoji"
      style={{ left: place.left, width: place.width, height: place.height, top: place.top, bottom: place.bottom }}
    >
      <div className="ep-top">
        <div className="ep-search">
          <Icon name="search" size={15} />
          <input
            id="emoji-search"
            autoFocus
            placeholder={tab === 'imessage' ? 'Search stickers' : tab === 'custom' ? 'Search your emoji' : 'Search emoji: a name or what it means'}
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
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
        {tab === 'emoji' && !needle && set.ready && (
          <nav className="ep-jump" aria-label="Emoji categories">
            <button type="button" title="Frequently used" onClick={() => jump(-1)}>
              <Icon name="clock" size={15} />
            </button>
            {set.groups.map((title, i) => (
              <button key={title} type="button" title={title} onClick={() => jump(i)}>
                {GROUP_ICONS[i] ?? '•'}
              </button>
            ))}
          </nav>
        )}
      </div>
      <div className="ep-body" ref={body} onMouseLeave={() => setHover(null)}>
        {tab === 'emoji' && needle && (
          <div className="ep-grid">
            {found.map((h) => cell(h.char, h.name, h.char))}
            {found.length === 0 && <p className="ep-empty">{set.ready ? `No emoji for “${q}”.` : 'Loading every emoji…'}</p>}
          </div>
        )}
        {tab === 'emoji' && !needle && (
          <>
            <section className="ep-section" data-section="-1">
              <h3 className="ep-section-title">Frequently used</h3>
              <div className="ep-grid">{recents.map((e) => cell(e.char, e.name, 'recent-' + e.char))}</div>
            </section>
            {sections.map((s, i) => (
              <section key={s.title} className="ep-section" data-section={i}>
                <h3 className="ep-section-title">{s.title}</h3>
                <div className="ep-grid">{s.items.map((e) => cell(e.char, e.names[0], e.char))}</div>
              </section>
            ))}
            {!set.ready && <p className="ep-empty">Loading every emoji…</p>}
          </>
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
      {tab === 'emoji' && (
        <div className="ep-foot" aria-live="polite">
          {hover ? (
            <>
              <span className="ep-foot-char">{hover.char}</span>
              <span className="ep-foot-name">{hover.name ? `:${hover.name}:` : ''}</span>
            </>
          ) : (
            <span className="ep-foot-hint">Type :name: in a message to find one fast, like :tired: or :lol:</span>
          )}
        </div>
      )}
    </div>,
    document.body,
  )
}
