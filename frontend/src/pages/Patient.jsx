// Patient view: what the patient will pay, in plain words, and which room saves them money.
import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, useLoad } from '../api'
import { Card, ErrorNote, money } from '../components/ui'

export default function Patient() {
  const { claimId } = useParams()
  const navigate = useNavigate()
  const claims = useLoad(api.claims)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    setResult(null)
    setError(null)
    if (claimId) api.patientNote(claimId).then(setResult, (err) => setError(err.message))
  }, [claimId])

  const note = result?.note
  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Patient view</h1>
          <p className="text-sm text-slate-500">The cost note staff check before handing it to the patient.</p>
        </div>
        <select value={claimId ?? ''} onChange={(e) => navigate(`/patient/${e.target.value}`)} className="rounded-md px-2 py-1.5 text-sm ring-1 ring-slate-300">
          <option value="" disabled>Choose a claim</option>
          {(claims.data ?? []).map((c) => <option key={c.claim_id} value={c.claim_id}>{c.patient} · {c.claim_id}</option>)}
        </select>
      </div>
      <ErrorNote error={error} />
      {claimId && !note && !error && <p className="text-sm text-slate-500">Working out the numbers…</p>}
      {note && (
        <>
          <Card>
            <p className="text-lg leading-relaxed text-slate-800">{note.note}</p>
            {note.recommended && (
              <p className="mt-4 rounded-md bg-emerald-50 px-3 py-2 text-emerald-900">
                Switch to a <b>{note.recommended.room.toLowerCase()}</b> and save about <b>{money(note.recommended.saving)}</b>.
              </p>
            )}
            {result.output.next === 'human_review' && (
              <p className="mt-4 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-900">A member of staff must talk this through with the patient before it is shared.</p>
            )}
          </Card>
          {note.options.length > 0 && (
            <Card title="Every room, priced for this stay">
              <table className="w-full text-left text-sm">
                <thead className="text-xs uppercase text-slate-500">
                  <tr><th className="py-1 font-medium">Room</th><th className="py-1 text-right font-medium">Per day</th><th className="py-1 text-right font-medium">You pay</th><th className="py-1 text-right font-medium">Insurer pays</th><th className="py-1 text-right font-medium">Saving</th></tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {note.options.map((o) => {
                    const best = note.recommended && o.room === note.recommended.room
                    return (
                      <tr key={o.room} className={best ? 'bg-emerald-50' : o.current ? 'bg-slate-50' : ''}>
                        <td className="py-1.5 pr-2">{o.room}{o.current && <span className="ml-2 text-xs text-slate-500">(current)</span>}{best && <span className="ml-2 text-xs font-medium text-emerald-700">recommended</span>}</td>
                        <td className="py-1.5 text-right tabular-nums">{money(o.rate)}</td>
                        <td className="py-1.5 text-right font-medium tabular-nums">{money(o.patient_pays)}</td>
                        <td className="py-1.5 text-right tabular-nums">{money(o.insurer_pays)}</td>
                        <td className={`py-1.5 text-right tabular-nums ${o.saving > 0 ? 'text-emerald-700' : o.saving < 0 ? 'text-red-700' : 'text-slate-400'}`}>
                          {o.current ? '—' : o.saving >= 0 ? money(o.saving) : `−${money(-o.saving)}`}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
              <p className="mt-3 text-xs text-slate-500">All amounts from the rules engine, applying this patient&apos;s policy to the same stay in each room.</p>
            </Card>
          )}
        </>
      )}
    </div>
  )
}
