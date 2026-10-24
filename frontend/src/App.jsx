// Routes inside the app shell.
import { Navigate, Route, Routes } from 'react-router-dom'
import Layout from './components/Layout'
import { OfficerProvider, ToastProvider } from './components/ui'
import Admit from './pages/Admit'
import Dashboard from './pages/Dashboard'
import Insurer from './pages/Insurer'
import PatientFile from './pages/PatientFile'
import Patients from './pages/Patients'
import Tasks from './pages/Tasks'
import { AuthProvider, useAuth } from './auth'
import PatientPortal from './pages/PatientPortal'

export default function App() {
  return <AuthProvider><Workspace /></AuthProvider>
}

function Workspace() {
  const { user, logout } = useAuth()
  if (user.role === 'patient') return <PatientPortal />
  return (
    <OfficerProvider accountName={user.display_name}>
      <ToastProvider>
        <Layout>
          <div className="mb-5 flex items-center justify-between gap-3 text-xs text-ink-muted"><span>Signed in as {user.display_name} · Billing officer</span><button className="underline" onClick={() => logout()}>Sign out</button></div>
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/patients" element={<Patients />} />
            <Route path="/patients/admit" element={<Admit />} />
            <Route path="/patients/:claimId" element={<PatientFile />} />
            <Route path="/tasks" element={<Tasks />} />
            <Route path="/insurer" element={<Insurer />} />
            <Route path="/insurer/:claimId" element={<Insurer />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Layout>
      </ToastProvider>
    </OfficerProvider>
  )
}
