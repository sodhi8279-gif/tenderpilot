# Progress log

## Session 1: 2026-11-09
Built phases 1–5 as working software, plus parts of phase 6. See `docs/FEATURE_STATUS.md` for the honest per-feature status.

Verified:
- `backend`: `pytest`, 14 acceptance tests passing (brief §P items 1–14).
- `frontend`: `tsc -b` clean; `vite build` succeeds.
- Browser checks of sign-in, portfolio, command centre, clause split view and BOQ screens against the demo data.

Bugs found and fixed during testing: session autoflush caused stale rule lookups; a quote route clash hid the export endpoint; package freeze never matched (JSON key types); clause numbers were off by one; the turnover/similar-work period was taken from a neighbouring clause; the post-login redirect didn't fire; Tailwind's `grid` utility clashed with the table class.

## Next steps (in priority order)
1. Automated Playwright browser test for the full journey (brief §P15). Manual check only so far.
2. Install Tesseract and run the OCR path against a real scanned tender; tune `MIN_TEXT_CHARS` and confidence.
3. Validate the heuristics on one real complete tender with corrigenda (brief section 4). Add patterns for the gaps found.
4. Apply `precedence_rank` automatically when base documents conflict.
5. PDF AcroForm filling and positioned overlays for scanned forms; rotate/crop/deskew for vault scans.
6. LibreOffice adapter for .xls/.doc and pixel-exact previews.
7. Editable scenario parameters in the UI; personnel and equipment quantitative rules.
8. Retention purge job, malware-scanner hook, Alembic migrations, and an S3 storage adapter.
9. Run `docker compose up` on a machine with Docker and fix any issues.
