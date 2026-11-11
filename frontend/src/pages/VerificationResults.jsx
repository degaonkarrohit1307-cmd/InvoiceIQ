
import { useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import Icon from '../components/Icon'
import { getInvoiceAnalysis } from '../services/api'

const TABS = [
  { id: 'extracted', label: 'Extracted fields', title: 'Structured invoice data', description: 'Extracted invoice information and financial details.' },
  { id: 'math', label: 'Math checks', title: 'Mathematical verification', description: 'Invoice calculation checks and reconciliation results.' },
  { id: 'fraud', label: 'Fraud risk', title: 'Fraud and anomaly signals', description: 'Model-based fraud indicators and risk assessment.' },
  { id: 'gst', label: 'GST compliance', title: 'GST compliance review', description: 'GST validation results and compliance exceptions.' },
  { id: 'shap', label: 'SHAP explanation', title: 'Model explanation', description: 'Feature-level contributions influencing the fraud prediction.' },
]

function displayValue(value) {
  if (value === null || value === undefined || value === '') return '—'
  return String(value)
}

function formatScore(value) {
  if (value === null || value === undefined) return '—'
  return `${Number(value).toFixed(2)}`
}

function formatPercent(value) {
  if (value === null || value === undefined) return '—'
  return `${(Number(value) * 100).toFixed(2)}%`
}

function StatusBadge({ value }) {
  const normalized = String(value || 'UNKNOWN').toUpperCase()

  const className =
    normalized === 'PASS' || normalized === 'COMPLETED' || normalized === 'AVAILABLE'
      ? 'badge-success'
      : normalized === 'FAIL' || normalized === 'ERROR' || normalized === 'HIGH'
        ? 'badge-danger'
        : 'badge-neutral'

  return <span className={className}>{normalized}</span>
}

function DetailGrid({ items }) {
  return (
    <div className="analysis-detail-grid">
      {items.map(([label, value]) => (
        <div className="detail-field" key={label}>
          <span>{label}</span>
          <strong>{displayValue(value)}</strong>
        </div>
      ))}
    </div>
  )
}

function VerificationResults() {
  const location = useLocation()
  const { invoiceId, extraction } = location.state || {}

  const [active, setActive] = useState('extracted')
  const [analysis, setAnalysis] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!invoiceId) {
      setAnalysis(null)
      setError('')
      setLoading(false)
      return
    }

    let cancelled = false

    async function loadAnalysis() {
      setLoading(true)
      setError('')
      setAnalysis(null)

      try {
        const result = await getInvoiceAnalysis(invoiceId)

        if (!cancelled) {
          setAnalysis(result)
        }
      } catch (requestError) {
        if (!cancelled) {
          setError(
            requestError.message || 'Unable to retrieve invoice analysis.'
          )
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    loadAnalysis()

    return () => {
      cancelled = true
    }
  }, [invoiceId])

  const selectedTab = TABS.find((tab) => tab.id === active) || TABS[0]

  const shap = analysis?.shap_explanation
  const contributions = shap?.contributions || []
  const gstChecks = analysis?.gst_compliance_result?.checks || []
  const mathChecks = extraction?.math_verification_details || []

  return (
    <div className="page-stack">

      <div className="page-header">
        <div className="eyebrow-light">REVIEW CENTRE</div>
        <h1>Verification results</h1>
        <p>Inspect extracted information and the evidence behind each verification check.</p>
      </div>

      {!invoiceId ? (
        <div className="results-status-banner">
          <span className="results-status-icon">
            <Icon name="clock" size={18} />
          </span>
          <div>
            <strong>No invoice selected</strong>
            <span>Upload and process an invoice before viewing its analysis.</span>
          </div>
          <span className="badge-neutral">Awaiting data</span>
        </div>
      ) : loading ? (
        <div className="results-status-banner">
          <span className="results-status-icon">
            <Icon name="clock" size={18} />
          </span>
          <div>
            <strong>Loading invoice analysis</strong>
            <span>Retrieving fraud, GST and SHAP results from the backend.</span>
          </div>
          <span className="badge-neutral">Loading</span>
        </div>
      ) : error ? (
        <div className="inline-alert error-alert" role="alert">
          <Icon name="alert" size={17} />
          <span>{error}</span>
        </div>
      ) : analysis ? (
        <div className="results-status-banner">
          <span className="results-status-icon">
            <Icon name="check" size={18} />
          </span>
          <div>
            <strong>Invoice analysis retrieved</strong>
            <span>
              Invoice ID: {invoiceId} · Analysis version: {displayValue(analysis.analysis_version)}
            </span>
          </div>
          <StatusBadge value={analysis.verification_status} />
        </div>
      ) : null}

      <section className="panel results-panel">

        <div className="panel-heading">
          <div>
            <h2>Verification breakdown</h2>
            <p>Explore the evidence returned by the InvoiceIQ analysis workflow.</p>
          </div>
          <span className="results-reference">
            {invoiceId ? `INVOICE — ${invoiceId.slice(0, 8)}` : 'INVOICE — NOT SELECTED'}
          </span>
        </div>

        <div className="results-tabs" role="tablist" aria-label="Verification result categories">
          {TABS.map((tab, index) => (
            <button
              key={tab.id}
              type="button"
              role="tab"
              id={`tab-${tab.id}`}
              aria-controls="results-tab-panel"
              aria-selected={active === tab.id}
              className={'results-tab' + (active === tab.id ? ' active' : '')}
              onClick={() => setActive(tab.id)}
            >
              <span className="results-tab-index">0{index + 1}</span>
              {tab.label}
            </button>
          ))}
        </div>

        <div
          className="results-tab-panel"
          id="results-tab-panel"
          role="tabpanel"
          aria-labelledby={`tab-${active}`}
        >
          <h3>{selectedTab.title}</h3>
          <p>{selectedTab.description}</p>

          {!invoiceId ? (
            <span className="result-empty-state">
              <span /> No invoice data available
            </span>
          ) : loading ? (
            <span className="result-empty-state">
              <span /> Loading analysis data...
            </span>
          ) : error ? (
            <span className="result-empty-state">
              <span /> Analysis data could not be retrieved.
            </span>
          ) : !analysis ? (
            <span className="result-empty-state">
              <span /> No analysis data available.
            </span>
          ) : (
            <div className="analysis-content">

              {active === 'extracted' && (
                <>
                  {extraction ? (
                    <>
                      <h4>Invoice information</h4>
                      <DetailGrid
                        items={[
                          ['Invoice number', extraction.invoice_number],
                          ['Invoice date', extraction.invoice_date],
                          ['Due date', extraction.due_date],
                          ['Supplier', extraction.supplier_name],
                          ['Supplier GSTIN', extraction.supplier_gstin],
                          ['Buyer', extraction.buyer_name],
                          ['Buyer GSTIN', extraction.buyer_gstin],
                          ['Currency', extraction.currency],
                        ]}
                      />

                      <h4>Financial summary</h4>
                      <DetailGrid
                        items={[
                          ['Subtotal', extraction.subtotal],
                          ['CGST', extraction.cgst],
                          ['SGST', extraction.sgst],
                          ['IGST', extraction.igst],
                          ['Total tax', extraction.total_tax],
                          ['Grand total', extraction.total_amount],
                        ]}
                      />
                    </>
                  ) : (
                    <p>Extraction details were not passed to this page.</p>
                  )}
                </>
              )}

              {active === 'math' && (
                <>
                  <h4>Mathematical verification status</h4>
                  <StatusBadge value={extraction?.math_verification_status} />

                  {mathChecks.length > 0 ? (
                    <div className="analysis-table-wrapper">
                      <table className="analysis-table">
                        <thead>
                          <tr>
                            <th>Rule</th>
                            <th>Result</th>
                            <th>Expected</th>
                            <th>Actual</th>
                            <th>Difference</th>
                          </tr>
                        </thead>
                        <tbody>
                          {mathChecks.map((check, index) => (
                            <tr key={`${check.rule}-${index}`}>
                              <td>{check.rule}</td>
                              <td><StatusBadge value={check.result} /></td>
                              <td>{displayValue(check.expected)}</td>
                              <td>{displayValue(check.actual)}</td>
                              <td>{displayValue(check.difference)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <p>No mathematical verification details were passed to this page.</p>
                  )}
                </>
              )}

              {active === 'fraud' && (
                <>
                  <h4>Fraud risk assessment</h4>
                  <DetailGrid
                    items={[
                      ['Risk category', analysis.fraud_risk_category],
                      ['Overall fraud score', analysis.fraud_score != null ? `${formatScore(analysis.fraud_score)}/100` : '—'],
                      ['Supervised score', analysis.fraud_supervised_score != null ? `${formatScore(analysis.fraud_supervised_score)}/100` : '—'],
                      ['Anomaly score', analysis.fraud_anomaly_score != null ? `${formatScore(analysis.fraud_anomaly_score)}/100` : '—'],
                      ['Mathematical score', analysis.fraud_mathematical_score != null ? `${formatScore(analysis.fraud_mathematical_score)}/100` : '—'],
                      ['Model type', analysis.model_type],
                      ['Predicted label', analysis.model_prediction?.predicted_label],
                      ['Predicted fraud probability', formatPercent(analysis.model_prediction?.predicted_probability)],
                    ]}
                  />

                  <p className="analysis-note">
                    These are model-generated risk indicators, not proof of fraudulent activity.
                  </p>

                  {analysis.key_signals?.length > 0 && (
                    <>
                      <h4>Key signals</h4>
                      <ul className="analysis-signal-list">
                        {analysis.key_signals.map((signal, index) => (
                          <li key={index}>{signal}</li>
                        ))}
                      </ul>
                    </>
                  )}
                </>
              )}

              {active === 'gst' && (
                <>
                  <h4>GST compliance overview</h4>
                  <StatusBadge value={analysis.gst_compliance_overall} />

                  {gstChecks.length > 0 ? (
                    <div className="analysis-table-wrapper">
                      <table className="analysis-table">
                        <thead>
                          <tr>
                            <th>Rule</th>
                            <th>Result</th>
                            <th>Details</th>
                          </tr>
                        </thead>
                        <tbody>
                          {gstChecks.map((check, index) => (
                            <tr key={`${check.rule}-${index}`}>
                              <td>{check.rule}</td>
                              <td><StatusBadge value={check.result} /></td>
                              <td>{displayValue(check.detail)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <p>No GST compliance checks were returned.</p>
                  )}

                  {analysis.gst_external_verification && (
                    <>
                      <h4>External GST verification</h4>
                      <pre className="analysis-json">
                        {JSON.stringify(analysis.gst_external_verification, null, 2)}
                      </pre>
                    </>
                  )}

                  <p className="analysis-note">
                    PASS does not necessarily mean every GST rule was evaluated.
                    Check individual NOT_CHECKABLE results and external verification status.
                  </p>
                </>
              )}

              {active === 'shap' && (
                <>
                  <h4>SHAP explanation status</h4>
                  <StatusBadge value={analysis.shap_status} />

                  {shap?.status === 'AVAILABLE' && contributions.length > 0 ? (
                    <>
                      <p>{shap.detail}</p>

                      <div className="analysis-detail-grid">
                        <div className="detail-field">
                          <span>Model type</span>
                          <strong>{displayValue(shap.model_type)}</strong>
                        </div>
                        <div className="detail-field">
                          <span>Predicted probability</span>
                          <strong>{formatPercent(shap.predicted_probability)}</strong>
                        </div>
                        <div className="detail-field">
                          <span>Reconstructed probability</span>
                          <strong>{formatPercent(shap.reconstructed_probability)}</strong>
                        </div>
                        <div className="detail-field">
                          <span>Base value</span>
                          <strong>{formatScore(shap.base_value)}</strong>
                        </div>
                      </div>

                      <h4>Top contributing features</h4>

                      <div className="analysis-shap-list">
                        {contributions.map((item, index) => {
                          const positive = Number(item.shap_value) > 0

                          return (
                            <div className="analysis-shap-item" key={`${item.feature}-${index}`}>
                              <div className="analysis-shap-heading">
                                <strong>{item.feature.replace(/_/g, ' ')}</strong>
                                <span className={positive ? 'shap-positive' : 'shap-negative'}>
                                  {positive ? '+' : ''}
                                  {Number(item.shap_value).toFixed(4)}
                                </span>
                              </div>

                              <div className="analysis-shap-track">
                                <div
                                  className={`analysis-shap-bar ${positive ? 'positive' : 'negative'}`}
                                  style={{
                                    width: `${Math.max(
                                      3,
                                      Math.min(
                                        100,
                                        (Math.abs(Number(item.shap_value)) /
                                          Math.max(
                                            ...contributions.map((entry) =>
                                              Math.abs(Number(entry.shap_value))
                                            ),
                                            1
                                          )) * 100
                                      )
                                    )}%`,
                                  }}
                                />
                              </div>

                              <span className="analysis-shap-feature-value">
                                Feature value: {displayValue(item.feature_value)}
                              </span>
                              <span className="analysis-shap-direction">
                                {positive
                                  ? 'Pushes the prediction towards the fraud class'
                                  : 'Pushes the prediction away from the fraud class'}
                              </span>
                            </div>
                          )
                        })}
                      </div>

                      <p className="analysis-note">
                        SHAP values explain the model prediction. Positive and negative
                        contributions show their direction of influence. They do not
                        establish that an invoice is fraudulent.
                      </p>
                    </>
                  ) : (
                    <p>
                      SHAP explanation is not available for this analysis.
                      Status: {displayValue(analysis.shap_status)}
                    </p>
                  )}
                </>
              )}

            </div>
          )}
        </div>
      </section>

      <div className="results-next-step">
        <div>
          <strong>Continue reviewing invoices</strong>
          <span>Return to invoice upload or begin another review.</span>
        </div>
        <Link to="/verify" className="button-primary">
          Choose invoice <Icon name="arrow" size={16} />
        </Link>
      </div>

    </div>
  )
}

export default VerificationResults
