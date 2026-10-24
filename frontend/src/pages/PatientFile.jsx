// The patient file: who they are, where the claim is, what happened, and the one thing to do now.
import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { api, useLoad, useTrace } from '../api'
import { ActionCard, AgentLog, Deductions, Journey, RiskSummary, StageProgress, Thread } from '../components/claim'
import PatientDesk from '../components/PatientDesk'
import {
  Avatar, Badge, Card, CardHeader, EmptyState, ErrorNote, Icon, Skeleton, Status, label, money, procedureName, roomName, stagger,
} from '../components/ui'

const TABS = [
  ['overview', 'Overview'],
  ['insurer', 'Insurer messages'],
  ['bill', 'Bill and documents'],
  ['estimate', 'Patient estimate'],
  ['log', 'Agent log'],
  ['portal', 'Patient portal'],
]

export default function PatientFile() {
  const { claimId } = useParams()
  const [params, setParams] = useSearchParams()
  const tab = params.get('tab') ?? 'overview'
  const claim = useLoad(() => api.claim(claimId), [claimId])
  const [events, setEvents] = useState([])
  const [after, setAfter] = useState(null)

  useEffect(() => {
    setEvents([])
    setAfter(null)
  }, [claimId])
  useEffect(() => {
    if (claim.data && after === null) {
      setEvents(claim.data.timeline)
      setAfter(claim.data.timeline.at(-1)?.event_id ?? 0)
    }
  }, [claim.data, after])
  useTrace({
    claimId,
    after,
    onEvent: (event) => {
      setEvents((shown) => (shown.some((e) => e.event_id === event.event_id) ? shown : [...shown, event]))
      claim.refresh()
    },
  })

  if (claim.error) return <ErrorNote error={claim.error} />
  if (!claim.data) return <FileSkeleton />
  const c = { ...claim.data, timeline: events.length ? events : claim.data.timeline }

  return (
    <>
      <Link to="/patients" className="mb-4 inline-flex items-center gap-1.5 text-[13px] text-ink-muted hover:text-ink"><Icon name="arrowLeft" className="h-4 w-4" />Patients</Link>
      <header className="mb-6 flex flex-wrap items-start gap-4">
        <Avatar name={c.patient} size="lg" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2.5">
            <h1 className="text-[22px] font-semibold tracking-tight">{c.patient}</h1>
            <Status state={c.state} busy={c.busy} />
          </div>
          <p className="mt-1 text-sm text-ink-muted">
            {procedureName(c.procedure)} · {c.age} years · {roomName(c.room_category)} · {c.insurer} ({c.tpa_id})
          </p>
        </div>
        <div className="flex gap-8 text-right">
          <div><p className="text-xs text-ink-muted">Requested</p><p className="mt-0.5 text-lg font-semibold tabular">{money(c.requested)}</p></div>
          <div><p className="text-xs text-ink-muted">Approved</p><p className="mt-0.5 text-lg font-semibold text-accent-700 tabular">{money(c.approved)}</p></div>
        </div>
      </header>

      <div className="mb-6"><StageProgress claim={c} /></div>

      <nav className="mb-6 flex gap-6 border-b border-line">
        {TABS.map(([key, name]) => (
          <button key={key} onClick={() => setParams(key === 'overview' ? {} : { tab: key }, { replace: true })}
            className={`relative -mb-px pb-3 text-sm transition-colors ${tab === key ? 'font-medium text-ink' : 'text-ink-muted hover:text-ink'}`}>
            {name}
            {key === 'insurer' && c.thread.length > 0 && <span className="ml-1.5 text-ink-faint tabular">{c.thread.length}</span>}
            {tab === key && <span className="absolute inset-x-0 bottom-0 h-0.5 animate-fade-in rounded-full bg-ink" />}
          </button>
        ))}
      </nav>

      <div key={tab} className="animate-fade-up">
        {tab === 'portal' && <PatientDesk claimId={claimId} onDone={claim.reload} />}
        {tab === 'overview' && <Overview claim={c} onDone={claim.reload} onTab={(t) => setParams({ tab: t })} />}
        {tab === 'insurer' && <InsurerTab claim={c} />}
        {tab === 'bill' && <BillTab claim={c} />}
        {tab === 'estimate' && <EstimateTab claimId={claimId} />}
        {tab === 'log' && (
          <>
            <p className="mb-4 text-sm text-ink-muted">Every agent step in order. Open one to see its findings, each with the document or clause it came from. LLM prompts are masked.</p>
            <AgentLog events={c.timeline} />
          </>
        )}
      </div>
    </>
  )
}

function Overview({ claim, onDone, onTab }) {
  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_400px]">
      <div className="space-y-6 lg:order-2">
        <ActionCard claim={claim} onDone={onDone} />
        {claim.agents?.denial_risk && (
          <Card>
            <CardHeader title="Risk check" subtitle="From the denial model, trained on past claims" />
            <div className="px-5 pb-5"><RiskSummary risk={claim.agents.denial_risk.data} /></div>
          </Card>
        )}
      </div>
      <Card className="self-start lg:order-1">
        <CardHeader title="What's happened" subtitle="Each step by the agents, by you, and by the insurer"
          action={claim.thread.length > 0 && <button onClick={() => onTab('insurer')} className="text-[13px] font-medium text-ink-soft hover:text-ink">Messages</button>} />
        <div className="px-5 pb-6 pt-2"><Journey claim={claim} /></div>
      </Card>
    </div>
  )
}

function InsurerTab({ claim }) {
  if (!claim.thread.length) {
    return <EmptyState icon="chat" title="Nothing sent yet">Once you approve the pre-auth, every message between the hospital and {claim.insurer} appears here.</EmptyState>
  }
  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_300px]">
      <Thread claim={claim} />
      <Card className="self-start">
        <CardHeader title="How this works" />
        <ul className="space-y-3 px-5 pb-5 text-[13px] leading-relaxed text-ink-muted">
          <li className="flex gap-2.5"><Icon name="send" className="mt-0.5 h-4 w-4 shrink-0 text-ink-soft" />You approve every request, reply and appeal before it goes.</li>
          <li className="flex gap-2.5"><Icon name="building" className="mt-0.5 h-4 w-4 shrink-0 text-ink-soft" />{claim.insurer} can approve, cut, ask a question, or deny. Every cut names a policy clause.</li>
          <li className="flex gap-2.5"><Icon name="sparkle" className="mt-0.5 h-4 w-4 shrink-0 text-ink-soft" />If a cut isn&apos;t in the policy, the appeal writer drafts an appeal for you to send.</li>
          <li className="flex gap-2.5"><Icon name="clock" className="mt-0.5 h-4 w-4 shrink-0 text-ink-soft" />Each stage gets three rounds. After that, you decide.</li>
        </ul>
      </Card>
    </div>
  )
}

function BillTab({ claim }) {
  const lines = claim.case.bill_lines
  const total = claim.totals?.billed  // from the API's rules-engine helpers
  const assessment = claim.agents?.policy?.data?.assessment
  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
      <div className="space-y-6">
        <Card className="overflow-hidden">
          <CardHeader title="Itemised bill" subtitle={`${lines.length} lines`} />
          <ul className="divide-y divide-line border-t border-line">
            {lines.map((l, i) => (
              <li key={l.line_id} className="flex animate-fade-up items-center justify-between gap-4 px-5 py-3" style={stagger(i, 20)}>
                <div className="min-w-0"><p className="truncate text-sm">{l.description}</p><p className="text-xs text-ink-faint">{label(l.category)}</p></div>
                <span className="text-sm tabular">{money(l.amount)}</span>
              </li>
            ))}
          </ul>
          <div className="flex justify-between border-t border-line bg-zinc-50/70 px-5 py-3 text-sm font-semibold"><span>Total</span><span className="tabular">{money(total)}</span></div>
        </Card>
        {assessment?.deductions?.length > 0 && (
          <Card>
            <CardHeader title="What the policy won't pay" subtitle="Priced by the rules engine before anything is sent" />
            <div className="px-5 pb-5"><Deductions deductions={assessment.deductions.map((d) => ({ item: d.item, amount: d.amount, clause: d.clause_id }))} /></div>
          </Card>
        )}
      </div>
      <div className="space-y-6">
        <Card>
          <CardHeader title="Documents" subtitle="Read by the document reader" />
          <ul className="space-y-1 px-3 pb-3">
            {claim.case.documents.map((d) => (
              <li key={d.doc_id} className="flex items-center gap-3 rounded-lg px-2 py-2">
                <span className="grid h-8 w-8 place-items-center rounded-md bg-zinc-100 text-ink-muted"><Icon name="file" className="h-4 w-4" /></span>
                <div><p className="text-sm">{label(d.type)}</p><p className="text-xs text-ink-faint">{d.doc_id}</p></div>
              </li>
            ))}
          </ul>
        </Card>
        <Card>
          <CardHeader title="Policy" subtitle={`${claim.case.policy.product} · ${money(claim.case.policy.sum_insured)}`} />
          <ul className="space-y-3 px-5 pb-5">
            {claim.case.policy.rules.map((r) => (
              <li key={r.rule_id} className="text-[13px]">
                <p className="font-medium">{label(r.type)} <span className="font-normal text-ink-faint">· clause {r.clause_id}</span></p>
                <p className="mt-0.5 leading-relaxed text-ink-muted">{r.clause_text}</p>
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </div>
  )
}

function EstimateTab({ claimId }) {
  const note = useLoad(() => api.patientNote(claimId), [claimId])
  if (note.error) return <ErrorNote error={note.error} />
  if (!note.data) return <Skeleton className="h-48 rounded-xl" />
  const n = note.data.note
  const current = n.options.find((o) => o.current)
  return (
    <div className="space-y-6">
      <Card className="overflow-hidden">
        <div className="grid gap-6 p-6 md:grid-cols-[1fr_auto]">
          <div>
            <p className="text-xs font-medium uppercase tracking-[0.08em] text-ink-muted">For the patient · {n.basis === 'final_bill' ? 'on the final bill' : 'estimate at admission'}</p>
            <p className="mt-2 max-w-2xl text-[15px] leading-relaxed text-ink-soft">{n.note}</p>
            {note.data.output.next === 'human_review' && <p className="mt-3 text-[13px] text-amber-700">A member of staff should talk this through before it&apos;s shared.</p>}
          </div>
          {current && (
            <div className="rounded-xl bg-zinc-50 p-5 text-right">
              <p className="text-[13px] text-ink-muted">Patient pays about</p>
              <p className="mt-1 text-3xl font-semibold tracking-tight tabular">{money(current.patient_pays)}</p>
              <p className="mt-1 text-[13px] text-ink-muted">Insurer pays {money(current.insurer_pays)}</p>
            </div>
          )}
        </div>
        {n.recommended && (
          <div className="flex items-center gap-3 border-t border-line bg-accent-50/60 px-6 py-4 text-sm text-accent-700">
            <Icon name="sparkle" className="h-4 w-4 shrink-0" />
            <span>A <b className="font-semibold">{n.recommended.room.toLowerCase()}</b> at {money(n.recommended.rate)} a day saves the patient about <b className="font-semibold">{money(n.recommended.saving)}</b>.</span>
          </div>
        )}
      </Card>
      {n.options.length > 0 && (
        <div>
          <h2 className="mb-3 text-[15px] font-semibold">The same stay, room by room <span className="text-[13px] font-normal text-ink-muted">· tariff scenarios, not live availability</span></h2>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {n.options.map((o, i) => {
              const best = n.recommended?.room === o.room
              return (
                <div key={o.room} style={stagger(i)} className={`animate-fade-up rounded-xl border bg-white p-4 transition hover:shadow-lift ${best ? 'border-accent-500 ring-1 ring-accent-500' : 'border-line'}`}>
                  <div className="flex items-start justify-between gap-2">
                    <div><p className="font-medium">{o.room}</p><p className="text-[13px] text-ink-muted">{money(o.rate)} a day</p></div>
                    {o.current ? <Badge>Current</Badge> : best ? <Badge tone="good">Best value</Badge> : null}
                  </div>
                  <p className="mt-4 text-xl font-semibold tabular">{money(o.patient_pays)}</p>
                  <p className="text-xs text-ink-muted">patient pays</p>
                  {!o.current && (
                    <p className={`mt-2 text-[13px] font-medium ${o.saving > 0 ? 'text-accent-700' : o.saving < 0 ? 'text-rose-600' : 'text-ink-faint'}`}>
                      {o.saving > 0 ? `${money(o.saving)} less` : o.saving < 0 ? `${money(-o.saving)} more` : 'No difference'}
                    </p>
                  )}
                </div>
              )
            })}
          </div>
          <p className="mt-3 text-xs text-ink-faint">Every amount is calculated by the rules engine from this patient&apos;s policy. None is written by AI.</p>
          {n.clause && <p className="mt-2 text-xs text-ink-muted">Why the room matters: {n.clause}</p>}
          {n.assumptions?.length > 0 && (
            <ul className="mt-2 list-disc space-y-0.5 pl-4 text-xs text-ink-faint">{n.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
          )}
        </div>
      )}
    </div>
  )
}

function FileSkeleton() {
  return (
    <div className="space-y-6">
      <div className="flex items-center gap-4"><Skeleton className="h-14 w-14 rounded-full" /><div className="space-y-2"><Skeleton className="h-6 w-48" /><Skeleton className="h-4 w-72" /></div></div>
      <Skeleton className="h-6" />
      <div className="grid gap-6 lg:grid-cols-[1fr_400px]"><Skeleton className="h-96 rounded-xl" /><Skeleton className="h-64 rounded-xl" /></div>
    </div>
  )
}
