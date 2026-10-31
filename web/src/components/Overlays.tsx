import { useEffect, useMemo, useRef, useState } from 'react'
import { isMock } from '../api/client'
import { isNative } from '../native'
import { EMOJI_NAME_RE, suggestEmojiName } from '../lib/emoji'
import type { Conversation, SearchResult, ServerSettings, StickerItem } from '../api/types'
import { ageShort, formatHandle, timeShort } from '../lib/format'
import { Icon } from '../lib/icons'
import {
  addChiefLoop,
  addCustomEmoji,
  removeCustomEmoji,
  saveStickerAsEmoji,
  closeSearch,
  displayName,
  refreshHealth,
  setServerSetting,
  getState,
  openConversation,
  openProfile,
  openView,
  personFor,
  requestDrafts,
  runSearch,
  setRail,
  setSettings,
  setState,
  startConversation,
  useStore,
  type Settings,
} from '../state/store'
import { Avatar, ConversationGlyph, Modal, Spinner } from './common'

function score(c: Conversation, q: string) {
  if (!q) return 1
  const hay = [c.title, ...c.participants.flatMap((p) => [p.name, ...p.handles.map(formatHandle), ...p.handles])].join(' ').toLowerCase()
  const t = c.title.toLowerCase()
  if (t.startsWith(q)) return 4
  if (t.split(/\s+/).some((w) => w.startsWith(q))) return 3
  if (hay.includes(q)) return 2
  // loose subsequence match: "lfam" → "Liu Family"
  let i = 0
  for (const ch of t) if (ch === q[i]) i++
  return i === q.length ? 1 : 0
}

function useListKeys(count: number, onEnter: (i: number) => void) {
  const [active, setActive] = useState(0)
  useEffect(() => setActive(0), [count])
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActive((a) => Math.min(count - 1, a + 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((a) => Math.max(0, a - 1))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (count) onEnter(active)
    }
  }
  return { active, setActive, onKeyDown }
}

// ------------------------------------------------------------------ Cmd+K

export function QuickSwitcher() {
  const open = useStore((s) => s.switcherOpen)
  if (!open) return null
  return <QuickSwitcherInner />
}

function QuickSwitcherInner() {
  const convs = useStore((s) => s.conversations)
  const [q, setQ] = useState('')
  const list = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return Object.values(convs)
      .map((c) => ({ c, s: score(c, needle) }))
      .filter((x) => x.s > 0)
      .sort((a, b) => b.s - a.s || (b.c.lastMessage?.sentAt ?? '').localeCompare(a.c.lastMessage?.sentAt ?? ''))
      .slice(0, 9)
      .map((x) => x.c)
  }, [convs, q])
  const close = () => setState({ switcherOpen: false })
  const keys = useListKeys(list.length, (i) => openConversation(list[i].id))
  return (
    <Modal onClose={close} label="Jump to a conversation" className="switcher">
      <div className="switcher-input">
        <Icon name="search" size={18} />
        <input id="switcher-q" autoFocus placeholder="Jump to a person or group…" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={keys.onKeyDown} />
        <kbd>esc</kbd>
      </div>
      <div className="switcher-list" role="listbox">
        {!q && <p className="switcher-label">Recent</p>}
        {list.map((c, i) => (
          <button
            key={c.id}
            type="button"
            role="option"
            aria-selected={i === keys.active}
            className={`switcher-row ${i === keys.active ? 'is-active' : ''}`}
            onMouseEnter={() => keys.setActive(i)}
            onClick={() => openConversation(c.id)}
          >
            <ConversationGlyph c={c} size={22} />
            <span className="switcher-name">{c.title}</span>
            {c.unreadCount > 0 && <span className="side-badge">{c.unreadCount}</span>}
            <span className="switcher-meta">{c.lastMessage ? ageShort(c.lastMessage.sentAt) : ''}</span>
          </button>
        ))}
        {list.length === 0 && <p className="side-empty">No one matches “{q}”.</p>}
      </div>
    </Modal>
  )
}

// ------------------------------------------------------------------ Search

export function SearchModal() {
  const open = useStore((s) => s.search.open)
  if (!open) return null
  return <SearchInner />
}

function highlight(text: string, q: string) {
  const i = q ? text.toLowerCase().indexOf(q.toLowerCase()) : -1
  if (i < 0) return text
  const start = Math.max(0, i - 40)
  return (
    <>
      {start > 0 ? '…' : ''}
      {text.slice(start, i)}
      <mark>{text.slice(i, i + q.length)}</mark>
      {text.slice(i + q.length)}
    </>
  )
}

function SearchInner() {
  const search = useStore((s) => s.search)
  const scopeTitle = useStore((s) => (s.search.scope ? s.conversations[s.search.scope]?.title : null))
  const [q, setQ] = useState(search.q)
  const timer = useRef<number>(0)
  useEffect(() => {
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => void runSearch(q), 160)
    return () => window.clearTimeout(timer.current)
  }, [q])
  const pick = (r: SearchResult) => {
    closeSearch()
    openConversation(r.conversation.id, r.message.id)
  }
  const keys = useListKeys(search.results.length, (i) => pick(search.results[i]))
  return (
    <Modal onClose={closeSearch} label="Search messages" className="switcher search">
      <div className="switcher-input">
        <Icon name="search" size={18} />
        {scopeTitle && (
          <button type="button" className="scope-chip" onClick={() => runSearch(q, null)} title="Search everywhere">
            in {scopeTitle} <Icon name="x" size={12} />
          </button>
        )}
        <input
          id="search-q"
          autoFocus
          placeholder={scopeTitle ? 'Search this conversation' : 'Search all your messages'}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={keys.onKeyDown}
        />
        {search.loading ? <Spinner /> : <kbd>esc</kbd>}
      </div>
      <div className="switcher-list" role="listbox">
        {!q.trim() && <p className="side-empty">Search every text on this Mac, going back years. Try “wifi password”, “address” or a name.</p>}
        {q.trim() && !search.loading && search.results.length === 0 && <p className="side-empty">No messages match “{q}”.</p>}
        {search.results.map((r, i) => {
          const sender = r.message.fromMe ? null : personFor(r.message.senderHandle)
          return (
            <button
              key={r.message.id}
              type="button"
              role="option"
              aria-selected={i === keys.active}
              className={`search-row ${i === keys.active ? 'is-active' : ''}`}
              onMouseEnter={() => keys.setActive(i)}
              onClick={() => pick(r)}
            >
              <Avatar person={sender} name={displayName(r.message.senderHandle)} size={32} />
              <span className="search-body">
                <span className="search-top">
                  <strong>{displayName(r.message.senderHandle)}</strong>
                  <span className="search-where">in {r.conversation.title}</span>
                  <span className="search-time">
                    {ageShort(r.message.sentAt)} · {timeShort(r.message.sentAt)}
                  </span>
                </span>
                <span className="search-text">{highlight(r.message.text, q.trim())}</span>
              </span>
            </button>
          )
        })}
      </div>
    </Modal>
  )
}

// ------------------------------------------------------------------ New message

export function ComposeModal() {
  const open = useStore((s) => s.composeOpen)
  if (!open) return null
  return <ComposeInner />
}

function ComposeInner() {
  const convs = useStore((s) => s.conversations)
  const [q, setQ] = useState('')
  const [target, setTarget] = useState<string | null>(null)
  const [text, setText] = useState('')
  const close = () => setState({ composeOpen: false })
  const dms = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return Object.values(convs)
      .filter((c) => !c.isAutomated)
      .map((c) => ({ c, s: score(c, needle) }))
      .filter((x) => x.s > 0)
      .sort((a, b) => b.s - a.s || (b.c.lastMessage?.sentAt ?? '').localeCompare(a.c.lastMessage?.sentAt ?? ''))
      .slice(0, 8)
      .map((x) => x.c)
  }, [convs, q])
  const looksLikeHandle = /^[+\d()\s.-]{7,}$/.test(q.trim()) || /^\S+@\S+\.\S+$/.test(q.trim())
  const go = (c: Conversation) => {
    close()
    openConversation(c.id)
  }
  const keys = useListKeys(dms.length, (i) => go(dms[i]))

  if (target) {
    return (
      <Modal onClose={close} label="New message" className="switcher">
        <form
          className="compose-first"
          onSubmit={(e) => {
            e.preventDefault()
            if (!text.trim()) return
            close()
            void startConversation(target, text.trim())
          }}
        >
          <div className="switcher-input">
            <span className="to-label">To:</span>
            <span className="to-chip">{formatHandle(target)}</span>
            <button type="button" className="link-btn" onClick={() => setTarget(null)}>
              Change
            </button>
          </div>
          <textarea
            id="compose-first"
            autoFocus
            rows={4}
            placeholder={`Message ${formatHandle(target)}`}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault()
                ;(e.currentTarget.form as HTMLFormElement).requestSubmit()
              }
            }}
          />
          <div className="modal-actions compose-actions">
            <span className="field-help">Goes out through Messages as an iMessage, or as a text if they don’t have iMessage.</span>
            <button type="submit" className="btn is-primary" disabled={!text.trim()}>
              <Icon name="send" size={14} /> Send
            </button>
          </div>
        </form>
      </Modal>
    )
  }

  return (
    <Modal onClose={close} label="New message" className="switcher">
      <div className="switcher-input">
        <span className="to-label">To:</span>
        <input
          id="compose-to"
          autoFocus
          placeholder="Name, phone number or email"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && looksLikeHandle && dms.length === 0) {
              e.preventDefault()
              setTarget(q.trim())
              return
            }
            keys.onKeyDown(e)
          }}
        />
        <kbd>esc</kbd>
      </div>
      <div className="switcher-list">
        {looksLikeHandle && (
          <button type="button" className="switcher-row" onClick={() => setTarget(q.trim())}>
            <span className="glyph glyph-hash" style={{ width: 22, height: 22 }}>
              <Icon name="plus" size={16} />
            </span>
            <span className="switcher-name">Message {formatHandle(q.trim())}</span>
          </button>
        )}
        {!q.trim() && <p className="switcher-label">Recent</p>}
        {dms.map((c, i) => (
          <button
            key={c.id}
            type="button"
            className={`switcher-row ${i === keys.active ? 'is-active' : ''}`}
            onMouseEnter={() => keys.setActive(i)}
            onClick={() => go(c)}
          >
            <ConversationGlyph c={c} size={22} />
            <span className="switcher-name">{c.title}</span>
            <span className="switcher-meta">{c.kind === 'dm' ? formatHandle(c.participants[0]?.handle ?? '') : `${c.participants.length + 1} people`}</span>
          </button>
        ))}
        {q.trim() && !looksLikeHandle && dms.length === 0 && (
          <p className="side-empty">Nobody you’ve texted matches “{q}”. Type a phone number or email to start a new conversation.</p>
        )}
      </div>
    </Modal>
  )
}

// ------------------------------------------------------------------ Settings

const SIDEBAR_THEMES: { id: Settings['sidebarTheme']; label: string }[] = [
  { id: 'aubergine', label: 'Aubergine' },
  { id: 'ocean', label: 'Ocean' },
  { id: 'forest', label: 'Forest' },
  { id: 'graphite', label: 'Graphite' },
]

export function SettingsModal() {
  const open = useStore((s) => s.settingsOpen)
  const settings = useStore((s) => s.settings)
  const health = useStore((s) => s.health)
  useEffect(() => {
    if (open) void refreshHealth()
  }, [open])
  if (!open) return null
  const close = () => setState({ settingsOpen: false })
  return (
    <Modal onClose={close} label="Settings" className="settings-modal">
      <header className="modal-head">
        <h2 className="modal-title">Preferences</h2>
        <button type="button" className="icon-btn" aria-label="Close" onClick={close}>
          <Icon name="x" size={18} />
        </button>
      </header>
      <div className="settings-body">
        <fieldset className="setting">
          <legend>Appearance</legend>
          <div className="segmented" role="radiogroup" aria-label="Appearance">
            {(['system', 'light', 'dark'] as const).map((t) => (
              <button key={t} type="button" role="radio" aria-checked={settings.theme === t} className={settings.theme === t ? 'is-on' : ''} onClick={() => setSettings({ theme: t })}>
                <Icon name={t === 'dark' ? 'moon' : t === 'light' ? 'sun' : 'sliders'} size={15} /> {t === 'system' ? 'Match system' : t[0].toUpperCase() + t.slice(1)}
              </button>
            ))}
          </div>
        </fieldset>
        <fieldset className="setting">
          <legend>Sidebar color</legend>
          <div className="swatches">
            {SIDEBAR_THEMES.map((t) => (
              <button
                key={t.id}
                type="button"
                className={`swatch swatch-${t.id} ${settings.sidebarTheme === t.id ? 'is-on' : ''}`}
                onClick={() => setSettings({ sidebarTheme: t.id })}
                aria-pressed={settings.sidebarTheme === t.id}
              >
                <span className="swatch-chip" />
                {t.label}
              </button>
            ))}
          </div>
        </fieldset>
        <fieldset className="setting">
          <legend>Inline replies</legend>
          <label className="radio-row">
            <input
              type="radio"
              name="threads"
              id="threads-only"
              checked={settings.threadReplies === 'thread-only'}
              onChange={() => setSettings({ threadReplies: 'thread-only' })}
            />
            <span>
              <strong>Keep them in threads</strong>
              <span className="field-help">Like Slack. The conversation shows “3 replies” and the thread opens on the side.</span>
            </span>
          </label>
          <label className="radio-row">
            <input
              type="radio"
              name="threads"
              id="threads-inline"
              checked={settings.threadReplies === 'also-inline'}
              onChange={() => setSettings({ threadReplies: 'also-inline' })}
            />
            <span>
              <strong>Also show them in the conversation</strong>
              <span className="field-help">Like Messages. Each reply appears in order with what it replied to.</span>
            </span>
          </label>
        </fieldset>
        {isNative && (
          <fieldset className="setting">
            <legend>Notifications</legend>
            <label className="toggle-row">
              <input type="checkbox" id="notify" checked={settings.notify} onChange={(e) => setSettings({ notify: e.target.checked })} />
              <span>
                Show a notification for new texts when Backchannel isn’t in front
                <span className="field-help">
                  Messages notifies you too. To avoid getting two, turn off Messages in System Settings › Notifications.
                </span>
              </span>
            </label>
            {settings.notify && (
              <div className="notify-levels" role="radiogroup" aria-label="Which texts notify you">
                {(
                  [
                    ['all', 'Every new text', null],
                    ['dms', 'Direct messages, and anything that needs you', 'Group chatter stays quiet unless it mentions you, replies to you or is time-sensitive.'],
                    ['important', 'Only what needs you', 'Questions for you, mentions, replies, time-sensitive and urgent texts.'],
                  ] as const
                ).map(([value, label, help]) => (
                  <label key={value} className="radio-row">
                    <input type="radio" name="notify-level" checked={(settings.notifyLevel ?? 'all') === value} onChange={() => setSettings({ notifyLevel: value })} />
                    <span>
                      <strong>{label}</strong>
                      {help && <span className="field-help">{help}</span>}
                    </span>
                  </label>
                ))}
              </div>
            )}
          </fieldset>
        )}
        <MessagesHelpers />
        <CustomEmojiSettings />
        <fieldset className="setting">
          <legend>Density</legend>
          <label className="toggle-row">
            <input type="checkbox" id="compact" checked={settings.compact} onChange={(e) => setSettings({ compact: e.target.checked })} />
            <span>Compact message spacing</span>
          </label>
        </fieldset>
        <fieldset className="setting">
          <legend>Connection to your Mac</legend>
          <ul className="health">
            <li className={health?.chatDb.readable ? 'is-ok' : 'is-bad'}>
              <Icon name={health?.chatDb.readable ? 'check' : 'alert'} size={15} />
              {health?.chatDb.readable ? 'Reading your messages' : health?.chatDb.error ?? 'Can’t read your messages yet'}
            </li>
            <li className={health?.send.mode === 'applescript' ? 'is-ok' : 'is-warn'}>
              <Icon name={health?.send.mode === 'applescript' ? 'check' : 'alert'} size={15} />
              {health?.send.mode === 'applescript'
                ? 'Sending through Messages'
                : health?.send.mode === 'dry-run'
                  ? isMock
                    ? 'Practice mode: nothing actually sends from this mock'
                    : 'Dry run: sends are logged, not delivered'
                  : 'Sending is unavailable'}
            </li>
            <li className={health?.ai.enabled ? 'is-ok' : 'is-warn'}>
              <Icon name={health?.ai.enabled ? 'check' : 'alert'} size={15} />
              {health?.ai.enabled
                ? health.ai.backend === 'claude-cli'
                  ? 'Drafts and catch-up use Claude Code (claude -p) on your subscription'
                  : 'Drafts and catch-up use your Anthropic API key'
                : 'Drafts and catch-up need Claude Code (the claude command) or an API key'}
            </li>
          </ul>
        </fieldset>
        <button type="button" className="link-btn" onClick={() => setState({ settingsOpen: false, shortcutsOpen: true })}>
          Keyboard shortcuts
        </button>
      </div>
    </Modal>
  )
}

export function ShortcutsModal() {
  const open = useStore((s) => s.shortcutsOpen)
  if (!open) return null
  const mod = /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘' : 'Ctrl'
  const rows: [string, string][] = [
    [`${mod} K`, 'Jump to a conversation'],
    [`${mod} G`, 'Search messages'],
    ['⌥ ↑ / ⌥ ↓', 'Previous / next conversation'],
    ['⌥ ⇧ ↑ / ⌥ ⇧ ↓', 'Previous / next unread'],
    [`${mod} ⇧ A`, 'All unreads'],
    ['⌥ click a message', 'Mark unread from there'],
    ['Enter', 'Send'],
    ['⇧ Enter', 'New line'],
    ['Esc', 'Close the side panel'],
    [`${mod} /`, 'Show shortcuts'],
  ]
  return (
    <Modal onClose={() => setState({ shortcutsOpen: false })} label="Keyboard shortcuts" className="small-modal">
      <h2 className="modal-title">Keyboard shortcuts</h2>
      <dl className="shortcuts">
        {rows.map(([k, v]) => (
          <div key={k}>
            <dt>
              <kbd>{k}</kbd>
            </dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
    </Modal>
  )
}

export function Toasts() {
  const toasts = useStore((s) => s.toasts)
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.tone === 'error' ? 'is-error' : ''}`}>
          {t.tone === 'error' && <Icon name="alert" size={16} />}
          {t.text}
        </div>
      ))}
    </div>
  )
}

// ------------------------------------------------------------------ Tour (mock only)

const TOUR: { title: string; body: string; go: () => void }[] = [
  {
    title: 'Unreads',
    body: 'Every conversation with something new on one page (⌘⇧A). Reply or mark read right from the card. Nothing is marked read until you say so.',
    go: () => openView('unreads'),
  },
  {
    title: 'Catch up',
    body: 'A short read of what needs you, with replies already drafted in your voice. Edit and send right from the card.',
    go: () => openView('catchup'),
  },
  {
    title: 'Waiting on you',
    body: 'Conversations where they asked you something and you haven’t answered. Reply and it drops back to its normal spot.',
    go: () => {
      setRail('home')
      setState((s) => ({ collapsed: { ...s.collapsed, waiting: false } }))
      openConversation('dm-marcus')
    },
  },
  {
    title: 'Draft reply',
    body: 'In any conversation, get three replies written the way you text. Pick one, tweak it, send.',
    go: () => {
      openConversation('dm-jordan')
      void requestDrafts('dm-jordan')
    },
  },
  {
    title: 'Threads',
    body: 'When someone swipes to reply on their phone, it shows up here as a Slack thread instead of cluttering the group.',
    go: () => openView('threads'),
  },
  {
    title: 'Tapbacks and real replies',
    body: 'Hover a text and click the smiley to send ❤️ 👍 😂 and friends, or click a reaction to add yours. Turn it on in Settings › Messages helpers; on your Mac it works by operating Messages for you.',
    go: () => {
      setRail('home')
      openConversation('dm-jordan')
    },
  },
  {
    title: 'Apps & alerts',
    body: 'Codes, deliveries and appointment texts get filed out of the way. Codes get a copy button.',
    go: () => {
      setRail('home')
      setState((s) => ({ collapsed: { ...s.collapsed, automated: false } }))
      openConversation('dm-24273')
    },
  },
  {
    title: 'Remind me later and scheduled sends',
    body: 'Hover any text to park it with a reminder. Use the arrow next to Send to send something later.',
    go: () => openView('scheduled'),
  },
  {
    title: 'Custom emoji and stickers',
    body: 'Add your own emoji (upload one, or paste a link from slackmojis.com) and reuse stickers and Genmoji from your messages. Type :name: or use the smiley button.',
    go: () => {
      openConversation('dm-jordan')
      setTimeout(() => document.querySelector<HTMLButtonElement>('.main [aria-label="Emoji"]')?.click(), 50)
    },
  },
  {
    title: 'Chief in profiles',
    body: 'Click a name to see what Chief knows: birthdays, open loops, the last time you hung out.',
    go: () => {
      openConversation('dm-jordan')
      openProfile('+14155550134', 'dm-jordan')
    },
  },
]

export function TourModal() {
  const open = useStore((s) => s.tourOpen)
  if (!open || !isMock) return null
  const close = () => setState({ tourOpen: false })
  return (
    <Modal onClose={close} label="Tour" className="settings-modal tour">
      <header className="modal-head">
        <h2 className="modal-title">What’s in this mock</h2>
        <button type="button" className="icon-btn" aria-label="Close" onClick={close}>
          <Icon name="x" size={18} />
        </button>
      </header>
      <div className="settings-body">
        <p className="tour-lede">
          Everything here is made up, and nothing sends. It’s the real interface running on sample texts, so click around. It works like Slack:
          sidebar sections, threads, reactions, ⌘K to jump anywhere. These parts go beyond Slack:
        </p>
        <ol className="tour-list">
          {TOUR.map((t) => (
            <li key={t.title}>
              <div>
                <strong>{t.title}</strong>
                <span>{t.body}</span>
              </div>
              <button
                type="button"
                className="btn"
                onClick={() => {
                  close()
                  t.go()
                }}
              >
                Show me
              </button>
            </li>
          ))}
        </ol>
        <p className="field-help">
          iMessage limits: other apps can’t send tapbacks or real threaded replies. You’ll see everyone’s tapbacks and threads here, and your thread
          replies go out as normal texts.
        </p>
      </div>
    </Modal>
  )
}

export function openTour() {
  if (getState().settingsOpen) setState({ settingsOpen: false })
  setState({ tourOpen: true })
}



// ------------------------------------------------------------------ Add to Chief

export function ChiefLoopModal() {
  const draft = useStore((s) => s.chiefDraft)
  if (!draft) return null
  return <ChiefLoopInner key={draft.mid} initial={draft.title} />
}

function ChiefLoopInner({ initial }: { initial: string }) {
  const [title, setTitle] = useState(initial)
  const close = () => setState({ chiefDraft: null })
  return (
    <Modal onClose={close} label="Add to Chief loops" className="small-modal">
      <form
        onSubmit={(e) => {
          e.preventDefault()
          void addChiefLoop(title)
        }}
      >
        <h2 className="modal-title">Add to Chief loops</h2>
        <label className="field">
          <span>Loop</span>
          <input id="chief-title" autoFocus value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <p className="field-help">Chief tracks it like your other open loops. Text “done #id” to close it.</p>
        <div className="modal-actions">
          <button type="button" className="btn" onClick={close}>
            Cancel
          </button>
          <button type="submit" className="btn is-primary" disabled={!title.trim()}>
            Add loop
          </button>
        </div>
      </form>
    </Modal>
  )
}

// ------------------------------------------------------------------ custom emoji

function CustomEmojiSettings() {
  const emoji = useStore((s) => s.customEmoji)
  return (
    <fieldset className="setting">
      <legend>Custom emoji</legend>
      {emoji.length > 0 ? (
        <div className="emoji-manage">
          {emoji.map((e) => (
            <span key={e.name} className="emoji-manage-item" title={`:${e.name}:`}>
              <img src={e.url} alt="" />
              <span className="emoji-manage-name">:{e.name}:</span>
              <button type="button" className="icon-btn" aria-label={`Remove :${e.name}:`} title="Remove" onClick={() => removeCustomEmoji(e.name)}>
                <Icon name="x" size={13} />
              </button>
            </span>
          ))}
        </div>
      ) : (
        <p className="field-help">None yet. Add your own images, or links from slackmojis.com, and send them with :name:.</p>
      )}
      <div>
        <button type="button" className="btn" onClick={() => setState({ emojiDialog: { fromSettings: true }, settingsOpen: false })}>
          <Icon name="plus" size={14} /> Add emoji
        </button>
      </div>
    </fieldset>
  )
}

export function AddEmojiModal() {
  const dialog = useStore((s) => s.emojiDialog)
  if (!dialog) return null
  return <AddEmojiInner key={dialog.sticker?.id ?? 'new'} sticker={dialog.sticker ?? null} fromSettings={!!dialog.fromSettings} />
}

function AddEmojiInner({ sticker, fromSettings }: { sticker: StickerItem | null; fromSettings: boolean }) {
  const existing = useStore((s) => s.customEmoji)
  const [mode, setMode] = useState<'upload' | 'link'>('upload')
  const [file, setFile] = useState<File | null>(null)
  const [url, setUrl] = useState('')
  const [name, setName] = useState(sticker ? suggestEmojiName(sticker.description ?? sticker.kind) : '')
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [filePreview, setFilePreview] = useState<string | null>(null)
  useEffect(() => {
    if (!file) return setFilePreview(null)
    const u = URL.createObjectURL(file)
    setFilePreview(u)
    return () => URL.revokeObjectURL(u)
  }, [file])
  const linkOk = /^https?:\/\/\S+$/i.test(url.trim())
  const preview = sticker ? sticker.url : mode === 'upload' ? filePreview : linkOk ? url.trim() : null
  const taken = existing.some((e) => e.name === name)
  const nameOk = EMOJI_NAME_RE.test(name) && !taken
  const sourceOk = sticker ? true : mode === 'upload' ? !!file : linkOk
  // Opened from Settings: go back there afterward.
  const close = () => setState({ emojiDialog: null, ...(fromSettings ? { settingsOpen: true } : {}) })

  const save = async () => {
    if (!nameOk || !sourceOk || saving) return
    setSaving(true)
    setError(null)
    try {
      if (sticker) await saveStickerAsEmoji(sticker.id, name)
      else if (mode === 'upload' && file) await addCustomEmoji(name, { file })
      else await addCustomEmoji(name, { url: url.trim() })
      close()
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't add that emoji.")
      setSaving(false)
    }
  }

  return (
    <Modal onClose={close} label="Add emoji" className="small-modal add-emoji">
      <form
        onSubmit={(e) => {
          e.preventDefault()
          void save()
        }}
      >
        <h2 className="modal-title">{sticker ? 'Save as a custom emoji' : 'Add a custom emoji'}</h2>
        {!sticker && (
          <div className="segmented" role="radiogroup" aria-label="Where the image comes from">
            <button type="button" role="radio" aria-checked={mode === 'upload'} className={mode === 'upload' ? 'is-on' : ''} onClick={() => setMode('upload')}>
              Upload
            </button>
            <button type="button" role="radio" aria-checked={mode === 'link'} className={mode === 'link' ? 'is-on' : ''} onClick={() => setMode('link')}>
              Image link
            </button>
          </div>
        )}
        <div className="add-emoji-row">
          <div className="add-emoji-preview" aria-hidden="true">
            {preview ? <img src={preview} alt="" /> : <Icon name="image" size={24} />}
          </div>
          <div className="add-emoji-fields">
            {!sticker && mode === 'upload' && (
              <label className="field">
                <span>Image</span>
                <input
                  id="emoji-file"
                  type="file"
                  accept="image/png,image/gif,image/jpeg,image/webp,image/heic"
                  onChange={(e) => {
                    const f = e.target.files?.[0] ?? null
                    setFile(f)
                    if (f && !name) setName(suggestEmojiName(f.name))
                  }}
                />
              </label>
            )}
            {!sticker && mode === 'link' && (
              <label className="field">
                <span>Image link</span>
                <input
                  id="emoji-url"
                  type="url"
                  placeholder="https://…/party.gif"
                  value={url}
                  onChange={(e) => {
                    setUrl(e.target.value)
                    if (!name) setName(suggestEmojiName(e.target.value))
                  }}
                />
              </label>
            )}
            <label className="field">
              <span>Name</span>
              <span className="emoji-name-input">
                <span>:</span>
                <input id="emoji-name" value={name} onChange={(e) => setName(e.target.value.toLowerCase().replace(/[^a-z0-9_+-]/g, '-'))} />
                <span>:</span>
              </span>
            </label>
          </div>
        </div>
        <p className="field-help">
          {taken
            ? `:${name}: is already taken.`
            : name && !EMOJI_NAME_RE.test(name)
              ? 'Use 2 to 40 lowercase letters, numbers, dashes or underscores.'
              : mode === 'link' && !sticker
                ? 'Paste a direct image link, like the one from right-clicking an emoji on slackmojis.com › Copy Image Address.'
                : 'Square images work best. GIFs stay animated.'}
        </p>
        {error && <p className="form-error">{error}</p>}
        <div className="modal-actions">
          <button type="button" className="btn" onClick={close}>
            Cancel
          </button>
          <button type="submit" className="btn is-primary" disabled={!nameOk || !sourceOk || saving}>
            {saving ? 'Saving…' : 'Save emoji'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

/** The things iMessage only accepts from the Messages app, done by operating it (macOS Accessibility). */
function MessagesHelpers() {
  const serverSettings = useStore((s) => s.serverSettings)
  const ui = useStore((s) => s.health?.messagesUi)
  const anyOn = !!serverSettings && (serverSettings.markReadInMessages || serverSettings.tapbacksViaMessages || serverSettings.threadRepliesViaMessages)
  const toggles: { key: keyof ServerSettings; id: string; label: string; help: string }[] = [
    {
      key: 'tapbacksViaMessages',
      id: 'tapbacks-messages',
      label: 'Send tapbacks',
      help: '❤️ 👍 👎 😂 ‼️ ❓ go out as real tapbacks. Backchannel right-clicks the message in Messages and picks the tapback.',
    },
    {
      key: 'threadRepliesViaMessages',
      id: 'replies-messages',
      label: 'Send thread replies as real replies',
      help: 'Your replies thread on everyone’s phone, not just here. Messages comes to the front for about a second while it sends.',
    },
    {
      key: 'markReadInMessages',
      id: 'mark-read-messages',
      label: 'Mark texts read in Messages',
      help: 'When you open a conversation here that Messages still shows as unread, it gets marked read there too, which syncs to your phone. Read receipts follow your Messages setting.',
    },
  ]
  return (
    <fieldset className="setting">
      <legend>
        Messages helpers <span className="beta-tag">Experimental</span>
      </legend>
      <p className="field-help setting-intro">
        iMessage only accepts these from the Messages app, so Backchannel operates Messages for you through macOS Accessibility. Allow
        Backchannel in System Settings › Privacy & Security › Accessibility. They work on recent messages and may need fixing after a macOS update.
      </p>
      {toggles.map((t) => (
        <label key={t.key} className="toggle-row">
          <input
            type="checkbox"
            id={t.id}
            checked={!!serverSettings?.[t.key]}
            disabled={!serverSettings}
            onChange={(e) => setServerSetting({ [t.key]: e.target.checked })}
          />
          <span>
            {t.label}
            <span className="field-help">{t.help}</span>
          </span>
        </label>
      ))}
      {ui && (anyOn || ui.lastResult) && (
        <p className={`field-help ui-status ${ui.permission === 'denied' ? 'is-bad' : ''}`}>
          {ui.permission === 'denied'
            ? 'macOS is blocking Accessibility for Backchannel. Turn it on in System Settings › Privacy & Security › Accessibility.'
            : ui.lastResult
              ? `Last try: ${ui.lastResult}`
              : 'Nothing tried yet.'}
        </p>
      )}
    </fieldset>
  )
}
