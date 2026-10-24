// Small shared pieces: money, state badges, findings, buttons.
import { createContext, useContext, useState } from 'react'

const rupees = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 })
export const money = (amount) => (amount === null || amount === undefined ? '—' : rupees.format(amount))
export const label = (slug) => (slug ? slug.replaceAll('_', ' ').replace(/^\w/, (c) => c.toUpperCase()) : '')

// Where each state sits on the board, and its colour.
export const STATE_GROUPS = [
  { name: 'Admission', states: ['ADMITTED', 'PREAUTH_DRAFT', 'RISK_CHECK'], tone: 'slate' },
  { name: 'Needs a person', states: ['AWAITING_HUMAN'], tone: 'amber' },
  { name: 'With the insurer', states: ['SUBMITTED', 'QUERY_RAISED'], tone: 'sky' },
  { name: 'Decided', states: ['APPROVED', 'PARTIALLY_APPROVED', 'DENIED'], tone: 'violet' },
  { name: 'In treatment', states: ['IN_TREATMENT', 'DISCHARGE_DRAFT'], tone: 'teal' },
  { name: 'Final and settled', states: ['FINAL_APPROVED', 'SETTLED', 'CLOSED'], tone: 'emerald' },
]

const TONES = {
  slate: 'bg-slate-100 text-slate-700 ring-slate-200',
  amber: 'bg-amber-50 text-amber-800 ring-amber-200',
  sky: 'bg-sky-50 text-sky-800 ring-sky-200',
  violet: 'bg-violet-50 text-violet-800 ring-violet-200',
  teal: 'bg-teal-50 text-teal-800 ring-teal-200',
  emerald: 'bg-emerald-50 text-emerald-800 ring-emerald-200',
  red: 'bg-red-50 text-red-800 ring-red-200',
}

export function StateBadge({ state }) {
  const tone = state === 'DENIED' ? 'red' : STATE_GROUPS.find((g) => g.states.includes(state))?.tone ?? 'slate'
  return <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ring-1 ${TONES[tone]}`}>{label(state)}</span>
}

export function RiskBadge({ risk }) {
  if (risk === null || risk === undefined) return null
  const tone = risk >= 0.5 ? 'red' : risk >= 0.2 ? 'amber' : 'emerald'
  return <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ring-1 ${TONES[tone]}`}>Risk {Math.round(risk * 100)}%</span>
}

export function Card({ title, actions, children, className = '' }) {
  return (
    <section className={`rounded-lg border border-slate-200 bg-white ${className}`}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-2 border-b border-slate-100 px-4 py-2.5">
          <h2 className="text-sm font-semibold text-slate-700">{title}</h2>
          {actions}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  )
}

export function Button({ variant = 'secondary', className = '', ...props }) {
  const styles = {
    primary: 'bg-indigo-600 text-white hover:bg-indigo-500 disabled:bg-indigo-300',
    secondary: 'bg-white text-slate-700 ring-1 ring-slate-300 hover:bg-slate-50 disabled:text-slate-400',
    danger: 'bg-white text-red-700 ring-1 ring-red-300 hover:bg-red-50 disabled:text-red-300',
  }
  return <button className={`rounded-md px-3 py-1.5 text-sm font-medium ${styles[variant]} ${className}`} {...props} />
}

// One agent finding: the issue, what backs it, and where it came from.
export function Findings({ findings }) {
  if (!findings?.length) return null
  return (
    <ul className="space-y-1.5">
      {findings.map((f, i) => (
        <li key={i} className="text-sm">
          <span className="font-medium text-slate-800">{f.issue}</span>
          <span className="text-slate-600">: {f.evidence}</span>
          <span className="ml-2 inline-block rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-600">{f.source}</span>
        </li>
      ))}
    </ul>
  )
}

export function ErrorNote({ error }) {
  if (!error) return null
  return <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-800">{error}</p>
}

// The billing officer's name, used on every decision. Kept for this browser only.
const OfficerContext = createContext(['Billing Officer', () => {}])
export function OfficerProvider({ children }) {
  const [officer, setOfficer] = useState(() => {
    try {
      return localStorage.getItem('officer') || 'Billing Officer'
    } catch {
      return 'Billing Officer'
    }
  })
  const update = (name) => {
    setOfficer(name)
    try {
      localStorage.setItem('officer', name)
    } catch {
      /* private window: keep it for this session only */
    }
  }
  return <OfficerContext.Provider value={[officer, update]}>{children}</OfficerContext.Provider>
}
export const useOfficer = () => useContext(OfficerContext)
