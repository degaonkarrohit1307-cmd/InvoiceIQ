import { motion } from 'motion/react'
import { useNavigate } from 'react-router-dom'
import ParticleBackground from '../components/ParticleBackground'
import Icon from '../components/Icon'
import '../styles/landing.css'

const PIPELINE = [
  { label: 'Invoice', detail: 'Document intake' },
  { label: 'Extraction', detail: 'Structured fields' },
  { label: 'Validation', detail: 'Arithmetic checks' },
  { label: 'Risk analysis', detail: 'Fraud signals' },
  { label: 'GST review', detail: 'Tax compliance' },
]

function Landing() {
  const navigate = useNavigate()
  return (
    <div className="landing">
      <ParticleBackground />
      <div className="landing-grid-overlay" aria-hidden="true" />
      <div className="landing-content">
        <nav className="landing-nav">
          <a href="/" className="landing-wordmark"><span className="landing-brand-mark"><Icon name="file" size={18} /></span>Invoice<span>IQ</span></a>
          <div className="landing-nav-right"><span className="landing-preview-dot" /> Prototype preview</div>
        </nav>

        <div className="landing-main">
          <motion.div className="landing-hero" initial={{ opacity: 0, y: 18 }} animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.65, ease: [0.16, 1, 0.3, 1] }}>
            <div className="hero-eyebrow"><span /> INTELLIGENT INVOICE CONTROL</div>
            <h1>Clarity behind<br /><span>every invoice.</span></h1>
            <p className="landing-sub">
              Bring invoice data, mathematical checks, fraud signals and GST compliance into one clear review workflow.
            </p>
            <div className="landing-actions">
              <button className="landing-cta" onClick={() => navigate('/dashboard')}>
                Open workspace <Icon name="arrow" size={17} />
              </button>
              <span className="landing-action-note">No backend connection required to explore the prototype.</span>
            </div>
            <div className="landing-proof-row">
              <span><Icon name="shield" size={15} /> Explainable checks</span>
              <span><Icon name="file" size={15} /> PDF & image intake</span>
              <span><Icon name="check" size={15} /> Review-led workflow</span>
            </div>
          </motion.div>

          <motion.div className="workflow-preview" initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.75, delay: 0.18 }}>
            <div className="preview-window">
              <div className="preview-window-top"><div className="window-dots"><i /><i /><i /></div><span>VERIFICATION WORKFLOW</span><span className="preview-window-state">PREVIEW</span></div>
              <div className="preview-window-body">
                <div className="preview-document">
                  <div className="preview-doc-icon"><Icon name="file" size={22} /></div>
                  <div><strong>Invoice document</strong><small>PDF · JPG · PNG</small></div>
                  <span className="preview-step-number">01</span>
                </div>
                <div className="preview-flow-line"><span /></div>
                <div className="preview-checks">
                  <div><span className="preview-check-icon"><Icon name="check" size={14} /></span><span><b>Field extraction</b><small>Supplier, date, totals</small></span></div>
                  <div><span className="preview-check-icon"><Icon name="check" size={14} /></span><span><b>Calculation review</b><small>Line items and tax</small></span></div>
                  <div><span className="preview-check-icon"><Icon name="shield" size={14} /></span><span><b>Risk & GST analysis</b><small>Signals and compliance</small></span></div>
                </div>
                <div className="preview-result"><span className="preview-result-mark"><Icon name="spark" size={17} /></span><div><b>Decision support</b><small>One review surface for finance teams</small></div><Icon name="arrow" size={17} /></div>
              </div>
            </div>
            <div className="preview-caption"><span className="caption-line" /> A connected view of the invoice review process</div>
          </motion.div>
        </div>

        <div className="landing-pipeline-wrap">
          <div className="landing-pipeline-heading">FROM DOCUMENT TO DECISION</div>
          <ol className="landing-pipeline">
            {PIPELINE.map((step, index) => (
              <li key={step.label}>
                <span className="pipeline-index">0{index + 1}</span>
                <span className="pipeline-copy"><b>{step.label}</b><small>{step.detail}</small></span>
                {index < PIPELINE.length - 1 && <span className="pipeline-connector" />}
              </li>
            ))}
          </ol>
        </div>
        <footer className="landing-footer"><span>© 2026 InvoiceIQ</span><span>AI-powered invoice verification & tax compliance</span><span>Frontend prototype</span></footer>
      </div>
    </div>
  )
}

export default Landing
