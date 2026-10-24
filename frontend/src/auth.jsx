import { createContext, useContext, useEffect, useState } from 'react'
import { request } from './api'
import { Button, Icon } from './components/ui'

const Auth = createContext(null)
export const useAuth = () => useContext(Auth)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    request('GET', '/api/auth/me').then(setUser).catch(() => {}).finally(() => setLoading(false))
    const expire = () => setUser(null)
    window.addEventListener('session-expired', expire)
    return () => window.removeEventListener('session-expired', expire)
  }, [])
  const logout = async () => { await request('POST', '/api/auth/logout'); setUser(null) }
  if (loading) return <div className="grid h-full place-items-center text-ink-muted">Opening your workspace…</div>
  return <Auth.Provider value={{ user, setUser, logout }}>{user ? children : <Login />}</Auth.Provider>
}

function Login() {
  const { setUser } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [mode, setMode] = useState('patient')
  async function submit(e) {
    e.preventDefault(); setBusy(true); setError('')
    try { setUser(await request('POST', '/api/auth/login', { username, password })) }
    catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  return <div className="min-h-full bg-[#f4f5f1] px-6 py-10 lg:py-20">
    <div className="mx-auto grid max-w-5xl overflow-hidden rounded-3xl border border-line bg-white shadow-card md:grid-cols-2">
      <section className="flex flex-col justify-between bg-[#163d37] p-10 text-white">
        <div><div className="mb-16 flex items-center gap-2 text-xl font-semibold"><Icon name="logo" /> ClaimPilot</div>
          <p className="text-xs uppercase tracking-widest text-emerald-200">Your care. Your cover. In view.</p>
          <h1 className="mt-5 text-4xl font-semibold leading-tight">A clearer picture of your claim.</h1>
          <p className="mt-6 leading-relaxed text-white/75">Follow hospital-reviewed updates, understand your insurance, and send documents directly to your billing team.</p></div>
        <p className="mt-12 text-xs text-white/60">Synthetic demonstration · No real patient information</p>
      </section>
      <section className="p-8 md:p-10">
        <div className="mb-8 flex rounded-lg bg-zinc-100 p-1">{['patient', 'officer'].map(x => <button key={x} onClick={() => { setMode(x); setError('') }} className={`flex-1 rounded-md px-4 py-2 text-sm ${mode === x ? 'bg-white shadow-sm' : 'text-ink-muted'}`}>{x === 'patient' ? 'Patient account' : 'Billing officer'}</button>)}</div>
        <h2 className="text-2xl font-semibold">Welcome back</h2><p className="mt-2 text-sm text-ink-muted">Sign in to your {mode === 'patient' ? 'personal dashboard' : 'hospital workspace'}.</p>
        <form onSubmit={submit} className="mt-8 space-y-5">
          <label className="block text-sm">Email<input className="input mt-2" type="email" autoComplete="username" required value={username} onChange={e => setUsername(e.target.value)} /></label>
          <label className="block text-sm">Password<input className="input mt-2" type="password" autoComplete="current-password" required value={password} onChange={e => setPassword(e.target.value)} /></label>
          {error && <p role="alert" className="text-sm text-rose-700">{error}</p>}
          <Button type="submit" variant="primary" className="w-full" disabled={busy}>{busy ? 'Signing in…' : 'Sign in securely'}</Button>
        </form>
        <div className="mt-8 rounded-xl border border-line bg-canvas p-4 text-xs text-ink-muted">
          <p className="font-semibold text-ink">Try a synthetic account</p>
          <p className="mt-2 break-all">{mode === 'patient' ? 'ramesh@claimpilot.demo' : 'officer@claimpilot.demo'}</p>
          <p className="mt-1">{mode === 'patient' ? 'PatientDemo!2026' : 'OfficerDemo!2026'}</p>
          <button type="button" className="mt-3 font-medium text-accent-700 underline" onClick={() => { setUsername(mode === 'patient' ? 'ramesh@claimpilot.demo' : 'officer@claimpilot.demo'); setPassword(mode === 'patient' ? 'PatientDemo!2026' : 'OfficerDemo!2026') }}>Fill demo credentials</button>
        </div>
      </section>
    </div>
  </div>
}
