import { useEffect, useMemo } from 'react'
import { isMock } from './api/client'
import { Icon, Logo } from './lib/icons'
import { initNative, isNative, postNative, syncBadge, syncTheme } from './native'
import {
  closePanel,
  getState,
  goBack,
  goForward,
  moveConversation,
  openConversation,
  openSearch,
  setRail,
  setSettings,
  setState,
  toast,
  useStore,
  type RailTab,
} from './state/store'
import { ConversationView } from './components/Conversation'
import { RightPanel } from './components/Panels'
import { Rail, SidebarColumn, useSections } from './components/Sidebar'
import { CatchUpView, ScheduledView, ThreadsView } from './components/Views'
import {
  ChiefLoopModal,
  ComposeModal,
  openTour,
  QuickSwitcher,
  SearchModal,
  SettingsModal,
  ShortcutsModal,
  Toasts,
  TourModal,
} from './components/Overlays'
import { Spinner } from './components/common'

export function App() {
  const booted = useStore((s) => s.booted)
  const bootError = useStore((s) => s.bootError)
  const settings = useStore((s) => s.settings)
  const panel = useStore((s) => s.panel)
  const mobilePane = useStore((s) => s.mobilePane)
  const sections = useSections()
  const setup = useStore((s) => s.setup)
  const conversations = useStore((s) => s.conversations)

  // Theme, sidebar color and density live on <html> so the CSS tokens can switch.
  useEffect(() => {
    const root = document.documentElement
    if (settings.theme === 'system') delete root.dataset.bcTheme
    else root.dataset.bcTheme = settings.theme
    root.dataset.bcSidebar = settings.sidebarTheme
    root.dataset.bcDensity = settings.compact ? 'compact' : 'comfortable'
    syncTheme()
  }, [settings])

  // Mac app: hooks for notifications and menu commands, and keep the title bar color in sync with dark mode.
  useEffect(() => {
    initNative({
      open: (id) => openConversation(id),
      command: (name) => {
        if (name === 'settings') setState({ settingsOpen: true })
        else if (name === 'compose') setState({ composeOpen: true })
        else if (name === 'shortcuts') setState({ shortcutsOpen: true })
      },
      permission: (granted) => {
        if (!granted) {
          setSettings({ notify: false })
          toast('Notifications are off for Backchannel. Turn them on in System Settings › Notifications.', 'error')
        }
      },
    })
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = () => syncTheme()
    mq.addEventListener('change', onChange)
    syncTheme()
    return () => mq.removeEventListener('change', onChange)
  }, [])

  // Dock badge: unread DMs plus unread mentions, like Slack.
  const badge = useMemo(
    () =>
      Object.values(conversations).reduce((n, c) => {
        if (c.prefs.muted || c.isAutomated) return n
        return n + (c.kind === 'dm' ? c.unreadCount : c.mentionCount)
      }, 0),
    [conversations],
  )
  useEffect(() => syncBadge(badge), [badge])

  useGlobalKeys(sections.order)

  if (!booted) {
    return (
      <div className="boot">
        <Spinner label="Loading your messages" />
        <p>Loading your messages…</p>
      </div>
    )
  }
  if (setup === 'fda') return <FullDiskAccess />
  if (bootError) return <BootError message={bootError} />

  return (
    <div className={`app pane-${mobilePane} ${panel ? 'has-panel' : ''}`}>
      <TopBar />
      <Rail />
      <div className="shell">
        <SidebarColumn />
        <main className="main">
          <MainView />
        </main>
        <RightPanel />
      </div>
      <TabBar />
      <QuickSwitcher />
      <SearchModal />
      <ComposeModal />
      <SettingsModal />
      <ShortcutsModal />
      <TourModal />
      <ChiefLoopModal />
      <Toasts />
    </div>
  )
}

function MainView() {
  const view = useStore((s) => s.view)
  const c = useStore((s) => (s.view?.kind === 'conversation' ? s.conversations[s.view.id] : undefined))
  if (!view) {
    return (
      <div className="msg-list is-empty">
        <p className="empty-title">Pick a conversation</p>
        <p className="empty-sub">Or press ⌘K to jump to anyone.</p>
      </div>
    )
  }
  if (view.kind === 'catchup') return <CatchUpView />
  if (view.kind === 'threads') return <ThreadsView />
  if (view.kind === 'scheduled') return <ScheduledView />
  if (!c) return null
  return <ConversationView key={c.id} c={c} />
}

function TopBar() {
  const canBack = useStore((s) => s.historyIndex > 0)
  const canFwd = useStore((s) => s.historyIndex < s.history.length - 1)
  const connection = useStore((s) => s.connection)
  const me = useStore((s) => s.me)
  return (
    <header className="topbar">
      <div className="topbar-nav">
        <button type="button" className="icon-btn" aria-label="Back" title="Back" disabled={!canBack} onClick={goBack}>
          <Icon name="arrowLeft" size={18} />
        </button>
        <button type="button" className="icon-btn" aria-label="Forward" title="Forward" disabled={!canFwd} onClick={goForward}>
          <Icon name="arrowLeft" size={18} style={{ transform: 'scaleX(-1)' }} />
        </button>
      </div>
      <button type="button" className="topbar-search" onClick={() => openSearch(null)}>
        <Icon name="search" size={15} />
        <span>Search {me ? `${me.shortName}’s messages` : 'messages'}</span>
        <kbd>⌘G</kbd>
      </button>
      <div className="topbar-right">
        {isMock && (
          <button type="button" className="sample-pill" title="Everyone and every message here is made up" onClick={openTour}>
            Sample data · Tour
          </button>
        )}
        {connection === 'reconnecting' && (
          <span className="reconnecting">
            <Spinner /> Reconnecting to your Mac…
          </span>
        )}
        <button type="button" className="icon-btn" aria-label="Keyboard shortcuts" title="Keyboard shortcuts" onClick={() => setState({ shortcutsOpen: true })}>
          <Icon name="help" size={18} />
        </button>
      </div>
    </header>
  )
}

function TabBar() {
  const rail = useStore((s) => s.rail)
  const tabs: { id: RailTab; icon: 'home' | 'dms' | 'bell' | 'bookmark'; label: string }[] = [
    { id: 'home', icon: 'home', label: 'Home' },
    { id: 'dms', icon: 'dms', label: 'DMs' },
    { id: 'activity', icon: 'bell', label: 'Activity' },
    { id: 'later', icon: 'bookmark', label: 'Later' },
  ]
  return (
    <nav className="tabbar" aria-label="Main">
      {tabs.map((t) => (
        <button key={t.id} type="button" className={rail === t.id ? 'is-active' : ''} onClick={() => setRail(t.id)}>
          <Icon name={t.icon} size={22} filled={rail === t.id && t.icon !== 'dms'} />
          <span>{t.label}</span>
        </button>
      ))}
    </nav>
  )
}

function BootError({ message }: { message: string }) {
  return (
    <div className="boot is-error">
      <Icon name="alert" size={28} />
      <h1>Backchannel can’t reach your messages</h1>
      <p>{message}</p>
      <ol>
        <li>Make sure the server is running: <code>./backchannel status</code></li>
        <li>Give your terminal Full Disk Access in System Settings › Privacy & Security, then restart it.</li>
      </ol>
      <button type="button" className="btn is-primary" onClick={() => window.location.reload()}>
        Try again
      </button>
    </div>
  )
}

/** Shown until macOS lets Backchannel read the Messages database. */
function FullDiskAccess() {
  const settingsUrl = 'x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles'
  const who = isNative ? 'Backchannel' : 'the terminal app you start Backchannel from (Terminal, iTerm…)'
  return (
    <div className="boot setup">
      <Logo size={56} />
      <h1>Let Backchannel read your messages</h1>
      <p>macOS keeps your texts private until you allow it. This takes a minute, once.</p>
      <ol>
        <li>
          Open <strong>System Settings › Privacy & Security › Full Disk Access</strong>.{' '}
          <a href={settingsUrl}>Open it for me</a>
        </li>
        <li>
          Turn on <strong>{who}</strong>. If it isn’t listed, click <strong>+</strong> and choose it
          {isNative ? ' from Applications' : ''}.
        </li>
        <li>
          {isNative ? (
            <>Quit Backchannel and open it again, so macOS applies the change.</>
          ) : (
            <>
              Restart Backchannel: <code>./backchannel off && tmux kill-server && ./backchannel on</code>
            </>
          )}
        </li>
      </ol>
      <div className="setup-actions">
        {isNative ? (
          <button type="button" className="btn is-primary" onClick={() => postNative({ type: 'quit' })}>
            Quit Backchannel
          </button>
        ) : (
          <button type="button" className="btn is-primary" onClick={() => window.location.reload()}>
            I’ve restarted it
          </button>
        )}
      </div>
    </div>
  )
}

function useGlobalKeys(order: string[]) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey
      const s = getState()
      const modalOpen = s.switcherOpen || s.search.open || s.composeOpen || s.settingsOpen || s.shortcutsOpen
      if (mod && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setState({ switcherOpen: !s.switcherOpen })
        return
      }
      if (mod && e.key.toLowerCase() === 'g') {
        e.preventDefault()
        openSearch(null)
        return
      }
      if (mod && e.key === '/') {
        e.preventDefault()
        setState({ shortcutsOpen: true })
        return
      }
      if (modalOpen) return
      if (e.altKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
        e.preventDefault()
        moveConversation(e.key === 'ArrowDown' ? 1 : -1, e.shiftKey, order)
        return
      }
      const target = e.target as HTMLElement
      const typing = target.closest('input, textarea, [contenteditable="true"]')
      if (e.key === 'Escape' && !typing && s.panel) {
        closePanel()
        return
      }
      // Typing anywhere starts a message, like Slack.
      if (!typing && !mod && !e.altKey && e.key.length === 1 && s.view?.kind === 'conversation') {
        document.querySelector<HTMLTextAreaElement>('.main .composer textarea')?.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [order])
}
