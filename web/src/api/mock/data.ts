// Fictional sample data for the clickable mock. Every person and number here is made up
// (555-01xx numbers). Times are relative to when the page loads so the mock always looks current.
import type {
  ActivityItem,
  Attachment,
  CatchUp,
  ChiefCard,
  Conversation,
  LaterItem,
  Me,
  Message,
  Person,
  Reaction,
  ScheduledMessage,
  TapbackKind,
} from '../types'
import { avatars, scenes } from './art'

const NOW = Date.now()

/** Minutes ago → ISO. */
const ago = (mins: number) => new Date(NOW - mins * 60000).toISOString()
/** A clock time some days back → ISO. */
const day = (daysAgo: number, h: number, m: number) => {
  const d = new Date(NOW)
  d.setDate(d.getDate() - daysAgo)
  d.setHours(h, m, 0, 0)
  return d.toISOString()
}
const daysAhead = (n: number, h: number, m: number) => {
  const d = new Date(NOW)
  d.setDate(d.getDate() + n)
  d.setHours(h, m, 0, 0)
  return d.toISOString()
}

const person = (
  handle: string,
  name: string,
  opts: { shortName?: string; isContact?: boolean; avatarUrl?: string | null; handles?: string[] } = {},
): Person => ({
  handle,
  handles: opts.handles ?? [handle],
  name,
  shortName: opts.shortName ?? name.split(' ')[0],
  isContact: opts.isContact ?? true,
  avatarUrl: opts.avatarUrl ?? null,
})

export const me: Me = {
  name: 'Harry Liu',
  shortName: 'Harry',
  handles: ['+15105550100', 'harry.liu@icloud.example'],
  avatarUrl: null,
}

export const P = {
  jordan: person('+14155550134', 'Jordan Park', {
    avatarUrl: avatars.jordan,
    handles: ['+14155550134', 'jordan.park@icloud.example'],
  }),
  priya: person('+14155550178', 'Priya Natarajan', { avatarUrl: avatars.priya }),
  mom: person('+15105550122', 'Mom', { avatarUrl: avatars.mom }),
  dad: person('+15105550123', 'Dad'),
  grace: person('+16285550190', 'Grace Liu', { avatarUrl: avatars.grace }),
  marcus: person('+14155550161', 'Marcus Webb'),
  ellie: person('+16505550147', 'Ellie Chen', { avatarUrl: avatars.ellie }),
  sam: person('+14155550112', 'Sam Rivera'),
  tomas: person('+14155550141', 'Tomás Ortega'),
  kevin: person('+14155550152', 'Kevin Huang'),
  aisha: person('+14155550163', 'Aisha Bello'),
  ben: person('+14155550174', 'Ben Carter'),
  nate: person('+14155550155', '(415) 555-0155', { isContact: false }),
  vet: person('+14155550199', '(415) 555-0199', { isContact: false }),
  mcu: person('24273', '24273', { isContact: false }),
  parcel: person('69525', '69525', { isContact: false }),
  dental: person('36320', '36320', { isContact: false }),
}

const EMOJI: Record<Exclude<TapbackKind, 'emoji' | 'sticker'>, string> = {
  love: '❤️',
  like: '👍',
  dislike: '👎',
  laugh: '😂',
  emphasize: '‼️',
  question: '❓',
}
const r = (kind: TapbackKind, from: (Person | 'me')[], emoji?: string): Reaction => ({
  kind,
  emoji: emoji ?? EMOJI[kind as keyof typeof EMOJI] ?? '❤️',
  from: from.map((p) => (p === 'me' ? 'me' : p.handle)),
})

let seq = 0
type Extra = Partial<Message> & { unread?: boolean }
const unreadIds = new Set<string>()

function msg(cid: string, from: Person | 'me', sentAt: string, text: string, extra: Extra = {}): Message {
  seq += 1
  const fromMe = from === 'me'
  const { unread, ...rest } = extra
  const m: Message = {
    id: `m${seq}`,
    conversationId: cid,
    senderHandle: fromMe ? null : from.handle,
    fromMe,
    text,
    sentAt,
    service: 'iMessage',
    attachments: [],
    reactions: [],
    linkPreview: null,
    threadRootId: null,
    thread: null,
    editedAt: null,
    unsent: false,
    status: fromMe ? 'delivered' : null,
    readAt: null,
    mentionsMe: false,
    effect: null,
    isAudioMessage: false,
    otpCode: null,
    event: null,
    clientId: null,
    ...rest,
  }
  if (unread) unreadIds.add(m.id)
  return m
}

function event(cid: string, from: Person, sentAt: string, type: 'renamed' | 'added', text: string): Message {
  return msg(cid, from, sentAt, '', { event: { type, text } })
}

let attSeq = 0
function image(src: string, name: string, w = 800, h = 600): Attachment {
  attSeq += 1
  return {
    id: `a${attSeq}`,
    kind: 'image',
    mimeType: 'image/jpeg',
    name,
    size: 2_400_000,
    url: src,
    thumbUrl: src,
    width: w,
    height: h,
    isSticker: false,
    isGenmoji: false,
    emojiDescription: null,
    missing: false,
  }
}
function file(name: string, kind: Attachment['kind'], size: number, mimeType: string, thumb: string | null = null): Attachment {
  attSeq += 1
  return {
    id: `a${attSeq}`,
    kind,
    mimeType,
    name,
    size,
    url: thumb ?? '#',
    thumbUrl: thumb,
    width: thumb ? 800 : null,
    height: thumb ? 600 : null,
    isSticker: false,
    isGenmoji: false,
    emojiDescription: null,
    missing: false,
  }
}

export const CID = {
  family: 'grp-family',
  soccer: 'grp-soccer',
  dinner: 'grp-dinner',
  roomies: 'grp-roomies',
  jordan: 'dm-jordan',
  marcus: 'dm-marcus',
  mom: 'dm-mom',
  priya: 'dm-priya',
  grace: 'dm-grace',
  dad: 'dm-dad',
  ellie: 'dm-ellie',
  sam: 'dm-sam',
  vet: 'dm-vet',
  mcu: 'dm-24273',
  parcel: 'dm-69525',
  dental: 'dm-36320',
} as const

// ---------------------------------------------------------------- Liu Family
const fam = CID.family
const famRoot = msg(fam, P.mom, day(3, 19, 42), "Thanksgiving this year: Grace offered to host! 🦃", {
  reactions: [r('love', [P.grace, P.dad, 'me'])],
})
const famPiesMe = msg(fam, 'me', day(2, 18, 10), 'i can grab them on the way', {
  reactions: [r('emphasize', [P.grace])],
})
const familyMsgs: Message[] = [
  event(fam, P.grace, day(6, 18, 2), 'renamed', 'Grace named the conversation “Liu Family 🏡”'),
  famRoot,
  msg(fam, P.grace, day(3, 19, 44), 'Yes!! We finally have a table that seats 12', { threadRootId: famRoot.id }),
  msg(fam, P.dad, day(3, 19, 51), "I'll smoke the turkey again. 14 lbs this time", { threadRootId: famRoot.id }),
  msg(fam, 'me', day(3, 20, 5), "we're in! i'll bring brussels sprouts + a pie", { threadRootId: famRoot.id }),
  msg(fam, P.dad, day(3, 20, 30), 'Last tomatoes of the year 🍅', {
    attachments: [image(scenes.garden, 'IMG_4821.HEIC')],
    reactions: [r('love', [P.mom]), r('like', [P.grace])],
  }),
  msg(fam, P.mom, day(2, 8, 15), 'Perfect. Everyone come by 2pm', { threadRootId: famRoot.id }),
  msg(fam, P.grace, day(2, 18, 3), "Who's picking up the pies Wednesday?"),
  famPiesMe,
  msg(fam, P.grace, day(2, 18, 11), "you're the best"),
  msg(fam, P.mom, day(2, 18, 30), "Pies are from the bakery on 24th, the order's under Liu", { editedAt: day(2, 18, 31) }),
  msg(fam, P.dad, day(1, 9, 12), "Reminder: Grandpa's 90th is in January. Start thinking about the trip"),
  msg(fam, P.mom, day(1, 9, 20), 'Already looking at flights!'),
  msg(fam, P.grace, ago(50), 'Parking is tight on our street so carpool if you can 🚗', { threadRootId: famRoot.id }),
  msg(fam, P.mom, ago(125), 'Harry can you bring the folding chairs from your garage? We need 6', {
    mentionsMe: true,
    unread: true,
  }),
  msg(fam, P.grace, ago(38), "Also Dad says the turkey will be ready by 4 so don't eat lunch 😂", {
    unread: true,
    reactions: [r('laugh', [P.dad])],
  }),
]

// ---------------------------------------------------------------- Sunday Soccer
const soc = CID.soccer
const socRoot = msg(soc, P.tomas, day(1, 9, 30), "Who's in for Sunday 9am at Crocker Amazon? Reply in the thread 👇", {
  reactions: [r('like', [P.kevin, P.aisha])],
})
const soccerMsgs: Message[] = [
  socRoot,
  msg(soc, P.kevin, day(1, 9, 34), 'in', { threadRootId: socRoot.id }),
  msg(soc, P.aisha, day(1, 9, 41), 'in!', { threadRootId: socRoot.id }),
  msg(soc, P.aisha, day(1, 9, 45), 'Field 3 again? Field 2 was a swamp last week'),
  msg(soc, P.tomas, day(1, 9, 50), 'Field 3 confirmed ✅', { reactions: [r('like', [P.aisha, P.ben, P.jordan])] }),
  msg(soc, P.ben, day(1, 10, 2), 'in, bringing my cousin Leo', { threadRootId: socRoot.id }),
  msg(soc, P.nate, day(1, 12, 15), 'out this week, hamstring 😩', { threadRootId: socRoot.id }),
  msg(soc, P.jordan, day(1, 13, 40), 'in', { threadRootId: socRoot.id }),
  msg(soc, P.ben, day(1, 21, 12), 'found the clip from last week 🎥', {
    attachments: [file('IMG_0391.MOV', 'video', 18_400_000, 'video/quicktime', scenes.field)],
    reactions: [r('emphasize', [P.kevin, P.tomas]), r('laugh', [P.aisha])],
  }),
  msg(soc, P.kevin, ago(70), 'do we have pinnies or should I bring mine', { threadRootId: socRoot.id }),
  msg(soc, P.kevin, ago(64), 'Forecast says 58° and sunny ☀️', { unread: true, reactions: [r('like', [P.tomas])] }),
  msg(soc, P.tomas, ago(20), 'Harry you in? need a head count by tonight', { unread: true, mentionsMe: true }),
]

// ---------------------------------------------------------------- Dinner Club
const din = CID.dinner
const dinBooked = msg(din, P.priya, day(1, 19, 22), "Booked! Fri Nov 6, 7:30pm, table for 5. Harry you're on dessert duty 🍰", {
  mentionsMe: true,
  reactions: [r('love', [P.ellie, P.sam, P.jordan]), r('emphasize', [P.sam])],
})
const dinOnIt = msg(din, 'me', day(1, 19, 40), 'on it 🫡', { reactions: [r('emoji', [P.jordan], '🍰')] })
const dinnerMsgs: Message[] = [
  event(din, P.priya, day(5, 12, 1), 'renamed', 'Priya named the conversation “Dinner Club 🍜”'),
  event(din, P.priya, day(5, 12, 2), 'added', 'Priya added Jordan to the conversation'),
  msg(din, P.ellie, day(5, 12, 30), 'November pick: whose turn is it?'),
  msg(din, P.sam, day(5, 12, 41), "Priya's!!"),
  msg(din, P.priya, day(5, 13, 5), 'ok ok. thinking this noodle place in the Richmond https://sevenriversnoodle.example/menu', {
    linkPreview: {
      url: 'https://sevenriversnoodle.example/menu',
      title: 'Seven Rivers Noodle House',
      siteName: 'sevenriversnoodle.example',
      summary: 'Hand-pulled noodles and Sichuan small plates on Clement Street.',
      imageUrl: scenes.noodles,
    },
  }),
  msg(din, P.jordan, day(5, 13, 20), 'the dan dan noodles there are unreal', { reactions: [r('like', [P.priya])] }),
  msg(din, P.ellie, day(5, 14, 2), 'Fri Nov 6 works for me', { editedAt: day(5, 14, 4) }),
  msg(din, P.sam, day(5, 14, 10), 'same'),
  dinBooked,
  dinOnIt,
]

// ---------------------------------------------------------------- Jordan (merged phone + email chats)
const jor = CID.jordan
const jordanMsgs: Message[] = [
  msg(jor, P.jordan, day(5, 21, 3), 'dude that bbq was elite'),
  msg(jor, 'me', day(5, 21, 10), 'haha thank you, 11 hours on the smoker'),
  msg(jor, P.jordan, day(5, 21, 11), 'worth every minute', { reactions: [r('love', ['me'])] }),
  msg(jor, 'me', day(2, 11, 2), 'you still have my camping stove?', { status: 'read', readAt: day(2, 11, 20) }),
  msg(jor, P.jordan, day(2, 11, 30), 'yes!! bringing it sunday'),
  msg(jor, P.jordan, ago(190), 'also are you down to hike Mt Tam next Saturday? thinking an 8am start'),
  msg(jor, P.jordan, ago(188), 'can carpool from mine'),
]

// ---------------------------------------------------------------- Marcus (SMS)
const mar = CID.marcus
const marcusMsgs: Message[] = [
  msg(mar, P.marcus, day(6, 10, 20), 'Hi Harry, this is Marcus re: the bathroom fan. I can do it for $340 installed, includes the new duct.', {
    service: 'SMS',
  }),
  msg(mar, 'me', day(6, 11, 45), "sounds good, let's do it", { service: 'SMS', status: 'sent' }),
  msg(mar, P.marcus, ago(26), 'I can come Thursday 9-11am for the install. Does that work? Need to confirm today to hold the slot.', {
    service: 'SMS',
    unread: true,
  }),
]

// ---------------------------------------------------------------- Mom
const mom = CID.mom
const momMsgs: Message[] = [
  msg(mom, P.mom, day(1, 16, 20), 'Call me when you get a chance ❤️'),
  msg(mom, 'me', day(1, 16, 45), 'will do tonight!', { status: 'read', readAt: day(1, 16, 46) }),
  msg(mom, P.mom, day(1, 21, 2), "Here's grandma's pie crust recipe for Thanksgiving", {
    attachments: [file("Grandma's pie crust.pdf", 'file', 184_000, 'application/pdf')],
  }),
  msg(mom, 'me', day(1, 21, 30), 'got it, thank you!!', { status: 'read', readAt: day(1, 21, 31) }),
]

// ---------------------------------------------------------------- Priya
const pri = CID.priya
const priyaMsgs: Message[] = [
  msg(pri, P.priya, day(8, 20, 15), 'that playlist from the party https://music.example/playlist/friday-night', {
    linkPreview: {
      url: 'https://music.example/playlist/friday-night',
      title: 'friday night kitchen',
      siteName: 'music.example',
      summary: 'Playlist · 42 songs',
      imageUrl: null,
    },
  }),
  msg(pri, 'me', day(8, 20, 40), 'ahh thank you, been looking for this', { status: 'read', readAt: day(8, 20, 41) }),
]

// ---------------------------------------------------------------- Grace
const gra = CID.grace
const graceMsgs: Message[] = [
  msg(gra, P.grace, day(4, 17, 40), 'We got the keys!! 🏠', { attachments: [image(scenes.house, 'IMG_2207.HEIC')] }),
  msg(gra, 'me', day(4, 17, 52), "it's gorgeous. so happy for you guys", { reactions: [r('love', [P.grace])] }),
  msg(gra, P.grace, day(4, 17, 55), '', { unsent: true }),
  msg(gra, P.grace, day(4, 17, 56), 'oops wrong chat lol'),
  msg(gra, 'me', day(4, 18, 1), '😂', { status: 'read', readAt: day(4, 18, 2) }),
]

// ---------------------------------------------------------------- Dad
const dad = CID.dad
const dadMsgs: Message[] = [
  msg(dad, P.dad, day(9, 22, 5), 'Did you see the Warriors game'),
  msg(dad, 'me', day(9, 22, 7), 'curry was cooking'),
  msg(dad, P.dad, day(9, 22, 8), '40 POINTS', { effect: 'Slam' }),
  msg(dad, 'me', day(9, 22, 10), 'the greatest', { status: 'read', readAt: day(9, 22, 10) }),
]

// ---------------------------------------------------------------- Ellie
const ell = CID.ellie
const ellieMsgs: Message[] = [
  msg(ell, P.ellie, day(7, 13, 0), 'can i borrow your pizza peel this weekend? hosting a pizza night'),
  msg(ell, 'me', day(7, 13, 20), "yes! come grab it anytime, it's on the porch", {
    status: 'read',
    readAt: day(7, 13, 21),
    reactions: [r('love', [P.ellie])],
  }),
]

// ---------------------------------------------------------------- Sam
const sam = CID.sam
const samMsgs: Message[] = [
  msg(sam, P.sam, day(12, 18, 0), 'you watching the game sunday?'),
  msg(sam, 'me', day(12, 18, 4), 'obviously', { status: 'read', readAt: day(12, 18, 5) }),
  msg(sam, P.sam, ago(33), "happy halloween!! 🎃 send me costume pics or it didn't happen", { unread: true }),
]

// ---------------------------------------------------------------- Roommates (unnamed group)
const roo = CID.roomies
const roomiesMsgs: Message[] = [
  msg(roo, P.sam, day(2, 22, 14), 'remember when we tried to cook a whole turkey in the dorm microwave'),
  msg(roo, P.ellie, day(2, 22, 15), 'the smoke alarm remembers', { reactions: [r('laugh', [P.jordan, 'me', P.sam])] }),
  msg(roo, 'me', day(2, 22, 20), 'never forget', { reactions: [r('love', [P.ellie])] }),
  msg(roo, P.ellie, ago(300), '', {
    isAudioMessage: true,
    attachments: [file('Audio Message.caf', 'audio', 96_000, 'audio/x-caf')],
  }),
  msg(roo, P.jordan, ago(290), "LMAO that's the most ellie voice memo ever"),
]

// ---------------------------------------------------------------- Automated
const vetMsgs: Message[] = [
  msg(CID.vet, P.vet, ago(140), "Hi! This is Dana from Glen Park Animal Hospital confirming your dog's dental cleaning on Tue Nov 3 at 8:30 AM. Reply YES to confirm or call (415) 555-0127.", {
    service: 'SMS',
    unread: true,
  }),
]
const mcuMsgs: Message[] = [
  msg(CID.mcu, P.mcu, day(12, 9, 3), "Mission Credit Union: Your one-time code is 318840. It expires in 10 minutes. Don't share this code with anyone, including us.", {
    service: 'SMS',
    otpCode: '318840',
  }),
  msg(CID.mcu, P.mcu, ago(9), "Mission Credit Union: Your one-time code is 482913. It expires in 10 minutes. Don't share this code with anyone, including us.", {
    service: 'SMS',
    otpCode: '482913',
    unread: true,
  }),
]
const parcelMsgs: Message[] = [
  msg(CID.parcel, P.parcel, day(3, 14, 10), 'Your package from Hearth & Home has shipped. Track: https://parcel.example/t/8H2K', { service: 'SMS' }),
  msg(CID.parcel, P.parcel, ago(115), 'Out for delivery today: your package from Hearth & Home. Track: https://parcel.example/t/8H2K', {
    service: 'SMS',
    unread: true,
  }),
]
const dentalMsgs: Message[] = [
  msg(CID.dental, P.dental, day(1, 10, 0), 'Bayview Dental: Reminder of your cleaning on Tue, Nov 10 at 10:00 AM. Reply C to confirm or call (415) 555-0120.', {
    service: 'SMS',
  }),
]

// ---------------------------------------------------------------- Conversations
interface Def {
  id: string
  kind: 'dm' | 'group'
  title: string
  isNamed: boolean
  participants: Person[]
  messages: Message[]
  automated?: boolean
  starred?: boolean
  muted?: boolean
}

const defs: Def[] = [
  { id: fam, kind: 'group', title: 'Liu Family 🏡', isNamed: true, participants: [P.mom, P.dad, P.grace], messages: familyMsgs, starred: true },
  { id: soc, kind: 'group', title: 'Sunday Soccer ⚽', isNamed: true, participants: [P.tomas, P.kevin, P.aisha, P.ben, P.jordan, P.nate], messages: soccerMsgs },
  { id: din, kind: 'group', title: 'Dinner Club 🍜', isNamed: true, participants: [P.priya, P.ellie, P.sam, P.jordan], messages: dinnerMsgs },
  { id: roo, kind: 'group', title: 'Jordan, Sam & Ellie', isNamed: false, participants: [P.jordan, P.sam, P.ellie], messages: roomiesMsgs },
  { id: jor, kind: 'dm', title: P.jordan.name, isNamed: false, participants: [P.jordan], messages: jordanMsgs },
  { id: mar, kind: 'dm', title: P.marcus.name, isNamed: false, participants: [P.marcus], messages: marcusMsgs },
  { id: mom, kind: 'dm', title: P.mom.name, isNamed: false, participants: [P.mom], messages: momMsgs, starred: true },
  { id: pri, kind: 'dm', title: P.priya.name, isNamed: false, participants: [P.priya], messages: priyaMsgs },
  { id: gra, kind: 'dm', title: P.grace.name, isNamed: false, participants: [P.grace], messages: graceMsgs },
  { id: dad, kind: 'dm', title: P.dad.name, isNamed: false, participants: [P.dad], messages: dadMsgs },
  { id: ell, kind: 'dm', title: P.ellie.name, isNamed: false, participants: [P.ellie], messages: ellieMsgs },
  { id: sam, kind: 'dm', title: P.sam.name, isNamed: false, participants: [P.sam], messages: samMsgs },
  { id: CID.vet, kind: 'dm', title: P.vet.name, isNamed: false, participants: [P.vet], messages: vetMsgs, automated: true },
  { id: CID.mcu, kind: 'dm', title: P.mcu.name, isNamed: false, participants: [P.mcu], messages: mcuMsgs, automated: true },
  { id: CID.parcel, kind: 'dm', title: P.parcel.name, isNamed: false, participants: [P.parcel], messages: parcelMsgs, automated: true },
  { id: CID.dental, kind: 'dm', title: P.dental.name, isNamed: false, participants: [P.dental], messages: dentalMsgs, automated: true },
]

// Fold thread replies into their roots' summaries.
function summarizeThreads(messages: Message[]) {
  const byId = new Map(messages.map((m) => [m.id, m]))
  for (const m of messages) {
    if (!m.threadRootId) continue
    const root = byId.get(m.threadRootId)
    if (!root) continue
    const who = m.fromMe ? 'me' : (m.senderHandle as string)
    const prev = root.thread
    const participants = [who, ...(prev?.participants ?? []).filter((p) => p !== who)].slice(0, 5)
    root.thread = { replyCount: (prev?.replyCount ?? 0) + 1, participants, lastReplyAt: m.sentAt }
  }
}

// "Waiting on you" — mirrors docs/api.md. Their message must ask something of you,
// and must not just close the loop ("perfect, see you then").
const CLOSER_RE = /^(ok|okay|k|kk|lol|lmao|haha+|ty|thanks|thank you|thx|np|no worries|sure|nice|cool|great|perfect|sounds good|got it|will do|yes|yeah|yep|same|true|word|bet|fr|see you|see ya|love you|👍|❤️|😂)\b/i
const ASK_RE = /\?|\b(can you|could you|would you|will you|are you|do you|did you|have you|lmk|let me know|call me|text me|send me|wanna|want to|down to|thoughts|when you get a chance)\b/i

export function awaiting(kind: 'dm' | 'group', messages: Message[], automated: boolean) {
  if (automated) return null
  const real = messages.filter((m) => !m.event && !m.unsent)
  const last = real[real.length - 1]
  if (!last || last.fromMe) return null
  if (Date.now() - new Date(last.sentAt).getTime() > 7 * 86400000) return null
  // Everything they've sent since the user last wrote.
  let i = real.length - 1
  while (i >= 0 && !real[i].fromMe) i--
  const run = real.slice(i + 1)
  const mine = new Set(real.filter((m) => m.fromMe).map((m) => m.id))
  const asks = (m: Message) =>
    m.mentionsMe || (!!m.threadRootId && mine.has(m.threadRootId)) || (kind === 'dm' && ASK_RE.test(m.text))
  const first = run.find(asks)
  if (!first) return null
  // "Perfect, see you then" closes the loop; "Perfect. Can you bring his records" doesn't.
  if (kind === 'dm' && CLOSER_RE.test(last.text.trim()) && !asks(last)) return null
  return first.sentAt
}

export function previewText(m: Message, people: Map<string, Person>): string {
  if (m.event) return m.event.text
  if (m.unsent) return 'Unsent a message'
  if (m.isAudioMessage) return 'Audio message'
  if (m.text) return m.text
  const a = m.attachments[0]
  if (!a) return ''
  if (a.kind === 'image') return 'Photo'
  if (a.kind === 'video') return 'Video'
  if (a.kind === 'audio') return 'Audio'
  void people
  return a.name
}

export function buildMockDb() {
  const people = new Map<string, Person>()
  for (const d of defs) for (const p of d.participants) people.set(p.handle, p)

  const messages = new Map<string, Message[]>()
  const conversations = new Map<string, Conversation>()
  for (const d of defs) {
    const list = [...d.messages].sort((a, b) => a.sentAt.localeCompare(b.sentAt))
    summarizeThreads(list)
    messages.set(d.id, list)
    const last = list[list.length - 1]
    const unread = list.filter((m) => unreadIds.has(m.id))
    const awaitingSince = awaiting(d.kind, list, !!d.automated)
    conversations.set(d.id, {
      id: d.id,
      kind: d.kind,
      title: d.title,
      isNamed: d.isNamed,
      participants: d.participants,
      service: last?.service ?? 'iMessage',
      lastMessage: last
        ? { id: last.id, text: previewText(last, people), senderHandle: last.senderHandle, fromMe: last.fromMe, sentAt: last.sentAt }
        : null,
      unreadCount: unread.length,
      mentionCount: unread.filter((m) => m.mentionsMe).length,
      isAutomated: !!d.automated,
      awaitingReply: !!awaitingSince,
      awaitingSince,
      prefs: { starred: !!d.starred, muted: !!d.muted, snoozedUntil: null, section: null },
    })
  }
  return { people, messages, conversations }
}

export const laterSeed = (): LaterItem[] => [
  {
    messageId: dinBooked.id,
    conversationId: din,
    savedAt: day(1, 19, 30),
    remindAt: daysAhead(6, 17, 0),
    done: false,
    ifNoReply: false,
    resolvedByReply: false,
    firedAt: null,
    message: dinBooked,
  },
]

export const scheduledSeed = (): ScheduledMessage[] => [
  {
    id: 's1',
    conversationId: mom,
    text: 'happy birthday mom!! 🎂❤️ love you',
    sendAt: daysAhead(8, 8, 0),
    createdAt: day(2, 21, 0),
    status: 'pending',
    error: null,
  },
]

export function activitySeed(all: Map<string, Message[]>): ActivityItem[] {
  const find = (cid: string, pred: (m: Message) => boolean) => all.get(cid)?.find(pred)
  const items: (ActivityItem | null)[] = [
    (() => {
      const m = find(soc, (x) => x.text.startsWith('Harry you in'))
      return m ? { id: 'act1', type: 'mention' as const, conversationId: soc, actorHandle: P.tomas.handle, at: m.sentAt, message: m } : null
    })(),
    (() => {
      const m = find(fam, (x) => x.text.startsWith('Parking is tight'))
      return m ? { id: 'act2', type: 'thread_reply' as const, conversationId: fam, actorHandle: P.grace.handle, at: m.sentAt, message: m } : null
    })(),
    (() => {
      const m = find(fam, (x) => x.text.startsWith('Harry can you bring'))
      return m ? { id: 'act3', type: 'mention' as const, conversationId: fam, actorHandle: P.mom.handle, at: m.sentAt, message: m } : null
    })(),
    { id: 'act4', type: 'reaction', conversationId: din, actorHandle: P.jordan.handle, at: day(1, 19, 41), message: dinOnIt, reaction: r('emoji', [P.jordan], '🍰') },
    { id: 'act5', type: 'mention', conversationId: din, actorHandle: P.priya.handle, at: dinBooked.sentAt, message: dinBooked },
    { id: 'act6', type: 'reaction', conversationId: fam, actorHandle: P.grace.handle, at: day(2, 18, 12), message: famPiesMe, reaction: r('emphasize', [P.grace]) },
  ]
  return items.filter((x): x is ActivityItem => !!x).sort((a, b) => b.at.localeCompare(a.at))
}

export const catchUpSeed = (): CatchUp => ({
  generatedAt: ago(4),
  vibe: "Happy Halloween. Nothing's on fire, but Marcus needs an answer today and the family is locking in Thanksgiving.",
  sections: [
    {
      kind: 'reply_now',
      title: 'Reply now',
      items: [
        {
          conversationId: mar,
          title: 'Marcus Webb',
          summary: 'Can install the bathroom fan Thursday 9–11am, but needs a yes today to hold the slot.',
          draft: "thursday 9-11 works! i'll leave the side gate open",
          status: null,
        },
        {
          conversationId: soc,
          title: 'Sunday Soccer ⚽',
          summary: 'Tomás needs a head count by tonight for tomorrow 9am, Field 3. Six people are in so far.',
          draft: "i'm in! see you at 9",
          status: null,
        },
      ],
    },
    {
      kind: 'reply_soon',
      title: 'Reply soon',
      items: [
        {
          conversationId: jor,
          title: 'Jordan Park',
          summary: 'Wants to hike Mt Tam next Saturday with an 8am start, carpooling from his place. He’s also bringing your camping stove tomorrow.',
          draft: "down! i'll be at yours by 7:30",
          status: null,
        },
        {
          conversationId: fam,
          title: 'Liu Family 🏡',
          summary: 'Mom asked you to bring 6 folding chairs from your garage for Thanksgiving.',
          draft: "yep i'll bring all 6",
          status: null,
        },
        {
          conversationId: sam,
          title: 'Sam Rivera',
          summary: 'Wants Halloween costume pics.',
          draft: "pics tonight, it's a good one 🎃",
          status: null,
        },
      ],
    },
    {
      kind: 'plans',
      title: 'Plans',
      items: [
        {
          conversationId: fam,
          title: "Thanksgiving at Grace's",
          summary: 'Thu Nov 26, arrive by 2pm, turkey at 4. You’re bringing brussels sprouts, a pie, and the chairs.',
          draft: null,
          status: 'needs_action',
        },
        {
          conversationId: din,
          title: 'Dinner Club at Seven Rivers',
          summary: 'Fri Nov 6, 7:30pm, table for 5. You’re on dessert.',
          draft: null,
          status: 'locked_in',
        },
        {
          conversationId: CID.vet,
          title: 'Dog’s dental cleaning',
          summary: 'Tue Nov 3 at 8:30 AM at Glen Park Animal Hospital. They want a YES to confirm.',
          draft: 'YES',
          status: 'needs_action',
        },
      ],
    },
    {
      kind: 'caught_up',
      title: 'Caught up',
      items: [
        { conversationId: mom, title: 'Mom', summary: 'Sent grandma’s pie crust recipe. You already said thanks.', draft: null, status: null },
        { conversationId: roo, title: 'Jordan, Sam & Ellie', summary: 'Ellie sent a voice memo; Jordan is still laughing about it.', draft: null, status: null },
        { conversationId: CID.parcel, title: 'Deliveries', summary: 'Your Hearth & Home package is out for delivery.', draft: null, status: null },
      ],
    },
  ],
})

/** Canned "AI draft" suggestions per conversation for the mock. */
export const draftSeed: Record<string, string[]> = {
  [mar]: ["thursday 9-11 works! i'll leave the side gate open", 'can we do 10-12 instead? have a call at 9', "thursday's perfect, see you then"],
  [jor]: ["down! i'll be at yours by 7:30", "i'm in but can we do 9? 8 is brutal on a saturday lol", "can't next sat, the one after?"],
  [fam]: ["yep i'll bring all 6", "on it! i'll bring the chairs and the folding table too", 'will do. dad save me a drumstick'],
  [soc]: ["i'm in! see you at 9", 'in, bringing a ball', "out this week, next sunday for sure"],
  [sam]: ["pics tonight, it's a good one 🎃", 'you first', 'happy halloween!! costume reveal at 8'],
}
export const draftFallback = ['sounds good!', 'haha love it', 'let me check and get back to you']

/** Scripted replies so sending feels alive in the mock. */
export const autoReplies: Record<string, { from: Person; text: string; delay: number }> = {
  [mar]: { from: P.marcus, text: "Perfect, you're on the schedule for Thursday 9-11. I'll text when I'm on the way.", delay: 4500 },
  [jor]: { from: P.jordan, text: "let's gooo 🏔️ i'll grab coffee", delay: 3500 },
  [sam]: { from: P.sam, text: '🎃🎃🎃', delay: 3000 },
  [soc]: { from: P.tomas, text: 'got you down 👍', delay: 4000 },
  [fam]: { from: P.mom, text: 'Thank you sweetie ❤️', delay: 4000 },
}

/** Messages that arrive on their own while the mock is open. */
export const timedArrivals: { cid: string; from: Person; text: string; after: number }[] = [
  { cid: soc, from: P.kevin, text: 'bringing the cones and a pump', after: 25000 },
  { cid: ell, from: P.ellie, text: 'returning the peel tomorrow, thank you!! 🍕', after: 70000 },
]

/** What Chief knows about a few people, for the profile cards in the mock. */
export const chiefSeed: Record<string, ChiefCard> = {
  [P.jordan.handle]: {
    name: 'Jordan Park',
    relationship: 'friend',
    birthday: 'March 3',
    notes: 'Met at Cal. Big hiker; always down for Mt Tam.',
    loops: [{ id: 71, title: 'Get the camping stove back from Jordan', status: 'open', dueDate: null }],
    facts: ['Jordan is training for the Dipsea in June.'],
  },
  [P.mom.handle]: {
    name: 'Mom',
    relationship: 'family',
    birthday: 'November 8',
    notes: null,
    loops: [{ id: 64, title: 'Call Mom back about Thanksgiving', status: 'open', dueDate: null }],
    facts: ['Mom prefers a call over texting for anything important.'],
  },
  [P.marcus.handle]: {
    name: 'Marcus Webb',
    relationship: 'vendor',
    birthday: null,
    notes: 'Handyman. Did the fence in the spring.',
    loops: [{ id: 80, title: 'Bathroom fan install with Marcus ($340 quoted)', status: 'in_progress', dueDate: null }],
    facts: [],
  },
}
