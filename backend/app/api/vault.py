"""Company vault: legal entities, JVs and evidence records (immutable originals, versioned)."""
from __future__ import annotations

import json
from decimal import Decimal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..audit import audit
from ..deps import Ctx, get_ctx
from ..models import EVIDENCE_CATEGORIES, JV, EvidenceDerivative, EvidenceLink, EvidenceRecord, JVMember, LegalEntity
from ..services import scanfix
from ..services.eligibility import norm_name
from ..services.ingest import UnsafeFile, sniff_kind
from ..services.money import try_D
from ..storage import sha256, storage
from .common import mask, parse_date_field, read_upload, to_dict

router = APIRouter(prefix="/api/v1", tags=["vault"])


class EntityIn(BaseModel):
    legal_name: str = Field(min_length=2, max_length=300)
    entity_type: str = "company"
    registered_address: str = ""
    pan: str | None = None
    gstin: str | None = None
    signatory_name: str | None = None
    signatory_designation: str | None = None
    contacts: dict = {}
    registrations: dict = {}


def entity_out(e: LegalEntity, reveal: bool) -> dict:
    d = to_dict(e)
    if not reveal:
        d["pan"], d["gstin"] = mask(e.pan), mask(e.gstin)
    return d


@router.get("/entities")
def list_entities(reveal: bool = False, ctx: Ctx = Depends(get_ctx)):
    ctx.require("vault.read")
    reveal = reveal and ctx.can("vault.write")
    return [entity_out(e, reveal) for e in ctx.db.scalars(select(LegalEntity).where(LegalEntity.org_id == ctx.org_id).order_by(LegalEntity.legal_name))]


@router.post("/entities")
def create_entity(body: EntityIn, ctx: Ctx = Depends(get_ctx)):
    ctx.require("vault.write")
    e = LegalEntity(org_id=ctx.org_id, **body.model_dump())
    ctx.db.add(e)
    ctx.db.flush()
    similar = [x.legal_name for x in ctx.db.scalars(select(LegalEntity).where(LegalEntity.org_id == ctx.org_id, LegalEntity.id != e.id))
               if norm_name(x.legal_name) == norm_name(e.legal_name)]
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="entity.created", subject_type="legal_entity", subject_id=e.id)
    ctx.db.commit()
    return {**entity_out(e, True), "similar_names_warning": similar}


@router.put("/entities/{entity_id}")
def update_entity(entity_id: int, body: EntityIn, ctx: Ctx = Depends(get_ctx)):
    ctx.require("vault.write")
    e = ctx.own(ctx.db.get(LegalEntity, entity_id), "Entity")
    for k, v in body.model_dump().items():
        setattr(e, k, v)
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="entity.updated", subject_type="legal_entity", subject_id=e.id)
    ctx.db.commit()
    return entity_out(e, True)


class JVIn(BaseModel):
    name: str
    tender_id: int | None = None
    members: list[dict]  # [{legal_entity_id, role, share_pct}]


@router.post("/jvs")
def create_jv(body: JVIn, ctx: Ctx = Depends(get_ctx)):
    ctx.require("vault.write")
    if body.tender_id:
        ctx.tender(body.tender_id)
    jv = JV(org_id=ctx.org_id, tender_id=body.tender_id, name=body.name)
    for m in body.members:
        ctx.own(ctx.db.get(LegalEntity, int(m["legal_entity_id"])), "Entity")
        share = try_D(m.get("share_pct"))
        if share is None or share < 0 or share > 100:
            raise HTTPException(422, "share_pct must be 0–100")
        if m.get("role") not in ("lead", "member"):
            raise HTTPException(422, "role must be lead or member")
        jv.members.append(JVMember(legal_entity_id=int(m["legal_entity_id"]), role=m["role"], share_pct=share))
    ctx.db.add(jv)
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="jv.created", subject_type="jv", tender_id=body.tender_id)
    ctx.db.commit()
    return jv_out(ctx, jv)


def jv_out(ctx: Ctx, jv: JV) -> dict:
    total = sum((Decimal(str(m.share_pct)) for m in jv.members), Decimal(0))
    return {**to_dict(jv), "share_total": str(total),
            "members": [{**to_dict(m), "legal_name": ctx.db.get(LegalEntity, m.legal_entity_id).legal_name} for m in jv.members]}


@router.get("/jvs")
def list_jvs(ctx: Ctx = Depends(get_ctx)):
    ctx.require("vault.read")
    return [jv_out(ctx, j) for j in ctx.db.scalars(select(JV).where(JV.org_id == ctx.org_id))]


# ---------------------------------------------------------------- evidence
@router.get("/evidence/categories")
def categories():
    return list(EVIDENCE_CATEGORIES)


@router.get("/evidence")
def list_evidence(entity_id: int | None = None, category: str | None = None, include_history: bool = False,
                  ctx: Ctx = Depends(get_ctx)):
    ctx.require("vault.read")
    stmt = select(EvidenceRecord).where(EvidenceRecord.org_id == ctx.org_id)
    if entity_id:
        stmt = stmt.where(EvidenceRecord.legal_entity_id == entity_id)
    if category:
        stmt = stmt.where(EvidenceRecord.category == category)
    if not include_history:
        stmt = stmt.where(EvidenceRecord.is_current.is_(True))
    names = {e.id: e.legal_name for e in ctx.db.scalars(select(LegalEntity).where(LegalEntity.org_id == ctx.org_id))}
    out = []
    for r in ctx.db.scalars(stmt.order_by(EvidenceRecord.id.desc())):
        d = to_dict(r, {"storage_key"})
        d["entity_name"] = names.get(r.legal_entity_id)
        d["name_mismatch"] = bool(r.subject_name_on_document and norm_name(r.subject_name_on_document) != norm_name(names.get(r.legal_entity_id)))
        out.append(d)
    return out


@router.post("/evidence")
async def upload_evidence(legal_entity_id: int = Form(...), category: str = Form(...), title: str = Form(...),
                          issuer: str | None = Form(None), subject_name_on_document: str | None = Form(None),
                          issue_date: str | None = Form(None), expiry_date: str | None = Form(None),
                          fields: str = Form("{}"), confidence: float = Form(1.0), replaces_id: int | None = Form(None),
                          file: UploadFile | None = File(None), ctx: Ctx = Depends(get_ctx)):
    ctx.require("vault.write")
    ent = ctx.own(ctx.db.get(LegalEntity, legal_entity_id), "Entity")
    if category not in EVIDENCE_CATEGORIES:
        raise HTTPException(422, f"Unknown category {category}")
    try:
        fdict = json.loads(fields or "{}")
        assert isinstance(fdict, dict)
    except (ValueError, AssertionError):
        raise HTTPException(422, "fields must be a JSON object")
    for k, v in fdict.items():
        if isinstance(v, float):
            fdict[k] = repr(v)
    rec = EvidenceRecord(org_id=ctx.org_id, legal_entity_id=ent.id, category=category, title=title, issuer=issuer,
                         subject_name_on_document=subject_name_on_document, issue_date=parse_date_field(issue_date),
                         expiry_date=parse_date_field(expiry_date), fields=fdict, confidence=confidence)
    issues = []
    if file is not None:
        data = await read_upload(file)
        try:
            kind = sniff_kind(data, file.filename or "")
        except UnsafeFile as exc:
            raise HTTPException(415, str(exc))
        if kind not in ("pdf", "image", "docx", "xlsx"):
            raise HTTPException(415, f"Evidence type {kind} not accepted; upload PDF/image/DOCX/XLSX")
        ext = (file.filename or "x.bin").rsplit(".", 1)[-1]
        rec.storage_key = storage.put(ctx.org_id, data, ext)
        rec.sha256, rec.filename = sha256(data), file.filename
        if kind == "pdf":
            import fitz
            try:
                with fitz.open(stream=data, filetype="pdf") as d:
                    scanned = sum(1 for p in d if len(p.get_text().strip()) < 25)
                    if scanned:
                        issues.append(f"{scanned} page(s) have no text layer (scanned); check legibility")
            except Exception:  # noqa: BLE001
                issues.append("PDF could not be opened")
    else:
        issues.append("No file attached")
    if subject_name_on_document and norm_name(subject_name_on_document) != norm_name(ent.legal_name):
        issues.append(f"Name on document differs from legal entity '{ent.legal_name}'")
    if confidence < 0.8:
        issues.append("Low confidence values: verify against the document")
    rec.issues = issues
    if replaces_id:
        old = ctx.own(ctx.db.get(EvidenceRecord, replaces_id), "Evidence")
        old.is_current = False
        rec.replaces_id, rec.version = old.id, old.version + 1
    ctx.db.add(rec)
    ctx.db.flush()
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="evidence.uploaded", subject_type="evidence", subject_id=rec.id,
          category=category, sha256=rec.sha256, version=rec.version)
    ctx.db.commit()
    return to_dict(rec, {"storage_key"})


class VerifyIn(BaseModel):
    state: str  # verified/rejected
    fields: dict | None = None
    note: str | None = None


@router.post("/evidence/{evidence_id}/verify")
def verify_evidence(evidence_id: int, body: VerifyIn, ctx: Ctx = Depends(get_ctx)):
    ctx.require("vault.verify")
    r = ctx.own(ctx.db.get(EvidenceRecord, evidence_id), "Evidence")
    if body.state not in ("verified", "rejected", "unverified"):
        raise HTTPException(422, "state must be verified/rejected/unverified")
    if body.state == "verified" and not r.storage_key:
        raise HTTPException(409, "Cannot verify a record without its document")
    if body.fields:
        r.fields = {**r.fields, **body.fields}
        r.confidence = 1.0
    r.verification_state, r.reviewer_id = body.state, ctx.user.id
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action=f"evidence.{body.state}", subject_type="evidence",
          subject_id=r.id, note=body.note)
    ctx.db.commit()
    return to_dict(r, {"storage_key"})


@router.get("/evidence/{evidence_id}/file")
def evidence_file(evidence_id: int, ctx: Ctx = Depends(get_ctx)):
    ctx.require("vault.read")
    r = ctx.own(ctx.db.get(EvidenceRecord, evidence_id), "Evidence")
    if not r.storage_key:
        raise HTTPException(404, "No file")
    data = storage.get(r.storage_key)
    mt = "application/pdf" if data[:4] == b"%PDF" else "application/octet-stream"
    return Response(data, media_type=mt, headers={"Content-Disposition": f'inline; filename="evidence-{r.id}"',
                                                  "X-Content-Type-Options": "nosniff"})


# ---------------------------------------------------------------- scan clean-up (cleaned copies; originals untouched)
class CleanIn(BaseModel):
    page: int = Field(default=0, ge=0)
    rotate: int = 0
    deskew: bool = False
    angle: float | None = None
    crop: list[float] | None = None


def _scan_source(ctx: Ctx, evidence_id: int) -> tuple[EvidenceRecord, bytes, str]:
    r = ctx.own(ctx.db.get(EvidenceRecord, evidence_id), "Evidence")
    if not r.storage_key:
        raise HTTPException(404, "No file")
    data = storage.get(r.storage_key)
    try:
        kind = sniff_kind(data, r.filename or "")
    except UnsafeFile as exc:
        raise HTTPException(415, str(exc))
    if kind not in ("pdf", "image"):
        raise HTTPException(415, "Only scanned PDFs and images can be rotated, cropped or straightened")
    return r, data, kind


def _edited(data: bytes, kind: str, body: CleanIn):
    try:
        img = scanfix.load_page(data, kind, body.page)
        return scanfix.apply_edits(img, rotate=body.rotate, deskew=body.deskew, crop=body.crop, angle=body.angle)
    except scanfix.ScanFixError as exc:
        raise HTTPException(422, str(exc))


def _derivative_out(d: EvidenceDerivative) -> dict:
    return to_dict(d, {"storage_key"})


@router.get("/evidence/{evidence_id}/scan")
def scan_info(evidence_id: int, ctx: Ctx = Depends(get_ctx)):
    ctx.require("vault.read")
    r, data, kind = _scan_source(ctx, evidence_id)
    cleaned = ctx.db.scalars(select(EvidenceDerivative).where(EvidenceDerivative.evidence_id == r.id)
                             .order_by(EvidenceDerivative.id.desc()))
    return {"kind": kind, "pages": scanfix.page_count(data, kind), "cleaned": [_derivative_out(d) for d in cleaned],
            "note": "Edits make a cleaned copy. The original file, its fingerprint and its verification never change."}


@router.post("/evidence/{evidence_id}/scan/preview")
def scan_preview(evidence_id: int, body: CleanIn, ctx: Ctx = Depends(get_ctx)):
    """Preview of the edited page (nothing is saved)."""
    ctx.require("vault.read")
    _, data, kind = _scan_source(ctx, evidence_id)
    img, done = _edited(data, kind, body)
    return Response(scanfix.to_png(img), media_type="image/png",
                    headers={"X-Deskew-Degrees": str(done["deskew_degrees"]), "Cache-Control": "no-store"})


@router.post("/evidence/{evidence_id}/scan/clean")
def scan_clean(evidence_id: int, body: CleanIn, ctx: Ctx = Depends(get_ctx)):
    """Save the edited page as a cleaned copy (single-page PDF) beside the untouched original."""
    ctx.require("vault.write")
    r, data, kind = _scan_source(ctx, evidence_id)
    img, done = _edited(data, kind, body)
    pdf = scanfix.to_pdf(img)
    d = EvidenceDerivative(org_id=ctx.org_id, evidence_id=r.id, page_index=body.page, params={**done, "page": body.page + 1},
                           storage_key=storage.put(ctx.org_id, pdf, "pdf"), sha256=sha256(pdf), source_sha256=r.sha256,
                           created_by=ctx.user.id)
    ctx.db.add(d)
    ctx.db.flush()
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="evidence.cleaned_copy", subject_type="evidence",
          subject_id=r.id, derivative_id=d.id, params=d.params)
    ctx.db.commit()
    return _derivative_out(d)


@router.get("/evidence/cleaned/{derivative_id}/file")
def cleaned_file(derivative_id: int, ctx: Ctx = Depends(get_ctx)):
    ctx.require("vault.read")
    d = ctx.own(ctx.db.get(EvidenceDerivative, derivative_id), "Cleaned copy")
    return Response(storage.get(d.storage_key), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="evidence-{d.evidence_id}-cleaned-{d.id}.pdf"',
                             "X-Content-Type-Options": "nosniff"})


class AttributionIn(BaseModel):
    tender_id: int
    evidence_id: int
    entity_id: int
    note: str = Field(min_length=10, description="Why this document belongs to this entity (e.g. name change certificate)")


@router.post("/evidence/attribution")
def approve_attribution(body: AttributionIn, ctx: Ctx = Depends(get_ctx)):
    ctx.require("vault.verify")
    ctx.tender(body.tender_id)
    ev = ctx.own(ctx.db.get(EvidenceRecord, body.evidence_id), "Evidence")
    ctx.own(ctx.db.get(LegalEntity, body.entity_id), "Entity")
    if ev.legal_entity_id != body.entity_id:
        raise HTTPException(409, "Evidence is filed under a different legal entity; credentials are never transferred")
    link = EvidenceLink(tender_id=body.tender_id, evidence_id=ev.id, attribution_entity_id=body.entity_id,
                        attribution_approved=True, attribution_note=body.note, approved_by=ctx.user.id, status="accepted")
    ctx.db.add(link)
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="evidence.attribution_approved", subject_type="evidence",
          subject_id=ev.id, tender_id=body.tender_id)
    ctx.db.commit()
    return to_dict(link)
