// Inline stroke icons (24×24 grid, currentColor). Kept local so the app works offline.
import type { ReactNode, SVGProps } from 'react'

const paths: Record<string, ReactNode> = {
  search: (
    <>
      <circle cx="11" cy="11" r="7.5" />
      <path d="m20.5 20.5-4.2-4.2" />
    </>
  ),
  x: (
    <>
      <path d="M18 6 6 18" />
      <path d="m6 6 12 12" />
    </>
  ),
  chevronDown: <path d="m6 9 6 6 6-6" />,
  chevronRight: <path d="m9 18 6-6-6-6" />,
  chevronLeft: <path d="m15 18-6-6 6-6" />,
  arrowLeft: (
    <>
      <path d="m12 19-7-7 7-7" />
      <path d="M19 12H5" />
    </>
  ),
  arrowDown: (
    <>
      <path d="M12 5v14" />
      <path d="m19 12-7 7-7-7" />
    </>
  ),
  plus: (
    <>
      <path d="M5 12h14" />
      <path d="M12 5v14" />
    </>
  ),
  minus: <path d="M5 12h14" />,
  home: (
    <>
      <path d="M3 10.5 12 3l9 7.5V20a1.5 1.5 0 0 1-1.5 1.5H15v-6.5H9v6.5H4.5A1.5 1.5 0 0 1 3 20z" />
    </>
  ),
  dms: (
    <>
      <path d="M21 14.5a2 2 0 0 1-2 2H8l-4.5 4V5.5a2 2 0 0 1 2-2H19a2 2 0 0 1 2 2z" />
      <path d="M8 9h9" />
      <path d="M8 12.5h6" />
    </>
  ),
  bell: (
    <>
      <path d="M6 8.5a6 6 0 0 1 12 0c0 6.5 2.5 8.5 2.5 8.5h-17S6 15 6 8.5" />
      <path d="M10.3 20.5a1.94 1.94 0 0 0 3.4 0" />
    </>
  ),
  bellOff: (
    <>
      <path d="M8.7 3.6A6 6 0 0 1 18 8.5c0 2.3.3 4 .8 5.3" />
      <path d="M17 17H3.5S6 15 6 8.5a5.6 5.6 0 0 1 .3-1.8" />
      <path d="M10.3 20.5a1.94 1.94 0 0 0 3.4 0" />
      <path d="m2.5 2.5 19 19" />
    </>
  ),
  bookmark: <path d="M18.5 21 12 17.2 5.5 21V5a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2z" />,
  star: <path d="m12 2.8 2.85 5.8 6.4.93-4.63 4.5 1.1 6.37L12 17.4l-5.72 3 1.1-6.37-4.63-4.5 6.4-.93z" />,
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3.2 1.9" />
    </>
  ),
  send: (
    <>
      <path d="M21.5 2.5 14.8 21l-3.6-8.2L3 9.2z" />
      <path d="m21.5 2.5-10.3 10.3" />
    </>
  ),
  smile: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M8.5 14.5s1.3 1.8 3.5 1.8 3.5-1.8 3.5-1.8" />
      <path d="M9 9.5h.01" />
      <path d="M15 9.5h.01" />
    </>
  ),
  smilePlus: (
    <>
      <path d="M21 12a9 9 0 1 1-8.2-9" />
      <path d="M8.5 14.5s1.3 1.8 3.5 1.8 3.5-1.8 3.5-1.8" />
      <path d="M9 9.5h.01" />
      <path d="M15 9.5h.01" />
      <path d="M16.5 5h5" />
      <path d="M19 2.5v5" />
    </>
  ),
  sparkles: (
    <>
      <path d="m12 3.5-1.7 5a2 2 0 0 1-1.3 1.3l-5 1.7 5 1.7a2 2 0 0 1 1.3 1.3l1.7 5 1.7-5a2 2 0 0 1 1.3-1.3l5-1.7-5-1.7a2 2 0 0 1-1.3-1.3z" />
      <path d="M5 3v3.5" />
      <path d="M3.25 4.75h3.5" />
      <path d="M19 17.5V21" />
      <path d="M17.25 19.25h3.5" />
    </>
  ),
  headphones: (
    <path d="M3.5 14H6a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H5.5a2 2 0 0 1-2-2v-7a8.5 8.5 0 0 1 17 0v7a2 2 0 0 1-2 2H18a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h2.5" />
  ),
  phone: (
    <path d="M21.5 16.4v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 1.6 3.7 2 2 0 0 1 3.6 1.5h3a2 2 0 0 1 2 1.7c.13.96.36 1.9.7 2.8a2 2 0 0 1-.45 2.1L7.6 9.4a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.1-.45c.9.34 1.84.57 2.8.7a2 2 0 0 1 1.73 2.02z" />
  ),
  video: (
    <>
      <path d="m22 8-6 4 6 4z" />
      <rect x="2" y="6" width="14" height="12" rx="2" />
    </>
  ),
  more: (
    <>
      <circle cx="5" cy="12" r="1.2" />
      <circle cx="12" cy="12" r="1.2" />
      <circle cx="19" cy="12" r="1.2" />
    </>
  ),
  moreV: (
    <>
      <circle cx="12" cy="5" r="1.2" />
      <circle cx="12" cy="12" r="1.2" />
      <circle cx="12" cy="19" r="1.2" />
    </>
  ),
  copy: (
    <>
      <rect x="8.5" y="8.5" width="12.5" height="12.5" rx="2" />
      <path d="M4.5 15.5A1.5 1.5 0 0 1 3 14V4.5A1.5 1.5 0 0 1 4.5 3H14a1.5 1.5 0 0 1 1.5 1.5" />
    </>
  ),
  check: <path d="M20 6 9 17l-5-5" />,
  checkCheck: (
    <>
      <path d="M17 6.5 6.5 17 2 12.5" />
      <path d="m22 10.5-6.5 6.5-1.5-1.5" />
    </>
  ),
  alert: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7.5v5" />
      <path d="M12 16.5h.01" />
    </>
  ),
  image: (
    <>
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <circle cx="9" cy="9" r="1.8" />
      <path d="m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21" />
    </>
  ),
  file: (
    <>
      <path d="M14.5 2.5H6.5a2 2 0 0 0-2 2v15a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V7.5z" />
      <path d="M14 2.5v5.5h5.5" />
    </>
  ),
  play: <path d="M7 4.5v15l12-7.5z" />,
  pause: (
    <>
      <path d="M8 5v14" />
      <path d="M16 5v14" />
    </>
  ),
  mic: (
    <>
      <rect x="9" y="2.5" width="6" height="12" rx="3" />
      <path d="M19 10.5v1a7 7 0 0 1-14 0v-1" />
      <path d="M12 18.5V22" />
    </>
  ),
  compose: (
    <>
      <path d="M12 3.5H5.5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h13a2 2 0 0 0 2-2V12" />
      <path d="M18.4 2.6a2 2 0 0 1 2.9 2.9l-8.6 8.6a2 2 0 0 1-.85.5l-3.1.9.9-3.1a2 2 0 0 1 .5-.85z" />
    </>
  ),
  sliders: (
    <>
      <path d="M20 5h-7" />
      <path d="M9 5H4" />
      <path d="M20 12h-9" />
      <path d="M7 12H4" />
      <path d="M20 19h-4" />
      <path d="M12 19H4" />
      <path d="M11 3v4" />
      <path d="M9 10v4" />
      <path d="M14 17v4" />
    </>
  ),
  hash: (
    <>
      <path d="M4.5 9h16" />
      <path d="M3.5 15h16" />
      <path d="M10 3.5 8 20.5" />
      <path d="M16 3.5 14 20.5" />
    </>
  ),
  at: (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M16 8v5a3 3 0 0 0 6 0v-1a10 10 0 1 0-3.9 7.9" />
    </>
  ),
  users: (
    <>
      <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
      <circle cx="9" cy="7" r="4" />
      <path d="M22 21v-2a4 4 0 0 0-3-3.9" />
      <path d="M16 3.1a4 4 0 0 1 0 7.8" />
    </>
  ),
  moon: <path d="M20.5 14.5A8.5 8.5 0 1 1 9.5 3.5a6.5 6.5 0 0 0 11 11" />,
  sun: (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2" />
      <path d="M12 20v2" />
      <path d="m4.9 4.9 1.4 1.4" />
      <path d="m17.7 17.7 1.4 1.4" />
      <path d="M2 12h2" />
      <path d="M20 12h2" />
      <path d="m6.3 17.7-1.4 1.4" />
      <path d="m19.1 4.9-1.4 1.4" />
    </>
  ),
  calendar: (
    <>
      <rect x="3" y="4.5" width="18" height="17" rx="2" />
      <path d="M16 2.5v4" />
      <path d="M8 2.5v4" />
      <path d="M3 10h18" />
    </>
  ),
  bot: (
    <>
      <path d="M12 8V4.5H9" />
      <rect x="4" y="8" width="16" height="12" rx="2.5" />
      <path d="M2 14h2" />
      <path d="M20 14h2" />
      <path d="M15 13v2" />
      <path d="M9 13v2" />
    </>
  ),
  key: (
    <>
      <circle cx="7.5" cy="15.5" r="4.5" />
      <path d="m10.7 12.3 8.8-8.8" />
      <path d="m16 7 3 3" />
      <path d="m18.5 4.5 2 2" />
    </>
  ),
  thread: (
    <>
      <path d="M21 14.5a2 2 0 0 1-2 2H8l-4.5 4V5.5a2 2 0 0 1 2-2H19a2 2 0 0 1 2 2z" />
    </>
  ),
  reply: (
    <>
      <path d="M9 14.5 4 9.5l5-5" />
      <path d="M20 19.5v-6a4 4 0 0 0-4-4H4" />
    </>
  ),
  listChecks: (
    <>
      <path d="m3 17 2 2 4-4" />
      <path d="m3 7 2 2 4-4" />
      <path d="M13 6h8" />
      <path d="M13 12h8" />
      <path d="M13 18h8" />
    </>
  ),
  dot: <circle cx="12" cy="12" r="4" />,
  external: (
    <>
      <path d="M15 3h6v6" />
      <path d="M10 14 21 3" />
      <path d="M18.5 13.5v5.5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7.5a2 2 0 0 1 2-2h5.5" />
    </>
  ),
  cloud: (
    <>
      <path d="M17.5 19H8a5.5 5.5 0 1 1 1.06-10.9A6 6 0 0 1 20.5 11 4 4 0 0 1 17.5 19Z" />
    </>
  ),
  download: (
    <>
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <path d="m7 10 5 5 5-5" />
      <path d="M12 15V3" />
    </>
  ),
  refresh: (
    <>
      <path d="M3 12a9 9 0 0 1 15.4-6.4L21 8" />
      <path d="M21 3v5h-5" />
      <path d="M21 12a9 9 0 0 1-15.4 6.4L3 16" />
      <path d="M8 16H3v5" />
    </>
  ),
  inbox: (
    <>
      <path d="M22 12h-6l-2 3h-4l-2-3H2" />
      <path d="M5.5 5.1 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.5-6.9a2 2 0 0 0-1.8-1.1H7.3a2 2 0 0 0-1.8 1.1" />
    </>
  ),
  help: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3" />
      <path d="M12 17h.01" />
    </>
  ),
  eye: (
    <>
      <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12" />
      <circle cx="12" cy="12" r="3" />
    </>
  ),
  filter: (
    <>
      <path d="M3 6h18" />
      <path d="M7 12h10" />
      <path d="M10 18h4" />
    </>
  ),
  edit: (
    <>
      <path d="M12 20h9" />
      <path d="M16.4 3.6a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" />
    </>
  ),
  pin: (
    <>
      <path d="M12 17v5" />
      <path d="M9 10.8a2 2 0 0 1-1.1 1.8l-1.8.9A2 2 0 0 0 5 15.2V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.8a2 2 0 0 0-1.1-1.8l-1.8-.9A2 2 0 0 1 15 10.8V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z" />
    </>
  ),
}

export type IconName = keyof typeof paths

export function Icon({
  name,
  size = 18,
  strokeWidth = 1.8,
  filled = false,
  ...rest
}: { name: IconName; size?: number; strokeWidth?: number; filled?: boolean } & SVGProps<SVGSVGElement>) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill={filled ? 'currentColor' : 'none'}
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {paths[name]}
    </svg>
  )
}

/** The app mark: two overlapping speech bubbles. */
export function Logo({ size = 36 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true">
      <rect width="64" height="64" rx="14" fill="var(--logo-bg)" />
      <path
        d="M17 20a6 6 0 0 1 6-6h18a6 6 0 0 1 6 6v12a6 6 0 0 1-6 6H29l-8 7v-7h2a6 6 0 0 1-6-6z"
        fill="var(--logo-fg)"
      />
      <path
        d="M27 42h12l8 7v-7a6 6 0 0 0 6-6V26"
        fill="none"
        stroke="var(--logo-accent)"
        strokeWidth="4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}
