import { Link, NavLink } from 'react-router-dom'
import Icon from './Icon'
import '../styles/app-shell.css'

const NAV = [
  { to: '/dashboard', label: 'Dashboard', icon: 'grid' },
  { to: '/verify', label: 'Verify invoice', icon: 'upload' },
  { to: '/results', label: 'Results', icon: 'file' },
  { to: '/history', label: 'History', icon: 'clock' },
]

function Sidebar() {
  return (
    <aside className="sidebar">
      <Link className="sidebar-brand" to="/dashboard" aria-label="InvoiceIQ dashboard">
        <span className="brand-mark"><Icon name="file" size={19} /></span>
        <span>Invoice<span className="brand-accent">IQ</span></span>
      </Link>
      <div className="sidebar-section-label">WORKSPACE</div>
      <nav className="sidebar-nav" aria-label="Main navigation">
        {NAV.map((item) => (
          <NavLink key={item.to} to={item.to}
            className={({ isActive }) => 'sidebar-link' + (isActive ? ' active' : '')}>
            <Icon name={item.icon} size={18} />
            <span>{item.label}</span>
          </NavLink>
        ))}
      </nav>
      <div className="sidebar-bottom">
        <div className="sidebar-status"><span className="status-dot" /> Frontend prototype</div>
        <NavLink to="/settings" className={({ isActive }) => 'sidebar-link settings-link' + (isActive ? ' active' : '')}>
          <Icon name="settings" size={18} /><span>Settings</span>
        </NavLink>
        <div className="sidebar-footnote">InvoiceIQ · Preview build</div>
      </div>
    </aside>
  )
}

export default Sidebar
