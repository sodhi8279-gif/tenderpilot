"""Tenders, source documents, processing jobs, pages and requirement review."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm.exc import StaleDataError

from ..audit import audit
from ..deps import Ctx, get_ctx
from ..models import (DOC_CLASSES, JV, WORKFLOW_STEPS, ChecklistTask, ExtractionJob, LegalEntity, Requirement,
                      RequirementVersion, SourceDocument, SourcePage, Tender, TenderVersion)
from ..services import jobs
from ..services.checklist import generate_checklist
from ..services.dates import parse_date
from ..services.eligibility import blockers, propose_rules
from ..services.ingest import UnsafeFile, sniff_kind
from ..services.requirements import current_version, invalidate_dependents, sync_tender_facts
from ..storage import sha256, storage
from .common import parse_date_field, read_upload, to_dict

router = APIRouter(prefix="/api/v1/tenders", tags=["tenders"])

PORTAL_URL_SCHEMES = ("https://", "http://")


class TenderIn(BaseModel):
    title: str = Field(min_length=3, max_length=400)
    tender_ref: str = ""
    department: str = ""
    portal_url: str = ""
    bidder_type: str = "entity"
    bidder_entity_id: int | None = None
    jv_id: int | None = None
    known_deadline: str | None = None  # free text as printed


class TenderPatch(BaseModel):
    row_version: int
    title: str | None = None
    tender_ref: str | None = None
    department: str | None = None
    portal_url: str | None = None
    bidder_type: str | None = None
    bidder_entity_id: int | None = None
    jv_id: int | None = None
    qualification_cutoff: str | None = None
    qualification_cutoff_source: str | None = None
    deadline_tz: str | None = None
    status: str | None = None


def _validate_bidder(ctx: Ctx, bidder_type, entity_id, jv_id):
    if bidder_type not in ("entity", "jv"):
        raise HTTPException(422, "bidder_type must be entity or jv")
    if entity_id:
        ctx.own(ctx.db.get(LegalEntity, entity_id), "Entity")
    if jv_id:
        ctx.own(ctx.db.get(JV, jv_id), "JV")


def tender_summary(ctx: Ctx, t: Tender) -> dict:
    d = to_dict(t)
    d["documents"] = ctx.db.scalar(select(func.count()).select_from(SourceDocument).where(SourceDocument.tender_id == t.id))
    b = blockers(ctx.db, t)
    d["blocker_count"] = len(b["blockers"])
    d["critical_count"] = sum(1 for x in b["blockers"] if x["severity"] == "critical")
    d["workload_percent"] = b["workload_percent"]
    d["bidder_name"] = (ctx.db.get(LegalEntity, t.bidder_entity_id).legal_name if t.bidder_entity_id else
                        (ctx.db.get(JV, t.jv_id).name if t.jv_id else None))
    return d


@router.get("")
def list_tenders(ctx: Ctx = Depends(get_ctx)):
    ctx.require("tender.read")
    return [tender_summary(ctx, t) for t in ctx.db.scalars(select(Tender).where(Tender.org_id == ctx.org_id).order_by(Tender.id.desc()))]


@router.post("")
def create_tender(body: TenderIn, ctx: Ctx = Depends(get_ctx)):
    ctx.require("tender.write")
    _validate_bidder(ctx, body.bidder_type, body.bidder_entity_id, body.jv_id)
    if body.portal_url and not body.portal_url.startswith(PORTAL_URL_SCHEMES):
        raise HTTPException(422, "Portal URL must start with https://")
    t = Tender(org_id=ctx.org_id, title=body.title, tender_ref=body.tender_ref, department=body.department,
               portal_url=body.portal_url, bidder_type=body.bidder_type, bidder_entity_id=body.bidder_entity_id,
               jv_id=body.jv_id, step_state={s: {"status": "pending"} for s in WORKFLOW_STEPS})
    t.step_state["create"] = {"status": "done", "at": datetime.now(timezone.utc).isoformat()}
    if body.known_deadline:
        pd = parse_date(body.known_deadline)
        t.deadline_raw = body.known_deadline
        if pd.value:
            t.deadline_at = pd.as_datetime()
            t.deadline_source = "Entered by user at intake (unverified; will be compared with the NIT)"
    ctx.db.add(t)
    ctx.db.flush()
    ctx.db.add(TenderVersion(tender_id=t.id, number=1, reason="Original tender"))
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="tender.created", subject_type="tender", subject_id=t.id, tender_id=t.id)
    ctx.db.commit()
    return tender_summary(ctx, t)


@router.get("/{tender_id}")
def get_tender(tender_id: int, ctx: Ctx = Depends(get_ctx)):
    ctx.require("tender.read")
    t = ctx.tender(tender_id)
    d = tender_summary(ctx, t)
    d["readiness"] = blockers(ctx.db, t)
    d["versions"] = [to_dict(v) for v in ctx.db.scalars(select(TenderVersion).where(TenderVersion.tender_id == t.id).order_by(TenderVersion.number))]
    d["next_action"] = next_action(ctx, t, d["readiness"])
    return d


def next_action(ctx: Ctx, t: Tender, readiness: dict) -> str:
    docs = ctx.db.scalar(select(func.count()).select_from(SourceDocument).where(SourceDocument.tender_id == t.id))
    if not docs:
        return "Upload all tender volumes (NIT, ITB, BDS, GCC/SCC, BOQ, forms, corrigenda)."
    pending = ctx.db.scalar(select(func.count()).select_from(ExtractionJob).where(ExtractionJob.tender_id == t.id,
                                                                                 ExtractionJob.status.in_(["queued", "running"])))
    if pending:
        return f"Processing {pending} job(s): wait, then review the page-completeness report."
    attention = ctx.db.scalar(select(func.count()).select_from(SourceDocument).where(
        SourceDocument.tender_id == t.id, SourceDocument.status.in_(["needs_password", "needs_classification", "needs_conversion", "failed"])))
    if attention:
        return f"{attention} document(s) need attention (password, classification or conversion)."
    if not t.bidder_entity_id and not t.jv_id:
        return "Choose the bidding entity or JV."
    if t.qualification_cutoff is None:
        return "Confirm the qualification reference (cutoff) date from the tender clause."
    for b in readiness["blockers"]:
        return b["message"]
    return "All checks clear: build the final package and run preflight."


@router.patch("/{tender_id}")
def patch_tender(tender_id: int, body: TenderPatch, ctx: Ctx = Depends(get_ctx)):
    ctx.require("tender.write")
    t = ctx.tender(tender_id)
    if t.row_version != body.row_version:
        raise HTTPException(409, "This tender was changed by someone else. Reload and try again.")
    data = body.model_dump(exclude_unset=True, exclude={"row_version"})
    if "bidder_type" in data or "bidder_entity_id" in data or "jv_id" in data:
        _validate_bidder(ctx, data.get("bidder_type", t.bidder_type), data.get("bidder_entity_id"), data.get("jv_id"))
    changed_bidder = any(k in data and data[k] != getattr(t, k) for k in ("bidder_type", "bidder_entity_id", "jv_id"))
    changed_cutoff = "qualification_cutoff" in data
    for k, v in data.items():
        if k == "qualification_cutoff":
            v = parse_date_field(v)
            if v and not data.get("qualification_cutoff_source"):
                raise HTTPException(422, "State the clause that defines the cutoff date (qualification_cutoff_source)")
        setattr(t, k, v)
    try:
        ctx.db.flush()
    except StaleDataError:
        ctx.db.rollback()
        raise HTTPException(409, "Concurrent edit detected. Reload and try again.")
    if changed_bidder:
        generate_checklist(ctx.db, t)
    if changed_bidder or changed_cutoff:
        ids = [r.id for r in ctx.db.scalars(select(Requirement).where(Requirement.tender_id == t.id))]
        if ids:
            from ..models import Evaluation
            for ev in ctx.db.scalars(select(Evaluation).where(Evaluation.tender_id == t.id, Evaluation.stale.is_(False))):
                ev.stale, ev.stale_reason = True, "Bidder or cutoff changed; re-evaluate"
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="tender.updated", subject_type="tender", subject_id=t.id,
          tender_id=t.id, fields=list(data))
    ctx.db.commit()
    return tender_summary(ctx, t)


class StepIn(BaseModel):
    status: str  # pending/in_progress/done/blocked
    owner_id: int | None = None
    due: str | None = None
    note: str | None = None


@router.put("/{tender_id}/steps/{step}")
def set_step(tender_id: int, step: str, body: StepIn, ctx: Ctx = Depends(get_ctx)):
    ctx.require("tasks.write")
    t = ctx.tender(tender_id)
    if step not in WORKFLOW_STEPS or body.status not in ("pending", "in_progress", "done", "blocked"):
        raise HTTPException(422, "Unknown step or status")
    state = dict(t.step_state or {})
    state[step] = {**state.get(step, {}), **body.model_dump(exclude_none=True), "at": datetime.now(timezone.utc).isoformat(),
                   "by": ctx.user.id}
    t.step_state = state
    ctx.db.commit()
    return t.step_state


# ---------------------------------------------------------------- documents
@router.post("/{tender_id}/documents")
async def upload_document(tender_id: int, file: UploadFile = File(...), doc_class: str = Form("unclassified"),
                          ctx: Ctx = Depends(get_ctx)):
    ctx.require("documents.write")
    t = ctx.tender(tender_id)
    if doc_class not in DOC_CLASSES:
        raise HTTPException(422, f"doc_class must be one of {DOC_CLASSES}")
    data = await read_upload(file)
    try:
        kind = sniff_kind(data, file.filename or "")
    except UnsafeFile as exc:
        raise HTTPException(415, str(exc))
    h = sha256(data)
    dup = ctx.db.scalar(select(SourceDocument).where(SourceDocument.tender_id == t.id, SourceDocument.sha256 == h))
    if dup:
        return {**to_dict(dup), "duplicate": True, "message": f"Identical file already uploaded as '{dup.filename}'"}
    name = (file.filename or "upload").replace("\\", "/").rsplit("/", 1)[-1][:300]
    ext = name.rsplit(".", 1)[-1] if "." in name else kind
    doc = SourceDocument(org_id=ctx.org_id, tender_id=t.id, filename=name, kind=kind, doc_class=doc_class, sha256=h,
                         size=len(data), storage_key=storage.put(ctx.org_id, data, ext),
                         is_amendment=doc_class in ("corrigendum", "addendum", "prebid_reply"))
    ctx.db.add(doc)
    ctx.db.flush()
    job = jobs.enqueue(ctx.db, kind="ingest_document", org_id=ctx.org_id, tender_id=t.id, document_id=doc.id, key=f"ingest:{doc.id}:{h}")
    state = dict(t.step_state or {})
    state["upload"] = {"status": "in_progress"}
    t.step_state = state
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="document.uploaded", subject_type="source_document",
          subject_id=doc.id, tender_id=t.id, sha256=h, size=len(data), kind=kind)
    ctx.db.commit()
    return {**to_dict(doc), "job_id": job.id}


@router.get("/{tender_id}/documents")
def list_documents(tender_id: int, ctx: Ctx = Depends(get_ctx)):
    ctx.require("tender.read")
    t = ctx.tender(tender_id)
    out = []
    for d in ctx.db.scalars(select(SourceDocument).where(SourceDocument.tender_id == t.id).order_by(SourceDocument.id)):
        counts = dict(ctx.db.execute(select(SourcePage.status, func.count()).where(SourcePage.document_id == d.id)
                                     .group_by(SourcePage.status)).all())
        out.append({**to_dict(d, {"storage_key", "converted_storage_key"}), "page_status": counts})
    return out


class ClassifyIn(BaseModel):
    doc_class: str
    precedence_rank: int | None = None


@router.post("/{tender_id}/documents/{doc_id}/classify")
def classify(tender_id: int, doc_id: int, body: ClassifyIn, ctx: Ctx = Depends(get_ctx)):
    ctx.require("documents.write")
    t = ctx.tender(tender_id)
    d = ctx.db.get(SourceDocument, doc_id)
    if d is None or d.tender_id != t.id:
        raise HTTPException(404, "Document not found")
    if body.doc_class not in DOC_CLASSES:
        raise HTTPException(422, "Unknown class")
    d.doc_class = body.doc_class
    d.is_amendment = body.doc_class in ("corrigendum", "addendum", "prebid_reply")
    d.precedence_rank = body.precedence_rank
    if d.status == "needs_classification":
        d.status = "processed"
    jobs.enqueue(ctx.db, kind="extract_requirements", org_id=ctx.org_id, tender_id=t.id, document_id=d.id,
                 key=jobs.extract_key(d))
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="document.classified", subject_type="source_document",
          subject_id=d.id, tender_id=t.id, doc_class=d.doc_class)
    ctx.db.commit()
    return to_dict(d, {"storage_key", "converted_storage_key"})


class PasswordIn(BaseModel):
    password: str = Field(min_length=1, max_length=200)


@router.post("/{tender_id}/documents/{doc_id}/password")
def supply_password(tender_id: int, doc_id: int, body: PasswordIn, ctx: Ctx = Depends(get_ctx)):
    ctx.require("documents.write")
    t = ctx.tender(tender_id)
    d = ctx.db.get(SourceDocument, doc_id)
    if d is None or d.tender_id != t.id or d.status != "needs_password":
        raise HTTPException(404, "No password-protected document here")
    jobs.set_password(d.id, body.password)  # memory only; not stored or logged
    job = ctx.db.scalar(select(ExtractionJob).where(ExtractionJob.document_id == d.id, ExtractionJob.kind == "ingest_document"))
    job.status, job.attempts, job.error = "queued", 0, None
    d.status = "uploaded"
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="document.password_supplied", subject_type="source_document",
          subject_id=d.id, tender_id=t.id)
    ctx.db.commit()
    return {"ok": True}


@router.get("/{tender_id}/documents/{doc_id}/pages")
def list_pages(tender_id: int, doc_id: int, ctx: Ctx = Depends(get_ctx)):
    ctx.require("tender.read")
    t = ctx.tender(tender_id)
    d = ctx.db.get(SourceDocument, doc_id)
    if d is None or d.tender_id != t.id:
        raise HTTPException(404, "Document not found")
    return [{"page_index": p.page_index, "page_label": p.page_label, "status": p.status, "reason": p.reason,
             "text_source": p.text_source, "confidence": p.confidence, "chars": len(p.text or "")}
            for p in ctx.db.scalars(select(SourcePage).where(SourcePage.document_id == d.id).order_by(SourcePage.page_index))]


@router.get("/{tender_id}/documents/{doc_id}/pages/{page_index}/text")
def page_text(tender_id: int, doc_id: int, page_index: int, ctx: Ctx = Depends(get_ctx)):
    ctx.require("tender.read")
    t = ctx.tender(tender_id)
    p = ctx.db.scalar(select(SourcePage).join(SourceDocument, SourceDocument.id == SourcePage.document_id).where(
        SourceDocument.tender_id == t.id, SourcePage.document_id == doc_id, SourcePage.page_index == page_index))
    if p is None:
        raise HTTPException(404, "Page not found")
    return {"text": p.text or "", "status": p.status, "reason": p.reason}


@router.get("/{tender_id}/documents/{doc_id}/pages/{page_index}/image")
def page_image(tender_id: int, doc_id: int, page_index: int, x0: float | None = None, y0: float | None = None,
               x1: float | None = None, y1: float | None = None, ctx: Ctx = Depends(get_ctx)):
    ctx.require("tender.read")
    t = ctx.tender(tender_id)
    d = ctx.db.get(SourceDocument, doc_id)
    if d is None or d.tender_id != t.id or d.kind != "pdf":
        raise HTTPException(404, "PDF page not available")
    import fitz
    try:
        pdf = fitz.open(stream=storage.get(d.storage_key), filetype="pdf")
    except Exception:  # noqa: BLE001
        raise HTTPException(422, "PDF cannot be rendered")
    if pdf.needs_pass:
        raise HTTPException(423, "Password protected")
    if not 0 <= page_index < pdf.page_count:
        raise HTTPException(404, "Page out of range")
    page = pdf.load_page(page_index)
    if None not in (x0, y0, x1, y1):
        annot = page.add_highlight_annot(fitz.Rect(x0, y0, x1, y1))  # on a transient in-memory copy only
        annot.set_colors(stroke=(0.98, 0.8, 0.2))
        annot.update()
    png = page.get_pixmap(dpi=110).tobytes("png")
    pdf.close()
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})


@router.get("/{tender_id}/documents/{doc_id}/file")
def document_file(tender_id: int, doc_id: int, ctx: Ctx = Depends(get_ctx)):
    ctx.require("tender.read")
    t = ctx.tender(tender_id)
    d = ctx.db.get(SourceDocument, doc_id)
    if d is None or d.tender_id != t.id:
        raise HTTPException(404, "Document not found")
    mt = {"pdf": "application/pdf"}.get(d.kind, "application/octet-stream")
    return Response(storage.get(d.storage_key), media_type=mt,
                    headers={"Content-Disposition": f'attachment; filename="document-{d.id}.{d.kind}"', "X-Content-Type-Options": "nosniff"})


# ---------------------------------------------------------------- jobs
@router.get("/{tender_id}/jobs")
def list_jobs(tender_id: int, ctx: Ctx = Depends(get_ctx)):
    ctx.require("tender.read")
    t = ctx.tender(tender_id)
    names = {d.id: d.filename for d in ctx.db.scalars(select(SourceDocument).where(SourceDocument.tender_id == t.id))}
    return [{**to_dict(j), "filename": names.get(j.document_id)}
            for j in ctx.db.scalars(select(ExtractionJob).where(ExtractionJob.tender_id == t.id).order_by(ExtractionJob.id.desc()))]


@router.post("/{tender_id}/jobs/{job_id}/{action}")
def job_action(tender_id: int, job_id: int, action: str, ctx: Ctx = Depends(get_ctx)):
    ctx.require("documents.write")
    t = ctx.tender(tender_id)
    j = ctx.db.get(ExtractionJob, job_id)
    if j is None or j.tender_id != t.id:
        raise HTTPException(404, "Job not found")
    if action == "cancel":
        if j.status in ("queued", "running"):
            j.cancel_requested = True
            if j.status == "queued":
                j.status = "cancelled"
    elif action == "retry":
        jobs.retry(ctx.db, j)
    else:
        raise HTTPException(422, "action must be cancel or retry")
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action=f"job.{action}", subject_type="job", subject_id=j.id, tender_id=t.id)
    ctx.db.commit()
    return to_dict(j)


@router.get("/{tender_id}/completeness")
def completeness(tender_id: int, ctx: Ctx = Depends(get_ctx)):
    ctx.require("tender.read")
    t = ctx.tender(tender_id)
    docs = list(ctx.db.scalars(select(SourceDocument).where(SourceDocument.tender_id == t.id)))
    rows = ctx.db.execute(select(SourcePage.status, func.count()).join(SourceDocument, SourceDocument.id == SourcePage.document_id)
                          .where(SourceDocument.tender_id == t.id).group_by(SourcePage.status)).all()
    by_class: dict[str, int] = {}
    for d in docs:
        by_class[d.doc_class] = by_class.get(d.doc_class, 0) + 1
    expected = ["nit", "itb", "bds", "gcc", "boq", "forms"]
    return {"pages": dict(rows), "total_pages": sum(c for _, c in rows), "documents": len(docs), "by_class": by_class,
            "possibly_missing_volumes": [c for c in expected if c not in by_class],
            "note": "'Possibly missing' is a reminder only: tender structures differ, confirm against the NIT document list."}


# ---------------------------------------------------------------- requirements
def req_out(ctx: Ctx, r: Requirement) -> dict:
    cur = current_version(r)
    docs = {}
    versions = []
    for v in r.versions:
        a = v.anchor
        if a and a.document_id not in docs:
            d = ctx.db.get(SourceDocument, a.document_id)
            docs[a.document_id] = d.filename if d else None
        versions.append({**to_dict(v), "anchor": ({**to_dict(a), "filename": docs.get(a.document_id)} if a else None)})
    return {**to_dict(r), "current": next((x for x in versions if x["is_current"]), None), "versions": versions,
            "low_confidence": bool(cur and cur.confidence < 0.8)}


@router.get("/{tender_id}/requirements")
def list_requirements(tender_id: int, category: str | None = None, ctx: Ctx = Depends(get_ctx)):
    ctx.require("tender.read")
    t = ctx.tender(tender_id)
    stmt = select(Requirement).where(Requirement.tender_id == t.id)
    if category:
        stmt = stmt.where(Requirement.category == category)
    return [req_out(ctx, r) for r in ctx.db.scalars(stmt.order_by(Requirement.category, Requirement.key))]


class ReviewIn(BaseModel):
    version_no: int
    decision: str  # approve / reject / make_current
    corrected_value: dict | None = None
    note: str | None = None


@router.post("/{tender_id}/requirements/{req_id}/review")
def review_requirement(tender_id: int, req_id: int, body: ReviewIn, ctx: Ctx = Depends(get_ctx)):
    ctx.require("requirements.review")
    t = ctx.tender(tender_id)
    r = ctx.db.get(Requirement, req_id)
    if r is None or r.tender_id != t.id:
        raise HTTPException(404, "Requirement not found")
    v = next((x for x in r.versions if x.version_no == body.version_no), None)
    if v is None:
        raise HTTPException(404, "Version not found")
    now = datetime.now(timezone.utc)
    if body.decision == "reject":
        v.review_status = "rejected"
        if v.is_current:
            # fall back to the latest approved earlier version if any
            v.is_current = False
            prev = [x for x in r.versions if x.version_no < v.version_no and x.review_status != "rejected"]
            if prev:
                prev[-1].is_current = True
                r.current_version_no = prev[-1].version_no
    elif body.decision in ("approve", "make_current"):
        if body.corrected_value is not None:
            # A correction is a new version: the extracted fact stays in history.
            new_no = max(x.version_no for x in r.versions) + 1
            for x in r.versions:
                x.is_current = False
            nv = RequirementVersion(requirement_id=r.id, version_no=new_no, tender_version=v.tender_version, nature="extracted",
                                    value=body.corrected_value, value_text=v.value_text, anchor_id=v.anchor_id, confidence=1.0,
                                    extractor=f"manual:{ctx.user.id}", review_status="approved", reviewer_id=ctx.user.id,
                                    reviewed_at=now, review_note=body.note or "Manual correction",
                                    amendment_document_id=v.amendment_document_id, is_current=True)
            ctx.db.add(nv)
            r.current_version_no = new_no
        else:
            for x in r.versions:
                x.is_current = x.version_no == v.version_no
            v.review_status, v.reviewer_id, v.reviewed_at = "approved", ctx.user.id, now
            v.review_note = body.note or v.review_note
            r.current_version_no = v.version_no
        r.has_conflict = False
    else:
        raise HTTPException(422, "decision must be approve, reject or make_current")
    ctx.db.flush()
    ctx.db.refresh(r)
    invalidate_dependents(ctx.db, t, [r.id], reason=f"Requirement '{r.title}' reviewed")
    propose_rules(ctx.db, t)
    generate_checklist(ctx.db, t)
    sync_tender_facts(ctx.db, t)
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action=f"requirement.{body.decision}", subject_type="requirement",
          subject_id=r.id, tender_id=t.id, version=body.version_no, corrected=body.corrected_value is not None)
    ctx.db.commit()
    return req_out(ctx, r)


class ManualReqIn(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9_.]+$", max_length=120)
    category: str
    title: str
    value: dict = {}
    nature: str = "question"
    mandatory: bool = True
    note: str | None = None


@router.post("/{tender_id}/requirements")
def add_requirement(tender_id: int, body: ManualReqIn, ctx: Ctx = Depends(get_ctx)):
    """Add an interpretation or unresolved question manually (kept separate from extracted facts)."""
    ctx.require("requirements.review")
    t = ctx.tender(tender_id)
    if ctx.db.scalar(select(Requirement).where(Requirement.tender_id == t.id, Requirement.key == body.key)):
        raise HTTPException(409, "Requirement key exists")
    if body.nature not in ("interpretation", "question", "derived"):
        raise HTTPException(422, "Manual entries must be interpretation, question or derived")
    r = Requirement(tender_id=t.id, key=body.key, category=body.category, title=body.title, mandatory=body.mandatory,
                    has_conflict=body.nature == "question")
    ctx.db.add(r)
    ctx.db.flush()
    ctx.db.add(RequirementVersion(requirement_id=r.id, version_no=1, tender_version=t.current_version, nature=body.nature,
                                  value=body.value, value_text=body.note or "", confidence=1.0, extractor=f"manual:{ctx.user.id}",
                                  review_status="needs_review" if body.nature == "question" else "approved", is_current=True))
    ctx.db.commit()
    ctx.db.refresh(r)
    return req_out(ctx, r)


@router.get("/{tender_id}/tasks")
def list_tasks(tender_id: int, ctx: Ctx = Depends(get_ctx)):
    ctx.require("tender.read")
    t = ctx.tender(tender_id)
    return [to_dict(x) for x in ctx.db.scalars(select(ChecklistTask).where(ChecklistTask.tender_id == t.id).order_by(ChecklistTask.group, ChecklistTask.id))]
