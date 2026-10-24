// Insurer console: the simulated TPA's side of each claim, as the message thread on the exchange.
import { useState } from 'react'
import { api, useLoad, useTrace } from '../api'
import { MessageBody } from '../components/claim'
import { Card, ErrorNote, StateBadge, label, money } from '../components/ui'

export default function Insurer() {
  const claims = useLoad(api.claims)
  const [selected, setSelected] = useState(null)
  const detail = useLoad(() => (selected ? api.claim(selected) : Promise.resolve(null)), [selected])
  useTrace({
    onEvent: (event) => {
      claims.refresh()
      if (event.claim_id === selected) detail.refresh()
    },
  })
  const sent = (claims.data ?? []).filter((c) => !['ADMITTED', 'PREAUTH_DRAFT', 'RISK_CHECK'].includes(c.state))

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Insurer console</h1>
        <p className="text-sm text-slate-500">The simulated TPA&apos;s view: what each hospital sent, and what the insurer answered. The two sides talk only through these messages.</p>
      </div>
      <ErrorNote error={claims.error} />
      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Claims">
          <ul className="space-y-1">
            {sent.map((c) => (
              <li key={c.claim_id}>
                <button onClick={() => setSelected(c.claim_id)}
                  className={`w-full rounded-md px-2 py-1.5 text-left text-sm ${selected === c.claim_id ? 'bg-indigo-50 ring-1 ring-indigo-200' : 'hover:bg-slate-50'}`}>
                  <span className="font-medium">{c.claim_id}</span> · {c.tpa_id}
                  <span className="ml-2"><StateBadge state={c.state} /></span>
                  <span className="block text-xs text-slate-500">{label(c.procedure)} · approved {money(c.approved)}</span>
                </button>
              </li>
            ))}
            {!sent.length && <li className="text-sm text-slate-500">No claims have reached the insurer yet.</li>}
          </ul>
        </Card>
        <div className="space-y-3 lg:col-span-2">
          {!selected && <p className="text-sm text-slate-500">Pick a claim to see the thread.</p>}
          {detail.data?.thread.map((m, i) => (
            <div key={i} className={`max-w-[85%] rounded-lg border p-3 ${m.sender === 'insurer' ? 'ml-auto border-sky-200 bg-sky-50/50' : 'border-slate-200 bg-white'}`}>
              <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
                {m.sender === 'insurer' ? `${detail.data.tpa_id} (insurer)` : 'Hospital'} · {m.type} · {label(m.stage)} · round {m.round}
              </p>
              <MessageBody message={m} />
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
