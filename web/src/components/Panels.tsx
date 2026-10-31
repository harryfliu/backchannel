import { useEffect, useMemo, useState } from 'react'
import type { Conversation, Message } from '../api/types'
import { formatHandle, fullTimestamp, plural } from '../lib/format'
import { Icon } from '../lib/icons'
import {
  closePanel,
  loadChiefCard,
  loadMedia,
  openConversation,
  openProfile,
  toast,
  toggleMute,
  toggleStar,
  useStore,
  useThread,
} from '../state/store'
import { Avatar, ConversationGlyph, IconButton, Spinner } from './common'
import { Composer } from './Composer'
import { MessageRow } from './Message'

export function RightPanel() {
  const panel = useStore((s) => s.panel)
  if (!panel) return null
  if (panel.kind === 'thread') return <ThreadPanel cid={panel.cid} mid={panel.mid} />
  if (panel.kind === 'profile') return <ProfilePanel handle={panel.handle} />
  return <DetailsPanel cid={panel.cid} />
}

function PanelHeader({ title, sub }: { title: string; sub?: string }) {
  return (
    <header className="panel-head">
      <div>
        <h2 className="panel-title">{title}</h2>
        {sub && <p className="panel-sub">{sub}</p>}
      </div>
      <IconButton icon="x" label="Close" onClick={closePanel} />
    </header>
  )
}

function ThreadPanel({ cid, mid }: { cid: string; mid: string }) {
  const t = useThread(cid, mid)
  const c = useStore((s) => s.conversations[cid])
  if (!c) return null
  return (
    <aside className="panel" aria-label="Thread">
      <PanelHeader title="Thread" sub={`${c.kind === 'group' && c.isNamed ? '#' : ''}${c.title}`} />
      <div className="panel-scroll thread-scroll">
        {!t || t.loading ? (
          <div className="panel-loading">
            <Spinner />
          </div>
        ) : t.error || !t.root ? (
          <p className="side-empty">{t.error ?? 'That message is no longer available.'}</p>
        ) : (
          <>
            <MessageRow m={t.root} opts={{ head: true, inThread: true, isGroup: c.kind === 'group' }} />
            <div className="thread-divider">
              <span>{plural(t.replies.length, 'reply', 'replies')}</span>
            </div>
            {t.replies.map((m, i) => (
              <MessageRow
                key={m.id}
                m={m}
                opts={{
                  head: i === 0 || headBreak(t.replies[i - 1], m),
                  inThread: true,
                  showStatus: m.fromMe && i === t.replies.length - 1,
                  isGroup: c.kind === 'group',
                }}
              />
            ))}
          </>
        )}
      </div>
      <Composer c={c} threadRootId={mid} compact />
    </aside>
  )
}

function headBreak(prev: Message, m: Message) {
  return prev.fromMe !== m.fromMe || prev.senderHandle !== m.senderHandle || new Date(m.sentAt).getTime() - new Date(prev.sentAt).getTime() > 5 * 60000
}

function ProfilePanel({ handle }: { handle: string }) {
  const p = useStore((s) => s.people[handle])
  const convs = useStore((s) => s.conversations)
  const dm = useMemo(() => Object.values(convs).find((c) => c.kind === 'dm' && c.participants[0]?.handles.includes(handle)), [convs, handle])
  const groups = useMemo(
    () => Object.values(convs).filter((c) => c.kind === 'group' && c.participants.some((x) => x.handles.includes(handle))),
    [convs, handle],
  )
  const media = useStore((s) => (dm ? s.media[dm.id] : undefined))
  const chief = useStore((s) => (p ? s.chiefCards[p.handle] : undefined))
  useEffect(() => {
    if (dm) void loadMedia(dm.id)
  }, [dm])
  useEffect(() => {
    if (p) void loadChiefCard(p.handle)
  }, [p])
  if (!p) return null
  const photos = (media ?? []).filter((m) => m.attachment.kind === 'image').slice(0, 6)
  return (
    <aside className="panel" aria-label="Profile">
      <PanelHeader title="Profile" />
      <div className="panel-scroll profile">
        <div className="profile-hero">
          <Avatar person={p} size={160} radius={24} />
        </div>
        <h3 className="profile-name">{p.name}</h3>
        <p className="profile-sub">
          {p.isContact ? 'In your contacts' : 'Not in your contacts'} · {dm?.service === 'SMS' ? 'Text message' : 'iMessage'}
        </p>
        <div className="profile-actions">
          {dm && (
            <button type="button" className="btn" onClick={() => openConversation(dm.id)}>
              <Icon name="dms" size={16} /> Message
            </button>
          )}
          <a className="btn" href={`facetime-audio:${p.handle}`}>
            <Icon name="headphones" size={16} /> FaceTime
          </a>
          <a className="btn" href={`tel:${p.handle}`}>
            <Icon name="phone" size={16} /> Call
          </a>
        </div>
        <section className="profile-section">
          <h4>Contact</h4>
          {p.handles.map((h) => (
            <CopyRow key={h} label={h.includes('@') ? 'Email' : 'Phone'} value={formatHandle(h)} />
          ))}
        </section>
        {chief && chief !== 'loading' && (
          <section className="profile-section chief-card">
            <h4>
              <Icon name="listChecks" size={15} /> From Chief
            </h4>
            {chief.relationship && (
              <div className="kv">
                <span>Relationship</span>
                <span className="kv-value">{chief.relationship}</span>
              </div>
            )}
            {chief.birthday && (
              <div className="kv">
                <span>Birthday</span>
                <span className="kv-value">{chief.birthday}</span>
              </div>
            )}
            {chief.loops.map((l) => (
              <div key={l.id} className="kv">
                <span>Loop #{l.id}</span>
                <span className="kv-value">
                  {l.title}
                  {l.status === 'in_progress' ? ' (in progress)' : ''}
                  {l.dueDate ? ` · due ${l.dueDate}` : ''}
                </span>
              </div>
            ))}
            {chief.notes && <p className="chief-notes">{chief.notes}</p>}
            {chief.facts.map((f) => (
              <p key={f} className="chief-notes">
                {f}
              </p>
            ))}
          </section>
        )}
        {photos.length > 0 && (
          <section className="profile-section">
            <h4>Shared photos</h4>
            <div className="profile-photos">
              {photos.map((m) => (
                <img key={m.attachment.id} src={m.attachment.thumbUrl ?? m.attachment.url} alt={m.attachment.name} />
              ))}
            </div>
          </section>
        )}
        {groups.length > 0 && (
          <section className="profile-section">
            <h4>Groups together</h4>
            {groups.map((g) => (
              <button key={g.id} type="button" className="side-row is-plain" onClick={() => openConversation(g.id)}>
                <span className="side-glyph">
                  <ConversationGlyph c={g} size={20} />
                </span>
                <span className="side-label">{g.title}</span>
              </button>
            ))}
          </section>
        )}
      </div>
    </aside>
  )
}

function CopyRow({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <div className="kv is-copy">
      <span>{label}</span>
      <span className="kv-value">{value}</span>
      <button
        type="button"
        className="icon-btn"
        aria-label={`Copy ${label.toLowerCase()}`}
        title="Copy"
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(value)
            setCopied(true)
            setTimeout(() => setCopied(false), 1400)
          } catch {
            toast("Copy didn't work here. Select the text instead.")
          }
        }}
      >
        <Icon name={copied ? 'check' : 'copy'} size={15} />
      </button>
    </div>
  )
}

function DetailsPanel({ cid }: { cid: string }) {
  const c = useStore((s) => s.conversations[cid]) as Conversation | undefined
  const media = useStore((s) => s.media[cid])
  const me = useStore((s) => s.me)
  if (!c) return null
  return (
    <aside className="panel" aria-label="Conversation details">
      <PanelHeader title="Details" />
      <div className="panel-scroll profile">
        <div className="details-hero">
          <ConversationGlyph c={c} size={64} />
          <h3 className="profile-name">{c.title}</h3>
          <p className="profile-sub">
            {plural(c.participants.length + 1, 'person', 'people')} · {c.service}
          </p>
        </div>
        <div className="profile-actions">
          <button type="button" className="btn" onClick={() => toggleStar(c.id)}>
            <Icon name="star" size={16} filled={c.prefs.starred} /> {c.prefs.starred ? 'Starred' : 'Star'}
          </button>
          <button type="button" className="btn" onClick={() => toggleMute(c.id)}>
            <Icon name="bellOff" size={16} /> {c.prefs.muted ? 'Unmute' : 'Mute'}
          </button>
        </div>
        <section className="profile-section">
          <h4>Members</h4>
          {me && (
            <div className="member-row">
              <Avatar name={me.name} size={28} />
              <span className="member-name">{me.name} (you)</span>
            </div>
          )}
          {c.participants.map((p) => (
            <button key={p.handle} type="button" className="member-row" onClick={() => openProfile(p.handle, c.id)}>
              <Avatar person={p} size={28} />
              <span className="member-name">{p.name}</span>
              {!p.isContact && <span className="msg-tag">Not in contacts</span>}
            </button>
          ))}
          <p className="field-help">Group names and members are managed in Messages on your iPhone or Mac.</p>
        </section>
        {media && media.length > 0 && (
          <section className="profile-section">
            <h4>Shared</h4>
            <p className="field-help">
              {plural(media.filter((m) => m.attachment.kind === 'image').length, 'photo')},{' '}
              {plural(media.filter((m) => m.attachment.kind !== 'image').length, 'other file')}. Latest {fullTimestamp(media[0].sentAt)}.
            </p>
          </section>
        )}
      </div>
    </aside>
  )
}
