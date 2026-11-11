import { motion } from 'motion/react'
import { Outlet, useLocation, Link } from 'react-router-dom'
import Sidebar from './Sidebar'
import Icon from './Icon'
import '../styles/app-shell.css'

const PAGE_NAMES = {
  '/dashboard': 'Dashboard',
  '/verify': 'Verify invoice',
  '/results': 'Verification results',
  '/history': 'History',
  '/settings': 'Settings',
}

function AppLayout() {
  const location = useLocation()
  const pageName = PAGE_NAMES[location.pathname] || 'Workspace'

  return (
    <div className="app-shell">
      <Sidebar />
      <div className="app-main">
        <header className="topbar">
          <div className="topbar-context">
            <span className="topbar-overline">INVOICEIQ WORKSPACE</span>
            <span className="topbar-page"><Icon name="chevron" size={14} /> {pageName}</span>
          </div>
          <div className="topbar-right">
            <span className="prototype-pill"><span className="status-dot" /> Prototype mode</span>
            <div className="profile-avatar" aria-label="Workspace profile">IQ</div>
          </div>
        </header>
        <main className="app-content">
          <motion.div key={location.pathname} initial={{ opacity: 0, y: 7 }}
            animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.22, ease: 'easeOut' }}>
            <Outlet />
          </motion.div>
          <footer className="app-footer">
            <span>InvoiceIQ</span><span>Secure invoice intelligence · Prototype</span>
            <Link to="/settings">Workspace settings</Link>
          </footer>
        </main>
      </div>
    </div>
  )
}

export default AppLayout
