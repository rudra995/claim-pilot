// Turns a claim's audit trail and insurer thread into plain language: what happened, and what to do now.
import { agentName, label, money } from './components/ui'

const insurerOf = (claim) => claim.insurer ?? 'the insurer'

// ---- What to do now ---------------------------------------------------------------------------

// One situation per claim, so every screen agrees on the next step.
export function situation(claim) {
  const draft = claim.pending_draft?.message
  const lastInsurer = [...(claim.thread ?? [])].reverse().find((m) => m.sender === 'insurer')
  if (claim.busy) return { kind: 'working', task: null }
  if (claim.state === 'AWAITING_HUMAN') {
    if (draft?.type === 'APPEAL') return { kind: 'appeal', task: 'Review appeal', draft, lastInsurer }
    if (draft?.type === 'RESPONSE') return { kind: 'reply', task: 'Answer insurer query', draft, lastInsurer }
    if (draft?.stage === 'enhancement') return { kind: 'enhancement', task: 'Approve more funds', draft }
    if (draft?.stage === 'discharge') return { kind: 'discharge', task: 'Send final claim', draft }
    if (draft) return { kind: 'preauth', task: 'Review pre-auth', draft }
    if (lastInsurer && lastInsurer.type !== 'QUERY') return { kind: 'denied', task: 'Decide on denial', lastInsurer }
    return { kind: 'stuck', task: 'Check claim' }
  }
  if (claim.state === 'PREAUTH_DRAFT' && claim.actions?.includes('redraft')) return { kind: 'held', task: null }
  if (['SUBMITTED', 'QUERY_RAISED'].includes(claim.state)) return { kind: 'insurer', task: null }
  if (claim.state === 'IN_TREATMENT') return { kind: 'treatment', task: null }
  if (claim.state === 'FINAL_APPROVED') return { kind: 'payment', task: 'Record payment' }
  if (claim.state === 'CLOSED') return { kind: 'closed', task: null }
  if (claim.state === 'DENIED') return { kind: 'closed_denied', task: null }
  return { kind: 'working', task: null }
}

// The next step in a few words, for lists.
export function nextStep(claim) {
  if (claim.busy) return 'Agents are working'
  const reason = claim.last_reason ?? ''
  switch (claim.state) {
    case 'AWAITING_HUMAN':
      if (/not appealed|already refused/i.test(reason)) return 'Decide on the cut'
      if (/appeal/i.test(reason)) return 'Review the appeal'
      if (/Enhancement/i.test(reason)) return 'Approve more funds'
      if (/Final package/i.test(reason)) return 'Send the final claim'
      if (/Reply/i.test(reason)) return 'Review the reply'
      if (/rounds|denied/i.test(reason)) return 'Decide on the denial'
      return 'Review the pre-auth'
    case 'PREAUTH_DRAFT': return 'Held back — prepare again'
    case 'SUBMITTED': return 'Waiting for the insurer'
    case 'QUERY_RAISED': return 'Insurer asked a question'
    case 'IN_TREATMENT': return 'In treatment'
    case 'FINAL_APPROVED': return 'Record the payment'
    case 'SETTLED': return 'Reconciling the payment'
    case 'CLOSED': return 'Done'
    case 'DENIED': return 'Denied'
    default: return 'Preparing the claim'
  }
}

// ---- What happened ----------------------------------------------------------------------------

// Rupee totals come from the API (the rules engine's helpers), never added up in the browser.
const deducted = (m, claim) => claim.totals?.deducted_by_message?.[(claim.thread ?? []).indexOf(m)]

function insurerStep(m, claim) {
  const who = insurerOf(claim)
  const round = `Round ${m.round} · ${m.stage === 'preauth' ? 'pre-auth' : m.stage}`
  if (m.type === 'APPROVE') return { icon: 'check', tone: 'good', title: `${who} approved ${money(m.approved_amount)}`, detail: round }
  if (m.type === 'PARTIAL_APPROVE') {
    const top = [...m.deductions].sort((a, b) => b.amount - a.amount)[0]
    return { icon: 'alert', tone: 'attention', title: `${who} approved ${money(m.approved_amount)} and cut ${money(deducted(m, claim))}`, detail: `${round} · biggest cut: ${top.item.split(':')[0]} (${money(top.amount)})` }
  }
  if (m.type === 'QUERY') return { icon: 'chat', tone: 'info', title: `${who} asked for more information`, detail: `${round} · ${m.missing.replace('Please send: ', '')}` }
  if (m.type === 'DENY') return { icon: 'x', tone: 'bad', title: `${who} denied the claim`, detail: `${round} · ${m.reason}` }
  if (m.type === 'FINAL') {
    const decided = m.decision === 'APPROVE' ? `approved ${money(m.approved_amount)}` : m.decision === 'DENY' ? 'denied the claim' : `approved ${money(m.approved_amount)} after cuts`
    return { icon: m.decision === 'DENY' ? 'x' : 'check', tone: m.decision === 'DENY' ? 'bad' : 'good', title: `Final word: ${who} ${decided}`, detail: `${round} · no rounds left` }
  }
  return { icon: 'building', tone: 'info', title: `${who} replied`, detail: round }
}

const SENT = { SUBMIT: 'request', RESPONSE: 'reply', APPEAL: 'appeal' }

export function journey(claim) {
  const steps = []
  const insurerMessages = (claim.thread ?? []).filter((m) => m.sender === 'insurer')
  const hospitalMessages = (claim.thread ?? []).filter((m) => m.sender === 'hospital')
  let insurerIndex = 0
  let hospitalIndex = 0
  for (const e of claim.timeline ?? []) {
    const add = (step) => steps.push({ id: e.event_id, time: e.created_at, ...step })
    const out = e.output
    if (e.action === 'open_claim') add({ icon: 'bed', tone: 'neutral', title: 'Patient admitted', detail: 'Claim opened; agents start reading the file' })
    else if (e.action === 'agent_output') {
      const o = out.output
      const data = out.data ?? {}
      if (e.actor === 'document_reader') add({ icon: 'file', tone: 'neutral', title: 'Documents read', detail: `${data.documents?.length ?? 0} documents, every field tied to its page` })
      else if (e.actor === 'preauth') add({ icon: 'sparkle', tone: 'neutral', title: `Pre-auth prepared for ${money(data.requested_amount)}`, detail: 'Typical bill for this treatment, from the cost model' })
      else if (e.actor === 'denial_risk') {
        const items = data.items?.length ?? 0
        add({ icon: 'shield', tone: items ? 'attention' : 'good', title: items ? `Risk check found ${items} issue${items > 1 ? 's' : ''}` : 'Risk check passed', detail: items ? data.items.map((i) => i.issue).join(' · ') : 'Nothing likely to be cut' })
      } else if (e.actor === 'appeal') {
        if (data.appeal) add({ icon: 'sparkle', tone: 'neutral', title: 'Appeal written', detail: `Contests ${money(data.disputed_total)}, with the evidence quoted` })
        else if (o.decision === 'accept_decision') add({ icon: 'check', tone: 'neutral', title: 'Cuts checked against the policy', detail: 'Every deduction is allowed — nothing to appeal' })
        else if (data.held_back?.length && data.evidence) add({ icon: 'alert', tone: 'attention', title: 'Cut not appealed: evidence too weak', detail: `Records ${data.evidence.verdict}: ${data.evidence.reason}` })
        else add({ icon: 'alert', tone: 'attention', title: 'Denial needs your judgement', detail: 'The agents won’t appeal a whole-claim denial on their own' })
      } else if (e.actor === 'query_responder') {
        add({ icon: 'sparkle', tone: o.decision === 'document_missing' ? 'attention' : 'neutral', title: 'Reply to the query drafted', detail: o.decision === 'document_missing' ? 'A requested document isn’t in the file' : 'Requested documents attached' })
      } else if (e.actor === 'enhancement' && data.submit) add({ icon: 'rupee', tone: 'attention', title: 'Bill passed the approved amount', detail: `Asking to raise the approval to ${money(data.submit.requested_amount)}` })
      else if (e.actor === 'discharge') add({ icon: 'file', tone: 'neutral', title: 'Final claim prepared', detail: 'Final bill, discharge summary and expected deductions' })
      else if (e.actor === 'reconciliation') add({ icon: 'receipt', tone: 'neutral', title: 'Payment reconciled', detail: o.findings[0]?.evidence })
    } else if (e.action === 'human_decision') {
      const who = agentName(e.actor)
      const verb = { approve: 'approved it', edit: 'edited and approved it', reject: 'held it back', accept: 'accepted the insurer’s decision' }[out.decision]
      add({ icon: 'user', tone: 'you', title: `${who} ${verb}`, detail: out.reason ?? null })
    } else if (e.action === 'message_sent' && e.actor === 'insurer') {
      const m = insurerMessages[insurerIndex++]
      if (m) add(insurerStep(m, claim))
    } else if (e.action === 'message_sent' && e.actor === 'hospital') {
      const m = hospitalMessages[hospitalIndex++]
      if (m) add({ icon: 'send', tone: 'neutral', title: `${m.stage === 'preauth' ? 'Pre-auth' : m.stage === 'enhancement' ? 'Enhancement' : 'Final claim'} ${SENT[m.type]} sent to ${insurerOf(claim)}`, detail: m.requested_amount ? `Asked for ${money(m.requested_amount)}` : `Round ${m.round}` })
    } else if (e.action === 'document_attached') add({ icon: 'file', tone: 'neutral', title: `${agentName(e.actor)} attached ${label(out.type)}`, detail: `${out.doc_id} · ${out.pages} page${out.pages > 1 ? 's' : ''}` })
    else if (e.action === 'recheck') add({ icon: 'shield', tone: out.remaining?.length || out.new?.length ? 'attention' : 'good', title: `Rechecked: draft v${out.draft_after ?? '?'} replaces v${out.draft_before ?? '?'}`, detail: [out.resolved?.length && `Resolved: ${out.resolved.join(', ')}`, out.remaining?.length && `Still open: ${out.remaining.join(', ')}`].filter(Boolean).join(' · ') || out.reason })
    else if (e.action === 'payment_advice') add({ icon: 'rupee', tone: 'good', title: `Payment of ${money(out.paid)} received`, detail: out.reference })
    else if (e.action === 'transition' && out.to === 'IN_TREATMENT') add({ icon: 'heart', tone: 'good', title: 'Treatment under way', detail: out.reason })
    else if (e.action === 'transition' && out.to === 'CLOSED') add({ icon: 'check', tone: 'good', title: 'Claim closed', detail: out.reason })
    else if (e.action === 'run_error' || e.action === 'escalate') add({ icon: 'alert', tone: 'bad', title: 'Something needs a person', detail: out.reason ?? out.error })
  }
  return steps
}
