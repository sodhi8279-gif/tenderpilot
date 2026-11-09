"""Envelope/upload-slot packaging, preflight, price-leakage scanning, manifest and frozen revisions."""
from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (JV, Approval, ChecklistTask, EnvelopeProfile, EvidenceRecord, ExportManifest, ExportPackage,
                      GeneratedDocument, LegalEntity, QuoteVersion, Requirement, Tender, UploadSlot)
from ..storage import sha256, storage
from .docgen import pdf_signature_info
from .eligibility import blockers
from .money import D, format_inr

PRICE_WORDS = re.compile(r"quoted\s+(?:rate|amount|price)|total\s+bid\s+(?:amount|price|value)|we\s+offer\s+to\s+execute\s+the\s+work\s+for\s+a\s+total", re.I)


def extract_all_text(data: bytes, filename: str) -> str:
    """Text from every part of a file, including hidden sheets, comments, headers/footers."""
    name = filename.lower()
    try:
        if data[:4] == b"%PDF":
            import fitz
            with fitz.open(stream=data, filetype="pdf") as d:
                return "\n".join(p.get_text() for p in d)
        if name.endswith((".xlsx", ".xlsm")):
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(data), data_only=False)
            parts = []
            for ws in wb.worksheets:  # includes hidden and very-hidden sheets
                for row in ws.iter_rows():
                    for c in row:
                        if c.value is not None:
                            parts.append(str(c.value))
                        if c.comment:
                            parts.append(c.comment.text)
            for dn in wb.defined_names.values() if hasattr(wb.defined_names, "values") else []:
                parts.append(str(getattr(dn, "attr_text", "")))
            return "\n".join(parts)
        if name.endswith(".docx"):
            import docx
            d = docx.Document(io.BytesIO(data))
            parts = [p.text for p in d.paragraphs]
            for t in d.tables:
                for r in t.rows:
                    parts.extend(c.text for c in r.cells)
            for s in d.sections:
                parts.extend(p.text for p in s.header.paragraphs)
                parts.extend(p.text for p in s.footer.paragraphs)
            return "\n".join(parts)
    except Exception as exc:  # noqa: BLE001
        return f"__UNREADABLE__ {type(exc).__name__}"
    return ""


def price_tokens(quote: QuoteVersion | None) -> set[str]:
    if quote is None:
        return set()
    toks: set[str] = set()

    def add(v: Decimal):
        if v is None or v < Decimal("100"):
            return  # tiny numbers produce false positives
        toks.add(format_inr(v))
        toks.add(f"{v:.2f}")
        if v == v.to_integral():
            toks.add(format_inr(v, 0))
    add(D(quote.total))
    for ln in quote.snapshot.get("lines", []):
        if ln.get("amount"):
            add(D(ln["amount"]))
        if ln.get("rate") and D(ln["rate"]) >= Decimal("1000"):
            add(D(ln["rate"]))
    return toks


def scan_for_prices(text: str, tokens: set[str]) -> list[str]:
    hits = []
    if text.startswith("__UNREADABLE__"):
        return ["File could not be read for price scanning"]
    norm = text.replace(" ", " ")
    for t in tokens:
        if re.search(rf"(?<![\d,.]){re.escape(t)}(?![\d])", norm):
            hits.append(f"quoted figure {t}")
    m = PRICE_WORDS.search(norm)
    if m:
        hits.append(f"price wording '{m.group(0)}'")
    return hits


def latest_quote(db: Session, tender_id: int) -> QuoteVersion | None:
    return db.scalar(select(QuoteVersion).where(QuoteVersion.tender_id == tender_id).order_by(QuoteVersion.version_no.desc()).limit(1))


def resolve_assignment(db: Session, tender: Tender, a: dict) -> dict:
    """Return {filename, data, source, signature_status, problems}."""
    problems: list[str] = []
    if a["type"] == "generated":
        g = db.get(GeneratedDocument, a["id"])
        if g is None or g.tender_id != tender.id:
            return {"problems": ["Generated document not found"]}
        if g.signed_key:
            data = storage.get(g.signed_key)
            if sha256(data) != g.signed_sha256:
                problems.append("Signed file hash mismatch: signed original changed")
            sig = "digitally_signed" if g.signed_has_digital_signature else "signed_copy_uploaded"
            fname = f"{g.name}.pdf"
        else:
            data = storage.get(g.pdf_key) if g.pdf_key else b""
            sig = "unsigned_draft"
            fname = f"{g.name} (DRAFT).pdf"
            if g.signing_required:
                problems.append(f"'{g.name}' requires signature; no signed upload received")
        if g.missing_fields:
            problems.append(f"'{g.name}' has missing mandatory fields: {', '.join(g.missing_fields[:5])}")
        if g.stale:
            problems.append(f"'{g.name}' is stale (tender/requirements changed); regenerate")
        if g.certification_required and g.status != "verified":
            problems.append(f"'{g.name}' needs external certification ({g.certification_required}) and verification")
        return {"filename": fname, "data": data, "signature_status": sig, "problems": problems,
                "source": {"type": "generated", "id": g.id, "revision": g.revision, "status": g.status}}
    if a["type"] == "evidence":
        e = db.get(EvidenceRecord, a["id"])
        if e is None or e.org_id != tender.org_id or not e.storage_key:
            return {"problems": ["Evidence file not found"]}
        allowed_entities = {tender.bidder_entity_id}
        if tender.bidder_type == "jv" and tender.jv_id:
            allowed_entities |= {m.legal_entity_id for m in db.get(JV, tender.jv_id).members}
        if e.legal_entity_id not in allowed_entities:
            problems.append(f"'{e.title}' belongs to a different legal entity")
        if e.verification_state != "verified":
            problems.append(f"'{e.title}' is not verified")
        if tender.qualification_cutoff and e.expiry_date and e.expiry_date < tender.qualification_cutoff:
            problems.append(f"'{e.title}' expired {e.expiry_date} before cutoff {tender.qualification_cutoff}")
        data = storage.get(e.storage_key)
        if sha256(data) != e.sha256:
            problems.append(f"'{e.title}' file hash mismatch")
        sig = "not_required"
        if data[:4] == b"%PDF" and pdf_signature_info(data).get("digital_signatures"):
            sig = "digitally_signed_original_preserved"
        return {"filename": e.filename or f"{e.title}.pdf", "data": data, "signature_status": sig, "problems": problems,
                "source": {"type": "evidence", "id": e.id, "version": e.version, "sha256": e.sha256}}
    if a["type"] == "quote_export":
        qv = db.get(QuoteVersion, a["id"])
        if qv is None or qv.tender_id != tender.id or not qv.export_key:
            return {"problems": ["Priced workbook export not found"]}
        if qv.status != "approved":
            problems.append(f"Quote v{qv.version_no} is {qv.status}, not approved")
        latest = latest_quote(db, tender.id)
        if latest and latest.id != qv.id:
            problems.append("A newer quote version exists")
        if not qv.export_report.get("ok"):
            problems.append("Workbook verification did not pass")
        return {"filename": qv.export_report.get("filename", f"BOQ_priced_v{qv.version_no}.xlsx"),
                "data": storage.get(qv.export_key), "signature_status": "not_required", "problems": problems,
                "source": {"type": "quote_export", "id": qv.id, "hash": qv.snapshot_hash}}
    return {"problems": [f"Unknown assignment type {a['type']}"]}


def _ext(fn: str) -> str:
    return fn.rsplit(".", 1)[-1].lower() if "." in fn else ""


def run_preflight(db: Session, tender: Tender) -> dict:
    checks: list[dict] = []

    def add(cid, title, ok, details=None, warn=False):
        checks.append({"id": cid, "title": title, "status": "pass" if ok else ("warn" if warn else "fail"),
                       "details": details or []})

    b = blockers(db, tender)
    crit = [x["message"] for x in b["blockers"] if x["type"] in ("conflict", "eligibility", "rule_unconfirmed", "not_evaluated", "low_confidence")]
    add("eligibility", "Eligibility confirmed, no unresolved conflicts or failures", not crit, crit)
    open_tasks = [x["message"] for x in b["blockers"] if x["type"] in ("task", "stale_task")]
    add("tasks", "All mandatory checklist items closed", not open_tasks, open_tasks)

    profile = db.scalar(select(EnvelopeProfile).where(EnvelopeProfile.tender_id == tender.id))
    add("envelope_profile", "Envelope profile defined from tender/portal instructions", profile is not None and bool(profile.envelopes),
        [] if profile else ["Define envelopes and upload slots"])
    slots = list(db.scalars(select(UploadSlot).where(UploadSlot.tender_id == tender.id)))
    quote = latest_quote(db, tender.id)
    tokens = price_tokens(quote)
    slot_issues, leak_issues, file_issues, sig_issues = [], [], [], []
    resolved: list[tuple[UploadSlot, dict]] = []
    for s in slots:
        if s.mandatory and not s.assignments:
            slot_issues.append(f"Mandatory slot '{s.name}' ({s.envelope}) is empty")
        if len(s.assignments) > s.max_count:
            slot_issues.append(f"Slot '{s.name}' allows {s.max_count} file(s); {len(s.assignments)} assigned")
        for a in s.assignments:
            r = resolve_assignment(db, tender, a)
            if "data" not in r:
                file_issues.extend(r["problems"])
                continue
            resolved.append((s, r))
            file_issues.extend(r["problems"])
            ext = _ext(r["filename"])
            if s.allowed_types and ext not in [t.lower() for t in s.allowed_types]:
                slot_issues.append(f"'{r['filename']}' type .{ext} not allowed in slot '{s.name}' ({', '.join(s.allowed_types)})")
            if s.max_size_mb is not None and len(r["data"]) > D(s.max_size_mb) * 1024 * 1024:
                slot_issues.append(f"'{r['filename']}' is {len(r['data']) / 1048576:.2f} MB > {s.max_size_mb} MB limit "
                                   "(not auto-compressed: reduce scan resolution manually while keeping legibility)")
            if s.signature_required and r["signature_status"] in ("unsigned_draft", "not_required") and a["type"] != "evidence":
                sig_issues.append(f"'{r['filename']}' in slot '{s.name}' must be signed")
            if s.envelope != "financial":
                hits = scan_for_prices(extract_all_text(r["data"], r["filename"]), tokens)
                if hits:
                    leak_issues.append(f"'{r['filename']}' in {s.envelope} envelope: {', '.join(hits[:3])}")
            if r["data"][:4] == b"%PDF":
                info = pdf_signature_info(r["data"])
                if not info.get("pages"):
                    file_issues.append(f"'{r['filename']}' could not be opened as PDF")
    add("slots", "Upload slots complete; file types, counts and sizes within limits", not slot_issues, slot_issues)
    add("files", "Files verified (entity, validity, hashes, generated-document status)", not file_issues, file_issues)
    add("signatures", "Required signatures/certifications received", not sig_issues, sig_issues)
    add("price_leakage", "No prices in technical/fee envelopes", not leak_issues, leak_issues)

    if quote is None:
        add("quote", "Financial quote approved and exported", False, ["No quote prepared"])
    else:
        q_issues = []
        if quote.status != "approved":
            q_issues.append(f"Latest quote v{quote.version_no} is {quote.status}")
        if not quote.export_key:
            q_issues.append("Priced workbook not exported")
        valid_appr = db.scalar(select(func.count()).select_from(Approval).where(
            Approval.subject_type == "quote", Approval.subject_id == quote.id, Approval.valid.is_(True),
            Approval.decision == "approved", Approval.stage == "approval", Approval.subject_hash == quote.snapshot_hash))
        if not valid_appr:
            q_issues.append("No valid approver sign-off for the current quote snapshot")
        fin_slots = [s for s in slots if s.envelope == "financial"]
        if fin_slots and not any(a["type"] == "quote_export" for s in fin_slots for a in s.assignments):
            q_issues.append("Priced BOQ not assigned to a financial upload slot")
        add("quote", "Financial quote approved and exported", not q_issues, q_issues)

    emd = db.scalar(select(Requirement).where(Requirement.tender_id == tender.id, Requirement.key == "commercial.emd"))
    if emd:
        has = any(r["source"].get("type") == "evidence" and db.get(EvidenceRecord, r["source"]["id"]).category in ("emd_proof", "fee_proof")
                  for _, r in resolved)
        add("fee_security", "EMD / fee evidence attached", has, [] if has else ["Attach EMD/bid security proof to its slot"])
    ent = db.get(LegalEntity, tender.bidder_entity_id) if tender.bidder_entity_id else None
    if tender.bidder_type == "jv" and tender.jv_id:
        lead = next((m for m in db.get(JV, tender.jv_id).members if m.role == "lead"), None)
        ent = db.get(LegalEntity, lead.legal_entity_id) if lead else None
    add("signatory", "Bidder legal entity and authorised signatory recorded",
        bool(ent and ent.signatory_name and ent.legal_name), [] if ent and ent.signatory_name else ["Record the authorised signatory"])
    if tender.deadline_at:
        now = datetime.now(timezone.utc)
        dl = tender.deadline_at if tender.deadline_at.tzinfo else tender.deadline_at.replace(tzinfo=timezone.utc)
        add("deadline", "Submission deadline not passed", dl > now, [f"Deadline {tender.deadline_at.isoformat()}"])
    else:
        add("deadline", "Submission deadline confirmed", False, ["Deadline not confirmed"])
    if profile and profile.zip_accepted is not True:
        checks.append({"id": "zip_note", "title": "Portal ZIP acceptance", "status": "warn",
                       "details": ["Portal ZIP acceptance not confirmed: upload files individually per slot."]})
    failed = [c for c in checks if c["status"] == "fail"]
    return {"checks": checks, "final_ready": not failed, "failed": len(failed),
            "ran_at": datetime.now(timezone.utc).isoformat(), "_resolved": resolved}


def _upstream(db: Session, tender: Tender) -> dict:
    q = latest_quote(db, tender.id)
    # string keys: the snapshot is stored as JSON and compared after a round-trip
    gens = {str(g.id): g.revision for g in db.scalars(select(GeneratedDocument).where(GeneratedDocument.tender_id == tender.id))}
    return {"tender_version": tender.current_version, "quote": {"id": q.id, "hash": q.snapshot_hash} if q else None,
            "generated": gens}


def build_package(db: Session, tender: Tender, user_id: int, final: bool) -> ExportPackage:
    pf = run_preflight(db, tender)
    resolved = pf.pop("_resolved")
    if final and not pf["final_ready"]:
        raise ValueError("Preflight has failing checks; only a DRAFT package can be exported.")
    rev = (db.scalar(select(func.max(ExportPackage.revision)).where(ExportPackage.tender_id == tender.id)) or 0) + 1
    for old in db.scalars(select(ExportPackage).where(ExportPackage.tender_id == tender.id, ExportPackage.status.in_(["draft", "final_ready"]))):
        old.status = "superseded"
    pkg = ExportPackage(tender_id=tender.id, revision=rev, status="final_ready" if final else "draft", preflight=pf,
                        upstream=_upstream(db, tender), created_by=user_id)
    db.add(pkg)
    db.flush()
    zbuf = io.BytesIO()
    prefix = "" if final else "DRAFT_"
    names_used: set[str] = set()
    with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as z:
        for slot, r in resolved:
            base = slot.naming.replace("{slot}", slot.name) if slot.naming else r["filename"]
            if slot.naming and "." not in base:
                base += "." + _ext(r["filename"])
            fname = re.sub(r"[^\w.\- ()]", "_", prefix + base)
            while fname in names_used:
                fname = "_" + fname
            names_used.add(fname)
            path = f"{slot.envelope}/{fname}"
            # signed PDFs are stored byte-for-byte; ZIP_STORED keeps them untouched inside the container too
            z.writestr(zipfile.ZipInfo(path), r["data"], compress_type=zipfile.ZIP_STORED if r["data"][:4] == b"%PDF" else zipfile.ZIP_DEFLATED)
            key = storage.put(tender.org_id, r["data"], _ext(fname) or "bin")
            pages = pdf_signature_info(r["data"]).get("pages") if r["data"][:4] == b"%PDF" else None
            db.add(ExportManifest(package_id=pkg.id, filename=fname, slot=slot.name, envelope=slot.envelope, size=len(r["data"]),
                                  pages=pages, sha256=sha256(r["data"]), storage_key=key, source=r["source"],
                                  signature_status=r["signature_status"], preparer_id=user_id))
    pkg.zip_key = storage.put(tender.org_id, zbuf.getvalue(), "zip")
    db.flush()
    db.refresh(pkg)
    pkg.package_hash = hashlib.sha256(json.dumps(sorted((e.envelope, e.filename, e.sha256) for e in pkg.entries)).encode()
                                      + json.dumps(pkg.upstream, sort_keys=True).encode()).hexdigest()
    pkg.guide_key = storage.put(tender.org_id, portal_guide(tender, pkg).encode("utf-8"), "txt")
    return pkg


def portal_guide(tender: Tender, pkg: ExportPackage) -> str:
    lines = [f"TenderPilot portal submission guide — {tender.title} ({tender.tender_ref})",
             f"Package revision {pkg.revision} — status {pkg.status.upper()}", f"Package hash {pkg.package_hash}", "",
             "TenderPilot does NOT submit bids, sign with DSC, pay fees, or solve OTP/CAPTCHA. Do these yourself on the portal.", ""]
    for env in sorted({e.envelope for e in pkg.entries}):
        lines.append(f"== {env.upper()} envelope ==")
        for e in [x for x in pkg.entries if x.envelope == env]:
            lines.append(f"  Slot: {e.slot}\n    File: {e.filename}  ({e.size:,} bytes, {e.pages or '-'} pages)\n"
                         f"    SHA-256: {e.sha256}\n    Signature: {e.signature_status}")
        lines.append("")
    lines += ["After final submission, download the portal acknowledgement and record it in TenderPilot",
              "(Export history → Record receipt). Saving this ZIP is NOT submission."]
    return "\n".join(lines)


def is_package_current(db: Session, tender: Tender, pkg: ExportPackage) -> bool:
    return pkg.upstream == _upstream(db, tender)
