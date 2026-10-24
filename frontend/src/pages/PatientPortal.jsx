// Patient portal: the patient's cover, officer-published claim updates, document requests and uploads.
// Only updates a billing officer reviewed and published appear here.
import { useEffect, useState } from 'react'
import { fileToBase64, request } from '../api'
import { useAuth } from '../auth'
import Assistant from '../components/Assistant'
import { Avatar, Badge, Button, Card, CardHeader, Icon, IconChip, money, procedureName, stagger } from '../components/ui'

const TYPES = ['xray_report', 'lab_report', 'prescription', 'admission_note', 'discharge_summary', 'operative_note', 'final_bill']
const human = (value) => value.replaceAll('_', ' ')
const date = (value) => value ? new Date(`${value.slice(0, 10)}T12:00:00`).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' }) : 'Not recorded'

export default function PatientPortal() {
  const { user, logout } = useAuth()
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState('')
  const [notice, setNotice] = useState('')
  const load = async () => { try { setData(await request('GET', '/api/portal/dashboard')); setError('') } catch (e) { setError(e.message) } }
  useEffect(() => { load(); const timer = setInterval(load, 10000); return () => clearInterval(timer) }, [])
  const claim = data?.claims.find((c) => c.claim_id === selected) ?? data?.claims[0]
  const profile = data?.profiles.find((p) => p.policy.policy_id === claim?.latest?.policy_id) ?? data?.profiles[0]
  const policy = profile?.policy
  const first = user.display_name.split(' ')[0]
  const openRequests = claim?.requests.filter((r) => r.status === 'open') ?? []

  return (
    <div className="h-full overflow-y-auto bg-canvas">
      <header className="sticky top-0 z-20 border-b border-line bg-white/85 backdrop-blur">
        <div className="mx-auto flex h-[72px] max-w-6xl items-center justify-between gap-4 px-6">
          <div className="flex items-center gap-3">
            <span className="grid h-10 w-10 place-items-center rounded-2xl bg-accent-600 text-white shadow-glow"><Icon name="logo" className="h-6 w-6" /></span>
            <div className="leading-tight">
              <p className="text-[17px] font-extrabold tracking-tight">ClaimPilot</p>
              <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-accent-600">Patient portal</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <Badge tone="info">Synthetic demo account</Badge>
            <div className="flex items-center gap-2.5 border-l border-line pl-3">
              <Avatar name={user.display_name} size="sm" />
              <p className="hidden text-sm font-bold sm:block">{user.display_name}</p>
            </div>
            <Button size="sm" icon="logout" onClick={() => logout().catch((e) => setError(e.message))}>Sign out</Button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-6xl animate-fade-up px-6 pb-12 pt-8">
        {error && <p role="alert" className="mb-4 rounded-2xl bg-rose-50 p-3 text-sm text-rose-800">{error}</p>}
        {!data ? <p className="text-sm text-ink-muted">Loading your dashboard…</p> : <>
          <section className="hero relative mb-6 overflow-hidden rounded-2xl p-7 text-white shadow-lift">
            <span className="absolute -bottom-24 -right-16 h-72 w-72 rounded-full border-[44px] border-white/5" />
            <span className="absolute right-40 top-6 h-24 w-24 rounded-full border-[16px] border-white/5" />
            <div className="relative grid gap-6 md:grid-cols-[1fr_auto] md:items-end">
              <div>
                <p className="text-[13px] font-semibold text-white/70">Your insurance, made clear</p>
                <h1 className="mt-2 text-[32px] font-extrabold leading-tight tracking-tight">Hello, {first}.</h1>
                <p className="mt-2 max-w-lg text-sm leading-relaxed text-white/75">Your cover, hospital updates and next steps in one place. Every update here was reviewed by your billing officer first.</p>
                {claim?.latest && <p className="mt-5 inline-flex items-center gap-2 rounded-full bg-white/15 px-4 py-2 text-sm font-semibold"><span className="h-2 w-2 animate-ring rounded-full bg-white" />{claim.latest.status}</p>}
              </div>
              {claim?.latest && (
                <div className="rounded-2xl bg-white/10 p-5 backdrop-blur md:min-w-[260px]">
                  <p className="text-[13px] font-medium text-white/70">You may pay about</p>
                  <p className="mt-1 text-[34px] font-extrabold leading-none tracking-tight tabular">{money(claim.latest.estimate.patient)}</p>
                  <p className="mt-2 text-xs text-white/70">Insurer share {money(claim.latest.estimate.insurer)} of {money(claim.latest.estimate.billed)}</p>
                </div>
              )}
            </div>
          </section>

          {policy && (
            <section className="mb-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <Tile icon="shield" tone="green" label="Sum insured" value={money(policy.sum_insured)} note="Policy limit, not remaining balance" />
              <Tile icon="building" tone="blue" label="Insurance company" value={policy.insurer} note={policy.product} />
              <Tile icon="calendar" tone="amber" label="Policy expires" value={date(policy.expiry_date)}
                note={policy.days_until_expiry == null ? 'Contact your billing team' : policy.days_until_expiry < 0 ? `Expired ${Math.abs(policy.days_until_expiry)} days ago` : `${policy.days_until_expiry} days remaining`} />
              <Tile icon="check" tone="violet" label="Cover status" value={policy.coverage_status} note={`As of ${date(data.as_of)}`} />
            </section>
          )}

          <div className="grid items-start gap-6 lg:grid-cols-3">
            <div className="space-y-6 lg:col-span-2">
              <Card>
                <CardHeader icon="file" title="Your claim" subtitle="Only updates reviewed and published by your billing officer appear here." />
                <div className="space-y-5 px-6 pb-6">
                  {!data.claims.length ? (
                    <div className="rounded-2xl bg-canvas p-5"><p className="font-semibold">No claim has been opened yet</p><p className="mt-2 text-sm text-ink-muted">Your insurance is listed here. Once the hospital opens a claim, it will appear on this page.</p></div>
                  ) : <>
                    {data.claims.length > 1 && (
                      <label className="block text-xs font-semibold text-ink-muted">Choose a claim
                        <select className="input mt-1.5" value={claim?.claim_id ?? ''} onChange={(e) => setSelected(e.target.value)}>
                          {data.claims.map((c) => <option key={c.claim_id} value={c.claim_id}>{c.claim_id} · {c.latest ? procedureName(c.latest.procedure) : 'Awaiting hospital update'}</option>)}
                        </select>
                      </label>
                    )}
                    <div className="flex flex-wrap items-center gap-4 rounded-2xl bg-accent-50/70 p-5">
                      <IconChip name="stethoscope" size="lg" />
                      <div className="min-w-0 flex-1">
                        <p className="text-lg font-bold">{claim.latest ? procedureName(claim.latest.procedure) : 'Your hospital claim'}</p>
                        <p className="mt-0.5 text-sm text-ink-muted">{claim.latest?.claim_type ?? 'Cashless hospitalisation'} · {claim.claim_id}</p>
                      </div>
                      <Badge tone="good">{claim.latest?.status ?? 'Awaiting first officer update'}</Badge>
                    </div>
                    {claim.latest && <>
                      <div className="grid grid-cols-3 gap-3">
                        <Amount title="Current bill" value={claim.latest.estimate.billed} />
                        <Amount title="Insurer share (est.)" value={claim.latest.estimate.insurer} />
                        <Amount title="Your share (est.)" value={claim.latest.estimate.patient} strong />
                      </div>
                      <p className="text-xs text-ink-muted">{claim.latest.estimate_label} Published {date(claim.latest.published_at)} by {claim.latest.approved_by}.</p>
                    </>}
                  </>}
                </div>
              </Card>

              {claim && (
                <Card>
                  <CardHeader icon="upload" title="Documents and next steps" subtitle="Your files go to the billing officer first. They are never submitted automatically."
                    action={openRequests.length > 0 && <Badge tone="attention">{openRequests.length} requested</Badge>} />
                  <div className="space-y-4 px-6 pb-6">
                    {openRequests.map((r) => (
                      <div key={r.request_id} className="flex gap-3 rounded-2xl border border-amber-200 bg-amber-50/70 p-4">
                        <IconChip name="alert" tone="amber" size="sm" />
                        <div><p className="font-bold capitalize">{human(r.document_type)} needed</p><p className="mt-1 text-sm">{r.message}</p><p className="mt-2 text-xs text-ink-muted">Requested by {r.officer}</p></div>
                      </div>
                    ))}
                    {!openRequests.length && <p className="text-sm text-ink-muted">No open document requests. You can still send a relevant synthetic PDF to your billing officer.</p>}
                    <UploadForm key={claim.claim_id} claim={claim} done={async (message) => { setNotice(message); await load() }} />
                    {notice && <p role="status" className="text-sm font-medium text-accent-700">{notice}</p>}
                    <div className="space-y-2">
                      {claim.uploads.map((u) => (
                        <div key={u.upload_id} className="flex flex-wrap items-center gap-3 rounded-2xl border border-line p-3">
                          <IconChip name="file" tone="ink" size="sm" />
                          <a className="min-w-0 flex-1 break-all text-sm font-semibold hover:text-accent-700 hover:underline" href={`/api/portal/claims/${claim.claim_id}/uploads/${u.upload_id}/file`}>{u.filename}</a>
                          <Badge tone={u.status === 'accepted' ? 'good' : u.status === 'rejected' ? 'bad' : 'attention'}>{u.status === 'pending' ? 'Awaiting officer review' : u.status === 'accepted' ? 'Accepted for rechecking' : 'Replacement needed'}</Badge>
                          {u.reason && <p className="w-full pl-11 text-sm text-ink-muted">Officer note: {u.reason}</p>}
                        </div>
                      ))}
                    </div>
                  </div>
                </Card>
              )}

              {claim && (
                <Card>
                  <CardHeader icon="activity" title="Claim updates" subtitle="A history of the updates your hospital has shared." />
                  <ol className="relative space-y-3 px-6 pb-6 pl-11">
                    <span className="absolute bottom-8 left-[29px] top-2 w-px bg-accent-200" />
                    {!claim.history.length && <li className="text-sm text-ink-muted">Your first reviewed update will appear here.</li>}
                    {[...claim.history].reverse().map((u, i) => (
                      <li key={u.update_id} className="relative animate-fade-up" style={stagger(i)}>
                        <span className="absolute -left-[19px] top-4 h-[11px] w-[11px] rounded-full border-2 border-white bg-accent-500 ring-2 ring-accent-200" />
                        <div className="rounded-2xl bg-canvas px-4 py-3"><p className="text-sm font-bold">{u.status}</p><p className="mt-0.5 text-xs text-ink-muted">{date(u.published_at)} · {u.approved_by}</p></div>
                      </li>
                    ))}
                  </ol>
                </Card>
              )}
            </div>

            <aside className="space-y-6">
              <Card>
                <CardHeader icon="shield" title="Your policy" subtitle="Hospital-reviewed enrolment details" />
                <dl className="space-y-3 px-6 pb-6">
                  {policy ? <>
                    <Detail label="Policy number" value={policy.policy_id} />
                    <Detail label="Plan" value={policy.product} />
                    <Detail label="Claim type" value={profile.claim_type} />
                    <Detail label="Original policy start" value={date(policy.start_date)} />
                    <Detail label="Current term starts" value={date(policy.coverage_start_date)} />
                    <Detail label="Expiry date" value={date(policy.expiry_date)} />
                    <Detail label="Policyholder" value={profile.patient.name} />
                  </> : <p className="text-sm text-ink-muted">No enrolment details available.</p>}
                </dl>
              </Card>
              <div className="relative overflow-hidden rounded-2xl bg-accent-50 p-5">
                <span className="absolute -right-6 -top-6 h-24 w-24 rounded-full bg-accent-100" />
                <span className="relative grid h-10 w-10 place-items-center rounded-xl bg-white text-accent-600 shadow-card"><Icon name="help" className="h-5 w-5" /></span>
                <h2 className="relative mt-3 font-bold text-accent-800">Your officer is here to help</h2>
                <p className="relative mt-1.5 text-sm leading-relaxed text-accent-700/85">For coverage questions or a changed document, contact the hospital billing desk. The team reviews your evidence and approves every insurer submission.</p>
              </div>
              <p className="px-1 text-xs leading-relaxed text-ink-muted">Use synthetic documents only. Coverage dates describe the policy term; expiry today does not decide whether an earlier admission was covered.</p>
            </aside>
          </div>
        </>}
      </main>
      <Assistant mode="patient" claimId={claim?.claim_id} />
    </div>
  )
}

function Tile({ icon, tone, label, value, note }) {
  return (
    <div className="rounded-xl border border-line/80 bg-white p-5 shadow-card transition hover:-translate-y-0.5 hover:shadow-lift">
      <IconChip name={icon} tone={tone} />
      <p className="mt-4 text-[13px] font-medium text-ink-muted">{label}</p>
      <p className="mt-1 text-lg font-bold tracking-tight">{value}</p>
      <p className="mt-1 text-xs text-ink-faint">{note}</p>
    </div>
  )
}

function Detail({ label, value }) {
  return <div className="flex justify-between gap-4 border-b border-line/70 pb-3 last:border-0 last:pb-0"><dt className="text-[13px] text-ink-muted">{label}</dt><dd className="text-right text-[13px] font-semibold">{value}</dd></div>
}

function Amount({ title, value, strong }) {
  return (
    <div className={`rounded-2xl p-4 ${strong ? 'bg-accent-600 text-white shadow-glow' : 'bg-canvas'}`}>
      <p className={`text-xs ${strong ? 'text-white/75' : 'text-ink-muted'}`}>{title}</p>
      <p className="mt-1 text-base font-bold tabular sm:text-lg">{money(value)}</p>
    </div>
  )
}

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
    } catch (err) { setError(err.message) } finally { setBusy(false) }
  }
  return (
    <form onSubmit={submit} className="space-y-3 rounded-2xl border border-dashed border-accent-200 bg-accent-50/40 p-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block text-xs font-semibold text-ink-muted">Respond to a request
          <select className="input mt-1.5" value={requestId} onChange={(e) => { setRequestId(e.target.value); const r = claim.requests.find((x) => x.request_id === e.target.value); if (r) setType(r.document_type) }}>
            <option value="">Additional document</option>
            {claim.requests.filter((r) => r.status === 'open').map((r) => <option key={r.request_id} value={r.request_id}>{human(r.document_type)} — {r.message}</option>)}
          </select>
        </label>
        <label className="block text-xs font-semibold text-ink-muted">Document type
          <select className="input mt-1.5" value={type} disabled={!!requestId} onChange={(e) => setType(e.target.value)}>{TYPES.map((t) => <option key={t} value={t}>{human(t)}</option>)}</select>
        </label>
      </div>
      <label className="block text-xs font-semibold text-ink-muted">Synthetic PDF · up to 5 MB, 20 pages
        <input className="input mt-1.5 file:mr-3 file:rounded-full file:border-0 file:bg-accent-600 file:px-3 file:py-1 file:text-xs file:font-semibold file:text-white" type="file" accept="application/pdf,.pdf" required onChange={(e) => setFile(e.target.files[0])} />
      </label>
      {error && <p role="alert" className="text-sm text-rose-700">{error}</p>}
      <Button type="submit" variant="primary" icon="send" loading={busy}>Send to billing officer</Button>
    </form>
  )
}
