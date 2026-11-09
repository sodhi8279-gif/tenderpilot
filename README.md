# TenderPilot: tender intelligence and bid preparation

A working application for Indian civil, water, sewerage, STP/WTP, electromechanical and EPC tendering. It covers ingestion of tender volumes, source-linked requirement extraction, versioned eligibility rules, JV evaluation, a clause-linked checklist, document generation, BOQ rate analysis and quotes, an official-workbook export, upload-slot packaging with preflight, and receipt recording.

Non-programmers: read **START_HERE.md**. Current status: **docs/FEATURE_STATUS.md**. Next steps: **PROGRESS.md**.

## Architecture (as built)

| Layer | Choice | Why |
|---|---|---|
| UI | React 18 + TypeScript + Vite + Tailwind 4 (`frontend/`) | The brief suggested Next.js. Vite was chosen because no server rendering is needed: the FastAPI app serves the built UI, so there is one process on Windows. |
| API | FastAPI, versioned under `/api/v1` (`backend/app/api`) | Pydantic validation; server-side permission check on every route |
| Data | SQLAlchemy 2; SQLite by default, PostgreSQL via `DATABASE_URL` | Postgres was not installed on the build machine |
| Jobs | Durable queue table plus worker thread (`services/jobs.py`) | Redis was not installed. Jobs are idempotent, resumable per page, cancellable and retried; Redis/RQ can replace the worker without changing callers |
| Files | Write-once local storage behind a `Storage` protocol (`storage.py`) | An S3 adapter can implement the same three methods |
| PDF | PyMuPDF (text, render, search), pypdf (signature detection), reportlab (previews) | |
| OCR | Tesseract adapter (optional; autodetected) | Without it, scanned pages are reported as `unreadable` with a reason |
| Office | python-docx (forms), openpyxl (BOQ round-trip with verification) | LibreOffice is not installed; legacy `.xls`/`.doc` need manual conversion |
| AI | Provider interface (`services/ai.py`); **offline by default** | Optional Anthropic provider: requires a key **and** org-admin consent; excerpts must match the page verbatim |

Money and quantities use `Decimal` throughout (`DecimalStr` column type; floats are rejected).

## Run locally (Windows)

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r backend\requirements.txt
cd frontend; npm install; npm run build; cd ..
cd backend; ..\.venv\Scripts\python -m uvicorn app.main:app --port 8000
```

Open http://127.0.0.1:8000. For UI development, run `npm run dev` in `frontend/` (it proxies `/api` to port 8000). `python seed_demo.py` (from `backend/`) creates a SYNTHETIC demo workspace.

## Tests

```powershell
cd backend; ..\.venv\Scripts\python -m pytest -q
cd frontend; npx tsc -b
```

`backend/tests/test_acceptance.py` maps one-to-one to the acceptance list in the brief (section P), using generated SYNTHETIC fixtures (`backend/fixtures/synthetic.py`). No real tender or company files are committed.

## Security notes

- bcrypt passwords and JWT sessions (sessionStorage). Org boundary on every query; another org's records return 404, not 403. Price data is role-restricted.
- Uploads: content sniffing, size limits, ZIP path-traversal, entry-count and expansion limits, and encrypted-entry rejection. Storage keys are generated server-side.
- PDF passwords are held in memory only until used. Audit events strip text, passwords and secrets.
- Not yet integrated: a malware scanner (hook point: `services/ingest.sniff_kind`), SSO/MFA, and at-rest encryption (use disk/volume encryption).

See `docs/OPERATIONS.md` for deployment, backup/restore, retention and cost assumptions.
