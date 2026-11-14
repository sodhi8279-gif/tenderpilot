# Feature status (as of 2026-11-14)

Legend: **Done**, meaning implemented and covered by automated tests and/or checked in a browser. **Partial**, meaning it works with stated limits. **Adapter pending**, meaning the interface exists but needs installation or configuration. **Not started**.

## Phase 1: foundation, vault, intake, ingestion, source-linked review
| Feature | Status | Notes |
|---|---|---|
| Auth (bcrypt + JWT), 8 roles, server-side permissions, org isolation | Done | Tested: cross-org 404s, price role restriction, forged token rejected |
| Company vault: entities, JVs, evidence (immutable bytes, hash, versions, verification, masking) | Done | Camera capture on mobile via file input. Physical scanner control: not started |
| Rotate/crop/deskew of scans | Done | Vault "Clean up scan": rotate, crop and automatic straightening into a cleaned PDF copy. Original file, hash, verification and version never change; use Replace to make a cleaned copy the new version |
| Tender intake (minimal form), multi-file upload | Done | |
| Format sniffing, ZIP limits and traversal checks, password-protected PDF flow | Done | Tested |
| Page-level tracking (processed/failed/unreadable/duplicate/excluded) | Done | Tested with a mixed text and scanned fixture |
| OCR (English + Hindi) | Done | Tesseract 5.4.0 installed; language data in `backend/data/tessdata` (eng, hin, osd), order `eng+hin`. Verified on a degraded synthetic scan (skew, blur, speckle, JPEG): Hindi read, EMD, estimate, turnover, similar works and deadline extracted. Not yet tried on a real scanner image |
| Legacy .doc/.xls conversion | Done | LibreOffice (headless, private profile). Text is read from a converted copy stored separately; the original file, its SHA-256 and its download stay byte-identical (tested). Without LibreOffice the document is marked "needs conversion" |
| Heuristic extraction (deadlines, EMD, fee, estimate, validity, completion, PS, LD, retention, DLP, PVC, turnover, net worth, solvency, bid capacity, similar works, JV, envelopes, documents, precedence) | Done | Genuinely parses uploads; confidence plus exact excerpt, clause, page and bbox |
| AI interpretation provider (Anthropic) | Partial | Settings: provider/model/cost table, encrypted write-only key, connection test, consent (only after a passed test), monthly budget, usage log. AI items are interpretations with a verbatim source excerpt, always need review, and never set thresholds, dates or amounts. Tested with a simulated API; **not exercised against the live API** because no key has been entered |
| Clause/source split view with highlight | Done | Browser-checked |
| Settings: dependency checks | Done | Database, storage, worker, OCR, converter, AI, frontend build, backup; each with a real self-test, status and "what to do" |
| Facts vs derived vs interpretation vs question separation | Done | `nature` field; manual entries cannot be marked "extracted" |

## Phase 2: rules, eligibility, checklist, amendments
| Feature | Status | Notes |
|---|---|---|
| Versioned rules, heuristic-proposed, reviewer-confirmed; low-confidence values must be re-entered | Done | Tested |
| Pass / Fail / Insufficient / Ambiguous / N/A; unknown never passes; overrides cannot erase failures | Done | Tested |
| Turnover (last N / best N of M), similar works (period, threshold, substantial completion, optional authorised escalation), net worth, solvency, documents, bid capacity | Done | Tested for turnover, similar work, documents and solvency |
| Cutoff-date validity (never today) | Done | Tested |
| JV: permitted, members, lead share, aggregation modes (sum with minima / lead only / each / any); never assumed additive | Done | Tested |
| Technical qualification extraction (technical-v1): key personnel (role, number, qualification level, discipline, years), plant & machinery (type, number, capacity, owned/leased), physical quantities (item, quantity, unit, any-one-year/cumulative, period, pipe dia/material), registration class, similar-work definition, JV sharing clause (lead %/member %, combined, lead-alone similar work, each-member net worth) | Done | Tested on synthetic NITs and common line layouts only; real tenders with tables split across pages may need tuning |
| General and specific experience (technical-v2): 'A) General Experience' alternatives (1 x 80% / 2 x 50% / 3 x 40%, completed or ongoing) with the kind of work and minimum sizes (height/length/depth...), 'B) Specific Experience' items (kind of work + sizes, optional value), JV notes per criterion (aggregated / any one partner / lead alone); machinery lists with i./ii. markers, several machines per line and 'of required capacity'; manpower in running text ('01 no. Civil Engineer with 05 years'); estimate and contractor class read from the NIT table; ban on foreign JVs no longer read as a ban on JVs | Done | Tested on a synthetic copy of a real state-PSU NIT layout and on the real NIT (read-only). Certificates are checked for period, value, sizes (from the scope/dimensions fields) and kind of work; similarity is confirmed per tender by a reviewer in the eligibility matrix |
| Re-reading after a reader upgrade | Done | Only on request (Documents tab) unless AUTO_REREAD=1; unreviewed readings are refreshed, reviewed values are kept with the new reading offered for comparison |
| Tables continued on the next page (reader-3) | Done | Personnel, machinery and quantity rows below no heading on the next page are read under the heading still open at the foot of the previous page; a row cut by the page break is joined. Tested on synthetic layouts only |
| Data safety on update | Done | Database copied before every migration; additive migrations only; `backend/verify_upgrade.py` checks a real database copy row by row |
| Technical eligibility rules: personnel (one person counted once across posts), equipment (capacity units, ownership, lease validity), physical quantities (FY window, unit conversion km→m, lakh cum), registration class | Done | Evidence categories: personnel, machinery, executed_quantity, registration (with class) |
| JV sharing per criterion suggested from the JV clause (pre-filled for reviewer confirmation, never applied automatically); new mode "each member meets its % share" for similar work; pooled personnel/equipment/quantities with member minima | Done | Tested |
| Qualification planner ("How to qualify" tab): each criterion vs each company alone, every single-company and JV combination (lead choice, minimum shares) ranked, per-partner contributions, still-needed list, one-click "Bid this way" | Done | Up to 8 entities and 3-member JVs; unconfirmed JV sharing rules are labelled |
| Attribution control for similar names | Done | Tested |
| Corrigendum: scoped changes, history kept, conflicts block, dependents invalidated | Done | Tested |
| Order-of-precedence ranking | Done | Ranks suggested from the clause; rank editor on the Documents tab; governing value shown on each base-document conflict and applied when a reviewer clicks. Ties and unranked documents stay with the reviewer |
| OEM / local establishment criteria | Partial | Handled as document-presence rules (personnel and equipment are now quantitative, see above) |
| Dynamic checklist with targeted questions, owners, due dates, history | Done | |

## Phase 3: documents and technical
| Feature | Status | Notes |
|---|---|---|
| Built-in letters (cover, bid form, undertaking, experience list, POA/JV drafts, clarification, financial letter) | Done | Drafts clearly labelled; missing fields highlighted and blocking |
| Tender DOCX forms with {{placeholders}} and confirmed mappings | Done | |
| PDF AcroForm / positioned overlays on scanned forms | Done | Fillable PDFs: fields mapped and filled in a copy. Flat/scanned PDFs: click to place text boxes. Latin text only (Helvetica); Hindi values need a DOCX form |
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
| Scenarios (rate change, delay, payment cycle, retention) | Done | Editable cases in the UI (per trade, delay, time cost, payment cycle, retention, interest), checked inputs, remembered per tender in the browser |

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
| Acceptance tests (brief §P) | Done | 30 automated tests pass (14 acceptance, 16 OCR/conversion/AI/settings). Browser journey (§P15) automated: `E2E=1 pytest tests/test_e2e_journey.py` (69 tests in the default suite) |
| Accessibility basics (focus, contrast, text with colour, keyboard, labels) | Partial | Not audited with a screen reader |
| Backup script, deployment guide | Done | `scripts/backup.ps1`, `docs/OPERATIONS.md` |
| Docker Compose | Partial | Written but **not run**: Docker was not available |
| Malware scanning, SSO/MFA, email/WhatsApp notifications | Adapter pending | In-app notifications only |
| Hindi UI labels | Partial | English/हिन्दी switch for navigation, tabs, steps, statuses, panel titles and common buttons; detailed help texts are still English |
