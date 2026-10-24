// The app shell: a white sidebar with grouped navigation, a top bar, and live notifications.
import { useRef } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { api, useLoad, useTrace } from '../api'
import { useAuth } from '../auth'
import { Avatar, Icon, useToast } from './ui'

const NAV = [
  { section: 'Workspace', items: [
    { to: '/', name: 'Dashboard', icon: 'grid' },
    { to: '/patients', name: 'Patients', icon: 'users' },
    { to: '/tasks', name: 'Tasks', icon: 'tasks', count: true },
  ] },
  { section: 'Simulation', items: [
    { to: '/insurer', name: 'Insurer desk', icon: 'building' },
  ] },
]

const TITLES = [
  ['/patients/admit', 'Admit patient'],
  ['/patients/', 'Patient file'],
  ['/patients', 'Patients'],
  ['/tasks', 'Tasks'],
  ['/insurer', 'Insurer desk'],
]

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
  const waiting = (claims.data ?? []).filter((c) => (c.state === 'AWAITING_HUMAN' || c.state === 'FINAL_APPROVED') && !c.busy).length
  const title = pathname === '/' ? 'Dashboard' : TITLES.find(([prefix]) => pathname.startsWith(prefix))?.[1] ?? 'ClaimPilot'

  return (
    <div className="flex h-full">
      <Sidebar waiting={waiting} />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar title={title} waiting={waiting} live={live} />
        <main className="min-w-0 flex-1 overflow-y-auto">
          <div key={pathname.split('/').slice(0, 2).join('/')} className="mx-auto max-w-[1360px] animate-fade-up px-6 pb-12 pt-7 lg:px-9">{children}</div>
        </main>
      </div>
    </div>
  )
}

const replyText = (type) => ({
  APPROVE: 'Approved.', PARTIAL_APPROVE: 'Approved with cuts.', QUERY: 'They have a question.', DENY: 'Denied.', FINAL: 'Final decision in.',
}[type] ?? 'New message.')

function Sidebar({ waiting }) {
  const navigate = useNavigate()
  const { user, logout } = useAuth()
  const item = ({ isActive }) =>
    `group relative flex items-center gap-3 rounded-full px-4 py-2.5 text-sm font-semibold transition-all duration-200 ${isActive ? 'bg-accent-600 text-white shadow-glow' : 'text-ink-muted hover:bg-accent-50 hover:text-accent-700'}`
  return (
    <aside className="hidden w-[264px] shrink-0 flex-col border-r border-line bg-white md:flex">
      <div className="flex items-center gap-3 px-6 pb-7 pt-6">
        <span className="grid h-10 w-10 place-items-center rounded-2xl bg-accent-600 text-white shadow-glow"><Icon name="logo" className="h-6 w-6" /></span>
        <div className="leading-tight">
          <p className="text-[17px] font-extrabold tracking-tight">ClaimPilot</p>
          <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-accent-600">Hospital desk</p>
        </div>
      </div>
      <div className="px-4">
        <button onClick={() => navigate('/patients/admit')}
          className="flex w-full items-center justify-center gap-2 rounded-full border-2 border-dashed border-accent-200 bg-accent-50/60 px-3 py-2.5 text-sm font-bold text-accent-700 transition hover:border-accent-400 hover:bg-accent-50 active:scale-[0.98]">
          <Icon name="plus" className="h-4 w-4" />Admit patient
        </button>
      </div>
      <nav className="mt-6 flex-1 space-y-6 overflow-y-auto px-4">
        {NAV.map(({ section, items }) => (
          <div key={section}>
            <p className="px-4 pb-2 text-[11px] font-bold uppercase tracking-[0.12em] text-ink-faint">{section}</p>
            <ul className="space-y-1">
              {items.map((n) => (
                <li key={n.to}>
                  <NavLink to={n.to} end={n.to === '/'} className={item}>
                    {({ isActive }) => (
                      <>
                        <Icon name={n.icon} className="h-[19px] w-[19px]" />
                        <span className="flex-1">{n.name}</span>
                        {n.count && waiting > 0 && (
                          <span key={waiting} className={`animate-scale-in rounded-full px-2 py-0.5 text-[11px] font-bold tabular ${isActive ? 'bg-white text-accent-700' : 'bg-accent-600 text-white'}`}>{waiting}</span>
                        )}
                      </>
                    )}
                  </NavLink>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </nav>
      <div className="space-y-3 p-4">
        <div className="relative overflow-hidden rounded-2xl bg-accent-50 p-4">
          <span className="absolute -right-4 -top-4 h-20 w-20 rounded-full bg-accent-100" />
          <span className="relative grid h-9 w-9 place-items-center rounded-xl bg-white text-accent-600 shadow-card"><Icon name="shieldCheck" className="h-5 w-5" /></span>
          <p className="relative mt-3 text-sm font-bold text-accent-800">Human gate is on</p>
          <p className="relative mt-0.5 text-xs leading-relaxed text-accent-700/80">Nothing reaches an insurer or a patient until you approve it.</p>
        </div>
        <div className="flex items-center gap-3 rounded-2xl border border-line p-2.5">
          <Avatar name={user?.display_name} size="sm" />
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-bold">{user?.display_name}</p>
            <p className="text-[11px] text-ink-muted">Billing officer</p>
          </div>
          <button onClick={() => logout()} title="Sign out" className="grid h-8 w-8 place-items-center rounded-full text-ink-muted transition hover:bg-rose-50 hover:text-rose-600">
            <Icon name="logout" className="h-4 w-4" />
          </button>
        </div>
      </div>
    </aside>
  )
}

function TopBar({ title, waiting, live }) {
  const navigate = useNavigate()
  const { user } = useAuth()
  const today = new Date().toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' })
  return (
    <header className="sticky top-0 z-20 border-b border-line bg-white/85 backdrop-blur">
      <div className="mx-auto flex h-[72px] max-w-[1360px] items-center gap-4 px-6 lg:px-9">
        <div className="min-w-0">
          <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-ink-faint">Demo Multispeciality</p>
          <p className="truncate text-[17px] font-bold tracking-tight">{title}</p>
        </div>
        <div className="relative ml-auto hidden w-full max-w-[340px] lg:block">
          <Icon name="search" className="pointer-events-none absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-faint" />
          <input placeholder="Search patients, claims, insurers…"
            onKeyDown={(e) => e.key === 'Enter' && navigate(`/patients?q=${encodeURIComponent(e.currentTarget.value)}`)}
            className="h-10 w-full rounded-full border border-line bg-canvas pl-10 pr-4 text-sm outline-none transition placeholder:text-ink-faint focus:border-accent-300 focus:bg-white focus:ring-4 focus:ring-accent-100/70" />
        </div>
        <span className="hidden items-center gap-2 rounded-full border border-line px-3.5 py-2 text-[13px] font-semibold text-ink-soft xl:inline-flex">
          <Icon name="calendar" className="h-4 w-4 text-accent-600" />{today}
        </span>
        <span className={`hidden items-center gap-2 rounded-full px-3.5 py-2 text-[13px] font-semibold sm:inline-flex ${live ? 'bg-accent-50 text-accent-700' : 'bg-canvas text-ink-muted'}`}>
          <span className={`h-2 w-2 rounded-full ${live ? 'animate-ring bg-accent-500' : 'bg-ink-faint'}`} />{live ? 'Agents live' : 'Reconnecting'}
        </span>
        <button onClick={() => navigate('/tasks')} title="Tasks" className="relative grid h-10 w-10 shrink-0 place-items-center rounded-full border border-line bg-white text-ink-soft transition hover:border-accent-200 hover:text-accent-700">
          <Icon name="bell" className="h-[18px] w-[18px]" />
          {waiting > 0 && <span key={waiting} className="absolute -right-1 -top-1 grid h-5 min-w-5 animate-scale-in place-items-center rounded-full bg-rose-500 px-1 text-[10px] font-bold text-white ring-2 ring-white">{waiting}</span>}
        </button>
        <div className="flex shrink-0 items-center gap-2.5 border-l border-line pl-4">
          <Avatar name={user?.display_name} size="sm" />
          <div className="hidden leading-tight sm:block">
            <p className="text-sm font-bold">{user?.display_name}</p>
            <p className="text-[11px] text-ink-muted">Billing officer</p>
          </div>
        </div>
      </div>
    </header>
  )
}
