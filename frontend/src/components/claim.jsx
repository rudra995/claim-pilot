// The patient file's building blocks: progress, the journey, the one thing to do now, and the insurer thread.
import { useState } from 'react'
import { api, documentUrl, fileToBase64 } from '../api'
import { journey, situation } from '../story'
import {
  Avatar, Badge, Button, Card, ErrorNote, Icon, Spinner, agentName, label, money, pct, riskTone, riskWord, stagger, timeOf, useOfficer, useToast,
} from './ui'

// ---- Progress through the stay ----------------------------------------------------------------

export const STAGES = ['Admission', 'Pre-auth', 'Treatment', 'Discharge', 'Settlement']

export function stageIndex(claim) {
  const stages = new Set([...(claim.thread ?? []).map((m) => m.stage), claim.pending_draft?.message?.stage].filter(Boolean))
  if (claim.state === 'CLOSED') return STAGES.length
  if (['FINAL_APPROVED', 'SETTLED'].includes(claim.state)) return 4
  if (stages.has('discharge') || claim.state === 'DISCHARGE_DRAFT') return 3
  if (stages.has('enhancement') || claim.state === 'IN_TREATMENT') return 2
  if (stages.size) return 1
  return 0
}

export function StageProgress({ claim }) {
  const current = stageIndex(claim)
  return (
    <ol className="flex items-center">
      {STAGES.map((stage, i) => {
        const done = i < current
        const active = i === current
        return (
          <li key={stage} className="flex flex-1 items-center last:flex-none">
            <div className="flex items-center gap-2.5">
              <span className={`grid h-8 w-8 shrink-0 place-items-center rounded-full text-xs font-bold transition-all duration-500 ${
                done ? 'bg-accent-600 text-white shadow-glow' : active ? 'bg-white text-accent-700 ring-2 ring-accent-500 animate-ring' : 'bg-white text-ink-faint ring-1 ring-line'
              }`}>
                {done ? <Icon name="check" className="h-4 w-4" /> : i + 1}
              </span>
              <span className={`whitespace-nowrap text-[13px] font-semibold ${active ? 'text-accent-700' : done ? 'text-ink-soft' : 'text-ink-faint'}`}>{stage}</span>
            </div>
            {i < STAGES.length - 1 && (
              <span className="mx-3 h-1 flex-1 overflow-hidden rounded-full bg-line">
                <span className={`block h-full origin-left rounded-full bg-accent-500 transition-transform duration-700 ${done ? 'scale-x-100' : 'scale-x-0'}`} />
              </span>
            )}
          </li>
        )
      })}
    </ol>
  )
}

// ---- The journey: what happened, in plain words ---------------------------------------------------

const STEP_TONES = {
  neutral: 'bg-[#eef1ef] text-ink-soft', good: 'bg-accent-50 text-accent-700', attention: 'bg-amber-100/70 text-amber-700',
  bad: 'bg-rose-100/70 text-rose-700', info: 'bg-sky-100/70 text-sky-700', you: 'bg-accent-600 text-white shadow-glow',
}

export function Journey({ claim }) {
  const steps = journey(claim)
  return (
    <ol className="relative">
      {steps.map((step, i) => (
        <li key={step.id} className="relative flex animate-fade-up gap-4 pb-6 last:pb-0" style={stagger(i, 25)}>
          {i < steps.length - 1 && <span className="absolute left-[15px] top-9 h-[calc(100%-2.25rem)] w-px bg-line" />}
          <span className={`relative z-10 grid h-8 w-8 shrink-0 place-items-center rounded-full ${STEP_TONES[step.tone]}`}><Icon name={step.icon} className="h-4 w-4" /></span>
          <div className="min-w-0 flex-1 pt-1">
            <div className="flex items-baseline justify-between gap-3">
              <p className="text-sm font-medium">{step.title}</p>
              <span className="shrink-0 text-xs text-ink-faint tabular">{timeOf(step.time)}</span>
            </div>
            {step.detail && <p className="mt-0.5 text-[13px] leading-relaxed text-ink-muted">{step.detail}</p>}
          </div>
        </li>
      ))}
      {claim.busy && (
        <li className="relative flex animate-fade-in gap-4">
          <span className="relative z-10 grid h-8 w-8 shrink-0 place-items-center rounded-full bg-accent-50 text-accent-700 animate-ring"><Spinner className="h-3.5 w-3.5" /></span>
          <p className="pt-1.5 text-sm text-ink-muted">{workingOn(claim)}…</p>
        </li>
      )}
    </ol>
  )
}

export function workingOn(claim) {
  const last = [...(claim.timeline ?? [])].reverse().find((e) => e.action === 'agent_output' || e.action === 'message_sent')
  if (!last) return 'Reading the documents'
  if (last.action === 'message_sent' && last.actor === 'hospital') return `Waiting for ${claim.insurer}`
  const next = last.output?.output?.next
  const NEXT = {
    clinical_coder: 'Coding the diagnosis', policy_and_cost: 'Checking the policy and estimating the cost', preauth: 'Writing the pre-auth',
    tpa_adjudicator: `${claim.insurer} is deciding`, reply: `${claim.insurer} is replying`, tpa_query_writer: `${claim.insurer} is writing a query`,
  }
  return NEXT[next] ?? 'Agents are working'
}

// ---- What to do now ---------------------------------------------------------------------------

export function ActionCard({ claim, onDone }) {
  const s = situation(claim)
  const Body = {
    working: Working, preauth: ReviewPreauth, appeal: ReviewAppeal, reply: ReviewReply, enhancement: ReviewEnhancement,
    discharge: ReviewDischarge, denied: DecideDenial, stuck: DecideDenial, held: Held, insurer: Working, treatment: Treatment,
    payment: Payment, closed: Closed, closed_denied: Closed,
  }[s.kind] ?? Working
  const needsYou = Boolean(s.task)
  return (
    <Card className={`overflow-hidden transition-shadow ${needsYou ? 'shadow-lift ring-1 ring-amber-200' : ''}`}>
      {needsYou && (
        <div className="flex items-center gap-2 border-b border-amber-100 bg-amber-50/70 px-5 py-2 text-xs font-medium text-amber-800">
          <span className="h-1.5 w-1.5 animate-breathe rounded-full bg-amber-500" />Your turn · {s.task}
        </div>
      )}
      <div key={`${s.kind}-${claim.pending_draft?.message?.round ?? ''}`} className="animate-fade-up p-5">
        <Body claim={claim} s={s} onDone={onDone} />
      </div>
    </Card>
  )
}

function Title({ children, sub }) {
  return (
    <div className="mb-4">
      <h3 className="text-[17px] font-semibold tracking-tight">{children}</h3>
      {sub && <p className="mt-1.5 text-sm leading-relaxed text-ink-muted">{sub}</p>}
    </div>
  )
}

function Working({ claim }) {
  const waiting = ['SUBMITTED', 'QUERY_RAISED'].includes(claim.state)
  return (
    <div className="flex items-center gap-4">
      <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-accent-50 text-accent-700 animate-ring"><Spinner className="h-4 w-4" /></span>
      <div>
        <p className="font-medium">{waiting ? `With ${claim.insurer}` : workingOn(claim)}</p>
        <p className="text-sm text-ink-muted">{waiting ? 'Their agents are reviewing it. This page updates by itself.' : 'You’ll be asked before anything is sent.'}</p>
      </div>
    </div>
  )
}

// The officer's three choices on a drafted message, with edit and hold-back forms.
function Decide({ claim, onDone, send = 'Send', hold = 'Hold back', accept, editAmount = false }) {
  const [officer] = useOfficer()
  const toast = useToast()
  const draft = claim.pending_draft?.message
  const [mode, setMode] = useState(null)
  const [amount, setAmount] = useState(draft?.requested_amount ?? '')
  const initialText = draft?.answer ?? draft?.argument ?? draft?.package?.justification ?? ''
  const [text, setText] = useState(initialText)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState(null)

  const decide = async (decision, note) => {
    setBusy(decision.decision)
    setError(null)
    try {
      await api.decide(claim.claim_id, { officer, draft_hash: claim.pending_draft?.hash ?? null, ...decision })
      toast(note)
      setMode(null)
      onDone?.()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(null)
    }
  }
  const sentNote = { title: `Sent to ${claim.insurer}`, body: 'Their agents are reviewing it now.', icon: 'send' }

  if (mode === 'edit') {
    const edits = {}
    if (editAmount && amount !== '' && Number(amount) !== draft.requested_amount) edits.requested_amount = Number(amount)
    if (text.trim() && text.trim() !== initialText.trim()) edits.justification = text.trim()
    return (
      <div className="animate-fade-in space-y-3">
        {editAmount && (
          <label className="block text-[13px] font-medium text-ink-soft">Amount to ask for
            <div className="relative mt-1.5"><span className="absolute left-3 top-1/2 -translate-y-1/2 text-sm text-ink-muted">₹</span>
              <input type="number" min="0" value={amount} onChange={(e) => setAmount(e.target.value)} className="input pl-7 tabular" /></div>
          </label>
        )}
        {initialText && (
          <label className="block text-[13px] font-medium text-ink-soft">What the insurer will read
            <textarea rows={7} value={text} onChange={(e) => setText(e.target.value)} className="input mt-1.5 leading-relaxed" />
          </label>
        )}
        <div className="flex gap-2">
          <Button variant="primary" icon="send" loading={busy === 'edit'} disabled={!Object.keys(edits).length}
            onClick={() => decide({ decision: 'edit', edits }, sentNote)}>Send with changes</Button>
          <Button variant="ghost" onClick={() => setMode(null)}>Cancel</Button>
        </div>
        <ErrorNote error={error} />
      </div>
    )
  }
  if (mode === 'hold') {
    return (
      <div className="animate-fade-in space-y-3">
        <label className="block text-[13px] font-medium text-ink-soft">Why hold it back? <span className="font-normal text-ink-muted">Saved on the claim's record.</span>
          <input autoFocus value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Get the X-ray report from radiology first" className="input mt-1.5" />
        </label>
        <div className="flex gap-2">
          <Button variant="danger" loading={busy === 'reject'} disabled={!reason.trim()}
            onClick={() => decide({ decision: 'reject', reason: reason.trim() }, { title: 'Held back', body: 'Nothing was sent.', icon: 'undo', tone: 'attention' })}>{hold}</Button>
          <Button variant="ghost" onClick={() => setMode(null)}>Cancel</Button>
        </div>
        <ErrorNote error={error} />
      </div>
    )
  }
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        {draft && <Button variant="primary" size="lg" icon="send" loading={busy === 'approve'} onClick={() => decide({ decision: 'approve' }, sentNote)}>{send}</Button>}
        {draft && <Button size="lg" onClick={() => setMode('edit')}>Edit first</Button>}
        {accept && <Button size="lg" loading={busy === 'accept'} onClick={() => decide({ decision: 'accept' }, { title: 'Decision accepted', icon: 'check' })}>{accept}</Button>}
        {claim.actions?.includes('reject') && <Button variant="ghost" size="lg" onClick={() => setMode('hold')}>{hold}</Button>}
      </div>
      <p className="flex items-center gap-1.5 text-xs text-ink-muted"><Icon name="shield" className="h-3.5 w-3.5" />Signed as {officer}. Nothing reaches the insurer without you.</p>
      {claim.pending_draft && <p className="text-xs text-ink-faint">Approving draft v{claim.pending_draft.version} · {claim.pending_draft.hash?.slice(0, 8)}. If it changes, you&apos;ll be asked again.</p>}
      <ErrorNote error={error} />
    </div>
  )
}

const DOC_TYPES = ['admission_note', 'lab_report', 'xray_report', 'prescription', 'operative_note', 'discharge_summary', 'final_bill']

// Open findings with what to do about each, a way to attach a record, and the result of the last recheck.
function FixAndRecheck({ claim, onDone }) {
  const [officer] = useOfficer()
  const toast = useToast()
  const tasks = claim.tasks ?? []
  const [docType, setDocType] = useState(tasks.find((t) => t.doc_type)?.doc_type ?? 'lab_report')
  const [file, setFile] = useState(null)
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState(null)
  const canRecheck = claim.actions?.includes('recheck')
  if (!tasks.length && !claim.last_recheck && !canRecheck) return null

  const attachAndRecheck = async () => {
    setBusy('attach')
    setError(null)
    try {
      const content = await fileToBase64(file)
      const result = await api.attach(claim.claim_id, { type: docType, filename: file.name, content_base64: content, officer })
      if (result.duplicate) toast({ title: 'Already attached', body: `${result.document.doc_id} is the same file`, icon: 'file' })
      if (canRecheck) await api.recheck(claim.claim_id, `${result.document.doc_id} attached`)
      toast({ title: 'Rechecking', body: 'The agents are re-reading the file and drafting a new version for you', icon: 'sparkle' })
      setFile(null)
      onDone?.()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(null)
    }
  }
  const last = claim.last_recheck
  return (
    <div className="mb-5 space-y-3 rounded-lg border border-line p-3">
      {tasks.length > 0 && (
        <div>
          <p className="text-[13px] font-medium text-ink-soft">{claim.pending_draft ? 'To fix before sending' : 'What you can do'}</p>
          <ul className="mt-1.5 space-y-1">
            {tasks.map((t, i) => <li key={i} className="text-[13px] text-ink-muted"><span className="font-medium text-ink">{t.issue}.</span> {t.fix}</li>)}
          </ul>
        </div>
      )}
      {last && (
        <p className="text-[13px] text-ink-muted">
          Last recheck: draft v{last.draft_after} replaced v{last.draft_before}.
          {last.resolved?.length > 0 && <span className="text-accent-700"> Resolved: {last.resolved.join(', ')}.</span>}
          {last.remaining?.length > 0 && <span> Still open: {last.remaining.join(', ')}.</span>}
          {last.new?.length > 0 && <span className="text-rose-700"> New: {last.new.join(', ')}.</span>}
        </p>
      )}
      {claim.actions?.includes('attach') && (
        <div className="flex flex-wrap items-center gap-2">
          <select value={docType} onChange={(e) => setDocType(e.target.value)} className="input w-auto">
            {DOC_TYPES.map((t) => <option key={t} value={t}>{label(t)}</option>)}
          </select>
          <input type="file" accept="application/pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)} className="text-[13px]" />
          <Button icon="file" loading={busy === 'attach'} disabled={!file} onClick={attachAndRecheck}>{canRecheck ? 'Attach and recheck' : 'Attach'}</Button>
        </div>
      )}
      <ErrorNote error={error} />
    </div>
  )
}

// The exact passages behind an evidence verdict, each linked to its document page.
function EvidenceList({ claim, evidence }) {
  if (!evidence) return null
  return (
    <div className="mb-4 rounded-lg bg-canvas p-3">
      <p className="text-[13px] font-medium text-ink-soft">Evidence check: {evidence.verdict}</p>
      <p className="mt-0.5 text-[13px] text-ink-muted">{evidence.reason}</p>
      <ul className="mt-2 space-y-1">
        {evidence.passages.filter((p) => p.kind !== 'against').slice(0, 6).map((p, i) => (
          <li key={i} className="text-[13px] text-ink-soft">
            <a className="font-medium underline decoration-line underline-offset-2" href={documentUrl(claim.claim_id, p.doc_id, p.page)} target="_blank" rel="noreferrer">{p.doc_id} p.{p.page}</a>: “{p.text}” <span className="text-ink-faint">({p.finding})</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function Fact({ label: name, value, tone }) {
  return (
    <div>
      <p className="text-xs text-ink-muted">{name}</p>
      <p className={`mt-0.5 text-[15px] font-semibold tabular ${tone === 'good' ? 'text-accent-700' : tone === 'bad' ? 'text-rose-700' : ''}`}>{value}</p>
    </div>
  )
}

function ReviewPreauth({ claim, onDone }) {
  const draft = claim.pending_draft.message
  const risk = claim.agents?.denial_risk?.data
  const policy = claim.agents?.policy?.data?.assessment
  return (
    <>
      <Title sub={`The agents read the file, checked ${claim.insurer}'s policy and priced the stay. Review the request, then send it.`}>
        Send the pre-auth to {claim.insurer}?
      </Title>
      <div className="mb-5 grid grid-cols-3 gap-4 rounded-lg bg-canvas p-4">
        <Fact label="Asking for" value={money(draft.requested_amount)} />
        <Fact label="Policy should pay" value={money(policy?.payable)} />
        <Fact label="Patient pays" value={money(policy?.patient_share)} />
      </div>
      <RiskNotes risk={risk} />
      <FixAndRecheck claim={claim} onDone={onDone} />
      <Decide claim={claim} onDone={onDone} send="Send to insurer" editAmount />
    </>
  )
}

function RiskNotes({ risk }) {
  if (!risk) return null
  if (!risk.items?.length) {
    return <p className="mb-5 flex items-center gap-2 text-sm text-accent-700"><Icon name="check" className="h-4 w-4" />Risk check passed: nothing here is likely to be cut.</p>
  }
  return (
    <div className="mb-5 space-y-2">
      <p className="text-[13px] font-medium text-ink-soft">The risk checker found {risk.items.length} thing{risk.items.length > 1 ? 's' : ''} the insurer may cut:</p>
      {risk.items.map((item) => (
        <div key={item.flag} className="rounded-lg border border-amber-200/70 bg-amber-50/40 p-3">
          <p className="text-sm font-medium">{item.issue}</p>
          <p className="mt-0.5 text-[13px] text-ink-muted">{item.evidence.split('; model weight')[0]}</p>
          <p className="mt-1.5 flex gap-1.5 text-[13px] text-amber-800"><Icon name="sparkle" className="mt-0.5 h-3.5 w-3.5 shrink-0" />{item.fix}</p>
        </div>
      ))}
    </div>
  )
}

function ReviewAppeal({ claim, s, onDone }) {
  const appeal = claim.agents?.appeal?.data
  const cut = s.lastInsurer
  const cutTotal = claim.totals?.deducted_by_message?.[(claim.thread ?? []).indexOf(cut)]
  return (
    <>
      <Title sub={`${claim.insurer} approved ${money(cut?.approved_amount)} and cut ${money(cutTotal)}. The appeal writer checked each cut against the policy.`}>
        Appeal the cut?
      </Title>
      {appeal?.contested?.length > 0 && (
        <div className="mb-4 space-y-2">
          {appeal.contested.map((c) => (
            <div key={c.clause} className="flex items-start justify-between gap-4 rounded-lg border border-line p-3">
              <div>
                <p className="text-sm font-medium">{c.clause}</p>
                <p className="mt-0.5 text-[13px] text-ink-muted">The insurer cut {money(c.insurer_cut)}; the policy allows {money(c.policy_cut)}.</p>
              </div>
              <Badge tone="good">Recover {money(c.disputed)}</Badge>
            </div>
          ))}
        </div>
      )}
      <EvidenceList claim={claim} evidence={appeal?.evidence} />
      <details className="group mb-5 rounded-lg bg-canvas">
        <summary className="flex cursor-pointer list-none items-center justify-between px-4 py-3 text-sm font-medium">
          Read the appeal <Icon name="chevronDown" className="h-4 w-4 text-ink-muted transition-transform group-open:rotate-180" />
        </summary>
        <p className="whitespace-pre-line px-4 pb-4 text-[13px] leading-relaxed text-ink-soft">{s.draft.argument}</p>
      </details>
      <Decide claim={claim} onDone={onDone} send="Send appeal" accept="Accept the cut" hold="Hold back" />
    </>
  )
}

function ReviewReply({ claim, s, onDone }) {
  const missing = claim.agents?.query_responder?.output?.decision === 'document_missing'
  return (
    <>
      <Title sub={`${s.lastInsurer?.reason ?? ''}`}>{claim.insurer} asked: {s.lastInsurer?.missing.replace('Please send: ', '')}</Title>
      {missing ? (
        <div className="mb-5 flex gap-2 rounded-lg bg-amber-50 p-3 text-sm text-amber-900">
          <Icon name="alert" className="mt-0.5 h-4 w-4 shrink-0" />
          <span>That document isn&apos;t in the patient&apos;s file. Hold back while you get it, or send the reply saying it&apos;s on its way. Each reply uses one of the three rounds.</span>
        </div>
      ) : (
        <p className="mb-5 flex items-center gap-2 text-sm text-accent-700"><Icon name="check" className="h-4 w-4" />Found in the file and attached: {s.draft.documents.join(', ')}</p>
      )}
      <blockquote className="mb-5 border-l-2 border-line pl-3 text-[13px] text-ink-soft">{s.draft.answer}</blockquote>
      <FixAndRecheck claim={claim} onDone={onDone} />
      <Decide claim={claim} onDone={onDone} send="Send reply" />
    </>
  )
}

function ReviewEnhancement({ claim, s, onDone }) {
  return (
    <>
      <Title sub={`The running bill has grown past what ${claim.insurer} approved. Ask them to raise the approval so the patient isn't left with the difference.`}>
        Ask for more funds?
      </Title>
      <div className="mb-5 grid grid-cols-2 gap-4 rounded-lg bg-canvas p-4">
        <Fact label="Approved so far" value={money(claim.approved)} />
        <Fact label="Ask to raise it to" value={money(s.draft.requested_amount)} />
      </div>
      <Decide claim={claim} onDone={onDone} send={`Request ${money(s.draft.requested_amount)}`} hold="Not now" editAmount />
    </>
  )
}

function ReviewDischarge({ claim, s, onDone }) {
  return (
    <>
      <Title sub="The discharge writer put together the final bill, discharge summary and expected deductions. Send it for final approval.">Send the final claim?</Title>
      <div className="mb-5 grid grid-cols-2 gap-4 rounded-lg bg-canvas p-4">
        <Fact label="Final claim" value={money(s.draft.requested_amount)} />
        <Fact label="Approved so far" value={money(claim.approved)} />
      </div>
      <FixAndRecheck claim={claim} onDone={onDone} />
      <Decide claim={claim} onDone={onDone} send="Send final claim" editAmount />
    </>
  )
}

function DecideDenial({ claim, s, onDone }) {
  const m = s.lastInsurer
  const reason = m?.reason ?? m?.deductions?.[0]?.item?.replace('Whole claim: ', '')
  const rounds = m?.type === 'FINAL'
  const heldBack = claim.agents?.appeal?.data?.held_back?.length > 0 && m?.type === 'PARTIAL_APPROVE'
  const sub = rounds ? 'All three rounds are used, so the agents can’t send anything more. It’s your call.'
    : heldBack ? 'The records don’t show enough to argue this cut, so the agents didn’t appeal. Attach a record that shows it and recheck, or accept the decision.'
      : 'The agents don’t appeal a whole-claim denial on their own. It’s your call.'
  return (
    <>
      <Title sub={sub}>
        {claim.insurer} {m?.decision === 'PARTIAL_APPROVE' || m?.type === 'PARTIAL_APPROVE' ? 'cut the claim' : 'denied the claim'}
      </Title>
      {reason && <div className="mb-5 rounded-lg border border-rose-200/70 bg-rose-50/40 p-3 text-sm text-rose-900">{reason}</div>}
      <EvidenceList claim={claim} evidence={claim.agents?.appeal?.data?.held_back?.length ? claim.agents.appeal.data.evidence : null} />
      <FixAndRecheck claim={claim} onDone={onDone} />
      <p className="mb-4 text-sm text-ink-muted">Accept it and the patient pays what isn&apos;t covered{claim.actions?.includes('reject') ? ', or hold it back to rework the claim' : ''}.</p>
      <Decide claim={claim} onDone={onDone} accept="Accept decision" />
    </>
  )
}

function Held({ claim, onDone }) {
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const reason = [...(claim.timeline ?? [])].reverse().find((e) => e.action === 'human_decision' && e.output.decision === 'reject')?.output.reason
  const redraft = async () => {
    setBusy(true)
    try {
      await api.redraft(claim.claim_id)
      toast({ title: 'Preparing the pre-auth again', icon: 'sparkle' })
      onDone?.()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      <Title sub={reason ? `You held it back: “${reason}”. When that's sorted, the agents can prepare a fresh request.` : 'When it’s ready, the agents can prepare a fresh request.'}>Pre-auth on hold</Title>
      <Button variant="primary" size="lg" icon="sparkle" loading={busy} onClick={redraft}>Prepare it again</Button>
      <ErrorNote error={error} />
    </>
  )
}

function Treatment({ claim, onDone }) {
  const toast = useToast()
  const [adding, setAdding] = useState(false)
  const [description, setDescription] = useState('ICU stay, 1 day')
  const [category, setCategory] = useState('icu')
  const [amount, setAmount] = useState('12000')
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState(null)
  const run = async (key, work, note) => {
    setBusy(key)
    setError(null)
    try {
      await work()
      toast(note)
      setAdding(false)
      onDone?.()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(null)
    }
  }
  const billed = claim.totals?.billed
  const addLine = () => api.addBill(claim.claim_id, [{
    line_id: `${claim.admission_id}-X${Date.now() % 1000000}`, category, description, quantity: 1, amount: Number(amount),
  }])
  return (
    <>
      <Title sub={`Charges go on the running bill. If it outgrows the approval, the bill watcher drafts a request for more.`}>{claim.patient} is in treatment</Title>
      <div className="mb-5 grid grid-cols-2 gap-4 rounded-lg bg-canvas p-4">
        <Fact label="Approved" value={money(claim.approved)} tone="good" />
        <Fact label="Bill so far" value={money(billed)} />
      </div>
      {adding ? (
        <div className="mb-4 animate-fade-in space-y-2 rounded-lg border border-line p-3">
          <input value={description} onChange={(e) => setDescription(e.target.value)} className="input" placeholder="What was it for?" />
          <div className="grid grid-cols-2 gap-2">
            <select value={category} onChange={(e) => setCategory(e.target.value)} className="input">
              {['room', 'icu', 'doctor_fees', 'nursing', 'ot_charges', 'implants', 'pharmacy', 'investigations', 'consumables', 'other'].map((c) => <option key={c} value={c}>{label(c)}</option>)}
            </select>
            <div className="relative"><span className="absolute left-3 top-1/2 -translate-y-1/2 text-sm text-ink-muted">₹</span>
              <input type="number" min="0" value={amount} onChange={(e) => setAmount(e.target.value)} className="input pl-7 tabular" /></div>
          </div>
          <div className="flex gap-2">
            <Button variant="primary" loading={busy === 'add'} onClick={() => run('add', addLine, { title: 'Charge added', body: 'The bill watcher is checking it against the approval.', icon: 'rupee' })}>Add charge</Button>
            <Button variant="ghost" onClick={() => setAdding(false)}>Cancel</Button>
          </div>
        </div>
      ) : null}
      <div className="flex flex-wrap gap-2">
        {!adding && <Button size="lg" icon="plus" onClick={() => setAdding(true)}>Add a charge</Button>}
        <Button variant="primary" size="lg" icon="bed" loading={busy === 'discharge'}
          onClick={() => run('discharge', () => api.discharge(claim.claim_id), { title: 'Discharge started', body: 'The final claim is being prepared.', icon: 'file' })}>Discharge patient</Button>
      </div>
      <ErrorNote error={error} />
    </>
  )
}

function Payment({ claim, onDone }) {
  const toast = useToast()
  const [paid, setPaid] = useState(String(claim.approved ?? 0))
  const [reference, setReference] = useState(`UTR${Date.now() % 100000000}`)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const pay = async () => {
    setBusy(true)
    setError(null)
    try {
      await api.pay(claim.claim_id, Number(paid), reference)
      toast({ title: 'Payment recorded', body: 'The reconciler is checking it against the bill.', icon: 'rupee' })
      onDone?.()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      <Title sub={`${claim.insurer} gave final approval for ${money(claim.approved)}. Enter what they actually paid; the reconciler flags any gap worth chasing.`}>Record the payment</Title>
      <div className="mb-4 grid grid-cols-2 gap-3">
        <label className="block text-[13px] font-medium text-ink-soft">Amount received
          <div className="relative mt-1.5"><span className="absolute left-3 top-1/2 -translate-y-1/2 text-sm text-ink-muted">₹</span>
            <input type="number" min="0" value={paid} onChange={(e) => setPaid(e.target.value)} className="input pl-7 tabular" /></div>
        </label>
        <label className="block text-[13px] font-medium text-ink-soft">UTR / reference
          <input value={reference} onChange={(e) => setReference(e.target.value)} className="input mt-1.5" />
        </label>
      </div>
      <Button variant="primary" size="lg" icon="rupee" loading={busy} onClick={pay}>Record payment</Button>
      <ErrorNote error={error} />
    </>
  )
}

function Closed({ claim }) {
  const recon = claim.agents?.reconciliation
  const denied = claim.state === 'DENIED'
  return (
    <div className="flex items-start gap-4">
      <span className={`grid h-10 w-10 shrink-0 place-items-center rounded-full ${denied ? 'bg-rose-100/70 text-rose-700' : 'bg-accent-50 text-accent-700'}`}><Icon name={denied ? 'x' : 'check'} className="h-5 w-5" /></span>
      <div>
        <p className="font-medium">{denied ? 'Claim denied' : 'Claim closed'}</p>
        <p className="mt-0.5 text-sm text-ink-muted">{denied ? 'The patient pays the bill. The record stays on file.' : recon?.output?.findings?.[0]?.evidence ?? claim.last_reason}</p>
      </div>
    </div>
  )
}

// ---- Risk, in plain words ---------------------------------------------------------------------

export function RiskSummary({ risk }) {
  if (!risk) return <p className="text-sm text-ink-muted">Not checked yet.</p>
  const p = risk.probabilities ?? {}
  const parts = [['Paid in full', p.APPROVED, 'bg-accent-500'], ['Cut', p.PARTIALLY_APPROVED, 'bg-amber-400'], ['Denied', p.DENIED, 'bg-rose-500']]
  return (
    <div>
      <div className="flex items-baseline justify-between">
        <p className="text-sm text-ink-muted">Chance it&apos;s not paid in full</p>
        <Badge tone={riskTone(risk.risk)}>{riskWord(risk.risk)}</Badge>
      </div>
      <p className="mt-1 text-2xl font-semibold tracking-tight tabular">{pct(risk.risk)}</p>
      <div className="mt-3 flex h-1.5 gap-0.5 overflow-hidden rounded-full">
        {parts.map(([name, value, colour]) => <div key={name} className={`${colour} transition-all duration-700`} style={{ width: `${(value ?? 0) * 100}%` }} />)}
      </div>
      <div className="mt-2.5 space-y-1">
        {parts.map(([name, value, colour]) => (
          <div key={name} className="flex items-center justify-between text-[13px]">
            <span className="flex items-center gap-2 text-ink-muted"><span className={`h-2 w-2 rounded-sm ${colour}`} />{name}</span>
            <span className="tabular">{pct(value)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ---- Insurer thread ---------------------------------------------------------------------------

const KIND = { SUBMIT: 'Request', RESPONSE: 'Reply', APPEAL: 'Appeal', APPROVE: 'Approved', PARTIAL_APPROVE: 'Part approved', DENY: 'Denied', QUERY: 'Question', FINAL: 'Final decision' }
const KIND_TONE = { APPROVE: 'good', PARTIAL_APPROVE: 'attention', DENY: 'bad', QUERY: 'info', FINAL: 'bad' }

export function Thread({ claim, side = 'hospital' }) {
  let lastStage = null
  return (
    <div className="space-y-4">
      {claim.thread.map((m, i) => {
        const header = m.stage !== lastStage
        lastStage = m.stage
        return (
          <div key={i} className="animate-fade-up" style={stagger(i)}>
            {header && (
              <div className="my-5 flex items-center gap-3 text-xs font-medium text-ink-muted first:mt-0">
                <span className="h-px flex-1 bg-line" />{m.stage === 'preauth' ? 'Pre-auth' : label(m.stage)}<span className="h-px flex-1 bg-line" />
              </div>
            )}
            <Bubble message={m} claim={claim} mine={side === 'hospital' ? m.sender === 'hospital' : m.sender === 'insurer'} />
          </div>
        )
      })}
    </div>
  )
}

function Bubble({ message: m, claim, mine }) {
  const insurer = m.sender === 'insurer'
  return (
    <div className={`flex gap-3 ${mine ? 'flex-row-reverse' : ''}`}>
      <Avatar name={insurer ? claim.tpa_id : 'Hospital Billing'} size="sm" />
      <div className={`max-w-[80%] rounded-2xl px-4 py-3 ${mine ? 'rounded-tr-md bg-accent-700 text-white' : 'rounded-tl-md border border-line bg-white'}`}>
        <div className="flex flex-wrap items-center gap-2">
          <span className={`text-[13px] font-semibold ${mine ? 'text-white' : ''}`}>{insurer ? claim.insurer : 'Hospital billing'}</span>
          {mine ? <span className="rounded bg-white/15 px-1.5 py-0.5 text-[11px] font-medium">{KIND[m.type]}</span> : <Badge tone={KIND_TONE[m.type] ?? 'neutral'}>{KIND[m.type]}</Badge>}
          <span className={`text-[11px] ${mine ? 'text-white/60' : 'text-ink-faint'}`}>Round {m.round}</span>
        </div>
        <MessageBody m={m} dark={mine} />
      </div>
    </div>
  )
}

function MessageBody({ m, dark }) {
  const sub = dark ? 'text-white/70' : 'text-ink-muted'
  const text = m.package?.preauth?.justification ?? m.package?.justification ?? m.answer ?? m.argument
  return (
    <div className="mt-2 space-y-2 text-[13px] leading-relaxed">
      {m.requested_amount !== undefined && <p>Asks for <b className="font-semibold tabular">{money(m.requested_amount)}</b></p>}
      {m.approved_amount !== undefined && m.type !== 'DENY' && <p>Approves <b className="font-semibold tabular">{money(m.approved_amount)}</b>{m.type === 'FINAL' ? ` · ${label(m.decision).toLowerCase()}` : ''}</p>}
      {m.missing && <p><b className="font-semibold">{m.missing.replace('Please send: ', 'Please send ')}</b><span className={`block ${sub}`}>{m.reason}</span></p>}
      {m.type === 'DENY' && <p><b className="font-semibold">Denied:</b> {m.reason} <span className={sub}>({m.clause})</span></p>}
      {text && <p className={`line-clamp-6 whitespace-pre-line ${sub}`}>{text}</p>}
      {m.deductions?.length > 0 && <Deductions deductions={m.deductions} dark={dark} />}
      {(m.documents?.length > 0 || m.cited_documents?.length > 0) && (
        <p className={`flex flex-wrap gap-1 ${sub}`}>
          {[...(m.documents ?? []), ...(m.cited_documents ?? [])].map((d) => (
            <span key={d} className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] ${dark ? 'bg-white/10' : 'bg-[#eef1ef]'}`}><Icon name="file" className="h-3 w-3" />{d}</span>
          ))}
        </p>
      )}
    </div>
  )
}

export function Deductions({ deductions, dark }) {
  return (
    <div className={`overflow-hidden rounded-lg ${dark ? 'bg-white/5' : 'border border-line'}`}>
      {deductions.map((d, i) => (
        <div key={i} className={`flex items-start justify-between gap-4 px-3 py-2 text-[13px] ${i ? (dark ? 'border-t border-white/10' : 'border-t border-line') : ''}`}>
          <div className="min-w-0">
            <p className="truncate">{d.item.split(':')[0]}</p>
            <p className={`text-[11px] ${dark ? 'text-white/60' : 'text-ink-muted'}`}>Clause {d.clause ?? d.clause_id}</p>
          </div>
          <span className="shrink-0 font-medium tabular">−{money(d.amount)}</span>
        </div>
      ))}
    </div>
  )
}

// ---- Agent log: the technical trace, for anyone who wants every step ------------------------------

const QUIET = new Set(['case_file', 'llm_call', 'draft_message', 'open_claim'])

export function AgentLog({ events }) {
  const [open, setOpen] = useState(null)
  const shown = events.filter((e) => !QUIET.has(e.action))
  return (
    <Card>
      <ol className="divide-y divide-line">
        {shown.map((e, i) => {
          const agent = e.action === 'agent_output' ? e.output.output : null
          const insurer = e.actor.startsWith('tpa_') || e.actor === 'insurer'
          return (
            <li key={e.event_id} className="animate-fade-in" style={stagger(i, 10)}>
              <button disabled={!agent} onClick={() => setOpen(open === e.event_id ? null : e.event_id)}
                className="flex w-full items-center gap-3 px-5 py-3 text-left enabled:hover:bg-canvas">
                <span className="w-12 shrink-0 text-xs text-ink-faint tabular">{timeOf(e.created_at)}</span>
                <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${agent ? (insurer ? 'bg-sky-500' : 'bg-accent-500') : e.action === 'human_decision' ? 'bg-amber-500' : 'bg-zinc-300'}`} />
                <span className="min-w-0 flex-1 truncate text-[13px]">
                  {agent ? <><b className="font-medium">{agentName(agent.agent)}</b>{insurer && <span className="text-sky-700"> · insurer</span>} <span className="text-ink-muted">→ {label(agent.decision).toLowerCase()}</span></> : <span className="text-ink-muted">{e.summary}</span>}
                </span>
                {agent && <span className="shrink-0 text-xs text-ink-faint tabular">{agent.confidence.toFixed(2)}</span>}
                {agent && <Icon name="chevronDown" className={`h-4 w-4 shrink-0 text-ink-faint transition-transform ${open === e.event_id ? 'rotate-180' : ''}`} />}
              </button>
              {agent && open === e.event_id && (
                <ul className="animate-fade-in space-y-2 bg-canvas/70 px-5 pb-4 pl-[5.5rem] pt-2">
                  {agent.findings.map((f, j) => (
                    <li key={j} className="text-[13px] leading-relaxed">
                      <span className="font-medium">{f.issue}</span><span className="text-ink-muted"> — {f.evidence}</span>
                      <span className="mt-0.5 block text-[11px] text-ink-faint">Source: {f.source}</span>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          )
        })}
      </ol>
    </Card>
  )
}
