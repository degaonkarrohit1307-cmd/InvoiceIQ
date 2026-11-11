
const API_ORIGIN = (
  import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
).replace(/\/+$/, '')

const API_PREFIX = '/api/v1'

export class ApiError extends Error {
  constructor(message, { status = 0, code = null, payload = null } = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.payload = payload
  }
}

async function request(path, options = {}) {
  let response

  try {
    response = await fetch(`${API_ORIGIN}${API_PREFIX}${path}`, options)
  } catch (error) {
    throw new ApiError(
      'Cannot connect to InvoiceIQ backend. Check whether FastAPI is running.',
      { payload: error }
    )
  }

  const responseText = await response.text()
  let payload = null

  if (responseText) {
    try {
      payload = JSON.parse(responseText)
    } catch {
      payload = responseText
    }
  }

  if (!response.ok) {
    const detail = payload?.detail

    const message =
      typeof detail === 'string'
        ? detail
        : detail?.message ||
          detail?.code ||
          `Request failed with HTTP ${response.status}`

    throw new ApiError(message, {
      status: response.status,
      code: detail?.code || null,
      payload,
    })
  }

  return payload
}

// Check whether FastAPI is running.
export function checkApiHealth() {
  return request('/health/live')
}

// Upload an invoice.
export function uploadInvoice(file) {
  const formData = new FormData()
  formData.append('file', file)

  return request('/invoices/upload', {
    method: 'POST',
    body: formData,
  })
}

// Trigger OCR and structured extraction.
export function extractInvoice(invoiceId) {
  return request(
    `/invoices/${encodeURIComponent(invoiceId)}/extract`,
    { method: 'POST' }
  )
}

// Retrieve saved invoice details.
export function getInvoice(invoiceId) {
  return request(
    `/invoices/${encodeURIComponent(invoiceId)}`
  )
}

export function getInvoices({ limit = 50, offset = 0 } = {}) {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  })

  return request(`/invoices?${params.toString()}`)
}

// Retrieve saved invoice analysis, including fraud, GST and SHAP results.
export function getInvoiceAnalysis(invoiceId) {
  return request(
    `/invoices/${encodeURIComponent(invoiceId)}/analysis`
  )
}