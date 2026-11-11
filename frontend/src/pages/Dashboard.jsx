import { Link } from 'react-router-dom'
import Icon from '../components/Icon'

const METRICS = [
  { label: 'Invoices processed', value: '0', note: 'No verified invoices yet', icon: 'file' },
  { label: 'High-risk flags', value: '0', note: 'No risk assessments yet', icon: 'alert' },
  { label: 'GST exceptions', value: '0', note: 'No compliance checks yet', icon: 'shield' },
  { label: 'Pending reviews', value: '0', note: 'Nothing awaiting review', icon: 'clock' },
]

const STEPS = [
  { number: '01', title: 'Upload an invoice', description: 'Select a PDF, JPG or PNG file.' },
  { number: '02', title: 'Extract and validate', description: 'Read fields and check invoice arithmetic.' },
  { number: '03', title: 'Review risk and GST', description: 'Inspect fraud signals and tax compliance.' },
]

function Dashboard() {
  return (
    <div className="page-stack">
      <div className="page-heading-row">
        <div className="page-header">
          <div className="eyebrow-light">OVERVIEW</div>
          <h1>Dashboard</h1>
          <p>Your invoice verification workspace at a glance.</p>
        </div>
        <Link to="/verify" className="button-primary"><Icon name="plus" size={17} /> Verify an invoice</Link>
      </div>

      <div className="dashboard-notice"><span className="notice-symbol"><Icon name="alert" size={16} /></span>
        <div><strong>Prototype workspace</strong><span>Backend processing is not connected. Metrics remain at zero until real invoice verification is integrated.</span></div>
      </div>

      <section className="metric-grid" aria-label="Invoice activity summary">
        {METRICS.map((metric) => (
          <article className="metric-card" key={metric.label}>
            <div className="metric-card-top"><span>{metric.label}</span><span className="metric-icon"><Icon name={metric.icon} size={17} /></span></div>
            <div className="metric-value">{metric.value}</div>
            <div className="metric-footnote">{metric.note}</div>
          </article>
        ))}
      </section>

      <div className="dashboard-columns">
        <section className="panel activity-panel">
          <div className="panel-heading"><div><h2>Recent activity</h2><p>Your latest invoice reviews will appear here.</p></div><Link to="/history" className="text-link">View history <Icon name="arrow" size={15} /></Link></div>
          <div className="activity-empty"><div className="empty-illustration"><Icon name="file" size={25} /></div><h3>No activity to show</h3><p>Once invoices are processed, their verification status and review history will appear here.</p><Link to="/verify" className="button-secondary">Choose an invoice</Link></div>
        </section>

        <section className="panel workflow-panel">
          <div className="panel-heading"><div><h2>How InvoiceIQ works</h2><p>A transparent review workflow.</p></div><span className="panel-icon"><Icon name="spark" size={17} /></span></div>
          <div className="workflow-steps">
            {STEPS.map((step, index) => <div className="workflow-step" key={step.number}>
              <span className="workflow-step-number">{step.number}</span><div><b>{step.title}</b><p>{step.description}</p></div>
              {index < STEPS.length - 1 && <span className="workflow-step-rail" />}
            </div>)}
          </div>
          <div className="workflow-panel-foot"><Icon name="shield" size={16} /><span>Designed to support human review, not replace it.</span></div>
        </section>
      </div>
    </div>
  )
}

export default Dashboard
