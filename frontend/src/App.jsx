import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import Landing from './pages/Landing'
import AppLayout from './components/AppLayout'
import Dashboard from './pages/Dashboard'
import VerifyInvoice from './pages/VerifyInvoice'
import VerificationResults from './pages/VerificationResults'
import History from './pages/History'
import Settings from './pages/Settings'

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route element={<AppLayout />}>
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/verify" element={<VerifyInvoice />} />
          <Route path="/results" element={<VerificationResults />} />
          <Route path="/history" element={<History />} />
          <Route path="/settings" element={<Settings />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}

export default App
