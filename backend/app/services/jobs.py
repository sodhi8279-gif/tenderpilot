"""Durable, idempotent job queue stored in the database (no Redis required).

* Jobs have a unique idempotency key: re-enqueueing returns the existing job.
* Page-level progress is committed after every page, so a crashed/restarted worker resumes
  from the first unfinished page (processed pages are never re-done or duplicated).
* Cancellation is cooperative between pages. Failures retry up to max_attempts.
A Redis/RQ adapter can replace `Worker` without changing callers (see docs/ARCHITECTURE.md).
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..audit import audit, notify
from ..config import settings
from ..db import SessionLocal
from ..models import ExtractionJob, Organization, SourceAnchor, SourceDocument, SourcePage, Tender, TenderVersion
from ..storage import sha256, storage
from . import ai, convert, ingest, secretbox
from .extract import READER_VERSION, extract_continuations, extract_from_page, find_bbox, merge_candidates
from .requirements import apply_candidates, bump_tender_version, sync_tender_facts

log = logging.getLogger("tenderpilot.jobs")

# Passwords for protected PDFs live only in memory, only until the job uses them. Never stored or logged.
_PASSWORDS: dict[int, str] = {}
STALE_LOCK = timedelta(minutes=10)


class Cancelled(Exception):
    pass


def set_password(doc_id: int, password: str) -> None:
    _PASSWORDS[doc_id] = password


def extract_key(doc: SourceDocument) -> str:
    """Includes the reader version, so a document read by an older reader is read again after an upgrade."""
    return f"extract:{doc.id}:{doc.sha256}:{doc.doc_class}:{READER_VERSION}"


def outdated_documents(db: Session, tender_id: int | None = None) -> list[SourceDocument]:
    """Processed documents last read by an older reader version."""
    read_before = set(db.scalars(select(ExtractionJob.document_id).where(
        ExtractionJob.kind == "extract_requirements", ExtractionJob.status == "succeeded")))
    q = select(SourceDocument).where(SourceDocument.status == "processed", SourceDocument.id.in_(read_before or {-1}))
    if tender_id is not None:
        q = q.where(SourceDocument.tender_id == tender_id)
    return [d for d in db.scalars(q)
            if db.scalar(select(ExtractionJob.id).where(ExtractionJob.idempotency_key == extract_key(d))) is None]


def requeue_outdated_extractions(db: Session, tender_id: int | None = None) -> int:
    """Queue documents read by an older reader to be read again by the current one. Runs at start-up only when
    AUTO_REREAD=1, otherwise only when a user asks for one tender. Reviewed values are kept
    (see apply_candidates(reread=True)); only unreviewed machine readings are refreshed."""
    n = 0
    read_before = set(db.scalars(select(ExtractionJob.document_id).where(
        ExtractionJob.kind == "extract_requirements", ExtractionJob.status == "succeeded")))
    q = select(SourceDocument).where(SourceDocument.status == "processed", SourceDocument.id.in_(read_before or {-1}))
    if tender_id is not None:
        q = q.where(SourceDocument.tender_id == tender_id)
    for doc in db.scalars(q):
        key = extract_key(doc)
        if db.scalar(select(ExtractionJob.id).where(ExtractionJob.idempotency_key == key)) is None:
            enqueue(db, kind="extract_requirements", org_id=doc.org_id, tender_id=doc.tender_id, document_id=doc.id, key=key)
            n += 1
    db.commit()
    return n


def enqueue(db: Session, *, kind: str, org_id: int, tender_id: int, document_id: int | None, key: str) -> ExtractionJob:
    job = db.scalar(select(ExtractionJob).where(ExtractionJob.idempotency_key == key))
    if job:
        return job
    job = ExtractionJob(kind=kind, org_id=org_id, tender_id=tender_id, document_id=document_id, idempotency_key=key)
    db.add(job)
    db.flush()
    return job


def retry(db: Session, job: ExtractionJob) -> None:
    if job.status in ("failed", "cancelled"):
        job.status, job.attempts, job.error, job.cancel_requested = "queued", 0, None, False


def claim_next(db: Session) -> ExtractionJob | None:
    now = datetime.now(timezone.utc)
    job = db.scalar(select(ExtractionJob).where(or_(
        ExtractionJob.status == "queued",
        (ExtractionJob.status == "running") & (ExtractionJob.locked_at < now - STALE_LOCK),  # resume abandoned
    )).order_by(ExtractionJob.id).limit(1))
    if job is None:
        return None
    job.status, job.locked_at = "running", now
    job.attempts += 1
    db.commit()
    return job


def run_job(db: Session, job: ExtractionJob) -> None:
    try:
        if job.cancel_requested:
            raise Cancelled()
        if job.kind == "ingest_document":
            _ingest(db, job)
        elif job.kind == "extract_requirements":
            _extract(db, job)
        elif job.kind == "ai_extract":
            _ai_extract(db, job)
        else:
            raise ValueError(f"Unknown job kind {job.kind}")
        job.status, job.error = "succeeded", None
        db.commit()
    except Cancelled:
        db.rollback()
        job = db.get(ExtractionJob, job.id)
        job.status, job.error = "cancelled", "Cancelled by user"
        db.commit()
    except Exception as exc:  # noqa: BLE001 - job boundary
        db.rollback()
        job = db.get(ExtractionJob, job.id)
        msg = secretbox.redact(f"{type(exc).__name__}: {str(exc)[:300]}")
        job.error = msg
        # Retrying cannot fix an unsafe file, a missing/disabled provider, a rejected key or an exhausted budget.
        permanent = isinstance(exc, (ingest.UnsafeFile, ai.AIUnavailable))
        job.status = "queued" if job.attempts < job.max_attempts and not permanent else "failed"
        if job.status == "failed" and job.document_id:
            doc = db.get(SourceDocument, job.document_id)
            if doc and doc.status == "processing":
                doc.status, doc.status_reason = "failed", msg
        db.commit()
        log.warning("job %s failed attempt %s: %s", job.id, job.attempts, msg)


def run_pending(max_jobs: int = 1000) -> int:
    """Synchronously drain the queue (used by tests and the CLI)."""
    n = 0
    while n < max_jobs:
        with SessionLocal() as db:
            job = claim_next(db)
            if job is None:
                return n
            run_job(db, job)
            n += 1
    return n


class Worker(threading.Thread):
    def __init__(self, interval: float = 1.0):
        super().__init__(daemon=True, name="tenderpilot-worker")
        self.interval = interval
        self._stop_event = threading.Event()

    def run(self) -> None:  # pragma: no cover - background loop
        while not self._stop_event.is_set():
            try:
                if run_pending(max_jobs=5) == 0:
                    time.sleep(self.interval)
            except Exception:  # noqa: BLE001
                log.exception("worker loop error")
                time.sleep(self.interval * 5)

    def stop(self) -> None:
        self._stop_event.set()


# ------------------------------------------------------------------ handlers
def _check_cancel(db: Session, job: ExtractionJob) -> None:
    db.refresh(job, attribute_names=["cancel_requested"])
    if job.cancel_requested:
        raise Cancelled()


def _store_pages(db: Session, doc: SourceDocument, pages: list[ingest.PageOut]) -> None:
    existing = {p.page_index: p for p in db.scalars(select(SourcePage).where(SourcePage.document_id == doc.id))}
    for po in pages:
        sp = existing.get(po.index) or SourcePage(document_id=doc.id, page_index=po.index)
        sp.page_label, sp.status, sp.reason, sp.text = po.label, po.status, po.reason, po.text
        sp.text_source, sp.confidence = po.text_source, po.confidence
        sp.text_hash = ingest.text_hash(po.text) if po.text else None
        db.add(sp)
    doc.page_count = len(pages)


def _ingest(db: Session, job: ExtractionJob) -> None:
    doc = db.get(SourceDocument, job.document_id)
    tender = db.get(Tender, doc.tender_id)
    data = storage.get(doc.storage_key)
    doc.status = "processing"
    db.commit()
    ocr = ingest.get_ocr()

    if doc.kind == "zip":
        members = ingest.safe_zip_members(data)
        job.progress_total = len(members)
        for name, blob in members:
            h = sha256(blob)
            try:
                kind = ingest.sniff_kind(blob, name)
            except ingest.UnsafeFile as exc:
                child = SourceDocument(org_id=doc.org_id, tender_id=doc.tender_id, filename=name, kind="unknown", sha256=h,
                                       size=len(blob), storage_key=storage.put(doc.org_id, blob, "bin"), parent_id=doc.id,
                                       status="rejected", status_reason=str(exc))
                db.add(child)
                continue
            dup = db.scalar(select(SourceDocument).where(SourceDocument.tender_id == doc.tender_id, SourceDocument.sha256 == h,
                                                         SourceDocument.id != doc.id))
            if dup:
                job.progress_done += 1
                continue
            ext = name.rsplit(".", 1)[-1] if "." in name else "bin"
            child = SourceDocument(org_id=doc.org_id, tender_id=doc.tender_id, filename=name, kind=kind, sha256=h,
                                   size=len(blob), storage_key=storage.put(doc.org_id, blob, ext), parent_id=doc.id,
                                   doc_class=doc.doc_class if doc.doc_class != "unclassified" else "unclassified",
                                   is_amendment=doc.is_amendment, status="uploaded")
            db.add(child)
            db.flush()
            enqueue(db, kind="ingest_document", org_id=doc.org_id, tender_id=doc.tender_id, document_id=child.id,
                    key=f"ingest:{child.id}:{h}")
            job.progress_done += 1
        doc.status, doc.status_reason = "container", f"ZIP expanded into {len(members)} file(s)"
        db.commit()
        return

    if doc.kind == "pdf":
        try:
            pdf = ingest.open_pdf(data, _PASSWORDS.get(doc.id))
        except PermissionError:
            doc.status, doc.status_reason = "needs_password", "Password-protected PDF: an authorised password is required"
            db.commit()
            return
        try:
            n = pdf.page_count
            if n > settings.max_pdf_pages:
                raise ingest.UnsafeFile(f"PDF has {n} pages; configured limit is {settings.max_pdf_pages}. Not truncated: raise MAX_PDF_PAGES.")
            existing = {p.page_index: p for p in db.scalars(select(SourcePage).where(SourcePage.document_id == doc.id))}
            for i in range(n):
                if i not in existing:
                    db.add(SourcePage(document_id=doc.id, page_index=i, status="pending"))
            doc.page_count = n
            job.progress_total = n
            db.commit()
            seen: dict[str, int] = {p.text_hash: p.page_index for p in existing.values() if p.text_hash and p.status == "processed"}
            pages = {p.page_index: p for p in db.scalars(select(SourcePage).where(SourcePage.document_id == doc.id))}
            for i in range(n):
                sp = pages[i]
                if sp.status in ("processed", "unreadable", "duplicate", "excluded"):
                    continue  # resume: never redo finished pages
                _check_cancel(db, job)
                po = ingest.process_pdf_page(pdf, i, ocr)
                sp.page_label, sp.status, sp.reason, sp.text = po.label, po.status, po.reason, po.text
                sp.text_source, sp.confidence, sp.width, sp.height = po.text_source, po.confidence, po.width, po.height
                if po.text and po.status == "processed":
                    h = ingest.text_hash(po.text)
                    sp.text_hash = h
                    if h in seen:
                        sp.status, sp.reason = "duplicate", f"Same text as page {seen[h] + 1}"
                    else:
                        seen[h] = i
                job.progress_done = sum(1 for p in pages.values() if p.status != "pending")
                db.commit()
        finally:
            pdf.close()
            _PASSWORDS.pop(doc.id, None)
    elif doc.kind == "docx":
        _store_pages(db, doc, ingest.docx_text_pages(data))
    elif doc.kind in ("xlsx", "xlsm"):
        _store_pages(db, doc, ingest.xlsx_text_pages(data))
    elif doc.kind == "csv":
        _store_pages(db, doc, ingest.csv_text_pages(data))
    elif doc.kind == "image":
        _store_pages(db, doc, [ingest.process_image(data, ocr)])
    elif doc.kind in ("xls", "ole_legacy"):
        src_ext = convert.legacy_type(doc.filename) or ("xls" if doc.kind == "xls" else None)
        if src_ext is None or not convert.available():
            doc.status = "needs_conversion"
            doc.status_reason = ("Legacy binary format with an unrecognised extension: save it as PDF/DOCX/XLSX and upload again."
                                 if src_ext is None else
                                 "Legacy binary format. Install LibreOffice (see Settings, System checks) and retry, or save "
                                 "as PDF/DOCX/XLSX and upload again.")
            db.commit()
            return
        try:
            out, target, secs = convert.convert(data, src_ext)
        except convert.ConversionError as exc:
            doc.status, doc.status_reason = "needs_conversion", str(exc)
            db.commit()
            return
        # Original upload (storage_key, sha256) is untouched; the derived copy is stored separately.
        doc.converted_storage_key = storage.put(doc.org_id, out, target)
        doc.converted_kind, doc.converted_by = target, f"LibreOffice ({secs}s)"
        db.commit()
        if target == "docx":
            _store_pages(db, doc, ingest.docx_text_pages(out))
        elif target == "xlsx":
            _store_pages(db, doc, ingest.xlsx_text_pages(out))
        else:
            doc.status, doc.status_reason = "needs_conversion", f"Converted to {target}, which is not read automatically yet."
            db.commit()
            return
    else:
        doc.status = "needs_conversion"
        doc.status_reason = "Unrecognised format. Save it as PDF/DOCX/XLSX and upload again."
        db.commit()
        return

    first_text = next((p.text for p in db.scalars(select(SourcePage).where(SourcePage.document_id == doc.id)
                                                   .order_by(SourcePage.page_index)) if p.text), "")
    if doc.doc_class == "unclassified":
        doc.doc_class = ingest.guess_doc_class(doc.filename, first_text or "")
    if doc.doc_class in ("corrigendum", "addendum", "prebid_reply"):
        doc.is_amendment = True
    if doc.doc_class == "unclassified":
        doc.status, doc.status_reason = "needs_classification", "Could not classify; please choose the document type."
    else:
        doc.status, doc.status_reason = "processed", None
    db.commit()
    enqueue(db, kind="extract_requirements", org_id=doc.org_id, tender_id=doc.tender_id, document_id=doc.id,
            key=extract_key(doc))
    audit(db, org_id=doc.org_id, user_id=None, action="document.processed", subject_type="source_document",
          subject_id=doc.id, tender_id=tender.id, pages=doc.page_count, doc_class=doc.doc_class)
    db.commit()


def _extract(db: Session, job: ExtractionJob) -> None:
    from .checklist import generate_checklist
    from .eligibility import propose_rules

    doc = db.get(SourceDocument, job.document_id)
    tender = db.get(Tender, doc.tender_id)
    org = db.get(Organization, tender.org_id)
    if doc.is_amendment:
        already = db.scalar(select(TenderVersion).where(TenderVersion.tender_id == tender.id,
                                                        TenderVersion.source_document_id == doc.id))
        if already is None:
            doc.effective_tender_version = bump_tender_version(db, tender, f"{doc.doc_class.title()}: {doc.filename}", doc)
        else:
            doc.effective_tender_version = already.number
    pages = list(db.scalars(select(SourcePage).where(SourcePage.document_id == doc.id, SourcePage.status == "processed")
                            .order_by(SourcePage.page_index)))
    job.progress_total = len(pages)
    cands = []
    for sp in pages:
        _check_cancel(db, job)
        cands.extend(extract_from_page(sp.text or "", sp.page_index, sp.confidence))
        job.progress_done += 1
    cands.extend(extract_continuations([(sp.text or "", sp.page_index, sp.confidence) for sp in pages], cands))
    merged = merge_candidates(cands)
    labels = {sp.page_index: sp.page_label for sp in pages}
    bboxes: dict[int, list | None] = {}
    if doc.kind == "pdf":
        pdf = ingest.open_pdf(storage.get(doc.storage_key), _PASSWORDS.get(doc.id))
        try:
            for i, c in enumerate(merged):
                bboxes[i] = find_bbox(pdf, c.page_index, c.excerpt)
        finally:
            pdf.close()
    reread = db.scalar(select(SourceAnchor.id).where(SourceAnchor.document_id == doc.id).limit(1)) is not None
    result = apply_candidates(db, tender, doc, merged, bboxes, labels, reread=reread)
    sync_tender_facts(db, tender)
    propose_rules(db, tender)
    generate_checklist(db, tender)
    audit(db, org_id=tender.org_id, user_id=None, action="requirements.extracted", subject_type="source_document",
          subject_id=doc.id, tender_id=tender.id, extractor=READER_VERSION, reread=reread,
          **{k: v for k, v in result.items() if k != "changed_requirement_ids"})
    if doc.is_amendment and result["amended"]:
        notify(db, org_id=tender.org_id, tender_id=tender.id,
               message=f"{doc.filename} amended {result['amended']} requirement(s). Review before relying on eligibility.")
    db.commit()
    # AI runs as its own job so a provider outage or budget stop never blocks deterministic extraction.
    if pages and ai.get_provider(db, org).live:
        enqueue(db, kind="ai_extract", org_id=doc.org_id, tender_id=tender.id, document_id=doc.id,
                key=f"ai:{doc.id}:{doc.sha256}:{doc.doc_class}")
        db.commit()


def _ai_extract(db: Session, job: ExtractionJob) -> None:
    doc = db.get(SourceDocument, job.document_id)
    tender = db.get(Tender, doc.tender_id)
    org = db.get(Organization, tender.org_id)
    provider = ai.get_provider(db, org)
    if not provider.live:
        raise ai.AIUnavailable("External AI is not enabled for this organization (Settings, AI provider).")
    pages = list(db.scalars(select(SourcePage).where(SourcePage.document_id == doc.id, SourcePage.status == "processed")
                            .order_by(SourcePage.page_index)))
    run = ai.run_document(db, provider, org, tender, doc, pages, job, check_cancel=lambda: _check_cancel(db, job))
    cands = run["candidates"]
    bboxes: dict[int, list | None] = {}
    if cands and doc.kind == "pdf":
        pdf = ingest.open_pdf(storage.get(doc.storage_key), _PASSWORDS.get(doc.id))
        try:
            for i, c in enumerate(cands):
                bboxes[i] = find_bbox(pdf, c.page_index, c.excerpt)
        finally:
            pdf.close()
    labels = {sp.page_index: sp.page_label for sp in pages}
    result = apply_candidates(db, tender, doc, cands, bboxes, labels) if cands else {"created": 0, "amended": 0, "unchanged": 0, "conflicts": 0}
    audit(db, org_id=tender.org_id, user_id=None, action="requirements.ai_extracted", subject_type="source_document",
          subject_id=doc.id, tender_id=tender.id, extractor=provider.extractor_id, accepted=len(cands),
          discarded=run["discarded"], requests=run["requests"], cost_usd=run["cost_usd"], stopped=run["stopped"],
          **{k: v for k, v in result.items() if k != "changed_requirement_ids"})
    notify(db, org_id=tender.org_id, tender_id=tender.id, message=(
        f"AI review of {doc.filename}: {len(cands)} interpretation(s) to review, {run['discarded']} discarded "
        f"(excerpt not found verbatim), est. cost USD {run['cost_usd']}." + (f" Stopped: {run['stopped']}" if run["stopped"] else "")))
    db.commit()
