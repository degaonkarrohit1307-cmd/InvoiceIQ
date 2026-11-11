# InvoiceIQ frontend prototype

React 19 + Vite frontend for the AI Powered Invoice Verification and Tax Compliance System.

## Run locally

Requirements: Node.js 22.12+ and npm.

```bash
cd frontend
npm ci
npm run dev
```

Create a production bundle with:

```bash
npm run build
```

Run the configured linter with:

```bash
npm run lint
```

## Current prototype scope

The frontend includes:

- Landing page with a restrained animated invoice-verification network and workflow preview.
- Responsive workspace navigation and page transitions.
- Dashboard with explicit zero-state metrics and recent activity placeholder.
- Invoice selection with PDF/JPG/PNG browser-side checks and a 15 MB limit.
- Results views for extraction, mathematical verification, fraud risk, GST compliance and SHAP explanations.
- Invoice history empty state.
- Session-level display preferences and application connection indicators.

## Important integration boundary

This is a frontend prototype. It does not call the backend, upload files, run OCR, calculate fraud scores, verify GSTINs, or save invoice records. The upload screen validates a selected file locally in the browser and clearly reports that no file is transmitted or processed. Dashboard counts and history remain empty until API integration is implemented.

Client-side file extension and MIME checks are for interface feedback only. The backend must continue to perform authoritative content validation and secure storage.

## Verification note for this checkpoint

The source files were checked for JavaScript/JSX syntax, CSS parsing, and unresolved relative imports. A Vite production build has **not** been verified in the packaging environment because npm dependency installation could not reach `registry.npmjs.org` (`EAI_AGAIN`). Run `npm ci` and `npm run build` in an environment with npm registry access before treating the production bundle as verified.
