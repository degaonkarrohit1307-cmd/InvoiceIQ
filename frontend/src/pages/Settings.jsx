import { useState } from 'react'
import Icon from '../components/Icon'

function Settings() {
  const [currency, setCurrency] = useState('INR')
  const [notifications, setNotifications] = useState(false)
  const [saved, setSaved] = useState(false)

  const update = (setter) => (value) => { setter(value); setSaved(false) }

  return (
    <div className="page-stack">
      <div className="page-header"><div className="eyebrow-light">WORKSPACE</div><h1>Settings</h1><p>Manage display preferences and review connection status.</p></div>

      <section className="panel settings-panel">
        <div className="settings-section-heading"><div className="settings-heading-icon"><Icon name="building" size={18} /></div><div><h2>Workspace preferences</h2><p>These settings affect the local prototype interface only.</p></div></div>
        <div className="preference-row"><div><strong>Default currency</strong><p>Display preference for invoice amounts when data is available.</p></div><select value={currency} onChange={(event) => update(setCurrency)(event.target.value)} aria-label="Default currency">
          <option value="INR">INR — Indian Rupee</option><option value="USD">USD — US Dollar</option><option value="EUR">EUR — Euro</option><option value="GBP">GBP — Pound Sterling</option>
        </select></div>
        <div className="preference-row"><div><strong>Email notifications</strong><p>Preference control preview. No email service is connected.</p></div><label className="toggle-control"><input type="checkbox" checked={notifications} onChange={(event) => update(setNotifications)(event.target.checked)} /><span className="toggle-track" /><span className="visually-hidden">Enable email notifications</span></label></div>
        <div className="settings-save-row"><span className="settings-save-note">{saved ? 'Preferences saved for this page session.' : 'Changes are held in this browser session only.'}</span><button type="button" className="button-primary" onClick={() => setSaved(true)}>Save preferences</button></div>
      </section>

      <section className="panel connection-panel">
        <div className="settings-section-heading"><div className="settings-heading-icon"><Icon name="shield" size={18} /></div><div><h2>Application connections</h2><p>Service connection status for this frontend prototype.</p></div></div>
        <div className="connection-row"><div className="connection-logo"><Icon name="building" size={19} /></div><div className="connection-copy"><strong>InvoiceIQ backend API</strong><span>Invoice processing, verification and records</span></div><span className="connection-state"><i /> Not connected</span></div>
        <div className="connection-row"><div className="connection-logo"><Icon name="file" size={19} /></div><div className="connection-copy"><strong>Document processing</strong><span>OCR and invoice field extraction</span></div><span className="connection-state"><i /> Not connected</span></div>
        <div className="connection-row"><div className="connection-logo"><Icon name="shield" size={19} /></div><div className="connection-copy"><strong>GST verification service</strong><span>GSTIN and tax compliance checks</span></div><span className="connection-state"><i /> Not connected</span></div>
      </section>
      <div className="settings-disclaimer"><Icon name="alert" size={16} /><span>Connection indicators are informational. No API requests, emails or external service calls are made by this frontend prototype.</span></div>
    </div>
  )
}

export default Settings
