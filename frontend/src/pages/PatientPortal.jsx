import { useEffect, useState } from 'react'
import { fileToBase64, request } from '../api'
import { useAuth } from '../auth'
import { Badge, Button, Card, CardHeader, Icon, money, procedureName } from '../components/ui'

const TYPES = ['xray_report', 'lab_report', 'prescription', 'admission_note', 'discharge_summary', 'operative_note', 'final_bill']
const human = value => value.replaceAll('_', ' ')
const date = value => value ? new Date(`${value.slice(0, 10)}T12:00:00`).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' }) : 'Not recorded'

export default function PatientPortal() {
  const { user, logout } = useAuth()
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState('')
  const [notice, setNotice] = useState('')
  const load = async () => { try { setData(await request('GET', '/api/portal/dashboard')); setError('') } catch (e) { setError(e.message) } }
  useEffect(() => { load(); const timer = setInterval(load, 10000); return () => clearInterval(timer) }, [])
  const claim = data?.claims.find(c => c.claim_id === selected) ?? data?.claims[0]
  const profile = data?.profiles.find(p => p.policy.policy_id === claim?.latest?.policy_id) ?? data?.profiles[0]
  const policy = profile?.policy
  return <div className="h-full overflow-y-auto bg-[#f5f6f2]">
    <header className="border-b border-line bg-white px-6 py-4"><div className="mx-auto flex max-w-6xl items-center justify-between gap-4"><div className="flex items-center gap-2 font-semibold"><Icon name="logo" />ClaimPilot <span className="ml-2 text-xs font-normal text-ink-muted">Patient portal</span></div><Button onClick={() => logout().catch(e => setError(e.message))}>Sign out</Button></div></header>
    <main className="mx-auto max-w-6xl px-6 py-8">
      <div className="mb-7 flex flex-wrap items-end justify-between gap-4"><div><p className="eyebrow">Your insurance, made clear</p><h1 className="mt-2 text-3xl font-semibold">Hello, {user.display_name.split(' ')[0]}.</h1><p className="mt-2 text-sm text-ink-muted">Your cover, hospital updates, and next steps in one place.</p></div><Badge tone="info">Synthetic demo account</Badge></div>
      {error && <p role="alert" className="mb-4 rounded-lg bg-rose-50 p-3 text-rose-800">{error}</p>}
      {!data ? <p>Loading your dashboard…</p> : <>
        {policy && <section className="mb-7 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Tile label="Sum insured" value={money(policy.sum_insured)} note="Policy limit, not remaining balance" />
          <Tile label="Insurance company" value={policy.insurer} note={policy.product} />
          <Tile label="Policy expires" value={date(policy.expiry_date)} note={policy.days_until_expiry == null ? 'Contact your billing team' : policy.days_until_expiry < 0 ? `Expired ${Math.abs(policy.days_until_expiry)} days ago` : `${policy.days_until_expiry} days remaining`} />
          <Tile label="Cover status" value={policy.coverage_status} note={`As of ${date(data.as_of)}`} />
        </section>}
        <div className="grid items-start gap-6 lg:grid-cols-3">
          <div className="space-y-6 lg:col-span-2">
            <Card><CardHeader title="Your claims" subtitle="Only updates reviewed and published by your billing officer appear here." />
              <div className="space-y-4 px-5 pb-5">{!data.claims.length ? <div className="rounded-lg bg-canvas p-5"><p className="font-medium">No claim has been opened yet</p><p className="mt-2 text-sm text-ink-muted">Your insurance is listed here. Once the hospital opens a claim, it will appear in this dashboard.</p></div> : <>
                <label className="block text-xs text-ink-muted">Choose a claim<select className="input mt-1" value={claim?.claim_id ?? ''} onChange={e => setSelected(e.target.value)}>{data.claims.map(c => <option key={c.claim_id} value={c.claim_id}>{c.claim_id} · {c.latest ? procedureName(c.latest.procedure) : 'Awaiting hospital update'}</option>)}</select></label>
                <div className="rounded-xl bg-[#edf5ef] p-5"><Badge tone="good">{claim.latest?.status ?? 'Awaiting first officer update'}</Badge><h2 className="mt-3 text-xl font-semibold">{claim.latest ? procedureName(claim.latest.procedure) : 'Your hospital claim'}</h2><p className="mt-2 text-sm text-ink-muted">{claim.latest?.claim_type ?? 'Cashless hospitalisation'} · {claim.claim_id}</p>{claim.latest && <p className="mt-3 text-xs text-ink-muted">Published {date(claim.latest.published_at)} by {claim.latest.approved_by}. New internal changes appear after officer review.</p>}</div>
                {claim.latest && <div><div className="grid grid-cols-3 gap-3"><Amount title="Current bill" value={claim.latest.estimate.billed} /><Amount title="Estimated insurer share" value={claim.latest.estimate.insurer} /><Amount title="Estimated your share" value={claim.latest.estimate.patient} /></div><p className="mt-3 text-xs text-ink-muted">{claim.latest.estimate_label}</p></div>}
              </>}</div>
            </Card>
            {claim && <Card><CardHeader title="Documents & next steps" subtitle="Your files go to the billing officer first. They are never submitted automatically." /><div className="space-y-4 px-5 pb-5">
              {claim.requests.filter(r => r.status === 'open').map(r => <div key={r.request_id} className="rounded-lg border border-amber-200 bg-amber-50 p-4"><p className="font-medium capitalize">{human(r.document_type)} needed</p><p className="mt-1 text-sm">{r.message}</p><p className="mt-2 text-xs text-ink-muted">Requested by {r.officer}</p></div>)}
              {!claim.requests.some(r => r.status === 'open') && <p className="text-sm text-ink-muted">No open document requests. You can still send a relevant synthetic PDF to your billing officer.</p>}
              <UploadForm key={claim.claim_id} claim={claim} done={async message => { setNotice(message); await load() }} />
              {notice && <p role="status" className="text-sm text-accent-700">{notice}</p>}
              <div className="space-y-3">{claim.uploads.map(u => <div key={u.upload_id} className="rounded-lg border border-line p-3"><div className="flex flex-wrap items-center justify-between gap-2"><a className="break-all text-sm font-medium underline" href={`/api/portal/claims/${claim.claim_id}/uploads/${u.upload_id}/file`}>{u.filename}</a><Badge tone={u.status === 'accepted' ? 'good' : u.status === 'rejected' ? 'bad' : 'attention'}>{u.status === 'pending' ? 'Awaiting officer review' : u.status === 'accepted' ? 'Accepted for rechecking' : 'Replacement needed'}</Badge></div>{u.reason && <p className="mt-2 text-sm text-ink-muted">Officer note: {u.reason}</p>}</div>)}</div>
            </div></Card>}
            {claim && <Card><CardHeader title="Claim updates" subtitle="A history of the updates your hospital has shared." /><ol className="space-y-4 px-5 pb-5">{!claim.history.length && <li className="text-sm text-ink-muted">Your first reviewed update will appear here.</li>}{[...claim.history].reverse().map(u => <li key={u.update_id} className="border-l-2 border-accent-200 pl-4"><p className="text-sm font-medium">{u.status}</p><p className="mt-1 text-xs text-ink-muted">{date(u.published_at)} · {u.approved_by}</p></li>)}</ol></Card>}
          </div>
          <aside className="space-y-6">
            <Card><CardHeader title="Your policy" subtitle="Hospital-reviewed enrollment details" /><dl className="space-y-4 px-5 pb-5">{policy ? <><Detail label="Policy number" value={policy.policy_id} /><Detail label="Plan" value={policy.product} /><Detail label="Claim type" value={profile.claim_type} /><Detail label="Original policy start" value={date(policy.start_date)} /><Detail label="Current term starts" value={date(policy.coverage_start_date)} /><Detail label="Expiry date" value={date(policy.expiry_date)} /><Detail label="Policyholder" value={profile.patient.name} /></> : <p>No enrollment details available.</p>}</dl></Card>
            <div className="rounded-xl bg-[#163d37] p-5 text-white"><Icon name="shield" /><h2 className="mt-3 font-semibold">Your officer is here to help</h2><p className="mt-2 text-sm leading-relaxed text-white/75">For coverage questions or a changed document, contact the hospital billing desk. The team reviews your evidence and approves every insurer submission.</p></div>
            <p className="text-xs leading-relaxed text-ink-muted">Use synthetic documents only. Coverage dates describe the policy term; expiry today does not determine whether an earlier admission was covered.</p>
          </aside>
        </div>
      </>}
    </main>
  </div>
}

function Tile({ label, value, note }) { return <Card className="p-5"><p className="eyebrow">{label}</p><p className="mt-3 text-xl font-semibold">{value}</p><p className="mt-2 text-xs text-ink-muted">{note}</p></Card> }
function Detail({ label, value }) { return <div><dt className="text-xs text-ink-muted">{label}</dt><dd className="mt-1 text-sm font-medium">{value}</dd></div> }
function Amount({ title, value }) { return <div><p className="text-xs text-ink-muted">{title}</p><p className="mt-1 text-base font-semibold sm:text-lg">{money(value)}</p></div> }
function UploadForm({ claim, done }) {
  const [type, setType] = useState('xray_report'), [file, setFile] = useState(null), [requestId, setRequestId] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState('')
  async function submit(e) {
    e.preventDefault(); setError('')
    if (!file || file.size > 5 * 1024 * 1024) { setError('Choose a synthetic PDF up to 5 MB.'); return }
    setBusy(true)
    try {
      const result = await request('POST', `/api/portal/claims/${claim.claim_id}/uploads`, { type, filename: file.name, content_base64: await fileToBase64(file), request_id: requestId || null })
      await done(result.duplicate ? 'This document was already received.' : 'Document sent to your billing officer for review.')
      setFile(null); e.target.reset()
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  return <form onSubmit={submit} className="space-y-3 rounded-xl bg-canvas p-4">
    <label className="block text-xs">Respond to a request<select className="input mt-1" value={requestId} onChange={e => { setRequestId(e.target.value); const r = claim.requests.find(r => r.request_id === e.target.value); if (r) setType(r.document_type) }}><option value="">Additional document</option>{claim.requests.filter(r => r.status === 'open').map(r => <option key={r.request_id} value={r.request_id}>{human(r.document_type)} — {r.message}</option>)}</select></label>
    <label className="block text-xs">Document type<select className="input mt-1" value={type} disabled={!!requestId} onChange={e => setType(e.target.value)}>{TYPES.map(t => <option key={t} value={t}>{human(t)}</option>)}</select></label>
    <label className="block text-xs">Synthetic PDF · up to 5 MB, 20 pages<input className="input mt-1" type="file" accept="application/pdf,.pdf" required onChange={e => setFile(e.target.files[0])} /></label>
    {error && <p role="alert" className="text-sm text-rose-700">{error}</p>}<Button type="submit" variant="primary" disabled={busy}>{busy ? 'Uploading…' : 'Send to billing officer'}</Button>
  </form>
}
