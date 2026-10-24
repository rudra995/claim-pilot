import { useEffect, useState } from 'react'
import { request } from '../api'
import { Badge, Button, Card, CardHeader, Icon, IconChip, money } from './ui'

const types = ['xray_report', 'lab_report', 'prescription', 'admission_note', 'discharge_summary', 'operative_note', 'final_bill']
export default function PatientDesk({ claimId, onDone }) {
  const [data, setData] = useState(null), [error, setError] = useState(''), [notice, setNotice] = useState(''), [busy, setBusy] = useState(false)
  const [type, setType] = useState('xray_report'), [message, setMessage] = useState('Please upload a clear copy of your pre-operative X-ray report.')
  const load = async () => { try { setData(await request('GET', `/api/patient-desk/${claimId}`)) } catch (e) { setError(e.message) } }
  useEffect(() => { load(); const timer = setInterval(load, 5000); return () => clearInterval(timer) }, [claimId])
  async function act(path, body, success) {
    setBusy(true); setError(''); setNotice('')
    try { await request('POST', `/api/patient-desk/${claimId}/${path}`, body); setNotice(success); await load(); onDone() }
    catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  return <div className="space-y-5">
    {error && <p role="alert" className="rounded-lg bg-rose-50 p-3 text-rose-800">{error}</p>}{notice && <p role="status" className="text-sm text-accent-700">{notice}</p>}
    {!data ? <p>Loading patient workspace…</p> : <>
      <Questions claimId={claimId} />
      <Card><CardHeader title="Review before sharing with the patient" subtitle="The patient sees this snapshot only after you approve and publish it." /><div className="space-y-4 px-5 pb-5">
        <div className="rounded-lg bg-canvas p-4"><p className="font-medium">{data.preview.status}</p><p className="mt-1 text-sm text-ink-muted">{data.preview.claim_type} · {data.preview.insurer} · {data.preview.plan}</p><div className="mt-4 grid grid-cols-3 gap-3">{[['Current bill', data.preview.estimate.billed], ['Estimated insurer share', data.preview.estimate.insurer], ['Estimated patient share', data.preview.estimate.patient]].map(([label, value]) => <div key={label}><p className="text-xs text-ink-muted">{label}</p><p className="mt-1 font-semibold">{money(value)}</p></div>)}</div><p className="mt-3 text-xs text-ink-muted">{data.preview.estimate_label}</p></div>
        <Button variant="primary" disabled={busy || data.busy} onClick={() => act('publish', { snapshot_hash: data.snapshot_hash }, 'Reviewed update published to the patient account.')}>Approve & publish patient update</Button>
        {data.last_published && <p className="text-xs text-ink-muted">Last published by {data.last_published.approved_by} · {new Date(data.last_published.created_at).toLocaleString()}</p>}
      </div></Card>
      <Card><CardHeader title="Request a document" subtitle="Sending this request is your approval for the patient to see its wording." /><form className="space-y-3 px-5 pb-5" onSubmit={e => { e.preventDefault(); act('requests', { type, message }, 'Document request sent to the patient.') }}>
        <label className="block text-xs">Document type<select className="input mt-1" value={type} onChange={e => setType(e.target.value)}>{types.map(t => <option key={t} value={t}>{t.replaceAll('_', ' ')}</option>)}</select></label>
        <label className="block text-xs">Patient-facing request<textarea required minLength={5} maxLength={500} className="input mt-1" value={message} onChange={e => setMessage(e.target.value)} /></label><Button type="submit" disabled={busy}>Approve & send request</Button>
        {data.requests.map(r => <p key={r.request_id} className="text-sm text-ink-muted">{r.message} <Badge tone={r.status === 'fulfilled' ? 'good' : 'attention'}>{r.status}</Badge></p>)}
      </form></Card>
      <Card><CardHeader title="Documents from the patient" subtitle="Download and inspect each file. Acceptance adds it to the claim and starts a fresh recheck; submission still needs approval." /><div className="space-y-4 px-5 pb-5">
        {!data.uploads.length && <p className="text-sm text-ink-muted">No patient uploads yet.</p>}
        {data.uploads.map(u => <Upload key={u.upload_id} item={u} claimId={claimId} busy={busy || data.busy} review={(accept, reason) => act(`uploads/${u.upload_id}/review`, { accept, reason }, accept ? 'Document accepted. Rechecking has started; review the new draft before submitting.' : 'Replacement requested with your review note.')} />)}
      </div></Card>
    </>}
  </div>
}

function Upload({ item, claimId, busy, review }) {
  const [reason, setReason] = useState('')
  return <div className="space-y-3 rounded-lg border border-line p-4"><div className="flex items-center justify-between gap-3"><a href={`/api/portal/claims/${claimId}/uploads/${item.upload_id}/file`} className="break-all text-sm font-medium underline">Download {item.filename}</a><Badge tone={item.status === 'pending' ? 'attention' : item.status === 'accepted' ? 'good' : 'bad'}>{item.status}</Badge></div>
    <p className="text-xs text-ink-muted">{item.document_type.replaceAll('_', ' ')} · {new Date(item.created_at).toLocaleString()}</p>
    {item.status === 'pending' ? <><label className="block text-xs">Review note shared with the patient<textarea className="input mt-1" maxLength={500} value={reason} onChange={e => setReason(e.target.value)} placeholder="Confirm what you checked, or explain what needs replacing." /></label><div className="flex flex-wrap gap-2"><Button disabled={busy || reason.trim().length < 3} variant="primary" onClick={() => review(true, reason)}>Accept & recheck</Button><Button disabled={busy || reason.trim().length < 3} onClick={() => review(false, reason)}>Request replacement</Button></div></> : <p className="text-sm text-ink-muted">{item.reason} · {item.reviewed_by}</p>}
  </div>
}

// Questions the patient's assistant handed to the officer. The officer's written reply is what the patient sees.
function Questions({ claimId }) {
  const [items, setItems] = useState(null)
  const [error, setError] = useState('')
  const load = () => request('GET', `/api/patient-desk/${claimId}/questions`).then(setItems).catch((e) => setError(e.message))
  useEffect(() => { load(); const timer = setInterval(load, 6000); return () => clearInterval(timer) }, [claimId])
  if (!items) return null
  const waiting = items.filter((q) => !q.answer)
  return <Card>
    <CardHeader icon="chat" title="Questions from the patient" subtitle="The assistant hands over anything that needs your judgement. Your reply is shared with the patient as written."
      action={waiting.length > 0 && <Badge tone="attention">{waiting.length} waiting</Badge>} />
    <div className="space-y-3 px-6 pb-6">
      {error && <p className="text-sm text-rose-700">{error}</p>}
      {!items.length && <p className="text-sm text-ink-muted">No questions yet. When the patient asks something only you can answer, it appears here.</p>}
      {items.map((q) => <Question key={q.question_id} claimId={claimId} item={q} done={load} />)}
    </div>
  </Card>
}

function Question({ claimId, item, done }) {
  const [reply, setReply] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const send = async () => {
    setBusy(true); setError('')
    try { await request('POST', `/api/patient-desk/${claimId}/questions/${item.question_id}/answer`, { answer: reply.trim() }); setReply(''); await done() }
    catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  return <div className={`rounded-2xl border p-4 ${item.answer ? 'border-line' : 'border-amber-200 bg-amber-50/40'}`}>
    <div className="flex items-start gap-3">
      <IconChip name="user" tone={item.answer ? 'ink' : 'amber'} size="sm" />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-bold">“{item.question}”</p>
        <p className="mt-0.5 text-xs text-ink-muted">{new Date(item.asked_at.endsWith('Z') ? item.asked_at : `${item.asked_at}Z`).toLocaleString()} · {item.why === 'judgement' ? 'Needs your judgement' : 'No grounded answer found'}</p>
      </div>
    </div>
    {item.answer ? (
      <p className="mt-3 flex gap-2 rounded-xl bg-accent-50/70 p-3 text-sm"><Icon name="check" className="mt-0.5 h-4 w-4 shrink-0 text-accent-600" /><span><b>{item.answer.officer}:</b> {item.answer.answer}</span></p>
    ) : (
      <div className="mt-3 space-y-2">
        <textarea className="input" rows={3} maxLength={1500} value={reply} onChange={(e) => setReply(e.target.value)} placeholder="Write the reply the patient will read. Don't promise an outcome the insurer hasn't decided." />
        {error && <p className="text-sm text-rose-700">{error}</p>}
        <Button variant="primary" icon="send" loading={busy} disabled={reply.trim().length < 3} onClick={send}>Approve & send reply</Button>
      </div>
    )}
  </div>
}
