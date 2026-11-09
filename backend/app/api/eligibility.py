"""Eligibility rules (review/approve), evaluation, overrides, readiness and checklist tasks."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..audit import audit
from ..deps import Ctx, get_ctx
from ..models import ChecklistTask, EligibilityRule, Evaluation, EvidenceLink, EvidenceRecord, Requirement, User
from ..services.eligibility import RuleError, approve_rule, blockers, evaluate_tender
from ..services.requirements import current_version
from .common import parse_date_field, to_dict

router = APIRouter(prefix="/api/v1/tenders/{tender_id}", tags=["eligibility"])


def rule_out(ctx: Ctx, r: EligibilityRule) -> dict:
    d = to_dict(r)
    if r.requirement_id:
        req = ctx.db.get(Requirement, r.requirement_id)
        v = current_version(req) if req else None
        d["requirement"] = {"id": req.id, "key": req.key, "title": req.title,
                            "excerpt": v.anchor.excerpt if v and v.anchor else None,
                            "clause_no": v.anchor.clause_no if v and v.anchor else None,
                            "page_index": v.anchor.page_index if v and v.anchor else None,
                            "document_id": v.anchor.document_id if v and v.anchor else None,
                            "bbox": v.anchor.bbox if v and v.anchor else None,
                            "confidence": v.confidence if v else None} if req else None
    return d


@router.get("/rules")
def list_rules(tender_id: int, include_history: bool = False, ctx: Ctx = Depends(get_ctx)):
    ctx.require("tender.read")
    t = ctx.tender(tender_id)
    stmt = select(EligibilityRule).where(EligibilityRule.tender_id == t.id)
    if not include_history:
        stmt = stmt.where(EligibilityRule.status.in_(["proposed", "approved"]))
    return [rule_out(ctx, r) for r in ctx.db.scalars(stmt.order_by(EligibilityRule.id))]


class ApproveIn(BaseModel):
    params: dict | None = None
    confirm_low_confidence: bool = False


@router.post("/rules/{rule_id}/approve")
def approve(tender_id: int, rule_id: int, body: ApproveIn, ctx: Ctx = Depends(get_ctx)):
    ctx.require("rules.approve")
    t = ctx.tender(tender_id)
    r = ctx.db.get(EligibilityRule, rule_id)
    if r is None or r.tender_id != t.id:
        raise HTTPException(404, "Rule not found")
    try:
        approve_rule(ctx.db, r, ctx.user.id, body.params, body.confirm_low_confidence)
    except RuleError as exc:
        raise HTTPException(409, str(exc))
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="rule.approved", subject_type="rule", subject_id=r.id,
          tender_id=t.id, params=r.params, version=r.version)
    ctx.db.commit()
    return rule_out(ctx, r)


class RuleEditIn(BaseModel):
    params: dict


@router.put("/rules/{rule_id}")
def edit_rule(tender_id: int, rule_id: int, body: RuleEditIn, ctx: Ctx = Depends(get_ctx)):
    """Editing creates a NEW proposed rule version; the old one is superseded (history retained)."""
    ctx.require("rules.approve")
    t = ctx.tender(tender_id)
    r = ctx.db.get(EligibilityRule, rule_id)
    if r is None or r.tender_id != t.id or r.status not in ("proposed", "approved"):
        raise HTTPException(404, "Rule not found")
    r.status = "superseded"
    from ..services.eligibility import _explain, _explainable
    params = {**r.params, **body.params}
    nr = EligibilityRule(tender_id=t.id, requirement_id=r.requirement_id, requirement_version_no=r.requirement_version_no,
                         rule_type=r.rule_type, params=params, version=r.version + 1, status="proposed", material=r.material,
                         proposed_by=f"user:{ctx.user.id}", low_confidence=False,
                         explanation=_explain(r.rule_type, params) if _explainable(r.rule_type, params) else r.explanation)
    ctx.db.add(nr)
    for ev in ctx.db.scalars(select(Evaluation).where(Evaluation.rule_id == r.id, Evaluation.stale.is_(False))):
        ev.stale, ev.stale_reason = True, "Rule edited"
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="rule.edited", subject_type="rule", subject_id=r.id, tender_id=t.id)
    ctx.db.commit()
    return rule_out(ctx, nr)


@router.post("/rules/{rule_id}/reject")
def reject(tender_id: int, rule_id: int, ctx: Ctx = Depends(get_ctx)):
    ctx.require("rules.approve")
    t = ctx.tender(tender_id)
    r = ctx.db.get(EligibilityRule, rule_id)
    if r is None or r.tender_id != t.id:
        raise HTTPException(404, "Rule not found")
    r.status = "rejected"
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="rule.rejected", subject_type="rule", subject_id=r.id, tender_id=t.id)
    ctx.db.commit()
    return rule_out(ctx, r)


@router.post("/evaluate")
def evaluate(tender_id: int, ctx: Ctx = Depends(get_ctx)):
    ctx.require("tender.read")
    t = ctx.tender(tender_id)
    res = evaluate_tender(ctx.db, t)
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="eligibility.evaluated", subject_type="tender",
          subject_id=t.id, tender_id=t.id, results={e.result: sum(1 for x in res if x.result == e.result) for e in res})
    state = dict(t.step_state or {})
    state["eligibility"] = {"status": "in_progress", "at": datetime.now(timezone.utc).isoformat()}
    t.step_state = state
    ctx.db.commit()
    return evaluations(tender_id, ctx)


@router.get("/evaluations")
def evaluations(tender_id: int, ctx: Ctx = Depends(get_ctx)):
    ctx.require("tender.read")
    t = ctx.tender(tender_id)
    rules = {r.id: r for r in ctx.db.scalars(select(EligibilityRule).where(EligibilityRule.tender_id == t.id))}
    out = []
    for e in ctx.db.scalars(select(Evaluation).where(Evaluation.tender_id == t.id, Evaluation.stale.is_(False)).order_by(Evaluation.rule_id)):
        r = rules.get(e.rule_id)
        out.append({**to_dict(e), "rule": rule_out(ctx, r) if r else None})
    stale = ctx.db.query(Evaluation).filter(Evaluation.tender_id == t.id, Evaluation.stale.is_(True),
                                            Evaluation.stale_reason != "Superseded by re-evaluation").count()
    return {"evaluations": out, "stale_count": stale, "readiness": blockers(ctx.db, t)}


class OverrideIn(BaseModel):
    result: str
    rationale: str = Field(min_length=20)


@router.post("/evaluations/{eval_id}/override")
def override(tender_id: int, eval_id: int, body: OverrideIn, ctx: Ctx = Depends(get_ctx)):
    ctx.require("eligibility.override")
    t = ctx.tender(tender_id)
    e = ctx.db.get(Evaluation, eval_id)
    if e is None or e.tender_id != t.id:
        raise HTTPException(404, "Evaluation not found")
    # The underlying computed result is never altered.
    e.override_result, e.override_rationale, e.override_by = body.result, body.rationale, ctx.user.id
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="eligibility.override", subject_type="evaluation",
          subject_id=e.id, tender_id=t.id, underlying=e.result, override=body.result)
    ctx.db.commit()
    return to_dict(e)


@router.get("/bid-no-bid")
def bid_no_bid(tender_id: int, ctx: Ctx = Depends(get_ctx)):
    ctx.require("tender.read")
    t = ctx.tender(tender_id)
    ev = evaluations(tender_id, ctx)
    b = ev["readiness"]
    rows = [{"criterion": e["rule"]["explanation"] if e["rule"] else "", "subject": e["subject"], "result": e["result"],
             "override": e["override_result"], "explanation": e["explanation"], "missing": e["missing_inputs"],
             "remedy": e["remedial_action"], "clause": (e["rule"] or {}).get("requirement", {}) and e["rule"]["requirement"].get("clause_no"),
             "rule_status": e["rule"]["status"] if e["rule"] else None} for e in ev["evaluations"]]
    fails = [r for r in rows if r["result"] == "fail"]
    gaps = [r for r in rows if r["result"] in ("insufficient_evidence", "ambiguous")]
    recommendation = ("Do not bid unless the failures can be remedied as the tender permits" if fails else
                      "Resolve evidence gaps and reviewer confirmations before deciding" if gaps else
                      "No eligibility blockers found in the reviewed rules")
    return {"tender": t.title, "generated_at": datetime.now(timezone.utc).isoformat(), "rows": rows,
            "summary": {"fail": len(fails), "gaps": len(gaps), "pass": sum(1 for r in rows if r["result"] == "pass")},
            "recommendation": recommendation, "review_status": "Draft — requires bid committee review",
            "disclaimer": "This report reflects reviewed rules and verified evidence only. It is not assurance of qualification or award.",
            "blockers": b["blockers"]}


# ---------------------------------------------------------------- tasks
class TaskPatch(BaseModel):
    status: str | None = None
    owner_id: int | None = None
    due_date: str | None = None
    note: str | None = None
    evidence_id: int | None = None
    slot_name: str | None = None


@router.patch("/tasks/{task_id}")
def update_task(tender_id: int, task_id: int, body: TaskPatch, ctx: Ctx = Depends(get_ctx)):
    ctx.require("tasks.write")
    t = ctx.tender(tender_id)
    task = ctx.db.get(ChecklistTask, task_id)
    if task is None or task.tender_id != t.id:
        raise HTTPException(404, "Task not found")
    hist = list(task.history or [])
    now = datetime.now(timezone.utc).isoformat()
    if body.owner_id is not None:
        u = ctx.db.get(User, body.owner_id)
        if u is None:
            raise HTTPException(422, "Unknown owner")
        task.owner_id = body.owner_id
    if body.due_date is not None:
        task.due_date = parse_date_field(body.due_date)
    if body.slot_name is not None:
        task.slot_name = body.slot_name
    if body.evidence_id is not None:
        ev = ctx.own(ctx.db.get(EvidenceRecord, body.evidence_id), "Evidence")
        allowed = {t.bidder_entity_id}
        if t.bidder_type == "jv" and t.jv_id:
            from ..models import JV
            allowed |= {m.legal_entity_id for m in ctx.db.get(JV, t.jv_id).members}
        if ev.legal_entity_id not in allowed:
            raise HTTPException(409, "Evidence belongs to an entity that is not part of this bid")
        if task.evidence_category and ev.category != task.evidence_category:
            raise HTTPException(409, f"Task needs '{task.evidence_category}', evidence is '{ev.category}'")
        if task.evidence_id and task.evidence_id != ev.id:
            hist.append({"at": now, "event": "replaced", "old_evidence": task.evidence_id, "by": ctx.user.id})
        task.evidence_id = ev.id
        ctx.db.add(EvidenceLink(tender_id=t.id, evidence_id=ev.id, requirement_id=task.requirement_id, task_id=task.id,
                                status="proposed"))
        if task.status == "open":
            task.status = "submitted"
    if body.status is not None:
        if body.status not in ("open", "in_progress", "submitted", "accepted", "rejected", "not_applicable"):
            raise HTTPException(422, "Invalid status")
        if body.status == "accepted":
            if task.evidence_category and not task.evidence_id:
                raise HTTPException(409, "Attach the evidence before accepting this task")
            if task.evidence_id:
                ev = ctx.db.get(EvidenceRecord, task.evidence_id)
                if ev.verification_state != "verified":
                    raise HTTPException(409, "Linked evidence must be verified first")
                if t.qualification_cutoff and ev.expiry_date and ev.expiry_date < t.qualification_cutoff:
                    raise HTTPException(409, f"Evidence expires {ev.expiry_date}, before the cutoff {t.qualification_cutoff}")
            task.stale = False
        if body.status == "not_applicable" and task.mandatory and not body.note:
            raise HTTPException(422, "Explain why a mandatory item is not applicable")
        task.status = body.status
    hist.append({"at": now, "by": ctx.user.id, "changes": body.model_dump(exclude_none=True)})
    task.history = hist
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="task.updated", subject_type="task", subject_id=task.id,
          tender_id=t.id, status=task.status)
    ctx.db.commit()
    return to_dict(task)
