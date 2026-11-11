# InvoiceIQ — Consolidated Phase 1–7 Project

**Project:** AI Powered Invoice Verification and Tax Compliance System  
**Checkpoint:** Consolidated source bundle, 30 October 2026  
**Included:** Backend Phase 1–7 implementation checkpoints and the latest React/Vite frontend prototype.

## Project status at a glance

The phase modules listed below are included in this consolidated source checkpoint and are marked completed according to the delivered Claude phase checkpoints. This means the implementation files and phase test suites are present; it does **not** mean every module is already exposed through one end-to-end API or that every test was rerun during packaging.

| Phase | Scope | Checkpoint status |
|---|---|---|
| 1 | Foundation / infrastructure | Completed |
| 2 | Invoice ingestion / secure upload | Completed |
| 3 | OCR + structured invoice extraction | Completed |
| 4A | Mathematical verification | Completed |
| 4B | Synthetic fraud data + feature engineering | Completed |
| 4C | ML fraud models (LightGBM + Isolation Forest) + combined fraud scoring | Completed |
| 5 | LangGraph orchestration | Completed |
| 6A | GST compliance foundation | Completed |
| 6B | GST Sandbox API integration | Completed |
| 6C | GST compliance + LangGraph integration | Completed |
| 7 | SHAP explainability | Completed |
| — | React/Vite frontend prototype | Prototype included; backend connection pending |

See [`docs/PHASE_COMPLETION_MANIFEST.md`](docs/PHASE_COMPLETION_MANIFEST.md) for the detailed phase inventory and [`docs/CONSOLIDATION_NOTES.md`](docs/CONSOLIDATION_NOTES.md) for source provenance and verification boundaries.

## Important integration boundary

This bundle contains the completed phase modules and a frontend prototype, but it is **not yet a fully integrated end-to-end application**.

The current FastAPI application registers health and invoice routes. The invoice API provides:

- `POST /api/v1/invoices/upload`
- `GET /api/v1/invoices/{invoice_id}`
- `POST /api/v1/invoices/{invoice_id}/extract`

The LangGraph verification workflow, trained fraud-model lifecycle, GST checks and SHAP explanations are present in backend modules, but the complete verification graph is not yet exposed as an integrated API workflow. The React frontend is still a prototype: its upload page performs browser-side file selection/validation and does not transmit invoices to FastAPI. Dashboard values and history remain placeholders until API integration is implemented.

The next project work is to connect the existing modules and frontend, persist complete verification results, add the required approval/audit workflow, and perform end-to-end testing. No new numbered phase is assigned to this integration work in this bundle.

## Repository structure

```text
InvoiceIQ/
├── backend/
│   ├── app/
│   │   ├── api/v1/          # FastAPI health and invoice endpoints
│   │   ├── compliance/      # GST foundation and GST Sandbox client
│   │   ├── core/             # Configuration, DB session, logging, errors
│   │   ├── fraud/            # Synthetic invoice data and feature engineering
│   │   ├── ml/               # Supervised model, anomaly detection, scoring, SHAP
│   │   ├── models/           # SQLAlchemy models
│   │   ├── ocr/              # PDF/text and Tesseract OCR processing
│   │   ├── orchestration/    # LangGraph state, nodes and graph
│   │   ├── schemas/          # Pydantic schemas
│   │   └── services/         # Ingestion, extraction, normalization, math checks
│   ├── migrations/           # Alembic migrations
│   ├── tests/                # Backend phase tests and safe fixtures
│   └── requirements.txt
├── frontend/                 # React 19 + Vite prototype
├── docker/                   # Backend/frontend Dockerfiles
├── docs/                     # Phase manifest and consolidation notes
├── .env.example
└── docker-compose.yml
```

## Local setup

### 1. Configure environment

Copy `.env.example` to `.env` in the project root and update the PostgreSQL credentials and other environment-specific settings. Never commit a real `.env` file or credentials.

### 2. Start PostgreSQL

The project is configured for PostgreSQL 16. Create the application database and role according to your local PostgreSQL setup, then update `.env`.

### 3. Install and start the backend

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

The API documentation is normally available at `http://localhost:8000/docs`.

### 4. Install and start the frontend

Open another terminal:

```powershell
cd frontend
npm ci
npm run dev
```

For a production bundle:

```powershell
npm run build
```

The frontend README documents the current prototype behaviour and its API integration boundary.

## Tests and verification

There are 15 backend test modules containing 300 `test_...` function definitions in this source checkpoint. A test definition count is not the same as a passing test run. The complete suite requires the configured Python dependencies and PostgreSQL test database; LangGraph tests also require the pinned LangGraph dependency.

The frontend production build was confirmed by the user for the preceding updated prototype. The latest cursor-interaction refinement changes `ParticleBackground.jsx` and `landing.css`; rerun `npm ci` and `npm run build` after extracting this consolidated bundle to validate that exact revision.

Docker configuration is included, but a successful Docker image build/run is not claimed by this consolidation.

## Security and packaging

- `.env.example` is included; real `.env` files and secrets are excluded.
- `node_modules`, Python virtual environments, caches, `dist`, logs and local storage are excluded.
- Test fixtures are synthetic validation fixtures; do not replace them with real customer invoices in a shared archive.
