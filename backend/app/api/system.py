"""System checks and AI provider settings. API keys are write-only: they are never returned or logged."""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..audit import audit
from ..deps import Ctx, get_ctx
from ..models import AiUsage, Organization, SourceDocument, SourcePage
from ..services import ai, health, secretbox
from ..services.jobs import enqueue
from .common import to_dict

router = APIRouter(prefix="/api/v1", tags=["system"])


@router.get("/system/checks")
def system_checks(refresh: bool = False, ctx: Ctx = Depends(get_ctx)):
    ctx.require("tender.read")
    if refresh:
        ctx.require("org.admin")
    org = ctx.db.get(Organization, ctx.org_id)
    checks = health.run_all(ctx.db, org, refresh=refresh)
    ctx.db.commit()
    return {"checks": checks, "generated_at": datetime.now(timezone.utc).isoformat()}


# ------------------------------------------------------------------ AI provider settings
def _settings_view(ctx: Ctx) -> dict:
    org = ctx.db.get(Organization, ctx.org_id)
    row = ai.settings_row(ctx.db, org.id)
    cfg = ai.load_config(ctx.db, org)
    return {
        "provider": row.provider, "model": row.model, "effort": row.effort,
        "monthly_budget_usd": str(row.monthly_budget_usd), "max_pages_per_run": row.max_pages_per_run,
        "key": {"stored": bool(row.key_ciphertext), "hint": row.key_hint, "source": cfg.key_source,
                "error": cfg.key_error, "updated_at": row.key_updated_at, "encryption": secretbox.scheme()},
        "last_test": {"at": row.last_test_at, "ok": row.last_test_ok, "message": row.last_test_message},
        "consent": bool(org.allow_external_ai),
        "live": ai.get_provider(ctx.db, org).live,
        "usage_this_month": ai.month_usage(ctx.db, org.id),
        "providers": [
            {"id": "offline", "label": "Offline (no AI)", "cost": "Free",
             "description": "Deterministic rules only. Nothing leaves this computer. Always used for thresholds, dates and money."},
            {"id": "anthropic", "label": "Anthropic Claude API", "cost": "Pay per token, billed by Anthropic to your account",
             "description": "Sends tender page text to api.anthropic.com to propose extra requirements with plain-language "
                            "interpretations. Each must quote the page verbatim and be reviewed. Needs your own API key "
                            "from the Claude Console."},
        ],
        "models": [{"id": k, "label": v["label"], "input_per_mtok": str(v["input"]), "output_per_mtok": str(v["output"]),
                    "note": v["note"], "estimate_100_pages": ai.estimate(k, row.effort, 100, 100 * 3000)}
                   for k, v in ai.MODELS.items()],
        "efforts": list(ai.EFFORTS), "price_as_of": ai.PRICE_AS_OF, "price_source": ai.PRICE_SOURCE,
    }


@router.get("/ai/settings")
def get_ai_settings(ctx: Ctx = Depends(get_ctx)):
    ctx.require("tender.read")
    view = _settings_view(ctx)
    ctx.db.commit()
    return view


class AiSettingsIn(BaseModel):
    provider: str | None = None
    model: str | None = None
    effort: str | None = None
    monthly_budget_usd: str | None = Field(default=None, max_length=20)
    max_pages_per_run: int | None = Field(default=None, ge=1, le=5000)


@router.put("/ai/settings")
def put_ai_settings(body: AiSettingsIn, ctx: Ctx = Depends(get_ctx)):
    ctx.require("org.admin")
    org = ctx.db.get(Organization, ctx.org_id)
    row = ai.settings_row(ctx.db, org.id)
    if body.provider is not None:
        if body.provider not in ("offline", "anthropic"):
            raise HTTPException(422, "Provider must be 'offline' or 'anthropic'.")
        if body.provider == "offline":
            org.allow_external_ai = False  # switching off always withdraws consent
        if body.provider != row.provider:
            row.last_test_ok, row.last_test_at, row.last_test_message = None, None, None
        row.provider = body.provider
    if body.model is not None:
        if body.model not in ai.MODELS:
            raise HTTPException(422, f"Unknown model. Choose one of {list(ai.MODELS)}.")
        if body.model != row.model:
            row.last_test_ok, row.last_test_at, row.last_test_message = None, None, "Model changed: run the connection test."
            org.allow_external_ai = False
        row.model = body.model
    if body.effort is not None:
        if body.effort not in ai.EFFORTS:
            raise HTTPException(422, f"Effort must be one of {list(ai.EFFORTS)}.")
        row.effort = body.effort
    if body.monthly_budget_usd is not None:
        try:
            b = Decimal(body.monthly_budget_usd.strip())
        except InvalidOperation:
            raise HTTPException(422, "Monthly budget must be a number of US dollars, e.g. 10.00") from None
        if not (Decimal("0") <= b <= Decimal("100000")):
            raise HTTPException(422, "Monthly budget must be between 0 and 100000 USD.")
        row.monthly_budget_usd = str(b.quantize(Decimal("0.01")))
    if body.max_pages_per_run is not None:
        row.max_pages_per_run = body.max_pages_per_run
    audit(ctx.db, org_id=org.id, user_id=ctx.user.id, action="ai.settings", subject_type="organization", subject_id=org.id,
          provider=row.provider, model=row.model, effort=row.effort, monthly_budget_usd=str(row.monthly_budget_usd),
          max_pages_per_run=row.max_pages_per_run, consent=org.allow_external_ai)
    ctx.db.commit()
    return _settings_view(ctx)


class KeyIn(BaseModel):
    api_key: str = Field(min_length=1, max_length=400)


_KEY_FORMAT = re.compile(r"^sk-ant-[A-Za-z0-9_\-]{20,380}$")


@router.put("/ai/key")
def put_ai_key(body: KeyIn, ctx: Ctx = Depends(get_ctx)):
    ctx.require("org.admin")
    key = body.api_key.strip()
    if not _KEY_FORMAT.match(key):
        # Do not echo the value back.
        raise HTTPException(422, "That does not look like an Anthropic API key (it should start with 'sk-ant-' and "
                                 "contain no spaces). Copy it again from the Claude Console, API keys.")
    org = ctx.db.get(Organization, ctx.org_id)
    row = ai.save_key(ctx.db, org.id, ctx.user.id, key)
    org.allow_external_ai = False  # a new key must pass the connection test before use
    audit(ctx.db, org_id=org.id, user_id=ctx.user.id, action="ai.key_saved", subject_type="organization", subject_id=org.id,
          key_hint=row.key_hint, encryption=secretbox.scheme())
    ctx.db.commit()
    return _settings_view(ctx)


@router.delete("/ai/key")
def delete_ai_key(ctx: Ctx = Depends(get_ctx)):
    ctx.require("org.admin")
    org = ctx.db.get(Organization, ctx.org_id)
    ai.clear_key(ctx.db, org.id)
    org.allow_external_ai = False
    audit(ctx.db, org_id=org.id, user_id=ctx.user.id, action="ai.key_removed", subject_type="organization", subject_id=org.id)
    ctx.db.commit()
    return _settings_view(ctx)


@router.post("/ai/test")
def test_ai(ctx: Ctx = Depends(get_ctx)):
    ctx.require("org.admin")
    org = ctx.db.get(Organization, ctx.org_id)
    provider = ai.configured_provider(ctx.db, org)
    if provider is None:
        raise HTTPException(409, "Choose the Anthropic provider first.")
    t0 = time.monotonic()
    ok, msg = provider.test_connection()
    msg = secretbox.redact(msg)
    row = ai.settings_row(ctx.db, org.id)
    row.last_test_at, row.last_test_ok, row.last_test_message = datetime.now(timezone.utc), ok, msg
    if not ok:
        org.allow_external_ai = False
    audit(ctx.db, org_id=org.id, user_id=ctx.user.id, action="ai.test", subject_type="organization", subject_id=org.id,
          ok=ok, model=row.model, seconds=round(time.monotonic() - t0, 2))
    ctx.db.commit()
    return {"ok": ok, "message": msg, "settings": _settings_view(ctx)}


@router.get("/ai/usage")
def ai_usage(limit: int = 50, ctx: Ctx = Depends(get_ctx)):
    ctx.require("tender.read")
    rows = ctx.db.scalars(select(AiUsage).where(AiUsage.org_id == ctx.org_id).order_by(AiUsage.id.desc()).limit(min(limit, 500)))
    return [to_dict(r) for r in rows]


# ------------------------------------------------------------------ per-document AI review
def _doc(ctx: Ctx, tender_id: int, doc_id: int) -> SourceDocument:
    ctx.tender(tender_id)
    d = ctx.db.get(SourceDocument, doc_id)
    if d is None or d.tender_id != tender_id or d.org_id != ctx.org_id:
        raise HTTPException(404, "Document not found")
    return d


@router.get("/tenders/{tender_id}/documents/{doc_id}/ai-estimate")
def ai_estimate(tender_id: int, doc_id: int, ctx: Ctx = Depends(get_ctx)):
    ctx.require("tender.read")
    d = _doc(ctx, tender_id, doc_id)
    org = ctx.db.get(Organization, ctx.org_id)
    cfg = ai.load_config(ctx.db, org)
    pages = list(ctx.db.scalars(select(SourcePage).where(SourcePage.document_id == d.id, SourcePage.status == "processed")))
    chars = sum(len(p.text or "") for p in pages)
    n = min(len(pages), cfg.max_pages)
    view = {**ai.estimate(cfg.model, cfg.effort, n, int(chars * (n / len(pages))) if pages else 0),
            "model": cfg.model, "effort": cfg.effort, "live": ai.get_provider(ctx.db, org).live,
            "spent_this_month": str(ai.month_spend(ctx.db, org.id)), "budget": str(cfg.budget),
            "capped": len(pages) > cfg.max_pages}
    ctx.db.commit()
    return view


@router.post("/tenders/{tender_id}/documents/{doc_id}/ai-review")
def ai_review(tender_id: int, doc_id: int, ctx: Ctx = Depends(get_ctx)):
    ctx.require("documents.write")
    d = _doc(ctx, tender_id, doc_id)
    org = ctx.db.get(Organization, ctx.org_id)
    if not ai.get_provider(ctx.db, org).live:
        raise HTTPException(409, "AI review is not enabled. An org admin must choose a provider, save a key, pass the "
                                 "connection test and enable sending tender text (Settings, AI provider).")
    if d.status != "processed":
        raise HTTPException(409, f"The document is '{d.status}'; AI review needs processed pages.")
    # Unique key per request; pages already read by the same model are skipped (no double billing).
    job = enqueue(ctx.db, kind="ai_extract", org_id=ctx.org_id, tender_id=tender_id, document_id=d.id,
                  key=f"ai:{d.id}:{d.sha256}:manual:{datetime.now(timezone.utc).timestamp():.0f}")
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="ai.review_requested", subject_type="source_document",
          subject_id=d.id, tender_id=tender_id)
    ctx.db.commit()
    return {"job_id": job.id}
