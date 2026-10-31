// Bridge to the Mac app (mac/Backchannel.swift). In a normal browser every function here does nothing.

type NativeMessage =
  | { type: 'badge'; count: number }
  | { type: 'theme'; frame: string }
  | { type: 'notify'; title: string; subtitle?: string; body: string; conversationId: string }
  | { type: 'requestNotifications' }
  | { type: 'requestAccessibility' }
  | { type: 'attention' }
  | { type: 'retry' }
  | { type: 'quit' }

interface Bridge {
  postMessage(message: unknown): void
}

declare global {
  interface Window {
    __BC_NATIVE__?: { version: number }
    __bcOpen?: (conversationId: string) => void
    __bcCommand?: (name: string) => void
    __bcNotifyPermission?: (granted: boolean) => void
    __bcAccessibility?: (trusted: boolean) => void
  }
}

function bridge(): Bridge | undefined {
  return (window as unknown as { webkit?: { messageHandlers?: { bc?: Bridge } } }).webkit?.messageHandlers?.bc
}

/** True inside the Mac app. */
export const isNative = typeof window !== 'undefined' && (!!window.__BC_NATIVE__ || !!bridge())

export function postNative(message: NativeMessage) {
  try {
    bridge()?.postMessage(message)
  } catch {
    // not inside the Mac app
  }
}

/** Hooks the Mac app calls: open a conversation from a notification, menu commands, permission answers. */
export function initNative(hooks: { open: (id: string) => void; command: (name: string) => void; permission: (granted: boolean) => void }) {
  if (!isNative) return
  document.documentElement.classList.add('is-native')
  window.__bcOpen = hooks.open
  window.__bcCommand = hooks.command
  window.__bcNotifyPermission = hooks.permission
}

/** Paint the window's title bar with the current frame color (it changes with theme and sidebar color). */
export function syncTheme() {
  if (!isNative) return
  const frame = getComputedStyle(document.documentElement).getPropertyValue('--frame').trim()
  if (/^#[0-9a-f]{6}$/i.test(frame)) postNative({ type: 'theme', frame })
}

let lastBadge = -1
/** The red number on the Dock icon. */
export function syncBadge(count: number) {
  if (!isNative || count === lastBadge) return
  lastBadge = count
  postNative({ type: 'badge', count })
}
