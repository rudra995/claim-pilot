// Approval queue: every claim waiting for a person, with its risk, what it would send, and why it was flagged.
import { Link } from 'react-router-dom'
import { api, useLoad, useTrace } from '../api'
import { DecisionPanel } from '../components/claim'
import { Card, ErrorNote, Findings, RiskBadge, StateBadge, label } from '../components/ui'

export default function Queue() {
  const queue = useLoad(api.queue)
  useTrace({ onEvent: queue.refresh })
  const waiting = queue.data ?? []

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Approval queue</h1>
        <p className="text-sm text-slate-500">Nothing reaches the insurer until a person approves it here.</p>
      </div>
      <ErrorNote error={queue.error} />
      {queue.data && !waiting.length && <p className="text-sm text-slate-500">Nobody is waiting. Start a case from the claims board.</p>}
      {waiting.map((claim) => (
        <Card key={claim.claim_id} title={
          <span className="flex flex-wrap items-center gap-2">
            <Link to={`/claims/${claim.claim_id}`} className="hover:underline">{claim.patient} · {label(claim.procedure)}</Link>
            <StateBadge state={claim.state} />
            <RiskBadge risk={claim.risk} />
          </span>
        } actions={<span className="text-xs text-slate-400">{claim.claim_id} · {claim.tpa_id}</span>}>
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-3">
              {claim.flags?.length > 0 && (
                <ul className="list-disc space-y-0.5 pl-4 text-sm text-amber-800">{claim.flags.map((f, i) => <li key={i}>{f}</li>)}</ul>
              )}
              {claim.agents?.denial_risk && (
                <div>
                  <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Denial-risk check</p>
                  <Findings findings={claim.agents.denial_risk.output.findings} />
                  {claim.agents.denial_risk.data.items?.length > 0 && (
                    <ul className="mt-2 list-disc space-y-0.5 pl-4 text-sm text-slate-700">
                      {claim.agents.denial_risk.data.items.map((item) => <li key={item.flag}><b>Fix:</b> {item.fix}</li>)}
                    </ul>
                  )}
                </div>
              )}
            </div>
            {claim.busy ? <p className="text-sm text-slate-600">Agents are working…</p> : <DecisionPanel claim={claim} onDone={queue.reload} />}
          </div>
        </Card>
      ))}
    </div>
  )
}
