// The app shell: navigation, the officer's name, and the five screens.
import { NavLink, Route, Routes } from 'react-router-dom'
import { OfficerProvider, useOfficer } from './components/ui'
import Board from './pages/Board'
import ClaimDetail from './pages/ClaimDetail'
import Insurer from './pages/Insurer'
import Patient from './pages/Patient'
import Queue from './pages/Queue'

const NAV = [
  ['/', 'Claims board'],
  ['/queue', 'Approval queue'],
  ['/insurer', 'Insurer console'],
  ['/patient', 'Patient view'],
]

export default function App() {
  return (
    <OfficerProvider>
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-screen-2xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
          <span className="text-lg font-semibold tracking-tight">ClaimPilot</span>
          <nav className="flex flex-wrap gap-1">
            {NAV.map(([to, name]) => (
              <NavLink key={to} to={to} end={to === '/'}
                className={({ isActive }) => `rounded-md px-3 py-1.5 text-sm ${isActive ? 'bg-slate-900 text-white' : 'text-slate-600 hover:bg-slate-100'}`}>
                {name}
              </NavLink>
            ))}
          </nav>
          <Officer />
        </div>
      </header>
      <main className="mx-auto max-w-screen-2xl px-4 py-6">
        <Routes>
          <Route path="/" element={<Board />} />
          <Route path="/claims/:claimId" element={<ClaimDetail />} />
          <Route path="/queue" element={<Queue />} />
          <Route path="/insurer" element={<Insurer />} />
          <Route path="/patient" element={<Patient />} />
          <Route path="/patient/:claimId" element={<Patient />} />
        </Routes>
      </main>
    </OfficerProvider>
  )
}

function Officer() {
  const [officer, setOfficer] = useOfficer()
  return (
    <label className="ml-auto flex items-center gap-2 text-sm text-slate-500">
      Billing officer
      <input value={officer} onChange={(e) => setOfficer(e.target.value)} className="w-40 rounded-md px-2 py-1 text-slate-900 ring-1 ring-slate-300" />
    </label>
  )
}
