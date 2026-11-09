"""Envelope profile, upload slots, preflight, package revisions, freeze/approval and portal receipts."""
from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..audit import audit
from ..deps import Ctx, get_ctx
from ..models import Approval, EnvelopeProfile, ExportPackage, SubmissionReceipt, UploadSlot
from ..services import package as pkgsvc
from ..services.money import try_D
from ..storage import sha256, storage
from .common import read_upload, to_dict

router = APIRouter(prefix="/api/v1/tenders/{tender_id}", tags=["packaging"])


class ProfileIn(BaseModel):
    envelopes: list[str] = Field(min_length=1)
    zip_accepted: bool | None = None
    source_note: str | None = None
    portal_name: str | None = None


@router.get("/envelope")
def get_profile(tender_id: int, ctx: Ctx = Depends(get_ctx)):
    ctx.require("tender.read")
    t = ctx.tender(tender_id)
    p = ctx.db.scalar(select(EnvelopeProfile).where(EnvelopeProfile.tender_id == t.id))
    slots = [to_dict(s) for s in ctx.db.scalars(select(UploadSlot).where(UploadSlot.tender_id == t.id).order_by(UploadSlot.envelope, UploadSlot.id))]
    return {"profile": to_dict(p) if p else None, "slots": slots}


@router.put("/envelope")
def set_profile(tender_id: int, body: ProfileIn, ctx: Ctx = Depends(get_ctx)):
    ctx.require("package.build")
    t = ctx.tender(tender_id)
    p = ctx.db.scalar(select(EnvelopeProfile).where(EnvelopeProfile.tender_id == t.id)) or EnvelopeProfile(tender_id=t.id)
    p.envelopes, p.zip_accepted, p.source_note, p.portal_name = body.envelopes, body.zip_accepted, body.source_note, body.portal_name
    ctx.db.add(p)
    ctx.db.commit()
    return get_profile(tender_id, ctx)


class SlotIn(BaseModel):
    envelope: str
    name: str = Field(min_length=1, max_length=300)
    allowed_types: list[str] = ["pdf"]
    max_size_mb: str | None = None
    max_count: int = Field(default=1, ge=1, le=100)
    naming: str | None = None
    signature_required: bool = False
    mandatory: bool = True
    assignments: list[dict] = []


def _check_assignments(ctx: Ctx, t, slot_env: str, assignments: list[dict]):
    for a in assignments:
        if a.get("type") not in ("generated", "evidence", "quote_export") or not isinstance(a.get("id"), int):
            raise HTTPException(422, "assignment must be {type: generated|evidence|quote_export, id: int}")
        if a["type"] == "quote_export" and slot_env != "financial":
            raise HTTPException(409, "The priced BOQ can only be placed in a financial envelope slot")
        if a["type"] == "generated":
            from ..models import GeneratedDocument
            g = ctx.db.get(GeneratedDocument, a["id"])
            if g is None or g.tender_id != t.id:
                raise HTTPException(404, "Generated document not found")
            if g.envelope == "financial" and slot_env != "financial":
                raise HTTPException(409, f"'{g.name}' is a financial document and cannot go into the {slot_env} envelope")


@router.post("/slots")
def add_slot(tender_id: int, body: SlotIn, ctx: Ctx = Depends(get_ctx)):
    ctx.require("package.build")
    t = ctx.tender(tender_id)
    if body.max_size_mb is not None and try_D(body.max_size_mb) is None:
        raise HTTPException(422, "max_size_mb must be a number")
    _check_assignments(ctx, t, body.envelope, body.assignments)
    s = UploadSlot(tender_id=t.id, **body.model_dump())
    ctx.db.add(s)
    ctx.db.commit()
    return to_dict(s)


@router.put("/slots/{slot_id}")
def edit_slot(tender_id: int, slot_id: int, body: SlotIn, ctx: Ctx = Depends(get_ctx)):
    ctx.require("package.build")
    t = ctx.tender(tender_id)
    s = ctx.db.get(UploadSlot, slot_id)
    if s is None or s.tender_id != t.id:
        raise HTTPException(404, "Slot not found")
    _check_assignments(ctx, t, body.envelope, body.assignments)
    for k, v in body.model_dump().items():
        setattr(s, k, v)
    for pk in ctx.db.scalars(select(ExportPackage).where(ExportPackage.tender_id == t.id, ExportPackage.status.in_(["final_ready", "frozen"]))):
        pk.status = "superseded"
    ctx.db.commit()
    return to_dict(s)


@router.delete("/slots/{slot_id}")
def delete_slot(tender_id: int, slot_id: int, ctx: Ctx = Depends(get_ctx)):
    ctx.require("package.build")
    t = ctx.tender(tender_id)
    s = ctx.db.get(UploadSlot, slot_id)
    if s is None or s.tender_id != t.id:
        raise HTTPException(404, "Slot not found")
    ctx.db.delete(s)
    ctx.db.commit()
    return {"ok": True}


@router.post("/preflight")
def preflight(tender_id: int, ctx: Ctx = Depends(get_ctx)):
    ctx.require("tender.read")
    t = ctx.tender(tender_id)
    res = pkgsvc.run_preflight(ctx.db, t)
    res.pop("_resolved", None)
    return res


class BuildIn(BaseModel):
    final: bool = False


@router.post("/packages")
def build(tender_id: int, body: BuildIn, ctx: Ctx = Depends(get_ctx)):
    ctx.require("package.build")
    t = ctx.tender(tender_id)
    try:
        pkg = pkgsvc.build_package(ctx.db, t, ctx.user.id, body.final)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="package.built", subject_type="package", subject_id=pkg.id,
          tender_id=t.id, revision=pkg.revision, status=pkg.status, hash=pkg.package_hash)
    ctx.db.commit()
    return pkg_out(ctx, t, pkg)


def pkg_out(ctx: Ctx, t, pkg: ExportPackage) -> dict:
    d = to_dict(pkg, {"zip_key", "guide_key"})
    d["entries"] = [to_dict(e, {"storage_key"}) for e in pkg.entries]
    d["current"] = pkgsvc.is_package_current(ctx.db, t, pkg)
    d["receipts"] = [to_dict(r, {"storage_key"}) for r in ctx.db.scalars(select(SubmissionReceipt).where(SubmissionReceipt.package_id == pkg.id))]
    return d


@router.get("/packages")
def list_packages(tender_id: int, ctx: Ctx = Depends(get_ctx)):
    ctx.require("tender.read")
    t = ctx.tender(tender_id)
    return [pkg_out(ctx, t, p) for p in ctx.db.scalars(select(ExportPackage).where(ExportPackage.tender_id == t.id).order_by(ExportPackage.revision.desc()))]


@router.post("/packages/{pkg_id}/freeze")
def freeze(tender_id: int, pkg_id: int, ctx: Ctx = Depends(get_ctx)):
    ctx.require("package.approve")
    t = ctx.tender(tender_id)
    pkg = ctx.db.get(ExportPackage, pkg_id)
    if pkg is None or pkg.tender_id != t.id:
        raise HTTPException(404, "Package not found")
    if pkg.status != "final_ready":
        raise HTTPException(409, f"Only a final-ready package can be frozen (this one is {pkg.status})")
    if not pkgsvc.is_package_current(ctx.db, t, pkg):
        pkg.status = "superseded"
        ctx.db.commit()
        raise HTTPException(409, "Inputs changed since this package was built; build a new revision")
    pf = pkgsvc.run_preflight(ctx.db, t)
    pf.pop("_resolved", None)
    if not pf["final_ready"]:
        raise HTTPException(409, "Preflight no longer passes")
    pkg.status, pkg.approved_by = "frozen", ctx.user.id
    ctx.db.add(Approval(tender_id=t.id, subject_type="package", subject_id=pkg.id, subject_hash=pkg.package_hash,
                        stage="package_approval", user_id=ctx.user.id, decision="approved"))
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="package.frozen", subject_type="package", subject_id=pkg.id,
          tender_id=t.id, hash=pkg.package_hash)
    ctx.db.commit()
    return pkg_out(ctx, t, pkg)


@router.get("/packages/{pkg_id}/{what}")
def download(tender_id: int, pkg_id: int, what: str, ctx: Ctx = Depends(get_ctx)):
    ctx.require("package.download")
    t = ctx.tender(tender_id)
    pkg = ctx.db.get(ExportPackage, pkg_id)
    if pkg is None or pkg.tender_id != t.id:
        raise HTTPException(404, "Package not found")
    if any(e.envelope == "financial" for e in pkg.entries):
        ctx.require("price.read")
    label = "DRAFT_" if pkg.status == "draft" else ""
    if what == "zip":
        return Response(storage.get(pkg.zip_key), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{label}tender{t.id}_rev{pkg.revision}_local_copy.zip"'})
    if what == "guide":
        return Response(storage.get(pkg.guide_key), media_type="text/plain; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{label}portal_guide_rev{pkg.revision}.txt"'})
    if what == "manifest":
        body = {"internal_record": True, "note": "Internal audit manifest — do not upload to the portal.",
                "tender": {"id": t.id, "title": t.title, "ref": t.tender_ref, "version": t.current_version},
                "package": {"revision": pkg.revision, "status": pkg.status, "hash": pkg.package_hash, "upstream": pkg.upstream,
                            "preparer": pkg.created_by, "approver": pkg.approved_by, "created_at": pkg.created_at.isoformat()},
                "files": [to_dict(e, {"storage_key"}) for e in pkg.entries], "preflight": pkg.preflight}
        return Response(json.dumps(body, indent=2, default=str), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="INTERNAL_manifest_rev{pkg.revision}.json"'})
    raise HTTPException(404, "Unknown download")


@router.post("/packages/{pkg_id}/receipt")
async def record_receipt(tender_id: int, pkg_id: int, receipt_identifier: str = Form(..., min_length=3),
                         submitted_at: str = Form(...), notes: str | None = Form(None), file: UploadFile = File(...),
                         ctx: Ctx = Depends(get_ctx)):
    ctx.require("receipt.record")
    t = ctx.tender(tender_id)
    pkg = ctx.db.get(ExportPackage, pkg_id)
    if pkg is None or pkg.tender_id != t.id:
        raise HTTPException(404, "Package not found")
    if pkg.status != "frozen":
        raise HTTPException(409, "Record receipts against the frozen package that was uploaded")
    data = await read_upload(file)
    try:
        when = datetime.fromisoformat(submitted_at)
    except ValueError:
        raise HTTPException(422, "submitted_at must be ISO date-time, e.g. 2026-11-20T14:35+05:30")
    if when.tzinfo is None:
        from zoneinfo import ZoneInfo
        when = when.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
    ext = (file.filename or "receipt.pdf").rsplit(".", 1)[-1]
    r = SubmissionReceipt(tender_id=t.id, package_id=pkg.id, receipt_identifier=receipt_identifier, submitted_at=when,
                          storage_key=storage.put(ctx.org_id, data, ext), sha256=sha256(data), notes=notes, recorded_by=ctx.user.id)
    ctx.db.add(r)
    state = dict(t.step_state or {})
    state["receipt"] = {"status": "done", "at": when.isoformat()}
    t.step_state = state
    t.status = "submitted"
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="submission.receipt_recorded", subject_type="package",
          subject_id=pkg.id, tender_id=t.id, receipt=receipt_identifier)
    ctx.db.commit()
    return to_dict(r, {"storage_key"})
