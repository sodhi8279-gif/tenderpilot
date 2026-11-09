"""Versioned deterministic eligibility engine (rules-1.0).

Results: pass / fail / insufficient_evidence / ambiguous / not_applicable. Unknown never passes.
* Only VERIFIED evidence of the exact legal entity is used. Evidence whose printed subject name
  differs from the entity's legal name needs an approved attribution link for this tender.
* Validity is tested at the tender's confirmed qualification cutoff date, never "today".
* Proposed (unconfirmed) material rules produce `ambiguous` and cannot make a tender ready.
* JV: aggregation is never assumed; each rule must state how members combine.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import (JV, ChecklistTask, EligibilityRule, Evaluation, EvidenceLink, EvidenceRecord, LegalEntity,
                      Requirement, Tender)
from .money import D, format_inr, q, try_D
from .requirements import current_version

ENGINE_VERSION = "rules-1.0"
RULE_MAP = {
    "fin.avg_turnover": "avg_turnover", "fin.net_worth": "net_worth", "fin.solvency": "solvency",
    "fin.bid_capacity": "bid_capacity", "jv.permitted": "jv_permitted", "jv.max_members": "jv_max_members",
    "jv.lead_share": "jv_lead_share",
}
PASS, FAIL, INSUFF, AMBIG, NA = "pass", "fail", "insufficient_evidence", "ambiguous", "not_applicable"


# ------------------------------------------------------------------ proposal & approval
def _explain(rule_type: str, p: dict) -> str:
    if rule_type == "avg_turnover":
        how = f"best {p.get('best')} of last {p.get('years')}" if p.get("method") == "best_n_of_m" else f"last {p.get('years') or '?'}"
        return f"Average annual turnover over the {how} financial years must be at least ₹{format_inr(p['threshold'])}."
    if rule_type == "similar_work":
        val = f"{p['percent_of_estimate']}% of the estimated cost" if p.get("percent_of_estimate") else f"₹{format_inr(p['amount'])}" if p.get("amount") else "the stated value"
        return (f"At least {p.get('count')} similar work(s), each of value not less than {val}, completed within the last "
                f"{p.get('period_years') or '?'} years before the cutoff." +
                (" Substantially completed works are accepted per the clause." if p.get("allow_substantial_completion") else ""))
    if rule_type == "net_worth":
        return "Net worth must be positive." if p.get("positive_only") else f"Net worth must be at least ₹{format_inr(p['min'])}."
    if rule_type == "solvency":
        return f"Solvency certificate of at least ₹{format_inr(p['min'])} valid at the cutoff."
    if rule_type == "document_present":
        return f"A verified '{p['evidence_category']}' document valid at the cutoff must be on record."
    if rule_type == "bid_capacity":
        return f"Bid capacity = A × N × {p.get('M') or 'M'} − B must be at least the estimated cost."
    if rule_type == "jv_permitted":
        return "Joint ventures are " + ("permitted." if p.get("permitted") else "NOT permitted.")
    if rule_type == "jv_max_members":
        return f"A JV may have at most {p.get('max_members')} members."
    if rule_type == "jv_lead_share":
        return f"Lead member share must be at least {p.get('min_percent')}%."
    return rule_type


def _params_for(key: str, value: dict) -> tuple[str, dict] | None:
    if key in RULE_MAP:
        rt = RULE_MAP[key]
        if rt == "avg_turnover":
            return rt, {"threshold": value.get("amount"), "years": value.get("years"), "method": value.get("method", "last_n"),
                        "best": value.get("best")}
        if rt == "net_worth":
            return rt, {"positive_only": value.get("positive_only", False), "min": value.get("amount")}
        if rt == "solvency":
            return rt, {"min": value.get("amount")}
        if rt == "bid_capacity":
            return rt, {"M": value.get("M")}
        if rt == "jv_permitted":
            return rt, {"permitted": value.get("permitted")}
        if rt == "jv_max_members":
            return rt, {"max_members": value.get("max_members")}
        if rt == "jv_lead_share":
            return rt, {"min_percent": value.get("min_percent")}
    if key.startswith("exp.similar_work"):
        p = dict(value)
        p["group"] = "similar_work"
        p.setdefault("substantial_threshold_pct", None)
        return "similar_work", p
    if key.startswith("doc."):
        return "document_present", {"evidence_category": value.get("evidence_category", "other")}
    return None


def propose_rules(db: Session, tender: Tender) -> int:
    n = 0
    for req in db.scalars(select(Requirement).where(Requirement.tender_id == tender.id)):
        v = current_version(req)
        if v is None or v.review_status == "rejected":
            continue
        mapped = _params_for(req.key, v.value)
        if not mapped:
            continue
        rt, params = mapped
        exists = db.scalar(select(EligibilityRule).where(EligibilityRule.requirement_id == req.id,
                                                         EligibilityRule.requirement_version_no == v.version_no,
                                                         EligibilityRule.status.in_(["proposed", "approved"])))
        if exists:
            continue
        prev = db.scalar(select(EligibilityRule).where(EligibilityRule.requirement_id == req.id)
                         .order_by(EligibilityRule.version.desc()).limit(1))
        db.add(EligibilityRule(tender_id=tender.id, requirement_id=req.id, requirement_version_no=v.version_no,
                               rule_type=rt, params=params, version=(prev.version + 1) if prev else 1,
                               engine_version=ENGINE_VERSION, status="proposed", material=req.mandatory,
                               explanation=_explain(rt, params) if _explainable(rt, params) else req.title,
                               low_confidence=v.confidence < settings.low_confidence_threshold))
        n += 1
    db.flush()
    return n


def _explainable(rt: str, p: dict) -> bool:
    try:
        _explain(rt, p)
        return True
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return False


class RuleError(ValueError):
    pass


def approve_rule(db: Session, rule: EligibilityRule, user_id: int, params: dict | None, confirm_low_confidence: bool) -> None:
    if rule.status not in ("proposed", "approved"):
        raise RuleError("Only proposed rules can be approved")
    req = db.get(Requirement, rule.requirement_id) if rule.requirement_id else None
    if req and req.has_conflict:
        raise RuleError("Resolve the conflicting/amended requirement before approving its rule")
    if rule.low_confidence and not (params and confirm_low_confidence):
        raise RuleError("Low-confidence extraction: re-enter the threshold values from the source and confirm")
    if params:
        merged = {**rule.params, **params}
        rule.params = merged
        rule.explanation = _explain(rule.rule_type, merged)
    rule.status, rule.approved_by, rule.approved_at = "approved", user_id, datetime.now(timezone.utc)


# ------------------------------------------------------------------ evidence access
_FORM_ALIASES = [(r"\bpvt\b\.?", "private"), (r"\bltd\b\.?", "limited"), (r"\bco\b\.?", "company"),
                 (r"\b(m/s|ms)\b\.?", " "), (r"\bthe\b", " "), (r"&", " and ")]


def norm_name(s: str | None) -> str:
    """Normalise spelling variants only. Legal form (Ltd vs LLP vs partnership) stays significant."""
    s = (s or "").lower()
    for pat, rep in _FORM_ALIASES:
        s = re.sub(pat, rep, s)
    return re.sub(r"[^a-z0-9]", "", s)


@dataclass
class EvidenceView:
    usable: list[EvidenceRecord] = field(default_factory=list)
    excluded: list[dict] = field(default_factory=list)


def evidence_for(db: Session, tender: Tender, entity: LegalEntity, categories: list[str]) -> EvidenceView:
    out = EvidenceView()
    recs = db.scalars(select(EvidenceRecord).where(EvidenceRecord.legal_entity_id == entity.id,
                                                   EvidenceRecord.org_id == tender.org_id,
                                                   EvidenceRecord.category.in_(categories),
                                                   EvidenceRecord.is_current.is_(True)))
    for r in recs:
        if r.verification_state != "verified":
            out.excluded.append({"evidence_id": r.id, "title": r.title, "reason": f"Not verified ({r.verification_state})"})
            continue
        if r.subject_name_on_document and norm_name(r.subject_name_on_document) != norm_name(entity.legal_name):
            link = db.scalar(select(EvidenceLink).where(EvidenceLink.tender_id == tender.id, EvidenceLink.evidence_id == r.id,
                                                        EvidenceLink.attribution_entity_id == entity.id,
                                                        EvidenceLink.attribution_approved.is_(True)))
            if link is None:
                out.excluded.append({"evidence_id": r.id, "title": r.title,
                                     "reason": f"Name on document '{r.subject_name_on_document}' differs from "
                                               f"'{entity.legal_name}'; approved attribution required"})
                continue
        out.usable.append(r)
    return out


def _valid_at(r: EvidenceRecord, cutoff: date) -> bool:
    return r.expiry_date is None or r.expiry_date >= cutoff


def fy_label(end_year: int) -> str:
    return f"{end_year - 1}-{str(end_year)[-2:]}"


def financial_years_before(cutoff: date, n: int) -> list[str]:
    last_end = cutoff.year if cutoff.month >= 4 else cutoff.year - 1
    return [fy_label(y) for y in range(last_end, last_end - n, -1)]


def _norm_fy(s: str) -> str:
    m = re.search(r"(20\d{2})\s*[-/–]\s*(\d{2,4})", str(s))
    if not m:
        return str(s)
    start = int(m.group(1))
    return fy_label(start + 1)


# ------------------------------------------------------------------ evaluation
@dataclass
class Outcome:
    result: str
    explanation: str
    calculation: dict = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    remedy: str | None = None
    evidence_ids: list[int] = field(default_factory=list)


def _requirement_value(db: Session, tender: Tender, key: str) -> dict | None:
    req = db.scalar(select(Requirement).where(Requirement.tender_id == tender.id, Requirement.key == key))
    if not req:
        return None
    v = current_version(req)
    return v.value if v and v.review_status != "rejected" else None


def eval_avg_turnover(db, tender, entity, p, cutoff) -> Outcome:
    thr, years = try_D(p.get("threshold")), p.get("years")
    if thr is None or not years:
        return Outcome(AMBIG, "Threshold or number of years not confirmed in the rule.", missing=["rule threshold/years"])
    ev = evidence_for(db, tender, entity, ["turnover_certificate", "audited_accounts"])
    fys = p.get("fy_list") or financial_years_before(cutoff, int(years))
    by_fy: dict[str, tuple[Decimal, int]] = {}
    for r in ev.usable:
        amt = try_D(r.fields.get("turnover") or r.fields.get("amount"))
        fy = r.fields.get("fy")
        if amt is not None and fy:
            by_fy.setdefault(_norm_fy(fy), (amt, r.id))
    missing = [fy for fy in fys if fy not in by_fy]
    calc = {"required_fys": fys, "values": {fy: str(by_fy[fy][0]) for fy in fys if fy in by_fy},
            "threshold": str(thr), "excluded_evidence": ev.excluded}
    if missing:
        return Outcome(INSUFF, f"Turnover evidence missing for FY {', '.join(missing)}.", calc,
                       [f"Turnover certificate / audited accounts for FY {fy}" for fy in missing],
                       "Upload CA-certified turnover or audited accounts for the missing years.",
                       [by_fy[f][1] for f in fys if f in by_fy])
    vals = [by_fy[fy][0] for fy in fys]
    if p.get("method") == "best_n_of_m" and p.get("best"):
        vals = sorted(vals, reverse=True)[: int(p["best"])]
    avg = q(sum(vals, Decimal(0)) / Decimal(len(vals)), 2)
    calc.update({"used_values": [str(v) for v in vals], "average": str(avg), "formula": "sum(values) / count"})
    ok = avg >= thr
    return Outcome(PASS if ok else FAIL,
                   f"Average ₹{format_inr(avg)} {'≥' if ok else '<'} required ₹{format_inr(thr)}.", calc,
                   remedy=None if ok else "Consider a JV only if permitted and if the clause allows aggregation.",
                   evidence_ids=[by_fy[f][1] for f in fys])


def eval_similar_work(db, tender, entity, p, cutoff) -> Outcome:
    count = int(p.get("count") or 0)
    estimate = try_D((_requirement_value(db, tender, "commercial.estimate") or {}).get("amount"))
    if p.get("amount"):
        thr = try_D(p["amount"])
        basis = "fixed amount in clause"
    elif p.get("percent_of_estimate"):
        if estimate is None:
            return Outcome(AMBIG, "Threshold depends on the estimated cost, which is not available.",
                           missing=["Estimated cost (commercial.estimate)"])
        thr = q(estimate * D(p["percent_of_estimate"]) / Decimal(100), 2)
        basis = f"{p['percent_of_estimate']}% × estimate ₹{format_inr(estimate)}"
    else:
        return Outcome(AMBIG, "Value threshold not defined in the rule.", missing=["similar work threshold"])
    period = p.get("period_years")
    if not period:
        return Outcome(AMBIG, "Qualifying period not confirmed.", missing=["period_years"])
    start = date(cutoff.year - int(period), cutoff.month, min(cutoff.day, 28))
    esc_rate = try_D(p.get("escalation_pct_per_year")) if p.get("escalation_authorized") else None
    ev = evidence_for(db, tender, entity, ["completion_certificate", "performance_certificate"])
    rows, qualifying, incomplete = [], [], []
    for r in ev.usable:
        f = r.fields
        val = try_D(f.get("executed_value") or f.get("value"))
        cdate = f.get("completion_date")
        status = f.get("status", "completed")
        row = {"evidence_id": r.id, "work": f.get("work_name") or r.title, "value": str(val) if val is not None else None,
               "completion_date": cdate, "status": status}
        if val is None or not cdate:
            row["result"] = "missing value or completion date"
            incomplete.append(r.title)
            rows.append(row)
            continue
        if f.get("is_similar") is not True:
            row["result"] = "similarity not confirmed by reviewer"
            incomplete.append(r.title)
            rows.append(row)
            continue
        cd = date.fromisoformat(cdate)
        if not (start <= cd <= cutoff):
            row["result"] = f"outside qualifying period {start} to {cutoff}"
            rows.append(row)
            continue
        if status == "substantially_completed":
            if not p.get("allow_substantial_completion"):
                row["result"] = "substantially completed works not allowed by clause"
                rows.append(row)
                continue
            st = try_D(p.get("substantial_threshold_pct"))
            pct = try_D(f.get("completed_pct"))
            if st is None or pct is None:
                row["result"] = "substantial-completion threshold or evidence missing"
                incomplete.append(r.title)
                rows.append(row)
                continue
            if pct < st:
                row["result"] = f"completed {pct}% < required {st}%"
                rows.append(row)
                continue
        eff = val
        if esc_rate is not None:
            yrs = Decimal((cutoff - cd).days) / Decimal("365")
            eff = q(val * (1 + esc_rate / 100) ** int(yrs), 2)
            row["escalated_value"] = str(eff)
        if eff >= thr:
            row["result"] = "qualifies"
            qualifying.append(r.id)
        else:
            row["result"] = f"value below ₹{format_inr(thr)}"
        rows.append(row)
    calc = {"threshold": str(thr), "threshold_basis": basis, "period": [start.isoformat(), cutoff.isoformat()],
            "works": rows, "required_count": count, "qualifying_count": len(qualifying), "excluded_evidence": ev.excluded}
    if len(qualifying) >= count:
        return Outcome(PASS, f"{len(qualifying)} qualifying work(s) ≥ {count} required.", calc, evidence_ids=qualifying)
    if incomplete:
        return Outcome(INSUFF, f"Only {len(qualifying)} qualifying; {len(incomplete)} work record(s) incomplete.", calc,
                       [f"Complete/confirm: {t}" for t in incomplete],
                       "Upload completion certificates showing executed value and completion date; confirm similarity.",
                       qualifying)
    if not ev.usable:
        return Outcome(INSUFF, "No verified completion certificates on record.", calc,
                       ["Completion certificates for similar works"], "Upload completion certificates.")
    return Outcome(FAIL, f"Only {len(qualifying)} qualifying work(s); {count} required.", calc, evidence_ids=qualifying)


def eval_net_worth(db, tender, entity, p, cutoff) -> Outcome:
    ev = evidence_for(db, tender, entity, ["net_worth", "audited_accounts"])
    cands = [(r, try_D(r.fields.get("net_worth") or r.fields.get("amount"))) for r in ev.usable]
    cands = [(r, a) for r, a in cands if a is not None and (r.fields.get("as_of") is None or date.fromisoformat(r.fields["as_of"]) <= cutoff)]
    if not cands:
        return Outcome(INSUFF, "No verified net worth certificate on record.", {"excluded_evidence": ev.excluded},
                       ["CA net worth certificate"], "Obtain CA-certified net worth certificate.")
    r, amt = max(cands, key=lambda x: x[0].fields.get("as_of") or "")
    if p.get("positive_only"):
        ok, need = amt > 0, "positive"
    else:
        m = try_D(p.get("min"))
        if m is None:
            return Outcome(AMBIG, "Minimum net worth not confirmed.", missing=["min"])
        ok, need = amt >= m, f"≥ ₹{format_inr(m)}"
    return Outcome(PASS if ok else FAIL, f"Net worth ₹{format_inr(amt)} (required {need}).",
                   {"net_worth": str(amt), "as_of": r.fields.get("as_of")}, evidence_ids=[r.id])


def eval_solvency(db, tender, entity, p, cutoff) -> Outcome:
    m = try_D(p.get("min"))
    if m is None:
        return Outcome(AMBIG, "Solvency amount not confirmed.", missing=["min"])
    ev = evidence_for(db, tender, entity, ["solvency"])
    valid = [r for r in ev.usable if _valid_at(r, cutoff)]
    expired = [r for r in ev.usable if not _valid_at(r, cutoff)]
    if not valid:
        msg = "Solvency certificate expired before the cutoff." if expired else "No verified solvency certificate."
        return Outcome(INSUFF, msg, {"expired": [r.id for r in expired], "cutoff": cutoff.isoformat()},
                       ["Bank solvency certificate valid at cutoff"], "Request a fresh solvency certificate from the bank.")
    best = max(valid, key=lambda r: try_D(r.fields.get("amount")) or Decimal(0))
    amt = try_D(best.fields.get("amount")) or Decimal(0)
    ok = amt >= m
    return Outcome(PASS if ok else FAIL, f"Solvency ₹{format_inr(amt)} vs required ₹{format_inr(m)}.",
                   {"amount": str(amt), "cutoff": cutoff.isoformat()}, evidence_ids=[best.id])


def eval_document(db, tender, entity, p, cutoff) -> Outcome:
    cat = p.get("evidence_category")
    ev = evidence_for(db, tender, entity, [cat])
    valid = [r for r in ev.usable if _valid_at(r, cutoff)]
    if valid:
        return Outcome(PASS, f"Verified '{cat}' on record, valid at {cutoff}.", {"cutoff": cutoff.isoformat()},
                       evidence_ids=[valid[0].id])
    expired = [r for r in ev.usable if not _valid_at(r, cutoff)]
    reason = (f"'{cat}' on record expires {expired[0].expiry_date}, before the cutoff {cutoff}." if expired
              else f"No verified '{cat}' document for {entity.legal_name}.")
    return Outcome(INSUFF, reason, {"cutoff": cutoff.isoformat(), "excluded_evidence": ev.excluded,
                                    "expired": [r.id for r in expired]},
                   [f"{cat} for {entity.legal_name}"], f"Upload a current '{cat}' document.")


def eval_bid_capacity(db, tender, entity, p, cutoff) -> Outcome:
    M = try_D(p.get("M"))
    est = try_D((_requirement_value(db, tender, "commercial.estimate") or {}).get("amount"))
    cp = _requirement_value(db, tender, "commercial.completion_period") or {}
    missing = []
    if M is None:
        missing.append("multiplier M")
    if est is None:
        missing.append("estimated cost")
    if not cp.get("value"):
        missing.append("completion period")
    if missing:
        return Outcome(AMBIG, "Bid-capacity inputs incomplete.", missing=missing)
    n_years = D(cp["value"]) / (Decimal(12) if cp.get("unit", "month").startswith("month") else Decimal(1))
    ev = evidence_for(db, tender, entity, ["turnover_certificate", "audited_accounts"])
    fys = financial_years_before(cutoff, int(p.get("years_for_A") or 5))
    vals = [try_D(r.fields.get("turnover") or r.fields.get("amount")) for r in ev.usable if _norm_fy(r.fields.get("fy", "")) in fys]
    vals = [v for v in vals if v is not None]
    com = evidence_for(db, tender, entity, ["existing_commitments"])
    if not vals or not com.usable:
        return Outcome(INSUFF, "Need turnover for the last years and a statement of existing commitments.",
                       missing=[m for m, ok in (("turnover (A)", bool(vals)), ("existing commitments (B)", bool(com.usable))) if not ok])
    A = max(vals)
    B = sum((try_D(r.fields.get("amount")) or Decimal(0) for r in com.usable), Decimal(0))
    cap = q(A * n_years * M - B, 2)
    ok = cap >= est
    return Outcome(PASS if ok else FAIL, f"Bid capacity ₹{format_inr(cap)} vs estimate ₹{format_inr(est)}.",
                   {"A": str(A), "N": str(n_years), "M": str(M), "B": str(B), "capacity": str(cap), "formula": "A*N*M-B"})


SINGLE = {"avg_turnover": eval_avg_turnover, "similar_work": eval_similar_work, "net_worth": eval_net_worth,
          "solvency": eval_solvency, "document_present": eval_document, "bid_capacity": eval_bid_capacity}


def eval_jv_rule(rule: EligibilityRule, jv: JV | None) -> Outcome:
    p = rule.params
    if jv is None:
        return Outcome(NA, "Bidder is a single entity.")
    if rule.rule_type == "jv_permitted":
        if p.get("permitted") is None:
            return Outcome(AMBIG, "JV permission not determined.", missing=["JV permission"])
        return Outcome(PASS if p["permitted"] else FAIL, _explain("jv_permitted", p))
    if rule.rule_type == "jv_max_members":
        n = len(jv.members)
        ok = n <= int(p.get("max_members") or 0)
        return Outcome(PASS if ok else FAIL, f"JV has {n} member(s); maximum {p.get('max_members')}.", {"members": n})
    if rule.rule_type == "jv_lead_share":
        leads = [m for m in jv.members if m.role == "lead"]
        if len(leads) != 1:
            return Outcome(INSUFF, "Exactly one lead member must be designated.", missing=["lead member"])
        share = D(leads[0].share_pct)
        need = D(p.get("min_percent") or 0)
        total = sum((D(m.share_pct) for m in jv.members), Decimal(0))
        if total != Decimal(100):
            return Outcome(FAIL, f"Member shares total {total}%, not 100%.", {"total": str(total)})
        return Outcome(PASS if share >= need else FAIL, f"Lead share {share}% (minimum {need}%).", {"lead_share": str(share)})
    return Outcome(AMBIG, "Unknown JV rule")


def eval_for_jv(db, tender, jv: JV, rule: EligibilityRule, cutoff) -> list[tuple[str, Outcome]]:
    """JV evaluation per the rule's explicit 'jv' configuration; never assumes additivity."""
    cfg = rule.params.get("jv") or {}
    mode = cfg.get("mode")  # aggregate_sum / lead_only / each_member / any_member
    if not mode:
        return [("jv", Outcome(AMBIG, "The tender's JV rule for this criterion is not configured (how members combine).",
                               missing=["JV aggregation method for this criterion (from the JV clause)"]))]
    fn = SINGLE[rule.rule_type]
    entities = {m.legal_entity_id: db.get(LegalEntity, m.legal_entity_id) for m in jv.members}
    outs: list[tuple[str, Outcome]] = []
    if mode in ("lead_only", "each_member", "any_member"):
        targets = [m for m in jv.members if m.role == "lead"] if mode == "lead_only" else list(jv.members)
        res = [(m, fn(db, tender, entities[m.legal_entity_id], rule.params, cutoff)) for m in targets]
        for m, o in res:
            outs.append((f"entity:{m.legal_entity_id}", o))
        if mode == "any_member":
            agg = PASS if any(o.result == PASS for _, o in res) else (INSUFF if any(o.result == INSUFF for _, o in res) else FAIL)
            outs.append(("jv:aggregate", Outcome(agg, f"Any one member must satisfy: {agg}.")))
        return outs
    if mode == "aggregate_sum" and rule.rule_type == "avg_turnover":
        thr = D(rule.params["threshold"])
        lead_pct = try_D(cfg.get("lead_min_pct"))
        mem_pct = try_D(cfg.get("member_min_pct"))
        total = Decimal(0)
        any_gap = False
        for m in jv.members:
            o = eval_avg_turnover(db, tender, entities[m.legal_entity_id], {**rule.params, "threshold": "0"}, cutoff)
            if o.result == INSUFF or "average" not in o.calculation:
                any_gap = True
                outs.append((f"entity:{m.legal_entity_id}", o))
                continue
            avg = D(o.calculation["average"])
            total += avg
            need_pct = lead_pct if m.role == "lead" else mem_pct
            if need_pct is not None:
                need = q(thr * need_pct / 100, 2)
                ok = avg >= need
                outs.append((f"entity:{m.legal_entity_id}", Outcome(
                    PASS if ok else FAIL, f"{entities[m.legal_entity_id].legal_name}: average ₹{format_inr(avg)} vs individual minimum "
                                          f"{need_pct}% = ₹{format_inr(need)}.", {"average": str(avg), "minimum": str(need)})))
            else:
                outs.append((f"entity:{m.legal_entity_id}", Outcome(NA, f"No individual minimum; average ₹{format_inr(avg)}.",
                                                                     {"average": str(avg)})))
        if any_gap:
            outs.append(("jv:aggregate", Outcome(INSUFF, "Aggregate cannot be computed: member evidence missing.")))
        else:
            ok = total >= thr
            outs.append(("jv:aggregate", Outcome(PASS if ok else FAIL,
                                                 f"Aggregate ₹{format_inr(total)} vs required ₹{format_inr(thr)}.",
                                                 {"aggregate": str(total), "threshold": str(thr)})))
        return outs
    return [("jv", Outcome(AMBIG, f"JV mode '{mode}' is not supported for {rule.rule_type}; evaluate manually."))]


def _hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def evaluate_tender(db: Session, tender: Tender) -> list[Evaluation]:
    rules = list(db.scalars(select(EligibilityRule).where(EligibilityRule.tender_id == tender.id,
                                                          EligibilityRule.status.in_(["proposed", "approved"]))))
    cutoff = tender.qualification_cutoff
    jv = db.get(JV, tender.jv_id) if tender.bidder_type == "jv" and tender.jv_id else None
    entity = db.get(LegalEntity, tender.bidder_entity_id) if tender.bidder_entity_id else None
    results: list[Evaluation] = []
    for old in db.scalars(select(Evaluation).where(Evaluation.tender_id == tender.id, Evaluation.stale.is_(False))):
        old.stale, old.stale_reason = True, "Superseded by re-evaluation"
    for rule in rules:
        outs: list[tuple[str, Outcome]]
        if rule.rule_type.startswith("jv_"):
            outs = [("bidder", eval_jv_rule(rule, jv))]
        elif jv is None and entity is None:
            outs = [("bidder", Outcome(INSUFF, "No bidder entity or JV selected.", missing=["bidder"]))]
        elif cutoff is None:
            outs = [("bidder", Outcome(AMBIG, "Qualification cutoff date not confirmed; results cannot be computed "
                                               "against today's date.", missing=["qualification cutoff date"]))]
        elif jv is not None:
            outs = eval_for_jv(db, tender, jv, rule, cutoff)
        else:
            outs = [("bidder", SINGLE[rule.rule_type](db, tender, entity, rule.params, cutoff))]
        for subject, o in outs:
            result, expl = o.result, o.explanation
            if rule.status != "approved" and rule.material and result != NA:
                result, expl = AMBIG, f"Preview only ({o.result}): rule awaits reviewer confirmation. {o.explanation}"
            ev = Evaluation(tender_id=tender.id, rule_id=rule.id, rule_version=rule.version, subject=subject,
                            result=result, explanation=expl,
                            calculation={**o.calculation, "engine": ENGINE_VERSION, "preview_result": o.result,
                                         "evidence_ids": o.evidence_ids},
                            missing_inputs=o.missing, remedial_action=o.remedy,
                            inputs_hash=_hash([rule.params, rule.version, str(cutoff), tender.bidder_entity_id,
                                               tender.jv_id, o.evidence_ids]))
            db.add(ev)
            results.append(ev)
    db.flush()
    return results


# ------------------------------------------------------------------ readiness
def blockers(db: Session, tender: Tender) -> dict:
    items: list[dict] = []
    for req in db.scalars(select(Requirement).where(Requirement.tender_id == tender.id)):
        v = current_version(req)
        if req.has_conflict:
            items.append({"type": "conflict", "severity": "critical", "requirement_id": req.id,
                          "message": f"Unresolved conflict/amendment: {req.title}"})
        elif v and req.mandatory and v.review_status in ("unreviewed", "needs_review") and v.confidence < settings.low_confidence_threshold:
            items.append({"type": "low_confidence", "severity": "high", "requirement_id": req.id,
                          "message": f"Low-confidence extraction not reviewed: {req.title}"})
    rules = list(db.scalars(select(EligibilityRule).where(EligibilityRule.tender_id == tender.id,
                                                          EligibilityRule.status.in_(["proposed", "approved"]))))
    for r in rules:
        if r.material and r.status != "approved":
            items.append({"type": "rule_unconfirmed", "severity": "high", "rule_id": r.id,
                          "message": f"Rule awaiting reviewer confirmation: {r.explanation[:120]}"})
    evals = list(db.scalars(select(Evaluation).where(Evaluation.tender_id == tender.id, Evaluation.stale.is_(False))))
    by_rule: dict[int, list[Evaluation]] = {}
    for e in evals:
        by_rule.setdefault(e.rule_id, []).append(e)
    sim_group_pass = False
    sim_group_items = []
    for r in rules:
        es = by_rule.get(r.id)
        if not es:
            if r.material and r.status != "approved":
                continue  # already listed once as "awaiting reviewer confirmation"
            items.append({"type": "not_evaluated", "severity": "high", "rule_id": r.id,
                          "message": f"Not evaluated: {r.explanation[:120]}"})
            continue
        decisive = [e for e in es if e.subject in ("bidder", "jv:aggregate", "jv")] or es
        worst = _worst([e.result for e in decisive] + [e.result for e in es if e.subject.startswith("entity:") and e.result == FAIL])
        if r.rule_type == "similar_work" and r.params.get("group") == "similar_work":
            if worst == PASS:
                sim_group_pass = True
            else:
                sim_group_items.append({"type": "eligibility", "severity": "critical", "rule_id": r.id, "result": worst,
                                        "message": f"{r.explanation[:120]} → {worst.replace('_', ' ')}"})
            continue
        if worst not in (PASS, NA):
            items.append({"type": "eligibility", "severity": "critical", "rule_id": r.id, "result": worst,
                          "message": f"{r.explanation[:120]} → {worst.replace('_', ' ')}"})
    if not sim_group_pass:
        items.extend(sim_group_items)
    for t in db.scalars(select(ChecklistTask).where(ChecklistTask.tender_id == tender.id, ChecklistTask.mandatory.is_(True))):
        if t.status not in ("accepted", "not_applicable"):
            items.append({"type": "task", "severity": "medium", "task_id": t.id, "message": f"Open mandatory task: {t.title}"})
        if t.stale:
            items.append({"type": "stale_task", "severity": "high", "task_id": t.id, "message": f"Task affected by amendment: {t.title}"})
    total_tasks = db.query(ChecklistTask).filter(ChecklistTask.tender_id == tender.id).count()
    done_tasks = db.query(ChecklistTask).filter(ChecklistTask.tender_id == tender.id,
                                                ChecklistTask.status.in_(["accepted", "not_applicable"])).count()
    return {"blockers": items, "eligible_ready": not any(i["severity"] == "critical" or i["type"] in ("rule_unconfirmed", "not_evaluated", "low_confidence") for i in items),
            "workload_percent": round(100 * done_tasks / total_tasks) if total_tasks else 0,
            "workload_note": "Share of checklist tasks closed. A workload metric only: not proof of qualification or award."}


ORDER = [FAIL, INSUFF, AMBIG, PASS, NA]


def _worst(results: list[str]) -> str:
    for r in ORDER:
        if r in results:
            return r
    return NA
