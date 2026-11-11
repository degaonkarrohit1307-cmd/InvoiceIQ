import { useState } from 'react'

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function InvoiceUpload() {
  const [selectedFile, setSelectedFile] = useState(null)
  const [state, setState] = useState('idle') // idle | uploading | success | error
  const [result, setResult] = useState(null)
  const [errorMessage, setErrorMessage] = useState('')

  const handleFileChange = (event) => {
    const file = event.target.files?.[0] ?? null
    setSelectedFile(file)
    setState('idle')
    setResult(null)
    setErrorMessage('')
  }

  const handleUpload = async () => {
    if (!selectedFile) return
    setState('uploading')
    setErrorMessage('')

    const formData = new FormData()
    formData.append('file', selectedFile)

    try {
      const response = await fetch(`${API_BASE}/api/v1/invoices/upload`, {
        method: 'POST',
        body: formData,
      })
      const body = await response.json()

      if (!response.ok) {
        const message = body?.detail?.message || `Upload failed (HTTP ${response.status})`
        setErrorMessage(message)
        setState('error')
        return
      }

      setResult(body)
      setState('success')
    } catch (err) {
      setErrorMessage(err.message || 'Network error while uploading.')
      setState('error')
    }
  }

  return (
    <div className="shell" style={{ marginTop: '2rem' }}>
      <h2>Invoice Upload (Phase 2)</h2>
      <p style={{ color: '#666', fontSize: '0.9rem' }}>
        Supported formats: PDF, PNG, JPG/JPEG. This proves the ingestion API
        end to end — OCR/extraction happen in a later phase.
      </p>

      <div style={{ marginTop: '1rem' }}>
        <input
          type="file"
          accept=".pdf,.png,.jpg,.jpeg"
          onChange={handleFileChange}
        />
      </div>

      {selectedFile && (
        <p style={{ marginTop: '0.5rem', fontSize: '0.9rem' }}>
          Selected: <strong>{selectedFile.name}</strong> ({formatBytes(selectedFile.size)})
        </p>
      )}

      <button
        onClick={handleUpload}
        disabled={!selectedFile || state === 'uploading'}
        style={{ marginTop: '1rem' }}
      >
        {state === 'uploading' ? 'Uploading...' : 'Upload Invoice'}
      </button>

      {state === 'success' && result && (
        <div className="status-row" style={{ marginTop: '1rem', flexDirection: 'column', alignItems: 'flex-start' }}>
          <span className="dot ok" />
          <div style={{ marginTop: '0.5rem' }}>
            <p>
              {result.duplicate_of_existing
                ? 'This exact file was already uploaded — returned the existing invoice.'
                : 'Upload successful.'}
            </p>
            <p>Invoice ID: <code>{result.invoice_id}</code></p>
            <p>Status: <strong>{result.processing_status}</strong></p>
          </div>
        </div>
      )}

      {state === 'error' && (
        <div className="status-row" style={{ marginTop: '1rem' }}>
          <span className="dot error" />
          <span>{errorMessage}</span>
        </div>
      )}
    </div>
  )
}

export default InvoiceUpload
