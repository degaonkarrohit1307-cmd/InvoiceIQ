
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import Icon from '../components/Icon'
import { getInvoices } from '../services/api'

const PAGE_SIZE = 10

function formatDate(value) {
  if (!value) return '—'

  const date = new Date(value)

  if (Number.isNaN(date.getTime())) return '—'

  return date.toLocaleDateString('en-IN', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  })
}

function formatAmount(value, currency = 'INR') {
  if (value === null || value === undefined || value === '') return '—'

  const amount = Number(value)

  if (!Number.isFinite(amount)) return '—'

  try {
    return new Intl.NumberFormat('en-IN', {
      style: 'currency',
      currency,
    }).format(amount)
  } catch {
    return `${currency} ${amount.toFixed(2)}`
  }
}

function History() {
  const [invoices, setInvoices] = useState([])
  const [summary, setSummary] = useState({
    total: 0,
    completed: 0,
    processing: 0,
    failed: 0,
  })

  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [expandedId, setExpandedId] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false

    async function loadHistory() {
      setLoading(true)
      setError('')

      try {
        const response = await getInvoices({
          limit: PAGE_SIZE,
          offset,
        })

        if (cancelled) return

        setInvoices(Array.isArray(response.items) ? response.items : [])
        setSummary(response.summary || {
          total: 0,
          completed: 0,
          processing: 0,
          failed: 0,
        })
        setTotal(response.total || 0)
      } catch (err) {
        if (!cancelled) {
          setError(err.message || 'Unable to load invoice history.')
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    loadHistory()

    return () => {
      cancelled = true
    }
  }, [offset])

  const pageNumber = Math.floor(offset / PAGE_SIZE) + 1
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div className="page-stack">
      <div className="page-heading-row">
        <div className="page-header">
          <div className="eyebrow-light">RECORDS</div>
          <h1>Invoice history</h1>
          <p>Find previously processed invoices and revisit their verification details.</p>
        </div>

        <Link to="/verify" className="button-primary">
          <Icon name="plus" size={17} /> New verification
        </Link>
      </div>

      <div className="history-summary-grid">
        <div className="panel">
          <span>Total invoices</span>
          <strong>{summary.total}</strong>
        </div>
        <div className="panel">
          <span>Completed</span>
          <strong>{summary.completed}</strong>
        </div>
        <div className="panel">
          <span>Processing</span>
          <strong>{summary.processing}</strong>
        </div>
        <div className="panel">
          <span>Failed</span>
          <strong>{summary.failed}</strong>
        </div>
      </div>

      <section className="panel history-panel">
        <div className="panel-heading">
          <div>
            <h2>All invoices</h2>
            <p>Records retrieved from the InvoiceIQ backend.</p>
          </div>

          <span className="record-count">
            {total} records
          </span>
        </div>

        {error && (
          <div className="inline-alert error-alert" role="alert">
            <Icon name="alert" size={17} />
            <span>{error}</span>
          </div>
        )}

        <div className="history-table-wrap">
          <table className="history-table">
            <thead>
              <tr>
                <th>INVOICE</th>
                <th>SUPPLIER</th>
                <th>INVOICE DATE</th>
                <th>DATE ADDED</th>
                <th>PROCESSING</th>
                <th>EXTRACTION</th>
                <th>ACTION</th>
              </tr>
            </thead>

            <tbody>
              {loading ? (
                <tr>
                  <td colSpan="7">
                    <div className="history-empty">
                      <Icon name="clock" size={24} />
                      <strong>Loading invoices...</strong>
                      <p>Retrieving records from the backend.</p>
                    </div>
                  </td>
                </tr>
              ) : invoices.length === 0 ? (
                <tr>
                  <td colSpan="7">
                    <div className="history-empty">
                      <div className="empty-illustration">
                        <Icon name="file" size={24} />
                      </div>
                      <strong>No invoice records found</strong>
                      <p>
                        Upload an invoice to create your first history record.
                      </p>
                      <Link to="/verify" className="text-link">
                        Start your first verification
                        <Icon name="arrow" size={15} />
                      </Link>
                    </div>
                  </td>
                </tr>
              ) : (
                invoices.map((invoice) => {
                  const extracted = invoice.latest_extraction
                  const isExpanded = expandedId === invoice.invoice_id

                  return (
                    <>
                      <tr key={invoice.invoice_id}>
                        <td>
                          <strong>
                            {extracted?.invoice_number || invoice.original_filename}
                          </strong>
                          <span style={{
                            display: 'block',
                            fontSize: '10px',
                            color: '#8794a6',
                            marginTop: '4px',
                          }}>
                            {invoice.original_filename}
                          </span>
                        </td>

                        <td>
                          {extracted?.supplier_name || '—'}
                        </td>

                        <td>
                          {formatDate(extracted?.invoice_date)}
                        </td>

                        <td>
                          {formatDate(invoice.upload_timestamp)}
                        </td>

                        <td>
                          <span className="badge-neutral">
                            {invoice.processing_status}
                          </span>
                        </td>

                        <td>
                          <span className="badge-neutral">
                            {extracted?.status || 'NOT_STARTED'}
                          </span>
                        </td>

                        <td>
                          <button
                            type="button"
                            className="button-secondary"
                            onClick={() =>
                              setExpandedId(
                                isExpanded ? null : invoice.invoice_id
                              )
                            }
                          >
                            {isExpanded ? 'Close' : 'View details'}
                          </button>
                        </td>
                      </tr>

                      {isExpanded && (
                        <tr key={`${invoice.invoice_id}-details`}>
                          <td colSpan="7">
                            <div style={{
                              padding: '16px',
                              background: '#f8fafc',
                              borderRadius: '8px',
                            }}>
                              <h3>Invoice details</h3>

                              <p>
                                <strong>Invoice ID:</strong> {invoice.invoice_id}
                              </p>

                              <div style={{
                                display: 'grid',
                                gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
                                gap: '14px',
                                marginTop: '15px',
                              }}>
                                <div>
                                  <strong>Buyer</strong>
                                  <p>{extracted?.buyer_name || '—'}</p>
                                </div>

                                <div>
                                  <strong>Supplier GSTIN</strong>
                                  <p>{extracted?.supplier_gstin || '—'}</p>
                                </div>

                                <div>
                                  <strong>Buyer GSTIN</strong>
                                  <p>{extracted?.buyer_gstin || '—'}</p>
                                </div>

                                <div>
                                  <strong>Due date</strong>
                                  <p>{formatDate(extracted?.due_date)}</p>
                                </div>

                                <div>
                                  <strong>Subtotal</strong>
                                  <p>
                                    {formatAmount(extracted?.subtotal, extracted?.currency)}
                                  </p>
                                </div>

                                <div>
                                  <strong>Total tax</strong>
                                  <p>
                                    {formatAmount(extracted?.total_tax, extracted?.currency)}
                                  </p>
                                </div>

                                <div>
                                  <strong>Grand total</strong>
                                  <p>
                                    {formatAmount(extracted?.total_amount, extracted?.currency)}
                                  </p>
                                </div>

                                <div>
                                  <strong>Math verification</strong>
                                  <p>
                                    {extracted?.math_verification_status || '—'}
                                  </p>
                                </div>
                              </div>

                              {invoice.processing_error && (
                                <div className="inline-alert error-alert">
                                  {invoice.processing_error}
                                </div>
                              )}

                              {extracted?.missing_critical_fields?.length > 0 && (
                                <p>
                                  <strong>Missing fields:</strong>{' '}
                                  {extracted.missing_critical_fields.join(', ')}
                                </p>
                              )}

                              {extracted?.warnings?.length > 0 && (
                                <p>
                                  <strong>Warnings:</strong>{' '}
                                  {extracted.warnings.join(' ')}
                                </p>
                              )}
                            </div>
                          </td>
                        </tr>
                      )}
                    </>
                  )
                })
              )}
            </tbody>
          </table>
        </div>

        <div style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          gap: '12px',
          padding: '16px 0',
          flexWrap: 'wrap',
        }}>
          <span>
            Showing {total === 0 ? 0 : offset + 1}
            {' '}–{Math.min(offset + PAGE_SIZE, total)}
            {' '}of {total}
          </span>

          <div style={{ display: 'flex', gap: '8px' }}>
            <button
              type="button"
              className="button-secondary"
              disabled={offset === 0 || loading}
              onClick={() => {
                setExpandedId(null)
                setOffset(Math.max(0, offset - PAGE_SIZE))
              }}
            >
              Previous
            </button>

            <span style={{ padding: '8px' }}>
              Page {pageNumber} of {totalPages}
            </span>

            <button
              type="button"
              className="button-secondary"
              disabled={offset + PAGE_SIZE >= total || loading}
              onClick={() => {
                setExpandedId(null)
                setOffset(offset + PAGE_SIZE)
              }}
            >
              Next
            </button>
          </div>
        </div>
      </section>

      <div className="history-footnote">
        <Icon name="shield" size={16} />
        <span>
          Invoice records and extraction results are retrieved directly
          from the InvoiceIQ backend.
        </span>
      </div>
    </div>
  )
}

export default History
