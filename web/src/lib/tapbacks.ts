// The six classic tapbacks and iMessage's rule for them: one per person per message.
import type { ClassicTapback, Reaction, TapbackKind } from '../api/types'

export const TAPBACKS: { kind: ClassicTapback; emoji: string; label: string }[] = [
  { kind: 'love', emoji: '❤️', label: 'Love' },
  { kind: 'like', emoji: '👍', label: 'Like' },
  { kind: 'dislike', emoji: '👎', label: 'Dislike' },
  { kind: 'laugh', emoji: '😂', label: 'Ha ha' },
  { kind: 'emphasize', emoji: '‼️', label: 'Emphasize' },
  { kind: 'question', emoji: '❓', label: 'Question' },
]

export const isClassic = (kind: TapbackKind): kind is ClassicTapback => TAPBACKS.some((t) => t.kind === kind)

export const tapbackEmoji = (kind: ClassicTapback) => TAPBACKS.find((t) => t.kind === kind)!.emoji

/** Your current tapback on a message (stickers don't count; you can place several). */
export function myTapback(reactions: Reaction[]): TapbackKind | null {
  return reactions.find((r) => r.kind !== 'sticker' && r.from.includes('me'))?.kind ?? null
}

/** Reactions after you add (replacing any tapback you had) or remove a tapback. */
export function applyTapback(reactions: Reaction[], kind: ClassicTapback, remove: boolean): Reaction[] {
  const out = reactions
    .map((r) => (r.kind === 'sticker' ? r : { ...r, from: r.from.filter((h) => h !== 'me') }))
    .filter((r) => r.from.length > 0)
  if (remove) return out
  const i = out.findIndex((r) => r.kind === kind)
  if (i === -1) return [...out, { kind, emoji: tapbackEmoji(kind), from: ['me'] }]
  out[i] = { ...out[i], from: [...out[i].from, 'me'] }
  return out
}
