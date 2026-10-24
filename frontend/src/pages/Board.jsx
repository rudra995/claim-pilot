// Claims board: every claim as a card, in a column by state, moving as agents work.
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, useLoad, useTrace } from '../api'
import { Button, Card, ErrorNote, RiskBadge, STATE_GROUPS, StateBadge, label, money } from '../components/ui'

export default function Board() {
  const claims = useLoad(api.claims)
  const cases = useLoad(api.cases)
  useTrace({ onEvent: claims.refresh })

  return (
    <div className="space-y-6">
      <StartCase cases={cases.data ?? []} />
      <ErrorNote error={claims.error} />
      <div className="grid gap-4 md:grid-cols-3 xl:grid-cols-6">
        {STATE_GROUPS.map((group) => {
          const inGroup = (claims.data ?? []).filter((c) => group.states.includes(c.state))
          return (
            <div key={group.name} className="min-w-0">
              <h2 className="mb-2 flex items-center justify-between text-xs font-semibold uppercase tracking-wide text-slate-500">
                {group.name}
                <span className="rounded-full bg-slate-200 px-2 text-slate-600">{inGroup.length}</span>
              </h2>
              <div className="space-y-2">
                {inGroup.map((claim) => <ClaimCard key={claim.claim_id} claim={claim} />)}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function ClaimCard({ claim }) {
  return (
    <Link to={`/claims/${claim.claim_id}`} className="block rounded-lg border border-slate-200 bg-white p-3 hover:border-indigo-300 hover:shadow-sm">
      <div className="flex items-start justify-between gap-2">
        <p className="font-medium leading-tight">{claim.patient}</p>
        {claim.busy && <span className="mt-0.5 h-2 w-2 shrink-0 animate-pulse rounded-full bg-indigo-500" title="Agents working" />}
      </div>
      <p className="text-xs text-slate-500">{label(claim.procedure)} · {claim.tpa_id}</p>
      <div className="mt-2 flex flex-wrap gap-1">
        <StateBadge state={claim.state} />
        <RiskBadge risk={claim.risk} />
      </div>
      <p className="mt-2 text-xs text-slate-600">Requested {money(claim.requested)} · Approved {money(claim.approved)}</p>
      {claim.last_reason && <p className="mt-1 line-clamp-2 text-xs text-slate-500">{claim.last_reason}</p>}
      <p className="mt-1 text-[11px] text-slate-400">{claim.claim_id}</p>
    </Link>
  )
}

function StartCase({ cases }) {
  const navigate = useNavigate()
  const [error, setError] = useState(null)
  const [starting, setStarting] = useState(null)
  const start = async (caseId) => {
    setStarting(caseId)
    setError(null)
    try {
      const { claim_id } = await api.start(caseId)
      navigate(`/claims/${claim_id}`)
    } catch (err) {
      setError(err.message)
      setStarting(null)
    }
  }
  return (
    <Card title="Start a demo case">
      <div className="grid gap-3 md:grid-cols-3">
        {cases.map((c) => (
          <div key={c.case_id} className="flex flex-col justify-between gap-2 rounded-md border border-slate-200 p-3">
            <div>
              <p className="text-sm font-medium">{c.title}</p>
              <p className="text-xs text-slate-500">{c.patient} · {label(c.procedure)} · {c.tpa_id}</p>
            </div>
            <Button variant="primary" disabled={starting !== null} onClick={() => start(c.case_id)}>
              {starting === c.case_id ? 'Starting…' : 'Admit and start'}
            </Button>
          </div>
        ))}
      </div>
      <ErrorNote error={error} />
    </Card>
  )
}
