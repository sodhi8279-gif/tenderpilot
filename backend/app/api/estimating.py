"""BOQ import, rate sources, rate analysis, quote versions, approvals, scenarios, workbook export."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..audit import audit
from ..deps import Ctx, get_ctx
from ..models import BOQ, Approval, BOQItem, ExportPackage, QuoteVersion, RateAnalysis, RateSource, Requirement
from ..services import boq as boqsvc
from ..services.money import D, try_D
from ..services.requirements import current_version
from ..storage import sha256, storage
from .common import parse_date_field, read_upload, to_dict

router = APIRouter(prefix="/api/v1/tenders/{tender_id}", tags=["estimating"])


def _boq(ctx: Ctx, tender_id: int, boq_id: int) -> BOQ:
    t = ctx.tender(tender_id)
    b = ctx.db.get(BOQ, boq_id)
    if b is None or b.tender_id != t.id:
        raise HTTPException(404, "BOQ not found")
    return b


@router.post("/boqs")
async def import_boq(tender_id: int, file: UploadFile = File(...), ctx: Ctx = Depends(get_ctx)):
    ctx.require("price.write")
    t = ctx.tender(tender_id)
    data = await read_upload(file)
    try:
        parsed = boqsvc.parse_boq(data, file.filename or "boq.xlsx")
    except boqsvc.BOQError as exc:
        raise HTTPException(422, str(exc))
    except Exception as exc:  # noqa: BLE001 - unreadable workbook
        raise HTTPException(422, f"Workbook could not be read: {type(exc).__name__}")
    ext = (file.filename or "boq.xlsx").rsplit(".", 1)[-1].lower()
    b = BOQ(tender_id=t.id, original_key=storage.put(ctx.org_id, data, ext if ext in ("xlsx", "xlsm") else "xlsx"),
            original_sha256=sha256(data), filename=file.filename or "boq.xlsx", sheet_name=parsed["sheet_name"],
            header_row=parsed["header_row"], columns=parsed["columns"], roundtrip_supported=parsed["roundtrip_supported"],
            roundtrip_issues=parsed["roundtrip_issues"], flags=parsed["flags"])
    for it in parsed["items"]:
        b.items.append(BOQItem(**it))
    ctx.db.add(b)
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="boq.imported", subject_type="boq", tender_id=t.id,
          items=len(parsed["items"]), sha256=b.original_sha256)
    ctx.db.commit()
    return boq_out(ctx, b, prices=True)


def boq_out(ctx: Ctx, b: BOQ, prices: bool) -> dict:
    analyses = {a.boq_item_id: a for a in ctx.db.scalars(select(RateAnalysis).where(RateAnalysis.boq_item_id.in_([i.id for i in b.items])))} if prices else {}
    items = []
    for i in b.items:
        d = to_dict(i)
        if prices and i.id in analyses:
            a = analyses[i.id]
            d["rate"] = str(a.computed_rate) if a.computed_rate is not None else None
            d["amount"] = str(boqsvc.q(D(i.quantity) * a.computed_rate, b.rounding_places)) if a.computed_rate is not None else None
            d["warnings"] = a.warnings
        items.append(d)
    return {**to_dict(b, {"original_key"}), "items": items, "prices_visible": prices}


@router.get("/boqs")
def list_boqs(tender_id: int, ctx: Ctx = Depends(get_ctx)):
    ctx.require("tender.read")
    t = ctx.tender(tender_id)
    prices = ctx.can("price.read")
    return [boq_out(ctx, b, prices) for b in ctx.db.scalars(select(BOQ).where(BOQ.tender_id == t.id))]


class BOQSettings(BaseModel):
    mode: str | None = None
    rounding_places: int | None = Field(default=None, ge=0, le=4)


@router.patch("/boqs/{boq_id}")
def boq_settings(tender_id: int, boq_id: int, body: BOQSettings, ctx: Ctx = Depends(get_ctx)):
    ctx.require("price.write")
    b = _boq(ctx, tender_id, boq_id)
    if body.mode and body.mode not in ("item_rate", "percentage", "lump_sum"):
        raise HTTPException(422, "mode must be item_rate, percentage or lump_sum")
    if body.mode:
        b.mode = body.mode
    if body.rounding_places is not None:
        b.rounding_places = body.rounding_places
    _invalidate_quotes(ctx, b, "BOQ settings changed")
    ctx.db.commit()
    return boq_out(ctx, b, True)


# ---------------------------------------------------------------- rate sources
class RateIn(BaseModel):
    resource: str
    resource_type: str = "material"
    specification: str = ""
    unit: str
    rate: str
    tax_basis: str = "excl_gst"
    gst_pct: str = "0"
    gst_recoverable: bool = True
    freight_basis: str = "ex_works"
    source_type: str
    source_url: str | None = None
    supplier: str | None = None
    location: str | None = None
    quote_date: str | None = None
    valid_until: str | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)


@router.get("/rates")
def list_rates(tender_id: int, ctx: Ctx = Depends(get_ctx)):
    ctx.require("price.read")
    t = ctx.tender(tender_id)
    today = date.today()
    rows = ctx.db.scalars(select(RateSource).where(RateSource.org_id == ctx.org_id,
                                                   (RateSource.tender_id == t.id) | (RateSource.tender_id.is_(None))))
    return [{**to_dict(r, {"storage_key"}), "stale": bool(r.valid_until and r.valid_until < today)} for r in rows]


@router.post("/rates")
def add_rate(tender_id: int, body: RateIn, ctx: Ctx = Depends(get_ctx)):
    ctx.require("price.write")
    t = ctx.tender(tender_id)
    if body.source_type not in ("quotation", "public_reference", "assumption", "user_input"):
        raise HTTPException(422, "source_type must be quotation, public_reference, assumption or user_input")
    if body.source_type == "quotation" and not (body.supplier and body.quote_date):
        raise HTTPException(422, "A quotation needs the supplier and quotation date")
    if body.source_type == "public_reference" and not (body.source_url and body.quote_date):
        raise HTTPException(422, "A public reference needs its URL and the date it was observed")
    if try_D(body.rate) is None or D(body.rate) < 0:
        raise HTTPException(422, "rate must be a non-negative decimal")
    data = body.model_dump()
    data["quote_date"] = parse_date_field(body.quote_date)
    data["valid_until"] = parse_date_field(body.valid_until)
    if body.source_type == "assumption":
        data["confidence"] = min(body.confidence, 0.5)
    r = RateSource(org_id=ctx.org_id, tender_id=t.id, **data)
    ctx.db.add(r)
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="rate_source.added", subject_type="rate_source", tender_id=t.id,
          source_type=body.source_type)
    ctx.db.commit()
    return to_dict(r, {"storage_key"})


# ---------------------------------------------------------------- analysis
class AnalysisIn(BaseModel):
    output_qty: str = "1"
    components: list[dict] = []
    overheads: dict = {}
    margin_pct: str = "0"
    margin_mode: str = "markup_on_cost"
    direct_rate: str | None = None  # simple entry with no build-up (must cite a rate source)
    direct_rate_source_id: int | None = None


def _sources(ctx: Ctx) -> dict[int, dict]:
    today = date.today()
    return {r.id: {"rate": r.rate, "source_type": r.source_type, "confidence": r.confidence, "gst_pct": r.gst_pct,
                   "gst_recoverable": r.gst_recoverable, "stale": bool(r.valid_until and r.valid_until < today)}
            for r in ctx.db.scalars(select(RateSource).where(RateSource.org_id == ctx.org_id))}


def _invalidate_quotes(ctx: Ctx, b: BOQ, reason: str) -> None:
    for qv in ctx.db.scalars(select(QuoteVersion).where(QuoteVersion.boq_id == b.id, QuoteVersion.status.in_(["estimator_reviewed", "approved"]))):
        qv.status, qv.invalidated_reason = "invalidated", reason
        for ap in ctx.db.scalars(select(Approval).where(Approval.subject_type == "quote", Approval.subject_id == qv.id, Approval.valid.is_(True))):
            ap.valid, ap.invalidated_reason = False, reason
        for pk in ctx.db.scalars(select(ExportPackage).where(ExportPackage.tender_id == b.tender_id, ExportPackage.status.in_(["final_ready", "frozen"]))):
            pk.status = "superseded"


@router.put("/boq-items/{item_id}/analysis")
def set_analysis(tender_id: int, item_id: int, body: AnalysisIn, ctx: Ctx = Depends(get_ctx)):
    ctx.require("price.write")
    t = ctx.tender(tender_id)
    item = ctx.db.get(BOQItem, item_id)
    b = ctx.db.get(BOQ, item.boq_id) if item else None
    if item is None or b.tender_id != t.id:
        raise HTTPException(404, "Item not found")
    if body.margin_mode not in ("markup_on_cost", "margin_on_sales"):
        raise HTTPException(422, "margin_mode must be markup_on_cost or margin_on_sales")
    sources = _sources(ctx)
    for c in body.components:
        if c.get("rate_source_id") and int(c["rate_source_id"]) not in sources:
            raise HTTPException(422, "Unknown rate source")
    comps = body.components
    if body.direct_rate is not None and not comps:
        comps = [{"type": "direct", "description": f"Direct rate for {item.item_code or item.row_index}", "qty": "1",
                  "rate": body.direct_rate, "rate_source_id": body.direct_rate_source_id}]
    try:
        res = boqsvc.compute_rate(comps, body.output_qty, body.overheads, body.margin_pct, body.margin_mode, sources,
                                  b.rounding_places)
    except (ValueError, ArithmeticError) as exc:
        raise HTTPException(422, str(exc))
    a = ctx.db.scalar(select(RateAnalysis).where(RateAnalysis.boq_item_id == item.id)) or RateAnalysis(boq_item_id=item.id)
    a.output_qty, a.components, a.overheads = body.output_qty, comps, body.overheads
    a.margin_pct, a.margin_mode, a.direct_rate = body.margin_pct, body.margin_mode, body.direct_rate
    a.computed_rate, a.breakdown, a.warnings = D(res["rate"]), res["breakdown"], res["warnings"]
    ctx.db.add(a)
    _invalidate_quotes(ctx, b, f"Rate changed for item {item.item_code or item.row_index}")
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="rate.analysed", subject_type="boq_item", subject_id=item.id,
          tender_id=t.id, rate=res["rate"])
    ctx.db.commit()
    return {**to_dict(a), "item": to_dict(item)}


@router.get("/boq-items/{item_id}/analysis")
def get_analysis(tender_id: int, item_id: int, ctx: Ctx = Depends(get_ctx)):
    ctx.require("price.read")
    t = ctx.tender(tender_id)
    item = ctx.db.get(BOQItem, item_id)
    if item is None or ctx.db.get(BOQ, item.boq_id).tender_id != t.id:
        raise HTTPException(404, "Item not found")
    a = ctx.db.scalar(select(RateAnalysis).where(RateAnalysis.boq_item_id == item.id))
    return {"item": to_dict(item), "analysis": to_dict(a) if a else None}


# ---------------------------------------------------------------- quotes
class QuoteIn(BaseModel):
    percentage: dict | None = None  # {"percent": "5.5", "direction": "below"}


@router.post("/boqs/{boq_id}/quotes")
def create_quote(tender_id: int, boq_id: int, body: QuoteIn, ctx: Ctx = Depends(get_ctx)):
    ctx.require("price.write")
    b = _boq(ctx, tender_id, boq_id)
    analyses = {a.boq_item_id: a for a in ctx.db.scalars(select(RateAnalysis).where(RateAnalysis.boq_item_id.in_([i.id for i in b.items])))}
    items = [{"id": i.id, "row_index": i.row_index, "item_code": i.item_code, "description": i.description[:300], "unit": i.unit,
              "quantity": str(i.quantity), "rate_cell": i.rate_cell, "trade": i.trade} for i in b.items]
    rates = {i.id: (str(analyses[i.id].computed_rate) if i.id in analyses and analyses[i.id].computed_rate is not None else None)
             for i in b.items}
    est_req = ctx.db.scalar(select(Requirement).where(Requirement.tender_id == b.tender_id, Requirement.key == "commercial.estimate"))
    est = try_D((current_version(est_req).value or {}).get("amount")) if est_req and current_version(est_req) else None
    try:
        snap = boqsvc.quote_snapshot(items, rates, b.rounding_places, b.mode, body.percentage, est)
    except (ValueError, ArithmeticError) as exc:
        raise HTTPException(422, str(exc))
    if snap["missing_rates"] and b.mode != "percentage":
        raise HTTPException(409, f"{len(snap['missing_rates'])} item(s) have no rate yet")
    snap["warnings"] = sorted({w for a in analyses.values() for w in (a.warnings or [])})
    snap["cost_basis"] = {str(k): a.breakdown.get("cost_per_unit") for k, a in analyses.items()}
    n = (ctx.db.scalar(select(func.max(QuoteVersion.version_no)).where(QuoteVersion.tender_id == b.tender_id)) or 0) + 1
    qv = QuoteVersion(tender_id=b.tender_id, boq_id=b.id, version_no=n, snapshot=snap, total=D(snap["total"]),
                      total_words=snap["total_words"], snapshot_hash=snap["hash"], status="draft")
    ctx.db.add(qv)
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="quote.created", subject_type="quote", tender_id=b.tender_id,
          version=n, total=snap["total"], hash=snap["hash"])
    ctx.db.commit()
    return quote_out(ctx, qv)


def quote_out(ctx: Ctx, qv: QuoteVersion) -> dict:
    aps = [to_dict(a) for a in ctx.db.scalars(select(Approval).where(Approval.subject_type == "quote", Approval.subject_id == qv.id))]
    return {**to_dict(qv), "approvals": aps}


@router.get("/quotes")
def list_quotes(tender_id: int, ctx: Ctx = Depends(get_ctx)):
    ctx.require("price.read")
    t = ctx.tender(tender_id)
    return [quote_out(ctx, q) for q in ctx.db.scalars(select(QuoteVersion).where(QuoteVersion.tender_id == t.id).order_by(QuoteVersion.version_no.desc()))]


class DecisionIn(BaseModel):
    decision: str = "approved"
    comment: str | None = None
    row_version: int


@router.post("/quotes/{quote_id}/estimator-review")
def quote_estimator_review(tender_id: int, quote_id: int, body: DecisionIn, ctx: Ctx = Depends(get_ctx)):
    return quote_decision(tender_id, quote_id, "estimator-review", body, ctx)


@router.post("/quotes/{quote_id}/approve")
def quote_approve(tender_id: int, quote_id: int, body: DecisionIn, ctx: Ctx = Depends(get_ctx)):
    return quote_decision(tender_id, quote_id, "approve", body, ctx)


def quote_decision(tender_id: int, quote_id: int, stage: str, body: DecisionIn, ctx: Ctx):
    t = ctx.tender(tender_id)
    qv = ctx.db.get(QuoteVersion, quote_id)
    if qv is None or qv.tender_id != t.id:
        raise HTTPException(404, "Quote not found")
    if qv.row_version != body.row_version:
        raise HTTPException(409, "Quote changed since you loaded it; reload")
    if body.decision not in ("approved", "rejected"):
        raise HTTPException(422, "decision must be approved or rejected")
    if stage == "estimator-review":
        ctx.require("quote.estimator_review")
        if qv.status != "draft":
            raise HTTPException(409, f"Quote is {qv.status}")
        new_status, st = ("estimator_reviewed" if body.decision == "approved" else "invalidated"), "estimator_review"
    elif stage == "approve":
        ctx.require("quote.approve")
        if qv.status != "estimator_reviewed":
            raise HTTPException(409, "Estimator review must be completed first")
        reviewer = ctx.db.scalar(select(Approval.user_id).where(Approval.subject_type == "quote", Approval.subject_id == qv.id,
                                                                Approval.stage == "estimator_review", Approval.valid.is_(True)))
        if reviewer == ctx.user.id:
            raise HTTPException(409, "Approver must be a different person from the estimator reviewer")
        if body.decision == "approved" and qv.snapshot.get("warnings") and not (body.comment and len(body.comment) >= 15):
            raise HTTPException(409, "This quote has rate warnings (assumed/low-confidence/unsourced rates). "
                                     "Record how each was resolved in the approval comment.")
        new_status, st = ("approved" if body.decision == "approved" else "invalidated"), "approval"
    else:
        raise HTTPException(404, "Unknown stage")
    ctx.db.add(Approval(tender_id=t.id, subject_type="quote", subject_id=qv.id, subject_hash=qv.snapshot_hash, stage=st,
                        user_id=ctx.user.id, decision=body.decision, comment=body.comment))
    qv.status = new_status
    if body.decision == "rejected":
        qv.invalidated_reason = f"Rejected at {st}: {body.comment or ''}"
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action=f"quote.{st}.{body.decision}", subject_type="quote",
          subject_id=qv.id, tender_id=t.id, hash=qv.snapshot_hash)
    ctx.db.commit()
    return quote_out(ctx, qv)


@router.post("/quotes/{quote_id}/export")
def export_quote(tender_id: int, quote_id: int, ctx: Ctx = Depends(get_ctx)):
    ctx.require("price.write")
    t = ctx.tender(tender_id)
    qv = ctx.db.get(QuoteVersion, quote_id)
    if qv is None or qv.tender_id != t.id:
        raise HTTPException(404, "Quote not found")
    if qv.status != "approved":
        raise HTTPException(409, "Only an approved quote can be written into the official workbook")
    b = ctx.db.get(BOQ, qv.boq_id)
    original = storage.get(b.original_key)
    if sha256(original) != b.original_sha256:
        raise HTTPException(500, "Original workbook hash mismatch")
    entries = {ln["rate_cell"]: ln["rate"] for ln in qv.snapshot["lines"] if ln.get("rate") is not None}
    try:
        out, report = boqsvc.export_priced_workbook(original, b.sheet_name, entries, b.roundtrip_supported)
    except boqsvc.BOQError as exc:
        qv.export_report = {"ok": False, "error": str(exc),
                            "manual_mapping": [{"cell": f"{b.sheet_name}!{ln['rate_cell']}", "item": ln["item_code"], "rate": ln["rate"]}
                                               for ln in qv.snapshot["lines"]]}
        ctx.db.commit()
        raise HTTPException(409, str(exc))
    report["independent_total"] = qv.snapshot["total"]
    report["filename"] = f"{b.filename.rsplit('.', 1)[0]}_priced_v{qv.version_no}.xlsx"
    qv.export_key, qv.export_report = storage.put(ctx.org_id, out, "xlsx"), report
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="quote.exported", subject_type="quote", subject_id=qv.id,
          tender_id=t.id, cells=report["cells_written"])
    ctx.db.commit()
    return quote_out(ctx, qv)


@router.get("/quotes/{quote_id}/export")
def download_export(tender_id: int, quote_id: int, ctx: Ctx = Depends(get_ctx)):
    ctx.require("price.read")
    t = ctx.tender(tender_id)
    qv = ctx.db.get(QuoteVersion, quote_id)
    if qv is None or qv.tender_id != t.id or not qv.export_key:
        raise HTTPException(404, "No export")
    return Response(storage.get(qv.export_key), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{qv.export_report.get("filename", "boq.xlsx")}"'})


class ScenarioIn(BaseModel):
    interest_pct_pa: str = "10"
    cases: list[dict] = Field(max_length=20)


@router.post("/quotes/{quote_id}/scenarios")
def run_scenarios(tender_id: int, quote_id: int, body: ScenarioIn, ctx: Ctx = Depends(get_ctx)):
    ctx.require("price.read")
    t = ctx.tender(tender_id)
    qv = ctx.db.get(QuoteVersion, quote_id)
    if qv is None or qv.tender_id != t.id:
        raise HTTPException(404, "Quote not found")
    cost, trade_totals = Decimal(0), {}
    basis = qv.snapshot.get("cost_basis", {})
    for ln in qv.snapshot["lines"]:
        cpu = try_D(basis.get(str(ln["id"])))
        if cpu is None:
            continue
        c = cpu * D(ln["quantity"])
        cost += c
        trade_totals[ln.get("trade", "civil")] = trade_totals.get(ln.get("trade", "civil"), Decimal(0)) + c
    try:
        return boqsvc.scenarios(D(qv.total), cost, trade_totals, body.model_dump())
    except (ValueError, ArithmeticError) as exc:
        raise HTTPException(422, str(exc))
