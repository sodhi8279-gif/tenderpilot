# Feature status (as of 2026-11-09)

Legend: **Done**, meaning implemented and covered by automated tests and/or checked in a browser. **Partial**, meaning it works with stated limits. **Adapter pending**, meaning the interface exists but needs installation or configuration. **Not started**.

## Phase 1: foundation, vault, intake, ingestion, source-linked review
| Feature | Status | Notes |
|---|---|---|
| Auth (bcrypt + JWT), 8 roles, server-side permissions, org isolation | Done | Tested: cross-org 404s, price role restriction, forged token rejected |
| Company vault: entities, JVs, evidence (immutable bytes, hash, versions, verification, masking) | Done | Camera capture on mobile via file input. Physical scanner control: not started |
| Rotate/crop/deskew of scans | Not started | Originals are preserved; image editing is planned |
| Tender intake (minimal form), multi-file upload | Done | |
| Format sniffing, ZIP limits and traversal checks, password-protected PDF flow | Done | Tested |
| Page-level tracking (processed/failed/unreadable/duplicate/excluded) | Done | Tested with a mixed text and scanned fixture |
| OCR | Adapter pending | Tesseract adapter with English and Hindi; autodetected. Not installed on the build machine, so scanned pages show as unreadable |
| Legacy .xls/.doc conversion | Adapter pending | Needs LibreOffice; marked "needs conversion" |
| Heuristic extraction (deadlines, EMD, fee, estimate, validity, completion, PS, LD, retention, DLP, PVC, turnover, net worth, solvency, bid capacity, similar works, JV, envelopes, documents, precedence) | Done | Genuinely parses uploads; confidence plus exact excerpt, clause, page and bbox |
| AI extraction provider | Adapter pending | Anthropic provider coded with verbatim-excerpt validation; off unless a key is set and the admin consents. Not exercised live in this session |
| Clause/source split view with highlight | Done | Browser-checked |
| Facts vs derived vs interpretation vs question separation | Done | `nature` field; manual entries cannot be marked "extracted" |

## Phase 2: rules, eligibility, checklist, amendments
| Feature | Status | Notes |
|---|---|---|
| Versioned rules, heuristic-proposed, reviewer-confirmed; low-confidence values must be re-entered | Done | Tested |
| Pass / Fail / Insufficient / Ambiguous / N/A; unknown never passes; overrides cannot erase failures | Done | Tested |
| Turnover (last N / best N of M), similar works (period, threshold, substantial completion, optional authorised escalation), net worth, solvency, documents, bid capacity | Done | Tested for turnover, similar work, documents and solvency |
| Cutoff-date validity (never today) | Done | Tested |
| JV: permitted, members, lead share, aggregation modes (sum with minima / lead only / each / any); never assumed additive | Done | Tested |
| Attribution control for similar names | Done | Tested |
| Corrigendum: scoped changes, history kept, conflicts block, dependents invalidated | Done | Tested |
| Order-of-precedence ranking | Partial | Clause located; reviewer records a rank per document; not yet applied automatically to conflicts |
| Personnel / equipment / OEM / local establishment criteria | Partial | Handled as document-presence rules; quantitative checks not started |
| Dynamic checklist with targeted questions, owners, due dates, history | Done | |

## Phase 3: documents and technical
| Feature | Status | Notes |
|---|---|---|
| Built-in letters (cover, bid form, undertaking, experience list, POA/JV drafts, clarification, financial letter) | Done | Drafts clearly labelled; missing fields highlighted and blocking |
| Tender DOCX forms with {{placeholders}} and confirmed mappings | Done | |
| PDF AcroForm / positioned overlays on scanned forms | Not started | Convert the form to DOCX for now |
| Preview PDF and layout QA | Partial | Built-in renderer (text and tables). Pixel-exact conversion needs LibreOffice |
| Signature/certification tracking, signed-PDF immutability, digital-signature detection | Done | DSC certificate validation is not performed |
| Technical compliance matrix, deviations, engineering-review flags, consistency check | Done | Method statement and programme are free text only |

## Phase 4: BOQ and pricing
| Feature | Status | Notes |
|---|---|---|
| Item-rate BOQ import (layout detection, duplicates, units, trade, locked cells) | Done | Percentage and lump-sum supported in the quote; import assumes a tabular sheet |
| Rate sources with provenance; assumptions flagged | Done | Live market research: not connected (stated in the UI) |
| Rate analysis (wastage, overheads, margin vs markup, recoverable vs non-recoverable GST) | Done | Tested against independent values |
| Quote freeze, estimator review, independent approval, invalidation on change | Done | Tested |
| Official workbook write-back with verification; fail-closed for macros, links, charts, locked cells, .xls | Done | Tested |
| Scenarios (rate change, delay, payment cycle, retention) | Partial | Fixed demo cases in the UI; parameters editable via the API |

## Phase 5: packaging
| Feature | Status | Notes |
|---|---|---|
| Envelope profile, upload slots (types, size, count, naming, signature) | Done | Slot rules are entered from portal instructions, not auto-extracted |
| Preflight (eligibility, tasks, slots, signatures, price leakage incl. hidden sheets, quote approval, EMD, signatory, deadline) | Done | Tested |
| Draft vs final, frozen revisions with hashes, manifest, portal guide, local ZIP with separate envelopes | Done | Tested |
| Receipt recording from portal evidence | Done | Tested |
| Portal adapters / auto-submission | Not started by design | |

## Phase 6: hardening
| Feature | Status | Notes |
|---|---|---|
| Acceptance tests (brief §P) | Done | 14 automated tests pass. The browser journey (§P15) was checked manually; an automated Playwright test is not written yet |
| Accessibility basics (focus, contrast, text with colour, keyboard, labels) | Partial | Not audited with a screen reader |
| Backup script, deployment guide | Done | `scripts/backup.ps1`, `docs/OPERATIONS.md` |
| Docker Compose | Partial | Written but **not run**: Docker was not available |
| Malware scanning, SSO/MFA, email/WhatsApp notifications | Adapter pending | In-app notifications only |
| Hindi UI labels | Not started | Hindi is recognised only through OCR language packs |
