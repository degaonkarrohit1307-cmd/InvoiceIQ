
import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import Icon from '../components/Icon'
import { uploadInvoice, extractInvoice } from '../services/api'

const MAX_SIZE_BYTES = 15 * 1024 * 1024
const ACCEPTED_EXTENSIONS = ['pdf', 'jpg', 'jpeg', 'png']
const ACCEPTED_MIME = ['application/pdf', 'image/jpeg', 'image/png']

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function getExtension(name = '') {
  return name.split('.').pop()?.toLowerCase() || ''
}

function displayValue(value) {
  return value === null || value === undefined || value === ''
    ? '—'
    : String(value)
}

function formatAmount(value, currency) {
  if (value === null || value === undefined) return '—'
  return `${currency || ''} ${Number(value).toLocaleString('en-IN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`.trim()
}

function VerifyInvoice() {
  const [file, setFile] = useState(null)
  const [error, setError] = useState('')
  const [dragging, setDragging] = useState(false)
  const [stage, setStage] = useState('idle')
  const [invoiceId, setInvoiceId] = useState(null)
  const [extraction, setExtraction] = useState(null)

  const inputRef = useRef(null)
  const busy = stage === 'uploading' || stage === 'extracting'

  const validate = (candidate) => {
    setError('')
    setInvoiceId(null)
    setExtraction(null)

    if (!candidate) return

    const extension = getExtension(candidate.name)
    const validExtension = ACCEPTED_EXTENSIONS.includes(extension)
    const validMime = !candidate.type || ACCEPTED_MIME.includes(candidate.type)

    if (!validExtension || !validMime) {
      setFile(null)
      setError('Unsupported file. Select a PDF, JPG or PNG invoice.')
      return
    }

    if (candidate.size === 0) {
      setFile(null)
      setError('This file is empty. Choose another invoice file.')
      return
    }

    if (candidate.size > MAX_SIZE_BYTES) {
      setFile(null)
      setError('The selected file exceeds the 15 MB limit.')
      return
    }

    setStage('idle')
    setFile(candidate)
  }

  const clearFile = () => {
    if (busy) return

    setFile(null)
    setError('')
    setInvoiceId(null)
    setExtraction(null)
    setStage('idle')

    if (inputRef.current) {
      inputRef.current.value = ''
    }
  }

  const handleSubmit = async () => {
    if (!file || busy) return

    setError('')
    setInvoiceId(null)
    setExtraction(null)

    try {
      // Step 1: Upload invoice to FastAPI.
      setStage('uploading')

      const uploaded = await uploadInvoice(file)
      setInvoiceId(uploaded.invoice_id)

      // Step 2: Trigger OCR and structured extraction.
      setStage('extracting')

      const extracted = await extractInvoice(uploaded.invoice_id)

      // Step 3: Save the actual backend response for display.
      setExtraction(extracted)
      setStage('completed')
    } catch (requestError) {
      setStage('error')
      setError(
        requestError.message || 'Invoice processing failed. Please try again.'
      )
    }
  }

  const mathChecks = extraction?.math_verification_details || []
  const lineItems = extraction?.line_items || []

  const failedMathChecks = mathChecks.filter(
  (check) => check.result === 'FAIL'
)

const notCheckableMathChecks = mathChecks.filter(
  (check) => check.result === 'NOT_CHECKABLE'
)

const mathStatus = extraction?.math_verification_status?.toUpperCase()

const requiresReview =
  mathStatus === 'FAIL' || failedMathChecks.length > 0

const verificationTitle = requiresReview
  ? 'FAILED — REVIEW REQUIRED'
  : mathStatus === 'PASS'
    ? 'VERIFICATION PASSED'
    : 'REVIEW REQUIRED — INCOMPLETE CHECKS'

const failedRuleLabels = failedMathChecks.map((check) => {
  const labels = {
    line_item_1_total: 'line-item total',
    subtotal_matches_line_items: 'subtotal reconciliation',
    cgst_plus_sgst: 'GST calculation',
    subtotal_plus_tax_equals_grand_total: 'grand-total reconciliation',
  }

  return labels[check.rule] || check.rule.replace(/_/g, ' ')
})

const verificationMessage = requiresReview
  ? `${failedMathChecks.length} mathematical inconsistencies detected. Review the ${failedRuleLabels.join(', ')}.`
  : mathStatus === 'PASS'
    ? notCheckableMathChecks.length > 0
      ? `${notCheckableMathChecks.length} check${notCheckableMathChecks.length === 1 ? '' : 's'} could not be evaluated. All checkable mathematical rules passed.`
      : 'All mathematical verification checks passed.'
    : 'Mathematical verification could not be completed. Review the available results.'

  return (
    <div className="page-stack">
      <div className="page-header">
        <div className="eyebrow-light">DOCUMENT INTAKE</div>
        <h1>Verify an invoice</h1>
        <p>Upload an invoice to run OCR extraction and mathematical verification.</p>
      </div>

      <section className="panel upload-panel">
        <div className="upload-panel-heading">
          <div>
            <h2>Upload invoice</h2>
            <p>Select a PDF or image invoice from your device.</p>
          </div>
          <span className="secure-tag">
            <Icon name="shield" size={15} /> Backend validation
          </span>
        </div>

        <div
          className={
            'upload-dropzone' +
            (dragging ? ' is-dragging' : '') +
            (file ? ' has-file' : '')
          }
          onDragOver={(event) => {
            event.preventDefault()
            if (!busy) setDragging(true)
          }}
          onDragLeave={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget)) {
              setDragging(false)
            }
          }}
          onDrop={(event) => {
            event.preventDefault()
            setDragging(false)
            if (!busy) validate(event.dataTransfer.files?.[0])
          }}
        >
          <div className="upload-icon-wrap">
            <Icon name="upload" size={25} />
          </div>

          <h3>{file ? 'Invoice ready to upload' : 'Drop your invoice here'}</h3>

          <p>
            {file
              ? 'Your document is ready for backend processing.'
              : 'Or browse your device to select an invoice.'}
          </p>

          <button
            type="button"
            className="button-secondary"
            disabled={busy}
            onClick={() => inputRef.current?.click()}
          >
            {file ? 'Choose a different file' : 'Browse files'}
          </button>

          <input
            ref={inputRef}
            className="visually-hidden"
            type="file"
            accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png"
            onChange={(event) => validate(event.target.files?.[0])}
            aria-label="Choose invoice file"
          />

          <div className="upload-constraints">
            PDF, JPG or PNG <span /> Maximum file size: 15 MB
          </div>
        </div>

        {error && (
          <div className="inline-alert error-alert" role="alert">
            <Icon name="alert" size={17} />
            <span>{error}</span>
          </div>
        )}

        {file && (
          <div className="selected-file-card">
            <span className="selected-file-icon">
              <Icon name="file" size={20} />
            </span>

            <div className="selected-file-info">
              <strong>{file.name}</strong>
              <span>
                {formatBytes(file.size)} · {getExtension(file.name).toUpperCase()} document
              </span>
            </div>

            <span className="file-valid-tag">
              <Icon name="check" size={14} /> Selected
            </span>

            <button
              type="button"
              className="icon-button"
              disabled={busy}
              onClick={clearFile}
              aria-label="Remove selected file"
            >
              <Icon name="close" size={17} />
            </button>
          </div>
        )}

        {busy && (
          <div className="inline-alert info-alert" role="status" aria-live="polite">
            <Icon name="clock" size={17} />
            <span>
              {stage === 'uploading'
                ? 'Uploading invoice to the InvoiceIQ backend...'
                : 'OCR extraction and mathematical verification are running...'}
            </span>
          </div>
        )}

        {stage === 'error' && invoiceId && (
          <div className="inline-alert info-alert" role="status">
            <Icon name="file" size={17} />
            <span>
              The invoice was uploaded successfully. Invoice ID: {invoiceId}.
              Extraction did not complete successfully.
            </span>
          </div>
        )}

        <div className="upload-actions">
          <button
            type="button"
            className="button-primary"
            disabled={!file || busy}
            onClick={handleSubmit}
          >
            {busy ? 'Processing...' : 'Upload and verify'}
            <Icon name="arrow" size={16} />
          </button>

          <span>Upload, OCR and extraction through FastAPI.</span>
        </div>
      </section>


{stage === 'completed' && extraction && (
  <section className="panel">
    <div className="panel-heading">
      <div>
        <h2>Invoice verification results</h2>
        <p>Complete information returned by the InvoiceIQ backend.</p>
      </div>
      <span className="badge-neutral">{extraction.status}</span>
    </div>

    <div style={{ marginTop: '12px' }}>
      <strong>Invoice ID:</strong> {invoiceId}
    </div>

    <div
  className={`verification-banner ${
    requiresReview
      ? 'verification-banner-failed'
      : mathStatus !== 'PASS'
    ? 'verification-banner-incomplete'
    : 'verification-banner-passed'
  }`}
  role="status"
>
  <div className="verification-banner-header">
    <div>
      <span>INVOICE VERIFICATION</span>
      <h3>{verificationTitle}</h3>
    </div>

    <strong>
  {requiresReview
    ? 'FAILED'
    : mathStatus === 'PASS'
      ? 'PASSED'
      : 'INCOMPLETE'}
</strong>
  </div>

  <p>{verificationMessage}</p>

  {failedRuleLabels.length > 0 && (
    <ul>
      {failedRuleLabels.map((label, index) => (
        <li key={`${label}-${index}`}>{label}</li>
      ))}
    </ul>
  )}
</div>

    <div className="details-section">
      <h3>Invoice information</h3>

      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
        gap: '14px',
        marginTop: '14px'
      }}>
        {[
          ['Invoice number', extraction.invoice_number],
          ['Invoice date', extraction.invoice_date],
          ['Due date', extraction.due_date],
          ['Supplier', extraction.supplier_name],
          ['Supplier GSTIN', extraction.supplier_gstin],
          ['Buyer', extraction.buyer_name],
          ['Buyer GSTIN', extraction.buyer_gstin],
          ['Currency', extraction.currency],
        ].map(([label, value]) => (
          <div key={label} className="detail-field">
            <span>{label}</span>
            <strong>{displayValue(value)}</strong>
          </div>
        ))}
      </div>
    </div>

    <div className="details-section">
      <h3>Financial summary</h3>

      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
        gap: '14px',
        marginTop: '14px'
      }}>
        {[
          ['Subtotal', extraction.subtotal],
          ['CGST', extraction.cgst],
          ['SGST', extraction.sgst],
          ['IGST', extraction.igst],
          ['Total tax', extraction.total_tax],
          ['Grand total', extraction.total_amount],
        ].map(([label, value]) => (
          <div key={label} className="detail-field">
            <span>{label}</span>
            <strong>{formatAmount(value, extraction.currency)}</strong>
          </div>
        ))}
      </div>
    </div>

    <div className="details-section">
      <h3>Line items ({lineItems.length})</h3>

      {lineItems.length > 0 ? (
        <div style={{ overflowX: 'auto', marginTop: '12px' }}>
          <table style={{
            width: '100%',
            borderCollapse: 'collapse',
            fontSize: '12px'
          }}>
            <thead>
              <tr>
                {['#', 'Description', 'Quantity', 'Unit price',
                  'Taxable amount', 'Tax rate', 'Line total'].map((heading) => (
                  <th key={heading} style={{
                    padding: '12px',
                    textAlign: heading === 'Description' ? 'left' : 'right',
                    borderBottom: '1px solid #e5e7eb'
                  }}>
                    {heading}
                  </th>
                ))}
              </tr>
            </thead>

            <tbody>
              {lineItems.map((item, index) => (
                <tr key={item.line_number ?? index}>
                  <td style={{ padding: '12px', textAlign: 'right' }}>
                    {item.line_number ?? index + 1}
                  </td>
                  <td style={{ padding: '12px' }}>
                    {displayValue(item.description)}
                  </td>
                  <td style={{ padding: '12px', textAlign: 'right' }}>
                    {displayValue(item.quantity)}
                  </td>
                  <td style={{ padding: '12px', textAlign: 'right' }}>
                    {formatAmount(item.unit_price, extraction.currency)}
                  </td>
                  <td style={{ padding: '12px', textAlign: 'right' }}>
                    {formatAmount(item.taxable_amount, extraction.currency)}
                  </td>
                  <td style={{ padding: '12px', textAlign: 'right' }}>
                    {item.tax_rate == null ? '—' : `${item.tax_rate}%`}
                  </td>
                  <td style={{ padding: '12px', textAlign: 'right' }}>
                    {formatAmount(item.line_total, extraction.currency)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p>No line items were extracted.</p>
      )}
    </div>

    <div className="details-section">
      <div className="math-summary-row">
        <div>
          <h3>Mathematical verification</h3>
          <p>Results from the backend verification engine.</p>
        </div>
        <strong>{displayValue(extraction.math_verification_status)}</strong>
      </div>

      {mathChecks.length > 0 ? (
        <div style={{ overflowX: 'auto', marginTop: '12px' }}>
          <table style={{
            width: '100%',
            borderCollapse: 'collapse',
            fontSize: '12px'
          }}>
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
                  <td>{check.result}</td>
                  <td>{displayValue(check.expected)}</td>
                  <td>{displayValue(check.actual)}</td>
                  <td>{displayValue(check.difference)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p>No mathematical verification details were returned.</p>
      )}
    </div>

    {extraction.missing_critical_fields?.length > 0 && (
      <div className="inline-alert info-alert">
        Missing critical fields: {extraction.missing_critical_fields.join(', ')}
      </div>
    )}

    {extraction.warnings?.length > 0 && (
      <div className="inline-alert info-alert">
        {extraction.warnings.join(' ')}
      </div>
    )}
    <div className="results-next-step">
  <div>
    <strong>Explore complete invoice analysis</strong>
    <span>
      Review fraud risk, GST compliance and SHAP explanations.
    </span>
  </div>

  <Link
    to="/results"
    state={{ invoiceId, extraction }}
    className="button-primary"
  >
    View Full Analysis
    <Icon name="arrow" size={16} />
  </Link>
</div>
  </section>
)}


      <div className="upload-bottom-grid">
        <div className="info-card">
          <span className="info-card-icon"><Icon name="shield" size={18} /></span>
          <div>
            <strong>Secure processing</strong>
            <p>The backend validates file content and type before storing an invoice.</p>
          </div>
        </div>

        <div className="info-card">
          <span className="info-card-icon"><Icon name="check" size={18} /></span>
          <div>
            <strong>Processing workflow</strong>
            <p>InvoiceIQ runs OCR, structured extraction and mathematical verification.</p>
          </div>
        </div>
      </div>
    </div>
  )
}

export default VerifyInvoice
