// Admit patient: pick one of the hospital's preset patients, see their admission, and start the claim.
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, useLoad } from '../api'
import { Avatar, Badge, Button, Card, ErrorNote, Icon, Skeleton, money, procedureName, roomName, stagger, useToast } from '../components/ui'

const GENDER = { M: 'Male', F: 'Female', O: 'Other' }

export default function Admit() {
  const cases = useLoad(api.cases)
  const navigate = useNavigate()
  const toast = useToast()
  const [query, setQuery] = useState('')
  const [insurer, setInsurer] = useState('All')
  const [picked, setPicked] = useState(null)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState(null)

  const all = cases.data ?? []
  const insurers = ['All', ...new Set(all.map((c) => c.insurer))]
  const q = query.trim().toLowerCase()
  const shown = all
    .filter((c) => insurer === 'All' || c.insurer === insurer)
    .filter((c) => !q || [c.patient, c.title, procedureName(c.procedure), c.city].some((v) => v.toLowerCase().includes(q)))
  const selected = all.find((c) => c.case_id === picked) ?? null

  const admit = async () => {
    setStarting(true)
    setError(null)
    try {
      const { claim_id } = await api.start(selected.case_id)
      toast({ title: `${selected.patient} admitted`, body: 'The agents are preparing the pre-auth.', icon: 'bed' })
      navigate(`/patients/${claim_id}`)
    } catch (err) {
      setError(err.message)
      setStarting(false)
    }
  }

  return (
    <>
      <Link to="/patients" className="mb-4 inline-flex items-center gap-1.5 text-[13px] text-ink-muted hover:text-ink"><Icon name="arrowLeft" className="h-4 w-4" />Patients</Link>
      <div className="mb-6">
        <h1 className="text-[22px] font-semibold tracking-tight">Admit a patient</h1>
        <p className="mt-1 text-sm text-ink-muted">Choose who is being admitted. Their documents, policy and estimated bill are already on file.</p>
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_380px]">
        <div>
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <div className="relative min-w-[220px] flex-1">
              <Icon name="search" className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-faint" />
              <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search by name, condition or city" className="input pl-9" />
            </div>
            <select value={insurer} onChange={(e) => setInsurer(e.target.value)} className="input w-auto">
              {insurers.map((name) => <option key={name}>{name}</option>)}
            </select>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            {!cases.data && [0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-[118px] rounded-xl" />)}
            {shown.map((c, i) => {
              const active = picked === c.case_id
              return (
                <button key={c.case_id} onClick={() => setPicked(c.case_id)} style={stagger(i, 25)}
                  className={`group animate-fade-up rounded-xl border bg-white p-4 text-left transition-all duration-200 hover:-translate-y-0.5 hover:shadow-lift ${active ? 'border-ink shadow-lift ring-1 ring-ink' : 'border-line shadow-card'}`}>
                  <div className="flex items-start gap-3">
                    <Avatar name={c.patient} />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <p className="truncate font-medium">{c.patient}</p>
                        {c.demo && <Badge tone="info">Demo</Badge>}
                      </div>
                      <p className="text-[13px] text-ink-muted">{c.age} · {GENDER[c.gender]} · {c.city}</p>
                    </div>
                    <span className={`grid h-5 w-5 place-items-center rounded-full border transition ${active ? 'border-ink bg-ink text-white' : 'border-zinc-300 text-transparent'}`}><Icon name="check" className="h-3 w-3" /></span>
                  </div>
                  <p className="mt-3 truncate text-sm">{procedureName(c.procedure)}</p>
                  <div className="mt-1 flex items-center justify-between text-[13px] text-ink-muted">
                    <span className="truncate">{c.insurer}</span>
                    {c.emergency && <span className="text-rose-600">Emergency</span>}
                  </div>
                </button>
              )
            })}
          </div>
        </div>

        <div className="lg:sticky lg:top-8 lg:self-start">
          {selected ? (
            <Card key={selected.case_id} className="animate-scale-in overflow-hidden">
              <div className="border-b border-line bg-zinc-50/70 p-5">
                <div className="flex items-center gap-3">
                  <Avatar name={selected.patient} size="lg" />
                  <div>
                    <p className="text-lg font-semibold tracking-tight">{selected.patient}</p>
                    <p className="text-[13px] text-ink-muted">{selected.age} years · {GENDER[selected.gender]} · {selected.city}</p>
                  </div>
                </div>
              </div>
              <dl className="space-y-3 p-5 text-sm">
                <Row label="Condition" value={selected.title} />
                <Row label="Treatment" value={procedureName(selected.procedure)} />
                <Row label="Admission" value={`${selected.emergency ? 'Emergency' : 'Planned'} · ${selected.stay_days} day${selected.stay_days === 1 ? '' : 's'}`} />
                <Row label="Room" value={roomName(selected.room_category)} />
                <Row label="Insurer" value={`${selected.insurer} · ${selected.tpa_id}`} />
                <Row label="Policy" value={`${selected.product} · ${money(selected.sum_insured)}`} />
                <Row label="On file" value={`${selected.documents} documents · bill est. ${money(selected.estimated_bill)}`} />
              </dl>
              <div className="border-t border-line p-5">
                <p className="mb-3 text-[13px] font-medium text-ink-soft">What happens next</p>
                <ol className="mb-5 space-y-2 text-[13px] text-ink-muted">
                  {['Agents read the documents and code the diagnosis', 'They check the policy and draft the pre-auth', 'You review it before anything is sent'].map((step, i) => (
                    <li key={step} className="flex gap-2.5"><span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-zinc-100 text-[11px] font-medium text-ink-soft">{i + 1}</span>{step}</li>
                  ))}
                </ol>
                <Button variant="primary" size="lg" className="w-full" loading={starting} onClick={admit} iconRight="arrowRight">Admit and start claim</Button>
                <div className="mt-3"><ErrorNote error={error} /></div>
              </div>
            </Card>
          ) : (
            <div className="grid h-64 place-items-center rounded-xl border border-dashed border-zinc-300 text-center text-sm text-ink-muted">
              <div><Icon name="users" className="mx-auto mb-2 h-6 w-6 text-ink-faint" />Select a patient to see their admission</div>
            </div>
          )}
        </div>
      </div>
    </>
  )
}

const Row = ({ label, value }) => (
  <div className="flex justify-between gap-4"><dt className="text-ink-muted">{label}</dt><dd className="text-right font-medium">{value}</dd></div>
)
