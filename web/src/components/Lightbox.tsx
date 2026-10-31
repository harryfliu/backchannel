// Full-size photo/video viewer with zoom: pinch or scroll to zoom, drag to pan,
// click to zoom in, double-click or 0 to fit, + / - keys and buttons.
import { useCallback, useEffect, useRef, useState } from 'react'
import type { Attachment } from '../api/types'
import { fileSize } from '../lib/format'
import { Icon } from '../lib/icons'
import { isNative } from '../native'
import { Modal } from './common'

const MIN = 1
const MAX = 8
const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))

export function Lightbox({ a, fallbackSrc, onClose }: { a: Attachment; fallbackSrc: string | null; onClose: () => void }) {
  const isVideo = a.kind === 'video' && !a.url.startsWith('data:') && a.url !== '#'
  const src = a.url !== '#' ? a.url : (fallbackSrc ?? '')
  const stage = useRef<HTMLDivElement>(null)
  const [scale, setScale] = useState(1)
  const [pos, setPos] = useState({ x: 0, y: 0 })
  const [dragging, setDragging] = useState(false)
  const view = useRef({ scale: 1, x: 0, y: 0 })
  view.current = { scale, x: pos.x, y: pos.y }
  const drag = useRef<{ startX: number; startY: number; x: number; y: number; moved: boolean } | null>(null)

  /** Zoom to `next`, keeping the point under (clientX, clientY) in place. */
  const zoomTo = useCallback((next: number, clientX?: number, clientY?: number) => {
    const target = clamp(next, MIN, MAX)
    const { scale: cur, x, y } = view.current
    if (target === MIN) {
      setScale(MIN)
      setPos({ x: 0, y: 0 })
      return
    }
    let nx = x
    let ny = y
    const el = stage.current
    if (el && clientX !== undefined && clientY !== undefined) {
      const r = el.getBoundingClientRect()
      const ox = clientX - (r.left + r.width / 2)
      const oy = clientY - (r.top + r.height / 2)
      nx = ox - (ox - x) * (target / cur)
      ny = oy - (oy - y) * (target / cur)
    }
    setScale(target)
    setPos({ x: nx, y: ny })
  }, [])

  // Wheel and trackpad pinch (Chrome sends ctrl+wheel, Safari/WebKit sends gesture events).
  useEffect(() => {
    const el = stage.current
    if (!el || isVideo) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const factor = Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0025))
      zoomTo(view.current.scale * factor, e.clientX, e.clientY)
    }
    let base = 1
    type Gesture = Event & { scale: number; clientX?: number; clientY?: number }
    const onGestureStart = (e: Event) => {
      e.preventDefault()
      base = view.current.scale
    }
    const onGestureChange = (e: Event) => {
      e.preventDefault()
      const g = e as Gesture
      zoomTo(base * g.scale, g.clientX, g.clientY)
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    el.addEventListener('gesturestart', onGestureStart)
    el.addEventListener('gesturechange', onGestureChange)
    return () => {
      el.removeEventListener('wheel', onWheel)
      el.removeEventListener('gesturestart', onGestureStart)
      el.removeEventListener('gesturechange', onGestureChange)
    }
  }, [isVideo, zoomTo])

  // Keyboard: + / - / 0. (Escape is handled by the modal.)
  useEffect(() => {
    if (isVideo) return
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return
      if (e.key === '+' || e.key === '=') zoomTo(view.current.scale * 1.5)
      else if (e.key === '-' || e.key === '_') zoomTo(view.current.scale / 1.5)
      else if (e.key === '0') zoomTo(MIN)
      else return
      e.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [isVideo, zoomTo])

  const zoomed = scale > MIN + 0.001
  return (
    <Modal onClose={onClose} label={a.name} className="lightbox">
      <div className="lightbox-bar">
        <span className="lightbox-name">{a.name}</span>
        <span className="lightbox-size">{fileSize(a.size)}</span>
        {!isVideo && (
          <span className="lb-zoom" role="group" aria-label="Zoom">
            <button type="button" className="icon-btn" aria-label="Zoom out" title="Zoom out (−)" disabled={!zoomed} onClick={() => zoomTo(scale / 1.5)}>
              <Icon name="minus" size={16} />
            </button>
            <button type="button" className="lb-level" title="Fit to window (0)" onClick={() => zoomTo(MIN)}>
              {zoomed ? `${Math.round(scale * 100)}%` : 'Fit'}
            </button>
            <button type="button" className="icon-btn" aria-label="Zoom in" title="Zoom in (+)" disabled={scale >= MAX} onClick={() => zoomTo(scale * 1.5)}>
              <Icon name="plus" size={16} />
            </button>
          </span>
        )}
        <a className="icon-btn" href={a.url} target="_blank" rel="noreferrer" aria-label={isNative ? 'Open in Preview' : 'Open original'} title={isNative ? 'Open in Preview' : 'Open original'}>
          <Icon name="external" size={18} />
        </a>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="Close">
          <Icon name="x" size={18} />
        </button>
      </div>
      {isVideo ? (
        <video src={a.url} controls autoPlay className="lightbox-media" />
      ) : (
        <div
          ref={stage}
          className={`lb-stage ${zoomed ? 'is-zoomed' : ''} ${dragging ? 'is-dragging' : ''}`}
          onPointerDown={(e) => {
            if (e.button !== 0) return
            drag.current = { startX: e.clientX, startY: e.clientY, x: pos.x, y: pos.y, moved: false }
            ;(e.currentTarget as HTMLDivElement).setPointerCapture(e.pointerId)
          }}
          onPointerMove={(e) => {
            const d = drag.current
            if (!d || !zoomed) return
            const dx = e.clientX - d.startX
            const dy = e.clientY - d.startY
            if (!d.moved && Math.hypot(dx, dy) < 4) return
            d.moved = true
            setDragging(true)
            setPos({ x: d.x + dx, y: d.y + dy })
          }}
          onPointerUp={(e) => {
            const d = drag.current
            drag.current = null
            setDragging(false)
            // A plain click zooms in where you clicked.
            if (d && !d.moved && !zoomed) zoomTo(2.5, e.clientX, e.clientY)
          }}
          onDoubleClick={(e) => {
            if (zoomed) zoomTo(MIN)
            else zoomTo(2.5, e.clientX, e.clientY)
          }}
        >
          <img
            src={src}
            alt={a.name}
            draggable={false}
            className="lb-img"
            style={{ transform: `translate(${pos.x}px, ${pos.y}px) scale(${scale})` }}
          />
        </div>
      )}
      {!isVideo && <p className="lb-hint">Pinch or scroll to zoom · drag to move · double-click to fit</p>}
    </Modal>
  )
}
