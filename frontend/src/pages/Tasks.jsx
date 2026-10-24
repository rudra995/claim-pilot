// Tasks: everything waiting for the officer, each with what it is and why.
import { Link } from 'react-router-dom'
import { api, useLoad, useTrace } from '../api'
import { situation } from '../story'
import { Avatar, Badge, Card, EmptyState, Icon, IconChip, PageHeader, Skeleton, ago, money, procedureName, riskTone, riskWord, stagger } from '../components/ui'

// An icon and tint for each kind of decision, so the queue scans at a glance.
const KIND_ICON = {
  preauth: ['send', 'blue'], appeal: ['shield', 'amber'], reply: ['chat', 'blue'], enhancement: ['rupee', 'violet'],
  discharge: ['file', 'green'], denied: ['x', 'rose'], stuck: ['alert', 'rose'], payment: ['rupee', 'green'],
}

const EXPLAIN = {
  preauth: (c) => `Pre-auth for ${money(c.pending_draft?.message?.requested_amount)} is ready to send to ${c.insurer}.`,
  appeal: (c) => `${c.insurer} cut the claim. An appeal is drafted.`,
  reply: (c) => `${c.insurer} asked a question. A reply is drafted.`,
  enhancement: (c) => `The bill passed the approval. A request for ${money(c.pending_draft?.message?.requested_amount)} is drafted.`,
  discharge: (c) => `Final claim for ${money(c.pending_draft?.message?.requested_amount)} is ready to send.`,
  denied: (c) => `${c.insurer} denied the claim. Accept it, or hold it back to rework.`,
  stuck: () => 'An agent couldn’t finish. Check the claim.',
  payment: (c) => `Final approval of ${money(c.approved)} is in. Record the payment.`,
}

export default function Tasks() {
  const queue = useLoad(api.queue)
  const claims = useLoad(api.claims)
  useTrace({ onEvent: () => { queue.refresh(); claims.refresh() } })
  const payments = (claims.data ?? []).filter((c) => c.state === 'FINAL_APPROVED').map((c) => ({ ...c, thread: [], pending_draft: null }))
  const tasks = [...(queue.data ?? []).filter((c) => !c.busy), ...payments]

  return (
    <>
      <PageHeader eyebrow="Your queue" title="Tasks" subtitle="Decisions only you can make. The agents have done the rest." />
      {!queue.data ? (
        <div className="space-y-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-20 rounded-xl" />)}</div>
      ) : !tasks.length ? (
        <EmptyState icon="check" title="All clear">When agents finish preparing something, it lands here for your approval.</EmptyState>
      ) : (
        <Card className="overflow-hidden">
          <ul className="divide-y divide-line">
            {tasks.map((c, i) => {
              const s = c.state === 'FINAL_APPROVED' ? { kind: 'payment', task: 'Record payment' } : situation({ ...c, thread: c.thread ?? [] })
              const risk = c.agents?.denial_risk?.data
              return (
                <li key={c.claim_id} className="animate-fade-up" style={stagger(i)}>
                  <Link to={`/patients/${c.claim_id}`} className="group flex items-center gap-4 px-6 py-4 transition hover:bg-accent-50/40">
                    <IconChip name={(KIND_ICON[s.kind] ?? KIND_ICON.stuck)[0]} tone={(KIND_ICON[s.kind] ?? KIND_ICON.stuck)[1]} />
                    <Avatar name={c.patient} size="sm" />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="font-bold">{s.task}</p>
                        <span className="text-ink-faint">·</span>
                        <p className="text-sm text-ink-soft">{c.patient}</p>
                      </div>
                      <p className="mt-0.5 truncate text-[13px] text-ink-muted">{(EXPLAIN[s.kind] ?? EXPLAIN.stuck)(c)}</p>
                    </div>
                    <div className="hidden text-right sm:block">
                      <p className="text-[13px] text-ink-muted">{procedureName(c.procedure)}</p>
                      <p className="text-xs text-ink-faint">{ago(c.updated_at)}</p>
                    </div>
                    {risk && s.kind === 'preauth' && <Badge tone={riskTone(risk.risk)}>{riskWord(risk.risk)} risk</Badge>}
                    <span className="grid h-8 w-8 place-items-center rounded-full bg-canvas text-ink-muted transition group-hover:bg-accent-600 group-hover:text-white"><Icon name="arrowRight" className="h-4 w-4" /></span>
                  </Link>
                </li>
              )
            })}
          </ul>
        </Card>
      )}
    </>
  )
}
