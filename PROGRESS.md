# Progress log

## Session 1: 2026-11-09
Built phases 1–5 as working software, plus parts of phase 6. See `docs/FEATURE_STATUS.md` for the honest per-feature status.

Verified:
- `backend`: `pytest`, 14 acceptance tests passing (brief §P items 1–14).
- `frontend`: `tsc -b` clean; `vite build` succeeds.
- Browser checks of sign-in, portfolio, command centre, clause split view and BOQ screens against the demo data.

Bugs found and fixed during testing: session autoflush caused stale rule lookups; a quote route clash hid the export endpoint; package freeze never matched (JSON key types); clause numbers were off by one; the turnover/similar-work period was taken from a neighbouring clause; the post-login redirect didn't fire; Tailwind's `grid` utility clashed with the table class.

## Session 2: 2026-11-09 (integrations)
Done and verified:
- Tesseract 5.4.0 with eng+hin data in `backend/data/tessdata`; tested on a degraded synthetic scan (Hindi text read, key values extracted).
- LibreOffice conversion of genuine OLE .doc/.xls; originals byte-identical after upload (download and SHA-256 checked).
- Anthropic AI provider: encrypted write-only key, connection test, consent guard, budget and usage log, source-verified interpretations that always need review. Tested against a simulated API only; no live key entered.
- Settings: real dependency checks with actionable messages.
- 30 tests pass; `tsc` clean; `vite build` succeeds. Documents and Clauses tabs checked in the browser on a separate TEST tender.

Bugs found and fixed: identical amounts printed differently ("Rs..12,50,000" from OCR vs "Rs. 12,50,000") were flagged as conflicts; the first similar-work alternative took its period from the previous turnover clause (3 instead of 7 years). Both have regression tests.

Data note: the demo tender's stored "3 similar works … 3 years" rule was extracted before the fix and still shows 3 years. Correct it in the Clauses tab (Correct value) or re-upload the NIT.

## Session 3: 2026-11-09 (Windows installers)
- `installer\TenderPilot-Setup.exe` (full + demo shortcut + backup shortcut) and `installer\TenderPilot-Demo-Setup.exe` (demo only), 136 MB each.
- Verified: silent install, first start, demo auto-seeded with its sign-in shown on the login page, real version starts at first-run setup, bundled OCR and LibreOffice detected, OCR and .doc conversion inside the packaged app, second launch reuses the running copy, backup zip integrity, uninstall removes program and shortcuts but keeps data.
- Fixed while packaging: Python 3.15's `abi3t-compat\python3t.dll` was not bundled (cryptography failed to load); the demo check ran before migrations.
- Not done: code signing (SmartScreen warning); LibreOffice is not bundled.

## Session 4: 2026-11-09 (technical qualifications and JV planning)
- Reads technical criteria: key personnel, plant & machinery, physical quantities, registration class, similar-work definition and the JV sharing clause (`services/technical.py`).
- New rules and evaluators for each; one person / machine is never counted twice; JV sharing suggested from the clause for reviewer confirmation.
- "How to qualify" tab (`services/planner.py`, `GET /tenders/{id}/qualification-plan`): compares each company alone and every JV combination, with partner contributions and what is still needed.
- Demo gains a second SYNTHETIC tender (DI pumping main) with two partner companies that qualify only together.
- 37 tests pass (7 new); `tsc` clean; `vite build` succeeds; planner and "Bid this way" checked in the browser.

## Next steps (in priority order)
1. Automated Playwright browser test for the full journey (brief §P15). Manual check only so far.
2. Run the OCR path against a real scanned tender (only synthetic scans so far); tune `MIN_TEXT_CHARS` and confidence.
3. Validate the heuristics on one real complete tender with corrigenda (brief section 4). Add patterns for the gaps found.
4. Apply `precedence_rank` automatically when base documents conflict.
5. PDF AcroForm filling and positioned overlays for scanned forms; rotate/crop/deskew for vault scans.
6. Pixel-exact previews through LibreOffice (conversion of .doc/.xls is done).
6a. Enter an Anthropic key and run one live AI review to confirm the live path.
7. Editable scenario parameters in the UI; personnel and equipment quantitative rules.
8. Retention purge job, malware-scanner hook, Alembic migrations, and an S3 storage adapter.
9. Run `docker compose up` on a machine with Docker and fix any issues.
