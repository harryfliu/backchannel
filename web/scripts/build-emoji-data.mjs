// Builds src/lib/emoji-data.json from emojibase-data (MIT, a dev dependency): every emoji in
// Unicode order with Slack-style short names (iamcal, which Slack uses, then GitHub, then CLDR) and
// keywords (CLDR annotations plus emojibase tags), grouped like Apple's and Slack's pickers.
// Run after updating emojibase-data:  node scripts/build-emoji-data.mjs
import { readFileSync, writeFileSync } from 'node:fs'

const load = (path) => JSON.parse(readFileSync(new URL(`../node_modules/emojibase-data/${path}`, import.meta.url), 'utf8'))
const data = load('en/data.json')
const sets = ['iamcal', 'github', 'cldr'].map((name) => load(`en/shortcodes/${name}.json`))
const messages = load('en/messages.json')

// Emoji newer than this may show as empty boxes on the Macs Backchannel runs on.
const MAX_VERSION = 16
const list = (v) => (v == null ? [] : Array.isArray(v) ? v : [v])
const titleCase = (s) => s.replace(/\b[a-z]/g, (c) => c.toUpperCase()).replace(/\bAnd\b/g, 'and')

const groupKeys = messages.groups.filter((g) => g.key !== 'component').sort((a, b) => a.order - b.order)
const groupIndex = new Map(groupKeys.map((g, i) => [g.order, i]))

const rows = []
for (const e of data.filter((e) => groupIndex.has(e.group) && e.version <= MAX_VERSION).sort((a, b) => a.order - b.order)) {
  const names = [...new Set(sets.flatMap((set) => list(set[e.hexcode])).map((n) => n.toLowerCase()))]
  if (!names.length) continue
  const words = new Set()
  for (const phrase of [e.label, ...(e.tags ?? [])]) {
    for (const w of phrase.toLowerCase().split(/[^a-z0-9]+/)) if (w.length >= 2) words.add(w)
  }
  for (const n of names) words.delete(n)
  // emojibase adds U+FE0F to some single emoji that show as emoji anyway (👍); keep it only where
  // it's needed (❤️, ☀️: type 0, text by default) and inside sequences.
  const cps = [...e.emoji]
  const char = cps.length === 2 && cps[1] === '\ufe0f' && e.type === 1 ? cps[0] : e.emoji
  rows.push([char, names.join(' '), [...words].join(' '), groupIndex.get(e.group)])
}

const out = { groups: groupKeys.map((g) => titleCase(g.message)), emoji: rows }
const file = new URL('../src/lib/emoji-data.json', import.meta.url)
writeFileSync(file, JSON.stringify(out))
console.log(`wrote src/lib/emoji-data.json: ${rows.length} emoji, ${(JSON.stringify(out).length / 1024).toFixed(0)} KB`)
