// Emoji for the picker and the composer's :name: autocomplete, with Slack-style :short_names:.
// The full set (every emoji with its short names and keywords, built by scripts/build-emoji-data.mjs
// from emojibase-data) loads on first use; until then a small list of common ones answers.
// Custom emoji (your own images) come from the server and are matched separately.
import { useSyncExternalStore } from 'react'

// Common emoji, most used first: the picker's "Frequently used" until you've used some yourself,
// and what search and :name: answer from before the full set has loaded.
export const POPULAR: [name: string, char: string][] = [
  ['joy', '😂'],
  ['heart', '❤️'],
  ['thumbsup', '👍'],
  ['pray', '🙏'],
  ['sob', '😭'],
  ['fire', '🔥'],
  ['tada', '🎉'],
  ['sweat_smile', '😅'],
  ['face_holding_back_tears', '🥹'],
  ['heart_eyes', '😍'],
  ['saluting_face', '🫡'],
  ['raised_hands', '🙌'],
  ['eyes', '👀'],
  ['100', '💯'],
  ['handshake', '🤝'],
  ['open_mouth', '😮'],
  ['thinking_face', '🤔'],
  ['sleeping', '😴'],
  ['partying_face', '🥳'],
  ['smile', '😄'],
  ['grin', '😁'],
  ['rolling_on_the_floor_laughing', '🤣'],
  ['wink', '😉'],
  ['blush', '😊'],
  ['kissing_heart', '😘'],
  ['star-struck', '🤩'],
  ['hugging_face', '🤗'],
  ['shushing_face', '🤫'],
  ['melting_face', '🫠'],
  ['face_with_raised_eyebrow', '🤨'],
  ['neutral_face', '😐'],
  ['face_with_rolling_eyes', '🙄'],
  ['smirk', '😏'],
  ['sunglasses', '😎'],
  ['nerd_face', '🤓'],
  ['cry', '😢'],
  ['angry', '😠'],
  ['scream', '😱'],
  ['flushed', '😳'],
  ['face_palm', '🤦'],
  ['shrug', '🤷'],
  ['skull', '💀'],
  ['clown_face', '🤡'],
  ['ghost', '👻'],
  ['see_no_evil', '🙈'],
  ['thumbsdown', '👎'],
  ['ok_hand', '👌'],
  ['clap', '👏'],
  ['wave', '👋'],
  ['muscle', '💪'],
  ['point_up', '☝️'],
  ['brain', '🧠'],
  ['orange_heart', '🧡'],
  ['yellow_heart', '💛'],
  ['green_heart', '💚'],
  ['blue_heart', '💙'],
  ['purple_heart', '💜'],
  ['broken_heart', '💔'],
  ['sparkles', '✨'],
  ['star', '⭐'],
  ['boom', '💥'],
  ['confetti_ball', '🎊'],
  ['balloon', '🎈'],
  ['gift', '🎁'],
  ['birthday', '🎂'],
  ['trophy', '🏆'],
  ['sports_medal', '🏅'],
  ['white_check_mark', '✅'],
  ['x', '❌'],
  ['warning', '⚠️'],
  ['question', '❓'],
  ['exclamation', '❗'],
  ['bulb', '💡'],
  ['moneybag', '💰'],
  ['rocket', '🚀'],
  ['car', '🚗'],
  ['airplane', '✈️'],
  ['house', '🏠'],
  ['sunny', '☀️'],
  ['rainbow', '🌈'],
  ['snowflake', '❄️'],
  ['coffee', '☕'],
  ['beer', '🍺'],
  ['wine_glass', '🍷'],
  ['champagne', '🍾'],
  ['pizza', '🍕'],
  ['taco', '🌮'],
  ['ramen', '🍜'],
  ['sushi', '🍣'],
  ['cake', '🍰'],
  ['cookie', '🍪'],
  ['turkey', '🦃'],
  ['jack_o_lantern', '🎃'],
  ['christmas_tree', '🎄'],
  ['dog', '🐶'],
  ['cat', '🐱'],
  ['soccer', '⚽'],
  ['basketball', '🏀'],
  ['football', '🏈'],
  ['runner', '🏃'],
  ['zzz', '💤'],
  ['iphone', '📱'],
  ['calendar', '📅'],
  ['pushpin', '📌'],
  ['key', '🔑'],
  ['mag', '🔍'],
  ['bell', '🔔'],
  ['musical_note', '🎵'],
]

// Short names Backchannel used before the full set, kept so old habits still work.
const ALIASES: Record<string, string> = {
  pleading: '🥺',
  thinking: '🤔',
  rofl: '🤣',
  star_struck: '🤩',
  hug: '🤗',
  shushing: '🤫',
  melting: '🫠',
  raised_eyebrow: '🤨',
  eye_roll: '🙄',
  nerd: '🤓',
  facepalm: '🤦',
  clown: '🤡',
  salute: '🫡',
  medal: '🏅',
  sun: '☀️',
  running: '🏃',
  phone: '📱',
  music: '🎵',
}

// Texting words that mean an emoji without naming it. Listed emoji come first, in this order.
const SLANG: Record<string, string> = {
  lol: '😂 🤣 😆',
  lmao: '🤣 😂 💀',
  haha: '😂 😆 😄',
  hehe: '😆 😁 🤭',
  dead: '💀 😭 🤣',
  ded: '💀',
  crying: '😭 😢 🥲',
  sad: '😢 😭 😞 🥲',
  yay: '🎉 🥳 🙌',
  woo: '🎉 🥳 🙌',
  woohoo: '🎉 🥳',
  congrats: '🎉 🥳 🎊 👏',
  party: '🥳 🎉 🎊',
  rip: '💀 🪦 🥀',
  ty: '🙏 🫶',
  thx: '🙏 🫶',
  thanks: '🙏 🫶 ❤️',
  pls: '🙏 🥺',
  please: '🙏 🥺',
  omg: '😱 😮 🤯',
  wow: '😮 🤯 😲',
  goat: '🐐',
  lit: '🔥 💯',
  love: '❤️ 😍 🥰 😘',
  luv: '❤️ 🥰',
  ily: '❤️ 🥰 😘',
  happy: '😀 😄 😊 🥰',
  excited: '🤩 🥳 😆',
  tired: '😴 🥱 😩 😫',
  sleepy: '😴 🥱 😪',
  mad: '😠 😡 🤬',
  ugh: '😩 😤 🙄',
  annoyed: '🙄 😤 😒',
  sick: '🤒 🤢 🤧',
  gross: '🤢 🤮',
  yum: '😋 🤤',
  hungry: '🤤 😋',
  cool: '😎 🆒',
  ok: '👌 👍 🆗',
  okay: '👌 👍',
  yes: '✅ 👍 🙌',
  yep: '👍 ✅',
  no: '❌ 🙅 👎',
  nope: '🙅 ❌',
  idk: '🤷',
  hmm: '🤔 🧐',
  sus: '🤨 👀',
  awkward: '😬 😅',
  oops: '😬 😅 🙈',
  sorry: '🥺 😔 🙏',
  cute: '🥰 🥹 🥺',
  aww: '🥹 🥰 🥺',
  cheers: '🍻 🥂',
  drinks: '🍻 🍷 🥂',
  money: '💰 💸 🤑',
  hot: '🥵 🔥',
  cold: '🥶 ❄️',
  bday: '🎂 🥳 🎉',
  hbd: '🎂 🥳 🎉',
  hope: '🤞 🙏',
  luck: '🍀 🤞',
  strong: '💪',
  gym: '💪 🏋️',
  bye: '👋',
  hi: '👋 😊',
  hey: '👋 😊',
  nervous: '😬 😰',
  scared: '😱 😨',
  stressed: '😩 😰 😫',
  bored: '🥱 😑',
  meh: '😐 🫤 😑',
  confused: '😕 🤔 🫤',
  shy: '😳 🙈',
  win: '🏆 🥇 🎉',
  hype: '🙌 🔥 🚀',
  ship: '🚀 🚢',
  baby: '👶 🍼',
}

export interface EmojiEntry {
  char: string
  names: string[] // short names, Slack's first
  tags: string[] // keywords: CLDR annotations and emojibase tags
  group: number // index into EmojiData.groups (-1: not in the full set yet)
}

export interface EmojiData {
  ready: boolean
  groups: string[]
  entries: EmojiEntry[]
  byChar: Map<string, EmojiEntry>
  byName: Map<string, EmojiEntry>
}

function makeData(entries: EmojiEntry[], groups: string[], ready: boolean): EmojiData {
  const byChar = new Map<string, EmojiEntry>()
  const byName = new Map<string, EmojiEntry>()
  for (const e of entries) {
    if (!byChar.has(e.char)) byChar.set(e.char, e)
    for (const n of e.names) if (!byName.has(n)) byName.set(n, e)
  }
  for (const [alias, char] of Object.entries(ALIASES)) {
    const e = byChar.get(char)
    if (e && !byName.has(alias)) byName.set(alias, e)
  }
  return { ready, groups, entries, byChar, byName }
}

let data: EmojiData = makeData(
  POPULAR.map(([name, char]) => ({ char, names: [name], tags: [], group: -1 })),
  [],
  false,
)
let loading: Promise<void> | null = null
const listeners = new Set<() => void>()

/** Load the full set (a separate chunk of the app, bundled: nothing is fetched from the internet). */
export function loadEmojiData(): Promise<void> {
  if (!loading) {
    loading = import('./emoji-data.json')
      .then((mod) => {
        const raw = (mod.default ?? mod) as { groups: string[]; emoji: [string, string, string, number][] }
        const entries = raw.emoji.map(([char, names, tags, group]) => ({
          char,
          names: names.split(' '),
          tags: tags ? tags.split(' ') : [],
          group,
        }))
        data = makeData(entries, raw.groups, true)
        listeners.forEach((l) => l())
      })
      .catch(() => {
        loading = null // try again next time
      })
  }
  return loading
}

/** The emoji set, re-rendering once the full one has loaded (and starting that load). */
export function useEmojiData(): EmojiData {
  const current = useSyncExternalStore(
    (l) => {
      listeners.add(l)
      return () => listeners.delete(l)
    },
    () => data,
  )
  if (!current.ready) void loadEmojiData()
  return current
}

export function emojiData(): EmojiData {
  return data
}

/** The short name to show for an emoji character (its first, Slack-style). */
export function emojiName(char: string): string {
  return data.byChar.get(char)?.names[0] ?? POPULAR.find(([, c]) => c === char)?.[0] ?? ''
}

/** An emoji's names as words ("face with tears of joy", "joy"): how Messages' emoji picker may label it. */
export function emojiLabels(char: string): string[] {
  const entry = data.byChar.get(char) ?? data.byChar.get(char.replace(/\uFE0F/g, ''))
  const names = entry?.names ?? POPULAR.filter(([, c]) => c === char).map(([n]) => n)
  return [...new Set(names.map((n) => n.replace(/_/g, ' ')))].sort((a, b) => b.length - a.length).slice(0, 6)
}

// ------------------------------------------------------------------ search

export interface EmojiHit {
  char: string
  name: string // the short name to show (:name:)
  via?: string // the keyword that matched, when it isn't the short name ("tired" for 😴)
}

const POPULAR_RANK = new Map(POPULAR.map(([, char], i) => [char, i]))

/**
 * Emoji for a search or a :name: being typed, best first: an exact short name, a texting word
 * ("lol", "ty"), a short name starting with it, a keyword ("tired" → 😴 🥱 😩), a keyword starting
 * with it, then a short name containing it. Several words must all match ("red heart").
 */
export function searchEmoji(query: string, limit = 40, set: EmojiData = data): EmojiHit[] {
  const q = query.trim().toLowerCase().replace(/^:+|:+$/g, '')
  if (!q) return []
  const asName = q.replace(/[\s-]+/g, '_')
  const words = q.split(/[\s_:-]+/).filter(Boolean)
  // tier: how it matched (lower is better); rank: order within a tier where it matters (texting words).
  const hits = new Map<string, { tier: number; rank: number; hit: EmojiHit }>()
  const add = (entry: EmojiEntry | undefined, tier: number, opts: { name?: string; via?: string; rank?: number } = {}) => {
    if (!entry) return
    const have = hits.get(entry.char)
    if (have && have.tier <= tier) return
    hits.set(entry.char, {
      tier,
      rank: opts.rank ?? 0,
      hit: { char: entry.char, name: opts.name ?? entry.names[0], via: opts.via },
    })
  }
  const named = set.byName.get(asName) ?? set.byName.get(q)
  add(named, 0, { name: asName }) // show the name that was typed (:thumbsup:, not :+1:)
  const slang = SLANG[asName]
  if (slang) {
    slang.split(' ').forEach((char, i) => {
      const entry = set.byChar.get(char) ?? { char, names: [emojiName(char) || asName], tags: [], group: -1 }
      add(entry, 1, { via: entry.names.includes(asName) ? undefined : asName, rank: i })
    })
  }
  for (const entry of set.entries) {
    const prefixed = entry.names.find((n) => n.startsWith(asName))
    if (prefixed) add(entry, 2, { name: prefixed })
    else if (words.length && words.every((w) => entry.tags.includes(w) || entry.names.some((n) => n.split('_').includes(w))))
      add(entry, 3, { via: q })
    else if (q.length >= 3 && words.every((w) => entry.tags.some((t) => t.startsWith(w)) || entry.names.some((n) => n.includes(w))))
      add(entry, 4, { via: entry.tags.find((t) => t.startsWith(words[0])) ?? q })
    else if (asName.length >= 3 && entry.names.some((n) => n.includes(asName))) add(entry, 5)
  }
  for (const [alias, char] of Object.entries(ALIASES)) {
    if (alias.startsWith(asName)) add(set.byChar.get(char), 2, { name: alias })
  }
  const order = new Map(set.entries.map((e, i) => [e.char, i]))
  return [...hits.values()]
    .sort(
      (a, b) =>
        a.tier - b.tier ||
        a.rank - b.rank ||
        (POPULAR_RANK.get(a.hit.char) ?? 999) - (POPULAR_RANK.get(b.hit.char) ?? 999) ||
        (order.get(a.hit.char) ?? 0) - (order.get(b.hit.char) ?? 0),
    )
    .slice(0, limit)
    .map((h) => h.hit)
}

// ------------------------------------------------------------------ recently used

const RECENT_KEY = 'backchannel:emoji-recent'
const RECENT_MAX = 24

export function recentEmoji(): string[] {
  try {
    const raw = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]')
    return Array.isArray(raw) ? raw.filter((x): x is string => typeof x === 'string').slice(0, RECENT_MAX) : []
  } catch {
    return []
  }
}

export function noteEmojiUsed(char: string) {
  try {
    const next = [char, ...recentEmoji().filter((c) => c !== char)].slice(0, RECENT_MAX)
    localStorage.setItem(RECENT_KEY, JSON.stringify(next))
  } catch {
    // storage blocked: the picker just shows the common ones
  }
}

// ------------------------------------------------------------------ custom emoji names

/** Names allowed for custom emoji (same rule as the server). */
export const EMOJI_NAME_RE = /^[a-z0-9_+-]{2,40}$/

/** "Party Blob!.gif" → "party-blob". */
export function suggestEmojiName(raw: string) {
  const base = raw
    .replace(/[?#].*$/, '')
    .split('/')
    .pop()!
    .replace(/\.[a-z0-9]{2,5}$/i, '')
    .toLowerCase()
    .replace(/[^a-z0-9_+-]+/g, '-')
    .replace(/^-+|-+$/g, '')
  return base.slice(0, 40)
}
