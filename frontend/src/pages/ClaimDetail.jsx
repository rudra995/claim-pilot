// Claim detail: the live agent trace, what the agents found, the insurer thread, and what to do next.
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, useLoad, useTrace } from '../api'
import { DecisionPanel, MessageBody, Trace } from '../components/claim'
import { Button, Card, ErrorNote, Findings, RiskBadge, StateBadge, label, money } from '../components/ui'

const TABS = ['Live trace', 'Agents', 'Insurer thread', 'Bill and documents']

export default function ClaimDetail() {
  const { claimId } = useParams()
  const claim = useLoad(() => api.claim(claimId), [claimId])
  const [events, setEvents] = useState([])
  const [after, setAfter] = useState(null)
  const [tab, setTab] = useState(TABS[0])
  const [showQuiet, setShowQuiet] = useState(false)

  // Seed the trace from the claim, then stream new steps live.
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
  const live = useTrace({
    claimId,
    after,
    onEvent: (event) => {
      setEvents((shown) => (shown.some((e) => e.event_id === event.event_id) ? shown : [...shown, event]))
      claim.refresh()
    },
  })

  if (claim.error) return <ErrorNote error={claim.error} />
  if (!claim.data) return <p className="text-sm text-slate-500">Loading…</p>
  const c = claim.data

  return (
    <div className="space-y-4">
      <Header claim={c} live={live} />
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <nav className="flex gap-1 border-b border-slate-200">
            {TABS.map((name) => (
              <button key={name} onClick={() => setTab(name)}
                className={`-mb-px border-b-2 px-3 py-2 text-sm ${tab === name ? 'border-indigo-600 font-medium text-indigo-700' : 'border-transparent text-slate-500 hover:text-slate-700'}`}>
                {name}
              </button>
            ))}
          </nav>
          {tab === 'Live trace' && (
            <Card title="Every step, as it happens" actions={
              <label className="flex items-center gap-1.5 text-xs text-slate-500">
                <input type="checkbox" checked={showQuiet} onChange={(e) => setShowQuiet(e.target.checked)} />
                Show LLM calls and case files
              </label>
            }>
              <Trace events={events} showQuiet={showQuiet} />
            </Card>
          )}
          {tab === 'Agents' && <Agents agents={c.agents} />}
          {tab === 'Insurer thread' && <Thread thread={c.thread} />}
          {tab === 'Bill and documents' && <CaseFile caseFile={c.case} />}
        </div>
        <div className="space-y-4">
          <NextStep claim={c} onDone={claim.reload} />
          {c.state === 'AWAITING_HUMAN' && c.flags?.length > 0 && (
            <Card title="Why it needs a person">
              <ul className="list-disc space-y-1 pl-4 text-sm text-slate-700">{c.flags.map((f, i) => <li key={i}>{f}</li>)}</ul>
            </Card>
          )}
        </div>
      </div>
    </div>
  )
}

function Header({ claim, live }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div>
        <Link to="/" className="text-xs text-indigo-600 hover:underline">← Claims board</Link>
        <h1 className="text-xl font-semibold">{claim.patient} <span className="font-normal text-slate-500">· {label(claim.procedure)}</span></h1>
        <p className="text-sm text-slate-500">{claim.claim_id} · {claim.insurer} · {claim.tpa_id} · sum insured {money(claim.sum_insured)}</p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <StateBadge state={claim.state} />
        <RiskBadge risk={claim.risk} />
        <span className="text-sm text-slate-600">Requested {money(claim.requested)} · Approved {money(claim.approved)}</span>
        <span className={`text-xs ${live ? 'text-emerald-600' : 'text-slate-400'}`}>{live ? '● live' : '○ connecting'}</span>
        {claim.busy && <span className="text-xs text-indigo-600">agents working…</span>}
      </div>
    </div>
  )
}

// What can happen next, depending on the claim's state.
function NextStep({ claim, onDone }) {
  const can = (action) => claim.actions?.includes(action)
  return (
    <Card title="Next step">
      {claim.errors?.length > 0 && <ErrorNote error={claim.errors.at(-1).error} />}
      {claim.busy && <p className="text-sm text-slate-600">Agents are working. Steps appear in the trace as they happen.</p>}
      {!claim.busy && claim.state === 'AWAITING_HUMAN' && <DecisionPanel claim={claim} onDone={onDone} />}
      {!claim.busy && can('bill') && <TreatmentActions claim={claim} onDone={onDone} />}
      {!claim.busy && can('payment') && <PaymentForm claim={claim} onDone={onDone} />}
      {!claim.busy && ['CLOSED', 'SETTLED', 'DENIED'].includes(claim.state) && <p className="text-sm text-slate-600">{claim.last_reason}</p>}
      {can('patient_note') && (
        <p className="mt-3 text-sm"><Link className="text-indigo-600 hover:underline" to={`/patient/${claim.claim_id}`}>Patient view →</Link></p>
      )}
    </Card>
  )
}

function TreatmentActions({ claim, onDone }) {
  const [description, setDescription] = useState('ICU, 1 day')
  const [category, setCategory] = useState('icu')
  const [amount, setAmount] = useState('12000')
  const [error, setError] = useState(null)
  const run = async (work) => {
    setError(null)
    try {
      await work()
      onDone()
    } catch (err) {
      setError(err.message)
    }
  }
  const addLine = () => {
    const n = claim.case.bill_lines.length + 1
    return api.addBill(claim.claim_id, [{
      line_id: `${claim.admission_id}-L${n}-${Date.now() % 100000}`,
      category, description, quantity: 1, amount: Number(amount),
    }])
  }
  return (
    <div className="space-y-3">
      <p className="text-sm text-slate-600">The patient is in treatment. Add charges to the running bill, or start discharge.</p>
      <div className="grid grid-cols-2 gap-2 text-sm">
        <input value={description} onChange={(e) => setDescription(e.target.value)} className="col-span-2 rounded-md px-2 py-1 ring-1 ring-slate-300" />
        <select value={category} onChange={(e) => setCategory(e.target.value)} className="rounded-md px-2 py-1 ring-1 ring-slate-300">
          {['room', 'icu', 'doctor_fees', 'nursing', 'ot_charges', 'implants', 'pharmacy', 'investigations', 'consumables', 'other'].map((c) => <option key={c} value={c}>{label(c)}</option>)}
        </select>
        <input type="number" min="0" value={amount} onChange={(e) => setAmount(e.target.value)} className="rounded-md px-2 py-1 ring-1 ring-slate-300" />
      </div>
      <div className="flex flex-wrap gap-2">
        <Button onClick={() => run(addLine)}>Add to bill</Button>
        <Button variant="primary" onClick={() => run(() => api.discharge(claim.claim_id))}>Discharge</Button>
      </div>
      <ErrorNote error={error} />
    </div>
  )
}

function PaymentForm({ claim, onDone }) {
  const [paid, setPaid] = useState(String(claim.approved ?? 0))
  const [reference, setReference] = useState(`UTR-${claim.claim_id}`)
  const [error, setError] = useState(null)
  const pay = async () => {
    setError(null)
    try {
      await api.pay(claim.claim_id, Number(paid), reference)
      onDone()
    } catch (err) {
      setError(err.message)
    }
  }
  return (
    <div className="space-y-2">
      <p className="text-sm text-slate-600">Final approval received. Record the insurer&apos;s payment to settle and reconcile.</p>
      <label className="block text-sm">Amount paid (₹)
        <input type="number" min="0" value={paid} onChange={(e) => setPaid(e.target.value)} className="mt-1 block w-full rounded-md px-2 py-1 ring-1 ring-slate-300" />
      </label>
      <label className="block text-sm">Payment reference
        <input value={reference} onChange={(e) => setReference(e.target.value)} className="mt-1 block w-full rounded-md px-2 py-1 ring-1 ring-slate-300" />
      </label>
      <Button variant="primary" onClick={pay}>Record payment</Button>
      <ErrorNote error={error} />
    </div>
  )
}

function Agents({ agents }) {
  const entries = Object.entries(agents ?? {})
  if (!entries.length) return <p className="text-sm text-slate-500">No agent has reported yet.</p>
  return (
    <div className="space-y-3">
      {entries.map(([name, result]) => (
        <Card key={name} title={label(name)} actions={
          <span className="text-xs text-slate-500">{label(result.output.decision)} · confidence {result.output.confidence.toFixed(2)} · next: {label(result.output.next)}</span>
        }>
          <Findings findings={result.output.findings} />
        </Card>
      ))}
    </div>
  )
}

function Thread({ thread }) {
  if (!thread?.length) return <p className="text-sm text-slate-500">Nothing has been sent to the insurer yet.</p>
  return (
    <div className="space-y-3">
      {thread.map((m, i) => (
        <div key={i} className={`max-w-[85%] rounded-lg border p-3 ${m.sender === 'hospital' ? 'ml-auto border-indigo-200 bg-indigo-50/40' : 'border-slate-200 bg-white'}`}>
          <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
            {m.sender} · {m.type} · {label(m.stage)} · round {m.round}
          </p>
          <MessageBody message={m} />
        </div>
      ))}
    </div>
  )
}

function CaseFile({ caseFile }) {
  const total = caseFile.bill_lines.reduce((sum, l) => sum + l.amount, 0)
  return (
    <div className="space-y-4">
      <Card title={`Bill · ${money(total)}`}>
        <table className="w-full text-left text-sm">
          <thead className="text-xs uppercase text-slate-500">
            <tr><th className="py-1 font-medium">Line</th><th className="py-1 font-medium">Category</th><th className="py-1 font-medium">Description</th><th className="py-1 text-right font-medium">Amount</th></tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {caseFile.bill_lines.map((l) => (
              <tr key={l.line_id}>
                <td className="py-1 pr-2 text-xs text-slate-500">{l.line_id}</td>
                <td className="py-1 pr-2">{label(l.category)}</td>
                <td className="py-1 pr-2">{l.description}</td>
                <td className="py-1 text-right tabular-nums">{money(l.amount)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      <Card title="Documents">
        <ul className="space-y-1 text-sm">
          {caseFile.documents.map((d) => <li key={d.doc_id}><span className="text-slate-500">{d.doc_id}</span> · {label(d.type)}</li>)}
        </ul>
      </Card>
      <Card title="Policy rules">
        <ul className="space-y-1 text-sm">
          {caseFile.policy.rules.map((r) => <li key={r.rule_id}><b>{label(r.type)}</b> · clause {r.clause_id}: <span className="text-slate-600">{r.clause_text}</span></li>)}
        </ul>
      </Card>
    </div>
  )
}
