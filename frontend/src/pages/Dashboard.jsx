// Dashboard: how the desk is doing, what needs you, and what just happened.
import { Link } from 'react-router-dom'
import { api, useLoad, useTrace } from '../api'
import { nextStep } from '../story'
import {
  Avatar, Button, Card, CardHeader, CountUp, EmptyState, Icon, Skeleton, Status, ago, money, pct, procedureName, stagger,
} from '../components/ui'

export default function Dashboard() {
  const numbers = useLoad(api.dashboard)
  const claims = useLoad(api.claims)
  useTrace({ onEvent: () => { numbers.refresh(); claims.refresh() } })
  const all = claims.data ?? []
  const tasks = all.filter((c) => c.state === 'AWAITING_HUMAN' || c.state === 'FINAL_APPROVED')
  const recent = [...all].sort((a, b) => (b.updated_at ?? '').localeCompare(a.updated_at ?? '')).slice(0, 6)
  const d = numbers.data
  const today = new Date().toLocaleDateString('en-IN', { weekday: 'long', day: 'numeric', month: 'long' })

  return (
    <>
      <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-sm text-ink-muted">{today}</p>
          <h1 className="mt-1 text-[26px] font-semibold tracking-tight">
            {tasks.length ? `${tasks.length} claim${tasks.length > 1 ? 's' : ''} need${tasks.length > 1 ? '' : 's'} you today` : 'You’re all caught up'}
          </h1>
        </div>
        <Link to="/patients/admit"><Button variant="primary" icon="plus">Admit patient</Button></Link>
      </div>

      <div className="mb-8 grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-line bg-line lg:grid-cols-4">
        <Metric label="Active claims" value={d && all.filter((c) => c.state !== 'CLOSED').length} />
        <Metric label="Approved first time" value={d?.first_pass_rate} format={(v) => pct(v / 100)} scale={100} hint={d?.average_rounds ? `${d.average_rounds} rounds on average` : null} />
        <Metric label="Approved by insurers" value={d?.approved_total} format={money} />
        <Metric label="Requested" value={d?.requested_total} format={money} />
      </div>

      {claims.data && !all.length ? (
        <EmptyState icon="bed" title="No patients yet" action={<Link to="/patients/admit"><Button variant="primary" icon="plus">Admit your first patient</Button></Link>}>
          Admit a patient and the agents prepare their insurance claim while you watch.
        </EmptyState>
      ) : (
        <div className="grid gap-6 lg:grid-cols-5">
          <Card className="lg:col-span-3">
            <CardHeader title="Your tasks" subtitle="Nothing goes to an insurer until you approve it"
              action={tasks.length > 0 && <Link to="/tasks" className="text-[13px] font-medium text-ink-soft hover:text-ink">See all</Link>} />
            {!claims.data ? <ListSkeleton /> : tasks.length ? (
              <ul className="px-2 pb-2">
                {tasks.slice(0, 5).map((c, i) => (
                  <li key={c.claim_id} className="animate-fade-up" style={stagger(i)}>
                    <Link to={`/patients/${c.claim_id}`} className="group flex items-center gap-3 rounded-lg px-3 py-3 transition hover:bg-zinc-50">
                      <Avatar name={c.patient} size="sm" />
                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-medium">{nextStep(c)}</p>
                        <p className="truncate text-[13px] text-ink-muted">{c.patient} · {procedureName(c.procedure)}</p>
                      </div>
                      <span className="text-xs text-ink-faint">{ago(c.updated_at)}</span>
                      <Icon name="arrowRight" className="h-4 w-4 text-ink-faint transition group-hover:translate-x-0.5 group-hover:text-ink" />
                    </Link>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="flex items-center gap-3 px-5 pb-6 text-sm text-ink-muted"><Icon name="check" className="h-4 w-4 text-accent-600" />Nothing waiting. New tasks appear here the moment agents finish.</div>
            )}
          </Card>

          <Card className="lg:col-span-2">
            <CardHeader title="Recently updated" action={<Link to="/patients" className="text-[13px] font-medium text-ink-soft hover:text-ink">All patients</Link>} />
            {!claims.data ? <ListSkeleton /> : (
              <ul className="px-2 pb-2">
                {recent.map((c, i) => (
                  <li key={c.claim_id} className="animate-fade-up" style={stagger(i)}>
                    <Link to={`/patients/${c.claim_id}`} className="flex items-center gap-3 rounded-lg px-3 py-2.5 transition hover:bg-zinc-50">
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium">{c.patient}</p>
                        <p className="truncate text-xs text-ink-muted">{ago(c.updated_at)}</p>
                      </div>
                      <Status state={c.state} busy={c.busy} />
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          {d?.top_deductions?.length > 0 && (
            <Card className="lg:col-span-5">
              <CardHeader title="Where money is lost" subtitle="Deductions by policy clause across insurer decisions" />
              <div className="grid gap-x-8 gap-y-3 px-5 pb-5 sm:grid-cols-2">
                {d.top_deductions.map((row) => {
                  const max = d.top_deductions[0].amount || 1
                  return (
                    <div key={row.clause}>
                      <div className="flex justify-between gap-3 text-[13px]"><span className="truncate text-ink-soft">{/^[0-9]/.test(row.clause) ? `Clause ${row.clause}` : row.clause}</span><span className="shrink-0 font-medium tabular">{money(row.amount)}</span></div>
                      <div className="mt-1.5 h-1 rounded-full bg-zinc-100"><div className="h-full rounded-full bg-ink/70 transition-all duration-700" style={{ width: `${(row.amount / max) * 100}%` }} /></div>
                    </div>
                  )
                })}
              </div>
            </Card>
          )}
        </div>
      )}
    </>
  )
}

function Metric({ label, value, format = (v) => v, scale = 1, hint }) {
  return (
    <div className="bg-white px-5 py-4">
      <p className="text-[13px] text-ink-muted">{label}</p>
      <div className="mt-1 text-2xl font-semibold tracking-tight tabular">
        {value === undefined ? <Skeleton className="mt-1 h-7 w-20" /> : <CountUp value={value === null ? null : value * scale} format={format} />}
      </div>
      {hint && <p className="mt-0.5 text-xs text-ink-faint">{hint}</p>}
    </div>
  )
}

function ListSkeleton() {
  return (
    <div className="space-y-4 px-5 pb-5">
      {[0, 1, 2].map((i) => <div key={i} className="flex items-center gap-3"><Skeleton className="h-8 w-8 rounded-full" /><div className="flex-1 space-y-1.5"><Skeleton className="h-3.5 w-1/3" /><Skeleton className="h-3 w-1/2" /></div></div>)}
    </div>
  )
}
