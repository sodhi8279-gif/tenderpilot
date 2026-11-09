"""Document studio (templates, mapping, generation, signature tracking) and technical compliance."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select

from ..audit import audit
from ..deps import Ctx, get_ctx
from ..models import (DOC_STATUSES, DocumentTemplate, FieldMapping, GeneratedDocument, Requirement, TechnicalResponse)
from ..services import docgen
from ..services.ingest import UnsafeFile, sniff_kind
from ..services.package import latest_quote
from ..storage import sha256, storage
from .common import read_upload, to_dict

router = APIRouter(prefix="/api/v1/tenders/{tender_id}", tags=["documents"])


def ensure_builtins(ctx: Ctx) -> None:
    have = {t.builtin_key for t in ctx.db.scalars(select(DocumentTemplate).where(DocumentTemplate.org_id == ctx.org_id,
                                                                                 DocumentTemplate.kind == "builtin"))}
    for key, (name, env) in docgen.BUILTINS.items():
        if key not in have:
            ctx.db.add(DocumentTemplate(org_id=ctx.org_id, name=name, kind="builtin", builtin_key=key, envelope=env))
    ctx.db.flush()


@router.get("/templates")
def list_templates(tender_id: int, ctx: Ctx = Depends(get_ctx)):
    ctx.require("tender.read")
    t = ctx.tender(tender_id)
    ensure_builtins(ctx)
    ctx.db.commit()
    rows = ctx.db.scalars(select(DocumentTemplate).where(DocumentTemplate.org_id == ctx.org_id,
                                                         (DocumentTemplate.tender_id == t.id) | (DocumentTemplate.tender_id.is_(None))))
    return {"templates": [to_dict(x, {"storage_key"}) for x in rows], "fields": docgen.FIELD_CATALOG}


@router.post("/templates")
async def upload_template(tender_id: int, name: str = Form(...), envelope: str = Form("technical"),
                          from_tender_form: bool = Form(True), file: UploadFile = File(...), ctx: Ctx = Depends(get_ctx)):
    ctx.require("docs.generate")
    t = ctx.tender(tender_id)
    data = await read_upload(file)
    try:
        kind = sniff_kind(data, file.filename or "")
    except UnsafeFile as exc:
        raise HTTPException(415, str(exc))
    if kind != "docx":
        raise HTTPException(415, "Upload the prescribed form as DOCX with {{placeholders}}. PDF AcroForm/overlay filling: "
                                 "see roadmap; convert the form to DOCX for now.")
    phs = docgen.find_placeholders(data)
    tpl = DocumentTemplate(org_id=ctx.org_id, tender_id=t.id, name=name, kind="docx", storage_key=storage.put(ctx.org_id, data, "docx"),
                           placeholders=phs, envelope=envelope, from_tender_form=from_tender_form)
    ctx.db.add(tpl)
    ctx.db.flush()
    for ph in phs:
        guess = next((k for k in docgen.FIELD_CATALOG if k.split(".")[-1] == ph.lower().replace(" ", "_")), None)
        ctx.db.add(FieldMapping(template_id=tpl.id, tender_id=t.id, placeholder=ph, source_path=guess, confirmed=False))
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="template.uploaded", subject_type="template", subject_id=tpl.id, tender_id=t.id)
    ctx.db.commit()
    return {**to_dict(tpl, {"storage_key"}), "mappings": mappings(tender_id, tpl.id, ctx)}


@router.get("/templates/{template_id}/mappings")
def mappings(tender_id: int, template_id: int, ctx: Ctx = Depends(get_ctx)):
    ctx.require("tender.read")
    t = ctx.tender(tender_id)
    return [to_dict(m) for m in ctx.db.scalars(select(FieldMapping).where(FieldMapping.template_id == template_id, FieldMapping.tender_id == t.id))]


class MappingIn(BaseModel):
    placeholder: str
    source_path: str | None = None
    manual_value: str | None = None
    confirmed: bool = True


@router.put("/templates/{template_id}/mappings")
def set_mappings(tender_id: int, template_id: int, body: list[MappingIn], ctx: Ctx = Depends(get_ctx)):
    ctx.require("docs.generate")
    t = ctx.tender(tender_id)
    tpl = ctx.own(ctx.db.get(DocumentTemplate, template_id), "Template")
    for m in body:
        if m.source_path and m.source_path not in docgen.FIELD_CATALOG:
            raise HTTPException(422, f"Unknown field {m.source_path}")
        if m.source_path and m.source_path.startswith(docgen.PRICE_PATHS) and tpl.envelope != "financial":
            raise HTTPException(409, "Price fields can only be mapped into financial-envelope documents")
        fm = ctx.db.scalar(select(FieldMapping).where(FieldMapping.template_id == tpl.id, FieldMapping.tender_id == t.id,
                                                      FieldMapping.placeholder == m.placeholder))
        if fm is None:
            raise HTTPException(404, f"Placeholder {m.placeholder} not in template")
        fm.source_path, fm.manual_value, fm.confirmed = m.source_path, m.manual_value, m.confirmed
    ctx.db.commit()
    return mappings(tender_id, template_id, ctx)


@router.post("/templates/{template_id}/generate")
def generate(tender_id: int, template_id: int, ctx: Ctx = Depends(get_ctx)):
    ctx.require("docs.generate")
    t = ctx.tender(tender_id)
    tpl = ctx.own(ctx.db.get(DocumentTemplate, template_id), "Template")
    quote = None
    if tpl.envelope == "financial":
        ctx.require("price.read")
        quote = latest_quote(ctx.db, t.id)
        if quote is None or quote.status != "approved":
            raise HTTPException(409, "Financial documents need an approved quote")
    context = docgen.build_context(ctx.db, t, quote)
    missing: list[str] = []
    if tpl.kind == "builtin":
        docx_bytes = docgen.render_builtin(ctx.db, t, tpl.builtin_key, context, missing)
    else:
        fms = list(ctx.db.scalars(select(FieldMapping).where(FieldMapping.template_id == tpl.id, FieldMapping.tender_id == t.id)))
        unconfirmed = [m.placeholder for m in fms if not m.confirmed]
        if unconfirmed:
            raise HTTPException(409, f"Confirm field mappings first: {', '.join(unconfirmed[:8])}")
        values = {m.placeholder: (m.manual_value if m.manual_value else context.get(m.source_path or "")) for m in fms}
        docx_bytes = docgen.fill_template(storage.get(tpl.storage_key), values, missing)
    pdf_bytes, issues = docgen.docx_to_preview_pdf(docx_bytes, tpl.name, draft=True)
    prev = ctx.db.scalar(select(GeneratedDocument).where(GeneratedDocument.tender_id == t.id, GeneratedDocument.template_id == tpl.id)
                         .order_by(GeneratedDocument.revision.desc()).limit(1))
    signing = tpl.builtin_key not in ("clarification_request",)
    g = GeneratedDocument(tender_id=t.id, template_id=tpl.id, name=re.sub(r"[^\w\- ]", "", tpl.name)[:120],
                          revision=(prev.revision + 1) if prev else 1, docx_key=storage.put(ctx.org_id, docx_bytes, "docx"),
                          pdf_key=storage.put(ctx.org_id, pdf_bytes, "pdf"), status="draft", missing_fields=sorted(set(missing)),
                          qa_issues=issues, envelope=tpl.envelope, signing_required=signing,
                          certification_required="notary" if tpl.builtin_key == "poa_draft" else None,
                          upstream={"tender_version": t.current_version, "requirement_keys": docgen.requirement_keys_used(),
                                    "quote": {"id": quote.id, "hash": quote.snapshot_hash} if quote else None})
    ctx.db.add(g)
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="document.generated", subject_type="generated_document",
          tender_id=t.id, template=tpl.name, missing=len(missing))
    ctx.db.commit()
    return to_dict(g)


@router.get("/generated")
def list_generated(tender_id: int, ctx: Ctx = Depends(get_ctx)):
    ctx.require("tender.read")
    t = ctx.tender(tender_id)
    rows = list(ctx.db.scalars(select(GeneratedDocument).where(GeneratedDocument.tender_id == t.id).order_by(GeneratedDocument.id.desc())))
    if not ctx.can("price.read"):
        rows = [g for g in rows if g.envelope != "financial"]
    return [to_dict(g) for g in rows]


@router.get("/generated/{gen_id}/{kind}")
def download_generated(tender_id: int, gen_id: int, kind: str, ctx: Ctx = Depends(get_ctx)):
    ctx.require("tender.read")
    t = ctx.tender(tender_id)
    g = ctx.db.get(GeneratedDocument, gen_id)
    if g is None or g.tender_id != t.id:
        raise HTTPException(404, "Not found")
    if g.envelope == "financial":
        ctx.require("price.read")
    key = {"docx": g.docx_key, "pdf": g.pdf_key, "signed": g.signed_key}.get(kind)
    if not key:
        raise HTTPException(404, "File not available")
    mt = {"docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}.get(kind, "application/pdf")
    ext = "docx" if kind == "docx" else "pdf"
    return Response(storage.get(key), media_type=mt,
                    headers={"Content-Disposition": f'{"inline" if ext == "pdf" else "attachment"}; filename="{g.name} r{g.revision}.{ext}"'})


class StatusIn(BaseModel):
    status: str


@router.post("/generated/{gen_id}/status")
def set_status(tender_id: int, gen_id: int, body: StatusIn, ctx: Ctx = Depends(get_ctx)):
    ctx.require("docs.generate")
    t = ctx.tender(tender_id)
    g = ctx.db.get(GeneratedDocument, gen_id)
    if g is None or g.tender_id != t.id:
        raise HTTPException(404, "Not found")
    if body.status not in DOC_STATUSES:
        raise HTTPException(422, f"status must be one of {DOC_STATUSES}")
    if body.status in ("signed_upload_received", "verified") and not g.signed_key:
        raise HTTPException(409, "Upload the signed/certified copy first. TenderPilot never marks a document signed by itself.")
    if body.status == "verified" and not ctx.can("vault.verify"):
        raise HTTPException(403, "Only a verifier can mark a signed document verified")
    if body.status in ("awaiting_signature", "awaiting_certification") and g.missing_fields:
        raise HTTPException(409, "Complete missing fields before sending for signature")
    g.status = body.status
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="document.status", subject_type="generated_document",
          subject_id=g.id, tender_id=t.id, status=g.status)
    ctx.db.commit()
    return to_dict(g)


@router.post("/generated/{gen_id}/signed")
async def upload_signed(tender_id: int, gen_id: int, file: UploadFile = File(...), ctx: Ctx = Depends(get_ctx)):
    ctx.require("docs.generate")
    t = ctx.tender(tender_id)
    g = ctx.db.get(GeneratedDocument, gen_id)
    if g is None or g.tender_id != t.id:
        raise HTTPException(404, "Not found")
    data = await read_upload(file)
    if data[:4] != b"%PDF":
        raise HTTPException(415, "Signed copy must be a PDF")
    info = docgen.pdf_signature_info(data)
    # Stored byte-for-byte: no merge/compression is ever applied to signed originals.
    g.signed_key, g.signed_sha256 = storage.put(ctx.org_id, data, "pdf"), sha256(data)
    g.signed_has_digital_signature = bool(info.get("digital_signatures"))
    g.status = "signed_upload_received"
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="document.signed_uploaded", subject_type="generated_document",
          subject_id=g.id, tender_id=t.id, sha256=g.signed_sha256, digital_signatures=info.get("digital_signatures"))
    ctx.db.commit()
    return {**to_dict(g), "signature_info": info,
            "note": "Digital signature presence detected only; certificate validity must be checked with your DSC validator."}


# ---------------------------------------------------------------- technical compliance
class TechIn(BaseModel):
    requirement_id: int | None = None
    clause_ref: str = ""
    requirement_text: str = ""
    response: str = ""
    compliance: str = "pending"
    deviation_note: str | None = None
    make_model: str | None = None
    capacity: str | None = None
    unit: str | None = None
    evidence_id: int | None = None
    needs_engineering_review: bool = False
    reviewed: bool = False


@router.get("/technical")
def list_tech(tender_id: int, ctx: Ctx = Depends(get_ctx)):
    ctx.require("tender.read")
    t = ctx.tender(tender_id)
    return [to_dict(x) for x in ctx.db.scalars(select(TechnicalResponse).where(TechnicalResponse.tender_id == t.id).order_by(TechnicalResponse.id))]


@router.post("/technical/seed")
def seed_tech(tender_id: int, ctx: Ctx = Depends(get_ctx)):
    """Create one compliance row per technical/commercial requirement (idempotent)."""
    ctx.require("technical.write")
    t = ctx.tender(tender_id)
    have = {x.requirement_id for x in ctx.db.scalars(select(TechnicalResponse).where(TechnicalResponse.tender_id == t.id))}
    n = 0
    from ..services.requirements import current_version
    for r in ctx.db.scalars(select(Requirement).where(Requirement.tender_id == t.id, Requirement.category.in_(["technical", "commercial", "document"]))):
        if r.id in have:
            continue
        v = current_version(r)
        ctx.db.add(TechnicalResponse(tender_id=t.id, requirement_id=r.id, clause_ref=(v.anchor.clause_no or "") if v and v.anchor else "",
                                     requirement_text=(v.value_text if v else r.title)[:2000]))
        n += 1
    ctx.db.commit()
    return {"created": n}


@router.post("/technical")
def add_tech(tender_id: int, body: TechIn, ctx: Ctx = Depends(get_ctx)):
    ctx.require("technical.write")
    t = ctx.tender(tender_id)
    row = TechnicalResponse(tender_id=t.id, **body.model_dump())
    ctx.db.add(row)
    ctx.db.commit()
    return to_dict(row)


@router.put("/technical/{row_id}")
def edit_tech(tender_id: int, row_id: int, body: TechIn, ctx: Ctx = Depends(get_ctx)):
    ctx.require("technical.write")
    t = ctx.tender(tender_id)
    row = ctx.db.get(TechnicalResponse, row_id)
    if row is None or row.tender_id != t.id:
        raise HTTPException(404, "Not found")
    if body.compliance not in ("complies", "deviation", "exclusion", "not_applicable", "pending"):
        raise HTTPException(422, "Invalid compliance value")
    if body.compliance in ("deviation", "exclusion") and not body.deviation_note:
        raise HTTPException(422, "Describe the deviation/exclusion")
    for k, v in body.model_dump().items():
        setattr(row, k, v)
    ctx.db.commit()
    return to_dict(row)


@router.get("/technical/consistency")
def consistency(tender_id: int, ctx: Ctx = Depends(get_ctx)):
    """Cross-document consistency: tender ID/bidder in generated docs, capacity/unit and make conflicts."""
    ctx.require("tender.read")
    t = ctx.tender(tender_id)
    findings = []
    rows = list(ctx.db.scalars(select(TechnicalResponse).where(TechnicalResponse.tender_id == t.id)))
    by_item: dict[str, set] = {}
    for r in rows:
        if r.capacity and r.make_model:
            by_item.setdefault(r.make_model.lower(), set()).add(f"{r.capacity} {r.unit or ''}".strip())
        if r.compliance == "pending":
            findings.append({"severity": "medium", "message": f"Compliance not stated for clause {r.clause_ref or r.id}"})
        if r.needs_engineering_review and not r.reviewed:
            findings.append({"severity": "high", "message": f"Engineering review pending: clause {r.clause_ref or r.id}"})
    for make, caps in by_item.items():
        if len(caps) > 1:
            findings.append({"severity": "high", "message": f"'{make}' listed with different capacities: {sorted(caps)}"})
    from ..services.package import extract_all_text
    for g in ctx.db.scalars(select(GeneratedDocument).where(GeneratedDocument.tender_id == t.id)):
        if not g.docx_key:
            continue
        text = extract_all_text(storage.get(g.docx_key), "x.docx")
        if t.tender_ref and t.tender_ref not in text and "MISSING" not in text:
            findings.append({"severity": "medium", "message": f"'{g.name}' r{g.revision} does not mention tender ID {t.tender_ref}"})
        if g.stale:
            findings.append({"severity": "high", "message": f"'{g.name}' r{g.revision} is stale; regenerate"})
    return {"findings": findings}
