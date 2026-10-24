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

export default function App() {
  return (
    <OfficerProvider>
      <ToastProvider>
        <Layout>
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
