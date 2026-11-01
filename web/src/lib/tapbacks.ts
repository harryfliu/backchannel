// Tapbacks: the six classics, or any emoji (iOS 18 / macOS 15), and iMessage's rule for them: one
// per person per message (stickers placed on a message don't count).
import type { ClassicTapback, Reaction, TapbackKind } from '../api/types'

export const TAPBACKS: { kind: ClassicTapback; emoji: string; label: string }[] = [
  { kind: 'love', emoji: '❤️', label: 'Love' },
  { kind: 'like', emoji: '👍', label: 'Like' },
  { kind: 'dislike', emoji: '👎', label: 'Dislike' },
  { kind: 'laugh', emoji: '😂', label: 'Ha ha' },
  { kind: 'emphasize', emoji: '‼️', label: 'Emphasize' },
  { kind: 'question', emoji: '❓', label: 'Question' },
]

/** A tapback you can send: a classic, or an emoji. */
export type TapbackChoice = { kind: ClassicTapback; emoji?: undefined } | { kind: 'emoji'; emoji: string }

export const isClassic = (kind: TapbackKind): kind is ClassicTapback => TAPBACKS.some((t) => t.kind === kind)

export const tapbackEmoji = (kind: ClassicTapback) => TAPBACKS.find((t) => t.kind === kind)!.emoji

/** Equal apart from U+FE0F (emoji presentation), which Messages may add or drop. */
export const sameEmoji = (a: string | null | undefined, b: string | null | undefined) =>
  (a ?? '').replace(/️/g, '') === (b ?? '').replace(/️/g, '')

export function sameChoice(a: TapbackChoice | null, b: TapbackChoice | null) {
  if (!a || !b || a.kind !== b.kind) return false
  return a.kind !== 'emoji' || sameEmoji(a.emoji, b.emoji)
}

/** Your current tapback on a message (stickers don't count: you can place several). */
export function myTapback(reactions: Reaction[]): TapbackChoice | null {
  const r = reactions.find((x) => x.kind !== 'sticker' && x.from.includes('me'))
  if (!r) return null
  if (r.kind === 'emoji') return { kind: 'emoji', emoji: r.emoji }
  return isClassic(r.kind) ? { kind: r.kind } : null
}

/** What to draw for a choice. */
export const choiceEmoji = (c: TapbackChoice) => (c.kind === 'emoji' ? c.emoji : tapbackEmoji(c.kind))

/** Reactions after you add (replacing any tapback you had) or remove a tapback. */
export function applyTapback(reactions: Reaction[], choice: TapbackChoice, remove: boolean): Reaction[] {
  const out = reactions
    .map((r) => (r.kind === 'sticker' ? r : { ...r, from: r.from.filter((h) => h !== 'me') }))
    .filter((r) => r.from.length > 0)
  if (remove) return out
  const i = out.findIndex((r) => r.kind === choice.kind && (choice.kind !== 'emoji' || sameEmoji(r.emoji, choice.emoji)))
  if (i === -1) return [...out, { kind: choice.kind, emoji: choiceEmoji(choice), from: ['me'] }]
  out[i] = { ...out[i], from: [...out[i].from, 'me'] }
  return out
}
