# AI Powered Invoice Verification and Tax Compliance System

Status: **Phase 2 — Invoice Ingestion & Secure Storage (complete)**. OCR,
invoice field extraction, verification, duplicate *invoice* detection
(semantic — see note below), fraud detection, GST compliance, vendor risk,
explainability, approval recommendation, and the analytics dashboard are
**not implemented yet** — those are later phases.

## What exists in Phase 2 (in addition to Phase 1)

- `POST /api/v1/invoices/upload` — accepts PDF/PNG/JPG/JPEG via
  `multipart/form-data`, validates it, stores it securely, registers it in
  PostgreSQL, and returns a structured response.
- File validation: extension **and** content-based MIME sniffing (magic
  bytes via libmagic) — a file can't pass by having a correct extension
  with mismatched actual content, or vice versa.
- Secure storage: the original filename is **never** used to build a
  filesystem path. Files are stored under a UUID-derived name inside
  `UPLOAD_DIR/invoices/YYYY/MM/`, with a defense-in-depth check that the
  resolved path can't escape the configured storage root.
- SHA-256 hashing of every uploaded file, with a real PostgreSQL **unique
  constraint** on `file_hash` (not just an application-level check), so
  duplicate detection is race-condition-safe even under concurrent uploads.
- Duplicate handling: uploading the identical file again does not create a
  second file or DB row — the existing invoice is returned with
  `duplicate_of_existing: true` and HTTP 200 (a genuinely new invoice
  returns HTTP 201).
- Transaction safety: if the DB write fails after a file was already
  written to disk, the file is deleted (no orphaned files); nothing is
  ever stored if validation fails.
- A minimal React upload page that exercises the real API (no mocking).

**Important distinction:** the SHA-256 hash check here is **exact binary
file duplicate detection only**. It catches "the identical file bytes were
uploaded twice." It does **not** detect two invoices that represent the
same underlying transaction but were scanned/exported differently (e.g. a
re-scanned copy, a re-saved PDF, or a resubmitted invoice with one digit
changed). That semantic/near-duplicate *invoice* intelligence is planned
for a later phase and is intentionally out of scope here.

## Supported file formats & limits

| Setting | Value | Configured via |
|---|---|---|
| Formats | PDF, PNG, JPG, JPEG | hard-coded allow-list (`app/services/file_validation.py`) |
| Max upload size | 15 MB by default | `MAX_UPLOAD_SIZE_MB` env var |
| Storage location | `./storage/uploads` by default (relative to wherever the backend process runs) | `UPLOAD_DIR` env var |

## Upload API

```
POST /api/v1/invoices/upload
Content-Type: multipart/form-data
Field: file
```

Success (new file) — `201 Created`:
```json
{
  "invoice_id": "60d17168-89f7-4ccd-ae6a-d5ffd8f5a31d",
  "original_filename": "invoice.pdf",
  "file_type": "pdf",
  "file_size": 241,
  "file_hash": "ead70355...",
  "processing_status": "READY_FOR_OCR",
  "upload_timestamp": "2026-08-16T12:25:05.371124Z",
  "duplicate_of_existing": false
}
```

Success (exact duplicate) — `200 OK`, same shape but `duplicate_of_existing: true`
and the *original* invoice's `invoice_id`.

Errors:
| Situation | Status | `detail.code` |
|---|---|---|
| Extension not in PDF/PNG/JPG/JPEG | 415 | `UNSUPPORTED_EXTENSION` |
| Content doesn't match claimed extension | 415 | `MIME_MISMATCH` |
| Empty file | 400 | `EMPTY_FILE` |
| No filename provided | 400 | `NO_FILENAME` |
| File exceeds `MAX_UPLOAD_SIZE_MB` | 413 | `FILE_TOO_LARGE` |
| Storage/DB failure after validation | 500 | `INGESTION_FAILED` |

## Processing status values

`UPLOADED` → `VALIDATING` → `READY_FOR_OCR` is the only path Phase 2 code
produces on success. `PROCESSING` and `COMPLETED` are reserved for the
Phase 3 OCR pipeline and are never set by Phase 2. `FAILED` is reserved for
future use.

## Cross-platform notes (Windows/Linux/Docker)

- All storage paths use `pathlib.Path` — nothing is hard-coded to a Linux
  or Windows path format.
- Content-type sniffing uses `python-magic`, which wraps the system
  `libmagic` library. **Linux/macOS ship libmagic already; Windows does
  not.** `requirements.txt` handles this with a platform marker:
  ```
  python-magic==0.4.27; sys_platform != "win32"
  python-magic-bin==0.4.14; sys_platform == "win32"
  ```
  `python-magic-bin` bundles a prebuilt libmagic DLL for Windows, so `pip
  install -r requirements.txt` installs the correct one automatically per
  platform. If you hit an import error for `magic` on Windows, confirm
  `python-magic-bin` installed successfully for your Python version — this
  package has historically lagged behind new Python releases, so if it
  fails to install on a very new Python version, that's a known
  limitation to watch for (not verified against Python 3.13 by this
  session, since it was built/tested on Linux).

## Running Phase 2 tests

```bash
cd backend
./venv/bin/pytest -v          # Linux/macOS
# or on Windows:
.venv\Scripts\pytest -v
```

This runs Phase 1 (15 tests) and Phase 2 (26 tests) together — 41 total —
against the same real, disposable PostgreSQL test database described in
the Phase 1 section below (never SQLite). Phase 2 tests additionally cover:
valid PDF/PNG/JPEG upload, unsupported/spoofed files, oversized/empty
files, SHA-256 correctness, secure storage (including a path-traversal
attempt), exact-duplicate behavior (no second file/row), transactional
rollback on a simulated DB failure, and the API's response schema and
status codes.

Test fixture files live in `backend/tests/fixtures/` (small synthetic
PDF/PNG/JPEG files plus deliberately invalid/malicious ones — none contain
real invoice data).

## Where uploaded files go during development

Relative to wherever the backend process's working directory is (typically
`backend/` when running `uvicorn` from there): `./storage/uploads/invoices/YYYY/MM/<uuid>.<ext>`.
This directory is gitignored and never committed.

---

## What exists in Phase 1

- FastAPI backend with a real PostgreSQL 16 database (no SQLite substitute)
- SQLAlchemy models + Alembic migrations
- `/`, `/api/v1/health/live`, `/api/v1/health/ready` endpoints (readiness
  does a real DB write + read)
- Global error handling with consistent JSON error responses
- Config/logging foundation, `.env`-based settings
- React (Vite) frontend shell that pings the backend readiness endpoint
- Dockerfiles for backend and frontend + `docker-compose.yml` (config-level
  only — not yet built/run in this environment, since it has no `docker`
  binary)
- 15 automated backend tests (pytest), all passing against a real Postgres
  instance

### Database privilege separation

There are two distinct PostgreSQL roles:

- **`invoice_app`** — the application's own role. It owns and uses
  `invoice_compliance_db`. It deliberately has **no `CREATEDB` and no
  `SUPERUSER`** privilege — a normal least-privilege application role.
- **`POSTGRES_ADMIN_USER`/`POSTGRES_ADMIN_PASSWORD`** (defaults to the
  standard `postgres` superuser) — used **only** by `tests/conftest.py` to
  create/drop the disposable `invoice_compliance_test_db` used during test
  runs. The application code never uses these credentials. The admin role
  creates the test database with `invoice_app` as its owner, so all actual
  test queries still run as the same low-privilege role used in
  production.

If you're setting this up fresh, make sure `POSTGRES_ADMIN_PASSWORD` in
your `.env` matches whatever password your local `postgres` superuser role
actually has.

## Tech stack

Backend: FastAPI, SQLAlchemy 2.x, Alembic, PostgreSQL 16, pytest
Frontend: React 19 + Vite

## Running locally (without Docker)

### 1. PostgreSQL

Install and start PostgreSQL 16, then create the app role/database:

```bash
sudo -u postgres psql -c "CREATE USER invoice_app WITH PASSWORD 'invoice_app_dev_pw';"
sudo -u postgres psql -c "CREATE DATABASE invoice_compliance_db OWNER invoice_app;"
```

`invoice_app` should NOT be given `CREATEDB` — that's intentional (see
"Database privilege separation" above). The `postgres` superuser role
(created automatically by any PostgreSQL install) is what test
infrastructure uses instead; just make sure you know its password and set
`POSTGRES_ADMIN_PASSWORD` accordingly in `.env`.

### 2. Backend

```bash
cd backend
python3 -m venv venv          # Windows: python -m venv .venv
./venv/bin/pip install -r requirements.txt      # Windows: .venv\Scripts\pip install -r requirements.txt
cp ../.env.example .env       # then edit values as needed
./venv/bin/alembic upgrade head                 # Windows: .venv\Scripts\alembic upgrade head
./venv/bin/uvicorn app.main:app --reload        # Windows: .venv\Scripts\uvicorn app.main:app --reload
```

Verify: `curl http://localhost:8000/api/v1/health/ready`

### 3. Backend tests

```bash
cd backend
./venv/bin/pytest -v          # Windows: .venv\Scripts\pytest -v
```

Tests spin up and tear down an isolated `invoice_compliance_test_db` on the
same PostgreSQL instance — no mocking of the database layer, using the
admin credential described above (never `invoice_app`).

### 4. Frontend

```bash
cd frontend
npm install
cp .env.example .env   # set VITE_API_BASE_URL if backend isn't on localhost:8000
npm run dev            # or: npm run build
```

## Running with Docker (not yet verified in this environment)

```bash
docker compose up --build
```

This has been validated for syntactic/structural correctness only
(`docker-compose.yml` parses as valid YAML with the expected services;
Dockerfiles reference real paths and have required instructions). It has
not been built or run, because this development sandbox does not have a
`docker` binary available.

## Project structure

```
backend/
  app/
    api/v1/        - versioned API routes (health.py, invoices.py)
    core/          - config, database session, logging, error handlers
    models/        - SQLAlchemy ORM models (system.py, invoice.py)
    schemas/       - Pydantic request/response schemas (invoice.py)
    services/      - business logic (storage.py, file_validation.py, invoice_ingestion.py)
    ml/ ocr/ compliance/ fraud/ risk/  - reserved for later phases
  migrations/       - Alembic migration scripts
  tests/            - pytest suite (incl. fixtures/ for sample upload files)
frontend/           - React (Vite) app
docker/             - Dockerfiles
data/               - reserved for raw/processed/synthetic datasets (Phase 3+)
```

## Security notes

- `.env` files are gitignored; only `.env.example` (no real secrets) is committed.
- The password in `.env.example` / this README is a local development
  placeholder, not a production credential.
- Uploaded invoice files (`storage/`) are gitignored and never committed.

---

## Phase 3 — OCR & Structured Invoice Extraction

Adds: `POST /api/v1/invoices/{id}/extract`, `GET /api/v1/invoices/{id}`.

**Pipeline:** stored file -> PDF embedded-text (PyMuPDF) or OCR fallback (Tesseract via pytesseract) for scanned pages/images -> regex-based structured field extraction -> normalization (₹/Rs./INR amounts, dates, GSTIN) -> deterministic rule-based math verification -> versioned persistence (`invoice_extractions`, `invoice_line_items`).

**Extracted fields:** invoice number/date/due date, supplier/buyer name+GSTIN, subtotal, CGST/SGST/IGST, total tax, grand total, currency, line items (qty/unit price/line total). Missing fields are reported as missing, never guessed.

**GSTIN:** local structural format validation only (`normalization.is_valid_gstin_format`) — does NOT verify the GSTIN is real/active; that requires an external GST API (later phase).

**Math verification (`app/services/math_verification.py`, deterministic, no ML):** line qty×price≈total, sum(line totals)≈subtotal, CGST+SGST≈tax (or IGST≈tax for inter-state), subtotal+tax≈grand total. Each check returns `PASS`/`FAIL`/`NOT_CHECKABLE` (missing inputs → `NOT_CHECKABLE`, never fabricated) with a configurable tolerance (default 0.02). A `FAIL` is a signal only, not an automatic fraud/invalid classification.

**Idempotency:** each `/extract` call creates a NEW versioned `InvoiceExtraction` row (audit trail); `GET /invoices/{id}` returns the latest version.

### OCR setup (Windows)
`pytesseract` requires the Tesseract **executable**, not installed by pip:
- Install: https://github.com/UB-Mannheim/tesseract/wiki
- If not on PATH, set `TESSERACT_CMD` in `.env` to the full path, e.g. `C:\Program Files\Tesseract-OCR\tesseract.exe`
- If Tesseract is missing, extraction of scanned documents fails gracefully with a structured `OCR_DEPENDENCY_UNAVAILABLE` (503) error rather than crashing or fabricating a result.

### New dependencies
`pymupdf`, `Pillow`, `pytesseract`, `reportlab` (fixture generation only). Run `pip install -r requirements.txt`.

### Migration
`e2e555521bac_phase3_invoice_extraction` (after `9129d651be22`). Run `alembic upgrade head`.

### Tests
`backend/tests/test_invoice_extraction.py`. Most tests (OCR, field extraction, normalization, math verification) are DB-independent. A handful (persistence + API) require the real PostgreSQL test DB, consistent with Phase 1/2 (never SQLite).

### Known limitations
- Regex/heuristic extraction works well on cleanly formatted invoices (and the test fixtures) but degrades on unusual layouts or poor scans — by design, missing/uncertain fields are reported as missing rather than guessed.
- This sandbox session validated OCR/extraction/math-verification logic directly (Tesseract 5.3.4, Linux) but could not run the full pytest suite against PostgreSQL (not installed in this fresh container) or verify on actual Windows/Python 3.13 — run `pytest -v` after `pip install` + `alembic upgrade head` on your machine to confirm.

---

## Phase 4A — Rule-Based Mathematical Verification

Extends `app/services/math_verification.py` (already present from Phase 3) with:
- `tolerance` now included in every structured `RuleCheck` result (not just used internally).
- Optional `discount` parameter on `verify_grand_total`/`run_math_verification`: `subtotal + total_tax - discount ≈ grand_total`. Absent discount defaults to 0 in the arithmetic (not NOT_CHECKABLE — most invoices have no discount).
- New `verify_discount_consistency`: discount must not exceed subtotal. `NOT_CHECKABLE` when no discount was extracted.
- `MathVerificationResult.summary` property: `{overall, total_checks, passed, failed, not_checkable, warnings}`.

Rules unchanged in behavior/name from Phase 3: `line_item_N_total`, `subtotal_matches_line_items`, `cgst_plus_sgst` / `igst_matches_total_gst`, `subtotal_plus_tax_equals_grand_total`.

Every check is PASS / FAIL / NOT_CHECKABLE — missing inputs are never fabricated as zero or silently skipped.

**Integration:** `app/services/invoice_extraction.py` already calls `run_math_verification()` and persists results to `InvoiceExtraction.math_verification_status`/`math_verification_details` (existing Phase 3 wiring, now also carrying `tolerance` per check). No API/schema breaking changes — `MathCheckResponse` gained an additional optional `tolerance` field.

### Running Phase 4A tests locally
```
cd backend
pip install -r requirements.txt
pytest tests/test_math_verification_phase4a.py -v          # pure logic, no DB needed
pytest tests/test_math_verification_phase4a.py --noconftest -v   # if conftest.py's DB fixture isn't available
```
Full-suite / API / persistence tests (in `test_invoice_extraction.py`) still require the real PostgreSQL test DB per Phase 1/2 convention — run those after `alembic upgrade head` on your machine.

### What remains (Phase 4B/4C — NOT in this checkpoint)
- Synthetic fraud dataset generator
- LightGBM supervised fraud classifier
- Isolation Forest anomaly detector
- Combined fraud analysis result / risk level

---

## Phase 4B — Synthetic Fraud Data + Feature Engineering

**Note: entirely synthetic data for ML development/training. Does not represent real invoices, companies, or real-world fraud statistics.**

Adds `app/fraud/`:
- `synthetic_data.py` — `SyntheticInvoiceGenerator` (seeded `random.Random`, not global `random`), 6 modular fraud scenarios (`FraudType`: MATH_MISMATCH, TAX_MISMATCH, ABNORMAL_DISCOUNT, DUPLICATE_INVOICE_NUMBER, UNUSUAL_AMOUNT, LINE_ITEM_INCONSISTENCY).
- `feature_engineering.py` — `extract_features()` converts a record into 18 numerical features (amounts, line-item stats, tax/discount ratios, Phase 4A math-verification-derived features, duplicate flag). Never reads `.label`/`.fraud_type`. `build_feature_matrix()` returns features and labels as separate lists.
- `dataset.py` — `build_dataset(DatasetConfig)` ties generation + features together; deterministic per seed; `DatasetResult.to_csv()` exports (features + label + fraud_type, clearly separated columns).

### Running Phase 4B tests locally
```
cd backend
pytest tests/test_fraud_synthetic_data.py -v --noconftest   # no DB required
```
19/19 passing (this sandbox, Linux).

### What remains (Phase 4C — NOT in this checkpoint)
- LightGBM supervised fraud classifier trained on this dataset
- Isolation Forest anomaly detector
- Combined fraud analysis result / risk level

---

## Phase 4C — ML Fraud Models + Combined Fraud Scoring

**All training/evaluation uses SYNTHETIC data (Phase 4B). Metrics reflect fit to that synthetic dataset only — not real-world fraud detection accuracy.**

Adds `app/ml/`:
- `supervised_model.py` — LightGBM classifier (fallback: sklearn `GradientBoostingClassifier` with manual class-weight balancing if LightGBM is unavailable in your environment; this run used **LightGBM successfully**). Reproducible stratified train/test split, `predict_proba()`/`predict()` inference-only on a fitted model.
- `anomaly_detector.py` — Isolation Forest, trained on features only (no labels). `normalized_score()` maps sklearn's raw `decision_function` to `[0,1]` via min-max scaling over the training distribution (1.0 = most anomalous).
- `evaluation.py` — precision/recall/F1/ROC-AUC (`None` when not validly computable, e.g. single-class y_true — never fabricated).
- `fraud_scoring.py` — `combine_fraud_score()`: weighted sum of supervised score (default 0.5), anomaly score (0.3), and a Phase-4A-derived mathematical score (0.2) → final score 0–100, `LOW`/`MEDIUM`/`HIGH` via configurable thresholds (39/69 by default — explicitly **not** industry-certified). Math score = `100 × (failed/total) + 15 × (not_checkable/total)`, capped at 100.
- `pipeline.py` — `FraudAnalysisPipeline`: single entry point (`.train()`, `.analyze(record)`, `.save()`/`.load()` via joblib). This is the interface Phase 5 should call.

### ML dependencies added
`scikit-learn==1.5.2`, `lightgbm==4.5.0`, `joblib==1.4.2` (added to `requirements.txt`).

### Running Phase 4C tests locally
```
cd backend
pytest tests/test_ml_fraud_pipeline.py -v --noconftest   # no DB required
```
28/28 passing (this sandbox, Linux, LightGBM installed successfully).

### What remains (Phase 5 — NOT in this checkpoint)
LangGraph agentic orchestration calling `FraudAnalysisPipeline.analyze()`.

---

## Phase 4C — ML Fraud Models + Combined Scoring

**Synthetic-data disclaimer:** all training/evaluation below uses the Phase 4B synthetic generator. Metrics describe fit to that synthetic dataset only — not real-world fraud detection accuracy.

Adds to `app/ml/`:
- `supervised_model.py` — LightGBM classifier (sklearn `GradientBoostingClassifier` fallback if LightGBM is unavailable; documented, not fake). Reproducible train/test split, `predict`/`predict_proba` separated from training.
- `anomaly_detector.py` — Isolation Forest, normalized anomaly score in [0,1].
- `evaluation.py` — precision/recall/F1/ROC-AUC (supervised), threshold-based metrics (anomaly), safely returns `None` when a metric can't be computed (e.g. single-class ROC-AUC).
- `fraud_scoring.py` — deterministic weighted combination of supervised score + anomaly score + Phase 4A mathematical-verification score → final 0–100 score with explicit `FraudScoringConfig` weights/thresholds and `RiskCategory` (LOW/MEDIUM/HIGH, thresholds are configuration, not certified industry values).
- `pipeline.py` — `FraudAnalysisPipeline`: `train()`, `analyze()`, `save()`/`load()` (joblib). Single high-level entry point for later phases.

**Dependencies added:** `scikit-learn`, `lightgbm`, `joblib` (installed and verified working in this sandbox — LightGBM required no special troubleshooting).

### Running Phase 4C tests locally
```
cd backend
pip install -r requirements.txt
pytest tests/test_ml_fraud_scoring.py -v --noconftest   # no DB required
```
26/26 passing (this sandbox).

### Combined score, briefly
`final_score = 100 × (supervised_prob × w1 + anomaly_score × w2 + math_score × w3)`, weights sum to 1.0, all configurable via `FraudScoringConfig`. Math score rewards FAIL checks (Phase 4A) much more than NOT_CHECKABLE ones.

### What remains (Phase 5 — NOT in this checkpoint)
LangGraph agentic framework.

---

## Phase 5 — LangGraph Orchestration (College 50% Milestone)

Adds `app/orchestration/`:
- `state.py` — typed `InvoiceGraphState` (TypedDict) carrying invoice fields, math verification result, fraud result, and final output through the graph.
- `nodes.py` — 4 modular nodes: `validate_invoice_node`, `mathematical_verification_node`, `fraud_analysis_node`, `aggregate_decision_node`. Each wraps existing logic with no duplication.
- `graph.py` — `build_invoice_verification_graph(pipeline)` + `run_invoice_verification(pipeline, invoice_fields, discount=None)`.

**Flow:** `validate → verify_math → analyze_fraud → aggregate → END`, with `error`/`is_valid=False` short-circuiting so a bad invoice yields a structured `ERROR` or `INVALID_INPUT` result rather than crashing.

**Reuse, not reimplementation:** `mathematical_verification_node` calls Phase 4A's `run_math_verification()` directly; `fraud_analysis_node` calls Phase 4C's `FraudAnalysisPipeline.analyze()` directly (pipeline must be pre-trained/loaded — the graph never trains a model itself).

**Final result** exposes: `verification_status`, `math_verification` (overall + per-check detail), `fraud_score`, `fraud_risk_category`, `key_signals`, `error`.

**Dependency added:** `langgraph==0.2.62`.

### Running Phase 5 tests locally
```
cd backend
pip install -r requirements.txt
pytest tests/test_orchestration_langgraph.py -v --noconftest   # no DB required
```
19/19 passing (this sandbox).

### What remains (Phase 6 — NOT in this checkpoint)
GST/tax compliance rules; LLM/RAG/chatbot functionality remains explicitly out of scope for this pipeline.

---

## Phase 7 — SHAP Explainability (College 70% Milestone)

Adds `app/ml/shap_explainer.py` and `FraudAnalysisPipeline.explain_supervised_prediction()`.

**Scope:** explains the SUPERVISED fraud model's prediction only (not the Isolation Forest, not math verification). SHAP values describe model behavior for this input, relative to training data -- **not proof of fraud**.

**Two verified computation paths** (chosen after discovering a real shap/lightgbm version-compatibility bug in this environment's `shap.TreeExplainer.shap_values()`/`explainer(X)` for LightGBM binary classification):
- **LightGBM** (primary, preferred): LightGBM's own native `pred_contrib` SHAP computation — unaffected by the shap-library bug, mathematically verified (in tests) to reconstruct the model's own `predict_proba` exactly via sigmoid.
- **sklearn `GradientBoostingClassifier` fallback**: `shap.TreeExplainer` (works correctly for this estimator type in this environment).

**Output distinguishes three sections**, never merged:
1. `model_prediction` — the supervised model's own `predict()`/`predict_proba()`
2. `fraud_score` — the **existing, unmodified** Phase 4C combined score + risk category (calls `.analyze()` as-is)
3. `shap_explanation` — top-N feature contributions, with an explicit `status` (`AVAILABLE` / `SHAP_LIBRARY_UNAVAILABLE` / `UNSUPPORTED_MODEL` / `ERROR`) so a missing dependency or unsupported model is reported honestly, never fabricated.

**Dependency added:** `shap==0.53.0`.

### Running Phase 7 tests locally
```
cd backend
pytest tests/test_ml_shap_explainability.py -v --noconftest   # no DB required
```
47/47 passing (this sandbox). Includes hardening added after review:
- Numerically stable `_sigmoid()`: splits on the sign of its input so `math.exp` is only ever called on a non-positive argument, avoiding `OverflowError` for large-magnitude finite inputs (naive `1/(1+exp(-x))` overflows beyond ~±746).
- Explicit finiteness check on predicted probability, reconstructed probability, base value, and all feature contributions, performed BEFORE the additivity tolerance comparison -- `>` comparisons involving NaN are always `False` in Python, so without this check a non-finite value could otherwise silently pass the additivity check and be reported as a falsely-"verified" `AVAILABLE` explanation.
- Runtime-enforced SHAP additivity check (documented tolerance `ADDITIVITY_TOLERANCE = 1e-4` in `shap_explainer.py`): if the reconstructed probability (sigmoid of the full contribution vector + base value) doesn't match the model's own `predict_proba()` within tolerance, the explanation is returned as `ERROR`, never shown as if verified.
- Feature validation: every model feature must be present in the input dict and hold a finite numeric value -- missing or NaN/inf features are rejected with a clear error, never silently defaulted to 0.0.
- Feature ordering preserved via `model.feature_names`; the additivity check always uses the complete contribution vector, even when `top_n` truncates the returned list.

### What remains (Phase 8+ — NOT in this checkpoint)
React UI, FastAPI endpoint exposure for SHAP explanations.
