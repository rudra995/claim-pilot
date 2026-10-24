// Patients: everyone admitted, where their claim is, and what happens next.
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, useLoad, useTrace } from '../api'
import { nextStep } from '../story'
import { Avatar, Button, Card, EmptyState, Icon, PageHeader, Skeleton, Status, ago, money, procedureName, stagger } from '../components/ui'

const FILTERS = [
  ['All', () => true],
  ['Needs you', (c) => c.state === 'AWAITING_HUMAN' || c.state === 'FINAL_APPROVED'],
  ['With insurer', (c) => ['SUBMITTED', 'QUERY_RAISED'].includes(c.state)],
  ['In treatment', (c) => ['IN_TREATMENT', 'DISCHARGE_DRAFT'].includes(c.state)],
  ['Closed', (c) => ['CLOSED', 'DENIED', 'SETTLED'].includes(c.state)],
]

export default function Patients() {
  const claims = useLoad(api.claims)
  useTrace({ onEvent: claims.refresh })
  const navigate = useNavigate()
  const [filter, setFilter] = useState('All')
  const [query, setQuery] = useState('')
  const all = claims.data ?? []
  const q = query.trim().toLowerCase()
  const shown = all
    .filter(FILTERS.find(([name]) => name === filter)[1])
    .filter((c) => !q || [c.patient, c.claim_id, procedureName(c.procedure), c.insurer].some((v) => v?.toLowerCase().includes(q)))
    .reverse()

  return (
    <>
      <PageHeader title="Patients" subtitle="Everyone admitted, and where their insurance claim stands"
        action={<Link to="/patients/admit"><Button variant="primary" icon="plus">Admit patient</Button></Link>} />
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="inline-flex rounded-lg bg-zinc-200/60 p-1">
          {FILTERS.map(([name, test]) => {
            const count = all.filter(test).length
            return (
              <button key={name} onClick={() => setFilter(name)}
                className={`rounded-md px-3 py-1.5 text-[13px] font-medium transition ${filter === name ? 'bg-white text-ink shadow-card' : 'text-ink-muted hover:text-ink'}`}>
                {name}{name !== 'All' && count > 0 && <span className="ml-1.5 text-ink-faint tabular">{count}</span>}
              </button>
            )
          })}
        </div>
        <div className="relative w-64">
          <Icon name="search" className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-faint" />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search patients" className="input pl-9" />
        </div>
      </div>

      {claims.data && !all.length ? (
        <EmptyState icon="users" title="No patients admitted" action={<Link to="/patients/admit"><Button variant="primary" icon="plus">Admit patient</Button></Link>}>
          Pick from the hospital&apos;s preset patients to start.
        </EmptyState>
      ) : (
        <Card className="overflow-hidden">
          <div className="grid grid-cols-[minmax(0,2.2fr)_minmax(0,1.6fr)_minmax(0,1.2fr)_120px_120px] gap-4 border-b border-line bg-zinc-50/70 px-5 py-2.5 text-xs font-medium text-ink-muted">
            <span>Patient</span><span>Next step</span><span>Insurer</span><span className="text-right">Approved</span><span className="text-right">Status</span>
          </div>
          {!claims.data && <div className="space-y-4 p-5">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-9" />)}</div>}
          {claims.data && !shown.length && <p className="px-5 py-10 text-center text-sm text-ink-muted">No patients match.</p>}
          <ul className="divide-y divide-line">
            {shown.map((c, i) => (
              <li key={c.claim_id} onClick={() => navigate(`/patients/${c.claim_id}`)} style={stagger(i)}
                className="grid animate-fade-up cursor-pointer grid-cols-[minmax(0,2.2fr)_minmax(0,1.6fr)_minmax(0,1.2fr)_120px_120px] items-center gap-4 px-5 py-3.5 transition hover:bg-zinc-50">
                <div className="flex min-w-0 items-center gap-3">
                  <Avatar name={c.patient} size="sm" />
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{c.patient}</p>
                    <p className="truncate text-[13px] text-ink-muted">{procedureName(c.procedure)}</p>
                  </div>
                </div>
                <div className="min-w-0">
                  <p className="truncate text-sm">{nextStep(c)}</p>
                  <p className="text-xs text-ink-faint">{ago(c.updated_at)}</p>
                </div>
                <div className="min-w-0">
                  <p className="truncate text-sm">{c.insurer}</p>
                  <p className="text-xs text-ink-faint">{c.tpa_id}</p>
                </div>
                <p className="text-right text-sm font-medium tabular">{money(c.approved)}</p>
                <div className="flex justify-end"><Status state={c.state} busy={c.busy} /></div>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </>
  )
}
