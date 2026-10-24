// The app shell: a quiet sidebar, the page, and live notifications when something needs the officer.
import { useRef, useState } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { api, useLoad, useTrace } from '../api'
import { Avatar, Icon, useOfficer, useToast } from './ui'

const NAV = [
  { to: '/', name: 'Dashboard', icon: 'home' },
  { to: '/patients', name: 'Patients', icon: 'users' },
  { to: '/tasks', name: 'Tasks', icon: 'tasks', count: true },
]
const INSURER_NAV = [{ to: '/insurer', name: 'Insurer desk', icon: 'building' }]

export default function Layout({ children }) {
  const { pathname } = useLocation()
  const navigate = useNavigate()
  const toast = useToast()
  const claims = useLoad(api.claims)
  const names = useRef({})
  names.current = Object.fromEntries((claims.data ?? []).map((c) => [c.claim_id, c.patient]))

  // Tell the officer when the insurer answers or something lands on their desk.
  const live = useTrace({
    onEvent: (event) => {
      claims.refresh()
      const patient = names.current[event.claim_id] ?? event.claim_id
      const open = { label: 'Open patient', onClick: () => navigate(`/patients/${event.claim_id}`) }
      if (event.action === 'message_sent' && event.actor === 'insurer') {
        toast({ title: `Insurer replied · ${patient}`, body: replyText(event.output.type), icon: 'building', tone: 'info', action: open })
      } else if (event.action === 'transition' && event.output.to === 'AWAITING_HUMAN') {
        toast({ title: `${patient} needs you`, body: 'A new task is waiting.', icon: 'tasks', tone: 'attention', action: open })
      }
    },
  })
  const waiting = (claims.data ?? []).filter((c) => c.state === 'AWAITING_HUMAN' && !c.busy).length

  return (
    <div className="flex h-full">
      <Sidebar waiting={waiting} live={live} />
      <main className="min-w-0 flex-1 overflow-y-auto">
        <div key={pathname.split('/').slice(0, 2).join('/')} className="mx-auto max-w-[1280px] animate-fade-up px-6 py-8 lg:px-10">{children}</div>
      </main>
    </div>
  )
}

const replyText = (type) => ({
  APPROVE: 'Approved.', PARTIAL_APPROVE: 'Approved with cuts.', QUERY: 'They have a question.', DENY: 'Denied.', FINAL: 'Final decision in.',
}[type] ?? 'New message.')

function Sidebar({ waiting, live }) {
  const navigate = useNavigate()
  const [officer, setOfficer] = useOfficer()
  const [editing, setEditing] = useState(false)
  const item = ({ isActive }) =>
    `group flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors ${isActive ? 'bg-white font-medium text-ink shadow-card ring-1 ring-line' : 'text-ink-muted hover:bg-black/[0.03] hover:text-ink'}`
  return (
    <aside className="hidden w-60 shrink-0 flex-col border-r border-line bg-[#efefec] md:flex">
      <div className="flex items-center gap-2.5 px-5 pb-6 pt-6">
        <span className="grid h-8 w-8 place-items-center rounded-[9px] bg-ink text-[#a7d7cc]"><Icon name="logo" className="h-5 w-5" /></span>
        <div className="leading-tight">
          <p className="text-[15px] font-semibold tracking-tight">ClaimPilot</p>
          <p className="text-[11px] text-ink-muted">Demo Multispeciality</p>
        </div>
      </div>
      <div className="px-3">
        <button onClick={() => navigate('/patients/admit')}
          className="flex w-full items-center justify-center gap-2 rounded-lg bg-ink px-3 py-2.5 text-sm font-medium text-white transition hover:bg-zinc-800 active:scale-[0.98]">
          <Icon name="plus" className="h-4 w-4" />Admit patient
        </button>
      </div>
      <nav className="mt-6 flex-1 space-y-6 px-3">
        <ul className="space-y-1">
          {NAV.map((n) => (
            <li key={n.to}>
              <NavLink to={n.to} end={n.to === '/'} className={item}>
                <Icon name={n.icon} className="h-[18px] w-[18px]" />
                <span className="flex-1">{n.name}</span>
                {n.count && waiting > 0 && <span key={waiting} className="animate-scale-in rounded-md bg-amber-400/90 px-1.5 text-[11px] font-semibold text-ink tabular">{waiting}</span>}
              </NavLink>
            </li>
          ))}
        </ul>
        <div>
          <p className="px-3 pb-2 text-[11px] font-medium uppercase tracking-[0.08em] text-ink-faint">Simulation</p>
          <ul className="space-y-1">
            {INSURER_NAV.map((n) => (
              <li key={n.to}><NavLink to={n.to} className={item}><Icon name={n.icon} className="h-[18px] w-[18px]" />{n.name}</NavLink></li>
            ))}
          </ul>
        </div>
      </nav>
      <div className="space-y-3 border-t border-line p-4">
        <div className="flex items-center gap-2 text-xs text-ink-muted">
          <span className={`h-2 w-2 rounded-full ${live ? 'bg-accent-500 animate-ring' : 'bg-zinc-400'}`} />
          {live ? 'Agents online' : 'Reconnecting…'}
        </div>
        <div className="flex items-center gap-3">
          <Avatar name={officer} size="sm" />
          <div className="min-w-0 flex-1">
            {editing ? (
              <input autoFocus value={officer} onChange={(e) => setOfficer(e.target.value)} onBlur={() => setEditing(false)}
                onKeyDown={(e) => e.key === 'Enter' && setEditing(false)} className="input h-7 px-2 py-0" />
            ) : (
              <button onClick={() => setEditing(true)} className="block w-full truncate text-left text-sm font-medium hover:underline" title="Change name">{officer}</button>
            )}
            <p className="text-[11px] text-ink-muted">Billing officer</p>
          </div>
        </div>
      </div>
    </aside>
  )
}
