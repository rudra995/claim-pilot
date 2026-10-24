// Insurer desk: the simulated TPA's side of each claim, and how its agents ruled.
import { useNavigate, useParams } from 'react-router-dom'
import { api, useLoad, useTrace } from '../api'
import { Thread } from '../components/claim'
import { Avatar, Badge, Card, CardHeader, EmptyState, Icon, PageHeader, Skeleton, Status, agentName, label, money, procedureName, stagger } from '../components/ui'

const INSURER_AGENTS = ['tpa_medical_reviewer', 'tpa_fraud_screen', 'tpa_adjudicator', 'tpa_query_writer']

export default function Insurer() {
  const { claimId } = useParams()
  const navigate = useNavigate()
  const claims = useLoad(api.claims)
  // Claims the insurer has heard about: past admission, and not waiting on their first approval.
  const sent = (claims.data ?? []).filter((c) =>
    !['ADMITTED', 'PREAUTH_DRAFT', 'RISK_CHECK'].includes(c.state) && !(c.state === 'AWAITING_HUMAN' && c.approved === null)).reverse()
  const selectedId = claimId ?? sent[0]?.claim_id
  const detail = useLoad(() => (selectedId ? api.claim(selectedId) : Promise.resolve(null)), [selectedId])
  useTrace({ onEvent: (event) => { claims.refresh(); if (event.claim_id === selectedId) detail.refresh() } })

  return (
    <>
      <PageHeader title="Insurer desk"
        subtitle="A simulated TPA with its own agents. It sees only what the hospital sends, never the hospital's code or reasoning." />
      {claims.data && !sent.length ? (
        <EmptyState icon="building" title="Nothing has reached an insurer">Approve a pre-auth and it shows up here, from the insurer's side.</EmptyState>
      ) : (
        <div className="grid gap-6 lg:grid-cols-[300px_minmax(0,1fr)]">
          <Card className="self-start overflow-hidden">
            {!claims.data && <div className="space-y-3 p-4">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-12" />)}</div>}
            <ul className="divide-y divide-line">
              {sent.map((c, i) => (
                <li key={c.claim_id} className="animate-fade-up" style={stagger(i)}>
                  <button onClick={() => navigate(`/insurer/${c.claim_id}`)}
                    className={`flex w-full items-center gap-3 px-4 py-3 text-left transition ${c.claim_id === selectedId ? 'bg-zinc-100/80' : 'hover:bg-zinc-50'}`}>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">{c.claim_id}</p>
                      <p className="truncate text-xs text-ink-muted">{c.tpa_id} · {procedureName(c.procedure)}</p>
                    </div>
                    <Status state={c.state} busy={c.busy} />
                  </button>
                </li>
              ))}
            </ul>
          </Card>
          {detail.data ? <Desk claim={detail.data} /> : <Skeleton className="h-96 rounded-xl" />}
        </div>
      )}
    </>
  )
}

function Desk({ claim }) {
  const rulings = INSURER_AGENTS.filter((name) => claim.agents?.[name])
  return (
    <div key={claim.claim_id} className="min-w-0 animate-fade-in space-y-6">
      <Card>
        <div className="flex flex-wrap items-center gap-4 p-5">
          <Avatar name={claim.tpa_id} size="md" />
          <div className="min-w-0 flex-1">
            <p className="font-semibold">{claim.insurer} <span className="font-normal text-ink-muted">via {claim.tpa_id}</span></p>
            <p className="text-[13px] text-ink-muted">{claim.claim_id} · {procedureName(claim.procedure)} · from Demo Multispeciality Hospital</p>
          </div>
          <div className="text-right"><p className="text-xs text-ink-muted">Approved</p><p className="text-lg font-semibold tabular">{money(claim.approved)}</p></div>
        </div>
      </Card>
      {rulings.length > 0 && (
        <Card>
          <CardHeader title="Latest ruling" subtitle="How the insurer's agents reached their last decision" />
          <div className="grid gap-px border-t border-line bg-line sm:grid-cols-2">
            {rulings.map((name) => {
              const out = claim.agents[name].output
              return (
                <div key={name} className="bg-white p-4">
                  <div className="mb-2 flex items-center gap-2"><Icon name="building" className="h-4 w-4 text-ink-muted" /><p className="text-sm font-medium">{agentName(name)}</p><Badge>{label(out.decision)}</Badge></div>
                  <ul className="space-y-1.5">
                    {out.findings.slice(0, 3).map((f, i) => <li key={i} className="text-[13px] leading-relaxed text-ink-muted"><span className="text-ink-soft">{f.issue}</span> — {f.evidence}</li>)}
                  </ul>
                </div>
              )
            })}
          </div>
        </Card>
      )}
      <Thread claim={claim} side="insurer" />
    </div>
  )
}
