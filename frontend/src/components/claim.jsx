// Pieces that show a claim's work: the agent trace, messages to and from the insurer, and the human gate.
import { useState } from 'react'
import { api } from '../api'
import { Button, ErrorNote, Findings, label, money, useOfficer } from './ui'

// ---- The trace: every agent step, decision, message and state change, as it happens ----------

const QUIET = new Set(['case_file', 'llm_call'])

export function Trace({ events, showQuiet = false }) {
  const shown = showQuiet ? events : events.filter((e) => !QUIET.has(e.action))
  if (!shown.length) return <p className="text-sm text-slate-500">Nothing yet.</p>
  return (
    <ol className="relative space-y-3 border-l border-slate-200 pl-4">
      {shown.map((event) => (
        <TraceStep key={event.event_id} event={event} />
      ))}
    </ol>
  )
}

function TraceStep({ event }) {
  const time = new Date(`${event.created_at.replace(' ', 'T')}${event.created_at.endsWith('Z') ? '' : 'Z'}`).toLocaleTimeString()
  const agent = event.action === 'agent_output' ? event.output.output : null
  const dot = {
    agent_output: 'bg-indigo-500',
    transition: 'bg-slate-400',
    message_sent: 'bg-sky-500',
    human_decision: 'bg-amber-500',
    escalate: 'bg-red-500',
    agent_error: 'bg-red-400',
    run_error: 'bg-red-500',
  }[event.action] ?? 'bg-slate-300'
  return (
    <li className="relative">
      <span className={`absolute -left-[21px] top-1.5 h-2.5 w-2.5 rounded-full ring-2 ring-white ${dot}`} />
      <div className="flex flex-wrap items-baseline gap-x-2">
        <span className="text-xs tabular-nums text-slate-400">{time}</span>
        {agent ? (
          <span className="text-sm">
            <span className="font-semibold">{label(agent.agent)}</span>
            <span className="text-slate-600"> decided </span>
            <span className="font-medium">{label(agent.decision)}</span>
            <span className="text-slate-500"> · confidence {agent.confidence.toFixed(2)} · next: {label(agent.next)}</span>
          </span>
        ) : (
          <span className="text-sm text-slate-700">{event.summary}</span>
        )}
      </div>
      {agent && (
        <div className="mt-1.5">
          <Findings findings={agent.findings} />
        </div>
      )}
    </li>
  )
}

// ---- Messages to and from the insurer --------------------------------------------------------

export function MessageBody({ message }) {
  const stagePackage = message.package?.[message.stage] ?? message.package
  return (
    <div className="space-y-2 text-sm">
      {message.requested_amount !== undefined && <p>Requested: <b>{money(message.requested_amount)}</b></p>}
      {message.approved_amount !== undefined && <p>Approved: <b>{money(message.approved_amount)}</b></p>}
      {message.type === 'SUBMIT' && stagePackage?.justification && <Quote text={stagePackage.justification} />}
      {message.answer && <Quote text={message.answer} />}
      {message.argument && <Quote text={message.argument} />}
      {message.missing && <p><b>Asks for:</b> {message.missing}. <span className="text-slate-600">{message.reason}</span></p>}
      {message.type === 'DENY' && <p><b>Denied:</b> {message.reason} <Clause text={message.clause} /></p>}
      {message.decision && message.type === 'FINAL' && <p>Final decision: <b>{label(message.decision)}</b></p>}
      {message.deductions?.length > 0 && <Deductions deductions={message.deductions} />}
      {message.documents?.length > 0 && <p className="text-xs text-slate-500">Documents: {message.documents.join(', ')}</p>}
      {message.cited_clauses?.length > 0 && <p className="text-xs text-slate-500">Cites clauses: {message.cited_clauses.join(', ')}</p>}
      {message.cited_documents?.length > 0 && <p className="text-xs text-slate-500">Cites records: {message.cited_documents.join(', ')}</p>}
    </div>
  )
}

function Quote({ text }) {
  return <p className="whitespace-pre-line rounded-md bg-slate-50 px-3 py-2 text-slate-700">{text}</p>
}

const Clause = ({ text }) => <span className="ml-1 rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-600">{text}</span>

export function Deductions({ deductions }) {
  return (
    <table className="w-full text-left text-sm">
      <thead className="text-xs uppercase text-slate-500">
        <tr><th className="py-1 font-medium">Deduction</th><th className="py-1 text-right font-medium">Amount</th><th className="py-1 pl-3 font-medium">Clause</th></tr>
      </thead>
      <tbody className="divide-y divide-slate-100">
        {deductions.map((d, i) => (
          <tr key={i}>
            <td className="py-1 pr-2">{d.item}</td>
            <td className="py-1 text-right tabular-nums">{money(d.amount)}</td>
            <td className="py-1 pl-3 text-slate-600">{d.clause ?? d.clause_id}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// ---- The human gate --------------------------------------------------------------------------

// What a person decides on: the drafted message, if any, and approve / edit / reject / accept.
export function DecisionPanel({ claim, onDone }) {
  const [officer] = useOfficer()
  const [mode, setMode] = useState(null) // 'edit' | 'reject'
  const [amount, setAmount] = useState('')
  const [text, setText] = useState('')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const draft = claim.pending_draft?.message
  const can = (action) => claim.actions?.includes(action)

  const send = async (decision) => {
    setBusy(true)
    setError(null)
    try {
      await api.decide(claim.claim_id, { officer, ...decision })
      setMode(null)
      onDone?.()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const startEdit = () => {
    const stagePackage = draft?.package?.[draft.stage] ?? draft?.package
    setAmount(draft?.requested_amount ?? '')
    setText(draft?.answer ?? draft?.argument ?? stagePackage?.justification ?? '')
    setMode('edit')
  }

  const edits = () => {
    const out = {}
    if (draft?.type === 'SUBMIT' && amount !== '' && Number(amount) !== draft.requested_amount) out.requested_amount = Number(amount)
    if (text.trim()) out.justification = text.trim()
    return out
  }

  return (
    <div className="space-y-3">
      {draft ? (
        <div className="rounded-md border border-amber-200 bg-amber-50/50 p-3">
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-amber-800">
            Drafted {draft.type} · {label(draft.stage)} · round {draft.round}
          </p>
          <MessageBody message={draft} />
        </div>
      ) : (
        <p className="text-sm text-slate-600">Nothing drafted to send. Accept the insurer&apos;s decision, or reject to send it back.</p>
      )}

      {mode === 'edit' && (
        <div className="space-y-2">
          {draft?.type === 'SUBMIT' && (
            <label className="block text-sm">Amount to request (₹)
              <input type="number" min="0" value={amount} onChange={(e) => setAmount(e.target.value)} className="mt-1 block w-48 rounded-md border-slate-300 px-2 py-1 ring-1 ring-slate-300" />
            </label>
          )}
          <label className="block text-sm">Text sent to the insurer
            <textarea rows={5} value={text} onChange={(e) => setText(e.target.value)} className="mt-1 block w-full rounded-md px-2 py-1 ring-1 ring-slate-300" />
          </label>
          <div className="flex gap-2">
            <Button variant="primary" disabled={busy} onClick={() => send({ decision: 'edit', edits: edits() })}>Approve with edits</Button>
            <Button onClick={() => setMode(null)}>Cancel</Button>
          </div>
        </div>
      )}
      {mode === 'reject' && (
        <div className="space-y-2">
          <label className="block text-sm">Why reject?
            <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. attach the X-ray report first" className="mt-1 block w-full rounded-md px-2 py-1 ring-1 ring-slate-300" />
          </label>
          <div className="flex gap-2">
            <Button variant="danger" disabled={busy || !reason.trim()} onClick={() => send({ decision: 'reject', reason: reason.trim() })}>Reject</Button>
            <Button onClick={() => setMode(null)}>Cancel</Button>
          </div>
        </div>
      )}
      {!mode && (
        <div className="flex flex-wrap gap-2">
          {can('approve') && <Button variant="primary" disabled={busy} onClick={() => send({ decision: 'approve' })}>Approve and send</Button>}
          {can('edit') && <Button disabled={busy} onClick={startEdit}>Edit</Button>}
          {can('accept') && <Button disabled={busy} onClick={() => send({ decision: 'accept' })}>Accept insurer&apos;s decision</Button>}
          {can('reject') && <Button variant="danger" disabled={busy} onClick={() => setMode('reject')}>Reject</Button>}
        </div>
      )}
      <p className="text-xs text-slate-500">Deciding as <b>{officer}</b></p>
      <ErrorNote error={error} />
    </div>
  )
}
